import json

import pytest

from core.settings import BadValue, apply_saved, change, describe
from tests.test_orchestrator import make, run


def test_values_are_checked(tmp_path):
    s = make(tmp_path, [])
    with pytest.raises(BadValue):
        change(s, "voice_en", "darth vader")
    with pytest.raises(BadValue):
        change(s, "speech_speed", "5")
    with pytest.raises(BadValue):
        change(s, "server_host", "0.0.0.0")  # security settings can't be changed by asking
    assert "unknown voice" in str(pytest.raises(BadValue, change, s, "voice_fr", "dora").value)


def test_change_applies_now_and_survives_a_restart(tmp_path):
    s = make(tmp_path, [])
    s._tts = object()
    msg = change(s, "voice_en", "Bella")
    assert s.cfg.get_path("voice.kokoro_voices.en") == "af_bella" and s._tts is None and "saved" in msg
    change(s, "speech_speed", "1,2")
    change(s, "web_search", "off")
    saved = json.loads((s.cfg.data_dir / "state.json").read_text())["settings"]["overrides"]
    assert saved == {"voice.kokoro_voices.en": "af_bella", "voice.speed": 1.2, "search.enabled": False}
    (tmp_path / "again").mkdir()
    fresh = make(tmp_path / "again", [])
    apply_saved(fresh.cfg, {"settings": {"overrides": saved}})
    assert fresh.cfg.get_path("voice.speed") == 1.2 and fresh.cfg.get_path("search.enabled") is False
    assert "voice_en = bella" in describe(s.cfg)


def test_switching_model_unloads_and_points_to_the_other(tmp_path):
    s = make(tmp_path, [])
    s.llm.model_id = "mlx-community/Qwen3.5-9B-4bit"
    change(s, "model", "smart")
    assert s.llm.model_id == "mlx-community/Qwen3.8-27B-4bit"


def test_lea_asks_sylvie_to_change_her_voice(tmp_path):
    call = (
        "<tool_call>\n<function=change_setting>\n<parameter=name>\nvoice_es\n</parameter>\n"
        "<parameter=value>\nalex\n</parameter>\n</function>\n</tool_call>"
    )
    s = make(tmp_path, [call, "Hecho: ahora hablo con la voz de Alex.", "{}"])
    ev = run(s, "Sylvie, cambia tu voz en español a Alex")
    tool = next(e for e in ev if e["type"] == "tool")
    assert tool["name"] == "change_setting" and tool["ok"]
    assert s.cfg.get_path("voice.kokoro_voices.es") == "em_alex"


def test_setting_requests_get_a_reminder_to_really_change_it(tmp_path):
    from core.settings import looks_like_setting_request

    assert looks_like_setting_request("Vuelve a hablar a velocidad normal")
    assert looks_like_setting_request("speak slower please")
    assert looks_like_setting_request("désactive la recherche web")
    assert not looks_like_setting_request("¿Qué tiempo hace en Madrid?")
    s = make(tmp_path, ["Vale.", "{}"])
    run(s, "Vuelve a hablar a velocidad normal")
    assert "you MUST call change_setting" in s.llm.calls[0]["messages"][-1]["content"]
