"""Is a piece of training text about Lea? Keeps her out of the shared ("general") part of Sylvie.

Lea, 2026-11-07: the general things Sylvie learns must be stored apart from her personal ones, so a
base Sylvie can be given to other people and learn from them. Personal facts already live in memory,
not in the model's weights (rule 12), and training already leaves out examples tagged "personal".
But a "general" example can still carry her: an answer written with her profile in the prompt greets
her by name ("Hola Lea."), or mentions her project, her city or her phone number.

So every general example is checked against what Sylvie knows about Lea:
- her name and surnames (config `user.name`, and names found in her facts),
- any phone number or email address at all,
- web addresses and domains from her facts,
- distinctive names from her facts (people, projects, places): capitalised words that are not
  common words. "Common" = seen at least twice in the open dataset's questions, so "Madrid",
  "Spotify" or "French" don't count, but "Kore" or a friend's name do.
A match means the example is personal: it is not trained on and never leaves the Mac.
The check is deliberately strict: losing a few general examples costs little; leaking costs a lot.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path

# International (+34 655 010 203) or grouped like a phone (655 010 203, 655-010-203); not sums.
PHONE = re.compile(
    r"(?<![\w.])(\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}|\d{3}[.-]?\d{3}[.-]?\d{3,4}|\d{3} \d{3} \d{3,4})(?![\w.])"
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DOMAIN = re.compile(r"\b(?:https?://)?((?:[\w-]+\.)+(?:com|es|fr|net|org|io|app|dev|ai|co|me|eu|mx))\b", re.I)
WORD = re.compile(r"\b[A-ZÀ-ÖØ-Þ][\w'’-]*\w|\b[A-ZÀ-ÖØ-Þ]\b")

# Always common, even if the open dataset sample is missing (tests, a fresh install).
BASIC = {
    "I", "A", "The", "This", "That", "She", "He", "Her", "His", "They", "It", "We", "You", "My", "Sylvie",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December", "English", "Spanish", "French", "Catalan", "Italian", "German",
    "AI", "API", "OK", "Mac", "MacBook", "iPhone", "Apple", "Google", "GitHub", "Spotify", "Python",
}  # fmt: skip
MIN_LEN = 3  # one- and two-letter capitalised words are too ambiguous to be identifiers


def _strip_possessive(w: str) -> str:
    return re.sub(r"['’]s$", "", w)


class PersonalTerms:
    def __init__(self, names: set[str], terms: set[str], domains: set[str]):
        self.names, self.terms, self.domains = names, terms, {d.lower() for d in domains}
        words = sorted(names | terms, key=len, reverse=True)
        # Case-sensitive on purpose: "Lea" is her name, "lea" is Spanish for "read".
        self._words = (
            re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, words)) + r")(?![\w-])") if words else None
        )

    def find(self, text: str) -> str | None:
        """The first thing in `text` that points at Lea, or None."""
        if not text:
            return None
        if EMAIL.search(text):
            return "an email address"
        if PHONE.search(text):
            return "a phone number"
        for m in DOMAIN.finditer(text):
            if m.group(1).lower() in self.domains:
                return m.group(1)
        if self._words and (m := self._words.search(text)):
            return m.group(1)
        return None

    def is_personal(self, *texts: str) -> bool:
        return any(self.find(t or "") for t in texts)

    @classmethod
    def build(cls, user_name: str, facts: list[str], common: set[str] | None = None) -> PersonalTerms:
        common = (common or set()) | BASIC
        names = {p for p in re.split(r"[\s-]+", user_name or "") if len(p) >= 2}
        terms: set[str] = set()
        domains: set[str] = set()
        for fact in facts:
            domains |= {m.group(1) for m in DOMAIN.finditer(fact)}
            # "Lea's legal name is Lea Moreau-Laval Ortega": every part of a stated name is hers.
            if m := re.search(r"\bname is ([^.,;]+)", fact):
                names |= {_strip_possessive(p) for p in re.split(r"[\s-]+", m.group(1)) if p[:1].isupper()}
            for m in WORD.finditer(DOMAIN.sub(" ", fact)):
                w = _strip_possessive(m.group())
                if len(w) >= MIN_LEN and w not in common and w.lower() not in {c.lower() for c in BASIC}:
                    terms.add(w)
        names = {n for n in names if len(n) >= 2}
        return cls(names, terms - names, domains)

    @classmethod
    def load(cls, cfg, db) -> PersonalTerms:
        """From Sylvie's real memory: config name + every fact (also forgotten or replaced ones: what
        was once true about Lea is still about Lea)."""
        facts = [r["text"] for r in db.query("SELECT text FROM facts")]
        prefs = (
            [r["text"] for r in db.query("SELECT text FROM preferences")]
            if _has_table(db, "preferences")
            else []
        )
        return cls.build(str(cfg.get_path("user.name", "")), facts + prefs, common_words(cfg))


def _has_table(db, name: str) -> bool:
    return db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)) is not None


def common_words(cfg, limit: int = 20000) -> set[str]:
    """Words that are generic, not Lea's: capitalised words seen at least twice in the open dataset
    (questions and answers), and capitalised forms of ordinary lowercase words ("Step", "Home")."""
    path = Path(cfg.data_dir) / "datasets" / "open" / "oasst2" / "2023-11-05_oasst2_ready.trees.jsonl.gz"
    if not path.exists():
        return set()
    caps: Counter = Counter()
    lower: Counter = Counter()

    def walk(node: dict) -> None:
        text = node.get("text", "")
        caps.update({_strip_possessive(w) for w in WORD.findall(text)})
        lower.update(set(re.findall(r"\b[a-zà-ÿ][a-zà-ÿ'-]+\b", text)))
        for r in node.get("replies", []):
            walk(r)

    with gzip.open(path, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            try:
                walk(json.loads(line).get("prompt", {}))
            except ValueError:
                continue
    out = {w for w, n in caps.items() if n >= 2}
    return out | {w.capitalize() for w, n in lower.items() if n >= 5}


def example_text(row: dict) -> list[str]:
    """Every piece of text in a stored training example."""
    msgs = row.get("messages")
    if isinstance(msgs, str):
        msgs = json.loads(msgs)
    parts = [m.get("content") or "" for m in msgs or []]
    return parts + [row.get(k) or "" for k in ("chosen", "rejected", "reasoning", "tool_calls")]
