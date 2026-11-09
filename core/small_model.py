"""Which model does Sylvie's small background jobs (efficiency plan A3).

Apple's on-device model (Foundation Models, macOS 26) is free, private and its memory is managed by
macOS, so it costs Sylvie nothing to keep. It is used for fact extraction once two things are true:
- Apple Intelligence is turned on (System Settings → Apple Intelligence & Siri: Lea's choice);
- the extraction check (`make extraction-check`) found it about as good as Sylvie's own model on
  `evals/sets/extraction.jsonl`. The verdict is saved in `<data_dir>/small-model.json`.
Until then, and whenever a call fails, Sylvie's own model does the job, as before.

The model is reached through a small Swift helper (`app/SmallModel`, `make small-model`): one process
per call, JSON in and out. Its context is 4,096 tokens, so long jobs (conversation summaries) stay
on Sylvie's model.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("sylvie.small_model")
ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "app" / "SmallModel" / ".build" / "release" / "sylvie-small"
VERDICT_FILE = "small-model.json"


class AppleModel:
    """llm.complete()-compatible wrapper around the helper."""

    def __init__(self, helper: Path = HELPER, timeout: float = 120.0):
        self.helper = Path(helper)
        self.timeout = timeout
        self._status: tuple[float, bool, str] | None = None

    def status(self) -> tuple[bool, str]:
        """(available, reason), checked at most every 10 minutes."""
        if self._status and time.time() - self._status[0] < 600:
            return self._status[1], self._status[2]
        if not self.helper.exists():
            ok, why = False, "helper not built (make small-model)"
        else:
            try:
                out = subprocess.run(
                    [str(self.helper), "--status"], capture_output=True, text=True, timeout=20
                )
                d = json.loads(out.stdout or "{}")
                ok, why = bool(d.get("available")), str(d.get("reason", ""))
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                ok, why = False, f"helper failed: {e}"
        self._status = (time.time(), ok, why)
        return ok, why

    def complete(self, messages, max_tokens: int = 600, stop: threading.Event | None = None, **_kw) -> str:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        prompt = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
        req = json.dumps(
            {"instructions": system, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0.2}
        )
        p = subprocess.Popen([str(self.helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)  # fmt: skip
        out: list[str] = []
        reader = threading.Thread(target=lambda: out.append(p.communicate(req)[0]), daemon=True)
        reader.start()
        end = time.time() + self.timeout
        while reader.is_alive():
            if (stop is not None and stop.is_set()) or time.time() > end:
                p.kill()
                reader.join(2)
                return ""  # stopped for Lea (or stuck): the caller sees stop and retries later
            reader.join(0.1)
        d = json.loads(out[0] or "{}") if out else {}
        if "text" not in d:
            raise RuntimeError(f"Apple's model failed: {d.get('error')} {d.get('reason', '')}".strip())
        return d["text"]

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text) // 3


def verdict(cfg) -> dict:
    try:
        return json.loads((Path(cfg.data_dir) / VERDICT_FILE).read_text())
    except (OSError, ValueError):
        return {}


class SmallJobs:
    """Picks the model for fact extraction: `learning.small_model` = auto (Apple's once it passed the
    check), apple (whenever available), or sylvie (always Sylvie's own)."""

    def __init__(self, cfg, llm, apple: AppleModel | None = None):
        self.cfg, self.llm = cfg, llm
        self.apple = apple or AppleModel()
        self.failures = 0

    def choice(self) -> str:
        mode = self.cfg.get_path("learning.small_model", "auto")
        if mode == "sylvie" or self.failures >= 3:
            return "sylvie"
        if mode == "auto" and not verdict(self.cfg).get("passed"):
            return "sylvie"
        return "apple" if self.apple.status()[0] else "sylvie"

    def complete(self, messages, stop=None, **kw) -> str:
        if self.choice() == "apple":
            try:
                text = self.apple.complete(messages, stop=stop, **kw)
                self.failures = 0
                return text
            except (OSError, RuntimeError, ValueError) as e:
                self.failures += 1
                log.warning("Apple's model failed (%s); using Sylvie's model", e)
        return self.llm.complete(messages, stop=stop, **kw)
