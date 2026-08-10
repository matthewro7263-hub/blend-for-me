"""Unit tests for the MCP-side bridge client. No Blender, no real sockets."""

from __future__ import annotations

import json
import socket
import threading

import pytest

from blender_agent_mcp import bridge_client
from blender_agent_mcp.bridge_client import BridgeClient, BridgeError, NotConnected


class FakeBridge:
    """A minimal in-process server speaking the real wire protocol."""

    def __init__(self, responder=None, *, drop_after: int | None = None):
        self.responder = responder or self._echo
        self.drop_after = drop_after
        self.requests: list[dict] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @staticmethod
    def _echo(req: dict) -> dict:
        if req.get("cmd") == "handshake":
            return {"id": req["id"], "ok": True, "result": {"protocol_version": 1, "session_id": "test"}}
        return {"id": req["id"], "ok": True, "result": {"echo": req.get("params")}}

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                self._sock.settimeout(0.2)
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buf = bytearray()
        served = 0
        with conn:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buf.extend(chunk)
                while b"\n" in buf:
                    line, _, rest = bytes(buf).partition(b"\n")
                    buf = bytearray(rest)
                    if not line.strip():
                        continue
                    req = json.loads(line)
                    self.requests.append(req)
                    if req.get("cmd") == "handshake":
                        response = {"id": req["id"], "ok": True, "result": {"protocol_version": 1}}
                    else:
                        if self.drop_after is not None and served >= self.drop_after:
                            return  # simulate the bridge dying mid-session
                        served += 1
                        response = self.responder(req)
                    if response is None:
                        continue  # simulate a hang
                    conn.sendall((json.dumps(response) + "\n").encode())

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def bridge():
    server = FakeBridge()
    yield server
    server.close()


def test_reconnects_once_after_the_bridge_drops():
    """If connection drops post-send, BridgeError with outcome_unknown is raised to prevent duplicate execution."""
    server = FakeBridge(drop_after=1)
    try:
        client = BridgeClient("127.0.0.1", server.port)
        assert client.call("ping")["echo"] is not None   # first call succeeds
        with pytest.raises(BridgeError) as excinfo:
            client.call("ping")
        assert excinfo.value.code == "outcome_unknown"
    finally:
        server.close()


def test_roundtrip_returns_result(bridge):
    client = BridgeClient("127.0.0.1", bridge.port)
    assert client.call("ping", {"a": 1})["echo"]["a"] == 1


def test_request_carries_id_cmd_and_params(bridge):
    client = BridgeClient("127.0.0.1", bridge.port)
    client.call("some.cmd", {"x": 2})
    req = bridge.requests[-1]
    assert req["cmd"] == "some.cmd"
    assert req["params"]["x"] == 2
    assert req["id"]


def test_timeout_is_forwarded_to_blender(bridge):
    """The bridge needs the deadline so it can bound its own main-thread wait."""
    client = BridgeClient("127.0.0.1", bridge.port)
    client.call("slow", {}, timeout=42.0)
    assert bridge.requests[-1]["params"]["_timeout"] == 42.0


def test_ids_are_unique_across_calls(bridge):
    client = BridgeClient("127.0.0.1", bridge.port)
    for _ in range(5):
        client.call("ping")
    ids = [r["id"] for r in bridge.requests]
    assert len(set(ids)) == len(ids)


def test_remote_failure_raises_with_traceback():
    server = FakeBridge(responder=lambda req: {
        "id": req["id"], "ok": False,
        "error": "KeyError: 'Sphere'",
        "traceback": "Traceback (most recent call last):\n  ...\nKeyError",
    })
    try:
        client = BridgeClient("127.0.0.1", server.port)
        with pytest.raises(BridgeError) as excinfo:
            client.call("get_object_info", {"name": "Sphere"})
        assert "KeyError" in str(excinfo.value)
        # The remote traceback must reach the agent, not be swallowed.
        assert "Blender traceback" in str(excinfo.value)
        assert excinfo.value.command == "get_object_info"
    finally:
        server.close()


def test_unreachable_bridge_gives_actionable_message():
    # Port 1 is reserved and nothing listens there.
    client = BridgeClient("127.0.0.1", 1)
    with pytest.raises(NotConnected) as excinfo:
        client.call("ping")
    message = str(excinfo.value)
    assert "Start Server" in message
    assert "Agent MCP" in message
    assert "BLENDER_AGENT_PORT" in message


def test_no_response_raises_timeout():
    server = FakeBridge(responder=lambda req: None)  # never answers
    try:
        client = BridgeClient("127.0.0.1", server.port)
        with pytest.raises(TimeoutError):
            client.call("ping", {}, timeout=0.3)
    finally:
        server.close()


def test_reconnects_once_after_the_bridge_drops():
    """Restarting Blender must not require the agent to call reconnect by hand."""
    server = FakeBridge(drop_after=1)
    try:
        client = BridgeClient("127.0.0.1", server.port)
        assert client.call("ping")["echo"] is not None   # first call succeeds
        server.drop_after = None                          # bridge comes back
        assert client.call("ping")["echo"] is not None   # transparently retried
    finally:
        server.close()


def test_stale_reply_from_a_timed_out_call_is_skipped():
    """A late response to request N must not be returned as the answer to N+1."""
    state = {"n": 0}

    def responder(req):
        state["n"] += 1
        if state["n"] == 1:
            return {"id": "999", "ok": True, "result": "stale"}  # wrong id
        return {"id": req["id"], "ok": True, "result": "fresh"}

    server = FakeBridge(responder=responder)
    try:
        client = BridgeClient("127.0.0.1", server.port)
        # The stale frame is discarded and the loop keeps reading; the correct
        # reply only arrives once a second request is issued, so this must time
        # out rather than hand back "stale".
        with pytest.raises(TimeoutError):
            client.call("ping", {}, timeout=0.3)
    finally:
        server.close()


def test_port_comes_from_environment(monkeypatch):
    monkeypatch.setenv("BLENDER_AGENT_PORT", "12345")
    assert BridgeClient().port == 12345


def test_explicit_port_beats_environment(monkeypatch):
    monkeypatch.setenv("BLENDER_AGENT_PORT", "12345")
    assert BridgeClient(port=999).port == 999


def test_get_client_is_a_singleton():
    bridge_client._client = None
    assert bridge_client.get_client() is bridge_client.get_client()
