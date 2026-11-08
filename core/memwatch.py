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


def descendants(pid: int) -> list[int]:
    """pid and every process it started (the night job runs `mlx_lm lora` as a child)."""
    try:
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True,
                             timeout=3).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return [pid]
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    tree, todo = [], [pid]
    while todo:
        p = todo.pop()
        tree.append(p)
        todo += kids.get(p, [])
    return tree


def tree_footprint(pid: int) -> int:
    """Memory footprint of a process and all its children, in bytes."""
    return sum((proc_usage(p) or {}).get("footprint", 0) for p in descendants(pid))


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
        # Full path: the server's launchd PATH has no /usr/sbin (the A1 timeline had no idle times).
        out = subprocess.run(["/usr/sbin/ioreg", "-r", "-k", "HIDIdleTime", "-d", "1"], capture_output=True,
                             text=True, timeout=3).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return round(int(m.group(1)) / 1e9, 1) if m else None


def page_counts() -> dict[str, int]:
    """`vm_stat` in bytes: {"free", "speculative", "purgeable", "file_backed", "occupied_by_compressor", …}."""
    try:
        out = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    m = re.search(r"page size of (\d+) bytes", out)
    page = int(m.group(1)) if m else 16384
    counts = {}
    for name, n in re.findall(r'^"?([A-Za-z][^:"]+)"?:\s+(\d+)\.', out, re.M):
        key = re.sub(r"\bpages\b", "", name.lower()).strip().replace(" ", "_").replace("-", "_")
        counts[key] = int(n) * page
    return counts


def available_bytes(pages: dict[str, int] | None = None) -> int | None:
    """Memory a new program can take without macOS compressing or swapping others: free pages plus
    cached files (what Activity Monitor shows as free + "Cached Files"). Measured 2026-11-08 23:55:
    macOS's own "free %" said 38% (13.7 GB) while 18 GB of RAM held compressed pages and only
    ~4.7 GB was really free, so the percentage alone would let a 20 GB job start into a full Mac."""
    p = page_counts() if pages is None else pages
    if not p:
        return None
    return sum(p.get(k, 0) for k in ("free", "speculative", "purgeable", "file_backed"))


def mac_memory() -> dict:
    level = _sysctl_int("kern.memorystatus_vm_pressure_level")
    swap = swap_used()
    comp = _sysctl_int("vm.compressor_bytes_used")
    pages = page_counts()
    avail = available_bytes(pages)
    held = pages.get("occupied_by_compressor")
    return {
        "pressure": PRESSURE.get(level or 0, str(level)),
        "free_pct": _sysctl_int("kern.memorystatus_level"),
        "available_gb": round(avail / GB, 2) if avail is not None else None,
        "swap_gb": round(swap / GB, 2) if swap is not None else None,
        "compressed_gb": round(comp / GB, 2) if comp is not None else None,
        "compressor_ram_gb": round(held / GB, 2) if held is not None else None,
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
        out["night_job"] = getattr(sch.current, "name", "?")
        out["night_job_gb"] = round(tree_footprint(sch.proc.pid) / GB, 2)  # training runs as its child
    return out


def sample(sylvie=None) -> dict:
    me = proc_usage(os.getpid()) or {}
    skip = {os.getpid()}
    sch = getattr(sylvie, "scheduler", None)
    if sch is not None and sch.proc is not None:
        skip.update(descendants(sch.proc.pid))
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
