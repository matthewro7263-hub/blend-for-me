"""TCP client for the in-Blender bridge.

Single long-lived connection with lazy reconnect. Commands are serialised behind
a lock: the bridge executes one command at a time on Blender's main thread, so
pipelining would buy nothing and would complicate response matching.
"""

from __future__ import annotations

import itertools
import json
import os
import socket
import threading
from typing import Any, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
PORT_ENV_VAR = "BLENDER_AGENT_PORT"
HOST_ENV_VAR = "BLENDER_AGENT_HOST"
DEFAULT_TIMEOUT = 10.0

NOT_CONNECTED_HELP = (
    "Could not reach Blender on {host}:{port}.\n"
    "Fix it like this:\n"
    "  1. Open Blender (the GUI app, not --background).\n"
    "  2. Edit ▸ Preferences ▸ Add-ons — enable 'Agent MCP Bridge'.\n"
    "  3. In the 3D Viewport press N ▸ 'Agent MCP' tab ▸ Start Server.\n"
    "  4. Confirm the port matches ({port}); override it with the "
    "{env} environment variable if you changed it.\n"
    "Underlying error: {err}"
)


class BridgeError(RuntimeError):
    """A command reached Blender but failed there. Carries the remote traceback."""

    def __init__(self, message: str, remote_traceback: str = "", command: str = ""):
        super().__init__(message)
        self.remote_traceback = remote_traceback
        self.command = command

    def __str__(self) -> str:
        base = super().__str__()
        if self.remote_traceback:
            return f"{base}\n\n--- Blender traceback ---\n{self.remote_traceback}"
        return base


class NotConnected(RuntimeError):
    """The bridge is unreachable. The message is written for an agent to act on."""


class BridgeClient:
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or os.environ.get(HOST_ENV_VAR, DEFAULT_HOST)
        self.port = int(port or os.environ.get(PORT_ENV_VAR, DEFAULT_PORT))
        self._sock: Optional[socket.socket] = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    # -- connection -----------------------------------------------------
    def connect(self, timeout: float = 5.0) -> None:
        self.close()
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
        except OSError as exc:
            raise NotConnected(
                NOT_CONNECTED_HELP.format(host=self.host, port=self.port,
                                          env=PORT_ENV_VAR, err=exc)
            ) from exc
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._buf.clear()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buf.clear()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # -- request/response ----------------------------------------------
    def call(self, cmd: str, params: Optional[dict] = None,
             timeout: float = DEFAULT_TIMEOUT) -> Any:
        """Send one command and return its ``result``.

        Raises:
            NotConnected: the bridge could not be reached (actionable message).
            BridgeError: the command ran in Blender and failed there.
            TimeoutError: no response within ``timeout``.
        """
        payload = dict(params or {})
        payload["_timeout"] = timeout

        with self._lock:
            for attempt in (1, 2):  # one transparent reconnect
                if self._sock is None:
                    self.connect()
                msg_id = str(next(self._ids))
                frame = (json.dumps({"id": msg_id, "cmd": cmd, "params": payload},
                                    separators=(",", ":")) + "\n").encode("utf-8")
                try:
                    self._sock.sendall(frame)
                    response = self._read_response(msg_id, timeout)
                except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                    self.close()
                    if attempt == 2:
                        raise NotConnected(
                            NOT_CONNECTED_HELP.format(host=self.host, port=self.port,
                                                      env=PORT_ENV_VAR, err=exc)
                        ) from exc
                    continue  # reconnect and retry once
                break

        if response.get("ok"):
            return response.get("result")
        raise BridgeError(response.get("error", "unknown error"),
                          response.get("traceback", ""), cmd)

    def _read_response(self, expect_id: str, timeout: float) -> dict:
        assert self._sock is not None
        self._sock.settimeout(timeout)
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                if not line.strip():
                    continue
                msg = json.loads(line.decode("utf-8"))
                # Ignore stale replies from a previous timed-out call.
                if msg.get("id") and msg.get("id") != expect_id:
                    continue
                return msg
            try:
                chunk = self._sock.recv(1 << 16)
            except socket.timeout as exc:
                raise TimeoutError(
                    f"Blender did not respond within {timeout:.0f}s. Long operations "
                    f"(remesh, render, quadriflow) accept a larger 'timeout' argument."
                ) from exc
            if not chunk:
                raise ConnectionResetError("bridge closed the connection")
            self._buf.extend(chunk)


_client: Optional[BridgeClient] = None
_client_lock = threading.Lock()


def get_client() -> BridgeClient:
    """Process-wide singleton client."""
    global _client
    with _client_lock:
        if _client is None:
            _client = BridgeClient()
        return _client


def call(cmd: str, params: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
    return get_client().call(cmd, params, timeout)


def reconnect() -> dict:
    client = get_client()
    client.connect()
    return {"connected": True, "host": client.host, "port": client.port}
