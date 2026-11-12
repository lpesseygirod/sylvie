"""The general part of Sylvie, kept apart from Lea's personal data, ready to give to other people.

My request: "make sure the general things learned are stored in another part than my personal
preferences… I would like to be able to put a base model to other people and it would help them,
learning from them."

Two parts:
- **Personal** (stays on this Mac, never exported): `sylvie.db` — conversations, memory facts,
  episodes, profile, style, preferences, shortcuts, personal training examples — plus `state.json`,
  logs, overnight reports and eval results.
- **General** (`<data dir>/general/`, refreshed after every night): training examples tagged general
  that pass the privacy check (learning/privacy.py), and adapters trained only on such data.

`make general-pack` refreshes the folder. On someone else's Mac, `make import-general PACK=<folder>`
adds its examples to their Sylvie (she keeps training on them, together with what she learns from
that person) and copies its adapters as candidate versions. Their personal data stays on their Mac.
Every file is scanned again before it is written: if anything about Lea is found, nothing is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from learning.privacy import PersonalTerms, example_text
from learning.store import ALLOWED_SOURCE_PREFIXES

FIELDS = (
    "kind",
    "messages",
    "chosen",
    "rejected",
    "reasoning",
    "tool_calls",
    "tools",
    "source",
    "license",
    "weight",
)
ADAPTER_FILES = ("adapters.safetensors", "adapter_config.json")
README = """# Sylvie — general part

What Sylvie learned that is not about anyone in particular: maths, finance, tools, languages,
general answers. It contains **no conversations, memory, profile, preferences or names**: every
example was checked against the owner's memory before export (learning/privacy.py).

- `examples.jsonl`: training examples (sources and licenses in each line; only local-model output
  and open datasets, never text written by Claude).
- `adapters/<model>/<version>/`: fine-tunes trained only on general data (optional to use).
- `manifest.json`: counts and checksums.

