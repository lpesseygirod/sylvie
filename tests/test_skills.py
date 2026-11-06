import json

from core.config import Config
from core.db import Database
from core.llm import FakeLLM
from learning.dataset import build
from learning.skills import practice_skills
from learning.store import LearningStore

CALL = "<tool_call>\n<function=calculator>\n<parameter=expression>\n48 + 48/2\n</parameter>\n</function>\n</tool_call>"


def data(tmp_path):
    p = tmp_path / "gsm.jsonl"
    rows = [
        {
            "question": "Natalia sold 48 clips in April and half as many in May. How many in total?",
            "answer": "72",
        }
    ] * 2
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_tool_and_reasoning_examples_are_verified_and_built(tmp_path):
    store = LearningStore(Database(tmp_path / "s.db", dim=8))
    cfg = Config({"search": {"enabled": False}})
    llm = FakeLLM([CALL, "She sold 72 clips.", "48 + 24 = 72</think>72 clips."])
    kept = practice_skills(llm, store, cfg, data(tmp_path), n=2, batch="b", model_name="Q", seed=0)
    assert kept == {"solved": 2, "tried": 2, "examples": 3}
    assert store.examples(status="staged") == []  # verified by code → ready without Lea's spot-check (T28)
    ready = store.examples(status="ready")
    decision, answer, reasoning = ready
    # the decision to call the calculator is the trained (last) message of its own example
    assert [m["role"] for m in decision["messages"]] == ["user"]
    assert json.loads(decision["tool_calls"])[0]["function"]["name"] == "calculator"
    msgs = answer["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool"] and msgs[2]["content"] == "72"
    assert reasoning["reasoning"] == "48 + 24 = 72" and reasoning["tools"]  # calculator offered, not needed
    rep = build(store, tmp_path / "builds", name="x")
    rows = [json.loads(line) for line in (rep.path / "train.jsonl").read_text().splitlines()] + [
        json.loads(line) for line in (rep.path / "valid.jsonl").read_text().splitlines()
    ]
    assert all("tools" in r for r in rows)
    assert any(r["messages"][-1].get("tool_calls") for r in rows)
    assert any(r["messages"][-1].get("reasoning_content") == "48 + 24 = 72" for r in rows)


def test_thinking_mode_can_use_the_calculator(tmp_path):
    store = LearningStore(Database(tmp_path / "s.db", dim=8))
    llm = FakeLLM(["She sold 72 clips.", "48 and half of it</think>" + CALL, "72 clips in total."])
    kept = practice_skills(llm, store, Config({}), data(tmp_path), n=2, batch="b", model_name="Q", seed=0)
    assert kept == {"solved": 1, "tried": 2, "examples": 2}  # plain mode without the tool isn't kept
    decision = store.examples()[0]
    assert decision["reasoning"] == "48 and half of it" and decision["tool_calls"]


def test_wrong_answers_are_dropped(tmp_path):
    store = LearningStore(Database(tmp_path / "s.db", dim=8))
    llm = FakeLLM([CALL, "She sold 70 clips.", "hmm</think>About 70."])
    kept = practice_skills(llm, store, Config({}), data(tmp_path), n=2, batch="b", model_name="Q", seed=0)
    assert kept == {"solved": 0, "tried": 2, "examples": 0} and store.examples(status=None) == []


def test_verified_examples_can_wait_for_lea_instead(tmp_path):
    store = LearningStore(Database(tmp_path / "s.db", dim=8))
    cfg = Config({"search": {"enabled": False}, "learning": {"auto_approve_verified": False}})
    llm = FakeLLM([CALL, "She sold 72 clips.", "48 + 24 = 72</think>72 clips."])
    practice_skills(llm, store, cfg, data(tmp_path), n=2, batch="b", model_name="Q", seed=0)
    assert len(store.examples(status="staged")) == 3 and store.examples() == []


def test_answers_opening_with_filler_are_not_kept(tmp_path):
    store = LearningStore(Database(tmp_path / "s.db", dim=8))
    cfg = Config({"search": {"enabled": False}})
    llm = FakeLLM([CALL, "Let me calculate this. She sold 72 clips.", "48 + 24 = 72</think>Sure! 72 clips."])
    kept = practice_skills(llm, store, cfg, data(tmp_path), n=2, batch="b", model_name="Q", seed=0)
    assert kept == {"solved": 0, "tried": 2, "examples": 0}
