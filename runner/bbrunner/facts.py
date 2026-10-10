"""Facts the container can't see for itself.

Each gatherer returns plain data, or {"error": ...}.  None of them change
anything.  Paths and commands come from Settings, so tests can point them at
fixtures, and /etc/battle-bridge/runner.toml can change a few.
"""

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field

from . import distro, dropins, parse, registry

LIB = "/usr/local/lib/battle-bridge"

# Filesystems worth a space bar.
REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "bcachefs", "jfs", "reiserfs",
           "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "nfs", "nfs4", "cifs", "smb3", "fuse.mergerfs",
           "fuse.sshfs"}
# Places that are part of the system, not storage.
SKIP_MOUNTS = re.compile(r"^/(boot|efi|snap|var/lib/(docker|containers|snapd)|var/snap|run|proc|sys|dev)(/|$)")


@dataclass
class Settings:
    proc: str = "/proc"
    sys: str = "/sys"
    os_release: str = "/etc/os-release"
    root_facts: list = field(default_factory=lambda: ["sudo", "-n", f"{LIB}/root-facts"])
    fstab: str = "/etc/fstab"
    pacman_log: str = "/var/log/pacman.log"
    modules_dirs: tuple = ("/usr/lib/modules", "/lib/modules")
    reboot_file: str = "/run/reboot-required"
    news_url: str = "https://archlinux.org/feeds/news/"
    pc_log_dir: str = os.environ.get("BB_PC_LOG_DIR", "/var/log/battle-bridge/pc-jobs")
    config_dir: str = dropins.CONFIG_DIR
    owner: int = 0                    # who must own drop-in files
    mounts: list = None               # from runner.toml: pin the list of drives
    hide_mounts: list = field(default_factory=list)
    after_reboot_mounts: list = None
    gpu_container: str = ""

    def load_toml(self):
        """Read /etc/battle-bridge/runner.toml if it's there and root-owned."""
        path = os.path.join(self.config_dir, "runner.toml")
        if dropins.tomllib is None or not os.path.isfile(path):
            return None
        try:
            if not dropins._trusted(path, self.owner):
                return "runner.toml must be owned by root and writable only by root.  Ignored."
            with open(path, "rb") as f:
                data = dropins.tomllib.load(f)
        except (OSError, dropins.tomllib.TOMLDecodeError) as e:
            return f"runner.toml: {e}"
        for key in ("mounts", "hide_mounts", "after_reboot_mounts"):
            v = data.get(key)
            if isinstance(v, list) and all(isinstance(x, str) and x.startswith("/") for x in v):
                setattr(self, key, v)
        if isinstance(data.get("gpu_container"), str):
            self.gpu_container = data["gpu_container"]
        return None


