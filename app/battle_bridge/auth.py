"""One password, sessions, and a lockout after repeated failures."""

import hashlib
import hmac
import secrets
import time

ITERATIONS = 600_000
COOKIE = "bb_session"


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def check_password(password, stored):
    try:
        algo, iterations, salt, digest = stored.split("$")
    except (AttributeError, ValueError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hash_password(password, salt, int(iterations)).split("$")[3]
    return hmac.compare_digest(candidate, digest)


class Auth:
    def __init__(self, db, cfg):
        self.db = db
        self.cfg = cfg

    def has_password(self):
        return bool(self.db.get_setting("password_hash"))

    def set_password(self, password):
        self.db.set_setting("password_hash", hash_password(password))
        self.db.conn().execute("DELETE FROM settings WHERE key = 'setup_code'")
        # A new password logs every existing session out.
        self.db.conn().execute("DELETE FROM sessions")

    def locked_for(self):
        """Seconds until logins are allowed again, or 0."""
        window = self.cfg.lockout_minutes * 60
        since = int(time.time()) - window
        rows = self.db.conn().execute(
            "SELECT ts FROM login_failures WHERE ts >= ? ORDER BY ts DESC", (since,)
        ).fetchall()
        if len(rows) >= self.cfg.lockout_failures:
            return max(1, rows[self.cfg.lockout_failures - 1][0] + window - int(time.time()))
        return 0

    def login(self, password, ip):
        """Returns (token, None) or (None, reason)."""
        wait = self.locked_for()
        if wait:
            return None, f"Too many wrong passwords.  Try again in {wait // 60 + 1} minutes."
        stored = self.db.get_setting("password_hash")
        if not stored:
            return None, "No password set yet.  Open the setup page."
        if not check_password(password, stored):
            self.failed(ip)
            left = self.cfg.lockout_failures - self._recent_failures()
            if left <= 0:
                return None, f"Wrong password.  Locked for {self.cfg.lockout_minutes} minutes."
            return None, "Wrong password."
        self.db.conn().execute("DELETE FROM login_failures")
        return self.new_session(), None

    def failed(self, ip):
        """Count a wrong guess (password or setup code) towards the lockout."""
        self.db.conn().execute("INSERT INTO login_failures (ts, ip) VALUES (?, ?)", (int(time.time()), ip))

    def new_session(self):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self.db.conn().execute(
            "INSERT INTO sessions (token, created, expires) VALUES (?, ?, ?)",
            (token, now, now + self.cfg.session_days * 86400))
        self.db.conn().execute("DELETE FROM sessions WHERE expires < ?", (now,))
        return token

    def setup_code(self):
        """A one-off code for the first-run page, so nobody else on the
        network can set the password first.  Shown in the container's log."""
        code = self.db.get_setting("setup_code")
        if not code:
            n = secrets.randbelow(10 ** 8)
            code = f"{n // 10000:04d}-{n % 10000:04d}"
            self.db.set_setting("setup_code", code)
        return code

    def check_setup_code(self, code, ip):
        """(ok, reason).  Wrong codes count towards the same lockout as passwords."""
        wait = self.locked_for()
        if wait:
            return False, f"Too many wrong tries.  Try again in {wait // 60 + 1} minutes."
        want = self.db.get_setting("setup_code") or ""
        got = "".join(ch for ch in str(code) if ch.isdigit())
        if not want or not hmac.compare_digest(got, want.replace("-", "")):
            self.failed(ip)
            return False, "That isn't the setup code."
        return True, None

    def _recent_failures(self):
        since = int(time.time()) - self.cfg.lockout_minutes * 60
        return self.db.conn().execute(
            "SELECT COUNT(*) FROM login_failures WHERE ts >= ?", (since,)).fetchone()[0]

    def valid(self, token):
        if not token:
            return False
        row = self.db.conn().execute(
            "SELECT expires FROM sessions WHERE token = ?", (token,)).fetchone()
        return bool(row) and row[0] > time.time()

    def logout(self, token):
        if token:
            self.db.conn().execute("DELETE FROM sessions WHERE token = ?", (token,))
