"""Web search through SearXNG running locally, then open the best pages.

Queries stay generic: Lea's name is stripped so it never reaches a search engine.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from tools.fetch import BlockedURL, fetch_page


class SearchUnavailable(RuntimeError):
    pass


def sanitize_query(query: str, private_terms: list[str]) -> str:
    q = query
    for term in private_terms:
        if term:
            q = re.sub(rf"\b{re.escape(term)}('s)?\b", "", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip()


def searxng(base_url: str, query: str, max_results: int = 5, language: str | None = None) -> list[dict]:
    params = {"q": query, "format": "json", "safesearch": 0}
    if language:
        params["language"] = language
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/search", params=params, timeout=10.0)  # SearXNG allows its engines 7 s
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise SearchUnavailable(f"SearXNG not reachable at {base_url}: {e}") from e
    out = []
    for item in r.json().get("results", []):
        if item.get("url", "").startswith(("http://", "https://")):
            out.append(
                {"title": item.get("title", ""), "url": item["url"], "snippet": item.get("content", "")}
            )
        if len(out) >= max_results:
            break
    return out


def web_search(
    query: str,
    base_url: str,
    max_results: int = 5,
    pages_to_open: int = 3,
    private_terms: list[str] | None = None,
    page_chars: int = 2500,
) -> tuple[str, list[dict]]:
    """Returns (text for the model, sources list)."""
    q = sanitize_query(query, private_terms or [])
    results = searxng(base_url, q, max_results=max_results)
    if not results:
        return f"No results for '{q}'.", []

    def _open(res):
        try:
            return fetch_page(res["url"], max_chars=page_chars)
        except (BlockedURL, httpx.HTTPError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=pages_to_open) as pool:
        pages = list(pool.map(_open, results[:pages_to_open]))

    blocks = []
    sources = []
    for n, res in enumerate(results, 1):
        page = pages[n - 1] if n <= len(pages) else None
        body = page["text"] if page and page["text"] else res["snippet"]
        blocks.append(f"[{n}] {res['title']}\nURL: {res['url']}\n{body}")
        sources.append({"n": n, "title": res["title"], "url": res["url"]})
    text = f"Search results for '{q}':\n<untrusted>\n" + "\n\n".join(blocks) + "\n</untrusted>"
    return text, sources
