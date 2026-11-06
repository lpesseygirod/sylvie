"""Learn from Lea's own messages to Claude Code, so Sylvie knows what Lea is working on and how she
likes answers, even when she talked to Claude instead of Sylvie.

Claude Code keeps every session as a JSON-lines file under ~/.claude/projects/<project>/. Every few
minutes this reads what was appended since last time and keeps only the messages Lea typed
(origin "human"): never Claude's answers, tool output, pasted text, task notifications or briefs
written by other Claude sessions. Each message then goes through Sylvie's normal background
learning. Nothing is copied into conversations or training data, and nothing leaves the Mac.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("sylvie.claude_history")

OFFSETS_KEY = "claude_history.offsets"
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
        self._stop = threading.Event()

    def _offsets(self) -> dict[str, int]:
        rows = self.s.db.query("SELECT value FROM meta WHERE key = ?", (OFFSETS_KEY,))
        try:
            return json.loads(rows[0]["value"]) if rows else {}
        except ValueError:
            return {}

    def _save_offsets(self, offsets: dict[str, int]) -> None:
        self.s.db.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (OFFSETS_KEY, json.dumps(offsets)),
        )

    def scan(self) -> int:
        """Queue every new message of Lea's. Returns how many were queued."""
        if not self.enabled:
            return 0
        offsets = self._offsets()
        msgs = read_new(self.folder, offsets, self.exclude)
        for m in msgs:
            source = f"claude:{m.session}#{m.uuid}"
            self.s.bg.submit(
                "learn-claude", lambda ev, m=m, source=source: self.s.learn_from_claude(m.text, source, ev)
            )
        self._save_offsets(offsets)
        if msgs:
            log.info("queued %d of Lea's Claude Code messages for learning", len(msgs))
        return len(msgs)

    def start(self) -> None:
        if not self.enabled:
            return

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
