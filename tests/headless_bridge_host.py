"""Run the bridge inside headless Blender so tests can drive it over TCP.

    blender --background --python tests/headless_bridge_host.py -- --port 9899

Loads ``blender_extension`` as a plain package (no install step) and pumps the
main-thread queue in a loop until the peer sends ``shutdown`` or the idle
timeout expires. GUI-only commands correctly fail here — that is the point.
"""

from __future__ import annotations

import os
import sys
import time

_ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _arg(flag: str, default):
    if flag in _ARGV:
        return _ARGV[_ARGV.index(flag) + 1]
    return default


PORT = int(_arg("--port", os.environ.get("BLENDER_AGENT_PORT", 9899)))
MAX_SECONDS = float(_arg("--max-seconds", 120))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import blender_extension  # noqa: E402
from blender_extension import bridge, registry  # noqa: E402

print(f"[host] commands registered: {len(registry.HANDLERS)}", flush=True)
print(f"[host] handler modules loaded: {blender_extension.handlers.loaded()}", flush=True)
failed = blender_extension.handlers.failed()
if failed:
    print(f"[host] HANDLER IMPORT FAILURES: {failed}", flush=True)

bridge.start_server("127.0.0.1", PORT)
print(f"[host] READY port={PORT}", flush=True)

deadline = time.monotonic() + MAX_SECONDS
try:
    while bridge.is_running() and time.monotonic() < deadline:
        # No Blender event loop in --background, so pump the queue by hand.
        if bridge.drain_once() == 0:
            time.sleep(0.005)
finally:
    bridge.stop_server()
    print("[host] stopped", flush=True)
