"""Finding each container's web page, and which services Battle Bridge
knows how to talk to.

A tile's link comes from, in order:
  1. the setup page (a link typed in there)
  2. Docker labels on the container: battlebridge.url, or battlebridge.port
     with battlebridge.path and battlebridge.scheme
  3. built-in defaults for common images (below)
  4. a quick probe of the container's ports for an HTTP answer
Nothing found means a tile without a link.

The address in a link depends on how the container is reached: its own LAN
address (macvlan or ipvlan), or the server's address and a published port.
"""

import re
import socket
import threading
import time

SAFE_URL = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!&()*+,;=%-]{1,500}")

# Image name (the last part, no tag) -> (label, port, path, scheme).
# port None means "no web page".
KNOWN = {
    "plex": ("Plex", 32400, "/web/", "http"),
    "pms-docker": ("Plex", 32400, "/web/", "http"),
    "jellyfin": ("Jellyfin", 8096, "/", "http"),
    "emby": ("Emby", 8096, "/", "http"),
    "embyserver": ("Emby", 8096, "/", "http"),
    "sonarr": ("Sonarr", 8989, "/", "http"),
    "radarr": ("Radarr", 7878, "/", "http"),
    "prowlarr": ("Prowlarr", 9696, "/", "http"),
    "lidarr": ("Lidarr", 8686, "/", "http"),
    "readarr": ("Readarr", 8787, "/", "http"),
    "bazarr": ("Bazarr", 6767, "/", "http"),
    "tautulli": ("Tautulli", 8181, "/", "http"),
    "transmission": ("Transmission", 9091, "/transmission/web/", "http"),
    "qbittorrent": ("qBittorrent", 8080, "/", "http"),
    "deluge": ("Deluge", 8112, "/", "http"),
    "sabnzbd": ("SABnzbd", 8080, "/", "http"),
    "nzbget": ("NZBGet", 6789, "/", "http"),
    "seerr": ("Seerr", 5055, "/", "http"),
    "overseerr": ("Overseerr", 5055, "/", "http"),
    "jellyseerr": ("Jellyseerr", 5055, "/", "http"),
    "ombi": ("Ombi", 3579, "/", "http"),
    "pihole": ("Pi-hole", 80, "/admin/", "http"),
    "adguardhome": ("AdGuard Home", 80, "/", "http"),
    "nginx-proxy-manager": ("Nginx Proxy Manager", 81, "/", "http"),
    "portainer-ce": ("Portainer", 9443, "/", "https"),
    "portainer-ee": ("Portainer", 9443, "/", "https"),
    "home-assistant": ("Home Assistant", 8123, "/", "http"),
    "homeassistant": ("Home Assistant", 8123, "/", "http"),
    "grafana": ("Grafana", 3000, "/", "http"),
    "prometheus": ("Prometheus", 9090, "/", "http"),
    "uptime-kuma": ("Uptime Kuma", 3001, "/", "http"),
    "syncthing": ("Syncthing", 8384, "/", "http"),
    "nextcloud": ("Nextcloud", 80, "/", "http"),
    "vaultwarden": ("Vaultwarden", 80, "/", "http"),
    "immich-server": ("Immich", 2283, "/", "http"),
    "paperless-ngx": ("Paperless-ngx", 8000, "/", "http"),
    "audiobookshelf": ("Audiobookshelf", 80, "/", "http"),
    "navidrome": ("Navidrome", 4533, "/", "http"),
    "photoprism": ("PhotoPrism", 2342, "/", "http"),
    "code-server": ("code-server", 8443, "/", "http"),
    "gitea": ("Gitea", 3000, "/", "http"),
    "filebrowser": ("File Browser", 80, "/", "http"),
    "netdata": ("Netdata", 19999, "/", "http"),
    "dozzle": ("Dozzle", 8080, "/", "http"),
    "heimdall": ("Heimdall", 80, "/", "http"),
    "unifi-network-application": ("UniFi Network", 8443, "/", "https"),
    "speedtest-tracker": ("Speedtest Tracker", 80, "/", "http"),
    "mealie": ("Mealie", 9000, "/", "http"),
    "freshrss": ("FreshRSS", 80, "/", "http"),
    "calibre-web": ("Calibre-Web", 8083, "/", "http"),
    "kavita": ("Kavita", 5000, "/", "http"),
    "wireguard-ui": ("WireGuard UI", 5000, "/", "http"),
    # No web page of their own.
    "cloudflare-ddns": ("Cloudflare DDNS", None, None, None),
    "socket-proxy": ("Docker proxy", None, None, None),
    "docker-socket-proxy": ("Docker proxy", None, None, None),
    "watchtower": ("Watchtower", None, None, None),
    "cloudflared": ("Cloudflare Tunnel", None, None, None),
    "postgres": ("PostgreSQL", None, None, None),
    "mariadb": ("MariaDB", None, None, None),
    "mysql": ("MySQL", None, None, None),
    "redis": ("Redis", None, None, None),
    "valkey": ("Valkey", None, None, None),
    "battle-bridge": ("Battle Bridge", None, None, None),
}

