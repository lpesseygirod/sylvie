import gzip
import json
import subprocess
from pathlib import Path

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
    assert parse_losses(log) == {
        "train_first": [2.31], "train_last": [1.8], "val": [2.1], "peak_mem_gb": 9.5, "nan": False,
    }  # fmt: skip


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
    assert out["peak_mem_gb"] == 21.47 and out["nan"] is False


def test_parse_losses_sees_nan_and_true_peak_memory():
    log = "    50    1.251 \u25bc  32  8.2k\n    60      nan \u25b2  31  9.3k\n   100    val   nan    1s\n"
    log += "  16613392384  maximum resident set size\n  38621711152  peak memory footprint\n"
    out = parse_losses(log)
    assert out["nan"] is True and out["peak_mem_gb"] == 38.62


def _fake_trainer(tmp_path, nan_on_calls):
    """Stands in for `mlx_lm lora`: writes an adapter (NaN on the given call numbers) and a log."""
    import mlx.core as mx

    calls = []

    def run(args, env):
        calls.append(args)
        out = Path(args[args.index("--adapter-path") + 1])
        bad = len(calls) in nan_on_calls
        value = float("nan") if bad else float(len(calls))
        mx.save_safetensors(str(out / "adapters.safetensors"), {"w": mx.array([value, 1.0])})
        loss = "nan" if bad else "1.000"
        return subprocess.CompletedProcess(args, 0, f"    10    {loss} \u25bc  1  1\n", "")

    return run, calls


def _train(tmp_path, monkeypatch, nan_on_calls, iters=300):
    import learning.train as tr
    from core.config import Config

    cfg = Config({"paths": {"adapters_dir": str(tmp_path / "adapters")}})
    monkeypatch.setattr(tr, "load_config", lambda: cfg)
    build = tmp_path / "build"
    build.mkdir()
    (build / "manifest.json").write_text("{}")
    run, calls = _fake_trainer(tmp_path, nan_on_calls)
    return tr, run, calls, build


def test_nan_segment_rolls_back_and_retries_gentler(tmp_path, monkeypatch):
    import mlx.core as mx

    tr, run, calls, build = _train(tmp_path, monkeypatch, nan_on_calls={3})
    out = tr.train("org/M", build, iters=300, run=run)
    lrs = [a[a.index("--learning-rate") + 1] for a in calls]
    assert lrs == ["1e-05", "1e-05", "1e-05", "5e-06"]  # segment 3 diverged, retried at half the rate
    assert "--resume-adapter-file" in calls[3]  # …from the good adapter after step 200
    meta = json.loads((out / "meta.json").read_text())
    assert meta["ok"] and meta["steps_done"] == 300 and meta["stopped_early"] is None
    assert mx.load(str(out / "adapters.safetensors"))["w"][0].item() == 4.0
    assert tr.adapter_is_finite(out / "adapters.safetensors")


def test_nan_twice_keeps_last_good_adapter(tmp_path, monkeypatch):
    import mlx.core as mx

    tr, run, calls, build = _train(tmp_path, monkeypatch, nan_on_calls={2, 3})
    out = tr.train("org/M", build, iters=300, run=run)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["steps_done"] == 100 and "NaN twice" in meta["stopped_early"]
    assert mx.load(str(out / "adapters.safetensors"))["w"][0].item() == 1.0  # the step-100 adapter


def test_nan_from_the_start_is_a_failure(tmp_path, monkeypatch):
    tr, run, calls, build = _train(tmp_path, monkeypatch, nan_on_calls={1, 2})
    with pytest.raises(RuntimeError):
        tr.train("org/M", build, iters=300, run=run)


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


def test_failed_fine_tune_does_not_delay_the_next(tmp_path):
    from datetime import datetime

    from learning.cycle import last_adapter_time

    cfg = Config({"paths": {"adapters_dir": str(tmp_path)}})
    v = tmp_path / "M" / "v001"
    v.mkdir(parents=True)
    (v / "meta.json").write_text(json.dumps({"created": datetime.now().isoformat(), "ok": False}))
    assert last_adapter_time(cfg, "org/M") is None


def test_build_drops_examples_that_dont_fit_the_training_length(tmp_path):
    from core.db import Database

    store = LearningStore(Database(tmp_path / "s.db", dim=8))
    for q in ("short one", "long one", "empty one"):
        store.add_example(
            [{"role": "user", "content": q}], chosen="fine", source="lea", license="lea", tag="general"
        )

    def measure(messages, tools):  # (total tokens, prompt tokens) per example
        q = messages[0]["content"]
        return {"short one": (100, 90), "long one": (600, 590), "empty one": (512, 510)}[q]

    rep = build(store, tmp_path / "builds", name="x", measure=measure, max_tokens=512)
    assert rep.counts["train"] + rep.counts["valid"] == 1
    assert rep.excluded["too_long"] == 1 and rep.excluded["nothing_to_learn"] == 1


def test_meta_never_contains_nan():
    from learning.train import finite

    out = finite({"val": [1.2, float("nan")], "peak": float("inf"), "ok": True, "n": 3})
    assert out == {"val": [1.2, None], "peak": None, "ok": True, "n": 3}
    json.dumps(out, allow_nan=False)
