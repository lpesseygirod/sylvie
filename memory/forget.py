"""'Sylvie, forget that' — hides facts from recall (kept for undo in the dashboard)."""

from __future__ import annotations

import re

from memory.recall import recall
from memory.store import Fact, MemoryStore

FORGET_RE = re.compile(
    r"\b(forget|olvida|olvídate|olvidate|olvídalo|oublie|oublies)\b"
    r"\s*(that|this|it|about|what|eso|esto|lo|de|que|ça|ca|cela|ce|le fait)?\s*(.*)",
    re.I,
)
NEGATED_RE = re.compile(r"(don'?t|do not|never|no|n'|ne)\s*(forget|olvid|oubli)", re.I)
BARE_TARGETS = {
    "",
    "it",
    "that",
    "this",
    "eso",
    "esto",
    "lo",
    "ça",
    "ca",
    "cela",
    "please",
    "por favor",
    "stp",
}


def detect_forget(message: str) -> str | None:
    """Returns '' for a bare 'forget that', the target text for 'forget that I like X', or None."""
    if NEGATED_RE.search(message):
        return None  # "don't forget…" is the opposite request
    m = FORGET_RE.search(message)
    if not m:
        return None
    target = m.group(3).strip(" .!?,").strip()
    return "" if target.lower() in BARE_TARGETS else target


def forget(store: MemoryStore, message: str, recent_sources: list[str]) -> list[Fact] | None:
    """recent_sources: memory sources of the previous turns, newest first.
    Returns the forgotten facts ([] if nothing matched) or None if this isn't a forget request."""
    target = detect_forget(message)
    if target is None:
        return None
    forgotten: list[Fact] = []
    if target == "":
        for src in recent_sources:
            facts = store.by_source(src)
            if facts:
                forgotten = facts
                break
    else:
        hits = recall(store.db, store.embedder, target, k=3)
        forgotten = hits[:1]
    for f in forgotten:
        store.forget(f.id)
    return forgotten
