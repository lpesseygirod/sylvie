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
from core.lang import guess_language, phrase
from core.persona import build_system_prompt, build_turn_context, wrap_user_message
from core.router import route
from core.toolcall import StreamSplitter, ToolCall
from core.trace import Trace
from knowledge.docs_index import DocsIndex
from lea_profile.interests import InterestStore
from lea_profile.notice import confirm_question, notice
from lea_profile.preferences import PreferenceStore, detect_preference
from lea_profile.style import StyleStore
from lea_profile.summary import profile_text
from memory.extract import run_extraction
from memory.forget import forget
from memory.recall import recall
from memory.store import MemoryStore
from tools.registry import ToolContext, available_tools, execute, validate

log = logging.getLogger("sylvie")

MAX_TOOL_ROUNDS = 4
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


class BackgroundWorker:
    """Runs learning jobs when Lea isn't mid-conversation. A new turn interrupts the current
    job (it's re-queued), so background work never slows Sylvie's answers down for long."""

    def __init__(self, idle_seconds: float = 8.0):
        self.q: queue.Queue[_Job] = queue.Queue()
        self.idle_seconds = idle_seconds
        self.last_activity = 0.0
        self.busy = threading.Event()
        self.current: _Job | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="sylvie-bg")
        self._thread.start()

    def submit(self, name: str, fn) -> None:
        self.q.put(_Job(name, fn))

    def turn_started(self) -> None:
        self.busy.set()
        self.last_activity = time.time()
        if self.current is not None:
            self.current.stop.set()

    def turn_finished(self) -> None:
        self.last_activity = time.time()
        self.busy.clear()

    def drain(self, timeout: float = 120) -> None:
        """Tests/CLI: wait until the queue is empty."""
        end = time.time() + timeout
        while (not self.q.empty() or self.current is not None) and time.time() < end:
            time.sleep(0.05)

    def _run(self) -> None:
        while True:
            job = self.q.get()
            while self.busy.is_set() or time.time() - self.last_activity < self.idle_seconds:
                time.sleep(0.2)
            job.stop.clear()
            self.current = job
            try:
                job.fn(job.stop)
                if job.stop.is_set() and job.tries < 2:
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
        self.bg = BackgroundWorker(idle_seconds=idle_seconds)
        self._stops: dict[str, threading.Event] = {}
        self._stt = None

    @classmethod
    def build(cls, cfg: Config | None = None) -> Sylvie:
        from core.llm import LLM
        from knowledge.embeddings import Embedder

        cfg = cfg or load_config()
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
        )

    def speech_to_text(self):
        if self._stt is None:
            from voice.stt import SpeechToText

            self._stt = SpeechToText(
                self.cfg.get_path("voice.stt_model", "mlx-community/whisper-large-v3-turbo")
            )
        return self._stt

    def warm(self) -> None:
        """Load models and pre-read the system prompt so the first answer is fast."""
        self.llm.load()
        self.embedder.embed_one("warm up")
        self.llm.warm(self.current_system_prompt(), available_tools(self.tool_context()))
        if self.cfg.get_path("voice.enabled", True):
            import numpy as np

            stt = self.speech_to_text()
            stt.load()
            # One throwaway transcription compiles Whisper's GPU kernels (first run is ~4 s).
            stt.transcribe(np.sin(np.linspace(0, 2000, 16000)).astype(np.float32) * 0.1, language="en")

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
    ) -> Iterator[dict]:
        trace = Trace()
        self.bg.turn_started()
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

            notes: list[str] = []
            if channel in ("voice", "phone"):
                notes.append(
                    "Lea is talking to you by voice. Answer in the SAME language as her message below "
                    f"(it sounds like {LANG_NAMES.get(guess_language(text), 'English')}). Start with a short "
                    "spoken answer: 1-3 plain sentences, no markdown, no lists. If details help, add them "
                    "after a blank line: they are shown on screen, not read aloud."
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
                if not conv.get("aside_used") and self.cfg.get_path("proactivity.mode") != "off":
                    aside = notice(self.interests, text)
                    if aside is None:
                        q = confirm_question(self.interests)
                        if q:
                            self.interests.mark_asked(q[0])
                            aside = q[1]
                    if aside:
                        self.convs.set(cid, aside_used=1)
            context = build_turn_context(self.cfg, memories=[f.text for f in facts] + notes, aside=aside)

            # 4. Attachments (dropped files) are untrusted data
            model_text = text
            for a in attachments or []:
                model_text += (
                    f'\n\n<untrusted source="file {a.name}">\n{a.text[:MAX_ATTACHMENT_CHARS]}\n</untrusted>'
                )
            user_model = wrap_user_message(model_text, context)
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
            for round_no in range(MAX_TOOL_ROUNDS + 1):
                # Tools are dropped on the last round, or after a second malformed call (retry once).
                use_tools = tools if round_no < MAX_TOOL_ROUNDS and invalid_calls < 2 else None
                sp = StreamSplitter(thinking=r.thinking)
                with trace.stage("llm"):
                    for chunk in self.llm.stream(
                        messages, tools=use_tools, thinking=r.thinking, effort=r.effort, stop=stop
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
                                trace.mark("first_token")
                            yield {"type": kind, "text": s}
                        if sp.in_tool and not announced:
                            # Say "let me check" the moment a tool call starts, not after it's written.
                            announced = True
                            trace.mark("first_status")
                            yield {"type": "status", "text": phrase("checking", text)}
                    for kind, s in sp.flush():
                        yield {"type": kind, "text": s}
                visible = sp.visible_text.strip()
                calls = sp.tool_calls if use_tools and not stop.is_set() else []
                if not calls:
                    display_parts.append(visible)
                    final_mid = self.convs.add_message(
                        cid,
                        "assistant",
                        "\n\n".join(p for p in display_parts if p),
                        model_content=visible,
                        sources=tctx.sources or None,
                        trace=trace.to_dict(),
                        mode=mode,
                        channel=channel,
                    )
                    break
                # Tool round
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
                for call in calls:
                    result = self._run_tool(call, tools, tctx, trace)
                    if result is None:  # invalid call
                        invalid_calls += 1
                        err = validate(call, tools) or "Invalid tool call format."
                        content = f"Error: {err} Please fix the call."
                        if invalid_calls >= 2:
                            content = f"Error: {err} Answer without tools now."
                    else:
                        content = result.content
                        yield {"type": "tool", "name": call.name, "args": call.arguments, "ok": result.ok}
                        if result.pending:
                            yield {"type": "action", "status": "pending", **result.pending}
                        if result.sources:
                            yield {"type": "sources", "sources": tctx.sources}
                    messages.append({"role": "tool", "content": content})
                    self.convs.add_message(cid, "tool", "", model_content=content)

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

    def _run_tool(self, call: ToolCall, tools, tctx, trace):
        if call.name == "__invalid__" or validate(call, tools):
            return None
        with trace.stage(f"tool:{call.name}"):
            return execute(call, tctx)

    # ---------- background learning ----------
    def _learn(self, message: str, reply: str, source: str, stop: threading.Event) -> None:
        if len(message.strip()) < 4:
            return
        before = self.current_system_prompt()
        existing = recall(self.db, self.embedder, message, k=8)
        ex = run_extraction(_Stoppable(self.llm, stop), message, reply, existing)
        if stop.is_set():
            return  # interrupted: the job is re-queued and will run again
        for f in ex.facts:
            self.memory.add(
                f["fact"], key=f["key"], category=f["category"], source=source, sensitive=f["sensitive"]
            )
        for topic in ex.interests:
            self.interests.bump(topic)
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
