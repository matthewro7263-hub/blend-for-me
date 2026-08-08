"""Loopback TCP bridge.

Threading model — this is the part that must not be got wrong:

* ``bpy`` is **not** thread-safe. The accept loop and the per-connection reader
  threads never touch ``bpy``. They only parse JSON and push work onto
  :data:`_INBOX`.
* A ``bpy.app.timers`` callback (:func:`_pump`) runs on Blender's **main thread**,
  drains :data:`_INBOX`, executes the handler, and hands the result back to the
  waiting connection thread through that request's own reply queue.

Anything that reads or writes Blender state therefore runs on the main thread,
serialised, one command at a time.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
import traceback
from collections import deque
from typing import Optional

from . import protocol, registry

# --------------------------------------------------------------------------
# module state
# --------------------------------------------------------------------------

_server: Optional["_BridgeServer"] = None
_INBOX: "queue.Queue[_Job]" = queue.Queue()

#: Rolling log shown in the N-panel. Newest last.
LOG: "deque[str]" = deque(maxlen=200)
_log_lock = threading.Lock()

#: Set by the pump so the UI can show liveness without touching the socket.
STATS = {"commands": 0, "errors": 0, "last_cmd": "", "last_ms": 0.0}


def log(msg: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with _log_lock:
        LOG.append(f"[{stamp}] {msg}")


def recent_log(n: int = 20) -> list[str]:
    with _log_lock:
        return list(LOG)[-n:]


class _Job:
    """One in-flight request handed from a reader thread to the main thread."""

    __slots__ = ("msg_id", "cmd", "params", "reply", "queued_at")

    def __init__(self, msg_id: str, cmd: str, params: dict):
        self.msg_id = msg_id
        self.cmd = cmd
        self.params = params
        self.reply: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        self.queued_at = time.monotonic()


# --------------------------------------------------------------------------
# main-thread execution
# --------------------------------------------------------------------------

def _execute(job: _Job) -> dict:
    """Run one command. **Main thread only.**"""
    import bpy  # noqa: F401  (imported here so the module stays importable w/o Blender)

    handler = registry.HANDLERS.get(job.cmd)
    if handler is None:
        known = ", ".join(sorted(registry.HANDLERS)[:12])
        return protocol.err(
            job.msg_id,
            f"unknown command {job.cmd!r}. Known commands include: {known} ... "
            f"(call 'list_commands' for the full catalog)",
        )

    meta = registry.META.get(job.cmd, {})

    # An unrecognised key is nearly always a wrong-parameter-name mistake, and
    # handlers that fall back to the active object would otherwise act on the
    # wrong one and report success. Fail loudly instead.
    accepted = meta.get("params")
    if accepted:
        allowed = set(accepted) | {"_timeout"}
        unknown = sorted(k for k in job.params if k not in allowed)
        if unknown:
            return protocol.err(
                job.msg_id,
                f"{job.cmd} does not accept {unknown}. Accepted parameters: "
                f"{sorted(accepted)}. (The object-identifying key is not uniform "
                f"across modules — mesh.* uses 'name', most others use 'object'.)",
            )

    if meta.get("mutates"):
        # Push an undo step *before* mutating so the agent can always step back.
        try:
            bpy.ops.ed.undo_push(message=f"agent: {job.cmd}")
        except Exception:  # pragma: no cover - undo is best-effort
            pass

    try:
        result = handler(job.params)
        return protocol.ok(job.msg_id, result)
    except Exception as exc:
        # The agent must always learn *why* something failed, so the full
        # traceback travels back over the wire.
        return protocol.err(job.msg_id, f"{type(exc).__name__}: {exc}", traceback.format_exc())


def _pump() -> float:
    """``bpy.app.timers`` callback. Drains the inbox on the main thread."""
    deadline = time.monotonic() + 0.05  # keep the UI responsive
    while time.monotonic() < deadline:
        try:
            job = _INBOX.get_nowait()
        except queue.Empty:
            break

        started = time.monotonic()
        response = _execute(job)
        elapsed_ms = (time.monotonic() - started) * 1000.0

        STATS["commands"] += 1
        STATS["last_cmd"] = job.cmd
        STATS["last_ms"] = elapsed_ms
        if not response.get("ok"):
            STATS["errors"] += 1
            log(f"{job.cmd} FAILED in {elapsed_ms:.0f}ms: {response.get('error','')[:90]}")
        else:
            log(f"{job.cmd} ok in {elapsed_ms:.0f}ms")

        try:
            job.reply.put_nowait(response)
        except queue.Full:  # reader gave up; nothing to do
            pass

    return 0.02  # re-arm in 20 ms


def pump_is_running() -> bool:
    try:
        import bpy

        return bpy.app.timers.is_registered(_pump)
    except Exception:
        return False


def start_pump() -> None:
    import bpy

    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, persistent=True)


def stop_pump() -> None:
    import bpy

    if bpy.app.timers.is_registered(_pump):
        try:
            bpy.app.timers.unregister(_pump)
        except ValueError:
            pass


def drain_once(timeout: float = 0.0) -> int:
    """Synchronously drain the inbox. Used by headless tests where no timer runs."""
    n = 0
    end = time.monotonic() + timeout
    while True:
        try:
            job = _INBOX.get_nowait()
        except queue.Empty:
            if time.monotonic() >= end:
                break
            time.sleep(0.005)
            continue
        job.reply.put_nowait(_execute(job))
        n += 1
    return n


# --------------------------------------------------------------------------
# socket side (never touches bpy)
# --------------------------------------------------------------------------

class _BridgeServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind loopback only: the bridge must never be reachable off-box.
        sock.bind((self.host, self.port))
        sock.listen(8)
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, name="agentmcp-accept", daemon=True)
        self._thread.start()
        log(f"listening on {self.host}:{self.port}")

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        log("stopped")

    # -- threads --------------------------------------------------------
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(None)
            with self._clients_lock:
                self._clients.append(conn)
            threading.Thread(
                target=self._client_loop, args=(conn, addr), name="agentmcp-client", daemon=True
            ).start()

    def _client_loop(self, conn: socket.socket, addr) -> None:
        log(f"client connected {addr[0]}:{addr[1]}")
        buf = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except (OSError, ConnectionResetError):
                    break
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > protocol.MAX_LINE_BYTES:
                    self._send(conn, protocol.err("", "request exceeded MAX_LINE_BYTES"))
                    break
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    line = bytes(buf[:nl])
                    del buf[: nl + 1]
                    if line.strip():
                        self._handle_line(conn, line)
        finally:
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass
            log(f"client disconnected {addr[0]}:{addr[1]}")

    def _handle_line(self, conn: socket.socket, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception as exc:
            self._send(conn, protocol.err("", f"malformed JSON: {exc}"))
            return

        msg_id = str(msg.get("id", ""))
        cmd = msg.get("cmd")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            self._send(conn, protocol.err(msg_id, "params must be an object"))
            return
        if not cmd:
            self._send(conn, protocol.err(msg_id, "missing 'cmd'"))
            return

        # `shutdown` is answered on the socket thread: it must not depend on the
        # main-thread pump still being alive.
        if cmd == "shutdown":
            self._send(conn, protocol.ok(msg_id, {"stopping": True}))
            threading.Thread(target=stop_server, daemon=True).start()
            return

        timeout = float(params.get("_timeout") or protocol.DEFAULT_TIMEOUT)
        # The client enforces its own deadline; give the main thread a little
        # extra so a slow-but-succeeding command still reports its real result.
        wait = max(1.0, timeout + 5.0)

        job = _Job(msg_id, cmd, params)
        _INBOX.put(job)
        try:
            response = job.reply.get(timeout=wait)
        except queue.Empty:
            response = protocol.err(
                msg_id,
                f"timed out after {wait:.0f}s waiting for Blender's main thread. "
                f"That budget comes from the request's '_timeout' parameter "
                f"({timeout:.0f}s, default {protocol.DEFAULT_TIMEOUT:.0f}s) plus a "
                f"5s grace period — for a genuinely slow operation, raise it "
                f"rather than retrying. If the command should have been fast, "
                f"Blender is busy: a modal operator, a render, or a blocking "
                f"dialog is holding the main thread.",
            )
        self._send(conn, response)

    @staticmethod
    def _send(conn: socket.socket, obj: dict) -> None:
        try:
            conn.sendall(protocol.encode(obj))
        except (OSError, BrokenPipeError):
            pass


# --------------------------------------------------------------------------
# public API used by the UI / operators
# --------------------------------------------------------------------------

def is_running() -> bool:
    return _server is not None


def current_port() -> Optional[int]:
    return _server.port if _server else None


def start_server(host: str = protocol.DEFAULT_HOST, port: int = protocol.DEFAULT_PORT) -> None:
    global _server
    if _server is not None:
        raise RuntimeError(f"bridge already running on port {_server.port}")
    srv = _BridgeServer(host, port)
    srv.start()          # may raise OSError(48) if the port is taken
    _server = srv
    start_pump()


def stop_server() -> None:
    global _server
    if _server is None:
        return
    srv, _server = _server, None
    srv.stop()
    stop_pump()
