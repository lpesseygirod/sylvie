"""Learning what Lea likes from the apps she uses most (Lea's request, 2026-11-08).

No logins and no scraping: Sylvie never signs in to Lea's accounts (and TikTok/Instagram forbid
automated access). Every one of these apps lets Lea download her own data ("Download your data",
Google Takeout). When such a download lands in ~/Downloads (or the App exports folder), Sylvie
reads it on this Mac and learns from it:

  Spotify    her most-played and liked artists         → interests "music: <artist>", one fact
  Instagram  the topics Instagram itself lists for her → interests (its own topic names)
  TikTok     what she searched, hashtags she saved     → topics, picked by the local model
  Google     YouTube watched/searched, Drive file names → topics; Docs go into her document search

Nothing is uploaded anywhere, the downloads are never changed or deleted, and every interest is a
"guess" Lea can confirm, reject or delete in About Lea → Interests. Learned facts are memory, never
training data.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import threading
import time
import zipfile
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from core.jsonutil import extract_json

log = logging.getLogger("sylvie.apps")

CHOSEN_KEY = "apps.chosen"  # the apps Lea said she uses most (None = never asked)
STATUS_KEY = "apps.status"  # per app: what was learned, from which file, when
FILES_KEY = "apps.files"  # export files already read: name → size:mtime
MAX_EXPORT_BYTES = 4_000_000_000
MAX_MEMBER_BYTES = 200_000_000
DOC_SUFFIXES = (".docx", ".pdf", ".txt", ".md", ".odt", ".rtf")

APPS = {
    "spotify": {
        "name": "Spotify",
        "what": "the artists you play and like",
        "how": [
            "Open spotify.com/account/privacy (signed in).",
            "Under “Download your data”, tick “Account data” and press “Request data”.",
            "Confirm the email; a day or two later Spotify emails a link to my_spotify_data.zip.",
            "Download it. Sylvie picks it up from Downloads by herself.",
        ],
    },
    "instagram": {
        "name": "Instagram",
        "what": "the topics Instagram thinks you like",
        "how": [
            "Instagram → Settings → Accounts Center → Your information and permissions → Download your information.",
            "Choose “Some of your information”: Your topics, Ads information, Likes. Format: JSON.",
            "When it's ready (minutes to a day), download the .zip. Sylvie picks it up from Downloads.",
        ],
    },
    "tiktok": {
        "name": "TikTok",
        "what": "what you search and the hashtags you save",
        "how": [
            "TikTok → Profile → ☰ → Settings and privacy → Account → Download your data.",
            "Choose JSON and “All data”, then Request data.",
            "When it's ready (a few days), download it from the same screen. Sylvie picks it up from Downloads.",
        ],
    },
    "google": {
        "name": "Google (Docs and YouTube)",
        "what": "your Docs (searchable by Sylvie) and what you watch on YouTube",
        "how": [
            "Open takeout.google.com. Press “Deselect all”, then tick Drive and YouTube and YouTube Music.",
            "For YouTube, keep history and subscriptions; JSON is fine.",
            "Next step → Export once → .zip. Google emails a link; download the .zip(s) to Downloads.",
        ],
    },
    "netflix": {
        "name": "Netflix",
        "what": "the series and films you watch",
        "how": [
            "Open netflix.com/viewingactivity (signed in, on your own profile).",
            "Scroll to the bottom and press “Download all”: you get NetflixViewingHistory.csv straight away.",
            "Leave it in Downloads. Sylvie picks it up by herself.",
        ],
    },
    "documents": {
        "name": "Your documents",
        "what": "Word, PowerPoint, Excel and PDF files in Documents, Desktop, OneDrive and iCloud Drive",
        "how": [
            "Nothing to do: Sylvie reads them on this Mac and keeps them up to date every few hours.",
            "Files that are only in the cloud are skipped (nothing is downloaded); open one once to include it.",
            "Ask “what does my criminology essay say about…?” or “summarise my notes on…”.",
        ],
    },
    "blackboard": {
        "name": "Blackboard",
        "what": "your assignments and deadlines (through your Mac's Calendar)",
        "how": [
            "In Blackboard, open Calendar → the ⚙ or “Share” button → copy the calendar link (it ends in .ics).",
            "On the Mac: Calendar app → File → New Calendar Subscription… → paste the link → Subscribe.",
            "Set Auto-refresh to “Every hour”. Sylvie then sees every deadline: “what's due this week?”.",
            "Course announcements arrive by email; Sylvie can read those with your Mail.",
        ],
    },
    "photos": {
        "name": "Photos",
        "what": "who and where you photograph; ask her “show me the photos with…”",
        "how": [
            "Open System Settings → Privacy & Security → Full Disk Access.",
            "Press +, press ⌘⇧G, type ~/Library/Caches/SylvieBuild and choose Sylvie. Turn it on.",
            "Quit Sylvie from the menu bar and open her again. When she asks to use Photos, press Allow.",
            "In the Photos app, name the people you want her to know (People & Pets → Add Name).",
        ],
    },
    "whatsapp": {
        "name": "WhatsApp",
        "what": "who you talk to, and your chats when you ask about them (read on this Mac, never sent)",
        "how": [
            "Install WhatsApp from the Mac App Store and open it.",
            "On your phone: WhatsApp → Settings → Linked devices → Link a device, and scan the code on the Mac.",
            "Keep WhatsApp open on the Mac now and then so it stays up to date. Sylvie reads it by herself.",
        ],
    },
}


@dataclass
class Signals:
    """What one export says about Lea, before anything is saved."""

    app: str
    topics: list[tuple[str, float]] = field(default_factory=list)  # ready-made topic names, weight
    items: list[str] = field(default_factory=list)  # raw signals (searches, titles) for the model
    facts: list[str] = field(default_factory=list)
    docs: list[tuple[str, bytes]] = field(default_factory=list)  # (name, content) to index
    counts: dict = field(default_factory=dict)


# ---------- reading exports ----------
class Export:
    """A zip, a folder or a single JSON file, read the same way (member name → bytes)."""

    def __init__(self, path: Path):
        self.path = path
        self._zip = zipfile.ZipFile(path) if path.suffix.lower() == ".zip" else None

    def names(self) -> list[str]:
        if self._zip is not None:
            return [
                i.filename for i in self._zip.infolist() if not i.is_dir() and i.file_size <= MAX_MEMBER_BYTES
            ]
        if self.path.is_dir():
            return [str(p.relative_to(self.path)) for p in self.path.rglob("*") if p.is_file()]
        return [self.path.name]

    def read(self, name: str) -> bytes:
        if self._zip is not None:
            return self._zip.read(name)
        if self.path.is_dir():
            return (self.path / name).read_bytes()
        return self.path.read_bytes()

    def json(self, name: str):
        try:
            return json.loads(self.read(name).decode("utf-8", "replace"))
        except ValueError:
            return None

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


def identify(path: Path, names: list[str]) -> str | None:
    """Which app an export comes from, by its file name and what's inside."""
    low = path.name.lower()
    inside = " ".join(n.lower() for n in names[:4000])
    if (
        "spotify" in low
        or "streaminghistory" in inside
        or "streaming_history_audio" in inside
        or "yourlibrary.json" in inside
    ):
        return "spotify"
    if "netflix" in low or "viewingactivity.csv" in inside:
        return "netflix"
    if low.startswith("takeout") or "takeout/" in inside:
        return "google"
    if "tiktok" in low or "user_data_tiktok" in inside:
        return "tiktok"
    if (
        low.startswith(("instagram-", "meta-"))
        or "your_instagram_activity" in inside
        or "instagram_ads" in inside
    ):
        return "instagram"
    return None


