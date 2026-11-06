import subprocess

import tools.claude as cl
from tests.test_orchestrator import make, run


def test_only_when_lea_asks_for_claude():
    assert cl.asked_for_claude("Ask Claude to review my essay intro")
    assert cl.asked_for_claude("Pregúntale a Claude qué opina de esto")
    assert cl.asked_for_claude("délègue à cloud la question")  # how speech recognition may spell it
    assert not cl.asked_for_claude("Is iCloud storage worth it?")
    assert not cl.asked_for_claude("What's the weather today?")


def test_claude_runs_without_tools_or_files(tmp_path):
    seen = {}

    def fake_run(args, cwd, stdin, capture_output, text, timeout, env):
        seen.update(args=args, cwd=cwd)
        return subprocess.CompletedProcess(args, 0, "Canberra.", "")

    ok, out = cl.ask("Capital of Australia?", tmp_path / "w", cli="/bin/claude", run=fake_run)
    assert ok and out == "Canberra."
    a = seen["args"]
    assert a[a.index("--tools") + 1] == "" and "--strict-mcp-config" in a and "--no-session-persistence" in a
    assert seen["cwd"] == tmp_path / "w"


def test_a_handover_in_a_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr(cl, "ask", lambda task, workdir, **kw: (True, f"Claude says: done ({task})"))
    call = "<tool_call>\n<function=ask_claude>\n<parameter=task>\nSummarise the plot of Hamlet in two lines\n</parameter>\n</function>\n</tool_call>"
    s = make(tmp_path, [call, "Claude dice: Hamlet venga a su padre…", "{}"])
    ev = run(s, "Pregúntale a Claude un resumen de Hamlet")
    assert any(e["type"] == "status" and "Claude" in (e.get("text") or "") for e in ev)
    tool = next(e for e in ev if e["type"] == "tool")
    assert tool["name"] == "ask_claude" and tool["ok"]


def test_not_sent_when_lea_did_not_ask(tmp_path, monkeypatch):
    from core.toolcall import ToolCall
    from tools.registry import execute

    monkeypatch.setattr(
        cl, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called"))
    )
    s = make(tmp_path, [])
    out = execute(ToolCall("ask_claude", {"task": "anything"}), s.tool_context("What's 2+2?"))
    assert not out.ok and "Not sent" in out.content


def test_answers_from_claude_never_become_training_data(tmp_path):
    s = make(tmp_path, [])
    cid = s.convs.create(title="t")["id"]
    s.convs.add_message(cid, "user", "Pregúntale a Claude por Hamlet", channel="chat")
    s.convs.add_message(
        cid, "assistant", "", tool_calls=[{"function": {"name": "ask_claude", "arguments": {}}}]
    )
    s.convs.add_message(cid, "tool", "", model_content="Claude's answer: …")
    mid = s.convs.add_message(cid, "assistant", "Claude dice: …", channel="chat")
    assert s.convs.turn_used_tool(mid, "ask_claude")
    corr = s.learning.add_correction(
        "wrong", "Hamlet?", "Claude dice: …", message_id=mid, conversation_id=cid
    )
    s.learning.update_correction(corr, status="proposed", proposal="Una versión mejor.")
    s.teacher.accept(corr)
    assert s.learning.examples(status=None) == []
