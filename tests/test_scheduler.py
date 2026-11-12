import time
from datetime import datetime

from learning.scheduler import Scheduler, in_window
from tests.test_orchestrator import make


def test_window_crossing_midnight():
    assert in_window(datetime(2026, 11, 6, 2, 0), "01:00", "07:00")
    assert not in_window(datetime(2026, 11, 6, 8, 0), "01:00", "07:00")
    assert in_window(datetime(2026, 11, 6, 23, 30), "23:00", "06:00")
    assert in_window(datetime(2026, 11, 6, 3, 0), "23:00", "06:00")
    assert not in_window(datetime(2026, 11, 6, 12, 0), "23:00", "06:00")


def sched(tmp_path, hour=2, power=True, idle_min=60):
    s = make(tmp_path, [])
    s.bg.last_activity = time.time() - idle_min * 60
    return Scheduler(s, power=lambda: power, clock=lambda: datetime(2026, 11, 6, hour, 0)), s


def test_conditions(tmp_path):
    assert sched(tmp_path)[0].can_run() == (True, "ok")
    assert "battery" in sched(tmp_path, power=False)[0].can_run()[1]
    assert "window" in sched(tmp_path, hour=15)[0].can_run()[1]
    assert "used Sylvie" in sched(tmp_path, idle_min=5)[0].can_run()[1]


def test_job_runs_and_lea_preempts_it(tmp_path):
    import sys

    sc, s = sched(tmp_path)
    s.warm = lambda: None
    s.scheduler = sc
    sc.next_job = lambda: __import__("learning.scheduler", fromlist=["Job"]).Job(
        "sleeper", [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    (s.cfg.data_dir / "logs").mkdir(exist_ok=True)
    sc.tick()
    assert sc.status()["running"] == "sleeper"
    s.llm.replies = ["Hi!", "{}"]
    ev = list(s.turn("Sylvie?"))  # Lea talks: the job stops, the assistant answers
    assert any(e["type"] == "status" and "waking" in e.get("text", "") for e in ev)
    assert ev[-1]["type"] == "done"
    assert sc.status()["running"] is None


def test_job_time_limit_never_runs_past_the_window(tmp_path):
    from learning.scheduler import minutes_until

    assert minutes_until(datetime(2026, 11, 6, 6, 55), "07:00") == 5
    assert minutes_until(datetime(2026, 11, 6, 23, 0), "07:00") == 480
    sc, _ = sched(tmp_path, hour=2)
    assert sc.next_job().args[-1] == "240"  # 02:00: the 4-hour cap
    sc.clock = lambda: datetime(2026, 11, 6, 6, 30)
    assert sc.next_job().args[-1] == "30"  # 06:30: only until 07:00
    sc.clock = lambda: datetime(2026, 11, 6, 6, 57)
    assert sc.next_job() is None  # not worth starting


def test_running_job_is_stopped_when_the_window_closes(tmp_path):
    import sys

    from learning.scheduler import Job

    sc, s = sched(tmp_path, hour=6)
    s.warm = lambda: None
    (s.cfg.data_dir / "logs").mkdir(exist_ok=True)
    sc.next_job = lambda: Job("sleeper", [sys.executable, "-c", "import time; time.sleep(30)"])
    sc.tick()
    assert sc.status()["running"] == "sleeper"
    sc.clock = lambda: datetime(2026, 11, 6, 7, 1)
    sc.tick()
    assert sc.status()["running"] is None
