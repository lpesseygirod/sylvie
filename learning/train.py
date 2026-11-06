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
import shutil
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
    """Reads losses from mlx-lm's output (older "Iter N: Train loss X" lines or the newer table)."""
    log = re.sub(r"\x1b\[[0-9;]*m", "", log)
    num = r"([\d.]+|nan)"
    train = [float(x) for x in re.findall(rf"Train loss {num}", log)]
    val = [float(x) for x in re.findall(rf"Val loss {num}", log)]
    train += [float(m) for m in re.findall(rf"^\s*\d+\s+{num}\s+[▲▼=]", log, re.M)]
    val += [float(m) for m in re.findall(rf"^\s*\d+\s+val\s+{num}", log, re.M)]
    peak = [float(x) for x in re.findall(r"Peak mem ([\d.]+) GB", log)]
    # /usr/bin/time -l on macOS (bytes). "peak memory footprint" includes GPU memory and is the
    # number that matters on 36 GB; "maximum resident set size" misses most of it (measured 12 vs 38 GB).
    footprint = re.findall(r"(\d+)\s+peak memory footprint", log)
    rss = re.findall(r"(\d+)\s+maximum resident set size", log)
    peak += [int(x) / 1e9 for x in (footprint or rss)]
    return {
        "train_first": train[:1],
        "train_last": train[-1:],
        "val": val,
        "peak_mem_gb": round(max(peak), 2) if peak else None,
        "nan": any(x != x for x in train + val),
    }


def finite(obj):
    """NaN/inf → None, so meta.json stays valid JSON (the dashboard couldn't load NaN losses)."""
    if isinstance(obj, float) and obj != obj or obj in (float("inf"), float("-inf")):
        return None
    if isinstance(obj, dict):
        return {k: finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [finite(v) for v in obj]
    return obj


def adapter_is_finite(path: Path) -> bool:
    """False if any LoRA weight is NaN/inf (a diverged run: its answers would be garbage)."""
    import mlx.core as mx

    if not path.exists():
        return False
    return not any(bool((mx.isnan(v) | mx.isinf(v)).any().item()) for v in mx.load(str(path)).values())


def train(
    model_id: str,
    build_dir: Path,
    iters: int = 200,
    num_layers: int = 8,
    learning_rate: float = 1e-5,
    batch_size: int = 1,
    max_seq_length: int = 512,
    version: str | None = None,
    segment: int = 100,
    seed: int = 0,
    run=lambda args, env: subprocess.run(args, capture_output=True, text=True, env=env),
) -> Path:
    cfg = load_config()
    root = adapters_root(cfg, model_id)
    root.mkdir(parents=True, exist_ok=True)
    version = version or next_version(root)
    out = root / version
    out.mkdir()
    # Train in segments, each a fresh process resuming from the last adapter. Measured: one long
    # 300-step run on the 9B hit a Metal out-of-memory error at the end (memory builds up over a long
    # run), while short runs were fine. Segments also mean an interrupted night loses little.
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    t0 = time.time()
    logs, returncode, done = [], 0, 0
    seg, retried, lr, stopped = 0, False, learning_rate, None
    good = out / "segments"  # a copy of the adapter after every good segment, to roll back to
    good.mkdir()
    adapter = out / "adapters.safetensors"
    while done < iters:
        n = min(segment, iters - done)
        args = [
            "/usr/bin/time", "-l",  # reports peak memory
            sys.executable, "-m", "mlx_lm", "lora",
            "--model", model_id, "--train", "--data", str(build_dir), "--adapter-path", str(out),
            "--fine-tune-type", "lora", "--mask-prompt", "--num-layers", str(num_layers),
            "--batch-size", str(batch_size), "--iters", str(n), "--learning-rate", str(lr),
            "--max-seq-length", str(max_seq_length), "--grad-checkpoint", "--seed", str(seed + seg),
            "--steps-per-report", "10", "--steps-per-eval", str(n), "--save-every", str(n),
        ]  # fmt: skip
        if done > 0:
            args += ["--resume-adapter-file", str(adapter)]
        p = run(args, env)
        seg_log = p.stdout + p.stderr
        logs.append(f"===== segment {seg + 1}: steps {done + 1}-{done + n} (lr {lr}) =====\n" + seg_log)
        returncode = p.returncode
        if returncode != 0:
            break
        seg += 1
        if parse_losses(seg_log)["nan"] or not adapter_is_finite(adapter):
            # Measured on the 27B (2026-11-06): the loss went to NaN mid-way through step 255 and
            # every weight became NaN. Roll back to the last good segment; retry once, gentler.
            last = good / f"after_{done:04d}.safetensors"
            if last.exists():
                shutil.copy(last, adapter)
            else:
                adapter.unlink(missing_ok=True)
            if retried:
                stopped = f"loss became NaN twice; kept the adapter after step {done}"
                break
            retried, lr = True, lr / 2
            logs.append(f"===== NaN: rolled back to step {done}, retrying at lr {lr} =====")
            continue
        done += n
        shutil.copy(adapter, good / f"after_{done:04d}.safetensors")
    log = "\n".join(logs)
    (out / "train.log").write_text(log)
    manifest = json.loads((Path(build_dir) / "manifest.json").read_text())
    meta = {
        "version": version, "base_model": model_id, "created": datetime.now().isoformat(timespec="seconds"),
        "minutes": round((time.time() - t0) / 60, 1), "ok": returncode == 0 and done > 0, "steps_done": done,
        "stopped_early": stopped,
        "settings": {"iters": iters, "num_layers": num_layers, "learning_rate": learning_rate, "batch_size": batch_size,
                     "max_seq_length": max_seq_length, "type": "lora (qlora on 4-bit base)", "mask_prompt": True,
                     "segment": segment},
        "dataset": manifest, "losses": parse_losses(log),
    }  # fmt: skip
    (out / "meta.json").write_text(json.dumps(finite(meta), indent=1, ensure_ascii=False, allow_nan=False))
    if not meta["ok"]:
        raise RuntimeError(f"training failed, see {out / 'train.log'}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--layers", type=int, default=8)
    ap.add_argument("--max-seq", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-5)
    a = ap.parse_args()
    out = train(a.model, Path(a.build), a.iters, a.layers, a.lr, max_seq_length=a.max_seq)
    print(json.dumps(json.loads((out / "meta.json").read_text())["losses"], indent=1))
    print(f"Adapter saved to {out}")


if __name__ == "__main__":
    main()
