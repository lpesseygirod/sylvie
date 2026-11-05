"""Which adapter version Sylvie uses, with one-click rollback.

The active version lives in <data>/state.json (not in config.yaml), per base model:
    {"adapters": {"Qwen3.8-27B-4bit": {"active": "v002", "history": ["v001", "v002"]}}}
A version is promoted only if it passes the gate: no regressions on the automatic evals and it
wins Lea's blind A/B (plan, Phase 4).
"""

from __future__ import annotations

import json
from pathlib import Path

from learning.train import adapters_root, model_short


def _state_path(cfg) -> Path:
    return cfg.data_dir / "state.json"


def load_state(cfg) -> dict:
    p = _state_path(cfg)
    return json.loads(p.read_text()) if p.exists() else {"adapters": {}}


def save_state(cfg, state: dict) -> None:
    _state_path(cfg).write_text(json.dumps(state, indent=1))


def active_adapter(cfg, model_id: str) -> Path | None:
    entry = load_state(cfg)["adapters"].get(model_short(model_id))
    if not entry or not entry.get("active"):
        return None
    p = adapters_root(cfg, model_id) / entry["active"]
    return p if (p / "adapters.safetensors").exists() else None


def gate(eval_comparison: dict, ab: dict | None) -> tuple[bool, str]:
    if eval_comparison.get("regressions"):
        return False, "regressions on: " + ", ".join(eval_comparison["regressions"])
    if not ab or ab.get("decided", 0) == 0:
        return False, "waiting for Lea's blind A/B"
    if ab["new_wins"] <= ab["old_wins"]:
        return False, f"didn't win the blind A/B ({ab['new_wins']} vs {ab['old_wins']})"
    return True, "passed"


def promote(cfg, model_id: str, version: str) -> None:
    root = adapters_root(cfg, model_id)
    if not (root / version / "adapters.safetensors").exists():
        raise FileNotFoundError(f"no adapter {version} for {model_short(model_id)}")
    state = load_state(cfg)
    entry = state["adapters"].setdefault(model_short(model_id), {"active": None, "history": []})
    entry["active"] = version
    entry["history"].append(version)
    save_state(cfg, state)


def rollback(cfg, model_id: str) -> str | None:
    """Back to the previous version (or the plain base model). Returns the now-active version."""
    state = load_state(cfg)
    entry = state["adapters"].get(model_short(model_id))
    if not entry or not entry["history"]:
        return None
    entry["history"].pop()
    entry["active"] = entry["history"][-1] if entry["history"] else None
    save_state(cfg, state)
    return entry["active"]
