"""Sylvie's actions: the allowlist, their tool definitions, and how each one runs.

Three kinds:
- harmless (music controls, reading the calendar, timers, opening apps) → run directly;
- undoable (creating an event or reminder) → run, log how to undo;
- needs a "yes" (sending email, running a Shortcut) → saved as pending, read back to Lea, run
  only when her next message is a clear yes and the Mac is unlocked (actions/confirm.py).
"""

from __future__ import annotations

import difflib
import logging
import re
import subprocess
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from actions import scripts, shell
from actions.confirm import screen_is_locked
from actions.log import ActionLog
from actions.osa import OSAError, OSARunner

EMAIL_RE = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[a-z]{2,}$", re.I)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def _fn(name, description, params, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": params, "required": required},
        },
    }


STR = {"type": "string"}
INT = {"type": "integer"}

TOOLS = {
    "music": _fn(
        "music",
        "Spotify playback controls with NO song name (to just open an app, Spotify too, use open_app): "
        "play (= resume what was playing), pause, next, previous, "
        "volume_up, volume_down, set_volume (0-100; 'max' = 100), now_playing. Volume is the Mac's volume. To play a specific song, artist, album or "
        "playlist, use play_music instead.",
        {
            "command": {
                **STR,
                "enum": [
                    "play",
                    "pause",
                    "next",
                    "previous",
                    "volume_up",
                    "volume_down",
                    "set_volume",
                    "now_playing",
                ],
            },
            "volume": INT,
        },
        ["command"],
    ),
    "play_music": _fn(
        "play_music",
        "Play a specific song, artist, album or playlist on Spotify, found by name (e.g. 'Fireball Pitbull', "
        "'lofi study playlist', 'Rosalía artist', or one of Lea's saved playlist names). It finds it and starts it "
        "by itself: no key or setup is needed, and Lea doesn't have to click anything.",
        {"query": STR},
        ["query"],
    ),
    "save_playlist": _fn(
        "save_playlist",
        "Remember one of Lea's Spotify playlists (or albums) by name, so 'play my <name> playlist' starts it "
        "directly. link: the Spotify share link (https://open.spotify.com/playlist/...) or a spotify: URI.",
        {"name": STR, "link": STR},
        ["name", "link"],
    ),
    "calendar_events": _fn(
        "calendar_events",
        "List Lea's calendar events for a day. day_offset: 0 = today, 1 = tomorrow, -1 = yesterday.",
        {"day_offset": INT},
        ["day_offset"],
    ),
    "create_event": _fn(
        "create_event",
        "Add an event to Lea's calendar. start: local time 'YYYY-MM-DDTHH:MM'.",
        {"title": STR, "start": STR, "duration_minutes": INT, "calendar": STR},
        ["title", "start"],
    ),
    "create_reminder": _fn(
        "create_reminder",
        "Add a reminder in the Reminders app. due (optional): local time 'YYYY-MM-DDTHH:MM'.",
        {"title": STR, "due": STR},
        ["title"],
    ),
    "set_timer": _fn(
        "set_timer",
        "Start a timer; a notification with a sound goes off when it ends.",
        {"minutes": {"type": "number"}, "label": STR},
        ["minutes"],
    ),
    "open_app": _fn("open_app", "Open a Mac app by name, e.g. 'Notes'.", {"name": STR}, ["name"]),
    "open_in_browser": _fn("open_in_browser", "Open a web address in Lea's browser.", {"url": STR}, ["url"]),
    "run_shortcut": _fn(
        "run_shortcut",
        "Run one of Lea's macOS Shortcuts by its exact name (asks Lea first).",
        {"name": STR},
        ["name"],
    ),
    "run_command": _fn(
        "run_command",
        "Run a command in Lea's Terminal (zsh, as her, from her home folder, no keyboard input) when that's "
        "the best way to do what she asks: look at files or the system, open things, install or change "
        "something. Read-only commands run at once; anything that changes, deletes, installs or sends is "
        "read back to Lea for a yes first. Never use sudo. Give the exact command and, in 'why', what it does "
        "in plain words.",
        {"command": STR, "why": STR},
        ["command", "why"],
    ),
    "read_emails": _fn(
        "read_emails",
        "Read Lea's most recent emails (sender, subject, start of the text) to summarize them.",
        {"count": INT, "unread_only": {"type": "boolean"}},
        [],
    ),
    "draft_email": _fn(
        "draft_email",
        "Open a new email draft in Mail for Lea to review (not sent).",
        {"to": STR, "subject": STR, "body": STR},
        ["to", "subject", "body"],
    ),
    "send_email": _fn(
        "send_email",
        "Send an email. Sylvie will read it back and only send after Lea says yes.",
        {"to": STR, "subject": STR, "body": STR},
        ["to", "subject", "body"],
    ),
    # Lea, 2026-11-08: "put Bridgerton on Netflix", "send a message to…", "and much more"
    "watch": _fn(
        "watch",
        "Put on a series, film or video for Lea: Netflix (default), YouTube, Prime Video, Disney+, Max, "
        "Movistar Plus+ or Apple TV. It opens in her browser and starts by itself when the exact title is "
        "found (Netflix, YouTube); otherwise the service's search for it opens.",
        {
            "title": STR,
            "service": {
                **STR,
                "enum": ["netflix", "youtube", "prime", "disney", "max", "movistar", "appletv"],
            },
        },
        ["title"],
    ),
    "send_message": _fn(
        "send_message",
        "Send a text message to someone. app 'messages' (default: iMessage/SMS with the Messages app) sends it; "
        "app 'whatsapp' opens the WhatsApp chat with the message written, and Lea presses send. to: the "
        "person as Lea calls them ('Mamá', 'Inés') or a phone number. Write the text as Lea would, in her words.",
        {"to": STR, "text": STR, "app": {**STR, "enum": ["messages", "whatsapp"]}},
        ["to", "text"],
    ),
    "call": _fn(
        "call",
        "Call someone with FaceTime (audio unless video is true). FaceTime shows the call before it starts. "
        "to: the person as Lea calls them, or a phone number.",
        {"to": STR, "video": {"type": "boolean"}},
        ["to"],
    ),
    "directions": _fn(
        "directions",
        "Open Apple Maps with directions to a place. mode: driving (default), walking or transit.",
        {"destination": STR, "mode": {**STR, "enum": ["driving", "walking", "transit"]}},
        ["destination"],
    ),
    "create_note": _fn(
        "create_note",
        "Write a note in the Notes app (a list, an idea, something to keep).",
        {"title": STR, "body": STR},
        ["title", "body"],
    ),
}

