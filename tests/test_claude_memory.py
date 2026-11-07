import json
import zipfile

from memory.claude_memory import ClaudeMemory, read_claude_ai_memories, read_claude_code_notes, split_notes
from tests.test_orchestrator import make

MEMORY_TEXT = """**Work context**
Lea is building Sylvie, a private assistant that runs on her Mac.

**Personal context**
- Lea lives in Madrid and speaks English, Spanish and French.
- She studies CS and BBA.
"""


def memories_zip(path, text=MEMORY_TEXT):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "memories.json",
            json.dumps(
                [
                    {
                        "conversations_memory": text,
                        "project_memories": {"p1": "Studiply is a study app for students."},
                        "account_uuid": "a-very-long-uuid-0000000000",
                    }
                ]
            ),
        )


def code_note(folder, project, name, description, kind="feedback"):
    d = folder / project / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f'---\nname: {name}\ndescription: "a summary"\nmetadata:\n  type: {kind}\n---\n\n{description}\n'
    )


def test_memory_text_becomes_short_notes_without_headings():
    assert split_notes(MEMORY_TEXT) == [
        "Lea is building Sylvie, a private assistant that runs on her Mac.",
        "Lea lives in Madrid and speaks English, Spanish and French.",
        "She studies CS and BBA.",
    ]


def test_reads_claude_ai_memories_and_claude_code_notes(tmp_path):
    memories_zip(tmp_path / "memories-000.zip")
    notes = read_claude_ai_memories(tmp_path / "memories-000.zip")
    assert "Studiply is a study app for students." in notes and len(notes) == 4  # ids skipped
    code_note(
        tmp_path / "claude",
        "-Users-lea-Studiply",
        "about-lea",
        "Lea studies CS and BBA and builds apps on her Mac.",
        kind="user",
    )
    for kind in ("feedback", "project", "reference"):  # coding instructions, not about Lea
        code_note(
            tmp_path / "claude", "-Users-lea-Studiply", kind, "Never write chart drawing code here.", kind
        )
    (tmp_path / "claude" / "-Users-lea-Studiply" / "memory" / "MEMORY.md").write_text("- [x](x.md) — index")
    assert read_claude_code_notes(tmp_path / "claude") == [
        ("Lea studies CS and BBA and builds apps on her Mac.", "-Users-lea-Studiply")
    ]
    assert read_claude_code_notes(tmp_path / "claude", {"-Users-lea-Studiply"}) == []


def test_suggestions_are_unused_until_lea_keeps_them(tmp_path):
    s = make(tmp_path, [])
    exports = tmp_path / "exports"
    exports.mkdir()
    memories_zip(exports / "memories-000.zip")
    cm = ClaudeMemory(s)
    assert cm.scan(tmp_path / "none", exports) == 4
    assert cm.scan(tmp_path / "none", exports) == 0  # read again: nothing new
    assert s.memory.list() == [] and "Lea lives in Madrid" not in s.current_system_prompt()

    by_text = {g["text"]: g["id"] for g in cm.list()}
    out = cm.decide(by_text["Lea lives in Madrid and speaks English, Spanish and French."], "fact")
    assert out["outcome"] == "added"
    assert [(f.text, f.source) for f in s.memory.list()] == [
        ("Lea lives in Madrid and speaks English, Spanish and French.", "claude-memory")
    ]
    cm.decide(
        by_text["She studies CS and BBA."], "rule", text="Explain things for someone who studies CS and BBA."
    )
    assert "Explain things for someone who studies CS and BBA." in s.current_system_prompt()
    cm.decide(by_text["Studiply is a study app for students."], "dismissed")
    assert len(cm.list()) == 1
    memories_zip(exports / "memories-001.zip")  # a newer export: dismissed notes don't come back
    assert cm.scan(tmp_path / "none", exports) == 0 and len(cm.list()) == 1


def test_training_never_includes_a_system_prompt(tmp_path):
    from learning.dataset import build
    from learning.store import LearningStore

    s = make(tmp_path, [])
    store = LearningStore(s.db)
    sys_msgs = [
        {"role": "system", "content": "What you know about Lea:\n- a note Claude wrote"},
        {"role": "user", "content": "hi there, how are you"},
    ]
    store.add_example(sys_msgs, "Fine, thanks!", source="sylvie-local:test", license="local", tag="general")
    report = build(store, tmp_path / "out", name="b")
    assert report.excluded["system_prompt"] == 1
