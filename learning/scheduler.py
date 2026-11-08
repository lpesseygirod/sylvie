"""Runs heavy jobs (overnight loop, fine-tuning, evals) only when it's safe:

- the Mac is plugged in (never on battery),
- Lea hasn't used Sylvie for a while,
- inside the night window (default 01:00–07:00) and within a time cap.

Heavy jobs and the live assistant don't fit in memory together, so the scheduler unloads the
assistant's model, runs the job as a separate process, and reloads afterwards. If Lea talks to
Sylvie meanwhile, the job is stopped (each job saves its progress and resumes next time) and
the assistant reloads.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("sylvie.scheduler")
ROOT = Path(__file__).resolve().parent.parent


def on_ac_power() -> bool:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "AC Power" in out


def in_window(now: datetime, start: str, end: str) -> bool:
    s = tuple(int(x) for x in start.split(":"))
    e = tuple(int(x) for x in end.split(":"))
    t = (now.hour, now.minute)
    return s <= t < e if s < e else (t >= s or t < e)  # windows may cross midnight


def minutes_until(now: datetime, end: str) -> int:
    """Whole minutes from `now` until the next `end` (HH:MM) time."""
    h, m = (int(x) for x in end.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds() // 60)


@dataclass
class Job:
    name: str
    args: list[str]


def _alive(pid: int) -> bool:
    """Running (a finished process waiting to be collected, a "zombie", counts as gone)."""
    stat = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout
    return bool(stat.strip()) and not stat.strip().startswith("Z")


class Scheduler:
    def __init__(self, sylvie, check_every: float = 60.0, power=on_ac_power, clock=None):
        self.s = sylvie
        cfg = sylvie.cfg
        self.window = (
            cfg.get_path("schedule.window_start", "01:00"),
            cfg.get_path("schedule.window_end", "07:00"),
        )
        # Permanent mode (Lea, 2026-11-07: "keep training until I start using Sylvie, then when I
        # finish train again"): any time of day, after a short quiet spell, no nightly cap.
        self.always = bool(cfg.get_path("schedule.always", False))
        self.idle_minutes = (
            cfg.get_path("schedule.always_idle_minutes", 5)
            if self.always
            else cfg.get_path("schedule.idle_minutes", 30)
        )
        self.job_minutes = cfg.get_path("schedule.always_job_minutes", 240)
        self.max_hours = cfg.get_path("schedule.max_hours_per_night", 5)
        self.enabled = cfg.get_path("schedule.enabled", True)
        self.check_every = check_every
        self.power = power
        self.clock = clock or (lambda: datetime.now(ZoneInfo(cfg.get_path("user.timezone", "Europe/Madrid"))))
        self.proc: subprocess.Popen | None = None
        self.current: Job | None = None
        self.manual = False  # started by "train now": ignores the night window, still stops for Lea
        self.pending_now: tuple[int, bool] | None = (
            None  # "train now" asked on battery: starts when plugged in
        )
        self.started_at: float | None = None
        self.used_tonight = 0.0
        self.night: str | None = None
        self.last_reason = "not checked yet"
        # Memory guard (efficiency plan A2): the job starts only if what it needs is free once Sylvie
        # has let go of her models, and is stopped past a hard cap or when macOS reports pressure.
        self.job_memory_gb = float(cfg.get_path("schedule.job_memory_gb", 20))
        self.max_job_gb = float(cfg.get_path("schedule.max_job_gb", 24))
        self.guard_pause_minutes = float(cfg.get_path("schedule.guard_pause_minutes", 30))
        self.blocked_until = 0.0
        self._tight_ticks = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---------- decisions ----------
    def can_run(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "scheduler disabled in config"
        now = self.clock()
        if not self.always and not in_window(now, *self.window):
            return False, f"outside the {self.window[0]}–{self.window[1]} window"
        if not self.power():
            return False, "on battery"
        idle = (time.time() - self.s.bg.last_activity) / 60
        if self.s.bg.busy.is_set() or idle < self.idle_minutes:
            return False, f"Lea used Sylvie {idle:.0f} min ago"
        night = (now - timedelta(hours=12)).strftime(
            "%Y-%m-%d"
        )  # 01:00 on the 6th belongs to the night of the 5th
        if night != self.night:
            self.night, self.used_tonight = night, 0.0
        if not self.always and self.used_tonight >= self.max_hours * 3600:
            return False, "tonight's time cap is used up"
        if time.time() < self.blocked_until:
            return False, "paused after running out of memory; trying again later"
        guard = getattr(self.s, "guard", None)
        if guard is not None:
            from core.memwatch import GB, proc_usage

            mine = (proc_usage(os.getpid()) or {}).get("footprint", 0) / GB  # freed by unload_models
            ok, why = guard.heavy_ok(self.job_memory_gb, freed_gb=mine)
            if not ok:
                return False, why
        return True, "ok"

    def _memory_check(self) -> str | None:
        """While a job runs: a reason to stop it, or None. Past the hard cap, critical pressure, or
        three minutes of "warn" pressure (the Mac starts swapping Lea's apps)."""
        from core.memwatch import GB, tree_footprint

        gb = tree_footprint(self.proc.pid) / GB
        if gb > self.max_job_gb:
            return f"it used {gb:.0f} GB, over the {self.max_job_gb:.0f} GB cap"
        guard = getattr(self.s, "guard", None)
        if guard is None or not guard.enabled:
            return None
        r = guard.reading()
        self._tight_ticks = self._tight_ticks + 1 if r.tight else 0
        if r.pressure == "critical" or self._tight_ticks >= max(1, int(180 / self.check_every)):
            return f"memory pressure is {r.pressure} ({gb:.0f} GB for the job)"
        return None

    def next_job(self) -> Job | None:
        """Overnight loop first; the fine-tune/eval cycle is started by the loop when it's due."""
        # Never past the window's end or tonight's cap: a job started at 06:55 gets 5 minutes, not 5 hours.
        if self.always:
            left = self.job_minutes  # then it simply starts again while Sylvie stays unused
        else:
            left = min(
                self.max_hours * 60 - self.used_tonight / 60, minutes_until(self.clock(), self.window[1])
            )
        if left < 5:
            return None
        py = [sys.executable, "-m", "learning.overnight", "--max-minutes", str(int(left))]
        return Job("overnight", py)

    # ---------- running ----------
    def tick(self) -> None:
        with self._lock:
            if self.proc is not None:
                if self.proc.poll() is not None:
                    self._finished(warm=not self.always)  # permanent mode: the next job starts soon
                elif not self.manual and not self.always and not in_window(self.clock(), *self.window):
                    self._stop("the night window is over")  # safety net; jobs also get a time limit
                elif why := self._memory_check():
                    self.blocked_until = time.time() + self.guard_pause_minutes * 60
                    self.last_reason = f"stopped: {why}"
                    self._stop(f"memory guard: {why}")
                return
            if self.pending_now and self.power():
                self._start_now(*self.pending_now)
                return
            ok, why = self.can_run()
            self.last_reason = why
            if not ok:
                return
            job = self.next_job()
            if job is None:
                return
            self._launch(job)

    def run_now(self, minutes: int = 180, finetune: bool = True) -> tuple[bool, str]:
        """Lea asked to train now (2026-11-07). Same job as at night; still plugged in only, and it
        stops the moment she talks to Sylvie (progress is saved per batch)."""
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return False, f"{self.current.name if self.current else 'a job'} is already running"
            if not self.power():
                self.pending_now = (int(minutes), finetune)
                return False, "on battery: it will start by itself as soon as the Mac is plugged in"
            guard = getattr(self.s, "guard", None)
            if guard is not None:
                from core.memwatch import GB, proc_usage

                mine = (proc_usage(os.getpid()) or {}).get("footprint", 0) / GB
                ok, why = guard.heavy_ok(self.job_memory_gb, freed_gb=mine)
                if not ok:
                    return False, f"not enough memory right now: {why}. Closing some apps would help"
            self._start_now(int(minutes), finetune)
            return True, f"started for up to {minutes} min"

    def _start_now(self, minutes: int, finetune: bool) -> None:
        args = [sys.executable, "-m", "learning.overnight", "--max-minutes", str(minutes)]
        self._launch(Job("overnight", args + (["--finetune"] if finetune else [])))
        self.manual, self.pending_now = True, None

    def _launch(self, job: Job) -> None:
        log.info("starting heavy job %s", job.name)
        self.s.bg.interrupt()  # background learning lets go of the model first (it's re-queued)
        self.s.unload_models()  # frees ~21 GB (LLM, Whisper, Kokoro, embeddings) for the job
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
        logf = open(self.s.cfg.data_dir / "logs" / f"{job.name}.log", "a")  # noqa: SIM115
        self.proc = subprocess.Popen(job.args, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
        self.current, self.started_at, self._tight_ticks = job, time.time(), 0
        self._pidfile().write_text(str(self.proc.pid))
        self._keep_awake(self.proc.pid)

    def _pidfile(self) -> Path:
        return Path(self.s.cfg.data_dir) / "logs" / "heavy-job.pid"

    leftover_grace = 20.0  # seconds a leftover job gets to save and exit before it's killed

    def stop_leftover(self) -> None:
        """A job started by a previous server (restarted meanwhile) would keep running unseen and
        double the memory use with the next one (seen 2026-11-07). Stop it; it saves progress."""
        try:
            pid = int(self._pidfile().read_text())
        except (OSError, ValueError):
            return
        cmd = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True
        ).stdout
        if "learning." in cmd:
            log.info("stopping a leftover heavy job (pid %s)", pid)
            os.kill(pid, signal.SIGTERM)
            # Like _stop: it gets 20 s to save and exit, then it's killed. Measured 2026-11-07: in the
            # middle of a long rewrite it didn't stop for 7+ minutes and slowed Lea's chat to minutes.
            end = time.time() + self.leftover_grace
            while time.time() < end:
                time.sleep(0.2)
                if not _alive(pid):
                    return
            log.warning("leftover heavy job %s didn't stop: killing it", pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def _keep_awake(self, pid: int) -> None:
        """The Mac doesn't fall asleep mid-training (only while this job runs, and only on power)."""
        if not Path("/usr/bin/caffeinate").exists() or "PYTEST_CURRENT_TEST" in os.environ:
            return
        try:
            subprocess.Popen(["/usr/bin/caffeinate", "-i", "-s", "-w", str(pid)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
        except OSError:
            pass

    def _finished(self, warm: bool = True) -> None:
        if self.started_at:
            self.used_tonight += time.time() - self.started_at
        log.info(
            "heavy job %s ended (code %s)", self.current.name if self.current else "?", self.proc.returncode
        )
        self.proc, self.current, self.started_at, self.manual = None, None, None, False
        if warm:
            threading.Thread(target=self.s.warm, daemon=True).start()  # assistant back, warm

    def preempt(self) -> bool:
        """Lea is talking to Sylvie: stop the heavy job now (it saves progress) and reload."""
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                return False
            self._stop("Lea is here")
            return True

    def _stop(self, why: str) -> None:
        log.info("%s: stopping %s", why, self.current.name if self.current else "job")
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._finished()

    def status(self) -> dict:
        return {
            "running": self.current.name if self.current else None,
            "reason_if_idle": self.last_reason,
            "used_tonight_min": round(self.used_tonight / 60, 1),
            "waiting_for_power": self.pending_now is not None,
            "window": self.window,
        }

    def start(self) -> None:
        self.stop_leftover()

        def loop():
            while True:
                try:
                    self.tick()
                except Exception:
                    log.exception("scheduler tick failed")
                time.sleep(self.check_every)

        self._thread = threading.Thread(target=loop, daemon=True, name="sylvie-scheduler")
        self._thread.start()
