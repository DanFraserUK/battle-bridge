"""bb-runner: parsers, distro adapters, drives, drop-ins and the main loop."""

import json
import logging
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runner"))

from bbrunner import distro, dropins, facts, parse, registry  # noqa: E402
from bbrunner.main import Runner  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def fx(name):
    return (FIX / name).read_text()


class TestParse(unittest.TestCase):
    def test_root_sections(self):
        names = [(s, a) for s, a, _ in parse.root_facts(fx("root-facts.txt"))]
        self.assertEqual(names, [("smart", "/dev/sda"), ("smart", "/dev/nvme0"), ("btrfs", "/mnt/pool"),
                                 ("zfs", ""), ("certs", "")])

    def test_smart(self):
        ata = parse.smart("/dev/sda", fx("smart-ata.json"))
        self.assertEqual((ata["name"], ata["ok"], ata["temp"]), ("sda", True, 31))
        self.assertEqual(ata["notes"], ["3 pending sectors"])
        self.assertNotIn("SECRET", json.dumps(ata))              # no serial numbers
        nvme = parse.smart("/dev/nvme0", fx("smart-nvme.json"))
        self.assertEqual((nvme["wear"], nvme["notes"]), (3, []))
        usb = parse.smart("/dev/sdc", fx("smart-usb.json"))
        self.assertIsNone(usb["ok"])
        self.assertIn("USB bridge", usb["error"])
        self.assertIn("error", parse.smart("/dev/sdd", "not json"))

    def test_btrfs_and_zfs(self):
        sec = {s: t for s, _, t in parse.root_facts(fx("root-facts.txt"))}
        b = parse.btrfs_section("/mnt/pool", sec["btrfs"])
        self.assertEqual(len(b["devices"]), 2)
        self.assertEqual(b["devices"][1]["read_io_errs"], 2)
        self.assertTrue(b["scrub"]["never"])
        done = parse.btrfs_scrub_status(fx("scrub-finished.txt"))
        self.assertEqual(done["status"], "finished")
        self.assertTrue(done["ok"])
        z = parse.zpools(sec["zfs"])
        self.assertEqual([(p["name"], p["ok"]) for p in z], [("tank", True), ("backup", False)])

    def test_certs(self):
        sec = {s: t for s, _, t in parse.root_facts(fx("root-facts.txt"))}
        c = parse.certs(sec["certs"])
        self.assertEqual([x["domain"] for x in c], ["media.example.org", "cloud.example.org"])
        self.assertEqual(c[0]["expires"], 1795017026.0)

    def test_package_lists(self):
        self.assertEqual(parse.update_list(fx("checkupdates.txt"))[0],
                         {"name": "linux", "old": "7.2.4.arch1-2", "new": "7.2.5.arch1-1"})
        apt = parse.apt_upgradable(fx("apt-upgradable.txt"))
        self.assertEqual([p["name"] for p in apt], ["bash", "linux-image-generic"])
        self.assertEqual(apt[0]["old"], "5.2.21-2ubuntu4")
        dnf = parse.dnf_check_update(fx("dnf-check-update.txt"))
        self.assertEqual([p["name"] for p in dnf], ["kernel", "podman"])    # stops at Obsoleting

    def test_systemd(self):
        t = {x["unit"]: x for x in parse.timers(fx("timers.json"))}
        self.assertIsNone(t["btrfs-scrub@mnt-pool.timer"]["last"])
        blocks = parse.systemctl_show("Id=a.service\nResult=success\n\nId=b.service\nResult=exit-code\n")
        self.assertEqual([b["Result"] for b in blocks], ["success", "exit-code"])

    def test_changed_since_boot(self):
        c = parse.changed_since(fx("pacman.log"), 1791400000, distro.REBOOT_PACKAGES)
        self.assertEqual(sorted(x["name"] for x in c), ["linux-lts", "nvidia-utils"])
        self.assertEqual(parse.changed_since(fx("pacman.log"), 1800000000, distro.REBOOT_PACKAGES), [])

    def test_news(self):
        n = parse.arch_news(fx("arch-news.xml"))
        self.assertEqual(n[0]["title"], "Manual intervention for foo 2.0")
        bad = parse.arch_news('<rss><channel><item><title>x</title><link>javascript:alert(1)</link></item></channel></rss>')
        self.assertEqual(bad[0]["link"], "https://archlinux.org/news/")


