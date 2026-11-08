"""Lea's WhatsApp chats, read on this Mac, read-only (Lea, 2026-11-08: "connect to my WhatsApp").

WhatsApp Desktop keeps every chat in a local database (ChatStorage.sqlite in its group container).
Sylvie reads a snapshot copy of it, so WhatsApp's own file is never touched or locked. Nothing is
sent anywhere and nothing is sent *to* anyone: Sylvie can find and summarise messages, not write them.

Messages are other people's words: tool results are untrusted (no actions follow without Lea's yes)
and a turn that read them can't search the web or ask Claude (tools/registry.py), so nothing private
leaves the Mac in a query. Never training data (rule 4); only a short "who she talks to most" fact
goes to memory.
"""

from __future__ import annotations

import shutil
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DIR = Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
APPLE_EPOCH = 978307200  # 2001-01-01, the start of WhatsApp's (Core Data) timestamps
MEDIA = {1: "[photo]", 2: "[video]", 3: "[voice message]", 8: "[document]", 15: "[sticker]"}
TEXT_TYPES = (0, 7)  # plain text, text with a link


@dataclass
class Message:
    when: datetime
    chat: str
    sender: str  # "Lea" for her own messages
    text: str
    group: bool

    def line(self) -> str:
        where = f" (in {self.chat})" if self.group else ""
        return f"[{self.when:%Y-%m-%d %H:%M}] {self.sender}{where}: {self.text}"


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _date(t) -> datetime | None:
    return datetime.fromtimestamp(t + APPLE_EPOCH) if t else None


class WhatsApp:
    def __init__(self, folder: Path = DEFAULT_DIR, cache_dir: Path | None = None):
        self.folder = Path(folder)
        self.cache_dir = Path(cache_dir) if cache_dir else None

    @property
    def source(self) -> Path:
        return self.folder / "ChatStorage.sqlite"

    def available(self) -> bool:
        try:
            with open(self.source, "rb"):
                return True
        except OSError:
            return False

    def _snapshot(self) -> Path:
        """A private copy (database + write-ahead log), refreshed when WhatsApp's file changed."""
        if self.cache_dir is None:
            return self.source
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        copy = self.cache_dir / "ChatStorage.sqlite"
        marker = self.cache_dir / "source-mtime"
        newest = str(
            max(
                (p.stat().st_mtime for p in self.folder.glob("ChatStorage.sqlite*") if p.is_file()), default=0
            )
        )
        if not copy.exists() or not marker.exists() or marker.read_text() != newest:
            for old in self.cache_dir.glob("ChatStorage.sqlite*"):
                old.unlink()  # Sylvie's own copy, not Lea's file
            for p in self.folder.glob("ChatStorage.sqlite*"):
                shutil.copy2(p, self.cache_dir / p.name)
            marker.write_text(newest)
        return copy

    def _connect(self) -> sqlite3.Connection:
        path = self._snapshot()
        # Not read-only: the copy's write-ahead log must be merged in to see the newest messages.
        mode = "ro" if path == self.source else "rw"
        return sqlite3.connect(f"file:{path}?mode={mode}", uri=True)

    def _messages(self, since: datetime | None = None) -> list[Message]:
        sql = """
            SELECT m.ZMESSAGEDATE, m.ZISFROMME, m.ZMESSAGETYPE, m.ZTEXT, s.ZPARTNERNAME, s.ZSESSIONTYPE,
                   COALESCE(g.ZCONTACTNAME, g.ZFIRSTNAME, m.ZPUSHNAME, p.ZPUSHNAME, '')
            FROM ZWAMESSAGE m
            JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
            LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
            LEFT JOIN ZWAPROFILEPUSHNAME p ON p.ZJID = COALESCE(g.ZMEMBERJID, m.ZFROMJID)
            WHERE m.ZMESSAGEDATE >= ?
            ORDER BY m.ZMESSAGEDATE
        """
        start = (since.timestamp() - APPLE_EPOCH) if since else 0
        out = []
        with self._connect() as db:
            for t, mine, kind, text, chat, stype, member in db.execute(sql, (start,)):
                if kind in TEXT_TYPES and text:
                    body = text.strip()
                elif kind in MEDIA:
                    body = MEDIA[kind] + (f" {text.strip()}" if text else "")
                else:
                    continue  # group events, reactions, calls
                group = stype == 1
                sender = "Lea" if mine else (member or chat or "?") if group else (chat or member or "?")
                out.append(Message(_date(t), chat or "?", sender, body, group))
        return out

    def last_date(self) -> datetime | None:
        with self._connect() as db:
            (t,) = db.execute("SELECT MAX(ZMESSAGEDATE) FROM ZWAMESSAGE").fetchone()
        return _date(t)

    def chats(self, limit: int = 15) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT ZPARTNERNAME, ZSESSIONTYPE, ZLASTMESSAGEDATE, ZUNREADCOUNT FROM ZWACHATSESSION "
                "WHERE ZPARTNERNAME IS NOT NULL AND ZSESSIONTYPE IN (0, 1) AND COALESCE(ZREMOVED, 0) = 0 "
                "ORDER BY ZLASTMESSAGEDATE DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"name": n, "group": t == 1, "last": _date(d), "unread": u or 0} for n, t, d, u in rows]

    def search(
        self, text: str = "", contact: str = "", days: int | None = None, limit: int = 40
    ) -> list[Message]:
        """Messages matching words and/or a chat or person; the newest `limit`, oldest first."""
        last = self.last_date()
        since = (last - timedelta(days=days)) if (days and last) else None
        words = [w for w in _fold(text).split() if len(w) > 2]
        who = _fold(contact).strip()
        hits = []
        for m in self._messages(since):
            if who and who not in _fold(m.chat) and who not in _fold(m.sender):
                continue
            if words and not all(w in _fold(m.text) for w in words):
                continue
            hits.append(m)
        return hits[-limit:]

    def phone_for(self, name: str) -> str | None:
        """The phone number of the one-to-one chat named like `name` ('Mamá' → +34…)."""
        want = _fold(name).strip()
        with self._connect() as db:
            rows = db.execute(
                "SELECT ZPARTNERNAME, ZCONTACTJID FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 0 AND ZCONTACTJID LIKE '%@s.whatsapp.net'"
            ).fetchall()
        exact = [j for n, j in rows if n and _fold(n) == want]
        words = [j for n, j in rows if n and want in _fold(n).split()]
        hits = exact or words
        return "+" + hits[0].split("@")[0] if len(set(hits)) == 1 else None

    def top_people(self, days: int = 365, n: int = 10) -> list[tuple[str, int]]:
        """The one-to-one chats with the most messages over the last `days` of the database."""
        last = self.last_date()
        since = (last - timedelta(days=days)) if last else None
        counts = Counter(m.chat for m in self._messages(since) if not m.group)
        return counts.most_common(n)