# Where each service's search lives (the fallback when the exact title isn't found).
WATCH_SEARCH = {
    "netflix": "https://www.netflix.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "prime": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "disney": "https://www.disneyplus.com/search?q={q}",
    "max": "https://play.max.com/search?q={q}",
    "movistar": "https://ver.movistarplus.es/busqueda/?q={q}",
    "appletv": "https://tv.apple.com/search?term={q}",
}
NETFLIX_TITLE = re.compile(r"netflix\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:title|watch)/(\d+)", re.I)
YOUTUBE_VIDEO = re.compile(r"youtube\.com/watch\?v=([\w-]{11})|youtu\.be/([\w-]{11})", re.I)


def find_to_watch(cfg, service: str, title: str, searx=None) -> str | None:
    """A link that starts `title` right away (Netflix /watch/<id>, a YouTube video), found through
    Lea's local SearXNG like songs are (find_on_spotify). None: open the service's search instead."""
    from tools.search import searxng

    site = {"netflix": "netflix.com/title", "youtube": "youtube.com/watch"}.get(service)
    if not site:
        return None
    url = cfg.get_path("search.searxng_url", "http://127.0.0.1:8888")
    try:
        results = (searx or searxng)(url, f"{title} site:{site}", max_results=8)
        if not results:
            results = (searx or searxng)(url, f"{title} site:{site}", max_results=8, engines="yahoo")
    except Exception:
        return None
    for r in results:
        u = r.get("url", "")
        if service == "netflix" and (m := NETFLIX_TITLE.search(u)):
            return f"https://www.netflix.com/watch/{m.group(1)}"
        if service == "youtube" and (m := YOUTUBE_VIDEO.search(u)):
            return f"https://www.youtube.com/watch?v={m.group(1) or m.group(2)}"
    return None


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


# What Lea calls someone → how Contacts may spell it
NICKNAMES = {
    "mama": ["mama", "mamá", "mum", "mom", "madre", "maman"],
    "papa": ["papa", "papá", "dad", "padre", "papi"],
}


