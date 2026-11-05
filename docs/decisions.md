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
| 9 | Voice per language | Kokoro: EN `af_heart`, ES `ef_dora`, FR `ff_siwis` (`config.yaml → voice.kokoro_voices`) | default — Lea can change |
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

### T11 — Speech output: macOS/iOS system voices for now (Kokoro blocked by GPL dependencies) — superseded by T27 on the Mac
- **What:** Sylvie speaks with the system voices (AVSpeechSynthesizer) on the device that plays the audio. Defaults: Samantha (EN), Mónica (ES), Thomas (FR) — decision 9, Lea can change in `config.yaml`. For nicer voices, Lea can download "Premium"/"Enhanced" voices in System Settings → Accessibility → Spoken Content.
- **Why:** Kokoro's Spanish and French pronunciation needs **espeak-ng (GPL-3.0)**, and its English path pulls a GPL phonemizer and **num2words (LGPL)**. The plan forbids adding GPL components without Lea. System voices are free, local, instant, and cover all three languages.
- **Alternatives:** Kokoro with espeak-ng (better voices; needs Lea's OK on GPL — private local use is allowed by the GPL, but it's her call). Listed in LEA-TODO.
- **Side benefit:** speaking on the device (Mac or iPhone) means no audio is sent back over the network; the server sends sentences as text.

### T12 — Whisper gets a short hint prompt
- **What:** `INITIAL_PROMPT = "Sylvie, Lea. Hola Sylvie, can you check esto? Oui, merci Sylvie."`
- **Why:** measured on test audio: fixed the spelling "Silvie" and a garbled Spanish/English mixed sentence ("¿can y o un cheque…" → "can you check el horario…"), and transcription got faster (~125 ms for 3 s of audio).

### T13 — Voice answers: short spoken part, details on screen
- **What:** in voice mode Sylvie is told to answer in Lea's language with 1–3 plain sentences first, details after a blank line. Only the first paragraph (max 4 sentences) is spoken; code/tables are never spoken. "Let me check…" is sent the moment a tool call starts.

### T14 — Warm-up runs a realistic dummy turn
- **Why:** measured: the first real question after start paid 1.5 s of one-time GPU kernel compilation unless the warm-up used the same path (cached system prompt + ~150 new tokens). Now the first question is as fast as later ones.

### T15 — Actions run through AppleScript with values as arguments
- **What:** Spotify/Calendar/Reminders/Mail are controlled with AppleScript; every value is passed via `on run argv`, never pasted into script text (no injection from email subjects or web pages). Sending email and running Shortcuts need a read-back + a clear "yes" that **code** checks (not the model) + an unlocked Mac (checked with `ioreg`, no permission needed). Creating events/reminders can be undone.
- **Alternatives:** EventKit via PyObjC (more deps, needs entitlements), Gmail API (OAuth tokens to manage; decision 5 default is Apple Mail).

### T16 — Phone access through `tailscale serve` + per-device tokens
- **What:** the server keeps listening on 127.0.0.1 only (rule 2); Tailscale forwards encrypted tailnet traffic to it. `/phone/*` endpoints accept only paired-device tokens (only hashes stored, revocable) and refuse browsers; the local token doesn't work there and device tokens don't open the local API. First client: a "Hey Siri, Sylvie" Shortcut (works on Watch/AirPods/CarPlay). The iPhone app comes next.

### T17 — Teacher and training data
- **What:** the teacher is Sylvie's own model in thinking mode + `core/prompts/rubric.md`. Training examples carry source + license and the store refuses anything that isn't `lea`, `sylvie-local:*` or `open-dataset:*`. Lea's own edits weigh 2×, accepted teacher answers 1.5×.
- **No DPO yet:** mlx-lm 0.32 supports LoRA/DoRA/full fine-tuning but not DPO, so training uses the *chosen* answers (SFT, prompt masked). Bad/good pairs are kept so DPO can be added when available.

### T18 — Open data: OpenAssistant oasst2 (Apache 2.0)
- **Why:** human-written (plan: avoid model collapse), multilingual with lots of Spanish and some French, permissive license. Used only filtered to EN/ES/FR, best-ranked replies. Not named in the plan → listed in LICENSES.md; Lea can veto.

