"""A voice turn on the server: audio → transcript → normal turn → sentences to speak.

The device that plays the audio (Mac app, iPhone) speaks the `speak` events with its own
system voice as they arrive, so the first sentence is heard while the rest is still being
written.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.lang import guess_language
from voice.speech import SentenceStreamer


def voice_turn(
    sylvie, wav: bytes, conversation_id: str | None = None, channel: str = "voice"
) -> Iterator[dict]:
    with_stt = sylvie.speech_to_text()
    tr = with_stt.transcribe(wav)
    yield {"type": "transcript", "text": tr.text, "language": tr.language, "ms": round(tr.ms)}
    if not tr.text:
        yield {"type": "done", "message_id": None, "conversation_id": conversation_id, "empty": True}
        return
    lang = tr.language
    speaker = SentenceStreamer()
    for ev in sylvie.turn(tr.text, conversation_id=conversation_id, channel=channel):
        if ev["type"] == "status" and ev.get("text"):
            yield ev
            yield {"type": "speak", "text": ev["text"], "language": lang, "filler": True}
            continue
        yield ev
        if ev["type"] == "text":
            for sentence in speaker.feed(ev["text"]):
                yield {"type": "speak", "text": sentence, "language": guess_language(sentence, default=lang)}
        elif ev["type"] == "tool":
            speaker = SentenceStreamer()  # text before a tool call was a filler; speak the real answer fresh
        elif ev["type"] == "done":
            for sentence in speaker.flush():
                yield {"type": "speak", "text": sentence, "language": guess_language(sentence, default=lang)}
