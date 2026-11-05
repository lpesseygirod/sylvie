"""Model wrapper: loads the base model (+ optional adapter) once and streams answers.

All generation runs on one dedicated thread: MLX models aren't safe to run from
several threads at once, and it gives a natural queue (live turns and background
jobs take turns).
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from core.config import Config, load_config


@dataclass
class GenStats:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    prefill_s: float = 0.0
    generation_tokens: int = 0
    prompt_tps: float = 0.0
    generation_tps: float = 0.0
    peak_memory_gb: float = 0.0
    time_to_first_token: float = 0.0
    finish_reason: str | None = None


@dataclass
class GenChunk:
    text: str = ""
    done: bool = False
    stats: GenStats | None = None
    extra: dict = field(default_factory=dict)


_DONE = object()


class LLM:
    def __init__(self, cfg: Config | None = None, model_id: str | None = None, adapter: str | None = None):
        self.cfg = cfg or load_config()
        self.model_id = model_id or self.cfg.get_path("llm.model")
        self.adapter = adapter if adapter is not None else self.cfg.get_path("llm.adapter")
        self.model = None
        self.tokenizer = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sylvie-llm")
        self._load_lock = threading.Lock()
        self._prompt_cache = None

    # ---------- loading ----------
    def _adapter_path(self) -> str | None:
        if not self.adapter:
            return None
        p = Path(self.cfg.get_path("paths.adapters_dir")).expanduser() / self.adapter
        return str(p) if p.exists() else None

    def _load_sync(self) -> None:
        with self._load_lock:
            if self.model is not None:
                return
            from mlx_lm import load

            self.model, self.tokenizer = load(self.model_id, adapter_path=self._adapter_path())

    def load(self) -> None:
        self._executor.submit(self._load_sync).result()

    @property
    def loaded(self) -> bool:
        return self.model is not None

    # ---------- prompts ----------
    def render(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        thinking: bool = False,
        effort: str = "low",
    ) -> str:
        # preserve_thinking=True renders past answers with their (empty) <think></think> block,
        # exactly as fast mode generated them, so the cached prefix still matches next turn.
        # We never store reasoning text in history, so this costs ~4 tokens per past answer.
        kwargs = {"enable_thinking": thinking, "preserve_thinking": True}
        if thinking:
            kwargs["reasoning_effort"] = effort
        return self.tokenizer.apply_chat_template(
            messages, tools=tools or None, add_generation_prompt=True, tokenize=False, **kwargs
        )

    def count_tokens(self, text: str) -> int:
        if self.tokenizer is None:
            return max(1, len(text) // 4)
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    # ---------- generation ----------
    def _cached_prefill(self, tokens: list[int], use_cache: bool = True):
        """Reuse the longest cached prefix of `tokens`, process the rest except the last token,
        and snapshot the cache. Returns (cache, remaining_tokens, reused_count).

        Why: reading the prompt runs at ~700 tok/s on the M5 Max, so re-reading a long system
        prompt + history every turn would cost seconds. With the snapshot, the next turn only
        reads what's new (the last answer + the new message).
        """
        import copy

        import mlx.core as mx
        from mlx_lm.models.cache import LRUPromptCache, make_prompt_cache

        if self._prompt_cache is None:
            self._prompt_cache = LRUPromptCache(max_size=self.cfg.get_path("llm.prompt_cache_entries", 6))
        cache, rest = (
            self._prompt_cache.fetch_nearest_cache(self.model_id, tokens) if use_cache else (None, [])
        )
        if cache is None or not rest:
            cache, rest = make_prompt_cache(self.model), tokens
        reused = len(tokens) - len(rest)
        body = rest[:-1]
        step = 2048
        for i in range(0, len(body), step):
            self.model(mx.array(body[i : i + step])[None], cache=cache)
            mx.eval([c.state for c in cache])
        if body and use_cache:
            self._prompt_cache.insert_cache(self.model_id, tokens[:-1], copy.deepcopy(cache))
        return cache, rest[-1:], reused

    def _generate_into(
        self,
        q: queue.Queue,
        prompt: str,
        max_tokens: int,
        thinking: bool,
        stop: threading.Event | None,
        use_cache: bool = True,
    ):
        try:
            if stop is not None and stop.is_set():  # cancelled while waiting in the queue
                q.put(GenChunk(done=True, stats=GenStats(finish_reason="stopped")))
                return
            self._load_sync()
            import mlx.core as mx
            from mlx_lm import stream_generate
            from mlx_lm.sample_utils import make_sampler

            if thinking:
                temp, top_p = (
                    self.cfg.get_path("llm.thinking_temperature", 0.6),
                    self.cfg.get_path("llm.thinking_top_p", 0.95),
                )
            else:
                temp, top_p = self.cfg.get_path("llm.temperature", 0.7), self.cfg.get_path("llm.top_p", 0.8)
            sampler = make_sampler(temp=temp, top_p=top_p, top_k=20)
            stats = GenStats()
            t0 = time.perf_counter()
            tokens = self.tokenizer.encode(prompt, add_special_tokens=False)
            cache, rest, reused = self._cached_prefill(tokens, use_cache=use_cache)
            stats.prefill_s = time.perf_counter() - t0
            last = None
            for resp in stream_generate(
                self.model, self.tokenizer, rest, max_tokens=max_tokens, sampler=sampler, prompt_cache=cache
            ):
                if not stats.time_to_first_token:
                    stats.time_to_first_token = time.perf_counter() - t0
                last = resp
                q.put(GenChunk(text=resp.text))
                if stop is not None and stop.is_set():
                    stats.finish_reason = "stopped"
                    break
            if last is not None:
                stats.prompt_tokens = len(tokens)
                stats.cached_tokens = reused
                stats.generation_tokens = last.generation_tokens
                stats.prompt_tps = last.prompt_tps
                stats.generation_tps = last.generation_tps
                stats.peak_memory_gb = last.peak_memory
                stats.finish_reason = stats.finish_reason or last.finish_reason
            mx.clear_cache()
            q.put(GenChunk(done=True, stats=stats))
        except Exception as e:  # surface errors to the consumer instead of hanging it
            q.put(e)
        finally:
            q.put(_DONE)

    def stream_prompt(
        self,
        prompt: str,
        max_tokens: int = 1024,
        thinking: bool = False,
        stop: threading.Event | None = None,
        use_cache: bool = True,
    ) -> Iterator[GenChunk]:
        q: queue.Queue = queue.Queue()
        self._executor.submit(self._generate_into, q, prompt, max_tokens, thinking, stop, use_cache)
        while True:
            item = q.get()
            if item is _DONE:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        thinking: bool = False,
        effort: str = "low",
        max_tokens: int | None = None,
        stop: threading.Event | None = None,
        use_cache: bool = True,
    ) -> Iterator[GenChunk]:
        if self.tokenizer is None:
            self.load()
        prompt = self.render(messages, tools=tools, thinking=thinking, effort=effort)
        if max_tokens is None:
            key = "llm.max_tokens_thinking" if thinking else "llm.max_tokens_fast"
            max_tokens = self.cfg.get_path(key, 1024)
        yield from self.stream_prompt(
            prompt, max_tokens=max_tokens, thinking=thinking, stop=stop, use_cache=use_cache
        )

    def warm(self, system_prompt: str, tools: list[dict] | None = None) -> None:
        """Pre-reads the system prompt (+ tool definitions) into the cache, so the first turn
        of a new conversation doesn't pay for it. Runs on the generation thread."""

        def _job():
            self._load_sync()
            full = self.render(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": "x"}], tools=tools
            )
            prefix = full[: full.rindex("<|im_start|>user")]
            tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
            # _cached_prefill snapshots everything but the last token, so add a throwaway one.
            self._cached_prefill(tokens + [tokens[-1]])
            # A tiny throwaway turn on top of the cached prefix compiles the GPU kernels for exactly
            # the path real turns take (cached prefix + new tokens). Without it the first real
            # question pays ~1.5 s extra.
            filler = "<context>\nTime: 09:00, Monday\n</context>\n\n" + "Hi Sylvie, how are you today? " * 18
            sample = self.render(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": filler}], tools=tools
            )
            q: queue.Queue = queue.Queue()
            self._generate_into(q, sample, 2, False, None, use_cache=True)

        self._executor.submit(_job).result()

    def complete(
        self,
        messages: list[dict],
        thinking: bool = False,
        max_tokens: int = 512,
        use_cache: bool = False,
        **kw,
    ) -> str:
        """Whole answer as a string, thinking removed. Used by background jobs, which skip the
        prompt cache so they don't push out the snapshots of Lea's conversations."""
        from core.toolcall import StreamSplitter

        sp = StreamSplitter(thinking=thinking)
        for chunk in self.stream(
            messages, thinking=thinking, max_tokens=max_tokens, use_cache=use_cache, **kw
        ):
            sp.feed(chunk.text)
        sp.flush()
        return sp.visible_text.strip()


class FakeLLM:
    """Stand-in for tests: returns scripted replies (or a function of the messages)."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls: list[dict] = []
        self.loaded = True
        self.tokenizer = None

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def load(self) -> None:
        pass

    def _next(self, messages):
        if callable(self.replies):
            return self.replies(messages)
        if self.replies:
            r = self.replies.pop(0)
            return r(messages) if callable(r) else r
        return "OK"

    def warm(self, system_prompt, tools=None):
        pass

    def stream(
        self, messages, tools=None, thinking=False, effort="low", max_tokens=None, stop=None, use_cache=True
    ):
        self.calls.append({"messages": messages, "tools": tools, "thinking": thinking})
        text = self._next(messages)
        if thinking and "</think>" not in text:
            text = "hmm</think>" + text
        for i in range(0, len(text), 7):
            yield GenChunk(text=text[i : i + 7])
        yield GenChunk(done=True, stats=GenStats(generation_tokens=len(text) // 4))

    def complete(self, messages, thinking=False, max_tokens=512, **kw) -> str:
        from core.toolcall import StreamSplitter

        sp = StreamSplitter(thinking=thinking)
        for c in self.stream(messages, thinking=thinking):
            sp.feed(c.text)
        sp.flush()
        return sp.visible_text.strip()
