"""Learn from Lea's own messages to Claude Code, so Sylvie knows what Lea is working on and how she
likes answers, even when she talked to Claude instead of Sylvie.

Two sources, both checked every few minutes:
- Claude Code keeps every session as a JSON-lines file under ~/.claude/projects/<project>/. New lines
  are read from where we stopped, keeping only the messages Lea typed (origin "human"): never
  Claude's answers, tool output, pasted text, task notifications or briefs from other Claude sessions.
- Chats on claude.ai (web, phone, the Claude app) live on Anthropic's servers. Lea downloads them
  (Settings → Privacy → Export data) and drops the zip in the exports folder; only her own turns
  ("sender": "human") are read, never Claude's replies or attached files' contents. Each message then goes through Sylvie's normal background
learning. Nothing is copied into conversations or training data, and nothing leaves the Mac.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("sylvie.claude_history")

OFFSETS_KEY = "claude_history.offsets"
EXPORT_SEEN_KEY = "claude_history.export_seen"
EXPORT_FILES_KEY = "claude_history.export_files"
PENDING_KEY = "claude_history.pending"  # read but not learned from yet: survives restarts
MAX_EXPORT_BYTES = 1_000_000_000
# Text Lea didn't write herself: system notes, pasted text, IDE selections, command output.
TAGGED_BLOCK_RE = re.compile(r"<([a-z_-]+)(?:\s[^>]*)?>.*?</\1>", re.S)
MIN_CHARS = 12


@dataclass
class ClaudeMessage:
    uuid: str
    session: str
    project: str
    at: str
    text: str


def lea_text(entry: dict) -> str | None:
    """Lea's own words from one transcript line, or None if she didn't type it."""
    if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
        return None
    if entry.get("isCompactSummary") or entry.get("isVisibleInTranscriptOnly"):
        return None
    origin = (entry.get("origin") or {}).get("kind")
    if origin != "human" and entry.get("turnOrigin") != "human":
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    if not isinstance(content, str):
        return None
    text = TAGGED_BLOCK_RE.sub("", content).strip()
    if len(text) < MIN_CHARS or text.startswith(("<", "/")):
        return None
    return text


def read_new(folder: Path, offsets: dict[str, int], exclude: set[str]) -> list[ClaudeMessage]:
    """Messages appended since `offsets` (updated in place). Only whole lines are consumed."""
    out: list[ClaudeMessage] = []
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*/*.jsonl")):
        project = path.parent.name
        if project in exclude:
            continue
        key = str(path)
        start = offsets.get(key, 0)
        try:
            size = path.stat().st_size
            if size < start:  # file was rewritten: read it again from the top
                start = 0
            if size == start:
                continue
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(size - start)
        except OSError:
            continue
        end = data.rfind(b"\n")
        if end < 0:
            continue
        offsets[key] = start + end + 1
        for line in data[: end + 1].splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            text = lea_text(entry)
            if text:
                out.append(
                    ClaudeMessage(
                        str(entry.get("uuid") or ""),
                        str(entry.get("sessionId") or path.stem),
                        project,
                        str(entry.get("timestamp") or ""),
                        text,
                    )
                )
    return out


def _conversations_in(data) -> list[dict]:
    """Conversations from one parsed JSON value: a list of them, one, or {"conversations": [...]}."""
    if isinstance(data, dict) and isinstance(data.get("conversations"), list):
        data = data["conversations"]
    items = data if isinstance(data, list) else [data]
    return [c for c in items if isinstance(c, dict) and isinstance(_turns(c), list)]


def _turns(conv: dict):
    return conv.get("chat_messages", conv.get("messages"))


def _parse(name: str, raw: bytes) -> list[dict]:
    if name.endswith(".jsonl"):
        out = []
        for line in raw.splitlines():
            try:
                out += _conversations_in(json.loads(line))
            except ValueError:
                continue
        return out
    try:
        return _conversations_in(json.loads(raw))
    except ValueError:
        return []


def _export_conversations(path: Path) -> list[dict]:
    """Conversations from a claude.ai export: the zip as downloaded (older exports hold
    conversations.json, newer ones come as several zips and only conversations-000.zip matters),
    or a JSON file. Read in memory: nothing is unpacked to disk."""
    if path.suffix != ".zip":
        if path.stat().st_size > MAX_EXPORT_BYTES:
            return []
        return _parse(path.name, path.read_bytes())
    out: list[dict] = []
    with zipfile.ZipFile(path) as z:
        budget = MAX_EXPORT_BYTES
        for info in z.infolist():
            if not info.filename.endswith((".json", ".jsonl")) or info.file_size > budget:
                continue
            budget -= info.file_size
            out += _parse(info.filename, z.read(info))
    return out


def read_export(path: Path) -> list[ClaudeMessage]:
    """Lea's own messages from a claude.ai export. Other formats (e.g. other chat apps) give []."""
    out: list[ClaudeMessage] = []
    for conv in _export_conversations(path):
        for m in _turns(conv):
            if not isinstance(m, dict) or (m.get("sender") or m.get("role")) not in ("human", "user"):
                continue
            text = m.get("text")
            if not text and isinstance(m.get("content"), list):
                text = "\n".join(
                    b.get("text", "") for b in m["content"] if isinstance(b, dict) and b.get("type") == "text"
                )
            text = TAGGED_BLOCK_RE.sub("", str(text or "")).strip()
            if len(text) >= MIN_CHARS:
                out.append(
                    ClaudeMessage(
                        str(m.get("uuid") or ""),
                        str(conv.get("uuid") or ""),
                        "claude.ai",
                        str(m.get("created_at") or ""),
                        text,
                    )
                )
    return out


