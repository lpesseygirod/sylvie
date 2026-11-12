"""Episodic memory: what was actually said in past conversations, searchable by words and meaning.

Facts (memory/store.py) keep what Sylvie learned about Lea; this keeps the conversations themselves.
"if I said something even 2 months ago she should remember". Each exchange (Lea's
message + Sylvie's answer) is one episode, indexed with FTS5 and an embedding. Every turn, the few
most relevant episodes from *other* conversations are given to Sylvie with their dates, and she has
a tool to search on purpose. Deleting a conversation only hides it from the list: Sylvie still
remembers what was said in it ("delete conversations but Sylvie not lose the
knowledge learned from it").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from knowledge.embeddings import to_blob
from memory.recall import fts_query
from memory.store import utcnow

MAX_CHARS = 700  # per episode: enough to remember what it was about
AUTO_MIN_SIM = 0.55  # only clearly related episodes are brought up on their own


def schema(dim: int) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    user_message_id INTEGER UNIQUE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(text);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_vec USING vec0(embedding float[{dim}] distance_metric=cosine);
"""


@dataclass
class Episode:
    id: int
    conversation_id: str
    text: str
    created_at: str
    score: float = 0.0

    def line(self, now: datetime | None = None) -> str:
        return f"[{_when(self.created_at, now)}] {self.text}"


def _when(iso: str, now: datetime | None = None) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:10]
    days = ((now or datetime.now(UTC)) - t).days
    ago = (
        "today"
        if days < 1
        else "yesterday"
        if days < 2
        else f"{days} days ago"
        if days < 60
        else f"{days // 30} months ago"
    )
    return f"{t.date().isoformat()}, {ago}"


class Episodes:
    def __init__(self, db, embedder):
        self.db, self.embedder = db, embedder
        self.db.conn.executescript(schema(db.dim))

    def add(self, conversation_id: str, user_message_id: int | None, question: str, answer: str,
            created_at: str | None = None) -> int | None:  # fmt: skip
        if not question.strip():
            return None
        text = f"Lea: {question.strip()}\nSylvie: {answer.strip()}"[:MAX_CHARS]
        if user_message_id is not None and self.db.one(
            "SELECT 1 FROM episodes WHERE user_message_id = ?", (user_message_id,)
        ):
            return None
        eid = self.db.execute(
            "INSERT INTO episodes(conversation_id, user_message_id, text, created_at) VALUES (?,?,?,?)",
            (conversation_id, user_message_id, text, created_at or utcnow()),
        ).lastrowid
        self.db.execute("INSERT INTO episodes_fts(rowid, text) VALUES (?, ?)", (eid, text))
        vec = self.embedder.embed_one(text)
        self.db.execute("INSERT INTO episodes_vec(rowid, embedding) VALUES (?, ?)", (eid, to_blob(vec)))
        return eid

    def search(self, query: str, k: int = 5, exclude_conversation: str | None = None,
               min_sim: float = 0.35) -> list[Episode]:  # fmt: skip
        """Hybrid search (keywords + meaning). score = vector similarity (0 if only keywords matched)."""
        sims: dict[int, float] = {}
        vec = self.embedder.embed_one(query, query=True)
        for r in self.db.query(
            "SELECT rowid, distance FROM episodes_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (to_blob(vec), k * 4),
        ):
            if 1 - r["distance"] >= min_sim:
                sims[r["rowid"]] = 1 - r["distance"]
        q = fts_query(query)
        if q:
            for r in self.db.query(
                "SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ? ORDER BY bm25(episodes_fts) LIMIT ?",
                (q, k * 2),
            ):
                sims.setdefault(r["rowid"], 0.0)
        out = []
        for eid, sim in sorted(sims.items(), key=lambda x: -x[1]):
            r = self.db.one(
                "SELECT * FROM episodes WHERE id = ?",
                (eid,),
            )
            if r is None or r["conversation_id"] == exclude_conversation:
                continue
            out.append(Episode(r["id"], r["conversation_id"], r["text"], r["created_at"], sim))
            if len(out) >= k:
                break
        return out

    def relevant(self, query: str, conversation_id: str, k: int = 3) -> list[Episode]:
        """For every turn: only clearly related episodes from other conversations."""
        if not self.db.one("SELECT 1 FROM episodes LIMIT 1"):
            return []
        return [
            e
            for e in self.search(query, k=k, exclude_conversation=conversation_id)
            if e.score >= AUTO_MIN_SIM
        ]

    def backfill(self, stop=None, limit: int = 2000) -> int:
        """Indexes past exchanges that aren't episodes yet (conversations before this feature)."""
        rows = self.db.query(
            "SELECT m.id, m.conversation_id, m.content, m.created_at FROM messages m "
            "WHERE m.role = 'user' "
            "AND m.id NOT IN (SELECT user_message_id FROM episodes WHERE user_message_id IS NOT NULL) "
            "ORDER BY m.id LIMIT ?",
            (limit,),
        )
        n = 0
        for r in rows:
            if stop is not None and stop.is_set():
                break
            ans = self.db.one(
                "SELECT content FROM messages WHERE conversation_id = ? AND role = 'assistant' AND id > ? "
                "AND content != '' ORDER BY id LIMIT 1",
                (r["conversation_id"], r["id"]),
            )
            if self.add(
                r["conversation_id"], r["id"], r["content"], ans["content"] if ans else "", r["created_at"]
            ):
                n += 1
        return n
