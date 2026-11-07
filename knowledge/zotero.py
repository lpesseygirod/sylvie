"""Lea's Zotero library, read locally: her saved sources, their PDFs, her notes and highlights.

Zotero is never changed: its database is copied to Sylvie's data folder (Zotero may be open and
writing to it) and read from the copy. For each source Sylvie keeps the citation details (authors,
year, journal, DOI…) and passages to search: the abstract, Lea's notes and highlights, and the full
text of the attached PDFs (Zotero's own extracted text, `.zotero-ft-cache`, page by page; read with
pypdf when Zotero hasn't extracted it). The index is refreshed when Zotero's database changes, and
only sources that changed are read again.

Everything stays on the Mac. This is Lea's personal material: it never goes into training data or
the shareable general part (learning/general_pack.py).
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from knowledge.cite import Ref, clean_doi

PASSAGE_CHARS = 1200
MAX_PDF_PAGES = 400
CHECK_EVERY_S = 30

# Zotero item types → the kinds knowledge/cite.py formats.
KINDS = {
    "journalArticle": "article", "magazineArticle": "article", "newspaperArticle": "article",
    "book": "book", "bookSection": "chapter", "report": "report", "thesis": "thesis",
    "conferencePaper": "conference", "webpage": "web", "blogPost": "web", "document": "report",
    "preprint": "article", "manuscript": "report",
}  # fmt: skip
SKIP_TYPES = {"attachment", "note", "annotation"}
AUTHOR_ROLES = ("author", "editor", "contributor", "bookAuthor")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(key TEXT PRIMARY KEY, signature TEXT, ref TEXT, abstract TEXT, files TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
    item_key UNINDEXED, kind UNINDEXED, location UNINDEXED, text,
    tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
STOP = set(
    "the a an of and or in on to for with by from is are was were be what which who how why does do "
    "about my me i it this that these those el la los las de del y o en un una que por para con "
    "le les des du et ou au aux une est sur pour dans qui quoi "
    # the wording of a request, not its topic ("write me a short essay… with my Zotero sources, APA")
    "write short long essay words using sources source zotero style format apa mla chicago harvard references "
    "please can you escribe escríbeme hazme ensayo corto palabras fuentes formato referencias mis sobre "
    "écris rédige essai court mots sources mes".split()
)


@dataclass
class Hit:
    key: str
    ref: Ref
    kind: str  # abstract | note | highlight | text
    location: str  # "PDF page 4", "note"
    text: str
    files: list[str]


def _load_ref(raw: str) -> Ref:
    d = json.loads(raw)
    d["authors"] = [tuple(a) for a in d.get("authors", [])]
    return Ref(**d)


def zotero_dir(cfg) -> Path:
    return Path(str(cfg.get_path("zotero.data_dir", "~/Zotero"))).expanduser()


def _year(date: str) -> str:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", date or "")
    return m.group(1) if m else ""


def _strip_html(s: str) -> str:
    s = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", s or "", flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _chunks(text: str, size: int = PASSAGE_CHARS) -> list[str]:
    from knowledge.docs_index import chunk_text

    return chunk_text(text, size=size, overlap=150)


def _ref(t: str, fields: dict, creators: list[tuple[str, str, str]]) -> Ref:
    authors = [(last, first) for role, first, last in creators if role == "author"] or [
        (last, first) for role, first, last in creators if role in AUTHOR_ROLES
    ]
    kind = KINDS.get(t, "article")
    container = (fields.get("publicationTitle") or fields.get("bookTitle") or fields.get("proceedingsTitle")
                 or fields.get("websiteTitle") or fields.get("blogTitle") or "")  # fmt: skip
    publisher = fields.get("publisher") or fields.get("institution") or fields.get("university") or ""
    return Ref(
        title=fields.get("title", "").strip() or "(untitled)",
        authors=authors,
        year=_year(fields.get("date", "")),
        kind=kind,
        container=container,
        volume=fields.get("volume", ""),
        issue=fields.get("issue", ""),
        pages=fields.get("pages", ""),
        publisher=publisher,
        doi=clean_doi(fields.get("DOI", "")),
        url=fields.get("url", ""),
        number=(f"Report No. {fields['reportNumber']}" if fields.get("reportNumber") else ""),
    )


def read_library(zdb: Path, storage: Path) -> dict[str, dict]:
    """{item key: {"type", "ref", "abstract", "notes", "highlights", "files", "signature"}} from a
    copy of zotero.sqlite. Items in the trash are left out."""
    con = sqlite3.connect(f"file:{zdb}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = con.execute
    deleted = {r[0] for r in q("SELECT itemID FROM deletedItems")}
    items = {}
    for r in q(
        "SELECT i.itemID, i.key, i.dateModified, t.typeName FROM items i JOIN itemTypes t USING(itemTypeID)"
    ):
        if r["typeName"] in SKIP_TYPES or r["itemID"] in deleted:
            continue
        items[r["itemID"]] = {"key": r["key"], "type": r["typeName"], "modified": r["dateModified"],
                              "fields": {}, "creators": [], "notes": [], "highlights": [], "files": []}  # fmt: skip
    for r in q("SELECT d.itemID, f.fieldName, v.value FROM itemData d JOIN fields f USING(fieldID) "
               "JOIN itemDataValues v USING(valueID)"):  # fmt: skip
        if r["itemID"] in items:
            items[r["itemID"]]["fields"][r["fieldName"]] = str(r["value"])
    for r in q("SELECT ic.itemID, ct.creatorType, c.firstName, c.lastName FROM itemCreators ic "
               "JOIN creators c USING(creatorID) JOIN creatorTypes ct USING(creatorTypeID) ORDER BY ic.itemID, ic.orderIndex"):  # fmt: skip
        if r["itemID"] in items:
            items[r["itemID"]]["creators"].append(
                (r["creatorType"], r["firstName"] or "", r["lastName"] or "")
            )
    for r in q("SELECT itemID, parentItemID, note FROM itemNotes"):
        if r["parentItemID"] in items and r["itemID"] not in deleted:
            text = _strip_html(r["note"])
            if len(text) > 20:
                items[r["parentItemID"]]["notes"].append(text)
    attach = {}
    for r in q(
        "SELECT a.itemID, a.parentItemID, a.path, i.key FROM itemAttachments a JOIN items i USING(itemID)"
    ):
        if r["parentItemID"] in items and r["itemID"] not in deleted and r["path"]:
            attach[r["itemID"]] = r["parentItemID"]
            p = r["path"]
            path = storage / r["key"] / p[len("storage:") :] if p.startswith("storage:") else Path(p)
            items[r["parentItemID"]]["files"].append(str(path))
    try:
        for r in q("SELECT parentItemID, text, comment, pageLabel FROM itemAnnotations"):
            parent = attach.get(r["parentItemID"])
            if parent is not None and (r["text"] or r["comment"]):
                page = f" (p. {r['pageLabel']})" if r["pageLabel"] else ""
                body = (r["text"] or "").strip()
                if r["comment"]:
                    body += f"\nLea's comment: {r['comment'].strip()}"
                items[parent]["highlights"].append(body + page)
    except sqlite3.OperationalError:  # Zotero 6 and older: no annotations table
        pass
    con.close()
    out = {}
    for it in items.values():
        sig_files = []
        for f in it["files"]:
            p = Path(f)
            cache = p.parent / ".zotero-ft-cache"
            sig_files.append(f"{p.name}:{_mtime(p)}:{_mtime(cache)}")
        out[it["key"]] = {
            "type": it["type"],
            "ref": _ref(it["type"], it["fields"], it["creators"]),
            "abstract": it["fields"].get("abstractNote", ""),
            "notes": it["notes"],
            "highlights": it["highlights"],
            "files": it["files"],
            "signature": json.dumps(
                [
                    it["modified"],
                    sorted(sig_files),
                    len(it["notes"]),
                    len(it["highlights"]),
                    sum(map(len, it["notes"])),
                ]
            ),  # fmt: skip
        }
    return out


def _mtime(p: Path) -> float:
    try:
        return round(p.stat().st_mtime, 3)
    except OSError:
        return 0.0


def pdf_pages(path: Path) -> list[str]:
    """Pages of an attached file: Zotero's extracted text (pages split by form feeds) or pypdf."""
    cache = path.parent / ".zotero-ft-cache"
    text = ""
    if cache.exists():
        text = cache.read_text(encoding="utf-8", errors="replace")
    if len(text.strip()) < 2000 and path.suffix.lower() == ".pdf" and path.exists():
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = [(p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES]]
            if sum(map(len, pages)) > len(text):
                return pages
        except Exception:  # an unreadable PDF must not stop the rest of the library
            pass
    return text.split("\f") if text else []


