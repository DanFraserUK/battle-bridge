"""The fixed job list, and running one job at a time in a pseudo-terminal.

A job is a name from JOBS (or a drop-in file in jobs.d) plus, for some, one
compose project or container.  That argument must exactly match one Docker
reports right now.  Every command is a fixed argument list: there is no
shell, and nothing from the request ever becomes a command or a path.

Compose projects are found from Docker's own labels, so they can live in any
folder.  Output goes to a log file per run and to anyone attached.  Jobs
carry on if nobody is watching, and a new viewer gets the scrollback.
"""

import fcntl
import functools
import json
import os
import pty
import signal
import struct
import subprocess
import termios
import threading
import time

from . import dropins, facts as facts_mod

LOG_DIR = os.environ.get("BB_LOG_DIR", "/var/log/battle-bridge/jobs")
KEEP_RUNS = 200
# A job that goes past either of these is stopped: a forgotten "Follow logs"
# would otherwise grow in memory and on disk until something gave way.
MAX_OUTPUT = 32 * 1024 * 1024
MAX_FOLLOW_SECONDS = 2 * 3600
# Images updated last, on their own.  Also the label battlebridge.update=last.
UPDATE_LAST = ("pihole", "adguardhome", "unbound", "bind9", "technitium")

BOLD, DIM, YEL, GRN, RED, RST = "\x1b[1m", "\x1b[2m", "\x1b[33m", "\x1b[32m", "\x1b[31m", "\x1b[0m"

_FMT = "\t".join(['{{.Names}}', '{{.Label "com.docker.compose.project"}}',
                  '{{.Label "com.docker.compose.project.working_dir"}}',
                  '{{.Label "com.docker.compose.project.config_files"}}', '{{.Image}}',
                  '{{.Label "battlebridge.update"}}', '{{.State}}'])


