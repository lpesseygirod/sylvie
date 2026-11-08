"""A memory timeline for the Mac (efficiency plan A1: "measure first").

Every minute, one JSON line in `logs/memory-YYYY-MM-DD.jsonl`:
- the Mac: macOS memory pressure (normal / warn / critical), free %, swap used, compressed memory;
- Sylvie: her own memory footprint (GPU included), which models are loaded, MLX's GPU memory,
  which background job, night job or indexing is running, and when Lea last talked to her;
- Lea: seconds since she last touched the keyboard or trackpad, and the biggest other apps
  (by memory footprint), so a freeze can be matched to what was open.

Reading these costs a few milliseconds (kernel counters through ctypes, no subprocess except
`ioreg` for the idle time). Nothing leaves the Mac. `scripts/memreport.py` summarises a day.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("sylvie.memwatch")
GB = 1024**3
PRESSURE = {1: "normal", 2: "warn", 4: "critical"}
# proc_name() cuts names at 32 characters; these are the ones that say little as they are.
FRIENDLY = {
    "com.apple.Virtualization.Virtua": "Virtual machine (Docker)",
    "com.apple.WebKit.WebContent": "Web pages (Safari/Sylvie/apps)",
    "python3.13": "Python (tests, evals, other scripts)",
}

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
try:
    _libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
except OSError:  # not macOS
    _libproc = None


def _sysctl_int(name: str) -> int | None:
    v = ctypes.c_int64(0)
    size = ctypes.c_size_t(8)
    if _libc.sysctlbyname(name.encode(), ctypes.byref(v), ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return None
    if size.value == 4:
        return ctypes.c_int32(v.value & 0xFFFFFFFF).value
    return v.value


class _SwapUsage(ctypes.Structure):
    _fields_ = [("total", ctypes.c_uint64), ("avail", ctypes.c_uint64), ("used", ctypes.c_uint64),
                ("pagesize", ctypes.c_uint32), ("encrypted", ctypes.c_bool)]  # fmt: skip


def swap_used() -> int | None:
    s = _SwapUsage()
    size = ctypes.c_size_t(ctypes.sizeof(s))
    if (
        _libc.sysctlbyname(b"vm.swapusage", ctypes.byref(s), ctypes.byref(size), None, ctypes.c_size_t(0))
        != 0
    ):
        return None
    return int(s.used)


# rusage_info_v4 (sys/resource.h): byte offsets of the fields we read.
_RUSAGE_V4 = 4
_OFF_FOOTPRINT = 72  # ri_phys_footprint: what Activity Monitor calls "Memory", GPU memory included
_OFF_WRITTEN = 152  # ri_diskio_byteswritten
_OFF_PEAK = 240  # ri_lifetime_max_phys_footprint


def proc_usage(pid: int) -> dict | None:
    """{"footprint", "peak", "written"} in bytes for one of Lea's processes, or None."""
    if _libproc is None:
        return None
    buf = (ctypes.c_uint8 * 512)()
    if _libproc.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(_RUSAGE_V4), buf) != 0:
        return None

    def u64(off):
        return int.from_bytes(bytes(buf[off : off + 8]), "little")

    return {"footprint": u64(_OFF_FOOTPRINT), "peak": u64(_OFF_PEAK), "written": u64(_OFF_WRITTEN)}


def proc_name(pid: int) -> str:
    if _libproc is None:
        return "?"
    buf = ctypes.create_string_buffer(256)
    _libproc.proc_name(ctypes.c_int(pid), buf, ctypes.c_uint32(256))
    return buf.value.decode(errors="replace") or "?"


def all_pids() -> list[int]:
    if _libproc is None:
        return []
    arr = (ctypes.c_int * 8192)()
    n = _libproc.proc_listallpids(arr, ctypes.sizeof(arr))
    return [arr[i] for i in range(max(n, 0)) if arr[i] > 0]


def top_processes(n: int = 6, skip: set[int] | None = None) -> list[list]:
    """The n biggest processes by footprint, helpers of one app added together
    ("Google Chrome Helper (Renderer)" counts as "Google Chrome"): [[name, GB], …]."""
    totals: dict[str, int] = {}
    for pid in all_pids():
        if skip and pid in skip:
            continue
        u = proc_usage(pid)
        if not u or u["footprint"] < 50 * 1024**2:
            continue
        name = re.sub(r" Helper.*$| \(.*\)$", "", proc_name(pid)).strip() or "?"
        name = FRIENDLY.get(name, name)
        totals[name] = totals.get(name, 0) + u["footprint"]
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:n]
    return [[k, round(v / GB, 2)] for k, v in top]


