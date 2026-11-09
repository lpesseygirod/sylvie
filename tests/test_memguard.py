"""The memory guard (core/memguard.py) and what it controls: backlog jobs, voice models, night jobs,
document indexing in stoppable batches, and a tool list that never opens WhatsApp's folder."""

import sys
import threading
import time
from datetime import datetime

from core.memguard import GuardLoop, MemoryGuard, Reading
from tests.test_orchestrator import make


def guard(tmp_path, pressure="normal", free_gb=20.0, idle_s=900.0):
    s = make(tmp_path, [])
    g = MemoryGuard(s.cfg, reader=lambda: Reading(pressure, 50, free_gb, idle_s), cache_seconds=0)
    return g, s


def test_backlog_needs_room_and_lea_away(tmp_path):
    assert guard(tmp_path)[0].backlog_ok() == (True, "ok")
    assert "warn" in guard(tmp_path, pressure="warn")[0].backlog_ok()[1]
    assert "free" in guard(tmp_path, free_gb=3)[0].backlog_ok()[1]
    g = guard(tmp_path, free_gb=7)[0]
    assert not g.backlog_ok()[0] and g.backlog_ok(starting=False)[0]  # 2 GB more to start than to keep going
    assert "using the Mac" in guard(tmp_path, idle_s=20)[0].backlog_ok()[1]
    assert guard(tmp_path, idle_s=None)[0].backlog_ok()[0]  # idle time unknown: memory decides


def test_heavy_job_counts_what_sylvie_frees(tmp_path):
    g, _ = guard(tmp_path, free_gb=8)
    ok, why = g.heavy_ok(20, freed_gb=10)
    assert not ok and "needs 20 GB" in why and "18.0 GB" in why
    assert g.heavy_ok(20, freed_gb=12) == (True, "ok")
    assert not guard(tmp_path, pressure="critical")[0].heavy_ok(1)[0]


def test_backlog_job_waits_for_the_gate(tmp_path):
    s = make(tmp_path, [])
    s.bg.backlog_gate = lambda: False
    ran = threading.Event()
    s.bg.submit("index-documents", lambda ev: ran.set(), backlog=True)
    time.sleep(0.6)
    assert not ran.is_set()
    s.bg.backlog_gate = lambda: True
    assert ran.wait(3)


def test_guard_pauses_a_running_backlog_job_and_requeues_it(tmp_path):
    g, s = guard(tmp_path)
    started, runs = threading.Event(), []

    def job(ev):
        runs.append(1)
        started.set()
        ev.wait(5)

    s.bg.submit("index-documents", job, backlog=True)
    assert started.wait(3)
    loop = GuardLoop(s, g)
    g.reader = lambda: Reading("normal", 50, 20, 10)  # Lea is back at the keyboard
    s.bg.backlog_gate = lambda: g.backlog_ok()[0]
    loop.tick()
    end = time.time() + 3
    while s.bg.current is not None and time.time() < end:
        time.sleep(0.05)
    assert s.bg.current is None and s.bg.backlog.qsize() == 1 and runs == [1]  # paused, not dropped
    g.reader = lambda: Reading("normal", 50, 20, 900)  # Lea leaves again at once…
    assert g.backlog_ok() == (False, "paused a few minutes ago")  # …but no restart for 10 minutes


def test_voice_models_unload_after_quiet_minutes(tmp_path):
    g, s = guard(tmp_path)

    class FakeSTT:
        _model = object()

        def unload(self):
            self._model = None

    s._stt = FakeSTT()
    loop = GuardLoop(s, g)
    s._voice_used = time.time()
    loop.tick()
    assert s._stt._model is not None  # just used
    s._voice_used = time.time() - 11 * 60
    loop.tick()
    assert s._stt._model is None
    s._stt._model = object()
    s._voice_used = time.time() - 3 * 60
    g.reader = lambda: Reading("warn", 30, 2, 900)
    loop.tick()
    assert s._stt._model is None  # memory tight: 2 minutes are enough


def test_warm_leaves_voice_models_for_later(tmp_path):
    s = make(tmp_path, [])
    s.cfg["voice"] = {**s.cfg["voice"], "enabled": True, "load_on_demand": True}
    s.warm()
    assert s._stt is None and s._tts is None


