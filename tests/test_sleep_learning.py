"""Sleep-time learning (efficiency plan A5) and the small-jobs model picker (A3)."""

import json
import threading

from core.small_model import AppleModel, SmallJobs
from tests.test_orchestrator import extraction, make, run


def quiet_later(s):
    """Hold sleep-time learning back (as if Lea were still chatting), like the real 5-minute wait."""
    s.bg.sleep_idle_seconds = 3600


def test_one_extraction_per_conversation_once_quiet(tmp_path):
    s = make(tmp_path, ["Nice!", "Sure.", "Noted."])
    quiet_later(s)
    cid = run(s, "I study law at IE")[0]["conversation_id"]
    run(s, "my sister Ana lives in Lyon", cid=cid)
    run(s, "what's the weather there?", cid=cid)
    assert s.memory.list() == [] and len(s.llm.calls) == 3  # nothing learned while she chats
    s.llm.replies += [
        extraction(
            [
                {"key": "lea.studies", "fact": "Lea studies law at IE", "category": "studies"},
                {"key": "lea.sister", "fact": "Lea's sister Ana lives in Lyon", "category": "people"},
            ]
        )  # fmt: skip
    ]
    s.bg.sleep_idle_seconds = 0  # she's been quiet long enough
    s.bg.drain()
    learn_calls = s.llm.calls[3:]
    assert len(learn_calls) == 1  # one call for the three messages
    prompt = learn_calls[0]["messages"][-1]["content"]
    assert "[1] I study law at IE" in prompt and "[3] what's the weather" in prompt and "numbered" in prompt
    assert sorted(f.text for f in s.memory.list()) == [
        "Lea studies law at IE",
        "Lea's sister Ana lives in Lyon",
    ]
    s.bg.drain()
    assert len(s.llm.calls) == 4 and s.unlearned() == []  # learned once, not again


def test_remember_messages_are_read_alone(tmp_path):
    s = make(tmp_path, ["Ok.", "Ok."])
    quiet_later(s)
    cid = run(s, "I like jazz")[0]["conversation_id"]
    run(s, "remember that I take medication for asthma", cid=cid)
    sensitive = {
        "key": "lea.health",
        "fact": "Lea takes asthma medication",
        "category": "other",
        "sensitive": True,
    }
    s.llm.replies += [extraction([sensitive]), extraction(interests=["jazz"])]
    s.bg.sleep_idle_seconds = 0
    s.bg.drain()
    prompts = [c["messages"][-1]["content"] for c in s.llm.calls[2:]]
    assert len(prompts) == 2 and "[1]" not in prompts[0] and "asthma" in prompts[0]
    assert [f.text for f in s.memory.list()] == ["Lea takes asthma medication"]  # allowed: she asked


def test_sensitive_fact_from_a_batch_is_dropped(tmp_path):
    s = make(tmp_path, ["Ok.", "Ok."])
    quiet_later(s)
    cid = run(s, "I've been anxious lately")[0]["conversation_id"]
    run(s, "summarise this article", cid=cid)
    bad = {"key": "lea.mood", "fact": "Lea is anxious", "category": "other", "sensitive": True}
    s.llm.replies += [extraction([bad])]
    s.bg.sleep_idle_seconds = 0
    s.bg.drain()
    assert s.memory.list() == []  # rule 13: no "remember", no sensitive fact


def test_interrupted_learning_is_kept_for_the_next_quiet_spell(tmp_path):
    s = make(tmp_path, ["Ok."])
    quiet_later(s)
    run(s, "I play padel on Tuesdays")
    stop = threading.Event()
    stop.set()
    s.sleep_learn(stop)  # Lea came back before it could run
    assert len(s.unlearned()) == 1
    s.llm.replies += [
        extraction([{"key": "lea.sport", "fact": "Lea plays padel on Tuesdays", "category": "other"}])
    ]
    s.sleep_learn(threading.Event())
    assert s.unlearned() == [] and [f.text for f in s.memory.list()] == ["Lea plays padel on Tuesdays"]


def test_messages_before_sleep_learning_are_not_relearned(tmp_path):
    from core.db import Database
    from core.llm import FakeLLM
    from core.orchestrator import Sylvie
    from knowledge.embeddings import HashEmbedder

    s = make(tmp_path, ["Ok.", extraction()])
    run(s, "I like green tea a lot")
    s.bg.drain()
    db = Database(tmp_path / "s.db", dim=256)
    s2 = Sylvie(s.cfg, FakeLLM([]), HashEmbedder(256), db, idle_seconds=0, actions=s.actions)
    assert s2.unlearned() == []  # restart: the cursor is kept


def test_small_jobs_use_sylvie_until_apple_passed_the_check(tmp_path):
    s = make(tmp_path, [])

    class FakeApple(AppleModel):
        def __init__(self):
            super().__init__(helper=tmp_path / "none")
            self.ok = True

        def status(self):
            return self.ok, "test"

        def complete(self, messages, **kw):
            return "apple"

    apple = FakeApple()
    small = SmallJobs(s.cfg, s.llm, apple)
    assert small.choice() == "sylvie"  # no verdict yet
    (s.cfg.data_dir).mkdir(parents=True, exist_ok=True)
    (s.cfg.data_dir / "small-model.json").write_text(json.dumps({"passed": True}))
    assert small.choice() == "apple" and small.complete([{"role": "user", "content": "x"}]) == "apple"
    apple.ok = False  # Apple Intelligence switched off again
    assert small.choice() == "sylvie"
    s.cfg["learning"] = {**s.cfg["learning"], "small_model": "sylvie"}
    apple.ok = True
    assert small.choice() == "sylvie"


def test_apple_model_reports_a_missing_helper(tmp_path):
    ok, why = AppleModel(helper=tmp_path / "missing").status()
    assert not ok and "make small-model" in why


def test_extraction_check_scoring():
    sys_path = __import__("sys").path
    from pathlib import Path

    sys_path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import extraction_check as ec

    items = ec.load_items()
    assert len(items) >= 16 and all(i["prompt"] for i in items)  # "prompt": kept out of training data
    by = {i["id"]: i for i in items}
    assert ec.score(by["ext-3"], ["Lea's sister Ana lives in Lyon"]) == (True, "")
    assert not ec.score(by["ext-3"], ["Lea's sister is Ana"])[0]
    assert "forbidden" in ec.score(by["ext-4"], ["Lea feels anxious"])[1]
    assert not ec.score(by["ext-2"], ["Lea likes lofi"])[0]
