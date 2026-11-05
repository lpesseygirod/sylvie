"""Parses the model's output stream: thinking, visible text, and native Qwen tool calls.

Qwen3.8's chat template uses this tool-call format:

    <tool_call>
    <function=web_search>
    <parameter=query>
    weather Madrid
    </parameter>
    </function>
    </tool_call>
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

TOOL_OPEN = "<tool_call>"
TOOL_CLOSE = "</tool_call>"
THINK_CLOSE = "</think>"

_FUNC_RE = re.compile(r"<function=([\w\-.]+)>(.*?)</function>", re.S)
_PARAM_RE = re.compile(r"<parameter=([\w\-.]+)>\n?(.*?)\n?</parameter>", re.S)


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)

    def to_message(self) -> dict:
        return {"function": {"name": self.name, "arguments": self.arguments}}


def _coerce(value: str):
    """Parameters arrive as text; turn numbers/booleans/JSON back into values."""
    v = value.strip()
    if v and v[0] in "[{" or v in ("true", "false", "null"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def parse_tool_calls(text: str) -> list[ToolCall]:
    calls = []
    for block in re.findall(r"<tool_call>(.*?)</tool_call>", text, re.S):
        for name, body in _FUNC_RE.findall(block):
            args = {k: _coerce(v) for k, v in _PARAM_RE.findall(body)}
            calls.append(ToolCall(name=name, arguments=args))
        if not _FUNC_RE.search(block):
            # Some models fall back to the older JSON format: {"name": ..., "arguments": {...}}
            try:
                obj = json.loads(block.strip())
                calls.append(ToolCall(name=obj["name"], arguments=obj.get("arguments") or {}))
            except (json.JSONDecodeError, KeyError, TypeError):
                calls.append(ToolCall(name="__invalid__", arguments={"raw": block.strip()}))
    return calls


class StreamSplitter:
    """Feeds raw model text in pieces and emits ('thinking'|'text', chunk) events.

    Text inside <tool_call>…</tool_call> is held back (not shown to Lea); the full raw
    output is kept so tool calls can be parsed at the end.
    """

    def __init__(self, thinking: bool):
        self.in_think = thinking
        self.in_tool = False
        self.buf = ""
        self.raw = ""
        self.thinking_text = ""
        self.visible_text = ""
        self._strip_leading = True  # drop the blank lines the model puts after </think>

    def _safe_prefix_len(self, s: str, marker: str) -> int:
        """Length of s that can't be the beginning of `marker` (so partial tags are held)."""
        for i in range(min(len(marker) - 1, len(s)), 0, -1):
            if marker.startswith(s[-i:]):
                return len(s) - i
        return len(s)

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self.raw += chunk
        self.buf += chunk
        out: list[tuple[str, str]] = []
        while self.buf:
            if self.in_think:
                idx = self.buf.find(THINK_CLOSE)
                if idx >= 0:
                    self._emit(out, "thinking", self.buf[:idx])
                    self.buf = self.buf[idx + len(THINK_CLOSE) :].lstrip("\n")
                    self.in_think = False
                    continue
                n = self._safe_prefix_len(self.buf, THINK_CLOSE)
                self._emit(out, "thinking", self.buf[:n])
                self.buf = self.buf[n:]
                break
            if self.in_tool:
                idx = self.buf.find(TOOL_CLOSE)
                if idx >= 0:
                    self.buf = self.buf[idx + len(TOOL_CLOSE) :]
                    self.in_tool = False
                    continue
                break
            # A stray </think> in fast mode is dropped (the model occasionally emits one).
            stray = self.buf.find(THINK_CLOSE)
            if stray >= 0:
                self.buf = self.buf[:stray] + self.buf[stray + len(THINK_CLOSE) :]
                continue
            idx = self.buf.find(TOOL_OPEN)
            if idx >= 0:
                self._emit(out, "text", self.buf[:idx])
                self.buf = self.buf[idx + len(TOOL_OPEN) :]
                self.in_tool = True
                continue
            n = min(self._safe_prefix_len(self.buf, TOOL_OPEN), self._safe_prefix_len(self.buf, THINK_CLOSE))
            self._emit(out, "text", self.buf[:n])
            self.buf = self.buf[n:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self.buf and not self.in_tool:
            self._emit(out, "thinking" if self.in_think else "text", self.buf)
        self.buf = ""
        return out

    def _emit(self, out: list, kind: str, s: str) -> None:
        if not s:
            return
        if kind == "thinking":
            self.thinking_text += s
        else:
            if self._strip_leading:
                s = s.lstrip("\n")
                if not s:
                    return
                self._strip_leading = False
            self.visible_text += s
        out.append((kind, s))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return parse_tool_calls(self.raw)
