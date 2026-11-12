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
    tools: list | None = None

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


MIN_TARGET_TOKENS = 4


def token_measure(model_id: str):
    """Token counts as mlx-lm's ChatDataset computes them (same tokenizer and chat template)."""
    from huggingface_hub import snapshot_download
    from mlx_lm.tuner.datasets import ChatDataset
    from mlx_lm.utils import load_tokenizer

    path = snapshot_download(
        model_id, local_files_only=True, allow_patterns=["*.json", "*.jinja", "*.txt", "*.model", "*.py"]
    )
    tok = load_tokenizer(Path(path))

    def measure(messages: list[dict], tools: list | None) -> tuple[int, int]:
        row = {"messages": messages} | ({"tools": tools} if tools else {})
        tokens, offset = ChatDataset([row], tok, mask_prompt=True).process(row)
        return len(tokens), offset

    return measure


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
    max_chars: int = 1600,
    measure=None,
    max_tokens: int = 512,
    privacy=None,
) -> BuildReport:
    """`measure(messages, tools) -> (total_tokens, prompt_tokens)` (see `token_measure`) checks each
    example against the training length exactly as mlx-lm will tokenize it.
    `privacy` (learning/privacy.PersonalTerms): a "general" example that mentions Lea (her name, a
    phone number, her projects…) is treated as personal and left out."""
    from learning.privacy import example_text

    guard = EvalGuard()
    examples: list[Example] = []
    excluded = {
        "personal": 0,
        "mentions_lea": 0,
        "eval_overlap": 0,
        "bad_source": 0,
        "system_prompt": 0,
        "too_long": 0,
        "nothing_to_learn": 0,
    }
    for row in store.examples():
        if not row["source"].startswith(ALLOWED_SOURCE_PREFIXES):
            excluded["bad_source"] += 1
            continue
        if any(m.get("role") == "system" for m in row["messages"]):
            # The system prompt carries Lea's profile and memory, including notes Claude wrote
            # (memory/claude_memory.py): it never goes into training (rule 4).
            excluded["system_prompt"] += 1
            continue
        if row["tag"] == "personal" and not include_personal:
            excluded["personal"] += 1
            continue
        if privacy is not None and not include_personal and privacy.is_personal(*example_text(row)):
            excluded["mentions_lea"] += 1
            continue
        final = {"role": "assistant", "content": row["chosen"]}
        if row.get("reasoning"):
            final["reasoning_content"] = row["reasoning"]  # the chat template renders it inside <think>
        if row.get("tool_calls"):
            final["tool_calls"] = json.loads(row["tool_calls"])  # trains the decision to call a tool
        tools = json.loads(row["tools"]) if row.get("tools") else None
        examples.append(
            Example(
                row["messages"] + [final], row["source"], row["license"], row["tag"], row["weight"], tools
            )
        )
    if open_data is not None:
        examples += oasst_examples(open_data, open_limit, seed)

    kept: list[Example] = []
    for ex in examples:
        if guard.overlaps(ex.prompt):
            excluded["eval_overlap"] += 1
            continue
        # Long examples are dropped, not truncated: training memory grows steeply with length on
        # these hybrid-attention models (a ~1300-token sample needed >40 GB on the 9B).
        if sum(len(m["content"]) for m in ex.messages) > max_chars:
            excluded["too_long"] += 1
            continue
        if measure is not None:
            # mlx-lm cuts examples at max_tokens and trains only on the last message. Measured
            # 2026-11-06: the calculator's tool description alone adds ~390 prompt tokens, so many
            # tool examples were cut before their answer → no target tokens → loss 0/0 = NaN, and
            # one such step turned every weight of the 27B fine-tune into NaN.
            total, prompt = measure(ex.messages, ex.tools)
            if total > max_tokens:
                excluded["too_long"] += 1
                continue
            if total - prompt < MIN_TARGET_TOKENS:
                excluded["nothing_to_learn"] += 1
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
                f.write(
                    json.dumps(
                        {"messages": ex.messages} | ({"tools": ex.tools} if ex.tools else {}),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
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
                "excluded": excluded, "include_personal": include_personal,
                "privacy_checked": privacy is not None, "sha256": hashes}  # fmt: skip
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    return BuildReport(out, counts, excluded)
