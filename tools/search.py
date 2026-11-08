"""Web search through SearXNG running locally, then open the best pages.

Queries stay generic: Lea's name is stripped so it never reaches a search engine.
"""

from __future__ import annotations

import atexit
import os
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

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


def searxng(
    base_url: str,
    query: str,
    max_results: int = 5,
    language: str | None = None,
    get=httpx.get,
    engines: str | None = None,
) -> list[dict]:
    params = {"q": query, "format": "json", "safesearch": 0}
    if language:
        params["language"] = language
    if engines:
        params["engines"] = engines
    for attempt in range(2):
        try:
            # SearXNG allows each engine 7 s; this waits a little longer for its answer.
            r = get(f"{base_url.rstrip('/')}/search", params=params, timeout=10.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SearchUnavailable(f"SearXNG not reachable at {base_url}: {e}") from e
        data = r.json()
        # Measured on Lea's network: after a quiet spell, the first connection to each engine is so
        # slow that all of them time out; asking again right away works (the connections are open).
        unresponsive = data.get("unresponsive_engines") or []
        cold = not data.get("results") and unresponsive and all("timeout" in str(u) for u in unresponsive)
        if not cold or attempt:
            break
    out = []
    for item in data.get("results", []):
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


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def start_searxng(cfg) -> str:
    """Starts the local SearXNG (installed by scripts/install_searxng.sh) with Sylvie's server, so
    web search works after a restart without a Terminal tab (measured 2026-11-06: after the Mac
    restarted, search was silently off). It listens on 127.0.0.1 only (its settings file)."""
    u = urlparse(cfg.get_path("search.searxng_url", "http://127.0.0.1:8888"))
    host, port = u.hostname or "", u.port or 80
    if host not in ("127.0.0.1", "localhost"):
        return "not local"
    if _port_open(host, port):
        return "already running"
    svc = cfg.data_dir / "services"
    python, settings = svc / "searxng" / ".venv" / "bin" / "python", svc / "searxng-settings.yml"
    if not python.exists() or not settings.exists():
        return "not installed"
    (cfg.data_dir / "logs").mkdir(exist_ok=True)
    logf = open(cfg.data_dir / "logs" / "searxng.log", "a")  # noqa: SIM115 (lives as long as the process)
    proc = subprocess.Popen(
        [str(python), "-m", "searx.webapp"],
        cwd=svc / "searxng",
        env={**os.environ, "SEARXNG_SETTINGS_PATH": str(settings)},
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    atexit.register(proc.terminate)
    return "started"
