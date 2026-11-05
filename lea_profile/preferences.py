"""Learning from corrections in normal conversation, from day one.

"No Sylvie, I like it this way", "shorter please", "don't use emojis", "prefiero que…"
→ detected, saved, applied immediately (same conversation) and in every future one.

Two steps so normal messages cost nothing: a cheap multilingual pattern check, then (only
when it fires) one small model call that turns the message into a clear instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.db import Database
from core.jsonutil import extract_json
from memory.store import utcnow

TRIGGER_RE = re.compile(
    r"\b("
    r"i (prefer|like it|want you to|'d rather|would rather|hate when|don'?t like)|"
    r"(don'?t|do not|stop|never|always|please don'?t) (do|say|use|be|call|give|write|answer|start|end|add|ask|talk|mix|explain|lecture)|"
    r"too (long|short|formal|casual|wordy|much|many|serious)|"
    r"(shorter|longer|more casual|less formal|more formal|more detail|less detail|more like this|like this)|"
    r"from now on|next time|"
    r"prefiero|me gusta (más|que)|no me gusta|no (hagas|digas|uses|me hables)|deja de|siempre|nunca|"
    r"más (corto|largo|breve|directo|informal)|demasiado (largo|formal)|a partir de ahora|"
    r"je préfère|j'aime (mieux|quand)|je n'aime pas|ne (fais|dis|mets) pas|arrête de|toujours|jamais|"
    r"plus (court|long|direct)|trop (long|formel)|désormais|à partir de maintenant"
    r")\b",
    re.I,
)

CLASSIFY_PROMPT = """Lea is talking to her assistant Sylvie. Decide whether Lea's message states a lasting \
preference about HOW Sylvie should behave or talk (length, tone, language, format, habits) — a correction \
or a rule for the future. A one-off request about the current task ("make this shorter") is NOT a lasting \
preference unless she says it should apply from now on, or it's clearly a general complaint about Sylvie's style.

Current saved preferences (id: text):
{current}

Lea's message:
<message>
{message}
</message>

Answer with JSON only:
{{"is_preference": true|false,
 "preference": "<the rule as a short instruction to Sylvie, in English, e.g. 'Keep answers short.'>",
 "replaces": [<ids of current preferences this one contradicts or updates>],
 "ack": "<a confirmation of at most 6 words, in the language of Lea's message, e.g. 'Got it, shorter from now on.'>"}}"""


@dataclass
class Preference:
    id: int
    text: str
    scope: str
    source_quote: str | None
    active: bool
    created_at: str


class PreferenceStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self, include_inactive: bool = False) -> list[Preference]:
        where = "" if include_inactive else "WHERE active = 1"
        rows = self.db.query(f"SELECT * FROM preferences {where} ORDER BY id")
        return [
            Preference(r["id"], r["text"], r["scope"], r["source_quote"], bool(r["active"]), r["created_at"])
            for r in rows
        ]

    def add(self, text: str, source_quote: str | None = None, replaces: list[int] | None = None) -> int:
        with self.db.transaction() as c:
            for pid in replaces or []:
                c.execute("UPDATE preferences SET active = 0 WHERE id = ?", (int(pid),))
            cur = c.execute(
                "INSERT INTO preferences(text, source_quote, created_at) VALUES (?, ?, ?)",
                (text.strip(), source_quote, utcnow()),
            )
            return cur.lastrowid

    def update(self, pid: int, text: str | None = None, active: bool | None = None) -> None:
        if text is not None:
            self.db.execute("UPDATE preferences SET text = ? WHERE id = ?", (text.strip(), pid))
        if active is not None:
            self.db.execute("UPDATE preferences SET active = ? WHERE id = ?", (int(active), pid))

    def as_prompt(self) -> str:
        prefs = self.list()
        if not prefs:
            return ""
        return "Lea's saved preferences (always follow these):\n" + "\n".join(f"- {p.text}" for p in prefs)


def maybe_preference(message: str) -> bool:
    return bool(TRIGGER_RE.search(message))


def detect_preference(llm, store: PreferenceStore, message: str) -> dict | None:
    """Returns {"id", "preference", "ack"} if a lasting preference was saved, else None."""
    if not maybe_preference(message):
        return None
    current = "\n".join(f"{p.id}: {p.text}" for p in store.list()) or "(none)"
    raw = llm.complete(
        [{"role": "user", "content": CLASSIFY_PROMPT.format(current=current, message=message[:2000])}],
        max_tokens=200,
    )
    data = extract_json(raw)
    if (
        not isinstance(data, dict)
        or not data.get("is_preference")
        or not str(data.get("preference", "")).strip()
    ):
        return None
    replaces = [int(x) for x in data.get("replaces") or [] if str(x).isdigit()]
    pid = store.add(str(data["preference"]), source_quote=message[:300], replaces=replaces)
    return {
        "id": pid,
        "preference": str(data["preference"]).strip(),
        "ack": str(data.get("ack") or "").strip(),
    }
