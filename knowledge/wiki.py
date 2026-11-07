"""Sylvie's offline Wikipedia (English, Spanish, French): general knowledge she looks up, with credit.

Lea, 2026-11-07: "can we add her knowledge?" Facts belong in a library, not in the weights (a small
model trained on facts mixes them up). So Sylvie searches this first for facts about the world
(history, science, people, places, definitions) — in well under a second, offline, without her
question leaving the Mac — and keeps web search for what is recent (news, prices, schedules).

- Source: Hugging Face `wikimedia/wikipedia`, snapshot 2023-11-01 (CC BY-SA 3.0 / GFDL), downloaded
  by `scripts/get_wikipedia.py`. Used for lookup and quoting with credit only, never as training data.
- Stored apart from Lea's data, in `<data dir>/wikipedia.db`: it's general, and it's big (~40 GB).
- Each article is cut into passages of ~1000 characters, keeping only its start (MAX_PASSAGES): that's
  where most answers are, and it keeps the library a manageable size. When Sylvie needs more, she
  opens the article's URL online (Lea, 2026-11-07: pull what's needed from the internet, build the
  answer locally; only the article's address leaves the Mac, never her question or context).
- Not part of the shareable general pack: anyone installs the same library with the same script. Search: SQLite FTS5 with BM25, titles weigh more,
  and an article whose title is what was asked ("Who was Marie Curie?") comes first.
- Indexing runs file by file and resumes where it stopped: `make wikipedia-index`.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

PASSAGE_CHARS = 1000
MAX_PASSAGES = 8  # the start of each article; the full article is fetched online when needed
SNAPSHOT = "20231101"
LANGS = ("en", "es", "fr")

# Words that carry no meaning for a search, in the three languages (kept short on purpose).
STOP = set(
    """the a an of in on at to for and or is are was were be been what who whom which when where why how
    do does did can could would should tell me about please explain give list with from by as that this it
    its his her their there than then into over under el la los las un una unos unas de del en y o que
    quien quién cual cuál cuando cuándo donde dónde por qué como cómo es son fue era para con sobre al lo
    le les des du et ou qui quoi quel quelle quand où pourquoi comment est sont était pour avec sur au aux
    ce cette dans par moi dime dis explique""".split()
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
    first_passage INTEGER NOT NULL  -- rowid of its first passage in passages_<lang>
);
CREATE INDEX IF NOT EXISTS articles_title ON articles(lang, title COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS indexed_files (path TEXT PRIMARY KEY, articles INTEGER NOT NULL, done_at TEXT NOT NULL);
""" + "".join(
    f"CREATE VIRTUAL TABLE IF NOT EXISTS passages_{lang} USING fts5("
    "title, text, article_id UNINDEXED, tokenize='unicode61 remove_diacritics 2');\n"
    for lang in ("en", "es", "fr")
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


def query_words(text: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]
    return list(dict.fromkeys(words))[:12]


class Wiki:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    # ---------- building ----------
    def index_parquet(self, file: Path, lang: str, stop=None, batch_rows: int = 2000) -> int:
        """Adds one downloaded parquet file. Returns the number of articles (0 if already done)."""
        import pyarrow.parquet as pq

        assert lang in LANGS
        key = f"{lang}/{Path(file).name}"
        if self.conn.execute("SELECT 1 FROM indexed_files WHERE path = ?", (key,)).fetchone():
            return 0
        c = self.conn
        c.execute("PRAGMA synchronous = OFF")
        n = 0
        start_id = (c.execute("SELECT max(id) FROM articles").fetchone()[0] or 0) + 1
        try:
            c.execute("BEGIN")
            for batch in pq.ParquetFile(file).iter_batches(
                batch_size=batch_rows, columns=["title", "url", "text"]
            ):
                if stop is not None and stop.is_set():
                    raise InterruptedError
                cols = batch.to_pydict()
                for title, url, text in zip(cols["title"], cols["url"], cols["text"], strict=True):
                    if not text or not title:
                        continue
                    parts = split_passages(text)
                    if not parts:
                        continue
                    aid = start_id + n
                    first = c.execute(
                        f"INSERT INTO passages_{lang}(title, text, article_id) VALUES (?,?,?)",
                        (title, parts[0], aid),
                    ).lastrowid
                    c.executemany(
                        f"INSERT INTO passages_{lang}(title, text, article_id) VALUES (?,?,?)",
                        [(title, p, aid) for p in parts[1:]],
                    )
                    c.execute(
                        "INSERT INTO articles(id, lang, title, url, first_passage) VALUES (?,?,?,?,?)",
                        (aid, lang, title, url, first),
                    )
                    n += 1
            c.execute(
                "INSERT INTO indexed_files(path, articles, done_at) VALUES (?, ?, datetime('now'))", (key, n)
            )
            c.execute("COMMIT")  # one file = one transaction: an interrupted file is simply redone
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return n

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
                out += self._search_lang(words, lg, k)
                if len(out) >= k:
                    break
        return out[:k]

    def _search_lang(self, words: list[str], lang: str, k: int) -> list[Passage]:
        c = self.conn
        # 1. An article whose title is exactly what was asked about ("marie curie", "photosynthesis").
        exact = []
        for n in range(min(len(words), 4), 0, -1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                r = c.execute(
                    "SELECT id, title, url, first_passage FROM articles WHERE lang = ? AND title = ? COLLATE NOCASE LIMIT 1",
                    (lang, phrase),
                ).fetchone()
                if r:
                    exact.append(r)
            if exact:
                break
        # 2. Full-text search: all words first (precise), then any of them (broad).
        found: dict[int, Passage] = {}
        per_article: dict[int, int] = {}
        table = f"passages_{lang}"
        for r in exact[:2]:
            p = c.execute(f"SELECT text FROM {table} WHERE rowid = ?", (r["first_passage"],)).fetchone()
            if p:
                found[r["first_passage"]] = Passage(r["title"], r["url"], lang, p["text"], 100.0)
                per_article[r["id"]] = 1
        for joiner in (" AND ", " OR "):
            if len(found) >= k:
                break
            q = joiner.join(f'"{w}"' for w in words)
            rows = c.execute(
                f"SELECT rowid, title, text, article_id, bm25({table}, 6.0, 1.0) s FROM {table} "
                f"WHERE {table} MATCH ? ORDER BY s LIMIT 40",
                (q,),
            ).fetchall()
            for r in rows:
                aid = r["article_id"]
                if r["rowid"] in found or per_article.get(aid, 0) >= 2:
                    continue
                url = c.execute("SELECT url FROM articles WHERE id = ?", (aid,)).fetchone()
                found[r["rowid"]] = Passage(r["title"], url["url"] if url else "", lang, r["text"], -r["s"])
                per_article[aid] = per_article.get(aid, 0) + 1
                if len(found) >= k:
                    break
        return list(found.values())


_open: dict[str, Wiki] = {}


def get(cfg) -> Wiki | None:
    """The library if it has been built (else None: the tool isn't offered)."""
    if not cfg.get_path("knowledge.wikipedia", True):
        return None
    p = db_path(cfg)
    if not p.exists():
        return None
    w = _open.get(str(p))
    if w is None:
        w = _open[str(p)] = Wiki(p)
    try:
        if not w.conn.execute("SELECT 1 FROM indexed_files LIMIT 1").fetchone():
            return None
    except sqlite3.Error:
        return None
    return w


def main() -> None:
    """make wikipedia-index: indexes every downloaded file not indexed yet (resumable)."""
    import sys
    import time

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.config import load_config

    cfg = load_config()
    root = Path(cfg.data_dir) / "datasets" / "wikipedia"
    w = Wiki(db_path(cfg))
    for lang in ("es", "fr", "en"):
        for f in sorted((root / f"{SNAPSHOT}.{lang}").glob("*.parquet")):
            t = time.time()
            n = w.index_parquet(f, lang)
            if n:
                print(f"{lang} {f.name}: {n} articles in {time.time() - t:.0f} s", flush=True)
    w.optimize()
    print("done:", w.stats(), flush=True)


if __name__ == "__main__":
    main()
