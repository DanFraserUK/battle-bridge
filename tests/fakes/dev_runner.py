"""Run bb-runner's job socket for dev, against the fake docker CLI and the
example drop-ins.  python3 dev_runner.py <state dir>"""

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
state = sys.argv[1]
os.environ["BB_LOG_DIR"] = os.path.join(state, "jobs")
os.environ["BB_PC_LOG_DIR"] = os.path.join(state, "pc-jobs")
os.makedirs(os.environ["BB_PC_LOG_DIR"], exist_ok=True)
with open(os.path.join(os.environ["BB_PC_LOG_DIR"], "update-system-20261008-091200.log"), "w") as f:
    f.write("\x1b[1;33m▶ System update on homelab\x1b[0m\n\n\x1b[1m== pacman -Syu\x1b[0m\n"
            ":: Synchronising package databases...\n core up to date\n extra up to date\n"
            ":: Starting full system upgrade...\n there is nothing to do\n\n\x1b[32m\x1b[1mDone.\x1b[0m\n")
# The examples, as drop-ins.  The runner insists they're owned by the owner
# it's told about and writable only by it.
conf = os.path.join(state, "etc")
shutil.rmtree(conf, ignore_errors=True)
shutil.copytree(os.path.join(ROOT, "examples"), conf)
for dirpath, dirs, files in os.walk(conf):
    os.chmod(dirpath, 0o755)
    for fn in files:
        os.chmod(os.path.join(dirpath, fn), 0o644)
os.environ["PATH"] = os.path.join(HERE, "bin") + os.pathsep + os.environ["PATH"]
sys.path.insert(0, os.path.join(ROOT, "runner"))
from bbrunner import facts, jobs  # noqa: E402
from bbrunner.sock import SocketServer  # noqa: E402


def refresh(write):
    for name in ("updates", "image updates", "news"):
        write(f"Checking {name}... ")
        time.sleep(0.3)
        write("\x1b[32mdone\x1b[0m\r\n")
    return 0


settings = facts.Settings(config_dir=conf, owner=os.getuid(), after_reboot_mounts=["/", "/proc"],
                          pc_log_dir=os.environ["BB_PC_LOG_DIR"])
m = jobs.Manager(os.environ["BB_LOG_DIR"], refresh=refresh, settings=settings)
SocketServer(os.path.join(state, "runner.sock"), m, group=None).start()
print("dev runner up", flush=True)
while True:
    time.sleep(3600)
