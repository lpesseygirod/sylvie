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
    r"rendez-vous|rappelle|rappel|minuteur|ouvre|"
    # 2026-11-08: series, messages, calls, directions, notes. Specific phrases only: "ver", "películas" or
    # "text" alone are questions too ("¿qué películas hay hoy?" must still be looked up, not acted on).
    r"netflix|youtube|prime video|disney\+?|hbo|movistar|"
    r"pon(me)? (la serie|la peli(cula)?|el capítulo)|put on (the )?(series|show|movie|film)|mets(-moi)? (la s[ée]rie|le film)|"
    r"(send|m[aá]nda(le)?|env[ií]a(le)?|envoie(-lui)?) (a |an |un |una )?(message|mensaje|text|whatsapp|sms)|"
    r"escr[ií]be(le)? a|dile a|text (my|her|him)|[ée]cris [àa]|"
    r"call (my|her|him|mom|mum|dad)|ll[aá]ma(le)? a|appelle|facetime|"
    r"directions|ruta (a|para|hasta)|c[oó]mo (llego|voy) a|how do i get to|navigate to|itin[ée]raire|"
    r"(make|write|take|create) a note|apunta|(crea|haz|escribe) una nota|prends note)\b",
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
    "what you put on. If she corrects you, just play what she says. Never ask her to click anything herself. "
    "Only if her request is really unclear, ask ONE short question instead of acting."
)

# Appended to a successful music tool result (Lea, 2026-11-08: "just say done", no questions after a song).
# 2026-11-08 (make apps): with a concrete example song ("La Reina, by Maluma") she copied it when the
# result named no song. Now: the opening word only, and the song only if the result names it.
MUSIC_DONE_NOTE = (
    '\n(Done. Answer Lea in one short sentence in {lang}, starting "{example}", and name the song only if '
    "the result above names it. Don't ask her anything and don't offer anything else.)"
)
MUSIC_DONE_EXAMPLE = {"English": "Done", "Spanish": "Listo", "French": "C'est fait"}
# After a tool result the 9B drifted back to the conversation's earlier language (2026-11-08: asked in
# English, answered in Spanish), so every tool result ends with the language of her last message.
REPLY_LANGUAGE_NOTE = "\n(Answer Lea in {lang}, the language of her last message.)"

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


# Questions only the internet can answer. Measured 2026-11-08 by voice: "dime qué tiempo hará mañana en
# Toledo" and "dime qué películas hay hoy en televisión" got "No tengo acceso a servicios de clima en
# tiempo real…" although web_search was there. Earlier refusals she remembered made it worse.
LIVE_RE = re.compile(
    r"\b(weather|forecast|rain(ing)?|temperature|news|headlines?|tonight|today'?s|right now|latest|"
    r"score|results?|price|costs?|open(ing)? hours|schedule|on tv|tv guide|cinema|movies? (on|playing)|"
    r"tiempo (hace|hará|hara|va a hacer)|qu[eé] tiempo|clima|lluvia|llover|temperatura|pron[oó]stico|"
    r"noticias|hoy|esta noche|ahora mismo|[uú]ltim[ao]s?|resultados?|marcador|precio|cu[aá]nto cuesta|"
    r"horarios?|televisi[oó]n|tele\b|cartelera|pel[ií]culas?|partido|"
    r"m[ée]t[ée]o|quel temps|pluie|actualit[ée]s|ce soir|aujourd'hui|prix|combien co[uû]te|horaires?|"
    r"programme t[ée]l[ée]|films?|"
    r"what time (does|do|is) .{0,40}(open|close)|(is|are) .{0,30} open (today|now|on)|"
    r"a qu[eé] hora (abre|cierra|empieza|juega)|cierra|est[aá] abierto|"
    r"[àa] quelle heure .{0,40}(ouvre|ferme)|ferme|ouvert|"
    # Lea asking for a look-up or for figures (2026-11-08: "búscame lo de los datos de la UE", "dame los datos")
    r"b[uú]sca(me|lo|la)?|b[uú]scame|search( for)?|look (it |that |this )?up|google|find out|cherche|recherche|"
    r"datos|estad[ií]sticas?|cifras|data|statistics|figures|donn[ée]es|chiffres)\b",
    re.I,
)

