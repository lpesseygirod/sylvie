"""Downloads MMLU (MIT license, cais/mmlu): ~15k multiple-choice questions in 57 subjects.

Overnight practice uses them as checkable questions: Sylvie answers, code checks the letter.
Saved to <data dir>/datasets/open/mmlu/mmlu.jsonl. Uses Hugging Face's public rows API (JSON), so
no parquet library is needed.

    uv run python scripts/get_mmlu.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import load_config  # noqa: E402

API = "https://datasets-server.huggingface.co/rows"


def main() -> None:
    out = load_config().data_dir / "datasets" / "open" / "mmlu"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with httpx.Client(timeout=60, headers={"User-Agent": "sylvie-local"}) as client:
        for split in ("dev", "validation", "test"):
            offset = 0
            while True:
                for attempt in range(8):
                    r = client.get(API, params={"dataset": "cais/mmlu", "config": "all", "split": split,
                                                "offset": offset, "length": 100})  # fmt: skip
                    if r.status_code == 200:
                        break
                    time.sleep(min(90, 10 * 2**attempt))  # rate-limited (429): back off
                time.sleep(1.5)  # stay well under the public API's rate limit
                r.raise_for_status()
                page = r.json()
                batch = [x["row"] for x in page["rows"]]
                rows += [{"split": split, "subject": b["subject"], "question": b["question"],
                          "choices": b["choices"], "answer": int(b["answer"])} for b in batch]  # fmt: skip
                offset += len(batch)
                if not batch or offset >= page["num_rows_total"]:
                    break
            print(split, offset, flush=True)
    with open(out / "mmlu.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} questions → {out / 'mmlu.jsonl'}")


if __name__ == "__main__":
    main()
