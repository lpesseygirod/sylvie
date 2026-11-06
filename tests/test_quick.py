import random

from core.config import Config
from core.quick import match, quick_reply
from tests.test_orchestrator import make, run

CFG = Config({"user": {"name": "Lea"}})


def test_whole_message_greetings_in_three_languages():
    assert match("Hello!") == ("hello", "en")
    assert match("Sylvie, hola") == ("hello", "es")
    assert match("¡Buenos días!") == ("good_morning", "es")
    assert match("Bonsoir Sylvie") == ("good_evening", "fr")
    assert match("merci beaucoup") == ("thanks", "fr")
    assert match("Hi, how are you?") == ("how_are_you", "en")
    assert match("hola, ¿qué tal?") == ("how_are_you", "es")
    assert match("ça va ?") == ("how_are_you", "fr")
    assert match("Hasta mañana") == ("bye", "es")


def test_anything_more_goes_to_the_model():
    assert match("hi, what's the weather tomorrow?") is None
    assert match("thanks, can you also book it") is None
    assert match("hello world program in python") is None
    assert match("") is None


def test_reply_uses_name_language_and_config():
    assert quick_reply("hola", CFG, random.Random(0)) in ("¡Hola, Lea! ¿Qué necesitas?", "Hola Lea, dime.")
    cfg = Config({"user": {"name": "Lea"}, "quick_replies": {"thanks": {"en": ["No problem, {name}."]}}})
    assert quick_reply("thank you", cfg) == "No problem, Lea."
    assert quick_reply("hello", Config({"quick_replies": {"enabled": False}})) is None


def test_turn_answers_instantly_without_the_model(tmp_path):
    s = make(tmp_path, [])
    ev = run(s, "Hola Sylvie")
    assert [e["text"] for e in ev if e["type"] == "text"][0].startswith(("¡Hola", "Hola"))
    assert ev[-1]["type"] == "done" and ev[-1]["message_id"]
    assert s.llm.calls == []  # no model call at all
    msgs = s.convs.messages(ev[0]["conversation_id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_greeting_plus_question_still_uses_the_model(tmp_path):
    s = make(tmp_path, ["Mañana sol.", "{}"])
    run(s, "hola, ¿qué tiempo hará mañana?")
    assert s.llm.calls


def test_greeting_openers_are_dropped_when_lea_did_not_greet():
    from core.quick import GreetingStripper

    def run_through(text, active=True):
        g = GreetingStripper(active)
        out = "".join(g.feed(text[i : i + 4]) for i in range(0, len(text), 4)) + g.flush()
        return out, g.clean(text)

    assert (
        run_through("Hola Lea. Mañana tienes clase a las diez.") == ("Mañana tienes clase a las diez.",) * 2
    )
    assert run_through("Hello Lea. Good afternoon. The capital is Canberra.")[0] == "The capital is Canberra."
    assert run_through("Hola, Lea. ¿Qué tal?", active=False)[0] == "Hola, Lea. ¿Qué tal?"  # she greeted
    assert run_through("Hola Lea.")[0] == "Hola Lea."  # never removes the whole answer
    assert run_through("Holanda está en Europa.")[0] == "Holanda está en Europa."


def test_turn_answer_has_no_greeting_opener(tmp_path):
    s = make(tmp_path, ["¡Hola Lea! Mañana hace sol en Madrid.", "{}"])
    ev = run(s, "¿Qué tiempo hará mañana en Madrid?")
    text = "".join(e["text"] for e in ev if e["type"] == "text")
    assert text == "Mañana hace sol en Madrid."
    saved = [m for m in s.convs.messages(ev[0]["conversation_id"]) if m["role"] == "assistant"][-1]
    assert saved["content"] == "Mañana hace sol en Madrid."
