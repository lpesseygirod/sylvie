export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export UV_NO_TELEMETRY=1
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

UV := uv run --no-sync

.PHONY: setup check lint test bench chat run open download search index app app-test pair phone eval everyday apps finetune ui-test general-pack import-general learn-now mmlu wikipedia small-model extraction-check

setup:
	uv sync

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

test:
	$(UV) pytest

check: lint test

check-all: check app-test

bench:
	$(UV) python scripts/benchmark.py

chat:
	$(UV) python scripts/chat.py

run:
	$(UV) python -m core.server

open:
	$(UV) python scripts/open_ui.py

download:
	HF_HUB_OFFLINE=0 $(UV) hf download mlx-community/Qwen3.8-27B-4bit
	HF_HUB_OFFLINE=0 $(UV) hf download mlx-community/Qwen3-Embedding-0.6B-8bit
	HF_HUB_OFFLINE=0 $(UV) hf download mlx-community/Kokoro-82M-bf16 --include "config.json" "kokoro-v1_0.safetensors" "README.md" "voices/*.safetensors"

SEARX := $(HOME)/Library/Application Support/Sylvie/services

search:
	SEARXNG_SETTINGS_PATH="$(SEARX)/searxng-settings.yml" "$(SEARX)/searxng/.venv/bin/python" -m searx.webapp

index:
	$(UV) python -c "from core.orchestrator import Sylvie; s = Sylvie.build(); print(s.docs.index_folders(s.cfg.get_path('knowledge.folders') or []))"

app:
	zsh scripts/build_mac_app.sh

small-model:   # the helper for Apple's on-device model (efficiency plan A3; needs Apple Intelligence on)
	swift build -c release --package-path app/SmallModel

extraction-check:   # learning facts from conversations: Apple's model vs Sylvie's; saves the verdict
	$(UV) python scripts/extraction_check.py --compare

ui-test:   # the web UI in headless Chrome against the dev server; VOICE=1 adds a real spoken round trip
	VOICE=$(VOICE) $(UV) python scripts/ui_check.py

app-test:
	swift test --package-path app/SylvieMac --scratch-path "$(HOME)/Library/Caches/SylvieBuild"

pair:
	$(UV) python scripts/pair_device.py $(NAME)

phone:
	$(UV) python scripts/setup_phone.py $(NAME)

spotify:   # once: lets Sylvie start songs/playlists by name (free Spotify developer key)
	$(UV) python scripts/setup_spotify.py

eval:
	$(UV) python evals/run.py --label base

everyday:   # ~32 everyday questions (weather, TV, songs, language switching); fake apps, throwaway memory
	$(UV) python evals/run.py --label everyday --sets everyday

apps:   # 28 app requests (Netflix, photos, messages, calls, maps, notes…); fake apps, made-up photos and chats
	$(UV) python evals/run.py --label apps --sets apps

learn-now:   # practice + fine-tune now instead of waiting for the night (stops when you talk to Sylvie)
	$(UV) python scripts/api.py POST /api/schedule/now '{"minutes": $(or $(MINUTES),180)}'

mmlu:   # once: download MMLU (MIT), 15k checkable questions in 57 subjects for practice
	$(UV) python scripts/get_mmlu.py

wikipedia:   # offline Wikipedia en/es/fr from Wikimedia's latest dump (~64 GB streamed, a few hours; resumes)
	$(UV) python -m knowledge.wiki_dump

general-pack:   # the general part of Sylvie (no personal data) → ~/Library/Application Support/Sylvie/general
	$(UV) python -m learning.general_pack

import-general:   # on another Mac: make import-general PACK=/path/to/general
	$(UV) python -m learning.general_pack --import "$(PACK)"

finetune:   # stop Sylvie first; ~2 h: build, train, evaluate, prepare the blind A/B
	$(UV) python -m learning.cycle --force