class TestDistro(unittest.TestCase):
    def test_family(self):
        f = distro.family
        self.assertEqual(f({"ID": "endeavouros", "ID_LIKE": "arch"}), "arch")
        self.assertEqual(f({"ID": "ubuntu", "ID_LIKE": "debian"}), "debian")
        self.assertEqual(f({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}), "debian")
        self.assertEqual(f({"ID": "rocky", "ID_LIKE": "rhel centos fedora"}), "fedora")
        self.assertEqual(f({"ID": "nixos"}), "other")

    def test_reboot(self):
        with tempfile.TemporaryDirectory() as t:
            mods = os.path.join(t, "modules")
            os.makedirs(os.path.join(mods, os.uname().release))
            log = os.path.join(t, "pacman.log")
            Path(log).write_text(fx("pacman.log"))
            flag = os.path.join(t, "reboot-required")
            nothing = lambda *a, **k: (0, "", "")  # noqa: E731
            r = distro.reboot("arch", nothing, 1800000000, (mods,), log, flag)
            self.assertFalse(r["needed"])
            r = distro.reboot("arch", nothing, 1791400000, (mods,), log, flag)
            self.assertIn("nvidia-utils updated since boot", r["reasons"])
            self.assertFalse(distro.reboot("debian", nothing, 0, (mods,), log, flag)["needed"])
            Path(flag).write_text("*** System restart required ***\n")
            Path(flag + ".pkgs").write_text("linux-image-6.8.0-90-generic\n")
            r = distro.reboot("debian", nothing, 0, (mods,), log, flag)
            self.assertTrue(r["needed"])
            self.assertIn("linux-image", r["reasons"][0])
            os.rmdir(os.path.join(mods, os.uname().release))
            r = distro.reboot("other", nothing, 0, (mods,), log, flag)
            self.assertIn("running kernel has been replaced", r["reasons"])
            fed = lambda cmd, t=0: (1, "Reboot is required", "")  # noqa: E731
            self.assertTrue(distro.reboot("fedora", fed, 0, ("/",), log, flag)["needed"])

    def test_updates_use_the_right_tool(self):
        calls = []

        def run(cmd, timeout=0, env=None):
            calls.append(cmd[0])
            return {"apt": (0, fx("apt-upgradable.txt"), "WARNING: apt does not have a stable CLI"),
                    "dnf": (100, fx("dnf-check-update.txt"), "")}.get(cmd[0], (1, "", "boom"))
        self.assertEqual(len(distro.system_updates("debian", run, "/tmp")["items"]), 2)
        self.assertEqual(len(distro.system_updates("fedora", run, "/tmp")["items"]), 2)
        self.assertIn("error", distro.system_updates("other", run, "/tmp"))
        self.assertEqual(calls, ["apt", "dnf"])


class TestRegistry(unittest.TestCase):
    def test_refs(self):
        self.assertEqual(registry.split_ref("pihole/pihole:latest"), ("registry-1.docker.io", "pihole/pihole", "latest"))
        self.assertEqual(registry.split_ref("lscr.io/linuxserver/plex:latest"), ("ghcr.io", "linuxserver/plex", "latest"))
        self.assertEqual(registry.split_ref("nginx"), ("registry-1.docker.io", "library/nginx", "latest"))
        self.assertEqual(registry.split_ref("localhost:5000/x:1"), ("localhost:5000", "x", "1"))


