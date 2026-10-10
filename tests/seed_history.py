"""Fill a test database with 24 hours of believable history, for graphs."""

import math
import random
import sys
import time

sys.path.insert(0, sys.argv[2])
from battle_bridge.db import DB  # noqa: E402

db = DB(sys.argv[1])
now = int(time.time())
rows = {}
for ts in range(now - 86400, now, 10):
    hour = (ts % 86400) / 3600
    night = 1 if 2 <= hour < 6 else 0                       # a nightly job
    evening = math.exp(-((hour - 20.5) ** 2) / 4)           # Plex in the evening
    cpu = 4 + 30 * night + 12 * evening + random.uniform(0, 3)
    v = {
        "cpu": cpu,
        "load": cpu / 12 + random.uniform(0, .2),
        "mem": 27 + 6 * night + random.uniform(0, 1),
        "net.rx": (2e5 + 4e6 * evening) * random.uniform(.5, 1.5),
        "net.tx": (1e5 + 9e6 * evening) * random.uniform(.5, 1.5),
        "cpu.temp": 38 + cpu / 3,
        "gpu.util": 60 * night + 15 * evening + random.uniform(0, 2),
        "gpu.temp": 30 + 30 * night + 10 * evening,
        "gpu.mem": 1 + 1800 * night + 400 * evening,
        "ctr.plex.cpu": 6 * evening + random.uniform(0, .5),
        "ctr.plex.mem": 900 + 200 * evening,
        "ctr.homeassistant.cpu": 25 * night + random.uniform(0, .3),
    }
    for i in range(8):
        v[f"core.{i}"] = max(0, cpu + random.uniform(-4, 8))
    for k, val in v.items():
        rows.setdefault(k, []).append((k, ts, val))
db.conn().transaction([("INSERT INTO samples (series, ts, value) VALUES (?, ?, ?)", series)
                       for series in rows.values()])
print("seeded", sum(len(r) for r in rows.values()), "rows")
