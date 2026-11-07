"""Writing practice: Sylvie learns academic writing from open-access papers, without keeping them.

Each night (learning/overnight.py) a few papers are read from Europe PMC: only articles under
CC BY or CC0 (commercial use allowed, plan rule on licenses), on varied subjects (energy, policy,
economics, education, climate, psychology…), English. For each paper Sylvie gets one exercise:

  - abstract:      from the paper's introduction and conclusion, write its abstract;
  - introduction:  from its title, abstract and section headings, write its introduction.

Then:  Sylvie's draft → the local judge grades it against the published text (faithful to the
material, structure, clarity, academic tone, no invented facts or references) → Sylvie rewrites it
with the feedback (thinking mode; she never sees the published text) → the judge grades again →
clear improvements become training pairs (chosen = her rewrite, rejected = her draft).

So the training targets are Sylvie's own writing (plan rule 4); the published text is only the
judge's yardstick. Nothing of the paper is kept except the excerpt in the exercise itself, with
its license and source recorded on the example. Tagged "general": it can go in the shareable part.
"""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from core.jsonutil import extract_json

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = {"User-Agent": "Sylvie-local-assistant/1.0"}
MAX_XML = 5_000_000
INPUT_CHARS = 3500
SUBJECTS = [
    "energy policy", "electric vehicles", "climate change mitigation", "urban transport", "renewable energy",
    "economics", "public policy", "education", "sociology", "psychology", "environmental science",
    "sustainability", "public health policy", "technology adoption", "agriculture", "water resources",
    "air pollution", "housing", "labor market", "innovation", "biodiversity", "migration", "ethics",
]  # fmt: skip
OPEN = re.compile(r"creativecommons\.org/(licenses/by/|publicdomain/zero/)", re.I)
RESTRICTED = re.compile(r"creativecommons\.org/licenses/by-(nc|nd)|non-?commercial|noderivatives", re.I)

WRITER = (
    "You are Sylvie, a careful academic writer. Write clear, precise, well-structured prose in an academic "
    "register. Use only the information you are given: never invent numbers, findings, names or references. "
    "Write only the requested text, with no title and no preamble."
)
TASKS = {
    "abstract": (
        "Write the abstract of this paper (150-250 words, one paragraph): context, aim, method, main results, "
        "conclusion.\n\nTitle: {title}\n\nIntroduction and conclusion of the paper:\n<paper>\n{material}\n</paper>"
    ),
    "introduction": (
        "Write the introduction of this paper (300-450 words): the context and why it matters, what is not "
        "yet known, and what this paper does. Don't cite references (you have none), and don't add facts "
        "that aren't in the abstract.\n\nTitle: {title}\n\nAbstract:\n<paper>\n{material}\n</paper>\n\n"
        "Sections of the paper: {headings}"
    ),
}
TARGET_WORDS = {"abstract": 250, "introduction": 450}

JUDGE = """You grade academic writing. The writer was given this task:
<task>
{task}
</task>

The writer's text:
<text>
{text}
</text>

For comparison, the version the authors published (good, not necessarily perfect):
<published>
{reference}
</published>

Grade the writer's text from 1 to 10 on: faithfulness to the material given (any invented number,
finding, name or reference: score 3 or less), structure and logical flow, clarity and concision,
academic register, and doing exactly the task (length, content). Don't reward length.

Return JSON only: {{"score": <1-10>, "feedback": "<two or three concrete things to improve>"}}"""

REWRITE = """Your text was graded {score}/10. Feedback: {feedback}

Rewrite it to fix exactly that, keeping what was good. Same task, same limits:
<task>
{task}
</task>

Your earlier text:
<text>
{text}
</text>

Write ONLY the improved text:"""


@dataclass
class Paper:
    pmcid: str
    title: str
    abstract: str
    sections: list[tuple[str, str]]  # (heading, text)
    license: str

    def section(self, *names: str) -> str:
        for h, t in self.sections:
            if any(n in h.lower() for n in names):
                return t
        return ""