def _strings(obj, keys: set[str]) -> list[str]:
    """Every string value under one of `keys`, anywhere in a JSON tree."""
    out: list[str] = []
    stack = deque([obj])  # first in, first out: keeps the export's order
    while stack:
        o = stack.popleft()
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys and isinstance(v, str) and v.strip():
                    out.append(v.strip())
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
    return out


def read_spotify(ex: Export) -> Signals:
    sig = Signals("spotify")
    played: Counter = Counter()
    liked: Counter = Counter()
    for name in ex.names():
        base = name.rsplit("/", 1)[-1].lower()
        if not base.endswith(".json"):
            continue
        if base.startswith(("streaminghistory", "streaming_history_audio")):
            for row in ex.json(name) or []:
                if not isinstance(row, dict):
                    continue
                artist = row.get("artistName") or row.get("master_metadata_album_artist_name")
                ms = row.get("msPlayed") or row.get("ms_played") or 0
                if artist and ms >= 30_000:  # skips under 30 s don't count
                    played[artist] += ms / 60_000
        elif base == "yourlibrary.json":
            data = ex.json(name) or {}
            for t in data.get("tracks", []) if isinstance(data, dict) else []:
                if isinstance(t, dict) and t.get("artist"):
                    liked[t["artist"]] += 1
            for a in data.get("artists", []) if isinstance(data, dict) else []:
                if isinstance(a, dict) and a.get("name"):
                    liked[a["name"]] += 3
    total: Counter = Counter()
    for a, minutes in played.items():
        total[a] += minutes / 10
    for a, n in liked.items():
        total[a] += n * 2
    top = [a for a, _ in total.most_common(25)]
    if top:
        best = total[top[0]] or 1
        sig.topics = [(f"music: {a}", round(1 + 2 * total[a] / best, 1)) for a in top]
        sig.facts = [f"Lea's favourite artists on Spotify (most played and liked): {', '.join(top[:12])}."]
    sig.counts = {"artists_played": len(played), "liked_artists": len(liked), "top": top[:10]}
    return sig


