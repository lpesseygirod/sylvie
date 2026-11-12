"""Finds what to play on Spotify by name, so Sylvie can start it herself (I told her: "no, you click it,
it's your job"). The Spotify app on the Mac can only play a known spotify: URI; finding one from words
needs Spotify's Web API search.

Uses the "client credentials" flow: no login to Lea's account, only search. Lea creates a free app at
developer.spotify.com once and stores its ID and secret in the Keychain with `make spotify`; Sylvie
never sees or logs them. What's sent to Spotify: only the search words (Lea's name is removed).
"""

from __future__ import annotations

import base64
import re
import threading
import time

import httpx

SERVICE = "Sylvie"
ID_ACCOUNT, SECRET_ACCOUNT = "spotify-client-id", "spotify-client-secret"

_lock = threading.Lock()
_token: tuple[str, float] | None = None  # (access token, expires at)


def credentials() -> tuple[str, str] | None:
    try:
        import keyring

        cid, secret = keyring.get_password(SERVICE, ID_ACCOUNT), keyring.get_password(SERVICE, SECRET_ACCOUNT)
    except Exception:
        return None
    return (cid, secret) if cid and secret else None


def _access_token(creds: tuple[str, str], post=httpx.post) -> str:
    global _token
    with _lock:
        if _token and _token[1] > time.time() + 60:
            return _token[0]
        basic = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
        r = post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        _token = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
        return _token[0]


def kind_for(query: str) -> str:
    """What Lea most likely means: a playlist, an album, an artist, or (by default) a song."""
    q = query.lower()
    if re.search(r"\b(playlist|lista)\b", q):
        return "playlist"
    if re.search(r"\b(album|álbum)\b", q):
        return "album"
    if re.search(r"\b(artist|artista|band|grupo|songs by|canciones de|musique de)\b", q):
        return "artist"
    return "track"


def search_uri(query: str, creds: tuple[str, str], get=httpx.get, post=httpx.post) -> tuple[str, str] | None:
    """(spotify: URI, "name — by") of the best match, or None."""
    kind = kind_for(query)
    words = re.sub(
        r"\b(playlist|lista|album|álbum|artist|artista|songs by|canciones de)\b", " ", query, flags=re.I
    )
    words = re.sub(r"\s+", " ", words).strip() or query
    r = get(
        "https://api.spotify.com/v1/search",
        params={"q": words, "type": kind, "limit": 1},
        headers={"Authorization": f"Bearer {_access_token(creds, post)}"},
        timeout=8,
    )
    r.raise_for_status()
    items = (r.json().get(f"{kind}s") or {}).get("items") or []
    item = next((i for i in items if i), None)
    if not item:
        return None
    by = ""
    if item.get("artists"):
        by = " — " + ", ".join(a["name"] for a in item["artists"][:2])
    elif item.get("owner"):
        by = " — " + item["owner"].get("display_name", "")
    return item["uri"], f"{item.get('name', '')}{by}"
