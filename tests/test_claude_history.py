import json
import zipfile

from memory.claude_history import ClaudeHistory, lea_text, read_export, read_new
from tests.test_orchestrator import extraction, make


def human(text, **kw):
    return {
        "type": "user",
        "origin": {"kind": "human"},
        "uuid": kw.pop("uuid", "u1"),
        "sessionId": "s1",
        "message": {"role": "user", "content": text},
        **kw,
    }


def test_only_what_lea_typed_is_kept():
    assert (
        lea_text(human("I like short answers in Spanish please")) == "I like short answers in Spanish please"
    )
    # Briefs from other Claude sessions, notifications, subagents, tool output, summaries: not hers.
    assert lea_text({**human("Advisor: 6.3 verified, continue"), "origin": None, "turnOrigin": "sdk"}) is None
    assert (
        lea_text(
            {**human("<task-notification>done</task-notification>"), "origin": {"kind": "task-notification"}}
        )
        is None
    )
    assert lea_text({**human("peer says hi there"), "origin": {"kind": "peer"}}) is None
    assert lea_text(human("subagent prompt text", isSidechain=True)) is None
    assert lea_text(human("This session is being continued", isCompactSummary=True)) is None
    tool = human([{"type": "tool_result", "content": "ok"}])
    assert lea_text(tool) is None
    assert lea_text({**human("an answer"), "type": "assistant"}) is None
    # Pasted text and system notes are cut out; her own words around them stay.
    pasted = '<pasted_content id="x">secret log line</pasted_content>\nwhy does this fail on my phone?'
    assert lea_text(human(pasted)) == "why does this fail on my phone?"
    assert lea_text(human("<system-reminder>ctx</system-reminder>ok")) is None  # too short once cleaned
    assert lea_text(human("/compact")) is None


def test_reads_only_new_whole_lines(tmp_path):
    proj = tmp_path / "-Users-lea-Studiply"
    proj.mkdir()
    f = proj / "s1.jsonl"
    f.write_text(json.dumps(human("I'm studying for my finance exam on Friday")) + "\n")
    offsets: dict = {}
    assert [m.text for m in read_new(tmp_path, offsets, set())] == [
        "I'm studying for my finance exam on Friday"
    ]
    assert read_new(tmp_path, offsets, set()) == []
    line = json.dumps(human("My sister Ana lives in Valencia now", uuid="u2"))
    with open(f, "a") as fh:
        fh.write(line[:20])  # Claude Code is mid-write
    assert read_new(tmp_path, offsets, set()) == []
    with open(f, "a") as fh:
        fh.write(line[20:] + "\n")
    assert [m.uuid for m in read_new(tmp_path, offsets, set())] == ["u2"]
    assert read_new(tmp_path, {}, {"-Users-lea-Studiply"}) == []  # excluded project


def test_scan_learns_facts_and_suggests_rules_switched_off(tmp_path):
    pref = '{"is_preference": true, "preference": "Keep answers short.", "replaces": [], "ack": "Ok"}'
    fact = {
        "key": "lea.project",
        "fact": "Lea is building a study app called Studiply",
        "category": "projects",
    }
    s = make(tmp_path, [pref, extraction([fact])])
    folder = tmp_path / "claude"
    (folder / "proj").mkdir(parents=True)
    (folder / "proj" / "a.jsonl").write_text(
        json.dumps(human("too long, I prefer shorter answers for my Studiply app work")) + "\n"
    )
    s.cfg["memory"] = {**s.cfg["memory"], "claude_history": {"enabled": True, "folder": str(folder)}}
    h = ClaudeHistory(s)
    assert h.scan() == 1
    s.bg.drain()
    prefs = s.prefs.list(include_inactive=True)
    assert [(p.text, p.scope, p.active) for p in prefs] == [("Keep answers short.", "claude", False)]
    assert "Keep answers short." not in s.current_system_prompt()  # not used until Lea turns it on
    facts = s.memory.list()
    assert facts[0].text.startswith("Lea is building") and facts[0].source.startswith("claude:s1#")
    assert "written to Claude" in s.llm.calls[1]["messages"][0]["content"]  # extraction knows the context
    assert h.scan() == 0  # nothing new
    assert s.convs.list() == []  # nothing copied into Sylvie's conversations


def test_background_learning_waits_while_a_night_job_runs(tmp_path):
    s = make(tmp_path, [])

    class Sched:
        proc = object()

    s.scheduler = Sched()
    assert s.bg.hold()
    s.scheduler.proc = None
    assert not s.bg.hold()


def claude_ai_export(path, messages):
    conv = {"uuid": "c1", "name": "Trip", "chat_messages": messages}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps([conv]))
        z.writestr("users.json", "[]")


def test_claude_ai_export_keeps_only_leas_turns(tmp_path):
    msgs = [
        {
            "uuid": "m1",
            "sender": "human",
            "text": "I'm moving to Barcelona in January",
            "attachments": [{"extracted_content": "lease"}],
        },
        {"uuid": "m2", "sender": "assistant", "text": "Congratulations! Here is a checklist."},
        {
            "uuid": "m3",
            "sender": "human",
            "text": "",
            "content": [{"type": "text", "text": "Explain it simply, no jargon"}],
        },
    ]
    claude_ai_export(tmp_path / "data-2026.zip", msgs)
    assert [m.text for m in read_export(tmp_path / "data-2026.zip")] == [
        "I'm moving to Barcelona in January",
        "Explain it simply, no jargon",
    ]
    other = tmp_path / "chatgpt.json"
    other.write_text(json.dumps([{"title": "x", "mapping": {}}]))  # another app's format
    assert read_export(other) == []


def test_export_folder_is_read_once_and_re_exports_skip_old_messages(tmp_path):
    s = make(tmp_path, [extraction()] * 4)
    exports = tmp_path / "exports"
    exports.mkdir()
    s.cfg["memory"] = {
        **s.cfg["memory"],
        "claude_history": {"enabled": True, "folder": str(tmp_path / "none"), "exports_folder": str(exports)},
    }
    h = ClaudeHistory(s)
    first = [{"uuid": "m1", "sender": "human", "text": "I'm learning to play the cello"}]
    claude_ai_export(exports / "data-1.zip", first)
    assert h.scan() == 1
    assert h.scan() == 0
    second = first + [{"uuid": "m9", "sender": "human", "text": "My cello teacher is called Marta"}]
    claude_ai_export(exports / "data-2.zip", second)  # a newer export holds the whole history again
    assert h.scan() == 1
    s.bg.drain()