def read_instagram(ex: Export) -> Signals:
    sig = Signals("instagram")
    topics: list[str] = []
    for name in ex.names():
        low = name.lower()
        if not low.endswith(".json"):
            continue
        if "topics" in low or "ads_interests" in low or "ads_and_topics" in low:
            topics += _strings(ex.json(name), {"value", "Name", "name", "title"})
    clean = [t for t in dict.fromkeys(topics) if 1 < len(t) <= 60 and not t.startswith("http")]
    sig.topics = [(t, 1.5) for t in clean[:60]]
    sig.counts = {"topics": len(clean), "top": clean[:10]}
    return sig


def read_tiktok(ex: Export) -> Signals:
    sig = Signals("tiktok")
    searches: list[str] = []
    tags: list[str] = []
    for name in ex.names():
        if not name.lower().endswith(".json"):
            continue
        data = ex.json(name)
        searches += _strings(data, {"SearchTerm", "searchTerm"})
        for link in _strings(data, {"Link", "link", "HashtagName", "Hashtag"}):
            m = re.search(r"/tag/([^/?#]+)", link)
            if m:
                tags.append(m.group(1))
            elif not link.startswith("http"):
                tags.append(link.lstrip("#"))
    counted = Counter(s.lower() for s in searches)
    sig.items = [s for s, _ in counted.most_common(150)] + [f"#{t}" for t in dict.fromkeys(tags)][:50]
    sig.counts = {
        "searches": len(searches),
        "hashtags": len(set(tags)),
        "top": [s for s, _ in counted.most_common(10)],
    }
    return sig


def read_google(ex: Export) -> Signals:
    sig = Signals("google")
    watched_channels: Counter = Counter()
    titles: list[str] = []
    searches: list[str] = []
    subs: list[str] = []
    doc_names: list[str] = []
    for name in ex.names():
        low = name.lower()
        base = name.rsplit("/", 1)[-1]
        if "youtube" in low and base == "watch-history.json":
            for row in ex.json(name) or []:
                if not isinstance(row, dict):
                    continue
                t = str(row.get("title", ""))
                if t.startswith("Watched "):
                    titles.append(t[len("Watched ") :])
                for sub in row.get("subtitles") or []:
                    if isinstance(sub, dict) and sub.get("name"):
                        watched_channels[sub["name"]] += 1
        elif "youtube" in low and base == "search-history.json":
            for row in ex.json(name) or []:
                t = str(row.get("title", "")) if isinstance(row, dict) else ""
                if t.startswith("Searched for "):
                    searches.append(t[len("Searched for ") :])
        elif "youtube" in low and base.lower() == "subscriptions.csv":
            reader = csv.DictReader(io.StringIO(ex.read(name).decode("utf-8", "replace")))
            subs += [r.get("Channel Title") or r.get("Channel title") or "" for r in reader]
        elif "/drive/" in low and low.endswith(DOC_SUFFIXES):
            doc_names.append(base.rsplit(".", 1)[0])
            if len(sig.docs) < 500:
                sig.docs.append((name, ex.read(name)))
    channels = [c for c, _ in watched_channels.most_common(40)] + [s for s in subs if s][:40]
    sig.items = (
        [f"YouTube channel: {c}" for c in dict.fromkeys(channels)]
        + [f"YouTube search: {s}" for s, _ in Counter(searches).most_common(80)]
        + [f"Watched: {t}" for t in titles[:150]]
        + [f"Document: {d}" for d in doc_names[:120]]
    )
    sig.counts = {
        "videos_watched": len(titles),
        "youtube_searches": len(searches),
        "subscriptions": len([s for s in subs if s]),
        "documents": len(doc_names),
        "top": channels[:10],
    }
    return sig


