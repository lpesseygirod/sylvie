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

### T28 — Answers verified by code are approved automatically (default — Lea can change)
- **What:** the nightly GSM8K practice (T26) keeps an answer only when code confirms it equals GSM8K's known answer. Those examples now go straight to "ready" for training. The judged overnight pairs (Gemma's opinion, not a proof) still wait for Lea's spot-check.
- **Why:** so Sylvie can keep her tool use and reasoning in shape on her own, and a fine-tune can become due without Lea having to approve maths drills. Promotion of any new version still needs her blind A/B win.
- **Change it:** `config.yaml → learning.auto_approve_verified: false` puts them back in the morning spot-check.

### T29 — Claude reviews Sylvie like a coach, but never writes her training data
- **What Lea asked (2026-11-06):** Claude should mentor Sylvie and give feedback on her answers to train her.
- **What Claude does:** it reads Sylvie's real answers and fixes the *causes*: her persona and rules (prompts), the local judge's rubric, and new evaluation questions (used only to measure her, never trained on; the dataset builder blocks them). It also writes no facts about Lea into memory that Lea didn't state herself.
- **What Claude doesn't do:** write example answers, grade answers that go into a dataset, or write "better answers" for her to learn from. The plan's rule 4 forbids it, and Anthropic's terms don't allow Claude's outputs to be used to train other AI models. Training signal comes from Lea (👍/👎, edits, "that's wrong"), from Sylvie's own model with code checks, from the local Gemma judge, and from open datasets.
- **First review (baseline answers, 2026-11-05 run):** strong on facts and in Spanish/French. Weak spots: 10–15-item catalogs for casual "ideas" questions; a closing "Want me to…?" on most answers; filler openers ("Honestly? … here's the honest take"); emojis (🎉 🌱); and one wrong date claim ("the Berlin Wall fell exactly 37 years ago today", asked on 5 November, not 9 November). Fixes: five new lines in `core/prompts/persona.md`, matching points in `core/prompts/rubric.md` (so the overnight judge scores them), six new style test questions, and an emoji check in the style scorer.

