"""Everything set on the setup page, kept in the database.

Settings are one JSON document with a revision number.  A save must quote
the revision it was based on, so an old tab can't overwrite newer changes.
Keys and passwords for other services are kept apart from it, are never
sent to the browser, and can only be replaced or cleared.
"""

import copy
import ipaddress
import json
import re
import threading
import time

DEFAULTS = {
    # Home network only.  Private ranges, so it works out of the box.
    "allowed_nets": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                     "fc00::/7", "fe80::/10", "::1/128"],
    # Addresses are always allowed.  Hostnames only if listed here.
    "allowed_hosts": [],
    # The address tiles use for ports published on the server.  Blank means
    # "whatever address the browser used to reach this page".
    "server_address": "",
    "net_iface": "",
    # Container name -> its web page, or "none" for no link.  Overrides
    # labels and the built-in defaults.
    "links": {},
    # Service kind -> {"url": ...}.  Where Battle Bridge itself reaches it,
    # when the address it finds doesn't work.
    "services": {},
    "unifi": {"url": "", "fingerprint": ""},
    "pc": {"terminal": "konsole", "ssh_target": "", "ssh_port": 22, "folders": ["~"]},
}

# Secret name -> the label the setup page shows.  Grouped by service.
SECRETS = {
    "plex_token": ("plex", "Plex token"),
    "jellyfin_key": ("jellyfin", "Jellyfin API key"),
    "sonarr_key": ("sonarr", "Sonarr API key"),
    "radarr_key": ("radarr", "Radarr API key"),
    "prowlarr_key": ("prowlarr", "Prowlarr API key"),
    "transmission_user": ("transmission", "Transmission username"),
    "transmission_pass": ("transmission", "Transmission password"),
    "seerr_key": ("seerr", "Seerr API key"),
    "pihole_password": ("pihole", "Pi-hole app password"),
    "unifi_key": ("unifi", "UniFi API key"),
}

TERMINALS = {
    "konsole": "Konsole (KDE)",
    "gnome-terminal": "GNOME Terminal",
    "ptyxis": "Ptyxis (GNOME, Fedora)",
    "xfce4-terminal": "Xfce Terminal",
    "kitty": "kitty",
    "alacritty": "Alacritty",
    "foot": "foot",
    "wezterm": "WezTerm",
    "xterm": "xterm",
}

SSH_TARGET = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}@[A-Za-z0-9.:-]{1,253}$")
# Folders end up inside single quotes in a command run over SSH, so quotes,
# dollars, backticks, backslashes and newlines are refused outright.
FOLDER = re.compile(r"^(~|~/[A-Za-z0-9 ._@+,/-]*|/[A-Za-z0-9 ._@+,/-]*)$")
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
SERVICE_KINDS = ("plex", "jellyfin", "sonarr", "radarr", "prowlarr", "transmission", "seerr", "pihole")


class Invalid(ValueError):
    pass


class Stale(Exception):
    pass


class Store:
    def __init__(self, db):
        self.db = db
        self.lock = threading.Lock()
        self._cache = None
        self._nets = None
        self._loaded = 0

    # --- settings -----------------------------------------------------------

    def settings(self):
        """A copy of the current settings, with defaults filled in."""
        with self.lock:
            # Re-read now and then, so a change made by a command in the
            # container (reset-access) takes effect without a restart.
            if self._cache is None or time.monotonic() - self._loaded > 5:
                self._loaded = time.monotonic()
                raw = self.db.get_setting("settings")
                try:
                    data = json.loads(raw) if raw else {}
                except ValueError:
                    data = {}
                merged = copy.deepcopy(DEFAULTS)
                for k, v in data.items():
                    if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                        merged[k].update(v)
                    else:
                        merged[k] = v
                merged.setdefault("rev", 0)
                self._cache = merged
                self._nets = [ipaddress.ip_network(n, strict=False) for n in merged["allowed_nets"]]
            return copy.deepcopy(self._cache)

    def save(self, changes, rev):
        """Apply validated changes.  Raises Stale or Invalid."""
        current = self.settings()
        if rev != current["rev"]:
            raise Stale()
        new = copy.deepcopy(current)
        for key, value in changes.items():
            if key not in DEFAULTS:
                raise Invalid(f"Unknown setting {key}.")
            new[key] = VALIDATORS[key](value)
        new["rev"] = current["rev"] + 1
        with self.lock:
            self.db.set_setting("settings", json.dumps(new))
            self._cache = None
        return self.settings()

    def reset_access(self):
        """Back to the default networks and no hostnames.  For the
        reset-access command, when a bad setting locks you out."""
        s = self.settings()
        s["allowed_nets"] = list(DEFAULTS["allowed_nets"])
        s["allowed_hosts"] = []
        s["rev"] += 1
        with self.lock:
            self.db.set_setting("settings", json.dumps(s))
            self._cache = None

    # --- access rules -------------------------------------------------------

    def client_allowed(self, ip):
        self.settings()
        try:
            addr = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in self._nets if net.version == addr.version)

    def host_allowed(self, host_header):
        """The page must be asked for by address, or by a hostname that's
        been allowed on the setup page.  This stops DNS rebinding."""
        host = split_host(host_header)
        if host is None:
            return False
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return host in self.settings()["allowed_hosts"]

    # --- secrets ------------------------------------------------------------

    def secret(self, name):
        return self.db.get_setting("secret." + name) or ""

    def set_secret(self, name, value):
        if name not in SECRETS:
            raise Invalid("Unknown key.")
        if value:
            if not isinstance(value, str) or len(value) > 500 or any(c in value for c in "\r\n"):
                raise Invalid("That doesn't look like a key.")
            self.db.set_setting("secret." + name, value.strip())
        else:
            self.db.conn().execute("DELETE FROM settings WHERE key = ?", ("secret." + name,))

    def secrets_set(self):
        """Which secrets have a value.  Booleans only: values never leave."""
        return {name: bool(self.secret(name)) for name in SECRETS}


