"""Conversation history shared by chat, voice and phone (one history for everything)."""

from __future__ import annotations

import json
import uuid

from core.db import Database
from memory.store import utcnow


class Conversations:
    def __init__(self, db: Database):
        self.db = db
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        cols = {r["name"] for r in self.db.query("PRAGMA table_info(conversations)")}
        if "system_prompt" not in cols:
            self.db.execute("ALTER TABLE conversations ADD COLUMN system_prompt TEXT")
        if "aside_used" not in cols:
            self.db.execute("ALTER TABLE conversations ADD COLUMN aside_used INTEGER NOT NULL DEFAULT 0")

    def create(self, title: str = "New chat") -> dict:
        cid = uuid.uuid4().hex[:16]
        now = utcnow()
        self.db.execute(
            "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (cid, title, now, now),
        )
        return self.get(cid)

    def get(self, cid: str) -> dict | None:
        r = self.db.one("SELECT * FROM conversations WHERE id = ? AND deleted_at IS NULL", (cid,))
        return dict(r) if r else None

    def list(self, search: str | None = None, limit: int = 200) -> list[dict]:
        if search:
            from memory.recall import fts_query

            q = fts_query(search)
            if not q:
                return []
            rows = self.db.query(
                "SELECT DISTINCT c.* FROM conversations c JOIN conversations_fts f ON f.conversation_id = c.id "
                "WHERE conversations_fts MATCH ? AND c.deleted_at IS NULL ORDER BY c.updated_at DESC LIMIT ?",
                (q, limit),
            )
            title_rows = self.db.query(
                "SELECT * FROM conversations WHERE title LIKE ? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
                (f"%{search}%", limit),
            )
            seen, out = set(), []
            for r in list(title_rows) + list(rows):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    out.append(dict(r))
            return out
        rows = self.db.query(
            "SELECT * FROM conversations WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def rename(self, cid: str, title: str) -> None:
        self.db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title.strip()[:120], cid))

    def turn_used_tool(self, message_id: int, tool: str) -> bool:
        """Did the turn that produced this answer call `tool`? (Looks back to the user's message.)"""
        m = self.db.one("SELECT conversation_id FROM messages WHERE id = ?", (message_id,))
        if m is None:
            return False
        start = (
            self.db.one(
                "SELECT max(id) i FROM messages WHERE conversation_id = ? AND role = 'user' AND id < ?",
                (m["conversation_id"], message_id),
            )["i"]
            or 0
        )
        for r in self.db.query(
            "SELECT tool_calls FROM messages WHERE conversation_id = ? AND id > ? AND id <= ? AND tool_calls IS NOT NULL",
            (m["conversation_id"], start, message_id),
        ):
            if f'"{tool}"' in (r["tool_calls"] or ""):
                return True
        return False

    def delete(self, cid: str) -> None:
        """Soft delete: hidden from the list, recoverable from the data folder. What Sylvie learned from
        it (facts, profile, episodes, night practice) is kept on purpose."""
        self.db.execute("UPDATE conversations SET deleted_at = ? WHERE id = ?", (utcnow(), cid))

    def set(self, cid: str, **fields) -> None:
        for k, v in fields.items():
            assert k in ("system_prompt", "summary", "summarized_upto", "aside_used", "title")
            self.db.execute(f"UPDATE conversations SET {k} = ? WHERE id = ?", (v, cid))

    def add_message(
        self,
        cid: str,
        role: str,
        content: str,
        model_content: str | None = None,
        tool_calls: list | None = None,
        sources: list | None = None,
        trace: dict | None = None,
        mode: str | None = None,
        channel: str = "chat",
    ) -> int:
        now = utcnow()
        mid = self.db.execute(
            "INSERT INTO messages(conversation_id, role, content, model_content, tool_calls, sources, trace, mode, channel, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                cid,
                role,
                content,
                model_content,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                json.dumps(sources, ensure_ascii=False) if sources else None,
                json.dumps(trace) if trace else None,
                mode,
                channel,
                now,
            ),
        ).lastrowid
        self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))
        if role in ("user", "assistant") and content.strip():
            self.db.execute(
                "INSERT INTO conversations_fts(conversation_id, text) VALUES (?, ?)", (cid, content)
            )
        return mid

    def messages(self, cid: str, after_id: int = 0) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id", (cid, after_id)
        )
        out = []
        for r in rows:
            d = dict(r)
            for k in ("tool_calls", "sources", "trace"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    def visible_messages(self, cid: str) -> list[dict]:
        """What the chat UI shows: user messages and final assistant answers."""
        return [m for m in self.messages(cid) if m["role"] in ("user", "assistant") and m["content"].strip()]

    def message(self, mid: int) -> dict | None:
        r = self.db.one("SELECT * FROM messages WHERE id = ?", (mid,))
        return dict(r) if r else None

    def set_feedback(self, mid: int, value: int | None) -> None:
        self.db.execute("UPDATE messages SET feedback = ? WHERE id = ?", (value, mid))

    def set_edit(self, mid: int, text: str) -> None:
        self.db.execute("UPDATE messages SET edited_content = ? WHERE id = ?", (text, mid))

    def log_event(self, kind: str, payload: dict) -> None:
        self.db.execute(
            "INSERT INTO events(kind, payload, created_at) VALUES (?, ?, ?)",
            (kind, json.dumps(payload, ensure_ascii=False), utcnow()),
        )
