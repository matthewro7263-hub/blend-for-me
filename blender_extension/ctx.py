"""Context-override and screenshot helpers.

Blender's context-sensitive operators (sculpt strokes, weight gradient, OpenGL
viewport renders) refuse to run unless the context they are handed actually
contains a ``VIEW_3D`` area with a ``WINDOW`` region. Under
``blender --background`` no such area exists, which is why those tools are marked
``needs_gui`` and fail with an explanation instead of a bare RuntimeError.
"""

from __future__ import annotations

import base64
import contextlib
import os
import tempfile
from typing import Iterator, Optional, Tuple

import bpy


class NeedsGUI(RuntimeError):
    """Raised when a GUI-only operation is attempted in background mode."""


def find_view3d() -> Optional[Tuple[object, object, object, object]]:
    """Return ``(window, area, region, space)`` for the largest VIEW_3D, else None.

    In ``--background`` Blender still reports the startup file's screen layout,
    areas and all, but there is no GL context behind it. Treat headless as
    "no viewport" so callers get an actionable message instead of a deep
    "Cannot use OpenGL render in background mode" further down the stack.
    """
    if bpy.app.background:
        return None
    wm = bpy.context.window_manager
    best = None
    best_area = -1
    for window in getattr(wm, "windows", []):
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            size = area.width * area.height
            if size > best_area:
                best_area = size
                best = (window, area, region, area.spaces.active)
    return best


def require_view3d() -> Tuple[object, object, object, object]:
    found = find_view3d()
    if found is None:
        raise NeedsGUI(
            "This operation needs a real 3D Viewport. Blender appears to be running "
            "headless (--background) or has no VIEW_3D area open. Run Blender "
            "normally with the Agent MCP bridge started, or use the data-API "
            "equivalent of this tool."
        )
    return found


@contextlib.contextmanager
def view3d(**extra) -> Iterator[dict]:
    """``temp_override`` onto the largest VIEW_3D area.

    Usage::

        with ctx.view3d():
            bpy.ops.sculpt.brush_stroke(stroke=[...])
    """
    window, area, region, space = require_view3d()
    override = dict(
        window=window,
        screen=window.screen,
        area=area,
        region=region,
        space_data=space,
        scene=bpy.context.scene,
    )
    override.update(extra)
    with bpy.context.temp_override(**override):
        yield override


@contextlib.contextmanager
def temp_attrs(obj, **values) -> Iterator[None]:
    """Set attributes for the duration of the block, then restore them."""
    saved = {}
    try:
        for key, value in values.items():
            saved[key] = getattr(obj, key)
            setattr(obj, key, value)
        yield
    finally:
        for key, value in saved.items():
            try:
                setattr(obj, key, value)
            except Exception:
                pass


@contextlib.contextmanager
def preserve_context(
    active_object: Optional[object] = None,
    restore_elements: bool = False,
) -> Iterator[dict]:
    """Snapshot and restore mode, active/selected objects, mesh select mode, and element selection.

    Args:
        active_object: Optional object to set active during block.
        restore_elements: If True, snapshots and restores vertex/edge/face selection state.
    """
    view_layer = bpy.context.view_layer
    prior_active = view_layer.objects.active
    prior_selected = [o for o in view_layer.objects if o.select_get()]
    prior_mode = bpy.context.mode

    prior_mesh_select_mode = None
    vert_sel, edge_sel, poly_sel = None, None, None

    target = active_object or prior_active
    if target is not None and getattr(target, "type", None) == "MESH" and hasattr(target, "data") and target.data is not None:
        prior_mesh_select_mode = tuple(bpy.context.tool_settings.mesh_select_mode)
        if restore_elements and hasattr(target.data, "vertices"):
            vert_sel = [v.index for v in target.data.vertices if v.select]
            edge_sel = [e.index for e in target.data.edges if e.select]
            poly_sel = [p.index for p in target.data.polygons if p.select]

    if active_object is not None:
        view_layer.objects.active = active_object
        active_object.select_set(True)

    snapshot = {
        "prior_active": prior_active,
        "prior_selected": prior_selected,
        "prior_mode": prior_mode,
        "prior_mesh_select_mode": prior_mesh_select_mode,
    }

    try:
        yield snapshot
    finally:
        # 1. Restore object selection and active object
        for o in view_layer.objects:
            try:
                o.select_set(o in prior_selected)
            except Exception:
                pass
        if prior_active is not None and prior_active.name in view_layer.objects:
            view_layer.objects.active = prior_active

        # 2. Restore mode
        if bpy.context.mode != prior_mode:
            target_mode = prior_mode
            if prior_mode == "EDIT_MESH":
                target_mode = "EDIT"
            try:
                bpy.ops.object.mode_set(mode=target_mode)
            except Exception:
                with contextlib.suppress(Exception):
                    bpy.ops.object.mode_set(mode="OBJECT")

        # 3. Restore mesh select mode & element selections
        if target is not None and getattr(target, "type", None) == "MESH" and target.data is not None:
            if prior_mesh_select_mode is not None:
                with contextlib.suppress(Exception):
                    bpy.context.tool_settings.mesh_select_mode = prior_mesh_select_mode

            if restore_elements:
                if vert_sel is not None:
                    for idx in vert_sel:
                        if idx < len(target.data.vertices):
                            target.data.vertices[idx].select = True
                if edge_sel is not None:
                    for idx in edge_sel:
                        if idx < len(target.data.edges):
                            target.data.edges[idx].select = True
                if poly_sel is not None:
                    for idx in poly_sel:
                        if idx < len(target.data.polygons):
                            target.data.polygons[idx].select = True


