"""GPU readings from nvidia-smi.  The nvidia runtime puts it in the container."""

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