def match_contacts(
    people: list[tuple[str, list[str], list[str]]], wanted: str
) -> list[tuple[str, list[str], list[str]]]:
    """Contacts matching how Lea named someone: the whole name, then a first name, then a nickname."""
    w = _fold(wanted)
    exact = [p for p in people if _fold(p[0]) == w]
    if exact:
        return exact
    words = [p for p in people if w in _fold(p[0]).split() or _fold(p[0]).startswith(w + " ")]
    if words:
        return words
    for key, names in NICKNAMES.items():
        if w in names or w == key:
            hits = [p for p in people if any(n in _fold(p[0]).split() for n in names)]
            if hits:
                return hits
    return [p for p in people if len(w) > 3 and w in _fold(p[0])]


NEEDS_YES = {"send_email", "run_shortcut", "send_message"}


@dataclass
class ActionOutcome:
    content: str
    ok: bool = True
    pending_id: int | None = None
    undo: dict | None = None


SPOTIFY_LINK = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(playlist|album|track|artist|show)/([A-Za-z0-9]+)"
)


def spotify_uri(text: str) -> str | None:
    """A spotify: URI from a share link or a URI; None for anything else."""
    t = text.strip()
    if re.fullmatch(r"spotify:(playlist|album|track|artist|show):[A-Za-z0-9]+", t):
        return t
    m = SPOTIFY_LINK.search(t)
    return f"spotify:{m.group(1)}:{m.group(2)}" if m else None


log = logging.getLogger("sylvie.actions")


class SpotifyNotSetUp(RuntimeError):
    pass


def _web_api_search(query: str) -> tuple[str, str] | None:
    from actions import spotify_api

    creds = spotify_api.credentials()
    if creds is None:
        raise SpotifyNotSetUp()
    return spotify_api.search_uri(query, creds)


def find_on_spotify(cfg, query: str, searx=None) -> tuple[str, str] | None:
    """A spotify: URI for a song/playlist/album/artist named in words. Uses the Spotify key if Lea set
    one up; otherwise searches the web (Lea's local SearXNG) for its open.spotify.com page, which needs
    no key (Lea, 2026-11-06: "why can't she just play the music?")."""
    try:
        return _web_api_search(query)
    except SpotifyNotSetUp:
        pass
    from actions.spotify_api import kind_for
    from tools.search import searxng

    kind = kind_for(query)
    words = re.sub(
        r"\b(playlist|lista|album|álbum|artist|artista|songs by|canciones de)\b", " ", query, flags=re.I
    )
    url = cfg.get_path("search.searxng_url", "http://127.0.0.1:8888")
    query_words = f"{words.strip()} site:open.spotify.com"
    results = (searx or searxng)(url, query_words, max_results=8)
    if not any(SPOTIFY_LINK.search(r.get("url", "")) for r in results):
        # 2026-11-08: the default engines refused (CAPTCHA, rate limits), so "Bloodline" found nothing.
        # Yahoo honours site: and still answered: ask it alone before giving up.
        results = (searx or searxng)(url, query_words, max_results=8, engines="yahoo")
    found = []
    for r in results:
        m = SPOTIFY_LINK.search(r.get("url", ""))
        if m:
            title = re.sub(
                r"\s*[-|]\s*(Spotify|song and lyrics.*|Song, Lyrics.*)$", "", r.get("title", ""), flags=re.I
            )
            found.append((m.group(1), f"spotify:{m.group(1)}:{m.group(2)}", title.strip()))
    best = next((f for f in found if f[0] == kind), None) or next((f for f in found if f[0] == "track"), None)
    best = best or (found[0] if found else None)
    return (best[1], best[2]) if best else None


def spotify_installed() -> bool:
    from pathlib import Path

    return any(
        p.exists() for p in (Path("/Applications/Spotify.app"), Path.home() / "Applications/Spotify.app")
    )


