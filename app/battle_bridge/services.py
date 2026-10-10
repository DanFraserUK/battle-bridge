"""Clients for the other services' APIs.  Server-side only: keys come from
the setup page and never reach the browser.  Each one returns plain data for
the page, or raises ServiceError with a message that is safe to show (no keys,
no URLs with tokens in them).
"""

import base64
import hashlib
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


class ServiceError(Exception):
    pass


class NotConfigured(ServiceError):
    pass


def _request(url, method="GET", headers=None, body=None, timeout=6, context=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json", "User-Agent": "battle-bridge"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
            raw = r.read()
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ServiceError(f"no answer ({reason})") from None


def get_json(url, headers=None, timeout=6, context=None, method="GET", body=None):
    status, _, raw = _request(url, method, headers, body, timeout, context)
    if status in (401, 403):
        raise ServiceError(f"refused the key ({status}).  Check it on the setup page")
    if status >= 400:
        raise ServiceError(f"error {status}")
    try:
        return json.loads(raw or b"null")
    except ValueError:
        raise ServiceError("answered, but not with JSON") from None


def _need(*values):
    if not all(values):
        raise NotConfigured("no key set yet")


# --- Plex --------------------------------------------------------------------

def plex(base, token):
    _need(token)
    d = get_json(f"{base}/status/sessions", {"X-Plex-Token": token})
    out = []
    for m in (d.get("MediaContainer") or {}).get("Metadata") or []:
        t = m.get("TranscodeSession") or {}
        decision = t.get("videoDecision") or t.get("audioDecision")
        out.append({
            "user": (m.get("User") or {}).get("title") or "?",
            "title": _title(m.get("grandparentTitle"), m.get("parentIndex"), m.get("index"), m.get("title"), m.get("type")),
            "state": (m.get("Player") or {}).get("state") or "?",
            "device": (m.get("Player") or {}).get("product") or (m.get("Player") or {}).get("title") or "",
            "transcode": decision == "transcode",
            "hw": bool(t.get("transcodeHwFullPipeline") or t.get("transcodeHwEncoding")),
            "progress": _pct(m.get("viewOffset"), m.get("duration")),
        })
    return {"sessions": out}


def _title(show, season, episode, title, kind):
    if kind == "episode" and show:
        if season is not None and episode is not None:
            return f"{show} S{int(season):02d}E{int(episode):02d}"
        return f"{show}: {title}"
    return title or "?"


def _pct(pos, total):
    try:
        return round(100 * float(pos) / float(total))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# --- Jellyfin ----------------------------------------------------------------

def jellyfin(base, key):
    _need(key)
    d = get_json(f"{base}/Sessions?activeWithinSeconds=960",
                 {"Authorization": f'MediaBrowser Token="{key}"'})
    out = []
    for s in d or []:
        item = s.get("NowPlayingItem")
        if not item:
            continue
        ps = s.get("PlayState") or {}
        ti = s.get("TranscodingInfo") or {}
        out.append({
            "user": s.get("UserName") or "?",
            "title": _title(item.get("SeriesName"), item.get("ParentIndexNumber"), item.get("IndexNumber"),
                            item.get("Name"), "episode" if item.get("Type") == "Episode" else "movie"),
            "state": "paused" if ps.get("IsPaused") else "playing",
            "device": s.get("Client") or s.get("DeviceName") or "",
            "transcode": ps.get("PlayMethod") == "Transcode",
            "hw": bool(ti.get("HardwareAccelerationType")),
            "progress": _pct(ps.get("PositionTicks"), item.get("RunTimeTicks")),
        })
    return {"sessions": out}


# --- Sonarr / Radarr / Prowlarr --------------------------------------------------

def arr(base, key, kind):
    _need(key)
    h = {"X-Api-Key": key}
    ver = "v1" if kind == "prowlarr" else "v3"
    health = get_json(f"{base}/api/{ver}/health", h)
    out = {"health": [{"type": x.get("type"), "message": (x.get("message") or "")[:160]} for x in health or []
                      if x.get("type") in ("warning", "error")]}
    if kind == "prowlarr":
        return out
    q = get_json(f"{base}/api/v3/queue?pageSize=50&includeUnknownSeriesItems=true"
                 + ("&includeSeries=true&includeEpisode=true" if kind == "sonarr" else "&includeMovie=true"), h)
    items = []
    for r in (q or {}).get("records") or []:
        items.append({
            "title": _arr_title(r, kind),
            "status": r.get("trackedDownloadState") or r.get("status") or "",
            "warn": r.get("trackedDownloadStatus") in ("warning", "error"),
            "progress": _pct((r.get("size") or 0) - (r.get("sizeleft") or 0), r.get("size")),
            "timeleft": r.get("timeleft"),
        })
    out["queue"] = items
    out["queue_total"] = (q or {}).get("totalRecords", len(items))
    now = datetime.now(timezone.utc)
    start, end = now.strftime("%Y-%m-%d"), (now + timedelta(days=7)).strftime("%Y-%m-%d")
    cal = get_json(f"{base}/api/v3/calendar?start={start}&end={end}"
                   + ("&includeSeries=true" if kind == "sonarr" else ""), h)
    upcoming = []
    for c in cal or []:
        if c.get("hasFile"):
            continue
        if kind == "sonarr":
            when = c.get("airDateUtc")
            title = _title((c.get("series") or {}).get("title"), c.get("seasonNumber"), c.get("episodeNumber"),
                           c.get("title"), "episode")
        else:
            when = c.get("digitalRelease") or c.get("physicalRelease") or c.get("inCinemas")
            title = c.get("title") or "?"
        upcoming.append({"title": title, "when": _iso(when)})
    upcoming.sort(key=lambda x: x["when"] or 0)
    out["upcoming"] = upcoming[:12]
    return out


def _arr_title(r, kind):
    if kind == "sonarr":
        s = (r.get("series") or {}).get("title")
        e = r.get("episode") or {}
        if s and e:
            return _title(s, e.get("seasonNumber"), e.get("episodeNumber"), e.get("title"), "episode")
    if kind == "radarr":
        m = (r.get("movie") or {}).get("title")
        if m:
            return m
    return r.get("title") or "?"


def _iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --- Transmission ------------------------------------------------------------

class Transmission:
    """RPC needs a session id, handed out on a 409.  Kept between calls."""

    def __init__(self):
        self.sid = None

    def fetch(self, base, user, password):
        # Transmission often runs with no login at all.  Then no header is sent.
        auth = base64.b64encode(f"{user}:{password}".encode()).decode() if password else None
        stats = self._call(base, auth, "session-stats", {})
        tor = self._call(base, auth, "torrent-get", {
            "fields": ["name", "percentDone", "rateDownload", "rateUpload", "status", "eta", "error"]})
        active = []
        errored = 0
        for t in tor.get("torrents") or []:
            if t.get("error"):
                errored += 1
            if t.get("status") in (3, 4) or t.get("rateDownload"):   # queued to download, downloading
                active.append({"name": (t.get("name") or "?")[:120], "progress": round(100 * (t.get("percentDone") or 0)),
                               "down": t.get("rateDownload") or 0, "eta": t.get("eta")})
        active.sort(key=lambda x: -x["down"])
        return {"down": stats.get("downloadSpeed", 0), "up": stats.get("uploadSpeed", 0),
                "active": stats.get("activeTorrentCount", 0), "total": stats.get("torrentCount", 0),
                "paused": stats.get("pausedTorrentCount", 0), "errored": errored, "downloading": active[:8]}

    def _call(self, base, auth, method, args):
        url = f"{base}/transmission/rpc"
        for _ in range(2):
            headers = {"Authorization": f"Basic {auth}"} if auth else {}
            if self.sid:
                headers["X-Transmission-Session-Id"] = self.sid
            status, h, raw = _request(url, "POST", headers, {"method": method, "arguments": args})
            if status == 409:
                self.sid = h.get("X-Transmission-Session-Id") or h.get("x-transmission-session-id")
                continue
            if status == 401:
                raise ServiceError("refused the username or password.  Check them on the setup page")
            if status >= 400:
                raise ServiceError(f"error {status}")
            d = json.loads(raw)
            if d.get("result") != "success":
                raise ServiceError(str(d.get("result"))[:100])
            return d.get("arguments") or {}
        raise ServiceError("no session id")


# --- Seerr -------------------------------------------------------------------

def seerr(base, key):
    _need(key)
    c = get_json(f"{base}/api/v1/request/count", {"X-Api-Key": key})
    return {k: c.get(k, 0) for k in ("pending", "approved", "processing", "available", "declined", "total")}


# --- Pi-hole v6 ----------------------------------------------------------------

class PiHole:
    """Logs in once with the app password and reuses the session, because
    Pi-hole has a small number of session seats."""

    def __init__(self):
        self.sid = None
        self.until = 0

    def fetch(self, base, password):
        _need(password)
        for attempt in range(2):
            if not self.sid or time.time() > self.until:
                self._login(base, password)
            status, _, raw = _request(f"{base}/api/stats/summary", headers={"X-FTL-SID": self.sid})
            if status == 401 and attempt == 0:
                self.sid = None
                continue
            if status >= 400:
                raise ServiceError(f"error {status}")
            d = json.loads(raw)
            q = d.get("queries") or {}
            return {"total": q.get("total"), "blocked": q.get("blocked"),
                    "percent": q.get("percent_blocked"),
                    "clients": (d.get("clients") or {}).get("active"),
                    "domains": (d.get("gravity") or {}).get("domains_being_blocked")}
        raise ServiceError("session refused")

    def _login(self, base, password):
        d = get_json(f"{base}/api/auth", method="POST", body={"password": password})
        s = (d or {}).get("session") or {}
        if not s.get("valid") or not s.get("sid"):
            raise ServiceError("refused the app password.  Check it on the setup page")
        self.sid = s["sid"]
        self.until = time.time() + max(60, int(s.get("validity") or 300) - 30)


# --- UniFi -------------------------------------------------------------------

def unifi_context():
    """The gateway's certificate is self-signed, so it can't be checked the
    usual way.  _check_fingerprint pins it instead, once pinned on the setup page."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fingerprint(url, timeout=5):
    """The sha256 of the certificate a https:// address presents."""
    host = url.split("://", 1)[1].split("/", 1)[0]
    port = 443
    if host.startswith("["):
        host, _, rest = host[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else 443
    elif ":" in host:
        host, port = host.rsplit(":", 1)
    ctx = unifi_context()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ValueError) as e:
        raise ServiceError(f"no answer ({e})") from None
    return hashlib.sha256(der).hexdigest()


def _check_fingerprint(url, fingerprint_, timeout=5):
    if not fingerprint_:
        return
    got = fingerprint(url, timeout)
    want = fingerprint_.replace(":", "").lower()
    if got != want:
        raise ServiceError("the gateway's certificate has changed.  Not sending the key")


def unifi(base, key, pin=""):
    _need(key)
    _check_fingerprint(base, pin)
    ctx = unifi_context()
    h = {"X-API-KEY": key}
    api = f"{base}/proxy/network/integration/v1"
    sites = get_json(f"{api}/sites", h, context=ctx)
    site = ((sites or {}).get("data") or [{}])[0]
    sid = site.get("id")
    if not sid:
        raise ServiceError("no site found")
    clients = get_json(f"{api}/sites/{sid}/clients?limit=1", h, context=ctx)
    devices = get_json(f"{api}/sites/{sid}/devices?limit=200", h, context=ctx)
    devs = (devices or {}).get("data") or []
    out = {"site": site.get("name") or "Default", "clients": (clients or {}).get("totalCount"),
           "devices": len(devs), "offline": sum(1 for d in devs if (d.get("state") or "").upper() != "ONLINE")}
    gw = next((d for d in devs if "gateway" in [f.lower() for f in d.get("features") or []]
               or (d.get("model") or "").upper().startswith("UCG")), devs[0] if devs else None)
    if gw:
        out["gateway"] = gw.get("name") or gw.get("model")
        try:
            st = get_json(f"{api}/sites/{sid}/devices/{gw['id']}/statistics/latest", h, context=ctx)
            up = (st or {}).get("uplink") or {}
            out.update(uptime=st.get("uptimeSec"), cpu=st.get("cpuUtilizationPct"),
                       mem=st.get("memoryUtilizationPct"), wan_tx=up.get("txRateBps"), wan_rx=up.get("rxRateBps"))
        except ServiceError as e:
            out["stats_error"] = str(e)
    # WAN latency is only in the older API.  Use it when the key is accepted there.
    try:
        hd = get_json(f"{base}/proxy/network/api/s/default/stat/health", h, context=ctx)
        wan = next((x for x in (hd or {}).get("data") or [] if x.get("subsystem") == "wan"), {})
        www = next((x for x in (hd or {}).get("data") or [] if x.get("subsystem") == "www"), {})
        if www.get("latency") is not None:
            out["latency"] = www.get("latency")
        if wan.get("wan_ip"):
            out["wan_ip"] = wan.get("wan_ip")
        if wan.get("isp_name"):
            out["isp"] = wan.get("isp_name")
    except ServiceError:
        pass
    return out


# --- Docker logs (cloudflare-ddns), through the read-only proxy -----------------

def docker_logs(docker_url, container, tail=400):
    status, _, raw = _request(f"{docker_url}/containers/{container}/logs?stdout=1&stderr=1&tail={tail}",
                              headers={"Accept": "*/*"}, timeout=8)
    if status >= 400:
        raise ServiceError(f"error {status}")
    return demux(raw)


def demux(raw):
    """Docker's log stream has an 8-byte header per frame unless it's a TTY."""
    if len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b"\0\0\0":
        out, i = [], 0
        while i + 8 <= len(raw):
            size = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + size])
            i += 8 + size
        raw = b"".join(out)
    return raw.decode("utf-8", "replace")


def ddns_health(log_text):
    lines = log_text.splitlines()
    dom = re.compile(r"[A-Za-z0-9._-]+\.[A-Za-z]{2,}")
    fine, fixed = set(), set()
    for ln in lines:
        if "already up to date" in ln:
            fine.update(d for d in dom.findall(ln) if "." in d)
        if "Updated an outdated" in ln or "Added a new" in ln:
            fixed.update(d for d in dom.findall(ln) if "." in d)
    problems = [ln.strip()[:200] for ln in lines
                if re.search(r"Failed|failed|error|Error|not found|invalid|denied", ln) and "No valid IPv6" not in ln]
    return {"up_to_date": sorted(fine), "corrected": sorted(fixed), "problems": problems[-5:]}
