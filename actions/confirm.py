"""Read back + clear "yes" before anything that sends, deletes, buys or can't be undone (rule 7).

The decision is made by code, not by the model: only Lea's own next message, matching a clear
yes, approves. Sending also needs the Mac to be unlocked (so someone else in the room can't
make Sylvie send an email by voice).
"""

from __future__ import annotations

import re
import subprocess

_YES_WORD = (
    r"(yes|yeah|yep|yup|sure|ok(ay)?|do it|send it|go ahead|confirm(ed)?|s[ií]|vale|dale|claro|env[ií]a(lo)?|"
    r"hazlo|adelante|confirmo|oui|ouais|vas-y|envoie(-le)?|d'accord|confirme)"
)
# The whole message must be a yes (one or two yes-words, optionally "please"/"Sylvie").
# "yes but change the subject" is NOT a clear yes.
YES_RE = re.compile(
    rf"^\s*{_YES_WORD}([\s,!.]+{_YES_WORD})?[\s,.!]*(please|por favor|s'il te pla[iî]t|stp|sylvie)?[\s.!]*$",
    re.I,
)


# The whole message must be the undo command ("Sylvie, undo that", "deshaz eso", "annule ça").
UNDO_RE = re.compile(
    r"^\W*(sylvie\W+)?(undo( that| it)?|take (that|it) back|deshaz(lo| eso)?|deshacer|an[uú]lalo|"
    r"annule(r)?( [çc]a)?|d[ée]fais( [çc]a)?)(\W+(please|por favor|stp|sylvie))?\W*$",
    re.I,
)


def is_undo(text: str) -> bool:
    return bool(UNDO_RE.match(text.strip()))


def is_clear_yes(text: str) -> bool:
    return bool(YES_RE.match(text.strip()))


def screen_is_locked() -> bool:
    """True when the Mac's screen is locked (or the lid is closed). Uses ioreg; no permissions needed."""
    try:
        out = subprocess.run(
            ["ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return True  # if we can't tell, be safe
    locked = re.search(r"<key>CGSSessionScreenIsLocked</key>\s*<true/>", out)
    off_console = re.search(
        r"<key>kCGSSessionOnConsoleKey</key>\s*<false/>", out
    )  # e.g. another user, lid closed
    return bool(locked or off_console)
