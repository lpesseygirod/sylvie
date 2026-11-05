"""Facts about Lea: add, update, supersede, forget, restore.

Contradictions: facts can carry a `key` (e.g. `lea.city`). A newer fact with the same key
supersedes the old one — the old one stays as history (superseded_by points to the new one).
Facts without a key that are near-duplicates of an existing fact just refresh it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from core.db import Database
from knowledge.embeddings import to_blob

DUPLICATE_SIMILARITY = 0.93


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Fact:
    id: int
    text: str
    key: str | None
    category: str
    source: str
    sensitive: bool
    created_at: str
    updated_at: str
    superseded_by: int | None = None
    forgotten_at: str | None = None

    @property
    def active(self) -> bool:
        return self.superseded_by is None and self.forgotten_at is None

    @classmethod
    def from_row(cls, r) -> Fact:
        return cls(
            id=r["id"],
            text=r["text"],
            key=r["key"],
            category=r["category"],
            source=r["source"],
            sensitive=bool(r["sensitive"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            superseded_by=r["superseded_by"],
            forgotten_at=r["forgotten_at"],
        )

    def to_dict(self) -> dict:
        return {**self.__dict__, "active": self.active}


ACTIVE = "superseded_by IS NULL AND forgotten_at IS NULL"


class MemoryStore:
    def __init__(self, db: Database, embedder):
        self.db = db
        self.embedder = embedder

    # ---------- write ----------
    def add(
        self,
        text: str,
        key: str | None = None,
        category: str = "general",
        source: str = "conversation",
        sensitive: bool = False,
    ) -> tuple[Fact, str]:
        """Returns (fact, outcome) where outcome is 'added' | 'superseded' | 'duplicate'."""
        text = text.strip()
        key = (key or "").strip().lower() or None
        vec = self.embedder.embed_one(text)
        now = utcnow()

        old = None
        if key:
            old = self.db.one(f"SELECT * FROM facts WHERE key = ? AND {ACTIVE} ORDER BY id DESC", (key,))
            if old is not None and old["text"].strip().lower() == text.lower():
                self.db.execute("UPDATE facts SET updated_at = ? WHERE id = ?", (now, old["id"]))
                return self.get(old["id"]), "duplicate"
        if old is None:
            dup = self._nearest_active(vec, min_sim=DUPLICATE_SIMILARITY)
            if dup is not None:
                self.db.execute("UPDATE facts SET updated_at = ? WHERE id = ?", (now, dup.id))
                return self.get(dup.id), "duplicate"

        with self.db.transaction() as c:
            cur = c.execute(
                "INSERT INTO facts(key, text, category, source, sensitive, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, text, category, source, int(sensitive), now, now),
            )
            fid = cur.lastrowid
            c.execute("INSERT INTO facts_vec(rowid, embedding) VALUES (?, ?)", (fid, to_blob(vec)))
            if old is not None:
                c.execute("UPDATE facts SET superseded_by = ? WHERE id = ?", (fid, old["id"]))
        return self.get(fid), ("superseded" if old is not None else "added")

    def update_text(self, fact_id: int, text: str) -> Fact:
        vec = self.embedder.embed_one(text)
        with self.db.transaction() as c:
            c.execute(
                "UPDATE facts SET text = ?, updated_at = ? WHERE id = ?", (text.strip(), utcnow(), fact_id)
            )
            c.execute("DELETE FROM facts_vec WHERE rowid = ?", (fact_id,))
            c.execute("INSERT INTO facts_vec(rowid, embedding) VALUES (?, ?)", (fact_id, to_blob(vec)))
        return self.get(fact_id)

    def forget(self, fact_id: int) -> None:
        self.db.execute("UPDATE facts SET forgotten_at = ? WHERE id = ?", (utcnow(), fact_id))

    def restore(self, fact_id: int) -> None:
        self.db.execute("UPDATE facts SET forgotten_at = NULL WHERE id = ?", (fact_id,))

    # ---------- read ----------
    def get(self, fact_id: int) -> Fact | None:
        r = self.db.one("SELECT * FROM facts WHERE id = ?", (fact_id,))
        return Fact.from_row(r) if r else None

    def list(self, include_inactive: bool = False, limit: int = 500) -> list[Fact]:
        where = "" if include_inactive else f"WHERE {ACTIVE}"
        rows = self.db.query(f"SELECT * FROM facts {where} ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [Fact.from_row(r) for r in rows]

    def history(self, key: str) -> list[Fact]:
        rows = self.db.query("SELECT * FROM facts WHERE key = ? ORDER BY id", (key.lower(),))
        return [Fact.from_row(r) for r in rows]

    def by_source(self, source: str) -> list[Fact]:
        rows = self.db.query(f"SELECT * FROM facts WHERE source = ? AND {ACTIVE} ORDER BY id", (source,))
        return [Fact.from_row(r) for r in rows]

    def _nearest_active(self, vec, min_sim: float) -> Fact | None:
        rows = self.db.query(
            "SELECT rowid, distance FROM facts_vec WHERE embedding MATCH ? AND k = 5 ORDER BY distance",
            (to_blob(vec),),
        )
        for r in rows:
            f = self.get(r["rowid"])
            if f and f.active and (1 - r["distance"]) >= min_sim:
                return f
        return None
