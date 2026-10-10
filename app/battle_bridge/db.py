"""sqlite storage: 24 hours of stats, settings, sessions and login failures.

One connection per thread.  WAL mode so the sampler can write while pages
read.
"""

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    series TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    value  REAL
);
CREATE INDEX IF NOT EXISTS samples_series_ts ON samples (series, ts);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token   TEXT PRIMARY KEY,
    created INTEGER NOT NULL,
    expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    ts INTEGER NOT NULL,
    ip TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    # --- history ------------------------------------------------------------

    def add_samples(self, ts, values):
        rows = [(k, ts, v) for k, v in values.items() if v is not None]
        if rows:
            c = self.conn()
            c.execute("BEGIN")
            c.executemany("INSERT INTO samples (series, ts, value) VALUES (?, ?, ?)", rows)
            c.execute("COMMIT")

    def prune(self, keep_seconds):
        self.conn().execute("DELETE FROM samples WHERE ts < ?", (int(time.time()) - keep_seconds,))

    def history(self, series, since, points=720):
        """Readings for one series since a time, averaged down to about `points`."""
        rows = self.conn().execute(
            "SELECT ts, value FROM samples WHERE series = ? AND ts >= ? ORDER BY ts",
            (series, since),
        ).fetchall()
        if len(rows) <= points:
            return [[t, round(v, 2)] for t, v in rows]
        step = len(rows) / points
        out = []
        for i in range(points):
            chunk = rows[int(i * step):int((i + 1) * step)] or rows[int(i * step):int(i * step) + 1]
            out.append([chunk[-1][0], round(sum(v for _, v in chunk) / len(chunk), 2)])
        return out

    def series_names(self, prefix=""):
        rows = self.conn().execute(
            "SELECT DISTINCT series FROM samples WHERE series LIKE ?", (prefix + "%",)
        ).fetchall()
        return sorted(r[0] for r in rows)

    # --- settings -----------------------------------------------------------

    def get_setting(self, key, default=None):
        row = self.conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        self.conn().execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
