"""Skill practice: keeps tool use and reasoning alive in fine-tunes (plan, Phases 4–5).

Measured: a LoRA trained only on plain chat (oasst2) made the 9B stop calling tools (tools eval
7/9 → 4/9). So each night Sylvie solves GSM8K word problems (MIT, human-written, with known
answers) in two ways, and only answers that code verifies as correct are kept:
  - with the calculator tool → tool-use examples (messages include the tool call and result)
  - in thinking mode → reasoning examples (Qwen recommends mostly reasoning data when tuning
    thinking models)
Questions come from the open dataset; answers from Sylvie's own model (plan rule 4).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from core.toolcall import StreamSplitter
from evals.run import number_candidates
from tools.registry import CALCULATOR, ToolContext, execute

SYSTEM = (
    "You are Sylvie, Lea's assistant. Use the calculator for arithmetic. Answer briefly and give the "
    'final number. Start with the working or the answer itself, never with a preamble like "Let me calculate".'
)
# Answers that still open with filler aren't kept: training on them would teach the habit (T29).
FILLER = re.compile(
    r"^\s*(let me|let's|i'll|sure|okay|ok|great|alright|déjame|vamos a|je vais|voyons)\b", re.I
)


def gsm8k(path: Path, rng: random.Random, n: int) -> list[dict]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rng.sample(rows, min(n, len(rows)))


def correct(text: str, answer: str) -> bool:
    try:
        target = float(answer)
    except ValueError:
        return False
    return any(abs(x - target) < 1e-6 for x in number_candidates(text))


def _gen(llm, messages, tools=None, thinking=False, max_tokens=800):
    sp = StreamSplitter(thinking=thinking)
    for chunk in llm.stream(
        messages, tools=tools, thinking=thinking, effort="low", max_tokens=max_tokens, use_cache=False
    ):
        if chunk.done:
            break
        sp.feed(chunk.text)
    sp.flush()
    return sp


def tool_example(llm, item: dict, cfg) -> dict | None:
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": item["question"]}]
    sp = _gen(llm, msgs, tools=[CALCULATOR])
    calls = [c for c in sp.tool_calls if c.name == "calculator"]
    if not calls:
        return None
    convo = [{"role": "user", "content": item["question"]},
             {"role": "assistant", "content": sp.visible_text.strip(), "tool_calls": [c.to_message() for c in calls]}]  # fmt: skip
    ctx = ToolContext(cfg=cfg)
    for c in calls:
        convo.append({"role": "tool", "content": execute(c, ctx).content})
    final = _gen(llm, [msgs[0]] + convo, tools=[CALCULATOR])
    if (
        final.tool_calls
        or not correct(final.visible_text, item["answer"])
        or FILLER.match(final.visible_text)
    ):
        return None
    return {"messages": convo, "chosen": final.visible_text.strip(), "tools": [CALCULATOR]}


def reasoning_example(llm, item: dict) -> dict | None:
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": item["question"]}]
    sp = _gen(llm, msgs, thinking=True, max_tokens=2000)
    if (
        not sp.thinking_text.strip()
        or not correct(sp.visible_text, item["answer"])
        or FILLER.match(sp.visible_text)
    ):
        return None
    return {
        "messages": [{"role": "user", "content": item["question"]}],
        "chosen": sp.visible_text.strip(),
        "reasoning": sp.thinking_text.strip(),
    }


def practice_skills(
    llm,
    store,
    cfg,
    data_path: Path,
    n: int,
    batch: str,
    model_name: str,
    seed: int | None = None,
    stop=lambda: False,
) -> dict:
    rng = random.Random(seed)
    kept = {"tools": 0, "reasoning": 0, "tried": 0}
    # Answers checked by code against GSM8K's known answer don't need Lea's spot-check (T28);
    # the judged overnight pairs still do.
    status = "ready" if cfg.get_path("learning.auto_approve_verified", True) else "staged"
    for i, item in enumerate(gsm8k(data_path, rng, n)):
        if stop():
            break
        kept["tried"] += 1
        ex = tool_example(llm, item, cfg) if i % 2 == 0 else reasoning_example(llm, item)
        if ex is None:
            continue
        store.add_example(
            ex["messages"], chosen=ex["chosen"], source=f"sylvie-local:{model_name}",
            license="MIT (question: GSM8K) + apache-2.0 (local model answer, verified by code)",
            tag="general", status=status, batch=batch, tools=ex.get("tools"), reasoning=ex.get("reasoning"),
        )  # fmt: skip
        kept["tools" if "tools" in ex else "reasoning"] += 1
    return kept
