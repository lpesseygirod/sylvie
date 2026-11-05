"""Runs AppleScript safely: values are passed as arguments (`on run argv`), never pasted into
the script text, so a subject line or a web page can't inject AppleScript commands."""

from __future__ import annotations

import subprocess


class OSAError(RuntimeError):
    pass


class OSARunner:
    def run(self, script: str, *args: str, timeout: float = 20) -> str:
        try:
            p = subprocess.run(
                ["osascript", "-", *[str(a) for a in args]],
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise OSAError("the app didn't answer in time") from e
        if p.returncode != 0:
            msg = p.stderr.strip()
            if "-1743" in msg or "Not authorized" in msg:
                raise OSAError("macOS hasn't allowed Sylvie to control this app yet (Automation permission)")
            if "-600" in msg or "isn't running" in msg:
                raise OSAError("the app isn't running")
            raise OSAError(msg[:200] or "AppleScript failed")
        return p.stdout.strip()


class FakeOSA:
    """For tests: records calls and returns canned output."""

    def __init__(self, outputs: dict[str, str] | None = None):
        self.calls: list[tuple[str, tuple]] = []
        self.outputs = outputs or {}

    def run(self, script: str, *args: str, timeout: float = 20) -> str:
        self.calls.append((script, args))
        for key, out in self.outputs.items():
            if key in script:
                return out
        return ""
