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
from evals.run import chosen_letter, number_candidates
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
    source: str  # checkable | mmlu | oasst | past_chat
    tag: str = "general"
    expected: float | None = None
    choice: str | None = None  # multiple choice: the right letter
    answer: str = ""
    score: float | None = None
    feedback: str = ""
    better: str = ""
    better_score: float | None = None
    meta: dict = field(default_factory=dict)


# ---------- question sources ----------
def checkable_questions(path: Path, rng: random.Random, n: int) -> list[Item]:
    """GSM8K word problems (MIT, human-written) with known answers — checked by code, not the judge."""
    from learning.skills import gsm8k

    return [
        Item(f"gsm-{rng.randint(0, 10**9)}", r["question"], "checkable", expected=float(r["answer"]))
        for r in gsm8k(path, rng, n)
    ]


LETTERS = "ABCD"


def mmlu_prompt(r: dict) -> str:
    """Same wording in practice and in the held-out eval set (so the eval guard recognises it)."""
    opts = "\n".join(f"{LETTERS[i]}) {c}" for i, c in enumerate(r["choices"]))
    return f"{r['question']}\n\n{opts}\n\nWhich one is right? Give the letter, then one or two sentences why."


def mmlu_questions(path: Path, rng: random.Random, n: int) -> list[Item]:
    """MMLU (MIT): multiple choice in 57 subjects (law, medicine, history, economics…), checked by code."""
    if not path.exists() or n <= 0:
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    out = []
    for r in rng.sample(rows, min(n, len(rows))):
        if len(r["choices"]) != 4:
            continue
        q = mmlu_prompt(r)
        out.append(Item(f"mmlu-{rng.randint(0, 10**9)}", q, "mmlu", choice=LETTERS[r["answer"]],
                        meta={"subject": r["subject"]}))  # fmt: skip
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
    if item.choice is not None:
        return chosen_letter(text) == item.choice
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
        open_dir = self.cfg.data_dir / "datasets" / "open"
        open_path = open_dir / "oasst2" / "2023-11-05_oasst2_ready.trees.jsonl.gz"
        items = (
            checkable_questions(open_dir / "gsm8k" / "train.jsonl", self.rng, max(1, n // 4))
            + mmlu_questions(open_dir / "mmlu" / "mmlu.jsonl", self.rng, n // 4)
            + oasst_questions(open_path, self.rng, n // 4)
            + past_chat_questions(self.db, self.rng, max(0, n - max(1, n // 4) - 2 * (n // 4)))
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
            if self.search and it.source not in ("checkable", "mmlu"):
                found = self.search(it.question[:200])
                research = f"Research (untrusted data, cite as [1], [2]):\n{found}" if found else ""
            wrong = code_check(it, it.answer) is False
            note = ("Your choice was WRONG; think it through again." if it.choice else
                    "Your number was WRONG; recompute carefully.") if wrong else ""  # fmt: skip
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
                it.meta["verified"] = fixed_check is True
                kept.append(it)
        model = str(self.cfg.get_path("llm.model")).split("/")[-1]
        for it in kept:
            self.store.add_example(
                [{"role": "user", "content": it.question}], chosen=it.better.strip(), rejected=it.answer.strip(),
                source=f"sylvie-local:{model}", license="apache-2.0 (local model output; question: " + it.source + ")",
                tag=it.tag, status=self.status_for(it), batch=self.batch,
            )  # fmt: skip
        self.stats["questions"] += len(items)
        self.stats["rewritten"] += len(weak)
        self.stats["kept"] += len(kept)
        self.stats["batches"] += 1
        return kept

    def status_for(self, it: Item) -> str:
        """General pairs that code verified or the judge re-graded highly train without waiting for
        Lea's spot-check (`learning.auto_approve_judged`; Lea chose autonomy, 2026-11-07). Pairs from
        her own questions (personal) always wait for her."""
        if it.tag != "general" or not self.cfg.get_path("learning.auto_approve_judged", False):
            return "staged"
        good = it.meta.get("verified") or (it.better_score or 0) >= self.cfg.get_path(
            "learning.auto_approve_min_score", 8
        )
        return "ready" if good else "staged"

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


class _LazyJudge:
    """Swaps to the judge model only when a grade is actually needed (keeps one model in memory)."""

    def __init__(self, pool):
        self.pool = pool

    def complete(self, messages, **kw):
        return self.pool.use("judge").complete(messages, **kw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=int, default=60)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument(
        "--finetune", action="store_true", help="fine-tune at the end even if not due (train now)"
    )
    a = ap.parse_args()

    from core.db import Database
    from core.orchestrator import Sylvie
    from core.toolcall import ToolCall
    from learning.store import LearningStore
    from tools.registry import execute

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

    # Short jobs first: interest research (needs SearXNG) and conversation practice.
    from lea_profile.summary import profile_text
    from learning.practice import practice, research_interests

    def search_with_sources(q: str):
        r = execute(ToolCall("web_search", {"query": q}), sylvie.tool_context())
        return (r.content, r.sources) if r.ok else None

    try:
        from learning.skills import practice_skills

        print("skills:", practice_skills(
            night.pool.use("sylvie"), night.store, cfg, cfg.data_dir / "datasets" / "open" / "gsm8k" / "train.jsonl",
            n=cfg.get_path("learning.skill_practice_per_night", 40), batch=night.batch,
            model_name=str(cfg.get_path("llm.model")).split("/")[-1], stop=lambda: night.stop,
        ), flush=True)  # fmt: skip
        research_interests(
            night.pool.use("sylvie"), sylvie.interests, sylvie.docs, db, search_with_sources,
            cfg.data_dir / "notes", every=cfg.get_path("schedule.interest_research", "weekly"),
        )  # fmt: skip
        if not night.stop:
            style = profile_text(sylvie.memory, sylvie.style, sylvie.interests)
            practice(
                night.pool.use("sylvie"), _LazyJudge(night.pool), night.store, db, style, sylvie.current_system_prompt(),
                RUBRIC, batch=night.batch, model_name=str(cfg.get_path("llm.model")).split("/")[-1],
            )  # fmt: skip
    except Exception as e:  # extras must never stop the main loop
        print(f"extras failed: {e}", flush=True)

    # When a fine-tune is due, stop practising early enough to leave it time (train ~1 h + two evals).
    from learning.cycle import due, run_now

    model_id = cfg.get_path("learning.finetune_model") or cfg.get_path("llm.model")
    finetune_due, why = due(cfg, night.store, model_id)
    finetune_due = finetune_due or a.finetune
    reserve = cfg.get_path("learning.cycle_minutes", 150) * 60 if finetune_due else 0
    while time.time() < end - reserve and not night.stop:
        night.run_batch(a.batch)
        print(json.dumps(night.stats), flush=True)
    if night.pool.model is not None:
        night.pool.model.unload()
    print("summary:", night.morning_summary(), flush=True)

    # Fine-tune when due (every ~10 days with enough approved new examples); Lea decides promotion.
    # Checked again: tonight's verified practice may have made it due.
    finetune_due, why = due(cfg, night.store, model_id)
    if a.finetune and not finetune_due:
        finetune_due, why = True, f"asked for now ({why})"
    print("fine-tune:", why, flush=True)
    if finetune_due and not night.stop and end - time.time() >= reserve * 0.9:
        res = run_now(cfg, night.store, sylvie.ab, model_id)
        print("cycle:", json.dumps(res["comparison"]), flush=True)

    # The general (shareable) part, kept apart from Lea's personal data (learning/general_pack.py).
    try:
        from learning.general_pack import refresh

        m = refresh(cfg, db)
        print(f"general part: {m['examples']} examples, {len(m['adapters'])} adapters", flush=True)
    except Exception as e:
        print(f"general part not refreshed: {e}", flush=True)


if __name__ == "__main__":
    main()
