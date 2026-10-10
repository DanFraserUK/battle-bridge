"""The web app: settings and access rules, login, discovery, collectors, and
the server end to end.  Run all tests with:
    python3 -m unittest discover -s tests
"""

import http.client
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from battle_bridge import VERSION, auth as auth_mod, config, discovery, store as store_mod  # noqa: E402
from battle_bridge.collectors import gpu  # noqa: E402
from battle_bridge.collectors.docker import DockerCollector  # noqa: E402
from battle_bridge.collectors.host import HostCollector  # noqa: E402
from battle_bridge.db import DB  # noqa: E402
from battle_bridge.server import App, make_server  # noqa: E402

FAKES = ROOT / "tests" / "fakes"


def make_cfg(**over):
    env = {"BB_LISTEN_HOST": "127.0.0.1", "BB_LISTEN_PORT": "0", "BB_HOST_PROC": "/proc",
           "BB_HOST_SYS": str(FAKES / "sys"), "BB_NVIDIA_SMI": str(FAKES / "nvidia-smi"),
           "BB_HOST_GATEWAY": "127.0.0.1"}
    env.update(over)
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return config.load()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg()
        self.db = DB(os.path.join(self.tmp.name, "t.db"))
        self.store = store_mod.Store(self.db)
        self.auth = auth_mod.Auth(self.db, self.cfg)

    def tearDown(self):
        self.tmp.cleanup()


class TestStore(Temp):
    def test_default_networks(self):
        s = self.store
        for ip in ("192.168.1.10", "10.1.2.3", "172.20.0.1", "127.0.0.1", "::ffff:192.168.1.10", "fd00::5"):
            self.assertTrue(s.client_allowed(ip), ip)
        for ip in ("8.8.8.8", "2001:4860::1", "not-an-ip", "172.32.0.1"):
            self.assertFalse(s.client_allowed(ip), ip)

    def test_address_only(self):
        s = self.store
        for h in ("192.168.1.5", "192.168.1.5:8080", "[fd00::1]:8080", "[::1]"):
            self.assertTrue(s.host_allowed(h), h)
        for h in ("bridge.example", "bridge.example:8080", "evil.com", None, "", "1.2.3.4:x", "a:b:c"):
            self.assertFalse(s.host_allowed(h), h)
        s.save({"allowed_hosts": ["bridge.home"]}, 0)
        self.assertTrue(s.host_allowed("bridge.home:8080"))
        self.assertTrue(s.host_allowed("BRIDGE.home"))
        self.assertFalse(s.host_allowed("other.home"))

    def test_revision_and_validation(self):
        s = self.store
        new = s.save({"allowed_nets": ["192.168.1.0/24"]}, 0)
        self.assertEqual(new["rev"], 1)
        self.assertFalse(s.client_allowed("10.0.0.1"))
        with self.assertRaises(store_mod.Stale):
            s.save({"server_address": "x"}, 0)
        for bad in [{"allowed_nets": []}, {"allowed_nets": ["nonsense"]}, {"allowed_hosts": ["bad host"]},
                    {"links": {"x": "javascript:alert(1)"}}, {"unifi": {"url": "ftp://x", "fingerprint": ""}},
                    {"unifi": {"url": "", "fingerprint": "zz"}}, {"pc": {"terminal": "rm"}},
                    {"pc": {"ssh_target": "me@host; rm -rf ~"}}, {"pc": {"folders": ["/srv/'x'"]}},
                    {"pc": {"folders": ["/srv/$(id)"]}}, {"pc": {"folders": ["relative"]}},
                    {"pc": {"folders": ["/a/../etc"]}}, {"services": {"rm": {"url": "http://x"}}}, {"nope": 1}]:
            with self.assertRaises(store_mod.Invalid, msg=bad):
                s.save(bad, s.settings()["rev"])
        s.save({"links": {"app": "https://{server}:8443/"}}, s.settings()["rev"])
        s.reset_access()
        self.assertTrue(s.client_allowed("10.0.0.1"))

    def test_secrets_never_listed(self):
        s = self.store
        s.set_secret("plex_token", "abc123")
        self.assertEqual(s.secret("plex_token"), "abc123")
        self.assertEqual(s.secrets_set()["plex_token"], True)
        self.assertNotIn("abc123", json.dumps(s.settings()))
        s.set_secret("plex_token", "")
        self.assertFalse(s.secrets_set()["plex_token"])
        with self.assertRaises(store_mod.Invalid):
            s.set_secret("unknown", "x")
        with self.assertRaises(store_mod.Invalid):
            s.set_secret("plex_token", "a\nb")


