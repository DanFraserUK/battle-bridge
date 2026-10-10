"""A stand-in for the Docker socket proxy, with a believable home server.

GET only, like the real proxy.  CPU counters move on each stats call so the
page sees changing figures.

    python3 fake_docker.py <port> [--trouble] [--probe-port N]
"""

import json
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

NOW = time.time()
DAY = 86400
PROBE = int(sys.argv[sys.argv.index("--probe-port") + 1]) if "--probe-port" in sys.argv else 18080
HERE = __file__.rsplit("/", 1)[0]

# name, image, compose project, LAN address, ports [(private, public)], host network,
# state, health, days up, MiB, gpu, labels
CONTAINERS = [
    ("pihole", "pihole/pihole:latest", "dns", "192.168.1.200", [(80, None), (53, None)], False,
     "running", "healthy", 4, 120, False, {}),
    ("plex", "lscr.io/linuxserver/plex:latest", "media", None, [], True, "running", None, 13, 900, True, {}),
    ("jellyfin", "jellyfin/jellyfin:latest", "media", None, [(8096, 8096)], False, "running", "healthy", 13, 700, True, {}),
    ("sonarr", "lscr.io/linuxserver/sonarr:latest", "downloads", None, [(8989, 8989)], False, "running", None, 13, 310, False, {}),
    ("radarr", "lscr.io/linuxserver/radarr:latest", "downloads", None, [(7878, 7878)], False, "running", None, 13, 290, False, {}),
    ("prowlarr", "lscr.io/linuxserver/prowlarr:latest", None, None, [(9696, 9696)], False, "running", None, 13, 160, False, {}),
    ("transmission", "lscr.io/linuxserver/transmission:latest", None, None, [(9091, 9091), (51413, 51413)], False,
     "running", None, 13, 80, False, {}),
    ("seerr", "ghcr.io/seerr-team/seerr:latest", None, None, [(5055, 5055)], False, "running", "healthy", 13, 250, False, {}),
    ("cloudflare-ddns", "favonia/cloudflare-ddns:latest", None, None, [], False, "running", None, 4, 12, False, {}),
    ("npm", "jc21/nginx-proxy-manager:latest", None, None, [(80, 80), (81, 81), (443, 443)], False,
     "running", None, 13, 190, False, {}),
    ("portainer", "portainer/portainer-ce:lts", None, None, [(8000, 8000), (9443, 9443)], False, "running", None, 13, 60, False, {}),
    ("whoami", "traefik/whoami:latest", "tools", None, [(80, PROBE)], False, "running", None, 2, 8, False, {}),
    ("myapp", "myapp:local", "myapp", None, [(8000, 8001)], False, "running", "healthy", 0.2, 45, False,
     {"battlebridge.port": "8000", "battlebridge.label": "My app"}),
    ("homeassistant", "ghcr.io/home-assistant/home-assistant:stable", None, None, [], True, "running", None, 6, 410, False, {}),
    ("battle-bridge", "ghcr.io/danfraseruk/battle-bridge:latest", "battle-bridge", None, [(8080, 8080)], False,
     "running", "healthy", 0.01, 40, False, {}),
    ("bb-dockerproxy", "lscr.io/linuxserver/socket-proxy:latest", "battle-bridge", None, [], False,
     "running", None, 0.01, 8, False, {}),
]
if "--trouble" in sys.argv:
    def _set(name, state):
        for i, c in enumerate(CONTAINERS):
            if c[0] == name:
                CONTAINERS[i] = c[:6] + (state, None, 0) + c[9:]
    _set("transmission", "restarting")
    _set("seerr", "exited")

CPU = {}
SYS = [10**12]


def cid(name):
    return (name.encode().hex() * 8)[:64]


def labels(c):
    name, _image, project, *_rest, extra = c
    out = dict(extra)
    if project:
        out["com.docker.compose.project"] = project
        out["com.docker.compose.project.working_dir"] = f"{HERE}/compose/{project}"
        out["com.docker.compose.service"] = name
    return out


