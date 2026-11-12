"""Finding Lea's photos (lea_profile/photos.py), with small fake copies of the Photos databases."""

import sqlite3
import struct
import uuid
from datetime import datetime

from lea_profile.photos import APPLE_EPOCH, PhotoLibrary, answer, uuid_from_ints

U = [str(uuid.UUID(int=i * 7919 + 1)).upper() for i in range(4)]


def fake_library(folder):
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "Photos.sqlite")
    db.executescript("""
        CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZUUID TEXT, ZDATECREATED REAL, ZFAVORITE INT,
            ZKIND INT, ZTRASHEDSTATE INT, ZHIDDEN INT);
        CREATE TABLE ZPERSON (Z_PK INTEGER PRIMARY KEY, ZFULLNAME TEXT, ZDISPLAYNAME TEXT);
        CREATE TABLE ZDETECTEDFACE (Z_PK INTEGER PRIMARY KEY, ZASSETFORFACE INT, ZPERSONFORFACE INT);
    """)

    def t(y, m, d):
        return datetime(y, m, d, 12).timestamp() - APPLE_EPOCH

    db.executemany("INSERT INTO ZASSET VALUES (?,?,?,?,?,?,0)", [
        (1, U[0], t(2025, 8, 14), 1, 0, 0),  # Ana + Mamá at the beach in Valencia
        (2, U[1], t(2026, 3, 2), 0, 0, 0),  # Ana's birthday, Madrid
        (3, U[2], t(2026, 10, 1), 0, 1, 0),  # a video of the dog
        (4, U[3], t(2026, 10, 2), 0, 0, 1),  # in the trash: never shown
    ])  # fmt: skip
    db.executemany("INSERT INTO ZPERSON VALUES (?,?,?)", [(1, "Ana García", "Ana"), (2, "", "Mamá")])
    db.executemany("INSERT INTO ZDETECTEDFACE VALUES (?,?,?)", [(1, 1, 1), (2, 1, 2), (3, 2, 1)])
    db.commit()
    db.close()

    psi = sqlite3.connect(folder / "psi.sqlite")
    psi.executescript("""
        CREATE TABLE groups (rowid INTEGER PRIMARY KEY, category INT, content_string TEXT, normalized_string TEXT);
        CREATE TABLE assets (rowid INTEGER PRIMARY KEY, uuid_0 INT, uuid_1 INT);
        CREATE TABLE ga (groupid INT, assetid INT);
    """)
    for i, u in enumerate(U[:3], 1):
        u0, u1 = struct.unpack("<qq", uuid.UUID(u).bytes)
        psi.execute("INSERT INTO assets VALUES (?,?,?)", (i, u0, u1))
    psi.executemany("INSERT INTO groups VALUES (?,?,?,?)", [
        (1, 5, "Valencia", "valencia"), (2, 2024, "Playa", "playa"), (3, 5, "Madrid", "madrid"),
        (4, 2024, "Cumpleaños", "cumpleaños"), (5, 2024, "Perro", "perro"),
    ])  # fmt: skip
    psi.executemany("INSERT INTO ga VALUES (?,?)", [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3)])
    psi.commit()
    psi.close()
    return folder


def test_uuid_round_trip():
    u0, u1 = struct.unpack("<qq", uuid.UUID(U[1]).bytes)
    assert uuid_from_ints(u0, u1) == U[1]


def test_find_by_person_place_thing_and_date(tmp_path):
    lib = PhotoLibrary(fake_library(tmp_path / "photos"))
    lib.refresh()
    assert len(lib.photos) == 3  # the trashed one is left out
    total, ana = lib.find(person="Ana")
    assert total == 2 and ana[0].uuid == U[1]  # newest first
    assert lib.find(person="Ana, mamá")[1][0].uuid == U[0]
    assert [p.uuid for p in lib.find(query="playa")[1]] == [U[0]]
    assert [p.uuid for p in lib.find(query="fotos de cumpleaños")[1]] == [U[1]]  # "fotos de" ignored
    assert lib.find(query="perro")[1][0].video
    assert lib.find(person="Ana", date_from="2026")[0] == 1
    assert lib.find(favorites=True)[1][0].uuid == U[0]
    assert ana[1].place == "Valencia" and "Ana García" in ana[1].people
    assert lib.people()[0] == ("Ana García", 2)


def test_tool_shows_photos_on_screen(tmp_path):
    from tests.test_orchestrator import make, run

    s = make(tmp_path, [])
    s.cfg["photos"] = {**s.cfg.get("photos", {}), "enabled": True}  # whatever the shared config says
    fake_library(s.cfg.data_dir / "app-data" / "photos")
    lib = PhotoLibrary(s.cfg.data_dir / "app-data" / "photos")
    text, cards = answer(lib, person="Ana")
    assert text.startswith("Found 2 photos with Ana") and cards[0]["uuid"] == U[1]
    assert "No photos found" in answer(lib, query="montaña")[0]

    call = "<tool_call>\n<function=find_photos>\n<parameter=person>\nAna\n</parameter>\n</function>\n</tool_call>"
    s.llm.replies = [call, "Aquí tienes 2 fotos con Ana.", "{}"]
    ev = run(s, "Sylvie, enséñame las fotos con Ana")
    shown = [e for e in ev if e["type"] == "photos"]
    assert shown and [c["uuid"] for c in shown[0]["items"]] == [U[1], U[0]]
