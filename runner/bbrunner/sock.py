"""The runner's Unix socket.  The only way in: no network port.

One JSON request per connection, one line.  Replies are JSON lines.  The
socket's group is the container's group, and nothing else can connect.

    {"op": "jobs"}                              job list, services, status
    {"op": "start", "job": "restart", "arg": "media"}
    {"op": "stream", "id": 7, "offset": 0}      output as it comes, then the end
    {"op": "input", "id": 7, "data": "y\\r"}
    {"op": "resize", "id": 7, "rows": 40, "cols": 160}
"""

import base64
import grp
import json
import logging
import os
import socket
import threading

from . import facts, jobs

log = logging.getLogger("bb-runner.sock")
MAX_LINE = 16 * 1024
CHUNK = 256 * 1024


class SocketServer:
    def __init__(self, path, manager, group="battlebridge"):
        self.path = path
        self.manager = manager
        self.group = group

    def start(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        if len(self.path.encode()) > 107:
            raise RuntimeError(f"socket path too long for a Unix socket: {self.path}")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o117)
        try:
            srv.bind(self.path)
        finally:
            os.umask(old)
        if self.group:
            try:
                os.chown(self.path, -1, grp.getgrnam(self.group).gr_gid)
            except (KeyError, PermissionError) as e:
                log.warning("couldn't give the socket to group %s: %s", self.group, e)
        os.chmod(self.path, 0o660)
        srv.listen(16)
        threading.Thread(target=self._accept, args=(srv,), daemon=True).start()
        return srv

    def _accept(self, srv):
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            try:
                f = conn.makefile("rwb")
                line = f.readline(MAX_LINE)
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError
                self.dispatch(req, lambda obj: (f.write(json.dumps(obj).encode() + b"\n"), f.flush()))
            except (ValueError, UnicodeDecodeError):
                _send_raw(conn, {"error": "Bad request."})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception("socket request failed")
                _send_raw(conn, {"error": "The runner hit a problem.  See journalctl -u bb-runner."})

    def dispatch(self, req, send):
        op = req.get("op")
        m = self.manager
        if op == "jobs":
            listed, problems = jobs.public_jobs(m.settings)
            rows = jobs._docker_ps()
            send({"jobs": listed, "problems": problems, "services": jobs.services(rows),
                  "containers": jobs.containers(rows), **m.status()})
        elif op == "start":
            arg = req.get("arg")
            if arg is not None and not isinstance(arg, str):
                return send({"error": "Bad request."})
            send(m.start(str(req.get("job", "")), arg))
        elif op == "stream":
            rid, offset = _int(req.get("id")), _int(req.get("offset"))
            for chunk, meta in m.stream(rid, max(0, offset)):
                if chunk:
                    # In pieces: a long log as one line would be too big to read back.
                    for i in range(0, len(chunk), CHUNK):
                        piece = chunk[i:i + CHUNK]
                        send({"data": base64.b64encode(piece).decode(), "offset": offset + len(piece)})
                        offset += len(piece)
                elif meta is not None or chunk is None:
                    send({"end": meta})
                else:
                    send({"beat": True})
        elif op == "pclog":
            name = str(req.get("file", ""))
            if not facts.PC_LOG.match(name):
                return send({"error": "No such log."})
            try:
                with open(os.path.join(m.settings.pc_log_dir, name), "rb") as fh:
                    data = fh.read()[-2_000_000:]
            except OSError:
                return send({"error": "No such log."})
            for i in range(0, len(data), CHUNK):
                send({"data": base64.b64encode(data[i:i + CHUNK]).decode(), "offset": i + len(data[i:i + CHUNK])})
            send({"end": {"file": name}})
        elif op == "input":
            data = req.get("data")
            if not isinstance(data, str):
                return send({"error": "Bad request."})
            send(m.send_input(_int(req.get("id")), data))
        elif op == "stop":
            send(m.stop(_int(req.get("id"))))
        elif op == "resize":
            send(m.resize(_int(req.get("id")), _int(req.get("rows")), _int(req.get("cols"))))
        else:
            send({"error": "No such operation."})


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _send_raw(conn, obj):
    try:
        conn.sendall(json.dumps(obj).encode() + b"\n")
    except OSError:
        pass