def listing():
    out = []
    for c in CONTAINERS:
        name, image, project, ip, ports, hostnet, st, health, days, _mem, _gpu, _lab = c
        if hostnet:
            nets = {"host": {"IPAddress": ""}}
        elif ip:
            nets = {"lan": {"IPAddress": ip}}
        else:
            nets = {f"{project or 'bridge'}_default" if project else "bridge": {"IPAddress": "172.18.0.5"}}
        if name in ("battle-bridge", "bb-dockerproxy"):
            nets["battle-bridge_bb-internal"] = {"IPAddress": "172.30.0.2"}
        status = {"running": f"Up {int(days)} days" + (f" ({health})" if health else ""),
                  "restarting": "Restarting (1) 5 seconds ago", "exited": "Exited (1) 2 hours ago"}[st]
        plist = []
        for private, public in ports:
            if public:
                plist.append({"IP": "0.0.0.0", "PrivatePort": private, "PublicPort": public, "Type": "tcp"})
                plist.append({"IP": "::", "PrivatePort": private, "PublicPort": public, "Type": "tcp"})
            else:
                plist.append({"PrivatePort": private, "Type": "tcp"})
        out.append({"Id": cid(name), "Names": ["/" + name], "Image": image, "ImageID": "sha256:" + cid(image),
                    "State": st, "Status": status, "Ports": plist, "Labels": labels(c),
                    "HostConfig": {"NetworkMode": "host" if hostnet else "default"},
                    "NetworkSettings": {"Networks": nets}})
    return out


def inspect(name):
    for n, image, project, ip, ports, hostnet, st, health, days, mem, gpu, lab in CONTAINERS:
        if n == name:
            started = time.strftime("%Y-%m-%dT%H:%M:%S.123456789Z", time.gmtime(NOW - days * DAY))
            state = {"Status": st, "StartedAt": started}
            if health:
                state["Health"] = {"Status": health}
            hc = {"RestartPolicy": {"Name": "unless-stopped"}}
            if gpu:
                hc["DeviceRequests"] = [{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}]
            return {"Id": cid(n), "State": state, "RestartCount": 3 if st == "restarting" else 0, "HostConfig": hc}


def stats(name):
    SYS[0] += 8 * 10**9
    for c in CONTAINERS:
        if c[0] == name:
            mem = c[9]
            load = {"plex": 0.08, "jellyfin": 0.03, "sonarr": 0.01, "homeassistant": 0.02}.get(name, 0.002)
            CPU[name] = CPU.get(name, 0) + int(8 * 10**9 * load * random.uniform(0.5, 1.5))
            return {"cpu_stats": {"cpu_usage": {"total_usage": CPU[name]}, "system_cpu_usage": SYS[0]},
                    "memory_stats": {"usage": mem * 1048576 + 50 * 1048576,
                                     "stats": {"inactive_file": 50 * 1048576},
                                     "limit": 16 * 1024**3}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        names = {cid(c[0]): c[0] for c in CONTAINERS}
        names.update({c[0]: c[0] for c in CONTAINERS})   # Docker accepts names too
        if path == "/containers/json":
            return self._json(listing())
        if path == "/info":
            return self._json({"Name": "homelab", "ServerVersion": "29.0.0"})
        if path == "/networks":
            return self._json([{"Name": "lan", "Driver": "macvlan"}, {"Name": "bridge", "Driver": "bridge"},
                               {"Name": "host", "Driver": "host"}])
        if path.startswith("/images/"):
            image_id = path.split("/")[2]
            built = image_id == "sha256:" + cid("myapp:local")
            return self._json({"Id": image_id, "RepoDigests": [] if built else ["x@sha256:abc"]})
        if path.startswith("/containers/"):
            parts = path.split("/")
            name = names.get(parts[2]) or next((n for k, n in names.items() if len(parts[2]) >= 12
                                                and k.startswith(parts[2])), None)
            if name and parts[3] == "json":
                return self._json(inspect(name))
            if name and parts[3] == "stats":
                return self._json(stats(name))
            if name == "cloudflare-ddns" and parts[3] == "logs":
                lines = ["🌟 Cloudflare DDNS", "🤷 No valid IPv6 address",
                         "✅ The A records of example.org are already up to date",
                         "✅ The A records of media.example.org are already up to date"]
                frames = b"".join(b"\x01\0\0\0" + len(x.encode() + b"\n").to_bytes(4, "big") + x.encode() + b"\n"
                                  for x in lines)
                self.send_response(200)
                self.send_header("Content-Length", str(len(frames)))
                self.end_headers()
                self.wfile.write(frames)
                return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        self.send_response(403)
        self.end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2375
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
