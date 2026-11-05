"""Builds a training set (mlx-lm chat format) from allowed sources only.

Sources (plan rule 4) — anything else is refused:
  - Lea's corrections and edits ("lea"), highest weight
  - answers from Sylvie's own local model that Lea accepted ("sylvie-local:…")
  - human-written open data with a training-friendly license ("open-dataset:oasst2", Apache 2.0)
Personal examples stay out unless `include_personal` (rule 12: personal facts live in memory, not
weights). Prompts that overlap the evaluation sets are dropped (evals are never trained on).
Every build writes a manifest: counts per source, license and tag, plus file hashes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from learning.store import ALLOWED_SOURCE_PREFIXES, LearningStore

EVAL_DIR = Path(__file__).resolve().parent.parent / "evals" / "sets"
OASST_LANGS = {"en", "es", "fr"}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def _words(text: str) -> set[str]:
    return set(_norm(text).split())


class EvalGuard:
    """Detects prompts that are (nearly) the same as an evaluation prompt."""

    def __init__(self, eval_dir: Path = EVAL_DIR):
        self.prompts = []
        for f in eval_dir.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.prompts.append(_words(json.loads(line).get("prompt", "")))

    def overlaps(self, prompt: str, threshold: float = 0.8) -> bool:
        w = _words(prompt)
        if not w:
            return False
        for p in self.prompts:
            if p and len(w & p) / len(w | p) >= threshold:
                return True
        return False


@dataclass
class Example:
    messages: list[dict]
    source: str
    license: str
    tag: str
    weight: float = 1.0

    @property
    def prompt(self) -> str:
        return next((m["content"] for m in reversed(self.messages) if m["role"] == "user"), "")


def oasst_examples(path: Path, limit: int, seed: int = 0) -> list[Example]:
    """Best-ranked conversation paths from OpenAssistant oasst2 (human-written, Apache 2.0)."""
    if not path.exists():
        return []
    rng = random.Random(seed)
    out: list[Example] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        trees = [json.loads(line) for line in f]
    rng.shuffle(trees)
    for tree in trees:
        root = tree.get("prompt") or {}
        if root.get("lang") not in OASST_LANGS or root.get("deleted") or not root.get("review_result", True):
            continue
        msgs, node = [], root
        while node and len(msgs) < 4:
            role = "user" if node.get("role") == "prompter" else "assistant"
            msgs.append({"role": role, "content": node.get("text", "").strip()})
            replies = [
                r for r in node.get("replies") or [] if not r.get("deleted") and r.get("rank") is not None
            ]
            node = min(replies, key=lambda r: r["rank"]) if replies else None
        if len(msgs) % 2:
            msgs = msgs[:-1]
        if (
            len(msgs) < 2
            or any(not m["content"] for m in msgs)
            or sum(len(m["content"]) for m in msgs) > 6000
        ):
            continue
        out.append(Example(msgs, "open-dataset:oasst2", "apache-2.0", "general"))
        if len(out) >= limit:
            break
    return out


@dataclass
class BuildReport:
    path: Path
    counts: dict = field(default_factory=dict)
    excluded: dict = field(default_factory=dict)


def build(
    store: LearningStore,
    out_root: Path,
    open_data: Path | None = None,
    open_limit: int = 600,
    include_personal: bool = False,
    valid_fraction: float = 0.08,
    seed: int = 0,
    name: str | None = None,
) -> BuildReport:
    guard = EvalGuard()
    examples: list[Example] = []
    excluded = {"personal": 0, "eval_overlap": 0, "bad_source": 0}
    for row in store.examples():
        if not row["source"].startswith(ALLOWED_SOURCE_PREFIXES):
            excluded["bad_source"] += 1
            continue
        if row["tag"] == "personal" and not include_personal:
            excluded["personal"] += 1
            continue
        msgs = row["messages"] + [{"role": "assistant", "content": row["chosen"]}]
        examples.append(Example(msgs, row["source"], row["license"], row["tag"], row["weight"]))
    if open_data is not None:
        examples += oasst_examples(open_data, open_limit, seed)

    kept: list[Example] = []
    for ex in examples:
        if guard.overlaps(ex.prompt):
            excluded["eval_overlap"] += 1
            continue
        # Weighting by repetition: Lea's corrections (weight 2) appear twice, etc.
        kept += [ex] * max(1, round(ex.weight))

    rng = random.Random(seed)
    rng.shuffle(kept)
    n_valid = max(1, int(len(kept) * valid_fraction)) if len(kept) > 10 else 0
    valid, train = kept[:n_valid], kept[n_valid:]

    name = name or datetime.now().strftime("build-%Y%m%d-%H%M%S")
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for split, rows in (("train", train), ("valid", valid or train[:1])):
        p = out / f"{split}.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            for ex in rows:
                f.write(json.dumps({"messages": ex.messages}, ensure_ascii=False) + "\n")
        hashes[split] = hashlib.sha256(p.read_bytes()).hexdigest()

    counts: dict = {"train": len(train), "valid": len(valid), "by_source": {}, "by_license": {}, "by_tag": {}}
    for ex in kept:
        for key, val in (
            ("by_source", ex.source.split(":")[0] if ex.source != "lea" else "lea"),
            ("by_license", ex.license),
            ("by_tag", ex.tag),
        ):
            counts[key][val] = counts[key].get(val, 0) + 1
    manifest = {"name": name, "created": datetime.now().isoformat(timespec="seconds"), "counts": counts,
                "excluded": excluded, "include_personal": include_personal, "sha256": hashes}  # fmt: skip
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    return BuildReport(out, counts, excluded)
