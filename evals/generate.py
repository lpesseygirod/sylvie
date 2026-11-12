"""Generates the fixed, checkable evaluation sets (seeded, so they never change).

Answers are computed by code, not written by hand. These sets are for evaluation ONLY:
learning/dataset.py refuses any training example whose prompt matches an eval prompt.

Sets: math, finance, multilingual, tools, general, style. Lea's own sets (lea_questions,
conversations, interests) are templates she fills in (evals/sets/README.md).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SETS = Path(__file__).parent / "sets"


def math_items(rng: random.Random) -> list[dict]:
    items = []
    for i in range(15):
        a, b = rng.randint(120, 9800), rng.randint(12, 980)
        op = rng.choice(["*", "+", "-"])
        val = {"*": a * b, "+": a + b, "-": a - b}[op]
        word = {"*": "times", "+": "plus", "-": "minus"}[op]
        items.append(
            {
                "id": f"math-{i}",
                "prompt": f"What is {a} {word} {b}? Just the number.",
                "check": {"type": "number", "value": val},
            }
        )
    for i in range(10):
        p, n = rng.choice([5, 12, 15, 17.5, 21, 33]), rng.randint(40, 2400)
        items.append(
            {
                "id": f"pct-{i}",
                "prompt": f"What is {p}% of {n}?",
                "check": {"type": "number", "value": round(p * n / 100, 2), "tol": 0.01},
            }
        )
    return items


def finance_items(rng: random.Random) -> list[dict]:
    items = []
    for i in range(8):
        pv, r, n = rng.choice([1000, 2500, 5000, 12000]), rng.choice([2, 3.5, 4, 6, 8]), rng.randint(2, 15)
        fv = pv * (1 + r / 100) ** n
        items.append(
            {
                "id": f"fv-{i}",
                "prompt": f"I invest {pv} euros at {r}% per year, compounded yearly, for {n} years. How much do I have at the end? Round to 2 decimals.",
                "check": {"type": "number", "value": round(fv, 2), "tol": 0.02},
            }
        )
    for i in range(6):
        rate = rng.choice([5, 8, 10])
        flows = [-rng.choice([1000, 2000, 5000])] + [rng.randint(300, 2500) for _ in range(rng.randint(2, 4))]
        npv = sum(cf / (1 + rate / 100) ** t for t, cf in enumerate(flows))
        items.append(
            {
                "id": f"npv-{i}",
                "prompt": f"Cash flows by year starting at year 0: {flows}. What's the NPV at a {rate}% discount rate? Round to 2 decimals.",
                "check": {"type": "number", "value": round(npv, 2), "tol": 0.05},
            }
        )
    return items


MULTI = [
    ("es", "¿Cuál es la capital de Portugal?", "lisboa"),
    ("es", "¿Cuántos días tiene un año bisiesto?", "366"),
    ("es", "¿Qué planeta es el más grande del sistema solar?", "júpiter"),
    ("fr", "Quelle est la capitale de l'Italie ?", "rome"),
    ("fr", "Combien de côtés a un hexagone ?", "6"),
    ("fr", "Quel est le symbole chimique de l'or ?", "au"),
    ("en", "What's the chemical symbol for sodium?", "na"),
    ("en", "How many minutes are in three hours?", "180"),
    (
        None,
        "Oye, ¿cuántos segundos hay en una hora? Quick answer please",
        "3600",
    ),  # mixed: any language is fine
    (None, "Dis-moi, what's the capital of Japan?", "tokyo"),
]


def multilingual_items() -> list[dict]:
    return [
        {
            "id": f"multi-{i}",
            "prompt": q,
            "check": {"type": "contains", "value": a} | ({"lang": lang} if lang else {}),
        }
        for i, (lang, q, a) in enumerate(MULTI)
    ]


TOOLS = [
    ("What is 48213 times 9172?", "calculator"),
    ("Calcula 17,5% de 23.480 euros", "calculator"),
    ("What's the latest news about the European Central Bank interest rate decision?", "web_search"),
    ("¿Quién ganó el último Gran Premio de Fórmula 1?", "web_search"),
    ("Hi Sylvie!", None),
    ("Can you explain what a hash table is, in two sentences?", None),
    ("Pause the music", "music"),
    ("Set a timer for 10 minutes for the pasta", "set_timer"),
    ("Mets un rappel pour appeler maman demain à 18h", "create_reminder"),
]


def tool_items() -> list[dict]:
    return [
        {"id": f"tool-{i}", "prompt": q, "check": {"type": "tool", "value": t}}
        for i, (q, t) in enumerate(TOOLS)
    ]


GENERAL = [
    ("Who wrote 'One Hundred Years of Solitude'?", "garcía márquez"),
    ("What's the boiling point of water at sea level in Celsius?", "100"),
    ("Which language is primarily spoken in Brazil?", "portuguese"),
    ("What does CPU stand for?", "central processing unit"),
    ("In what year did the Berlin Wall fall?", "1989"),
    ("What is the derivative of x squared?", "2x"),
    ("What is the time complexity of binary search?", "log"),
    ("Which gas do plants absorb from the air for photosynthesis?", "carbon dioxide"),
]


def general_items() -> list[dict]:
    return [
        {"id": f"gen-{i}", "prompt": q, "check": {"type": "contains", "value": a}}
        for i, (q, a) in enumerate(GENERAL)
    ]


STYLE = [
    "hey sylvie, how are you?",
    "what's a good name for a study group chat?",
    "¿me das un consejo rápido para concentrarme?",
    "c'est quoi un ETF, en deux mots ?",
    "is it worth learning Rust this year?",
    "tell me something interesting about octopuses",
    # Added 2026-11-06 after reviewing the baseline answers: open-ended "many options" questions
    # (she answered with 15-item catalogs) and plain facts (she added emojis and closing offers).
    "any ideas for a birthday present for my sister? she likes hiking",
    "¿qué serie me recomiendas para esta noche?",
    "donne-moi une idée de dîner rapide",
    "what should I name my new plant?",
    "who painted the Mona Lisa?",
    "¿cuántos días tiene un año bisiesto?",
]


def style_items() -> list[dict]:
    # Checks Sylvie's defaults: short, no "As an AI language model", same language, no emojis.
    return [
        {"id": f"style-{i}", "prompt": q, "check": {"type": "style", "max_words": 90}}
        for i, q in enumerate(STYLE)
    ]


# Everyday questions ("make tests of common questions and see if she is able to do it").
# Each checks what she must DO, never a written answer: the tool she must call, the language she must
# answer in, and that she doesn't say she has no access. "before" turns set up a conversation in another
# language; "channel"/"heard" make it a voice turn with the language Whisper detected.
EVERYDAY = [
    # needs the internet
    {
        "prompt": "Sylvie, what's the weather going to be like tomorrow in Toledo?",
        "lang": "en",
        "tool": "web_search",
    },
    {"prompt": "Sylvie, dime qué tiempo hará mañana en Toledo.", "lang": "es", "tool": "web_search"},
    {"prompt": "Quel temps fera-t-il demain à Paris ?", "lang": "fr", "tool": "web_search"},
    {"prompt": "Sylvie, dime qué películas hay hoy en televisión.", "lang": "es", "tool": "web_search"},
    {"prompt": "What's on TV tonight in Spain?", "lang": "en", "tool": "web_search"},
    {
        "prompt": "¿Cuáles son las noticias más importantes de hoy en España?",
        "lang": "es",
        "tool": "web_search",
    },
    {"prompt": "What are today's top news headlines?", "lang": "en", "tool": "web_search"},
    {"prompt": "¿Cómo quedó el último partido del Real Madrid?", "lang": "es", "tool": "web_search"},
    {"prompt": "How much does an iPhone 17 Pro cost in Spain?", "lang": "en", "tool": "web_search"},
    {"prompt": "¿A qué hora cierra Mercadona los domingos?", "lang": "es", "tool": "web_search"},
    {"prompt": "Quelles sont les actualités du jour en France ?", "lang": "fr", "tool": "web_search"},
    {"prompt": "What's the euro to dollar exchange rate right now?", "lang": "en", "tool": "web_search"},
    {"prompt": "Sylvie, ¿va a llover este fin de semana en Madrid?", "lang": "es", "tool": "web_search"},
    # she knows these
    {"prompt": "What is the capital of Australia?", "lang": "en", "contains": "canberra"},
    {"prompt": "¿Quién escribió Cien años de soledad?", "lang": "es", "contains": "garcía márquez"},
    {"prompt": "Qui a peint la Joconde ?", "lang": "fr", "contains": "vinci"},
    {"prompt": "Explain in two sentences what inflation is.", "lang": "en"},
    {"prompt": "¿Cuánto es el 21% de 350 euros?", "lang": "es", "contains": "73,5"},
    {"prompt": "What is 15% of 80?", "lang": "en", "contains": "12"},
    # things to do (a fake app controller in the eval: nothing really plays or gets added)
    {"prompt": "Sylvie, put the song Bloodline.", "lang": "en", "tool": "play_music"},
    {"prompt": "Sylvie, pon la canción La Reina de Maluma.", "lang": "es", "tool": "play_music"},
    {"prompt": "Sylvie, put the volume to the max.", "lang": "en", "tool": "music"},
    {"prompt": "Sylvie, pausa la música.", "lang": "es", "tool": "music"},
    {"prompt": "¿Qué tengo mañana en el calendario?", "lang": "es", "tool": "calendar_events"},
    {"prompt": "Remind me to call my mom tomorrow at 10.", "lang": "en", "tool": "create_reminder"},
    {"prompt": "Mets un minuteur de 5 minutes.", "lang": "fr", "tool": "set_timer"},
    # switching language mid-conversation
    {"before": ["Hola Sylvie, ¿qué tal estás?"], "prompt": "Sylvie, how are you doing today?", "lang": "en"},
    {
        "before": ["Sylvie, ¿qué hora es?"],
        "prompt": "Sylvie, stop listening.",
        "lang": "en",
        "channel": "voice",
        "heard": "en",
    },
    {"before": ["Hi Sylvie, how are you?"], "prompt": "Sylvie, ¿me recomiendas una serie?", "lang": "es"},
    {
        "before": ["Hola Sylvie."],
        "prompt": "Sylvie, merci beaucoup, c'est parfait.",
        "lang": "fr",
        "channel": "voice",
        "heard": "fr",
    },
    {"prompt": "Sylvie, what time is it?", "lang": "en", "channel": "voice", "heard": "en"},
    {"prompt": "I'm stressed about my exams.", "lang": "en"},
    # asked to look something up (2026-11-08: "Estoy buscando…" three times and never a search)
    {
        "prompt": "Sylvie, búscame los datos de la UE sobre muertes en accidentes de coche y de avión.",
        "lang": "es",
        "tool": "web_search",
    },
    {
        "before": ["¿Qué es más seguro, viajar en coche o en avión?"],
        "prompt": "dame los datos de la Unión Europea",
        "lang": "es",
        "tool": ["web_search", "wikipedia"],
    },
    {"prompt": "Look up how many people live in Lyon.", "lang": "en", "tool": ["web_search", "wikipedia"]},
]


# "make more tests of access to apps… and see that she does it". Each checks the tool
# she calls and one argument it must carry. Run against fake apps, a made-up photo library and made-up
# WhatsApp chats (evals/run.py): nothing is sent, played or opened for real.
APPS = [
    ("Sylvie, pon Bridgerton en Netflix", "es", "watch", ("title", "bridgerton")),
    ("Put Stranger Things on Netflix", "en", "watch", ("title", "stranger things")),
    ("Mets-moi Lupin sur Netflix", "fr", "watch", ("title", "lupin")),
    ("Ponme un vídeo de recetas de pasta en YouTube", "es", "watch", ("service", "youtube")),
    ("Quiero ver The Mandalorian en Disney", "es", "watch", ("service", "disney")),
    ("Sylvie, enséñame las fotos con mamá", "es", "find_photos", ("person", "mam")),
    ("Show me my photos from the beach", "en", "find_photos", ("query", "beach")),
    ("¿Tengo fotos de algún cumpleaños con Ana?", "es", "find_photos", ("person", "ana")),
    ("¿Qué me ha dicho mamá por WhatsApp?", "es", "whatsapp", ("contact", "mam")),
    ("What did Ana say about the exam on WhatsApp?", "en", "whatsapp", ("contact", "ana")),
    ("Mándale un mensaje a mamá diciendo que llego tarde", "es", "send_message", ("text", "tarde")),
    ("Text Ana that I'll be there in 10 minutes", "en", "send_message", ("to", "ana")),
    ("Envoie un message à Inés : je t'appelle ce soir", "fr", "send_message", ("to", "in")),
    ("Escríbele a mamá por WhatsApp que ya he llegado", "es", "send_message", ("app", "whatsapp")),
    ("Llama a mamá", "es", "call", ("to", "mam")),
    ("Call Ana on FaceTime video", "en", "call", ("to", "ana")),
    ("¿Cómo llego a la Puerta del Sol andando?", "es", "directions", ("destination", "sol")),
    ("How do I get to Madrid-Barajas airport?", "en", "directions", ("destination", "barajas")),
    ("Apunta en una nota: comprar leche, huevos y pan", "es", "create_note", ("body", "leche")),
    ("Abre Spotify", "es", "open_app", ("name", "spotify")),
    ("Open the Notes app", "en", "open_app", ("name", "notes")),
    ("Pon música para estudiar", "es", "play_music", None),
    ("Sube el volumen", "es", "music", ("command", "volume_up")),
    ("Next song please", "en", "music", ("command", "next")),
    (
        "Añade al calendario cena con Ana el viernes a las 9 de la noche",
        "es",
        "create_event",
        ("title", "ana"),
    ),
    ("Recuérdame comprar el regalo de mamá mañana a las 6", "es", "create_reminder", ("title", "regalo")),
    ("Pon un temporizador de 20 minutos para la pasta", "es", "set_timer", ("minutes", "20")),
    ("Lee mis últimos correos", "es", "read_emails", None),
    # calendar, Blackboard deadlines, reminders, contacts, documents
    ("¿Qué tengo esta semana?", "es", "calendar_events", None),
    ("When is my criminology essay due?", "en", "calendar_events", ("search", "")),
    ("What are my reminders?", "en", "list_reminders", None),
    ("¿Cuál es el número de Inés Pérez?", "es", "contact_info", ("name", "in")),
    ("Resume mi ensayo de criminología", "es", ["read_document", "search_documents"], None),
    ("What does my criminology essay say about Merton?", "en", ["search_documents", "read_document"], None),
]


def apps_items() -> list[dict]:
    items = []
    for i, (prompt, lang, tool, arg) in enumerate(APPS):
        check = {"type": "everyday", "lang": lang, "tool": tool}
        if arg:
            check["arg"] = list(arg)
        items.append({"id": f"app-{i}", "prompt": prompt, "check": check})
    return items


def everyday_items() -> list[dict]:
    items = []
    for i, e in enumerate(EVERYDAY):
        check = {"type": "everyday", **{k: e[k] for k in ("lang", "tool", "contains") if k in e}}
        extra = {k: e[k] for k in ("before", "channel", "heard") if k in e}
        items.append({"id": f"day-{i}", "prompt": e["prompt"], **extra, "check": check})
    return items


def main() -> None:
    rng = random.Random(20261105)
    SETS.mkdir(exist_ok=True)
    sets = {
        "math": math_items(rng),
        "finance": finance_items(rng),
        "multilingual": multilingual_items(),
        "tools": tool_items(),
        "general": general_items(),
        "style": style_items(),
        "everyday": everyday_items(),
        "apps": apps_items(),
    }
    for name, items in sets.items():
        with open(SETS / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps({"set": name, **it}, ensure_ascii=False) + "\n")
        print(f"{name}: {len(items)} items")


if __name__ == "__main__":
    main()
