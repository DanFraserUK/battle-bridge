"""Write a believable facts.json for the dev server, built from the
fixtures.  python3 make_facts.py <dir> [--trouble]"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "runner"))
from bbrunner import VERSION, parse  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
trouble = "--trouble" in sys.argv
now = time.time()
T = 1024 ** 4
G = 1024 ** 3


def fx(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def stamp(d, ago=60):
    d = d if isinstance(d, dict) else {"items": d}
    d["checked"] = now - ago
    return d


smart, btrfs, zfs, certs = [], [], [], []
for section, arg, text in parse.root_facts(fx("root-facts.txt")):
    if section == "smart":
        smart.append(parse.smart(arg, text))
    elif section == "btrfs":
        btrfs.append(parse.btrfs_section(arg, text))
    elif section == "zfs":
        zfs = parse.zpools(text)
    elif section == "certs":
        certs = parse.certs(text)
if not trouble:
    for b in btrfs:
        for d in b["devices"]:
            d["read_io_errs"] = 0
            d["total"] = 0
    zfs = [z for z in zfs if z["ok"]]
    smart[0]["notes"] = []
smart.append(parse.smart("/dev/sdc", fx("smart-usb.json")))
timers = parse.timers(fx("timers.json"))
for t in timers:
    t["result"] = "failed" if trouble and t["unit"].startswith("restic") else "success"
facts = {
    "os": {"id": "arch", "name": "Arch Linux", "family": "arch"},
    "disks": stamp([
        {"label": "System", "mount": "/", "fs": "ext4", "mounted": True, "disks": ["nvme0n1"],
         "total": 931 * G, "used": 210 * G, "free": 721 * G, "pct": 22.6},
        {"label": "media", "mount": "/mnt/media", "fs": "ext4", "mounted": True, "disks": ["sda"],
         "total": 12.7 * T, "used": (11.8 if trouble else 7.1) * T, "free": (0.9 if trouble else 5.6) * T,
         "pct": 92.9 if trouble else 55.9},
        {"label": "pool", "mount": "/mnt/pool", "fs": "btrfs", "mounted": True, "disks": ["sdb", "sdc"],
         "total": 3.6 * T, "used": 1.2 * T, "free": 2.4 * T, "pct": 33.3},
    ]),
    "reboot": stamp({"running": "6.12.50-1-lts", "booted": now - 13 * 86400, "needed": trouble,
                     "reasons": ["nvidia-utils updated since boot"] if trouble else [], "changed": []}),
    "timers": stamp({"items": timers}),
    "smartd": stamp({"state": "inactive" if trouble else "active", "unit": "smartd.service"}),
    "checks": stamp({"items": [
        {"name": "backup-fresh", "label": "Newest backup under 26 hours old", "ok": not trouble,
         "output": "" if trouble else "/mnt/backup/home-20261010.tar.gz", "exit": 0, "every": 3600,
         "ran": now - 1200, "note": "nightly backup to /mnt/backup"},
        {"name": "camera-cleanup", "label": "No recordings the cleanup missed", "ok": True, "output": "",
         "exit": 0, "every": 21600, "ran": now - 5000, "note": "keeps 31 days"},
    ], "problems": ["jobs.d/Bad Name.toml: the name must be lower case letters, digits and dashes"] if trouble else []}),
    "panels": stamp({"items": [
        {"name": "solar", "title": "Solar today", "link": "http://192.168.1.60/", "read": now - 120,
         "values": [{"label": "generated", "value": 14.2, "unit": "kWh"}, {"label": "peak", "value": 3870, "unit": "W"},
                    {"label": "battery", "value": 86, "unit": "%"}]},
        {"name": "weather", "title": "Garden weather", "link": None, "read": now - 300,
         "values": [{"label": "outside", "value": 13.4, "unit": "°C"}, {"label": "rain today", "value": 2.1, "unit": "mm"},
                    {"label": "wind", "value": "SW 14", "unit": "mph"}]},
    ], "problems": []}),
    "extip": stamp({"ip": "203.0.113.7"}, 900),
    "root": stamp({"smart": smart, "btrfs": btrfs, "zfs": zfs, "notes": [],
                   "certs": certs + [dict(parse.openssl_enddate(
                       f"subject=CN=old.example.org\nnotAfter={time.strftime('%b %d %H:%M:%S %Y', time.gmtime(now + 9 * 86400))} GMT"),
                       name="npm-3")] if trouble else certs}, 1200),
    "updates": stamp({"family": "arch", "items": parse.update_list(fx("checkupdates.txt")),
                      "aur": {"items": [], "helper": "yay"}}, 5 * 3600),
    "image_updates": stamp({"items": [
        {"container": "plex", "image": "lscr.io/linuxserver/plex:latest", "status": "newer"},
        {"container": "sonarr", "image": "lscr.io/linuxserver/sonarr:latest", "status": "newer"},
        {"container": "pihole", "image": "pihole/pihole:latest", "status": "current"},
        {"container": "battle-bridge", "image": "ghcr.io/danfraseruk/battle-bridge:latest", "status": "newer"},
        {"container": "seerr", "image": "ghcr.io/seerr-team/seerr:latest", "status": "unknown", "error": "timed out"},
        {"container": "myapp", "image": "myapp:local", "status": "built here"},
    ]}, 5 * 3600),
    "news": stamp({"items": parse.arch_news(fx("arch-news.xml"))}, 5 * 3600),
    "pc_jobs": stamp({"items": [
        {"file": "update-system-20261008-091200.log", "job": "update-system", "when": now - 38 * 3600, "ok": True},
        {"file": "reboot-20261008-093000.log", "job": "reboot", "when": now - 37 * 3600, "ok": True}]}),
    "runner": {"version": VERSION, "written": now, "pid": 1, "config_problem": None},
}
facts["news"]["items"][0]["when"] = now - 4 * 86400
os.makedirs(sys.argv[1], exist_ok=True)
with open(os.path.join(sys.argv[1], "facts.json"), "w") as f:
    json.dump(facts, f)
print("facts written")
