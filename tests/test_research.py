import json
import threading
import time

from core.llm import FakeLLM
from tools.research import plan, research

PLAN = json.dumps(
    {
        "parts": [
            {"question": "Price of A?", "query": "A price"},
            {"question": "Price of B?", "query": "B price"},
        ]
    }
)


def fake_search(delay=0.0, seen=None):
    lock = threading.Lock()

    def search(query):
        with lock:
            if seen is not None:
                seen.append(query)
        time.sleep(delay)
        body = f"[1] {query} result one\nURL: https://a.example/{query}\n...\n\n[2] {query} two\nURL: https://b.example/{query}\n..."
        return body, [
            {"n": 1, "title": f"{query} 1", "url": f"https://a.example/{query}"},
            {"n": 2, "title": f"{query} 2", "url": f"https://b.example/{query}"},
        ]

    return search


def test_plan_falls_back_to_the_whole_question():
    assert plan(FakeLLM(["not json"]), "What is X?") == [{"question": "What is X?", "query": "What is X?"}]
    assert len(plan(FakeLLM([PLAN]), "Compare A and B")) == 2


def test_searches_run_at_the_same_time_and_sources_are_numbered_across_parts():
    llm = FakeLLM([PLAN, "A costs 10 [1].", "B costs 12 [3]."])
    t0 = time.perf_counter()
    text, sources, stats = research("Compare A and B", llm, fake_search(delay=0.3), start=2)
    assert time.perf_counter() - t0 < 0.55  # two 0.3 s searches in parallel, not 0.6 s in a row
    assert stats == {"parts": 2}
    assert [s["n"] for s in sources] == [3, 4, 5, 6]  # after 2 earlier sources in this turn
    prompts = [c["messages"][0]["content"] for c in llm.calls[1:]]
    assert "[3] A price result one" in prompts[0] and "[5] B price result one" in prompts[1]
    assert "<untrusted>" in text and "A costs 10" in text and "B costs 12" in text


def test_research_tool_in_a_turn(tmp_path):
    import tools.registry as reg
    from tests.test_orchestrator import make, run

    call = "<tool_call>\n<function=research>\n<parameter=question>\nCompare A and B\n</parameter>\n</function>\n</tool_call>"
    s = make(tmp_path, [call, PLAN, "A costs 10 [1].", "B costs 12 [3].", "A is cheaper [1][3].", "{}"])
    s.cfg["search"] = {**s.cfg["search"], "enabled": True}
    original = reg.web_search
    reg.web_search = lambda q, **kw: fake_search()(q)
    try:
        ev = run(s, "Should I buy A or B?")
    finally:
        reg.web_search = original
    statuses = [e["text"] for e in ev if e["type"] == "status" and e.get("text")]
    assert any("splitting this into parts" in t for t in statuses)
    tool = next(e for e in ev if e["type"] == "tool")
    assert tool["name"] == "research" and tool["ok"]
    assert next(e for e in ev if e["type"] == "sources")["sources"][0]["url"].startswith("https://")
    assert "A is cheaper" in "".join(e["text"] for e in ev if e["type"] == "text")


def test_search_material_is_capped_and_tools_switch_off(tmp_path):
    import tools.registry as reg
    from tests.test_orchestrator import make, run

    call = "<tool_call>\n<function=web_search>\n<parameter=query>\nlaptops\n</parameter>\n</function>\n</tool_call>"
    s = make(tmp_path, [call, call, "Here's what I found.", "{}"])
    s.cfg["search"] = {**s.cfg["search"], "enabled": True}
    s.cfg["llm"] = {**s.cfg["llm"], "tool_budget_chars": 6000}
    original = reg.web_search
    reg.web_search = lambda q, **kw: ("x" * 5000, [{"n": 1, "title": "t", "url": "https://a.example/"}])
    try:
        ev = run(s, "Which laptop?")
    finally:
        reg.web_search = original
    third = s.llm.calls[2]
    assert third["tools"] is None  # budget used up: the last round must answer
    tool_msgs = [m["content"] for m in third["messages"] if m["role"] == "tool"]
    assert "trimmed" in tool_msgs[1] and sum(len(t) for t in tool_msgs) < 7000
    assert "Here's what I found." in "".join(e["text"] for e in ev if e["type"] == "text")


