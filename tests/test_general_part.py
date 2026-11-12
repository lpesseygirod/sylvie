"""The general (shareable) part of Sylvie stays free of Lea."""

import json
import random
import sys
import time
from pathlib import Path

import pytest

from core.config import Config, load_config
from core.db import Database
from learning.dataset import build
from learning.general_pack import LeakFound, export, import_pack, shareable_adapter
from learning.overnight import Item, Overnight, chosen_letter, code_check, mmlu_questions
from learning.privacy import PersonalTerms
from learning.store import LearningStore

FACTS = [
    "Lea's legal name is Lea Moreau-Laval Ortega.",
    "Lea's current mobile phone number is +34 655 010 203.",
    "Lea intends to purchase the domain lunafloristeria.com to host a website.",
    "Lea completed Step 1.2 of the Kore project on Monday.",
    "Lea's friend Bruno lives in Madrid.",
]


def terms(common=("Madrid", "Step", "Monday")):
    return PersonalTerms.build("Lea", FACTS, set(common))


@pytest.mark.parametrize(
    "text",
    [
        "Hola Lea, aquí tienes la receta.",
        "Call me on +34 655 010 203",
        "Write to someone@example.com",
        "Your site lunafloristeria.com is down",
        "How is the Kore app going?",
        "Say hi to Bruno",
        "Ms Moreau-Laval, your order",
        "Ortega said yes",
    ],
)
def test_lea_is_found(text):
    assert terms().find(text)


@pytest.mark.parametrize(
    "text",
    [
        "Que lea el libro antes del examen.",  # Spanish verb, not her name
        "Madrid is the capital of Spain.",  # her city, but generic
        "Step 1: mix the flour on Monday.",
        "100 - 19 - 50 - 25 = 6 people",
        "The year 2026-11-07 and 12.5 GB",
        "Visit example.com for docs",
    ],
)
def test_generic_text_is_not_personal(text):
    assert terms().find(text) is None


def store_with_examples(tmp_path):
    db = Database(tmp_path / "s.db", dim=8)
    st = LearningStore(db)
    src, lic = "sylvie-local:test", "apache-2.0"
    st.add_example([{"role": "user", "content": "What is 17% of 2340?"}], "397.80", src, lic, "general")
    st.add_example([{"role": "user", "content": "Hi!"}], "Hola Lea! ¿Qué tal?", src, lic, "general")
    st.add_example([{"role": "user", "content": "Where do I live?"}], "In Madrid.", src, lic, "personal")
    st.add_example([{"role": "user", "content": "Capital of Peru?"}], "Lima.", src, lic, "general")
    return db, st


def test_training_build_leaves_out_general_examples_that_mention_lea(tmp_path):
    _, st = store_with_examples(tmp_path)
    rep = build(st, tmp_path / "builds", name="x", privacy=terms())
    assert rep.excluded["mentions_lea"] == 1 and rep.excluded["personal"] == 1
    text = (rep.path / "train.jsonl").read_text() + (rep.path / "valid.jsonl").read_text()
    assert "Lea" not in text and "Lima" in text
    assert json.loads((rep.path / "manifest.json").read_text())["privacy_checked"] is True


def cfg_for(tmp_path):
    cfg = Config(dict(load_config()))
    cfg["paths"] = {
        **cfg["paths"],
        "data_dir": str(tmp_path / "data"),
        "adapters_dir": str(tmp_path / "adapters"),
    }
    return cfg


def fake_adapter(root: Path, model: str, version: str, dataset: dict):
    d = root / model / version
    d.mkdir(parents=True)
    (d / "adapters.safetensors").write_bytes(b"weights")
    (d / "adapter_config.json").write_text("{}")
    (d / "meta.json").write_text(json.dumps({"version": version, "ok": True, "dataset": dataset}))


def test_general_folder_has_no_personal_data_and_imports_elsewhere(tmp_path):
    cfg = cfg_for(tmp_path)
    _, st = store_with_examples(tmp_path)
    ads = tmp_path / "adapters"
    fake_adapter(ads, "M", "v001", {"counts": {"by_source": {"open-dataset": 10}}})  # open data only
    fake_adapter(ads, "M", "v002", {"counts": {"by_source": {"lea": 3}}})  # before the privacy check
    fake_adapter(ads, "M", "v003", {"privacy_checked": True, "include_personal": False, "counts": {}})
    m = export(cfg, st, terms())
    out = Path(cfg.data_dir) / "general"
    assert m["examples"] == 2 and m["left_out_because_personal"] == 1
    assert sorted(m["adapters"]) == ["M/v001", "M/v003"]
    everything = "".join(p.read_text(errors="ignore") for p in out.rglob("*") if p.is_file())
    assert "Lea" not in everything and "Madrid" not in everything

    # Someone else's Sylvie: gets the general examples and the adapters as candidates, nothing else.
    other = Config(dict(load_config()))
    other["paths"] = {
        **other["paths"],
        "data_dir": str(tmp_path / "other"),
        "adapters_dir": str(tmp_path / "other_ads"),
    }
    ost = LearningStore(Database(tmp_path / "other.db", dim=8))
    res = import_pack(other, ost, out)
    assert res["examples_added"] == 2 and len(res["adapters"]) == 2
    assert import_pack(other, ost, out)["examples_added"] == 0  # not twice
    assert {e["chosen"] for e in ost.examples(tag="general")} == {"397.80", "Lima."}
    meta = json.loads((tmp_path / "other_ads" / "M" / "v001" / "meta.json").read_text())
    assert meta["imported"] is True


def test_export_refuses_when_something_slips_through(tmp_path):
    cfg = cfg_for(tmp_path)
    _, st = store_with_examples(tmp_path)

    class Leaky(PersonalTerms):
        def is_personal(self, *texts):
            return False  # the first check misses it…

    leaky = Leaky(terms().names, terms().terms, terms().domains)
    with pytest.raises(LeakFound):  # …the check on the written text catches it
        export(cfg, st, leaky)
    assert not (Path(cfg.data_dir) / "general" / "examples.jsonl").exists()


def test_shareable_adapter_rules():
    assert shareable_adapter({"dataset": {"privacy_checked": True}})
    assert not shareable_adapter({"dataset": {"privacy_checked": True, "include_personal": True}})
    assert not shareable_adapter({"ok": False, "dataset": {"privacy_checked": True}})
    assert shareable_adapter({"dataset": {"counts": {"by_source": {"open-dataset": 5}}}})
    assert not shareable_adapter(
        {"dataset": {"counts": {"by_source": {"open-dataset": 5, "sylvie-local": 1}}}}
    )


# ---------- more to learn: MMLU, checked by code ----------
def test_mmlu_questions_and_letter_check(tmp_path):
    p = tmp_path / "mmlu.jsonl"
    p.write_text(
        json.dumps(
            {
                "subject": "astronomy",
                "question": "Closest star?",
                "choices": ["Sun", "Vega", "Sirius", "Rigel"],
                "answer": 0,
            }
        )
        + "\n"
    )
    [it] = mmlu_questions(p, random.Random(0), 5)
    assert it.choice == "A" and "A) Sun" in it.question and it.source == "mmlu"
    for good in ["A) The Sun is closest.", "**A** — the Sun.", "The answer is A.", "A. Sun"]:
        assert chosen_letter(good) == "A" and code_check(it, good)
    assert code_check(it, "B) Vega, obviously.") is False


def test_auto_approval_only_for_good_general_pairs(tmp_path):
    cfg = cfg_for(tmp_path)
    cfg["learning"] = {"auto_approve_judged": True, "auto_approve_min_score": 8}
    n = Overnight(cfg, None, None, None, "sys", seed=0)
    assert n.status_for(Item("1", "q", "oasst", better_score=9)) == "ready"
    assert n.status_for(Item("2", "q", "oasst", better_score=7)) == "staged"
    assert n.status_for(Item("3", "q", "mmlu", better_score=5, meta={"verified": True})) == "ready"
    assert n.status_for(Item("4", "q", "past_chat", tag="personal", better_score=10)) == "staged"
    cfg["learning"] = {"auto_approve_judged": False}
    assert n.status_for(Item("5", "q", "oasst", better_score=10)) == "staged"


