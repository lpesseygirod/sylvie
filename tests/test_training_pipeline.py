import gzip
import json

import pytest

from core.config import Config
from learning.dataset import EvalGuard, build, oasst_examples
from learning.promote import active_adapter, gate, promote, rollback
from learning.store import LearningStore
from learning.train import next_version, parse_losses


def oasst_file(tmp_path):
    tree = {
        "prompt": {
            "role": "prompter", "lang": "es", "text": "¿Cómo hago una tortilla?", "review_result": True,
            "replies": [
                {"role": "assistant", "text": "Bate huevos…", "rank": 1, "replies": []},
                {"role": "assistant", "text": "Primero pela las patatas…", "rank": 0, "replies": []},
            ],
        }
    }  # fmt: skip
    de = {"prompt": {"role": "prompter", "lang": "de", "text": "Hallo", "replies": []}}
    p = tmp_path / "oasst.jsonl.gz"
    with gzip.open(p, "wt") as f:
        f.write(json.dumps(tree) + "\n" + json.dumps(de) + "\n")
    return p


def test_oasst_takes_best_ranked_reply_and_filters_languages(tmp_path):
    ex = oasst_examples(oasst_file(tmp_path), limit=10)
    assert len(ex) == 1
    assert ex[0].messages[1]["content"] == "Primero pela las patatas…"
    assert ex[0].source == "open-dataset:oasst2" and ex[0].license == "apache-2.0"


def test_build_excludes_personal_and_eval_overlap_and_weights_lea(tmp_path):
    from core.db import Database

    store = LearningStore(Database(tmp_path / "t.db", dim=8))
    store.add_example(
        [{"role": "user", "content": "explain inflation simply"}],
        "Prices go up…",
        "lea",
        "own",
        "general",
        weight=2.0,
    )
    store.add_example(
        [{"role": "user", "content": "when is my sister's birthday"}], "May 3", "lea", "own", "personal"
    )
    store.add_example(
        [{"role": "user", "content": "What is the time complexity of binary search?"}],
        "O(log n)",
        "sylvie-local:x",
        "apache",
        "general",
    )
    rep = build(store, tmp_path / "builds", open_data=oasst_file(tmp_path), name="b1")
    assert rep.excluded["personal"] == 1 and rep.excluded["eval_overlap"] == 1
    assert rep.counts["by_source"] == {"lea": 2, "open-dataset": 1}
    manifest = json.loads((rep.path / "manifest.json").read_text())
    assert manifest["include_personal"] is False and len(manifest["sha256"]["train"]) == 64
    rows = [json.loads(line) for line in (rep.path / "train.jsonl").read_text().splitlines()]
    assert all(r["messages"][-1]["role"] == "assistant" for r in rows)


def test_eval_guard():
    g = EvalGuard()
    assert g.overlaps("what is the time complexity of binary search")
    assert not g.overlaps("what's a good pasta recipe")


def test_versions_promote_rollback(tmp_path):
    cfg = Config({"paths": {"data_dir": str(tmp_path), "adapters_dir": str(tmp_path / "adapters")}})
    root = tmp_path / "adapters" / "M"
    for v in ("v001", "v002"):
        (root / v).mkdir(parents=True)
        (root / v / "adapters.safetensors").write_bytes(b"x")
    assert next_version(root) == "v003"
    promote(cfg, "org/M", "v001")
    promote(cfg, "org/M", "v002")
    assert active_adapter(cfg, "org/M").name == "v002"
    assert rollback(cfg, "org/M") == "v001"
    assert rollback(cfg, "org/M") is None and active_adapter(cfg, "org/M") is None
    with pytest.raises(FileNotFoundError):
        promote(cfg, "org/M", "v009")


def test_gate():
    assert not gate({"regressions": ["math"]}, {"decided": 10, "new_wins": 8, "old_wins": 1})[0]
    assert not gate({"regressions": []}, None)[0]
    assert not gate({"regressions": []}, {"decided": 10, "new_wins": 4, "old_wins": 5})[0]
    assert gate({"regressions": []}, {"decided": 10, "new_wins": 6, "old_wins": 3})[0]


def test_parse_losses():
    log = "Iter 10: Train loss 2.31, ...\nIter 20: Val loss 2.10, ...\nIter 20: Train loss 1.80, Peak mem 9.5 GB"
    assert parse_losses(log) == {"train_first": [2.31], "train_last": [1.8], "val": [2.1], "peak_mem_gb": 9.5}


def test_blind_ab_hides_labels_and_counts_wins(tmp_path):
    from core.db import Database
    from learning.ab import ABTests

    ab = ABTests(Database(tmp_path / "ab.db", dim=8))
    old = {
        "results": [
            {"id": f"s{i}", "set": "style", "prompt": f"q{i}", "answer": f"old {i}"} for i in range(6)
        ]
    }
    new = {
        "results": [
            {"id": f"s{i}", "set": "style", "prompt": f"q{i}", "answer": f"new {i}"} for i in range(6)
        ]
    }
    assert ab.create_round(old, new, "r1", seed=1) == 6
    while (p := ab.next_pair()) is not None:
        assert "left_is" not in p  # the UI never learns which is which
        ab.choose(p["id"], "left" if p["left"].startswith("new") else "right")  # Lea always prefers "new"
    assert ab.summary() == {"pending": 0, "decided": 6, "new_wins": 6, "old_wins": 0, "ties": 0}


def test_parse_losses_new_table_format():
    log = "  iter   train_loss\n     1    val 1.187    7.08s\n    10    1.078 \u25bc  37  2.6k\n   300    0.900 \u25bc  1  1\n   300    val 1.020    4.89s\n  21474836480  maximum resident set size\n"
    out = parse_losses(log)
    assert out["train_first"] == [1.078] and out["train_last"] == [0.9] and out["val"] == [1.187, 1.02]
    assert out["peak_mem_gb"] == 21.47


def test_cycle_due_and_run(tmp_path):
    from datetime import UTC, datetime, timedelta

    from core.db import Database
    from learning.ab import ABTests
    from learning.cycle import due, run_cycle

    cfg = Config({"paths": {"data_dir": str(tmp_path), "adapters_dir": str(tmp_path / "adapters")},
                  "schedule": {"finetune_every_days": 10}, "learning": {"min_new_examples": 2}})  # fmt: skip
    db = Database(tmp_path / "c.db", dim=8)
    store, ab = LearningStore(db), ABTests(db)
    assert not due(cfg, store, "org/M")[0]  # no examples yet
    for i in range(2):
        store.add_example(
            [{"role": "user", "content": f"how do I cook rice {i}"}], "Rinse it…", "lea", "own", "general"
        )
    assert due(cfg, store, "org/M") == (True, "due")

    def train_fn(model, build_dir):
        out = tmp_path / "adapters" / "M" / "v001"
        out.mkdir(parents=True)
        (out / "meta.json").write_text(json.dumps({"created": datetime.now().isoformat()}))
        return out

    def eval_fn(label, adapter):
        answer = "new answer" if adapter else "old answer"
        return {
            "summary": {"style": {"accuracy": 0.5}},
            "results": [{"id": "s1", "set": "style", "prompt": "hey", "answer": answer}],
        }

    res = run_cycle(cfg, store, ab, "org/M", None, train_fn, eval_fn)
    assert res["adapter"] == "v001" and res["comparison"]["regressions"] == [] and res["ab_pairs"] == 1
    assert not due(cfg, store, "org/M", now=datetime.now(UTC) + timedelta(days=3))[0]  # just trained
