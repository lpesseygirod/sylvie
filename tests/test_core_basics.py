from datetime import datetime
from zoneinfo import ZoneInfo

from core.persona import build_system_prompt, build_turn_context
from core.toolcall import StreamSplitter, parse_tool_calls
from core.trace import Trace


def test_parse_qwen_xml_tool_call():
    text = (
        "Let me check.\n<tool_call>\n<function=web_search>\n<parameter=query>\nweather Madrid\n</parameter>\n"
        "<parameter=max_results>\n3\n</parameter>\n</function>\n</tool_call>"
    )
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "web_search"
    assert calls[0].arguments == {"query": "weather Madrid", "max_results": 3}


def test_parse_json_fallback_and_invalid():
    calls = parse_tool_calls(
        '<tool_call>{"name": "calculator", "arguments": {"expression": "1+1"}}</tool_call>'
    )
    assert calls[0].name == "calculator" and calls[0].arguments["expression"] == "1+1"
    assert parse_tool_calls("<tool_call>garbage</tool_call>")[0].name == "__invalid__"


def test_splitter_separates_thinking_text_and_hides_tool_calls():
    sp = StreamSplitter(thinking=True)
    raw = "I should search.</think>\n\nOne moment.<tool_call>\n<function=web_search>\n<parameter=query>\nx\n</parameter>\n</function>\n</tool_call>"
    events = []
    for i in range(0, len(raw), 3):  # feed in awkward small pieces to test partial tags
        events += sp.feed(raw[i : i + 3])
    events += sp.flush()
    assert sp.thinking_text == "I should search."
    assert sp.visible_text == "One moment."
    assert "tool_call" not in "".join(s for k, s in events if k == "text")
    assert sp.tool_calls[0].arguments == {"query": "x"}


def test_system_prompt_is_stable_within_a_day():
    a = build_system_prompt(today=datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Europe/Madrid")))
    b = build_system_prompt(today=datetime(2026, 11, 5, 23, 59, tzinfo=ZoneInfo("Europe/Madrid")))
    assert a == b
    assert "Sylvie" in a and "Madrid" in a and "Thursday 5 November 2026" in a
    assert "{" not in a  # every placeholder filled


def test_turn_context_has_time_and_memories():
    ctx = build_turn_context(memories=["Lea studies CS + BBA"], when=datetime(2026, 11, 5, 18, 30))
    assert "18:30" in ctx and "Lea studies CS + BBA" in ctx


def test_trace_records_stages():
    t = Trace()
    with t.stage("recall"):
        pass
    t.mark("first_token")
    d = t.to_dict()
    assert "recall" in d["stages_ms"] and "first_token" in d["marks_ms"]
