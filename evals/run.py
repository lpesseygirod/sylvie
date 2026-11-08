"""Runs the evaluation sets against a model version and scores them.

    make eval                         # current base model (+ active adapter)
    uv run python evals/run.py --label v001 --adapter v001 --sets math,finance

Each run uses a throwaway database and a fake app controller, so it never touches Lea's memory
or apps. Results (answers + scores + timings) go to ~/Library/Application Support/Sylvie/evals/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent import says_no_access  # noqa: E402
from core.lang import PHRASES, guess_language, reply_language  # noqa: E402

SETS_DIR = Path(__file__).parent / "sets"
BANNED = re.compile(
    r"as an ai (language )?model|como (una )?ia|en tant qu'ia|as a large language model", re.I
)
EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf]")


# ---------- scoring ----------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def number_candidates(text: str) -> list[float]:
    """All plausible numbers in a text, trying both '1.234,5' and '1,234.5' conventions."""
    out = []
    text = text.replace("−", "-").replace("–", "-")  # typographic minus / en dash
    for tok in re.findall(r"-?\d[\d.,   ]*\d|-?\d", text):
        t = re.sub(r"[   ]", "", tok)
        variants = {t.replace(",", ""), t.replace(".", "").replace(",", ".")}
        if t.count(",") == 1 and "." not in t:
            variants.add(t.replace(",", "."))
        for v in variants:
            try:
                out.append(float(v))
            except ValueError:
                pass
    return out


_CHOICE = [
    re.compile(r"answer is[:\s]*\**\(?([A-D])\b"),
    re.compile(r"^\W*\(?([A-D])\)?(?:[).:*\s]|$)", re.M),
    re.compile(r"\*\*\(?([A-D])\)?[).:]?"),
    re.compile(r"\b([A-D])\)"),
]


def chosen_letter(text: str) -> str | None:
    for pat in _CHOICE:
        if m := pat.search(text or ""):
            return m.group(1)
    return None


def score(item: dict, answer: str, tools: list[str]) -> bool | None:
    c = item["check"]
    kind = c["type"]
    if kind == "number":
        tol = c.get("tol", 0.0)
        target = float(c["value"])
        return any(abs(x - target) <= max(tol, abs(target) * 1e-6) for x in number_candidates(answer))
    if kind == "contains":
        value = str(c["value"])
        if re.fullmatch(r"-?\d+(\.\d+)?", value):  # numbers: "3.600" / "3 600" / "3600" all count
            ok = float(value) in number_candidates(answer)
        else:
            ok = _norm(value) in _norm(answer)
        if ok and c.get("lang"):
            ok = guess_language(answer, default=c["lang"]) == c["lang"]
        return ok
    if kind == "choice":  # multiple choice (MMLU): the letter she picked
        return chosen_letter(answer) == c["value"]
    if kind == "tool":
        first = tools[0] if tools else None
        return first == c["value"]
    if kind == "style":
        words = len(re.findall(r"\w+", answer))
        same_lang = guess_language(answer) == guess_language(item["prompt"])
        emoji = bool(EMOJI.search(answer)) and not EMOJI.search(item["prompt"])
        return words <= c.get("max_words", 120) and not BANNED.search(answer) and same_lang and not emoji
    if kind == "everyday":
        return not everyday_problems(item, answer, tools)
    return None  # "judge" items are rated by Lea in the blind A/B


def everyday_problems(item: dict, answer: str, tools: list[str]) -> list[str]:
    """What went wrong with an everyday question (empty: she did it)."""
    c, problems = item["check"], []
    if c.get("tool") and c["tool"] not in tools:
        problems.append(f"didn't call {c['tool']} (called {', '.join(tools) or 'nothing'})")
    # The opening sentence tells best: lists of Spanish TV shows in an English answer aren't Spanish.
    first = re.split(r"(?<=[.!?:])\s|\n", answer.strip())[0]
    lang = reply_language(first) or reply_language(answer) or guess_language(answer, default="?")
    if c.get("lang") and lang != c["lang"]:
        problems.append(f"answered in {lang}, not {c['lang']}")
    if says_no_access(answer):
        problems.append("said she has no access")
    if c.get("contains") and _norm(c["contains"]) not in _norm(answer):
        if not (re.fullmatch(r"[\d.,]+", c["contains"]) and _num(c["contains"]) in number_candidates(answer)):
            problems.append(f"no '{c['contains']}' in the answer")
    if not answer.strip():
        problems.append("empty answer")
    elif any(answer.strip().startswith(t) for t in PHRASES["lost"].values()):
        problems.append("gave up ('I got lost in what I found')")
    return problems


def _num(s: str) -> float:
    return float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)


# ---------- running ----------
def load_items(sets: list[str], limit: int | None) -> list[dict]:
    items = []
    for name in sets:
        path = SETS_DIR / f"{name}.jsonl"
        if not path.exists():
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        items += rows[:limit] if limit else rows
    return items


def build_sylvie(adapter: str | None, model: str | None):
    import subprocess

    from actions.log import ActionLog
    from actions.osa import FakeOSA
    from actions.runner import ActionRunner
    from core.config import Config, load_config
    from core.db import Database
    from core.llm import LLM
    from core.orchestrator import Sylvie
    from knowledge.embeddings import Embedder

    cfg = Config(json.loads(json.dumps(load_config())))
    if model:
        cfg["llm"]["model"] = model
    cfg["llm"]["adapter"] = adapter
    cfg["voice"]["enabled"] = False  # text-only evals: don't load Whisper/Kokoro next to the model
    tmp = Path(tempfile.mkdtemp(prefix="sylvie-eval-"))
    dim = cfg.get_path("embeddings.dim", 1024)
    db = Database(tmp / "eval.db", dim=dim)
    runner = ActionRunner(
        cfg,
        ActionLog(db),
        osa=FakeOSA(),
        run_cmd=lambda a: subprocess.CompletedProcess(a, 0, "", ""),
        is_locked=lambda: False,
    )
    s = Sylvie(cfg, LLM(cfg), Embedder(cfg.get_path("embeddings.model"), dim=dim), db, actions=runner)
    s.bg.submit = lambda *a, **k: None  # no background learning during evals
    return s


def run(
    label: str,
    sets: list[str],
    adapter: str | None = None,
    model: str | None = None,
    limit: int | None = None,
) -> dict:
    s = build_sylvie(adapter, model)
    s.warm()
    results = []
    for item in load_items(sets, limit):
        for fact in item.get("facts", []):
            s.memory.add(fact, source="eval")
        cid = None
        for before in item.get("before", []):  # earlier turns of the same conversation
            for ev in s.turn(before, conversation_id=cid):
                cid = ev["conversation_id"] if ev["type"] == "meta" else cid
        t0 = time.perf_counter()
        answer, tools, first = "", [], None
        for ev in s.turn(
            item["prompt"],
            conversation_id=cid,
            channel=item.get("channel", "chat"),
            language=item.get("heard"),
        ):
            if ev["type"] == "text":
                first = first or time.perf_counter() - t0
                answer += ev["text"]
            elif ev["type"] == "tool":
                tools.append(ev["name"])
        ok = score(item, answer, tools)
        results.append(
            {"id": item["id"], "set": item["set"], "prompt": item["prompt"], "answer": answer, "tools": tools, "ok": ok,
             "first_word_s": round(first or 0, 2), "total_s": round(time.perf_counter() - t0, 2)}
        )  # fmt: skip
        mark = {True: "✓", False: "✗", None: "·"}[ok]
        why = (
            "  <- " + "; ".join(everyday_problems(item, answer, tools))
            if ok is False and item["check"]["type"] == "everyday"
            else ""
        )
        print(f"{mark} {item['id']:<10} {answer[:90]!r}{why}", flush=True)
    summary = {}
    for r in results:
        sm = summary.setdefault(r["set"], {"n": 0, "scored": 0, "correct": 0})
        sm["n"] += 1
        if r["ok"] is not None:
            sm["scored"] += 1
            sm["correct"] += int(r["ok"])
    for sm in summary.values():
        sm["accuracy"] = round(sm["correct"] / sm["scored"], 3) if sm["scored"] else None
    report = {
        "label": label,
        "model": s.cfg.get_path("llm.model"),
        "adapter": adapter,
        "date": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "results": results,
    }
    out_dir = s.cfg.data_dir / "evals"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{label}-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"\nSaved {out}")
    for name, sm in summary.items():
        print(f"  {name:<14} {sm['correct']}/{sm['scored']}  ({sm['accuracy']})")
    return report


def compare(old: dict, new: dict, tolerance: float = 0.05) -> dict:
    """Regressions: any set where the new version scores clearly below the old one."""
    rows, regressions = [], []
    for name, o in old["summary"].items():
        n = new["summary"].get(name)
        if not n or o["accuracy"] is None or n["accuracy"] is None:
            continue
        delta = round(n["accuracy"] - o["accuracy"], 3)
        rows.append({"set": name, "old": o["accuracy"], "new": n["accuracy"], "delta": delta})
        if delta < -tolerance:
            regressions.append(name)
    return {"rows": rows, "regressions": regressions}


def rescore(path: Path) -> dict:
    """Re-scores a saved run with the current scorer (no model needed)."""
    report = json.loads(path.read_text())
    items = {i["id"]: i for i in load_items(sorted({r["set"] for r in report["results"]}), None)}
    for r in report["results"]:
        if r["id"] in items:
            r["ok"] = score(items[r["id"]], r["answer"], r["tools"])
    summary = {}
    for r in report["results"]:
        sm = summary.setdefault(r["set"], {"n": 0, "scored": 0, "correct": 0})
        sm["n"] += 1
        if r["ok"] is not None:
            sm["scored"] += 1
            sm["correct"] += int(r["ok"])
    for sm in summary.values():
        sm["accuracy"] = round(sm["correct"] / sm["scored"], 3) if sm["scored"] else None
    report["summary"] = summary
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescore", default=None, help="re-score a saved results file")
    ap.add_argument("--label", default="base")
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--sets", default="math,finance,multilingual,tools,general,style,memory,lea_questions")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.rescore:
        for name, sm in rescore(Path(a.rescore))["summary"].items():
            print(f"  {name:<14} {sm['correct']}/{sm['scored']}  ({sm['accuracy']})")
        return
    run(a.label, a.sets.split(","), a.adapter, a.model, a.limit)


if __name__ == "__main__":
    main()
