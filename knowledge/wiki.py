"""Sylvie's offline Wikipedia (English, Spanish, French): general knowledge she looks up, with credit.

Lea, 2026-11-07: "can we add her knowledge?" Facts belong in a library, not in the weights (a small
model trained on facts mixes them up). So Sylvie searches this first for facts about the world
(history, science, people, places, definitions) — fast, offline, without her question leaving the
Mac — and keeps web search for what is recent (news, prices, schedules).

- Sources (CC BY-SA; used for lookup and quoting with credit, never as training data):
  - Wikimedia's own CirrusSearch dumps (`knowledge/wiki_dump.py`, `make wikipedia`): complete and
    recent, with each article's other names (redirects) and how many pages link to it (popularity).
  - Hugging Face `wikimedia/wikipedia` 2023-11-01 (parquet): the first version; it turned out to miss
    about half of the big English articles (Paris, DNA, Moon…), so it's replaced by the above.
- Stored apart from Lea's data, in `<data dir>/wikipedia.db`, and not part of the shareable general
  pack: anyone installs the same library with the same command.
- Only the start of each article is kept (MAX_PASSAGES × ~1000 characters): that's where most answers
  are. For more, Sylvie opens the article online — only its address leaves the Mac.
- Search: an article whose title (or other name) is exactly what was asked comes first; then SQLite
  FTS5 (BM25, titles weigh more) with all the key words, dropping the most common ones if needed,
  re-ranked by popularity. Disambiguation pages are skipped.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PASSAGE_CHARS = 1000
MAX_PASSAGES = 8  # the start of each article; the full article is fetched online when needed
SNAPSHOT = "20231101"
LANGS = ("en", "es", "fr")
POPULARITY_WEIGHT = 0.6
COMMON_DF = 100_000  # a word in more articles than this doesn't narrow a search down  # × log(1 + pages linking to it): Canberra beats "Capital gains tax in Australia"

# Words that carry no meaning for a search, in the three languages (kept short on purpose).
STOP = set(
    """the a an of in on at to for and or is are was were be been what who whom which when where why how
    do does did can could would should tell me about please explain give list with from by as that this it
    its his her their there than then into over under el la los las un una unos unas de del en y o que
    quien quién cual cuál cuando cuándo donde dónde por qué como cómo es son fue era para con sobre al lo
    le les des du et ou qui quoi quel quelle quand où pourquoi comment est sont était pour avec sur au aux
    ce cette dans par moi dime dis explique best way""".split()
)
DISAMBIGUATION = re.compile(r"\((disambiguation|desambiguación|homonymie)\)$", re.I)
DISAMBIGUATION_TEXT = re.compile(
    r"may refer to|puede referirse a|puede hacer referencia a|peut (désigner|faire référence)", re.I
)


@dataclass
class Passage:
    title: str
    url: str
    lang: str
    text: str
    score: float = 0.0


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY, lang TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
    first_passage INTEGER NOT NULL,  -- rowid of its first passage in passages_<lang>
    popularity REAL NOT NULL DEFAULT 0  -- pages linking to it (0 when the source doesn't say)
);
CREATE INDEX IF NOT EXISTS articles_title ON articles(lang, title COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS redirects (lang TEXT NOT NULL, title TEXT NOT NULL, article_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS redirects_title ON redirects(lang, title COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS indexed_files (path TEXT PRIMARY KEY, articles INTEGER NOT NULL, done_at TEXT NOT NULL);
""" + "".join(
    f"CREATE VIRTUAL TABLE IF NOT EXISTS passages_{lang} USING fts5("
    "title, text, article_id UNINDEXED, tokenize='unicode61 remove_diacritics 2');\n"
    f"CREATE VIRTUAL TABLE IF NOT EXISTS vocab_{lang} USING fts5vocab(passages_{lang}, 'row');\n"
    for lang in LANGS
)  # one table per language: a Spanish search never scans English text


def db_path(cfg) -> Path:
    return Path(cfg.data_dir) / "wikipedia.db"


def split_passages(text: str) -> list[str]:
    """Paragraphs joined into passages of ~PASSAGE_CHARS; very long paragraphs are cut at sentences."""
    out, cur = [], ""
    for para in (p.strip() for p in text.split("\n")):
        if not para:
            continue
        while len(para) > PASSAGE_CHARS * 1.5:
            cut = para.rfind(". ", 0, PASSAGE_CHARS) + 1 or PASSAGE_CHARS
            out.append(((cur + "\n") if cur else "") + para[:cut].strip())
            cur, para = "", para[cut:].strip()
            if len(out) >= MAX_PASSAGES:
                return out[:MAX_PASSAGES]
        if len(cur) + len(para) > PASSAGE_CHARS and cur:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
        if len(out) >= MAX_PASSAGES:
            return out[:MAX_PASSAGES]
    if cur:
        out.append(cur)
    return out[:MAX_PASSAGES]


