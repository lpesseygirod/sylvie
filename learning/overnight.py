"""The overnight self-improvement loop (plan, Phase 5). Started by learning/scheduler.py.

    questions → Sylvie answers → judge grades (rubric + code checks + length penalty)
      → low scores: research + rewrite (thinking mode) → judge re-grades → keep clear improvements

Only one big model is loaded at a time (Sylvie's model or the judge), swapped per batch.
Kept pairs are *staged*: they're trained on only after Lea approves the morning spot-check.
Stops cleanly on SIGTERM (Lea started talking to Sylvie); progress is saved per batch.

    uv run python -m learning.overnight --max-minutes 60 --batch 12
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.config import ROOT, load_config
from core.jsonutil import extract_json
from evals.run import number_candidates
from learning.dataset import EvalGuard

RUBRIC = (ROOT / "core" / "prompts" / "rubric.md").read_text(encoding="utf-8")
CRITERIA = [
    ln.split("—")[0].strip(" 0123456789.") for ln in RUBRIC.splitlines() if re.match(r"^\d+\. \w+", ln)
]

JUDGE_PROMPT = """You are a strict grader. Grade the assistant's answer to the user's message.

Criteria (check all of them): {criteria}
{rubric}

User's message:
<message>
{question}
</message>

Answer to grade:
<answer>
{answer}
</answer>

Return JSON only: {{"score": <1-10 overall>, "worst_criterion": "<name>", "feedback": "<one or two sentences on what to fix>"}}"""

REWRITE_PROMPT = """Your earlier answer to Lea was graded {score}/10. Grader's feedback: {feedback}
{check_note}
Write a better answer: correct, in the same language as her message, as short as the question allows
(longer is NOT better). {research}

Lea's message:
<message>
{question}
</message>

Your earlier answer:
<answer>
{answer}
</answer>

