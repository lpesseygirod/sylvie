from core.config import Config
from core.db import Database
from core.shortcuts import MIN_REPEATS, Shortcuts
from knowledge.embeddings import HashEmbedder
from tests.test_orchestrator import make, run


def store(tmp_path, enabled=True):
    cfg = Config({"shortcuts": {"enabled": enabled}})
    return Shortcuts(Database(tmp_path / "s.db", dim=64), HashEmbedder(64), cfg)


def test_a_request_becomes_a_shortcut_after_repeats(tmp_path):
    s = store(tmp_path)
    q = "what's on my calendar today?"
    for _ in range(MIN_REPEATS - 1):
        assert s.record(q, "calendar_events", {"day_offset": 0}) is None
    assert s.find(q) is None
    sc = s.record(q, "calendar_events", {"day_offset": 0})
    assert sc is not None and s.find(q).tool == "calendar_events"
    assert s.record(q, "calendar_events", {"day_offset": 0}) is None  # not made twice
    assert s.find("tell me a joke about cats") is None


def test_only_read_only_tools_without_dates(tmp_path):
    s = store(tmp_path)
    for _ in range(MIN_REPEATS + 1):
        assert s.record("play my focus playlist", "play_music", {"query": "focus"}) is None
        assert s.record("events on the 7th", "calendar_events", {"day": "2026-11-07"}) is None
    assert s.list() == []


def test_lea_can_switch_one_off_or_all(tmp_path):
    s = store(tmp_path)
    q = "news today"
    for _ in range(MIN_REPEATS):
        s.record(q, "web_search", {"query": "news today"})
    sid = s.list()[0]["id"]
    s.set_status(sid, "off")
    assert s.find(q) is None
    for _ in range(MIN_REPEATS):
        s.record(q, "web_search", {"query": "news today"})
    assert len(s.list()) == 1 and s.list()[0]["status"] == "off"  # stays off
    (tmp_path / "x").mkdir()
    off = store(tmp_path / "x", enabled=False)
    for _ in range(MIN_REPEATS):
        off.record(q, "web_search", {"query": "news today"})
    assert off.find(q) is None


def test_turn_starts_the_lookup_before_the_model(tmp_path):
    s = make(tmp_path, ["You have nothing today.", "{}"])
    q = "what's on my calendar today?"
    for _ in range(MIN_REPEATS):
        s.shortcuts.record(q, "calendar_events", {"day_offset": 0})
    ev = run(s, q)
    tool = next(e for e in ev if e["type"] == "tool")
    assert tool["name"] == "calendar_events" and tool.get("shortcut") is True
    first_call = s.llm.calls[0]["messages"]
    assert first_call[-1]["role"] == "tool" and "Lea often asks this" in first_call[-1]["content"]
    assert s.shortcuts.list()[0]["uses"] == 1
    assert ev[-1]["type"] == "done"


def test_shortcut_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from core.server import create_app
    from tests.test_server import AUTH, BASE, TOKEN

    s = make(tmp_path, [])
    for _ in range(MIN_REPEATS):
        s.shortcuts.record("what's on my calendar today?", "calendar_events", {"day_offset": 0})
    with TestClient(create_app(sylvie=s, token=TOKEN, warm=False), base_url=BASE) as c:
        items = c.get("/api/shortcuts", headers=AUTH).json()["shortcuts"]
        assert items[0]["tool"] == "calendar_events" and items[0]["status"] == "on"
        assert c.post(f"/api/shortcuts/{items[0]['id']}", json={"status": "off"}, headers=AUTH).json()["ok"]
        assert c.get("/api/shortcuts", headers=AUTH).json()["shortcuts"][0]["status"] == "off"
        assert (
            c.post(f"/api/shortcuts/{items[0]['id']}", json={"status": "maybe"}, headers=AUTH).status_code
            == 400
        )
