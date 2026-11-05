export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export UV_NO_TELEMETRY=1
export TOKENIZERS_PARALLELISM=false

UV := uv run --no-sync

.PHONY: setup check lint test bench chat run download

setup:
	uv sync

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

test:
	$(UV) pytest

check: lint test

bench:
	$(UV) python scripts/benchmark.py

chat:
	$(UV) python scripts/chat.py

run:
	$(UV) python -m core.server

download:
	HF_HUB_OFFLINE=0 $(UV) hf download mlx-community/Qwen3.8-27B-4bit
	HF_HUB_OFFLINE=0 $(UV) hf download mlx-community/Qwen3-Embedding-0.6B-8bit
