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
    }
    for name, items in sets.items():
        with open(SETS / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps({"set": name, **it}, ensure_ascii=False) + "\n")
        print(f"{name}: {len(items)} items")


if __name__ == "__main__":
    main()
