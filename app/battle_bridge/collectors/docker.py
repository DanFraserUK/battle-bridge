"""Containers, from the read-only Docker socket proxy.

Only GET requests are ever made.  The proxy refuses anything else anyway.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Network drivers that give a container its own address on the LAN.
LAN_DRIVERS = ("macvlan", "ipvlan")
# Labels the page has a use for.  The rest stay here.
KEEP_LABELS = ("battlebridge.", "com.docker.compose.project", "com.docker.compose.service")


class DockerError(Exception):
    pass


class DockerCollector:
    def __init__(self, base_url, timeout=5):
        self.base = base_url
        self.timeout = timeout
        self._cpu_prev = {}
        self._net_prev = {}
        self._built = {}        # image id -> built on this machine?
        self.hostname = None

    def _get(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise DockerError(f"{path}: {e}") from e

    def snapshot(self):
        """One entry per container, sorted by LAN address, then name."""
        listed = self._get("/containers/json?all=1")
        if self.hostname is None:
            try:
                self.hostname = self._get("/info").get("Name")
            except DockerError:
                pass
        drivers = {}
        try:
            for n in self._get("/networks"):
                drivers[n.get("Name")] = n.get("Driver")
        except DockerError:
            pass
        out = []
        seen = set()
        for c in listed:
            cid = c["Id"]
            seen.add(cid)
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            nets = (c.get("NetworkSettings") or {}).get("Networks") or {}
            lan = [n for n in nets if drivers.get(n) in LAN_DRIVERS]
            ip = next(((nets[n] or {}).get("IPAddress") for n in lan if (nets[n] or {}).get("IPAddress")), None)
            labels = {k: v for k, v in (c.get("Labels") or {}).items() if k.startswith(KEEP_LABELS)}
            entry = {
                "id": cid[:12],
                "name": name,
                "image": c.get("Image", ""),
                "state": c.get("State", "unknown"),
                "status": c.get("Status", ""),
                "ip": ip,
                "networks": sorted(nets),
                "host_network": ((c.get("HostConfig") or {}).get("NetworkMode") == "host"),
                "ports": _ports(c.get("Ports")),
                "labels": labels,
                "built_here": self._built_here(c.get("ImageID"), c.get("Image", "")),
                "health": None,
                "started": None,
                "restarts": None,
                "gpu_capable": False,
                "gpu": None,
                "cpu": None,
                "mem": None,
                "mem_limit": None,
                "net_rx": None,
                "net_tx": None,
            }
            try:
                info = self._get(f"/containers/{cid}/json")
                st = info.get("State") or {}
                entry["health"] = (st.get("Health") or {}).get("Status")
                entry["started"] = _epoch(st.get("StartedAt"))
                entry["restarts"] = info.get("RestartCount")
                env = (info.get("Config") or {}).get("Env") or []
                # "utility" only means it can read nvidia-smi, like Battle Bridge itself.
                monitor_only = "NVIDIA_DRIVER_CAPABILITIES=utility" in env
                entry["gpu_capable"] = not monitor_only and _uses_gpu(info.get("HostConfig") or {})
            except DockerError:
                pass
            if entry["state"] == "running":
                try:
                    self._add_stats(cid, entry)
                except DockerError:
                    pass
            out.append(entry)
        for gone in set(self._cpu_prev) - seen:
            del self._cpu_prev[gone]
        for gone in set(self._net_prev) - seen:
            del self._net_prev[gone]
        out.sort(key=_order)
        return out

    def _built_here(self, image_id, image):
        """Built on this machine: the image has no digest from any registry.
        Newer Docker (the containerd image store) gives local builds a digest
        too, so the runner's view, from compose files, is added on top."""
        if not image_id:
            return image.endswith(":local")
        if image_id not in self._built:
            try:
                info = self._get("/images/" + urllib.parse.quote(image_id, safe=":") + "/json")
                self._built[image_id] = not info.get("RepoDigests")
            except DockerError:
                return image.endswith(":local")
        return self._built[image_id]

    def _add_stats(self, cid, entry):
        s = self._get(f"/containers/{cid}/stats?stream=false&one-shot=true")
        cpu = s.get("cpu_stats") or {}
        total = (cpu.get("cpu_usage") or {}).get("total_usage")
        system = cpu.get("system_cpu_usage")
        if total is not None and system is not None:
            prev = self._cpu_prev.get(cid)
            if prev and system > prev[1]:
                # Share of the whole machine, so it sits beside the host CPU figure.
                entry["cpu"] = round(100 * (total - prev[0]) / (system - prev[1]), 1)
            self._cpu_prev[cid] = (total, system)
        mem = s.get("memory_stats") or {}
        usage = mem.get("usage")
        if usage is not None:
            stats = mem.get("stats") or {}
            cache = stats.get("inactive_file", stats.get("total_inactive_file", 0))
            entry["mem"] = max(0, usage - cache)
            entry["mem_limit"] = mem.get("limit")
        nets = s.get("networks")
        if nets:
            rx = sum(n.get("rx_bytes", 0) for n in nets.values())
            tx = sum(n.get("tx_bytes", 0) for n in nets.values())
            now = time.time()
            prev = self._net_prev.get(cid)
            # Counters reset when a container restarts, so skip a drop.
            if prev and now > prev[2] and rx >= prev[0] and tx >= prev[1]:
                entry["net_rx"] = (rx - prev[0]) / (now - prev[2])
                entry["net_tx"] = (tx - prev[1]) / (now - prev[2])
            self._net_prev[cid] = (rx, tx, now)


def _uses_gpu(hc):
    if hc.get("Runtime") == "nvidia":
        return True
    for d in hc.get("DeviceRequests") or []:
        for caps in d.get("Capabilities") or []:
            if "gpu" in caps:
                return True
    return False


def _ports(raw):
    """Docker lists IPv4 and IPv6 bindings separately.  One entry per port."""
    out = {}
    for p in raw or []:
        if p.get("Type", "tcp") != "tcp" or not p.get("PrivatePort"):
            continue
        key = p["PrivatePort"]
        cur = out.setdefault(key, {"private": key, "public": None})
        if p.get("PublicPort") and cur["public"] is None:
            cur["public"] = p["PublicPort"]
    return sorted(out.values(), key=lambda x: x["private"])


def _epoch(stamp):
    if not stamp or stamp.startswith("0001-"):
        return None
    # Docker gives nanoseconds; trim to microseconds for fromisoformat.
    stamp = stamp.rstrip("Z")
    if "." in stamp:
        head, frac = stamp.split(".", 1)
        stamp = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _order(entry):
    ip = entry["ip"]
    if ip and "." in ip:
        return (0, tuple(int(p) for p in ip.split(".")), entry["name"].lower())
    return (1, (), entry["name"].lower())
