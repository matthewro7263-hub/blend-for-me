"""Sculpting: asset-based brushes, real brush strokes, dyntopo, remesh, masks, face sets.

Blender 5.2 specifics that shape this module (see docs/BLENDER_5X_API_NOTES.md):

* ``Brush.sculpt_tool`` no longer exists. Brushes are **assets**, activated by
  library-relative identifier, and their real names are compound
  (``Inflate/Deflate``, ``Scrape/Fill``…), so :data:`BRUSH_ALIASES` maps the
  friendly names agents actually say onto the real ones.
* ``sculpt.brush_stroke`` consumes ``OperatorStrokeElement`` dicts in which every
  key must be present. With ``override_location=False`` the 3D ``location`` array
  is authoritative, so strokes can be driven from object-space coordinates —
  ``mouse`` still matters because the brush radius is measured in screen pixels.
* Radial symmetry was removed.
"""

from __future__ import annotations

import math

import bpy

from .. import ctx
from ..registry import command

SCULPT_LIB = "brushes/essentials_brushes-mesh_sculpt.blend/Brush/{name}"

#: Friendly name -> real 5.2 essentials asset name.
BRUSH_ALIASES = {
    "crease": "Crease Sharp",
    "inflate": "Inflate/Deflate",
    "deflate": "Inflate/Deflate",
    "flatten": "Flatten/Contrast",
    "contrast": "Flatten/Contrast",
    "scrape": "Scrape/Fill",
    "fill": "Fill/Deepen",
    "deepen": "Fill/Deepen",
    "pinch": "Pinch/Magnify",
    "magnify": "Pinch/Magnify",
    "elastic deform": "Elastic Grab",
    "elastic": "Elastic Grab",
    "snakehook": "Snake Hook",
    "clay strips": "Clay Strips",
    "draw sharp": "Draw Sharp",
    "grab 2d": "Grab 2D",
    "smooth": "Smooth",
    "relax": "Relax Slide",
}


def _sculpt_settings():
    return bpy.context.scene.tool_settings.sculpt


def _unified():
    """Unified paint settings.

    These moved in 5.x: they are per paint mode now
    (``tool_settings.sculpt.unified_paint_settings``), not a single shared block
    on ``tool_settings``.
    """
    return bpy.context.scene.tool_settings.sculpt.unified_paint_settings


def _require_sculpt_session() -> None:
    """Refuse sculpt-session operators when there is no session to operate on.

    Operators that walk the PBVH (mask fills and filters, face sets, mesh
    filters) do not merely *fail* under ``blender --background`` — they
    **segfault**, taking the whole process and any unsaved work with it. Verified
    on 5.2 by running each in an isolated process. An exception here is vastly
    preferable, so this guard runs before the operator is ever invoked.
    """
    if bpy.app.background:
        raise ctx.NeedsGUI(
            "This sculpt operation needs a live sculpt session and would crash "
            "Blender in background mode. Run Blender normally (GUI) with the "
            "Agent MCP bridge started. Data-API alternatives that do work "
            "headless: sculpt.mask_from_selection, sculpt.voxel_remesh, "
            "sculpt.quadriflow_remesh, sculpt.get_state."
        )
    ctx.require_view3d()


def _active_object(name: str | None = None):
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise KeyError(f"no object named {name!r}")
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
    obj = bpy.context.view_layer.objects.active
    if obj is None:
        raise RuntimeError("no active object")
    if obj.type != "MESH":
        raise TypeError(f"{obj.name!r} is a {obj.type}, sculpting needs a MESH")
    return obj


def _ensure_sculpt_mode(name: str | None = None):
    obj = _active_object(name)
    if bpy.context.mode != "SCULPT":
        bpy.ops.object.mode_set(mode="SCULPT")
    return obj


def available_brushes() -> list:
    """Names of the bundled sculpt brush assets, read from the essentials library."""
    import os

    base = bpy.utils.system_resource("DATAFILES", path="assets/brushes")
    path = os.path.join(base or "", "essentials_brushes-mesh_sculpt.blend")
    if not os.path.isfile(path):
        return []
    with bpy.data.libraries.load(path, assets_only=True) as (src, _dst):
        return sorted(src.brushes)