def _docker_ps():
    try:
        r = subprocess.run(["docker", "ps", "-a", "--format", _FMT], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in r.stdout.splitlines():
        f = line.split("\t")
        if len(f) == 7:
            rows.append(f)
    return rows


_baked = {}     # image -> (when checked, has compose labels of its own)


def _image_has_compose_labels(image):
    """True if the image itself carries com.docker.compose labels.  Compose
    sets those labels on the containers it makes.  An image that brings its
    own could steer which compose files a job runs, so its containers are
    left out of project discovery."""
    hit = _baked.get(image)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        r = subprocess.run(["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
                           capture_output=True, text=True, timeout=20)
        labels = json.loads(r.stdout or "null") if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        labels = None
    baked = any(k.startswith("com.docker.compose.") for k in (labels or {}))
    _baked[image] = (time.time(), baked)
    return baked


def _image_name(image):
    last = image.split("@", 1)[0].rsplit("/", 1)[-1]
    return last.split(":", 1)[0].lower()


def _built_here(files):
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                if any(line.strip().startswith("build:") for line in fh):
                    return True
        except OSError:
            pass
    return False


def projects(rows=None):
    """Every compose project Docker knows: where it lives, its containers,
    whether it's built here, and whether it goes last in "Update all".

    A project whose containers disagree about its folder or files is marked
    as a conflict and can't be used, rather than guessing which is right."""
    found = {}
    for name, project, wd, files, image, update, _state in (rows if rows is not None else _docker_ps()):
        if not project or not wd or _image_has_compose_labels(image):
            continue
        mine = []
        for f in files.split(","):
            f = f.strip()
            if f:
                f = f if os.path.isabs(f) else os.path.join(wd, f)
                if f not in mine:
                    mine.append(f)
        p = found.setdefault(project, {"name": project, "dir": wd, "files": mine, "containers": [],
                                       "update_last": False, "conflict": False})
        if p["dir"] != wd or p["files"] != mine:
            p["conflict"] = True
        p["containers"].append(name)
        if update == "last" or _image_name(image) in UPDATE_LAST:
            p["update_last"] = True
    out = []
    for p in sorted(found.values(), key=lambda x: x["name"].lower()):
        p["missing"] = p["conflict"] or not os.path.isdir(p["dir"])
        p["built_here"] = _built_here(p["files"])
        out.append(p)
    return out


def containers(rows=None):
    return sorted((r[0] for r in (rows if rows is not None else _docker_ps())), key=str.lower)


def services(rows=None):
    """What the page's pickers show."""
    return [{"folder": p["name"], "dir": p["dir"], "built_here": p["built_here"],
             "containers": p["containers"], "missing": p["missing"], "conflict": p["conflict"]}
            for p in projects(rows)]


@functools.lru_cache(maxsize=1)
def compose_cmd():
    try:
        if subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=10).returncode == 0:
            return ["docker", "compose"]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ["docker-compose"]


# --- the jobs ------------------------------------------------------------------

class Step:
    def __init__(self, argv=None, cwd=None, title=None, keep_going=False, func=None):
        self.argv, self.cwd, self.title, self.keep_going, self.func = argv, cwd, title, keep_going, func


def _note(text, title=None):
    def show(w):
        w(text)
        return 0, None
    return Step(func=show, title=title)


def _in(p, *args, **kw):
    base = compose_cmd() + ["-p", p["name"], "--project-directory", p["dir"]]
    for f in p["files"]:
        base += ["-f", f]
    return Step(base + list(args), cwd=p["dir"], **kw)


def _validate(_p, all_projects):
    return [_in(p, "config", "-q", title=f"Checking {p['name']}", keep_going=True)
            for p in all_projects if not p["missing"]]


def _update_one(p, _all):
    if p["built_here"]:
        return [_in(p, "build", "--pull", title=f"{p['name']} is built here, so it's rebuilt"),
                _in(p, "up", "-d")]
    return [_in(p, "pull"), _in(p, "up", "-d")]


def _update_all(_p, all_projects):
    steps, later = [], []
    for p in all_projects:
        if p["conflict"]:
            steps.append(_note(f"{DIM}Its containers disagree about where its compose files are.  "
                               f"Skipped.{RST}\r\n", title=p["name"]))
            continue
        if p["missing"]:
            steps.append(_note(f"{DIM}Its folder {p['dir']} is gone.  Skipped.{RST}\r\n", title=p["name"]))
            continue
        if p["built_here"]:
            steps.append(_note(f"{DIM}Built on this server, nothing to pull.  Skipped.  "
                               f"Use Rebuild for it.{RST}\r\n", title=p["name"]))
            continue
        target = later if p["update_last"] else steps
        target += [_in(p, "pull", title=p["name"] + (" (last, on its own)" if p["update_last"] else ""),
                       keep_going=True),
                   _in(p, "up", "-d", keep_going=True)]
    return steps + later


def _rebuild(p, _all):
    # The pull picks up any pulled images in the same project (a database
    # beside a built app, say).  Nothing to pull is fine.
    return [_in(p, "build", "--pull", title=f"Rebuilding {p['name']}"),
            _in(p, "pull", "--ignore-buildable", keep_going=True),
            _in(p, "up", "-d")]


JOBS = {
    "status": {"label": "Status of every container", "group": "Containers", "arg": None, "changes": False,
               "steps": lambda p, a: [Step(["docker", "ps", "-a", "--format",
                                            "table {{.Names}}\t{{.Status}}\t{{.Image}}"])]},
    "validate": {"label": "Validate every compose file", "group": "Containers", "arg": None, "changes": False,
                 "steps": _validate},
    "logs": {"label": "Read a project's logs (last 200 lines)", "group": "Containers", "arg": "project",
             "changes": False, "steps": lambda p, a: [_in(p, "logs", "--tail=200", "--no-color")]},
    "logs-follow": {"label": "Follow a project's logs live", "group": "Containers", "arg": "project",
                    "interruptible": True, "changes": False,
                    "steps": lambda p, a: [_in(p, "logs", "--tail=50", "-f")]},
    "restart": {"label": "Restart a project", "group": "Containers", "arg": "project", "changes": True,
                "confirm": "Restart {arg}?", "steps": lambda p, a: [_in(p, "restart")]},
    "restart-container": {"label": "Restart a container", "group": "Containers", "arg": "container",
                          "changes": True, "confirm": "Restart the container {arg}?",
                          "steps": lambda c, a: [Step(["docker", "restart", c])]},
    "update-one": {"label": "Update one project", "group": "Updates", "arg": "project", "changes": True,
                   "confirm": "Pull the newest images for {arg} and recreate it?", "steps": _update_one},
    "update-all": {"label": "Update all images", "group": "Updates", "arg": None, "changes": True,
                   "confirm": "Pull new images and recreate every container that has one?  "
                              "DNS containers like Pi-hole go last.  Services drop for a minute or two.",
                   "steps": _update_all},
    "rebuild": {"label": "Rebuild a built-here project", "group": "Updates", "arg": "built", "changes": True,
                "confirm": "Rebuild {arg} from its folder, with the newest base image?", "steps": _rebuild},
    "refresh-checks": {"label": "Check for updates now", "group": "Updates", "arg": None, "changes": False,
                       "steps": lambda p, a: [Step(func="refresh", title="Packages, images and news")]},
    "prune": {"label": "Reclaim space from unused images", "group": "Housekeeping", "arg": None, "changes": True,
              "confirm": "Remove images no container uses?  Containers and data are untouched.",
              "steps": lambda p, a: [Step(["docker", "image", "ls", "--filter", "dangling=true"]),
                                     Step(["docker", "image", "prune", "-f"])]},
    "post-checks": {"label": "After-reboot checks", "group": "Housekeeping", "arg": None, "changes": False,
                    "steps": lambda p, a: [Step(func="post_checks", title="Services, drives and GPU")]},
}


def all_jobs(settings):
    """Built-in jobs plus drop-ins.  (jobs, problems)."""
    extra, problems = dropins.load("jobs", settings.config_dir, settings.owner)
    out = dict(JOBS)
    for j in extra:
        if j["name"] in JOBS:
            problems.append(f"jobs.d/{j['name']}.toml: {j['name']} is a built-in job name.  Pick another.")
            continue
        cmd, cwd = j["command"], j["cwd"]
        out[j["name"]] = {"label": j["label"], "group": j["group"], "arg": None, "changes": j["changes"],
                          "confirm": j["confirm"], "interruptible": j["interruptible"], "dropin": True,
                          "steps": lambda p, a, cmd=cmd, cwd=cwd: [Step(list(cmd), cwd=cwd)]}
    return out, problems


def public_jobs(settings):
    jobs, problems = all_jobs(settings)
    return [{"name": n, "label": j["label"], "group": j["group"], "arg": j["arg"], "changes": j["changes"],
             "confirm": j.get("confirm"), "interruptible": bool(j.get("interruptible")),
             "dropin": bool(j.get("dropin"))} for n, j in jobs.items()], problems


def check_request(name, arg, settings):
    """(steps, label, None) or (None, None, reason).  The only gate between a
    request and a command."""
    jobs, _ = all_jobs(settings)
    job = jobs.get(name)
    if not job:
        return None, None, "No such job."
    if job["arg"] is None:
        if arg not in (None, ""):
            return None, None, "That job takes no project."
        return job["steps"](None, projects()), job["label"], None
    if job["arg"] == "container":
        if arg not in containers():
            return None, None, "Pick a container from the list."
        return job["steps"](arg, None), f"{job['label']}: {arg}", None
    all_projects = projects()
    allowed = {p["name"]: p for p in all_projects
               if not p["missing"] and (job["arg"] == "project" or p["built_here"])}
    if arg not in allowed:
        return None, None, "Pick a project from the list."
    return job["steps"](allowed[arg], all_projects), f"{job['label']}: {arg}", None


# --- after-reboot checks (no shell, plain Python) ---------------------------------

def post_checks(write, settings):
    results = []

    def run(argv):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            return r.returncode, (r.stdout + r.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)

    # Containers meant to be running: any with a restart policy.
    code, out = run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"])
    stopped = [ln.split("\t")[0] for ln in out.splitlines() if "\t" in ln and ln.split("\t")[1] != "running"]
    down = []
    if stopped:
        c2, o2 = run(["docker", "inspect", "-f", "{{.Name}}\t{{.HostConfig.RestartPolicy.Name}}"] + stopped)
        for ln in o2.splitlines():
            if "\t" in ln:
                n, policy = ln.split("\t", 1)
                if policy in ("always", "unless-stopped", "on-failure"):
                    down.append(n.lstrip("/"))
    results.append({"label": "All services up", "ok": code == 0 and not down,
                    "detail": "every container that should run is running" if not down
                    else "not running: " + ", ".join(down)})

    # Drives: the ones runner.toml names, else everything /etc/fstab mounts at
    # boot.  One findmnt per mount: it takes at most two paths.
    mounts = settings.after_reboot_mounts if settings.after_reboot_mounts is not None \
        else facts_mod.fstab_mounts(settings.fstab)
    missing = [m for m in mounts if run(["findmnt", "-n", "-o", "TARGET", "--mountpoint", m])[0] != 0]
    results.append({"label": "Drives mounted", "ok": not missing,
                    "detail": (f"all {len(mounts)} mounted" if mounts else "none listed in /etc/fstab")
                    if not missing else "missing: " + ", ".join(missing)})

    # GPU: the container runner.toml names, else the first running one that
    # asks for a GPU.  Skipped when nothing uses one.
    gpu = settings.gpu_container
    if not gpu:
        code, out = run(["docker", "ps", "-q"])
        ids = out.split()
        if ids:
            c3, o3 = run(["docker", "inspect", "-f",
                          "{{.Name}}\t{{.HostConfig.Runtime}}\t{{json .HostConfig.DeviceRequests}}"] + ids)
            for ln in o3.splitlines():
                f = ln.split("\t")
                if len(f) == 3 and (f[1] == "nvidia" or '"gpu"' in f[2]):
                    gpu = f[0].lstrip("/")
                    break
    if gpu:
        code, out = run(["docker", "exec", gpu, "nvidia-smi", "-L"])
        results.append({"label": "GPU visible in containers", "ok": code == 0 and "GPU" in out,
                        "detail": (out.splitlines()[0][:120] if out else "no answer") + f" ({gpu})"})
    for r in results:
        mark = f"{GRN}✓{RST}" if r["ok"] else f"{RED}✕{RST}"
        write(f"{mark} {BOLD}{r['label']}{RST}: {r['detail']}\r\n")
    return 0 if all(r["ok"] for r in results) else 1, results


# --- running -------------------------------------------------------------------------

class Run:
    def __init__(self, rid, name, arg, label):
        self.id, self.name, self.arg, self.label = rid, name, arg, label
        self.started = time.time()
        self.finished = None
        self.exit = None
        self.result = None
        self.buf = bytearray()
        self.cond = threading.Condition()
        self.master = None
        self.proc = None
        self.stopping = False
        self.interruptible = False
        self.log_broken = False
        self.note = None
        self.size = (40, 160)

    def meta(self):
        return {"id": self.id, "name": self.name, "arg": self.arg, "label": self.label,
                "started": self.started, "finished": self.finished, "exit": self.exit, "result": self.result}


class Manager:
    def __init__(self, log_dir=LOG_DIR, refresh=None, settings=None):
        self.dir = log_dir
        os.makedirs(self.dir, exist_ok=True)
        self.refresh = refresh
        self.settings = settings or facts_mod.Settings()
        self.lock = threading.Lock()
        self.current = None
        self.history = self._load_history()
        self.next_id = max([h["id"] for h in self.history] + [0]) + 1

    # history -------------------------------------------------------------

    def _hist_path(self):
        return os.path.join(self.dir, "history.json")

    def _load_history(self):
        try:
            with open(self._hist_path()) as f:
                history = json.load(f)
        except (OSError, ValueError):
            return []
        # Runs that were going when the runner last stopped never finished.
        for h in history:
            if h.get("finished") is None:
                h.update(finished=h.get("started"), exit=-1, interrupted=True)
        return history

    def _save_history(self):
        tmp = self._hist_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.history[-KEEP_RUNS:], f)
        os.replace(tmp, self._hist_path())
        keep = {self.log_path(h["id"]) for h in self.history[-KEEP_RUNS:]}
        for name in os.listdir(self.dir):
            p = os.path.join(self.dir, name)
            if name.endswith(".log") and p not in keep:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def log_path(self, rid):
        return os.path.join(self.dir, f"{int(rid)}.log")

    # start ---------------------------------------------------------------

    def start(self, name, arg):
        steps, label, err = check_request(name, arg, self.settings)
        if err:
            return {"error": err}
        cur = self.current
        if cur and cur.finished is None and cur.interruptible:
            # Following logs never ends by itself, so it mustn't block the drawer.
            self.stop(cur.id)
            for _ in range(100):
                if cur.finished is not None:
                    break
                time.sleep(0.1)
        jobs, _ = all_jobs(self.settings)
        with self.lock:
            if self.current and self.current.finished is None:
                return {"error": f"Busy: job {self.current.id} ({self.current.label}) is still running.",
                        "running": self.current.id}
            run = Run(self.next_id, name, arg, label)
            run.interruptible = bool(jobs[name].get("interruptible"))
            self.history.append(run.meta())
            try:
                self._save_history()
            except OSError as e:
                # A full disk, say.  Nothing has started, so nothing is left busy.
                self.history.pop()
                return {"error": f"Couldn't record the job, so it hasn't started: {e.strerror or e}"}
            self.next_id += 1
            self.current = run
        threading.Thread(target=self._run, args=(run, steps), daemon=True).start()
        if run.interruptible:
            limit = threading.Timer(MAX_FOLLOW_SECONDS, self._time_limit, args=(run,))
            limit.daemon = True
            limit.start()
        return {"ok": True, "id": run.id}

    def _time_limit(self, run):
        if run.finished is None and not run.stopping:
            run.note = f"Stopped after {MAX_FOLLOW_SECONDS // 3600} hours.  Start it again to carry on."
            self.stop(run.id)

    def _emit(self, run, data, log):
        if isinstance(data, str):
            data = data.encode()
        if log is not None and not run.log_broken:
            try:
                log.write(data)
                log.flush()
            except OSError:
                # Keep the job going and visible, just not saved.
                run.log_broken = True
        with run.cond:
            run.buf += data
            run.cond.notify_all()
        if len(run.buf) > MAX_OUTPUT and not run.stopping:
            run.note = f"Stopped: more than {MAX_OUTPUT // 1048576} MB of output."
            self.stop(run.id)

    def _run(self, run, steps):
        code = 0
        log = None
        try:
            try:
                log = open(self.log_path(run.id), "wb")
            except OSError as e:
                run.log_broken = True
                self._emit(run, f"{YEL}Can't save this job's log ({e.strerror or e}).  "
                                f"It runs anyway.{RST}\r\n", None)
            w = lambda text: self._emit(run, text, log)  # noqa: E731
            w(f"{BOLD}{YEL}▶ {run.label}{RST}  {DIM}{time.strftime('%a %d %b %H:%M:%S')}{RST}\r\n")
            for step in steps:
                if run.stopping:
                    break
                if step.title:
                    w(f"\r\n{BOLD}== {step.title}{RST}\r\n")
                if step.func:
                    rc = self._func_step(run, step, w)
                else:
                    shown = " ".join(step.argv)
                    w(f"{DIM}$ {shown}{'  (in ' + step.cwd + ')' if step.cwd else ''}{RST}\r\n")
                    rc = self._pty_step(run, step, w)
                if run.stopping:
                    code = code or rc or 130
                    break
                if rc != 0:
                    w(f"{RED}Exit {rc}.{RST}\r\n")
                    code = code or rc
                    if not step.keep_going:
                        break
            if run.stopping:
                code = code or 130
                w(f"{YEL}{run.note or 'Stopped.'}{RST}\r\n")
            if run.log_broken and log is not None:
                w(f"{YEL}Part of this job's log couldn't be saved (is the disk full?).{RST}\r\n")
            w(f"\r\n{GRN if code == 0 else RED}{BOLD}{'Done.' if code == 0 else 'Finished with errors.'}{RST}\r\n")
        except Exception as e:
            # Whatever went wrong, the job must still end, or the runner stays busy.
            code = code or 1
            try:
                self._emit(run, f"\r\n{RED}The runner hit a problem: {type(e).__name__}: {e}{RST}\r\n", log)
            except Exception:
                pass
        finally:
            if log is not None:
                try:
                    log.close()
                except OSError:
                    pass
            ended = time.time()
            with self.lock:
                run.exit = code
                for h in self.history:
                    if h["id"] == run.id:
                        h.update(run.meta(), finished=ended)
                try:
                    self._save_history()
                except OSError:
                    pass
                # Last: anyone waiting on finished sees the history already saved.
                run.finished = ended
            with run.cond:
                run.cond.notify_all()

    def _func_step(self, run, step, w):
        try:
            if step.func == "refresh":
                if not self.refresh:
                    w("Not available.\r\n")
                    return 1
                return self.refresh(w)
            if step.func == "post_checks":
                rc, result = post_checks(w, self.settings)
            else:
                rc, result = step.func(w)
            if result is not None:
                run.result = result
            return rc
        except Exception as e:
            w(f"{RED}{type(e).__name__}: {e}{RST}\r\n")
            return 1

    def _pty_step(self, run, step, w):
        master, slave = pty.openpty()
        _set_size(master, *run.size)
        env = dict(os.environ, TERM="xterm-256color", COLUMNS=str(run.size[1]), LINES=str(run.size[0]))
        try:
            # New session, with the pty as its controlling terminal, so the
            # job sees a real terminal.  Stop is what ends a job reliably.
            proc = subprocess.Popen(step.argv, cwd=step.cwd, stdin=slave, stdout=slave, stderr=slave,
                                    start_new_session=True, env=env, close_fds=True,
                                    preexec_fn=_take_terminal)
        except OSError as e:
            os.close(master)
            os.close(slave)
            w(f"{RED}Couldn't start: {e}{RST}\r\n")
            return 127
        os.close(slave)
        run.master = master
        run.proc = proc
        if run.stopping:
            threading.Thread(target=_escalate, args=(run,), daemon=True).start()
        try:
            while True:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                w(data)
        finally:
            run.master = None
            os.close(master)
        rc = proc.wait()
        run.proc = None
        return rc

    # while running -----------------------------------------------------

    def stop(self, rid):
        """Ctrl+C to the job's process group, then firmer if it ignores it."""
        run = self.current
        if not run or run.id != rid or run.finished is not None:
            return {"error": "That job isn't running."}
        run.stopping = True
        threading.Thread(target=_escalate, args=(run,), daemon=True).start()
        return {"ok": True}

    def send_input(self, rid, data):
        run = self.current
        if not run or run.id != rid or run.finished is not None or run.master is None:
            return {"error": "That job isn't waiting for input."}
        os.write(run.master, data.encode()[:4096])
        return {"ok": True}

    def resize(self, rid, rows, cols):
        run = self.current
        if run and run.id == rid:
            run.size = (max(5, min(200, rows)), max(20, min(400, cols)))
            if run.master is not None:
                _set_size(run.master, *run.size)
        return {"ok": True}

    def status(self):
        cur = self.current.meta() if self.current else None
        return {"current": cur, "history": list(reversed(self.history[-50:]))}

    def stream(self, rid, offset):
        """Yields (bytes, None) chunks, then (None, meta) at the end."""
        run = self.current if self.current and self.current.id == rid else None
        if run is None:
            try:
                with open(self.log_path(rid), "rb") as f:
                    f.seek(offset)
                    yield f.read(), None
            except OSError:
                yield b"", None
            meta = next((h for h in self.history if h["id"] == rid), None)
            yield None, meta
            return
        while True:
            with run.cond:
                while len(run.buf) <= offset and run.finished is None:
                    run.cond.wait(15)
                    if len(run.buf) <= offset and run.finished is None:
                        break   # heartbeat
                chunk = bytes(run.buf[offset:])
                done = run.finished is not None and len(run.buf) <= offset + len(chunk)
            offset += len(chunk)
            yield chunk, None
            if done:
                yield None, run.meta()
                return


def _take_terminal():
    # Runs in the child after setsid: make stdin (the pty) the controlling terminal.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _escalate(run):
    # The job may be between steps, or its first command not started yet.
    for _ in range(100):
        if run.proc is not None or run.finished is not None:
            break
        time.sleep(0.05)
    for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 0)):
        proc = run.proc
        if proc is None or run.finished is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(wait * 10):
            if run.proc is None or run.finished is not None:
                return
            time.sleep(0.1)


def _set_size(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass
