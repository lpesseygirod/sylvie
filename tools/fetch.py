"""Opens a web page and extracts readable text. Page content is untrusted data.

Safety: only http(s), and never local or private addresses — otherwise a malicious page or
search result could make Sylvie call her own API or other services on Lea's network.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

MAX_BYTES = 2_000_000
TIMEOUT = 6.0
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"
)


class BlockedURL(ValueError):
    pass


def check_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise BlockedURL("only http(s) URLs are allowed")
    host = p.hostname
    if host in ("localhost",) or host.endswith((".local", ".localhost", ".internal")):
        raise BlockedURL("local addresses are not allowed")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise BlockedURL(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise BlockedURL("private or local network addresses are not allowed")


class _TextExtractor(HTMLParser):
    SKIP = {
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "svg",
        "iframe",
        "button",
    }
    BLOCK = {
        "p",
        "div",
        "br",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "section",
        "article",
        "pre",
        "blockquote",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    # Drop very short lines (menus, buttons) but keep headings-ish lines with some content.
    lines = [ln for ln in lines if len(ln) > 25 or (len(ln) > 3 and ln.endswith((".", ":", "?", "!")))]
    return p.title.strip(), "\n".join(lines)


def fetch_page(url: str, max_chars: int = 6000, client: httpx.Client | None = None) -> dict:
    check_url(url)
    own = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    )
    try:
        current = url
        for _ in range(4):  # follow redirects manually so each hop is checked
            r = client.get(current)
            if r.is_redirect and "location" in r.headers:
                current = str(r.url.join(r.headers["location"]))
                check_url(current)
                continue
            break
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        body = r.content[:MAX_BYTES]
        if "html" in ctype or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            title, text = html_to_text(body.decode(r.encoding or "utf-8", errors="replace"))
        elif ctype.startswith("text/"):
            title, text = "", body.decode(r.encoding or "utf-8", errors="replace")
        else:
            raise ValueError(f"unsupported content type {ctype}")
        return {"url": current, "title": title, "text": text[:max_chars]}
    finally:
        if own:
            client.close()
