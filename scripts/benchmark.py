"""Phase 0 benchmark: speed and memory of the main model.

Measures, for fast mode and thinking mode, at short and long context:
time to first token, prompt (prefill) speed, generation speed, peak memory.
Writes results to docs/benchmarks/<date>.json and prints a table.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ROOT, load_config  # noqa: E402
from core.llm import LLM  # noqa: E402

FILLER = (
    "The history of Madrid begins with a small Moorish fortress called Mayrit, built in the ninth century "
    "near the Manzanares river. Over the centuries the town grew, and in 1561 Philip II moved the royal court "
    "there, which turned it into the capital of Spain. "
)


def run_case(llm: LLM, name: str, context_tokens: int, thinking: bool, max_tokens: int) -> dict:
    question = "In two sentences, why did Madrid become the capital of Spain?"
    msgs = [{"role": "system", "content": "You are Sylvie, a helpful assistant."}]
    if context_tokens > 500:
        reps = max(1, context_tokens // max(1, llm.count_tokens(FILLER)))
        msgs.append({"role": "user", "content": "Background notes:\n" + FILLER * reps})
        msgs.append({"role": "assistant", "content": "Got it."})
    msgs.append({"role": "user", "content": question})

    t0 = time.perf_counter()
    stats = None
    text = ""
    for chunk in llm.stream(msgs, thinking=thinking, effort="low", max_tokens=max_tokens):
        text += chunk.text
        if chunk.done:
            stats = chunk.stats
    wall = time.perf_counter() - t0
    return {
        "case": name,
        "thinking": thinking,
        "prompt_tokens": stats.prompt_tokens,
        "generated_tokens": stats.generation_tokens,
        "ttft_s": round(stats.time_to_first_token, 2),
        "prefill_tok_s": round(stats.prompt_tps, 1),
        "gen_tok_s": round(stats.generation_tps, 1),
        "peak_mem_gb": round(stats.peak_memory_gb, 2),
        "wall_s": round(wall, 2),
        "sample": text[:200],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--long", type=int, default=8000, help="tokens of context for the long case")
    args = ap.parse_args()

    cfg = load_config()
    llm = LLM(cfg, model_id=args.model)
    t0 = time.perf_counter()
    llm.load()
    load_s = time.perf_counter() - t0
    import mlx.core as mx

    weights_gb = mx.get_active_memory() / 1e9
    print(f"Loaded {llm.model_id} in {load_s:.1f}s, weights in memory: {weights_gb:.2f} GB")

    # Warm-up (first call compiles kernels)
    list(llm.stream([{"role": "user", "content": "Hi"}], max_tokens=8))

    results = []
    for name, ctx, thinking, mt in [
        ("short / fast", 0, False, 200),
        ("short / thinking", 0, True, 800),
        (f"long {args.long} / fast", args.long, False, 200),
        (f"long {args.long} / thinking", args.long, True, 800),
    ]:
        mx.reset_peak_memory()
        r = run_case(llm, name, ctx, thinking, mt)
        results.append(r)
        print(
            f"{r['case']:<24} prompt={r['prompt_tokens']:>6} ttft={r['ttft_s']:>5}s "
            f"prefill={r['prefill_tok_s']:>7} tok/s gen={r['gen_tok_s']:>5} tok/s peak={r['peak_mem_gb']} GB"
        )

    out_dir = ROOT / "docs" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{date.today().isoformat()}-{llm.model_id.split('/')[-1]}.json"
    out.write_text(
        json.dumps(
            {
                "model": llm.model_id,
                "load_s": round(load_s, 1),
                "weights_gb": round(weights_gb, 2),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"Saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
