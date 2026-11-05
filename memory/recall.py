"""Hybrid recall: keyword search (FTS5) + meaning search (vectors), merged with
reciprocal rank fusion. Keywords catch names and exact terms; vectors catch paraphrases
and other languages."""

from __future__ import annotations

import re

from core.db import Database
from knowledge.embeddings import to_blob
from memory.store import ACTIVE, Fact

RRF_K = 60
MIN_VECTOR_SIM = 0.35


def fts_query(text: str) -> str | None:
    """Turns free text into a safe FTS5 OR-query of its words (no FTS syntax injection)."""
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


def hybrid_search(
    db: Database,
    embedder,
    query: str,
    fts_table: str,
    vec_table: str,
    k: int = 6,
    candidates: int = 20,
    min_sim: float = MIN_VECTOR_SIM,
) -> list[tuple[int, float]]:
    """Returns [(rowid, score)] best first. Rows are not filtered for 'active' here."""
    scores: dict[int, float] = {}
    q = fts_query(query)
    if q:
        rows = db.query(
            f"SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ? ORDER BY bm25({fts_table}) LIMIT ?",
            (q, candidates),
        )
        for rank, r in enumerate(rows):
            scores[r["rowid"]] = scores.get(r["rowid"], 0) + 1 / (RRF_K + rank)
    vec = embedder.embed_one(query, query=True)
    rows = db.query(
        f"SELECT rowid, distance FROM {vec_table} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (to_blob(vec), candidates),
    )
    for rank, r in enumerate(rows):
        if 1 - r["distance"] >= min_sim:
            scores[r["rowid"]] = scores.get(r["rowid"], 0) + 1 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda x: -x[1])[: k * 3]


def recall(db: Database, embedder, query: str, k: int = 6, include_sensitive: bool = True) -> list[Fact]:
    hits = hybrid_search(db, embedder, query, "facts_fts", "facts_vec", k=k)
    out = []
    for rowid, _ in hits:
        r = db.one(f"SELECT * FROM facts WHERE id = ? AND {ACTIVE}", (rowid,))
        if r is None or (r["sensitive"] and not include_sensitive):
            continue
        out.append(Fact.from_row(r))
        if len(out) >= k:
            break
    return out
