"""Turning command output into data.  Pure functions, so they can be tested
against real output."""

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree


# --- root-facts ----------------------------------------------------------------

def root_facts(text):
    """Split root-facts output into [(section, argument, text)]."""
    out = []
    current = None
    for line in text.splitlines():
        m = re.match(r"^=== (\S+)(?: (.+))?$", line)
        if m:
            if m.group(1) == "end":
                current = None
                continue
            current = [m.group(1), m.group(2) or "", []]
            out.append(current)
        elif current is not None:
            current[2].append(line)
    return [(s, a, "\n".join(t)) for s, a, t in out]


# --- SMART (smartctl --json) ------------------------------------------------------

def _attr(table, ident):
    for a in table or []:
        if a.get("id") == ident:
            return (a.get("raw") or {}).get("value")
    return None


def smart(device, text):
    """One disk's health from `smartctl -a --json`.  No serial numbers."""
    name = device.rsplit("/", 1)[-1]
    try:
        d = json.loads(text)
    except ValueError:
        return {"name": name, "ok": None, "error": "smartctl gave no JSON"}
    msgs = [m.get("string", "") for m in (d.get("smartctl") or {}).get("messages") or []
            if m.get("severity") == "error"]
    status = (d.get("smart_status") or {}).get("passed")
    out = {
        "name": name,
        "model": d.get("model_name") or d.get("model_family") or (d.get("device") or {}).get("type", ""),
        "ok": status,
        "temp": (d.get("temperature") or {}).get("current"),
        "hours": (d.get("power_on_time") or {}).get("hours"),
        "notes": [],
    }
    if status is None:
        out["error"] = msgs[0][:160] if msgs else "no SMART status (USB enclosure?)"
    table = (d.get("ata_smart_attributes") or {}).get("table")
    for ident, what in ((5, "reallocated sectors"), (197, "pending sectors"), (198, "uncorrectable sectors")):
        v = _attr(table, ident)
        if v:
            out["notes"].append(f"{v} {what}")
    nvme = d.get("nvme_smart_health_information_log") or {}
    if nvme:
        if nvme.get("media_errors"):
            out["notes"].append(f"{nvme['media_errors']} media errors")
        if nvme.get("critical_warning"):
            out["notes"].append("critical warning set")
        if nvme.get("percentage_used") is not None:
            out["wear"] = nvme["percentage_used"]
    tests = ((d.get("ata_smart_self_test_log") or {}).get("standard") or {}).get("table") or []
    if tests:
        t = tests[0]
        out["last_test"] = {"passed": (t.get("status") or {}).get("passed"),
                            "hours": t.get("lifetime_hours"), "type": (t.get("type") or {}).get("string")}
        if out["last_test"]["passed"] is False:
            out["notes"].append("last self-test failed")
    return out


# --- btrfs and ZFS ---------------------------------------------------------------------

def btrfs_device_stats(text):
    devs = {}
    for line in text.splitlines():
        m = re.match(r"\[(.+?)\]\.(\w+)\s+(\d+)", line.strip())
        if m:
            dev, key, val = m.group(1), m.group(2), int(m.group(3))
            devs.setdefault(dev, {"device": dev, "total": 0})
            devs[dev][key] = val
            devs[dev]["total"] += val
    return list(devs.values())


def btrfs_scrub_status(text):
    out = {"never": "no stats available" in text, "started": None, "status": None,
           "duration": None, "errors": None, "total": None}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        if key == "Scrub started":
            out["started"] = val
        elif key == "Status":
            out["status"] = val
        elif key == "Duration":
            out["duration"] = val
        elif key == "Error summary":
            out["errors"] = val
        elif key == "Total to scrub":
            out["total"] = val
    out["ok"] = (out["errors"] or "").startswith("no errors")
    return out


def btrfs_section(mount, text):
    stats, _, scrub = text.partition("--- scrub")
    return {"mount": mount, "devices": btrfs_device_stats(stats), "scrub": btrfs_scrub_status(scrub)}


