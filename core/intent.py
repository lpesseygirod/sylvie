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
    r"c'est fait|ajout[ée]|c'est lanc[ée]|"
    # 2026-11-10: "Got it. I'll add a reminder…", "I'll keep a note…", "Te lo apunto": a promise, nothing saved
    r"i'?ll (add|note|keep|put|save|set|remind|remember|make a note)|i'?ve noted|noted[.!]|"
    r"(te )?lo (apunto|anoto|guardo)|(te lo )?recordar[eé]|je (le )?note|je te le rappellerai)",
    re.I,
)

ACTION_NOTE = (
    "Lea may be asking you to do something (music, a series on Netflix, a message, a call, directions in "
    "Maps, a note, calendar, reminders, a timer, opening an app). If so, "
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


# Her OWN existing essay ("resume mi ensayo", "what does my essay say") is reading, not writing: measured
# 2026-11-08 (make apps), the writing note sent her to Zotero instead of opening the document.
OWN_DOC_RE = re.compile(
    r"\b(my|mi|mis|mon|ma|mes)\s+(\w+\s+){0,2}(essays?|ensayos?|tesis|thesis|trabajo|m[ée]moire|dissertation)\b",
    re.I,
)
READ_VERB_RE = re.compile(
    r"\b(resume|res[uú]meme|summari[sz]e|what does|qu[eé] dice|lee|read|r[ée]sume|que dit)\b",
    re.I,
)


def looks_like_writing(text: str) -> bool:
    if OWN_DOC_RE.search(text) and READ_VERB_RE.search(text):
        return False
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


# "I sent the message to Ana", "Ya he enviado el mensaje": measured 2026-11-08 (make apps) after the
# send had failed (WhatsApp not installed) or was still waiting for Lea's yes.
SENT_CLAIM_RE = re.compile(
    r"\b(i'?ve sent|i sent|(it'?s|message|email) (been )?sent|he enviado|ya (est[aá] )?enviad[oa]|"
    r"(mensaje|correo|email) enviad[oa]|lo he mandado|ya se lo he mandado|j'ai envoy[ée]|(message|mail) envoy[ée])",
    re.I,
)

UNSENT_NUDGE = (
    "(Check: nothing was sent. The tool said: {why} Tell Lea plainly, in her language, that it was NOT "
    "sent and why; if it's waiting for her yes, read it back and ask.)"
)


def claims_sent(text: str) -> bool:
    return bool(SENT_CLAIM_RE.search(text))


# ---------- Her to-do list (Apple Reminders) ----------
# Measured 2026-11-10 by voice: eight "remind me to…" / "add that I need to…" requests in a row got
# "Got it, I'll add a reminder…" (often with a <doc> block and "anything else?") and no tool call, so
# nothing was saved; "remind me of the things I need to do" was answered from the open chat only.
# Lea asked four times for just "Got it". Now code decides: a to-do goes into Reminders and the
# answer is a fixed "Got it"; "what do I need to do?" reads Reminders before she answers.
_NOT_A_QUESTION = r"(?!\s+(of |about )?(what|who|whom|where|when|why|how|which|if|whether|the things|everything|all the things)\b)"

TODO_LIST_RE = re.compile(
    r"\b((remind|tell) me (of |about )?(the things|what|everything|all the things) (that )?i (need|have|got) to do|"
    r"what (do|did|else do) i (still )?(need|have) to do|"
    r"what(?:'s| is| do i have)( still)? (on|in) my (to-?do|todo|reminders?|list)|"
    r"(read|show|tell|give) me my (to-?do|todo|reminders|tasks|list)|"
    r"qu[eé] (tengo|me queda) (que hacer|pendiente|en (mi|la) lista)|"
    r"(recu[eé]rdame|dime|l[eé]eme) (lo que|las cosas que|todo lo que|qu[eé]) (tengo|me queda) (que hacer|pendiente)|"
    r"(dime|l[eé]eme) (mis|los) (recordatorios|tareas|pendientes)|"
    r"qu'est-ce que (je dois faire|j'ai [àa] faire)|rappelle-moi ce que (je dois|j'ai [àa]) faire|"
    r"(lis|dis)-moi (ma liste|mes rappels))",
    re.I,
)

TODO_ADD_RE = re.compile(
    r"\b(remind me" + _NOT_A_QUESTION + r"|remember me (that|to|about)|"
    r"(give|set|add|create|make|put) (me )?(a|one more|another) reminder|"
    r"(add|put) (it |that |this )?(to|on|in) my (to-?do|todo|list|reminders)|"
    r"(also )?add (that|to) |to[- ]?do (things|list|items?)\b|don'?t let me forget|"
    r"recu[eé]rda(me|melo)\b" + _NOT_A_QUESTION.replace("of |about ", "de ") + r"|"
    r"ap[uú]nta(me|lo|la)?\b|a[nñ]ade(lo)? (a|en) (mi|la) lista|(pon|crea|hazme)(me)? un recordatorio|no me dejes olvidar|"
    r"rappelle-moi\b(?!\s+(ce que|qui|quoi|o[uù]|quand|comment))|(ajoute|mets)(-le)? (à|a|sur|dans) ma liste|"
    r"(cr[ée]e|mets)(-moi)? un rappel|note que)",
    re.I,
)

# A calendar event, a song or a note in Notes isn't a to-do (create_event, play_music, create_note do those).
_NOT_TODO_RE = re.compile(
    r"\b(calendar|calendario|calendrier|agenda|event|evento|[ée]v[ée]nement|meeting|reuni[oó]n|r[ée]union|"
    r"playlist|spotify|song|canci[oó]n|chanson|in (my )?notes|en (mis )?notas|dans (mes )?notes|timer|alarm|"
    r"temporizador|alarma|minuteur)\b|"
    # How she wants to-dos handled, not a to-do: "when I ask you to remind me of something, just say got it"
    r"\b(say|respond|answer|reply)\b.{0,25}\bgot it\b|\bjust say\b|\bnothing more\b|"
    r"\b(di|dime|responde|contesta) (solo|s[oó]lo|solamente)\b|nada m[aá]s\b|\b(dis|r[ée]ponds) (juste|seulement)\b|rien de plus",
    re.I,
)


def todo_request(text: str) -> str | None:
    """'add' (put something on her to-do list), 'list' (read it to her) or None."""
    if TODO_LIST_RE.search(text):
        return "list"
    if TODO_ADD_RE.search(text) and not _NOT_TODO_RE.search(text):
        return "add"
    return None


TODO_ADD_NOTE = (
    "Lea is adding to her to-do list. Call create_reminder once for each separate thing she asks you to "
    "remember (two different emails are two reminders), with a short title in her own words and her "
    'language, starting with the verb ("Call dad", "Send the emails to the professors"). Add \'due\' only '
    "if she said a day or time. Call the tool and write nothing else: Sylvie's reply is added for you."
)
TODO_NUDGE = (
    "(Check: nothing was saved, because you didn't call create_reminder. Call it now for each thing Lea "
    "asked to remember. Write nothing else.)"
)
TODO_LIST_NOTE = (
    "\n(These are Lea's open reminders, her to-do list. Tell her what's on it in plain spoken sentences: "
    "only the things to do and any due dates, not the list names. Don't add anything that isn't there.)"
)
# Lea, 2026-11-10: "when I ask you for reminders and anything, just say, got it."
GOT_IT = {"English": "Got it.", "Spanish": "Apuntado.", "French": "C'est noté."}


# Measured 2026-11-10: "Remind me tomorrow at 9…" asked on the 10th was saved for the 21st in one run and
# the 11th in another. The model keeps the time; code sets the day when Lea named it relatively.
_RELATIVE_DAYS = [
    (re.compile(r"\b(day after tomorrow|pasado ma[nñ]ana|apr[eè]s-demain)\b", re.I), 2),
    (re.compile(r"\b(this morning|esta ma[nñ]ana|ce matin)\b", re.I), 0),
    (re.compile(r"\b(tomorrow|demain)\b|(?<!la )(?<!esta )\bma[nñ]ana\b", re.I), 1),
    (
        re.compile(
            r"\b(today|tonight|this (afternoon|evening)|hoy|esta (tarde|noche)|aujourd'hui|ce soir)\b", re.I
        ),
        0,
    ),
]
_ISO_DUE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})")


def fix_due_day(text: str, due: str, today) -> str:
    """`due` with its date set from "tomorrow", "hoy", "demain"… in Lea's words (time kept)."""
    m = _ISO_DUE.match(due or "")
    if not m:
        return due
    for rx, offset in _RELATIVE_DAYS:
        if rx.search(text):
            from datetime import timedelta

            return (today + timedelta(days=offset)).isoformat() + m.group(1)
    return due