def _fold(w: str) -> str:
    """As the index stores words: lower case, no accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", w.lower()) if not unicodedata.combining(c))


def query_words(text: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]
    return list(dict.fromkeys(words))[:12]


def is_disambiguation(title: str, text: str) -> bool:
    return bool(DISAMBIGUATION.search(title) or DISAMBIGUATION_TEXT.search(text[:300]))


def article_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"


class Wiki:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(articles)")}
        if cols and "popularity" not in cols:  # a library built before popularity existed
            self.conn.execute("ALTER TABLE articles ADD COLUMN popularity REAL NOT NULL DEFAULT 0")
        self.conn.executescript(SCHEMA)
        for lang in LANGS:  # titles weigh 3×; "ORDER BY rank" lets FTS5 sort without a temp table
            self.conn.execute(
                f"INSERT INTO passages_{lang}(passages_{lang}, rank) VALUES ('rank', 'bm25(3.0, 1.0)')"
            )
        self.conn.commit()
        self.lock = threading.Lock()

    # ---------- building ----------
    def done(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM indexed_files WHERE path = ?", (key,)).fetchone() is not None

    def index_rows(self, rows: Iterable[dict], lang: str, key: str, stop=None) -> int:
        """rows: {title, text, url?, redirects?: [titles], popularity?}. One source file = one
        transaction, so an interrupted file is simply redone. Returns the number of articles."""
        assert lang in LANGS
        if self.done(key):
            return 0
        c = self.conn
        c.execute("PRAGMA synchronous = OFF")
        n = 0
        next_id = (c.execute("SELECT max(id) FROM articles").fetchone()[0] or 0) + 1
        table = f"passages_{lang}"
        try:
            c.execute("BEGIN")
            for row in rows:
                if stop is not None and stop.is_set():
                    raise InterruptedError
                title, text = row.get("title"), row.get("text")
                if not title or not text or is_disambiguation(title, text):
                    continue
                parts = split_passages(text)
                if not parts:
                    continue
                aid = next_id
                next_id += 1
                first = c.execute(
                    f"INSERT INTO {table}(title, text, article_id) VALUES (?,?,?)", (title, parts[0], aid)
                ).lastrowid
                c.executemany(
                    f"INSERT INTO {table}(title, text, article_id) VALUES (?,?,?)",
                    [(title, p, aid) for p in parts[1:]],
                )
                c.execute(
                    "INSERT INTO articles(id, lang, title, url, first_passage, popularity) VALUES (?,?,?,?,?,?)",
                    (
                        aid,
                        lang,
                        title,
                        row.get("url") or article_url(lang, title),
                        first,
                        row.get("popularity") or 0,
                    ),
                )
                c.executemany(
                    "INSERT INTO redirects(lang, title, article_id) VALUES (?,?,?)",
                    [(lang, r, aid) for r in dict.fromkeys(row.get("redirects") or []) if r and r != title],
                )
                n += 1
            c.execute(
                "INSERT INTO indexed_files(path, articles, done_at) VALUES (?, ?, datetime('now'))", (key, n)
            )
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return n

    def index_parquet(self, file: Path, lang: str, stop=None, batch_rows: int = 2000) -> int:
        """Adds one Hugging Face parquet file (title, url, text)."""
        import pyarrow.parquet as pq

        def rows():
            for batch in pq.ParquetFile(file).iter_batches(
                batch_size=batch_rows, columns=["title", "url", "text"]
            ):
                cols = batch.to_pydict()
                for title, url, text in zip(cols["title"], cols["url"], cols["text"], strict=True):
                    yield {"title": title, "url": url, "text": text}

        return self.index_rows(rows(), lang, f"{lang}/{Path(file).name}", stop=stop)

    def optimize(self) -> None:
        for lang in LANGS:
            self.conn.execute(f"INSERT INTO passages_{lang}(passages_{lang}) VALUES ('optimize')")
        self.conn.commit()

    def stats(self) -> dict:
        rows = self.conn.execute("SELECT lang, count(*) n FROM articles GROUP BY lang").fetchall()
        return {r["lang"]: r["n"] for r in rows}

    # ---------- searching ----------
    def search(self, query: str, lang: str | None = None, k: int = 4) -> list[Passage]:
        words = query_words(query)
        if not words:
            return []
        langs = [lang] if lang in LANGS else []
        langs += [x for x in ("en",) if x not in langs]  # English has the most; always a fallback
        with self.lock:
            out: list[Passage] = []
            for lg in langs:
                out += self._search_lang(words, lg, k - len(out))
                if len(out) >= k:
                    break
        return out[:k]

    def _exact(self, phrase: str, lang: str):
        """The most popular article titled (or also known as) exactly `phrase`."""
        return self.conn.execute(
            "SELECT a.* FROM articles a WHERE a.lang = ? AND a.title = ? COLLATE NOCASE "
            "UNION SELECT a.* FROM redirects r JOIN articles a ON a.id = r.article_id "
            "WHERE r.lang = ? AND r.title = ? COLLATE NOCASE ORDER BY popularity DESC LIMIT 1",
            (lang, phrase, lang, phrase),
        ).fetchone()

    def snapshot(self) -> str:
        """The date of the Wikipedia copy the library was built from, e.g. "2026-11-01"."""
        dates = [m for (k,) in self.conn.execute("SELECT path FROM indexed_files LIMIT 400")
                 for m in re.findall(r"(?<!\d)(20\d{6})(?!\d)", k)]  # fmt: skip
        d = max(dates, default="")
        return f"{d[:4]}-{d[4:6]}-{d[6:]}" if d else "unknown date"

    def _df(self, words: list[str], lang: str) -> dict[str, int]:
        out = {}
        for w in words:
            r = self.conn.execute(f"SELECT doc FROM vocab_{lang} WHERE term = ?", (_fold(w),)).fetchone()
            if r:
                out[w] = r[0]
        return out

    def _rarest_first(self, words: list[str], lang: str) -> list[str]:
        """Drops words the library doesn't have; rarest (most telling) first."""
        df = {}
        for w in words:
            r = self.conn.execute(f"SELECT doc FROM vocab_{lang} WHERE term = ?", (_fold(w),)).fetchone()
            if r:
                df[w] = r[0]
        return sorted(df, key=df.get)

    def _search_lang(self, words: list[str], lang: str, k: int) -> list[Passage]:
        c = self.conn
        table = f"passages_{lang}"
        found: dict[int, Passage] = {}
        per_article: dict[int, int] = {}

        def add(rowid: int, aid: int, title: str, url: str, text: str, score: float) -> None:
            if rowid in found or per_article.get(aid, 0) >= 2 or is_disambiguation(title, text):
                return
            found[rowid] = Passage(title, url, lang, text, score)
            per_article[aid] = per_article.get(aid, 0) + 1

        def add_article(r, score: float, passages: int = 1) -> bool:
            before = len(found)
            for p in c.execute(
                f"SELECT rowid, text, article_id FROM {table} WHERE rowid >= ? AND rowid < ? ORDER BY rowid",
                (r["first_passage"], r["first_passage"] + passages),
            ).fetchall():
                if p["article_id"] == r["id"]:
                    add(p["rowid"], r["id"], r["title"], r["url"], p["text"], score)
            return len(found) > before

        # 1. The whole question is a title or another name of one ("marie curie", "jfk"): its start.
        if r := self._exact(" ".join(words), lang):
            add_article(r, 1000.0, passages=2)
        ranked = self._rarest_first(words, lang)
        df = self._df(ranked, lang)
        # 1b. Otherwise the most telling part of it that is a title: "how does photosynthesis work" →
        # Photosynthesis; "capital of Australia" → Australia (measured: word search alone found neither).
        if not found and len(words) > 1:
            grams = [
                (n, words[i : i + n]) for n in range(len(words) - 1, 0, -1) for i in range(len(words) - n + 1)
            ]
            grams.sort(key=lambda g: (-g[0], min(df.get(w, 10**9) for w in g[1])))
            for _, g in grams[:12]:
                r = self._exact(" ".join(g), lang)
                if r is not None and (r["popularity"] or 0) >= 20 and add_article(r, 900.0, passages=2):
                    break
        # 2. All key words, then without the most common ones; re-ranked by popularity. A level whose
        # rarest word is in 100k+ articles is skipped once something was found: slow (bm25 over all of
        # them, "paris" took 5.6 s) and no better than what's there.
        for m in range(len(ranked), 0, -1):
            if len(found) >= k or (m == 1 and len(ranked) > 2):
                break
            if found and (df.get(ranked[0], 0) > COMMON_DF or len(words) == 1):
                break  # (a one-word question that is a title: the article itself is the answer)
            q = " AND ".join(f'"{w}"' for w in ranked[:m])
            rows = []
            for r in c.execute(
                f"SELECT rowid, title, text, article_id, rank FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT 60",
                (q,),
            ).fetchall():
                a = c.execute(
                    "SELECT url, popularity FROM articles WHERE id = ?", (r["article_id"],)
                ).fetchone()
                pop = a["popularity"] if a else 0
                rows.append((r["rank"] - POPULARITY_WEIGHT * math.log1p(pop), r, a["url"] if a else ""))
            for score, r, url in sorted(rows, key=lambda x: x[0]):
                add(r["rowid"], r["article_id"], r["title"], url, r["text"], -score)
                if len(found) >= k:
                    break
        return list(found.values())[:k]


_open: dict[str, Wiki] = {}


def get(cfg) -> Wiki | None:
    """The library if it has been built (else None: the tool isn't offered)."""
    if not cfg.get_path("knowledge.wikipedia", True):
        return None
    p = db_path(cfg)
    if not p.exists():
        return None
    key = f"{p}:{p.stat().st_ino}"  # a rebuilt library swapped in is a new file: open the new one
    w = _open.get(key)
    if w is None:
        for old in [k for k in _open if k.startswith(f"{p}:")]:
            _open.pop(old).conn.close()
        w = _open[key] = Wiki(p)
    try:
        if not w.conn.execute("SELECT 1 FROM indexed_files LIMIT 1").fetchone():
            return None
    except sqlite3.Error:
        return None
    return w