def test_train_now_ignores_the_window_but_still_stops_for_lea(tmp_path):
    from learning.scheduler import Scheduler
    from tests.test_orchestrator import make

    s = make(tmp_path, [])
    s.warm = lambda: None
    (s.cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    sc = Scheduler(s, power=lambda: True, clock=lambda: datetime(2026, 11, 7, 15, 0))  # afternoon
    assert not sc.can_run()[0]
    started = []
    sc._launch = lambda job: (started.append(job.args), setattr(sc, "proc", __import__("subprocess").Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"])), setattr(sc, "current", job),
        setattr(sc, "started_at", time.time()))  # fmt: skip
    ok, _ = sc.run_now(60)
    assert ok and "--finetune" in started[0] and "60" in started[0]
    assert sc.run_now(60)[0] is False  # one at a time
    sc.tick()  # outside the night window, but asked for: keeps running
    assert sc.status()["running"] == "overnight"
    assert sc.preempt() and sc.status()["running"] is None
    plugged = [False]
    later = Scheduler(s, power=lambda: plugged[0], clock=lambda: datetime(2026, 11, 7, 15, 0))
    later._launch = sc._launch
    ok, why = later.run_now(45)
    assert not ok and "plugged in" in why and later.status()["waiting_for_power"]
    later.tick()
    assert later.proc is None  # still on battery
    plugged[0] = True
    later.tick()  # plugged in: starts by itself
    assert later.manual and "45" in started[-1]
    sc.proc.kill()  # the fake launcher put the process on `sc`


def test_permanent_mode_trains_any_hour_after_a_short_quiet_spell(tmp_path):
    from datetime import datetime

    from learning.scheduler import Scheduler
    from tests.test_orchestrator import make

    s = make(tmp_path, [])
    s.cfg["schedule"] = {
        **s.cfg["schedule"],
        "always": True,
        "always_idle_minutes": 5,
        "always_job_minutes": 240,
    }
    sc = Scheduler(s, power=lambda: True, clock=lambda: datetime(2026, 11, 7, 15, 0))  # afternoon
    s.bg.last_activity = time.time() - 2 * 60
    assert "used Sylvie" in sc.can_run()[1]  # she was talking 2 minutes ago
    s.bg.last_activity = time.time() - 6 * 60
    sc.used_tonight = 99 * 3600  # no nightly cap in this mode
    assert sc.can_run() == (True, "ok")
    assert "240" in sc.next_job().args
    assert not Scheduler(s, power=lambda: False).can_run()[0]  # never on battery
    s.cfg["schedule"] = {**s.cfg["schedule"], "always": False}
    assert (
        "window" in Scheduler(s, power=lambda: True, clock=lambda: datetime(2026, 11, 7, 15, 0)).can_run()[1]
    )


def test_knowledge_eval_set_is_scored_by_letter_and_kept_out_of_practice():
    from evals.run import score
    from learning.dataset import EvalGuard

    rows = [json.loads(x) for x in Path("evals/sets/knowledge.jsonl").read_text().split("\n") if x.strip()]
    assert len(rows) == 150 and len({r["subject"] for r in rows}) == 57
    item = rows[0]
    right = item["check"]["value"]
    wrong = next(x for x in "ABCD" if x != right)
    assert score(item, f"{right}) because…", []) is True
    assert score(item, f"**{wrong}**", []) is False
    assert EvalGuard().overlaps(item["prompt"])  # practice never sees it


def test_a_job_left_by_a_previous_server_is_stopped(tmp_path):
    import subprocess

    from learning.scheduler import Scheduler
    from tests.test_orchestrator import make

    s = make(tmp_path, [])
    (s.cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "learning.fake"])
    sc = Scheduler(s, power=lambda: True)
    sc._pidfile().write_text(str(p.pid))
    sc.stop_leftover()
    assert p.wait(timeout=5) != 0
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # not ours: left alone
    sc._pidfile().write_text(str(other.pid))
    sc.stop_leftover()
    time.sleep(0.3)
    assert other.poll() is None
    other.kill()
    # One that ignores the request to stop (busy in a long generation) is killed after the grace time.
    stubborn = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, "
                                 "signal.SIG_IGN); time.sleep(60)", "learning.fake"])  # fmt: skip
    time.sleep(0.5)
    sc._pidfile().write_text(str(stubborn.pid))
    sc.leftover_grace = 1.0
    sc.stop_leftover()
    assert stubborn.wait(timeout=5) == -9
