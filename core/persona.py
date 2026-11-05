"""Builds Sylvie's system prompt and the per-turn context block.

Layout (chosen so the model's prompt cache keeps hitting):
- system prompt: persona + rules + Lea's profile + saved preferences. Changes rarely
  (once a day for the date, or when the profile changes).
- per-turn context: exact time, recalled memories, an optional aside hint. Goes at the
  top of Lea's message, so it never invalidates the cached history before it.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import ROOT, Config, load_config

PROMPTS = ROOT / "core" / "prompts"


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def now(cfg: Config | None = None) -> datetime:
    cfg = cfg or load_config()
    return datetime.now(ZoneInfo(cfg.get_path("user.timezone", "Europe/Madrid")))


def build_system_prompt(
    cfg: Config | None = None,
    profile_text: str = "",
    preferences_text: str = "",
    today: datetime | None = None,
) -> str:
    cfg = cfg or load_config()
    today = today or now(cfg)
    persona = _read("persona.md").format(
        today=today.strftime("%A %-d %B %Y"),
        timezone=cfg.get_path("user.timezone", "Europe/Madrid"),
        city=cfg.get_path("user.city", "Madrid"),
        currency=cfg.get_path("user.currency", "EUR"),
        profile=profile_text.strip(),
        preferences=preferences_text.strip(),
        memories="",
        aside="",
    )
    rules = _read("rules.md")
    text = persona.strip() + "\n\n" + rules.strip()
    # Collapse the blank gaps left by empty sections.
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def build_turn_context(
    cfg: Config | None = None,
    memories: list[str] | None = None,
    aside: str | None = None,
    when: datetime | None = None,
) -> str:
    cfg = cfg or load_config()
    when = when or now(cfg)
    lines = [f"Time: {when.strftime('%H:%M, %A %-d %B %Y')}"]
    if memories:
        lines.append("Things you remember about Lea that may be relevant:")
        lines += [f"- {m}" for m in memories]
    if aside:
        lines.append(
            "Possible aside (mention briefly at the end ONLY if genuinely useful, otherwise ignore): " + aside
        )
    return "<context>\n" + "\n".join(lines) + "\n</context>"


def wrap_user_message(text: str, context: str) -> str:
    return f"{context}\n\n{text}"
