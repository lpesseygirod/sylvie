"""Read back + clear "yes" before anything that sends, deletes, buys or can't be undone (rule 7).

The decision is made by code, not by the model: only Lea's own next message, matching a clear
yes, approves. A yes said by voice also needs the Mac to be unlocked (so someone else in the room
can't make Sylvie act by voice while Lea is away); a typed yes is enough.
"""

from __future__ import annotations

import re
import subprocess

# A clear yes: a short reply made only of yes-words (and fillers like "please", "Sylvie"), in any of
# Lea's languages. Lea, 2026-11-06: "a yes from me should be enough", so "Sylvie, yes", "Yes please,
# go ahead" and "Sí, claro, hazlo" all count. Any "no / but / wait / change" makes it not a yes.
_YES = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "go", "ahead", "do", "send",
    "run", "proceed", "absolutely", "course", "definitely", "fine", "perfect", "great", "right", "correct",
    "sí", "si", "vale", "dale", "claro", "hazlo", "adelante", "confirmo", "venga", "perfecto", "envíalo",
    "envialo", "envía", "envia", "ejecuta", "ejecútalo", "ejecutalo", "supuesto", "correcto", "bueno",
    "oui", "ouais", "vas-y", "vas", "y", "envoie", "envoie-le", "d'accord", "confirme", "parfait", "bien",
    "exactement", "lance", "lance-le",
}  # fmt: skip
_FILLER = {
    "please", "sylvie", "it", "that", "now", "por", "favor", "lo", "eso", "ya", "ahora", "que", "the", "of",
    "s'il", "te", "plaît", "plait", "stp", "maintenant", "a", "an", "and", "y", "e", "et", "it's", "is",
}  # fmt: skip
_NOT = {
    "no", "not", "don't", "dont", "wait", "but", "change", "stop", "cancel", "nope", "never", "instead",
    "espera", "pero", "cambia", "cancela", "para", "nunca", "mejor", "non", "pas", "attends", "mais",
    "annule", "jamais",
}  # fmt: skip


def is_clear_yes(text: str) -> bool:
    words = re.findall(r"[\w'-]+", text.lower())
    if not words or len(words) > 8 or any(w in _NOT for w in words):
        return False
    core = [w for w in words if w in _YES]
    return bool(core) and all(w in _YES or w in _FILLER for w in words)


# The whole message must be the undo command ("Sylvie, undo that", "deshaz eso", "annule ça").
UNDO_RE = re.compile(
    r"^\W*(sylvie\W+)?(undo( that| it)?|take (that|it) back|deshaz(lo| eso)?|deshacer|an[uú]lalo|"
    r"annule(r)?( [çc]a)?|d[ée]fais( [çc]a)?)(\W+(please|por favor|stp|sylvie))?\W*$",
    re.I,
)


def is_undo(text: str) -> bool:
    return bool(UNDO_RE.match(text.strip()))


def screen_is_locked() -> bool:
    """True when the Mac's screen is locked (or the lid is closed). Uses ioreg; no permissions needed."""
    try:
        out = subprocess.run(
            # Full path: the background service (launchd) has no /usr/sbin in its PATH, so plain "ioreg"
            # failed there and the safe fallback said "locked" every time (Lea, 2026-11-06).
            ["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return True  # if we can't tell, be safe
    # IOConsoleLocked is macOS's own "the screen is locked" flag. (The older check also treated
    # "not on console" as locked, which refused Lea's yes while she was at the Mac.)
    return bool(
        re.search(r"<key>IOConsoleLocked</key>\s*<true/>", out)
        or re.search(r"<key>CGSSessionScreenIsLocked</key>\s*<true/>", out)
    )