### T30 — The "midnight glass" redesign (design/DESIGN.md), and where it differs
- **Built:** new chat workspace (sidebar with orb logo, Today / This week / Older, About Lea, Learning dashboard, "Running on this Mac"; header with version chip and Voice mode; activity notes above answers; source pills; composer with attach, web-search switch, mic, send), a right **work panel** (Preview / Edit, Copy, Export PDF, Save to notes), a new **voice mode** page (`/voice`), the restyled **wake panel** (blurred glass, mini orb, "Send?" card), and an **orb menu bar icon**. One orb everywhere: `ui/orb.css` + `ui/orb.js` on the web, `OrbGlyph` (Core Graphics) in the Mac app. Fonts (Inter, Space Grotesk, OFL) are bundled in `ui/fonts/`, so no Google Fonts requests.
- **How Sylvie "creates something":** she wraps it in `<doc title="…">…</doc>` (new lines in `core/prompts/persona.md`). The chat shows a card and opens the panel, and voice never reads it aloud (`voice/speech.py: DocFilter`). "Save to notes" writes `~/Library/Application Support/Sylvie/notes/saved/<date>-<title>.md` and indexes it, so Sylvie can search it later.
- **Voice mode, two drivers:** in the Mac app the page shows the app's own microphone, wake word, echo cancellation and Kokoro voice (the app sends states, events and mic level). In a browser the page records with getUserMedia, detects the end of speech, and plays Kokoro sentences (system voice as a fallback). After an answer it listens again, until Stop.
- **Differences from the design, on purpose:**
  1. *Live transcript while listening:* Whisper writes down what Lea said only after she stops, so listening shows a caret and "Go ahead, I'm listening." The words appear a moment later. (Streaming speech recognition would be a bigger change; it's in ideas.md.)
  2. *Light mode toggle removed* (spec: dark by default, light only if Lea asks).
  3. *Kept extras:* the Auto / Fast / Think chip in the composer and a copy button under answers (existing features).
  4. *"Remembered: …"* shows when Sylvie saves a preference. She doesn't announce every memory she recalls, because the top matches are included every turn and that would be noise.
  5. *Edit on the "Send?" card* lets Lea say what to change. Nothing is sent meanwhile.
- **Also fixed:** in the Mac app's chat window, confirm/prompt dialogs (delete a chat, edit a fact) were silently answered "no" by WKWebView. They are now real dialogs.


### T31 — First 27B fine-tunes: what went wrong and the safeguards (2026-11-06)
- **v001** (300 steps, 25 min): the loss became NaN at step ~255 and **every LoRA weight became NaN**. The step-200 checkpoint had been overwritten, so nothing was usable. Marked failed; never usable.
- **Cause (found on the third try):** Qwen's chat template adds ~290 tokens of tool instructions whenever a tool is offered, so many calculator examples (prompt ~390 tokens) were cut at 512 tokens *before their answer*. mlx-lm trains only on the last message, so those examples had **zero target tokens → loss 0/0 = NaN**, and one such step poisons every weight. (A first length check said "0 too long" because of a bug in how I counted tokens.)
- **Safeguards now in place:**
  1. The dataset builder tokenizes every example exactly as mlx-lm will (`learning/dataset.py: token_measure`) and drops ones over 512 tokens or with fewer than 4 target tokens. Of 77 verified practice examples, 49 fit.
  2. Training keeps a copy after every good 100-step segment. A segment that produces NaN is rolled back and retried once at half the learning rate; a second NaN keeps the last good adapter, or fails the run if there is none (`learning/train.py`). Failed runs don't delay the next fine-tune.
  3. Peak memory is now read from macOS "peak memory footprint", which counts GPU memory: **38.7 GB** (the old number, 12.5 GB, came from RSS and was wrong). That is above the 36 GB of RAM, so macOS swaps during training. It's slow but works; options are in T24.
- **v002** (the rollback saved it: NaN in segment 3, retried at half rate): same as the current model on maths, multilingual, tools, general and style, but **finance 14/14 → 12/14**: it did compound interest in its head instead of with the calculator (€18,727 instead of €20,780). The gate flags it as a regression, so it can't be promoted.
- **v003:** NaN at step 1 twice (new over-long tool examples in the validation set); failed cleanly, nothing kept. This is what exposed the real cause above.
- **v004** (after the fixes; 509 examples: 460 oasst2 + 49 verified practice, 168 over-long dropped): clean, 33 min, val loss 1.27 → 0.90, peak 38.5 GB. Vs the current model: maths 25/25, finance 14/14, multilingual 10/10, tools 9/9, general 8/8, style 4/12 → 6/12. **No regressions** after re-scoring (one French answer was misread as Spanish by the language check; fixed in `core/lang.py`). Blind A/B (28 pairs) waiting for Lea. The undecided v002 round was withdrawn (exported to `_to_delete/`).

### T32 — Practice trains the decision to use the calculator
- **What:** mlx-lm learns only an example's last message. Our tool examples ended with the write-up after a calculator result, so the *decision to call the calculator* was never trained. That is why v002 stopped using it.
- **Now:** each solved GSM8K problem gives a "decision" example (question → Sylvie's own calculator call, with her reasoning in thinking mode) as well as the answer example. Thinking-mode practice also has the calculator available. 34 earlier reasoning examples made without the calculator offered are set to `retired` (kept, not trained on).

### T33 — Rule: never claim an action that didn't happen
- In one test run the current model answered "C'est noté. Je viens de créer le rappel…" **without calling `create_reminder`**. A previous run did call it. New safety rule 5 in `core/prompts/rules.md`: never say something was done unless its tool call succeeded in this turn.

### T34 — Sylvie learns from Lea's messages to Claude Code
- Lea asked (2026-11-06) for Sylvie to see her interactions with Claude, to learn how she likes answers.
- `memory/claude_history.py` reads `~/.claude/projects/*/*.jsonl` locally every 5 minutes, from where it stopped last time. It keeps only lines Claude Code marks as typed by a human (`origin.kind == "human"` or `turnOrigin == "human"`), and cuts out pasted text, system notes and IDE selections. It drops Claude's answers, tool output, task notifications, subagents, and briefs or relays from other Claude sessions, since those aren't Lea's words. On 2026-11-06 that came to 254 messages.
- Each message goes through the normal background learning (facts, interests, style), told it was written to a coding assistant. Nothing is copied into conversations or training data, so rule 4 holds: no Claude-written text reaches Sylvie, only Lea's own words.
- A rule about answers ("shorter please") is saved **switched off** (`scope = "claude"`). About Lea → Preferences labels it "Suggested from your Claude chats", and Lea turns it on if she wants it. Coding instructions like "don't ask, just do it" must not become rules for Sylvie on their own.
- Background learning now waits while a night job has the models unloaded, and the scheduler interrupts the running background job before unloading. A long backlog could otherwise reload the 27B in the middle of training.
- Off switch: `config.yaml → memory.claude_history.enabled`, plus `exclude_projects` to skip folders.
- Chats on claude.ai (web, phone, the Claude app's chat tab) live on Anthropic's servers. Lea exports them (Settings → Privacy → Export data) and drops `conversations-000.zip` (newer exports come as a manifest of several zips; only this one is used) in `Sylvie/Claude exports/` inside the project folder (git-ignored). Sylvie reads every JSON file in it in memory, keeps only `"sender": "human"` turns (never Claude's replies or the contents of attached files), and skips message ids she has already seen, because each new export contains the whole history again.

### T34 — Instant replies and learned shortcuts (Lea's request, 2026-11-06)
- **Instant replies** (`core/quick.py`): a message that is *only* a greeting, thanks, "how are you", good night or goodbye (EN/ES/FR, optionally with "Sylvie") is answered without the model: 0.01–0.04 s on the real server, against ~0.3–1 s with the model. Replies are short, use Lea's name, and are editable in `config.yaml → quick_replies`. "Hi, what's the weather?" still goes to the model. The replies are conversation strings like the wake greeting, not training data.
- **Learned shortcuts** (`core/shortcuts.py`): after Lea asks about the same thing 3 times (embedding similarity ≥ 0.90; measured: the same request in other words or languages 0.90–0.99, "weather in Paris" vs "…in Madrid" 0.77), Sylvie starts that lookup the moment the question arrives instead of after a model round. That saves the time it takes the model to decide on and write the tool call (~1 s), and "Let me check…" is spoken at once. Only read-only tools (web search, calendar, documents, mail), never actions; arguments with dates are never reused; the answer is always written from fresh results, with a note telling Sylvie to look again if the results don't fit. List: `GET /api/shortcuts`; switch one off: `POST /api/shortcuts/<id> {"status": "off"}`; all off: `shortcuts.enabled: false`.
- **Not done (ideas):** replaying whole answers for questions whose answer never changes (small gain, risk of stale answers), and proactive routines ("tell me the weather every morning at 8"), which need notifications and Lea's approval of each routine.
- **"Let me think…"** already existed: spoken when the router picks thinking mode, "Let me check…" when a tool starts, "One moment, waking up…" when a night job had the model unloaded.

### T35 — Lea chose speed: Sylvie runs on the 9B (2026-11-06)
- **Why:** with the 27B (~18 GB, plus Whisper, Kokoro and embeddings ≈ 22 GB) and Lea's usual apps, the Mac swapped: 9–10 tokens/s, first word 1.5–4.3 s, full answers 15–35 s. Lea: "there's no way I'm waiting 1 minute".
- **Measured on the 9B (same day):** 62–74 tokens/s, first word 0.2–0.6 s in chat, voice first sentence 0.8 s (transcription 0.3 s); the calculator and web search still work (compound interest correct via the calculator; Madrid weather from 5 sources in 5.3 s).
- **Trade-offs:** less capable on hard questions; it follows formatting rules less well (it wrapped one-line results in a document, so the chat now shows documents of a line or two inline and only opens the panel for real ones). The 27B fine-tune v004 doesn't apply to the 9B; night training now targets the 9B (`learning.finetune_model` follows `llm.model`). Going back is one line in `config.yaml` (the 27B is still downloaded).
- **Also:** Sylvie's server now starts SearXNG itself (it only ran in a Terminal tab, so search was off after the Mac restarted). Lea's English voice is Isabella.

### T36 — Deleting a conversation keeps what Sylvie learned (Lea's request, 2026-11-07)
- Lea: "I want to be able to delete conversations but Sylvie not to have lost the knowledge learned from it."
- Delete already only hid the conversation (`deleted_at`), so facts, preferences, style and interests were kept. Two things did skip deleted chats, and now use them: **episodic memory** (search, automatic recall and the backfill), so Sylvie still remembers what was said in them; and **night practice and fine-tune questions** (`learning/practice.py`, `learning/overnight.py`).
- Still hidden: the conversation list, conversation search and the traces page. The UI's delete prompt now says "Sylvie keeps what she learned from it."
- To make Sylvie forget something on purpose: "Sylvie, forget that", or the Memory page. Not built: a "delete and forget completely" option for a whole conversation.

### T37 — General learning kept apart from Lea's personal data (Lea's request, 2026-11-07)
- Lea: the general things Sylvie learns must be stored apart from her personal ones, so a base Sylvie can be given to other people and learn from them.
- **Already true:** personal facts live in memory, not in the weights (rule 12), and training left out examples tagged personal.
- **The gap, measured on real data:** 3 of 91 "general" examples still carried her (answers greeting "Hola Lea", written with her profile in the prompt).
- **Privacy check** (`learning/privacy.py`), built from Sylvie's memory:
  - Her name and surnames, any phone number or email, domains from her facts.
  - Distinctive names from her facts (people, projects): capitalised words that aren't common in the open dataset.
  - Case-sensitive, so the Spanish verb "lea" is not her name.
  - On her data it flags those 3 and nothing generic (Madrid, Step, sums).
  - Every fine-tune build runs it; the manifest records `privacy_checked`.
- **Identity batches** are now tagged personal: they greet her and use her preferences.
- **The general part** is `~/Library/Application Support/Sylvie/general/`, refreshed after every night or with `make general-pack`:
  - General examples that pass the check.
  - Adapters trained only on such data (or only on open datasets).
  - A manifest with checksums.
  - Every written line is checked again; any hit means nothing is written.
- **On another Mac:** `make import-general PACK=…` adds the examples (that Sylvie keeps learning from them plus her new owner) and copies the adapters as candidate versions.
- **Not done yet:** the code and prompts still say "Lea" in many places. Giving Sylvie to someone else also needs their name, city and voice in the config and the prompts.

### T38 — "Train now", more to learn, judged auto-approval (Lea's request, 2026-11-07)
- **Train now:**
  - `make learn-now` (or `POST /api/schedule/now`) runs the night job immediately: practice → grade → rewrite → fine-tune → evals → blind A/B.
  - It unloads the live models while it runs, only runs plugged in, and stops the moment Lea talks to Sylvie.
- **More to learn:**
  - MMLU (MIT; ~15k multiple-choice questions in 57 subjects: law, medicine, economics, history…) joins GSM8K maths and OpenAssistant questions. The letter is checked by code.
  - Considered and left out: Aya (gated), OpenBookQA and TriviaQA (license "unknown"), ARC (CC BY-SA).
- **Auto-approval** (`learning.auto_approve_judged: true`, Lea chose autonomy): night pairs on general questions that code verified, or that the local judge re-graded ≥ 8, train without her spot-check. Pairs from her own questions still wait.
- **Promotion is unchanged:** a new version replaces the current one only with no eval regressions and after Lea's blind A/B.

### T39 — Permanent training (Lea's request, 2026-11-07)
- Lea: "keep training until I start talking or using Sylvie, then when I finish train again".
- **`schedule.always: true`:** the practice + fine-tune job runs at any hour once Sylvie has been unused for 5 minutes. There is no nightly cap, and each run lasts up to 4 h before the next one starts.
- **Still never on battery.** The Mac is kept awake only while a job runs (`caffeinate -w <pid>`).
- **Training stops as soon as she hears "Sylvie"** (the greeting request), so the models reload while the greeting plays.
- **Fine-tunes may come every 3 days** instead of 10. Promotion still needs no regressions and Lea's blind A/B.
- **Trade-off:** while training, the Mac's memory is mostly taken (judge model ~18 GB), so other heavy apps can feel slower. The first answer after training stops takes a few seconds longer while she reloads. "Pause learning" in the menu bar turns it off.

### T40 — A bigger, held-out general-knowledge test (2026-11-07)
- Lea asked whether the tests change. **Practice questions change every run:** random draws from ~30k questions (GSM8K, MMLU, OpenAssistant). **The tests stay fixed on purpose,** so versions are compared fairly. The eval guard keeps them out of practice and training.
- The fixed sets were small (~80 items), so a new `knowledge` set was added: 150 MMLU test questions, a few from each of the 57 subjects, with a fixed seed and scored by the letter (`evals/make_knowledge.py`).
- **Also:**
  - A server restart left its training job running unseen, so a second job doubled memory use. The scheduler now writes a pid file and stops a leftover job when it starts.
  - Some MMLU lines contain Unicode line separators, so they are now read with `split("\n")`, not `splitlines()`.

### T41 — Offline Wikipedia, plus the internet for the rest (Lea's request, 2026-11-07)
- Lea: "can we add her knowledge?", then: rather than loading the shareable base, Sylvie should pull what she needs from online and build the answer locally, so nothing leaks.
- **Knowledge goes in a library, not in the weights:** a small model fine-tuned on facts mixes them up.
- **Local:** Wikipedia in English, Spanish and French (`wikimedia/wikipedia` 2023-11-01, CC BY-SA, 18 GB download), in `<data dir>/wikipedia.db`.
  - One SQLite FTS5 table per language. The title is weighted, and an article whose title is what was asked comes first.
  - Only the first ~8 passages of each article are kept, which is where most answers are and keeps the size down.
  - Measured on 141k Spanish articles: indexed in 52 s; searches take 1–75 ms and found Simón Bolívar, Fotosíntesis, Frida Kahlo, Teorema de Pitágoras and Guerra civil española.
- **Online for the rest:**
  - Every result carries the article's URL, which Sylvie can `open_url` for the full text.
  - `web_search` covers anything recent.
  - Only generic search words leave the Mac, with her name stripped; never the conversation or her memory.
- **Privacy:** the tool is called `wikipedia`, offered first, and marked untrusted. The persona now says "Wikipedia first for facts about the world, web for anything recent". Night rewrites also research offline first.
- **Not in the shareable `general/` pack:** another Sylvie installs the same library with `make wikipedia`.
- **Update, same night:** on the full Hugging Face library (10.8M articles, 45 GB, indexed in 30 min), half of 20 big English articles were missing: Paris, DNA, Moon, Gravity, Roman Empire, Marie Curie, Photosynthesis… So Sylvie now builds from **Wikimedia's own CirrusSearch dumps** (`knowledge/wiki_dump.py`, `make wikipedia`; snapshot 2026-11-01).
  - They're complete and recent, and they carry each article's other names (redirects) and how many pages link to it (popularity).
  - ~64 GB is streamed and unpacked on the fly; nothing but the library stays.
  - Ranking: the whole question as a title or other name first; then all key words, dropping the most common ones (no slow "any word" search); then re-ranked by popularity. Disambiguation pages are skipped.
  - The new library is built next to the old one and swapped in; the old one goes to `_to_delete/`.
- **Built, 2026-11-07 (16:00–18:12):** 103 shards, 14.7M articles (en 9,991,780, fr 2,719,392, es 2,026,320), 66 GB. Paris, DNA, Moon, Marie Curie are all there now.
  - Two search fixes after testing on it: (1) when the whole question isn't a title, its most telling part that is one comes first ("how does photosynthesis work" → Photosynthesis, "capital of Australia" → Australia, whose start names Canberra); (2) words found in 100k+ articles no longer trigger a full word search once a title matched ("Paris" took 5.6 s, now 14 ms; DNA 2.1 s → 3 ms).
  - The tool states the library's real snapshot date (read from the library).
  - The old library (45 GB) and the 2023 parquet download (18 GB) are in `_to_delete/`.

### T42 — Academic writing: Lea's Zotero, research papers, real citations, writing practice (Lea's request, 2026-11-07)
- Lea: add scientific sources "like Zotero and JSTOR" so Sylvie writes better essays; and rather than downloading papers, let her learn *how* to write from them.
- **Lea's Zotero library** (`knowledge/zotero.py`, tool `zotero_library`): read from a copy of `~/Zotero/zotero.sqlite` (Zotero's own files are never changed). Citation details, abstracts, her notes and highlights, and PDF full text (Zotero's `.zotero-ft-cache`, page by page; pypdf when missing). Indexed in `<data>/zotero.db`, refreshed when Zotero changes (only changed sources re-read). Her 9 sources indexed in 3 s. Personal: never training data, never in the general pack.
- **JSTOR: no.** No public API, and its terms forbid automated downloading. Articles she saves from JSTOR into Zotero are covered.
- **Papers online** (`tools/scholar.py`): `find_papers` searches OpenAlex (250M+ works, CC0 metadata; abstracts, citation counts, free PDFs); `check_reference` checks a reference against Crossref and says NOT FOUND for invented ones (measured: a made-up title scored 0.04, real ones 1.0). Only the search words leave the Mac, with her name stripped; no account and no email sent (OpenAlex's anonymous budget, ~100 searches a day, is enough; a key in the Keychain `openalex-api-key` is used if she adds one).
- **Citations** (`knowledge/cite.py`): APA 7 (default, `writing.citation_style`), MLA 9, Chicago author-date, Harvard. Every tool result carries the in-text citation and the reference, so a bibliography is copied from what was found, never written from memory.
- **Writing mode** (`core/intent.py`): essays, literature reviews, theses, bibliographies (en/es/fr) get a note (Zotero first, then papers; cite exactly; no invented sources) and room for ~2,000 words (`llm.max_tokens_writing` 3072, within the 16k context).
- **Writing practice at night** (`learning/writing.py`, `writing.practice_per_night` 6): open-access papers from Europe PMC, **CC BY or CC0 only** (NC/ND refused), on varied subjects. Exercise: write the abstract from the introduction and conclusion, or the introduction from the abstract. The local judge grades against the published text; Sylvie rewrites from the feedback (never seeing the published text); clear gains become pairs (chosen = her rewrite). Rule 4 holds: targets are her own writing. The paper isn't kept, except the excerpt in the exercise, whose license and source are on the example. Tagged general (shareable).
- **Safeguards, measured live:** in one try without them, the 9B called no tool and wrote two invented references that looked real. Now (1) an essay request searches her Zotero before the first word; (2) after the answer, every entry of its references list must match a work a tool found this turn, or it's listed under "**Check before using:** … may be wrong or invented"; works cited in the text but left out of the list are added; (3) the style named in her message (APA/MLA/Chicago/Harvard) is used by every tool. With them, a Harvard essay came out with three real references from her Zotero, written once in the document panel.
- Why training for style but lookups for facts: fine-tuning teaches tone and structure, but a model that "learns" papers remembers them loosely, which is exactly how invented references happen.

### T40 — Claude's own memory notes, as suggestions (Lea's answer, 2026-11-07: "Yes, as suggestions")
- Sources, read locally: the `memories-*.zip` of a claude.ai export (dropped in `Claude exports/`) and
  Claude Code's per-project notes about Lea herself (`~/.claude/projects/*/memory/*.md`, `type: user`
  only: her feedback/project notes are coding instructions, e.g. "never write chart drawing here"). Checked with the Claude history scan, every 5 minutes.
- Claude wrote them, so none is used until Lea decides in About Lea → Facts: **Keep** (becomes a fact,
  source `claude-memory`), **Use as a rule** (an active preference, scope `claude-memory`) or
  **Dismiss** (never offered again, even from a newer export).
- Rule 4: Claude-written text never reaches training. Kept notes only live in the system prompt, and
  the dataset builder now refuses any example that carries a system prompt (`excluded.system_prompt`).

### T43 — Practice rewrites always end in an answer (found 2026-11-07, after Lea asked to keep training)
- Measured: in thinking mode the 9B often spent the whole token budget thinking (2,500 tokens, ~3 min
  each) and never wrote the rewrite, so it came back empty. Today 237 general questions and 17 writing
  exercises kept 4 pairs in all; only the code-checked math (~60 per run) was really training her.
- Now `LLM.complete` treats `max_tokens` as the thinking budget: if the thinking is still open when it
  runs out, it is closed and she writes the answer from what she has (up to 1,000 more tokens). Her
  own words only, so rule 4 holds.
- Each run now records why rewrites were dropped (`dropped`: empty / no_gain / too_long / still_wrong)
  in the night summary, so a run that keeps nothing explains itself.

### T44 — Learning Lea's tastes from her apps (Lea's idea, 2026-11-08)
- Through each app's own "download your data" (Spotify, Instagram, TikTok, Google Takeout), never by
  signing in: Sylvie can't enter Lea's passwords, and TikTok/Instagram forbid automated access. The
  downloads are read on the Mac (`lea_profile/apps.py`), from ~/Downloads or `App exports/`, and left untouched.
- Spotify: most-played (≥30 s) and liked artists → interests `music: <artist>` + one fact.
  Instagram: its own topic list for her. TikTok searches/hashtags and YouTube channels/searches/titles
  and Drive file names → topics picked by the local model, in the background when she's away.
  Google Docs from Takeout are copied into Sylvie's data folder and added to document search.
- Every interest starts as a "guess" (About Lea → Interests: confirm / not me / delete); sensitive
  topics are filtered as everywhere else. Facts from apps are memory only, never training data (rule 4).
- First run: the chat window opens About Lea → Your apps until she ticks her apps or says "None of these".

### T45 — Her language, her name, and looking things up (Lea, 2026-11-08)
- Measured in the 13:34–13:40 voice conversation: every nameless turn ("la la la", "No, no, no…",
  "Hey Nini") came from follow-up listening, not from a false wake word. Follow-up listening is now off
  (`defaults write app.sylvie.mac followUpListening -bool true` brings it back): she only wakes on "Sylvie"
  or the hotkey. This reverses Lea's 2026-11-06 request ("I need to repeat her name every sentence");
  her latest wish wins.
- The reply language for voice comes from Whisper's detection when the words alone don't tell
  (`core/lang.reply_language`); clear words still win. "(Reply in X.)" is the last line she reads.
- Weather / TV / news / prices / hours questions get a "call web_search first" note; an answer saying
  she has no access (EN/ES/FR) sends her back to search once; recalled episodes with such refusals are
  dropped so she doesn't copy them.
- `make everyday`: 32 everyday questions checked for the tool she must call, the answer language, and
  no "I don't have access". Fake apps and a throwaway database; checks only, no written answers, never
  training data.
- First `make everyday` run: 27/32. Fixed: French with shared words ("Mets un minuteur de 5 minutes"
  answered in Spanish) — the guesser now also counts words only one language has; "I don't have access"
  after looking only in offline Wikipedia now also sends her to web_search; opening-hours questions count
  as live; an empty answer after tools gets one more plain "answer now" (was "me he perdido…").
- "Stop" vs "stop listening" (Lea, 2026-11-08): "stop" cuts her off and listens for the next order
  without the name (0.8 s later, so "stop listening" can still win); "stop listening" / "deja de
  escuchar" / "arrête d'écouter" stops talking and turns the mic off until ⌥Space or the menu. Caught by
  the on-device recognizer while she talks, and by the server (voice/loop.stop_command, no model) when it
  arrives in a recording.

### T46 — Netflix, WhatsApp (Lea, 2026-11-08: "connect to my Netflix, photos, WhatsApp… learn and help me")
- Netflix: the viewing-activity CSV (netflix.com/viewingactivity → Download all) or the full "Get my
  info" ViewingActivity.csv, picked up from Downloads like the other app downloads. Most-watched shows
  → interests `watching: <show>` + one fact; titles → topics by the local model.
- WhatsApp: read from WhatsApp Desktop's own database on the Mac (a snapshot copy in Sylvie's data
  folder; WhatsApp's file is never touched). A `whatsapp` tool finds messages by person/words/days or
  lists recent chats; it can't send anything. Messages are untrusted, and a turn that read them can't
  use any tool that leaves the Mac (web search, open_url, research, Claude, paper search), so nothing
  private ends up in a query. Memory gets only "who she writes to most" (saved contacts, not numbers);
  message contents are never stored, never training data. The copy on this Mac stops at 21 Nov 2025
  (WhatsApp Desktop isn't installed any more): reinstalling and linking it makes it current.
- Instagram/TikTok stay download-only: no way to act in them for her (no API; automated use forbidden).
- Photos: Apple Photos already names people, labels things and knows places. The Mac app (which has
  Full Disk Access) copies the library's two index databases (Photos.sqlite, search/psi.sqlite) into
  Sylvie's data folder at most every 6 h (Photos.sqlite is 867 MB here); `lea_profile/photos.py` searches
  the copy (person, place, thing, dates, favourites) and the `find_photos` tool shows the matches in the
  chat. The pictures themselves come from PhotoKit through `sylvie-photo://thumb/<uuid>` in the app's web
  views (one "Allow Photos" prompt); none is copied or sent anywhere. Things are labelled in English by
  Photos ("beach" 170 photos vs "playa" 28), so the tool asks for English things and local place names.
  Memory gets only who appears most. Lea's library: 17,019 photos (1980–2026), 6,519 with a place,
  4 people named — naming more people in Photos makes "photos with X" work for them.
- "Estoy buscando los datos…" (Lea, 2026-11-08): three turns in a row she said she was searching and
  never called a tool. Now: an answer that promises a search (EN/ES/FR) without one sends her back to
  call web_search; "búscame", "look it up", "datos/estadísticas/data" count as look-up requests; a "sí"
  to her own "¿quieres que lo busque?" does too. `make everyday` has these cases: 35/35.

### T47 — More things she can do in apps (Lea, 2026-11-08: "put Bridgerton on Netflix… send a message… and see that she does it")
- New actions: `watch` (Netflix, YouTube, Prime Video, Disney+, Max, Movistar Plus+, Apple TV: starts the
  exact title when SearXNG finds its Netflix/YouTube page, else opens the service's search),
  `send_message` (Messages/iMessage, to a contact found in Contacts by how Lea calls them — "mamá" finds
  "Mama"; WhatsApp opens the chat with the text written for her to send, once WhatsApp is installed),
  `call` (FaceTime, which shows the call first), `directions` (Apple Maps), `create_note` (Notes, undoable).
  send_message follows Lea's confirm setting like send_email (she chose "never": it sends at once).
  Several contacts with the same name → she asks which one.
- `make apps`: 28 requests across all app actions, checked for the tool and a key argument, with fake
  apps, a made-up photo library and made-up WhatsApp chats. First run 25/28 ("Llama a mamá" answered in
  English; she copied the example song "La Reina, by Maluma" from her own instructions; "Abre Spotify"
  played music) → fixed → 28/28; everyday 35/35.
- Lea, 2026-11-08: "Yes, ask me first": send_message and send_email are always read back for her yes,
  whatever actions.confirm says. The contact is looked up before the read-back (two "Inés" → she asks
  which one first). An answer claiming "sent" after a failed or still-pending send is corrected.
- English questions in a Spanish conversation still got Spanish answers (live, "Sylvie, can you put
  Bridgerton on Netflix please?", with "(Reply in English.)" in the prompt). Now the first ~60 characters
  are held back and checked (core/lang.LanguageGuard): a start in the wrong language is dropped and redone
  once with "Answer ONLY in English…" before anything is shown or spoken. With Whisper's language known,
  only a clear majority of words overrules it ("make me a plan" had been taken for Spanish).

### T48 — Calendar, Blackboard, documents, reminders, contacts (Lea, 2026-11-08: "think of all options")
- Calendar: `calendar_events` takes a range (days, up to 62) and a title search ("entrega", "exam").
  Blackboard needs no login: its calendar link (.ics) subscribed in the Mac's Calendar app puts every
  deadline where Sylvie already reads (steps in About Lea → Your apps). Announcements come by email.
- Documents: `knowledge.folders` = Documents, Desktop, OneDrive, iCloud Drive (Lea asked for access).
  Word/PowerPoint/Excel/OpenDocument/RTF are now read (stdlib XML, no new packages). Code projects (.git),
  app libraries and cloud-only files (SF_DATALESS: reading would download them) are skipped. Indexed in
  the background every 6 h when Lea is away. New `read_document` opens one by name (untrusted content).
  "Resume mi ensayo" is reading her own document, not academic writing (it had sent her to Zotero).
- `list_reminders`, `contact_info`. Sylvie's data folder is now 700 (only Lea's user).
- Evals never read Lea's Zotero, documents, photos or chats (Zotero had been read). apps 33/34
  (directions for "how do I get to…" sometimes answered in words), everyday 35/35.
- Passwords (Lea asked to give Sylvie some "to just access"): not done. Each connection uses macOS
  permissions, Apple's own accounts (Mail, Calendar) or Lea's own downloads instead; a model that reads
  web pages and emails must not hold passwords (a planted instruction could leak them), and Instagram/
  TikTok forbid automated logins.

### T49 — Efficiency plan A1: measure memory before changing anything (2026-11-08)
- **What:** a memory recorder inside the server (`core/memwatch.py`, one JSON line a minute, every 10 s under pressure, kept 14 days) and a day summary (`scripts/memreport.py`). Findings: `docs/reports/a1-memory-2026-11-08.md`.
- **Why:** Lea's Mac froze three times today (answers took 61–214 s instead of ~1.5 s). The plan says measure first. Main causes found: MLX's uncapped GPU cache (26.7 GB; capped in 038d988), document indexing holding the embedding model for whole e-books, ~20 GB of other apps (Docker VM 7.8 GB), test runs loading a second 9B, a night job running into the morning, and a WhatsApp permission prompt that blocks server startup.
- **How:** kernel counters through ctypes (`proc_pid_rusage`, `sysctlbyname`), no extra process except `ioreg` for keyboard idle time. ~50 ms per sample. Nothing leaves the Mac.
- **Alternatives:** Activity Monitor or `powermetrics` (needs sudo); a separate launchd recorder (would not know what Sylvie was doing).
- **Change it:** `config.yaml → memwatch.enabled: false`.

### T50 — Efficiency plan A2: memory guard and voice models on demand (2026-11-09)
- **What:** `core/memguard.py`. Backlog jobs (documents, old Claude chats, app downloads) start only with normal macOS pressure, ≥6 GB really free and Lea away from the keyboard 5 min; a running one is paused (re-queued) when that changes. Night jobs start only if 20 GB would be free once Sylvie unloads, and are stopped above 24 GB (job + training child) or after 3 min of pressure (then 30 min pause). Evals refuse to start without 11 GB free (`--force` to override; measured peak 9.8 GB). Whisper and Kokoro load when voice is used (Whisper while the greeting plays) and unload after 10 quiet minutes (2 while memory is tight). Indexing embeds 8 pieces at a time (the model lock is per batch, "stop" works inside a file), files that failed are skipped until they change. The WhatsApp tool is listed from Sylvie's own copy of the chats, so building the tool list never opens WhatsApp's folder (that waited on macOS's permission prompt at every restart). The night job and evals cap MLX's GPU cache like the server.
- **Why:** A1 (T49) found the freezes came from heavy work next to Lea's ~20–25 GB of apps, a lookup waiting 52 s behind indexing, and voice models held while she types.
- **"Free" memory:** macOS's own free % said 38% while 18 GB of RAM held compressed pages and ~4.7 GB was really free; the guard uses free + cached-file pages (`vm_stat`), like Activity Monitor.
- **Not done, measured:** KV-cache quantization. The 9B (Qwen3.5) has only 8 full-attention layers of 32: 32 KB per token, 0.5 GB for a full 16k chat. Quantizing would save ≤0.25 GB at some accuracy risk. Context stays 16k (`llm.context_length`).
- **Trade-off:** after 10 quiet minutes the first spoken greeting takes ~1.7 s instead of 0.05 s (Kokoro reload); Whisper's 3.5 s load is hidden behind the greeting and Lea's question. Raise `voice.unload_after_minutes` if that feels slow; `voice.load_on_demand: false` restores loading at startup.
- **Change it:** `config.yaml → memguard`, `voice.load_on_demand / unload_after_minutes`, `schedule.job_memory_gb / max_job_gb`.

### T51 — Efficiency plan A5 + A3: sleep-time learning, Apple's on-device model for extraction (2026-11-09)
- **What (A5):** facts, interests and style are learned once a conversation has been quiet 5 min (`learning.learn_after_quiet_minutes`): one extraction per conversation (parts of ≤3,000 characters) from the database (cursor `meta.learning.upto`), not one call 8 s after every message. Prompt re-warm once per quiet spell. Chat summaries also then (from 6.6k tokens); the 11k mid-conversation summary stays as a safety net (a chat that outgrows the 16k context must be cut). Episode embeddings stay per turn (no model call).
- **Why:** Nov 8: 80 messages → 80 extraction calls and up to 57 re-warms of a ~7k-token prompt; Lea's follow-ups come a median of 30 s apart, so the per-message job ran (or was interrupted and redone) inside conversations. Grouped by 5-minute quiet: 23 calls.
- **Rule 13 kept:** a "remember…" message is extracted on its own; sensitive facts from a batch are dropped by the existing parser (tested).
- **What (A3):** `app/SmallModel` (Swift, Foundation Models) + `core/small_model.py`. Extraction uses Apple's model only if Apple Intelligence is on and `make extraction-check` found it within 1 item of the 9B with no forbidden fact (`small-model.json`); otherwise, or after 3 failures, the 9B. Summaries stay on the 9B (4K context). Today Apple Intelligence is off (LEA-TODO 24).
- **Alternatives:** a separate small MLX model (more memory, the opposite of the goal); extraction at night only (facts from the morning would be missing all day).
- **Measured (05:49):** extraction eval with the 9B, single 12/16 (37 calls, 27.5 s) → batch 15/16 (17 calls, 17.9 s), 0 forbidden facts in both. Batch catches facts spread over two messages, and missed ext-9. The check needs 6.1 GB at peak, so its memory gate went from 11 to 8 GB.
- **Also:** the A2 guard flapped (105 pauses in an hour); start needs 2 GB more than keep-going, 10 min cooldown after a pause.
- **Change it:** `learning.learn_after_quiet_minutes`, `learning.small_model: auto|apple|sylvie`, `memguard.start_margin_gb / pause_cooldown_minutes`.

### T52 — Shareable Sylvie and the welcome journey: Claude helps answer, never trains (2026-11-09)
- **What Lea asked:** a plan for Sylvie as a project anyone can install. At first start, a journey where Sylvie learns what the owner wants her for, their interests, and gets access to their apps (Netflix and more) and the internet. Optionally connect a Claude account "so she can train with an external". Otherwise she trains by herself, with no judges. By default, when she doesn't know something or lacks access, she looks it up, or asks for what she needs, including an email and password.
- **Plan:** `docs/shareable-plan.md`, S1–S7. It comes after Part A of the efficiency plan, since the journey's access step builds on A6 (live macOS data).
- **Changed from the request:** Claude can be connected as a helper for answers ("ask Claude…", `tools/claude.py`), not as a trainer. Anthropic's terms don't allow Claude's outputs to train other AI models, and build-plan rule 4 says the same. Sylvie trains alone, as Lea said: owner feedback, code checks plus her own thinking mode as grader (A7), and open datasets.
- **Passwords:** saved in the Keychain only. Each is used only on its own site (against prompt-injection tricks). She asks before every login and before anything that sends, buys, deletes or posts. She never pays on her own. Each service's own sign-in page is preferred where one exists.
- **Lea decides:** when to merge the new-owner branch (`claude/youthful-cray-5fa0cc`), the license (MIT or Apache 2.0), and publishing (she pushes herself).

### T53 — "Sylvie, stop" works while she talks (2026-11-10)
- **Problem (Lea):** saying "Sylvie, stop" while she talked didn't stop her. The log at 17:31 showed nothing heard during a long answer. Echo cancellation is off on Lea's MacBook (T-entry of 2026-11-06), so the speech recognizer mostly heard Sylvie's own voice, and Lea's words were lost under it.
- **What:** `BargeInDetector` (SylvieCore). The app measures how loud it plays (a tap on the mixer) and compares it with the mic.
  - Mic level beyond what her own echo explains (the loud end, the 90th percentile of this answer's echo, +6 dB), or a voice during her pauses, is someone talking over her.
  - Her voice then drops to 15% at once, and comes back 1.5 s after the other voice stops.
  - Meanwhile her name counts as Lea's: "Sylvie" interrupts her, like "stop" already did.
  - ⌥Space while she talks or thinks also stops her.
- **Measured (built-in speakers and mic, the Mac's own voice playing Lea's part):**
  - First version (median echo): 16 false triggers during one answer. Her echo varies (mic minus played: −19 / −8 / −4 dB at the 10th / 50th / 90th percentile).
  - With the 90th percentile: 0 false triggers in a 52 s answer.
  - "Sylvie, stop" said mid-answer: heard and stopped her (2 tries out of 2).
- **Not yet measured:** Lea's own voice. Each answer now logs, as numbers only, how often someone talked over her and her echo spread (Console, subsystem app.sylvie.mac), to tune if needed.

### T54 — To-dos go into Reminders, and she just says "Got it" (2026-11-10)
- **Problem (Lea, by voice, 16:26–16:31):** eight to-do requests in a row ("remind me to call my dad", "also add that I need to…"). Each got "Got it, I'll add a reminder…", often with a `<doc>` block and "anything else?", and **no tool call**: nothing was saved. "Remind me of the things I need to do" was answered from the open chat only. Lea asked four times for just "Got it". The claim check missed it, because "I'll add…" is a promise, not "I've added". The preference detector also narrowed her rule to "say got it when asked for a reminder to call dad".
- **What:** code decides (`core/intent.py`: `todo_request`).
  - **Adding:** one model round whose text is never shown. It may only produce `create_reminder` calls (one per thing; it is sent back once if it only talks). Then a fixed reply: "Got it." / "Apuntado." / "C'est noté.". If a call fails (e.g. no Automation permission yet), she answers normally and says it failed.
  - **Reading:** "what do I need to do?" → `list_reminders` runs first, then she reads them out in plain sentences.
  - **Dates:** "tomorrow / mañana / demain", "today", "day after tomorrow" set the day in code (`fix_due_day`); the model keeps only the time. One run saved "tomorrow at 9" on the 10th as the 21st.
  - **Not to-dos:** calendar events, music, timers, questions ("remind me what the capital of…") and instructions about answering ("when I ask you…, just say got it").
  - Voice answers: no `<doc>` blocks unless she asks for a document. "I'll add / I'll remind you / te lo apunto" now count as claims (the check sends her back to the tool).
  - Her preference fixed: the narrowed one (#14) replaced by "When Lea asks you to remind her of something or to note something down, answer only 'Got it'" (#15).
- **Why Reminders:** it's her Mac's and iPhone's to-do list, so items sync to her phone and can be ticked off there, and `list_reminders` already existed. Notes and Sylvie's memory would only be readable through Sylvie.
- **Measured (new eval set `todo`, 15 items: Lea's own 8 phrasings + Spanish, French, a timed one, 2 list requests, 2 non-to-dos):** 3/15 → **15/15**. Saved to Reminders: 4/11 → **11/11**. Short answer: 0/11 → **11/11**. List requests read Reminders: 2/2 → 2/2 (one before had a `<doc>` block). Time to her reply for a to-do: ~0.5–1.5 s. Neither version saved a reminder for a question.
- **Not measured live:** the first real reminder makes macOS ask "Sylvie wants to control Reminders" (LEA-TODO 25).

### T55 — "Listen and actually do what I say": audit of Lea's requests (2026-11-11)
- **Lea:** "I would like overall that Sylvie listens and actually does what I say."
- **Audit (230 messages from Lea, 6–10 Nov):** 23 requests to *do* something got no tool call, and 6 times her next message was a correction. Most were already fixed on 8 Nov (music claimed but not played, "turn it up", Netflix) or on 10 Nov (to-dos, T54). Still open:
  - **"Just stop talking, I want to listen the song"** → she paused the music and explained. Now a short "stop talking / cállate / deja de hablar / tais-toi" gets "Okay." / "Vale." / "D'accord." from code: no model, no tools.
  - **Whisper's made-up tails:** "Sylvie, stop Spotify, por favor. ¡Suscríbete al canal!" The noise filter of 8 Nov drops the whole thing, losing her real "stop Spotify". Now only the made-up sentence is dropped (`strip_noise`).
- **New eval set `requests`:** 22 of her real voice requests, word for word (music, volume, previous song, Netflix, calendar, weather, "stop talking"). Not run yet: with Lea working there was 4.8–6.6 GB free (8 GB needed) and 12.7 GB of swap in use, so it waits for room rather than slowing her Mac. Results go in this entry.
