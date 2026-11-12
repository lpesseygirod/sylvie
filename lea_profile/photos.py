"""Lea's photo library: find photos by who's in them, where, what, and when (
"show me the photos with…").

Apple Photos already did the hard part on the Mac: it names the people Lea tagged, recognises things
(beach, dog, birthday cake) and knows places. That lives in two databases inside the library:
Photos.sqlite (photos, dates, faces → people) and search/psi.sqlite (the words Photos search uses).
macOS only lets an app with Full Disk Access read them, so the Mac app (PhotosSync.swift) copies them
into Sylvie's data folder; this module only ever reads those copies. Showing a photo is the Mac app's
job too (sylvie-photo://<uuid>, through Apple's PhotoKit): no photo is copied or sent anywhere.

Column names differ between macOS versions, so they're looked up rather than assumed.
"""

from __future__ import annotations

import sqlite3
import struct
import unicodedata
import uuid as uuidlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APPLE_EPOCH = 978307200
SKIP_CATEGORIES = {2100}  # file names ("0004afca-….jpg"): every search for "jpg" or a number would match
STOPWORDS = set(
    "the a an of with and in at on my me photo photos picture pictures show find "
    "el la los las de del con y en mi mis foto fotos imagen imagenes muestra muestrame ensename busca "
    "le les des du avec et au aux mon ma mes photo photos montre moi".split()
)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def uuid_from_ints(u0: int, u1: int) -> str:
    """psi.sqlite stores each photo's UUID as two signed 64-bit integers."""
    return str(uuidlib.UUID(bytes=struct.pack("<qq", u0, u1))).upper()


@dataclass
class Photo:
    uuid: str
    when: datetime | None
    favorite: bool = False
    video: bool = False
    people: list[str] = field(default_factory=list)
    place: str = ""

    def line(self) -> str:
        bits = [f"{self.when:%Y-%m-%d}" if self.when else "undated"]
        if self.place:
            bits.append(self.place)
        if self.people:
            bits.append("with " + ", ".join(self.people))
        if self.video:
            bits.append("video")
        return " · ".join(bits)

    def card(self) -> dict:
        return {
            "uuid": self.uuid,
            "date": f"{self.when:%Y-%m-%d}" if self.when else "",
            "caption": self.line(),
        }


