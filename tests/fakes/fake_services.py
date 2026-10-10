"""One fake server answering as Plex, Jellyfin, Sonarr, Radarr, Prowlarr,
Transmission, Seerr, Pi-hole v6, and UniFi.  Paths start with the service
name, e.g. /plex/status/sessions.  Keys are checked, like the real thing."""

import json
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

KEY = "test-key"
SID = "transmission-sid"
now = datetime.now(timezone.utc)


def iso(d):
    return (now + timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")


PLEX = {"MediaContainer": {"size": 2, "Metadata": [
    {"type": "episode", "grandparentTitle": "The Expanse", "parentIndex": 3, "index": 4,
     "title": "x", "viewOffset": 1200000, "duration": 3000000, "User": {"title": "Sam"},
     "Player": {"state": "playing", "product": "Plex for Android TV"},
     "TranscodeSession": {"videoDecision": "transcode", "transcodeHwFullPipeline": True}},
    {"type": "movie", "title": "Arrival", "viewOffset": 100, "duration": 1000,
     "User": {"title": "Alex"}, "Player": {"state": "paused", "product": "Plex Web"}}]}}
JELLY = [{"UserName": "Kids", "Client": "Jellyfin Android",
          "NowPlayingItem": {"Name": "Bluey", "Type": "Movie", "RunTimeTicks": 1000},
          "PlayState": {"PositionTicks": 400, "IsPaused": False, "PlayMethod": "DirectPlay"}},
         {"UserName": "idle", "Client": "TV"}]
HEALTH = [{"type": "warning", "message": "Indexer NZBgeek is unavailable due to failures"}]
SONARR_Q = {"totalRecords": 2, "records": [
    {"series": {"title": "Severance"}, "episode": {"seasonNumber": 5, "episodeNumber": 9, "title": "x"},
     "size": 1000, "sizeleft": 250, "timeleft": "00:04:10", "trackedDownloadState": "downloading", "trackedDownloadStatus": "ok"},
    {"title": "Some.Show.S01E01", "size": 1000, "sizeleft": 1000, "trackedDownloadState": "importPending",
     "trackedDownloadStatus": "warning"}]}
SONARR_CAL = [{"series": {"title": "Slow Horses"}, "seasonNumber": 4, "episodeNumber": 1, "title": "x",
               "airDateUtc": iso(2), "hasFile": False},
              {"series": {"title": "Old"}, "seasonNumber": 1, "episodeNumber": 1, "airDateUtc": iso(1), "hasFile": True}]
RADARR_Q = {"totalRecords": 1, "records": [{"movie": {"title": "Dune: Part Two"}, "size": 4000, "sizeleft": 1000,
                                            "timeleft": "00:12:00", "trackedDownloadState": "downloading"}]}
RADARR_CAL = [{"title": "The Martian", "digitalRelease": iso(5), "hasFile": False}]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, headers=None):
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        p = urlsplit(self.path).path
        h = self.headers
        if p.startswith("/redirect/"):
            # Like a service sending its API somewhere else.  Keys mustn't follow.
            return self.send(302, b"", {"Location": "http://127.0.0.1:1/stolen"})
        if p == "/plex/status/sessions":
            return self.send(200, PLEX) if h.get("X-Plex-Token") == KEY else self.send(401, {})
        if p == "/jellyfin/Sessions":
            return self.send(200, JELLY) if KEY in (h.get("Authorization") or "") else self.send(401, {})
        for kind in ("sonarr", "radarr", "prowlarr"):
            if p.startswith(f"/{kind}/"):
                if h.get("X-Api-Key") != KEY:
                    return self.send(401, {})
                if p.endswith("/health"):
                    return self.send(200, HEALTH if kind == "prowlarr" else [])
                if p.endswith("/queue"):
                    return self.send(200, SONARR_Q if kind == "sonarr" else RADARR_Q)
                if p.endswith("/calendar"):
                    return self.send(200, SONARR_CAL if kind == "sonarr" else RADARR_CAL)
        if p == "/seerr/api/v1/request/count":
            return self.send(200, {"total": 40, "pending": 2, "approved": 30, "processing": 3, "available": 25, "declined": 1})
        if p == "/pihole/api/stats/summary":
            if h.get("X-FTL-SID") != "pihole-sid":
                return self.send(401, {})
            return self.send(200, {"queries": {"total": 48211, "blocked": 9120, "percent_blocked": 18.9},
                                   "clients": {"active": 23}, "gravity": {"domains_being_blocked": 1203442}})
        if p.startswith("/unifi/"):
            if h.get("X-API-KEY") != KEY:
                return self.send(401, {})
            if p.endswith("/integration/v1/sites"):
                return self.send(200, {"data": [{"id": "site1", "name": "Default"}]})
            if p.endswith("/clients"):
                return self.send(200, {"totalCount": 31, "data": []})
            if p.endswith("/devices"):
                return self.send(200, {"data": [
                    {"id": "gw", "name": "Gateway", "model": "UCG-Ultra", "state": "ONLINE", "features": ["gateway", "switching"]},
                    {"id": "ap", "name": "Lounge AP", "model": "U7-Lite", "state": "ONLINE", "features": ["accessPoint"]}]})
            if p.endswith("/statistics/latest"):
                return self.send(200, {"uptimeSec": 1209600, "cpuUtilizationPct": 12.0, "memoryUtilizationPct": 61.0,
                                       "uplink": {"txRateBps": 2400000, "rxRateBps": 38000000}})
            if p.endswith("/stat/health"):
                return self.send(200, {"data": [{"subsystem": "wan", "wan_ip": "203.0.113.7", "isp_name": "Example ISP"},
                                                {"subsystem": "www", "latency": 9}]})
        self.send(404, {})

    def do_POST(self):
        p = urlsplit(self.path).path
        if p == "/pihole/api/auth":
            ok = self.body().get("password") == KEY
            return self.send(200, {"session": {"valid": ok, "sid": "pihole-sid" if ok else None, "validity": 1800}})
        if p == "/transmission/transmission/rpc":
            if self.headers.get("Authorization") != "Basic dXNlcjp0ZXN0LWtleQ==":   # user:test-key
                return self.send(401, {})
            if self.headers.get("X-Transmission-Session-Id") != SID:
                return self.send(409, b"", {"X-Transmission-Session-Id": SID})
            req = self.body()
            if req["method"] == "session-stats":
                return self.send(200, {"result": "success", "arguments": {
                    "downloadSpeed": 4_200_000, "uploadSpeed": 310_000, "activeTorrentCount": 3,
                    "pausedTorrentCount": 1, "torrentCount": 27}})
            return self.send(200, {"result": "success", "arguments": {"torrents": [
                {"name": "ubuntu-26.04-desktop-amd64.iso", "percentDone": 0.62, "rateDownload": 3_900_000, "status": 4, "eta": 410, "error": 0},
                {"name": "archlinux-2026.10.01-x86_64.iso", "percentDone": 0.95, "rateDownload": 300_000, "status": 4, "eta": 60, "error": 0},
                {"name": "seeding thing", "percentDone": 1, "rateDownload": 0, "status": 6, "error": 0}]}})
        self.send(404, {})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
