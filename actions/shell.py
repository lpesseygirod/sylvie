"""Terminal commands for Sylvie (Lea, 2026-11-06: "she's free to do all this type of stuff, she has
control over my computer").

Three kinds of command:
- read-only ones (look, list, check) run straight away;
- anything else (changes, deletes, installs, sends) is read back to Lea and needs her "yes" first,
  and so does everything once outside content (web, email, files) was read in the same turn;
- a few never run, even with a yes: sudo, erasing disks, wiping her home folder, printing Keychain
  passwords, and "download and run" (curl … | sh).
Commands run as Lea, from her home folder, without a keyboard (no prompts), with a time limit.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

TIMEOUT_S = 120
MAX_OUTPUT = 6000

# Commands that only look at things. Arguments are checked too where a flag would change something.
READ_ONLY = {
    "ls", "cat", "head", "tail", "less", "wc", "grep", "egrep", "rg", "pwd", "whoami", "date", "cal", "df",
    "du", "ps", "uptime", "sw_vers", "uname", "hostname", "which", "whereis", "file", "stat", "echo",
    "printf", "env", "printenv", "id", "mdfind", "mdls", "system_profiler", "pmset", "defaults", "diskutil",
    "networksetup", "ifconfig", "ping", "dig", "nslookup", "top", "vm_stat", "sysctl", "lsof", "pgrep",
    "open", "shortcuts", "say", "sort", "uniq", "cut", "tr", "column", "jq", "find", "git", "brew", "pip",
    "pip3", "npm", "python3", "node", "tree", "realpath", "basename", "dirname", "sleep", "true",
}  # fmt: skip
# Subcommands/flags that make an otherwise read-only command change things.
WRITE_ARGS = {
    "defaults": {"write", "delete", "import", "rename"},
    "pmset": {"-a", "-b", "-c", "-u", "schedule", "repeat", "sleepnow", "displaysleepnow", "restoredefaults"},
    "diskutil": {"erasedisk", "erasevolume", "partitiondisk", "unmount", "unmountdisk", "eject", "mount",
                 "rename", "repairvolume", "apfs", "secureerase", "zerodisk", "randomdisk"},
    "networksetup": {"-set", "-setairportpower", "-setairportnetwork", "-setdnsservers", "-setwebproxy",
                     "-setv4off", "-setdhcp", "-setmanual", "-removepreferredwirelessnetwork"},
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint"},
    "git": {"push", "commit", "reset", "checkout", "clean", "rebase", "merge", "pull", "rm", "mv", "stash",
            "add", "tag", "branch", "remote", "switch", "restore", "init", "clone", "config", "am", "apply"},
    "brew": {"install", "uninstall", "remove", "upgrade", "update", "reinstall", "link", "unlink", "cleanup",
             "tap", "untap", "services", "autoremove"},
    "pip": {"install", "uninstall", "download"},
    "pip3": {"install", "uninstall", "download"},
    "npm": {"install", "i", "uninstall", "ci", "publish", "update", "run", "exec", "link"},
    "shortcuts": {"run", "sign"},
    "sysctl": {"-w"},
}  # fmt: skip
# Interpreters run code: read-only only when just asking for a version.
VERSION_ONLY = {"python3", "node"}

NEVER = [
    (re.compile(r"(^|[\s;&|(])sudo\b"), "sudo (it needs Lea's password; Sylvie never uses it)"),
    (re.compile(r"\b(diskutil\s+(erase\w*|secureErase|zeroDisk|randomDisk|partitionDisk)|mkfs|newfs_\w+)\b", re.I),
     "erasing or formatting a disk"),
    (re.compile(r"\bdd\b[^|;&]*\bof=/dev/"), "writing raw data to a disk"),
    (re.compile(r"\brm\s+(-\w*\s+)*(/|~|\$HOME|/Users/?\w*|~/(Documents|Desktop|Library|Pictures))/?\*?(\s|$)"),
     "deleting the whole disk, the home folder or a main folder"),
    (re.compile(r"\bsecurity\s+(dump-keychain|find-(generic|internet)-password\b[^|;&]*\s-w)"),
     "revealing passwords from the Keychain"),
    (re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(ba|z)?sh\b"), "downloading and running code"),
    (re.compile(r"\b(csrutil|spctl\s+--master-disable)\b"), "turning off macOS security protections"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "a fork bomb"),
]  # fmt: skip

_SPLIT = re.compile(r"\|\||&&|;|\||\n")


def never_reason(command: str) -> str | None:
    for pattern, why in NEVER:
        if pattern.search(command):
            return why
    return None


def is_read_only(command: str) -> bool:
    """True only if every part of the command just looks at things."""
    if re.search(r"(?<![0-9&])>|>>|<\(|\$\(|`", command):  # writing files, command substitution
        return False
    for part in _SPLIT.split(command):
        part = part.strip()
        if not part:
            continue
        try:
            words = shlex.split(part)
        except ValueError:
            return False
        while words and re.fullmatch(r"\w+=\S*", words[0]):  # FOO=bar cmd
            words = words[1:]
        if not words:
            continue
        cmd = os.path.basename(words[0])
        if cmd not in READ_ONLY:
            return False
        args = {w.lower() for w in words[1:]}
        if cmd in VERSION_ONLY and not args <= {"--version", "-v", "-V"}:
            return False
        if cmd in WRITE_ARGS and args & {a.lower() for a in WRITE_ARGS[cmd]}:
            return False
    return True


def run(command: str, cwd: Path | None = None, runner=subprocess.run) -> tuple[bool, str]:
    """Runs it in zsh as Lea; returns (exit ok, output)."""
    try:
        p = runner(
            ["/bin/zsh", "-lc", command],
            cwd=str(cwd or Path.home()),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"Stopped after {TIMEOUT_S} s without finishing."
    out = ((p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")).strip()
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n… ({len(out) - MAX_OUTPUT} more characters)"
    return p.returncode == 0, f"exit code {p.returncode}\n{out or '(no output)'}"
