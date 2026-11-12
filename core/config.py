"""Loads config.yaml and resolves paths. One place for every setting."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Rule 1: telemetry off in every tool that has it, set as early as possible.
for _var, _val in {
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "TOKENIZERS_PARALLELISM": "false",
    # Runtime never contacts Hugging Face; downloads are explicit (`make download`).
    "HF_HUB_OFFLINE": "1",
}.items():
    os.environ.setdefault(_var, _val)


class Config(dict):
    """dict with dotted access: cfg.get_path('llm.model')."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data_dir(self) -> Path:
        override = os.environ.get("SYLVIE_DATA_DIR")
        path = Path(override or self.get_path("paths.data_dir")).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sylvie.db"


def config_path(path: str | None = None) -> Path:
    """The config in use: an explicit path, $SYLVIE_CONFIG, else config.local.yaml (your own settings,
    not in git) if it exists, else the shared config.yaml with safe defaults."""
    if path or os.environ.get("SYLVIE_CONFIG"):
        return Path(path or os.environ["SYLVIE_CONFIG"])
    local = ROOT / "config.local.yaml"
    return local if local.exists() else ROOT / "config.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> Config:
    cfg_path = config_path(path)
    with open(cfg_path, encoding="utf-8") as f:
        return Config(yaml.safe_load(f) or {})
