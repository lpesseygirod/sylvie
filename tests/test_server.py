import json

import pytest
from fastapi.testclient import TestClient

from core.server import create_app
from tests.test_orchestrator import extraction, make

TOKEN = "test-token-123"
BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path):
    s = make(tmp_path, ["Hello Lea!", extraction()] * 5)
    app = create_app(sylvie=s, token=TOKEN, warm=False)
    with TestClient(app, base_url=BASE) as c:
        yield c


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_api_requires_token(client):
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/conversations", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/conversations", headers=AUTH).status_code == 200


def test_foreign_origin_rejected_even_with_token(client):
    r = client.get("/api/conversations", headers={**AUTH, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_dns_rebinding_host_rejected(client):
    r = client.get("/api/health", headers={**AUTH, "Host": "evil.example:8765"})
    assert r.status_code == 403


def test_ui_locked_without_cookie_and_auth_sets_cookie(client):
    assert client.get("/").status_code == 401
    assert client.get("/auth", params={"t": "nope"}, follow_redirects=False).status_code == 401
    r = client.get("/auth", params={"t": TOKEN}, follow_redirects=False)
    assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower()


def test_chat_streams_and_history(client):
    r = client.post("/api/chat", json={"text": "hi"}, headers=AUTH)
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "meta" and events[-1]["type"] == "done"
    cid = events[0]["conversation_id"]
    msgs = client.get(f"/api/conversations/{cid}/messages", headers=AUTH).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    mid = msgs[1]["id"]
    assert client.post(f"/api/messages/{mid}/feedback", json={"value": 1}, headers=AUTH).json()["ok"]
    assert client.post(f"/api/messages/{mid}/edit", json={"text": "Hi Lea :)"}, headers=AUTH).json()["ok"]
    assert client.patch(f"/api/conversations/{cid}", json={"title": "Greeting"}, headers=AUTH).json()["ok"]
    assert (
        client.get("/api/conversations", params={"search": "Greeting"}, headers=AUTH).json()[0]["id"] == cid
    )
    assert client.delete(f"/api/conversations/{cid}", headers=AUTH).json()["ok"]
    assert client.get("/api/conversations", headers=AUTH).json() == []


def test_upload_text(client):
    r = client.post("/api/upload", files={"file": ("notes.txt", b"hello notes")}, headers=AUTH)
    assert r.json() == {"name": "notes.txt", "text": "hello notes", "chars": 11}
