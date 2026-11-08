"""Summarises Sylvie's memory timeline (core/memwatch.py) for one day.

Usage: python scripts/memreport.py [YYYY-MM-DD]   (default: today)

Prints: minutes at each macOS pressure level, swap and Sylvie's footprint (min / median / max),
which Sylvie jobs ran while memory was tight, and the biggest apps at the worst moment.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a line cut short by a crash
    return rows


def summary(rows: list[dict]) -> dict:
    if not rows:
        return {"samples": 0}
    # Samples are 60 s apart normally, 10 s while memory is tight: weight each by the gap to the next.
    from datetime import datetime

    ts = [datetime.fromisoformat(r["t"]) for r in rows]
    gaps = [min((b - a).total_seconds(), 120) for a, b in zip(ts, ts[1:], strict=False)] + [60]
    minutes = Counter()
    tight_jobs = Counter()
    for r, g in zip(rows, gaps, strict=True):
        p = r["mac"]["pressure"]
        minutes[p] += g / 60
        if p != "normal":
            st = r.get("state", {})
            for what in filter(
                None, [st.get("bg_job"), st.get("night_job"), "in a turn" if st.get("in_turn") else None]
            ):
                tight_jobs[what] += g / 60

    def stats(key):
        vals = [v for v in (key(r) for r in rows) if v is not None]
        if not vals:
            return None
        return {"min": min(vals), "median": round(statistics.median(vals), 2), "max": max(vals)}

    worst = max(rows, key=lambda r: (r["mac"].get("swap_gb") or 0) - (r["mac"].get("free_pct") or 0) / 10)
    return {
        "samples": len(rows),
        "from": rows[0]["t"],
        "to": rows[-1]["t"],
        "minutes": {k: round(v) for k, v in minutes.items()},
        "swap_gb": stats(lambda r: r["mac"].get("swap_gb")),
        "free_pct": stats(lambda r: r["mac"].get("free_pct")),
        "sylvie_gb": stats(lambda r: r.get("sylvie_gb")),
        "mlx_cache_gb": stats(lambda r: r.get("mlx", {}).get("cache_gb")),
        "night_job_gb": stats(lambda r: r.get("state", {}).get("night_job_gb")),
        "jobs_while_tight_min": {k: round(v) for k, v in tight_jobs.most_common(8)},
        "worst": {
            "t": worst["t"],
            "mac": worst["mac"],
            "sylvie_gb": worst.get("sylvie_gb"),
            "state": worst.get("state"),
            "top": worst.get("top"),
        },  # fmt: skip
    }


def main() -> None:
    from core.config import load_config

    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    path = load_config().data_dir / "logs" / f"memory-{day}.jsonl"
    if not path.exists():
        print(f"No memory timeline for {day} ({path})")
        return
    print(json.dumps(summary(load(path)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