class TestDrives(unittest.TestCase):
    def test_mounts(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "proc", "self"))
            Path(t, "proc", "self", "mountinfo").write_text(
                f"22 1 259:2 / / rw - ext4 /dev/nvme0n1p2 rw\n"
                f"23 22 259:1 / /boot rw - vfat /dev/nvme0n1p1 rw\n"
                f"24 22 8:17 / {t} rw - btrfs /dev/sdb1 rw\n"
                f"25 22 8:17 /@snap {t}/snapshots rw - btrfs /dev/sdb1 rw\n"
                f"26 22 0:50 / /var/lib/docker/overlay2/x rw - overlay overlay rw\n"
                f"27 22 0:51 / /mnt/my\\040media rw - ext4 /dev/sda1 rw\n")
            s = facts.Settings(proc=os.path.join(t, "proc"), sys=os.path.join(t, "sys"))
            items = facts.disks(s)["items"]
            mounts = [d["mount"] for d in items]
            self.assertEqual(mounts[0], "/")
            self.assertIn(t, mounts)                    # btrfs subvolume shown once, shortest path
            self.assertNotIn(f"{t}/snapshots", mounts)
            self.assertNotIn("/boot", mounts)
            self.assertIn("/mnt/my media", mounts)      # escaped spaces undone
            self.assertGreater(items[0]["total"], 0)
            s.mounts = ["/", "/not/mounted"]
            pinned = facts.disks(s)["items"]
            self.assertEqual([d["mounted"] for d in pinned], [True, False])

    def test_disks_of(self):
        with tempfile.TemporaryDirectory() as t:
            blk = Path(t, "class", "block")
            Path(t, "devices", "sda", "sda1").mkdir(parents=True)
            Path(t, "devices", "sda", "sda1", "partition").write_text("1")
            Path(t, "devices", "dm-0", "slaves").mkdir(parents=True)
            blk.mkdir(parents=True)
            os.symlink(Path(t, "devices", "sda"), blk / "sda")
            os.symlink(Path(t, "devices", "sda", "sda1"), blk / "sda1")
            os.symlink(Path(t, "devices", "dm-0"), blk / "dm-0")
            os.symlink(Path(t, "devices", "sda", "sda1"), Path(t, "devices", "dm-0", "slaves", "sda1"))
            dev = Path(t, "dev")
            dev.mkdir()
            Path(dev, "sda1").touch()
            Path(dev, "dm-0").touch()
            orig = facts.os.path.realpath
            self.assertEqual(facts.disks_of(str(dev / "sda1"), t), [])  # not under /dev/: refused
            try:
                facts.os.path.realpath = lambda p: orig(p.replace("/dev/", str(dev) + "/")) if p.startswith("/dev/") else orig(p)
                self.assertEqual(facts.disks_of("/dev/sda1", t), ["sda"])
                self.assertEqual(facts.disks_of("/dev/dm-0", t), ["sda"])
            finally:
                facts.os.path.realpath = orig

    def test_fstab(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# comment\nUUID=1 / ext4 defaults 0 1\nUUID=2 /boot/efi vfat defaults 0 2\n"
                    "UUID=3 none swap sw 0 0\nUUID=4 /mnt/media ext4 defaults,nofail 0 2\n"
                    "UUID=5 /mnt/usb ext4 noauto 0 0\ntmpfs /tmp tmpfs defaults 0 0\n")
        try:
            self.assertEqual(facts.fstab_mounts(f.name), ["/", "/boot/efi", "/mnt/media"])
        finally:
            os.unlink(f.name)


