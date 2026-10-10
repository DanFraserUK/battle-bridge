"""GPU readings from nvidia-smi.  The nvidia runtime puts it in the container."""

import os
import re
import shutil
import subprocess

FIELDS = "name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw,encoder.stats.sessionCount"


def snapshot(nvidia_smi="nvidia-smi"):
    exe = shutil.which(nvidia_smi) if "/" not in nvidia_smi else nvidia_smi
    if not exe:
        return {"ok": False, "error": "nvidia-smi not found"}
    try:
        res = subprocess.run(
            [exe, f"--query-gpu={FIELDS}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e)}
    if res.returncode != 0 or not res.stdout.strip():
        return {"ok": False, "error": (res.stderr or "no output").strip()[:200]}
    f = [x.strip() for x in res.stdout.strip().splitlines()[0].split(",")]
    return {
        "ok": True,
        "name": f[0],
        "util": _num(f[1]),
        "temp": _num(f[2]),
        "mem_used": _num(f[3]),
        "mem_total": _num(f[4]),
        "power": _num(f[5]),
        "encoders": _num(f[6]),
    }


def _num(s):
    try:
        return float(s)
    except ValueError:
        return None


def processes(nvidia_smi="nvidia-smi"):
    """Load per process from `nvidia-smi pmon`, as {pid: {"sm", "enc", "dec"}}.

    Other containers' processes only show up when this container shares the
    host's PID namespace (pid: host in compose.yml).  Any failure gives {}.
    """
    exe = shutil.which(nvidia_smi) if "/" not in nvidia_smi else nvidia_smi
    if not exe:
        return {}
    try:
        res = subprocess.run([exe, "pmon", "-c", "1", "-s", "u"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if res.returncode != 0:
        return {}
    return parse_pmon(res.stdout)


def parse_pmon(text):
    cols = None
    out = {}
    for line in text.splitlines():
        if line.startswith("#"):
            if cols is None:
                cols = line.lstrip("#").split()
            continue
        f = line.split()
        if not cols or len(f) < len(cols) - 1 or not f[1].isdigit():
            continue
        row = dict(zip(cols, f))
        pid = int(f[1])
        load = out.setdefault(pid, {"sm": 0.0, "enc": 0.0, "dec": 0.0})
        for k in load:
            # "-" means no work in the sample.
            load[k] += _num(row.get(k, "-")) or 0.0
    return out


CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


def container_of(pid, proc="/proc"):
    """The Docker container ID a host PID belongs to, from its cgroup, or None."""
    try:
        with open(os.path.join(proc, str(pid), "cgroup")) as f:
            m = CONTAINER_ID.search(f.read())
    except OSError:
        return None
    return m.group(0) if m else None


def by_container(procs, proc="/proc"):
    """GPU % per container (first 12 of its ID): its busiest engine."""
    sums = {}
    for pid, load in procs.items():
        cid = container_of(pid, proc)
        if not cid:
            continue
        acc = sums.setdefault(cid[:12], {"sm": 0.0, "enc": 0.0, "dec": 0.0})
        for k in acc:
            acc[k] += load[k]
    return {cid: min(100.0, max(acc.values())) for cid, acc in sums.items()}
