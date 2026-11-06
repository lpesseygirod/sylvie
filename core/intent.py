"""Spotting requests to *do* something, and answers that claim it was done.

Measured 2026-11-06 with the 9B: "Sylvie, can you put my playlist ERA?" got "I'm putting your ERA
playlist on for you now." with no tool call at all, so nothing happened. Two safeguards use this:
a reminder before she answers, and a check after: claiming an action without the tool call sends
her back to actually call it (or say she couldn't).
"""

from __future__ import annotations

import re

ACTION_RE = re.compile(
    r"\b(play|put on|put (my|some|the)|pause|next song|skip|music|playlist|song|spotify|"
    r"pon(me)?|ponme|reproduce|m[uú]sica|canci[oó]n|lista|pausa|siguiente|"
    r"mets|joue|musique|chanson|"
    r"calendar|event|meeting|appointment|remind|reminder|timer|alarm|open|"
    r"calendario|agenda|evento|cita|reuni[oó]n|recu[eé]rdame|recordatorio|temporizador|alarma|abre|"
    r"rendez-vous|rappelle|rappel|minuteur|ouvre)\b",
    re.I,
)

# "I'm putting it on", "Done!", "He añadido…", "Je lance…": she says it happened.
CLAIM_RE = re.compile(
    r"\b(i'?m (putting|playing|starting|adding|opening|setting)|i'?ve (put|added|set|started|opened|created|scheduled)|"
    r"(it'?s|is) (playing|added|done|set)|playing (it|your|now)|done[.!]|"
    r"(te )?(pongo|estoy poniendo|he puesto|he a[nñ]adido|he creado|a[nñ]adido|listo[.!]|hecho[.!])|"
    r"je (lance|mets|joue|l'ai ajout|l'ai mis|t'ai ajout)|c'est fait|ajout[ée]|c'est lanc[ée])",
    re.I,
)

ACTION_NOTE = (
    "Lea may be asking you to do something (music, calendar, reminders, a timer, opening an app). If so, "
    "do it by calling the matching tool: nothing happens unless you call it. Never say it's done or "
    "happening unless the tool call succeeded in this turn; if a tool fails, say so plainly. Never invent "
    "results a tool didn't give you (playlists, songs, events, files)."
)

CLAIM_NUDGE = (
    "(Check: you said you did it, but you didn't call any tool, so nothing happened. Call the right tool "
    "now. If you can't, tell Lea plainly that you couldn't do it and why.)"
)


def looks_like_action(text: str) -> bool:
    return bool(ACTION_RE.search(text))


def claims_done(text: str) -> bool:
    return bool(CLAIM_RE.search(text))