@unittest.skipIf(dropins.tomllib is None, "drop-ins need Python 3.11")
class TestDropins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        for k in ("jobs", "checks", "panels"):
            os.makedirs(os.path.join(self.dir, f"{k}.d"))
        self.me = os.getuid()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, kind, name, text, mode=0o644):
        p = os.path.join(self.dir, f"{kind}.d", name)
        Path(p).write_text(text)
        os.chmod(p, mode)
        return p

    def test_examples_are_valid(self):
        for kind in ("jobs", "checks", "panels"):
            items, problems = dropins.load(kind, str(ROOT / "examples"), owner=os.stat(ROOT / "examples").st_uid)
            self.assertEqual(problems, [], kind)
            self.assertTrue(items, kind)

    def test_jobs(self):
        self.write("jobs", "ok.toml", 'label = "Fine"\ncommand = ["/bin/echo", "hi"]\nconfirm = "Sure?"\n')
        self.write("jobs", "Bad Name.toml", 'label = "x"\ncommand = ["/bin/true"]\n')
        self.write("jobs", "shell.toml", 'label = "x"\ncommand = "rm -rf /"\n')
        self.write("jobs", "relative.toml", 'label = "x"\ncommand = ["echo", "hi"]\n')
        self.write("jobs", "broken.toml", 'label = \n')
        self.write("jobs", "writable.toml", 'label = "x"\ncommand = ["/bin/true"]\n', 0o666)
        items, problems = dropins.load("jobs", self.dir, self.me)
        self.assertEqual([i["name"] for i in items], ["ok"])
        self.assertTrue(items[0]["changes"])                       # confirm implies it changes things
        self.assertEqual(len(problems), 5)
        text = " ".join(problems)
        for bit in ("lower case", "should be a list", "full path", "not valid TOML", "writable only by root"):
            self.assertIn(bit, text)

    def test_folder_and_owner_checked(self):
        self.write("jobs", "ok.toml", 'label = "Fine"\ncommand = ["/bin/true"]\n')
        self.assertEqual(dropins.load("jobs", self.dir, self.me + 1)[0], [])   # someone else's file
        os.chmod(os.path.join(self.dir, "jobs.d"), 0o777)
        items, problems = dropins.load("jobs", self.dir, self.me)
        self.assertEqual(items, [])
        self.assertIn("Ignored", problems[0])
        self.assertEqual(dropins.load("jobs", "/no/such/dir", 0), ([], []))

    def test_checks(self):
        run = facts.run
        cases = [('expect = ""', ["/bin/true"], True), ('expect = ""', ["/bin/echo", "x"], False),
                 ('expect = "not empty"', ["/bin/echo", "x"], True), ('expect = "exit 1"', ["/bin/false"], True),
                 ('expect = "hello"', ["/bin/echo", "hello"], True), ("", ["/bin/false"], False)]
        for i, (exp, cmd, want) in enumerate(cases):
            self.write("checks", f"c{i}.toml", f'label = "c"\ncommand = {json.dumps(cmd)}\n{exp}\nevery = "15m"\n')
        items, problems = dropins.load("checks", self.dir, self.me)
        self.assertEqual(problems, [])
        self.assertEqual(items[0]["every"], 900)
        for item, (_, _, want) in zip(items, cases):
            self.assertEqual(dropins.run_check(item, run)["ok"], want, item)
        self.write("checks", "bad.toml", 'label = "c"\ncommand = ["/bin/true"]\nevery = "often"\n')
        self.assertIn("every", dropins.load("checks", self.dir, self.me)[1][0])

    def test_panels(self):
        src = os.path.join(self.dir, "data.json")
        Path(src).write_text(json.dumps({"today": {"kwh": 14.2}, "items": [{"name": "a"}], "on": True, "secret": "x"}))
        self.write("panels", "p.toml", f'title = "Solar"\nsource = "{src}"\n'
                   '[[show]]\nlabel = "made"\nkey = "today.kwh"\nunit = "kWh"\n'
                   '[[show]]\nlabel = "first"\nkey = "items[0].name"\n'
                   '[[show]]\nlabel = "on"\nkey = "on"\n'
                   '[[show]]\nlabel = "gone"\nkey = "nope.x"\n')
        items, problems = dropins.load("panels", self.dir, self.me)
        self.assertEqual(problems, [])
        vals = dropins.read_panel(items[0])
        self.assertEqual([v["value"] for v in vals], [14.2, "a", "yes", None])
        self.assertNotIn("secret", json.dumps(vals))               # only the listed figures leave
        self.write("panels", "bad.toml", 'title = "x"\nsource = "ftp://x"\n[[show]]\nlabel = "a"\nkey = "b"\n')
        self.assertIn("source", dropins.load("panels", self.dir, self.me)[1][0])

    def test_check_schedule(self):
        self.write("checks", "c.toml", 'label = "c"\ncommand = ["/bin/date", "+%N"]\nevery = "1h"\n')
        s = facts.Settings(config_dir=self.dir, owner=self.me)
        first = facts.checks(s, None)
        again = facts.checks(s, first)
        self.assertEqual(first["items"][0]["output"], again["items"][0]["output"])   # not rerun within the hour
        first["items"][0]["ran"] -= 7200
        self.assertNotEqual(facts.checks(s, first)["items"][0]["output"], first["items"][0]["output"])


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "pacman.log").write_text(fx("pacman.log"))
        (t / "os-release").write_text('ID=arch\nPRETTY_NAME="Arch Linux"\n')
        self.s = facts.Settings(root_facts=["cat", str(FIX / "root-facts.txt")], pacman_log=str(t / "pacman.log"),
                                os_release=str(t / "os-release"), config_dir=str(t / "etc"), owner=os.getuid())

    def tearDown(self):
        self.tmp.cleanup()

    def test_root(self):
        r = facts.root(self.s)
        self.assertEqual([d["name"] for d in r["smart"]], ["sda", "nvme0"])
        self.assertEqual(r["btrfs"][0]["mount"], "/mnt/pool")
        self.assertEqual(len(r["zfs"]), 2)
        self.assertEqual(len(r["certs"]), 2)
        self.s.root_facts = ["/no/such/root-facts"]
        self.assertIn("error", facts.root(self.s))

    def test_writes_atomically_and_restarts(self):
        r = Runner(self.tmp.name, self.s)
        self.assertEqual(r.family, "arch")
        r.gather("disks", force=True)
        r.gather("reboot", force=True)
        r.publish()
        data = json.loads((Path(self.tmp.name) / "facts.json").read_text())
        self.assertIn("disks", data)
        self.assertEqual(data["os"]["family"], "arch")
        self.assertFalse((Path(self.tmp.name) / "facts.json.tmp").exists())
        self.assertFalse(r.due("disks", 60))
        # A restart keeps results to show, but redoes the quick ones straight away.
        r.facts["updates"] = {"checked": time.time(), "items": []}
        r.publish()
        r2 = Runner(self.tmp.name, self.s)
        self.assertEqual(r2.facts["disks"]["checked"], 0)
        self.assertTrue(r2.due("disks", 60))
        self.assertFalse(r2.due("updates", 86400))

    def test_news_only_on_arch(self):
        r = Runner(self.tmp.name, self.s)
        self.assertTrue(r.wanted("news"))
        Path(self.s.os_release).write_text("ID=debian\n")
        self.assertFalse(Runner(self.tmp.name, self.s).wanted("news"))

    def test_updates_rerun_after_packages_change(self):
        r = Runner(self.tmp.name, self.s)
        now = time.time()
        r.facts["updates"] = {"checked": now - 600, "items": []}
        marker = distro.changed_marker
        distro.changed_marker = lambda fam: self.s.pacman_log
        try:
            os.utime(self.s.pacman_log, (now - 300, now - 300))
            self.assertTrue(r.due("updates", 86400))
            os.utime(self.s.pacman_log, (now - 900, now - 900))
            self.assertFalse(r.due("updates", 86400))
        finally:
            distro.changed_marker = marker

    def test_broken_gatherer_is_reported(self):
        r = Runner(self.tmp.name, self.s)
        r._gather = lambda name: 1 / 0
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        r.gather("disks", force=True)
        self.assertIn("ZeroDivisionError", r.facts["disks"]["error"])


class TestRootFactsScript(unittest.TestCase):
    def test_takes_no_arguments(self):
        import subprocess
        r = subprocess.run(["bash", str(ROOT / "runner" / "root-facts"), "x"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run(["/bin/bash", str(ROOT / "runner" / "root-facts")], capture_output=True, text=True,
                           env={"PATH": "/nonexistent"}, timeout=60)
        self.assertTrue(r.stdout.rstrip().endswith("=== end"))
        self.assertTrue(re.search(r"=== note\nsmartctl isn't installed", r.stdout) or "=== smart" in r.stdout)


if __name__ == "__main__":
    unittest.main()