def read_netflix(ex: Export) -> Signals:
    """NetflixViewingHistory.csv (Title, Date) or the full "Get my info" ViewingActivity.csv."""
    sig = Signals("netflix")
    shows: Counter = Counter()
    for name in ex.names():
        base = name.rsplit("/", 1)[-1].lower()
        if not (base.startswith("netflixviewinghistory") or base == "viewingactivity.csv"):
            continue
        for row in csv.DictReader(io.StringIO(ex.read(name).decode("utf-8-sig", "replace"))):
            if (row.get("Supplemental Video Type") or "").strip():  # trailers, teasers
                continue
            title = (row.get("Title") or "").strip()
            if title:
                shows[title.split(":")[0].strip()] += 1  # "Show: Season 1: Episode" → "Show"
    top = [t for t, _ in shows.most_common(25)]
    sig.topics = [(f"watching: {t}", 3.0 if i < 5 else 2.0 if i < 12 else 1.0) for i, t in enumerate(top)]
    sig.items = [f"Watched on Netflix: {t}" for t, _ in shows.most_common(120)]
    if top:
        sig.facts.append(f"On Netflix, Lea watches most: {', '.join(top[:8])}.")
    sig.counts = {"titles": sum(shows.values()), "shows": len(shows), "top": top[:10]}
    return sig


READERS = {
    "spotify": read_spotify,
    "instagram": read_instagram,
    "tiktok": read_tiktok,
    "google": read_google,
    "netflix": read_netflix,
}

TOPICS_PROMPT = """Below is what Lea did on {app}: {kind}. Name the topics and interests it shows she
cares about (hobbies, music, subjects, sports, places, creators, styles). Ignore one-off noise.
Use short topic names (1-4 words) in English. Never include anything about health, religion,
politics, sexuality or other sensitive matters.

<data>
{data}
</data>

Return JSON only: {{"topics": [{{"topic": "<name>", "strength": <1-3>}}]}} with at most 25 topics."""


def topics_from_items(llm, app: str, items: list[str]) -> list[tuple[str, float]]:
    """The local model turns raw signals (searches, titles) into topic names."""
    if not items:
        return []
    kind = {"tiktok": "searches and saved hashtags", "google": "YouTube and Google Drive activity"}.get(
        app, "activity"
    )
    data = "\n".join(i[:120] for i in items[:300])
    raw = llm.complete(
        [{"role": "user", "content": TOPICS_PROMPT.format(app=APPS[app]["name"], kind=kind, data=data)}],
        max_tokens=700,
    )
    out = []
    for t in (extract_json(raw) or {}).get("topics", []) or []:
        if isinstance(t, dict) and isinstance(t.get("topic"), str) and 1 < len(t["topic"].strip()) <= 50:
            try:
                strength = min(3.0, max(1.0, float(t.get("strength", 1))))
            except (TypeError, ValueError):
                strength = 1.0
            out.append((t["topic"].strip(), strength))
    return out[:25]


