"""`python3 -m battle_bridge check`: try every data source once, from inside
the real container, and say plainly what works and what doesn't.  Changes
nothing.  Prints no keys."""

import time

from . import VERSION, discovery
from .collectors import gpu
from .collectors.docker import DockerCollector, DockerError
from .collectors.host import HostCollector
from .facts import Facts
from .poller import SCHEDULE, Poller
from .runner_client import RunnerClient, RunnerError

G, R, Y, N = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[0m"


def line(ok, what, detail=""):
    mark = f"{G}✓{N}" if ok is True else f"{R}✕{N}" if ok is False else f"{Y}-{N}"
    print(f"  {mark} {what:<28} {detail}")
    return ok


class _Sampler:
    """Just enough of the sampler for the poller to find addresses."""

    def __init__(self, containers):
        self.containers = containers

    def snapshot(self):
        return {"containers": self.containers}


def run(cfg, db, auth, store):
    bad = 0
    print(f"\nBattle Bridge {VERSION} check.  Reads only, changes nothing.\n")

    print("Host stats")
    h = HostCollector(cfg.host_proc, cfg.host_sys, store.settings()["net_iface"])
    try:
        h.snapshot()
        time.sleep(1)
        s = h.snapshot()
        line(True, "CPU, memory, load", f"{len(s['cores'])} cores, {s['mem_pct']}% memory used")
        bad += not line(s["iface"] is not None, "Network", f"interface {s['iface']}" if s["iface"] else
                        f"can't read {cfg.host_proc}/1/net/dev")
        line(s["cpu_temp"] is not None or None, "CPU temperature",
             f"{s['cpu_temp']}°C" if s["cpu_temp"] is not None else "no sensor found (the box shows –)")
    except OSError as e:
        bad += 1
        line(False, "Host stats", str(e))
    g = gpu.snapshot(cfg.nvidia_smi)
    line(g.get("ok") or None, "GPU", g.get("name") if g.get("ok") else "no NVIDIA GPU visible (fine if there isn't one)")

    print("\nDocker, through the read-only proxy")
    containers = []
    try:
        containers = DockerCollector(cfg.docker_url).snapshot()
        line(True, "Container list", f"{len(containers)} containers")
    except DockerError as e:
        bad += 1
        line(False, "Container list", str(e))
    # A stand-in address (from the documentation range) when none is set.
    server = store.settings()["server_address"] or "192.0.2.1"
    links = store.settings()["links"]
    nolink = [c["name"] for c in containers if not discovery.tile_link(c, server, links)
              and not (discovery.known(c) and discovery.known(c)[1] is None)]
    if nolink:
        line(None, "No link found yet for", ", ".join(nolink) + "  (the page probes for these, "
             "or set one on the setup page)")

    print("\nHost runner (optional: jobs, drives, updates)")
    data, problem = Facts(cfg.runner_dir).get()
    if data is None:
        line(None, "Not installed", "see the setup page for the one-line install")
    else:
        line(problem is None, "facts.json", problem or "fresh")
        rv = (data.get("runner") or {}).get("version")
        line(rv == VERSION or None, "Runner version", rv if rv == VERSION else
             f"{rv}, but this is {VERSION}.  Run the install line again to update it")
        for name in ("disks", "root", "timers", "updates", "image_updates"):
            f = data.get(name)
            if f is None:
                line(None, f"  {name}", "not gathered yet")
            elif isinstance(f, dict) and f.get("error"):
                line(False, f"  {name}", f["error"][:90])
            else:
                line(True, f"  {name}")
        try:
            reply = RunnerClient(cfg.runner_dir).call({"op": "jobs"}, timeout=10)
            line(True, "Runner socket", f"{len(reply.get('jobs', []))} jobs, "
                 f"{len(reply.get('services', []))} compose projects")
        except (RunnerError, OSError, ValueError) as e:
            bad += 1
            line(False, "Runner socket", str(e))

    print("\nServices (keys from the setup page)")
    p = Poller(cfg, _Sampler(containers), store)
    for name in SCHEDULE:
        p.poll(name)
        r = p.results[name]
        if not r["found"]:
            line(None, name, "not found")
        elif not r["configured"]:
            line(None, name, r["error"])
        elif r["error"]:
            bad += 1
            line(False, name, r["error"])
        else:
            line(True, name)

    print("\nLogin")
    has = auth.has_password()
    bad += not line(has, "Password set", "" if has else "open the page in a browser to set one")

    print(f"\n{G}All good.{N}\n" if not bad else f"\n{R}{bad} thing(s) to look at.{N}\n")
    return 0 if not bad else 1