def _playlist_key(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"\b(my|mi|ma|mon|the|la|el|le|playlist|lista|liste)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip() or "music"


class ActionRunner:
    def __init__(
        self,
        cfg,
        log: ActionLog,
        osa=None,
        run_cmd: Callable | None = None,
        is_locked=screen_is_locked,
        has_spotify: Callable[[], bool] | None = None,
        spotify_search: Callable[[str], tuple[str, str] | None] | None = None,
        shell_runner: Callable | None = None,
        find_title: Callable[[str, str], str | None] | None = None,
    ):
        self.find_title = find_title or (lambda service, title: find_to_watch(self.cfg, service, title))
        self._contacts: tuple[float, list] | None = None
        self.has_spotify = has_spotify or spotify_installed
        self.shell_runner = shell_runner or subprocess.run
        self.spotify_search = spotify_search or (lambda q: find_on_spotify(self.cfg, q))
        self.cfg = cfg
        self.log = log
        self.osa = osa or OSARunner()
        self.run_cmd = run_cmd or (
            lambda args: subprocess.run(args, capture_output=True, text=True, timeout=30)
        )
        self.is_locked = is_locked
        self.timers: dict[int, threading.Timer] = {}

    def needs_yes(self, name: str, args: dict, after_untrusted: bool) -> bool:
        """actions.confirm (Lea's choice): 'delete_or_send' (default since 2026-11-06), 'changes' (also
        anything that changes something) or 'never'. After outside content was read in the turn, a
        command or Shortcut always needs her yes: text in a web page or email must not make Sylvie act."""
        mode = self.cfg.get_path("actions.confirm", "delete_or_send")
        cmd = str(args.get("command", ""))
        if name in ("run_command", "run_shortcut") and after_untrusted:
            return True
        if mode == "never":
            return False
        if name in ("send_email", "send_message"):
            return True
        if name == "run_command":
            return shell.deletes_or_sends(cmd) if mode == "delete_or_send" else not shell.is_read_only(cmd)
        if name == "run_shortcut":
            return mode == "changes"
        return name in NEEDS_YES

    def shortcut_names(self) -> list[str] | None:
        """Lea's Shortcuts (None if they can't be listed: then the name isn't checked)."""
        try:
            p = self.run_cmd(["shortcuts", "list"])
        except Exception:
            return None
        if getattr(p, "returncode", 1) != 0:
            return None
        return [line.strip() for line in (p.stdout or "").splitlines() if line.strip()]

    def _saved_playlist(self, query: str) -> str | None:
        saved = self.cfg.get_path("music.playlists") or {}
        key = _playlist_key(query)
        if key in saved:
            return saved[key]
        hits = [uri for name, uri in saved.items() if name in key or key in name]
        return hits[0] if len(hits) == 1 else None

    def tool_defs(self) -> list[dict]:
        enabled = self.cfg.get_path("actions.enabled") or list(TOOLS)
        return [TOOLS[n] for n in enabled if n in TOOLS]

    def handles(self, name: str) -> bool:
        return name in TOOLS

    # ---------- entry point from the tool loop ----------
    def call(
        self, name: str, args: dict, conversation_id: str | None, after_untrusted: bool = False
    ) -> ActionOutcome:
        err = self._check(name, args)
        if err:
            return ActionOutcome(f"Error: {err}", ok=False)
        needs_yes = self.needs_yes(name, args, after_untrusted)
        if needs_yes:
            summary = self._summary(name, args)
            if after_untrusted:
                summary += (
                    " (Note: this was suggested after reading outside content such as an email or web page.)"
                )
            aid = self.log.add(
                name, args, "pending", conversation_id, summary=summary, after_untrusted=after_untrusted
            )
            return ActionOutcome(
                "NOT DONE YET. Read this back to Lea in her language and ask her to confirm with a yes: "
                + summary,
                pending_id=aid,
            )
        return self._run_logged(name, args, conversation_id)

    def confirm(self, action_id: int, channel: str = "chat") -> ActionOutcome:
        rec = self.log.get(action_id)
        if rec is None or rec.status != "pending":
            return ActionOutcome("There's nothing waiting for confirmation.", ok=False)
        if channel == "voice" and self.is_locked():  # a typed yes means Lea is at the unlocked Mac
            self.log.update(action_id, "cancelled", result="Mac locked")
            return ActionOutcome(
                "Not done: the Mac is locked. For safety, a yes by voice only works while it's unlocked.",
                ok=False,
            )
        out = self._execute(rec.name, rec.args)
        self.log.update(action_id, "done" if out.ok else "failed", result=out.content[:500])
        return out

    def cancel(self, action_id: int) -> None:
        self.log.update(action_id, "cancelled")

    def undo(self, action_id: int) -> ActionOutcome:
        rec = self.log.get(action_id)
        if rec is None or rec.status != "done" or not rec.undo:
            return ActionOutcome("That action can't be undone.", ok=False)
        try:
            if rec.undo["kind"] == "event":
                self.osa.run(scripts.CALENDAR_DELETE, rec.undo["uid"])
            elif rec.undo["kind"] == "reminder":
                self.osa.run(scripts.REMINDER_DELETE, rec.undo["id"])
            elif rec.undo["kind"] == "note":
                self.osa.run(scripts.NOTE_DELETE, rec.undo["id"])
            elif rec.undo["kind"] == "timer":
                t = self.timers.pop(action_id, None)
                if t:
                    t.cancel()
        except OSAError as e:
            return ActionOutcome(f"Couldn't undo: {e}", ok=False)
        self.log.update(action_id, "undone")
        return ActionOutcome("Undone.")

    # ---------- internals ----------
    def _run_logged(self, name, args, conversation_id) -> ActionOutcome:
        aid = self.log.add(name, args, "running", conversation_id)
        out = self._execute(name, args, action_id=aid)
        self.log.update(aid, "done" if out.ok else "failed", result=out.content[:500], undo=out.undo)
        return out

    def _check(self, name: str, a: dict) -> str | None:
        if name in ("send_email", "draft_email") and not EMAIL_RE.match(str(a.get("to", "")).strip()):
            return "'to' must be one valid email address."
        if name == "run_command":
            if re.search(r"make\s+spotify|setup_spotify|make\s+phone|make\s+pair", str(a.get("command", ""))):
                return (
                    "This setup needs Lea herself: it asks her to type keys or codes, so it can't run here. "
                    "Tell her to open the Terminal app and type it there (in the Sylvie folder)."
                )
            why = shell.never_reason(str(a.get("command", "")))
            if why:
                return f"Refused: Sylvie never runs this ({why}). Tell Lea plainly; she can do it herself if she really wants."
        if name == "run_shortcut":
            names = self.shortcut_names()
            if names is not None:
                wanted = str(a.get("name", "")).strip()
                exact = next((n for n in names if n.lower() == wanted.lower()), None)
                if exact is None:
                    close = difflib.get_close_matches(wanted, names, n=3, cutoff=0.4)
                    hint = f" Closest: {', '.join(close)}." if close else ""
                    return f"Lea has no Shortcut called '{wanted}'.{hint} Don't ask her to confirm; tell her."
                a["name"] = exact
        if name == "save_playlist" and not spotify_uri(str(a.get("link", ""))):
            return "link must be a Spotify share link (open.spotify.com/...) or a spotify: URI."
        if name == "create_event" and not ISO_RE.match(str(a.get("start", ""))):
            return "start must look like 2026-11-05T15:30."
        if name == "create_reminder" and a.get("due") and not ISO_RE.match(str(a["due"])):
            return "due must look like 2026-11-05T15:30."
        if name == "open_in_browser" and not str(a.get("url", "")).startswith(("https://", "http://")):
            return "only http(s) addresses can be opened."
        if name == "set_timer":
            try:
                m = float(a["minutes"])
            except (TypeError, ValueError):
                return "minutes must be a number."
            if not 0 < m <= 24 * 60:
                return "a timer must be between a few seconds and 24 hours."
        return None

    # ---------- people ----------
    def contacts(self) -> list[tuple[str, list[str], list[str]]]:
        """Lea's Contacts (name, phones, emails), read with AppleScript and kept for 10 minutes."""
        import time

        if self._contacts and time.time() - self._contacts[0] < 600:
            return self._contacts[1]
        raw = self.osa.run(scripts.CONTACTS_ALL, timeout=60)
        people = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].strip():
                people.append((parts[0].strip(), [x for x in parts[1].split(",") if x.strip()],
                               [x for x in parts[2].split(",") if x.strip()]))  # fmt: skip
        self._contacts = (time.time(), people)
        return people

    def _whatsapp_phone(self, wanted: str) -> str | None:
        """The phone number of a WhatsApp chat named like `wanted` (lea_profile/whatsapp.py)."""
        from lea_profile.whatsapp import get

        wa = get(self.cfg)
        if wa is None:
            return None
        try:
            jid = wa.phone_for(wanted)
        except Exception:
            return None
        return jid

    def _resolve(self, wanted: str, want_email: bool = False) -> tuple[str | None, str]:
        """(phone or email, name) for a person, or (None, why not)."""
        w = str(wanted).strip()
        if re.fullmatch(r"\+?[\d\s().-]{6,}", w):
            return re.sub(r"[^\d+]", "", w), w
        try:
            hits = match_contacts(self.contacts(), w)
        except OSAError as e:
            return None, f"couldn't read Contacts ({e})"
        if not hits:
            return None, f"there's no contact called {w} in Contacts"
        if len({h[0] for h in hits}) > 1:
            return None, "several contacts match: " + ", ".join(
                sorted({h[0] for h in hits})[:6]
            ) + ". Ask Lea which one."
        name, phones, emails = hits[0]
        handle = (emails or phones)[0] if want_email else (phones or emails or [None])[0]
        if not handle:
            return None, f"{name} has no phone number or email in Contacts"
        return handle.strip(), name

    def _summary(self, name: str, a: dict) -> str:
        if name == "send_message":
            app = "WhatsApp" if a.get("app") == "whatsapp" else "Messages"
            return f'Send a message to {a["to"]} ({app}): "{a["text"]}"'
        if name == "send_email":
            body = str(a["body"])
            preview = body if len(body) < 400 else body[:400] + "…"
            return f'Send an email to {a["to"]}, subject "{a["subject"]}": "{preview}"'
        if name == "run_command":
            return f"Run in Terminal: {a['command']}  ({a.get('why', '')})"
        if name == "run_shortcut":
            return f'Run the Shortcut "{a["name"]}"'
        return f"{name} {a}"

    def _execute(self, name: str, a: dict, action_id: int | None = None) -> ActionOutcome:
        try:
            if name == "music":
                cmd = a["command"]
                if cmd == "now_playing":
                    return ActionOutcome(
                        "Now playing: " + (self.osa.run(scripts.SPOTIFY_COMMAND, "status") or "nothing")
                    )
                res = self.osa.run(scripts.SPOTIFY_COMMAND, cmd, str(a.get("volume", 50)))
                return ActionOutcome(f"Done ({cmd}). Spotify: {res}")
            if name == "save_playlist":
                from core.settings import save_override

                uri = spotify_uri(str(a["link"]))
                key = _playlist_key(str(a["name"]))
                save_override(self.cfg, f"music.playlists.{key}", uri)
                return ActionOutcome(f"Saved '{key}' → {uri}. Say 'play my {key} playlist' to start it.")
            if name in ("music", "play_music") and not self.has_spotify():
                return ActionOutcome(
                    "Spotify isn't installed on this Mac, so nothing can play. Tell Lea: install the Spotify "
                    "desktop app (spotify.com/download) and log in once; then this works.",
                    ok=False,
                )
            if name == "play_music":
                q = str(a["query"]).strip()
                q = spotify_uri(q) or self._saved_playlist(q) or q
                if q.startswith("spotify:"):
                    return ActionOutcome(
                        "Playing: " + self.osa.run(scripts.SPOTIFY_PLAY_URI, q) + " (on this Mac)."
                    )
                try:
                    found = self.spotify_search(q)
                except SpotifyNotSetUp:
                    found = None
                    setup_missing = True
                except Exception as e:  # network, Spotify down: fall back to the search screen
                    found, setup_missing = None, False
                    log.warning("Spotify search failed: %s", e)
                else:
                    setup_missing = False
                if found:
                    uri, label = found
                    playing = self.osa.run(scripts.SPOTIFY_PLAY_URI, uri)
                    return ActionOutcome(
                        f"Playing: {playing or label} (found '{q}' on Spotify; on this Mac)."
                    )
                self.osa.run(scripts.OPEN_LOCATION, "spotify:search:" + quote(q))
                if setup_missing:
                    return ActionOutcome(
                        f"Opened Spotify's search for '{q}', but couldn't start it: to play songs by name Sylvie "
                        "needs a free Spotify developer key. Lea adds it once herself by typing the command "
                        "'make spotify' in the Terminal app, in the Sylvie folder (about 5 minutes; it is NOT a "
                        "Shortcut and you can't run it for her). Say this in one sentence; don't ask her to click."
                    )
                return ActionOutcome(
                    f"Nothing on Spotify matched '{q}'. Opened the search; suggest another name."
                )
            if name == "calendar_events":
                raw = self.osa.run(scripts.CALENDAR_EVENTS, str(int(a.get("day_offset", 0))), timeout=40)
                lines = sorted(x for x in raw.splitlines() if x.strip())
                if not lines:
                    return ActionOutcome("No events that day.")
                events = []
                for ln in lines:
                    start, title, cal = (ln.split("\t") + ["", "", ""])[:3]
                    events.append(f"- {start[11:16]} {title} ({cal})")
                return ActionOutcome(
                    'Events:\n<untrusted source="calendar">\n' + "\n".join(events) + "\n</untrusted>"
                )
            if name == "create_event":
                mins = int(a.get("duration_minutes") or 60)
                uid = self.osa.run(
                    scripts.CALENDAR_CREATE, a["title"], a["start"][:16], str(mins), a.get("calendar", "")
                )
                out = ActionOutcome(
                    f'Added "{a["title"]}" on {a["start"][:16].replace("T", " at ")} ({mins} min). Lea can say "undo" to remove it.'
                )
                out.undo = {"kind": "event", "uid": uid}
                return out
            if name == "create_reminder":
                rid = self.osa.run(scripts.REMINDER_CREATE, a["title"], (a.get("due") or "")[:16])
                out = ActionOutcome(
                    f'Reminder added: "{a["title"]}"'
                    + (f" for {a['due'][:16].replace('T', ' at ')}" if a.get("due") else "")
                    + "."
                )
                out.undo = {"kind": "reminder", "id": rid}
                return out
            if name == "set_timer":
                return self._timer(float(a["minutes"]), str(a.get("label") or "Timer"), action_id)
            if name == "open_app":
                p = self.run_cmd(["open", "-a", str(a["name"])])
                return ActionOutcome(
                    f"Opened {a['name']}."
                    if p.returncode == 0
                    else f"Couldn't find an app called {a['name']}.",
                    ok=p.returncode == 0,
                )
            if name == "open_in_browser":
                p = self.run_cmd(["open", str(a["url"])])
                return ActionOutcome(
                    "Opened it in the browser." if p.returncode == 0 else "Couldn't open it.",
                    ok=p.returncode == 0,
                )
            if name == "run_command":
                ok, out = shell.run(str(a["command"]), runner=self.shell_runner)
                return ActionOutcome(out, ok=ok)
            if name == "run_shortcut":
                p = self.run_cmd(["shortcuts", "run", str(a["name"])])
                return ActionOutcome(
                    f'Ran the Shortcut "{a["name"]}".'
                    if p.returncode == 0
                    else f"The Shortcut failed: {p.stderr[:150]}",
                    ok=p.returncode == 0,
                )
            if name == "read_emails":
                n = max(1, min(int(a.get("count") or 5), 15))
                raw = self.osa.run(
                    scripts.MAIL_READ, str(n), "1" if a.get("unread_only") else "0", timeout=60
                )
                return ActionOutcome(
                    'Lea\'s latest emails:\n<untrusted source="email">\n'
                    + (raw or "(none)")
                    + "\n</untrusted>"
                )
            if name == "watch":
                service = str(a.get("service") or "netflix").lower()
                service = service if service in WATCH_SEARCH else "netflix"
                title = str(a["title"]).strip()
                link = self.find_title(service, title)
                url = link or WATCH_SEARCH[service].format(q=quote(title))
                p = self.run_cmd(["open", url])
                if p.returncode != 0:
                    return ActionOutcome("Couldn't open the browser.", ok=False)
                where = {"netflix": "Netflix", "youtube": "YouTube", "prime": "Prime Video", "disney": "Disney+",
                         "max": "Max", "movistar": "Movistar Plus+", "appletv": "Apple TV"}[service]  # fmt: skip
                if link:
                    return ActionOutcome(f"{title} is starting on {where} in the browser.")
                return ActionOutcome(
                    f"Opened {where}'s search for {title} in the browser (couldn't find the exact title to start "
                    "it directly): Lea picks it there."
                )
            if name == "send_message":
                text = str(a["text"]).strip()
                if a.get("app") == "whatsapp":
                    phone = self._whatsapp_phone(str(a["to"])) or self._resolve(str(a["to"]))[0]
                    if not phone or "@" in phone:
                        return ActionOutcome(f"Couldn't find a WhatsApp number for {a['to']}.", ok=False)
                    if not any(
                        Path(d).exists()
                        for d in (
                            "/Applications/WhatsApp.app",
                            str(Path.home() / "Applications/WhatsApp.app"),
                        )
                    ):
                        return ActionOutcome(
                            "WhatsApp isn't installed on this Mac, so the message can't be written there. Tell Lea; "
                            "Messages works, or she can install WhatsApp from the App Store.",
                            ok=False,
                        )
                    digits = re.sub(r"\D", "", phone)
                    p = self.run_cmd(["open", f"whatsapp://send?phone={digits}&text={quote(text)}"])
                    return ActionOutcome(
                        f"WhatsApp is open with the message to {a['to']} written; Lea presses send.",
                        ok=p.returncode == 0,
                    )
                handle, who = self._resolve(str(a["to"]))
                if handle is None:
                    return ActionOutcome(f"Not sent: {who}.", ok=False)
                res = self.osa.run(scripts.MESSAGES_SEND, text, handle, timeout=30)
                if res == "sent":
                    return ActionOutcome(f"Message sent to {who}.")
                return ActionOutcome("Messages didn't confirm it was sent.", ok=False)
            if name == "call":
                handle, who = self._resolve(str(a["to"]))
                if handle is None:
                    return ActionOutcome(f"No call: {who}.", ok=False)
                scheme = "facetime" if a.get("video") in (True, "true", 1) else "facetime-audio"
                p = self.run_cmd(["open", f"{scheme}://{quote(handle)}"])
                return ActionOutcome(
                    f"FaceTime is calling {who}." if p.returncode == 0 else "Couldn't open FaceTime.",
                    ok=p.returncode == 0,
                )
            if name == "directions":
                flag = {"walking": "w", "transit": "r"}.get(str(a.get("mode") or ""), "d")
                p = self.run_cmd(["open", f"maps://?daddr={quote(str(a['destination']))}&dirflg={flag}"])
                return ActionOutcome(
                    f"Maps is open with directions to {a['destination']}."
                    if p.returncode == 0
                    else "Couldn't open Maps.",
                    ok=p.returncode == 0,
                )
            if name == "create_note":
                import html

                body = "<br>".join(html.escape(line) for line in str(a["body"]).splitlines())
                nid = self.osa.run(
                    scripts.NOTE_CREATE, str(a["title"]), f"<h1>{html.escape(str(a['title']))}</h1>{body}"
                )
                out = ActionOutcome(f'Note "{a["title"]}" written in Notes.')
                out.undo = {"kind": "note", "id": nid}
                return out
            if name in ("draft_email", "send_email"):
                sender = self.cfg.get_path("actions.mail_from", "") or ""
                res = self.osa.run(
                    scripts.MAIL_COMPOSE,
                    a["to"],
                    a["subject"],
                    a["body"],
                    sender,
                    "1" if name == "send_email" else "0",
                )
                if name == "send_email":
                    if res == "sent":
                        return ActionOutcome("Email sent.")
                    return ActionOutcome(
                        "Mail didn't confirm that it sent the email; check the Outbox.", ok=False
                    )
                return ActionOutcome("Draft opened in Mail for Lea to check.")
        except OSAError as e:
            return ActionOutcome(f"Couldn't do it: {e}.", ok=False)
        return ActionOutcome(f"Unknown action {name}.", ok=False)

    def _timer(self, minutes: float, label: str, action_id: int | None) -> ActionOutcome:
        def ring():
            try:
                self.osa.run(scripts.NOTIFY, "Sylvie", f"⏰ {label} — time's up!")
            except OSAError:
                pass
            self.timers.pop(action_id, None)

        t = threading.Timer(minutes * 60, ring)
        t.daemon = True
        t.start()
        if action_id is not None:
            self.timers[action_id] = t
        tz = ZoneInfo(self.cfg.get_path("user.timezone", "Europe/Madrid"))
        end = datetime.now(tz) + timedelta(minutes=minutes)
        out = ActionOutcome(f"Timer set: {label}, {minutes:g} min (ends at {end:%H:%M}).")
        out.undo = {"kind": "timer"}
        return out
