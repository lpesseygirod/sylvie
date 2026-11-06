"""Handing a task to Claude when Lea asks for it ("ask Claude…", "pregúntale a Claude", "délègue à Claude").

Lea, 2026-11-06: delegate to Claude and pass the answer back. This is the one thing Sylvie does that
leaves the Mac: the task text goes to Anthropic through Claude Code (already installed and signed in),
so it only runs when Lea's own message asks for Claude. Claude runs with no tools, no MCP servers and
an empty working folder: it can only write an answer, never read or change anything here. The answer
is kept in the conversation (and so in Sylvie's memory), but never becomes training data: Lea's plan
(rule 4) and Anthropic's terms don't allow training other models on Claude's output
(learning/teacher.py checks this).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

# Lea named Claude. Speech recognition often writes it as "cloud"/"clod" after "ask"/"pregunta".
EXPLICIT_RE = re.compile(
    r"\bclaude\b|\b(ask|tell|delegate( (it|this))?( to)?|pass (it|this) to|talk to|speak (to|with)|"
    r"pregunta(le)?( a)?|preg[uú]ntale a|delega(lo|r)?( (a|en))?|p[aá]sale(lo)? a|habla(r)? con|"
    r"demande( à)?|délègue( à)?|passe(-le)? à|parle(r)? à)\s+(a\s+|à\s+)?(cloud|clod|claud)\b",
    re.I,
)
TIMEOUT_S = 180
MAX_CHARS = 6000
SYSTEM = (
    "You are answering a request that Lea passed on through Sylvie, her private assistant on her Mac. "
    "Answer directly and concisely, in the language of the request. You have no tools here."
)


def asked_for_claude(text: str) -> bool:
    return bool(EXPLICIT_RE.search(text))


def claude_cli() -> str | None:
    found = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    return found if Path(found).exists() else None


def ask(task: str, workdir: Path, cli: str | None = None, run=subprocess.run) -> tuple[bool, str]:
    """(ok, Claude's answer or what went wrong)."""
    cli = cli or claude_cli()
    if cli is None:
        return False, "Claude Code isn't installed on this Mac, so Sylvie can't reach Claude."
    workdir.mkdir(parents=True, exist_ok=True)
    args = [
        cli, "-p", task, "--output-format", "text", "--tools", "", "--strict-mcp-config",
        "--no-session-persistence", "--setting-sources", "user", "--append-system-prompt", SYSTEM,
    ]  # fmt: skip
    try:
        p = run(args, cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=TIMEOUT_S, env={**os.environ, "NO_COLOR": "1"})  # fmt: skip
    except subprocess.TimeoutExpired:
        return False, f"Claude didn't answer within {TIMEOUT_S} seconds."
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out:
        err = (p.stderr or "").strip().splitlines()[-1:] or ["no answer"]
        return (
            False,
            f"Claude couldn't answer ({err[0][:200]}). Lea may need to open Claude Code and sign in.",
        )
    return True, out[:MAX_CHARS]