def _cols(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _pick(cols: set[str], *names: str) -> str | None:
    return next((n for n in names if n in cols), None)


class PhotoLibrary:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._loaded_at: float | None = None
        self.photos: dict[str, Photo] = {}
        self.words: dict[str, set[str]] = {}  # folded search word/phrase → photo uuids

    @property
    def main_db(self) -> Path:
        return self.folder / "Photos.sqlite"

    @property
    def search_db(self) -> Path:
        return self.folder / "psi.sqlite"

    def available(self) -> bool:
        return self.main_db.exists()

    def _open(self, path: Path) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # with its -wal copy, if any

    @property
    def version(self) -> str:
        """Changes whenever the Mac app copied the databases again (it writes `copied-at` last)."""
        marker = self.folder / "copied-at"
        return marker.read_text().strip() if marker.exists() else str(self.main_db.stat().st_mtime)

    def _merge_wal(self, path: Path) -> None:
        """Fold the copied write-ahead log into Sylvie's own copy, so reading needs nothing else."""
        if path.exists() and Path(f"{path}-wal").exists():
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def refresh(self) -> None:
        """(Re)load the copies when the Mac app copied newer ones."""
        if not self.available():
            return
        mtime = self.version
        if self._loaded_at == mtime:
            return
        for path in (self.main_db, self.search_db):
            self._merge_wal(path)
        photos = self._load_photos()
        words = self._load_words(photos) if self.search_db.exists() else {}
        for p in photos.values():  # people and dates are searchable too
            for name in p.people:
                words.setdefault(fold(name), set()).add(p.uuid)
        self.photos, self.words, self._loaded_at = photos, words, mtime

    def _load_photos(self) -> dict[str, Photo]:
        with self._open(self.main_db) as db:
            ac = _cols(db, "ZASSET")
            trashed = "AND COALESCE(ZTRASHEDSTATE, 0) = 0" if "ZTRASHEDSTATE" in ac else ""
            hidden = "AND COALESCE(ZHIDDEN, 0) = 0" if "ZHIDDEN" in ac else ""
            fav = "ZFAVORITE" if "ZFAVORITE" in ac else "0"
            kind = "ZKIND" if "ZKIND" in ac else "0"
            date = _pick(ac, "ZDATECREATED", "ZADDEDDATE") or "NULL"
            photos, by_pk = {}, {}
            for pk, uid, t, f, k in db.execute(
                f"SELECT Z_PK, ZUUID, {date}, {fav}, {kind} FROM ZASSET WHERE ZUUID IS NOT NULL {trashed} {hidden}"
            ):
                when = datetime.fromtimestamp(t + APPLE_EPOCH) if t else None
                photos[uid] = by_pk[pk] = Photo(uid, when, bool(f), k == 1)
            fc, pc = _cols(db, "ZDETECTEDFACE"), _cols(db, "ZPERSON")
            face_asset = _pick(fc, "ZASSETFORFACE", "ZASSET")
            face_person = _pick(fc, "ZPERSONFORFACE", "ZPERSON")
            name = _pick(pc, "ZFULLNAME") and "COALESCE(NULLIF(p.ZFULLNAME, ''), p.ZDISPLAYNAME)"
            if face_asset and face_person and name:
                for pk, who in db.execute(
                    f"SELECT f.{face_asset}, {name} FROM ZDETECTEDFACE f JOIN ZPERSON p ON p.Z_PK = f.{face_person}"
                ):
                    if who and pk in by_pk and who not in by_pk[pk].people:
                        by_pk[pk].people.append(who)
        return photos

    def _load_words(self, photos: dict[str, Photo]) -> dict[str, set[str]]:
        """Every word Photos search knows for each photo (places, things, events, people), plus the
        most specific place name as the photo's caption."""
        words: dict[str, set[str]] = {}
        with self._open(self.search_db) as db:
            gc = _cols(db, "groups")
            if not gc or not _cols(db, "ga") or not _cols(db, "assets"):
                return words
            text = (
                "COALESCE(g.normalized_string, g.content_string)"
                if "normalized_string" in gc
                else "g.content_string"
            )
            rows = db.execute(
                f"SELECT a.uuid_0, a.uuid_1, g.category, g.content_string, {text} "
                "FROM ga JOIN groups g ON g.rowid = ga.groupid JOIN assets a ON a.rowid = ga.assetid"
            )
            for u0, u1, category, shown, norm in rows:
                if category in SKIP_CATEGORIES:
                    continue
                # Measured on Lea's library (macOS 26): strings end with a NUL byte.
                shown, norm = (shown or "").strip("\x00 "), (norm or "").strip("\x00 ")
                if not any(c.isalnum() for c in norm):
                    continue
                try:
                    uid = uuid_from_ints(u0, u1)
                except (struct.error, TypeError):
                    continue
                if uid not in photos or not norm:
                    continue
                words.setdefault(fold(norm), set()).add(uid)
                # Place categories are the low numbers (1 = place name … 9 = country); the city (5)
                # or the smallest named place makes the best caption.
                if isinstance(category, int) and 1 <= category <= 9 and shown:
                    p = photos[uid]
                    if not p.place or category == 5:
                        p.place = shown
        return words

    def people(self) -> list[tuple[str, int]]:
        return Counter(n for p in self.photos.values() for n in p.people).most_common()

    def find(
        self,
        query: str = "",
        person: str = "",
        date_from: str = "",
        date_to: str = "",
        favorites: bool = False,
        limit: int = 24,
    ) -> tuple[int, list[Photo]]:
        """(how many match, the newest `limit` of them)."""
        self.refresh()
        hits = set(self.photos)
        for who in [
            w.strip() for w in person.replace(" y ", ",").replace(" and ", ",").split(",") if w.strip()
        ]:
            f = fold(who)
            hits &= {p.uuid for p in self.photos.values() if any(f in fold(n) for n in p.people)}
        for word in [w for w in fold(query).replace(",", " ").split() if w not in STOPWORDS and len(w) > 1]:
            matched = set()
            for key, uids in self.words.items():
                if word in key.split() or (len(word) > 3 and word in key):
                    matched |= uids
            hits &= matched
        start = _date(date_from)
        end = _date(date_to, end=True)
        out = [
            self.photos[u]
            for u in hits
            if (not favorites or self.photos[u].favorite)
            and (start is None or (self.photos[u].when and self.photos[u].when >= start))
            and (end is None or (self.photos[u].when and self.photos[u].when <= end))
        ]
        out.sort(key=lambda p: p.when or datetime.min, reverse=True)
        return len(out), out[:limit]


def _date(s: str, end: bool = False) -> datetime | None:
    s = (s or "").strip()
    for fmt, size in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            d = datetime.strptime(s[:size], fmt)
        except ValueError:
            continue
        if not end:
            return d
        if size == 4:
            return d.replace(month=12, day=31, hour=23, minute=59)
        if size == 7:
            nxt = d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1)
            return datetime.fromtimestamp(nxt.timestamp() - 1)
        return d.replace(hour=23, minute=59)
    return None


_libraries: dict[str, PhotoLibrary] = {}


def folder(cfg) -> Path:
    custom = cfg.get_path("photos.folder", "")  # the evals point it at a made-up library
    return Path(custom).expanduser() if custom else cfg.data_dir / "app-data" / "photos"


def get(cfg) -> PhotoLibrary | None:
    """The copied photo library index, or None (no Full Disk Access yet, or turned off)."""
    if not cfg.get_path("photos.enabled", True):
        return None
    f = folder(cfg)
    lib = _libraries.setdefault(str(f), PhotoLibrary(f))
    if not lib.available():
        return None
    try:
        lib.refresh()
    except sqlite3.DatabaseError:
        return lib if lib.photos else None
    return lib if lib.photos else None


def answer(lib: PhotoLibrary, query="", person="", date_from="", date_to="", favorites=False):
    """(text for the model, photo cards for the screen)."""
    total, photos = lib.find(query, person, date_from, date_to, favorites)
    what = ", ".join(x for x in (person and f"with {person}", query and f"'{query}'",
                                 date_from and f"from {date_from}", date_to and f"to {date_to}") if x)  # fmt: skip
    if not total:
        names = ", ".join(n for n, _ in lib.people()[:15])
        hint = f" People Photos knows: {names}." if names else ""
        return (
            f"No photos found{' ' + what if what else ''}. Photos search words are in the Mac's language: "
            f"try the word in another language, or fewer words.{hint}",
            [],
        )
    lines = "\n".join(f"- {p.line()}" for p in photos[:12])
    return (
        f"Found {total} photo{'s' if total != 1 else ''}{' ' + what if what else ''}; the newest "
        f"{len(photos)} are now shown to Lea on screen. Some of them:\n{lines}\n"
        "Tell her briefly what you found (how many, when, where); don't list them all.",
        [p.card() for p in photos],
    )