def run(cmd, timeout=60, env=None):
    """(returncode, stdout, stderr).  Never raises for a missing command."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]} timed out"


def _err(msg):
    return {"error": str(msg)[:300]}


def stamp(data):
    data = data if isinstance(data, dict) else {"items": data}
    data["checked"] = time.time()
    return data


# --- drives -----------------------------------------------------------------------------

def _unescape(path):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def mount_table(proc="/proc"):
    """[(mount point, fs type, source, major:minor)] from mountinfo."""
    out = []
    with open(os.path.join(proc, "self", "mountinfo")) as f:
        for line in f:
            left, _, right = line.partition(" - ")
            lf, rf = left.split(), right.split()
            if len(lf) < 5 or len(rf) < 2:
                continue
            out.append((_unescape(lf[4]), rf[0], _unescape(rf[1]), lf[2]))
    return out


def disks_of(source, sysfs="/sys"):
    """The whole disks under a block device: /dev/sda2 -> ['sda'],
    an LVM or LUKS volume -> the disks beneath it."""
    if not source.startswith("/dev/"):
        return []
    try:
        name = os.path.basename(os.path.realpath(source))
    except OSError:
        return []
    out = []
    seen = set()

    def walk(n):
        if n in seen:
            return
        seen.add(n)
        base = os.path.join(sysfs, "class", "block", n)
        slaves = os.path.join(base, "slaves")
        if os.path.isdir(slaves) and os.listdir(slaves):
            for s in os.listdir(slaves):
                walk(s)
            return
        if os.path.exists(os.path.join(base, "partition")):
            parent = os.path.basename(os.path.dirname(os.path.realpath(base)))
            out.append(parent)
        elif os.path.exists(base):
            out.append(n)
    walk(name)
    return sorted(set(out))


def disks(s):
    try:
        table = mount_table(s.proc)
    except OSError as e:
        return _err(e)
    chosen = {}
    for mount, fstype, source, dev in table:
        if s.mounts is not None:
            if mount not in s.mounts:
                continue
        elif fstype not in REAL_FS or SKIP_MOUNTS.match(mount) or mount in s.hide_mounts:
            continue
        # One entry per filesystem: btrfs subvolumes and bind mounts share a
        # device, so keep the shortest path.
        key = dev if fstype not in ("nfs", "nfs4", "cifs", "smb3") else source
        if key not in chosen or len(mount) < len(chosen[key][0]):
            chosen[key] = (mount, fstype, source)
    out = []
    for mount, fstype, source in sorted(chosen.values(), key=lambda x: (x[0] != "/", x[0])):
        d = {"label": "System" if mount == "/" else os.path.basename(mount) or mount, "mount": mount,
             "fs": fstype, "mounted": True, "disks": disks_of(source, s.sys)}
        try:
            st = os.statvfs(mount)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - st.f_bfree * st.f_frsize
            d.update(total=total, used=used, free=free,
                     pct=round(100 * used / (used + free), 1) if used + free else None)
        except OSError as e:
            d["error"] = str(e)
        out.append(d)
    if s.mounts is not None:
        found = {d["mount"] for d in out}
        for m in s.mounts:
            if m not in found:
                out.append({"label": os.path.basename(m) or m, "mount": m, "mounted": False, "disks": []})
    return {"items": out}


def fstab_mounts(path):
    """Mount points /etc/fstab expects at boot: the after-reboot drive check."""
    out = []
    try:
        with open(path) as f:
            for line in f:
                f2 = line.split()
                if len(f2) < 4 or f2[0].startswith("#"):
                    continue
                target, fstype, opts = _unescape(f2[1]), f2[2], f2[3].split(",")
                if fstype in ("swap", "none", "tmpfs", "proc") or "noauto" in opts or not target.startswith("/"):
                    continue
                if SKIP_MOUNTS.match(target) and target not in ("/boot", "/efi", "/boot/efi"):
                    continue
                out.append(target)
    except OSError:
        pass
    return out


# --- every minute ----------------------------------------------------------------------

def boot_time(s):
    with open(os.path.join(s.proc, "stat")) as f:
        for line in f:
            if line.startswith("btime "):
                return int(line.split()[1])
    return None


def reboot(s, fam):
    try:
        booted = boot_time(s)
    except OSError:
        booted = None
    return distro.reboot(fam, run, booted, s.modules_dirs, s.pacman_log, s.reboot_file)


def timers(s):
    code, out, err = run(["systemctl", "list-timers", "--all", "--no-pager", "-o", "json"])
    if code != 0:
        return _err(err or f"exit {code}")
    try:
        items = parse.timers(out)
    except ValueError as e:
        return _err(e)
    # How each timer's job last ended, in one call.
    units = [t["activates"] for t in items if t["activates"]]
    if units:
        code, text, _ = run(["systemctl", "show", "--no-pager", "-p", "Id,Result,ActiveState"] + units)
        if code == 0:
            res = {b.get("Id"): b for b in parse.systemctl_show(text)}
            for t in items:
                b = res.get(t["activates"]) or {}
                t["result"] = b.get("Result")
                t["active"] = b.get("ActiveState")
    return {"items": items}


def smartd(s):
    code, text, _ = run(["systemctl", "show", "--no-pager", "-p", "Id,ActiveState,LoadState",
                         "smartd.service", "smartmontools.service"])
    if code != 0:
        return {"state": None}
    for b in parse.systemctl_show(text):
        if b.get("LoadState") == "loaded":
            return {"state": b.get("ActiveState"), "unit": b.get("Id")}
    return {"state": None}


PC_LOG = re.compile(r"^(update-system|reboot)-(\d{8}-\d{6})\.log$")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")


def pc_jobs(s):
    """The logs the PC's terminal jobs leave behind."""
    try:
        names = [n for n in os.listdir(s.pc_log_dir) if PC_LOG.match(n)]
    except OSError:
        return {"items": []}
    items = []
    for n in sorted(names, key=lambda x: PC_LOG.match(x).group(2), reverse=True)[:10]:
        path = os.path.join(s.pc_log_dir, n)
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 4000))
                tail = ANSI.sub("", f.read().decode("utf-8", "replace")).strip().splitlines()
        except OSError:
            continue
        last = "\n".join(tail[-6:])
        ok = None
        if "Finished with errors" in last or "Cancelled" in last or "Stopped before" in last:
            ok = False
        elif "Done." in last or "Rebooting." in last:
            ok = True
        items.append({"file": n, "job": PC_LOG.match(n).group(1), "when": os.path.getmtime(path), "ok": ok})
    return {"items": items}


# --- drop-ins: checks and panels, each on its own schedule --------------------------------

def checks(s, previous):
    defs, problems = dropins.load("checks", s.config_dir, s.owner)
    old = {c["name"]: c for c in (previous or {}).get("items") or []}
    now = time.time()
    items = []
    for c in defs:
        prev = old.get(c["name"])
        if prev and prev.get("ran") and now - prev["ran"] < c["every"] and prev.get("every") == c["every"]:
            items.append(dict(prev, label=c["label"], note=c["note"]))
            continue
        try:
            r = dropins.run_check(c, run)
        except Exception as e:
            r = {"ok": False, "output": str(e)[:300]}
        items.append({"name": c["name"], "label": c["label"], "note": c["note"], "every": c["every"],
                      "ran": now, **r})
    return {"items": items, "problems": problems}


