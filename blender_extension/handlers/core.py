"""System, introspection and escape-hatch commands."""

from __future__ import annotations

import contextlib
import io
import sys
import traceback

import bpy

from .. import bridge, ctx
from ..registry import HANDLERS, META, command


# ---------------------------------------------------------------------------
# liveness
# ---------------------------------------------------------------------------

@command("ping")
def ping(params: dict) -> dict:
    """Round-trip liveness check."""
    return {"pong": True, "echo": params.get("echo")}


@command("get_version")
def get_version(params: dict) -> dict:
    """Blender, Python and bridge version info."""
    return {
        "blender_version": list(bpy.app.version),
        "blender_version_string": bpy.app.version_string,
        "build_branch": bpy.app.build_branch.decode() if isinstance(bpy.app.build_branch, bytes) else bpy.app.build_branch,
        "python_version": sys.version.split()[0],
        "background": bpy.app.background,
        "online_access": bool(getattr(bpy.app, "online_access", False)),
        "has_view3d": ctx.find_view3d() is not None,
        "bridge_port": bridge.current_port(),
        "stats": dict(bridge.STATS),
    }


@command("list_commands")
def list_commands(params: dict) -> dict:
    """Full catalog of bridge commands with their mutates/needs_gui flags."""
    return {"commands": {name: META.get(name, {}) for name in sorted(HANDLERS)},
            "count": len(HANDLERS)}


# ---------------------------------------------------------------------------
# scene introspection
# ---------------------------------------------------------------------------

def _obj_brief(obj) -> dict:
    return {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "visible": obj.visible_get() if obj.name in bpy.context.view_layer.objects else None,
        "collections": [c.name for c in obj.users_collection],
    }


@command("get_scene_info")
def get_scene_info(params: dict) -> dict:
    """Objects, collections, active object, mode and current frame."""
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    limit = int(params.get("limit", 200))

    objects = [_obj_brief(o) for o in scene.objects][:limit]
    return {
        "scene": scene.name,
        "filepath": bpy.data.filepath,
        "frame_current": scene.frame_current,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "fps": scene.render.fps / max(1e-9, scene.render.fps_base),
        "render_engine": scene.render.engine,
        "unit_system": scene.unit_settings.system,
        "object_count": len(scene.objects),
        "objects": objects,
        "truncated": len(scene.objects) > limit,
        "collections": [c.name for c in bpy.data.collections],
        "active_object": active.name if active else None,
        "mode": bpy.context.mode,
        "selected": [o.name for o in view_layer.objects if o.select_get()][:limit],
    }


@command("list_objects")
def list_objects(params: dict) -> dict:
    """List objects, optionally filtered by type (MESH, ARMATURE, CAMERA, ...)."""
    type_filter = params.get("type_filter")
    limit = int(params.get("limit", 500))
    objs = list(bpy.context.scene.objects)
    if type_filter:
        wanted = {type_filter} if isinstance(type_filter, str) else set(type_filter)
        objs = [o for o in objs if o.type in wanted]
    return {"count": len(objs), "objects": [_obj_brief(o) for o in objs[:limit]],
            "truncated": len(objs) > limit}


@command("get_object_info")
def get_object_info(params: dict) -> dict:
    """Transforms, mesh stats, modifiers, vertex groups, materials and parent."""
    name = params["name"]
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError(f"no object named {name!r}")

    info = {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": list(obj.rotation_euler),
        "rotation_mode": obj.rotation_mode,
        "scale": list(obj.scale),
        "dimensions": list(obj.dimensions),
        "matrix_world": [list(row) for row in obj.matrix_world],
        "parent": obj.parent.name if obj.parent else None,
        "parent_type": obj.parent_type,
        "modifiers": [{"name": m.name, "type": m.type, "show_viewport": m.show_viewport}
                      for m in obj.modifiers],
        "vertex_groups": [g.name for g in obj.vertex_groups],
        "materials": [m.name if m else None for m in obj.data.materials]
        if getattr(obj.data, "materials", None) is not None else [],
        "collections": [c.name for c in obj.users_collection],
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
    }

    data = obj.data
    if obj.type == "MESH":
        info["mesh"] = {
            "vertices": len(data.vertices),
            "edges": len(data.edges),
            "polygons": len(data.polygons),
            "uv_layers": [l.name for l in data.uv_layers],
            "shape_keys": [k.name for k in data.shape_keys.key_blocks]
            if data.shape_keys else [],
        }
    elif obj.type == "ARMATURE":
        info["armature"] = {
            "bones": [b.name for b in data.bones],
            "bone_count": len(data.bones),
        }
    elif obj.type == "CAMERA":
        info["camera"] = {"lens": data.lens, "type": data.type,
                          "clip_start": data.clip_start, "clip_end": data.clip_end}
    elif obj.type == "LIGHT":
        info["light"] = {"type": data.type, "energy": data.energy,
                         "color": list(data.color)}
    return info


# ---------------------------------------------------------------------------
# modes / undo
# ---------------------------------------------------------------------------

