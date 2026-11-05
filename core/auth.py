"""The local API token: random, stored in the macOS Keychain, shared with the Mac app (rule 2, rule 5)."""

from __future__ import annotations

import os
import secrets

SERVICE = "Sylvie"
ACCOUNT = "local-api-token"


def get_or_create_token() -> str:
    # Tests (and only tests) may inject a token through the environment.
    env = os.environ.get("SYLVIE_TEST_TOKEN")
    if env:
        return env
    import keyring

    token = keyring.get_password(SERVICE, ACCOUNT)
    if not token:
        token = secrets.token_urlsafe(32)
        keyring.set_password(SERVICE, ACCOUNT, token)
    return token


def tokens_equal(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and secrets.compare_digest(a, b)