class TestAuth(Temp):
    def test_hash(self):
        h = auth_mod.hash_password("correct horse", iterations=1000)
        self.assertTrue(auth_mod.check_password("correct horse", h))
        self.assertFalse(auth_mod.check_password("wrong", h))
        self.assertFalse(auth_mod.check_password("x", "garbage"))

    def test_login_and_lockout(self):
        self.auth.set_password("a long password")
        token, err = self.auth.login("a long password", "127.0.0.1")
        self.assertIsNone(err)
        self.assertTrue(self.auth.valid(token))
        for _ in range(self.cfg.lockout_failures):
            self.assertIsNone(self.auth.login("nope", "127.0.0.1")[0])
        token2, err = self.auth.login("a long password", "127.0.0.1")
        self.assertIsNone(token2)
        self.assertIn("Too many", err)

    def test_setup_code(self):
        code = self.auth.setup_code()
        self.assertRegex(code, r"^\d{4}-\d{4}$")
        self.assertEqual(self.auth.setup_code(), code)        # stable until used
        self.assertFalse(self.auth.check_setup_code("0000-0000" if code != "0000-0000" else "1", "1.2.3.4")[0])
        self.assertTrue(self.auth.check_setup_code(code.replace("-", " "), "1.2.3.4")[0])
        self.auth.set_password("a long password")
        self.assertIsNone(self.db.get_setting("setup_code"))   # gone once a password is set
        self.assertFalse(self.auth.check_setup_code(code, "1.2.3.4")[0])

    def test_setup_code_lockout(self):
        self.auth.setup_code()
        for _ in range(self.cfg.lockout_failures):
            self.auth.check_setup_code("1", "1.2.3.4")
        ok, err = self.auth.check_setup_code(self.auth.setup_code(), "1.2.3.4")
        self.assertFalse(ok)
        self.assertIn("Too many", err)


