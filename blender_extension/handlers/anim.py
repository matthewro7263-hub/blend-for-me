"""Animation: frames, keyframes, fcurve interpolation, actions, NLA, playblast."""

from __future__ import annotations

import json
import math
import os

import bpy

from .. import ctx
from ..registry import command

INTERPOLATIONS = {i.identifier for i in
                  bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items} \
    if hasattr(bpy.types, "Keyframe") else set()
EASINGS = {i.identifier for i in
           bpy.types.Keyframe.bl_rna.properties["easing"].enum_items} \
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
    return obj.pose.bones[bone].path_from_id(data_path)


def _anim_target(obj, data_path: str, bone: str | None):
    """Return (thing to set, local property path, full object keying path)."""
    full_path = _resolve_path(obj, data_path, bone)
    target = obj.pose.bones[bone] if bone else obj
    return target, data_path, full_path


def _set_anim_value(target, data_path: str, value, index: int) -> None:
    """Set a simple RNA attribute or custom property before key insertion."""
    if data_path.startswith("[") and data_path.endswith("]"):
        try:
            key = json.loads(data_path[1:-1])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError(
                f"custom property path {data_path!r} must look like '[\"my_prop\"]'"
            ) from None
        if not isinstance(key, str) or key not in target:
            raise KeyError(
                f"custom property {key!r} does not exist on {target.name!r}; "
                "create it with set_custom_property before animating it"
            )
        if index >= 0:
            current = list(target[key])
            current[index] = value
            target[key] = current
        else:
            target[key] = value
        return

    if "." in data_path or "[" in data_path:
        raise ValueError(
            f"setting nested path {data_path!r} is ambiguous. Address the owning "
            "object/datablock directly, or omit value and key its current value."
        )
    if not hasattr(target, data_path):
        raise AttributeError(f"{target.name!r} has no animatable property {data_path!r}")
    if isinstance(value, (list, tuple)):
        setattr(target, data_path, value)
    elif index >= 0:
        getattr(target, data_path)[index] = value
    else:
        setattr(target, data_path, value)


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
    step = None
    if params.get("step") is not None:
        step = int(params["step"])
        if step < 1:
            raise ValueError("step must be at least 1")

    # Avoid Blender's cross-clamping when moving a whole valid range beyond the
    # current one (e.g. 300-400 while the old range ends at 250).
    if start > scene.frame_end:
        scene.frame_end = end
        scene.frame_start = start
    else:
        scene.frame_start = start
        scene.frame_end = end
    if step is not None:
        scene.frame_step = step
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
    target, local_path, data_path = _anim_target(
        obj, params["data_path"], params.get("bone")
    )
    frame = int(params.get("frame", bpy.context.scene.frame_current))
    index = params.get("index", -1)

    if params.get("value") is not None:
        # Set the value first so the key records it, rather than keying whatever
        # happened to be there.
        _set_anim_value(target, local_path, params["value"], int(index))

    ok = obj.keyframe_insert(data_path=data_path, frame=frame,
                             index=int(index) if index is not None else -1)
    if not ok:
        raise RuntimeError(
            f"could not key {data_path!r} on {obj.name!r}. Check the path with "
            f"describe_api('bpy.types.{obj.type.title()}') — common ones are "
            "'location', 'rotation_euler', 'scale', 'hide_viewport'."
        )
    return {"object": obj.name, "data_path": data_path, "frame": frame, "index": index}


