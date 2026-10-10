"""Settings that come from the environment.

Only things that describe the container itself live here: where it listens,
where its data and mounts are.  Everything a person chooses (password,
networks, keys, links) is set on the setup page and kept in the database.
See store.py.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR.parent / "static"


@dataclass
class Config:
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    db_path: str = "/data/battle-bridge.db"
    host_proc: str = "/host/proc"
    host_sys: str = "/host/sys"
    docker_url: str = "http://bb-dockerproxy:2375"
    # How this container reaches ports published on the host.  compose.yml
    # maps it to the host's gateway address.
    host_gateway: str = "host.docker.internal"
    runner_dir: str = "/runner"
    nvidia_smi: str = "nvidia-smi"
    container_name: str = "battle-bridge"
    live_seconds: float = 2.0
    sample_seconds: int = 10
    history_hours: int = 24
    session_days: int = 30
    lockout_failures: int = 5
    lockout_minutes: int = 15
    # Testing only: replaces every address a service is found at.
    url_overrides: dict = None


def load():
    env = os.environ.get
    c = Config()
    c.listen_host = env("BB_LISTEN_HOST", c.listen_host)
    c.listen_port = int(env("BB_LISTEN_PORT", c.listen_port))
    c.db_path = env("BB_DB", c.db_path)
    c.host_proc = env("BB_HOST_PROC", c.host_proc)
    c.host_sys = env("BB_HOST_SYS", c.host_sys)
    c.docker_url = env("BB_DOCKER_URL", c.docker_url).rstrip("/")
    c.host_gateway = env("BB_HOST_GATEWAY", c.host_gateway)
    c.runner_dir = env("BB_RUNNER_DIR", c.runner_dir)
    c.nvidia_smi = env("BB_NVIDIA_SMI", c.nvidia_smi)
    c.container_name = env("BB_CONTAINER_NAME", c.container_name)
    c.live_seconds = float(env("BB_LIVE_SECONDS", c.live_seconds))
    c.sample_seconds = int(env("BB_SAMPLE_SECONDS", c.sample_seconds))
    c.url_overrides = {}
    for k, v in os.environ.items():
        if k.startswith("BB_TEST_URL_") and v:
            c.url_overrides[k[len("BB_TEST_URL_"):].lower()] = v.rstrip("/")
    return c