class ZoteroIndex:
    def __init__(self, zdir: Path, db_path: Path):
        self.zdir, self.db_path = zdir, db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()
        self._checked = 0.0

    @property
    def source(self) -> Path:
        return self.zdir / "zotero.sqlite"

    def _meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None

    def refresh(self, force: bool = False) -> dict:
        """Re-reads what changed in Zotero. Cheap when nothing changed (one stat)."""
        with self.lock:
            if not force and time.time() - self._checked < CHECK_EVERY_S:
                return {"checked": False}
            self._checked = time.time()
            if not self.source.exists():
                return {"checked": True, "missing": True}
            stamp = f"{_mtime(self.source)}:{self.source.stat().st_size}"
            if not force and self._meta("source_stamp") == stamp:
                return {"checked": True, "changed": 0}
            copy = self.db_path.with_name("zotero-copy.sqlite")
            shutil.copy2(self.source, copy)  # Sylvie's own copy; Zotero's file is only read
            for ext in ("-wal", "-journal"):
                if (self.zdir / f"zotero.sqlite{ext}").exists():
                    shutil.copy2(self.zdir / f"zotero.sqlite{ext}", copy.with_name(copy.name + ext))
            lib = read_library(copy, self.zdir / "storage")
            known = dict(self.conn.execute("SELECT key, signature FROM items"))
            changed = [k for k, v in lib.items() if known.get(k) != v["signature"]]
            removed = [k for k in known if k not in lib]
            with self.conn:
                for k in removed + changed:
                    self.conn.execute("DELETE FROM items WHERE key = ?", (k,))
                    self.conn.execute("DELETE FROM passages WHERE item_key = ?", (k,))
                for k in changed:
                    self._add(k, lib[k])
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('source_stamp', ?)", (stamp,))
            return {"checked": True, "changed": len(changed), "removed": len(removed), "items": len(lib)}

    def _add(self, key: str, it: dict) -> None:
        ref: Ref = it["ref"]
        self.conn.execute(
            "INSERT INTO items VALUES (?, ?, ?, ?, ?)",
            (key, it["signature"], json.dumps(ref.__dict__), it["abstract"], json.dumps(it["files"])),
        )
        rows = []
        head = f"{ref.title}. {ref.container}".strip(". ")
        if it["abstract"]:
            rows += [("abstract", "abstract", head + "\n" + c) for c in _chunks(it["abstract"])]
        else:
            rows.append(("abstract", "details", head))
        rows += [("note", "Lea's note", c) for n in it["notes"] for c in _chunks(n)]
        rows += [("highlight", "Lea's highlight", h[: PASSAGE_CHARS * 2]) for h in it["highlights"]]
        for f in it["files"]:
            for i, page in enumerate(pdf_pages(Path(f))):
                for c in _chunks(page):
                    rows.append(("text", f"PDF page {i + 1}", c))
        self.conn.executemany(
            "INSERT INTO passages(item_key, kind, location, text) VALUES (?, ?, ?, ?)",
            [(key, k, loc, t) for k, loc, t in rows if t.strip()],
        )

    def items(self) -> list[tuple[str, Ref]]:
        return [(k, _load_ref(r)) for k, r in self.conn.execute("SELECT key, ref FROM items")]

    def count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM items").fetchone()[0]

    def search(self, query: str, k: int = 6, per_item: int = 3) -> list[Hit]:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2 and w not in STOP]
        if not words:
            return []
        match = " OR ".join(f'"{w}"*' for w in words[:12])
        rows = self.conn.execute(
            "SELECT p.item_key, p.kind, p.location, p.text, i.ref, i.files FROM passages p "
            "JOIN items i ON i.key = p.item_key WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT 60",
            (match,),
        ).fetchall()
        # Lea's own notes and highlights first among equals: they're what she found important.
        boost = {"highlight": 0, "note": 0}
        rows = sorted(enumerate(rows), key=lambda x: (x[0] // 6, boost.get(x[1][1], 1)))
        hits, per = [], {}
        for _, (key, kind, loc, text, ref, files) in rows:
            if per.get(key, 0) >= per_item:
                continue
            per[key] = per.get(key, 0) + 1
            hits.append(Hit(key, _load_ref(ref), kind, loc, text, json.loads(files)))
            if len(hits) >= k:
                break
        return hits


_open: dict[str, ZoteroIndex] = {}


def get(cfg) -> ZoteroIndex | None:
    """The index of Lea's Zotero library, or None when she has no Zotero library (or turned it off)."""
    if not cfg.get_path("zotero.enabled", True):
        return None
    zdir = zotero_dir(cfg)
    if not (zdir / "zotero.sqlite").exists():
        return None
    key = str(zdir)
    if key not in _open:
        _open[key] = ZoteroIndex(zdir, cfg.data_dir / "zotero.db")
    idx = _open[key]
    try:
        idx.refresh()
    except (sqlite3.DatabaseError, OSError):  # Zotero mid-write or an odd file: use what's indexed
        pass
    return idx if idx.count() else None


def main() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.config import load_config

    cfg = load_config()
    idx = ZoteroIndex(zotero_dir(cfg), cfg.data_dir / "zotero.db")
    print(idx.refresh(force=True))
    for key, ref in idx.items():
        print(f"- {key}: {ref.title} ({ref.year})")


if __name__ == "__main__":
    main()
