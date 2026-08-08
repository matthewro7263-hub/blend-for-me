"""System, introspection and escape-hatch tools."""

from __future__ import annotations

from typing import Any, Optional

from .. import bridge_client
from ..server import call, clean, png_image


def register(mcp) -> None:

    @mcp.tool()
    def health() -> dict:
        """Check whether the Blender bridge is reachable, and report what it can do.

        Call this first when anything else fails. Returns connection state plus,
        when connected, the Blender version, whether Blender is running headless,
        and whether a 3D Viewport exists (GUI-only tools need one).
        """
        try:
            info = call("get_version", timeout=5.0)
        except bridge_client.NotConnected as exc:
            return {"connected": False, "error": str(exc)}
        except Exception as exc:
            return {"connected": False, "error": f"{type(exc).__name__}: {exc}"}
        client = bridge_client.get_client()
        return {
            "connected": True,
            "host": client.host,
            "port": client.port,
            "blender": info.get("blender_version_string"),
            "background": info.get("background"),
            "has_view3d": info.get("has_view3d"),
            "gui_tools_available": bool(info.get("has_view3d")),
            "commands_served": info.get("stats", {}).get("commands"),
        }

    @mcp.tool()
    def get_blender_version() -> dict:
        """Blender / Python versions, build info and bridge statistics."""
        return call("get_version", timeout=5.0)

    @mcp.tool()
    def reconnect() -> dict:
        """Force-drop and re-establish the TCP connection to Blender.

        Use after restarting Blender or after restarting the bridge from the
        N-panel; ordinary calls already reconnect once transparently.
        """
        return bridge_client.reconnect()

    @mcp.tool()
    def list_bridge_commands() -> dict:
        """List every command the connected Blender bridge serves.

        Each entry reports `mutates` (an undo step is pushed first) and
        `needs_gui` (fails under `blender --background`). Useful for spotting a
        version mismatch between this MCP server and the installed extension.
        """
        return call("list_commands", timeout=5.0)

    # -- scene ---------------------------------------------------------
    @mcp.tool()
    def get_scene_info(limit: int = 200) -> dict:
        """Snapshot of the scene: objects, collections, active object, mode, frame.

        Start here. Almost every other tool addresses objects by name, and this is
        how you learn the real names rather than guessing. `limit` caps the object
        list (the count is always exact).
        """
        return call("get_scene_info", {"limit": limit})

    @mcp.tool()
    def list_objects(type_filter: Optional[str] = None, limit: int = 500) -> dict:
        """List scene objects, optionally of one type.

        Args:
            type_filter: Blender type id, e.g. MESH, ARMATURE, CAMERA, LIGHT,
                EMPTY, CURVE. Omit for all objects.
            limit: Maximum objects returned.
        """
        return call("list_objects", clean(type_filter=type_filter, limit=limit))

    @mcp.tool()
    def get_object_info(name: str) -> dict:
        """Everything about one object: transforms, mesh stats, modifiers, groups, materials.

        Includes `matrix_world`, so this is how you convert between local and
        world space before placing sculpt strokes or empties.
        """
        return call("get_object_info", {"name": name})

    @mcp.tool()
    def set_mode(mode: str, object: Optional[str] = None) -> dict:
        """Switch interaction mode for an object.

        Args:
            mode: OBJECT, EDIT, SCULPT, POSE, WEIGHT_PAINT, VERTEX_PAINT, TEXTURE_PAINT.
            object: Object to make active first. Omit to use the current active object.

        Many operators silently do nothing in the wrong mode, so set the mode
        explicitly rather than assuming it carried over from a previous call.
        """
        return call("set_mode", clean(mode=mode, object=object))

    # -- undo ----------------------------------------------------------
    @mcp.tool()
    def undo_checkpoint(label: str = "agent checkpoint") -> dict:
        """Push a named undo step, so you can return to this exact state later.

        Every mutating tool already pushes its own step; use this to mark a
        milestone before a risky multi-step experiment.
        """
        return call("undo_checkpoint", {"label": label})

    @mcp.tool()
    def undo() -> dict:
        """Step one undo level back."""
        return call("undo")

    @mcp.tool()
    def redo() -> dict:
        """Step one undo level forward."""
        return call("redo")

    # -- escape hatch --------------------------------------------------
    @mcp.tool()
    def execute_python(code: str, timeout: float = 30.0) -> dict:
        """Run arbitrary Python inside Blender. The escape hatch — prefer a real tool.

        Runs on Blender's main thread with `bpy` in scope. Returns captured
        stdout, the repr of a trailing expression, and the full traceback on
        failure (the call itself does not raise, so you always get the diagnosis).

        Use it for genuinely novel operations. For anything the tool catalog
        covers, the dedicated tool is safer: it pushes undo, validates arguments
        and returns a structured result.
        """
        return call("execute_python", {"code": code}, timeout=timeout)

    @mcp.tool()
    def describe_api(path: str) -> dict:
        """Introspect a live `bpy.ops.*` operator or `bpy.types.*` type via RNA.

        This reads the *running* Blender build, so unlike documentation it can
        never be stale or version-wrong. Use it whenever you are unsure of an
        operator's parameter names, enum values or defaults.

        Args:
            path: e.g. `bpy.ops.sculpt.brush_stroke`, `bpy.ops.object` (lists the
                module), `bpy.types.Brush`, `bpy.types.Sculpt`.

        For conceptual "how do I do X" questions use `search_blender_manual`
        instead — this tool returns signatures, not explanations.
        """
        return call("describe_api", {"path": path}, timeout=15.0)

    # -- imagery -------------------------------------------------------
    @mcp.tool()
    def viewport_screenshot(
        shading_mode: Optional[str] = None,
        camera_view: bool = False,
        max_size: int = 1024,
    ) -> Any:
        """See the 3D viewport. Returns an image you can actually look at. GUI Blender only.

        This is your eyes — take one after any visual change (sculpt pass, weight
        edit, material tweak) instead of assuming the result.

        Args:
            shading_mode: Force WIREFRAME, SOLID, MATERIAL or RENDERED for this
                shot only; the viewport is restored afterwards. Omit to keep the
                current shading.
            camera_view: Render through the scene camera instead of the current
                viewport view.
            max_size: Longest edge in pixels (default 1024). Raise only when you
                need to inspect fine detail — large images cost a lot of context.
        """
        payload = call(
            "viewport_screenshot",
            clean(shading_mode=shading_mode, camera_view=camera_view, max_size=max_size),
            timeout=60.0,
        )
        return png_image(payload)

    @mcp.tool()
    def render_frame(
        engine: Optional[str] = None,
        resolution: Optional[list[int]] = None,
        samples: Optional[int] = None,
        timeout: float = 300.0,
    ) -> Any:
        """Render the current frame properly and return the image.

        Much slower than `viewport_screenshot` — use that for iteration and this
        for a final look.

        Args:
            engine: BLENDER_EEVEE or CYCLES. Choosing CYCLES enables the Cycles
                add-on and selects Metal GPU compute on macOS when available.
            resolution: [width, height] in pixels. Omit to keep the scene's.
            samples: Render samples (Cycles) or TAA samples (EEVEE).
            timeout: Seconds to wait. Raise for heavy Cycles renders.

        Scene render settings are restored afterwards.
        """
        payload = call(
            "render_frame",
            clean(engine=engine, resolution=resolution, samples=samples),
            timeout=timeout,
        )
        return png_image(payload)
