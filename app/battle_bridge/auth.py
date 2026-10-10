"""One password, sessions, and a lockout after repeated failures."""

import hashlib
import hmac
import secrets
import threading
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
        # One guess at a time.  Otherwise many guesses sent at once would all
        # be checked before the first failure was counted, skipping the lockout.
        self.guess_lock = threading.Lock()

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
        with self.guess_lock:
            wait = self.locked_for()
            if wait:
                return None, _wait_message(wait)
            stored = self.db.get_setting("password_hash")
            if not stored:
                return None, "No password set yet.  Open the setup page."
            if not check_password(password, stored):
                self.failed(ip)
                wait = self.locked_for()
                return None, "Wrong password." + (f"  {_wait_message(wait)}" if wait else "")
            self.db.conn().execute("DELETE FROM login_failures")
            return self.new_session(), None

    def check_current(self, password, ip):
        """(ok, reason) for the current password, when changing it."""
        with self.guess_lock:
            wait = self.locked_for()
            if wait:
                return False, _wait_message(wait)
            if not check_password(str(password or ""), self.db.get_setting("password_hash")):
                self.failed(ip)
                return False, "The current password is wrong."
            return True, None

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
        with self.guess_lock:
            wait = self.locked_for()
            if wait:
                return False, _wait_message(wait)
            want = self.db.get_setting("setup_code") or ""
            # ASCII digits only: compare_digest refuses other characters.
            got = "".join(ch for ch in str(code) if ch in "0123456789")
            if not want or not hmac.compare_digest(got, want.replace("-", "")):
                self.failed(ip)
                return False, "That isn't the setup code."
            return True, None

    def valid(self, token):
        if not token:
            return False
        row = self.db.conn().execute(
            "SELECT expires FROM sessions WHERE token = ?", (token,)).fetchone()
        return bool(row) and row[0] > time.time()

    def logout(self, token):
        if token:
            self.db.conn().execute("DELETE FROM sessions WHERE token = ?", (token,))


def _wait_message(seconds):
    minutes = seconds // 60 + 1
    return f"Too many wrong tries.  Try again in {minutes} minute{'s' if minutes != 1 else ''}."
