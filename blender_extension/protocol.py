"""Wire protocol shared by the bridge and the MCP server.

One JSON object per line, UTF-8, ``\\n``-terminated.

Request   {"id": str, "cmd": str, "params": {...}}
Response  {"id": str, "ok": true,  "result": <any>}
          {"id": str, "ok": false, "error": str, "traceback": str}

This module is deliberately dependency-free (no ``bpy``) so the MCP server can
import the exact same constants without Blender present.
"""

from __future__ import annotations

DEFAULT_PORT = 9876
DEFAULT_HOST = "127.0.0.1"
PORT_ENV_VAR = "BLENDER_AGENT_PORT"

#: Per-command wall-clock budget, in seconds, unless the caller overrides it.
DEFAULT_TIMEOUT = 10.0

#: Hard ceiling on a single line so a malformed peer cannot exhaust memory.
MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MiB; screenshots are base64 and can be large.

PROTOCOL_VERSION = 1


def encode(obj: dict) -> bytes:
    """Serialise one message to a newline-terminated UTF-8 frame."""
    import json

    return (json.dumps(obj, separators=(",", ":"), default=str) + "\n").encode("utf-8")


def ok(msg_id: str, result) -> dict:
    return {"id": msg_id, "ok": True, "result": result}


def err(msg_id: str, error: str, tb: str = "") -> dict:
    return {"id": msg_id, "ok": False, "error": error, "traceback": tb}
