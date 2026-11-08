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
    r"volume|volumen|louder|quieter|turn (it )?(up|down)|sube|s[uú]bele|baja|b[aá]jale|monte|baisse|"
    r"calendar|event|meeting|appointment|remind|reminder|timer|alarm|open|"
    r"calendario|agenda|evento|cita|reuni[oó]n|recu[eé]rdame|recordatorio|temporizador|alarma|abre|"
    r"rendez-vous|rappelle|rappel|minuteur|ouvre)\b",
    re.I,
)

# "I'm putting it on", "Done!", "He añadido…", "Je lance…": she says it happened.
# 2026-11-08: "Ya está la música puesta" and "Subo el volumen" slipped through (nothing played).
CLAIM_RE = re.compile(
    r"\b(i'?m (putting|playing|starting|adding|opening|setting)|i'?ve (put|added|set|started|opened|created|scheduled)|"
    r"(it'?s|is) (playing|added|done|set)|playing (it|your|now)|done[.!]|"
    r"(it'?s|music is) on\b|turn(ed|ing) (it )?(up|down)|^\W*(now )?playing\b|\bplaying\b.{0,80}\bnow\b|"
    r"\b(est[aá] )?sonando\b|"
    r"(te )?(pongo|estoy poniendo|he puesto|he a[nñ]adido|he creado|a[nñ]adido|listo[.!]|hecho[.!])|"
    r"ya est[aá]|est[aá] (puest[ao]|sonando)|ya suena|(lo )?(subo|bajo)|he (subido|bajado)|"
    r"je (lance|mets|joue|monte|baisse|l'ai ajout|l'ai mis|t'ai ajout)|j'ai (mont|baiss)|c'est parti|"
    r"c'est fait|ajout[ée]|c'est lanc[ée])",
    re.I,
)

ACTION_NOTE = (
    "Lea may be asking you to do something (music, calendar, reminders, a timer, opening an app). If so, "
    "do it by calling the matching tool: nothing happens unless you call it. Never say it's done or "
    "happening unless the tool call succeeded in this turn; if a tool fails, say so plainly. Never invent "
    "results a tool didn't give you (playlists, songs, events, files). For music, don't ask Lea which song "
    "or artist: pick something yourself from what you know she likes (her interests, earlier requests, "
    "saved playlists) or the mood she describes, play it with play_music (it finds and starts anything by "
    "name; no Spotify key is needed any more, whatever earlier conversations said), and say in a few words "
    "what you put on. If she corrects you, just play what she says. Never ask her to click anything herself."
)

CLAIM_NUDGE = (
    "(Check: you said you did it, but you didn't call any tool, so nothing happened. Call the right tool "
    "now. If you can't, tell Lea plainly that you couldn't do it and why.)"
)


def looks_like_action(text: str) -> bool:
    return bool(ACTION_RE.search(text))


def claims_done(text: str) -> bool:
    return bool(CLAIM_RE.search(text))


# Essays, reports, literature reviews, bibliographies: academic writing from real sources (T42).
WRITING_RE = re.compile(
    r"\b(essays?|dissertations?|literature review|research paper|term paper|thesis|theses|"
    r"bibliograph\w*|citations?|references|works cited|apa|mla|chicago style|harvard style|"
    r"ensayos?|monograf[ií]as?|tesis|tesina|trabajo (escrito|de investigaci[oó]n|final)|revisi[oó]n bibliogr[aá]fica|"
    r"citas|referencias|m[ée]moire|revue de (la )?litt[ée]rature|r[ée]f[ée]rences)\b",
    re.I,
)

WRITING_NOTE = (
    "This is academic writing. Build it from real sources: search zotero_library (Lea's own saved sources) "
    "first, then find_papers if you need more. Use only what the passages and abstracts actually say. Cite "
    "in the text exactly as each result says ('Cite in text as'), and end with a References list made ONLY "
    "of the entries the tools gave you for the works you cited (every one of them), copied exactly, in one "
    "style (APA unless Lea asked for another). "
    "Never cite a source from memory without check_reference; never invent authors, years, pages or "
    "quotes. If the sources don't cover a point, say so or leave the claim uncited and cautious. Put the "
    "WHOLE essay, References list included, inside ONE <doc> block and write it only once: START your reply "
    "with the <doc title=…> tag (no text before it, the essay is never repeated in the chat), and after "
    "</doc> say one or two sentences at most (which sources you used, anything missing)."
)


def looks_like_writing(text: str) -> bool:
    return bool(WRITING_RE.search(text))