def zpools(text):
    """`zpool list -H -o name,health,size,allocated,free,capacity` lines."""
    out = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) >= 6:
            out.append({"name": f[0], "health": f[1], "size": f[2], "alloc": f[3], "free": f[4], "cap": f[5],
                        "ok": f[1] == "ONLINE"})
    return out


# --- systemd ---------------------------------------------------------------------

def timers(text):
    """`systemctl list-timers --all -o json`.  Times are microseconds."""
    out = []
    for t in json.loads(text or "[]"):
        out.append({
            "unit": t.get("unit"),
            "activates": t.get("activates"),
            "next": _usec(t.get("next")),
            "last": _usec(t.get("last")),
        })
    return out


def _usec(v):
    if v in (None, 0, "", "n/a"):
        return None
    try:
        return int(v) / 1_000_000
    except (TypeError, ValueError):
        return None


def systemctl_show(text):
    """`systemctl show a b -p X,Y` output: blocks split by blank lines."""
    blocks = [{}]
    for line in text.splitlines():
        if not line.strip():
            if blocks[-1]:
                blocks.append({})
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            blocks[-1][k] = v
    return [b for b in blocks if b]


# --- packages ----------------------------------------------------------------

def update_list(text):
    """checkupdates / yay -Qua lines: 'name old -> new'."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(\S+)\s+(\S+)\s+->\s+(\S+)", line.strip())
        if m:
            out.append({"name": m.group(1), "old": m.group(2), "new": m.group(3)})
    return out


def apt_upgradable(text):
    """`apt list --upgradable`: 'bash/noble-updates 5.2-4.1 amd64 [upgradable from: 5.2-4]'."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^([^/\s]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]", line)
        if m:
            out.append({"name": m.group(1), "old": m.group(3), "new": m.group(2)})
    return out


def dnf_check_update(text):
    """`dnf -q check-update`: 'kernel.x86_64   6.11.4-301.fc41   updates'."""
    out = []
    for line in text.splitlines():
        if line.startswith(("Obsoleting", "Security:", "Last metadata")):
            break
        f = line.split()
        if len(f) == 3 and "." in f[0]:
            out.append({"name": f[0].rsplit(".", 1)[0], "old": None, "new": f[1]})
    return out


def changed_since(pacman_log, since_epoch, watched):
    """Packages matching `watched` upgraded or installed after a time (the boot)."""
    hits = {}
    for line in pacman_log.splitlines():
        m = re.match(r"^\[(\S+?)\] \[ALPM\] (upgraded|installed|reinstalled) (\S+) \((.+)\)$", line)
        if not m or not watched.match(m.group(3)):
            continue
        try:
            when = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            continue
        if when > since_epoch:
            hits[m.group(3)] = {"name": m.group(3), "when": when, "change": m.group(4)}
    return list(hits.values())


# --- Arch news ---------------------------------------------------------------

def arch_news(xml_text, limit=5):
    root = ElementTree.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        date = item.findtext("pubDate")
        try:
            when = parsedate_to_datetime(date).timestamp() if date else None
        except (TypeError, ValueError):
            when = None
        if not link.startswith("https://archlinux.org/"):
            link = "https://archlinux.org/news/"
        items.append({"title": title[:200], "link": link, "when": when})
        if len(items) >= limit:
            break
    return items


# --- certificates ------------------------------------------------------------

def openssl_enddate(text):
    """`openssl x509 -noout -subject -enddate` output."""
    subject = re.search(r"CN\s*=\s*([^,\n/]+)", text)
    end = re.search(r"notAfter=(.+)", text)
    when = None
    if end:
        try:
            when = datetime.strptime(end.group(1).strip().replace(" GMT", ""), "%b %d %H:%M:%S %Y")
            when = when.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            when = None
    return {"domain": subject.group(1).strip() if subject else None, "expires": when}


def certs(text):
    """root-facts' certs section: '--- <name>' then openssl's two lines."""
    out = []
    for block in re.split(r"^--- ", text, flags=re.M):
        if not block.strip():
            continue
        name, _, rest = block.partition("\n")
        c = openssl_enddate(rest)
        c["name"] = name.strip()
        out.append(c)
    return out
