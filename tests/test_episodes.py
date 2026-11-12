from memory.episodes import Episodes
from tests.test_orchestrator import extraction, make, run


def test_an_earlier_conversation_is_remembered_in_a_new_one(tmp_path):
    s = make(tmp_path, ["Great choice, the Sagrada Família tickets sell out fast.", extraction(), "{}"])
    old = run(s, "I'm going to Barcelona in March to see the Sagrada Família")[0]["conversation_id"]
    s.bg.drain()
    assert s.db.one("SELECT count(*) n FROM episodes")["n"] == 1
    s.llm.replies = ["You told me you're going to Barcelona in March.", extraction(), "{}"]
    run(s, "Where did I say I'm going to Barcelona to see the Sagrada Família?")
    # Everything she read for that answer (her message, and the earlier conversations searched first)
    prompt = next(
        "\n".join(str(m["content"]) for m in c["messages"])
        for c in s.llm.calls
        if any("Barcelona to see" in str(m["content"]) for m in c["messages"])
    )
    assert (
        "From earlier conversations" in prompt and "Sagrada Família tickets" in prompt and "today" in prompt
    )
    assert old


def test_search_tool_still_remembers_deleted_conversations(tmp_path):
    """Lea, 2026-11-07: deleting a conversation must not make Sylvie lose what she learned from it."""
    s = make(tmp_path, [])
    keep = s.convs.create(title="a")["id"]
    gone = s.convs.create(title="b")["id"]
    s.episodes.add(keep, None, "My sister's birthday is on 3 May", "Noted!", "2026-09-04T10:00:00+00:00")
    s.episodes.add(gone, None, "My brother's birthday is on 4 June", "Noted!", "2026-09-05T10:00:00+00:00")
    s.convs.delete(gone)
    assert [c["id"] for c in s.convs.list()] == [keep]  # hidden from the list…
    found = s.episodes.search("sister birthday", k=5)
    assert found[0].conversation_id == keep
    assert "2026-09-04" in found[0].line() and "months ago" in found[0].line()
    from core.toolcall import ToolCall
    from tools.registry import execute

    out = execute(ToolCall("search_past_conversations", {"query": "brother birthday"}), s.tool_context("x"))
    assert "4 June" in out.content  # …but still remembered


def test_facts_and_episodes_survive_deleting_the_conversation(tmp_path):
    s = make(
        tmp_path,
        [
            "Nice!",
            extraction([{"key": "lea.pet", "fact": "Lea has a cat named Miso", "category": "people"}]),
            "{}",
        ],
    )
    cid = run(s, "I have a cat called Miso")[0]["conversation_id"]
    s.bg.drain()
    s.convs.delete(cid)
    assert [f.text for f in s.memory.list()] == ["Lea has a cat named Miso"]
    assert [e.conversation_id for e in s.episodes.search("cat Miso")] == [cid]


def test_backfill_includes_deleted_conversations(tmp_path):
    s = make(tmp_path, [])
    cid = s.convs.create(title="old")["id"]
    s.convs.add_message(cid, "user", "My gym is open until 23:00 on weekdays", channel="chat")
    s.convs.delete(cid)
    assert Episodes(s.db, s.embedder).backfill() == 1


def test_backfill_indexes_older_conversations_once(tmp_path):
    s = make(tmp_path, [])
    cid = s.convs.create(title="old")["id"]
    q = s.convs.add_message(cid, "user", "Remember my locker code is 4412", channel="chat")
    s.convs.add_message(cid, "assistant", "Saved.", channel="chat")
    eps = Episodes(s.db, s.embedder)
    assert eps.backfill() == 1 and eps.backfill() == 0
    assert s.db.one("SELECT user_message_id FROM episodes")["user_message_id"] == q