# Service kind -> image names it's found by.  The first running container
# with one of these images is the one Battle Bridge talks to.
SERVICES = {
    "plex": ("plex", "pms-docker"),
    "jellyfin": ("jellyfin",),
    "sonarr": ("sonarr",),
    "radarr": ("radarr",),
    "prowlarr": ("prowlarr",),
    "transmission": ("transmission",),
    "seerr": ("seerr", "overseerr", "jellyseerr"),
    "pihole": ("pihole",),
    "ddns": ("cloudflare-ddns",),
    "npm": ("nginx-proxy-manager",),
}

# Images updated last by "Update all", on their own: if DNS goes down
# mid-update, pulling everything else fails.  The label
# battlebridge.update=last does the same for any container.
UPDATE_LAST = ("pihole", "adguardhome", "unbound", "bind9", "technitium")


def image_name(image):
    """'lscr.io/linuxserver/plex:latest' -> 'plex'."""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        last = last.split(":", 1)[0]
    return last.lower()


def known(container):
    return KNOWN.get(image_name(container.get("image", "")))


def label(container):
    lab = (container.get("labels") or {}).get("battlebridge.label")
    if lab:
        return lab[:60]
    k = known(container)
    return k[0] if k else container["name"]


def service_kind(container):
    name = image_name(container.get("image", ""))
    for kind, images in SERVICES.items():
        if name in images:
            return kind
    return None


def find_service(containers, kind):
    """The container for a service kind: a running one first."""
    hits = [c for c in containers or [] if service_kind(c) == kind]
    hits.sort(key=lambda c: (c["state"] != "running", c["name"]))
    return hits[0] if hits else None


def update_last(container):
    if (container.get("labels") or {}).get("battlebridge.update") == "last":
        return True
    return image_name(container.get("image", "")) in UPDATE_LAST


# --- addresses ------------------------------------------------------------------

def _host(host):
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _join(scheme, host, port, path):
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    return f"{scheme}://{_host(host)}{'' if default else f':{port}'}{path or '/'}"


def address(container, port, server, scheme="http", path="/"):
    """Where a container's port can be reached from `server`'s point of view:
    its own LAN address, or the server's address and the published port."""
    if container.get("host_network"):
        return _join(scheme, server, port, path)
    for p in container.get("ports") or []:
        if p["private"] == port and p["public"]:
            return _join(scheme, server, p["public"], path)
    if container.get("ip"):
        return _join(scheme, container["ip"], port, path)
    return None


def service_url(container, server):
    """Where Battle Bridge itself reaches a service's API, or None."""
    target = web_target(container)
    if not target:
        return None
    url = address(container, target[0], server, target[2], "/")
    return url.rstrip("/") if url else None


