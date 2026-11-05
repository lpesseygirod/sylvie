"""Interest noticing: does Lea's message connect to something she cares about?

Returns at most one short hint for the model ("Lea might be interested in this"). The model
decides whether it's genuinely useful; the orchestrator allows at most one per conversation.
"""

from __future__ import annotations

from lea_profile.interests import InterestStore

MIN_SIM = 0.55
MIN_SCORE = 1.5


def notice(interests: InterestStore, message: str) -> str | None:
    candidates = [i for i in interests.list() if i.status == "confirmed" or i.score >= MIN_SCORE]
    if not candidates or len(message) < 15:
        return None
    v = interests.embedder.embed_one(message, query=True)
    best, best_sim = None, 0.0
    for i in candidates:
        sim = float(v @ interests._vec(i.topic.lower()))
        if sim > best_sim:
            best, best_sim = i, sim
    if best is None or best_sim < MIN_SIM:
        return None
    # If the message is directly about the topic, an aside would be redundant.
    if best.topic.lower() in message.lower():
        return None
    return f"this may connect to Lea's interest in {best.topic}"


def confirm_question(interests: InterestStore) -> tuple[int, str] | None:
    i = interests.to_confirm()
    if i is None:
        return None
    return i.id, (
        f"Lea has mentioned '{i.topic}' several times. At the end, ask her lightly (one short sentence) "
        "whether she'd like you to keep an eye on it."
    )
