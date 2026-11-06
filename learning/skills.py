"""Skill practice: keeps tool use and reasoning alive in fine-tunes (plan, Phases 4–5).

Measured: a LoRA trained only on plain chat (oasst2) made the 9B stop calling tools (tools eval
7/9 → 4/9). So each night Sylvie solves GSM8K word problems (MIT, human-written, with known
answers), alternating plain and thinking mode, always with the calculator available. Only
problems whose final answer code verifies as correct are kept, as up to two examples each:
  - the *decision*: question → Sylvie's calculator call. mlx-lm trains only on an example's last
    message, so without this the call itself was never learned (measured 2026-11-06: the 27B
    fine-tune v002 stopped calling the calculator for compound interest and got it wrong).
  - the *answer*: question, call and result → her final answer.
  In thinking mode her reasoning is kept too (Qwen recommends mostly reasoning data when tuning
  thinking models); if she solves it without the calculator, that answer alone is kept.
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


def solve(llm, item: dict, cfg, thinking: bool = False) -> list[dict]:
    """Sylvie solves one problem with the calculator available; returns the examples to keep."""
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": item["question"]}]
    sp = _gen(llm, msgs, tools=[CALCULATOR], thinking=thinking, max_tokens=2000 if thinking else 800)
    think = sp.thinking_text.strip() if thinking else None
    if thinking and not think:
        return []
    user = {"role": "user", "content": item["question"]}
    text = sp.visible_text.strip()
    calls = [c for c in sp.tool_calls if c.name == "calculator"]
    if not calls:
        # Solved in her head: worth keeping only as reasoning practice (plain mode must use the tool).
        if not thinking or not correct(text, item["answer"]) or FILLER.match(text):
            return []
        return [{"messages": [user], "chosen": text, "reasoning": think, "tools": [CALCULATOR]}]
    call_msgs = [c.to_message() for c in calls]
    convo = [user, {"role": "assistant", "content": text, "tool_calls": call_msgs}]
    ctx = ToolContext(cfg=cfg)
    for c in calls:
        convo.append({"role": "tool", "content": execute(c, ctx).content})
    final = _gen(llm, [msgs[0]] + convo, tools=[CALCULATOR])
    answer = final.visible_text.strip()
    if final.tool_calls or not correct(answer, item["answer"]) or FILLER.match(answer):
        return []
    out = [{"messages": convo, "chosen": answer, "tools": [CALCULATOR]}]
    if not FILLER.match(text):
        out.insert(0, {"messages": [user], "chosen": text, "tool_calls": call_msgs, "reasoning": think,
                       "tools": [CALCULATOR]})  # fmt: skip
    return out


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
    kept = {"solved": 0, "tried": 0, "examples": 0}
    # Answers checked by code against GSM8K's known answer don't need Lea's spot-check (T28);
    # the judged overnight pairs still do.
    status = "ready" if cfg.get_path("learning.auto_approve_verified", True) else "staged"
    for i, item in enumerate(gsm8k(data_path, rng, n)):
        if stop():
            break
        kept["tried"] += 1
        examples = solve(llm, item, cfg, thinking=i % 2 == 1)
        kept["solved"] += bool(examples)
        for ex in examples:
            store.add_example(
                ex["messages"], chosen=ex["chosen"], source=f"sylvie-local:{model_name}",
                license="MIT (question: GSM8K) + apache-2.0 (local model answer, verified by code)",
                tag="general", status=status, batch=batch, tools=ex.get("tools"),
                reasoning=ex.get("reasoning"), tool_calls=ex.get("tool_calls"),
            )  # fmt: skip
            kept["examples"] += 1
    return kept
