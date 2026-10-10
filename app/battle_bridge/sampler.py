"""Background loops: live readings every couple of seconds, history every ten.

The page only ever reads `state`.  Collectors that fail leave an error in
their slot rather than stopping the loop.
"""

import logging
import threading
import time

from . import discovery
from .collectors import gpu as gpu_mod
from .collectors.docker import DockerCollector, DockerError
from .collectors.host import HostCollector

log = logging.getLogger("battle_bridge.sampler")


class Sampler:
    def __init__(self, cfg, db, store):
        self.cfg = cfg
        self.db = db
        self.store = store
        self.host = HostCollector(cfg.host_proc, cfg.host_sys, store.settings()["net_iface"])
        self.docker = DockerCollector(cfg.docker_url)
        self.prober = discovery.Prober(cfg.host_gateway)
        self.lock = threading.Lock()
        self.state = {"host": None, "gpu": None, "containers": None, "hostname": None,
                      "docker_error": None, "host_error": None, "updated": None}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._live_loop, name="live", daemon=True).start()
        threading.Thread(target=self._slow_loop, name="slow", daemon=True).start()

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    # --- loops --------------------------------------------------------------

    def _live_loop(self):
        while not self._stop.is_set():
            self.tick_live()
            self._stop.wait(self.cfg.live_seconds)

    def _slow_loop(self):
        last_prune = 0
        while not self._stop.is_set():
            self.tick_slow()
            if time.time() - last_prune > 3600:
                self.db.prune(self.cfg.history_hours * 3600)
                last_prune = time.time()
            self._stop.wait(self.cfg.sample_seconds)

    def tick_live(self):
        self.host.iface = self.store.settings()["net_iface"]
        try:
            host = self.host.snapshot()
            err = None
        except OSError as e:
            host, err = None, str(e)
            log.warning("host stats: %s", e)
        g = gpu_mod.snapshot(self.cfg.nvidia_smi)
        with self.lock:
            self.state["host"] = host
            self.state["host_error"] = err
            self.state["gpu"] = g
            self.state["updated"] = time.time()

    def tick_slow(self):
        try:
            containers = self.docker.snapshot()
            err = None
        except DockerError as e:
            containers, err = None, str(e)
            log.warning("docker: %s", e)
        if containers:
            self.prober.run(containers)
        with self.lock:
            if containers is not None:
                self.state["containers"] = containers
                self.state["hostname"] = self.docker.hostname
            self.state["docker_error"] = err
            host = self.state["host"]
            g = self.state["gpu"]
        self._record(host, g, containers)

    def _record(self, host, g, containers):
        values = {}
        if host:
            values["cpu"] = host["cpu"]
            for i, v in enumerate(host["cores"]):
                values[f"core.{i}"] = v
            values["load"] = host["load"][0]
            values["mem"] = host["mem_pct"]
            values["net.rx"] = host["net_rx"]
            values["net.tx"] = host["net_tx"]
            values["cpu.temp"] = host["cpu_temp"]
        if g and g.get("ok"):
            values["gpu.util"] = g["util"]
            values["gpu.temp"] = g["temp"]
            values["gpu.mem"] = g["mem_used"]
        for c in containers or []:
            values[f"ctr.{c['name']}.cpu"] = c["cpu"]
            if c["mem"] is not None:
                values[f"ctr.{c['name']}.mem"] = c["mem"] / 1048576
        self.db.add_samples(int(time.time()), values)
