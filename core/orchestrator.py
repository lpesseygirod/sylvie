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
from datetime import UTC, datetime, timedelta

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
    GOT_IT,
    LIVE_NOTE,
    MUSIC_DONE_EXAMPLE,
    MUSIC_DONE_NOTE,
    PAST_NOTE,
    QUIET_REPLY,
    REPLY_LANGUAGE_NOTE,
    SEARCH_NUDGE,
    SEARCH_PROMISE_NUDGE,
    TODO_ADD_NOTE,
    TODO_LIST_NOTE,
    TODO_NUDGE,
    TOLD_TODOS_NOTE,
    UNSENT_NUDGE,
    WRITING_NOTE,
    accepts_search_offer,
    claims_done,
    claims_sent,
    fix_due_day,
    looks_like_action,
    looks_like_writing,
    looks_live,
    music_control,
    past_reference,
    promises_search,
    says_no_access,
    todo_request,
    wants_quiet,
)
from core.lang import LANGUAGE_RETRY, LanguageGuard, guess_language, phrase, reply_language
from core.persona import build_system_prompt, build_turn_context, wrap_user_message
from core.persona import now as local_now
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
from memory.recall import ABOUT_ASSISTANT_RE, recall
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

    def __init__(
        self,
        idle_seconds: float = 8.0,
        backlog_idle_seconds: float = 600.0,
        sleep_idle_seconds: float = 300.0,
    ):
        self.q: queue.Queue[_Job] = queue.Queue()
        self.backlog: queue.Queue[_Job] = queue.Queue()
        self.idle_seconds = idle_seconds
        self.backlog_idle_seconds = backlog_idle_seconds
        # Sleep-time learning (efficiency plan A5): one job that learns from the conversation once it
        # has gone quiet, instead of a model call 8 s after every message.
        self.sleep_idle_seconds = sleep_idle_seconds
        self.sleep_job: _Job | None = None
        self._sleep_lock = threading.Lock()
        self.last_activity = 0.0
        self.busy = threading.Event()
        self.current: _Job | None = None
        self.hold = lambda: False  # True while a night job has the models unloaded
        # Backlog jobs also wait for the memory guard (core/memguard.py): normal pressure, enough
        # free memory, Lea away from the Mac. Conversation jobs are short and use the loaded model.
        self.backlog_gate = lambda: True
        self._thread = threading.Thread(target=self._run, daemon=True, name="sylvie-bg")
        self._thread.start()

    def submit(self, name: str, fn, backlog: bool = False) -> None:
        (self.backlog if backlog else self.q).put(_Job(name, fn, backlog_job=backlog))

    def after_quiet(self, name: str, fn) -> None:
        """Run `fn` once Lea has been quiet for `sleep_idle_seconds` (one pending at a time: the job
        itself reads everything that is waiting)."""
        with self._sleep_lock:
            if self.sleep_job is None:
                self.sleep_job = _Job(name, fn)

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
            not self.q.empty() or not self.backlog.empty() or self.current is not None or self.sleep_job
        ) and time.time() < end:
            time.sleep(0.05)

    def _next(self) -> _Job:
        while True:
            idle = time.time() - self.last_activity
            if not self.busy.is_set() and not self.hold():
                if idle >= self.idle_seconds and not self.q.empty():
                    return self.q.get()
                if idle >= self.sleep_idle_seconds and self.q.empty() and self.sleep_job is not None:
                    with self._sleep_lock:
                        job, self.sleep_job = self.sleep_job, None
                    if job is not None:
                        return job
                if (
                    idle >= self.backlog_idle_seconds
                    and self.q.empty()
                    and not self.backlog.empty()
                    and self.backlog_gate()
                ):
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
                elif job.stop.is_set() and job.name == "sleep-learn":
                    with self._sleep_lock:  # what's still unlearned stays in the database for next time
                        self.sleep_job = self.sleep_job or job
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
        sleep_idle = cfg.get_path("learning.learn_after_quiet_minutes", 5) * 60 if idle_seconds > 0 else 0
        self.bg = BackgroundWorker(
            idle_seconds=idle_seconds, backlog_idle_seconds=backlog_idle, sleep_idle_seconds=sleep_idle
        )
        if self.db.one("SELECT value FROM meta WHERE key = ?", (LEARNED_UPTO,)) is None:
            # From now on (efficiency plan A5): messages before this were already learned one by one.
            top = self.db.one("SELECT COALESCE(MAX(id), 0) n FROM messages WHERE role = 'user'")["n"]
            self._set_learned_upto(top)
        self.scheduler = None  # set by the server (learning/scheduler.py)
        self.bg.hold = lambda: self.scheduler is not None and self.scheduler.proc is not None
        self.claude_history = None  # set by the server (memory/claude_history.py)
        self.app_learning = None  # set by the server (lea_profile/apps.py)
        self._stops: dict[str, threading.Event] = {}
        self._stt = None
        self._tts = None
        self._voice_used = 0.0  # last time Whisper or Kokoro was asked for (unloaded when quiet)
        self._voice_warming = False
        self.guard = None  # set by the server (core/memguard.py)
        from core.small_model import SmallJobs

        self.small = SmallJobs(cfg, llm)  # fact extraction: Apple's on-device model once it qualifies (A3)

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
        self._voice_used = time.time()
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
        self._voice_used = time.time()
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

    # ---------- voice models: loaded when voice is used (efficiency plan A2) ----------
    def warm_voice(self) -> None:
        """Load and compile Whisper and Kokoro. Whisper's first transcription compiles its GPU kernels
        (~4 s), Kokoro's first sentence per language too, so both get a throwaway run."""
        import numpy as np

        stt = self.speech_to_text()
        stt.load()
        stt.transcribe(np.sin(np.linspace(0, 2000, 16000)).astype(np.float32) * 0.1, language="en")
        tts = self.text_to_speech()
        if tts:
            try:
                tts.load()
                tts.warm()
            except Exception as e:  # missing model/espeak: the app falls back to the system voice
                log.warning("Kokoro unavailable, using system voices: %s", e)
                self._tts = None
                self.cfg["voice"]["tts"] = "system"

    def prepare_voice(self) -> None:
        """Lea said "Sylvie": load the voice models in the background while the greeting plays and she
        speaks, so the transcription is ready by the time her question arrives."""
        self._voice_used = time.time()
        if not self.cfg.get_path("voice.enabled", True):
            return
        if self._stt is not None and getattr(self._stt, "_model", None) is not None:
            return
        if self._voice_warming:
            return
        self._voice_warming = True

        def run():
            try:
                import numpy as np

                t0 = time.perf_counter()
                # Whisper only: the greeting that's playing already loads Kokoro for its language.
                stt = self.speech_to_text()
                stt.load()
                stt.transcribe(np.sin(np.linspace(0, 2000, 16000)).astype(np.float32) * 0.1, language="en")
                log.info("Whisper loaded on demand in %.1f s", time.perf_counter() - t0)
            except Exception:
                log.exception("loading the voice models failed")
            finally:
                self._voice_warming = False

        threading.Thread(target=run, daemon=True, name="sylvie-voice-warm").start()

    def voice_idle_seconds(self) -> float:
        return time.time() - self._voice_used if self._voice_used else float("inf")

    def unload_voice(self) -> bool:
        """Frees Whisper and Kokoro (~1.9 GB). Returns True if anything was loaded."""
        if self._voice_warming:
            return False
        had = (self._stt is not None and getattr(self._stt, "_model", None) is not None) or (
            self._tts is not None and getattr(self._tts, "_model", None) is not None
        )
        if not had:
            return False
        if self._stt is not None:
            self._stt.unload()
        if self._tts is not None:
            with self._tts._lock:
                self._tts._model, self._tts._packs = None, {}
        import gc

        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            pass
        return True

    def warm(self) -> None:
        """Load the LLM and pre-read the system prompt so the first answer is fast. The voice models are
        loaded on demand (`voice.load_on_demand`, default on): Lea mostly types, and Whisper + Kokoro
        hold ~1.9 GB. With it off, Whisper warms first (measured: after the LLM, the first question was
        ~1.3 s slower)."""
        if self.cfg.get_path("voice.enabled", True) and not self.cfg.get_path("voice.load_on_demand", True):
            self.warm_voice()
            self._voice_used = time.time()
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
                        "spoken: plain sentences, no markdown, no lists or tables unless she asks for them, "
                        "and no <doc> blocks unless she asks you to write or make a document."
                    )
                else:
                    notes.append(
                        "Lea is talking to you by voice. Answer in the SAME language as her message below "
                        f"(it sounds like {lang_name}). Start with a short spoken answer: 1-3 plain sentences, "
                        "no markdown, no lists. If details help, add them after a blank line: they are shown "
                        "on screen, not read aloud. No <doc> blocks unless she asks you to write or make a document."
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
            # "Remind me to call my dad" → Reminders, then just "Got it"; "what do I need to do?" → read them
            todo = todo_request(text) if not attachments else None
            if todo == "add":
                notes.append(TODO_ADD_NOTE)
                action_request = True
            elif action_request:
                notes.append(ACTION_NOTE)
            # 1f. Essays and reports: real sources, exact citations, room for a long answer (core/intent.py)
            writing = looks_like_writing(text)
            if writing:
                notes.append(WRITING_NOTE)
            # 1g. Weather, what's on TV, news: she must look it up, not say she has no access (core/intent.py)
            # "¿Quieres que busque…?" → "Sí": that's a request to search too
            last_answer_text = next(
                (
                    m["content"]
                    for m in reversed(self.convs.messages(cid))
                    if m["role"] == "assistant" and m["content"]
                ),
                "",
            )
            live = (
                (looks_live(text) or accepts_search_offer(text, last_answer_text))
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
            if not reply and forgotten is None and pending is None and not attachments and wants_quiet(text):
                reply = QUIET_REPLY.get(LANG_NAMES.get(said_in or guess_language(text), "English"), "Okay.")
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
                facts = recall(
                    self.db,
                    self.embedder,
                    text,
                    k=self.cfg.get_path("memory.recall_top_k", 6),
                    for_answer=True,
                )
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

            # 5d. Her to-do list (Lea, 2026-11-10): reading it is a lookup code does first; adding to it is
            # one model round that may only call create_reminder, then a fixed "Got it" (core/intent.py).
            has_tool = {t["function"]["name"] for t in tools}
            if todo == "list" and "list_reminders" in has_tool:
                call = ToolCall("list_reminders", {})
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
                    lang = LANG_NAMES.get(said_in or guess_language(text), "English")
                    content = result.content + (TODO_LIST_NOTE if result.ok else "")
                    told = self.told_todos()
                    if told:
                        content += TOLD_TODOS_NOTE.format(items="\n".join(told))
                    messages.append(
                        {"role": "tool", "content": content + REPLY_LANGUAGE_NOTE.format(lang=lang)}
                    )
                    self.convs.add_message(cid, "tool", "", model_content=content)
                    tool_chars += len(content)
            # 5e. "Do you remember…?", "el día que…": search earlier conversations before she answers
            # (core/intent.py PAST_RE). Measured 2026-11-12: without it she said she remembered and made it up.
            elif todo is None and past_reference(text) and "search_past_conversations" in has_tool:
                call = ToolCall("search_past_conversations", {"query": text[:500]})
                trace.mark("first_status")
                result = self._run_tool(call, tools, tctx, trace)
                if result is not None:
                    assistant_msg = {"role": "assistant", "content": "", "tool_calls": [call.to_message()]}
                    messages.append(assistant_msg)
                    self.convs.add_message(cid, "assistant", "", model_content="",
                                           tool_calls=assistant_msg["tool_calls"], mode=mode)  # fmt: skip
                    yield {"type": "tool", "name": call.name, "args": call.arguments, "ok": result.ok}
                    lang = LANG_NAMES.get(said_in or guess_language(text), "English")
                    messages.append(
                        {
                            "role": "tool",
                            "content": result.content + PAST_NOTE + REPLY_LANGUAGE_NOTE.format(lang=lang),
                        }
                    )
                    self.convs.add_message(cid, "tool", "", model_content=result.content)
                    tool_chars += len(result.content)
            saved = None
            control = music_control(text) if todo is None and "music" in has_tool else None
            if todo == "add" and "create_reminder" in has_tool:
                saved = yield from self._add_todos(
                    text, cid, messages, tools, tctx, trace, stop, mode, said_in
                )
            elif control is not None:
                saved = yield from self._music_control(
                    text, control, cid, messages, tools, tctx, trace, mode, said_in
                )
            if saved is not None:
                final_mid = self.convs.add_message(
                    cid, "assistant", saved, model_content=saved, trace=trace.to_dict(), mode=mode,
                    channel=channel,
                )  # fmt: skip
                trace_d = trace.to_dict()
                yield {"type": "done", "message_id": final_mid, "conversation_id": cid,
                       "trace": trace_d, "stopped": False}  # fmt: skip
                self.bg.submit("episode", lambda ev: self.episodes.add(cid, user_mid, text, saved))
                self.bg.after_quiet("sleep-learn", self.sleep_learn)
                return

            called_tools = False
            # web_search ran this turn (Wikipedia alone isn't enough for prices or the weather)
            searched = False
            shown_photos: list[
                dict
            ] = []  # photos shown this turn: saved with the answer, shown again on reload
            nudged = False
            greeting = GreetingStripper(active=not greets(text))
            answer_nudged = False
            unsent: list[str] = []  # sends that failed or wait for Lea's yes, this turn
            lang_guard = LanguageGuard(said_in)  # the answer's first words must be in Lea's language
            for round_no in range(MAX_TOOL_ROUNDS + 2):  # the last one only after a nudge below
                # Tools are dropped on the last round, or after a second malformed call (retry once).
                use_tools = (
                    tools
                    if round_no < MAX_TOOL_ROUNDS and invalid_calls < 2 and tool_chars < tool_budget
                    else None
                )
                sp = StreamSplitter(thinking=r.thinking)
                wrong_lang = False
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
                                s, wrong_lang = lang_guard.feed(s)
                                if wrong_lang:
                                    break
                                if not s:
                                    continue  # held until its language is clear (a few words)
                                trace.mark("first_token")
                            yield {"type": kind, "text": s}
                        if wrong_lang:
                            break  # stop generating: this start gets redone in her language
                        if sp.in_tool and not announced:
                            # Say "let me check" the moment a tool call starts, not after it's written.
                            announced = True
                            trace.mark("first_status")
                            yield {"type": "status", "text": phrase("checking", text)}
                    if not wrong_lang:
                        tail = []
                        for kind, s in sp.flush():
                            if kind == "text":
                                s = greeting.feed(s)
                                if not s:
                                    continue
                                s, wrong_lang = lang_guard.feed(s)
                                if wrong_lang:
                                    break
                            tail.append((kind, s))
                        if not wrong_lang:
                            rest, wrong_lang = lang_guard.feed(greeting.flush())
                            held, wrong2 = lang_guard.flush()
                            wrong_lang = wrong_lang or wrong2
                            if not wrong_lang:
                                for kind, s in tail:
                                    if s:
                                        yield {"type": kind, "text": s}
                                if rest + held:
                                    yield {"type": "text", "text": rest + held}
                if wrong_lang and not stop.is_set():
                    log.warning("answer started in the wrong language (wanted %s), redoing it", said_in)
                    lang_guard = LanguageGuard(None)  # one retry
                    greeting = GreetingStripper(active=not greets(text))
                    messages.append(
                        {"role": "user", "content": LANGUAGE_RETRY.format(lang=LANG_NAMES[said_in])}
                    )
                    continue
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
                    and (says_no_access(visible) or promises_search(visible))
                ):
                    # "I don't have access to real-time weather": she can search, so send her to do it.
                    log.warning("said she has no access instead of searching: %r", visible[:120])
                    nudged = True
                    display_parts.append(visible)
                    messages.append({"role": "assistant", "content": visible})
                    messages.append(
                        {
                            "role": "user",
                            "content": SEARCH_NUDGE if says_no_access(visible) else SEARCH_PROMISE_NUDGE,
                        }
                    )
                    continue
                if not calls and unsent and not nudged and not stop.is_set() and claims_sent(visible):
                    # "I sent it" when it wasn't sent: correct it before Lea believes it.
                    log.warning("claimed a send that didn't happen: %r", visible[:120])
                    nudged = True
                    display_parts.append(visible)
                    messages.append({"role": "assistant", "content": visible})
                    messages.append({"role": "user", "content": UNSENT_NUDGE.format(why=unsent[-1][:300])})
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
                        if call.name in ("send_message", "send_email") and (not result.ok or result.pending):
                            unsent.append(result.content)
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
            self.bg.submit(
                "episode", lambda ev: self.episodes.add(cid, user_mid, text, answer)
            )  # embedding only
            # Facts, interests, style and summaries: once the conversation goes quiet (A5), from the database.
            self.bg.after_quiet("sleep-learn", self.sleep_learn)
            max_ctx = self.cfg.get_path("llm.summarize_after_tokens", 11000)
            # Safety net only: a conversation that outgrows the context still gets summarized mid-way.
            self.bg.submit("summarize", lambda ev: maybe_summarize(self.llm, self.convs, cid, max_ctx))
        except Exception as e:
            log.exception("turn failed")
            yield {"type": "error", "text": f"Something went wrong: {type(e).__name__}: {e}"}
        finally:
            if cid:
                self._stops.pop(cid, None)
            self.bg.turn_finished()

    def _music_control(self, text, command, cid, messages, tools, tctx, trace, mode, said_in):
        """ "Skip backwards", "stop Spotify": the playback command runs from code, then "Done." (in her
        language). Returns None if it failed: the result is then in `messages` for a normal answer."""
        call = ToolCall("music", {"command": command})
        result = self._run_tool(call, tools, tctx, trace)
        if result is None:
            return None
        assistant_msg = {"role": "assistant", "content": "", "tool_calls": [call.to_message()]}
        messages.append(assistant_msg)
        self.convs.add_message(cid, "assistant", "", model_content="", tool_calls=assistant_msg["tool_calls"],
                               mode=mode)  # fmt: skip
        yield {"type": "tool", "name": call.name, "args": call.arguments, "ok": result.ok}
        lang = LANG_NAMES.get(said_in or guess_language(text), "English")
        messages.append({"role": "tool", "content": result.content + REPLY_LANGUAGE_NOTE.format(lang=lang)})
        self.convs.add_message(cid, "tool", "", model_content=result.content)
        if not result.ok:
            return None
        reply = MUSIC_DONE_EXAMPLE.get(lang, MUSIC_DONE_EXAMPLE["English"]) + "."
        trace.mark("first_token")
        yield {"type": "text", "text": reply}
        return reply

    def _add_todos(self, text, cid, messages, tools, tctx, trace, stop, mode, said_in):
        """Puts what Lea asked to remember into Reminders. Yields the turn's events; returns the reply
        ("Got it.") when everything was saved, or None to answer normally (no call came, or a call
        failed: the results are then in `messages`, so she can say what went wrong).

        Nothing the model writes here is shown: measured 2026-11-10, its text was "Got it. I'll add a
        reminder…" plus a <doc> block and "anything else?", which Lea asked four times not to hear."""
        attempt = list(messages)
        for _ in range(2):
            sp = StreamSplitter(thinking=False)
            with trace.stage("llm"):
                for chunk in self.llm.stream(attempt, tools=tools, thinking=False, stop=stop):
                    if chunk.done:
                        if chunk.stats:
                            st = chunk.stats
                            trace.llm_rounds.append({"prompt": st.prompt_tokens, "cached": st.cached_tokens,
                                                     "prefill_ms": round(st.prefill_s * 1000),
                                                     "ttft_ms": round(st.time_to_first_token * 1000),
                                                     "generated": st.generation_tokens,
                                                     "tok_s": round(st.generation_tps, 1)})  # fmt: skip
                        break
                    sp.feed(chunk.text)
                sp.flush()
            if stop.is_set():
                return None
            calls = [c for c in sp.tool_calls if c.name == "create_reminder"]
            if calls:
                break
            attempt = attempt + [
                {"role": "assistant", "content": sp.visible_text.strip()},
                {"role": "user", "content": TODO_NUDGE},
            ]
        else:
            log.warning("to-do request, but no create_reminder call came")
            return None
        yield {"type": "status", "text": phrase("checking", text)}
        assistant_msg = {"role": "assistant", "content": "", "tool_calls": [c.to_message() for c in calls]}
        messages.append(assistant_msg)
        self.convs.add_message(cid, "assistant", "", model_content="", tool_calls=assistant_msg["tool_calls"],
                               mode=mode)  # fmt: skip
        all_ok = True
        lang = LANG_NAMES.get(said_in or guess_language(text), "English")
        for call in calls:
            if call.arguments.get("due"):
                call.arguments["due"] = fix_due_day(
                    text, str(call.arguments["due"]), local_now(self.cfg).date()
                )
            result = self._run_tool(call, tools, tctx, trace)
            if result is None:
                all_ok = False
                content = f"Error: {validate(call, tools) or 'Invalid tool call.'}"
            else:
                all_ok = all_ok and result.ok
                content = result.content
                yield {"type": "tool", "name": call.name, "args": call.arguments, "ok": result.ok}
            messages.append({"role": "tool", "content": content + REPLY_LANGUAGE_NOTE.format(lang=lang)})
            self.convs.add_message(cid, "tool", "", model_content=content)
        if not all_ok:
            return None
        reply = GOT_IT.get(lang, GOT_IT["English"])
        trace.mark("first_token")
        yield {"type": "text", "text": reply}
        return reply

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
    def told_todos(self, days: int = 45, limit: int = 15) -> list[str]:
        """Things Lea asked Sylvie to note in a chat whose turn saved no reminder (before T54, or when
        Reminders failed): "- [2026-11-10] her words". Oldest first."""
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.db.query(
            "SELECT id, conversation_id, role, content, tool_calls, created_at FROM messages "
            "WHERE created_at >= ? AND role IN ('user', 'assistant') ORDER BY id",
            (since,),
        )
        open_todo: dict[str, dict] = {}  # conversation → its last to-do message, until a reminder is saved
        found: list[dict] = []
        for r in rows:
            cid = r["conversation_id"]
            if r["role"] == "user":
                if cid in open_todo:
                    found.append(open_todo.pop(cid))
                if todo_request(r["content"] or "") == "add":
                    open_todo[cid] = r
            elif cid in open_todo and "create_reminder" in (r["tool_calls"] or ""):
                open_todo.pop(cid)
        found += open_todo.values()
        found.sort(key=lambda r: r["id"])
        return [
            f"- [{r['created_at'][:10]}] {' '.join((r['content'] or '').split())[:220]}"
            for r in found[-limit:]
        ]

    def learn_from_claude(self, message: str, source: str, stop: threading.Event) -> None:
        """One of Lea's own messages to Claude (memory/claude_history.py). Facts and interests
        are learned as usual; a rule about answers is saved switched off, for Lea to approve."""
        detect_preference(_Stoppable(self.llm, stop), self.prefs, message, source="claude")
        if not stop.is_set():
            self._learn(message, "", source, stop, note=CLAUDE_NOTE)

    # ---------- sleep-time learning (efficiency plan A5) ----------
    def _set_learned_upto(self, mid: int) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (LEARNED_UPTO, str(int(mid))),
        )

    def unlearned(self) -> list[dict]:
        """Lea's messages not learned from yet, grouped by conversation in order:
        [{"cid", "items": [(message_id, text, reply)]}]."""
        row = self.db.one("SELECT value FROM meta WHERE key = ?", (LEARNED_UPTO,))
        upto = int(row["value"]) if row else 0
        rows = self.db.query(
            "SELECT id, conversation_id, role, content FROM messages WHERE id > ? AND role IN ('user', 'assistant') "
            "ORDER BY id",
            (upto,),
        )
        groups: dict[str, list] = {}
        for r in rows:
            items = groups.setdefault(r["conversation_id"], [])
            if r["role"] == "user":
                items.append([r["id"], r["content"] or "", ""])
            elif items and r["content"] and not items[-1][2]:
                items[-1][2] = r["content"]  # the visible answer to that message
        return [{"cid": c, "items": [tuple(i) for i in items]} for c, items in groups.items() if items]

    def sleep_learn(self, stop: threading.Event) -> None:
        """Learn from every conversation that has gone quiet: one model call per conversation (in parts
        of ~3,000 characters) instead of one per message. A message where Lea says "remember…" is read
        on its own, so only it can save a sensitive fact (rule 13). A long conversation is summarized
        now, while she's away, so it isn't summarized in the middle of her next question."""
        before = self.current_system_prompt()
        changed = False
        for group in self.unlearned():
            cid, items = group["cid"], group["items"]
            for part in learning_parts(items):
                if stop.is_set():
                    return  # nothing marked learned: the next quiet spell does it again
                changed |= self._learn_items(cid, part, stop)
            if stop.is_set():
                return
            self._set_learned_upto(max(i[0] for i in items))
            max_ctx = self.cfg.get_path("llm.summarize_after_tokens", 11000)
            maybe_summarize(_Stoppable(self.llm, stop), self.convs, cid, int(max_ctx * 0.6), stop=stop)
        if changed and not stop.is_set() and self.current_system_prompt() != before:
            # The next new conversation will start with this prompt: read it into the cache now (once).
            self.llm.warm(self.current_system_prompt(), available_tools(self.tool_context()))

    def _learn_items(self, cid: str, items: list, stop: threading.Event) -> bool:
        message, reply, note = join_for_learning(items)
        return self._learn(message, reply, f"{cid}#{items[0][0]}", stop, note=note, warm=False)

    def _learn(
        self, message: str, reply: str, source: str, stop: threading.Event, note: str = "", warm: bool = True
    ) -> bool:
        """Returns True if anything was learned."""
        if len(message.strip()) < 4:
            return False
        before = self.current_system_prompt() if warm else ""
        existing = recall(self.db, self.embedder, message[:2000], k=8)
        ex = run_extraction(_Stoppable(self.small, stop), message, reply, existing, note=note)
        if stop.is_set():
            return False  # interrupted: the job is re-queued and will run again
        if source.startswith("claude:"):
            # "Lea needs Sylvie to retest test 3…": a task for the coding assistant, not a fact about her.
            ex.facts = [f for f in ex.facts if not ABOUT_ASSISTANT_RE.search(f["fact"])]
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
        if warm and not stop.is_set():
            after = self.current_system_prompt()
            if after != before:
                # The next new conversation will start with this prompt: read it into the cache now.
                self.llm.warm(after, available_tools(self.tool_context()))
        return bool(ex.facts or ex.interests or ex.interests_removed or ex.style)


