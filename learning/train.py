"""Fine-tunes a LoRA adapter with mlx-lm. The base model files are never modified.

Adapters are versioned: <data>/adapters/<model>/v001, v002, … each with meta.json (base model,
dataset manifest, settings, losses). On a 4-bit base model this is QLoRA.

    uv run python -m learning.train --model mlx-community/Qwen3.5-9B-4bit --build <dir> --iters 200
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from core.config import load_config


def model_short(model_id: str) -> str:
    return model_id.split("/")[-1]


def adapters_root(cfg, model_id: str) -> Path:
    return Path(cfg.get_path("paths.adapters_dir")).expanduser() / model_short(model_id)


def next_version(root: Path) -> str:
    nums = [int(p.name[1:]) for p in root.glob("v[0-9][0-9][0-9]") if p.is_dir()]
    return f"v{(max(nums) + 1) if nums else 1:03d}"


def parse_losses(log: str) -> dict:
    train = [float(x) for x in re.findall(r"Train loss ([\d.]+)", log)]
    val = [float(x) for x in re.findall(r"Val loss ([\d.]+)", log)]
    peak = [float(x) for x in re.findall(r"Peak mem ([\d.]+) GB", log)]
    return {
        "train_first": train[:1],
        "train_last": train[-1:],
        "val": val,
        "peak_mem_gb": max(peak) if peak else None,
    }


def train(
    model_id: str,
    build_dir: Path,
    iters: int = 200,
    num_layers: int = 16,
    learning_rate: float = 1e-5,
    batch_size: int = 1,
    max_seq_length: int = 2048,
    version: str | None = None,
) -> Path:
    cfg = load_config()
    root = adapters_root(cfg, model_id)
    root.mkdir(parents=True, exist_ok=True)
    version = version or next_version(root)
    out = root / version
    out.mkdir()
    args = [
        sys.executable, "-m", "mlx_lm", "lora",
        "--model", model_id, "--train", "--data", str(build_dir), "--adapter-path", str(out),
        "--fine-tune-type", "lora", "--mask-prompt", "--num-layers", str(num_layers),
        "--batch-size", str(batch_size), "--iters", str(iters), "--learning-rate", str(learning_rate),
        "--max-seq-length", str(max_seq_length), "--grad-checkpoint",
        "--steps-per-report", "10", "--steps-per-eval", str(max(20, iters // 4)), "--save-every", str(max(50, iters // 2)),
    ]  # fmt: skip
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    t0 = time.time()
    p = subprocess.run(args, capture_output=True, text=True, env=env)
    log = p.stdout + p.stderr
    (out / "train.log").write_text(log)
    manifest = json.loads((Path(build_dir) / "manifest.json").read_text())
    meta = {
        "version": version, "base_model": model_id, "created": datetime.now().isoformat(timespec="seconds"),
        "minutes": round((time.time() - t0) / 60, 1), "ok": p.returncode == 0,
        "settings": {"iters": iters, "num_layers": num_layers, "learning_rate": learning_rate, "batch_size": batch_size,
                     "max_seq_length": max_seq_length, "type": "lora (qlora on 4-bit base)", "mask_prompt": True},
        "dataset": manifest, "losses": parse_losses(log),
    }  # fmt: skip
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    if p.returncode != 0:
        raise RuntimeError(f"training failed, see {out / 'train.log'}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--layers", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-5)
    a = ap.parse_args()
    out = train(a.model, Path(a.build), a.iters, a.layers, a.lr)
    print(json.dumps(json.loads((out / "meta.json").read_text())["losses"], indent=1))
    print(f"Adapter saved to {out}")


if __name__ == "__main__":
    main()
