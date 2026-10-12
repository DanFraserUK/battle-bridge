"""The job runner: compose projects from labels, the request gate, running
in a pty, input, Stop, history, drop-in jobs and the after-reboot checks."""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKES = ROOT / "tests" / "fakes"
sys.path.insert(0, str(ROOT / "runner"))
os.environ["PATH"] = str(FAKES / "bin") + os.pathsep + os.environ["PATH"]

from bbrunner import dropins, facts, jobs  # noqa: E402


def settings(conf="/no/such/dir", **kw):
    return facts.Settings(config_dir=conf, owner=os.getuid(), **kw)


def wait_done(m, rid, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if m.current and m.current.id == rid and m.current.finished:
            return m.current
        time.sleep(0.05)
    raise AssertionError("job didn't finish")


class TestProjects(unittest.TestCase):
    def test_found_from_labels(self):
        p = {x["name"]: x for x in jobs.projects()}
        self.assertEqual(sorted(p), ["battle-bridge", "dns", "downloads", "gone", "media", "myapp", "tools"])
        self.assertEqual(p["media"]["containers"], ["plex", "jellyfin"])
        self.assertTrue(p["myapp"]["built_here"])
        self.assertFalse(p["media"]["built_here"])
        self.assertTrue(p["dns"]["update_last"])                    # Pi-hole goes last
        self.assertTrue(p["gone"]["missing"])                       # its folder no longer exists
        self.assertTrue(p["tools"]["files"][0].endswith("docker-compose.yml"))
        self.assertIn("portainer", jobs.containers())               # not compose, still a container


class TestGate(unittest.TestCase):
    def setUp(self):
        self.s = settings()

    def test_refusals(self):
        for name, arg in [("rm-rf", None), ("restart", None), ("restart", "../../etc"), ("restart", "media; reboot"),
                          ("restart", "Media"), ("status", "media"), ("rebuild", "media"), ("restart", ""),
                          ("restart", "gone"), ("restart-container", "nosuch"), ("restart-container", "-f")]:
            steps, _, err = jobs.check_request(name, arg, self.s)
            self.assertIsNone(steps, (name, arg))
            self.assertTrue(err)

    def test_allowed(self):
        steps, label, err = jobs.check_request("restart", "media", self.s)
        self.assertIsNone(err)
        self.assertEqual(label, "Restart a project: media")
        argv = steps[0].argv
        self.assertEqual(argv[:4], ["docker", "compose", "-p", "media"])
        self.assertIn("--project-directory", argv)
        self.assertEqual(argv[-1], "restart")
        self.assertTrue(steps[0].cwd.endswith("compose/media"))
        steps, _, _ = jobs.check_request("rebuild", "myapp", self.s)
        self.assertIn("build", steps[0].argv)
        steps, _, _ = jobs.check_request("restart-container", "portainer", self.s)
        self.assertEqual(steps[0].argv, ["docker", "restart", "portainer"])

    def test_update_all_order(self):
        steps, _, _ = jobs.check_request("update-all", None, self.s)
        titles = [s.title for s in steps if s.title]
        self.assertTrue(titles[-1].startswith("dns"))               # DNS last
        self.assertIn("myapp", titles)                              # built here: a note, not a pull
        self.assertIn("gone", titles)                               # missing folder: a note
        pulls = [s.cwd for s in steps if s.argv and "pull" in s.argv]
        self.assertFalse(any(p.endswith("/myapp") or p.endswith("/gone") for p in pulls))
        self.assertTrue(all(s.keep_going for s in steps if s.argv))


@unittest.skipIf(dropins.tomllib is None, "drop-ins need Python 3.11")
class TestDropinJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "jobs.d"))
        Path(self.tmp, "jobs.d", "hello.toml").write_text(
            'label = "Say hello"\ngroup = "Mine"\ncommand = ["/bin/echo", "hello", "$(id)"]\n')
        Path(self.tmp, "jobs.d", "status.toml").write_text('label = "x"\ncommand = ["/bin/true"]\n')
        self.s = settings(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_listed_and_run_without_a_shell(self):
        listed, problems = jobs.public_jobs(self.s)
        mine = [j for j in listed if j["dropin"]]
        self.assertEqual([j["name"] for j in mine], ["hello"])
        self.assertIn("built-in job name", problems[0])
        self.assertIsNone(jobs.check_request("hello", "media", self.s)[0])    # drop-ins take no argument
        m = jobs.Manager(os.path.join(self.tmp, "logs"), settings=self.s)
        r = m.start("hello", None)
        run = wait_done(m, r["id"])
        self.assertEqual(run.exit, 0)
        self.assertIn(b"hello $(id)", bytes(run.buf))              # passed as text, never run by a shell


class TestManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.m = jobs.Manager(self.tmp, settings=settings(after_reboot_mounts=["/", "/proc"]))

    def tearDown(self):
        cur = self.m.current
        if cur and cur.finished is None:
            self.m.stop(cur.id)
            wait_done(self.m, cur.id, 15)
        shutil.rmtree(self.tmp)

    def test_run_and_history(self):
        r = self.m.start("restart", "downloads")
        self.assertTrue(r["ok"])
        run = wait_done(self.m, r["id"])
        self.assertEqual(run.exit, 0)
        self.assertIn(b"Restarting downloads", bytes(run.buf))
        log = Path(self.m.log_path(r["id"])).read_bytes()
        self.assertEqual(log, bytes(run.buf))
        again = jobs.Manager(self.tmp, settings=self.m.settings)
        self.assertEqual(again.history[-1]["exit"], 0)
        self.assertEqual(again.next_id, r["id"] + 1)
        chunks = list(again.stream(r["id"], 0))
        self.assertEqual(chunks[0][0], log)
        self.assertEqual(chunks[-1][1]["id"], r["id"])

    def test_one_at_a_time_and_input(self):
        r = self.m.start("rebuild", "myapp")
        self.assertIn("Busy", self.m.start("status", None)["error"])
        time.sleep(0.8)
        self.assertTrue(self.m.send_input(r["id"], "y\r")["ok"])
        run = wait_done(self.m, r["id"])
        self.assertEqual(run.exit, 0)
        self.assertIn(b"Built", bytes(run.buf))
        self.assertIn("error", self.m.send_input(r["id"], "x"))

    def test_failure_stops(self):
        r = self.m.start("rebuild", "myapp")
        time.sleep(0.8)
        self.m.send_input(r["id"], "n\r")
        run = wait_done(self.m, r["id"])
        self.assertEqual(run.exit, 1)
        self.assertNotIn(b" up -d", bytes(run.buf))

    def test_live_stream_follows(self):
        r = self.m.start("update-one", "downloads")
        got = b""
        for chunk, meta in self.m.stream(r["id"], 0):
            if chunk:
                got += chunk
            if meta:
                break
        self.assertIn(b"Pull complete", got)
        self.assertIn(b"Done.", got)

    def test_stop_button(self):
        r = self.m.start("logs-follow", "media")
        time.sleep(1.0)
        self.assertTrue(self.m.stop(r["id"])["ok"])
        run = wait_done(self.m, r["id"], 15)
        self.assertIn(b"Stopped.", bytes(run.buf))
        self.assertIn("error", self.m.stop(r["id"]))

    def test_new_job_ends_a_follow(self):
        self.m.start("logs-follow", "media")
        time.sleep(1.0)
        r2 = self.m.start("status", None)
        self.assertTrue(r2.get("ok"), r2)
        self.assertIsNotNone(self.m.history[-2]["finished"])
        wait_done(self.m, r2["id"])

    def test_post_checks(self):
        r = self.m.start("post-checks", None)
        run = wait_done(self.m, r["id"])
        res = {x["label"]: x for x in run.result}
        self.assertEqual(list(res), ["All services up", "Drives mounted", "GPU visible in containers"])
        self.assertTrue(res["All services up"]["ok"])               # oldthing exited, but has no restart policy
        self.assertTrue(res["Drives mounted"]["ok"], res["Drives mounted"])
        self.assertTrue(res["GPU visible in containers"]["ok"])
        self.assertIn("(plex)", res["GPU visible in containers"]["detail"])

    def test_drive_check_reads_each_mount(self):
        # findmnt takes at most two paths, so each mount is checked on its own.
        rc, res = jobs.post_checks(lambda t: None, settings(after_reboot_mounts=["/proc", "/no/such/mount"]))
        self.assertFalse(res[1]["ok"])
        self.assertEqual(res[1]["detail"], "missing: /no/such/mount")


if __name__ == "__main__":
    unittest.main()
