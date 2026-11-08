"""Lea's WhatsApp, read on the Mac (lea_profile/whatsapp.py) — with a small fake database."""

import sqlite3
from datetime import datetime

from lea_profile.whatsapp import APPLE_EPOCH, WhatsApp, answer_lines


def fake_whatsapp(folder):
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "ChatStorage.sqlite")
    db.executescript("""
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZPARTNERNAME TEXT, ZSESSIONTYPE INT,
            ZLASTMESSAGEDATE REAL, ZUNREADCOUNT INT, ZREMOVED INT);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INT, ZISFROMME INT, ZMESSAGETYPE INT,
            ZTEXT TEXT, ZMESSAGEDATE REAL, ZGROUPMEMBER INT, ZFROMJID TEXT, ZPUSHNAME TEXT);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCONTACTNAME TEXT, ZFIRSTNAME TEXT, ZMEMBERJID TEXT);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
    """)

    def t(day, hour=12):
        return datetime(2026, 11, day, hour).timestamp() - APPLE_EPOCH

    db.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,0)", [
        (1, "Mamá", 0, t(7), 2), (2, "Amigas uni", 1, t(6), 0),
    ])  # fmt: skip
    db.execute("INSERT INTO ZWAGROUPMEMBER VALUES (1, 'Ana García', 'Ana', 'ana@s.whatsapp.net')")
    db.executemany("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,?,?,?)", [
        (1, 1, 0, 0, "¿Vienes a cenar el domingo? Hago paella", t(5), None, None, None),
        (2, 1, 1, 0, "¡Sí! Llevo el postre", t(5, 13), None, None, None),
        (3, 2, 0, 0, "El examen de criminología es el jueves", t(6), 1, None, None),
        (4, 2, 0, 1, None, t(6, 13), 1, None, None),
        (5, 2, 0, 6, None, t(6, 14), None, None, None),  # group event: skipped
        (6, 1, 0, 0, "Te quiero", t(7), None, None, None),
    ])  # fmt: skip
    db.commit()
    db.close()
    return folder


def test_search_by_person_and_words(tmp_path):
    wa = WhatsApp(fake_whatsapp(tmp_path / "wa"), tmp_path / "copy")
    assert [m.text for m in wa.search(contact="mama")] == [
        "¿Vienes a cenar el domingo? Hago paella", "¡Sí! Llevo el postre", "Te quiero",
    ]  # fmt: skip
    hit = wa.search(text="examen criminologia")
    assert len(hit) == 1 and hit[0].sender == "Ana García" and hit[0].group
    assert wa.search(contact="Amigas")[-1].text == "[photo]"
    assert wa.top_people()[0] == ("Mamá", 3)
    assert (tmp_path / "copy" / "ChatStorage.sqlite").exists()  # read from Sylvie's own copy


def test_tool_answer_and_nothing_leaves_the_mac_after(tmp_path):
    from tests.test_orchestrator import make, run

    folder = fake_whatsapp(tmp_path / "wa")
    lines = answer_lines(WhatsApp(folder), contact="Mamá")
    assert "paella" in lines and "last updated" not in lines.split("\n")[1]
    assert "Mamá" in answer_lines(WhatsApp(folder))  # no arguments: the recent chats

    call = (
        "<tool_call>\n<function=whatsapp>\n<parameter=contact>\nmamá\n</parameter>\n</function>\n</tool_call>"
    )
    leak = "<tool_call>\n<function=web_search>\n<parameter=query>\npaella domingo\n</parameter>\n</function>\n</tool_call>"
    s = make(tmp_path, [call, leak, "Tu madre te invita a cenar paella el domingo.", "{}"])
    s.cfg["whatsapp"] = {"enabled": True, "folder": str(folder)}
    s.cfg["search"] = {**s.cfg["search"], "enabled": True}
    ev = run(s, "¿Qué me ha dicho mi madre por WhatsApp?")
    tools = [m["content"] for m in s.llm.calls[-1]["messages"] if m["role"] == "tool"]
    assert "paella" in tools[0] and tools[1].startswith("Not allowed in this turn")
    assert "paella" in "".join(e["text"] for e in ev if e["type"] == "text")


def test_who_she_talks_to_goes_to_memory_once(tmp_path):
    from lea_profile.apps import AppLearning
    from tests.test_orchestrator import make

    s = make(tmp_path, [])
    s.cfg["whatsapp"] = {"enabled": True, "folder": str(fake_whatsapp(tmp_path / "wa"))}
    a = AppLearning(s)
    a.folders = []
    assert a.scan() == ["whatsapp"] and a.scan() == []
    fact = s.memory.by_source("app:whatsapp")[0].text
    assert "Mamá" in fact and "paella" not in fact  # people, never message contents
    wa = next(x for x in a.overview()["apps"] if x["key"] == "whatsapp")
    assert wa["status"]["counts"]["top"] == ["Mamá"]