@command("set_mode", mutates=False)
def set_mode(params: dict) -> dict:
    """Switch the active object into OBJECT/EDIT/SCULPT/POSE/WEIGHT_PAINT/... mode."""
    mode = params["mode"].upper()
    name = params.get("object")
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise KeyError(f"no object named {name!r}")
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
    active = bpy.context.view_layer.objects.active
    if active is None:
        raise RuntimeError("no active object to set a mode on")
    bpy.ops.object.mode_set(mode=mode)
    return {"object": active.name, "mode": bpy.context.mode}


@command("undo_checkpoint")
def undo_checkpoint(params: dict) -> dict:
    """Push a named undo step so the agent can roll back to this point."""
    label = params.get("label", "agent checkpoint")
    bpy.ops.ed.undo_push(message=label)
    return {"pushed": label}


@command("undo")
def undo(params: dict) -> dict:
    """Step one undo level back."""
    bpy.ops.ed.undo()
    return {"undone": True, "mode": bpy.context.mode}


@command("redo")
def redo(params: dict) -> dict:
    """Step one undo level forward."""
    bpy.ops.ed.redo()
    return {"redone": True, "mode": bpy.context.mode}


# ---------------------------------------------------------------------------
# escape hatch + RNA introspection
# ---------------------------------------------------------------------------

@command("execute_python", mutates=True)
def execute_python(params: dict) -> dict:
    """Run arbitrary Python inside Blender; returns stdout, last value and traceback."""
    code = params["code"]
    globals_ns = {"bpy": bpy, "__name__": "__agent__"}

    stdout, stderr = io.StringIO(), io.StringIO()
    result_repr = None
    error = None
    tb = None

    # Compile as a module, but if the final statement is an expression evaluate
    # it separately so the agent sees the value it probably wanted.
    try:
        import ast

        tree = ast.parse(code, mode="exec")
        last_expr = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_expr = ast.Expression(tree.body.pop().value)

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if tree.body:
                exec(compile(tree, "<agent>", "exec"), globals_ns)
            if last_expr is not None:
                value = eval(compile(last_expr, "<agent>", "eval"), globals_ns)
                result_repr = repr(value)[:4000]
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()

    return {
        "stdout": stdout.getvalue()[:20000],
        "stderr": stderr.getvalue()[:8000],
        "result": result_repr,
        "error": error,
        "traceback": tb,
    }


def _describe_rna_props(rna) -> dict:
    props = {}
    for prop in rna.properties:
        if prop.identifier == "rna_type":
            continue
        entry = {
            "type": prop.type,
            "name": prop.name,
            "description": prop.description,
            "required": not getattr(prop, "is_skip_save", False) and not prop.is_never_none
            if hasattr(prop, "is_never_none") else None,
        }
        if prop.type == "ENUM":
            entry["items"] = [
                {"id": i.identifier, "name": i.name, "description": i.description}
                for i in prop.enum_items
            ]
        if prop.type in {"FLOAT", "INT", "BOOLEAN"}:
            try:
                length = getattr(prop, "array_length", 0)
                entry["default"] = list(prop.default_array) if length else prop.default
            except Exception:
                pass
            for bound in ("hard_min", "hard_max", "soft_min", "soft_max"):
                if hasattr(prop, bound):
                    with contextlib.suppress(Exception):
                        entry[bound] = getattr(prop, bound)
        if prop.type in {"POINTER", "COLLECTION"}:
            with contextlib.suppress(Exception):
                entry["fixed_type"] = prop.fixed_type.identifier
        props[prop.identifier] = entry
    return props


@command("describe_api")
def describe_api(params: dict) -> dict:
    """Introspect any ``bpy.ops.*`` operator or ``bpy.types.*`` type live via RNA."""
    path = params["path"].strip()

    if path.startswith("bpy.ops."):
        rest = path[len("bpy.ops."):]
        module, _, op_name = rest.partition(".")
        if not op_name:
            mod = getattr(bpy.ops, module)
            return {"kind": "operator_module", "path": path, "operators": sorted(dir(mod))}
        op = getattr(getattr(bpy.ops, module), op_name)
        rna = op.get_rna_type()  # raises cleanly if the operator does not exist
        return {
            "kind": "operator",
            "path": path,
            "description": rna.description,
            "parameters": _describe_rna_props(rna),
        }

    if path.startswith("bpy.types."):
        type_name = path[len("bpy.types."):]
        attr, _, prop_name = type_name.partition(".")
        btype = getattr(bpy.types, attr)
        rna = btype.bl_rna
        if prop_name:
            prop = rna.properties[prop_name]
            return {"kind": "property", "path": path,
                    "detail": _describe_rna_props(type("X", (), {"properties": [prop]})())}
        return {
            "kind": "type",
            "path": path,
            "description": rna.description,
            "properties": _describe_rna_props(rna),
            "functions": sorted(f.identifier for f in rna.functions),
        }

    raise ValueError(
        f"describe_api expects a path starting with 'bpy.ops.' or 'bpy.types.', got {path!r}"
    )


