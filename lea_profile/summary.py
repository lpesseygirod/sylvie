"""Compact text version of the Lea model for the system prompt (facts, style, interests)."""

from __future__ import annotations

from lea_profile.interests import InterestStore
from lea_profile.style import StyleStore
from memory.store import MemoryStore

CORE_CATEGORIES = ("identity", "studies", "work", "projects", "people", "places")
MAX_FACTS = 20
MIN_TRAIT_CONFIDENCE = 0.45
MAX_INTERESTS = 8


def profile_text(memory: MemoryStore, style: StyleStore, interests: InterestStore) -> str:
    parts = []
    facts = [f for f in memory.list() if f.category in CORE_CATEGORIES and not f.sensitive][:MAX_FACTS]
    if facts:
        parts.append("What you know about Lea:\n" + "\n".join(f"- {f.text}" for f in facts))
    traits = [t for t in style.list() if t.status == "confirmed" or t.confidence >= MIN_TRAIT_CONFIDENCE]
    if traits:
        lines = [f"- {t.description}" + ("" if t.status == "confirmed" else " (likely)") for t in traits]
        parts.append("How Lea likes to talk:\n" + "\n".join(lines))
    top = [i for i in interests.list() if i.status == "confirmed" or i.score >= 1.5][:MAX_INTERESTS]
    if top:
        parts.append("Topics Lea cares about: " + ", ".join(i.topic for i in top) + ".")
    return "\n\n".join(parts)
