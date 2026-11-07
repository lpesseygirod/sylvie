"""Claude's own memory notes about Lea, offered to her as suggestions (Lea, 2026-11-07: "Yes, as suggestions").

Claude wrote these notes, not Lea, so nothing is used until she says so. Each note becomes a
suggestion in About Lea, where she can keep it as a fact, use it as a rule for answers, or dismiss
it. Suggestions are never used in prompts, and Claude-written text never reaches training data:
training examples carry no system prompt (learning/dataset.py refuses any that do).

Two sources, both read locally:
- claude.ai memory: the `memories-*.zip` part of a claude.ai data export, dropped in the exports
  folder next to the conversations zip.
- Claude Code memory notes: `~/.claude/projects/*/memory/*.md` (one note per file; the one-line
  `description` is offered; index files and `reference` notes are skipped).
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("sylvie.claude_memory")

MIN_CHARS, MAX_CHARS = 15, 400
MAX_FILE_BYTES = 50_000_000
SKIP_KEYS = {"uuid", "id", "created_at", "updated_at", "account_uuid", "project_uuid", "name", "type"}
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z¿¡])")
DECISIONS = {"fact", "rule", "dismissed"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def split_notes(text: str) -> list[str]:
    """A memory text (markdown-ish prose) → short standalone notes."""
    out = []
    for line in text.replace("\r", "").split("\n"):
        line = BULLET_RE.sub("", line).strip()
        heading = re.fullmatch(r"(\*\*|__).*\1:?", line) is not None
        line = line.strip("*_").strip()
        if not line or heading or line.startswith("#") or line.endswith(":"):
            continue  # headings like "**Work context**" or "## Personal" aren't notes
        parts = SENTENCE_RE.split(line) if len(line) > MAX_CHARS else [line]
        out += [p.strip() for p in parts if MIN_CHARS <= len(p.strip()) <= MAX_CHARS]
    return out


def _strings(data, key: str | None = None):
    if isinstance(data, str):
        if key not in SKIP_KEYS and len(data) >= MIN_CHARS:
            yield data
    elif isinstance(data, dict):
        for k, v in data.items():
            yield from _strings(v, k)
    elif isinstance(data, list):
        for v in data:
            yield from _strings(v, key)


def _parse(raw: bytes) -> list:
    text = raw.decode("utf-8", errors="replace")
    try:
        return [json.loads(text)]
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip().startswith(("{", "["))]


def read_claude_ai_memories(path: Path) -> list[str]:
    """Notes from a `memories-*.zip` (or a loose memories .json) of a claude.ai export."""
    blobs: list[bytes] = []
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if (
                        info.filename.endswith((".json", ".jsonl", ".md", ".txt"))
                        and info.file_size < MAX_FILE_BYTES
                    ):
                        blobs.append(z.read(info))
        else:
            blobs.append(path.read_bytes())
    except (OSError, zipfile.BadZipFile) as e:
        log.warning("Could not read %s: %s", path.name, e)
        return []
    notes: list[str] = []
    for raw in blobs:
        try:
            docs = _parse(raw)
        except ValueError:
            docs = [raw.decode("utf-8", errors="replace")]  # plain markdown / text
        for doc in docs:
            for s in _strings(doc):
                notes += split_notes(s)
    return list(dict.fromkeys(notes))


def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    out = {}
    for line in (m.group(1) if m else "").splitlines():
        k, _, v = line.strip().partition(":")
        if v:
            out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return out


def read_claude_code_notes(folder: Path, exclude: set[str] = frozenset()) -> list[tuple[str, str]]:
    """(note, project) from Claude Code's per-project memory folders."""
    out = []
    for f in sorted(folder.glob("*/memory/*.md")):
        if f.name == "MEMORY.md" or f.parent.parent.name in exclude:
            continue
        try:
            meta = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        desc = meta.get("description", "")
        if meta.get("type") == "reference" or not (MIN_CHARS <= len(desc) <= MAX_CHARS):
            continue
        out.append((desc, f.parent.parent.name))
    return out


class ClaudeMemory:
    """The suggestions list: filled from Claude's notes, decided by Lea."""

    def __init__(self, sylvie):
        self.s = sylvie
        self.db = sylvie.db  # table memory_suggestions: core/db.py

    def _offer(self, text: str, origin: str) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO memory_suggestions(text, origin, created_at) VALUES (?, ?, ?)",
            (text, origin, _now()),
        )
        return bool(cur.rowcount)

    def scan(self, claude_folder: Path, exports_folder: Path, exclude: set[str] = frozenset()) -> int:
        """Adds notes not seen before (a dismissed note never comes back). Returns how many are new."""
        new = 0
        if exports_folder.is_dir():
            for f in sorted(exports_folder.iterdir()):
                if f.name.lower().startswith("memories") and f.suffix in (".zip", ".json", ".jsonl"):
                    new += sum(self._offer(n, "claude.ai") for n in read_claude_ai_memories(f))
        if claude_folder.is_dir():
            for note, project in read_claude_code_notes(claude_folder, exclude):
                new += self._offer(note, f"claude-code:{project}")
        if new:
            log.info("%d new suggestions from Claude's memory", new)
        return new

    def list(self, status: str = "pending") -> list[dict]:
        rows = self.db.query("SELECT * FROM memory_suggestions WHERE status = ? ORDER BY id", (status,))
        return [dict(r) for r in rows]

    def decide(self, sid: int, decision: str, text: str | None = None) -> dict:
        """fact → added to memory; rule → an active preference; dismissed → hidden for good."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        row = self.db.one("SELECT * FROM memory_suggestions WHERE id = ?", (sid,))
        if row is None:
            raise KeyError(sid)
        text = (text or row["text"]).strip()
        result: dict = {"id": sid, "status": decision}
        if decision == "fact":
            fact, outcome = self.s.memory.add(text, category="general", source="claude-memory")
            result |= {"fact_id": fact.id, "outcome": outcome}
        elif decision == "rule":
            result["preference_id"] = self.s.prefs.add(text, scope="claude-memory", active=True)
        self.db.execute(
            "UPDATE memory_suggestions SET status = ?, decided_at = ? WHERE id = ?", (decision, _now(), sid)
        )
        return result