class TestDiscovery(unittest.TestCase):
    def c(self, **kw):
        base = {"id": "abc", "name": "x", "image": "", "state": "running", "ip": None, "ports": [],
                "host_network": False, "labels": {}}
        base.update(kw)
        return base

    def test_image_names(self):
        self.assertEqual(discovery.image_name("lscr.io/linuxserver/plex:latest"), "plex")
        self.assertEqual(discovery.image_name("pihole/pihole"), "pihole")
        self.assertEqual(discovery.image_name("localhost:5000/app:1@sha256:abc"), "app")

    def test_links(self):
        link = discovery.tile_link
        s = "192.168.1.5"
        self.assertEqual(link(self.c(image="pihole/pihole", ip="192.168.1.200", ports=[{"private": 80, "public": None}]), s),
                         "http://192.168.1.200/admin/")
        self.assertEqual(link(self.c(image="jellyfin/jellyfin", ports=[{"private": 8096, "public": 18096}]), s),
                         "http://192.168.1.5:18096/")
        self.assertEqual(link(self.c(image="lscr.io/linuxserver/plex", host_network=True), s),
                         "http://192.168.1.5:32400/web/")
        self.assertEqual(link(self.c(image="portainer/portainer-ce:lts", ports=[{"private": 9443, "public": 9443}]), s),
                         "https://192.168.1.5:9443/")
        # Not published, no LAN address: nothing a browser can open.
        self.assertIsNone(link(self.c(image="sonarr", ports=[{"private": 8989, "public": None}]), s))
        self.assertIsNone(link(self.c(image="favonia/cloudflare-ddns"), s))
        self.assertIsNone(link(self.c(image="unknown/thing", ports=[{"private": 5000, "public": 5000}]), s))
        # Labels, then the setup page, win.
        c = self.c(image="unknown/thing", ports=[{"private": 5000, "public": 5001}],
                   labels={"battlebridge.port": "5000", "battlebridge.path": "app"})
        self.assertEqual(link(c, s), "http://192.168.1.5:5001/app")
        self.assertEqual(link(self.c(labels={"battlebridge.url": "https://{server}:1/"}), s), "https://192.168.1.5:1/")
        # A label from an image can't make a script link.
        self.assertIsNone(link(self.c(labels={"battlebridge.url": "javascript:alert(1)"}), s))
        self.assertIsNone(link(self.c(labels={"battlebridge.url": "http://x/\"onclick=x"}), s))
        self.assertIsNone(link(self.c(image="pihole", ip="1.2.3.4", labels={"battlebridge.link": "none"}), s))
        self.assertEqual(link(c, s, {"x": "http://elsewhere/"}), "http://elsewhere/")
        self.assertIsNone(link(c, s, {"x": "none"}))
        self.assertEqual(link(c, "fd00::5"), "http://[fd00::5]:5001/app")
        # Probe results fill the gap.
        u = self.c(image="unknown/thing", ports=[{"private": 5000, "public": 5000}])
        self.assertEqual(link(u, s, probed={"abc": (5000, "http")}), "http://192.168.1.5:5000/")

    def test_services_and_order(self):
        cs = [self.c(name="b", image="linuxserver/sonarr", state="exited"), self.c(name="a", image="sonarr:latest")]
        self.assertEqual(discovery.find_service(cs, "sonarr")["name"], "a")
        self.assertIsNone(discovery.find_service(cs, "plex"))
        self.assertTrue(discovery.update_last(self.c(image="pihole/pihole")))
        self.assertTrue(discovery.update_last(self.c(labels={"battlebridge.update": "last"})))
        self.assertEqual(discovery.label(self.c(image="seerr-team/seerr")), "Seerr")
        self.assertEqual(discovery.label(self.c(labels={"battlebridge.label": "Mine"})), "Mine")

    def test_probe(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
        srv.RequestHandlerClass.log_message = lambda *a: None
        threading.Thread(target=srv.handle_request, daemon=True).start()
        self.assertEqual(discovery.probe(f"127.0.0.1:{srv.server_address[1]}"), "http")
        srv.server_close()
        self.assertIsNone(discovery.probe("127.0.0.1:1", timeout=0.5))


class TestHost(unittest.TestCase):
    def test_snapshot(self):
        h = HostCollector("/proc", str(FAKES / "sys"))
        self.assertIsNone(h.snapshot()["cpu"])
        time.sleep(0.3)
        s = h.snapshot()
        self.assertIsNotNone(s["cpu"])
        self.assertEqual(len(s["cores"]), os.cpu_count())
        self.assertEqual(s["cpu_temp"], 41.0)


class FakeDocker:
    def __init__(self, port, *extra):
        self.port = port
        self.extra = extra

    def __enter__(self):
        self.p = subprocess.Popen([sys.executable, str(FAKES / "fake_docker.py"), str(self.port), *self.extra])
        for _ in range(50):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
            try:
                c.request("GET", "/info")
                c.getresponse().read()
                break
            except OSError:
                time.sleep(0.1)
            finally:
                c.close()
        return self

    def __exit__(self, *a):
        self.p.terminate()
        self.p.wait()


class TestDocker(unittest.TestCase):
    def test_snapshot(self):
        with FakeDocker(23751, "--trouble") as fd:
            d = DockerCollector(f"http://127.0.0.1:{fd.port}")
            d.snapshot()
            time.sleep(0.05)
            items = {c["name"]: c for c in d.snapshot()}
        self.assertEqual(d.hostname, "homelab")
        self.assertEqual(items["pihole"]["ip"], "192.168.1.200")        # macvlan address
        self.assertIsNone(items["jellyfin"]["ip"])                       # bridge: no LAN address
        self.assertEqual(items["jellyfin"]["ports"], [{"private": 8096, "public": 8096}])
        self.assertTrue(items["plex"]["host_network"])
        self.assertTrue(items["plex"]["gpu_capable"])
        self.assertIsNone(items["plex"]["net_rx"])                       # host network: no counters
        self.assertGreater(items["jellyfin"]["net_tx"], 0)
        self.assertGreater(items["sonarr"]["net_rx"], 0)
        self.assertEqual(items["seerr"]["state"], "exited")
        self.assertTrue(items["myapp"]["built_here"])
        self.assertFalse(items["sonarr"]["built_here"])
        self.assertEqual(items["myapp"]["labels"]["battlebridge.port"], "8000")
        self.assertEqual(items["sonarr"]["labels"]["com.docker.compose.project"], "downloads")
        self.assertIsNotNone(items["plex"]["cpu"])
        self.assertEqual(list(items)[0], "pihole")                      # LAN addresses first


PMON = """# gpu         pid   type     sm    mem    enc    dec    jpg    ofa    command
# Idx           #    C/G      %      %      %      %      %      %    name
    0     242640     C      3      1     12     20      -      -    ffmpeg
    0     242700     C      -      -      -      -      -      -    ffmpeg
    0       3100     C      -      -      5      -      -      -    Plex Transcoder
"""


class TestGpu(unittest.TestCase):
    def test_parse_pmon(self):
        p = gpu.parse_pmon(PMON)
        self.assertEqual(p[242640], {"sm": 3.0, "enc": 12.0, "dec": 20.0})
        self.assertEqual(p[242700], {"sm": 0.0, "enc": 0.0, "dec": 0.0})

    def test_nothing_running(self):
        self.assertEqual(gpu.parse_pmon(PMON.splitlines()[0] + "\n    0  -  -  -  -  -  -  -  -  -\n"), {})

    def test_by_container(self):
        jf = "a" * 64
        px = "b" * 64
        with tempfile.TemporaryDirectory() as proc:
            for pid, line in ((242640, f"0::/system.slice/docker-{jf}.scope"),
                              (242700, f"0::/system.slice/docker-{jf}.scope"),
                              (3100, f"0::/docker/{px}")):
                os.makedirs(os.path.join(proc, str(pid)))
                with open(os.path.join(proc, str(pid), "cgroup"), "w") as f:
                    f.write(line + "\n")
            use = gpu.by_container(gpu.parse_pmon(PMON) | {999: {"sm": 50.0, "enc": 0.0, "dec": 0.0}}, proc)
        self.assertEqual(use, {jf[:12]: 20.0, px[:12]: 5.0})


class TestServer(unittest.TestCase):
    """The access rules and the setup flow, end to end over HTTP."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fd = FakeDocker(23752).__enter__()
        cls.cfg = make_cfg(BB_DB=os.path.join(cls.tmp.name, "s.db"), BB_DOCKER_URL="http://127.0.0.1:23752",
                           BB_RUNNER_DIR=cls.tmp.name)
        from battle_bridge.sampler import Sampler
        db = DB(cls.cfg.db_path)
        cls.auth = auth_mod.Auth(db, cls.cfg)
        cls.store = store_mod.Store(db)
        sampler = Sampler(cls.cfg, db, cls.store)
        sampler.tick_slow()
        cls.srv = make_server(App(cls.cfg, db, cls.auth, cls.store, sampler))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.fd.__exit__()
        cls.tmp.cleanup()

    def req(self, method, path, host="192.168.1.5:8080", body=None, headers=None, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host}
        if cookie:
            h["Cookie"] = cookie
        if body is not None:
            body = json.dumps(body)
            h.update({"Content-Type": "application/json", "X-Battle-Bridge": "1", "Origin": f"http://{host}"})
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def login(self):
        st, h, _ = self.req("POST", "/api/login", body={"password": "server test password"})
        self.assertEqual(st, 200)
        return h["Set-Cookie"].split(";")[0]

    def test_1_first_run(self):
        # No password yet: the setup page's data is open, without keys.
        st, _, b = self.req("GET", "/api/setup")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(b)["first_run"])
        st, _, b = self.req("POST", "/api/first-run", body={"code": "nope", "password": "server test password"})
        self.assertEqual(st, 403)
        code = self.auth.setup_code()
        st, _, b = self.req("POST", "/api/first-run", body={"code": code, "password": "short"})
        self.assertEqual(st, 400)
        st, h, b = self.req("POST", "/api/first-run", body={"code": code, "password": "server test password"})
        self.assertEqual(st, 200)
        self.assertIn("HttpOnly", h["Set-Cookie"])
        st, _, _ = self.req("POST", "/api/first-run", body={"code": code, "password": "another password"})
        self.assertEqual(st, 409)
        # From now on the setup page needs the login.
        self.assertEqual(self.req("GET", "/api/setup")[0], 401)

    def test_2_hostname_refused(self):
        self.assertEqual(self.req("GET", "/", host="bridge.example")[0], 421)
        self.assertEqual(self.req("GET", "/", host="192.168.1.5:8080")[0], 200)

    def test_2_page_and_headers(self):
        st, h, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        csp = h["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertEqual(self.req("GET", "/setup")[0], 200)

    def test_2_static_files_are_self_contained(self):
        # Our own files: no inline styles (the CSP blocks them) and nothing
        # loaded from elsewhere.  Vendored xterm.js is checked in a browser.
        for f in (ROOT / "app" / "static").glob("*"):
            if not f.is_file():
                continue
            text = f.read_text(errors="ignore")
            self.assertNotIn("style=", text, f.name)
            self.assertNotIn(".style.", text, f.name)
            self.assertIsNone(re.search(r"""(src|href)=["']https?://|url\(\s*["']?https?://|fetch\(\s*["'`]https?://""", text), f.name)

    def test_2_static_traversal(self):
        self.assertEqual(self.req("GET", "/static/../battle_bridge/auth.py")[0], 404)
        self.assertEqual(self.req("GET", "/static/%2e%2e/battle_bridge/auth.py")[0], 404)

    def test_3_live(self):
        st, _, body = self.req("GET", "/api/live")
        d = json.loads(body)
        self.assertEqual(st, 200)
        self.assertEqual(d["version"], VERSION)
        self.assertEqual(d["hostname"], "homelab")
        links = {c["name"]: c["link"] for c in d["containers"]}
        self.assertEqual(links["jellyfin"], "http://192.168.1.5:8096/")    # the address the browser used
        self.assertEqual(links["pihole"], "http://192.168.1.200/admin/")
        self.assertIn("No host runner", d["facts_problem"])

    def test_3_posts_need_header_and_origin(self):
        body = json.dumps({"password": "server test password"})
        st, _, _ = self.req("POST", "/api/login", body=None, headers={"Content-Type": "application/json"})
        self.assertEqual(st, 403)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/login", body=body, headers={"Host": "192.168.1.5:8080", "X-Battle-Bridge": "1",
                                                            "Origin": "http://evil.example"})
        self.assertEqual(c.getresponse().status, 403)

    def test_3_settings_need_login_and_revision(self):
        self.assertEqual(self.req("POST", "/api/settings", body={"rev": 0, "changes": {}})[0], 401)
        ck = self.login()
        rev = json.loads(self.req("GET", "/api/setup", cookie=ck)[2])["settings"]["rev"]
        st, _, b = self.req("POST", "/api/settings", cookie=ck,
                            body={"rev": rev, "changes": {"server_address": "192.168.1.9"}})
        self.assertEqual(st, 200, b)
        st, _, _ = self.req("POST", "/api/settings", cookie=ck, body={"rev": rev, "changes": {"server_address": ""}})
        self.assertEqual(st, 409)       # an old tab
        st, _, b = self.req("POST", "/api/settings", cookie=ck,
                            body={"rev": rev + 1, "changes": {"pc": {"ssh_target": "bad"}}})
        self.assertEqual(st, 400)
        self.req("POST", "/api/settings", cookie=ck, body={"rev": rev + 1, "changes": {"server_address": ""}})

    def test_3_keys_are_write_only(self):
        ck = self.login()
        st, _, b = self.req("POST", "/api/secrets", cookie=ck, body={"sonarr_key": "s3cret-value"})
        self.assertEqual(st, 200)
        self.assertNotIn(b"s3cret-value", b)
        for path in ("/api/setup", "/api/live"):
            _, _, b = self.req("GET", path, cookie=ck)
            self.assertNotIn(b"s3cret-value", b, path)
        self.assertTrue(json.loads(self.req("GET", "/api/setup", cookie=ck)[2])["secrets"]["sonarr_key"])

    def test_3_pc_installer(self):
        self.assertEqual(self.req("GET", "/api/pc/installer")[0], 401)
        ck = self.login()
        self.assertEqual(self.req("GET", "/api/pc/installer", cookie=ck)[0], 409)    # no SSH login yet
        rev = json.loads(self.req("GET", "/api/setup", cookie=ck)[2])["settings"]["rev"]
        self.req("POST", "/api/settings", cookie=ck, body={"rev": rev, "changes": {
            "pc": {"terminal": "kitty", "ssh_target": "me@192.168.1.5", "ssh_port": 22, "folders": ["~", "/srv/a b"]}}})
        st, h, b = self.req("GET", "/api/pc/installer", cookie=ck)
        self.assertEqual(st, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        self.assertIn(b"kitty", b)
        self.assertIn(b"me@192.168.1.5", b)


if __name__ == "__main__":
    unittest.main()
