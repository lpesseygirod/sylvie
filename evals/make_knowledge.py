"""Builds the held-out general-knowledge eval set from MMLU (MIT): 150 questions, a few per subject.

Fixed seed, so it never changes once made; the eval guard keeps these exact questions out of
practice and training. Answers are MMLU's own (checked by code: the letter), never written by Claude.

    uv run python evals/make_knowledge.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_config  # noqa: E402
from learning.overnight import LETTERS, mmlu_prompt  # noqa: E402

N = 150


def main() -> None:
    src = load_config().data_dir / "datasets" / "open" / "mmlu" / "mmlu.jsonl"
    rows = [json.loads(x) for x in src.read_text(encoding="utf-8").split("\n") if x.strip()]
    by_subject = defaultdict(list)
    for r in rows:
        if r["split"] == "test" and len(r["choices"]) == 4:
            by_subject[r["subject"]].append(r)
    rng = random.Random(20261107)
    for v in by_subject.values():
        rng.shuffle(v)
    picked, i = [], 0
    while len(picked) < N:  # round-robin over subjects: every subject is represented
        for subject in sorted(by_subject):
            if i < len(by_subject[subject]) and len(picked) < N:
                picked.append(by_subject[subject][i])
        i += 1
    out = Path(__file__).parent / "sets" / "knowledge.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for n, r in enumerate(picked):
            item = {"set": "knowledge", "id": f"knowledge-{n}", "subject": r["subject"], "prompt": mmlu_prompt(r),
                    "check": {"type": "choice", "value": LETTERS[r["answer"]]}}  # fmt: skip
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"{len(picked)} questions from {len(by_subject)} subjects → {out}")


if __name__ == "__main__":
    main()