def idle_seconds() -> float | None:
    """Seconds since Lea last used the keyboard, mouse or trackpad."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
                             timeout=3).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return round(int(m.group(1)) / 1e9, 1) if m else None


def mac_memory() -> dict:
    level = _sysctl_int("kern.memorystatus_vm_pressure_level")
    swap = swap_used()
    comp = _sysctl_int("vm.compressor_bytes_used")
    return {
        "pressure": PRESSURE.get(level or 0, str(level)),
        "free_pct": _sysctl_int("kern.memorystatus_level"),
        "swap_gb": round(swap / GB, 2) if swap is not None else None,
        "compressed_gb": round(comp / GB, 2) if comp is not None else None,
    }


def mlx_memory() -> dict:
    try:
        import mlx.core as mx

        return {
            "active_gb": round(mx.get_active_memory() / GB, 2),
            "cache_gb": round(mx.get_cache_memory() / GB, 2),
            "peak_gb": round(mx.get_peak_memory() / GB, 2),
        }
    except Exception:
        return {}


def sylvie_state(s) -> dict:
    """What Sylvie has loaded and is doing (attributes are read without locks: a snapshot)."""
    if s is None:
        return {}
    loaded = []
    if getattr(s.llm, "model", None) is not None:
        loaded.append("llm")
    if s._stt is not None and getattr(s._stt, "_model", None) is not None:
        loaded.append("whisper")
    if s._tts is not None and getattr(s._tts, "_model", None) is not None:
        loaded.append("kokoro")
    if getattr(s.embedder, "_model", None) is not None:
        loaded.append("embeddings")
    bg = s.bg.current.name if s.bg.current is not None else None
    out = {
        "loaded": loaded,
        "bg_job": bg,
        "bg_waiting": s.bg.q.qsize() + s.bg.backlog.qsize(),
        "since_turn_s": round(time.time() - s.bg.last_activity) if s.bg.last_activity else None,
        "in_turn": s.bg.busy.is_set(),
    }
    sch = s.scheduler
    if sch is not None and sch.proc is not None and sch.proc.poll() is None:
        u = proc_usage(sch.proc.pid) or {}
        out["night_job"] = getattr(sch.current, "name", "?")
        out["night_job_gb"] = round(u.get("footprint", 0) / GB, 2)
    return out


def sample(sylvie=None) -> dict:
    me = proc_usage(os.getpid()) or {}
    skip = {os.getpid()}
    sch = getattr(sylvie, "scheduler", None)
    if sch is not None and sch.proc is not None:
        skip.add(sch.proc.pid)
    return {
        "t": datetime.now().isoformat(timespec="seconds"),
        "mac": mac_memory(),
        "sylvie_gb": round(me.get("footprint", 0) / GB, 2),
        "sylvie_peak_gb": round(me.get("peak", 0) / GB, 2),
        "sylvie_written_gb": round(me.get("written", 0) / GB, 2),
        "mlx": mlx_memory(),
        "state": sylvie_state(sylvie),
        "lea_idle_s": idle_seconds(),
        "top": top_processes(skip=skip),
    }


class MemWatch:
    """Writes one sample a minute (every 10 s while macOS reports memory pressure)."""

    def __init__(self, sylvie, folder: Path, every: float = 60.0, keep_days: int = 14):
        self.s = sylvie
        self.folder = Path(folder)
        self.every = every
        self.keep_days = keep_days
        self._stop = threading.Event()
        self._last_pressure = "normal"

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._run, daemon=True, name="sylvie-memwatch").start()

    def stop(self) -> None:
        self._stop.set()

    def write_one(self) -> dict:
        row = sample(self.s)
        path = self.folder / f"memory-{row['t'][:10]}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        p = row["mac"]["pressure"]
        if p != self._last_pressure:
            log.warning("memory pressure %s → %s: swap %s GB, Sylvie %s GB, state %s, top %s",
                        self._last_pressure, p, row["mac"]["swap_gb"], row["sylvie_gb"], row["state"],
                        row["top"][:3])  # fmt: skip
            self._last_pressure = p
        return row

    def _prune(self) -> None:
        cutoff = time.time() - self.keep_days * 86400
        for f in self.folder.glob("memory-*.jsonl"):
            if f.stat().st_mtime < cutoff:
                f.unlink()  # Sylvie's own measurement logs, not Lea's files

    def _run(self) -> None:
        self._prune()
        while not self._stop.is_set():
            try:
                row = self.write_one()
                tight = row["mac"]["pressure"] != "normal"
            except Exception:
                log.exception("memory sample failed")
                tight = False
            self._stop.wait(10 if tight else self.every)
