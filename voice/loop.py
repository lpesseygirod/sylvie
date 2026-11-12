"""A voice turn on the server: audio → transcript → normal turn → sentences to speak.

The device that plays the audio (Mac app, iPhone) speaks the `speak` events with its own
system voice as they arrive, so the first sentence is heard while the rest is still being
written.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from collections.abc import Iterator

from core.lang import PHRASES, guess_language
from voice.speech import DocFilter, SentenceStreamer

# When an answer takes a while, say a little sentence so she knows Sylvie is on it.
FIRST_FILLER_S = 2.5  # nothing spoken yet after this → "One moment…"
STILL_WORKING_S = 12.0  # then, while still quiet → "Still on it…" (at most twice)
_DONE = object()

# "stop listening" = stop talking AND stop listening, completely (the app mutes the mic);
# a bare "stop" = stop talking and listen for the next order right away. No model needed for either.
STOP_LISTENING_RE = re.compile(
    r"\b(stop|quit) listening\b|\b(deja|dejas|para|parar|dejar) de (escuchar|o[ií]r)(me)?\b|"
    r"\barr[eê]te[rz]? d'?\s?[ée]couter\b",
    re.I,
)
BARE_STOP_RE = re.compile(
    r"^\W*((hey|oye)\W+)?(sylvie\W*)?(stop|para|basta|arr[eê]te|c[aá]llate|tais-toi|cállate ya)"
    r"(\W+(sylvie|please|por favor|ya|s'il te pla[iî]t))*\W*$",
    re.I,
)


def stop_command(text: str) -> str | None:
    """'mute' for "stop listening", 'listen' for a bare "stop", else None."""
    if STOP_LISTENING_RE.search(text):
        return "mute"
    if BARE_STOP_RE.match(text):
        return "listen"
    return None


def with_fillers(events: Iterator[dict], lang: str, first_after: float = FIRST_FILLER_S,
                 again_after: float = STILL_WORKING_S) -> Iterator[dict]:  # fmt: skip
    """Passes `events` through; adds a short spoken filler whenever Sylvie has been silent too long."""
    q: queue.Queue = queue.Queue()

    def pump():
        try:
            for ev in events:
                q.put(ev)
        except Exception as e:  # surfaced to the caller below
            q.put(e)
        q.put(_DONE)

    threading.Thread(target=pump, daemon=True, name="sylvie-voice-turn").start()
    last_spoken, said_first, stills = time.monotonic(), False, 0
    while True:
        wait = (again_after if said_first else first_after) - (time.monotonic() - last_spoken)
        try:
            ev = q.get(timeout=max(wait, 0.01))
        except queue.Empty:
            if said_first and stills >= 2:
                last_spoken = time.monotonic()
                continue
            key = "still" if said_first else "moment"
            stills += said_first
            said_first = True
            last_spoken = time.monotonic()
            yield {"type": "speak", "text": PHRASES[key].get(lang, PHRASES[key]["en"]), "language": lang,
                   "filler": True}  # fmt: skip
            continue
        if ev is _DONE:
            return
        if isinstance(ev, Exception):
            raise ev
        if ev["type"] == "speak":
            last_spoken, said_first = time.monotonic(), True
        yield ev


def voice_turn(
    sylvie, wav: bytes, conversation_id: str | None = None, channel: str = "voice"
) -> Iterator[dict]:
    # Pause background learning *before* Whisper runs: otherwise it shares the GPU with a background
    # job and the transcript took 55 s (measured 2026-11-06, while Sylvie was learning from Lea's
    # Claude history). sylvie.turn() below keeps it paused until the answer is done.
    sylvie.bg.turn_started()
    sylvie.bg.interrupt(timeout=5)  # Whisper gets the GPU to itself
    with_stt = sylvie.speech_to_text()
    try:
        tr = with_stt.transcribe(wav)
    except Exception:
        sylvie.bg.turn_finished()
        raise
    yield {"type": "transcript", "text": tr.text, "language": tr.language, "ms": round(tr.ms)}
    if not tr.text:
        sylvie.bg.turn_finished()
        yield {"type": "done", "message_id": None, "conversation_id": conversation_id, "empty": True}
        return
    lang = tr.language
    if action := stop_command(tr.text):
        sylvie.bg.turn_finished()
        yield {"type": "control", "action": action}
        yield {"type": "done", "message_id": None, "conversation_id": conversation_id, "empty": True}
        return
    yield from with_fillers(_answer(sylvie, tr.text, lang, conversation_id, channel), lang)


def _answer(sylvie, text: str, lang: str, conversation_id: str | None, channel: str) -> Iterator[dict]:
    full = bool(sylvie.cfg.get_path("voice.speak_full_answer", True))
    speaker = SentenceStreamer(full=full)
    docs = DocFilter()  # documents Sylvie writes go to the screen, not the speaker
    for ev in sylvie.turn(text, conversation_id=conversation_id, channel=channel, language=lang):
        if ev["type"] == "status" and ev.get("text"):
            yield ev
            yield {"type": "speak", "text": ev["text"], "language": lang, "filler": True}
            continue
        if ev["type"] == "done":
            # The last sentence goes out before "done", so clients know all speech has arrived.
            for sentence in speaker.feed(docs.flush()) + speaker.flush():
                yield {"type": "speak", "text": sentence, "language": guess_language(sentence, default=lang)}
            yield ev
            continue
        yield ev
        if ev["type"] == "text":
            for sentence in speaker.feed(docs.feed(ev["text"])):
                yield {"type": "speak", "text": sentence, "language": guess_language(sentence, default=lang)}
        elif ev["type"] == "tool":
            speaker = SentenceStreamer(
                full=full
            )  # text before a tool call was a filler; speak the real answer fresh
