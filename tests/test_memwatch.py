"""The memory timeline (core/memwatch.py) and its day summary (scripts/memreport.py)."""

import json
import os
import sys
from pathlib import Path

import pytest

from core import memwatch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import memreport  # noqa: E402

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS kernel counters")


@mac_only
def test_reads_the_macs_memory_and_this_process():
    m = memwatch.mac_memory()
    assert m["pressure"] in {"normal", "warn", "critical"}
    assert 0 <= m["free_pct"] <= 100 and m["swap_gb"] >= 0
    me = memwatch.proc_usage(os.getpid())
    assert me["footprint"] > 1024**2 and me["peak"] >= me["footprint"]
    assert memwatch.proc_name(os.getpid()).startswith("python")
    top = memwatch.top_processes(3)
    assert len(top) <= 3 and all(isinstance(gb, float) for _, gb in top)


def test_sample_with_sylvie_state(tmp_path):
    from tests.test_orchestrator import make

    s = make(tmp_path, [])
    s.bg.current = type("J", (), {"name": "index-documents"})()
    w = memwatch.MemWatch(s, tmp_path / "logs")
    w.folder.mkdir()
    row = w.write_one()
    s.bg.current = None
    assert row["state"]["bg_job"] == "index-documents"
    assert row["state"]["in_turn"] is False
    files = list((tmp_path / "logs").glob("memory-*.jsonl"))
    assert len(files) == 1 and json.loads(files[0].read_text())["t"] == row["t"]


def test_day_summary_weights_tight_minutes():
    def row(t, p, job=None, swap=1.0):
        return {"t": f"2026-11-08T{t}", "mac": {"pressure": p, "free_pct": 40, "swap_gb": swap},
                "sylvie_gb": 9.6, "mlx": {"cache_gb": 1.0}, "state": {"bg_job": job}, "top": []}  # fmt: skip

    rows = [row("10:00:00", "normal"), row("10:01:00", "warn", "index-documents", 6.0),
            row("10:01:10", "warn", "index-documents", 7.0), row("10:01:20", "normal")]  # fmt: skip
    s = memreport.summary(rows)
    assert s["minutes"]["normal"] == 2 and s["minutes"]["warn"] == 0  # 20 s of warn rounds to 0 min
    assert "index-documents" in s["jobs_while_tight_min"]
    assert s["swap_gb"]["max"] == 7.0 and s["worst"]["t"].endswith("10:01:10")
    assert memreport.summary([]) == {"samples": 0}
