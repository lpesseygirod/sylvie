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


# ---------- Kokoro (voice/g2p.py, voice/tts.py) ----------
def test_english_phonemes_map_to_kokoro_set():
    from voice.g2p import map_english, map_other

    assert map_english("hˈe^ɪ ðɚ") == "hˈA ðəɹ"  # diphthong letters, r-colored schwa
    assert map_english("bˈʌɾɚ") == "bˈʌTəɹ"  # American flap
    assert map_other("t^ʃˈe-ɾo") == "ʧˈeɾo"


def test_long_phoneme_strings_are_cut_at_pauses():
    from voice.tts import chunk_phonemes

    ps = ("abc def, " * 80).strip()
    parts = chunk_phonemes(ps, limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert all(p.endswith(",") for p in parts[:-1])
    assert "".join(parts).replace(" ", "") == ps.replace(" ", "")


def test_wav_output_is_16_bit_mono():
    from voice.tts import to_wav

    data = to_wav(np.array([0.0, 0.5, -2.0], dtype=np.float32), 24000)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 3)


class FakeTTS:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, language="en"):
        from voice.tts import Speech, to_wav

        self.calls.append((text, language))
        return Speech(to_wav(np.zeros(10, dtype=np.float32), 24000), 0.0, 1.0)


def test_speak_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from core.server import create_app
    from tests.test_orchestrator import make
    from tests.test_server import AUTH, BASE, TOKEN

    s = make(tmp_path, [])
    s.cfg["voice"]["tts"] = "kokoro"
    s._tts = FakeTTS()
    with TestClient(create_app(sylvie=s, token=TOKEN, warm=False), base_url=BASE) as c:
        assert c.post("/api/voice/speak", json={"text": "Hola", "language": "es"}).status_code == 401
        r = c.post("/api/voice/speak", json={"text": "Hola Lea.", "language": "es"}, headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
        assert s._tts.calls == [("Hola Lea.", "es")]
        assert c.post("/api/voice/speak", json={"text": ""}, headers=AUTH).status_code == 400
        s.cfg["voice"]["tts"] = "system"  # Lea switched back: the app uses its own voice
        assert c.post("/api/voice/speak", json={"text": "Hi"}, headers=AUTH).status_code == 404
