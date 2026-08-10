"""End-to-end socket tests driving headless Blender over TCP."""

from __future__ import annotations

import os
import subprocess
import time
import pytest

from blender_agent_mcp.bridge_client import BridgeClient

BLENDER_BIN = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")


@pytest.fixture(scope="module")
def bridge_process():
    if not os.path.exists(BLENDER_BIN):
        pytest.skip(f"Blender executable not found at {BLENDER_BIN}")

    port = 9898
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    host_script = os.path.join(repo_dir, "tests", "headless_bridge_host.py")

    cmd = [
        BLENDER_BIN,
        "--background",
        "--factory-startup",
        "--python", host_script,
        "--",
        "--port", str(port),
        "--max-seconds", "30",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ready = False
    start_time = time.monotonic()

    while time.monotonic() - start_time < 10.0:
        if proc.poll() is not None:
            break
        line = proc.stdout.readline() if proc.stdout else ""
        if "[host] READY" in line:
            ready = True
            break
        time.sleep(0.05)

    if not ready:
        proc.kill()
        pytest.skip("Could not start headless Blender socket server")

    yield {"port": port, "process": proc}

    if proc.poll() is None:
        try:
            client = BridgeClient("127.0.0.1", port)
            client.call("shutdown", timeout=2.0)
        except Exception:
            proc.kill()
        proc.wait(timeout=3.0)


@pytest.mark.integration
def test_socket_e2e_handshake_and_ping(bridge_process):
    port = bridge_process["port"]
    client = BridgeClient("127.0.0.1", port)
    
    # Handshake runs automatically on connect
    client.connect()
    assert client.connected

    res = client.call("ping")
    assert res.get("pong") is True


@pytest.mark.integration
def test_socket_e2e_object_creation_and_query(bridge_process):
    port = bridge_process["port"]
    client = BridgeClient("127.0.0.1", port)

    res = client.call("objects.create_primitive", {"kind": "CUBE", "name": "SocketCube"})
    assert res["name"] == "SocketCube"

    info = client.call("get_object_info", {"name": "SocketCube"})
    assert info["name"] == "SocketCube"
    assert info["type"] == "MESH"