class ClaudeHistory:
    """Checks for new messages every few minutes and queues them for background learning."""

    def __init__(self, sylvie, every_seconds: float = 300.0):
        self.s = sylvie
        self.every = every_seconds
        cfg = sylvie.cfg
        self.enabled = bool(cfg.get_path("memory.claude_history.enabled", False))
        self.folder = Path(
            str(cfg.get_path("memory.claude_history.folder", "~/.claude/projects"))
        ).expanduser()
        self.exclude = set(cfg.get_path("memory.claude_history.exclude_projects", []) or [])
        self.exports = Path(
            str(
                cfg.get_path(
                    "memory.claude_history.exports_folder", "~/Documents/Projects/Apps/Sylvie/Claude exports"
                )
            )
        ).expanduser()
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _get(self, key: str, default):
        rows = self.s.db.query("SELECT value FROM meta WHERE key = ?", (key,))
        try:
            return json.loads(rows[0]["value"]) if rows else default
        except ValueError:
            return default

    def _set(self, key: str, value) -> None:
        self.s.db.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def _new_from_exports(self) -> list[ClaudeMessage]:
        """Messages from exports Lea dropped in the folder. Each export holds her whole history, so
        messages already learned from an earlier export are skipped by id."""
        if not self.exports.is_dir():
            return []
        files = self._get(EXPORT_FILES_KEY, {})
        seen = set(self._get(EXPORT_SEEN_KEY, []))
        out: list[ClaudeMessage] = []
        for path in sorted([*self.exports.glob("*.zip"), *self.exports.glob("*.json")]):
            st = path.stat()
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            if files.get(path.name) == sig:
                continue
            try:
                msgs = read_export(path)
            except (OSError, ValueError, zipfile.BadZipFile) as e:
                log.warning("couldn't read Claude export %s: %s", path.name, e)
                continue
            files[path.name] = sig
            for m in msgs:
                if m.uuid not in seen:
                    seen.add(m.uuid)
                    out.append(m)
        self._set(EXPORT_FILES_KEY, files)
        self._set(EXPORT_SEEN_KEY, sorted(seen))
        return out

    def scan(self) -> int:
        """Queue every new message of Lea's. Returns how many were queued."""
        if not self.enabled:
            return 0
        offsets = self._get(OFFSETS_KEY, {})
        msgs = read_new(self.folder, offsets, self.exclude) + self._new_from_exports()
        with self._lock:
            pending = self._get(PENDING_KEY, [])
            new = [[f"claude:{m.session}#{m.uuid}", m.text] for m in msgs]
            self._set(PENDING_KEY, pending + new)
        self._set(OFFSETS_KEY, offsets)
        for source, text in new:
            self._queue(source, text)
        if msgs:
            log.info("queued %d of Lea's Claude messages for learning", len(msgs))
        return len(msgs)

    def _queue(self, source: str, text: str) -> None:
        def job(ev):
            self.s.learn_from_claude(text, source, ev)
            if not ev.is_set():
                with self._lock:
                    self._set(PENDING_KEY, [p for p in self._get(PENDING_KEY, []) if p[0] != source])

        self.s.bg.submit("learn-claude", job)

    def resume(self) -> int:
        """After a restart: queue what was read but not learned from yet."""
        pending = self._get(PENDING_KEY, [])
        for source, text in pending:
            self._queue(source, text)
        return len(pending)

    def start(self) -> None:
        if not self.enabled:
            return
        self.exports.mkdir(parents=True, exist_ok=True)
        if n := self.resume():
            log.info("resuming %d Claude messages not learned from yet", n)

        def loop():
            while not self._stop.is_set():
                try:
                    self.scan()
                except Exception:
                    log.exception("reading Claude Code history failed")
                self._stop.wait(self.every)

        threading.Thread(target=loop, daemon=True, name="sylvie-claude-history").start()

    def stop(self) -> None:
        self._stop.set()
