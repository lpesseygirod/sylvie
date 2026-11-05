"""Index of the folders Lea chooses (course PDFs, notes…) for answers with citations.

Only folders listed in config.yaml → knowledge.folders are indexed (decision 4). Files are
re-indexed when their modification time changes. Supported: PDF (per page), Markdown/text/code.
"""

from __future__ import annotations

import logging
import os
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
    return []


class DocsIndex:
    def __init__(self, db: Database, embedder):
        self.db = db
        self.embedder = embedder

    def index_file(self, path: Path) -> int:
        path = path.resolve()
        mtime = path.stat().st_mtime
        row = self.db.one("SELECT id, mtime FROM documents WHERE path = ?", (str(path),))
        if row and abs(row["mtime"] - mtime) < 1e-6:
            return 0
        pieces = []
        for loc, text in read_file(path):
            for i, ch in enumerate(chunk_text(text)):
                pieces.append((loc or f"part {i + 1}", ch))
        vecs = self.embedder.embed([f"{path.name}: {t}" for _, t in pieces]) if pieces else []
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

    def index_folders(self, folders: list[str]) -> dict:
        seen, counts = set(), {"files": 0, "chunks": 0, "errors": 0}
        for folder in folders:
            root = Path(folder).expanduser()
            if not root.is_dir():
                log.warning("not a folder: %s", root)
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__")
                ]
                for name in filenames:
                    p = Path(dirpath) / name
                    if name.startswith(".") or p.suffix.lower() not in TEXT_EXT | PDF_EXT:
                        continue
                    if p.stat().st_size > MAX_FILE_BYTES:
                        continue
                    seen.add(str(p.resolve()))
                    try:
                        n = self.index_file(p)
                        counts["files"] += 1
                        counts["chunks"] += n
                    except Exception as e:  # one broken PDF shouldn't stop the whole index
                        log.warning("could not index %s: %s", p, e)
                        counts["errors"] += 1
        # Forget the index entries (not the files!) of files that were removed from the folders.
        with self.db.transaction() as c:
            for r in c.execute("SELECT id, path FROM documents").fetchall():
                if r["path"] not in seen:
                    self._remove_doc(c, r["id"])
        return counts

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

    def stats(self) -> dict:
        return {
            "documents": self.db.one("SELECT count(*) n FROM documents")["n"],
            "chunks": self.db.one("SELECT count(*) n FROM chunks")["n"],
        }
