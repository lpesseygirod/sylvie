"""Builds Sylvie's offline Wikipedia from Wikimedia's own CirrusSearch dumps (complete, weekly).

    make wikipedia            # en, es, fr: ~64 GB streamed (nothing kept but the library)

Source: https://dumps.wikimedia.org/other/cirrus_search_index/<date>/index_name=<lang>wiki_content/
— bz2 shards of JSON lines, alternately {"index": …} and the page: title, plain text, other names
(redirects), how many pages link to it (incoming_links). CC BY-SA: credit each article when quoting.

Shards are downloaded and unpacked in parallel worker processes, each into a temporary list of
articles that the main process adds to the library (SQLite has one writer) and then deletes.
Each shard is one transaction: interrupted, the run resumes at the next missing shard. The new
library is built next to the old one and swapped in at the end (the old one goes to _to_delete/).
"""

from __future__ import annotations

import argparse
import bz2
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

BASE = "https://dumps.wikimedia.org/other/cirrus_search_index"
UA = {"User-Agent": "Sylvie-local-assistant/1.0 (offline Wikipedia for a private assistant)"}


def pages(lines: Iterable[bytes]) -> Iterator[dict]:
    """Articles from the dump's JSON lines (skips the {"index": …} lines and non-articles)."""
    for line in lines:
        if not line.strip() or line.startswith(b'{"index"'):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if str(d.get("namespace")) != "0" or not d.get("title") or not d.get("text"):
            continue
        yield {
            "title": d["title"],
            "text": d["text"],
            "redirects": [r["title"] for r in d.get("redirect") or [] if str(r.get("namespace", 0)) == "0"],
            "popularity": d.get("incoming_links") or 0,
        }


def bz2_lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Lines from a stream of bz2 bytes (several concatenated bz2 streams are fine)."""
    dec, rest = bz2.BZ2Decompressor(), b""
    for chunk in chunks:
        while chunk:
            data = dec.decompress(chunk)
            chunk = b""
            if dec.eof:  # a new bz2 stream starts in the leftover bytes
                chunk, dec = dec.unused_data, bz2.BZ2Decompressor()
            rest += data
            *lines, rest = rest.split(b"\n")
            yield from lines
    if rest:
        yield rest


def shard_urls(date: str, lang: str, client=None) -> list[str]:
    import httpx

    url = f"{BASE}/{date}/index_name%3D{lang}wiki_content/"
    html = (client or httpx).get(url, headers=UA, timeout=60, follow_redirects=True).text
    return [url + name for name in sorted(set(re.findall(r'href="([^"/]+\.json\.bz2)"', html)))]


def latest_date(client=None) -> str:
    import httpx

    html = (client or httpx).get(BASE + "/", headers=UA, timeout=60, follow_redirects=True).text
    return sorted(re.findall(r'href="(\d{8})/"', html))[-1]


def fetch_shard(url: str, out: Path, keep_passages: int) -> tuple[str, int]:
    """Worker: streams one shard and writes its articles (start of each only) to `out` (jsonl)."""
    import httpx

    from knowledge import wiki

    wiki.MAX_PASSAGES = keep_passages
    tmp = out.with_suffix(".part")
    n = 0
    for attempt in range(5):
        try:
            with httpx.stream("GET", url, headers=UA, timeout=120, follow_redirects=True) as r:
                r.raise_for_status()
                with open(tmp, "w", encoding="utf-8") as f:
                    n = 0
                    for p in pages(bz2_lines(r.iter_bytes(1 << 20))):
                        p["text"] = "\n".join(wiki.split_passages(p["text"]))  # keep only what's indexed
                        f.write(json.dumps(p, ensure_ascii=False) + "\n")
                        n += 1
            tmp.rename(out)
            return url, n
        except (httpx.HTTPError, OSError, EOFError):
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"could not download {url}")


def build(data_dir: Path, langs: list[str], date: str | None = None, workers: int = 3,
          fetch=fetch_shard, list_shards=shard_urls, pool_cls=ProcessPoolExecutor) -> dict:  # fmt: skip
    from knowledge.wiki import MAX_PASSAGES, Wiki

    date = date or latest_date()
    new = data_dir / "wikipedia.new.db"
    tmpdir = data_dir / "datasets" / "wikipedia-cirrus-tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    w = Wiki(new)
    todo = []
    for lang in langs:
        for url in list_shards(date, lang):
            key = f"{lang}/cirrus-{date}/{url.rsplit('/', 1)[-1]}"
            if not w.done(key):
                todo.append((lang, url, key))
    print(f"snapshot {date}: {len(todo)} shards to do", flush=True)
    with pool_cls(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch, url, tmpdir / (key.replace("/", "_") + ".jsonl"), MAX_PASSAGES): (lang, key)
            for lang, url, key in todo
        }
        for fut in as_completed(futures):
            lang, key = futures[fut]
            fut.result()
            f = tmpdir / (key.replace("/", "_") + ".jsonl")
            t = time.time()
            with open(f, encoding="utf-8") as fh:
                n = w.index_rows((json.loads(x) for x in fh), lang, key)
            f.unlink()  # Sylvie's own temporary file
            print(f"{key}: {n} articles indexed in {time.time() - t:.0f} s", flush=True)
    w.optimize()
    stats = w.stats()
    w.conn.close()
    # Swap in the new library; the old one is kept in _to_delete/ (never deleted by Sylvie).
    old = data_dir / "wikipedia.db"
    if old.exists():
        trash = data_dir / "_to_delete"
        trash.mkdir(exist_ok=True)
        shutil.move(str(old), trash / f"wikipedia-replaced-{datetime.now():%Y%m%d-%H%M}.db")
    os.replace(new, old)
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("done:", stats, flush=True)
    return stats


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.config import load_config

    ap = argparse.ArgumentParser()
    ap.add_argument("langs", nargs="*", default=["es", "fr", "en"])
    ap.add_argument("--date", help="dump date (default: the latest)")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    build(load_config().data_dir, a.langs, a.date, a.workers)


if __name__ == "__main__":
    main()
