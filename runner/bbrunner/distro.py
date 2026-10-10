"""Small adapters for each distro family: what's waiting to update, and
whether a reboot is needed.

    arch     Arch, EndeavourOS, Manjaro, CachyOS...   pacman, plus yay or paru
    debian   Debian, Ubuntu, Mint, Pop!_OS...         apt
    fedora   Fedora, RHEL, Rocky, Alma...             dnf

Every check runs as the runner's user, never root, and changes nothing.
"""

import os
import re
import shutil

from . import parse

# Packages that only take effect after a reboot.
REBOOT_PACKAGES = re.compile(r"^(linux(-[a-z0-9-]+)?|nvidia(-[a-z0-9-]+)?|systemd|glibc|docker|containerd|"
                             r"amd-ucode|intel-ucode|libnvidia-container|nvidia-container-toolkit)$")


def os_release(path="/etc/os-release"):
    info = {}
    try:
        with open(path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    info[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return info


def family(info):
    ids = [info.get("ID", "")] + info.get("ID_LIKE", "").split()
    for i in ids:
        if i in ("arch", "archlinux", "endeavouros", "manjaro", "cachyos", "garuda"):
            return "arch"
        if i in ("debian", "ubuntu", "linuxmint", "pop", "raspbian"):
            return "debian"
        if i in ("fedora", "rhel", "centos", "rocky", "almalinux"):
            return "fedora"
    return "other"


def describe(path="/etc/os-release"):
    info = os_release(path)
    return {"id": info.get("ID", ""), "name": info.get("PRETTY_NAME") or info.get("NAME") or "Linux",
            "family": family(info)}


def aur_helper():
    for h in ("yay", "paru"):
        if shutil.which(h):
            return h
    return None


# --- what's waiting ----------------------------------------------------------------

def system_updates(fam, run, cache_dir):
    """{"items": [{name, old, new}]} or {"error": ...}."""
    if fam == "arch":
        env = dict(os.environ, CHECKUPDATES_DB=os.path.join(cache_dir, "checkup-db"))
        if not shutil.which("checkupdates"):
            return {"error": "checkupdates not found.  Install pacman-contrib."}
        code, out, err = run(["checkupdates", "--nocolor"], 300, env)
        # 0 = updates, 2 = none, 1 = failed.
        if code not in (0, 2):
            return {"error": err.strip() or f"checkupdates exit {code}"}
        return {"items": parse.update_list(out)}
    if fam == "debian":
        # Reads the package lists apt already has.  Debian and Ubuntu refresh
        # them daily (apt-daily.timer), so no root is needed here.
        code, out, err = run(["apt", "list", "--upgradable"], 120, dict(os.environ, LANG="C"))
        if code != 0:
            return {"error": err.strip().splitlines()[-1] if err.strip() else f"apt exit {code}"}
        return {"items": parse.apt_upgradable(out)}
    if fam == "fedora":
        env = dict(os.environ, XDG_CACHE_HOME=cache_dir)
        code, out, err = run(["dnf", "-q", "check-update"], 300, env)
        # 100 = updates, 0 = none.
        if code not in (0, 100):
            return {"error": err.strip().splitlines()[-1] if err.strip() else f"dnf exit {code}"}
        return {"items": parse.dnf_check_update(out)}
    return {"error": "Not a distro Battle Bridge knows (Arch, Debian, Ubuntu or Fedora families)."}


def aur_updates(run, cache_dir):
    helper = aur_helper()
    if not helper:
        return None
    env = dict(os.environ, XDG_CACHE_HOME=cache_dir)
    code, out, err = run([helper, "-Qua", "--color", "never"], 300, env)
    # Both exit 1 when there's nothing to update.
    if code not in (0, 1):
        return {"error": err.strip() or f"{helper} exit {code}", "helper": helper}
    return {"items": parse.update_list(out), "helper": helper}


def changed_marker(fam):
    """A file whose time changes when packages are installed, so update
    checks can rerun after an update."""
    for p in {"arch": ["/var/log/pacman.log"],
              "debian": ["/var/log/dpkg.log"],
              "fedora": ["/usr/lib/sysimage/rpm/rpmdb.sqlite", "/var/lib/rpm/rpmdb.sqlite", "/var/lib/rpm"]
              }.get(fam, []):
        if os.path.exists(p):
            return p
    return None


# --- reboot needed? -----------------------------------------------------------------

def reboot(fam, run, booted, modules_dirs=("/usr/lib/modules", "/lib/modules"),
           pacman_log="/var/log/pacman.log", reboot_file="/run/reboot-required"):
    running = os.uname().release
    reasons = []
    if not any(os.path.isdir(os.path.join(d, running)) for d in modules_dirs):
        reasons.append("running kernel has been replaced")
    changed = []
    if fam == "arch":
        try:
            with open(pacman_log, errors="replace") as f:
                changed = parse.changed_since(f.read(), booted or 0, REBOOT_PACKAGES)
        except OSError:
            pass
        reasons += [f"{c['name']} updated since boot" for c in changed]
    elif fam == "debian":
        if os.path.exists(reboot_file):
            pkgs = []
            try:
                with open(reboot_file + ".pkgs") as f:
                    pkgs = sorted({ln.strip() for ln in f if ln.strip()})
            except OSError:
                pass
            reasons.append("the system says so" + (f" ({', '.join(pkgs[:4])})" if pkgs else ""))
    elif fam == "fedora":
        for cmd in (["dnf", "needs-restarting", "-r"], ["needs-restarting", "-r"]):
            code, out, _ = run(cmd, 120)
            if code in (0, 1) and out:
                if code == 1:
                    reasons.append("core packages updated since boot")
                break
    return {"running": running, "booted": booted, "needed": bool(reasons), "reasons": reasons,
            "changed": changed}
