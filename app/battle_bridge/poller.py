"""Polls the services Battle Bridge knows, each on its own schedule, in one
background thread.

A service is found by its image.  Its address comes live from Docker, or
from the setup page when the found one doesn't work.  Keys come from the
setup page.  Results are kept per service as
{"found", "configured", "data", "error", "checked", "container"}.
"""

import logging
import threading
import time

from . import discovery, services

log = logging.getLogger("battle_bridge.poller")

# kind: seconds between polls
SCHEDULE = {
    "plex": 10,
    "jellyfin": 10,
    "transmission": 10,
    "pihole": 30,
    "unifi": 15,
    "sonarr": 60,
    "radarr": 60,
    "prowlarr": 300,
    "seerr": 60,
    "ddns": 300,
}


class Poller:
    def __init__(self, cfg, sampler, store):
        self.cfg = cfg
        self.sampler = sampler
        self.store = store
        self.results = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._transmission = services.Transmission()
        self._pihole = services.PiHole()

    def start(self):
        threading.Thread(target=self._loop, name="poller", daemon=True).start()

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.results.items()}

    def forget(self, kind):
        """Poll again straight away, after a key or address changes."""
        with self.lock:
            self.results.pop(kind, None)

    def base_url(self, kind, container):
        if kind in self.cfg.url_overrides:
            return self.cfg.url_overrides[kind]
        conf = self.store.settings()["services"].get(kind) or {}
        if conf.get("url"):
            return conf["url"]
        if container is None:
            raise services.ServiceError("not found")
        if container["state"] != "running":
            raise services.ServiceError(f"{container['name']} isn't running")
        url = discovery.service_url(container, self.cfg.host_gateway)
        if not url:
            raise services.ServiceError("no address found.  Set one on the setup page")
        return url

    def _loop(self):
        while not self._stop.is_set():
            for name, every in SCHEDULE.items():
                last = (self.results.get(name) or {}).get("checked", 0)
                if time.time() - last >= every:
                    self.poll(name)
            self._stop.wait(2)

    def poll(self, kind):
        containers = self.sampler.snapshot().get("containers")
        if containers is None and kind != "unifi" and kind not in self.cfg.url_overrides:
            return      # Docker hasn't been read yet: nothing to say about this service
        result = {"checked": time.time(), "data": None, "error": None,
                  "configured": True, "found": True, "container": None}
        key = self.store.secret
        try:
            if kind == "unifi":
                u = self.store.settings()["unifi"]
                url = self.cfg.url_overrides.get("unifi") or u["url"]
                if not url:
                    result["found"] = False
                    raise services.NotConfigured("no address set")
                if not key("unifi_key"):
                    raise services.NotConfigured("no key set yet")
                # The gateway's certificate is self-signed.  Until it's pinned,
                # there's no telling it from an impostor, so the key isn't sent.
                if url.startswith("https://") and not u["fingerprint"]:
                    raise services.NotConfigured("pin its certificate on the setup page")
                result["data"] = services.unifi(url, key("unifi_key"), u["fingerprint"])
            else:
                c = discovery.find_service(containers, kind)
                if c is None and kind not in self.cfg.url_overrides \
                        and not self.store.settings()["services"].get(kind):
                    result["found"] = False
                    raise services.NotConfigured("not found")
                result["container"] = c["name"] if c else None
                if kind == "ddns":
                    if c is None or c["state"] != "running":
                        raise services.ServiceError("isn't running")
                    result["data"] = services.ddns_health(services.docker_logs(self.cfg.docker_url, c["id"]))
                else:
                    self._fetch(kind, c, result, key)
        except services.NotConfigured as e:
            result["configured"] = False
            result["error"] = str(e)
        except services.ServiceError as e:
            result["error"] = str(e)
        except Exception as e:  # a surprise from one service must not stop the rest
            log.exception("%s poll failed", kind)
            result["error"] = f"unexpected answer ({type(e).__name__})"
        with self.lock:
            self.results[kind] = result

    def _fetch(self, kind, c, result, key):
        need = {"plex": "plex_token", "jellyfin": "jellyfin_key", "sonarr": "sonarr_key", "radarr": "radarr_key",
                "prowlarr": "prowlarr_key", "seerr": "seerr_key", "pihole": "pihole_password"}
        if kind in need and not key(need[kind]):
            raise services.NotConfigured("no key set yet")
        base = self.base_url(kind, c)
        if kind == "plex":
            result["data"] = services.plex(base, key("plex_token"))
        elif kind == "jellyfin":
            result["data"] = services.jellyfin(base, key("jellyfin_key"))
        elif kind in ("sonarr", "radarr", "prowlarr"):
            result["data"] = services.arr(base, key(f"{kind}_key"), kind)
        elif kind == "transmission":
            result["data"] = self._transmission.fetch(base, key("transmission_user") or "admin",
                                                      key("transmission_pass"))
        elif kind == "seerr":
            result["data"] = services.seerr(base, key("seerr_key"))
        elif kind == "pihole":
            result["data"] = self._pihole.fetch(base, key("pihole_password"))