@command("anim.insert_keyframes_bulk", mutates=True)
def insert_keyframes_bulk(params: dict) -> dict:
    """Insert many object, bone and custom-property keys in one undoable call."""
    tracks = params["tracks"]
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("tracks must be a non-empty list")
    if len(tracks) > 500:
        raise ValueError("one bulk animation call is limited to 500 tracks")

    prepared = []
    total_keys = 0
    for track_index, track in enumerate(tracks):
        if not isinstance(track, dict):
            raise TypeError(f"tracks[{track_index}] must be an object")
        if not track.get("object"):
            raise ValueError(f"tracks[{track_index}].object is required")
        if not isinstance(track.get("data_path"), str) or not track["data_path"]:
            raise ValueError(f"tracks[{track_index}].data_path is required")
        obj = _object(track["object"])
        bone = track.get("bone")
        target, local_path, full_path = _anim_target(obj, track["data_path"], bone)
        index = int(track.get("index", -1))
        keys = track.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError(f"tracks[{track_index}].keys must be a non-empty list")
        total_keys += len(keys)
        if total_keys > 10_000:
            raise ValueError("one bulk animation call is limited to 10,000 keys")

        frames = set()
        normalized_keys = []
        for key_index, key in enumerate(keys):
            if not isinstance(key, dict) or "frame" not in key:
                raise ValueError(f"tracks[{track_index}].keys[{key_index}] needs frame")
            frame = float(key["frame"])
            if not math.isfinite(frame):
                raise ValueError(
                    f"tracks[{track_index}].keys[{key_index}].frame must be finite"
                )
            if frame in frames:
                raise ValueError(
                    f"tracks[{track_index}] has duplicate key frame {frame}"
                )
            frames.add(frame)
            if "value" in key:
                # Validate custom props/attributes before any track mutates.
                if local_path.startswith("[") and local_path.endswith("]"):
                    try:
                        custom_key = json.loads(local_path[1:-1])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        raise ValueError(
                            f"custom property path {local_path!r} must look like "
                            "'[\"my_prop\"]'"
                        ) from None
                    if custom_key not in target:
                        raise KeyError(
                            f"custom property {custom_key!r} does not exist on "
                            f"{target.name!r}"
                        )
                elif "." in local_path or "[" in local_path:
                    raise ValueError(
                        f"setting nested path {local_path!r} is ambiguous; omit value "
                        "to key its current state or address the owning datablock"
                    )
                elif not hasattr(target, local_path):
                    raise AttributeError(
                        f"{target.name!r} has no animatable property {local_path!r}"
                    )
            interpolation = str(
                key.get("interpolation", track.get("interpolation", "BEZIER"))
            ).upper()
            if interpolation not in INTERPOLATIONS:
                raise ValueError(
                    f"tracks[{track_index}].keys[{key_index}] interpolation "
                    f"{interpolation!r} invalid; valid: {sorted(INTERPOLATIONS)}"
                )
            easing = key.get("easing", track.get("easing"))
            easing = str(easing).upper() if easing else None
            if easing is not None and easing not in EASINGS:
                raise ValueError(
                    f"tracks[{track_index}].keys[{key_index}] easing {easing!r} "
                    f"invalid; valid: {sorted(EASINGS)}"
                )
            normalized_keys.append({
                "frame": frame,
                "value": key.get("value"),
                "has_value": "value" in key,
                "interpolation": interpolation,
                "easing": easing,
            })

        clear_range = track.get("clear_range")
        if clear_range is not None:
            if not isinstance(clear_range, (list, tuple)) or len(clear_range) != 2:
                raise ValueError(
                    f"tracks[{track_index}].clear_range must be [start, end]"
                )
            clear_range = [float(clear_range[0]), float(clear_range[1])]
            if not all(math.isfinite(value) for value in clear_range):
                raise ValueError(
                    f"tracks[{track_index}].clear_range values must be finite"
                )
            if clear_range[1] < clear_range[0]:
                raise ValueError(
                    f"tracks[{track_index}].clear_range end is before start"
                )
        prepared.append({
            "object": obj, "bone": bone, "target": target,
            "local_path": local_path, "full_path": full_path, "index": index,
            "keys": normalized_keys, "clear_range": clear_range,
        })

    cleared = 0
    for track in prepared:
        clear_range = track["clear_range"]
        if clear_range is None:
            continue
        action, slot = _action_of(track["object"])
        if action is None:
            continue
        for fcurve in iter_fcurves(action, slot):
            if fcurve.data_path != track["full_path"]:
                continue
            if track["index"] >= 0 and fcurve.array_index != track["index"]:
                continue
            for point in list(fcurve.keyframe_points):
                if clear_range[0] <= point.co[0] <= clear_range[1]:
                    fcurve.keyframe_points.remove(point)
                    cleared += 1
            fcurve.update()

    inserted = 0
    track_results = []
    for track in prepared:
        obj = track["object"]
        for key in track["keys"]:
            if key["has_value"]:
                _set_anim_value(
                    track["target"], track["local_path"], key["value"], track["index"]
                )
            ok = obj.keyframe_insert(
                data_path=track["full_path"], frame=key["frame"], index=track["index"]
            )
            if not ok:
                raise RuntimeError(
                    f"could not key {track['full_path']!r} on {obj.name!r} at "
                    f"frame {key['frame']}"
                )
            inserted += 1

        action, slot = _action_of(obj)
        touched_points = 0
        if action is not None:
            key_settings = {round(key["frame"], 5): key for key in track["keys"]}
            for fcurve in iter_fcurves(action, slot):
                if fcurve.data_path != track["full_path"]:
                    continue
                if track["index"] >= 0 and fcurve.array_index != track["index"]:
                    continue
                for point in fcurve.keyframe_points:
                    setting = key_settings.get(round(float(point.co[0]), 5))
                    if setting is None:
                        continue
                    point.interpolation = setting["interpolation"]
                    if setting["easing"]:
                        point.easing = setting["easing"]
                    touched_points += 1
                fcurve.update()
        track_results.append({
            "object": obj.name,
            "bone": track["bone"],
            "data_path": track["full_path"],
            "index": track["index"],
            "keys": len(track["keys"]),
            "fcurve_points_touched": touched_points,
            "action": action.name if action else None,
        })

    return {
        "tracks": track_results,
        "track_count": len(track_results),
        "keys_inserted": inserted,
        "keyframe_points_cleared": cleared,
    }


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
