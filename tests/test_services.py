"""The API clients against tests/fakes/fake_services.py."""

import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from battle_bridge import services  # noqa: E402

PORT = 23761
B = f"http://127.0.0.1:{PORT}"


class TestServices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = subprocess.Popen([sys.executable, str(ROOT / "tests/fakes/fake_services.py"), str(PORT)])
        for _ in range(50):
            try:
                urllib.request.urlopen(B + "/nothing", timeout=1)
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.p.terminate()
        cls.p.wait()

    def test_plex(self):
        d = services.plex(B + "/plex", "test-key")["sessions"]
        self.assertEqual(d[0]["title"], "The Expanse S03E04")
        self.assertTrue(d[0]["transcode"] and d[0]["hw"])
        self.assertEqual(d[0]["progress"], 40)
        self.assertFalse(d[1]["transcode"])
        with self.assertRaises(services.ServiceError) as e:
            services.plex(B + "/plex", "wrong")
        self.assertIn("refused the key", str(e.exception))
        self.assertNotIn("wrong", str(e.exception))
        with self.assertRaises(services.NotConfigured):
            services.plex(B + "/plex", "")

    def test_jellyfin(self):
        d = services.jellyfin(B + "/jellyfin", "test-key")["sessions"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["user"], "Kids")

    def test_arr(self):
        s = services.arr(B + "/sonarr", "test-key", "sonarr")
        self.assertEqual(s["queue_total"], 2)
        self.assertEqual(s["queue"][0]["progress"], 75)
        self.assertTrue(s["queue"][1]["warn"])
        self.assertEqual([u["title"] for u in s["upcoming"]], ["Slow Horses S04E01"])
        r = services.arr(B + "/radarr", "test-key", "radarr")
        self.assertEqual(r["queue"][0]["title"], "Dune: Part Two")
        p = services.arr(B + "/prowlarr", "test-key", "prowlarr")
        self.assertEqual(len(p["health"]), 1)

    def test_transmission_session_dance(self):
        t = services.Transmission()
        d = t.fetch(B + "/transmission", "user", "test-key")
        self.assertEqual(d["active"], 3)
        self.assertEqual(len(d["downloading"]), 2)
        self.assertEqual(t.sid, "transmission-sid")
        with self.assertRaises(services.ServiceError):
            services.Transmission().fetch(B + "/transmission", "user", "nope")

    def test_seerr_pihole(self):
        self.assertEqual(services.seerr(B + "/seerr", "k")["pending"], 2)
        ph = services.PiHole()
        self.assertEqual(ph.fetch(B + "/pihole", "test-key")["percent"], 18.9)
        sid = ph.sid
        ph.fetch(B + "/pihole", "test-key")
        self.assertEqual(ph.sid, sid)   # session reused, no second login
        with self.assertRaises(services.ServiceError):
            services.PiHole().fetch(B + "/pihole", "wrong")

    def test_unifi(self):
        u = services.unifi(B + "/unifi", "test-key")
        self.assertEqual(u["clients"], 31)
        self.assertEqual(u["gateway"], "Gateway")
        self.assertEqual(u["wan_rx"], 38000000)
        self.assertEqual(u["latency"], 9)

    def test_transmission_without_login(self):
        # No username or password set: no Authorization header is sent, and
        # the fake refuses that, which shows the header really was left out.
        with self.assertRaises(services.ServiceError) as e:
            services.Transmission().fetch(B + "/transmission", "", "")
        self.assertIn("refused", str(e.exception))

    def test_unifi_pin(self):
        with self.assertRaises(services.ServiceError):
            services.fingerprint("https://127.0.0.1:1/")

    def test_no_answer(self):
        with self.assertRaises(services.ServiceError) as e:
            services.plex("http://127.0.0.1:1", "k")
        self.assertIn("no answer", str(e.exception))

    def test_ddns(self):
        log = ("✅ The A records of a.example.org are already up to date\n"
               "😡 Failed to update b.example.org: invalid token\n🤷 No valid IPv6\n")
        d = services.ddns_health(log)
        self.assertEqual(d["up_to_date"], ["a.example.org"])
        self.assertEqual(len(d["problems"]), 1)
        frame = b"\x01\x00\x00\x00" + (5).to_bytes(4, "big") + b"hello"
        self.assertEqual(services.demux(frame), "hello")
        self.assertEqual(services.demux(b"plain tty text"), "plain tty text")


if __name__ == "__main__":
    unittest.main()