To use it in another Sylvie: `make import-general PACK=/path/to/this/folder`.
"""


class LeakFound(RuntimeError):
    pass


def shareable_adapter(meta: dict) -> bool:
    """Trained without personal data: a privacy-checked build without personal examples, or a build
    made only from open datasets."""
    ds = meta.get("dataset") or {}
    if not meta.get("ok", True) or ds.get("include_personal"):
        return False
    if ds.get("privacy_checked"):
        return True
    sources = set((ds.get("counts") or {}).get("by_source", {}))
    return bool(sources) and sources <= {"open-dataset"}


def _adapters_dir(cfg) -> Path:
    return Path(cfg.get_path("paths.adapters_dir")).expanduser()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def export(cfg, store, privacy: PersonalTerms, out: Path | None = None) -> dict:
    out = Path(out or Path(cfg.data_dir) / "general")
    rows, dropped = [], 0
    for r in store.examples(tag="general", status="ready"):
        if not r["source"].startswith(ALLOWED_SOURCE_PREFIXES):
            continue
        if privacy.is_personal(*example_text(r)):
            dropped += 1
            continue
        rows.append({k: r.get(k) for k in FIELDS})
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    for line in lines:  # second check on exactly what will be written
        if hit := privacy.find(line):
            raise LeakFound(f"an example still mentions {hit}; nothing written")

    adapters = []
    root = _adapters_dir(cfg)
    for meta_p in sorted(root.glob("*/v[0-9][0-9][0-9]/meta.json")):
        meta = json.loads(meta_p.read_text())
        if meta.get("imported") or not shareable_adapter(meta):
            continue
        if not all((meta_p.parent / f).exists() for f in ADAPTER_FILES):
            continue
        keep = {k: meta.get(k) for k in ("version", "base_model", "created", "settings")}
        keep["dataset"] = {k: (meta.get("dataset") or {}).get(k) for k in ("counts", "privacy_checked")}
        if hit := privacy.find(json.dumps(keep, ensure_ascii=False)):
            raise LeakFound(f"adapter notes mention {hit}; nothing written")
        adapters.append((meta_p.parent, keep))

    out.mkdir(parents=True, exist_ok=True)
    (out / "examples.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    (out / "README.md").write_text(README, encoding="utf-8")
    files = {"examples.jsonl": _sha(out / "examples.jsonl")}
    listed = []
    for src, keep in adapters:
        dst = out / "adapters" / src.parent.name / src.name
        dst.mkdir(parents=True, exist_ok=True)
        for f in ADAPTER_FILES:
            if not (dst / f).exists() or _sha(dst / f) != _sha(src / f):
                shutil.copy2(src / f, dst / f)
            files[f"adapters/{src.parent.name}/{src.name}/{f}"] = _sha(dst / f)
        (dst / "meta.json").write_text(json.dumps(keep, indent=1, ensure_ascii=False))
        listed.append(f"{src.parent.name}/{src.name}")
    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "examples": len(rows),
        "left_out_because_personal": dropped,
        "adapters": listed,
        "never_contains": ["conversations", "memory facts", "episodes", "profile", "preferences", "names"],
        "sha256": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    return manifest


def import_pack(cfg, store, pack: Path) -> dict:
    """Into someone else's Sylvie: examples become general training data, adapters candidate versions."""
    pack = Path(pack)
    manifest = json.loads((pack / "manifest.json").read_text())
    for rel, sha in manifest["sha256"].items():
        if _sha(pack / rel) != sha:
            raise ValueError(f"{rel} doesn't match the manifest (damaged or changed)")
    have = {(json.dumps(e["messages"], ensure_ascii=False), e["chosen"]) for e in store.examples(status=None)}
    added = 0
    for line in (pack / "examples.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if (json.dumps(r["messages"], ensure_ascii=False), r["chosen"]) in have:
            continue
        store.add_example(
            r["messages"], chosen=r["chosen"], rejected=r.get("rejected"), source=r["source"],
            license=r["license"], tag="general", weight=r.get("weight") or 1.0, status="ready",
            batch="general-pack", tools=json.loads(r["tools"]) if r.get("tools") else None,
            reasoning=r.get("reasoning"), tool_calls=json.loads(r["tool_calls"]) if r.get("tool_calls") else None,
        )  # fmt: skip
        added += 1
    copied = []
    for name in manifest.get("adapters", []):
        model, version = name.split("/")
        root = _adapters_dir(cfg) / model
        n = 1 + max([int(p.name[1:]) for p in root.glob("v[0-9][0-9][0-9]")] or [0])
        dst = root / f"v{n:03d}"
        dst.mkdir(parents=True)
        for f in ADAPTER_FILES:
            shutil.copy2(pack / "adapters" / model / version / f, dst / f)
        meta = json.loads((pack / "adapters" / model / version / "meta.json").read_text())
        meta.update(version=dst.name, imported=True, imported_from=version, ok=True)
        (dst / "meta.json").write_text(json.dumps(meta, indent=1))
        copied.append(f"{model}/{dst.name}")
    return {"examples_added": added, "adapters": copied}


def refresh(cfg, db) -> dict:
    from learning.store import LearningStore

    return export(cfg, LearningStore(db), PersonalTerms.load(cfg, db))


def main() -> None:
    from core.config import load_config
    from core.db import Database
    from learning.store import LearningStore

    ap = argparse.ArgumentParser(description="Sylvie's general (shareable) part")
    ap.add_argument("--import", dest="pack", help="import a general pack folder into this Sylvie")
    a = ap.parse_args()
    cfg = load_config()
    db = Database(cfg.db_path, dim=cfg.get_path("embeddings.dim", 1024))
    if a.pack:
        print(json.dumps(import_pack(cfg, LearningStore(db), Path(a.pack)), indent=1))
    else:
        m = refresh(cfg, db)
        print(json.dumps({k: m[k] for k in ("examples", "left_out_because_personal", "adapters")}, indent=1))
        print("Folder:", Path(cfg.data_dir) / "general")


if __name__ == "__main__":
    main()
