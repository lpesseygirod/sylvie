# Sylvie

Lea's private AI assistant. Runs entirely on her Mac (Apple M5 Max): Qwen3.8-27B via MLX, local memory,
local web search, voice, a menu bar app, actions, and a learning loop. Nothing personal leaves the Mac.

Plan: `sylvie-build-plan.md` · Status: `docs/PROGRESS.md` · Your to-dos: `docs/LEA-TODO.md` ·
Decisions: `docs/decisions.md` · Licenses: `docs/LICENSES.md` · Reports: `docs/reports/latest.md`

## Everyday commands

| Command | What it does |
|---|---|
| `make run` | Start Sylvie's server (127.0.0.1:8765) — loads the model (~10 s) |
| `make open` | Open the chat in your browser (unlocked with the Keychain token) |
| `make app` | Build the menu bar app → `~/Library/Caches/SylvieBuild/Sylvie.app` |
| `make chat` | Simple terminal chat |
| `make check` | Lint + tests (must pass before every commit) |
| `make check-all` | Also the Swift app's tests |
| `make eval` | Run the evaluation sets |
| `make bench` | Speed/memory benchmark |
| `make search` | Run SearXNG (after `scripts/install_searxng.sh`) |
| `make index` | Index the folders in `config.yaml → knowledge.folders` |
| `make pair NAME="Lea's iPhone"` | Pair a phone (see `docs/iphone.md`) |

Dashboard: http://127.0.0.1:8765/dashboard · Blind comparison: http://127.0.0.1:8765/ab

## Layout

```
core/         server (FastAPI), orchestrator (one turn), router, LLM wrapper + prompt cache, prompts/
memory/       facts: store, hybrid recall (FTS5 + sqlite-vec), extraction, "forget that"
lea_profile/  the Lea model: style traits, interests, saved preferences, interest noticing
knowledge/    embeddings (Qwen3-Embedding), document index with citations
tools/        web search (SearXNG), safe page fetching, calculator, tool registry
actions/      Spotify, Calendar, Reminders, timers, apps, Shortcuts, Mail — confirm + log + undo
voice/        Whisper speech-to-text, sentence streaming for speech
learning/     teacher, training store, dataset builder, LoRA training, versions, blind A/B,
              scheduler, overnight loop, identity data, interest research, conversation practice
ui/           chat UI, dashboard, A/B page (plain HTML/JS, no external requests)
app/SylvieMac SwiftUI menu bar app (wake word, echo-cancelled audio, floating panel, ⌥Space)
evals/        fixed evaluation sets — never trained on
tests/        unit tests (fake model; never touch real apps or data)
```

All data lives in `~/Library/Application Support/Sylvie` (not in Documents, so it can't sync to iCloud).
Models stay in `~/.cache/huggingface`.
