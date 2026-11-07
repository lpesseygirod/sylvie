"""Downloads Wikipedia (English, Spanish, French) for Sylvie's offline library.

Source: Hugging Face `wikimedia/wikipedia`, snapshot 20231101 (CC BY-SA 3.0 / GFDL): parquet files
with each article's title, URL and plain text. ~20 GB in all; resumes if interrupted.
Used only for looking things up (with credit to the article), never as training data.

    uv run python scripts/get_wikipedia.py            # en es fr
    uv run python scripts/get_wikipedia.py es fr
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import snapshot_download  # noqa: E402

from core.config import load_config  # noqa: E402

SNAPSHOT = "20231101"


def main() -> None:
    langs = sys.argv[1:] or ["es", "fr", "en"]  # smallest first: something usable sooner
    out = load_config().data_dir / "datasets" / "wikipedia"
    for lang in langs:
        print(f"downloading {lang}…", flush=True)
        snapshot_download("wikimedia/wikipedia", repo_type="dataset", local_dir=out,
                          allow_patterns=[f"{SNAPSHOT}.{lang}/*.parquet"], max_workers=4)  # fmt: skip
        files = sorted((out / f"{SNAPSHOT}.{lang}").glob("*.parquet"))
        print(f"{lang}: {len(files)} files, {sum(f.stat().st_size for f in files) / 1e9:.1f} GB", flush=True)


if __name__ == "__main__":
    main()
