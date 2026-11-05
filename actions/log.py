"""Every action is logged (what, arguments, result, how to undo) and pending confirmations live here."""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.db import Database
from memory.store import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    name TEXT NOT NULL,
    args TEXT NOT NULL,
    status TEXT NOT NULL,        -- pending | done | failed | cancelled | undone
    summary TEXT,                -- the read-back shown/spoken to Lea
    result TEXT,
    undo TEXT,                   -- JSON: how to undo, if possible
    after_untrusted INTEGER NOT NULL DEFAULT 0,  -- proposed after reading outside content
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class ActionRecord:
    id: int
    conversation_id: str | None
    name: str
    args: dict
    status: str
    summary: str | None
    result: str | None
    undo: dict | None
    after_untrusted: bool
    created_at: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class ActionLog:
    def __init__(self, db: Database):
        self.db = db
        self.db.conn.executescript(SCHEMA)

    def _row(self, r) -> ActionRecord:
        return ActionRecord(
            r["id"],
            r["conversation_id"],
            r["name"],
            json.loads(r["args"]),
            r["status"],
            r["summary"],
            r["result"],
            json.loads(r["undo"]) if r["undo"] else None,
            bool(r["after_untrusted"]),
            r["created_at"],
        )

    def add(self, name: str, args: dict, status: str, conversation_id: str | None = None, **kw) -> int:
        now = utcnow()
        return self.db.execute(
            "INSERT INTO actions(conversation_id, name, args, status, summary, result, undo, after_untrusted, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                conversation_id,
                name,
                json.dumps(args, ensure_ascii=False),
                status,
                kw.get("summary"),
                kw.get("result"),
                json.dumps(kw["undo"]) if kw.get("undo") else None,
                int(kw.get("after_untrusted", False)),
                now,
                now,
            ),
        ).lastrowid

    def update(
        self, action_id: int, status: str, result: str | None = None, undo: dict | None = None
    ) -> None:
        self.db.execute(
            "UPDATE actions SET status = ?, result = coalesce(?, result), undo = coalesce(?, undo), updated_at = ? WHERE id = ?",
            (status, result, json.dumps(undo) if undo else None, utcnow(), action_id),
        )

    def get(self, action_id: int) -> ActionRecord | None:
        r = self.db.one("SELECT * FROM actions WHERE id = ?", (action_id,))
        return self._row(r) if r else None

    def pending(self, conversation_id: str) -> ActionRecord | None:
        r = self.db.one(
            "SELECT * FROM actions WHERE conversation_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        )
        return self._row(r) if r else None

    def recent(self, limit: int = 100) -> list[ActionRecord]:
        return [
            self._row(r) for r in self.db.query("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))
        ]