def get(cfg) -> WhatsApp | None:
    """Lea's WhatsApp on this Mac, or None (not installed, never used here, or turned off)."""
    if not cfg.get_path("whatsapp.enabled", True):
        return None
    wa = WhatsApp(Path(cfg.get_path("whatsapp.folder", str(DEFAULT_DIR))).expanduser(),
                  cfg.data_dir / "app-data" / "whatsapp")  # fmt: skip
    return wa if wa.available() else None


def answer_lines(wa: WhatsApp, text: str = "", contact: str = "", days: int | None = None) -> str:
    """What the whatsapp tool gives the model."""
    last = wa.last_date()
    stale = ""
    if last and datetime.now() - last > timedelta(days=14):
        stale = (
            f"\n(Note: WhatsApp on this Mac was last updated on {last:%d %B %Y}; newer messages aren't here. "
            "Tell Lea if it matters. Opening WhatsApp on the Mac brings it up to date.)"
        )
    if not text and not contact:
        rows = wa.chats()
        if not rows:
            return "No WhatsApp chats on this Mac." + stale
        lines = [
            f"- {c['name']}{' (group)' if c['group'] else ''}: last message {c['last']:%Y-%m-%d %H:%M}"
            + (f", {c['unread']} unread" if c["unread"] else "")
            for c in rows
            if c["last"]
        ]
        return "Lea's most recent WhatsApp chats:\n" + "\n".join(lines) + stale
    hits = wa.search(text, contact, days)
    if not hits:
        what = " and ".join(
            x for x in (f"'{text}'" if text else "", f"with {contact}" if contact else "") if x
        )
        return f"No WhatsApp messages found {what}." + stale
    return "WhatsApp messages (oldest first):\n" + "\n".join(m.line() for m in hits) + stale