def panels(s, previous):
    defs, problems = dropins.load("panels", s.config_dir, s.owner)
    old = {p["name"]: p for p in (previous or {}).get("items") or []}
    now = time.time()
    items = []
    for p in defs:
        prev = old.get(p["name"])
        if prev and prev.get("read") and now - prev["read"] < p["every"]:
            items.append(dict(prev, title=p["title"], link=p["link"]))
            continue
        item = {"name": p["name"], "title": p["title"], "link": p["link"], "read": now}
        try:
            item["values"] = dropins.read_panel(p)
        except Exception as e:
            item["error"] = f"couldn't read {p['source'].rsplit('/', 1)[-1]}: {type(e).__name__}"
            item["values"] = (prev or {}).get("values")
        items.append(item)
    return {"items": items, "problems": problems}


# --- every hour --------------------------------------------------------------------------

def root(s):
    """Drive health, btrfs and ZFS, and NPM's certificates: the things only
    root can read.  One fixed script, allowed by one sudoers line."""
    code, out, err = run(s.root_facts, timeout=300)
    if code != 0:
        msg = err.strip() or f"exit {code}"
        if "password is required" in msg or "a terminal is required" in msg:
            msg = "sudo refused root-facts.  Run the install line again."
        return _err(msg)
    smart, btrfs, zfs, certs, notes = [], [], [], [], []
    for section, arg, text in parse.root_facts(out):
        if section == "smart":
            smart.append(parse.smart(arg, text))
        elif section == "btrfs":
            btrfs.append(parse.btrfs_section(arg, text))
        elif section == "zfs":
            zfs = parse.zpools(text)
        elif section == "certs":
            certs = parse.certs(text)
        elif section == "note":
            notes.append(text.strip()[:200])
    return {"smart": smart, "btrfs": btrfs, "zfs": zfs, "certs": certs, "notes": notes}


def wants_external_ip():
    """Only worth asking the outside world when there's DDNS to compare with."""
    code, out, _ = run(["docker", "ps", "--format", "{{.Image}}"], 30)
    return code == 0 and "cloudflare-ddns" in out


def external_ip():
    if not wants_external_ip():
        return {"skipped": True}
    req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "battle-bridge"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ip = r.read().decode().strip()
    except Exception as e:
        return _err(e)
    if not re.fullmatch(r"[0-9a-fA-F:.]{3,45}", ip):
        return _err("odd answer")
    return {"ip": ip}


# --- every day ---------------------------------------------------------------------------

def updates(fam, cache_dir):
    out = distro.system_updates(fam, run, cache_dir)
    out["family"] = fam
    if fam == "arch":
        aur = distro.aur_updates(run, cache_dir)
        if aur is not None:
            out["aur"] = aur
    return out


def image_signature():
    """Changes whenever an image is pulled, built or removed.  Cheap."""
    code, out, _ = run(["docker", "images", "-q", "--no-trunc"], 30)
    if code != 0:
        return None
    return hashlib.sha1("\n".join(sorted(set(out.split()))).encode()).hexdigest()


def image_updates():
    from . import jobs     # here, not at the top: jobs imports this module
    code, out, err = run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}"], 30)
    if code != 0:
        return _err(err.strip() or f"exit {code}")
    # Built here: its compose file has a build: line.  Newer Docker gives
    # locally built images a digest too, so that alone can't tell.
    built = {c for p in jobs.projects() if p["built_here"] for c in p["containers"]}
    items = []
    checked = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, image = line.split("\t", 1)
        item = {"container": name, "image": image}
        c2, o2, _ = run(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], 30)
        try:
            have = registry.local_digests(json.loads(o2)) if c2 == 0 else set()
        except ValueError:
            have = set()
        if name in built or (c2 == 0 and not have):
            item["status"] = "built here"
            items.append(item)
            continue
        try:
            if image not in checked:
                checked[image] = registry.remote_digest(image)
            remote = checked[image]
            if not have or not remote:
                item["status"] = "unknown"
            else:
                item["status"] = "current" if remote in have else "newer"
        except Exception as e:  # network, auth, odd registries: report, don't stop
            item["status"] = "unknown"
            item["error"] = str(e)[:200]
        items.append(item)
    return {"items": items, "signature": image_signature()}


def arch_news(s):
    req = urllib.request.Request(s.news_url, headers={"User-Agent": "battle-bridge"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return {"items": parse.arch_news(r.read().decode("utf-8", "replace"))}
    except Exception as e:
        return _err(e)


def changed_since_mtime(path):
    try:
        return os.path.getmtime(path) if path else None
    except OSError:
        return None
