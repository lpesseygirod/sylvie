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


def test_voice_answer_follows_the_language_whisper_heard(tmp_path):
    """2026-11-08: "Sylvie, stop listening." in a Spanish conversation got a Spanish answer."""
    from core.lang import reply_language
    from tests.test_orchestrator import extraction, make
    from voice.loop import voice_turn
    from voice.stt import Transcript

    assert reply_language("Sylvie, stop listening.", heard="en") == "en"
    assert reply_language("Sylvie, what is the weather like?", heard="es") == "en"  # clear words win
    assert reply_language("Sylvie, stop listening.") is None  # typed, no telling words: can't tell
    assert reply_language("Gracias Sylvie") == "es"  # one telling word, nothing else
    assert reply_language("Mets un minuteur de 5 minutes.") == "fr"  # was answered in Spanish (un, de)
    assert reply_language("Sylvie, pon Bloodline please") is None  # mixed: she may mix too
    s = make(tmp_path, ["Okay, I stopped.", extraction()])

    class STT:
        def transcribe(self, wav):
            return Transcript("Sylvie, Coldplay?", "en", 1.5, 90.0)  # no telling words: the audio decides

    s._stt = STT()
    list(voice_turn(s, b"RIFF"))
    prompt = s.llm.calls[0]["messages"][-1]["content"]
    assert prompt.endswith("(Reply in English.)") and "it sounds like English" in prompt


def test_stop_and_stop_listening_need_no_model(tmp_path):
    """Saying "stop listening" stops her completely; "stop" cuts her and listens again."""
    from tests.test_orchestrator import make
    from voice.loop import stop_command, voice_turn
    from voice.stt import Transcript

    for text in [
        "Sylvie, stop listening.",
        "Deja de escuchar.",
        "Sylvie, para de escucharme",
        "Arrête d'écouter.",
    ]:
        assert stop_command(text) == "mute", text
    for text in ["Stop.", "Sylvie, stop.", "Para, Sylvie.", "Basta ya.", "Arrête !"]:
        assert stop_command(text) == "listen", text
    for text in ["Stop the music and play jazz", "Para mañana, ¿qué tengo?", "Is the bus stop near?"]:
        assert stop_command(text) is None, text
    s = make(tmp_path, [])

    class STT:
        def transcribe(self, wav):
            return Transcript("Sylvie, stop listening.", "en", 1.5, 90.0)

    s._stt = STT()
    ev = list(voice_turn(s, b"RIFF"))
    assert {"type": "control", "action": "mute"} in ev and ev[-1]["type"] == "done" and not s.llm.calls


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


# ---------- documents are shown, not spoken (<doc> blocks, design §2.1 work panel) ----------
def test_doc_blocks_are_not_spoken():
    from voice.speech import DocFilter, strip_docs

    text = 'Here it is.\n<doc title="TVM cheat sheet">\n## FV\n`FV = PV(1+r)^n`\n</doc>\nWant examples?'
    assert (
        "FV" not in strip_docs(text)
        and "Here it is." in strip_docs(text)
        and "Want examples?" in strip_docs(text)
    )
    assert strip_docs("Sure. <doc title='x'>never closed") == "Sure.  "
    f = DocFilter()
    out = (
        "".join(f.feed(text[i : i + 3]) for i in range(0, len(text), 3)) + f.flush()
    )  # tags split across chunks
    assert out.replace("\n", " ").split() == "Here it is. Want examples?".split()
    f = DocFilter()
    assert f.feed("I like <b>bold</b> and a < b") + f.flush() == "I like <b>bold</b> and a < b"


def test_voice_turn_skips_documents(tmp_path):
    from tests.test_orchestrator import extraction, make
    from voice.loop import voice_turn

    s = make(tmp_path, ['Done.\n<doc title="Plan">\nStep one. Step two.\n</doc>', extraction()])
    s.speech_to_text = lambda: type(
        "STT",
        (),
        {
            "transcribe": lambda self, wav: type(
                "T", (), {"text": "make me a plan", "language": "en", "ms": 1.0}
            )()
        },
    )()
    spoken = [e["text"] for e in voice_turn(s, b"RIFF") if e["type"] == "speak"]
    assert spoken == ["Done."]


def test_background_learning_pauses_before_transcription(tmp_path):
    """Whisper must not share the GPU with a background job (measured: 55 s for a transcript)."""
    from tests.test_orchestrator import make
    from voice.loop import voice_turn
    from voice.stt import Transcript

    s = make(tmp_path, [])
    seen = {}

    class STT:
        def transcribe(self, wav):
            seen["busy"] = s.bg.busy.is_set()
            return Transcript("", "en", 0.2, 0.0)

    s._stt = STT()
    list(voice_turn(s, b""))
    assert seen["busy"] is True  # background work was told to stop before Whisper ran
    assert not s.bg.busy.is_set()  # and resumes afterwards (empty transcript: no turn follows)


