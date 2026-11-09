"""The memory guard (efficiency plan A2): heavy work only when the Mac has room and Lea isn't using it.

Measured 2026-11-08 (docs/reports/a1-memory-2026-11-08.md): the freezes came from heavy work
running next to Lea's own apps (~20–25 GB: Docker, browsers, editors). The guard reads macOS's
own memory pressure and free memory, and how long ago Lea touched the keyboard or trackpad:

- backlog jobs (indexing documents, reading old Claude chats, app downloads) start only when
  pressure is normal, enough memory is free and Lea has been away from the Mac for a few
  minutes; a running one is paused (and re-queued) as soon as that stops being true;
- the night job starts only when the memory it needs is free (after Sylvie lets go of hers),
  and is stopped if it grows past a hard cap or macOS reports pressure;
- Whisper and Kokoro are loaded when voice is used and unloaded after a quiet spell.

Readings are cached for a few seconds: the background worker asks often, and each reading
costs ~20 ms (kernel counters plus one `ioreg` call).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from core import memwatch

log = logging.getLogger("sylvie.memguard")
GB = 1024**3


def total_memory() -> int:
    return memwatch._sysctl_int("hw.memsize") or 36 * GB


@dataclass
class Reading:
    pressure: str  # normal / warn / critical
    free_pct: int | None
    free_gb: float
    lea_idle_s: float | None  # None: unknown (not on a Mac)

    @property
    def tight(self) -> bool:
        return self.pressure != "normal"


def read_now() -> Reading:
    level = memwatch._sysctl_int("kern.memorystatus_vm_pressure_level")
    avail = memwatch.available_bytes()
    free_gb = avail / GB if avail is not None else 99.0  # unknown (not a Mac): don't block
    return Reading(memwatch.PRESSURE.get(level or 1, str(level)), memwatch._sysctl_int("kern.memorystatus_level"),
                   round(free_gb, 1), memwatch.idle_seconds())  # fmt: skip


class MemoryGuard:
    def __init__(self, cfg, reader=read_now, cache_seconds: float = 5.0):
        g = cfg.get_path("memguard", {}) or {}
        self.enabled = bool(g.get("enabled", True))
        self.lea_away_minutes = float(g.get("lea_away_minutes", 5))
        self.min_free_gb = float(g.get("backlog_min_free_gb", 6))
        # Measured 2026-11-09 01:00–02:00: indexing started at 6 GB free, its own reading dropped it
        # below 6, it was paused, and it started again: 105 pauses in an hour, each redoing a file.
        # So a job needs 2 GB more to start than to keep going, and waits 10 minutes after a pause.
        self.start_margin_gb = float(g.get("start_margin_gb", 2))
        self.pause_cooldown = float(g.get("pause_cooldown_minutes", 10)) * 60
        self.paused_at = 0.0
        self.reader = reader
        self.cache_seconds = cache_seconds
        self._last: tuple[float, Reading] | None = None
        self._lock = threading.Lock()

    def reading(self) -> Reading:
        with self._lock:
            now = time.time()
            if self._last is None or now - self._last[0] > self.cache_seconds:
                self._last = (now, self.reader())
            return self._last[1]

    def backlog_ok(self, starting: bool = True) -> tuple[bool, str]:
        """May a backlog job (indexing, history, downloads) start (`starting`) or keep running?"""
        if not self.enabled:
            return True, "guard off"
        if starting and time.time() - self.paused_at < self.pause_cooldown:
            return False, "paused a few minutes ago"
        r = self.reading()
        if r.tight:
            return False, f"memory pressure is {r.pressure}"
        if r.free_gb < self.min_free_gb + (self.start_margin_gb if starting else 0):
            return False, f"only {r.free_gb:.0f} GB free"
        if r.lea_idle_s is not None and r.lea_idle_s < self.lea_away_minutes * 60:
            return False, "Lea is using the Mac"
        return True, "ok"

    def heavy_ok(self, need_gb: float, freed_gb: float = 0.0) -> tuple[bool, str]:
        """May a job that needs `need_gb` start, once Sylvie has freed `freed_gb` of her own?"""
        if not self.enabled:
            return True, "guard off"
        r = self.reading()
        if r.pressure == "critical":
            return False, "memory pressure is critical"
        room = r.free_gb + freed_gb
        if room < need_gb:
            return (
                False,
                f"the job needs {need_gb:.0f} GB, only ~{room:.1f} GB would be free (other apps open)",
            )
        return True, "ok"


class GuardLoop:
    """Every few seconds: pause a backlog job when the guard says no, and unload Whisper/Kokoro
    after `voice.unload_after_minutes` without voice (2 minutes while memory is tight)."""

    def __init__(self, sylvie, guard: MemoryGuard, every: float = 10.0):
        self.s = sylvie
        self.guard = guard
        self.every = every
        self.voice_ttl = float(sylvie.cfg.get_path("voice.unload_after_minutes", 10)) * 60
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="sylvie-memguard").start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        job = self.s.bg.current
        if job is not None and job.backlog_job and not job.stop.is_set():
            ok, why = self.guard.backlog_ok(starting=False)
            if not ok:
                self.guard.paused_at = time.time()
                log.info("pausing %s: %s", job.name, why)
                job.stop.set()  # backlog jobs are re-queued, never dropped
        tight = self.guard.reading().tight if self.guard.enabled else False
        ttl = min(self.voice_ttl, 120) if tight else self.voice_ttl
        if self.s.voice_idle_seconds() > ttl and self.s.unload_voice():
            log.info("voice models unloaded after %.0f quiet minutes", ttl / 60)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("memory guard tick failed")
            self._stop.wait(self.every)
