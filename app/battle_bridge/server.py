"""The web server: static files, JSON endpoints, and the access rules.

Every request is checked first: the client must be on an allowed network and
must ask for the page by an address, or by a hostname allowed on the setup
page.  Anything else gets a plain refusal.  Reading is open on the home
network.  Anything that changes something needs a login.
"""

import json
import logging
import math
import mimetypes
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import VERSION, discovery, pc, services, store as store_mod
from .auth import COOKIE
from .config import STATIC_DIR
from .facts import Facts
from .runner_client import RunnerClient, RunnerError

log = logging.getLogger("battle_bridge.server")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; manifest-src 'self'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")

MAX_BODY = 64 * 1024
MIN_PASSWORD = 10


class App:
    def __init__(self, cfg, db, auth, store, sampler, facts=None, poller=None):
        self.cfg = cfg
        self.db = db
        self.auth = auth
        self.store = store
        self.sampler = sampler
        self.facts = facts or Facts(cfg.runner_dir)
        self.poller = poller
        self.runner = RunnerClient(cfg.runner_dir)

    def server_address(self, host_header):
        return self.store.settings()["server_address"] or store_mod.split_host(host_header) or "localhost"

    def containers(self, host_header):
        s = self.sampler.snapshot()
        if s["containers"] is None:
            return None
        server = self.server_address(host_header)
        links = self.store.settings()["links"]
        probed = self.sampler.prober.results()
        # The runner reads compose files, so it knows better what's built here.
        facts, _ = self.facts.get()
        built = {i.get("container") for i in ((facts or {}).get("image_updates") or {}).get("items") or []
                 if i.get("status") == "built here"}
        out = []
        for c in s["containers"]:
            c = dict(c)
            c["built_here"] = c["built_here"] or c["name"] in built
            c["link"] = discovery.tile_link(c, server, links, probed)
            c["label"] = discovery.label(c)
            c["icon"] = discovery.icon(c)
            c["kind"] = discovery.service_kind(c)
            out.append(c)
        return out

    def live(self, logged_in, host_header):
        s = self.sampler.snapshot()
        facts, problem = self.facts.get()
        settings = self.store.settings()
        return {
            "facts": facts,
            "facts_problem": problem,
            "services": self.poller.snapshot() if self.poller else {},
            "unifi_url": (settings["unifi"]["url"] + "/") if settings["unifi"]["url"] else None,
            "version": VERSION,
            "now": time.time(),
            "updated": s["updated"],
            "logged_in": logged_in,
            "has_password": self.auth.has_password(),
            "hostname": s.get("hostname"),
            "host": s["host"],
            "host_error": s["host_error"],
            "gpu": s["gpu"],
            "containers": self.containers(host_header),
            "docker_error": s["docker_error"],
            "pc_ready": bool(settings["pc"]["ssh_target"]),
            "pc_folders": settings["pc"]["folders"],
        }

    def setup_info(self, host_header):
        settings = self.store.settings()
        containers = self.containers(host_header) or []
        links = settings["links"]
        probed = self.sampler.prober.results()
        rows = []
        for c in containers:
            lab = c.get("labels") or {}
            if c["name"] in links:
                source = "set here"
            elif lab.get("battlebridge.url") or lab.get("battlebridge.port") or lab.get("battlebridge.link"):
                source = "label"
            elif discovery.known(c):
                source = "built in"
            elif c["id"] in probed:
                source = "found by probe"
            else:
                source = "none"
            rows.append({"name": c["name"], "label": c["label"], "image": c["image"], "state": c["state"],
                         "link": c["link"], "source": source, "override": links.get(c["name"], "")})
        found = {}
        for kind in store_mod.SERVICE_KINDS:
            c = discovery.find_service(containers, kind)
            found[kind] = {
                "container": c["name"] if c else None,
                "address": discovery.service_url(c, self.cfg.host_gateway) if c else None,
                "override": (settings["services"].get(kind) or {}).get("url", ""),
            }
        folders = sorted({(c.get("labels") or {}).get("com.docker.compose.project.working_dir")
                          for c in containers} - {None, ""}, key=str.lower)
        facts, problem = self.facts.get()
        runner_version = ((facts or {}).get("runner") or {}).get("version")
        return {
            "first_run": not self.auth.has_password(),
            "settings": settings,
            "secrets": self.store.secrets_set(),
            "secret_labels": store_mod.SECRETS,
            "containers": rows,
            "services": found,
            "statuses": self.poller.snapshot() if self.poller else {},
            "terminals": store_mod.TERMINALS,
            "folders_found": folders,
            "page_address": store_mod.split_host(host_header),
            "server_address": self.server_address(host_header),
            "runner": {"version": runner_version, "app_version": VERSION, "problem": problem},
            "install_command": (f"docker cp {self.cfg.container_name}:/opt/battle-bridge/host ./battle-bridge-host"
                                " && sudo bash battle-bridge-host/install-runner.sh"),
            "version": VERSION,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "BattleBridge"
    sys_version = ""
    app: App = None  # set by make_server

    # --- plumbing -----------------------------------------------------------

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def _headers(self, status, ctype, length, extra=None, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _send(self, status, body, ctype="text/plain; charset=utf-8", extra=None, cache="no-store"):
        if isinstance(body, str):
            body = body.encode()
        self._headers(status, ctype, len(body), extra, cache)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=HTTPStatus.OK, extra=None):
        try:
            text = json.dumps(data, separators=(",", ":"), allow_nan=False)
        except ValueError:
            # A NaN or infinity from somewhere (a service, a panel).  Browsers
            # can't parse those, so they go out as null instead.
            text = json.dumps(_finite(data), separators=(",", ":"))
        self._send(status, text, "application/json; charset=utf-8", extra)

    def _token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = SimpleCookie(raw)
        except Exception:
            return None
        return c[COOKIE].value if COOKIE in c else None

    def _logged_in(self):
        return self.app.auth.valid(self._token())

    def _allowed(self):
        st = self.app.store
        if not st.client_allowed(self.client_address[0]):
            self._send(HTTPStatus.FORBIDDEN, "Home network only.\n")
            return False
        if not st.host_allowed(self.headers.get("Host")):
            self._send(HTTPStatus.MISDIRECTED_REQUEST,
                       "Use the address, not a hostname.  Hostnames can be allowed on the setup page.\n")
            return False
        return True

    def _same_origin_post(self):
        """Writes must come from this page: our header, and our origin if given."""
        if self.headers.get("X-Battle-Bridge") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            if urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").strip().lower():
                return False
        return True

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return None

    def _need_login(self):
        if self._logged_in():
            return False
        self._json({"error": "Log in first."}, HTTPStatus.UNAUTHORIZED)
        return True

    # --- GET ----------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        # Docker's own healthcheck, from inside the container only.
        if self.path == "/healthz" and self.client_address[0] in ("127.0.0.1", "::1"):
            return self._send(HTTPStatus.OK, "ok\n")
        if not self._allowed():
            return
        url = urlsplit(self.path)
        path = url.path
        host = self.headers.get("Host")
        if path == "/":
            return self._static("index.html")
        if path == "/setup":
            return self._static("setup.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/live":
            return self._json(self.app.live(self._logged_in(), host))
        if path == "/api/history":
            return self._history(parse_qs(url.query))
        if path == "/api/session":
            return self._json({"logged_in": self._logged_in(),
                               "has_password": self.app.auth.has_password(),
                               "locked_for": self.app.auth.locked_for()})
        if path == "/api/setup":
            # Open before the first password is set (it says so, and holds no
            # keys), and behind the login after that.
            if self.app.auth.has_password() and self._need_login():
                return
            return self._json(self.app.setup_info(host))
        if path == "/api/pc/installer":
            if self._need_login():
                return
            return self._pc_installer(host)
        if path == "/api/jobs":
            if self._need_login():
                return
            return self._runner_call({"op": "jobs"})
        if path == "/api/jobs/stream":
            if self._need_login():
                return
            return self._stream(parse_qs(url.query))
        if path == "/api/jobs/pclog":
            if self._need_login():
                return
            return self._stream(parse_qs(url.query), pc=True)
        self._send(HTTPStatus.NOT_FOUND, "Not found.\n")

    def _static(self, name):
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._send(HTTPStatus.NOT_FOUND, "Not found.\n")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        cache = "no-cache" if name.endswith(".html") else "max-age=3600"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype, cache=cache)

    def _history(self, q):
        series = (q.get("series") or [""])[0]
        if not series or len(series) > 200:
            return self._json({"error": "series needed"}, HTTPStatus.BAD_REQUEST)
        try:
            hours = min(24.0, max(0.1, float((q.get("hours") or ["24"])[0])))
        except ValueError:
            hours = 24.0
        since = int(time.time() - hours * 3600)
        return self._json({"series": series, "points": self.app.db.history(series, since)})

    def _pc_installer(self, host):
        p = self.app.store.settings()["pc"]
        if not p["ssh_target"]:
            return self._json({"error": "Set the SSH login on the setup page first."}, HTTPStatus.CONFLICT)
        body = pc.installer(p, store_mod.split_host(host) or "")
        self._send(HTTPStatus.OK, body, "text/x-shellscript; charset=utf-8",
                   extra={"Content-Disposition": 'attachment; filename="battlebridge-pc-install.sh"'})

    # --- the job runner -----------------------------------------------------

    def _runner_call(self, req):
        try:
            reply = self.app.runner.call(req)
        except RunnerError as e:
            return self._json({"error": str(e)}, HTTPStatus.SERVICE_UNAVAILABLE)
        status = HTTPStatus.CONFLICT if reply.get("error") else HTTPStatus.OK
        return self._json(reply, status)

    def _stream(self, q, pc=False):
        """Job output as server-sent events.  Base64, because it's raw terminal
        bytes and may split a character."""
        try:
            rid = int((q.get("id") or ["0"])[0])
            offset = max(0, int((q.get("offset") or ["0"])[0]))
        except ValueError:
            return self._json({"error": "Bad request."}, HTTPStatus.BAD_REQUEST)
        source = (self.app.runner.pclog((q.get("file") or [""])[0]) if pc
                  else self.app.runner.stream(rid, offset))
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            for msg in source:
                if "data" in msg:
                    out = f"event: out\ndata: {msg['data']}\nid: {msg['offset']}\n\n"
                elif "end" in msg:
                    out = f"event: end\ndata: {json.dumps(msg['end'])}\n\n"
                elif "error" in msg:
                    out = f"event: fail\ndata: {json.dumps(msg['error'])}\n\n"
                else:
                    out = ": beat\n\n"
                self.wfile.write(out.encode())
                self.wfile.flush()
        except RunnerError as e:
            self.wfile.write(f"event: fail\ndata: {json.dumps(str(e))}\n\n".encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    # --- POST ---------------------------------------------------------------

    def do_POST(self):
        if not self._allowed():
            return
        if not self._same_origin_post():
            return self._json({"error": "Refused."}, HTTPStatus.FORBIDDEN)
        path = urlsplit(self.path).path
        if path == "/api/login":
            return self._login()
        if path == "/api/first-run":
            return self._first_run()
        if path == "/api/logout":
            self.app.auth.logout(self._token())
            return self._json({"ok": True}, extra={"Set-Cookie": _cookie("", 0)})
        if self._need_login():
            return
        body = self._body_json()
        if not isinstance(body, dict):
            return self._json({"error": "Bad request."}, HTTPStatus.BAD_REQUEST)
        if path.startswith("/api/jobs/"):
            return self._jobs(path[len("/api/jobs/"):], body)
        if path == "/api/settings":
            return self._save_settings(body)
        if path == "/api/secrets":
            return self._save_secrets(body)
        if path == "/api/password":
            return self._change_password(body)
        if path == "/api/unifi/pin":
            return self._pin_unifi(body)
        self._send(HTTPStatus.NOT_FOUND, "Not found.\n")

    def _jobs(self, action, body):
        if action == "start":
            return self._runner_call({"op": "start", "job": body.get("job"), "arg": body.get("arg")})
        if action == "input":
            return self._runner_call({"op": "input", "id": body.get("id"), "data": body.get("data")})
        if action == "stop":
            return self._runner_call({"op": "stop", "id": body.get("id")})
        if action == "resize":
            return self._runner_call({"op": "resize", "id": body.get("id"),
                                      "rows": body.get("rows"), "cols": body.get("cols")})
        return self._send(HTTPStatus.NOT_FOUND, "Not found.\n")

    def _login(self):
        body = self._body_json() or {}
        pw = body.get("password")
        if not isinstance(pw, str) or not pw:
            return self._json({"error": "Enter the password."}, HTTPStatus.BAD_REQUEST)
        token, err = self.app.auth.login(pw, self.client_address[0])
        if err:
            return self._json({"error": err}, HTTPStatus.FORBIDDEN)
        return self._json({"ok": True}, extra={"Set-Cookie": self._session_cookie(token)})

    def _session_cookie(self, token):
        return _cookie(token, self.app.cfg.session_days * 86400)

    def _first_run(self):
        a = self.app.auth
        if a.has_password():
            return self._json({"error": "A password is already set.  Log in instead."}, HTTPStatus.CONFLICT)
        body = self._body_json() or {}
        ok, err = a.check_setup_code(body.get("code") or "", self.client_address[0])
        if not ok:
            return self._json({"error": err}, HTTPStatus.FORBIDDEN)
        pw = body.get("password")
        if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
            return self._json({"error": f"Use at least {MIN_PASSWORD} characters."}, HTTPStatus.BAD_REQUEST)
        a.set_password(pw)
        log.info("Password set from the first-run page by %s", self.client_address[0])
        return self._json({"ok": True}, extra={"Set-Cookie": self._session_cookie(a.new_session())})

    def _change_password(self, body):
        a = self.app.auth
        ok, err = a.check_current(body.get("current"), self.client_address[0])
        if not ok:
            return self._json({"error": err}, HTTPStatus.FORBIDDEN)
        new = body.get("new")
        if not isinstance(new, str) or len(new) < MIN_PASSWORD:
            return self._json({"error": f"Use at least {MIN_PASSWORD} characters."}, HTTPStatus.BAD_REQUEST)
        a.set_password(new)
        return self._json({"ok": True}, extra={"Set-Cookie": self._session_cookie(a.new_session())})

    def _save_settings(self, body):
        changes = body.get("changes")
        if not isinstance(changes, dict) or not isinstance(body.get("rev"), int):
            return self._json({"error": "Bad request."}, HTTPStatus.BAD_REQUEST)
        try:
            new = self.app.store.save(changes, body["rev"])
        except store_mod.Stale:
            return self._json({"error": "Changed in another tab since this page loaded.  "
                                        "Reload to see the newer settings."}, HTTPStatus.CONFLICT)
        except store_mod.Invalid as e:
            return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if "allowed_nets" in changes and not self.app.store.client_allowed(self.client_address[0]):
            log.warning("The allowed networks no longer include %s", self.client_address[0])
        if self.app.poller and ("services" in changes or "unifi" in changes):
            for kind in list(store_mod.SERVICE_KINDS) + ["unifi"]:
                self.app.poller.forget(kind)
        return self._json({"ok": True, "settings": new})

    def _save_secrets(self, body):
        st = self.app.store
        try:
            for name, value in body.items():
                if value is None:
                    continue
                st.set_secret(name, value)
        except store_mod.Invalid as e:
            return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if self.app.poller:
            for name in body:
                kind = store_mod.SECRETS.get(name, (None,))[0]
                if kind:
                    self.app.poller.forget(kind)
        return self._json({"ok": True, "secrets": st.secrets_set()})

    def _pin_unifi(self, body):
        """Trust the gateway's certificate as it is now.  The UniFi key is
        never sent to a gateway whose certificate doesn't match the pin."""
        st = self.app.store
        s = st.settings()
        url = s["unifi"]["url"]
        if not url.startswith("https://"):
            return self._json({"error": "Set a https:// address for the gateway first."}, HTTPStatus.BAD_REQUEST)
        try:
            fp = services.fingerprint(url)
        except services.ServiceError as e:
            return self._json({"error": f"Couldn't reach the gateway: {e}"}, HTTPStatus.BAD_GATEWAY)
        try:
            new = st.save({"unifi": {"url": url, "fingerprint": fp}}, body.get("rev", s["rev"]))
        except store_mod.Stale:
            return self._json({"error": "Changed in another tab.  Reload first."}, HTTPStatus.CONFLICT)
        if self.app.poller:
            self.app.poller.forget("unifi")
        return self._json({"ok": True, "settings": new})


def _finite(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def _cookie(value, max_age):
    return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"


def make_server(app):
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((app.cfg.listen_host, app.cfg.listen_port), handler)
    srv.daemon_threads = True
    return srv