def sched(tmp_path, g):
    from learning.scheduler import Scheduler

    s = g[1]
    s.guard = g[0]
    s.bg.last_activity = time.time() - 3600
    (s.cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    s.warm = lambda: None
    return Scheduler(s, power=lambda: True, clock=lambda: datetime(2026, 11, 6, 2, 0)), s


def test_night_job_waits_for_memory(tmp_path):
    sc, _ = sched(tmp_path, guard(tmp_path, free_gb=1))
    sc.job_memory_gb = 10_000  # more than this test process frees
    ok, why = sc.can_run()
    assert not ok and "needs" in why
    ok, why = sc.run_now(10)
    assert not ok and "memory" in why and sc.proc is None


def test_night_job_stopped_over_the_cap(tmp_path):
    from learning.scheduler import Job

    sc, _ = sched(tmp_path, guard(tmp_path))
    sc.next_job = lambda: Job("sleeper", [sys.executable, "-c", "import time; time.sleep(30)"])
    sc.tick()
    assert sc.status()["running"] == "sleeper"
    sc.max_job_gb = 0.001  # the sleeper's few MB are "over the cap"
    sc.tick()
    assert sc.status()["running"] is None and "cap" in sc.last_reason
    assert sc.can_run() == (False, "paused after running out of memory; trying again later")


def test_night_job_stopped_after_minutes_of_pressure(tmp_path):
    from learning.scheduler import Job

    g = guard(tmp_path)
    sc, _ = sched(tmp_path, g)
    sc.next_job = lambda: Job("sleeper", [sys.executable, "-c", "import time; time.sleep(30)"])
    sc.tick()
    g[0].reader = lambda: Reading("warn", 30, 2, 900)
    sc.tick()
    sc.tick()
    assert sc.status()["running"] == "sleeper"  # two minutes of "warn": still running
    sc.tick()
    assert sc.status()["running"] is None and "pressure" in sc.last_reason


def test_indexing_stops_inside_a_file_and_skips_broken_ones(tmp_path):
    import os

    s = make(tmp_path, [])
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "book.md").write_text("\n\n".join(f"Chapter {i}. " + "words " * 200 for i in range(60)))
    bad = folder / "broken.docx"
    bad.write_bytes(b"not a zip")
    calls = []
    real = s.docs.embedder.embed
    stop = threading.Event()

    def embed(texts, **kw):
        calls.append(len(texts))
        if len(calls) == 2:
            stop.set()  # Lea starts talking during the book
        return real(texts, **kw)

    s.docs.embedder.embed = embed
    counts = s.docs.index_folders([str(folder)], stop=stop)
    assert counts["stopped"] and max(calls) <= 16  # small batches, stopped midway
    assert s.docs.stats()["documents"] <= 1  # the half-done book wasn't saved
    s.docs.embedder.embed = real
    counts = s.docs.index_folders([str(folder)])
    assert counts["errors"] == 1 and s.docs.stats()["chunks"] > 16
    counts = s.docs.index_folders([str(folder)])
    assert counts["errors"] == 0 and counts["skipped_broken"] == 1  # not retried while unchanged
    os.utime(bad, (time.time() + 5, time.time() + 5))
    assert s.docs.index_folders([str(folder)])["errors"] == 1  # changed: tried again


def test_whatsapp_tool_listed_without_opening_its_folder(tmp_path, monkeypatch):
    import lea_profile.whatsapp as wa
    from tools.registry import available_tools

    s = make(tmp_path, [])
    s.cfg["whatsapp"] = {
        "enabled": True,
        "folder": "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared",
    }

    def boom(*a, **k):
        raise AssertionError("opened WhatsApp's folder while building the tool list")

    monkeypatch.setattr(wa, "get", boom)
    names = lambda: [t["function"]["name"] for t in available_tools(s.tool_context())]  # noqa: E731
    assert "whatsapp" not in names()  # no copy of the chats yet
    copy = s.cfg.data_dir / "app-data" / "whatsapp" / "ChatStorage.sqlite"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"")
    assert "whatsapp" in names()
