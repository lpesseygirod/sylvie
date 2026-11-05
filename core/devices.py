"""Paired devices (Lea's iPhone, Watch Shortcut, room listener) for the phone gateway.

Each device gets its own random token, shown once when Lea pairs it. Only a SHA-256 hash is
stored here, so the database never holds a usable secret. Devices can be revoked.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from core.db import Database
from memory.store import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen TEXT,
    revoked_at TEXT
);
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Device:
    id: int
    name: str
    created_at: str
    last_seen: str | None
    revoked_at: str | None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Devices:
    def __init__(self, db: Database):
        self.db = db
        self.db.conn.executescript(SCHEMA)

    def pair(self, name: str) -> str:
        """Creates a device and returns its token (show it to Lea once; it isn't stored)."""
        token = "sylv_" + secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO devices(name, token_hash, created_at) VALUES (?, ?, ?)",
            (name.strip()[:60], _hash(token), utcnow()),
        )
        return token

    def authenticate(self, token: str | None) -> Device | None:
        if not token or not token.startswith("sylv_"):
            return None
        h = _hash(token)
        for r in self.db.query("SELECT * FROM devices WHERE revoked_at IS NULL"):
            if hmac.compare_digest(r["token_hash"], h):
                self.db.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (utcnow(), r["id"]))
                return Device(r["id"], r["name"], r["created_at"], r["last_seen"], r["revoked_at"])
        return None

    def list(self) -> list[Device]:
        rows = self.db.query("SELECT * FROM devices ORDER BY id")
        return [Device(r["id"], r["name"], r["created_at"], r["last_seen"], r["revoked_at"]) for r in rows]

    def revoke(self, device_id: int) -> None:
        self.db.execute("UPDATE devices SET revoked_at = ? WHERE id = ?", (utcnow(), device_id))