### T19 — Overnight loop design
- **What:** one big model in memory at a time (Sylvie's model ↔ Gemma judge, swapped per batch). The judge sees a shuffled criteria order each time (rubric rotation); code applies a length penalty (>150 words) and verifies checkable answers itself. A rewrite is kept only if it gains ≥ 2 points after the penalty, isn't much longer, and fixes any checkable error. Kept pairs are **staged** and become trainable only after Lea's morning spot-check.

### T20 — Adapter versions live in the data folder; the active one in `state.json`
- **Why:** promoting/rolling back shouldn't edit `config.yaml` in the repo. `<data>/adapters/<model>/v001…`, each with `meta.json` (dataset manifest, settings, losses).

### T21 — Dashboard as a separate page (`/dashboard`) of the same web UI
- **Why:** same look, no extra toolkit; also opens in the Mac app's chat window.

### T22 — Prompt cache priorities (bug found by the eval)
- **Problem:** the baseline eval showed time-to-first-word growing from 0.2 s to 3–10 s. Cause: the prompt cache kept 6 snapshots and evicted the oldest — the shared system-prompt snapshot — so after 6 conversations every new chat re-read ~2,000 tokens.
- **Fix:** the warm-up snapshot is stored as a "system" entry (evicted last); the cache is capped at 3 GB. Measured after the fix: 12 fresh conversations in a row all start in ~0.22 s.

### T23 — Training settings for these hybrid-attention models (measured)
- **Finding:** Qwen3.5/3.8 mix normal attention with linear "Gated DeltaNet" layers. LoRA training memory grows steeply with sequence length: on the 9B a single ~1,300-token sample pushed the process past 40 GB (Metal out-of-memory), even with gradient checkpointing.
- **What:** training builds drop examples over ~1,600 characters (not truncated — a truncated answer teaches broken endings), sequences ≤ 512 tokens, LoRA on 8 layers, training in resumable 100-step segments. Result on the 9B: ~200 tokens/s, stable.
- **Cost:** long answers are under-represented in training. Most of Sylvie's style is in short answers anyway; revisit when mlx-lm gets memory-efficient training kernels for these layers.

### T24 — QLoRA on the 27B: fits, barely (measured 2026-11-05)
- **Measured:** 20 steps on `Qwen3.8-27B-4bit`, LoRA on 8 layers, 512-token sequences, gradient checkpointing: works, ~20 tokens/s, **peak memory footprint 34.8 GB** (of 36 GB). A 300-step fine-tune ≈ 45–60 min.
- **Default (Lea can change):** fine-tune the 27B itself with these settings, only at night via the scheduler, in 100-step segments (an out-of-memory error then loses at most one segment).
- **Options for Lea:**
  1. Keep the default (recommended).
  2. More headroom: 4 LoRA layers or 384-token sequences (slightly weaker learning).
  3. Let the GPU use more memory (Lea runs it herself; resets at reboot): `sudo sysctl iogpu.wired_limit_mb=30720`.
  4. Train only on the 9B (much faster, 10 GB) and keep the 27B unadapted — not recommended: the 27B is the one Lea talks to.

### T25 — First 9B fine-tune: the regression check works
- 9B base vs 9B+v004 (oasst2 only): math 8→10/10, but tools 7→4/9, style 4→3/6, multilingual 10→9/10 → flagged as regressions, so it could never be promoted. Cause: no tool-calling data in the mix. Fix: nightly skill practice (T26) adds verified tool-use and reasoning examples.

### T26 — Training questions come from open datasets, never from Claude's templates
- The eval sets use my templates (evaluation only, never trained on — the dataset builder blocks overlaps). For *training*, checkable questions come from **GSM8K (MIT)** and chat from **oasst2 (Apache 2.0)**; all answers come from Sylvie's own model and are verified by code where possible.

### T27 — Kokoro voices on the Mac (Lea approved the GPL parts, 2026-11-06)
- **What:** Sylvie's spoken answers come from Kokoro-82M on MLX (`voice/tts.py`), one sentence at a time. The Mac app asks `POST /api/voice/speak` for each sentence as it arrives (so the next one is ready while the current one plays) and plays it through the echo-cancelling pipeline. If that fails, or `voice.tts: system`, the app speaks with the macOS voice as before. The iPhone Shortcut still uses Siri's voice.
- **Pronunciation:** espeak-ng for all three languages (`voice/g2p.py`, mappings adapted from misaki, Apache 2.0). Not misaki's own English pipeline: its package excludes Python 3.13 and needs spaCy + PyTorch, which is a lot to add for slightly better English.
- **Names:** espeak reads "Lea" as "Lee". Default respellings: EN "Lea" → "Leia" (LAY-a), FR → "Léa", ES "Sylvie" → "Silvi" (`voice.respell`) — guesses, Lea can change them.
- **Measured:** load 0.3 s; first sentence per language compiles (~4 s at startup, done in warm-up). Then 60–500 ms per sentence (4–5 s of audio), so about 10× faster than real time. Running at the same time as the 27B writing an answer: no slowdown of the 27B (200 tokens in 9.6 s vs 10.1 s alone), Kokoro ~0.5 s per sentence. Whisper transcribed the EN/ES/FR test sentences back correctly.
- **Licenses:** Kokoro Apache 2.0, mlx-audio MIT; phonemizer-fork and espeak-ng GPL-3.0 (approved by Lea; private use). See `docs/LICENSES.md`.
