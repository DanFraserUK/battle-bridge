"""sqlite storage: 24 hours of stats, settings, sessions and login failures.

One connection, shared by every thread behind a lock.  (The web server
starts a thread per request, so a connection per thread would mean one per
request.)  Each call fetches its rows while it holds the lock.  WAL mode,
so a reader in another process (the check command) doesn't block writes.
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


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Conn:
    """Just the part of a sqlite3 connection the app uses, safe across threads."""

    def __init__(self, path):
        self.lock = threading.RLock()
        self.c = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=NORMAL")

    def execute(self, sql, params=()):
        with self.lock:
            return _Rows(self.c.execute(sql, params).fetchall())

    def transaction(self, statements):
        """[(sql, rows)] run with executemany, all or nothing."""
        with self.lock:
            self.c.execute("BEGIN")
            try:
                for sql, rows in statements:
                    self.c.executemany(sql, rows)
                self.c.execute("COMMIT")
            except BaseException:
                self.c.execute("ROLLBACK")
                raise


class DB:
    def __init__(self, path):
        self.path = path
        self._conn = _Conn(path)
        with self._conn.lock:
            self._conn.c.executescript(SCHEMA)

    def conn(self):
        return self._conn

    # --- history ------------------------------------------------------------

    def add_samples(self, ts, values):
        rows = [(k, ts, v) for k, v in values.items() if v is not None]
        if rows:
            self._conn.transaction([("INSERT INTO samples (series, ts, value) VALUES (?, ?, ?)", rows)])

    def prune(self, keep_seconds):
        now = int(time.time())
        c = self._conn
        c.execute("DELETE FROM samples WHERE ts < ?", (now - keep_seconds,))
        # Old failed guesses don't count towards a lockout any more.
        c.execute("DELETE FROM login_failures WHERE ts < ?", (now - 86400,))

    def history(self, series, since, points=720):
        """Readings for one series since a time, averaged down to about `points`."""
        rows = self._conn.execute(
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

    # --- settings -----------------------------------------------------------

    def get_setting(self, key, default=None):
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