# ---------- the background watcher ----------
class AppLearning:
    """Checks Downloads (and the App exports folder) for app downloads every few minutes."""

    def __init__(self, sylvie, every_seconds: float = 300.0):
        self.s = sylvie
        cfg = sylvie.cfg
        self.enabled = bool(cfg.get_path("apps.enabled", True))
        self.folders = [
            Path(str(f)).expanduser()
            for f in cfg.get_path(
                "apps.folders", ["~/Downloads", "~/Documents/Projects/Apps/Sylvie/App exports"]
            )
        ]
        self.docs_dir = Path(cfg.data_dir) / "app-data" / "google-drive"
        self.every = every_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._docs_queued = False

    # meta helpers
    def _get(self, key: str, default):
        rows = self.s.db.query("SELECT value FROM meta WHERE key = ?", (key,))
        try:
            return json.loads(rows[0]["value"]) if rows else default
        except ValueError:
            return default

    def _set(self, key: str, value) -> None:
        self.s.db.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def chosen(self) -> list[str] | None:
        return self._get(CHOSEN_KEY, None)

    def choose(self, apps: list[str]) -> list[str]:
        picked = [a for a in apps if a in APPS]
        self._set(CHOSEN_KEY, picked)
        return picked

    def overview(self) -> dict:
        status = self._get(STATUS_KEY, {})
        chosen = self.chosen()
        return {
            "asked": chosen is not None,
            "folders": [str(f) for f in self.folders],
            "apps": [
                {
                    "key": k,
                    "name": a["name"],
                    "what": a["what"],
                    "how": a["how"],
                    "chosen": bool(chosen and k in chosen),
                    "status": status.get(k),
                }
                for k, a in APPS.items()
            ],  # fmt: skip
        }

    def _candidates(self) -> list[Path]:
        out = []
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for p in folder.iterdir():
                if p.name.startswith("."):
                    continue
                low = p.name.lower()
                if (
                    p.suffix.lower() in (".zip", ".json")
                    or (p.suffix.lower() == ".csv" and low.startswith("netflix"))
                    or (
                        p.is_dir()
                        and low.startswith(("takeout", "tiktok", "instagram", "my_spotify", "netflix"))
                    )
                ):
                    out.append(p)
        return out

    def scan(self) -> list[str]:
        """Read every new app download. Returns the apps learned from."""
        if not self.enabled:
            return []
        files = self._get(FILES_KEY, {})
        learned = []
        for path in sorted(self._candidates()):
            try:
                st = path.stat()
                if st.st_size > MAX_EXPORT_BYTES:
                    continue
                sig_key = f"{st.st_size}:{int(st.st_mtime)}"
                if files.get(str(path)) == sig_key:
                    continue
                files[str(path)] = sig_key  # an unrelated zip is looked at once, then left alone
                ex = Export(path)
                try:
                    app = identify(path, ex.names())
                    if app is None:
                        continue
                    sig = READERS[app](ex)
                finally:
                    ex.close()
            except (OSError, zipfile.BadZipFile, ValueError) as e:
                log.warning("couldn't read %s: %s", path.name, e)
                continue
            self.apply(sig, path)
            learned.append(sig.app)
        if self._learn_whatsapp(files):
            learned.append("whatsapp")
        if self._learn_photos(files):
            learned.append("photos")
        self._index_documents()
        self._set(FILES_KEY, files)
        return learned

    def _index_documents(self) -> None:
        """Lea's document folders (knowledge.folders), every few hours, when she's away (it uses the
        embedding model): searchable with search_documents, readable with read_document."""
        folders = list(self.s.cfg.get_path("knowledge.folders", []) or [])
        if not folders or self.s.docs is None:
            return
        every = float(self.s.cfg.get_path("knowledge.reindex_hours", 6)) * 3600
        last = self._get("docs.indexed_at", 0)
        if time.time() - last < every or self._docs_queued:
            return
        self._docs_queued = True
        folders.append(str(self.docs_dir))  # the Google Docs from Takeout stay indexed too

        def job(ev):
            try:
                counts = self.s.docs.index_folders(folders, stop=ev)
            except Exception:
                log.exception("indexing Lea's documents failed")
                self._docs_queued = False
                return
            if counts.get("stopped"):
                return  # Lea needed the model: the job is re-queued and carries on later
            self._set("docs.indexed_at", time.time())
            self._record(
                "documents", Path(folders[0]), {**self.s.docs.stats(), "errors": counts["errors"]}, [], None
            )
            self._docs_queued = False

        self.s.bg.submit("index-documents", job, backlog=True)

    def _learn_photos(self, files: dict) -> bool:
        """Who's in Lea's photos most, once per library copy (lea_profile/photos.py). Only names and
        counts go to memory; the photos stay in Photos."""
        from lea_profile.photos import get

        try:
            lib = get(self.s.cfg)
            if lib is None:
                return False
            key = f"photos:{lib.version}"
            if files.get("photos") == key:
                return False
            files["photos"] = key
            people = [n for n, _ in lib.people()[:10]]
            dates = [p.when for p in lib.photos.values() if p.when]
        except (OSError, sqlite3.DatabaseError) as e:
            log.warning("couldn't read the photo library copy: %s", e)
            return False
        if people:
            self.s.memory.add(
                f"The people in Lea's photos most often are: {', '.join(people)}.",
                key="lea.photos.people", category="people", source="app:photos",
            )  # fmt: skip
        self._record("photos", lib.main_db, {"photos": len(lib.photos), "top": people,
                     "since": f"{min(dates):%Y}" if dates else None}, [], None)  # fmt: skip
        return True

    def _learn_whatsapp(self, files: dict) -> bool:
        """Who Lea writes to most, once per WhatsApp update. Only that goes to memory: the messages
        themselves stay in WhatsApp and are read when she asks (lea_profile/whatsapp.py)."""
        from lea_profile.whatsapp import get

        try:
            wa = get(self.s.cfg)
            if wa is None:
                return False
            last = wa.last_date()
            key = f"whatsapp:v2:{last}"  # v2: phone numbers left out
            if files.get("whatsapp") == key:
                return False
            files["whatsapp"] = key
            # Unsaved contacts are just phone numbers: not worth remembering as "people".
            people = [n for n, _ in wa.top_people(n=12) if not re.fullmatch(r"[+\d\s()-]+", n)][:8]
            chats = len(wa.chats(limit=1000))
        except (OSError, sqlite3.DatabaseError) as e:
            log.warning("couldn't read WhatsApp: %s", e)
            return False
        if people:
            self.s.memory.add(
                f"On WhatsApp, the people Lea writes to most are: {', '.join(people)}.",
                key="lea.whatsapp.people", category="people", source="app:whatsapp",
            )  # fmt: skip
        self._record("whatsapp", wa.source, {"chats": chats, "top": people,
                     "last_message": f"{last:%Y-%m-%d}" if last else None}, [], None)  # fmt: skip
        return True

    def apply(self, sig: Signals, path: Path) -> None:
        source = f"app:{sig.app}"
        bumped = []
        for topic, weight in sig.topics:
            if self.s.interests.bump(topic, weight) is not None:
                bumped.append(topic)
        for text in sig.facts:
            self.s.memory.add(text, key=f"lea.{sig.app}.taste", category="preferences", source=source)
        indexed = self._index_docs(sig.docs)
        self._record(sig.app, path, sig.counts, bumped, indexed)
        if sig.items:
            items, app = sig.items, sig.app

            def job(ev):
                from core.orchestrator import _Stoppable

                topics = topics_from_items(_Stoppable(self.s.llm, ev), app, items)
                if ev.is_set():
                    return
                names = [t for t, w in topics if self.s.interests.bump(t, w) is not None]
                self._record(app, path, None, names, None)

            self.s.bg.submit(f"learn-{sig.app}", job, backlog=True)  # uses the model: only when Lea is away
        log.info("learned from %s download %s", sig.app, path.name)

    def _index_docs(self, docs: list[tuple[str, bytes]]) -> int:
        if not docs:
            return 0
        from knowledge.docs_index import DocsIndex

        idx = DocsIndex(self.s.db, self.s.embedder)
        n = 0
        for name, content in docs:
            safe = re.sub(r"[^\w .\-()]+", "_", name.split("/Drive/", 1)[-1]).strip() or "document"
            out = self.docs_dir / safe
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(content)
            try:
                if idx.index_file(out):
                    n += 1
            except Exception:
                log.exception("couldn't index %s", safe)
        return n

    def _record(self, app, path, counts, topics, indexed) -> None:
        with self._lock:
            status = self._get(STATUS_KEY, {})
            st = status.get(app) or {"topics": []}
            st["file"] = path.name
            st["learned_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            if counts is not None:
                st["counts"] = counts
            if indexed is not None:
                st["documents_indexed"] = indexed
            st["topics"] = list(dict.fromkeys(st.get("topics", []) + list(topics)))[:80]
            status[app] = st
            self._set(STATUS_KEY, status)

    def start(self) -> None:
        if not self.enabled:
            return

        def loop():
            while not self._stop.is_set():
                try:
                    self.scan()
                except Exception:
                    log.exception("reading app downloads failed")
                self._stop.wait(self.every)

        threading.Thread(target=loop, name="app-learning", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
