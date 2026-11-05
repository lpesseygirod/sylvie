# Licenses

Every model, dataset and dependency with its license. Checked against Hugging Face / PyPI metadata.

## Models

| Model | Use | License | Checked |
|---|---|---|---|
| mlx-community/Qwen3.8-27B-4bit (base: Qwen/Qwen3.8-27B) | main model | Apache 2.0 | 2026-11-05 |
| mlx-community/Qwen3-Embedding-0.6B-8bit | embeddings | Apache 2.0 | 2026-11-05 |
| mlx-community/Qwen3.5-9B-4bit | learning sandbox (Phase 4) | Apache 2.0 (Qwen) — re-check before download | planned |
| mlx-community/gemma-4-31b-it-4bit (base: google/gemma-4-31B-it) | overnight judge (Phase 5) | Apache 2.0 | 2026-11-05 |
| mlx-community/Kokoro-82M-bf16 | voice (Phase 2) | Apache 2.0 | 2026-11-05 |
| mlx-community/whisper-large-v3-turbo | speech to text (Phase 2) | MIT (OpenAI Whisper); HF card has no license field — confirm before Phase 2 | 2026-11-05 |

## Datasets

None yet.

## Python packages (direct dependencies)

| Package | License | In plan? |
|---|---|---|
| mlx, mlx-lm | MIT | yes |
| huggingface_hub | Apache 2.0 | yes (model downloads) |
| fastapi | MIT | yes |
| sqlite-vec | MIT / Apache 2.0 | yes |
| uvicorn | BSD-3 | no — needed to run FastAPI |
| pyyaml | MIT | no — config file |
| keyring | MIT | no — Keychain access (rule 5) |
| pypdf | BSD-3 | no — reading PDFs |
| python-multipart | Apache 2.0 | no — file uploads |
| httpx | BSD-3 | no — HTTP client |
| pytest, ruff (dev) | MIT | no — tests and lint |

## Services (separate programs, not linked into Sylvie)

| Service | Use | License | Note |
|---|---|---|---|
| SearXNG (github.com/searxng/searxng) | local web search | **AGPL-3.0 — flagged** | Named in the plan. Runs as its own process; Sylvie only calls its HTTP API on 127.0.0.1. AGPL obligations apply when you offer a *modified* SearXNG to others over a network — not to private local use. Lea installs it herself (`scripts/install_searxng.sh`). |

Nothing else GPL, non-commercial or unclear so far.
