"""One turn: recall → route → answer (with tools) → log → learn in the background.

`Sylvie.turn()` yields events that the server streams to the chat UI / Mac app:
  meta, status, thinking, text, tool, sources, notice, done, error
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from actions.confirm import is_clear_yes, is_undo
from actions.log import ActionLog
from actions.runner import ActionRunner
from core.config import Config, load_config
from core.context import history_messages, maybe_summarize, system_with_summary
from core.conversations import Conversations
from core.db import Database
from core.devices import Devices
from core.intent import (
    ACTION_NOTE,
    CLAIM_NUDGE,
    LIVE_NOTE,
    MUSIC_DONE_EXAMPLE,
    MUSIC_DONE_NOTE,
    REPLY_LANGUAGE_NOTE,
    SEARCH_NUDGE,
    WRITING_NOTE,
    claims_done,
    looks_like_action,
    looks_like_writing,
    looks_live,
    says_no_access,
)
from core.lang import guess_language, phrase, reply_language
from core.persona import build_system_prompt, build_turn_context, wrap_user_message
from core.quick import GreetingStripper, greets, quick_reply
from core.router import route
from core.settings import INTENT_NOTE as SETTING_NOTE
from core.settings import looks_like_setting_request
from core.shortcuts import NOTE as SHORTCUT_NOTE
from core.shortcuts import Shortcuts
from core.toolcall import StreamSplitter, ToolCall
from core.trace import Trace
from knowledge.docs_index import DocsIndex
from lea_profile.interests import InterestStore
from lea_profile.notice import confirm_question, notice
from lea_profile.preferences import PreferenceStore, detect_preference
from lea_profile.style import StyleStore
from lea_profile.summary import profile_text
from learning.ab import ABTests
from learning.store import LearningStore
from learning.teacher import Teacher, is_criticism, is_wrong_feedback
from memory.episodes import Episodes
from memory.extract import REMEMBER_RE, run_extraction
from memory.forget import forget
from memory.recall import recall
from memory.store import MemoryStore
from tools.registry import ToolContext, available_tools, execute, validate

log = logging.getLogger("sylvie")

MAX_TOOL_ROUNDS = 4
ANSWER_NUDGE = (
    "(You have what you need. Answer Lea now in {lang} with what the tools found, even if it's partial: "
    "say what you found and what you couldn't. No more tool calls.)"
)
CLAUDE_NOTE = (
    "This message was written to Claude, another AI assistant (often for coding), not to Sylvie, and "
    "there is no reply. Instructions about the task itself (what to build, run, fix, write or commit) "
    "are not facts about Lea and not style."
)
LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French"}
MAX_ATTACHMENT_CHARS = 20000


@dataclass
class Attachment:
    name: str
    text: str


@dataclass
class _Job:
    name: str
    fn: callable
    stop: threading.Event = field(default_factory=threading.Event)
    tries: int = 0
    backlog_job: bool = False


class BackgroundWorker:
    """Runs learning jobs when Lea isn't mid-conversation. A new turn interrupts the current
    job (it's re-queued), so background work never slows Sylvie's answers down for long.

    Two kinds of jobs: learning from the conversation that just happened (short; after
    `idle_seconds` of quiet) and *backlog* work like reading Lea's Claude history (hundreds of
    jobs; only after `backlog_idle_seconds`). Measured 2026-11-06: with the backlog running after
    every 8-second pause, a question took 135 s (memory lookup 25 s, prefill 61 s, 11 tok/s)."""

    def __init__(self, idle_seconds: float = 8.0, backlog_idle_seconds: float = 600.0):
        self.q: queue.Queue[_Job] = queue.Queue()
        self.backlog: queue.Queue[_Job] = queue.Queue()
        self.idle_seconds = idle_seconds
        self.backlog_idle_seconds = backlog_idle_seconds
        self.last_activity = 0.0
        self.busy = threading.Event()
        self.current: _Job | None = None
        self.hold = lambda: False  # True while a night job has the models unloaded
        self._thread = threading.Thread(target=self._run, daemon=True, name="sylvie-bg")
        self._thread.start()

    def submit(self, name: str, fn, backlog: bool = False) -> None:
        (self.backlog if backlog else self.q).put(_Job(name, fn, backlog_job=backlog))

    def turn_started(self) -> None:
        self.busy.set()
        self.last_activity = time.time()
        if self.current is not None:
            self.current.stop.set()

    def turn_finished(self) -> None:
        self.last_activity = time.time()
        self.busy.clear()

    def interrupt(self, timeout: float = 30) -> None:
        """Stop the running job (it's re-queued) and wait until it has let go of the model."""
        job = self.current
        if job is not None:
            job.stop.set()
        end = time.time() + timeout
        while self.current is not None and time.time() < end:
            time.sleep(0.05)

    def drain(self, timeout: float = 120) -> None:
        """Tests/CLI: wait until the queue is empty."""
        end = time.time() + timeout
        while (
            not self.q.empty() or not self.backlog.empty() or self.current is not None
        ) and time.time() < end:
            time.sleep(0.05)

    def _next(self) -> _Job:
        while True:
            idle = time.time() - self.last_activity
            if not self.busy.is_set() and not self.hold():
                if idle >= self.idle_seconds and not self.q.empty():
                    return self.q.get()
                if idle >= self.backlog_idle_seconds and self.q.empty() and not self.backlog.empty():
                    return self.backlog.get()
            time.sleep(0.2)

    def _run(self) -> None:
        while True:
            job = self._next()
            job.stop.clear()
            self.current = job
            try:
                job.fn(job.stop)
                if job.stop.is_set() and job.backlog_job:
                    self.backlog.put(job)  # backlog work is never dropped, just postponed
                elif job.stop.is_set() and job.tries < 2:
                    job.tries += 1
                    self.q.put(job)
            except Exception:
                log.exception("background job %s failed", job.name)
            finally:
                self.current = None


class Sylvie:
    def __init__(self, cfg: Config, llm, embedder, db: Database, idle_seconds: float = 8.0, actions=None):
        self.cfg = cfg
        self.llm = llm
        self.embedder = embedder
        self.db = db
        self.convs = Conversations(db)
        self.memory = MemoryStore(db, embedder)
        self.prefs = PreferenceStore(db)
        self.style = StyleStore(db)
        self.interests = InterestStore(db, embedder)
        self.docs = DocsIndex(db, embedder)
        self.actions = actions if actions is not None else ActionRunner(cfg, ActionLog(db))
        self.learning = LearningStore(db)
        self.devices = Devices(db)
        self.ab = ABTests(db)
        self.shortcuts = Shortcuts(db, embedder, cfg)
        self.episodes = Episodes(db, embedder)
        self.teacher = Teacher(self, self.learning)
        backlog_idle = cfg.get_path("learning.backlog_idle_minutes", 10) * 60 if idle_seconds > 0 else 0
        self.bg = BackgroundWorker(idle_seconds=idle_seconds, backlog_idle_seconds=backlog_idle)
        self.scheduler = None  # set by the server (learning/scheduler.py)
        self.bg.hold = lambda: self.scheduler is not None and self.scheduler.proc is not None
        self.claude_history = None  # set by the server (memory/claude_history.py)
        self.app_learning = None  # set by the server (lea_profile/apps.py)
        self._stops: dict[str, threading.Event] = {}
        self._stt = None
        self._tts = None

    @classmethod
    def build(cls, cfg: Config | None = None) -> Sylvie:
        from core.llm import LLM
        from knowledge.embeddings import Embedder

        cfg = cfg or load_config()
        from core.settings import apply_saved
        from learning.promote import load_state

        apply_saved(cfg, load_state(cfg))  # what Lea changed by asking (core/settings.py)
        dim = cfg.get_path("embeddings.dim", 1024)
        return cls(
            cfg,
            LLM(cfg),
            Embedder(cfg.get_path("embeddings.model"), dim=dim),
            Database(cfg.db_path, dim=dim),
        )

    # ---------- prompts ----------
    def current_system_prompt(self) -> str:
        return build_system_prompt(
            self.cfg,
            profile_text=profile_text(self.memory, self.style, self.interests),
            preferences_text=self.prefs.as_prompt(),
        )

    def tool_context(self, user_text: str = "", conversation_id: str | None = None) -> ToolContext:
        return ToolContext(
            cfg=self.cfg,
            docs=self.docs,
            user_text=user_text,
            private_terms=[self.cfg.get_path("user.name", "Lea")],
            actions=self.actions if self.cfg.get_path("actions.enabled") != [] else None,
            conversation_id=conversation_id,
            llm=self.llm if self.cfg.get_path("search.research", True) else None,
            settings=self,
        )

    @staticmethod
    def _check_references(answer: str, tctx) -> str:
        """A note for Lea when an essay's references don't match what was looked up: entries no tool
        found (possibly invented), and works cited in the text but missing from the list."""
        from knowledge.cite import missing_references, reference, unverified_references
        from tools.registry import turn_style

        out = ""
        bad = unverified_references(answer, tctx.refs)
        if bad:
            out += ("\n\n**Check before using:** I couldn't match these references to any source I looked up, "
                    "so they may be wrong or invented:\n" + "\n".join(f"- {b}" for b in bad))  # fmt: skip
        missing = missing_references(answer, tctx.refs)
        if missing:
            st = turn_style(tctx)
            out += "\n\nAlso cited, missing from the references:\n" + "\n".join(
                f"- {reference(r, st)}" for r in missing
            )
        return out

    def speech_to_text(self):
        if self._stt is None:
            from voice.stt import SpeechToText

            self._stt = SpeechToText(
                self.cfg.get_path("voice.stt_model", "mlx-community/whisper-large-v3-turbo")
            )
        return self._stt

    def text_to_speech(self):
        """Kokoro on this Mac, or None when speech is spoken by the device's system voice."""
        if self.cfg.get_path("voice.tts", "system") != "kokoro":
            return None
        if self._tts is None:
            from voice.tts import TextToSpeech

            v = self.cfg.get_path("voice", {}) or {}
            self._tts = TextToSpeech(
                v.get("tts_model", "mlx-community/Kokoro-82M-bf16"),
                voices=v.get("kokoro_voices"),
                speed=v.get("speed", 1.0),
                respell=v.get("respell"),
            )
        return self._tts

    def unload_models(self) -> None:
        """Frees every model's memory for a heavy night job (training the 27B needs ~35 GB of 36).
        Anything Lea asks afterwards reloads what it needs."""
        import gc

        self.llm.unload()
        if self._stt is not None:
            self._stt.unload()
        self._tts = None
        if hasattr(self.embedder, "unload"):
            self.embedder.unload()
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            pass

    def warm(self) -> None:
        """Load models and pre-read the system prompt so the first answer is fast.
        Whisper warms up first: measured, warming it after the LLM made the first question ~1.3 s slower."""
        if self.cfg.get_path("voice.enabled", True):
            import numpy as np

            stt = self.speech_to_text()
            stt.load()
            # One throwaway transcription compiles Whisper's GPU kernels (first run is ~4 s).
            stt.transcribe(np.sin(np.linspace(0, 2000, 16000)).astype(np.float32) * 0.1, language="en")
            tts = self.text_to_speech()
            if tts:
                try:
                    tts.load()
                    tts.warm()  # first sentence per language compiles kernels (~4 s in total)
                except Exception as e:  # missing model/espeak: the app falls back to the system voice
                    log.warning("Kokoro unavailable, using system voices: %s", e)
                    self._tts = None
                    self.cfg["voice"]["tts"] = "system"
        self.llm.load()
        self.embedder.embed_one("warm up")
        self.llm.warm(self.current_system_prompt(), available_tools(self.tool_context()))

    def stop(self, conversation_id: str) -> None:
        ev = self._stops.get(conversation_id)
        if ev:
            ev.set()

    # ---------- the turn ----------
    def turn(
        self,
        text: str,
        conversation_id: str | None = None,
        channel: str = "chat",
        attachments: list[Attachment] | None = None,
        thinking: bool | None = None,
        language: str | None = None,
    ) -> Iterator[dict]:
        """`language`: what Whisper heard, for voice turns (decides when the words alone don't tell)."""
        trace = Trace()
        self.bg.turn_started()
        self.bg.interrupt(timeout=5)  # let a background job release the model before we use it
        if self.scheduler is not None and self.scheduler.preempt():
            yield {"type": "status", "text": "One moment, waking up…"}
        stop = threading.Event()
        cid = None
        try:
            conv = self.convs.get(conversation_id) if conversation_id else None
            if conv is None:
                conv = self.convs.create(title=_title_from(text))
            cid = conv["id"]
            self._stops[cid] = stop
            prev_user_ids = [m["id"] for m in self.convs.messages(cid) if m["role"] == "user"][::-1]
            user_mid = self.convs.add_message(cid, "user", text, channel=channel)
            yield {
                "type": "meta",
                "conversation_id": cid,
                "user_message_id": user_mid,
                "title": conv["title"],
            }

            # The language Lea spoke in (None: can't tell, she follows the conversation)
            said_in = reply_language(text, language)
            notes: list[str] = []
            base_notes = 1 if channel in ("voice", "phone") else 0  # notes beyond these mean "special turn"
            if channel in ("voice", "phone"):
                lang_name = LANG_NAMES.get(said_in or guess_language(text), "English")
                if self.cfg.get_path("voice.speak_full_answer", True):
                    notes.append(
                        "Lea is talking to you by voice. Answer in the SAME language as her message below "
                        f"(it sounds like {lang_name}). Your whole answer is read aloud, so write it to be "
                        "spoken: plain sentences, no markdown, no lists or tables unless she asks for them."
                    )
                else:
                    notes.append(
                        "Lea is talking to you by voice. Answer in the SAME language as her message below "
                        f"(it sounds like {lang_name}). Start with a short spoken answer: 1-3 plain sentences, "
                        "no markdown, no lists. If details help, add them after a blank line: they are shown "
                        "on screen, not read aloud."
                    )
            # 1. "Sylvie, forget that"
            with trace.stage("forget"):
                forgotten = forget(self.memory, text, [f"{cid}#{i}" for i in prev_user_ids])
            if forgotten is not None:
                if forgotten:
                    notes.append(
                        "You just deleted these from your memory at Lea's request: "
                        + "; ".join(f.text for f in forgotten)
                        + ". Confirm briefly."
                    )
                    yield {"type": "notice", "kind": "forgot", "items": [f.to_dict() for f in forgotten]}
                else:
                    notes.append(
                        "Lea asked you to forget something, but you had nothing saved matching it. Say so briefly."
                    )

            # 1b. An action waiting for Lea's "yes" (decided by code, never by the model), or "undo that"
            pending = self.actions.log.pending(cid)
            if pending is not None:
                if is_clear_yes(text):
                    out = self.actions.confirm(pending.id, channel=channel)
                    notes.append(
                        f"Lea said yes to: {pending.summary}. Result: {out.content} Tell her in a few words."
                    )
                    yield {
                        "type": "action",
                        "status": "done" if out.ok else "failed",
                        "id": pending.id,
                        "result": out.content,
                    }
                else:
                    self.actions.cancel(pending.id)
                    notes.append(
                        f"Lea did not confirm this, so it was cancelled and NOT done: {pending.summary}. "
                        "Mention that in a few words, then answer her message."
                    )
                    yield {"type": "action", "status": "cancelled", "id": pending.id}
            elif is_undo(text):
                last = next(
                    (
                        a
                        for a in self.actions.log.recent(20)
                        if a.conversation_id == cid and a.status == "done" and a.undo
                    ),
                    None,
                )
                if last is not None:
                    out = self.actions.undo(last.id)
                    notes.append(
                        f"Lea asked to undo the last action ({last.name}). Result: {out.content} Confirm briefly."
                    )
                    yield {"type": "action", "status": "undone" if out.ok else "failed", "id": last.id}

            # 1c. "Sylvie, that's wrong" / any criticism → thumbs down on the last answer + teacher review
            criticism = forgotten is None and not is_wrong_feedback(text) and is_criticism(text)
            if criticism:
                notes.append(
                    "Lea is criticizing you. Take it seriously and don't defend or justify yourself. Say in a few "
                    "words that you understand, say concretely what you'll do differently, and do it from now on. "
                    "If she is pointing at your last answer and you can fix it, fix it now."
                )
            if forgotten is None and (is_wrong_feedback(text) or criticism):
                last_answer = next(
                    (m for m in reversed(self.convs.visible_messages(cid)) if m["role"] == "assistant"), None
                )
                if last_answer is not None:
                    self.convs.set_feedback(last_answer["id"], -1)
                    self.teacher.report(last_answer["id"], "wrong", comment=text)
                    if not criticism:
                        notes.append(
                            "Lea says your previous answer was wrong. Say sorry in a few words; if you can see the "
                            "mistake, correct it now, otherwise ask briefly what was wrong. A deeper review runs later."
                        )
                    yield {"type": "notice", "kind": "feedback", "message_id": last_answer["id"]}

            # 1d. "speak faster", "use the Bella voice": make sure the change really happens (core/settings.py)
            if looks_like_setting_request(text):
                notes.append(SETTING_NOTE)
            # 1e. "play my playlist", "add it to my calendar": the action must really happen (core/intent.py)
            action_request = looks_like_action(text)
            if action_request:
                notes.append(ACTION_NOTE)
            # 1f. Essays and reports: real sources, exact citations, room for a long answer (core/intent.py)
            writing = looks_like_writing(text)
            if writing:
                notes.append(WRITING_NOTE)
            # 1g. Weather, what's on TV, news: she must look it up, not say she has no access (core/intent.py)
            live = (
                looks_live(text)
                and not action_request
                and not writing
                and self.cfg.get_path("search.enabled", True)
            )
            if live:
                notes.append(LIVE_NOTE)

            # 1c. "Hello" / "thanks" / "bye": an instant answer, no model (core/quick.py)
            reply = None
            if forgotten is None and pending is None and not attachments and len(notes) == base_notes:
                reply = quick_reply(text, self.cfg)
            if reply:
                trace.mark("first_token")
                yield {"type": "status", "mode": "quick", "reason": "greeting"}
                yield {"type": "text", "text": reply}
                final_mid = self.convs.add_message(
                    cid, "assistant", reply, model_content=reply, trace=trace.to_dict(), mode="quick",
                    channel=channel,
                )  # fmt: skip
                yield {"type": "done", "message_id": final_mid, "conversation_id": cid,
                       "trace": trace.to_dict(), "stopped": False}  # fmt: skip
                return

            # 2. "No Sylvie, I prefer…" → saved and applied right now
            system_prompt = conv.get("system_prompt")
            with trace.stage("preference"):
                pref = detect_preference(self.llm, self.prefs, text) if forgotten is None else None
            if pref:
                system_prompt = None  # rebuild with the new preference
                notes.append(
                    f'Lea just stated a lasting preference, now saved: "{pref["preference"]}". '
                    f'Start your answer with a few-word confirmation like "{pref["ack"]}" and follow it from now on.'
                )
                yield {"type": "notice", "kind": "preference", "item": pref}
            if not system_prompt:
                system_prompt = self.current_system_prompt()
                self.convs.set(cid, system_prompt=system_prompt)

            # 3. Recall memories + interest noticing
            with trace.stage("recall"):
                facts = recall(self.db, self.embedder, text, k=self.cfg.get_path("memory.recall_top_k", 6))
                aside = None
                max_asides = self.cfg.get_path("proactivity.max_asides_per_conversation", 2)
                if (conv.get("aside_used") or 0) < max_asides and self.cfg.get_path(
                    "proactivity.mode"
                ) != "off":
                    aside = notice(self.interests, text)
                    if aside is None:
                        q = confirm_question(self.interests)
                        if q:
                            self.interests.mark_asked(q[0])
                            aside = q[1]
                    if aside:
                        self.convs.set(cid, aside_used=(conv.get("aside_used") or 0) + 1)
                # What was said in earlier conversations (memory/episodes.py), with dates
                past = self.episodes.relevant(text, cid) if self.cfg.get_path("memory.episodes", True) else []
                # An old "I can't check the weather" teaches her to refuse again (measured 2026-11-08)
                past = [e for e in past if not says_no_access(e.text)]
                if past:
                    notes.append(
                        "From earlier conversations with Lea (use only if it helps; say when it was if you refer "
                        "to it):\n" + "\n".join(e.line() for e in past)
                    )
            context = build_turn_context(self.cfg, memories=[f.text for f in facts] + notes, aside=aside)

            # 4. Attachments (dropped files) are untrusted data
            model_text = text
            for a in attachments or []:
                model_text += (
                    f'\n\n<untrusted source="file {a.name}">\n{a.text[:MAX_ATTACHMENT_CHARS]}\n</untrusted>'
                )
            user_model = wrap_user_message(model_text, context)
            # Last thing she reads before answering: the language Lea used. Measured 2026-11-08: in a Spanish
            # conversation, English questions got Spanish answers (the note above, before her message, wasn't enough).
            if said_in:
                user_model += f"\n\n(Reply in {LANG_NAMES[said_in]}.)"
            self.db.execute("UPDATE messages SET model_content = ? WHERE id = ?", (user_model, user_mid))

            # 5. Route
            r = route(text)
            if thinking is not None:
                r.thinking = thinking
            mode = "thinking" if r.thinking else "fast"
            yield {"type": "status", "mode": mode, "reason": r.reason}
            if r.thinking:
                yield {"type": "status", "text": phrase("thinking", text)}

            # 6. Answer, with tools
            conv = self.convs.get(cid)
            rows = self.convs.messages(cid, after_id=conv["summarized_upto"] or 0)
            messages = [
                {"role": "system", "content": system_with_summary(system_prompt, conv.get("summary"))}
            ]
            messages += history_messages(rows)
            tctx = self.tool_context(text, cid)
            tools = available_tools(tctx)
            display_parts: list[str] = []
            invalid_calls = 0
            announced = False
            final_mid = None
            # Search material per question is capped. Measured 2026-11-06: seven web searches added
            # ~44k characters, the last round needed 17,282 tokens (over the 16,384 context) and the
            # answer came out empty after 7 minutes.
            tool_chars, tool_budget = 0, self.cfg.get_path("llm.tool_budget_chars", 20000)

            # 5b. Something Lea asks often: start its lookup now instead of after a model round
            sc = self.shortcuts.find(text) if tools else None
            if sc is not None:
                call = ToolCall(sc.tool, sc.args)
                if validate(call, tools) is None:
                    announced = True
                    trace.mark("first_status")
                    yield {"type": "status", "text": phrase("checking", text)}
                    result = self._run_tool(call, tools, tctx, trace)
                    self.shortcuts.used(sc.id)
                    assistant_msg = {"role": "assistant", "content": "", "tool_calls": [call.to_message()]}
                    messages.append(assistant_msg)
                    self.convs.add_message(
                        cid,
                        "assistant",
                        "",
                        model_content="",
                        tool_calls=assistant_msg["tool_calls"],
                        mode=mode,
                    )
                    content = SHORTCUT_NOTE.format(q=sc.question) + result.content
                    yield {"type": "tool", "name": call.name, "args": call.arguments, "ok": result.ok,
                           "shortcut": True}  # fmt: skip
                    if result.sources:
                        yield {"type": "sources", "sources": tctx.sources}
                    messages.append({"role": "tool", "content": content})
                    self.convs.add_message(cid, "tool", "", model_content=content)
                    tool_chars += len(content)

            # 5c. An essay: look in Lea's Zotero library before the first word, so the essay is built on
            # her sources (measured 2026-11-07: without it the 9B once wrote two invented references).
            if writing and any(t["function"]["name"] == "zotero_library" for t in tools):
                call = ToolCall("zotero_library", {"query": text[:300]})
                announced = True
                trace.mark("first_status")
                yield {"type": "status", "text": phrase("checking", text)}
                result = self._run_tool(call, tools, tctx, trace)
                if result is not None:
                    assistant_msg = {"role": "assistant", "content": "", "tool_calls": [call.to_message()]}
                    messages.append(assistant_msg)
                    self.convs.add_message(cid, "assistant", "", model_content="",
                                           tool_calls=assistant_msg["tool_calls"], mode=mode)  # fmt: skip
                    yield {"type": "tool", "name": call.name, "args": call.arguments, "ok": result.ok}
                    if result.sources:
                        yield {"type": "sources", "sources": tctx.sources}
                    messages.append({"role": "tool", "content": result.content})
                    self.convs.add_message(cid, "tool", "", model_content=result.content)
                    tool_chars += len(result.content)

            called_tools = False
            # web_search ran this turn (Wikipedia alone isn't enough for prices or the weather)
            searched = False
            shown_photos: list[
                dict
            ] = []  # photos shown this turn: saved with the answer, shown again on reload
            nudged = False
            greeting = GreetingStripper(active=not greets(text))
            answer_nudged = False
            for round_no in range(MAX_TOOL_ROUNDS + 2):  # the last one only after a nudge below
                # Tools are dropped on the last round, or after a second malformed call (retry once).
                use_tools = (
                    tools
                    if round_no < MAX_TOOL_ROUNDS and invalid_calls < 2 and tool_chars < tool_budget
                    else None
                )
                sp = StreamSplitter(thinking=r.thinking)
                with trace.stage("llm"):
                    for chunk in self.llm.stream(
                        messages,
                        tools=use_tools,
                        thinking=r.thinking,
                        effort=r.effort,
                        stop=stop,
                        max_tokens=self.cfg.get_path("llm.max_tokens_writing", 3072) if writing else None,
                    ):
                        if chunk.done:
                            if chunk.stats:
                                trace.llm_rounds.append(
                                    {
                                        "prompt": chunk.stats.prompt_tokens,
                                        "cached": chunk.stats.cached_tokens,
                                        "prefill_ms": round(chunk.stats.prefill_s * 1000),
                                        "ttft_ms": round(chunk.stats.time_to_first_token * 1000),
                                        "generated": chunk.stats.generation_tokens,
                                        "tok_s": round(chunk.stats.generation_tps, 1),
                                    }
                                )
                            break
                        for kind, s in sp.feed(chunk.text):
                            if kind == "text":
                                s = greeting.feed(s)  # no "Hola Lea." opener unless she greeted
                                if not s:
                                    continue
                                trace.mark("first_token")
                            yield {"type": kind, "text": s}
                        if sp.in_tool and not announced:
                            # Say "let me check" the moment a tool call starts, not after it's written.
                            announced = True
                            trace.mark("first_status")
                            yield {"type": "status", "text": phrase("checking", text)}
                    for kind, s in sp.flush():
                        if kind == "text":
                            s = greeting.feed(s)
                            if not s:
                                continue
                        yield {"type": kind, "text": s}
                    if rest := greeting.flush():
                        yield {"type": "text", "text": rest}
                visible = greeting.clean(sp.visible_text.strip())
                calls = sp.tool_calls if use_tools and not stop.is_set() else []
                if not calls and not visible and sp.raw.strip() and not stop.is_set() and use_tools:
                    # Everything went into a tool call that never closed or didn't parse, so Lea
                    # would get a blank answer. Retry once without tools instead.
                    log.warning("Unusable tool call, retrying without tools: %r", sp.raw[:300])
                    invalid_calls = 2
                    continue
                if (
                    not calls
                    and action_request
                    and use_tools
                    and not called_tools
                    and not nudged
                    and not stop.is_set()
                    and claims_done(visible)
                ):
                    # She said it's done without calling a tool: send her back to really do it.
                    log.warning("claimed an action without a tool call: %r", visible[:120])
                    nudged = True
                    display_parts.append(visible)
                    messages.append({"role": "assistant", "content": visible})
                    messages.append({"role": "user", "content": CLAIM_NUDGE})
                    continue
                if (
                    not calls
                    and use_tools
                    and not searched
                    and not nudged
                    and not stop.is_set()
                    and any(t["function"]["name"] == "web_search" for t in use_tools)
                    and says_no_access(visible)
                ):
                    # "I don't have access to real-time weather": she can search, so send her to do it.
                    log.warning("said she has no access instead of searching: %r", visible[:120])
                    nudged = True
                    display_parts.append(visible)
                    messages.append({"role": "assistant", "content": visible})
                    messages.append({"role": "user", "content": SEARCH_NUDGE})
                    continue
                if not calls and not visible and called_tools and not answer_nudged and not stop.is_set():
                    # Measured 2026-11-08 (make everyday): after four searches about the weekend's rain the
                    # last round (no tools left) came back empty. Ask once more, plainly, for the answer.
                    log.warning("empty answer after tools, asking for it: %r", sp.raw[:200])
                    answer_nudged = True
                    invalid_calls = 2  # no more tools
                    lang = LANG_NAMES.get(said_in or guess_language(text), "English")
                    messages.append({"role": "user", "content": ANSWER_NUDGE.format(lang=lang)})
                    continue
                if not calls:
                    if not visible and not stop.is_set():
                        # Never leave Lea with silence: say plainly that this one didn't work out.
                        visible = phrase("lost", text)
                        yield {"type": "text", "text": visible}
                    if writing and visible:
                        # Every reference must be one a tool found this turn (knowledge/cite.py).
                        extra = self._check_references(visible, tctx)
                        if extra:
                            yield {"type": "text", "text": extra}
                            visible += extra
                    display_parts.append(visible)
                    final_mid = self.convs.add_message(
                        cid,
                        "assistant",
                        "\n\n".join(p for p in display_parts if p),
                        model_content=visible,
                        sources=(tctx.sources + [{"photo": c} for c in shown_photos]) or None,
                        trace=trace.to_dict(),
                        mode=mode,
                        channel=channel,
                    )
                    break
                # Tool round
                called_tools = True
                searched = searched or any(c.name in ("web_search", "research") for c in calls)
                if visible:
                    display_parts.append(visible)
                assistant_msg = {
                    "role": "assistant",
                    "content": visible,
                    "tool_calls": [c.to_message() for c in calls],
                }
                messages.append(assistant_msg)
                self.convs.add_message(
                    cid,
                    "assistant",
                    "",
                    model_content=visible,
                    tool_calls=assistant_msg["tool_calls"],
                    mode=mode,
                )
                if round_no == 0:  # remember the lookup; asked often enough, it becomes a shortcut
                    first = calls[0]
                    self.bg.submit("shortcut", lambda ev, c=first: self._note_lookup(text, c))
                for call in calls:
                    if call.name == "research":
                        yield {"type": "status", "text": phrase("research", text)}
                    elif call.name == "ask_claude":
                        yield {"type": "status", "text": phrase("claude", text)}
                    result = self._run_tool(call, tools, tctx, trace)
                    turn_note = ""
                    if result is None:  # invalid call
                        invalid_calls += 1
                        err = validate(call, tools) or "Invalid tool call format."
                        content = f"Error: {err} Please fix the call."
                        if invalid_calls >= 2:
                            content = f"Error: {err} Answer without tools now."
                    else:
                        content = result.content
                        room = max(tool_budget - tool_chars, 1500)
                        if len(content) > room:
                            content = (
                                content[:room]
                                + "\n…(trimmed: you have enough material now; answer Lea with it)"
                            )
                        yield {"type": "tool", "name": call.name, "args": call.arguments, "ok": result.ok}
                        reply_lang = LANG_NAMES.get(said_in or guess_language(text), "English")
                        if call.name in ("play_music", "music") and result.ok:
                            # Lea, 2026-11-08: a song request gets "done", not questions ("¿Te gusta el ritmo?").
                            example = MUSIC_DONE_EXAMPLE.get(reply_lang, MUSIC_DONE_EXAMPLE["English"])
                            turn_note = MUSIC_DONE_NOTE.format(lang=reply_lang, example=example)
                        else:
                            turn_note = REPLY_LANGUAGE_NOTE.format(lang=reply_lang)
                        if call.name == "play_music" and result.ok:
                            # What she plays is her taste: it feeds the interests Sylvie picks music from.
                            q = str(call.arguments.get("query", "")).strip()
                            if q and not q.startswith(("spotify:", "http")):
                                self.bg.submit(
                                    "taste", lambda ev, q=q: self.interests.bump(f"music: {q}", 0.5)
                                )
                        if result.pending:
                            yield {"type": "action", "status": "pending", **result.pending}
                        if result.photos:
                            shown_photos += result.photos
                            yield {"type": "photos", "items": result.photos}
                        if result.sources:
                            yield {"type": "sources", "sources": tctx.sources}
                    messages.append({"role": "tool", "content": content + turn_note})  # note: this turn only
                    self.convs.add_message(cid, "tool", "", model_content=content)
                    tool_chars += len(content)

            trace_d = trace.to_dict()
            if final_mid:
                self.db.execute("UPDATE messages SET trace = ? WHERE id = ?", (_json(trace_d), final_mid))
            yield {
                "type": "done",
                "message_id": final_mid,
                "conversation_id": cid,
                "trace": trace_d,
                "stopped": stop.is_set(),
            }

            # 7. Learn in the background
            answer = "\n\n".join(display_parts)
            source = f"{cid}#{user_mid}"
            self.bg.submit("episode", lambda ev: self.episodes.add(cid, user_mid, text, answer))
            self.bg.submit("learn", lambda ev: self._learn(text, answer, source, ev))
            max_ctx = self.cfg.get_path("llm.summarize_after_tokens", 11000)
            self.bg.submit("summarize", lambda ev: maybe_summarize(self.llm, self.convs, cid, max_ctx))
        except Exception as e:
            log.exception("turn failed")
            yield {"type": "error", "text": f"Something went wrong: {type(e).__name__}: {e}"}
        finally:
            if cid:
                self._stops.pop(cid, None)
            self.bg.turn_finished()

    def _note_lookup(self, text: str, call: ToolCall) -> None:
        sc = self.shortcuts.record(text, call.name, call.arguments)
        if sc is not None:
            log.info("new shortcut #%s: %s → %s", sc.id, sc.question[:60], sc.tool)

    def _run_tool(self, call: ToolCall, tools, tctx, trace):
        if call.name == "__invalid__" or validate(call, tools):
            return None
        with trace.stage(f"tool:{call.name}"):
            return execute(call, tctx)

    # ---------- background learning ----------
    def learn_from_claude(self, message: str, source: str, stop: threading.Event) -> None:
        """One of Lea's own messages to Claude (memory/claude_history.py). Facts and interests
        are learned as usual; a rule about answers is saved switched off, for Lea to approve."""
        detect_preference(_Stoppable(self.llm, stop), self.prefs, message, source="claude")
        if not stop.is_set():
            self._learn(message, "", source, stop, note=CLAUDE_NOTE)

    def _learn(self, message: str, reply: str, source: str, stop: threading.Event, note: str = "") -> None:
        if len(message.strip()) < 4:
            return
        before = self.current_system_prompt()
        existing = recall(self.db, self.embedder, message, k=8)
        ex = run_extraction(_Stoppable(self.llm, stop), message, reply, existing, note=note)
        if stop.is_set():
            return  # interrupted: the job is re-queued and will run again
        for f in ex.facts:
            self.memory.add(
                f["fact"], key=f["key"], category=f["category"], source=source, sensitive=f["sensitive"]
            )
        explicit = bool(REMEMBER_RE.search(message))
        for topic in ex.interests:
            i = self.interests.bump(topic)
            if i is not None and explicit and i.status == "guess":
                self.interests.set_status(i.id, "confirmed")  # "remember I'm into X" — Lea said it herself
        for topic in ex.interests_removed:
            self.interests.remove(topic)
        for s in ex.style:
            self.style.observe(s["trait"], s["description"], s.get("evidence"))
        if ex.facts or ex.interests or ex.style:
            self.convs.log_event(
                "learned", {"source": source, "facts": ex.facts, "interests": ex.interests, "style": ex.style}
            )
        after = self.current_system_prompt()
        if after != before and not stop.is_set():
            # The next new conversation will start with this prompt: read it into the cache now.
            self.llm.warm(after, available_tools(self.tool_context()))


class _Stoppable:
    """Passes a stop event into llm.complete() for background jobs."""

    def __init__(self, llm, stop):
        self.llm, self.stop = llm, stop

    def complete(self, messages, **kw):
        return self.llm.complete(messages, stop=self.stop, **kw)


def _title_from(text: str) -> str:
    words = text.strip().split()
    title = " ".join(words[:7])
    return (title[:60] + "…") if len(title) > 60 or len(words) > 7 else (title or "New chat")


def _json(d) -> str:
    import json

    return json.dumps(d)
