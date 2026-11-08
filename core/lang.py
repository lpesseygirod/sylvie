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
    "moment": {"en": "One moment…", "es": "Un momento…", "fr": "Un instant…"},
    "claude": {"en": "I'm asking Claude…", "es": "Se lo pregunto a Claude…", "fr": "Je demande à Claude…"},
    "lost": {
        "en": "Sorry, I got lost in what I found and don't have a good answer. Could you ask me again, a bit more specifically?",
        "es": "Perdona, me he perdido entre lo que encontré y no tengo una buena respuesta. ¿Me lo preguntas otra vez, un poco más concreto?",
        "fr": "Désolée, je me suis perdue dans ce que j'ai trouvé et je n'ai pas de bonne réponse. Tu peux me reposer la question, un peu plus précisément ?",
    },
    "research": {
        "en": "I'm splitting this into parts and looking them up at the same time. It takes a little while.",
        "es": "Lo divido en partes y las busco a la vez. Tardo un poquito.",
        "fr": "Je découpe ça en parties et je les cherche en même temps. Ça prend un petit moment.",
    },
    "still": {"en": "Still on it…", "es": "Sigo con ello…", "fr": "J'y suis toujours…"},
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


def clear_language(text: str) -> str | None:
    """The language of `text` when it's clear (2+ telling words and a clear lead), else None.
    Mixed or very short messages get None, so she may mix the way Lea does (persona)."""
    words = re.findall(r"[\w']+", text.lower())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in _WORDS.items()}
    ranked = sorted(scores.values(), reverse=True)
    best = max(scores, key=scores.get)
    if ranked[0] >= 2 and ranked[0] >= 2 * ranked[1] and best == guess_language(text):
        return best
    return None


def phrase(key: str, text: str) -> str:
    return PHRASES[key][guess_language(text)]
