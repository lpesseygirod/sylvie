# Licenses

Every model, dataset and dependency with its license. Checked against Hugging Face / PyPI metadata.

## Models

| Model | Use | License | Checked |
|---|---|---|---|
| mlx-community/Qwen3.8-27B-4bit (base: Qwen/Qwen3.8-27B) | main model | Apache 2.0 | 2026-11-05 |
| mlx-community/Qwen3-Embedding-0.6B-8bit | embeddings | Apache 2.0 | 2026-11-05 |
| mlx-community/Qwen3.5-9B-4bit | learning sandbox (Phase 4) | Apache 2.0 (Qwen) — re-check before download | planned |
| mlx-community/gemma-4-31b-it-4bit (base: google/gemma-4-31B-it) | overnight judge (Phase 5) | Apache 2.0 | 2026-11-05 |
| mlx-community/Kokoro-82M-bf16 | voice (Sylvie's spoken answers, T27) | Apache 2.0 | 2026-11-06 (in use) |
| mlx-community/whisper-large-v3-turbo | speech to text (Phase 2) | MIT (OpenAI Whisper); HF card has no license field — confirm before Phase 2 | 2026-11-05 |

## Datasets

| Dataset | Use | License | Checked |
|---|---|---|---|
| OpenAssistant/oasst2 (`2023-11-05_oasst2_ready.trees.jsonl.gz`) | human-written chat data mixed into fine-tunes; overnight questions | Apache 2.0 | 2026-11-05 |
| GSM8K (github.com/openai/grade-school-math, `train.jsonl`) | checkable overnight questions; tool-use and reasoning practice (answers by Sylvie's model, verified by code) | MIT | 2026-11-05 |
| MMLU (cais/mmlu on Hugging Face, `mmlu.jsonl`) | checkable overnight questions in 57 subjects (multiple choice; Sylvie's answers checked by code) | MIT | 2026-11-07 |
| Wikipedia en/es/fr (Wikimedia CirrusSearch dumps, snapshot 20261101; earlier `wikimedia/wikipedia` 20231101) | offline library Sylvie looks things up in and cites (`knowledge/wiki.py`); **not** training data | CC BY-SA 4.0 / GFDL (share-alike: credit each article; fine for lookup) | 2026-11-07 |
| Europe PMC open-access articles (CC BY / CC0 only, fetched a few per night) | writing practice (`learning/writing.py`): excerpts in exercise prompts, published text as the judge's yardstick; targets are Sylvie's own rewrites | CC BY 4.0 / CC0 (source PMCID recorded on each example) | 2026-11-07 |
| OpenAlex (api.openalex.org) | `find_papers` lookups (titles, authors, abstracts); **not** training data | CC0 | 2026-11-07 |
| Crossref (api.crossref.org) | `check_reference` lookups; **not** training data | metadata open (facts, no license needed) | 2026-11-07 |
| Lea's Zotero library (`~/Zotero`, read-only copy) | `zotero_library` lookups; personal, **never** training data or the general pack | Lea's own | 2026-11-07 |

Not named in the plan (the plan asks for "open datasets whose license allows it") — Lea can veto.

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
| mlx-audio | MIT | yes (Kokoro on MLX) |
| pyarrow | Apache 2.0 | no — reading the Wikipedia parquet files |
| phonemizer-fork | **GPL-3.0 — approved by Lea 2026-11-06** | no — text → phonemes for Kokoro |
| espeakng-loader (bundles the espeak-ng library and data) | loader MIT-style (no PyPI license field); **espeak-ng GPL-3.0 — approved by Lea 2026-11-06** | no — pronunciation for EN/ES/FR |
| `voice/g2p.py` phoneme mappings, adapted from misaki (hexgrad/misaki) | Apache 2.0 (credited in the file) | yes (Kokoro's own G2P) |

New transitive packages from mlx-audio/phonemizer (attrs, babel, cloudpickle, csvw, dlinfo, isodate, joblib, jsonschema, language-tags, miniaudio, pyparsing, python-dateutil, rdflib, referencing, rfc3986, rpds-py, segments, sounddevice, termcolor, tzdata, uritemplate): all MIT, BSD or Apache 2.0 (checked 2026-11-06).

## Fonts (bundled in ui/fonts/)

| Font | Use | License | Checked |
|---|---|---|---|
| Inter 5.3.0 (@fontsource/inter, npm) | interface text | SIL Open Font License 1.1 (`ui/fonts/Inter-OFL.txt`) | 2026-11-06 |
| Space Grotesk 5.3.0 (@fontsource/space-grotesk, npm) | headings, voice transcript | SIL Open Font License 1.1 (`ui/fonts/SpaceGrotesk-OFL.txt`) | 2026-11-06 |

## Services (separate programs, not linked into Sylvie)

| Service | Use | License | Note |
|---|---|---|---|
| SearXNG (github.com/searxng/searxng) | local web search | **AGPL-3.0 — flagged** | Named in the plan. Runs as its own process; Sylvie only calls its HTTP API on 127.0.0.1. AGPL obligations apply when you offer a *modified* SearXNG to others over a network — not to private local use. Lea installs it herself (`scripts/install_searxng.sh`). |

GPL in Sylvie: only phonemizer-fork and espeak-ng, approved by Lea on 2026-11-06 for private use on her Mac (T27). If Sylvie is ever shared or distributed, these need another look. Nothing non-commercial or unclear.
