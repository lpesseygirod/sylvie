# Decisions

Every technical or design decision, with what / why / alternatives. Lea can change any of them — say so and it gets changed.

## Technical decisions (made by Claude)

### T1 — Python 3.13 in a uv environment
- **What:** `uv` installed with Homebrew; project venv on Python 3.13 (managed by uv), versions pinned in `uv.lock`.
- **Why:** the system Python is 3.14, which some ML wheels don't support yet. 3.13 is the newest version MLX supports well.
- **Alternatives:** Homebrew Python 3.14 (risk of missing wheels), conda (heavier, unnecessary).

### T2 — The repo root is the project folder
- **What:** the plan's `sylvie/` layout lives directly in `~/Documents/Projects/Apps/Sylvie`.
- **Why:** simplest; no extra nesting.

### T3 — Main model: `mlx-community/Qwen3.8-27B-4bit`
- **What:** checked on 2026-11-05: exists, Apache 2.0, 16.1 GB on disk. Most downloaded MLX 4-bit build.
- **Alternatives:** `Qwen3.8-27B-MTP-4bit` (multi-token prediction, possibly faster; less used — worth benchmarking later, see ideas.md), OptiQ builds (community re-quantizations, less proven).

### T4 — Embeddings: `mlx-community/Qwen3-Embedding-0.6B-8bit` (proposal)
- **What:** Qwen3-Embedding 0.6B, 8-bit MLX, Apache 2.0, ~0.65 GB, 1024 dims, multilingual (EN/ES/FR).
- **Why:** small enough to stay loaded next to the main model; strong multilingual retrieval for its size.
- **Alternatives:** 4B version (better quality, ~2.5 GB — too much RAM next to the 27B during the day), the 4-bit DWQ 0.6B (smaller, slightly lower quality).

### T5 — Extra packages not named in the plan (all permissive licenses)
`uvicorn` (BSD, runs FastAPI), `pyyaml` (MIT, config), `keyring` (MIT, macOS Keychain access), `pypdf` (BSD, reading PDFs for the document index), `python-multipart` (Apache, file uploads in the chat), `httpx` (BSD, talking to SearXNG). Listed in `LICENSES.md`. Lea can veto any of them.

### T6 — Web page text extraction with the Python standard library
- **What:** a small own HTML-to-text extractor instead of a library like trafilatura.
- **Why:** no new dependency; enough for search answers. Can be swapped later.

### T7 — Server streaming with plain Server-Sent Events from FastAPI
- **Why:** built into FastAPI (`StreamingResponse`), no extra package, works in WKWebView.

### T8 — Chat UI as plain HTML/CSS/JS (no build step)
- **What:** `ui/` is static files served by FastAPI; markdown rendered by a small own renderer.
- **Why:** no Node toolchain, no CDN calls (rule 1: no network for the UI), easy to embed in the Mac app's WKWebView.
- **Alternatives:** React/Svelte (needs npm + build), marked.js (would need vendoring; possible later).

## Lea's decisions (plan section 9) — defaults chosen, Lea can change

| # | Decision | Default chosen | Status |
|---|---|---|---|
| 1 | Name | "Sylvie" | decided by Lea |
| 2 | Persona | Warm, direct, a bit playful; short by default, deeper when asked; answers in the language Lea uses (mixing allowed) | default — Lea can change (`core/prompts/persona.md`, `config.yaml`) |
| 3 | Memory: own vs Orma | **Own implementation** (SQLite + FTS5 + sqlite-vec). Why: ~400 lines, no extra dependency, full control over contradictions/history/forget, good for learning. Orma stays an option. | default — Lea can change |
| 4 | Folders to index | **None** until Lea picks them. | waiting for Lea (LEA-TODO) |
| 5 | Email account / Mail vs Gmail API | Apple Mail via AppleScript, personal account (no tokens to manage, nothing leaves the Mac beyond normal mail) | default — Lea can change |
| 6 | Spotify | Start with app control (AppleScript); Web API later only if Lea wants search/Connect | default — Lea can change |
| 7 | Overnight judge | **Gemma 4 31B-it 4-bit MLX** (different family, Apache 2.0, checked 2026-11-05), loaded only at night | default — Lea can change |
| 8 | iPhone tunnel | Tailscale (easier; coordination server sees metadata only, not content). WireGuard documented as the fully self-run alternative. | default — Lea can change |
| 9 | Voice per language | Kokoro defaults: EN `af_heart`, ES `ef_dora`, FR `ff_siwis` | default — Lea can change |
| 10 | Proactivity | Only when the topic comes up; max 1 aside per conversation | default — Lea can change |
| 11 | Offline mini Sylvie on iPhone | No (for now) | default — Lea can change |
| 12 | Room listener | "Hey Siri, Sylvie" only for now | default — Lea can change |
| 13 | Keep Mac awake lid closed | Lea's choice; instructions in LEA-TODO (pmset or Amphetamine) | waiting for Lea |

### T9 — `profile/` is named `lea_profile/`
- **What:** the plan's `profile/` package (the Lea model) lives in `lea_profile/`.
- **Why:** a top-level package called `profile` shadows Python's standard-library `profile` module (used by `cProfile`), which breaks profiling tools in confusing ways.

### T10 — The system prompt is frozen per conversation
- **What:** each conversation stores the system prompt it started with; new facts learned mid-conversation are already in the history. It's rebuilt only when Lea states a new preference (so it applies immediately).
- **Why:** the prompt cache only works if the beginning of the prompt doesn't change. Rebuilding it after every new fact would make every turn re-read the whole conversation (seconds instead of ~0.3 s).
