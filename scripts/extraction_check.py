"""How well a model learns facts from conversations (evals/sets/extraction.jsonl).

Usage: python scripts/extraction_check.py --model sylvie|apple --mode single|batch [--force]
       python scripts/extraction_check.py --compare     (both models, batch mode: saves the verdict)

- single: one extraction call per message (how Sylvie learned until 2026-11-09);
- batch: one call per conversation, as sleep-time learning does (efficiency plan A5).
Scored by code: every expected fact found (keywords), nothing forbidden saved (sensitive topics
without "remember"), nothing saved where nothing should be. The eval items are invented and are
never trained on (the dataset builder drops prompts that overlap evals/sets).
With --compare, `<data_dir>/small-model.json` records whether Apple's model passed: at most one item
fewer than Sylvie's model and no forbidden fact. core/small_model.py reads it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SET = Path(__file__).resolve().parent.parent / "evals" / "sets" / "extraction.jsonl"


def load_items() -> list[dict]:
    return [json.loads(x) for x in SET.read_text(encoding="utf-8").splitlines() if x.strip()]


def score(item: dict, facts: list[str]) -> tuple[bool, str]:
    c = item["check"]
    low = [f.lower() for f in facts]
    for kw in c.get("forbid", []):
        if any(kw in f for f in low):
            return False, f"saved a forbidden fact ({kw})"
    if c.get("none") and facts:
        return False, f"saved {len(facts)} fact(s) where there was nothing to learn"
    for group in c.get("expect", []):
        if not any(any(k in f for k in group) for f in low):
            return False, f"missed {group[0]}"
    return True, ""


def extract(model, item: dict, mode: str) -> tuple[list[str], int]:
    from core.orchestrator import join_for_learning, learning_parts
    from memory.extract import run_extraction

    items = [(n, m, "") for n, m in enumerate(item["messages"], 1)]
    parts = [[i] for i in items if len(i[1].strip()) >= 4] if mode == "single" else learning_parts(items)
    facts = []
    for part in parts:
        message, reply, note = join_for_learning(part)
        ex = run_extraction(model, message, reply, [], note=note)
        facts += [f["fact"] for f in ex.facts]
    return facts, len(parts)


def run(model_name: str, mode: str) -> dict:
    from core.config import load_config

    cfg = load_config()
    if model_name == "apple":
        from core.small_model import AppleModel

        model = AppleModel()
        ok, why = model.status()
        if not ok:
            raise SystemExit(f"Apple's model isn't available: {why}")
    else:
        from core.llm import LLM, limit_gpu_cache

        limit_gpu_cache()
        model = LLM(cfg)
        model.load()
    results, t0 = [], time.perf_counter()
    calls = 0
    for item in load_items():
        t = time.perf_counter()
        facts, n = extract(model, item, mode)
        calls += n
        ok, why = score(item, facts)
        results.append(
            {"id": item["id"], "ok": ok, "why": why, "facts": facts, "s": round(time.perf_counter() - t, 2)}
        )
        print(f"{'✓' if ok else '✗'} {item['id']:<7} {why or ''} {facts}", flush=True)
    out = {
        "model": model_name,
        "mode": mode,
        "date": datetime.now().isoformat(timespec="seconds"),
        "correct": sum(r["ok"] for r in results),
        "total": len(results),
        "forbidden": sum("forbidden" in r["why"] for r in results),
        "calls": calls,
        "seconds": round(time.perf_counter() - t0, 1),
        "results": results,
    }
    folder = cfg.data_dir / "evals"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"extraction-{model_name}-{mode}-{datetime.now():%Y%m%d-%H%M}.json").write_text(
        json.dumps(out, indent=1)
    )
    print(f"{model_name}/{mode}: {out['correct']}/{out['total']}, {calls} calls, {out['seconds']} s")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sylvie", choices=["sylvie", "apple"])
    ap.add_argument("--mode", default="batch", choices=["single", "batch"])
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even when the memory guard says no")
    a = ap.parse_args()
    from core.config import load_config
    from core.memguard import MemoryGuard

    cfg = load_config()
    if (a.compare or a.model == "sylvie") and not a.force:
        ok, why = MemoryGuard(cfg).heavy_ok(11)  # loads its own copy of Sylvie's model
        if not ok:
            raise SystemExit(f"Not running: {why}. Close some apps, or pass --force.")
    if not a.compare:
        run(a.model, a.mode)
        return
    apple = run("apple", "batch")
    sylvie = run("sylvie", "batch")
    passed = apple["forbidden"] == 0 and apple["correct"] >= sylvie["correct"] - 1
    verdict = {"passed": passed, "date": apple["date"], "apple": apple["correct"], "sylvie": sylvie["correct"],
               "total": apple["total"], "apple_s": apple["seconds"], "sylvie_s": sylvie["seconds"]}  # fmt: skip
    from core.small_model import VERDICT_FILE

    (cfg.data_dir / VERDICT_FILE).write_text(json.dumps(verdict, indent=1))
    print("verdict:", verdict)


if __name__ == "__main__":
    main()
