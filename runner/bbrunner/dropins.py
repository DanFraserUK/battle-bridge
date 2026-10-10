"""Drop-in files: your own jobs, checks and stat boxes.

They live in root-owned folders on the host, one TOML file each:

    /etc/battle-bridge/jobs.d/<name>.toml     a job for the Engineering drawer
    /etc/battle-bridge/checks.d/<name>.toml   an outcome check for Scheduled jobs
    /etc/battle-bridge/panels.d/<name>.toml   a stat box from a JSON file or URL

A file is used only if it and its folder are owned by root and nobody else
can write to them.  Otherwise the runner's own user (or anything that got
in as it) could add a job.  Commands are argument lists, never a shell
string.  See examples/ in the repo.
"""

import json
import os
import re
import stat
import urllib.request

try:
    import tomllib
except ImportError:          # Python older than 3.11
    tomllib = None

CONFIG_DIR = os.environ.get("BB_CONFIG_DIR", "/etc/battle-bridge")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
EVERY = re.compile(r"^(\d+)\s*([smhd])$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class Bad(ValueError):
    pass


def seconds(text, default):
    if text is None:
        return default
    m = EVERY.match(str(text).strip().lower())
    if not m:
        raise Bad(f"'every' should look like 15m, 1h or 1d, not {text!r}")
    return max(60, int(m.group(1)) * UNITS[m.group(2)])


def _trusted(path, owner):
    st = os.stat(path)
    return st.st_uid == owner and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def load(kind, config_dir=None, owner=0):
    """(items, problems) for one folder.  Each item is the file's table plus
    its name.  Problems are short sentences for the page."""
    folder = os.path.join(config_dir or CONFIG_DIR, f"{kind}.d")
    if not os.path.isdir(folder):
        return [], []
    if tomllib is None:
        return [], [f"{kind}.d: drop-in files need Python 3.11 or newer on the host."]
    if not _trusted(folder, owner):
        return [], [f"{folder} must be owned by root and writable only by root.  Ignored."]
    items, problems = [], []
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".toml"):
            continue
        name = fn[:-5]
        path = os.path.join(folder, fn)
        try:
            if not NAME.match(name):
                raise Bad("the name must be lower case letters, digits and dashes")
            if not _trusted(path, owner):
                raise Bad("must be owned by root and writable only by root")
            with open(path, "rb") as f:
                data = tomllib.load(f)
            items.append(CHECKERS[kind](name, data))
        except (Bad, OSError) as e:
            problems.append(f"{kind}.d/{fn}: {e}")
        except tomllib.TOMLDecodeError as e:
            problems.append(f"{kind}.d/{fn}: not valid TOML ({e})")
    return items, problems


def _text(data, key, required=True, limit=120):
    v = data.get(key)
    if v is None:
        if required:
            raise Bad(f"needs {key} = \"...\"")
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > limit or "\n" in v:
        raise Bad(f"{key} should be one line of text, {limit} characters at most")
    return v.strip()


def _command(data):
    cmd = data.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
        raise Bad('command should be a list, like ["/usr/bin/restic", "snapshots"]')
    if not cmd[0].startswith("/"):
        raise Bad("the command's first item should be a full path, like /usr/bin/restic")
    if len(cmd) > 64 or any(len(a) > 1000 or "\0" in a for a in cmd):
        raise Bad("command is too long")
    return cmd


def _job(name, data):
    cwd = data.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.startswith("/")):
        raise Bad("cwd should be a full path")
    confirm = _text(data, "confirm", required=False, limit=300)
    changes = data.get("changes", confirm is not None)
    if not isinstance(changes, bool):
        raise Bad("changes should be true or false")
    return {"name": name, "label": _text(data, "label", limit=80),
            "group": _text(data, "group", required=False, limit=40) or "Your jobs",
            "command": _command(data), "cwd": cwd, "confirm": confirm, "changes": changes,
            "interruptible": bool(data.get("interruptible", False))}


def _check(name, data):
    expect = data.get("expect")
    if expect is not None and not isinstance(expect, str):
        raise Bad('expect should be text: "" for no output, "not empty", "exit 0", or the exact output')
    timeout = data.get("timeout", 60)
    if not isinstance(timeout, int) or not 1 <= timeout <= 600:
        raise Bad("timeout should be a number of seconds, 600 at most")
    return {"name": name, "label": _text(data, "label", limit=80), "command": _command(data),
            "expect": expect, "every": seconds(data.get("every"), 3600), "timeout": timeout,
            "note": _text(data, "note", required=False, limit=200)}


def _panel(name, data):
    source = _text(data, "source", limit=500)
    if not (source.startswith("/") or re.match(r"^https?://", source)):
        raise Bad("source should be a full file path or an http(s) address")
    link = _text(data, "link", required=False, limit=500)
    if link and not re.match(r"^https?://", link):
        raise Bad("link should be an http(s) address")
    show = data.get("show")
    if not isinstance(show, list) or not show or len(show) > 12:
        raise Bad("needs one to twelve [[show]] tables, each with a label and a key")
    fields = []
    for s in show:
        if not isinstance(s, dict):
            raise Bad("each [[show]] needs a label and a key")
        key = _text(s, "key", limit=200)
        if not re.match(r"^[A-Za-z0-9_.\-\[\]]+$", key):
            raise Bad(f"key {key!r} should be a dotted path like last_7_days.vehicles")
        fields.append({"label": _text(s, "label", limit=60), "key": key,
                       "unit": _text(s, "unit", required=False, limit=12) or ""})
    return {"name": name, "title": _text(data, "title", limit=60), "source": source, "link": link,
            "every": seconds(data.get("every"), 300), "show": fields}


CHECKERS = {"jobs": _job, "checks": _check, "panels": _panel}


# --- running checks and reading panels -------------------------------------------------

def run_check(check, run):
    code, out, err = run(check["command"], check["timeout"])
    output = (out or "").strip()
    expect = check["expect"]
    if expect is None:
        ok = code == 0
    elif expect.startswith("exit "):
        ok = str(code) == expect[5:].strip()
    elif expect == "not empty":
        ok = bool(output)
    else:
        ok = output == expect.strip()
    shown = output or (err or "").strip()
    return {"ok": ok, "exit": code, "output": shown[-300:]}


def pick(data, key):
    """'last_7_days.vehicles' or 'items[0].name' out of parsed JSON."""
    cur = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", key):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
    if isinstance(cur, bool):
        return "yes" if cur else "no"
    if isinstance(cur, (int, float)):
        return cur
    if isinstance(cur, str):
        return cur[:80]
    return None


def read_panel(panel, timeout=10):
    src = panel["source"]
    if src.startswith("/"):
        with open(src, "rb") as f:
            raw = f.read(2_000_000)
    else:
        req = urllib.request.Request(src, headers={"User-Agent": "battle-bridge", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(2_000_000)
    data = json.loads(raw)
    return [{"label": f["label"], "value": pick(data, f["key"]), "unit": f["unit"]} for f in panel["show"]]
