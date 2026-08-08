"""Animation: frames, keyframes, fcurve interpolation, actions, NLA, playblast."""

from __future__ import annotations

import os

import bpy

from .. import ctx
from ..registry import command

INTERPOLATIONS = {i.identifier for i in
                  bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items} \
    if hasattr(bpy.types, "Keyframe") else set()


def iter_fcurves(action, slot=None):
    """Yield an action's F-Curves across Blender's layered and legacy layouts.

    Blender 4.4+ introduced *slotted actions*: ``Action.fcurves`` no longer
    exists and curves live at
    ``action.layers[].strips[].channelbags[].fcurves``. Files authored before
    that still round-trip as legacy actions, so both shapes are handled.

    Args:
        slot: Restrict to one ``ActionSlot`` (an object's is
            ``obj.animation_data.action_slot``). Omit for every slot, which is
            what you want when reporting on the action as a whole.
    """
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        yield from legacy
        return

    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            bags = getattr(strip, "channelbags", None)
            if bags is None:
                continue
            for bag in bags:
                if slot is not None and getattr(bag, "slot", None) != slot:
                    continue
                yield from bag.fcurves


def _action_of(obj):
    anim = obj.animation_data
    if anim is None or anim.action is None:
        return None, None
    return anim.action, getattr(anim, "action_slot", None)


def _object(name: str | None):
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise KeyError(f"no object named {name!r}")
        return obj
    obj = bpy.context.view_layer.objects.active
    if obj is None:
        raise RuntimeError("no object given and nothing is active")
    return obj


def _resolve_path(obj, data_path: str, bone: str | None) -> str:
    """Bone channels live under pose.bones["name"].<path>, not on the object."""
    if not bone:
        return data_path
    if obj.type != "ARMATURE":
        raise TypeError(f"{obj.name!r} is a {obj.type}; 'bone' only applies to an ARMATURE")
    if bone not in obj.pose.bones:
        raise KeyError(f"{obj.name!r} has no pose bone {bone!r}. "
                       f"Bones: {[b.name for b in obj.pose.bones][:40]}")
    return f'pose.bones["{bone}"].{data_path}'


@command("anim.set_frame", mutates=False)
def set_frame(params: dict) -> dict:
    """Jump the timeline to a frame."""
    scene = bpy.context.scene
    scene.frame_set(int(params["frame"]))
    return {"frame_current": scene.frame_current}


@command("anim.set_frame_range", mutates=False)
def set_frame_range(params: dict) -> dict:
    """Set the scene's start/end/step frame range."""
    scene = bpy.context.scene
    start = int(params["start"]) if params.get("start") is not None else scene.frame_start
    end = int(params["end"]) if params.get("end") is not None else scene.frame_end
    if end < start:
        # Assigning these in either order lets Blender clamp one against the
        # other, quietly turning an invalid range into a valid one.
        raise ValueError(f"end ({end}) is before start ({start})")

    scene.frame_start = start
    scene.frame_end = end
    if params.get("step") is not None:
        scene.frame_step = int(params["step"])
    return {"start": scene.frame_start, "end": scene.frame_end, "step": scene.frame_step}


@command("anim.set_fps", mutates=False)
def set_fps(params: dict) -> dict:
    """Set the scene frame rate."""
    scene = bpy.context.scene
    scene.render.fps = int(params["fps"])
    if params.get("fps_base") is not None:
        scene.render.fps_base = float(params["fps_base"])
    return {"fps": scene.render.fps, "fps_base": scene.render.fps_base,
            "effective_fps": scene.render.fps / max(1e-9, scene.render.fps_base)}


@command("anim.insert_keyframe", mutates=True)
def insert_keyframe(params: dict) -> dict:
    """Insert a keyframe on an object or pose-bone channel."""
    obj = _object(params.get("object"))
    data_path = _resolve_path(obj, params["data_path"], params.get("bone"))
    frame = int(params.get("frame", bpy.context.scene.frame_current))
    index = params.get("index", -1)

    if params.get("value") is not None:
        # Set the value first so the key records it, rather than keying whatever
        # happened to be there.
        value = params["value"]
        target = obj
        path = data_path
        if data_path.startswith("pose.bones["):
            close = data_path.index("]")
            bone_name = data_path[len('pose.bones["'):close - 1]
            target = obj.pose.bones[bone_name]
            path = data_path[close + 2:]
        if isinstance(value, (list, tuple)):
            setattr(target, path, value)
        elif index is not None and index >= 0:
            getattr(target, path)[index] = value
        else:
            setattr(target, path, value)

    ok = obj.keyframe_insert(data_path=data_path, frame=frame,
                             index=int(index) if index is not None else -1)
    if not ok:
        raise RuntimeError(
            f"could not key {data_path!r} on {obj.name!r}. Check the path with "
            f"describe_api('bpy.types.{obj.type.title()}') — common ones are "
            "'location', 'rotation_euler', 'scale', 'hide_viewport'."
        )
    return {"object": obj.name, "data_path": data_path, "frame": frame, "index": index}


