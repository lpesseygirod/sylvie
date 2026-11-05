"""Blind A/B: Lea compares the old and the new version's answers without knowing which is which.

A round is made from two eval reports (same prompts). Left/right is random per pair and the
labels are never sent to the UI until she has chosen.
"""

from __future__ import annotations

import random

from core.db import Database
from memory.store import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS ab_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round TEXT NOT NULL,
    item_id TEXT,
    prompt TEXT NOT NULL,
    left TEXT NOT NULL,
    right TEXT NOT NULL,
    left_is TEXT NOT NULL,        -- old | new (hidden from the UI)
    choice TEXT,                  -- left | right | tie
    created_at TEXT NOT NULL,
    decided_at TEXT
);
"""

DEFAULT_SETS = ("lea", "conversations", "interests", "style", "multilingual", "general")


class ABTests:
    def __init__(self, db: Database):
        self.db = db
        self.db.conn.executescript(SCHEMA)

    def create_round(
        self, old: dict, new: dict, round_name: str, sets=DEFAULT_SETS, seed: int | None = None
    ) -> int:
        rng = random.Random(seed)
        new_by_id = {r["id"]: r for r in new["results"]}
        n = 0
        for o in old["results"]:
            nr = new_by_id.get(o["id"])
            if nr is None or o["set"] not in sets or o["answer"].strip() == nr["answer"].strip():
                continue
            new_left = rng.random() < 0.5
            left, right = (nr["answer"], o["answer"]) if new_left else (o["answer"], nr["answer"])
            self.db.execute(
                "INSERT INTO ab_pairs(round, item_id, prompt, left, right, left_is, created_at) VALUES (?,?,?,?,?,?,?)",
                (round_name, o["id"], o["prompt"], left, right, "new" if new_left else "old", utcnow()),
            )
            n += 1
        return n

    def next_pair(self, round_name: str | None = None) -> dict | None:
        sql = "SELECT id, round, prompt, left, right FROM ab_pairs WHERE choice IS NULL"
        params: tuple = ()
        if round_name:
            sql += " AND round = ?"
            params = (round_name,)
        r = self.db.one(sql + " ORDER BY id LIMIT 1", params)
        return dict(r) if r else None

    def choose(self, pair_id: int, choice: str) -> None:
        if choice not in ("left", "right", "tie"):
            raise ValueError("choice must be left, right or tie")
        self.db.execute(
            "UPDATE ab_pairs SET choice = ?, decided_at = ? WHERE id = ?", (choice, utcnow(), pair_id)
        )

    def summary(self, round_name: str | None = None) -> dict:
        sql, params = "SELECT left_is, choice FROM ab_pairs", ()
        if round_name:
            sql, params = sql + " WHERE round = ?", (round_name,)
        out = {"pending": 0, "decided": 0, "new_wins": 0, "old_wins": 0, "ties": 0}
        for r in self.db.query(sql, params):
            if r["choice"] is None:
                out["pending"] += 1
                continue
            out["decided"] += 1
            if r["choice"] == "tie":
                out["ties"] += 1
            else:
                winner_is_left = r["choice"] == "left"
                winner = r["left_is"] if winner_is_left else ("new" if r["left_is"] == "old" else "old")
                out[f"{winner}_wins"] += 1
        return out
