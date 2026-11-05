import io
import wave

import numpy as np

from voice.speech import SentenceStreamer, clean_for_speech
from voice.stt import wav_to_float32


def stream(text, step=3):
    s = SentenceStreamer()
    out = []
    for i in range(0, len(text), step):
        out += s.feed(text[i : i + step])
    return out + s.flush()


def test_sentences_come_out_as_they_complete():
    s = SentenceStreamer()
    assert s.feed("Hello Lea! How are") == ["Hello Lea!"]
    assert s.feed(" you today? I'm") == ["How are you today?"]
    assert s.flush() == ["I'm"]


def test_numbers_and_abbreviations_dont_split():
    assert stream("It costs 3.50 euros, e.g. for a coffee. Nice.") == [
        "It costs 3.50 euros, e.g. for a coffee.",
        "Nice.",
    ]


def test_only_first_paragraph_is_spoken_and_markdown_removed():
    text = "It's **12.90 €** [1]. That's cheap.\n\nHere is the breakdown:\n\n| a | b |\n|---|---|"
    assert stream(text) == ["It's 12.90 €.", "That's cheap."]


def test_code_is_not_spoken():
    assert stream("```python\nprint(1)\n```\n\nThat prints one.") == ["That prints one."]
    assert clean_for_speech("Use `npv()` see [docs](https://x.y/z)") == "Use npv() see docs"


def test_max_sentences():
    assert len(stream("One. Two. Three. Four. Five. Six.")) == 4


def test_wav_decoding_and_resampling():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.ones((4800, 2)) * 16384).astype("<i2").tobytes())
    audio = wav_to_float32(buf.getvalue())
    assert audio.dtype == np.float32 and len(audio) == 1600 and abs(audio.mean() - 0.5) < 1e-3


def test_voice_turn_streams_speak_events(tmp_path):
    from tests.test_orchestrator import extraction, make
    from voice.loop import voice_turn
    from voice.stt import Transcript

    s = make(
        tmp_path, ["Mañana hará sol en Madrid. Unos 18 grados.\n\n| hora | temp |\n|---|---|", extraction()]
    )

    class FakeSTT:
        def transcribe(self, wav):
            return Transcript("¿Qué tiempo hará mañana?", "es", 2.0, 120.0)

    s._stt = FakeSTT()
    ev = list(voice_turn(s, b"RIFF..."))
    assert ev[0] == {"type": "transcript", "text": "¿Qué tiempo hará mañana?", "language": "es", "ms": 120}
    spoken = [e["text"] for e in ev if e["type"] == "speak"]
    assert spoken == ["Mañana hará sol en Madrid.", "Unos 18 grados."]
    assert ev[-1]["type"] == "done"  # all speech arrives before "done"
    assert "by voice" in s.llm.calls[0]["messages"][-1]["content"]


def test_voice_turn_with_silence(tmp_path):
    from tests.test_orchestrator import make
    from voice.loop import voice_turn
    from voice.stt import Transcript

    s = make(tmp_path, [])

    class Silent:
        def transcribe(self, wav):
            return Transcript("", "en", 0.2, 0.0)

    s._stt = Silent()
    ev = list(voice_turn(s, b""))
    assert [e["type"] for e in ev] == ["transcript", "done"] and not s.llm.calls
