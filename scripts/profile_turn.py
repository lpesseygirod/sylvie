"""Profiles one generation step by step: tokenize, cache lookup, prefill, snapshot copy, first token."""

import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mlx.core as mx  # noqa: E402
from mlx_lm import stream_generate  # noqa: E402
from mlx_lm.models.cache import make_prompt_cache  # noqa: E402

from core.llm import LLM  # noqa: E402
from core.persona import build_system_prompt  # noqa: E402

llm = LLM()
llm.load()
sysmsg = build_system_prompt()


def timed(label, fn):
    t = time.perf_counter()
    out = fn()
    print(f"{label:<28}{(time.perf_counter() - t) * 1000:8.1f} ms")
    return out


for attempt in range(2):
    print(f"--- attempt {attempt + 1}")
    msgs = [
        {"role": "system", "content": sysmsg},
        {"role": "user", "content": f"Say hi in five words ({attempt})."},
    ]
    prompt = llm.render(msgs)
    toks = timed("tokenize", lambda: llm.tokenizer.encode(prompt, add_special_tokens=False))
    cache = make_prompt_cache(llm.model)

    def prefill():
        llm.model(mx.array(toks[:-1])[None], cache=cache)
        mx.eval([c.state for c in cache])

    timed(f"prefill {len(toks)} tok", prefill)
    timed("deepcopy snapshot", lambda: mx.eval([c.state for c in copy.deepcopy(cache)]))
    t = time.perf_counter()
    for i, _r in enumerate(
        stream_generate(llm.model, llm.tokenizer, toks[-1:], max_tokens=20, prompt_cache=cache)
    ):
        if i == 0:
            print(f"{'first token':<28}{(time.perf_counter() - t) * 1000:8.1f} ms")
    print(f"{'20 tokens total':<28}{(time.perf_counter() - t) * 1000:8.1f} ms")
