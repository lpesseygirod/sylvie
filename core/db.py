"""One SQLite database in the data folder for everything: conversations, memory, profile, documents.

FTS5 gives keyword search, sqlite-vec gives vector search; together they make hybrid search.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import sqlite_vec

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Conversations -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT,                -- running summary of older turns (context management)
    summarized_upto INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT              -- soft delete: Lea can restore; never hard-deleted silently
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,          -- user | assistant | tool
    content TEXT NOT NULL,       -- what Lea sees
    model_content TEXT,          -- what the model saw (with <context>), keeps the prompt cache stable
    tool_calls TEXT,             -- JSON
    sources TEXT,                -- JSON list of {title,url|path}
    trace TEXT,                  -- JSON timing trace
    mode TEXT,                   -- fast | thinking
    channel TEXT,                -- chat | voice | phone
    feedback INTEGER,            -- 1 thumbs up, -1 thumbs down
    edited_content TEXT,         -- "edit to what I wanted"
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(conversation_id UNINDEXED, text);

-- Memory: facts about Lea ---------------------------------------------------
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT,                    -- e.g. lea.city; same key + newer fact = supersedes
    text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    source TEXT NOT NULL,        -- conversation id, 'lea', 'dashboard'
    sensitive INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by INTEGER REFERENCES facts(id),
    forgotten_at TEXT            -- "Sylvie, forget that": hidden from recall, kept for undo
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text, content='facts', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF text ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;

-- Profile: style traits, preferences, interests -----------------------------
CREATE TABLE IF NOT EXISTS traits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,   -- e.g. prefers_short_answers
    description TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.3,
    status TEXT NOT NULL DEFAULT 'guess',  -- guess | confirmed | rejected
    evidence TEXT NOT NULL DEFAULT '[]',   -- JSON list of short quotes
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,          -- "Keep answers short", "Don't use emojis"
    scope TEXT NOT NULL DEFAULT 'style',
    source_quote TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
-- Claude's own memory notes about Lea, waiting for her decision (memory/claude_memory.py)
CREATE TABLE IF NOT EXISTS memory_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL,        -- claude.ai | claude-code:<project>
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | fact | rule | dismissed
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS interests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL UNIQUE,
    score REAL NOT NULL DEFAULT 1.0,
    mentions INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'guess',  -- guess | confirmed | rejected
    asked_at TEXT,               -- when Sylvie asked Lea to confirm
    last_seen TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Documents ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    mtime REAL NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    location TEXT,               -- "page 3", "slide 2", "lines 10-40"
    text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- Feedback & training queue (Phase 3+) --------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def vec_schema(dim: int) -> str:
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(embedding float[{dim}] distance_metric=cosine);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}] distance_metric=cosine);
"""


class Database:
    """Thread-safe wrapper: one connection, one lock. SQLite is plenty fast for one user."""

    def __init__(self, path: Path | str, dim: int = 1024):
        self.path = str(path)
        self.dim = dim
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.lock:
            self.conn.executescript(SCHEMA + vec_schema(dim))
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params=()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def transaction(self):
        return _Tx(self)

    def close(self) -> None:
        self.conn.close()


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN")
        return self.db.conn

    def __exit__(self, exc_type, *_):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()
