"""Learned shortcuts: requests Lea makes often get their lookup started right away.

I asked that things I ask often be automated and answered faster. After
each answer where Sylvie used a read-only tool, the request is remembered (with its embedding).
Once Lea has asked about the same thing MIN_REPEATS times, it becomes a shortcut: next time,
Sylvie runs that lookup immediately instead of first spending a model round deciding to. The
answer is still written from fresh results, never replayed from an old one.

Only tools that just read (web search, calendar, documents, mail) qualify, never actions.
Arguments with dates aren't reused (they'd be stale). Measured with Sylvie's embedder: the same
request in other words or languages scores ≥ 0.90; "weather in Paris" vs "…in Madrid" 0.77,
"news in Madrid" 0.86. Close calls ("today" vs "tomorrow", 0.94) are covered by a note with the
results telling Sylvie to look again if they don't fit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import numpy as np

from memory.store import utcnow

SAFE_TOOLS = {"web_search", "wikipedia", "search_documents", "calendar_events", "read_emails"}
SIMILAR = 0.90
MIN_REPEATS = 3
KEEP_USES = 500
DATED = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}/\d{1,2}\b")
NOTE = (
    '(Looked up straight away because Lea often asks this, based on an earlier request: "{q}". '
    "If these results don't fit what she's asking now, look again with a better query.)\n"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_uses (
    id INTEGER PRIMARY KEY, question TEXT NOT NULL, emb TEXT NOT NULL, tool TEXT NOT NULL,
    args TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shortcuts (
    id INTEGER PRIMARY KEY, question TEXT NOT NULL, emb TEXT NOT NULL, tool TEXT NOT NULL,
    args TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'on', uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, last_used TEXT
);
"""


@dataclass
class Shortcut:
    id: int
    question: str
    tool: str
    args: dict
    score: float = 0.0


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))


class Shortcuts:
    def __init__(self, db, embedder, cfg):
        self.db, self.embedder, self.cfg = db, embedder, cfg
        self.db.conn.executescript(SCHEMA)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get_path("shortcuts.enabled", True))

    def _emb(self, text: str) -> np.ndarray:
        return np.asarray(self.embedder.embed_one(text, query=True), dtype=np.float32)

    def record(self, question: str, tool: str, args: dict) -> Shortcut | None:
        """Background, after an answer: remember the lookup; returns a shortcut if one was just made."""
        a = json.dumps(args, ensure_ascii=False, sort_keys=True)
        if tool not in SAFE_TOOLS or DATED.search(a) or not question.strip():
            return None
        e = self._emb(question)
        self.db.execute(
            "INSERT INTO tool_uses(question, emb, tool, args, created_at) VALUES (?,?,?,?,?)",
            (question, json.dumps(e.round(5).tolist()), tool, a, utcnow()),
        )
        self.db.execute(
            "DELETE FROM tool_uses WHERE id NOT IN (SELECT id FROM tool_uses ORDER BY id DESC LIMIT ?)",
            (KEEP_USES,),
        )
        rows = self.db.query(
            "SELECT emb FROM tool_uses WHERE tool = ? AND created_at >= datetime('now', '-60 days')", (tool,)
        )
        repeats = sum(_cos(e, np.asarray(json.loads(r["emb"]))) >= SIMILAR for r in rows)
        if repeats < MIN_REPEATS:
            return None
        existing = self._best(e, statuses=("on", "off"))
        if existing is not None and existing.tool == tool:
            # Already known (or switched off by Lea): keep the latest wording of the lookup.
            self.db.execute("UPDATE shortcuts SET args = ? WHERE id = ?", (a, existing.id))
            return None
        sid = self.db.execute(
            "INSERT INTO shortcuts(question, emb, tool, args, created_at) VALUES (?,?,?,?,?)",
            (question, json.dumps(e.round(5).tolist()), tool, a, utcnow()),
        ).lastrowid
        return Shortcut(sid, question, tool, args)

    def _best(self, e: np.ndarray, statuses=("on",)) -> Shortcut | None:
        marks = ",".join("?" * len(statuses))
        best = None
        for r in self.db.query(f"SELECT * FROM shortcuts WHERE status IN ({marks})", statuses):
            score = _cos(e, np.asarray(json.loads(r["emb"])))
            if score >= SIMILAR and (best is None or score > best.score):
                best = Shortcut(r["id"], r["question"], r["tool"], json.loads(r["args"]), score)
        return best

    def find(self, question: str) -> Shortcut | None:
        if not self.enabled or not self.db.one("SELECT 1 FROM shortcuts WHERE status = 'on' LIMIT 1"):
            return None  # nothing learned yet: don't spend an embedding on it
        return self._best(self._emb(question))

    def used(self, sid: int) -> None:
        self.db.execute("UPDATE shortcuts SET uses = uses + 1, last_used = ? WHERE id = ?", (utcnow(), sid))

    def set_status(self, sid: int, status: str) -> None:
        assert status in ("on", "off")
        self.db.execute("UPDATE shortcuts SET status = ? WHERE id = ?", (status, sid))

    def list(self) -> list[dict]:
        return [
            {k: r[k] for k in ("id", "question", "tool", "args", "status", "uses", "created_at", "last_used")}
            for r in self.db.query("SELECT * FROM shortcuts ORDER BY uses DESC, id DESC")
        ]
