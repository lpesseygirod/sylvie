"""Index of the folders Lea chooses (course PDFs, notes…) for answers with citations.

Only folders listed in config.yaml → knowledge.folders are indexed (decision 4). Files are
re-indexed when their modification time changes. Supported: PDF (per page), Markdown/text/code.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from core.db import Database
from knowledge.embeddings import to_blob
from memory.recall import hybrid_search
from memory.store import utcnow

log = logging.getLogger("sylvie.docs")

TEXT_EXT = {
    ".md",
    ".txt",
    ".markdown",
    ".rst",
    ".tex",
    ".py",
    ".swift",
    ".js",
    ".ts",
    ".java",
    ".c",
    ".cpp",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".html",
}
PDF_EXT = {".pdf"}
# Office and other document formats, read from their XML with the standard library (no new packages).
OFFICE_EXT = {".docx", ".pptx", ".xlsx", ".odt", ".odp", ".rtf"}
SF_DATALESS = 0x40000000  # macOS: a OneDrive/iCloud file that's only in the cloud (reading it downloads it)
# Pieces embedded at once; Lea's lookups and "stop" get in between. Measured 2026-11-09 on a 240-piece
# file: one call for the file made a lookup wait up to 16.9 s; batches of 16, 1.4 s (worst 2.4 s);
# batches of 8, 0.41 s (worst 0.57 s) at the same indexing speed (~11 pieces/s). Alone: 0.01 s.
EMBED_BATCH = 8
FAILED_KEY = "docs.failed"  # meta: {path: mtime} of files that couldn't be read (retried once changed)
CHUNK_CHARS = 1200
OVERLAP = 200
MAX_FILE_BYTES = 50_000_000


@dataclass
class Hit:
    path: str
    location: str
    text: str
    score: float


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        # Prefer to cut at a paragraph or sentence boundary.
        if end < len(text):
            for sep in ("\n\n", "\n", ". "):
                cut = text.rfind(sep, start + size // 2, end)
                if cut > 0:
                    end = cut + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def read_file(path: Path) -> list[tuple[str, str]]:
    """Returns [(location, text)]."""
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [(f"page {i + 1}", (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    if ext in TEXT_EXT:
        return [("", path.read_text(encoding="utf-8", errors="replace"))]
    if ext in OFFICE_EXT:
        return read_office(path)
    return []


def _xml_text(xml: str, para_tags: str) -> str:
    """Text of an Office XML part: paragraphs become lines, tags go, entities are decoded."""
    import html
    import re

    xml = re.sub(rf"</({para_tags})>", "\n", xml)
    xml = re.sub(r"<(w:tab|a:tab|text:tab)/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def read_office(path: Path) -> list[tuple[str, str]]:
    """Word, PowerPoint, Excel, OpenDocument and RTF → [(location, text)]."""
    import re
    import zipfile

    ext = path.suffix.lower()
    if ext == ".rtf":
        raw = path.read_text(encoding="latin-1", errors="replace")
        raw = re.sub(r"\\par[d]?\b", "\n", raw)
        return [("", re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw))]
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

        def part(n: str) -> str:
            return z.read(n).decode("utf-8", "replace")

        if ext == ".docx":
            return [("", _xml_text(part("word/document.xml"), "w:p"))] if "word/document.xml" in names else []
        if ext == ".pptx":
            slides = sorted(
                (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                key=lambda n: int(re.findall(r"\d+", n)[0]),
            )
            return [(f"slide {i + 1}", _xml_text(part(n), "a:p")) for i, n in enumerate(slides)]
        if ext == ".xlsx":
            shared = (
                re.findall(r"<si>(.*?)</si>", part("xl/sharedStrings.xml"), re.S)
                if "xl/sharedStrings.xml" in names
                else []
            )
            return [("", "\n".join(_xml_text(x, "a:p").strip() for x in shared))]
        if ext in (".odt", ".odp") and "content.xml" in names:
            return [("", _xml_text(part("content.xml"), "text:p|text:h"))]
    return []


class DocsIndex:
    def __init__(self, db: Database, embedder):
        self.db = db
        self.embedder = embedder

    def index_file(self, path: Path, stop=None) -> int | None:
        """Pieces indexed (0: unchanged). None when `stop` was set midway: nothing is saved for the
        file, so the next run does it again."""
        path = path.resolve()
        mtime = path.stat().st_mtime
        row = self.db.one("SELECT id, mtime FROM documents WHERE path = ?", (str(path),))
        if row and abs(row["mtime"] - mtime) < 1e-6:
            return 0
        pieces = []
        for loc, text in read_file(path):
            for i, ch in enumerate(chunk_text(text)):
                pieces.append((loc or f"part {i + 1}", ch))
        vecs = []
        for i in range(0, len(pieces), EMBED_BATCH):
            if stop is not None and stop.is_set():
                return None
            vecs.extend(
                self.embedder.embed(
                    [f"{path.name}: {t}" for _, t in pieces[i : i + EMBED_BATCH]], batch_size=EMBED_BATCH
                )
            )
        with self.db.transaction() as c:
            if row:
                self._remove_doc(c, row["id"])
            cur = c.execute(
                "INSERT INTO documents(path, mtime, indexed_at) VALUES (?, ?, ?)",
                (str(path), mtime, utcnow()),
            )
            doc_id = cur.lastrowid
            for (loc, text), v in zip(pieces, vecs, strict=True):
                cid = c.execute(
                    "INSERT INTO chunks(document_id, location, text) VALUES (?, ?, ?)", (doc_id, loc, text)
                ).lastrowid
                c.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)", (cid, to_blob(v)))
        return len(pieces)

    @staticmethod
    def _remove_doc(c, doc_id: int) -> None:
        for r in c.execute("SELECT id FROM chunks WHERE document_id = ?", (doc_id,)).fetchall():
            c.execute("DELETE FROM chunks_vec WHERE rowid = ?", (r[0],))
        c.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
        c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def index_folders(self, folders: list[str], stop=None) -> dict:
        """Index every document under `folders`. Code repositories (folders with .git), app libraries and
        files that are only in the cloud are skipped. `stop` (a threading.Event) pauses it for Lea."""
        seen = set()
        counts = {"files": 0, "chunks": 0, "errors": 0, "skipped_broken": 0, "stopped": False}
        failed = self._failed()
        for folder in folders:
            root = Path(folder).expanduser()
            if not root.is_dir():
                log.warning("not a folder: %s", root)
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                if (
                    ".git" in dirnames or ".venv" in dirnames
                ):  # a code project: its files aren't Lea's documents
                    dirnames[:] = []
                    continue
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not d.startswith(".")
                    and d not in ("node_modules", "__pycache__", "Library", "_to_delete")
                    and not d.endswith((".app", ".photoslibrary", ".musiclibrary"))
                ]
                for name in filenames:
                    p = Path(dirpath) / name
                    if name.startswith(".") or p.suffix.lower() not in TEXT_EXT | PDF_EXT | OFFICE_EXT:
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    if st.st_size > MAX_FILE_BYTES or getattr(st, "st_flags", 0) & SF_DATALESS:
                        continue
                    key = str(p.resolve())
                    seen.add(key)
                    if stop is not None and stop.is_set():
                        counts["stopped"] = True
                        self._set_failed(failed)
                        return counts  # nothing forgotten: the next run carries on
                    if failed.get(key) == st.st_mtime:
                        counts["skipped_broken"] += 1  # failed before and unchanged since
                        continue
                    try:
                        n = self.index_file(p, stop=stop)
                    except Exception as e:  # one broken PDF shouldn't stop the whole index
                        log.warning("could not index %s: %s", p, e)
                        counts["errors"] += 1
                        failed[key] = st.st_mtime
                        continue
                    if n is None:
                        counts["stopped"] = True
                        self._set_failed(failed)
                        return counts
                    failed.pop(key, None)
                    counts["files"] += 1
                    counts["chunks"] += n
        # Forget the index entries (not the files!) of files that were removed from the folders.
        self._set_failed({k: v for k, v in failed.items() if k in seen})
        with self.db.transaction() as c:
            for r in c.execute("SELECT id, path FROM documents").fetchall():
                if r["path"] not in seen:
                    self._remove_doc(c, r["id"])
        return counts

    def _failed(self) -> dict[str, float]:
        import json

        row = self.db.one("SELECT value FROM meta WHERE key = ?", (FAILED_KEY,))
        try:
            return json.loads(row["value"]) if row else {}
        except ValueError:
            return {}

    def _set_failed(self, failed: dict[str, float]) -> None:
        import json

        self.db.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (FAILED_KEY, json.dumps(failed)),
        )

    def search(self, query: str, k: int = 5) -> list[Hit]:
        out = []
        for rowid, score in hybrid_search(
            self.db, self.embedder, query, "chunks_fts", "chunks_vec", k=k, min_sim=0.3
        ):
            r = self.db.one(
                "SELECT c.text, c.location, d.path FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ?",
                (rowid,),
            )
            if r:
                out.append(Hit(r["path"], r["location"], r["text"], score))
            if len(out) >= k:
                break
        return out

    def find(self, name: str, limit: int = 8) -> list[str]:
        """Indexed documents whose file name has every word of `name` (accents and case ignored),
        most recently changed first."""
        import unicodedata

        def fold(x: str) -> str:
            x = unicodedata.normalize("NFKD", x.lower())
            return "".join(c for c in x if not unicodedata.combining(c))

        small = {
            "de",
            "del",
            "la",
            "el",
            "los",
            "las",
            "mi",
            "mis",
            "my",
            "the",
            "of",
            "a",
            "le",
            "du",
            "des",
            "mon",
            "ma",
            "doc",
            "documento",
            "document",
            "file",
            "archivo",
        }
        name = re.sub(
            r"\.(docx?|pptx?|xlsx?|pdf|txt|md|odt|odp|rtf|pages|key)$", "", name.strip(), flags=re.I
        )
        words = [w for w in fold(name).replace("_", " ").split() if len(w) > 1 and w not in small]
        rows = self.db.query("SELECT path, mtime FROM documents ORDER BY mtime DESC") if words else []
        return [
            r["path"] for r in rows if all(w in fold(Path(r["path"]).stem.replace("_", " ")) for w in words)
        ][:limit]

    def text_of(self, path: str, max_chars: int = 15000) -> str:
        """A document's text as indexed (its parts in order)."""
        rows = self.db.query(
            "SELECT c.location, c.text FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.path = ? ORDER BY c.id",
            (path,),
        )
        text = "\n".join(r["text"] for r in rows)  # neighbouring parts overlap a little
        return (
            text if len(text) <= max_chars else text[:max_chars] + "\n…(the rest is longer; ask for a part)"
        )

    def stats(self) -> dict:
        return {
            "documents": self.db.one("SELECT count(*) n FROM documents")["n"],
            "chunks": self.db.one("SELECT count(*) n FROM chunks")["n"],
        }
