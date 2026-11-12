"""The app actions I asked for: watch, send_message, call, directions, create_note.
Everything runs against a FAKE AppleScript runner and a fake `open`: nothing real happens."""

import subprocess

from actions.log import ActionLog
from actions.osa import FakeOSA
from actions.runner import ActionRunner, match_contacts
from actions.scripts import MESSAGES_SEND, NOTE_CREATE, NOTE_DELETE
from core.db import Database
from tests.test_orchestrator import make

CONTACTS = "Mama\t+34 600 111 222,\t\nInés Pérez\t+34 600 333 444,\tines@example.com,\nInés Gil\t+34 600 555 666,\t\n"


def runner(tmp_path, confirm="never", found=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    s = make(tmp_path, [])
    s.cfg["actions"] = {**s.cfg["actions"], "confirm": confirm}
    opened = []

    def run_cmd(args):
        opened.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")

    osa = FakeOSA(
        {'application "Contacts"': CONTACTS, 'application "Messages"': "sent", "make new note": "note-1"}
    )
    r = ActionRunner(s.cfg, ActionLog(Database(tmp_path / "a.db", dim=16)), osa=osa, run_cmd=run_cmd,
                     is_locked=lambda: False, find_title=lambda service, title: found)  # fmt: skip
    return r, osa, opened


def test_watch_starts_the_title_or_opens_the_search(tmp_path):
    r, _, opened = runner(tmp_path, found="https://www.netflix.com/watch/80232398")
    out = r.call("watch", {"title": "Bridgerton"}, None)
    assert (
        out.ok
        and "starting on Netflix" in out.content
        and opened == ["https://www.netflix.com/watch/80232398"]
    )
    r, _, opened = runner(tmp_path / "2")
    out = r.call("watch", {"title": "The Bear", "service": "disney"}, None)
    assert opened == ["https://www.disneyplus.com/search?q=The%20Bear"] and "search" in out.content


def test_contacts_by_how_lea_calls_them():
    people = [("Mama", ["+34600111222"], []), ("Inés Pérez", ["+34600333444"], []), ("Inés Gil", [], [])]
    assert match_contacts(people, "mamá")[0][0] == "Mama"
    assert match_contacts(people, "Inés Pérez")[0][0] == "Inés Pérez"
    assert len(match_contacts(people, "ines")) == 2  # two Inés: Sylvie must ask which one


def test_send_message_reads_contacts_and_sends(tmp_path):
    r, osa, _ = runner(tmp_path)
    out = r.call("send_message", {"to": "Mamá", "text": "Llego tarde, a las 9"}, None)
    assert (
        out.pending_id and "Send a message to Mama" in out.content
    )  # even with confirm: never
    out = r.confirm(out.pending_id)
    assert out.ok and out.content == "Message sent to Mama."
    sent = [args for script, args in osa.calls if script == MESSAGES_SEND]
    assert sent == [("Llego tarde, a las 9", "+34 600 111 222")]
    out = r.call("send_message", {"to": "Inés", "text": "hola"}, None)  # asked before any read-back
    assert not out.ok and not out.pending_id and "Inés Gil, Inés Pérez" in out.content
    out = r.call("send_message", {"to": "Inés Pérez", "text": "hola"}, None)
    assert 'Send a message to Inés Pérez (Messages): "hola"' in out.content


def test_send_message_waits_for_a_yes_when_lea_wants_that(tmp_path):
    r, osa, _ = runner(tmp_path, confirm="delete_or_send")
    out = r.call("send_message", {"to": "Mamá", "text": "Llego tarde"}, None)
    assert (
        out.pending_id
        and "NOT DONE YET" in out.content
        and not [c for c in osa.calls if c[0] == MESSAGES_SEND]
    )


def test_call_directions_and_notes(tmp_path):
    r, osa, opened = runner(tmp_path)
    assert r.call("call", {"to": "Inés Pérez"}, None).content == "FaceTime is calling Inés Pérez."
    r.call("directions", {"destination": "Puerta del Sol, Madrid", "mode": "walking"}, None)
    assert opened == [
        "facetime-audio://%2B34%20600%20333%20444",
        "maps://?daddr=Puerta%20del%20Sol%2C%20Madrid&dirflg=w",
    ]
    out = r.call("create_note", {"title": "Compra", "body": "leche\nhuevos"}, None)
    assert out.ok and [a for s, a in osa.calls if s == NOTE_CREATE][0][1] == "<h1>Compra</h1>leche<br>huevos"
    aid = r.log.list()[0].id if hasattr(r.log, "list") else None
    if aid:
        r.undo(aid)
        assert any(s == NOTE_DELETE for s, _ in osa.calls)


def test_saying_sent_when_it_was_not_is_corrected(tmp_path):
    """2026-11-08 (make apps): WhatsApp isn't installed, yet "I sent the message to Ana on WhatsApp"."""
    from tests.test_actions import call
    from tests.test_orchestrator import run

    send = call("send_message", to="Ana", text="I'll be there in 10 minutes", app="whatsapp")
    s = make(
        tmp_path,
        [
            send,
            "I sent the message to Ana on WhatsApp.",
            "I couldn't send it: WhatsApp isn't on this Mac.",
            "{}",
        ],
    )
    ev = run(s, "Text Ana on WhatsApp that I'll be there in 10 minutes")
    said = [m["content"] for m in s.llm.calls[2]["messages"] if m["role"] == "user"]
    assert said[-1].startswith("(Check: nothing was sent.")
    assert "couldn't send it" in "".join(e["text"] for e in ev if e["type"] == "text")


def test_calendar_week_search_reminders_and_contact_info(tmp_path):
    from actions.scripts import CALENDAR_EVENTS

    r, osa, _ = runner(tmp_path)
    osa.outputs["every event of c"] = (
        "2026-11-10T09:00:00\tClase de Derecho\tMy IE Agenda\n2026-11-12T23:59:00\tEntrega ensayo criminología\tBlackboard\n"
    )
    osa.outputs["completed is false"] = (
        "2026-11-09T18:00:00\tComprar regalo\tPersonal\n\tLlamar al banco\tPersonal\n"
    )
    week = r.call("calendar_events", {"day_offset": 0, "days": 7}, None).content
    assert "Tue 10 Nov 09:00 Clase de Derecho" in week and "Entrega" in week
    assert [c[1] for c in osa.calls if c[0] == CALENDAR_EVENTS][-1] == ("0", "7")
    due = r.call("calendar_events", {"day_offset": 0, "days": 14, "search": "entrega"}, None).content
    assert "Entrega ensayo" in due and "Derecho" not in due
    rem = r.call("list_reminders", {}, None).content
    assert "- Llamar al banco [Personal]" in rem and "Comprar regalo (due 2026-11-09 18:00)" in rem
    assert "+34 600 111 222" in r.call("contact_info", {"name": "mamá"}, None).content
