"""Decides fast mode vs thinking mode for a message.

Fast mode for most things (first word in well under a second). Thinking mode when Lea asks
for it or the question clearly needs reasoning (math, proofs, debugging, careful comparisons).
On battery, thinking mode is used only when Lea asks for it explicitly.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass

EXPLICIT_RE = re.compile(
    r"\b(think (hard|carefully|deeply|it through|step by step)|step[- ]by[- ]step|reason (it )?through|"
    r"piensa( bien| con calma)?|pi[eé]nsalo|razona|paso a paso|analiza a fondo|"
    r"r[ée]fl[ée]chis|pas [àa] pas|[ée]tape par [ée]tape|analyse en d[ée]tail)\b",
    re.I,
)
HARD_RE = re.compile(
    r"\b(prove|proof|derive|derivation|demuestra|demostraci[oó]n|d[ée]montre|integral|derivative|derivada|"
    r"d[ée]riv[ée]e|solve|resuelve|r[ée]sous|optimi[sz]e|complexity|big[- ]o|debug|why does (this|my) code|"
    r"npv|irr|wacc|black[- ]scholes|regression|probability|probabilidad|probabilit[ée])\b",
    re.I,
)
COMPARE_RE = re.compile(
    r"\b(compare|pros and cons|trade-?offs?|compara|ventajas y desventajas|avantages et inconv)\b", re.I
)


@dataclass
class Route:
    thinking: bool
    effort: str  # low | medium | xhigh
    reason: str


_battery_cache: tuple[float, bool] = (0.0, False)


def on_battery() -> bool:
    global _battery_cache
    t, val = _battery_cache
    if time.time() - t < 60:
        return val
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=2).stdout
        val = "Battery Power" in out
    except (OSError, subprocess.SubprocessError):
        val = False
    _battery_cache = (time.time(), val)
    return val


def route(text: str, battery: bool | None = None) -> Route:
    battery = on_battery() if battery is None else battery
    if EXPLICIT_RE.search(text):
        return Route(True, "medium", "Lea asked to think")
    if battery:
        return Route(False, "low", "on battery")
    has_code = "```" in text or re.search(r"\b(def|class|function|return|import)\b.*[:{(]", text)
    if HARD_RE.search(text) or (
        has_code and re.search(r"\b(why|bug|error|fix|wrong|por qu[eé]|pourquoi)\b", text, re.I)
    ):
        return Route(True, "low", "reasoning-heavy question")
    if COMPARE_RE.search(text) and len(text) > 120:
        return Route(True, "low", "careful comparison")
    if re.search(r"(\d+\s*[\^*/]\s*\d+.*){3,}", text) or re.search(r"[a-z]\s*=\s*[\d(].*[a-z]\s*=", text):
        return Route(True, "low", "multi-step math")
    return Route(False, "low", "default fast")
