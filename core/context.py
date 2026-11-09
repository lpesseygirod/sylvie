"""Context management: builds the message list for the model and summarizes long chats.

The context length is set explicitly in config.yaml (llm.context_length). When a conversation
grows past llm.summarize_after_tokens, its older part is summarized into a few paragraphs
(stored on the conversation) so the KV cache stays within memory.
"""

from __future__ import annotations

import json

SUMMARY_PROMPT = """Summarize this earlier part of a conversation between Lea and her assistant Sylvie, \
so Sylvie can continue it naturally. Keep: what Lea asked or decided, facts and numbers that matter, \
open questions, the tone. At most 200 words. Write in English.

{previous}

Conversation:
{transcript}

Summary:"""


def history_messages(rows: list[dict]) -> list[dict]:
    """DB rows → chat-template messages (model view, including tool calls/results)."""
    out = []
    for m in rows:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["model_content"] or m["content"]})
        elif m["role"] == "assistant":
            msg = {
                "role": "assistant",
                "content": m["model_content"] if m["model_content"] is not None else m["content"],
            }
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "content": m["model_content"] or m["content"]})
    return out


def system_with_summary(system_prompt: str, summary: str | None) -> str:
    if not summary:
        return system_prompt
    return f"{system_prompt}\n\nSummary of the earlier part of this conversation:\n{summary}"


def estimate_tokens(llm, messages: list[dict]) -> int:
    text = "".join(
        (m.get("content") or "") + (json.dumps(m["tool_calls"]) if m.get("tool_calls") else "")
        for m in messages
    )
    return llm.count_tokens(text) + 8 * len(messages)


def maybe_summarize(llm, convs, cid: str, max_tokens: int, stop=None) -> bool:
    """If the conversation is too long, summarize its older half. Returns True if it did."""
    conv = convs.get(cid)
    if conv is None:  # deleted meanwhile
        return False
    rows = convs.messages(cid, after_id=conv["summarized_upto"] or 0)
    msgs = history_messages(rows)
    if estimate_tokens(llm, msgs) <= max_tokens or len(rows) < 6:
        return False
    # Cut at a user message roughly in the middle so tool calls stay with their results.
    half = len(rows) // 2
    cut = next((i for i in range(half, len(rows)) if rows[i]["role"] == "user"), None)
    if cut is None or cut == 0:
        return False
    old = rows[:cut]
    transcript = "\n".join(
        f"{'Lea' if m['role'] == 'user' else 'Sylvie'}: {m['content'][:1500]}"
        for m in old
        if m["role"] in ("user", "assistant") and m["content"].strip()
    )
    previous = f"Summary so far:\n{conv['summary']}\n" if conv.get("summary") else ""
    summary = llm.complete(
        [{"role": "user", "content": SUMMARY_PROMPT.format(previous=previous, transcript=transcript)}],
        max_tokens=400,
    )
    if stop is not None and stop.is_set():
        return False  # cut short by Lea: don't keep half a summary
    convs.set(cid, summary=summary, summarized_upto=old[-1]["id"])
    return True