# ---------------------------------------------------------------------------
# imagery
# ---------------------------------------------------------------------------

@command("viewport_screenshot", needs_gui=True)
def viewport_screenshot(params: dict) -> dict:
    """Capture the 3D viewport as a PNG (GUI Blender only)."""
    return ctx.capture_viewport(
        shading_mode=params.get("shading_mode"),
        camera_view=bool(params.get("camera_view", False)),
        max_size=int(params.get("max_size", 1024)),
    )


def _describe_render(path: str) -> dict:
    """Sample a rendered image and say whether anything is actually in it.

    A render that returns fully transparent or a single flat colour is reported
    by Blender as a success. For an agent that is the worst possible outcome:
    silent empty output leaves nothing to react to. Sampling is capped so this
    stays cheap on large frames.
    """
    verdict = {"checked": False}
    image = None
    try:
        image = bpy.data.images.load(path)
        width, height = image.size
        channels = image.channels
        if not width or not height:
            return {"checked": False, "note": "image reported zero size"}

        pixels = image.pixels[:]
        total = width * height
        step = max(1, total // 20000)  # cap the sample regardless of resolution

        alpha_max = 0.0
        lo, hi = 1e9, -1e9
        sampled = 0
        for index in range(0, total, step):
            base = index * channels
            rgb = pixels[base:base + 3]
            if not rgb:
                break
            lo = min(lo, *rgb)
            hi = max(hi, *rgb)
            if channels >= 4:
                alpha_max = max(alpha_max, pixels[base + 3])
            sampled += 1

        verdict = {
            "checked": True,
            "sampled_pixels": sampled,
            "rgb_min": round(lo, 5) if sampled else None,
            "rgb_max": round(hi, 5) if sampled else None,
            "alpha_max": round(alpha_max, 5) if channels >= 4 else None,
        }
        if channels >= 4 and alpha_max <= 1e-6:
            verdict["blank"] = True
            verdict["warning"] = (
                "the render is fully transparent — nothing was visible to the "
                "camera. Check the camera is aimed at the subject "
                "(objects.frame_object), that the objects are not hidden in "
                "render, and that the film is not set to transparent with no "
                "geometry in frame."
            )
        elif sampled and (hi - lo) <= 1e-6:
            verdict["blank"] = True
            verdict["warning"] = (
                f"the render is a single flat colour (value {round(hi, 4)}) — most "
                "likely nothing is in frame or there is no light in the scene."
            )
        else:
            verdict["blank"] = False
    except Exception as exc:  # never fail a good render because the check broke
        verdict = {"checked": False, "note": f"could not inspect: {type(exc).__name__}: {exc}"}
    finally:
        if image is not None:
            with contextlib.suppress(Exception):
                bpy.data.images.remove(image)
    return verdict


@command("render_frame", mutates=False)
def render_frame(params: dict) -> dict:
    """Render the current frame with EEVEE or Cycles and return the PNG."""
    import os
    import tempfile

    scene = bpy.context.scene
    engine = params.get("engine")
    resolution = params.get("resolution")
    samples = params.get("samples")

    gpu_info = None
    saved = {
        "engine": scene.render.engine,
        "filepath": scene.render.filepath,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "percentage": scene.render.resolution_percentage,
        "file_format": scene.render.image_settings.file_format,
    }

    out_dir = tempfile.mkdtemp(prefix="agentmcp-render-")
    out_path = os.path.join(out_dir, "frame.png")

    try:
        if engine:
            engine = engine.upper()
            if engine == "CYCLES":
                gpu_info = ctx.enable_cycles_metal()
            valid = [i.identifier for i in
                     bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
            if engine not in valid:
                raise ValueError(f"engine {engine!r} unavailable; valid: {valid}")
            scene.render.engine = engine

        if resolution:
            scene.render.resolution_x = int(resolution[0])
            scene.render.resolution_y = int(resolution[1])
        scene.render.resolution_percentage = 100

        if samples is not None:
            if scene.render.engine == "CYCLES":
                scene.cycles.samples = int(samples)
                if gpu_info and gpu_info.get("device_type") == "METAL":
                    scene.cycles.device = "GPU"
            elif hasattr(scene, "eevee"):
                with contextlib.suppress(Exception):
                    scene.eevee.taa_render_samples = int(samples)

        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = out_path
        bpy.ops.render.render(write_still=True)

        result = ctx._read_png(out_path)
        result.update(
            width=scene.render.resolution_x,
            height=scene.render.resolution_y,
            engine=scene.render.engine,
            gpu=gpu_info,
        )
        result["content"] = _describe_render(out_path)
        return result
    finally:
        scene.render.engine = saved["engine"]
        scene.render.filepath = saved["filepath"]
        scene.render.resolution_x = saved["resolution_x"]
        scene.render.resolution_y = saved["resolution_y"]
        scene.render.resolution_percentage = saved["percentage"]
        scene.render.image_settings.file_format = saved["file_format"]
        with contextlib.suppress(OSError):
            os.remove(out_path)
        with contextlib.suppress(OSError):
            os.rmdir(out_dir)
