"""Reads the runner's facts.json (a read-only mount), re-reading only when it
changes."""

import json
import os
import threading
import time


class Facts:
    def __init__(self, runner_dir):
        self.path = os.path.join(runner_dir, "facts.json")
        self._mtime = None
        self._data = None
        self._lock = threading.Lock()

    def get(self):
        """(facts or None, problem or None)."""
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            return None, "No host runner yet.  It's optional, and adds jobs, drives and updates.  See Setup."
        with self._lock:
            if mtime != self._mtime:
                try:
                    with open(self.path) as f:
                        self._data = json.load(f)
                    self._mtime = mtime
                except (OSError, ValueError):
                    pass  # mid-replace; keep the last good copy
            data = self._data
        if data is None:
            return None, "Couldn't read the runner's facts."
        written = (data.get("runner") or {}).get("written") or mtime
        if time.time() - written > 300:
            return data, "The host runner hasn't reported for over 5 minutes.  Is bb-runner running?"
        return data, None
