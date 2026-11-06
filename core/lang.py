"""Very small language guesser for EN/ES/FR (for short fixed phrases like "let me check").
The model itself handles languages in answers; this is only for Sylvie's own canned lines."""

from __future__ import annotations

import re

_WORDS = {
    "es": set(
        "el la los las que qué de y es por para con una un cómo como dónde cuándo hola puedes está esta "
        "mi me lo hay pero muy sí gracias".split()
    ),
    "fr": set(
        "le la les des est et que qui une un pour avec pas je tu vous comment où quand bonjour merci c'est "
        "mon ma mes ce cette dans sur oui".split()
    ),
    "en": set(
        "the is and what how where when you can please my to of a an in it this that do does hi hello "
        "thanks with for are i".split()
    ),
}

PHRASES = {
    "checking": {"en": "Let me check…", "es": "Déjame mirar…", "fr": "Je regarde…"},
    "thinking": {"en": "Let me think…", "es": "Déjame pensar…", "fr": "Je réfléchis…"},
}


def guess_language(text: str, default: str = "en") -> str:
    words = re.findall(r"[\w']+", text.lower())
    if not words:
        return default
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in _WORDS.items()}
    if re.search(r"[ñ¿¡]", text):
        scores["es"] += 2
    # ô â î û ë ï œ never appear in Spanish (é does, so it doesn't count). Measured: "Un hexagone a
    # 6 côtés." was guessed as Spanish ("un", "a" are Spanish words too).
    if re.search(r"[çèêàùôâîûëïœ]|\b(qu'|j'|l'|d')", text.lower()):
        scores["fr"] += 2
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else default


def phrase(key: str, text: str) -> str:
    return PHRASES[key][guess_language(text)]
