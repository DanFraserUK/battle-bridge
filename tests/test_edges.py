"""Edge cases found in review: races, full disks, huge logs, odd input.
Each test is named after what used to go wrong."""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKES = ROOT / "tests" / "fakes"
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "runner"))
os.environ["PATH"] = str(FAKES / "bin") + os.pathsep + os.environ["PATH"]

from battle_bridge import auth as auth_mod, pc, server, services, store as store_mod  # noqa: E402
from battle_bridge.db import DB  # noqa: E402
from bbrunner import dropins, facts, jobs, sock  # noqa: E402
from bbrunner.main import Runner  # noqa: E402
from test_core import make_cfg  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_cfg()
        self.db = DB(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# --- the web app ------------------------------------------------------------------

class TestWebEdges(Temp):
    def test_concurrent_guesses_still_hit_the_lockout(self):
        a = auth_mod.Auth(self.db, self.cfg)
        self.db.set_setting("password_hash", auth_mod.hash_password("right password", iterations=20000))
        checked = []
        real = auth_mod.check_password

        def counting(pw, stored):
            checked.append(pw)
            return real(pw, stored)
        auth_mod.check_password = counting
        try:
            threads = [threading.Thread(target=a.login, args=(f"guess {i}", "10.0.0.9")) for i in range(30)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            auth_mod.check_password = real
        self.assertEqual(len(checked), self.cfg.lockout_failures)
        self.assertGreater(a.locked_for(), 0)

    def test_two_saves_from_one_revision_cant_both_win(self):
        st = store_mod.Store(self.db)
        results = []

        def save(addr):
            try:
                st.save({"server_address": addr}, 0)
                results.append("ok")
            except store_mod.Stale:
                results.append("stale")
        threads = [threading.Thread(target=save, args=(f"192.168.1.{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(st.settings()["rev"], 1)

    def test_odd_digits_in_the_setup_code(self):
        a = auth_mod.Auth(self.db, self.cfg)
        a.setup_code()
        ok, err = a.check_setup_code("１２３４-５６７８", "10.0.0.9")     # full-width digits
        self.assertFalse(ok)
        self.assertIn("isn't the setup code", err)

    def test_nan_never_reaches_the_browser(self):
        data = {"a": float("nan"), "b": [1.5, float("inf")], "c": {"d": -float("inf")}}
        text = json.dumps(server._finite(data), allow_nan=False)
        self.assertEqual(json.loads(text), {"a": None, "b": [1.5, None], "c": {"d": None}})
        self.assertIsNone(dropins.pick({"x": float("nan")}, "x"))
        self.assertEqual(dropins.pick({"x": 2.5}, "x"), 2.5)

    def test_hosts_with_scope_ids_are_refused(self):
        self.assertIsNone(store_mod.split_host("[fe80::1%$(id)]:8080"))
        self.assertFalse(store_mod.Store(self.db).host_allowed("[fe80::1%eth0]"))

    def test_installer_never_trusts_the_address(self):
        conf = {"terminal": "konsole", "ssh_target": "me@192.168.1.5", "ssh_port": 22, "folders": ["~"]}
        text = pc.installer(conf, '1.2.3.4"; touch /tmp/x; "')
        self.assertNotIn("touch", text)
        self.assertIn("192.168.1.5", pc.installer(conf, "192.168.1.5"))

    def test_server_placeholder_only_in_tile_links(self):
        st = store_mod.Store(self.db)
        st.save({"links": {"app": "https://{server}:8443/"}}, 0)
        with self.assertRaises(store_mod.Invalid):
            st.save({"services": {"sonarr": {"url": "http://{server}:8989"}}}, 1)
        with self.assertRaises(store_mod.Invalid):
            st.save({"unifi": {"url": "http://192.168.1.1", "fingerprint": ""}}, 1)    # never cleartext

    def test_check_counts_label_links_with_server(self):
        from battle_bridge import discovery
        c = {"id": "x", "name": "x", "image": "a/b", "state": "running", "ip": None, "ports": [],
             "host_network": False, "labels": {"battlebridge.url": "https://{server}:8443/"}}
        self.assertEqual(discovery.tile_link(c, "192.0.2.1"), "https://192.0.2.1:8443/")


class TestServiceRedirects(unittest.TestCase):
    PORT = 23771

    @classmethod
    def setUpClass(cls):
        cls.p = subprocess.Popen([sys.executable, str(FAKES / "fake_services.py"), str(cls.PORT)])
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.PORT}/nothing", timeout=1)
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.p.terminate()
        cls.p.wait()

    def test_keys_dont_follow_redirects(self):
        with self.assertRaises(services.ServiceError) as e:
            services.get_json(f"http://127.0.0.1:{self.PORT}/redirect/api", {"X-Api-Key": "secret"})
        self.assertIn("redirected", str(e.exception))
        with self.assertRaises(services.ServiceError) as e:
            services.unifi(f"http://127.0.0.1:{self.PORT}/redirect", "secret")
        self.assertIn("302", str(e.exception))

    def test_unifi_needs_a_pin_for_https(self):
        with self.assertRaises(services.NotConfigured):
            services.unifi("https://192.0.2.1", "secret", "")


# --- the runner --------------------------------------------------------------------

def settings(**kw):
    return facts.Settings(config_dir="/no/such/dir", owner=os.getuid(), **kw)


def wait_done(m, rid, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if m.current and m.current.id == rid and m.current.finished:
            return m.current
        time.sleep(0.05)
    raise AssertionError("job didn't finish")


class TestRunnerEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_full_disk_at_start_doesnt_leave_it_busy(self):
        m = jobs.Manager(self.tmp, settings=settings())
        real = m._save_history

        def full():
            raise OSError(28, "No space left on device")
        m._save_history = full
        r = m.start("status", None)
        self.assertIn("No space left", r["error"])
        m._save_history = real
        r = m.start("status", None)
        self.assertTrue(r.get("ok"), r)
        wait_done(m, r["id"])

    def test_a_full_disk_mid_job_still_ends_the_job(self):
        m = jobs.Manager(self.tmp, settings=settings())
        real_open = open

        class Full:
            def write(self, data):
                raise OSError(28, "No space left on device")

            def flush(self):
                pass

            def close(self):
                pass
        import builtins
        builtins.open = lambda p, *a, **k: Full() if str(p).endswith(".log") and "w" in (a[0] if a else k.get("mode", "")) else real_open(p, *a, **k)
        try:
            r = m.start("status", None)
            run = wait_done(m, r["id"])
        finally:
            builtins.open = real_open
        self.assertIn(b"couldn't be saved", bytes(run.buf))
        self.assertTrue(m.start("status", None).get("ok"))           # not stuck busy
        wait_done(m, m.current.id)

    def test_any_surprise_ends_the_job(self):
        m = jobs.Manager(self.tmp, settings=settings())
        m._pty_step = lambda run, step, w: 1 / 0
        r = m.start("status", None)
        run = wait_done(m, r["id"])
        self.assertNotEqual(run.exit, 0)
        self.assertIn(b"ZeroDivisionError", bytes(run.buf))
        self.assertTrue(m.start("prune", None).get("ok"))

    def test_runaway_output_is_stopped(self):
        old = jobs.MAX_OUTPUT
        jobs.MAX_OUTPUT = 700     # just past the command line and a few log lines
        try:
            m = jobs.Manager(self.tmp, settings=settings())
            r = m.start("logs-follow", "media")
            run = wait_done(m, r["id"], 30)
        finally:
            jobs.MAX_OUTPUT = old
        self.assertIn(b"more than", bytes(run.buf))

    def test_runs_cut_short_by_a_restart_say_so(self):
        Path(self.tmp, "history.json").write_text(json.dumps([
            {"id": 1, "name": "status", "label": "x", "started": 100, "finished": None, "exit": None}]))
        m = jobs.Manager(self.tmp, settings=settings())
        self.assertTrue(m.history[0]["interrupted"])
        self.assertIsNotNone(m.history[0]["finished"])
        self.assertTrue(m.start("status", None).get("ok"))
        wait_done(m, m.current.id)

    def test_huge_logs_stream_in_readable_pieces(self):
        m = jobs.Manager(self.tmp, settings=settings())
        Path(m.log_path(5)).write_bytes(b"x" * (1024 * 1024 + 17))
        m.history.append({"id": 5, "name": "status", "label": "x", "started": 1, "finished": 2, "exit": 0})
        sent = []
        sock.SocketServer("/unused", m).dispatch({"op": "stream", "id": 5, "offset": 0}, sent.append)
        pieces = [x for x in sent if "data" in x]
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(x["data"]) < 400_000 for x in pieces))
        self.assertEqual(sum(len(base64.b64decode(x["data"])) for x in pieces), 1024 * 1024 + 17)
        self.assertEqual(pieces[-1]["offset"], 1024 * 1024 + 17)
        self.assertIn("end", sent[-1])

    def test_images_with_their_own_compose_labels_are_ignored(self):
        p = {x["name"]: x for x in jobs.projects()}
        self.assertNotIn("sneaky", p["media"]["containers"])
        self.assertFalse(p["media"]["conflict"])
        self.assertNotIn("/srv/media/data/x.yml", p["media"]["files"])

    def test_containers_that_disagree_make_a_conflict(self):
        rows = [["a", "web", "/srv/web", "/srv/web/compose.yml", "nginx:1", "", "running"],
                ["b", "web", "/elsewhere", "/elsewhere/compose.yml", "nginx:1", "", "running"]]
        p = jobs.projects(rows)[0]
        self.assertTrue(p["conflict"] and p["missing"])
        self.assertIsNone(jobs.check_request("restart", "web", settings())[0])

    def test_relative_compose_files_arent_repeated(self):
        rows = [[n, "web", "/srv/web", "docker-compose.yml", "nginx:1", "", "running"] for n in "abc"]
        self.assertEqual(jobs.projects(rows)[0]["files"], ["/srv/web/docker-compose.yml"])

    def test_a_hung_network_share_doesnt_hang_the_runner(self):
        real = os.statvfs
        gate = threading.Event()

        def slow(path):
            if path == "/mnt/nfs":
                gate.wait(10)
            return real("/")
        os.statvfs = slow
        try:
            t0 = time.time()
            with self.assertRaises(OSError) as e:
                facts.statvfs("/mnt/nfs", timeout=0.3)
            self.assertLess(time.time() - t0, 2)
            self.assertIn("isn't answering", str(e.exception))
            with self.assertRaises(OSError):                       # still stuck: not asked again
                facts.statvfs("/mnt/nfs", timeout=0.3)
            gate.set()
            time.sleep(0.2)
            self.assertTrue(facts.statvfs("/mnt/nfs", timeout=1))  # back: asked again
        finally:
            os.statvfs = real

    def test_failed_daily_checks_are_retried_sooner(self):
        Path(self.tmp, "os-release").write_text("ID=debian\n")
        r = Runner(self.tmp, facts.Settings(os_release=str(Path(self.tmp, "os-release"))))
        now = time.time()
        r.facts["updates"] = {"checked": now - 2000, "error": "no network yet"}
        marker, booted = facts.distro.changed_marker, facts.boot_time
        facts.distro.changed_marker = lambda fam: None
        facts.boot_time = lambda s: now - 86400        # a machine that's been up a while
        try:
            self.assertTrue(r.due("updates", 86400))
            r.facts["updates"] = {"checked": now - 2000, "items": []}
            self.assertFalse(r.due("updates", 86400))
        finally:
            facts.distro.changed_marker, facts.boot_time = marker, booted


if __name__ == "__main__":
    unittest.main()
