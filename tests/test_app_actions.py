"""The app actions Lea asked for on 2026-11-08: watch, send_message, call, directions, create_note.
Everything runs against a FAKE AppleScript runner and a fake `open`: nothing real happens."""

import subprocess

from actions.log import ActionLog
from actions.osa import FakeOSA
from actions.runner import ActionRunner, match_contacts
from actions.scripts import CONTACTS_ALL, MESSAGES_SEND, NOTE_CREATE, NOTE_DELETE
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
        {
            CONTACTS_ALL.strip()[:40]: CONTACTS,
            MESSAGES_SEND.strip()[:40]: "sent",
            NOTE_CREATE.strip()[:40]: "note-1",
        }
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
    assert out.ok and out.content == "Message sent to Mama."
    sent = [args for script, args in osa.calls if script == MESSAGES_SEND]
    assert sent == [("Llego tarde, a las 9", "+34 600 111 222")]
    out = r.call("send_message", {"to": "Inés", "text": "hola"}, None)
    assert not out.ok and "Inés Gil, Inés Pérez" in out.content


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