@command("anim.remove_keyframe", mutates=True)
def remove_keyframe(params: dict) -> dict:
    """Remove a keyframe from a channel."""
    obj = _object(params.get("object"))
    data_path = _resolve_path(obj, params["data_path"], params.get("bone"))
    frame = int(params.get("frame", bpy.context.scene.frame_current))
    index = int(params.get("index", -1))
    removed = obj.keyframe_delete(data_path=data_path, frame=frame, index=index)
    if not removed:
        raise RuntimeError(f"no keyframe on {data_path!r} at frame {frame}")
    return {"object": obj.name, "data_path": data_path, "frame": frame, "removed": True}


@command("anim.list_keyframes", mutates=False)
def list_keyframes(params: dict) -> dict:
    """List an object's animated channels and their keyframes (capped)."""
    obj = _object(params.get("object"))
    limit = int(params.get("limit", 200))
    wanted = params.get("data_path")

    action, slot = _action_of(obj)
    if action is None:
        return {"object": obj.name, "action": None, "channels": []}

    channels = []
    total = 0
    for fcurve in iter_fcurves(action, slot):
        if wanted and fcurve.data_path != wanted:
            continue
        keys = [{"frame": kp.co[0], "value": kp.co[1],
                 "interpolation": kp.interpolation, "easing": kp.easing}
                for kp in fcurve.keyframe_points]
        total += len(keys)
        channels.append({
            "data_path": fcurve.data_path,
            "array_index": fcurve.array_index,
            "keyframe_count": len(keys),
            "keyframes": keys[:limit],
            "truncated": len(keys) > limit,
        })
    return {"object": obj.name, "action": action.name, "channels": channels,
            "total_keyframes": total}


@command("anim.set_interpolation", mutates=True)
def set_interpolation(params: dict) -> dict:
    """Set interpolation/easing on keyframes of matching fcurves."""
    obj = _object(params.get("object"))
    action, slot = _action_of(obj)
    if action is None:
        raise RuntimeError(f"{obj.name!r} has no action to edit")

    interpolation = params.get("interpolation")
    if interpolation:
        interpolation = str(interpolation).upper()
        valid = {i.identifier for i in
                 bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items}
        if interpolation not in valid:
            raise ValueError(f"interpolation must be one of {sorted(valid)}")
    easing = str(params["easing"]).upper() if params.get("easing") else None

    wanted = params.get("data_path")
    frame_range = params.get("frame_range")
    changed = 0
    for fcurve in iter_fcurves(action, slot):
        if wanted and fcurve.data_path != wanted:
            continue
        for kp in fcurve.keyframe_points:
            if frame_range and not (frame_range[0] <= kp.co[0] <= frame_range[1]):
                continue
            if interpolation:
                kp.interpolation = interpolation
            if easing:
                kp.easing = easing
            changed += 1
        fcurve.update()
    return {"object": obj.name, "keyframes_changed": changed,
            "interpolation": interpolation, "easing": easing}


@command("anim.list_actions", mutates=False)
def list_actions(params: dict) -> dict:
    """Every action in the file, with its frame range and channel count."""
    actions = []
    for action in bpy.data.actions:
        frame_range = list(action.frame_range)
        actions.append({"name": action.name, "frame_start": frame_range[0],
                        "frame_end": frame_range[1],
                        "fcurves": sum(1 for _ in iter_fcurves(action)),
                        "slots": [s.identifier for s in getattr(action, "slots", [])],
                        "users": action.users})
    return {"count": len(actions), "actions": actions}