def resolve_brush(name: str) -> str:
    """Map a friendly brush name onto the real 5.2 asset name.

    Raises KeyError listing every available brush when there is no match, so an
    agent can recover in one step instead of guessing again.
    """
    wanted = name.strip()
    names = available_brushes()
    lowered = {n.lower(): n for n in names}

    if wanted in names:
        return wanted
    if wanted.lower() in lowered:
        return lowered[wanted.lower()]

    alias = BRUSH_ALIASES.get(wanted.lower())
    if alias and alias in names:
        return alias

    # "clay" should not silently become "Clay Strips", but "pinch magnify" should
    # find "Pinch/Magnify": compare on alphanumerics only.
    def norm(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    normalized = {norm(n): n for n in names}
    if norm(wanted) in normalized:
        return normalized[norm(wanted)]

    prefixed = [n for n in names if n.lower().startswith(wanted.lower())]
    if len(prefixed) == 1:
        return prefixed[0]

    raise KeyError(
        f"no sculpt brush matching {name!r}. Available brushes: {names}. "
        f"Friendly aliases: {sorted(BRUSH_ALIASES)}"
    )


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

@command("sculpt.enter", mutates=False)
def enter_sculpt(params: dict) -> dict:
    """Make an object active and switch into Sculpt Mode."""
    obj = _ensure_sculpt_mode(params.get("object"))
    return {"object": obj.name, "mode": bpy.context.mode,
            "vertices": len(obj.data.vertices)}


@command("sculpt.get_state", mutates=False)
def get_sculpt_state(params: dict) -> dict:
    """Active brush, size/strength, dyntopo state, symmetry and mask/face-set presence."""
    settings = _sculpt_settings()
    unified = _unified()
    brush = settings.brush
    obj = bpy.context.view_layer.objects.active

    state = {
        "mode": bpy.context.mode,
        "object": obj.name if obj else None,
        "brush": None,
        "unified": {
            "use_unified_size": unified.use_unified_size,
            "use_unified_strength": unified.use_unified_strength,
            "size": unified.size,
            "strength": unified.strength,
        },
        "symmetry": {
            "x": settings.use_symmetry_x,
            "y": settings.use_symmetry_y,
            "z": settings.use_symmetry_z,
            "feather": settings.use_symmetry_feather,
            "radial_supported": hasattr(settings, "radial_symmetry"),
        },
        "dyntopo": {
            "enabled": bool(getattr(bpy.context.sculpt_object, "use_dynamic_topology_sculpting", False))
            if hasattr(bpy.context, "sculpt_object") else None,
            "detail_type_method": settings.detail_type_method,
            "detail_refine_method": settings.detail_refine_method,
            "detail_size": settings.detail_size,
            "detail_percent": settings.detail_percent,
            "constant_detail_resolution": settings.constant_detail_resolution,
        },
    }
    # `use_dynamic_topology_sculpting` lives on the context in some builds; fall
    # back to the operator's poll state rather than reporting a wrong value.
    try:
        state["dyntopo"]["enabled"] = bool(bpy.context.object.use_dynamic_topology_sculpting)
    except AttributeError:
        try:
            state["dyntopo"]["enabled"] = bool(bpy.context.sculpt_object.use_dynamic_topology_sculpting)
        except Exception:
            state["dyntopo"]["enabled"] = None

    if brush is not None:
        state["brush"] = {
            "name": brush.name,
            "size_px": brush.size,
            "strength": brush.strength,
            "direction": brush.direction,
            "hardness": brush.hardness,
            "auto_smooth_factor": brush.auto_smooth_factor,
            "normal_radius_factor": brush.normal_radius_factor,
            "falloff_shape": brush.falloff_shape,
            "use_frontface": brush.use_frontface,
        }

    if obj is not None and obj.type == "MESH":
        mesh = obj.data
        mask_layer = mesh.attributes.get(".sculpt_mask")
        state["has_mask"] = mask_layer is not None
        fs = mesh.attributes.get(".sculpt_face_set")
        state["has_face_sets"] = fs is not None
        state["vertices"] = len(mesh.vertices)
        multires = next((m for m in obj.modifiers if m.type == "MULTIRES"), None)
        if multires:
            state["multires"] = {
                "name": multires.name, "levels": multires.levels,
                "sculpt_levels": multires.sculpt_levels,
                "render_levels": multires.render_levels,
                "total_levels": multires.total_levels,
            }
    return state


@command("sculpt.list_brushes", mutates=False)
def list_brushes(params: dict) -> dict:
    """Every bundled sculpt brush asset name, plus the friendly-name aliases."""
    return {"brushes": available_brushes(), "aliases": BRUSH_ALIASES}


@command("sculpt.set_brush", mutates=False)
def set_brush(params: dict) -> dict:
    """Activate a sculpt brush asset and set its size/strength/direction/etc."""
    _ensure_sculpt_mode(params.get("object"))

    result = {}
    name = params.get("name")
    if name:
        real = resolve_brush(name)
        bpy.ops.brush.asset_activate(
            asset_library_type="ESSENTIALS",
            relative_asset_identifier=SCULPT_LIB.format(name=real),
        )
        result["brush"] = real
        if real != name:
            result["resolved_from"] = name

    settings = _sculpt_settings()
    brush = settings.brush
    if brush is None:
        raise RuntimeError("no active sculpt brush after activation")
    result.setdefault("brush", brush.name)

    unified = _unified()

    # Writing brush.size is silently ignored while unified size is on, so write
    # whichever field the UI is actually reading.
    size = params.get("size_px")
    if size is not None:
        if unified.use_unified_size:
            unified.size = int(size)
            result["size_written_to"] = "unified_paint_settings.size"
        else:
            brush.size = int(size)
            result["size_written_to"] = "brush.size"
        result["size_px"] = int(size)

    strength = params.get("strength")
    if strength is not None:
        if unified.use_unified_strength:
            unified.strength = float(strength)
            result["strength_written_to"] = "unified_paint_settings.strength"
        else:
            brush.strength = float(strength)
            result["strength_written_to"] = "brush.strength"
        result["strength"] = float(strength)

    simple = {
        "direction": ("direction", lambda v: str(v).upper()),
        "hardness": ("hardness", float),
        "auto_smooth": ("auto_smooth_factor", float),
        "normal_radius": ("normal_radius_factor", float),
        "falloff_shape": ("falloff_shape", lambda v: str(v).upper()),
        "use_frontface": ("use_frontface", bool),
    }
    for key, (attr, cast) in simple.items():
        if params.get(key) is not None:
            setattr(brush, attr, cast(params[key]))
            result[attr] = getattr(brush, attr)

    return result


@command("sculpt.symmetry", mutates=False)
def symmetry(params: dict) -> dict:
    """Set sculpt symmetry axes. Radial symmetry no longer exists in 5.2."""
    settings = _sculpt_settings()
    for axis in ("x", "y", "z"):
        if params.get(axis) is not None:
            setattr(settings, f"use_symmetry_{axis}", bool(params[axis]))
    if params.get("feather") is not None:
        settings.use_symmetry_feather = bool(params["feather"])

    result = {
        "x": settings.use_symmetry_x,
        "y": settings.use_symmetry_y,
        "z": settings.use_symmetry_z,
        "feather": settings.use_symmetry_feather,
        "radial_supported": hasattr(settings, "radial_symmetry"),
    }
    if params.get("radial_counts") is not None and not result["radial_supported"]:
        result["note"] = (
            "radial symmetry was removed in Blender 5.x; radial_counts ignored. "
            "Use sculpt.radial_strokes to lay down repeated strokes instead."
        )
    return result


# ---------------------------------------------------------------------------
# strokes
# ---------------------------------------------------------------------------

def _stroke_point(location, mouse, size: float, pressure: float, index: int,
                  is_start: bool) -> dict:
    """One OperatorStrokeElement. Every key must be present or the op rejects it."""
    return {
        "name": f"p{index}",
        "location": (float(location[0]), float(location[1]), float(location[2])),
        "mouse": (float(mouse[0]), float(mouse[1])),
        "mouse_event": (float(mouse[0]), float(mouse[1])),
        "pressure": float(pressure),
        "size": float(size),
        "x_tilt": 0.0,
        "y_tilt": 0.0,
        "time": float(index) * 0.01,
        "is_start": bool(is_start),
    }


def _run_stroke(obj, points: list, space: str, mode: str, brush_size: float | None):
    """Project object/world points to the region and run sculpt.brush_stroke.

    Returns (stroke_length, dropped) where ``dropped`` lists points that fell
    behind the camera — reported rather than silently skipped, because a stroke
    that half-missed looks like a brush problem otherwise.
    """
    from bpy_extras import view3d_utils

    window, area, region, space_data = ctx.require_view3d()
    rv3d = space_data.region_3d

    matrix = obj.matrix_world
    inverse = matrix.inverted()

    settings = _sculpt_settings()
    unified = _unified()
    default_size = float(
        unified.size if unified.use_unified_size
        else (settings.brush.size if settings.brush else 50)
    )

    stroke = []
    dropped = []
    for index, point in enumerate(points):
        location = point["location"] if isinstance(point, dict) else point
        # brush_stroke wants object space; convert if the caller gave world space.
        if space.upper() == "WORLD":
            obj_co = inverse @ _vector(location)
            world_co = _vector(location)
        else:
            obj_co = _vector(location)
            world_co = matrix @ obj_co

        region_co = view3d_utils.location_3d_to_region_2d(region, rv3d, world_co)
        if region_co is None:
            dropped.append({"index": index, "reason": "projects behind the view"})
            continue

        size = float(point.get("size", brush_size or default_size)) if isinstance(point, dict) \
            else float(brush_size or default_size)
        pressure = float(point.get("pressure", 1.0)) if isinstance(point, dict) else 1.0
        stroke.append(_stroke_point(obj_co, region_co, size, pressure,
                                    len(stroke), is_start=not stroke))

    if len(stroke) < 2:
        raise ValueError(
            f"need at least 2 projectable points, got {len(stroke)} "
            f"({len(dropped)} dropped). Is the viewport looking at the object?"
        )

    with ctx.view3d():
        bpy.ops.sculpt.brush_stroke(
            stroke=stroke,
            mode=mode.upper(),
            override_location=False,
            ignore_background_click=False,
        )
    return len(stroke), dropped


def _vector(value):
    from mathutils import Vector

    return Vector((float(value[0]), float(value[1]), float(value[2])))


def _maybe_screenshot(params: dict) -> dict | None:
    if not params.get("return_screenshot", True):
        return None
    if ctx.find_view3d() is None:
        return None
    try:
        return ctx.capture_viewport(max_size=int(params.get("screenshot_size", 800)))
    except Exception:
        return None


def _stroke_result(obj, count: int, dropped: list, params: dict) -> dict:
    result = {"object": obj.name, "points_applied": count,
              "vertices": len(obj.data.vertices)}
    if dropped:
        result["dropped_points"] = dropped
    shot = _maybe_screenshot(params)
    if shot is not None:
        result["screenshot"] = shot
    elif params.get("return_screenshot", True):
        result["screenshot"] = None
        result["screenshot_note"] = "no 3D viewport available (headless Blender)"
    return result


@command("sculpt.stroke", mutates=True, needs_gui=True)
def stroke(params: dict) -> dict:
    """Apply a freeform brush stroke through a list of 3D points."""
    obj = _ensure_sculpt_mode(params.get("object"))
    points = params["points"]
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("'points' must be a list of at least 2 entries")
    count, dropped = _run_stroke(
        obj, points, params.get("space", "OBJECT"), params.get("mode", "NORMAL"),
        params.get("size_px"),
    )
    return _stroke_result(obj, count, dropped, params)


@command("sculpt.stroke_line", mutates=True, needs_gui=True)
def stroke_line(params: dict) -> dict:
    """Straight stroke from a to b, sampled into ``steps`` points."""
    obj = _ensure_sculpt_mode(params.get("object"))
    a = _vector(params["a"])
    b = _vector(params["b"])
    steps = max(2, int(params.get("steps", 12)))
    points = [{"location": a.lerp(b, i / (steps - 1))} for i in range(steps)]
    count, dropped = _run_stroke(obj, points, params.get("space", "OBJECT"),
                                 params.get("mode", "NORMAL"), params.get("size_px"))
    return _stroke_result(obj, count, dropped, params)


@command("sculpt.stroke_curve", mutates=True, needs_gui=True)
def stroke_curve(params: dict) -> dict:
    """Smooth stroke along a Catmull-Rom spline through the control points."""
    obj = _ensure_sculpt_mode(params.get("object"))
    controls = [_vector(p) for p in params["control_points"]]
    if len(controls) < 2:
        raise ValueError("need at least 2 control points")
    steps = max(2, int(params.get("steps", 24)))

    # Duplicate the ends so the spline actually reaches its first/last control.
    pts = [controls[0]] + controls + [controls[-1]]
    samples = []
    segments = len(pts) - 3
    for i in range(steps):
        t = i / (steps - 1) * segments
        seg = min(int(t), segments - 1)
        local = t - seg
        p0, p1, p2, p3 = pts[seg:seg + 4]
        t2, t3 = local * local, local * local * local
        samples.append({"location": 0.5 * (
            (2 * p1) + (-p0 + p2) * local
            + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
            + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
        )})

    count, dropped = _run_stroke(obj, samples, params.get("space", "OBJECT"),
                                 params.get("mode", "NORMAL"), params.get("size_px"))
    return _stroke_result(obj, count, dropped, params)


@command("sculpt.stroke_on_surface", mutates=True, needs_gui=True)
def stroke_on_surface(params: dict) -> dict:
    """Stroke along a 2D viewport path, raycast onto the model's surface."""
    from bpy_extras import view3d_utils

    obj = _ensure_sculpt_mode(params.get("object"))
    path = params["view_path_2d"]
    if not isinstance(path, list) or len(path) < 2:
        raise ValueError("'view_path_2d' must be a list of at least 2 [x, y] region points")

    window, area, region, space_data = ctx.require_view3d()
    rv3d = space_data.region_3d
    depsgraph = bpy.context.evaluated_depsgraph_get()

    normalized = bool(params.get("normalized", False))
    inverse = obj.matrix_world.inverted()

    points = []
    missed = []
    for index, coord in enumerate(path):
        x, y = float(coord[0]), float(coord[1])
        if normalized:  # 0-1 across the region, easier for an agent to reason about
            x, y = x * region.width, y * region.height
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
        hit, location, _normal, _index, _obj, _matrix = bpy.context.scene.ray_cast(
            depsgraph, origin, direction)
        if not hit:
            missed.append({"index": index, "reason": "ray missed all geometry"})
            continue
        points.append({"location": inverse @ location})

    if len(points) < 2:
        raise ValueError(
            f"only {len(points)} of {len(path)} path points hit geometry. "
            "Aim the viewport at the object, or use sculpt.stroke with 3D coordinates."
        )

    count, dropped = _run_stroke(obj, points, "OBJECT", params.get("mode", "NORMAL"),
                                 params.get("size_px"))
    result = _stroke_result(obj, count, dropped, params)
    if missed:
        result["missed_rays"] = missed
    return result


@command("sculpt.radial_strokes", mutates=True, needs_gui=True)
def radial_strokes(params: dict) -> dict:
    """``count`` strokes radiating from a centre point — a stand-in for radial symmetry."""
    obj = _ensure_sculpt_mode(params.get("object"))
    centre = _vector(params["center"])
    radius = float(params["radius"])
    count = max(1, int(params.get("count", 6)))
    steps = max(2, int(params.get("steps", 8)))
    inward = bool(params.get("inward", False))
    axis = str(params.get("axis", "Z")).upper()

    def offset(angle):
        cos, sin = math.cos(angle) * radius, math.sin(angle) * radius
        if axis == "X":
            return _vector((0.0, cos, sin))
        if axis == "Y":
            return _vector((cos, 0.0, sin))
        return _vector((cos, sin, 0.0))

    applied = 0
    all_dropped = []
    for i in range(count):
        angle = (2.0 * math.pi) * i / count
        outer = centre + offset(angle)
        a, b = (outer, centre) if inward else (centre, outer)
        points = [{"location": a.lerp(b, s / (steps - 1))} for s in range(steps)]
        n, dropped = _run_stroke(obj, points, params.get("space", "OBJECT"),
                                 params.get("mode", "NORMAL"), params.get("size_px"))
        applied += n
        all_dropped.extend(dropped)

    result = _stroke_result(obj, applied, all_dropped, params)
    result["strokes"] = count
    return result


# ---------------------------------------------------------------------------
# topology
# ---------------------------------------------------------------------------

@command("sculpt.dyntopo_enable", mutates=True)
def dyntopo_enable(params: dict) -> dict:
    """Turn on dynamic topology, configuring detail settings first."""
    _ensure_sculpt_mode(params.get("object"))
    settings = _sculpt_settings()

    mode = str(params.get("mode", "RELATIVE")).upper()
    valid = [i.identifier for i in
             bpy.types.Sculpt.bl_rna.properties["detail_type_method"].enum_items]
    if mode not in valid:
        raise ValueError(f"mode must be one of {valid}, got {mode!r}")
    settings.detail_type_method = mode

    detail = params.get("detail")
    if detail is not None:
        if mode == "CONSTANT":
            settings.constant_detail_resolution = float(detail)
        elif mode == "BRUSH":
            settings.detail_percent = float(detail)
        else:
            settings.detail_size = float(detail)

    if params.get("refine_method"):
        settings.detail_refine_method = str(params["refine_method"]).upper()

    if not _dyntopo_active():
        bpy.ops.sculpt.dynamic_topology_toggle()

    return {"enabled": _dyntopo_active(), "mode": settings.detail_type_method,
            "detail_size": settings.detail_size,
            "constant_detail_resolution": settings.constant_detail_resolution,
            "detail_percent": settings.detail_percent,
            "refine_method": settings.detail_refine_method}


def _dyntopo_active() -> bool:
    obj = bpy.context.view_layer.objects.active
    for holder in (obj, getattr(bpy.context, "sculpt_object", None)):
        if holder is not None and hasattr(holder, "use_dynamic_topology_sculpting"):
            return bool(holder.use_dynamic_topology_sculpting)
    return False


@command("sculpt.dyntopo_disable", mutates=True)
def dyntopo_disable(params: dict) -> dict:
    """Turn dynamic topology off."""
    _ensure_sculpt_mode(params.get("object"))
    if _dyntopo_active():
        bpy.ops.sculpt.dynamic_topology_toggle()
    return {"enabled": _dyntopo_active()}


@command("sculpt.dyntopo_flood_fill", mutates=True, needs_gui=True)
def dyntopo_flood_fill(params: dict) -> dict:
    """Re-tessellate the whole mesh to the current dyntopo detail setting."""
    _require_sculpt_session()
    obj = _ensure_sculpt_mode(params.get("object"))
    before = len(obj.data.vertices)
    if not _dyntopo_active():
        raise RuntimeError("dynamic topology is off — call sculpt.dyntopo_enable first")
    bpy.ops.sculpt.detail_flood_fill()
    return {"object": obj.name, "vertices_before": before,
            "vertices_after": len(obj.data.vertices)}


@command("sculpt.voxel_remesh", mutates=True)
def voxel_remesh(params: dict) -> dict:
    """Rebuild the mesh as a uniform voxel grid. The workhorse for blocking out forms."""
    obj = _active_object(params.get("object"))
    mesh = obj.data

    # object.voxel_remesh takes no arguments; everything is read off the mesh.
    if params.get("voxel_size") is not None:
        size = float(params["voxel_size"])
        if size <= 0:
            raise ValueError("voxel_size must be > 0")
        mesh.remesh_voxel_size = size
    if params.get("adaptivity") is not None:
        mesh.remesh_voxel_adaptivity = float(params["adaptivity"])
    if params.get("preserve_volume") is not None:
        mesh.use_remesh_preserve_volume = bool(params["preserve_volume"])
    if params.get("preserve_attributes") is not None:
        mesh.use_remesh_preserve_attributes = bool(params["preserve_attributes"])
    if params.get("fix_poles") is not None:
        mesh.use_remesh_fix_poles = bool(params["fix_poles"])

    before = (len(mesh.vertices), len(mesh.polygons))
    bpy.context.view_layer.objects.active = obj

    # Invoking this from Sculpt Mode routes through the sculpt undo system, which
    # segfaults under --background where there is no sculpt session. Object Mode
    # is equivalent and safe in both.
    previous_mode = bpy.context.mode
    if previous_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        bpy.ops.object.voxel_remesh()
    finally:
        if previous_mode == "SCULPT":
            bpy.ops.object.mode_set(mode="SCULPT")
    mesh = obj.data
    return {
        "object": obj.name,
        "voxel_size": mesh.remesh_voxel_size,
        "vertices_before": before[0], "vertices_after": len(mesh.vertices),
        "faces_before": before[1], "faces_after": len(mesh.polygons),
    }


@command("sculpt.quadriflow_remesh", mutates=True)
def quadriflow_remesh(params: dict) -> dict:
    """Retopologise to clean quads. Slow — expect tens of seconds."""
    obj = _active_object(params.get("object"))
    mesh = obj.data
    before = (len(mesh.vertices), len(mesh.polygons))

    kwargs = {"mode": str(params.get("mode", "FACES")).upper()}
    if kwargs["mode"] == "FACES":
        kwargs["target_faces"] = int(params.get("target_faces", 5000))
    elif kwargs["mode"] == "RATIO":
        kwargs["target_ratio"] = float(params.get("target_ratio", 1.0))
    elif kwargs["mode"] == "EDGE":
        kwargs["target_edge_length"] = float(params.get("target_edge_length", 0.1))

    for key, prop in (("use_mesh_symmetry", "use_mesh_symmetry"),
                      ("preserve_sharp", "use_preserve_sharp"),
                      ("preserve_boundary", "use_preserve_boundary"),
                      ("preserve_attributes", "preserve_attributes"),
                      ("smooth_normals", "smooth_normals")):
        if params.get(key) is not None:
            kwargs[prop] = bool(params[key])
    if params.get("seed") is not None:
        kwargs["seed"] = int(params["seed"])

    bpy.context.view_layer.objects.active = obj
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.quadriflow_remesh(**kwargs)

    mesh = obj.data
    return {"object": obj.name, "mode": kwargs["mode"],
            "vertices_before": before[0], "vertices_after": len(mesh.vertices),
            "faces_before": before[1], "faces_after": len(mesh.polygons)}


# ---------------------------------------------------------------------------
# masks
# ---------------------------------------------------------------------------

@command("sculpt.mask_box", mutates=True, needs_gui=True)
def mask_box(params: dict) -> dict:
    """Mask a rectangular screen region (region pixel coordinates)."""
    _ensure_sculpt_mode(params.get("object"))
    with ctx.view3d():
        bpy.ops.paint.mask_box_gesture(
            xmin=int(params["xmin"]), xmax=int(params["xmax"]),
            ymin=int(params["ymin"]), ymax=int(params["ymax"]),
            mode=str(params.get("mode", "VALUE")).upper(),
            value=float(params.get("value", 1.0)),
            use_front_faces_only=bool(params.get("front_faces_only", False)),
            wait_for_input=False,
        )
    return {"masked": True, "region": [params["xmin"], params["ymin"],
                                       params["xmax"], params["ymax"]]}


@command("sculpt.mask_from_selection", mutates=True)
def mask_from_selection(params: dict) -> dict:
    """Write the mask attribute directly from selected vertices (works headless)."""
    obj = _active_object(params.get("object"))
    mesh = obj.data
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    value = float(params.get("value", 1.0))
    invert = bool(params.get("invert", False))

    layer = mesh.attributes.get(".sculpt_mask")
    if layer is None:
        layer = mesh.attributes.new(".sculpt_mask", "FLOAT", "POINT")

    masked = 0
    for index, vertex in enumerate(mesh.vertices):
        selected = vertex.select
        if invert:
            selected = not selected
        layer.data[index].value = value if selected else 0.0
        masked += 1 if selected else 0
    mesh.update()
    return {"object": obj.name, "masked_vertices": masked,
            "total_vertices": len(mesh.vertices)}


@command("sculpt.mask_by_cavity", mutates=True, needs_gui=True)
def mask_by_cavity(params: dict) -> dict:
    """Mask concave/convex areas — good for isolating creases before a filter."""
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    kwargs = {}
    rna = bpy.ops.sculpt.mask_from_cavity.get_rna_type()
    available = {p.identifier for p in rna.properties}
    if "mix_mode" in available and params.get("mix_mode"):
        kwargs["mix_mode"] = str(params["mix_mode"]).upper()
    if "mix_factor" in available and params.get("mix_factor") is not None:
        kwargs["mix_factor"] = float(params["mix_factor"])
    if "settings_source" in available and params.get("settings_source"):
        kwargs["settings_source"] = str(params["settings_source"]).upper()
    bpy.ops.sculpt.mask_from_cavity(**kwargs)
    return {"masked": True, "applied": kwargs}


@command("sculpt.invert_mask", mutates=True, needs_gui=True)
def invert_mask(params: dict) -> dict:
    """Invert the sculpt mask."""
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    bpy.ops.paint.mask_flood_fill(mode="INVERT")
    return {"inverted": True}


@command("sculpt.clear_mask", mutates=True, needs_gui=True)
def clear_mask(params: dict) -> dict:
    """Clear the sculpt mask (fills it with ``value``, default 0)."""
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    bpy.ops.paint.mask_flood_fill(mode="VALUE", value=float(params.get("value", 0.0)))
    return {"cleared": True}


@command("sculpt.mask_filter", mutates=True, needs_gui=True)
def mask_filter(params: dict) -> dict:
    """Grow / shrink / smooth / sharpen the existing mask."""
    aliases = {"GROW": "GROW", "SHRINK": "SHRINK", "SMOOTH": "SMOOTH",
               "SHARPEN": "SHARPEN",
               "CONTRAST_INCREASE": "CONTRAST_INCREASE",
               "CONTRAST_DECREASE": "CONTRAST_DECREASE"}
    kind = str(params.get("filter_type", params.get("type", "SMOOTH"))).upper()
    if kind not in aliases:
        raise ValueError(f"filter_type must be one of {sorted(aliases)}, got {kind!r}")
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    bpy.ops.sculpt.mask_filter(
        filter_type=aliases[kind],
        iterations=int(params.get("iterations", 1)),
        auto_iteration_count=bool(params.get("auto_iteration_count", False)),
    )
    return {"filter_type": aliases[kind], "iterations": int(params.get("iterations", 1))}


# ---------------------------------------------------------------------------
# face sets
# ---------------------------------------------------------------------------

@command("sculpt.face_sets_create", mutates=True, needs_gui=True)
def face_sets_create(params: dict) -> dict:
    """Create a face set from the mask, the visible geometry, all, or the selection."""
    mode = str(params.get("mode", "MASKED")).upper()
    valid = {"MASKED", "VISIBLE", "ALL", "SELECTION"}
    if mode not in valid:
        raise ValueError(f"mode must be one of {sorted(valid)}, got {mode!r}")
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    bpy.ops.sculpt.face_sets_create(mode=mode)
    return {"created_from": mode}


@command("sculpt.face_sets_init", mutates=True, needs_gui=True)
def face_sets_init(params: dict) -> dict:
    """Initialise face sets from mesh structure (loose parts, materials, normals…)."""
    mode = str(params.get("mode", "LOOSE_PARTS")).upper()
    valid = [i.identifier for i in
             bpy.ops.sculpt.face_sets_init.get_rna_type().properties["mode"].enum_items]
    if mode not in valid:
        raise ValueError(f"mode must be one of {valid}, got {mode!r}")
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    kwargs = {"mode": mode}
    if params.get("threshold") is not None:
        kwargs["threshold"] = float(params["threshold"])
    bpy.ops.sculpt.face_sets_init(**kwargs)
    return {"initialized_from": mode}


@command("sculpt.face_set_visibility", mutates=True, needs_gui=True)
def face_set_visibility(params: dict) -> dict:
    """Show/hide face sets: TOGGLE, SHOW_ACTIVE or HIDE_ACTIVE."""
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    mode = str(params.get("mode", "TOGGLE")).upper()
    kwargs = {"mode": mode}
    if params.get("active_face_set") is not None:
        kwargs["active_face_set"] = int(params["active_face_set"])
    bpy.ops.sculpt.face_set_change_visibility(**kwargs)
    return {"mode": mode}


@command("sculpt.reveal_all", mutates=True, needs_gui=True)
def reveal_all(params: dict) -> dict:
    """Unhide everything hidden by face-set visibility operations."""
    _require_sculpt_session()
    _ensure_sculpt_mode(params.get("object"))
    bpy.ops.paint.hide_show_all(action="SHOW")
    return {"revealed": True}


# ---------------------------------------------------------------------------
# whole-mesh filters + multires
# ---------------------------------------------------------------------------

@command("sculpt.mesh_filter", mutates=True, needs_gui=True)
def mesh_filter(params: dict) -> dict:
    """Apply a whole-mesh filter (inflate, smooth, relax, sharpen, scale…)."""
    kind = str(params.get("type", "SMOOTH")).upper()
    valid = [i.identifier for i in
             bpy.ops.sculpt.mesh_filter.get_rna_type().properties["type"].enum_items]
    if kind not in valid:
        raise ValueError(f"type must be one of {valid}, got {kind!r}")
    _require_sculpt_session()
    obj = _ensure_sculpt_mode(params.get("object"))

    kwargs = {
        "type": kind,
        "strength": float(params.get("strength", 1.0)),
        "iteration_count": int(params.get("iterations", 1)),
    }
    if params.get("deform_axis"):
        kwargs["deform_axis"] = str(params["deform_axis"]).upper()
    if params.get("orientation"):
        kwargs["orientation"] = str(params["orientation"]).upper()

    # mesh_filter is a modal-capable operator; give it a VIEW_3D when we have one
    # so it behaves exactly as it does interactively.
    if ctx.find_view3d() is not None:
        with ctx.view3d():
            bpy.ops.sculpt.mesh_filter(**kwargs)
    else:
        bpy.ops.sculpt.mesh_filter(**kwargs)

    result = {"type": kind, "strength": kwargs["strength"],
              "iterations": kwargs["iteration_count"], "object": obj.name}
    shot = _maybe_screenshot(params) if params.get("return_screenshot") else None
    if shot is not None:
        result["screenshot"] = shot
    return result


@command("sculpt.multires_set_level", mutates=True)
def multires_set_level(params: dict) -> dict:
    """Set the Multires sculpt/viewport/render subdivision levels."""
    obj = _active_object(params.get("object"))
    modifier = next((m for m in obj.modifiers if m.type == "MULTIRES"), None)
    if modifier is None:
        raise RuntimeError(
            f"{obj.name!r} has no Multires modifier — add one with "
            "modifiers.add_multires first"
        )
    for key, attr in (("sculpt_levels", "sculpt_levels"), ("levels", "levels"),
                      ("render_levels", "render_levels")):
        if params.get(key) is not None:
            value = int(params[key])
            if value > modifier.total_levels:
                raise ValueError(
                    f"{key}={value} exceeds total_levels={modifier.total_levels}; "
                    "subdivide first with modifiers.multires_subdivide"
                )
            setattr(modifier, attr, value)
    return {"object": obj.name, "modifier": modifier.name,
            "levels": modifier.levels, "sculpt_levels": modifier.sculpt_levels,
            "render_levels": modifier.render_levels,
            "total_levels": modifier.total_levels}
