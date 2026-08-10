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

TOKEN_ENV_VAR = "BLENDER_AGENT_TOKEN"
ALLOW_INSECURE_ENV_VAR = "BLENDER_AGENT_ALLOW_INSECURE"

#: Structured Error Codes
class ErrorCode:
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    WRONG_TYPE = "wrong_type"
    NEEDS_GUI = "needs_gui"
    DESTRUCTIVE_CONFIRMATION_REQUIRED = "destructive_confirmation_required"
    PERMISSION_ERROR = "permission_error"
    AUTH_REQUIRED = "auth_required"
    AUTH_FAILED = "auth_failed"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    WRONG_PEER = "wrong_peer"
    NOT_CONNECTED = "not_connected"
    TIMEOUT = "timeout"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"
    BLENDER_BUSY = "blender_busy"
    OPERATOR_CANCELLED = "operator_cancelled"
    PARTIAL_FAILURE = "partial_failure"
    INTERNAL_ERROR = "internal_error"


class ProtocolError(ValueError):
    """Raised when a wire message violates protocol rules."""
    def __init__(self, message: str, code: str = ErrorCode.VALIDATION_ERROR):
        super().__init__(message)
        self.code = code


import hmac
import secrets


def generate_pairing_token() -> str:
    """Generate a 256-bit cryptographically secure pairing token."""
    return secrets.token_hex(32)


def verify_pairing_token(token: str | None, expected_token: str | None) -> bool:
    """Compare token and expected_token in constant time."""
    if not token or not expected_token:
        return False
    return hmac.compare_digest(str(token).strip().encode("utf-8"), str(expected_token).strip().encode("utf-8"))


def encode(obj: dict) -> bytes:
    """Serialise one message to a newline-terminated UTF-8 frame."""
    import json

    return (json.dumps(obj, separators=(",", ":"), default=str) + "\n").encode("utf-8")


def ok(msg_id: str, result) -> dict:
    return {"id": str(msg_id), "ok": True, "result": result}


def err(msg_id: str, error: str, tb: str = "", code: str = ErrorCode.INTERNAL_ERROR, side_effects: bool = False) -> dict:
    return {
        "id": str(msg_id),
        "ok": False,
        "error": error,
        "error_code": code,
        "side_effects_possible": side_effects,
        "traceback": tb,
    }


def validate_request_frame(msg: dict) -> dict:
    """Validate that incoming raw dict conforms to Request frame requirements."""
    if not isinstance(msg, dict):
        raise ProtocolError("request must be a JSON object", ErrorCode.VALIDATION_ERROR)
    
    msg_id = msg.get("id")
    if msg_id is None or not isinstance(msg_id, (str, int)):
        raise ProtocolError("request requires string or integer 'id'", ErrorCode.VALIDATION_ERROR)
    
    cmd = msg.get("cmd")
    if not cmd or not isinstance(cmd, str):
        raise ProtocolError("request requires non-empty string 'cmd'", ErrorCode.VALIDATION_ERROR)
    if len(cmd) > 256:
        raise ProtocolError("command name exceeds max length of 256 chars", ErrorCode.VALIDATION_ERROR)

    params = msg.get("params")
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        raise ProtocolError("'params' must be a JSON object", ErrorCode.VALIDATION_ERROR)

    timeout = params.get("_timeout")
    if timeout is not None:
        try:
            timeout_val = float(timeout)
            if timeout_val <= 0 or not (timeout_val == timeout_val):  # NaN check
                raise ValueError()
        except (ValueError, TypeError):
            raise ProtocolError("'_timeout' must be a finite positive number", ErrorCode.VALIDATION_ERROR)

    return {"id": str(msg_id), "cmd": cmd, "params": params}


def validate_response_frame(msg: dict, expected_id: str | None = None) -> dict:
    """Validate that incoming raw dict conforms to Response frame requirements."""
    if not isinstance(msg, dict):
        raise ProtocolError("response must be a JSON object", ErrorCode.VALIDATION_ERROR)

    msg_id = msg.get("id")
    if expected_id is not None and str(msg_id) != str(expected_id):
        raise ProtocolError(f"response ID mismatch: expected {expected_id!r}, got {msg_id!r}", ErrorCode.VALIDATION_ERROR)

    if "ok" not in msg or not isinstance(msg["ok"], bool):
        raise ProtocolError("response requires boolean 'ok'", ErrorCode.VALIDATION_ERROR)

    return msg