Write ONLY the improved answer:"""


@dataclass
class Item:
    id: str
    question: str
    source: str  # checkable | oasst | past_chat
    tag: str = "general"
    expected: float | None = None
    answer: str = ""
    score: float | None = None
    feedback: str = ""
    better: str = ""
    better_score: float | None = None
    meta: dict = field(default_factory=dict)


# ---------- question sources ----------
def checkable_questions(rng: random.Random, n: int) -> list[Item]:
    """Arithmetic/finance with answers computed by code (seed differs from the eval sets)."""
    out = []
    for _ in range(n):
        kind = rng.choice(["pct", "mul", "fv"])
        if kind == "pct":
            p, x = rng.choice([4, 7.5, 12, 19, 25, 45]), rng.randint(30, 5000)
            q, v = (
                rng.choice(
                    [f"What's {p}% of {x}?", f"¿Cuánto es el {p}% de {x}?", f"Combien font {p} % de {x} ?"]
                ),
                p * x / 100,
            )
        elif kind == "mul":
            a, b = rng.randint(130, 9900), rng.randint(13, 990)
            q, v = f"{a} × {b} = ?", a * b
        else:
            pv, r, yrs = rng.choice([800, 3000, 15000]), rng.choice([1.5, 3, 5, 7]), rng.randint(2, 20)
            q, v = (
                f"If I put {pv}€ at {r}% a year compounded yearly for {yrs} years, how much will I have?",
                pv * (1 + r / 100) ** yrs,
            )
        out.append(Item(f"chk-{rng.randint(0, 10**9)}", q, "checkable", expected=round(v, 2)))
    return out


def oasst_questions(path: Path, rng: random.Random, n: int) -> list[Item]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        prompts = [
            t["prompt"]["text"]
            for t in map(json.loads, f)
            if t.get("prompt", {}).get("lang") in ("en", "es", "fr")
            and 15 < len(t["prompt"].get("text", "")) < 600
        ]
    return [
        Item(f"oa-{rng.randint(0, 10**9)}", q, "oasst") for q in rng.sample(prompts, min(n, len(prompts)))
    ]


def past_chat_questions(db, rng: random.Random, n: int) -> list[Item]:
    rows = db.query(
        "SELECT content FROM messages WHERE role = 'user' AND channel IN ('chat','voice','phone') AND length(content) > 25 "
        "ORDER BY id DESC LIMIT 400"
    )
    qs = [r["content"] for r in rows]
    return [
        Item(f"pc-{rng.randint(0, 10**9)}", q, "past_chat", tag="personal")
        for q in rng.sample(qs, min(n, len(qs)))
    ]


# ---------- grading ----------
def length_penalty(text: str) -> float:
    """Longer is not better: answers over ~150 words lose points (guards against padding)."""
    words = len(re.findall(r"\w+", text))
    return max(0.0, (words - 150) / 100)


def adjusted(score: float | None, text: str) -> float:
    return (score or 0) - length_penalty(text)


def code_check(item: Item, text: str) -> bool | None:
    if item.expected is None:
        return None
    return any(
        abs(x - item.expected) <= max(0.01, abs(item.expected) * 1e-4) for x in number_candidates(text)
    )


def grade(judge, item: Item, text: str, rng: random.Random) -> tuple[float, str]:
    crit = CRITERIA[:]
    rng.shuffle(crit)  # rotate the rubric so answers can't game one fixed phrasing
    raw = judge.complete(
        [
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(
                    criteria=", ".join(crit), rubric=RUBRIC, question=item.question, answer=text
                ),
            }
        ],
        max_tokens=300,
    )
    data = extract_json(raw) or {}
    try:
        score = float(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    ok = code_check(item, text)
    if ok is not None:  # checkable answers are verified by code, not by the judge
        score = min(score, 3.0) if not ok else max(score, 7.0)
    return score, str(data.get("feedback", ""))[:400]


# ---------- the loop ----------
class ModelPool:
    """Holds one big model at a time: 'sylvie' or 'judge'."""

    def __init__(self, cfg, factory=None):
        from core.llm import LLM

        self.factory = factory or (
            lambda role: LLM(
                cfg,
                model_id=cfg.get_path("llm.model")
                if role == "sylvie"
                else cfg.get_path("learning.judge_model", "mlx-community/gemma-4-31b-it-4bit"),
            )
        )
        self.role, self.model = None, None

    def use(self, role: str):
        if self.role != role:
            if self.model is not None:
                self.model.unload()
            self.model, self.role = self.factory(role), role
        return self.model


class Overnight:
    def __init__(
        self, cfg, store, db, pool: ModelPool, system_prompt: str, search=None, seed: int | None = None
    ):
        self.cfg, self.store, self.db, self.pool = cfg, store, db, pool
        self.system_prompt = system_prompt
        self.search = search  # callable(query) -> text or None
        self.rng = random.Random(seed)
        self.guard = EvalGuard()
        self.stop = False
        self.batch = "night-" + datetime.now().strftime("%Y%m%d-%H%M")
        self.stats = {"questions": 0, "rewritten": 0, "kept": 0, "batches": 0}

    def questions(self, n: int) -> list[Item]:
        open_path = (
            self.cfg.data_dir / "datasets" / "open" / "oasst2" / "2023-11-05_oasst2_ready.trees.jsonl.gz"
        )
        items = (
            checkable_questions(self.rng, n // 3)
            + oasst_questions(open_path, self.rng, n // 3)
            + past_chat_questions(self.db, self.rng, n - 2 * (n // 3))
        )
        return [i for i in items if not self.guard.overlaps(i.question)]

    def run_batch(self, n: int) -> list[Item]:
        items = self.questions(n)
        sylvie = self.pool.use("sylvie")
        for it in items:
            if self.stop:
                return []
            it.answer = sylvie.complete(
                [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": it.question}],
                max_tokens=700,
            )
        judge = self.pool.use("judge")
        for it in items:
            if self.stop:
                return []
            it.score, it.feedback = grade(judge, it, it.answer, self.rng)
        weak = [i for i in items if adjusted(i.score, i.answer) < 7 or code_check(i, i.answer) is False]
        sylvie = self.pool.use("sylvie")
        for it in weak:
            if self.stop:
                return []
            research = ""
            if self.search and it.source != "checkable":
                found = self.search(it.question[:200])
                research = f"Research (untrusted data, cite as [1], [2]):\n{found}" if found else ""
            note = "Your number was WRONG; recompute carefully." if code_check(it, it.answer) is False else ""
            it.better = sylvie.complete(
                [{"role": "system", "content": self.system_prompt},
                 {"role": "user", "content": REWRITE_PROMPT.format(score=it.score, feedback=it.feedback, check_note=note, research=research, question=it.question, answer=it.answer)}],
                thinking=True, effort="medium", max_tokens=2500,
            )  # fmt: skip
        judge = self.pool.use("judge")
        kept = []
        for it in weak:
            if self.stop or not it.better:
                continue
            it.better_score, _ = grade(judge, it, it.better, self.rng)
            fixed_check = code_check(it, it.better)
            clear_gain = adjusted(it.better_score, it.better) - adjusted(it.score, it.answer) >= 2
            not_bloated = len(it.better) <= len(it.answer) * 1.5 + 300
            if (fixed_check is not False) and clear_gain and not_bloated:
                kept.append(it)
        model = str(self.cfg.get_path("llm.model")).split("/")[-1]
        for it in kept:
            self.store.add_example(
                [{"role": "user", "content": it.question}], chosen=it.better.strip(), rejected=it.answer.strip(),
                source=f"sylvie-local:{model}", license="apache-2.0 (local model output; question: " + it.source + ")",
                tag=it.tag, status="staged", batch=self.batch,
            )  # fmt: skip
        self.stats["questions"] += len(items)
        self.stats["rewritten"] += len(weak)
        self.stats["kept"] += len(kept)
        self.stats["batches"] += 1
        return kept

    def morning_summary(self, sample: int = 20) -> Path:
        rows = [r for r in self.store.examples(status="staged") if r["batch"] == self.batch]
        picked = self.rng.sample(rows, min(sample, len(rows)))
        out_dir = self.cfg.data_dir / "overnight"
        out_dir.mkdir(exist_ok=True)
        p = out_dir / f"{self.batch}.json"
        p.write_text(json.dumps({
            "batch": self.batch, "stats": self.stats, "finished": datetime.now().isoformat(timespec="seconds"),
            "spot_check": [{"id": r["id"], "question": r["messages"][-1]["content"], "before": r["rejected"], "after": r["chosen"], "tag": r["tag"]} for r in picked],
        }, indent=1, ensure_ascii=False))  # fmt: skip
        return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=int, default=60)
    ap.add_argument("--batch", type=int, default=12)
    a = ap.parse_args()

    from core.db import Database
    from core.orchestrator import Sylvie
    from learning.store import LearningStore
    from tools.registry import ToolCall, execute

    cfg = load_config()
    sylvie = Sylvie.build(cfg)  # for prompts/stores only; its LLM stays unloaded (the pool loads models)
    db = Database(cfg.db_path, dim=cfg.get_path("embeddings.dim", 1024))
    ctx = sylvie.tool_context()

    def search(q: str) -> str | None:
        r = execute(ToolCall("web_search", {"query": q}), ctx)
        return r.content if r.ok else None

    night = Overnight(
        cfg, LearningStore(db), db, ModelPool(cfg), sylvie.current_system_prompt(), search=search
    )
    signal.signal(signal.SIGTERM, lambda *_: setattr(night, "stop", True))
    end = time.time() + a.max_minutes * 60
    while time.time() < end and not night.stop:
        night.run_batch(a.batch)
        print(json.dumps(night.stats), flush=True)
    if night.pool.model is not None:
        night.pool.model.unload()
    print("summary:", night.morning_summary(), flush=True)


if __name__ == "__main__":
    main()