# ---------------------------------------------------------------------------
# image capture
# ---------------------------------------------------------------------------


def _fit(width: int, height: int, max_size: int) -> Tuple[int, int]:
    """Scale (w, h) down so the long edge is at most ``max_size``."""
    longest = max(width, height)
    if max_size <= 0 or longest <= max_size:
        return max(1, width), max(1, height)
    scale = max_size / float(longest)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _read_png(path: str) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    return {"png_b64": base64.b64encode(raw).decode("ascii"), "bytes": len(raw)}


def capture_viewport(
    shading_mode: Optional[str] = None,
    camera_view: bool = False,
    max_size: int = 1024,
) -> dict:
    """Render the 3D viewport to a PNG and return it base64-encoded.

    Uses ``render.opengl`` rather than a raw framebuffer grab so the result is a
    clean image of the scene (no UI chrome, no overlay gizmos at odd DPI).

    Args:
        shading_mode: Temporarily force ``WIREFRAME``/``SOLID``/``MATERIAL``/``RENDERED``.
        camera_view: Render through the scene camera instead of the current view.
        max_size: Cap for the longest edge, in pixels.
    """
    window, area, region, space = require_view3d()
    scene = bpy.context.scene

    width, height = _fit(region.width, region.height, max_size)

    out_dir = tempfile.mkdtemp(prefix="agentmcp-shot-")
    out_path = os.path.join(out_dir, "viewport.png")

    render = scene.render
    saved = {
        "filepath": render.filepath,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "file_format": render.image_settings.file_format,
        "color_mode": render.image_settings.color_mode,
    }
    saved_shading = space.shading.type if shading_mode else None

    try:
        render.filepath = out_path
        render.resolution_x = width
        render.resolution_y = height
        render.resolution_percentage = 100
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGBA"
        if shading_mode:
            space.shading.type = shading_mode.upper()

        with bpy.context.temp_override(
            window=window, screen=window.screen, area=area, region=region,
            space_data=space, scene=scene,
        ):
            # view_context=True renders the viewport's own view; False uses the
            # scene camera.
            bpy.ops.render.opengl(write_still=True, view_context=not camera_view)

        result = _read_png(out_path)
        result.update(width=width, height=height,
                      shading=space.shading.type, camera_view=bool(camera_view))
        return result
    finally:
        if saved_shading is not None:
            try:
                space.shading.type = saved_shading
            except Exception:
                pass
        render.filepath = saved["filepath"]
        render.resolution_x = saved["resolution_x"]
        render.resolution_y = saved["resolution_y"]
        render.resolution_percentage = saved["resolution_percentage"]
        render.image_settings.file_format = saved["file_format"]
        render.image_settings.color_mode = saved["color_mode"]
        with contextlib.suppress(OSError):
            os.remove(out_path)
        with contextlib.suppress(OSError):
            os.rmdir(out_dir)


def enable_cycles_metal() -> dict:
    """Enable the Cycles add-on and select Metal GPU compute when available.

    macOS-specific: Apple Silicon and recent Intel Macs expose ``METAL`` as a
    Cycles compute device type. Falls back to CPU with a note when unavailable.
    """
    import addon_utils

    info = {"enabled": False, "device_type": None, "devices": [], "note": ""}
    try:
        addon_utils.enable("cycles", default_set=False, persistent=True)
        info["enabled"] = True
    except Exception as exc:
        info["note"] = f"could not enable cycles add-on: {exc}"
        return info

    addon = bpy.context.preferences.addons.get("cycles")
    if addon is None:
        info["note"] = "cycles add-on not present in preferences"
        return info

    prefs = addon.preferences
    available = [i.identifier for i in prefs.bl_rna.properties["compute_device_type"].enum_items]
    info["available_device_types"] = available

    if "METAL" in available:
        try:
            prefs.compute_device_type = "METAL"
            prefs.get_devices()
            for dev in prefs.devices:
                if dev.type == "METAL":
                    dev.use = True
                info["devices"].append({"name": dev.name, "type": dev.type, "use": dev.use})
            info["device_type"] = "METAL"
        except Exception as exc:
            info["note"] = f"METAL present but not selectable: {exc}"
    else:
        info["note"] = "METAL not available; Cycles will use CPU"

    return info