def test_fillers_when_sylvie_is_quiet_too_long():
    import time

    from voice.loop import with_fillers

    def slow():
        time.sleep(0.25)
        yield {"type": "status", "mode": "fast"}
        time.sleep(0.5)
        yield {"type": "speak", "text": "Here it is.", "language": "es"}
        yield {"type": "done"}

    ev = list(with_fillers(slow(), "es", first_after=0.1, again_after=0.3))
    spoken = [e["text"] for e in ev if e["type"] == "speak"]
    assert spoken[0] == "Un momento…" and "Sigo con ello…" in spoken and spoken[-1] == "Here it is."
    assert ev[-1]["type"] == "done"


def test_no_filler_when_the_answer_is_quick():
    from voice.loop import with_fillers

    def quick():
        yield {"type": "speak", "text": "Paris.", "language": "en"}
        yield {"type": "done"}

    ev = list(with_fillers(iter(quick()), "en", first_after=1.0, again_after=1.0))
    assert [e["text"] for e in ev if e["type"] == "speak"] == ["Paris."]


def test_errors_inside_the_turn_still_reach_the_caller():
    import pytest

    from voice.loop import with_fillers

    def broken():
        yield {"type": "status"}
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        list(with_fillers(broken(), "en", first_after=1.0))


def test_full_answers_are_spoken_entirely_except_code_and_tables():
    st = SentenceStreamer(full=True)
    text = (
        "Primera frase. Segunda frase.\n\nTercera, en otro párrafo. Cuarta. Quinta. Sexta.\n\n"
        "```python\nprint('hola')\n```\n\n| a | b |\n|---|---|\n\nY la última frase."
    )
    out = []
    for i in range(0, len(text), 5):
        out += st.feed(text[i : i + 5])
    out += st.flush()
    assert out == [
        "Primera frase.", "Segunda frase.", "Tercera, en otro párrafo.", "Cuarta.", "Quinta.", "Sexta.",
        "Y la última frase.",
    ]  # fmt: skip


def test_lea_rules_in_spanish_are_noticed():
    from lea_profile.preferences import maybe_preference

    assert maybe_preference(
        "A menos que yo te diga stop, quiero la respuesta completa. No quiero que te cortes."
    )
    assert maybe_preference("Tienes que hablarme más despacio")
    assert maybe_preference("Je veux que tu répondes en français")
    assert maybe_preference("The rule is: never cut your answer")


def test_music_and_silence_transcripts_are_dropped():
    """2026-11-08: 30 s of ducked music or silence became "Sylvie. Merci." / "Sous-titrage ST' 501"."""
    from voice.stt import is_noise_transcript

    assert is_noise_transcript("Sous-titrage ST' 501 So, boy.", 31)
    assert is_noise_transcript("Sous-titres réalisés par la communauté d'Amara.org", 4)
    assert is_noise_transcript("Sylvie. Merci.", 31.6)
    assert is_noise_transcript("Sylvie. Thank you.", 31.6)
    assert is_noise_transcript("Sylvie.", 5)
    assert not is_noise_transcript("Sylvie, merci.", 2.5)  # a real, quick thanks still gets an answer
    assert not is_noise_transcript("Sylvie, pon música.", 9.3)
    assert not is_noise_transcript("Thank you for the summary, can you shorten it?", 20)


def test_only_the_made_up_part_is_dropped():
    from voice.stt import strip_noise

    assert (
        strip_noise("Sylvie, stop Spotify, por favor. ¡Suscríbete al canal!")
        == "Sylvie, stop Spotify, por favor."
    )
    assert strip_noise("Sous-titrage ST' 501 So, boy.") == ""
    assert strip_noise("Sylvie, pon música.") == "Sylvie, pon música."


def test_a_spanish_start_to_an_english_question_is_redone(tmp_path):
    """2026-11-08: "Sylvie, can you put Bridgerton on Netflix please?" after Spanish turns → Spanish answer,
    although "(Reply in English.)" was there. The start is held, checked, and redone before anyone hears it."""
    from tests.test_orchestrator import extraction, make, run

    s = make(tmp_path, [
        "¡Claro! Te pongo la música que quieras.", extraction(),
        "Sí, puedo intentar reproducir Bridgerton en Netflix ahora mismo.", "Sure! Bridgerton is starting on Netflix.",
        extraction(),
    ])  # fmt: skip
    cid = run(s, "Hola Sylvie, ¿qué tal estás hoy?")[0]["conversation_id"]
    s.bg.drain()
    ev = run(s, "Sylvie, can you put Bridgerton on Netflix please?", cid)
    shown = "".join(e["text"] for e in ev if e["type"] == "text")
    assert shown == "Sure! Bridgerton is starting on Netflix." and "Sí" not in shown
    assert "Answer ONLY in English" in s.llm.calls[-2]["messages"][-1]["content"]


def test_language_guard_passes_the_right_language_through():
    from core.lang import LanguageGuard

    g = LanguageGuard("en")
    assert g.feed("Sure, ") == ("", False)  # held: too short to tell
    out, wrong = g.feed("here is what I found for you today. And more")
    assert not wrong and out.startswith("Sure, here is")
    assert g.feed(" text") == (" text", False)  # decided once: no more holding
