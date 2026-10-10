"""The runner's main loop: gather facts on their schedules, write facts.json.

facts.json is replaced atomically (write, then rename), so the container
never reads half a file.  Slow results (daily checks) are kept in
state.json so a restart doesn't repeat them.  Everything quicker is redone
as soon as the runner starts: a new version may count differently.
"""

import json
import logging
import os
import signal
import sys
import threading
import time

from . import VERSION, distro, facts, jobs
from .sock import SocketServer

log = logging.getLogger("bb-runner")

STATE_DIR = os.environ.get("BB_STATE_DIR", "/var/lib/battle-bridge")

# name: seconds between runs
SCHEDULE = {
    "disks": 60,
    "reboot": 60,
    "timers": 60,
    "smartd": 60,
    "pc_jobs": 60,
    "checks": 60,
    "panels": 60,
    "root": 3600,
    "extip": 3600,
    "updates": 86400,
    "image_updates": 86400,
    "news": 86400,
}
DAILY = ("updates", "image_updates", "news")


class Runner:
    def __init__(self, state_dir=STATE_DIR, settings=None):
        self.dir = state_dir
        self.settings = settings or facts.Settings()
        self.config_problem = self.settings.load_toml()
        self.cache = os.path.join(state_dir, "cache")
        os.makedirs(self.cache, exist_ok=True)
        self.os = distro.describe(self.settings.os_release)
        self.family = self.os["family"]
        self.facts = self._load("state.json")
        self.facts.pop("runner", None)
        for name, every in SCHEDULE.items():
            if every < 86400 and isinstance(self.facts.get(name), dict):
                self.facts[name]["checked"] = 0
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.stop = threading.Event()
        self._last_sig_check = 0

    def _load(self, name):
        try:
            with open(os.path.join(self.dir, name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, name, data):
        path = os.path.join(self.dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)

    def wanted(self, name):
        return name != "news" or self.family == "arch"

    def due(self, name, every):
        last = (self.facts.get(name) or {}).get("checked", 0)
        if time.time() - last >= every:
            return True
        return self.stale(name, last)

    def stale(self, name, last):
        """Update checks also rerun after a boot, a package run or an image
        change, so the box never shows figures from before the last update."""
        if name not in ("updates", "image_updates"):
            return False
        now = time.time()
        try:
            booted = facts.boot_time(self.settings) or 0
        except OSError:
            booted = 0
        if last < booted and now - booted > 60:      # a minute after boot, to let things settle
            return True
        if name == "updates":
            changed = facts.changed_since_mtime(distro.changed_marker(self.family))
            return bool(changed and changed > last and now - changed > 60)
        sig = (self.facts.get("image_updates") or {}).get("signature")
        if not sig or now - self._last_sig_check < 300:
            return False
        self._last_sig_check = now
        return facts.image_signature() not in (None, sig)

    def _gather(self, name):
        s = self.settings
        if name == "disks":
            return facts.disks(s)
        if name == "reboot":
            return facts.reboot(s, self.family)
        if name == "timers":
            return facts.timers(s)
        if name == "smartd":
            return facts.smartd(s)
        if name == "pc_jobs":
            return facts.pc_jobs(s)
        if name == "checks":
            return facts.checks(s, self.facts.get("checks"))
        if name == "panels":
            return facts.panels(s, self.facts.get("panels"))
        if name == "root":
            return facts.root(s)
        if name == "extip":
            return facts.external_ip()
        if name == "updates":
            return facts.updates(self.family, self.cache)
        if name == "image_updates":
            return facts.image_updates()
        if name == "news":
            return facts.arch_news(s)
        raise KeyError(name)

    def gather(self, name, force=False):
        if not self.wanted(name):
            return False
        every = SCHEDULE[name]
        if not force and not self.due(name, every):
            return False
        started = time.time()
        try:
            data = self._gather(name)
        except Exception as e:  # one broken gatherer must not stop the rest
            log.exception("%s failed", name)
            data = {"error": f"{type(e).__name__}: {e}"[:300]}
        data = facts.stamp(data)
        data["took"] = round(time.time() - started, 2)
        with self.lock:
            self.facts[name] = data
        if data.get("error"):
            log.warning("%s: %s", name, data["error"])
        return True

    def publish(self):
        with self.lock:
            out = dict(self.facts)
        out["os"] = self.os
        out["runner"] = {"version": VERSION, "written": time.time(), "pid": os.getpid(),
                         "config_problem": self.config_problem}
        with self.write_lock:
            self._write("facts.json", out)
            self._write("state.json", out)

    def loop(self, names, pause):
        while not self.stop.is_set():
            for name in names:
                if self.stop.is_set():
                    break
                if self.gather(name):
                    self.publish()
            self.stop.wait(pause)

    def refresh(self, write):
        """The 'Check for updates now' job: rerun the daily checks."""
        bad = 0
        for name in DAILY:
            if not self.wanted(name):
                continue
            write(f"Checking {name.replace('_', ' ')}... ")
            self.gather(name, force=True)
            f = self.facts.get(name) or {}
            err = f.get("error") or (f.get("aur") or {}).get("error")
            write(f"\x1b[31m{err}\x1b[0m\r\n" if err else "\x1b[32mdone\x1b[0m\r\n")
            bad += bool(err)
        self.publish()
        return 1 if bad else 0

    def start(self):
        """Quick facts on one thread, slow ones (hourly and daily) on another,
        so a long update check never holds up the disk figures."""
        quick = [n for n, every in SCHEDULE.items() if every < 3600]
        slow = [n for n, every in SCHEDULE.items() if every >= 3600]
        threads = [threading.Thread(target=self.loop, args=(quick, 15), daemon=True),
                   threading.Thread(target=self.loop, args=(slow, 60), daemon=True)]
        for t in threads:
            t.start()
        return threads


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    r = Runner()
    signal.signal(signal.SIGTERM, lambda *_: r.stop.set())
    log.info("bb-runner %s on %s (%s family), writing to %s", VERSION, r.os["name"], r.family, r.dir)
    if r.config_problem:
        log.warning("%s", r.config_problem)
    r.start()
    manager = jobs.Manager(refresh=r.refresh, settings=r.settings)
    SocketServer(os.path.join(r.dir, "runner.sock"), manager).start()
    while not r.stop.wait(60):
        r.publish()   # keeps 'written' fresh, so the page can tell the runner is alive
    return 0
