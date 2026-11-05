"""Layer 2 of the Lea model: how she talks and likes to be talked to.

Each trait has evidence (short quotes) and a confidence. Inferred traits are 'guess' until
Lea confirms them in the dashboard (rule 13). Repeated evidence raises confidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core.db import Database
from memory.store import utcnow

GUESS_START = 0.3
GUESS_STEP = 0.15
GUESS_CAP = 0.9
MAX_EVIDENCE = 6

# Never store traits about these (rule 13) — even as guesses.
SENSITIVE_RE = re.compile(
    r"health|ill|sick|depress|anxi|mood|mental|relationship|boyfriend|girlfriend|partner|dating|"
    r"politic|religio|faith|sexual",
    re.I,
)


@dataclass
class Trait:
    id: int
    name: str
    description: str
    confidence: float
    status: str
    evidence: list[str]

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:60]


class StyleStore:
    def __init__(self, db: Database):
        self.db = db

    def _row(self, r) -> Trait:
        return Trait(
            r["id"], r["name"], r["description"], r["confidence"], r["status"], json.loads(r["evidence"])
        )

    def list(self, include_rejected: bool = False) -> list[Trait]:
        where = "" if include_rejected else "WHERE status != 'rejected'"
        return [self._row(r) for r in self.db.query(f"SELECT * FROM traits {where} ORDER BY confidence DESC")]

    def observe(self, name: str, description: str, evidence: str | None = None) -> Trait | None:
        if SENSITIVE_RE.search(name + " " + description):
            return None
        name = _slug(name)
        if not name:
            return None
        now = utcnow()
        r = self.db.one("SELECT * FROM traits WHERE name = ?", (name,))
        if r is None:
            ev = [evidence[:200]] if evidence else []
            self.db.execute(
                "INSERT INTO traits(name, description, confidence, evidence, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (name, description.strip(), GUESS_START, json.dumps(ev, ensure_ascii=False), now, now),
            )
        else:
            t = self._row(r)
            if t.status == "rejected":
                return t  # Lea said it's not true — don't bring it back
            ev = t.evidence
            if evidence and evidence[:200] not in ev:
                ev = (ev + [evidence[:200]])[-MAX_EVIDENCE:]
            conf = t.confidence if t.status == "confirmed" else min(GUESS_CAP, t.confidence + GUESS_STEP)
            self.db.execute(
                "UPDATE traits SET confidence = ?, evidence = ?, updated_at = ? WHERE id = ?",
                (conf, json.dumps(ev, ensure_ascii=False), now, t.id),
            )
        return self._row(self.db.one("SELECT * FROM traits WHERE name = ?", (name,)))

    def set_status(self, trait_id: int, status: str) -> None:
        assert status in ("guess", "confirmed", "rejected")
        conf = 1.0 if status == "confirmed" else None
        if conf is None:
            self.db.execute(
                "UPDATE traits SET status = ?, updated_at = ? WHERE id = ?", (status, utcnow(), trait_id)
            )
        else:
            self.db.execute(
                "UPDATE traits SET status = ?, confidence = ?, updated_at = ? WHERE id = ?",
                (status, conf, utcnow(), trait_id),
            )

    def update(self, trait_id: int, description: str) -> None:
        self.db.execute(
            "UPDATE traits SET description = ?, updated_at = ? WHERE id = ?",
            (description, utcnow(), trait_id),
        )

    def delete(self, trait_id: int) -> None:
        self.db.execute("DELETE FROM traits WHERE id = ?", (trait_id,))