@dataclass
class Exercise:
    paper: Paper
    kind: str
    prompt: str
    reference: str
    draft: str = ""
    score: float = 0.0
    feedback: str = ""
    better: str = ""
    better_score: float = 0.0
    meta: dict = field(default_factory=dict)


def _text(el) -> str:
    """Element text without citation markers ([12], superscripts), tables or figures."""
    if el is None:
        return ""
    parts = []

    def walk(e):
        if e.tag in ("xref", "table-wrap", "fig", "disp-formula", "table", "graphic"):
            if e.tail:
                parts.append(e.tail)
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
        if e.tag in ("p", "title"):
            parts.append("\n")
        if e.tail:
            parts.append(e.tail)

    walk(el)
    text = "".join(parts)
    text = re.sub(r"\[\s*[,–\-\s]*\]|\(\s*[,;–\-\s]*\)", "", text)  # brackets emptied by removed citations
    text = re.sub(
        r"\s+,(\s*,)+", ",", text
    )  # "fragmentation , , urbanization" → "fragmentation, urbanization"
    text = re.sub(r"\s+[–-](?=[.,;:)])", "", text)  # "at a global scale –." → "at a global scale."
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"[,;]+(?=[.:])", "", text)  # "Cities grow,." → "Cities grow."
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def parse_article(xml: bytes, pmcid: str) -> Paper | None:
    """A paper from Europe PMC's JATS XML, or None if its license isn't CC BY / CC0."""
    root = ET.fromstring(xml)
    perm = root.find(".//permissions")
    lic_text = " ".join(
        [_text(perm)] + [v for e in (perm.iter() if perm is not None else []) for v in e.attrib.values()]
    )
    if not OPEN.search(lic_text) or RESTRICTED.search(lic_text):
        return None
    lic = "CC0" if "publicdomain/zero" in lic_text.lower() else "CC BY 4.0"
    title = _text(root.find(".//title-group/article-title"))
    abstract = _text(root.find(".//abstract"))
    body = root.find("body")
    sections = []
    for sec in body.findall("sec") if body is not None else []:
        t = sec.find("title")
        heading = _text(t)
        if t is not None:
            sec.remove(t)
        sections.append((heading, _text(sec)))
    if not title or not abstract or not sections:
        return None
    return Paper(pmcid, title, abstract, sections, lic)


def find_papers(rng: random.Random, n: int, get=httpx.get) -> list[Paper]:
    """Up to n open papers on varied subjects (several searches; skips papers that don't qualify)."""
    out: list[Paper] = []
    for _ in range(n * 4):
        if len(out) >= n:
            break
        subject, year = rng.choice(SUBJECTS), rng.randint(2012, 2025)
        query = (f'"{subject}" AND OPEN_ACCESS:y AND HAS_FT:y AND LANG:eng AND PUB_YEAR:{year} '
                 '(LICENSE:"cc by" OR LICENSE:"cc0")')  # fmt: skip
        try:
            r = get(f"{EPMC}/search", params={"query": query, "format": "json", "pageSize": 25,
                                              "resultType": "lite"}, headers=UA, timeout=30)  # fmt: skip
            r.raise_for_status()
            hits = [h for h in r.json()["resultList"]["result"] if h.get("pmcid")]
            if not hits:
                continue
            pmcid = rng.choice(hits)["pmcid"]
            if any(p.pmcid == pmcid for p in out):
                continue
            x = get(f"{EPMC}/{pmcid}/fullTextXML", headers=UA, timeout=60)
            x.raise_for_status()
            if len(x.content) > MAX_XML:
                continue
            paper = parse_article(x.content, pmcid)
        except (httpx.HTTPError, ValueError, KeyError, ET.ParseError):
            continue
        if paper is not None:
            out.append(paper)
    return out


