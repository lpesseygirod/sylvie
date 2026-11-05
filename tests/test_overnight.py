import json
import random

from core.config import Config
from core.db import Database
from core.llm import FakeLLM
from learning.overnight import Item, ModelPool, Overnight, adjusted, code_check, length_penalty
from learning.store import LearningStore


class Pool(ModelPool):
    def __init__(self, sylvie, judge):
        self.models = {"sylvie": sylvie, "judge": judge}
        self.role, self.model, self.swaps = None, None, 0

    def use(self, role):
        if role != self.role:
            self.swaps += 1
        self.role, self.model = role, self.models[role]
        return self.model


def night(tmp_path, sylvie_replies, judge_replies, questions):
    cfg = Config({"paths": {"data_dir": str(tmp_path)}, "llm": {"model": "org/Qwen-test"}})
    db = Database(tmp_path / "n.db", dim=8)
    store = LearningStore(db)
    o = Overnight(
        cfg, store, db, Pool(FakeLLM(sylvie_replies), FakeLLM(judge_replies)), "You are Sylvie.", seed=1
    )
    o.questions = lambda n: questions
    return o, store


def judge(score):
    return json.dumps({"score": score, "worst_criterion": "correct", "feedback": "fix it"})


def test_length_penalty_and_code_check():
    assert length_penalty("word " * 100) == 0 and length_penalty("word " * 350) == 2.0
    assert adjusted(9, "word " * 350) == 7.0
    it = Item("x", "q", "checkable", expected=397.8)
    assert code_check(it, "It's 397,80 €") and code_check(it, "about 400") is False


def test_wrong_number_is_fixed_and_staged(tmp_path):
    q = Item("c1", "What's 17% of 2340?", "checkable", expected=397.8)
    o, store = night(tmp_path, ["It's 380.", "It's 397.80."], [judge(9), judge(8)], [q])
    kept = o.run_batch(1)
    assert [k.better for k in kept] == ["It's 397.80."]
    assert store.examples() == []  # staged, not trainable yet
    staged = store.examples(status="staged")
    assert staged[0]["source"] == "sylvie-local:Qwen-test" and staged[0]["rejected"] == "It's 380."
    summary = json.loads(o.morning_summary().read_text())
    assert summary["stats"]["kept"] == 1 and summary["spot_check"][0]["after"] == "It's 397.80."
    store.set_batch_status(o.batch, "ready")
    assert len(store.examples()) == 1


def test_good_answers_are_left_alone_and_bloat_is_rejected(tmp_path):
    good = Item("o1", "Give me a tip to focus", "oasst")
    weak = Item("o2", "Explain ETFs", "oasst")
    long_rewrite = "ETFs are " + "very " * 400
    o, store = night(
        tmp_path,
        ["Pomodoro: 25 min on, 5 off.", "ETF = fund.", long_rewrite],
        [judge(9), judge(4), judge(10)],
        [good, weak],
    )
    assert o.run_batch(2) == []  # the rewrite was much longer: not a clear improvement
    assert o.stats == {"questions": 2, "rewritten": 1, "kept": 0, "batches": 1}


def test_stop_flag_stops_quickly(tmp_path):
    o, _ = night(tmp_path, ["a"], [judge(9)], [Item("o1", "hello there friend", "oasst")])
    o.stop = True
    assert o.run_batch(1) == [] and o.stats["batches"] == 0


def test_rubric_rotation_changes_order():
    from learning.overnight import CRITERIA

    assert len(CRITERIA) == 9 and CRITERIA[0] == "correct"
    a, b = CRITERIA[:], CRITERIA[:]
    random.Random(1).shuffle(a)
    random.Random(2).shuffle(b)
    assert a != b