LIVE_NOTE = (
    "Lea's question may need current information (weather, news, what's on, prices, opening hours, results). "
    "You CAN look things up: call web_search first, with a generic query (the place and the day, never her "
    "name), and answer from what you find. For the weather, search in English ('<town> weather forecast "
    "tomorrow'): those pages give the clearest numbers. Answer in her language anyway. Never tell her you "
    "have no access to real-time information."
)

# "I don't have access to real-time data", "No tengo acceso…", "Je n'ai pas accès…": she gave up without looking.
NO_ACCESS_RE = re.compile(
    r"(i (don'?t|do not|can'?t|cannot) (have )?(access|look up|check|browse|get)|"
    r"(no|without) (access|connection) to (the )?(internet|real[- ]time|live|current)|"
    r"\breal[- ]time\b|check (a|an|the) (trusted |reliable |local )?(weather |news )?(app|website|site|service)|"
    r"no tengo acceso|no puedo (acceder|consultar|buscar|ver|darte (el|la|los|las) (tiempo|informaci))|"
    r"en tiempo real|te recomiendo consultar|consulta (una|la|el) (app|aplicaci[oó]n|web|p[aá]gina)|"
    r"je n'ai pas acc[eè]s|je ne peux pas (consulter|acc[eé]der|chercher)|en temps r[ée]el)",
    re.I,
)

# "Estoy buscando los datos…", "Let me look that up.", "Je cherche…": she says she's searching, but
# nothing runs and the answer ends there. Measured 2026-11-08: three turns in a row, never a search.
SEARCH_PROMISE_RE = re.compile(
    r"\b(estoy buscando|voy a buscar|(te |lo |los |la |las )?busco( ahora| ya)?\b|d[eé]jame (buscar|mirar|consultar)|"
    r"(ahora |ya )?(mismo )?lo (miro|consulto)|"
    r"i'?m (searching|looking (it |that |this )?up|looking for|checking|pulling up)|let me (search|look|check|find)|"
    r"i'?ll (search|look|check|find)|one moment while i|"
    r"je (cherche|vais chercher|regarde|v[ée]rifie)|laisse-moi (chercher|regarder|v[ée]rifier))",
    re.I,
)

SEARCH_PROMISE_NUDGE = (
    "(Check: you said you're looking it up, but you didn't call any tool, so nothing is being searched and "
    "Lea is left waiting. Call web_search now and answer her from what you find.)"
)

# Sylvie offered to look something up ("¿Quieres que busque…?") and Lea said yes.
OFFER_SEARCH_RE = re.compile(
    r"(quieres|te gustar[ií]a|prefieres) que (te )?(busque|mire|consulte)|"
    r"(shall|should|do you want me to|want me to|would you like me to) (i )?(search|look|check|find)|"
    r"(veux|voudrais)-tu que je (cherche|regarde)",
    re.I,
)
YES_RE = re.compile(
    r"^\W*(sylvie\W*)?(s[ií]|yes|yeah|yep|vale|ok(ay)?|claro|dale|venga|porfa(vor)?|por favor|please|sure|"
    r"oui|d'accord|bien s[uû]r)\b",
    re.I,
)

SEARCH_NUDGE = (
    "(Check: you said you can't get that, but you have web_search and didn't use it. Call web_search now "
    "with a generic query and answer Lea from what you find. Only if the search finds nothing, say so.)"
)


def looks_live(text: str) -> bool:
    return bool(LIVE_RE.search(text))


def promises_search(text: str) -> bool:
    return bool(SEARCH_PROMISE_RE.search(text))


def accepts_search_offer(text: str, previous_answer: str) -> bool:
    return bool(previous_answer and OFFER_SEARCH_RE.search(previous_answer) and YES_RE.search(text))


def says_no_access(text: str) -> bool:
    return bool(NO_ACCESS_RE.search(text))