LEARNED_UPTO = "learning.upto"  # meta: the last of Lea's message ids already learned from
LEARN_BATCH_CHARS = 3000  # Lea's messages read in one extraction call
BATCH_NOTE = (
    "This time <message> holds several of Lea's messages from one conversation, numbered [1], [2]…, "
    "and <reply> Sylvie's answers with the same numbers. Learn from all of them together."
)


def join_for_learning(items: list) -> tuple[str, str, str]:
    """[(message_id, text, reply)] → (message, reply, note) for one extraction call."""
    if len(items) == 1:
        return items[0][1], items[0][2], ""
    message = "\n".join(f"[{n}] {text}" for n, (_, text, _r) in enumerate(items, 1))
    reply = "\n".join(f"[{n}] {r[:300]}" for n, (_, _t, r) in enumerate(items, 1) if r)
    return message, reply, BATCH_NOTE


def learning_parts(items: list) -> list[list]:
    """Explicit "remember…" messages alone (only they may save a sensitive fact), the rest in parts."""
    explicit = [i for i in items if REMEMBER_RE.search(i[1])]
    rest = [i for i in items if i not in explicit and len(i[1].strip()) >= 4]
    return [[i] for i in explicit] + _parts(rest, LEARN_BATCH_CHARS)


def _parts(items: list, limit: int) -> list[list]:
    out, cur, size = [], [], 0
    for it in items:
        if cur and size + len(it[1]) > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(it[1])
    return out + ([cur] if cur else [])


class _Stoppable:
    """Passes a stop event into llm.complete() for background jobs."""

    def __init__(self, llm, stop):
        self.llm, self.stop = llm, stop

    def complete(self, messages, **kw):
        return self.llm.complete(messages, stop=self.stop, **kw)

    def __getattr__(self, name):  # count_tokens etc.
        return getattr(self.llm, name)


def _title_from(text: str) -> str:
    words = text.strip().split()
    title = " ".join(words[:7])
    return (title[:60] + "…") if len(title) > 60 or len(words) > 7 else (title or "New chat")


def _json(d) -> str:
    import json

    return json.dumps(d)