def test_an_empty_answer_becomes_an_honest_sentence(tmp_path):
    from tests.test_orchestrator import make, run

    s = make(tmp_path, ["", "{}"])
    ev = run(s, "¿Qué opinas de esto?")
    text = "".join(e["text"] for e in ev if e["type"] == "text")
    assert text.startswith("Perdona") and ev[-1]["type"] == "done"


def test_weather_question_is_looked_up_not_refused(tmp_path):
    """2026-11-08: "dime qué tiempo hará mañana en Toledo" got "No tengo acceso a servicios de clima"."""
    import tools.registry as reg
    from core.intent import looks_live, says_no_access
    from tests.test_orchestrator import make, run

    assert looks_live("Sylvie, dime que tiempo hará mañana en Toledo.")
    assert looks_live("what's on TV tonight?") and looks_live("quel temps fera-t-il demain ?")
    assert not looks_live("what is 17% of 2340?") and not looks_live("What is 15% of 80?")
    assert looks_live("¿A qué hora cierra Mercadona los domingos?")  # made up an hour without searching
    assert looks_live("How much does an iPhone 17 Pro cost in Spain?")
    assert says_no_access("No tengo acceso a servicios de clima en tiempo real.")
    assert says_no_access("I can't give you the current weather since I don't have access to a live service")
    assert not says_no_access("Mañana hará sol en Toledo, unos 18 grados.")

    call = "<tool_call>\n<function=web_search>\n<parameter=query>\ntiempo mañana Toledo\n</parameter>\n</function>\n</tool_call>"
    refusal = "No tengo acceso a servicios de clima en tiempo real."
    s = make(tmp_path, [refusal, call, "Mañana hará sol, unos 18 grados.", "{}"])
    s.cfg["search"] = {**s.cfg["search"], "enabled": True}
    original = reg.web_search
    reg.web_search = lambda q, **kw: (
        "Toledo mañana: soleado, 18 °C",
        [{"n": 1, "title": "t", "url": "https://a.example/"}],
    )
    try:
        ev = run(s, "Sylvie, dime qué tiempo hará mañana en Toledo.")
    finally:
        reg.web_search = original
    said = [m["content"] for m in s.llm.calls[-1]["messages"] if m["role"] == "user"]
    assert "call web_search first" in said[-2] and "web_search now" in said[-1]  # told, then sent back
    assert any(e["type"] == "tool" and e["name"] == "web_search" for e in ev)
    assert "18 grados" in "".join(e["text"] for e in ev if e["type"] == "text")


def test_wikipedia_is_not_enough_for_live_questions(tmp_path):
    """2026-11-08 (make everyday): looked in offline Wikipedia, then "I don't have access to real-time prices"."""
    import tools.registry as reg
    from tests.test_orchestrator import make, run

    wiki = "<tool_call>\n<function=calculator>\n<parameter=expression>\n1+1\n</parameter>\n</function>\n</tool_call>"
    call = "<tool_call>\n<function=web_search>\n<parameter=query>\niPhone 17 Pro price Spain\n</parameter>\n</function>\n</tool_call>"
    s = make(tmp_path, [wiki, "I don't have access to real-time prices.", call, "It costs 1.319 €.", "{}"])
    s.cfg["search"] = {**s.cfg["search"], "enabled": True}
    original = reg.web_search
    reg.web_search = lambda q, **kw: (
        "iPhone 17 Pro: 1.319 €",
        [{"n": 1, "title": "t", "url": "https://a.example/"}],
    )
    try:
        ev = run(s, "How much does an iPhone 17 Pro cost in Spain?")
    finally:
        reg.web_search = original
    assert [e["name"] for e in ev if e["type"] == "tool"] == ["calculator", "web_search"]
    assert "1.319" in "".join(e["text"] for e in ev if e["type"] == "text")


def test_empty_answer_after_searching_gets_one_more_try(tmp_path):
    """2026-11-08 (make everyday): "¿va a llover este fin de semana?" → four searches, then nothing."""
    from tests.test_orchestrator import TOOL_CALL, make, run

    s = make(tmp_path, [TOOL_CALL, "", "Son 397,80 €.", "{}"])
    ev = run(s, "¿Cuánto es el 17% de 2340 euros?")
    text = "".join(e["text"] for e in ev if e["type"] == "text")
    assert "397,80" in text and "perdido" not in text
    assert "Answer Lea now in Spanish" in s.llm.calls[2]["messages"][-1]["content"]
    assert s.llm.calls[2]["tools"] is None
