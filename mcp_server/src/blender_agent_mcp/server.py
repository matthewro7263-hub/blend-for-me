"""FastMCP-style MCP server exposing Blender to agents over stdio.

The ergonomic server class in mcp SDK 2.x is ``MCPServer`` (``mcp.server.fastmcp``
was removed in 2.0.0) — see docs/BLENDER_5X_API_NOTES.md.
"""

from __future__ import annotations

import base64
from typing import Any, Optional

from mcp.server.mcpserver import Image, MCPServer

from . import bridge_client

INSTRUCTIONS = """\
Drive Blender directly: model, sculpt, rig, weight-paint, shade, animate and export.

Connection: tools relay to a loopback bridge inside a running Blender. If a tool
reports it cannot reach Blender, tell the user to enable the 'Blender for me'
add-on and press Start Server in the 3D Viewport's N-panel ▸ Blender for me tab.

Working effectively:
* Call `get_scene_info` before acting so you address objects by their real names.
* Sculpting and painting are visual. Take a `viewport_screenshot` after each pass;
  most stroke tools return one automatically.
* Prefer the specific tool over `execute_python`. Use `describe_api` when you are
  unsure of an operator's exact parameters in this Blender build — it reads live
  RNA, so it is never out of date.
* `search_blender_manual` / `search_python_api` give prose and deep links;
  `describe_api` gives the live signature. Use them for different questions.
* Tools whose description says "GUI Blender only" fail under `blender --background`.
"""

mcp = MCPServer(
    name="Blender for me",
    version="0.0.1-beta",
    instructions=INSTRUCTIONS,
)


# ---------------------------------------------------------------------------
# helpers shared by every tool module
# ---------------------------------------------------------------------------

def call(cmd: str, params: Optional[dict] = None, timeout: float = 10.0) -> Any:
    """Relay one command to Blender, letting errors surface with their traceback."""
    return bridge_client.call(cmd, params or {}, timeout=timeout)


def png_image(payload: dict) -> Image:
    """Turn a bridge ``{"png_b64": ...}`` payload into an MCP image content block."""
    return Image(data=base64.b64decode(payload["png_b64"]), format="png")


def image_with_metadata(payload: dict) -> list:
    """Return structured JSON metadata text alongside the MCP Image content block."""
    import json
    meta = dict(payload)
    b64_data = meta.pop("png_b64", None)
    img_block = Image(data=base64.b64decode(b64_data), format="png") if b64_data else None
    meta_text = json.dumps(meta, default=str)
    if img_block:
        return [meta_text, img_block]
    return [meta_text]


def clean(**kwargs) -> dict:
    """Drop None values so Blender-side defaults win."""
    return {k: v for k, v in kwargs.items() if v is not None}


def register_all() -> None:
    """Import every tool module; each one registers its tools on ``mcp``."""
    from . import tools  # noqa: F401

    tools.load_all(mcp)


def main() -> None:
    register_all()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
