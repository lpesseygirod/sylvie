"""Research papers online: OpenAlex to find them, Crossref to check that a reference really exists.

Only the search words leave the Mac (Lea's name is stripped, as for web search). No account, no
email address: OpenAlex's free daily budget for anonymous use is plenty for one person (each
search costs $0.001 of a $0.10/day allowance). An API key in the Keychain (`openalex-api-key`) is
used if Lea adds one.

    OpenAlex: https://openalex.org (CC0 data) — 250M+ works, abstracts, citation counts, open PDFs.
    Crossref: https://www.crossref.org (metadata is open) — the registry of DOIs.
"""

from __future__ import annotations

import re

import httpx

from knowledge.cite import Ref, clean_doi

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
UA = {"User-Agent": "Sylvie-local-assistant/1.0"}
TIMEOUT = 15.0
FIELDS = ("id,doi,title,publication_year,authorships,primary_location,abstract_inverted_index,"
          "open_access,cited_by_count,biblio,type,best_oa_location")  # fmt: skip
KINDS = {"article": "article", "review": "article", "book": "book", "book-chapter": "chapter",
         "report": "report", "dissertation": "thesis", "preprint": "article"}  # fmt: skip


class ScholarUnavailable(RuntimeError):
    pass


def _name(display: str) -> tuple[str, str]:
    """ "Mark A. Delucchi" → ("Delucchi", "Mark A."); single words stay whole (organisations)."""
    parts = display.strip().split()
    if len(parts) < 2:
        return (display.strip(), "")
    particles = {"van", "von", "de", "del", "da", "di", "la", "le", "du", "dos"}
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in particles:
        i -= 1
    return (" ".join(parts[i:]), " ".join(parts[:i]))


def abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    pos = sorted((i, w) for w, idx in inverted.items() for i in idx)
    return " ".join(w for _, w in pos)


def openalex_ref(w: dict) -> Ref:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    b = w.get("biblio") or {}
    pages = "–".join(p for p in (b.get("first_page"), b.get("last_page")) if p)
    if b.get("first_page") and b.get("first_page") == b.get("last_page"):
        pages = b["first_page"]
    return Ref(
        title=(w.get("title") or "").strip(),
        authors=[_name(a["author"]["display_name"]) for a in w.get("authorships") or [] if a.get("author")],
        year=str(w.get("publication_year") or ""),
        kind=KINDS.get(w.get("type") or "", "article"),
        container=src.get("display_name") or "",
        volume=b.get("volume") or "",
        issue=b.get("issue") or "",
        pages=pages,
        publisher=src.get("host_organization_name") or "",
        doi=clean_doi(w.get("doi") or ""),
        url=loc.get("landing_page_url") or "",
    )


def _api_key() -> str | None:
    try:
        from core.auth import get_secret

        return get_secret("openalex-api-key")
    except Exception:
        return None


def search_papers(query: str, n: int = 5, since: int | None = None, get=None) -> list[dict]:
    """[{ref, abstract, cited_by, open_pdf, openalex}] — the most relevant works first."""
    params = {"search": query, "per_page": n, "select": FIELDS}
    if since:
        params["filter"] = f"from_publication_date:{since}-01-01"
    if key := _api_key():
        params["api_key"] = key
    try:
        r = _get(get, OPENALEX, params=params)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ScholarUnavailable(str(e)) from e
    out = []
    for w in r.json().get("results", []):
        if not w.get("title"):
            continue
        oa = w.get("best_oa_location") or {}
        out.append({
            "ref": openalex_ref(w),
            "abstract": abstract(w.get("abstract_inverted_index")),
            "cited_by": w.get("cited_by_count") or 0,
            "open_pdf": oa.get("pdf_url") or (w.get("open_access") or {}).get("oa_url") or "",
            "openalex": w.get("id", ""),
        })  # fmt: skip
    return out


def crossref_ref(m: dict) -> Ref:
    issued = ((m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    kind = {"journal-article": "article", "book": "book", "monograph": "book", "book-chapter": "chapter",
            "report": "report", "dissertation": "thesis", "proceedings-article": "conference",
            "posted-content": "article"}.get(m.get("type", ""), "article")  # fmt: skip
    authors = [(a.get("family") or a.get("name") or "", a.get("given") or "") for a in m.get("author") or []]
    return Ref(
        title=" ".join((m.get("title") or [""])[0].split()),
        authors=[a for a in authors if a[0]],
        year=str(issued or ""),
        kind=kind,
        container=(m.get("container-title") or [""])[0],
        volume=m.get("volume", ""),
        issue=m.get("issue", ""),
        pages=m.get("page", ""),
        publisher=m.get("publisher", ""),
        doi=clean_doi(m.get("DOI", "")),
        url=m.get("URL", ""),
    )


def _get(get, url: str, **kw) -> httpx.Response:
    """GET with a short wait and one retry when the service says "too many requests" (429)."""
    import time

    get = get or httpx.get
    for attempt in range(3):
        r = get(url, headers=UA, timeout=TIMEOUT, **kw)
        if r.status_code != 429 or attempt == 2:
            return r
        try:
            wait = float(r.headers.get("retry-after", 2))
        except ValueError:
            wait = 2.0
        time.sleep(min(6.0, max(1.0, wait)))
    return r


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", s.lower()) if len(w) > 2}


def check_reference(title: str, authors: str = "", year: str = "", get=None) -> tuple[Ref | None, float]:
    """Finds the real record for a reference: by DOI (in `title`), or by title + authors + year.
    Returns (record, confidence 0-1). Low confidence means it probably doesn't exist as written."""
    doi = re.search(r"10\.\d{4,9}/[^\s\"<>]+", title)
    try:
        if doi:
            r = _get(get, f"{CROSSREF}/{clean_doi(doi.group(0)).rstrip('.,;')}")
            if r.status_code == 404:
                return None, 0.0
            r.raise_for_status()
            return crossref_ref(r.json()["message"]), 1.0
        params = {"query.bibliographic": f"{title} {year}"[:400], "rows": 1}
        if authors:
            params["query.author"] = authors[:200]
        r = _get(get, CROSSREF, params=params)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ScholarUnavailable(str(e)) from e
    items = r.json()["message"].get("items") or []
    if not items:
        return None, 0.0
    ref = crossref_ref(items[0])
    want, got = _words(title), _words(ref.title)
    score = len(want & got) / max(1, len(want))  # how much of the claimed title the record has
    if len(want & got) / max(1, len(got)) < 0.3:  # the record is mostly about something else
        score *= 0.5
    if authors and ref.authors and not _words(authors) & {w for f, _ in ref.authors for w in _words(f)}:
        score *= 0.6
    if year and ref.year and abs(int(ref.year) - int(re.sub(r"\D", "", year)[:4] or ref.year)) > 1:
        score *= 0.6
    return ref, round(score, 2)
