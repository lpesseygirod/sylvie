"""Times every stage of a turn (wake → STT → recall → LLM → TTS) so slow parts are visible."""

from __future__ import annotations

import time
from contextlib import contextmanager


class Trace:
    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.stages: dict[str, float] = {}
        self.marks: dict[str, float] = {}
        self.llm_rounds: list[dict] = []  # token stats per model call (prompt, cached, generated)

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - t0)

    def mark(self, name: str) -> None:
        """Record a moment relative to the start (e.g. 'first_token') — only the first one counts."""
        self.marks.setdefault(name, time.perf_counter() - self.start)

    def total(self) -> float:
        return time.perf_counter() - self.start

    def to_dict(self) -> dict:
        return {
            "stages_ms": {k: round(v * 1000, 1) for k, v in self.stages.items()},
            "marks_ms": {k: round(v * 1000, 1) for k, v in self.marks.items()},
            "total_ms": round(self.total() * 1000, 1),
            "llm": self.llm_rounds,
        }