def make_exercise(p: Paper, rng: random.Random) -> Exercise | None:
    intro = p.section("introduction", "background")
    concl = p.section("conclusion") or p.section("discussion")
    kinds = []
    if intro and concl and 120 < len(p.abstract.split()) < 400:
        kinds.append("abstract")
    if intro and len(intro.split()) > 200:
        kinds.append("introduction")
    if not kinds:
        return None
    kind = rng.choice(kinds)
    if kind == "abstract":
        half = INPUT_CHARS // 2
        material = intro[:half].rsplit(" ", 1)[0] + " […]\n\n" + concl[:half].rsplit(" ", 1)[0] + " […]"
        prompt = TASKS["abstract"].format(title=p.title, material=material)
        reference = p.abstract
    else:
        headings = "; ".join(h for h, _ in p.sections if h)[:400]
        prompt = TASKS["introduction"].format(
            title=p.title, material=p.abstract[:INPUT_CHARS], headings=headings
        )
        reference = " ".join(intro.split()[:600])
    return Exercise(p, kind, prompt, reference)


def too_long(ex: Exercise, text: str) -> bool:
    return len(text.split()) > TARGET_WORDS[ex.kind] * 1.4


def grade(judge, ex: Exercise, text: str) -> tuple[float, str]:
    raw = judge.complete(
        [{"role": "user", "content": JUDGE.format(task=ex.prompt, text=text, reference=ex.reference)}],
        max_tokens=300,
    )
    data = extract_json(raw) or {}
    try:
        score = float(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    if too_long(ex, text):
        score -= 2
    return score, str(data.get("feedback", ""))[:500]


def writing_practice(pool, store, cfg, n: int, batch: str, model_name: str, seed: int | None = None,
                     stop=lambda: False, get=httpx.get) -> dict:  # fmt: skip
    """One round of writing practice; returns counts. `pool` swaps Sylvie's model and the judge."""
    rng = random.Random(seed)
    stats = {"papers": 0, "exercises": 0, "kept": 0}
    if n <= 0:
        return stats
    papers = find_papers(rng, n, get=get)
    stats["papers"] = len(papers)
    exercises = [e for e in (make_exercise(p, rng) for p in papers) if e is not None]
    stats["exercises"] = len(exercises)
    if not exercises:
        return stats

    def write(messages, **kw):
        return pool.use("sylvie").complete([{"role": "system", "content": WRITER}] + messages, **kw).strip()

    for ex in exercises:
        if stop():
            return stats
        ex.draft = write([{"role": "user", "content": ex.prompt}], max_tokens=900)
    for ex in exercises:
        if stop():
            return stats
        ex.score, ex.feedback = grade(pool.use("judge"), ex, ex.draft)
    weak = [e for e in exercises if e.score < 9 and e.draft]
    for ex in weak:
        if stop():
            return stats
        ex.better = write([{"role": "user", "content": REWRITE.format(
            score=ex.score, feedback=ex.feedback, task=ex.prompt, text=ex.draft)}],
            thinking=True, effort="medium", max_tokens=3000)  # fmt: skip
    auto = cfg.get_path("learning.auto_approve_judged", False)
    min_score = cfg.get_path("learning.auto_approve_min_score", 8)
    for ex in weak:
        if stop() or not ex.better:
            continue
        ex.better_score, _ = grade(pool.use("judge"), ex, ex.better)
        if ex.better_score - ex.score >= 1.5 and ex.better_score >= 7 and not too_long(ex, ex.better):
            store.add_example(
                [{"role": "user", "content": ex.prompt}], chosen=ex.better, rejected=ex.draft,
                source=f"sylvie-local:{model_name}",
                license=f"apache-2.0 (local model output) + {ex.paper.license} excerpt: Europe PMC {ex.paper.pmcid}",
                tag="general", status="ready" if auto and ex.better_score >= min_score else "staged", batch=batch,
            )  # fmt: skip
            stats["kept"] += 1
    return stats