@command("anim.assign_action", mutates=True)
def assign_action(params: dict) -> dict:
    """Assign an action to an object, optionally creating it."""
    obj = _object(params.get("object"))
    name = params["action"]
    action = bpy.data.actions.get(name)
    if action is None:
        if not params.get("create_if_missing", True):
            raise KeyError(f"no action named {name!r}. Existing: "
                           f"{[a.name for a in bpy.data.actions]}")
        action = bpy.data.actions.new(name)
    if obj.animation_data is None:
        obj.animation_data_create()
    obj.animation_data.action = action
    return {"object": obj.name, "action": action.name,
            "frame_range": list(action.frame_range)}


@command("anim.nla_push_down", mutates=True)
def nla_push_down(params: dict) -> dict:
    """Push the active action down into a new NLA strip."""
    obj = _object(params.get("object"))
    anim = obj.animation_data
    if anim is None or anim.action is None:
        raise RuntimeError(f"{obj.name!r} has no active action to push down")

    action = anim.action
    track = anim.nla_tracks.new()
    if params.get("track_name"):
        track.name = str(params["track_name"])
    start = int(action.frame_range[0])
    strip = track.strips.new(action.name, start, action)
    anim.action = None
    return {"object": obj.name, "track": track.name, "strip": strip.name,
            "frame_start": strip.frame_start, "frame_end": strip.frame_end}


@command("anim.playblast", mutates=False, needs_gui=True)
def playblast(params: dict) -> dict:
    """OpenGL viewport render of a frame range to an MP4 or PNG sequence."""
    scene = bpy.context.scene
    out_path = os.path.expanduser(str(params["out_path"]))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    fmt = str(params.get("format", "MP4")).upper()
    if fmt not in {"MP4", "PNG"}:
        raise ValueError("format must be 'MP4' or 'PNG'")

    window, area, region, space = ctx.require_view3d()
    render = scene.render
    saved = {
        "filepath": render.filepath, "file_format": render.image_settings.file_format,
        "start": scene.frame_start, "end": scene.frame_end,
        "x": render.resolution_x, "y": render.resolution_y,
        "pct": render.resolution_percentage, "fps": render.fps,
    }
    saved_ffmpeg = None
    if fmt == "MP4":
        saved_ffmpeg = {"format": render.ffmpeg.format, "codec": render.ffmpeg.codec}

    try:
        if params.get("frame_start") is not None:
            scene.frame_start = int(params["frame_start"])
        if params.get("frame_end") is not None:
            scene.frame_end = int(params["frame_end"])
        if params.get("fps") is not None:
            render.fps = int(params["fps"])
        if params.get("resolution"):
            render.resolution_x = int(params["resolution"][0])
            render.resolution_y = int(params["resolution"][1])
        render.resolution_percentage = int(params.get("percentage", 100))

        if fmt == "MP4":
            render.image_settings.file_format = "FFMPEG"
            render.ffmpeg.format = "MPEG4"
            render.ffmpeg.codec = "H264"
        else:
            render.image_settings.file_format = "PNG"
        render.filepath = out_path

        with bpy.context.temp_override(window=window, screen=window.screen,
                                       area=area, region=region, space_data=space,
                                       scene=scene):
            bpy.ops.render.opengl(animation=True, view_context=True, write_still=False)

        frames = scene.frame_end - scene.frame_start + 1
        produced = out_path
        if fmt == "MP4" and not os.path.isfile(produced):
            # Blender appends the frame range to movie filenames.
            directory = os.path.dirname(os.path.abspath(out_path))
            base = os.path.basename(out_path)
            candidates = [f for f in os.listdir(directory)
                          if f.startswith(os.path.splitext(base)[0])]
            if candidates:
                produced = os.path.join(directory, sorted(candidates)[-1])

        return {
            "path": produced,
            "exists": os.path.isfile(produced),
            "bytes": os.path.getsize(produced) if os.path.isfile(produced) else 0,
            "frames": frames,
            "frame_start": scene.frame_start, "frame_end": scene.frame_end,
            "format": fmt, "fps": render.fps,
        }
    finally:
        render.filepath = saved["filepath"]
        render.image_settings.file_format = saved["file_format"]
        scene.frame_start, scene.frame_end = saved["start"], saved["end"]
        render.resolution_x, render.resolution_y = saved["x"], saved["y"]
        render.resolution_percentage = saved["pct"]
        render.fps = saved["fps"]
        if saved_ffmpeg:
            render.ffmpeg.format = saved_ffmpeg["format"]
            render.ffmpeg.codec = saved_ffmpeg["codec"]