def web_target(container):
    """(port, path, scheme) from labels or the built-in list, or None."""
    lab = container.get("labels") or {}
    k = known(container)
    port = lab.get("battlebridge.port")
    if port:
        try:
            port = int(port)
        except ValueError:
            port = None
    elif k:
        port = k[1]
    if not port:
        return None
    path = lab.get("battlebridge.path") or (k[2] if k and k[1] == port else "/") or "/"
    if not path.startswith("/"):
        path = "/" + path
    scheme = lab.get("battlebridge.scheme") or (k[3] if k and k[1] == port else None) or (
        "https" if port in (443, 8443, 9443) else "http")
    return port, path, scheme if scheme in ("http", "https") else "http"


def tile_link(container, server, overrides=None, probed=None):
    """The link a browser should open for this container, or None."""
    name = container["name"]
    o = (overrides or {}).get(name)
    if o:
        return None if o == "none" else o.replace("{server}", _host(server))
    lab = container.get("labels") or {}
    if lab.get("battlebridge.link", "").lower() in ("none", "false", "no", "off"):
        return None
    if lab.get("battlebridge.url"):
        # Labels can come baked into an image, so only plain web links count.
        url = lab["battlebridge.url"].replace("{server}", _host(server))
        return url if SAFE_URL.fullmatch(url) else None
    target = web_target(container)
    if target:
        port, path, scheme = target
        return address(container, port, server, scheme, path)
    k = known(container)
    if k and k[1] is None:
        return None
    hit = (probed or {}).get(container["id"])
    if hit:
        port, scheme = hit
        return address(container, port, server, scheme, "/")
    return None


# --- the probe -------------------------------------------------------------------

class Prober:
    """Looks for an HTTP answer on the ports of containers that have no
    label and aren't in the built-in list.  Each port is tried once an hour
    at most, in the background, so a slow port never holds up the page."""

    def __init__(self, gateway, timeout=1.5):
        self.gateway = gateway
        self.timeout = timeout
        self.found = {}       # container id -> (private port, scheme)
        self._tried = {}      # (container id, port) -> time
        self._lock = threading.Lock()
        self._busy = False

    def results(self):
        with self._lock:
            return dict(self.found)

    def run(self, containers):
        """Start a probe pass if one isn't already running."""
        with self._lock:
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._pass, args=(list(containers),), daemon=True).start()

    def _pass(self, containers):
        try:
            now = time.time()
            for c in containers:
                if c["state"] != "running" or web_target(c) or (known(c) and known(c)[1] is None):
                    continue
                lab = c.get("labels") or {}
                if lab.get("battlebridge.url") or lab.get("battlebridge.link"):
                    continue
                with self._lock:
                    if c["id"] in self.found:
                        continue
                for p in c.get("ports") or []:
                    key = (c["id"], p["private"])
                    if now - self._tried.get(key, 0) < 3600:
                        continue
                    self._tried[key] = now
                    host = address(c, p["private"], self.gateway)
                    if not host:
                        continue
                    hostport = host.split("://", 1)[1].rstrip("/")
                    scheme = probe(hostport, self.timeout)
                    if scheme:
                        with self._lock:
                            self.found[c["id"]] = (p["private"], scheme)
                        break
        finally:
            with self._lock:
                self._busy = False


def probe(hostport, timeout=1.5):
    """'http' or 'https' if something answers like a web server, else None."""
    m = re.fullmatch(r"\[?([^\]]+?)\]?(?::(\d+))?", hostport)
    if not m:
        return None
    host, port = m.group(1), int(m.group(2) or 80)
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"GET / HTTP/1.0\r\nHost: " + _host(host).encode() + b"\r\n\r\n")
            head = s.recv(16)
    except OSError:
        return None
    if head.startswith(b"HTTP/"):
        return "http"
    if head[:1] == b"\x15" or head[:1] == b"\x16":   # a TLS alert: it wants https
        return "https"
    return None
