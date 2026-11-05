"""The fine-tune cycle (plan, Phases 4–5): build → train → evaluate → blind A/B for Lea.

Runs at the end of a night when it's due: at least `schedule.finetune_every_days` since the last
adapter and at least `learning.min_new_examples` approved examples since then. It never promotes
by itself — a version is promoted only when it has no regressions AND wins Lea's blind A/B
(learning/promote.py → gate, done from the dashboard).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from learning.train import adapters_root


def last_adapter_time(cfg, model_id: str) -> datetime | None:
    root = adapters_root(cfg, model_id)
    times = []
    for meta in root.glob("v[0-9][0-9][0-9]/meta.json") if root.exists() else []:
        try:
            times.append(datetime.fromisoformat(json.loads(meta.read_text())["created"]).replace(tzinfo=UTC))
        except (OSError, ValueError, KeyError):
            pass
    return max(times) if times else None


def due(cfg, store, model_id: str, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(UTC)
    every = cfg.get_path("schedule.finetune_every_days", 10)
    last = last_adapter_time(cfg, model_id)
    if last and now - last < timedelta(days=every):
        return False, f"last fine-tune {(now - last).days} days ago (every {every})"
    since = last.isoformat() if last else "1970-01-01"
    fresh = [e for e in store.examples() if e["created_at"] >= since]
    need = cfg.get_path("learning.min_new_examples", 50)
    if len(fresh) < need:
        return False, f"only {len(fresh)} new approved examples (need {need})"
    return True, "due"


def run_cycle(cfg, store, ab, model_id: str, open_data: Path | None, train_fn, eval_fn) -> dict:
    """train_fn(model_id, build_dir) -> adapter dir; eval_fn(label, adapter) -> report dict."""
    from evals.run import compare
    from learning.dataset import build
    from learning.promote import active_adapter

    rep = build(store, cfg.data_dir / "datasets" / "builds", open_data=open_data)
    adapter_dir = train_fn(model_id, rep.path)
    current = active_adapter(cfg, model_id)
    old = eval_fn("current", current.name if current else None)
    new = eval_fn(adapter_dir.name, adapter_dir.name)
    comparison = compare(old, new)
    pairs = ab.create_round(
        old, new, round_name=f"{adapter_dir.name}-vs-{current.name if current else 'base'}"
    )
    result = {"adapter": adapter_dir.name, "dataset": rep.counts, "comparison": comparison, "ab_pairs": pairs}
    (adapter_dir / "cycle.json").write_text(json.dumps(result, indent=1))
    return result
