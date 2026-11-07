"""References and citations in the usual academic styles: APA 7, MLA 9, Chicago author-date, Harvard.

Sylvie never writes a reference from memory: every reference comes from a source a tool returned
(Lea's Zotero library, OpenAlex, Crossref), is turned into a `Ref`, and formatted here, so the
bibliography of an essay is exactly what was found. Formatting is plain markdown (*italics*).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STYLES = {
    "apa": "APA 7",
    "mla": "MLA 9",
    "chicago": "Chicago author-date",
    "harvard": "Harvard",
}
ALIASES = {"apa7": "apa", "mla9": "mla", "chicago-author-date": "chicago", "cite them right": "harvard"}


STYLE_IN_TEXT = re.compile(r"\b(apa|mla|chicago|harvard)\b", re.I)


def style_asked(text: str) -> str | None:
    """The style Lea named in her message ("…in Chicago style", "formato APA"), if any."""
    m = STYLE_IN_TEXT.search(text or "")
    return m.group(1).lower() if m else None


def style_key(style: str | None) -> str:
    s = (style or "apa").strip().lower()
    s = ALIASES.get(s, s)
    for key in STYLES:
        if s.startswith(key):
            return key
    return "apa"


@dataclass
class Ref:
    title: str
    authors: list[tuple[str, str]] = field(default_factory=list)  # (family, given); given "" = organisation
    year: str = ""
    kind: str = "article"  # article | book | chapter | report | thesis | conference | web
    container: str = ""  # journal, book or website
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    doi: str = ""
    url: str = ""
    number: str = ""  # report number

    @property
    def link(self) -> str:
        return f"https://doi.org/{self.doi}" if self.doi else self.url


def clean_doi(doi: str) -> str:
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", (doi or "").strip(), flags=re.I)


def initials(given: str) -> str:
    """ "Mark A." → "M. A."; "Jean-Paul" → "J.-P."."""
    out = []
    for part in given.replace(".", ". ").split():
        sub = [p[0].upper() + "." for p in part.split("-") if p and p[0].isalpha()]
        if sub:
            out.append("-".join(sub))
    return " ".join(out)


def _and(names: list[str], word: str = "and", serial: bool = True) -> str:
    """["A"] → "A"; ["A", "B"] → "A and B"; ["A", "B", "C"] → "A, B, and C" (serial comma)."""
    if len(names) <= 2:
        return f" {word} ".join(names)
    return ", ".join(names[:-1]) + ("," if serial else "") + f" {word} " + names[-1]


def _period(s: str) -> str:
    s = s.strip()
    return s if not s or s[-1] in ".?!" else s + "."


def _pages(p: str) -> str:
    return p.replace("--", "–").replace("-", "–")


# ---------- APA 7 ----------
def _apa_name(f: str, g: str) -> str:
    return f if not g else f"{f}, {initials(g)}"


def apa(r: Ref) -> str:
    names = [_apa_name(f, g) for f, g in r.authors]
    if len(names) > 20:
        names = names[:19] + ["… " + names[-1]]
        who = ", ".join(names)
    elif len(names) >= 2:
        who = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        who = "".join(names)
    year = f"({r.year or 'n.d.'})."
    head = f"{_period(who)} {year}" if who else ""
    if r.kind in ("book", "report", "thesis"):
        title = f"*{r.title}*"
        if r.number:
            title += f" ({r.number})"
        parts = [head, _period(title)] if head else [_period(title), year]
        if r.publisher:
            parts.append(_period(r.publisher))
    elif r.kind == "web":
        parts = [head, f"*{_period(r.title)}*"] if head else [f"*{_period(r.title)}*", year]
        if r.container:
            parts.append(_period(r.container))
    else:
        parts = [head, _period(r.title)] if head else [_period(r.title), year]
        src = f"*{r.container}*" if r.container else ""
        if r.volume:
            src += f", *{r.volume}*"
        if r.issue:
            src += f"({r.issue})"
        if r.pages:
            src += f", {_pages(r.pages)}"
        if src:
            parts.append(_period(src))
    if r.link:
        parts.append(r.link)
    return " ".join(p for p in parts if p)


def apa_in_text(r: Ref) -> str:
    fam = [f for f, _ in r.authors]
    who = (
        fam[0]
        if len(fam) == 1
        else f"{fam[0]} & {fam[1]}"
        if len(fam) == 2
        else f"{fam[0]} et al."
        if fam
        else _short_title(r)
    )
    return f"({who}, {r.year or 'n.d.'})"


# ---------- MLA 9 ----------
def mla(r: Ref) -> str:
    a = r.authors
    if not a:
        who = ""
    elif len(a) == 1:
        who = a[0][0] + (f", {a[0][1]}" if a[0][1] else "")
    elif len(a) == 2:
        second = f"{a[1][1]} {a[1][0]}".strip()
        who = f"{a[0][0]}, {a[0][1]}, and {second}" if a[0][1] else f"{a[0][0]} and {second}"
    else:
        who = f"{a[0][0]}, {a[0][1]}, et al."
    parts = [_period(who)] if who else []
    if r.kind in ("book", "report", "thesis"):
        parts.append(f"*{_period(r.title)}*")
        tail = [r.publisher, r.year]
    else:
        parts.append(f"“{_period(r.title)}”")
        tail = [f"*{r.container}*" if r.container else "", f"vol. {r.volume}" if r.volume else "",
                f"no. {r.issue}" if r.issue else "", r.year, f"pp. {_pages(r.pages)}" if r.pages else ""]  # fmt: skip
    tail = [t for t in tail if t]
    if tail:
        parts.append(_period(", ".join(tail)))
    if r.link:
        parts.append(_period(r.link.replace("https://", "")))
    return " ".join(parts)


def mla_in_text(r: Ref) -> str:
    fam = [f for f, _ in r.authors]
    who = (
        fam[0]
        if len(fam) == 1
        else f"{fam[0]} and {fam[1]}"
        if len(fam) == 2
        else f"{fam[0]} et al."
        if fam
        else _short_title(r)
    )
    return f"({who})"


# ---------- Chicago author-date ----------
def chicago(r: Ref) -> str:
    a = r.authors
    names = []
    for i, (f, g) in enumerate(a[:10]):
        names.append((f"{f}, {g}" if g else f) if i == 0 else (f"{g} {f}" if g else f))
    if len(a) > 10:
        names = names[:7] + ["et al."]
        who = ", ".join(names)
    else:
        who = _and(names)
    parts = [_period(who)] if who else []
    parts.append(_period(r.year or "n.d."))
    if r.kind in ("book", "report", "thesis"):
        parts.append(f"*{_period(r.title)}*")
        if r.publisher:
            parts.append(_period(r.publisher))
    else:
        parts.append(f"“{_period(r.title)}”")
        src = f"*{r.container}*" if r.container else ""
        if r.volume:
            src += f" {r.volume}"
        if r.issue:
            src += f" ({r.issue})"
        if r.pages:
            src += f": {_pages(r.pages)}"
        if src:
            parts.append(_period(src))
    if r.link:
        parts.append(_period(r.link))
    return " ".join(parts)


def chicago_in_text(r: Ref) -> str:
    fam = [f for f, _ in r.authors]
    if not fam:
        who = _short_title(r)
    elif len(fam) <= 3:
        who = _and(fam)
    else:
        who = f"{fam[0]} et al."
    return f"({who} {r.year or 'n.d.'})"


# ---------- Harvard (Cite Them Right) ----------
def harvard(r: Ref) -> str:
    names = [f if not g else f"{f}, {initials(g)}" for f, g in r.authors]
    if len(names) >= 4:
        who = f"{names[0]} et al."
    else:
        who = _and(names, serial=False)
    head = f"{who} ({r.year or 'no date'})" if who else f"({r.year or 'no date'})"
    if r.kind in ("book", "report", "thesis"):
        out = f"{head} *{r.title}*."
        if r.publisher:
            out += f" {_period(r.publisher)}"
    else:
        out = f"{head} ‘{r.title}’"
        if r.container:
            out += f", *{r.container}*"
        if r.volume:
            out += f", {r.volume}"
            if r.issue:
                out += f"({r.issue})"
        if r.pages:
            out += f", pp. {_pages(r.pages)}"
        out += "."
    if r.doi:
        out += f" doi:{r.doi}."
    elif r.url:
        out += f" Available at: {r.url}."
    return out


def harvard_in_text(r: Ref) -> str:
    fam = [f for f, _ in r.authors]
    who = _and(fam, serial=False) if 0 < len(fam) <= 3 else f"{fam[0]} et al." if fam else _short_title(r)
    return f"({who}, {r.year or 'no date'})"


def _short_title(r: Ref) -> str:
    words = r.title.split()
    return "“" + " ".join(words[:4]) + ("…" if len(words) > 4 else "") + "”"


FORMATS = {"apa": (apa, apa_in_text), "mla": (mla, mla_in_text), "chicago": (chicago, chicago_in_text),
           "harvard": (harvard, harvard_in_text)}  # fmt: skip


def reference(r: Ref, style: str | None = None) -> str:
    return FORMATS[style_key(style)][0](r)


def in_text(r: Ref, style: str | None = None) -> str:
    return FORMATS[style_key(style)][1](r)


def describe(r: Ref, style: str | None = None) -> str:
    """The lines a tool returns for one source: cite-in-text and the bibliography entry."""
    key = style_key(style)
    return f"Cite in text as: {in_text(r, key)}\nReference ({STYLES[key]}): {reference(r, key)}"


def missing_references(text: str, refs: list[Ref]) -> list[Ref]:
    """Works cited in the text (family name next to the year) whose reference isn't in the text."""
    low = text.lower()
    out, seen = [], set()
    for r in refs:
        if not r.authors or not r.year or r.title in seen:
            continue
        seen.add(r.title)
        fam = re.escape(r.authors[0][0])
        cited = re.search(rf"{fam}[^()\n]{{0,60}}?\(?{r.year}", text)
        listed = r.title.lower()[:40] in low
        if cited and not listed:
            out.append(r)
    return out


REF_HEADING = re.compile(
    r"^\W*(references|bibliography|works cited|sources|referencias|bibliograf[ií]a|r[ée]f[ée]rences|"
    r"bibliographie)\W*$",
    re.I | re.M,
)


def reference_lines(text: str) -> list[str]:
    """The entries of the references list at the end of an essay (after its heading)."""
    heads = list(REF_HEADING.finditer(text))
    if not heads:
        return []
    body = text[heads[-1].end() :].split("</doc>")[0]
    lines = [ln.strip(" -*•\t") for ln in body.splitlines()]
    return [ln for ln in lines if len(ln) > 25 and re.search(r"\b(1[5-9]|20)\d\d\b|n\.d\.|no date", ln)]


def _norm(s: str) -> str:
    return " ".join(re.findall(r"\w+", s.lower()))


def unverified_references(text: str, refs: list[Ref]) -> list[str]:
    """Entries of the essay's references list that match no work a tool found this turn."""
    titles = [_norm(r.title)[:50] for r in refs if r.title]
    return [ln for ln in reference_lines(text) if not any(t and t in _norm(ln) for t in titles)]
