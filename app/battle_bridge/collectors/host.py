"""Host stats from a read-only bind mount of the host's /proc and /sys.

Rates (CPU %, network bytes per second) need two readings, so the collector
keeps the previous one.  The first call returns None for those.
"""

import os
import time
from pathlib import Path

# Interfaces that are not the server's own network connection.
_SKIP_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "macv", "mv-", "ipvl", "tun", "tap", "ifb", "wg",
                  "tailscale", "zt", "cni", "flannel", "cali", "podman", "lxc", "vnet")


class HostCollector:
    def __init__(self, proc, sys, iface=""):
        self.proc = Path(proc)
        self.sys = Path(sys)
        self.iface = iface
        self._cpu_prev = None
        self._net_prev = None
        self._names = None

    # --- raw readers ------------------------------------------------------

    def _read(self, *parts):
        return (self.proc.joinpath(*parts)).read_text()

    def _cpu_times(self):
        times = {}
        for line in self._read("stat").splitlines():
            if not line.startswith("cpu"):
                break
            name, *vals = line.split()
            vals = [int(v) for v in vals[:8]]
            idle = vals[3] + vals[4]          # idle + iowait
            times[name] = (sum(vals), idle)
        return times

    def _net_bytes(self):
        # /proc/1/net is the host's network namespace when /proc is the host's.
        text = self._read("1", "net", "dev")
        rows = {}
        for line in text.splitlines()[2:]:
            name, data = line.split(":", 1)
            name = name.strip()
            f = data.split()
            rows[name] = (int(f[0]), int(f[8]))
        if self.iface and self.iface in rows:
            return self.iface, rows[self.iface]
        best = None
        for name, (rx, tx) in rows.items():
            if name.startswith(_SKIP_PREFIXES):
                continue
            if best is None or rx + tx > sum(rows[best]):
                best = name
        return best, rows.get(best, (0, 0))

    def _meminfo(self):
        info = {}
        for line in self._read("meminfo").splitlines():
            key, rest = line.split(":", 1)
            info[key] = int(rest.split()[0]) * 1024
        return info

    def _cpu_temp(self):
        """Package temperature from coretemp (Intel) or k10temp (AMD)."""
        base = self.sys / "class" / "hwmon"
        try:
            hwmons = sorted(base.iterdir())
        except OSError:
            return None
        for hw in hwmons:
            try:
                name = (hw / "name").read_text().strip()
            except OSError:
                continue
            if name not in ("coretemp", "k10temp", "zenpower"):
                continue
            for label_file in sorted(hw.glob("temp*_label")):
                label = label_file.read_text().strip()
                if label.startswith(("Package", "Tctl", "Tdie")):
                    val = label_file.with_name(label_file.name.replace("_label", "_input"))
                    try:
                        return int(val.read_text()) / 1000
                    except (OSError, ValueError):
                        return None
            try:
                return int((hw / "temp1_input").read_text()) / 1000
            except (OSError, ValueError):
                return None
        return None

    def _cpu_and_kernel(self):
        """Read once: they only change with a reboot."""
        if self._names is None:
            model = None
            try:
                for line in self._read("cpuinfo").splitlines():
                    if line.startswith("model name"):
                        model = " ".join(line.split(":", 1)[1].split())
                        model = model.replace("(R)", "").replace("(TM)", "").replace(" CPU", "")
                        model = model.split(" @ ")[0]
                        break
            except OSError:
                pass
            try:
                kernel = self._read("sys", "kernel", "osrelease").strip()
            except OSError:
                kernel = None
            self._names = (model, kernel)
        return self._names

    # --- the snapshot -------------------------------------------------------

    def snapshot(self):
        now = time.monotonic()
        out = {}

        cpu = self._cpu_times()
        if self._cpu_prev:
            pct = {}
            for name, (total, idle) in cpu.items():
                p_total, p_idle = self._cpu_prev.get(name, (total, idle))
                dt = total - p_total
                pct[name] = round(100 * (1 - (idle - p_idle) / dt), 1) if dt > 0 else 0.0
            out["cpu"] = pct.get("cpu", 0.0)
            out["cores"] = [pct[n] for n in sorted(pct, key=_core_order) if n != "cpu"]
        else:
            out["cpu"] = None
            out["cores"] = [None] * (len(cpu) - 1)
        self._cpu_prev = cpu

        load = self._read("loadavg").split()
        out["load"] = [float(load[0]), float(load[1]), float(load[2])]

        mem = self._meminfo()
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", 0)
        out["mem_total"] = total
        out["mem_used"] = total - avail
        out["mem_pct"] = round(100 * (total - avail) / total, 1) if total else None
        swap_total = mem.get("SwapTotal", 0)
        out["swap_used"] = swap_total - mem.get("SwapFree", 0)
        out["swap_total"] = swap_total

        out["uptime"] = float(self._read("uptime").split()[0])

        try:
            iface, (rx, tx) = self._net_bytes()
        except (OSError, ValueError, IndexError):
            iface, rx, tx = None, None, None
        out["iface"] = iface
        if self._net_prev and rx is not None and self._net_prev[0] == iface:
            _, p_rx, p_tx, p_t = self._net_prev
            dt = now - p_t
            out["net_rx"] = max(0, (rx - p_rx) / dt) if dt > 0 else 0
            out["net_tx"] = max(0, (tx - p_tx) / dt) if dt > 0 else 0
        else:
            out["net_rx"] = out["net_tx"] = None
        if rx is not None:
            self._net_prev = (iface, rx, tx, now)

        out["cpu_temp"] = self._cpu_temp()
        out["cpu_model"], out["kernel"] = self._cpu_and_kernel()
        out["cpu_count"] = os.cpu_count()
        return out


def _core_order(name):
    return int(name[3:]) if name[3:].isdigit() else -1
