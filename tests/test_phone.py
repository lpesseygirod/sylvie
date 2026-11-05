import pytest
from fastapi.testclient import TestClient

from core.server import create_app
from tests.test_orchestrator import extraction, make

LOCAL = {"Authorization": "Bearer local-token"}
TS_HOST = "lea-mac.tail1234.ts.net"


@pytest.fixture
def setup(tmp_path):
    s = make(
        tmp_path, ["Mañana hará sol, unos 18 grados. Lleva gafas de sol.\n\nDetalles: …", extraction()] * 3
    )
    s.cfg["phone"] = {"allowed_hosts": [TS_HOST]}
    app = create_app(sylvie=s, token="local-token", warm=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        token = c.post("/api/devices", json={"name": "Lea's iPhone"}, headers=LOCAL).json()["token"]
        yield c, s, token


def test_paired_device_can_ask_through_the_tunnel(setup):
    c, s, token = setup
    r = c.post(
        "/phone/ask",
        json={"text": "¿Qué tiempo hará mañana?"},
        headers={"Authorization": f"Bearer {token}", "Host": TS_HOST},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["spoken"] == "Mañana hará sol, unos 18 grados. Lleva gafas de sol."
    assert "Detalles" in body["answer"]
    assert s.convs.visible_messages(body["conversation_id"])[0]["content"] == "¿Qué tiempo hará mañana?"


def test_unpaired_device_is_rejected(setup):
    c, _, _ = setup
    for auth in [None, "Bearer sylv_madeup", "Bearer local-token"]:
        headers = {"Host": TS_HOST} | ({"Authorization": auth} if auth else {})
        assert c.post("/phone/ask", json={"text": "hi"}, headers=headers).status_code == 401


def test_device_token_does_not_open_the_local_api(setup):
    c, _, token = setup
    assert c.get("/api/memory", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert (
        c.get("/api/memory", headers={"Authorization": f"Bearer {token}", "Host": TS_HOST}).status_code == 403
    )


def test_revoked_device_and_browser_origin_rejected(setup):
    c, s, token = setup
    auth = {"Authorization": f"Bearer {token}", "Host": TS_HOST}
    assert c.get("/phone/ping", headers=auth).status_code == 200
    assert c.get("/phone/ping", headers={**auth, "Origin": "https://evil.example"}).status_code == 403
    s.devices.revoke(s.devices.list()[0].id)
    assert c.get("/phone/ping", headers=auth).status_code == 401


def test_follow_up_from_phone_continues_recent_conversation(setup):
    c, _, token = setup
    auth = {"Authorization": f"Bearer {token}", "Host": TS_HOST}
    a = c.post("/phone/ask", json={"text": "hola"}, headers=auth).json()["conversation_id"]
    b = c.post("/phone/ask", json={"text": "¿y pasado mañana?"}, headers=auth).json()["conversation_id"]
    assert a == b


def test_unknown_host_rejected_even_for_devices(setup):
    c, _, token = setup
    assert (
        c.get("/phone/ping", headers={"Authorization": f"Bearer {token}", "Host": "evil.example"}).status_code
        == 403
    )