def split_host(host_header):
    """'192.168.1.5:8080' -> '192.168.1.5', '[fd00::1]:80' -> 'fd00::1'."""
    if not host_header:
        return None
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        if end < 0:
            return None
        rest = h[end + 1:]
        if rest and not re.fullmatch(r":\d{1,5}", rest):
            return None
        return h[1:end]
    if h.count(":") == 1:
        h, port = h.split(":")
        if not port.isdigit():
            return None
    elif h.count(":") > 1:
        return None
    return h or None


# --- validators: each returns a clean value or raises Invalid ------------------

def _nets(v):
    if not isinstance(v, list) or not v:
        raise Invalid("List at least one network, or you'd lock yourself out.")
    out = []
    for n in v:
        try:
            out.append(str(ipaddress.ip_network(str(n).strip(), strict=False)))
        except ValueError:
            raise Invalid(f"{n} isn't a network.  Use a form like 192.168.1.0/24.") from None
    return out


def _hosts(v):
    if not isinstance(v, list):
        raise Invalid("Hostnames must be a list.")
    out = []
    for h in v:
        h = str(h).strip().lower()
        if not h:
            continue
        if not HOSTNAME.match(h):
            raise Invalid(f"{h} isn't a hostname.")
        out.append(h)
    return out


def _server(v):
    v = str(v or "").strip()
    if not v:
        return ""
    try:
        ipaddress.ip_address(v.strip("[]"))
        return v
    except ValueError:
        pass
    if not HOSTNAME.match(v.lower()):
        raise Invalid("The server address should be an address or a hostname.")
    return v.lower()


def _iface(v):
    v = str(v or "").strip()
    if v and not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,32}", v):
        raise Invalid("That isn't a network interface name.")
    return v


def _url(v, what):
    v = str(v or "").strip()
    if not v:
        return ""
    if not re.fullmatch(r"https?://[A-Za-z0-9._~:/?#\[\]@!&()*+,;=%{}-]{1,500}", v):
        raise Invalid(f"{what} must be a full http:// or https:// address.")
    return v


def _links(v):
    if not isinstance(v, dict):
        raise Invalid("Bad links.")
    out = {}
    for name, url in v.items():
        name = str(name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise Invalid(f"{name} isn't a container name.")
        url = str(url or "").strip()
        if not url:
            continue
        out[name] = "none" if url == "none" else _url(url, f"The link for {name}")
    return out


def _services(v):
    if not isinstance(v, dict):
        raise Invalid("Bad services.")
    out = {}
    for kind, conf in v.items():
        if kind not in SERVICE_KINDS or not isinstance(conf, dict):
            raise Invalid(f"Unknown service {kind}.")
        url = _url(conf.get("url"), f"The address for {kind}").rstrip("/")
        if url:
            out[kind] = {"url": url}
    return out


def _unifi(v):
    if not isinstance(v, dict):
        raise Invalid("Bad UniFi settings.")
    url = _url(v.get("url"), "The UniFi address").rstrip("/")
    fp = str(v.get("fingerprint") or "").replace(":", "").strip().lower()
    if fp and not re.fullmatch(r"[0-9a-f]{64}", fp):
        raise Invalid("The certificate fingerprint should be 64 hex characters.")
    return {"url": url, "fingerprint": fp}


def _pc(v):
    if not isinstance(v, dict):
        raise Invalid("Bad PC settings.")
    term = v.get("terminal") or "konsole"
    if term not in TERMINALS:
        raise Invalid("Pick a terminal from the list.")
    target = str(v.get("ssh_target") or "").strip()
    if target and not SSH_TARGET.match(target):
        raise Invalid("The SSH login should look like you@192.168.1.10.")
    try:
        port = int(v.get("ssh_port") or 22)
    except (TypeError, ValueError):
        raise Invalid("The SSH port should be a number.") from None
    if not 1 <= port <= 65535:
        raise Invalid("The SSH port should be between 1 and 65535.")
    folders = []
    for f in v.get("folders") or []:
        f = str(f).strip()
        if not f:
            continue
        if not FOLDER.match(f) or "/../" in f + "/" or len(f) > 300:
            raise Invalid(f"{f} can't be used.  Folders must start with / or ~, "
                          "and can't hold quotes, $, backslashes or backticks.")
        if f not in folders:
            folders.append(f)
    if len(folders) > 40:
        raise Invalid("Forty folders at most.")
    return {"terminal": term, "ssh_target": target, "ssh_port": port, "folders": folders or ["~"]}


VALIDATORS = {
    "allowed_nets": _nets,
    "allowed_hosts": _hosts,
    "server_address": _server,
    "net_iface": _iface,
    "links": _links,
    "services": _services,
    "unifi": _unifi,
    "pc": _pc,
}
