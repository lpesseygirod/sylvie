"""UI development server: fake model, throwaway database, dummy token, port 8766.

Lets the chat UI be tested without loading the real model or touching Lea's data.
Open http://127.0.0.1:8766/auth?t=dev
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess  # noqa: E402

import uvicorn  # noqa: E402

from actions.log import ActionLog  # noqa: E402
from actions.osa import FakeOSA  # noqa: E402
from actions.runner import ActionRunner  # noqa: E402
from core.config import Config, load_config  # noqa: E402
from core.db import Database  # noqa: E402
from core.llm import FakeLLM, GenChunk, GenStats  # noqa: E402
from core.orchestrator import Sylvie  # noqa: E402
from core.server import create_app  # noqa: E402
from knowledge.embeddings import HashEmbedder  # noqa: E402

PORT = int(os.environ.get("SYLVIE_UI_PORT", "8766"))
DEMO = """Sure! Here's a quick overview of **NPV** (net present value):

1. Discount each cash flow: `CF_t / (1 + r)^t`
2. Add them up and subtract the initial investment.

| Year | Cash flow | Discounted (8%) |
|---|---|---|
| 0 | -1000 € | -1000.00 € |
| 1 | 600 € | 555.56 € |
| 2 | 600 € | 514.40 € |

```python
def npv(rate, flows):
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(flows))
```

So the NPV is about **69.96 €** — positive, so it's worth it [1].

> Tip: a higher discount rate lowers the NPV."""


DOC = """Here it is. I put it in the panel so you can edit it and keep it.

<doc title="TVM cheat sheet">
## Time value of money

#### Future value
`FV = PV × (1 + r)^n`

#### Present value
`PV = FV / (1 + r)^n`

#### Annuity (PV)
`PV = C × [1 − (1 + r)^−n] / r`

#### Perpetuity
`PV = C / r`

Tip: match r and n to the same period (monthly rate with months).
</doc>

Formulas first, one example each if you want them."""


class SlowFake(FakeLLM):
    def stream(
        self, messages, tools=None, thinking=False, effort="low", max_tokens=None, stop=None, use_cache=True
    ):
        last = messages[-1]["content"] if messages else ""
        if "JSON" in last or "Answer with JSON" in last:
            text = '{"facts": [], "interests": [], "style": [], "is_preference": false}'
        elif messages[-1]["role"] == "tool" and "NOT DONE YET" in last:
            text = "I'll send this to prof@uni.es: “Can we meet Monday at 10?” Shall I send it?"
        elif messages[-1]["role"] == "tool":
            text = DEMO
        elif messages[-1]["role"] == "user" and "email" in last.lower():
            text = (
                "Sure.\n<tool_call>\n<function=send_email>\n<parameter=to>\nprof@uni.es\n</parameter>\n"
                "<parameter=subject>\nMeeting\n</parameter>\n<parameter=body>\nCan we meet Monday at 10?\n</parameter>\n"
                "</function>\n</tool_call>"
            )
        elif "cheat sheet" in last.lower():
            text = DOC
        elif "percent" in last.lower():  # the 9B wraps one-line results in <doc>: must stay inline
            text = '17% of 2,340 euros is **397.80 euros**.\n\n<doc title="Calculation">\n**17% of 2,340 = 397.80 €**\n</doc>'

        elif "search" in last.lower():
            text = "Let me look.\n<tool_call>\n<function=web_search>\n<parameter=query>\nNPV formula\n</parameter>\n</function>\n</tool_call>"
        else:
            text = DEMO
        if thinking:
            text = "Lea asked something; I'll answer clearly.</think>" + text
        for i in range(0, len(text), 6):
            if stop is not None and stop.is_set():
                break
            time.sleep(0.015)
            yield GenChunk(text=text[i : i + 6])
        yield GenChunk(done=True, stats=GenStats())


def main():
    os.environ["SYLVIE_TEST_TOKEN"] = "dev"
    cfg = Config(dict(load_config()))
    cfg["server"] = {**cfg["server"], "port": PORT, "allowed_origins": [f"http://127.0.0.1:{PORT}"]}
    # The UI check exercises the Send? card, whatever Lea chose in config.yaml (she chose "never").
    cfg["actions"] = {**cfg["actions"], "confirm": "delete_or_send"}
    tmp = Path(tempfile.mkdtemp(prefix="sylvie-ui-dev-"))
    cfg["paths"] = {**cfg["paths"], "data_dir": str(tmp / "data")}  # notes etc. never land in Lea's data
    db = Database(tmp / "dev.db", dim=256)
    # Fake AppleScript and commands: the dev server can never control Lea's real apps.
    runner = ActionRunner(
        cfg,
        ActionLog(db),
        osa=FakeOSA({"outgoing message": "sent"}),
        run_cmd=lambda a: subprocess.CompletedProcess(a, 0, "", ""),
        is_locked=lambda: False,
    )
    s = Sylvie(cfg, SlowFake(), HashEmbedder(256), db, idle_seconds=1, actions=runner)
    s.memory.add("Lea studies Computer Science and BBA in Madrid", key="lea.studies", category="studies")
    s.interests.bump("Formula 1", 3)
    s.style.observe("mixes_languages", "Mixes Spanish and English in the same message", "vale, let's go")
    s.prefs.add("Keep answers short unless I ask for detail.", source_quote="shorter please")
    s.db.execute(  # one of Claude's memory notes, waiting for Lea's decision
        "INSERT INTO memory_suggestions(text, origin, created_at) VALUES (?, 'claude.ai', '2026-11-07')",
        ("Lea is training for a half marathon in March.",),
    )
    app = create_app(sylvie=s, token="dev", warm=False)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
