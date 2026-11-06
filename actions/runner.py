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
        "Control Spotify on this Mac: play, pause, next, previous, volume_up, volume_down, set_volume (0-100), now_playing.",
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
        "Play something on Spotify. For one of Lea's saved playlists pass its name (e.g. 'study'); a "
        "spotify: URI or open.spotify.com link plays directly; other words open Spotify's search.",
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
}

NEEDS_YES = {"send_email", "run_shortcut"}


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
    ):
        self.has_spotify = has_spotify or spotify_installed
        self.shell_runner = shell_runner or subprocess.run
        self.spotify_search = spotify_search or _web_api_search
        self.cfg = cfg
        self.log = log
        self.osa = osa or OSARunner()
        self.run_cmd = run_cmd or (
            lambda args: subprocess.run(args, capture_output=True, text=True, timeout=30)
        )
        self.is_locked = is_locked
        self.timers: dict[int, threading.Timer] = {}

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
        needs_yes = name in NEEDS_YES or (
            name == "run_command"
            and (after_untrusted or not shell.is_read_only(str(args.get("command", ""))))
        )
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

    def _summary(self, name: str, a: dict) -> str:
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
