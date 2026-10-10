"""Talks to bb-runner over its Unix socket (bind-mounted read-only; connecting
to a socket isn't a write)."""

import json
import os
import socket


class RunnerError(Exception):
    pass


class RunnerClient:
    def __init__(self, runner_dir):
        self.path = os.path.join(runner_dir, "runner.sock")

    def _open(self, req, timeout):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(self.path)
            s.sendall(json.dumps(req).encode() + b"\n")
        except OSError as e:
            s.close()
            raise RunnerError("The job runner isn't answering.  Is bb-runner running?") from e
        return s

    def call(self, req, timeout=20):
        s = self._open(req, timeout)
        with s, s.makefile("rb") as f:
            try:
                line = f.readline(4 * 1024 * 1024)
            except OSError as e:
                raise RunnerError("The job runner stopped answering.") from e
        if not line:
            raise RunnerError("The job runner closed the connection.")
        try:
            return json.loads(line)
        except ValueError:
            raise RunnerError("The job runner sent an answer that couldn't be read.") from None

    def stream(self, rid, offset):
        """Yields reply objects until the run ends or the runner goes away."""
        return self._lines({"op": "stream", "id": rid, "offset": offset})

    def pclog(self, name):
        return self._lines({"op": "pclog", "file": name})

    def _lines(self, req):
        s = self._open(req, timeout=60)
        with s, s.makefile("rb") as f:
            while True:
                try:
                    line = f.readline(8 * 1024 * 1024)
                except OSError:
                    return
                if not line:
                    return
                try:
                    msg = json.loads(line)
                except ValueError:
                    raise RunnerError("The job runner sent output that couldn't be read.") from None
                yield msg
                if "end" in msg or "error" in msg:
                    return
