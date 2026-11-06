"""Instant replies to greetings, thanks, "how are you" and goodbyes: no model, no thinking.

Lea asked (2026-11-06) that "hello" gets an answer right away. Only a message that is *nothing but*
one of these phrases (in English, Spanish or French, optionally with "Sylvie") is answered here;
anything more ("hi, what's the weather?") goes to the model as usual. The replies are editable in
config.yaml → quick_replies; they're conversation strings like the wake greeting, never training data.
"""

from __future__ import annotations

import random
import re
import unicodedata

# phrase (normalized: lowercase, no accents or punctuation) → (intent, language)
PHRASES: dict[str, tuple[str, str]] = {}


def _add(intent: str, lang: str, *phrases: str) -> None:
    for p in phrases:
        PHRASES[p] = (intent, lang)


_add("hello", "en", "hi", "hello", "hey", "hey there", "hi there", "hello there", "yo")
_add("hello", "es", "hola", "holi", "buenas", "hola hola")
_add("hello", "fr", "bonjour", "salut", "coucou", "allo")
_add("good_morning", "en", "good morning", "morning")
_add("good_morning", "es", "buenos dias", "buen dia")
_add("good_afternoon", "en", "good afternoon")
_add("good_afternoon", "es", "buenas tardes")
_add("good_evening", "en", "good evening")
_add("good_evening", "fr", "bonsoir")
_add("good_night", "en", "good night", "goodnight", "night night")
_add("good_night", "es", "buenas noches")
_add("good_night", "fr", "bonne nuit")
_add("thanks", "en", "thanks", "thank you", "thanks a lot", "thank you so much", "thx", "cheers")
_add("thanks", "es", "gracias", "muchas gracias", "mil gracias")
_add("thanks", "fr", "merci", "merci beaucoup", "merci bien")
_add("bye", "en", "bye", "bye bye", "goodbye", "see you", "see you later", "talk later", "later")
_add("bye", "es", "adios", "hasta luego", "chao", "chau", "hasta manana", "nos vemos")
_add("bye", "fr", "au revoir", "a plus", "a bientot", "a demain")
_add("how_are_you", "en", "how are you", "how are you doing", "hows it going", "how is it going")
_add("how_are_you", "es", "que tal", "como estas", "que tal estas", "como vas")
_add("how_are_you", "fr", "ca va", "comment ca va", "comment vas tu", "comment tu vas")

DEFAULT_REPLIES = {
    "hello": {
        "en": ["Hi {name}! What can I do for you?", "Hey {name}, what's up?"],
        "es": ["¡Hola, {name}! ¿Qué necesitas?", "Hola {name}, dime."],
        "fr": ["Salut {name} ! Qu'est-ce que je peux faire pour toi ?", "Bonjour {name}, je t'écoute."],
    },
    "good_morning": {
        "en": ["Good morning, {name}! What's first today?"],
        "es": ["¡Buenos días, {name}! ¿Qué necesitas?"],
    },
    "good_afternoon": {
        "en": ["Good afternoon, {name}! What can I do for you?"],
        "es": ["¡Buenas tardes, {name}! Dime."],
    },
    "good_evening": {
        "en": ["Good evening, {name}! What do you need?"],
        "fr": ["Bonsoir {name} ! Je t'écoute."],
    },
    "good_night": {
        "en": ["Good night, {name}. Sleep well."],
        "es": ["Buenas noches, {name}. Que descanses."],
        "fr": ["Bonne nuit, {name}. Dors bien."],
    },
    "thanks": {
        "en": ["You're welcome!", "Anytime."],
        "es": ["¡De nada!", "Para eso estoy."],
        "fr": ["Avec plaisir !", "De rien !"],
    },
    "bye": {
        "en": ["Bye, {name}! Talk soon."],
        "es": ["¡Hasta luego, {name}!"],
        "fr": ["À plus tard, {name} !"],
    },
    "how_are_you": {
        "en": ["All good here, thanks! And you?"],
        "es": ["¡Todo bien, gracias! ¿Y tú?"],
        "fr": ["Ça va bien, merci ! Et toi ?"],
    },
}

_GREETING_WORDS = ("hi", "hello", "hey", "hola", "salut", "bonjour", "coucou")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\b(sylvie|silvie|sylvia|silvia|sylvi)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match(text: str) -> tuple[str, str] | None:
    """(intent, language) if the whole message is a greeting, thanks, goodbye or "how are you"."""
    t = normalize(text)
    if not t or len(t.split()) > 6:
        return None
    if t in PHRASES:
        return PHRASES[t]
    # "hi, how are you" / "hola, ¿qué tal?" → how_are_you; "hi Lea" is still just hello
    first, _, rest = t.partition(" ")
    if first in _GREETING_WORDS and rest in PHRASES:
        return PHRASES[rest]
    return None


def quick_reply(text: str, cfg, rng: random.Random | None = None) -> str | None:
    if not cfg.get_path("quick_replies.enabled", True):
        return None
    m = match(text)
    if m is None:
        return None
    intent, lang = m
    replies = {**DEFAULT_REPLIES.get(intent, {}), **(cfg.get_path(f"quick_replies.{intent}", {}) or {})}
    options = replies.get(lang) or replies.get("en")
    if not options:
        return None
    name = cfg.get_path("user.name", "Lea")
    return (rng or random).choice(options).format(name=name)


# Answers that open with "Hola Lea." / "Hello Lea!" when Lea didn't greet (the 9B does it constantly).
_OPENING = re.compile(
    r"^\s*[¡!]?\s*(hola|hello|hi|hey|bonjour|salut|coucou|buenas|good (morning|afternoon|evening)|"
    r"buenos d[ií]as|buenas (tardes|noches)|bonsoir)(\s*,?\s*(lea|sylvie))?\s*[.!,]+\s*",
    re.I,
)


def greets(text: str) -> bool:
    """Lea's message opens with a greeting ("Hi Sylvie, …"): then greeting back is fine."""
    return bool(_OPENING.match(text)) or match(text) is not None


class GreetingStripper:
    """Drops a greeting at the very start of an answer while streaming (it waits for the first ~40
    characters or the first sentence end, then passes everything through unchanged)."""

    def __init__(self, active: bool):
        self.active, self.done, self.buf, self.removed = active, not active, "", ""

    def _cut(self, text: str) -> str:
        out = text
        for _ in range(2):  # "Hello Lea. Good afternoon." → both
            m = _OPENING.match(out)
            if not m or not out[m.end() :].strip():
                break  # never remove the whole answer
            out = out[m.end() :]
        if out != text:
            self.removed = text[: len(text) - len(out)]
            out = out[:1].upper() + out[1:]
        return out

    def feed(self, chunk: str) -> str:
        if self.done:
            return chunk
        self.buf += chunk
        if len(self.buf) < 60 and len(re.findall(r"[.!?\n]\s*\S", self.buf)) < 2:
            return ""  # ~0.2 s of text: enough to see "Hello Lea. Good afternoon." as a whole
        self.done = True
        return self._cut(self.buf)

    def flush(self) -> str:
        if self.done:
            return ""
        self.done = True
        return self._cut(self.buf)

    def clean(self, text: str) -> str:
        """The same cut on the saved answer."""
        if self.removed and text.startswith(self.removed):
            rest = text[len(self.removed) :]
            return rest[:1].upper() + rest[1:]
        return text
