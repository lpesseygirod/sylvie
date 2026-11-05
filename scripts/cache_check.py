"""Checks that the prompt cache makes follow-up turns fast in a long conversation."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm import LLM  # noqa: E402
from core.toolcall import StreamSplitter  # noqa: E402

FILLER = "Madrid became the capital in 1561 when Philip II moved the court there. " * 400

llm = LLM()
llm.load()
msgs = [
    {"role": "system", "content": "You are Sylvie. Notes:\n" + FILLER},
    {"role": "user", "content": "When did Madrid become the capital? One sentence."},
]
for turn in range(3):
    t0 = time.perf_counter()
    sp = StreamSplitter(thinking=False)
    ttft = None
    stats = None
    for c in llm.stream(msgs, max_tokens=60):
        if c.text and ttft is None:
            ttft = time.perf_counter() - t0
        sp.feed(c.text)
        if c.done:
            stats = c.stats
    sp.flush()
    print(
        f"turn {turn + 1}: prompt={stats.prompt_tokens} reused={stats.cached_tokens} "
        f"first_token={ttft:.2f}s answer={sp.visible_text.strip()[:80]!r}"
    )
    msgs.append({"role": "assistant", "content": sp.visible_text.strip()})
    msgs.append({"role": "user", "content": ["And who moved it?", "Say that in Spanish.", "Thanks!"][turn]})
