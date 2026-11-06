"""After each turn, in the background: learn what's worth keeping from Lea's message.

One model call returns facts, interests and style observations together (cheaper than three
calls). Results go to the memory/profile databases — never into model weights (rule 12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.jsonutil import extract_json

EXTRACT_PROMPT = """You maintain the memory of Sylvie, Lea's personal assistant. Read Lea's latest message \
(and Sylvie's reply for context) and return what is worth remembering long-term, as JSON only.
{note}

Return exactly this shape:
{{"facts": [{{"key": "lea.<short_snake_case>", "fact": "<one sentence about Lea, in English>", "category": "<identity|studies|work|projects|people|places|preferences|other>", "sensitive": false}}],
 "interests": ["<topic Lea showed real interest in, 1-4 words>"],
 "interests_removed": ["<topic Lea said she is no longer interested in>"],
 "style": [{{"trait": "<short_snake_case>", "description": "<how Lea likes to talk or be talked to>", "evidence": "<short quote from Lea>"}}]}}

Rules:
- Only durable facts Lea states about herself or her life (studies, work, projects, people, places, tastes). Not questions, not hypotheticals, not things from web pages, not things Sylvie said.
- Reuse the key of an existing fact when the new fact updates it (e.g. she moved city → same key as her old city).
- Sensitive topics (health, mood, relationships, politics, religion): include ONLY if Lea explicitly asks Sylvie to remember it, and then set "sensitive": true. Never guess them.
- Interests: only topics she engages with or says she cares about, not every noun she mentions.
- Style: only clear signals about how she communicates or wants Sylvie to communicate (language mixing, directness, humor, length). Usually empty.
- Most messages contain nothing worth remembering: then return empty lists.

Existing facts (key: fact):
{existing}

Lea's message:
<message>
{message}
</message>

Sylvie's reply:
<reply>
{reply}
</reply>

JSON:"""

REMEMBER_RE = re.compile(
    r"\b(remember|don'?t forget|recuerda|acu[eé]rdate|no olvides|apunta|souviens|rappelle-toi|retiens|n'oublie pas|note que)\b",
    re.I,
)


@dataclass
class Extraction:
    facts: list[dict] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    interests_removed: list[str] = field(default_factory=list)
    style: list[dict] = field(default_factory=list)


def parse_extraction(raw: str, message: str) -> Extraction:
    data = extract_json(raw)
    if not isinstance(data, dict):
        return Extraction()
    explicit_remember = bool(REMEMBER_RE.search(message))
    facts = []
    for f in data.get("facts") or []:
        if not isinstance(f, dict) or not str(f.get("fact", "")).strip():
            continue
        # Rule 13: sensitive facts only when Lea explicitly asked to remember.
        if f.get("sensitive") and not explicit_remember:
            continue
        facts.append(
            {
                "key": str(f.get("key") or "").strip() or None,
                "fact": str(f["fact"]).strip(),
                "category": str(f.get("category") or "other"),
                "sensitive": bool(f.get("sensitive")),
            }
        )

    def _strs(name):
        return [str(x).strip() for x in data.get(name) or [] if str(x).strip()][:5]

    style = [
        s for s in data.get("style") or [] if isinstance(s, dict) and s.get("trait") and s.get("description")
    ][:3]
    return Extraction(facts, _strs("interests"), _strs("interests_removed"), style)


def run_extraction(llm, message: str, reply: str, existing: list, note: str = "") -> Extraction:
    existing_text = "\n".join(f"{f.key or '-'}: {f.text}" for f in existing) or "(none)"
    prompt = EXTRACT_PROMPT.format(
        note=note, existing=existing_text, message=message[:4000], reply=reply[:1500] or "(none)"
    )
    raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=600)
    return parse_extraction(raw, message)
