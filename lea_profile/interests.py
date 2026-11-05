"""Layer 3 of the Lea model: topics she cares about.

Each mention adds 1 to the topic's score; scores fade with a half-life (default 30 days)
when she doesn't bring the topic up. After a few mentions Sylvie asks lightly whether to
keep an eye on it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from core.db import Database
from lea_profile.style import SENSITIVE_RE
from memory.store import utcnow

HALF_LIFE_DAYS = 30.0
ASK_AFTER_MENTIONS = 3
SAME_TOPIC_SIM = 0.85


def decayed(score: float, last_seen_iso: str, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    days = max(0.0, (now - datetime.fromisoformat(last_seen_iso)).total_seconds() / 86400)
    return score * math.pow(0.5, days / HALF_LIFE_DAYS)


@dataclass
class Interest:
    id: int
    topic: str
    score: float  # already decayed to now
    mentions: int
    status: str
    asked_at: str | None
    last_seen: str

    def to_dict(self) -> dict:
        return {**self.__dict__, "score": round(self.score, 2)}


class InterestStore:
    def __init__(self, db: Database, embedder):
        self.db = db
        self.embedder = embedder
        self._vec_cache: dict[str, np.ndarray] = {}
        # One row per mention, so the dashboard can chart an interest's strength over time.
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS interest_events (interest_id INTEGER NOT NULL, at TEXT NOT NULL, amount REAL NOT NULL)"
        )

    def _vec(self, topic: str) -> np.ndarray:
        if topic not in self._vec_cache:
            self._vec_cache[topic] = self.embedder.embed_one(topic)
        return self._vec_cache[topic]

    def _row(self, r, now=None) -> Interest:
        return Interest(
            r["id"],
            r["topic"],
            decayed(r["score"], r["last_seen"], now),
            r["mentions"],
            r["status"],
            r["asked_at"],
            r["last_seen"],
        )

    def list(self, include_rejected: bool = False) -> list[Interest]:
        where = "" if include_rejected else "WHERE status != 'rejected'"
        items = [self._row(r) for r in self.db.query(f"SELECT * FROM interests {where}")]
        return sorted(items, key=lambda i: -i.score)

    def find(self, topic: str) -> Interest | None:
        r = self.db.one("SELECT * FROM interests WHERE lower(topic) = lower(?)", (topic.strip(),))
        if r:
            return self._row(r)
        v = self._vec(topic.strip().lower())
        best, best_sim = None, 0.0
        for i in self.list(include_rejected=True):
            sim = float(v @ self._vec(i.topic.lower()))
            if sim > best_sim:
                best, best_sim = i, sim
        return best if best_sim >= SAME_TOPIC_SIM else None

    def bump(self, topic: str, amount: float = 1.0) -> Interest | None:
        topic = topic.strip()
        if not topic or SENSITIVE_RE.search(topic):
            return None
        now = utcnow()
        existing = self.find(topic)
        if existing is None:
            self.db.execute(
                "INSERT INTO interests(topic, score, last_seen, created_at) VALUES (?,?,?,?)",
                (topic, amount, now, now),
            )
        elif existing.status != "rejected":
            self.db.execute(
                "UPDATE interests SET score = ?, mentions = mentions + 1, last_seen = ? WHERE id = ?",
                (existing.score + amount, now, existing.id),
            )
        found = self.find(topic)
        if found is not None and found.status != "rejected":
            self.db.execute(
                "INSERT INTO interest_events(interest_id, at, amount) VALUES (?,?,?)", (found.id, now, amount)
            )
        return found

    def history(self, interest_id: int, days: int = 120) -> list[dict]:
        """Strength per week (with the same decay as the live score)."""
        from datetime import UTC, datetime, timedelta

        rows = self.db.query(
            "SELECT at, amount FROM interest_events WHERE interest_id = ? ORDER BY at", (interest_id,)
        )
        now = datetime.now(UTC)
        out = []
        for w in range(days // 7, -1, -1):
            t = now - timedelta(days=7 * w)
            score = sum(
                decayed(r["amount"], r["at"], t) for r in rows if datetime.fromisoformat(r["at"]) <= t
            )
            out.append({"week": t.date().isoformat(), "score": round(score, 2)})
        return out

    def set_status(self, interest_id: int, status: str) -> None:
        assert status in ("guess", "confirmed", "rejected")
        self.db.execute("UPDATE interests SET status = ? WHERE id = ?", (status, interest_id))

    def remove(self, topic: str) -> Interest | None:
        """Lea said she's no longer interested → rejected (kept so it isn't re-learned)."""
        i = self.find(topic)
        if i:
            self.set_status(i.id, "rejected")
        return i

    def mark_asked(self, interest_id: int) -> None:
        self.db.execute("UPDATE interests SET asked_at = ? WHERE id = ?", (utcnow(), interest_id))

    def to_confirm(self) -> Interest | None:
        """A guessed interest mentioned often enough that Sylvie should ask about it (once)."""
        for i in self.list():
            if i.status == "guess" and i.mentions >= ASK_AFTER_MENTIONS and not i.asked_at:
                return i
        return None

    def delete(self, interest_id: int) -> None:
        self.db.execute("DELETE FROM interests WHERE id = ?", (interest_id,))
