"""Storage for corrections (the teacher's proposals) and the training queue.

Training data rule (plan rule 4): every example records its source and license. Only these
sources are allowed into a training set:
  - "lea"                → written or edited by Lea herself
  - "sylvie-local:<model>" → produced by Sylvie's own local model (and accepted by Lea)
  - "open-dataset:<name>"  → open datasets whose license allows training (checked in Phase 4)
Anything else (for example text written by Claude) is rejected by `add_example`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.db import Database
from memory.store import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    conversation_id TEXT,
    kind TEXT NOT NULL,            -- thumbs_down | wrong | edit
    question TEXT NOT NULL,
    original TEXT NOT NULL,
    comment TEXT,                  -- what Lea said was wrong (optional)
    diagnosis TEXT,                -- JSON from the teacher
    proposal TEXT,                 -- the teacher's better answer
    sources TEXT,                  -- JSON
    rule TEXT,                     -- JSON {"type": "preference"|"fact", "text": ...}
    status TEXT NOT NULL,          -- queued | proposed | accepted | rejected | failed
    final_answer TEXT,             -- what Lea accepted (maybe edited)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS training_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- sft | preference (chosen vs rejected)
    messages TEXT NOT NULL,        -- JSON chat messages before the answer
    chosen TEXT NOT NULL,
    rejected TEXT,
    tag TEXT NOT NULL,             -- personal | general (rule 12)
    source TEXT NOT NULL,
    license TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    correction_id INTEGER,
    created_at TEXT NOT NULL
);
"""

ALLOWED_SOURCE_PREFIXES = ("lea", "sylvie-local:", "open-dataset:")


class DisallowedSource(ValueError):
    pass


@dataclass
class Correction:
    id: int
    message_id: int | None
    conversation_id: str | None
    kind: str
    question: str
    original: str
    comment: str | None
    diagnosis: dict | None
    proposal: str | None
    sources: list | None
    rule: dict | None
    status: str
    final_answer: str | None
    created_at: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class LearningStore:
    def __init__(self, db: Database):
        self.db = db
        self.db.conn.executescript(SCHEMA)

    # ---------- corrections ----------
    def _row(self, r) -> Correction:
        j = lambda v: json.loads(v) if v else None  # noqa: E731
        return Correction(
            r["id"], r["message_id"], r["conversation_id"], r["kind"], r["question"], r["original"], r["comment"],
            j(r["diagnosis"]), r["proposal"], j(r["sources"]), j(r["rule"]), r["status"], r["final_answer"], r["created_at"],
        )  # fmt: skip

    def add_correction(
        self, kind, question, original, message_id=None, conversation_id=None, comment=None
    ) -> int:
        now = utcnow()
        return self.db.execute(
            "INSERT INTO corrections(message_id, conversation_id, kind, question, original, comment, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (message_id, conversation_id, kind, question, original, comment, "queued", now, now),
        ).lastrowid

    def update_correction(self, cid: int, **fields) -> None:
        for k, v in fields.items():
            assert k in ("diagnosis", "proposal", "sources", "rule", "status", "final_answer")
            if k in ("diagnosis", "sources", "rule") and v is not None:
                v = json.dumps(v, ensure_ascii=False)
            self.db.execute(
                f"UPDATE corrections SET {k} = ?, updated_at = ? WHERE id = ?", (v, utcnow(), cid)
            )

    def correction(self, cid: int) -> Correction | None:
        r = self.db.one("SELECT * FROM corrections WHERE id = ?", (cid,))
        return self._row(r) if r else None

    def corrections(self, status: str | None = None, limit: int = 100) -> list[Correction]:
        if status:
            rows = self.db.query(
                "SELECT * FROM corrections WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM corrections ORDER BY id DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def correction_for_message(self, message_id: int) -> Correction | None:
        r = self.db.one("SELECT * FROM corrections WHERE message_id = ? ORDER BY id DESC", (message_id,))
        return self._row(r) if r else None

    # ---------- training queue ----------
    def add_example(
        self,
        messages: list[dict],
        chosen: str,
        source: str,
        license: str,
        tag: str,
        rejected: str | None = None,
        weight: float = 1.0,
        correction_id: int | None = None,
    ) -> int:
        if not source.startswith(ALLOWED_SOURCE_PREFIXES):
            raise DisallowedSource(f"source '{source}' is not allowed in training data (plan rule 4)")
        if tag not in ("personal", "general"):
            raise ValueError("tag must be personal or general")
        return self.db.execute(
            "INSERT INTO training_examples(kind, messages, chosen, rejected, tag, source, license, weight, correction_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "preference" if rejected else "sft",
                json.dumps(messages, ensure_ascii=False),
                chosen,
                rejected,
                tag,
                source,
                license,
                weight,
                correction_id,
                utcnow(),
            ),
        ).lastrowid

    def examples(self, tag: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM training_examples", ()
        if tag:
            sql, params = sql + " WHERE tag = ?", (tag,)
        return [
            {**dict(r), "messages": json.loads(r["messages"])}
            for r in self.db.query(sql + " ORDER BY id", params)
        ]

    def stats(self) -> dict:
        rows = self.db.query("SELECT tag, source, count(*) n FROM training_examples GROUP BY tag, source")
        return {"total": sum(r["n"] for r in rows), "by": [dict(r) for r in rows]}
