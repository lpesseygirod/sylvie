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


# Lea, 2026-11-12: "i need sylvie to remember … things i have said, because is not working". Measured that
# day: 217 of her 241 facts came from her coding sessions with Claude (memory/claude_history.py), mostly
# task chatter ("Lea needs Sylvie to retest test 3 on the iPhone…"), and filled the recalled facts ahead
# of what she told Sylvie herself; one was then read out as a to-do. What she said to Sylvie comes first;
# at most half the facts come from Claude sessions, and none that is about the assistant rather than her.
FROM_CLAUDE_WEIGHT = 0.5
ABOUT_ASSISTANT_RE = re.compile(r"\b(Sylvie|the assistant|Claude|the AI)\b", re.I)


def from_claude(f: Fact) -> bool:
    return (f.source or "").startswith("claude:")


def recall(
    db: Database, embedder, query: str, k: int = 6, include_sensitive: bool = True, for_answer: bool = False
) -> list[Fact]:
    """for_answer: the facts given to Sylvie before she answers (Lea's own words first, see above).
    Learning and "forget that" search all facts alike."""
    hits = hybrid_search(db, embedder, query, "facts_fts", "facts_vec", k=k)
    ranked: list[tuple[float, Fact]] = []
    for rowid, score in hits:
        r = db.one(f"SELECT * FROM facts WHERE id = ? AND {ACTIVE}", (rowid,))
        if r is None or (r["sensitive"] and not include_sensitive):
            continue
        f = Fact.from_row(r)
        if for_answer and from_claude(f):
            if ABOUT_ASSISTANT_RE.search(f.text):
                continue
            score *= FROM_CLAUDE_WEIGHT
        ranked.append((score, f))
    out, claude_n = [], 0
    for _, f in sorted(ranked, key=lambda x: -x[0]):
        if for_answer and from_claude(f):
            if claude_n >= max(1, k // 2):
                continue
            claude_n += 1
        out.append(f)
        if len(out) >= k:
            break
    return out
