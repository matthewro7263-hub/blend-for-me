"""Shot planning, camera cuts, Video Sequencer editing and final animation renders."""

from __future__ import annotations

import os
import warnings

import bpy

from ..registry import command


def _scene(name=None):
    if name:
        scene = bpy.data.scenes.get(name)
        if scene is None:
            raise KeyError(f"no scene named {name!r}; scenes: {[s.name for s in bpy.data.scenes]}")
        return scene
    return bpy.context.scene


def _editor(scene, *, create: bool):
    editor = scene.sequence_editor
    if editor is None and create:
        editor = scene.sequence_editor_create()
    return editor


def _top_strip(editor, name: str):
    strip = editor.strips.get(name) if editor else None
    if strip is not None:
        return strip
    nested = editor.strips_all.get(name) if editor else None
    if nested is not None:
        raise RuntimeError(
            f"strip {name!r} is nested inside a Meta strip. Enter/unpack that Meta "
            "before editing it through this tool."
        )
    raise KeyError(
        f"no top-level sequencer strip named {name!r}; call list_sequencer_strips first"
    )


def _strip_source(strip):
    if strip.type == "SOUND" and getattr(strip, "sound", None):
        return bpy.path.abspath(strip.sound.filepath)
    if strip.type == "MOVIE" and getattr(strip, "filepath", None):
        return bpy.path.abspath(strip.filepath)
    if strip.type == "IMAGE" and getattr(strip, "elements", None):
        element = strip.elements[0] if len(strip.elements) else None
        if element:
            return os.path.join(bpy.path.abspath(strip.directory), element.filename)
    if strip.type == "SCENE" and getattr(strip, "scene", None):
        return strip.scene.name
    return None


def _strip_brief(strip) -> dict:
    result = {
        "name": strip.name,
        "type": strip.type,
        "channel": strip.channel,
        "start": strip.left_handle,
        "end": strip.right_handle,
        "duration": strip.duration,
        "content_start": strip.content_start,
        "content_end": strip.content_end,
        "content_duration": strip.content_duration,
        "mute": strip.mute,
        "lock": strip.lock,
        "blend_type": strip.blend_type,
        "blend_alpha": strip.blend_alpha,
        "source": _strip_source(strip),
    }
    for prop in ("volume", "pan", "text", "font_size", "alignment_x",
                 "anchor_x", "anchor_y", "use_shadow", "use_outline", "use_box"):
        if hasattr(strip, prop):
            result[prop] = getattr(strip, prop)
    if hasattr(strip, "color"):
        result["color"] = list(strip.color)
    if hasattr(strip, "location"):
        result["location"] = list(strip.location)
    transform = getattr(strip, "transform", None)
    if transform is not None:
        result["transform"] = {
            prop: getattr(transform, prop)
            for prop in ("offset_x", "offset_y", "scale_x", "scale_y", "rotation")
            if hasattr(transform, prop)
        }
    return result


def _set_strip_range(strip, start=None, end=None, duration=None) -> None:
    """Move/trim using the 5.2 handles while preserving duration when moving."""
    if end is not None and duration is not None:
        raise ValueError("pass end or duration, not both")
    current_duration = strip.duration
    if start is not None:
        start = int(start)
        # frame_start remains the only 5.2 property that moves source content and
        # handles together. Blender marks it for a 6.0 rename, hence suppression.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            strip.frame_start += start - strip.left_handle
        strip.left_handle = start
        if end is None and duration is None:
            strip.right_handle = start + current_duration
    if duration is not None:
        duration = int(duration)
        if duration < 1:
            raise ValueError("duration must be at least 1 frame")
        strip.duration = duration
    if end is not None:
        end = int(end)
        if end <= strip.left_handle:
            raise ValueError(f"end ({end}) must be after start ({strip.left_handle})")
        strip.right_handle = end


def _camera_cut_summary(scene) -> list[dict]:
    markers = sorted(
        (marker for marker in scene.timeline_markers if marker.camera is not None),
        key=lambda marker: (marker.frame, marker.name),
    )
    shots = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].frame - 1 if index + 1 < len(markers) else scene.frame_end
        shots.append({
            "name": marker.name,
            "start": marker.frame,
            "end": end,
            "duration": max(0, end - marker.frame + 1),
            "camera": marker.camera.name,
        })
    return shots


def _set_scene_range_exact(scene, start: int, end: int) -> None:
    if end < start:
        raise ValueError(f"frame_end {end} is before frame_start {start}")
    if start > scene.frame_end:
        scene.frame_end = end
        scene.frame_start = start
    else:
        scene.frame_start = start
        scene.frame_end = end


@command("cinematics.list_timeline_markers")
def list_timeline_markers(params: dict) -> dict:
    """List timeline markers and derive camera-cut shot ranges."""
    scene = _scene(params.get("scene"))
    markers = sorted(scene.timeline_markers, key=lambda marker: (marker.frame, marker.name))
    return {
        "scene": scene.name,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "markers": [
            {
                "name": marker.name,
                "frame": marker.frame,
                "camera": marker.camera.name if marker.camera else None,
                "selected": marker.select,
            }
            for marker in markers
        ],
        "shots": _camera_cut_summary(scene),
    }


@command("cinematics.build_camera_cuts", mutates=True)
def build_camera_cuts(params: dict) -> dict:
    """Create/update a retry-safe camera-cut plan from named timeline markers."""
    scene = _scene(params.get("scene"))
    cuts = params["cuts"]
    clear_existing = bool(params.get("clear_existing", False))
    if not isinstance(cuts, list) or not cuts:
        raise ValueError("cuts must be a non-empty list")
    if len(cuts) > 1000:
        raise ValueError("one camera-cut build is limited to 1000 shots")

    prepared = []
    names = set()
    frames = set()
    for index, cut in enumerate(cuts):
        if not isinstance(cut, dict):
            raise TypeError(f"cuts[{index}] must be an object")
        name = cut.get("name")
        camera_name = cut.get("camera")
        if not isinstance(name, str) or not name:
            raise ValueError(f"cuts[{index}].name is required")
        if name in names:
            raise ValueError(f"duplicate cut name {name!r}")
        names.add(name)
        frame = int(cut.get("frame"))
        if frame in frames:
            raise ValueError(f"two camera cuts cannot share frame {frame}")
        frames.add(frame)
        camera = bpy.data.objects.get(camera_name)
        if camera is None:
            raise KeyError(f"cuts[{index}] names missing camera {camera_name!r}")
        if camera.type != "CAMERA":
            raise TypeError(f"{camera_name!r} is a {camera.type}, not a CAMERA")
        prepared.append((name, frame, camera))

    if not clear_existing:
        for marker in scene.timeline_markers:
            if marker.camera is None or marker.name in names:
                continue
            if marker.frame in frames:
                raise ValueError(
                    f"existing camera marker {marker.name!r} already occupies frame "
                    f"{marker.frame}; use clear_existing=true or choose another frame"
                )

    removed = []
    if clear_existing:
        for marker in list(scene.timeline_markers):
            if marker.camera is not None:
                removed.append(marker.name)
                scene.timeline_markers.remove(marker)

    created = []
    updated = []
    for name, frame, camera in prepared:
        marker = scene.timeline_markers.get(name)
        if marker is None:
            marker = scene.timeline_markers.new(name, frame=frame)
            created.append(name)
        else:
            marker.frame = frame
            updated.append(name)
        marker.camera = camera

    ordered = sorted(prepared, key=lambda item: item[1])
    if bool(params.get("set_scene_range", False)):
        frame_end = params.get("frame_end")
        if frame_end is not None:
            frame_end = int(frame_end)
            if frame_end < ordered[-1][1]:
                raise ValueError(
                    f"frame_end {frame_end} is before final cut at {ordered[-1][1]}"
                )
        _set_scene_range_exact(
            scene, ordered[0][1],
            frame_end if frame_end is not None else max(scene.frame_end, ordered[0][1]),
        )

    active = next(
        (item for item in reversed(ordered) if item[1] <= scene.frame_current),
        ordered[0],
    )
    scene.camera = active[2]
    return {
        "scene": scene.name,
        "created": created,
        "updated": updated,
        "removed": removed,
        "active_camera": scene.camera.name,
        "shots": _camera_cut_summary(scene),
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
    }


@command("cinematics.remove_timeline_markers", mutates=True)
def remove_timeline_markers(params: dict) -> dict:
    """Remove named markers, all camera-cut markers, or explicitly every marker."""
    scene = _scene(params.get("scene"))
    names = params.get("names") or []
    camera_only = bool(params.get("camera_only", False))
    remove_all = bool(params.get("all", False))
    if not names and not camera_only and not remove_all:
        raise ValueError("pass names, camera_only=true, or all=true")
    wanted = set(names)
    removed = []
    for marker in list(scene.timeline_markers):
        if remove_all or marker.name in wanted or (camera_only and marker.camera is not None):
            removed.append(marker.name)
            scene.timeline_markers.remove(marker)
    missing = sorted(wanted - set(removed))
    return {"scene": scene.name, "removed": removed, "missing": missing,
            "remaining": len(scene.timeline_markers)}


@command("cinematics.list_sequencer_strips")
def list_sequencer_strips(params: dict) -> dict:
    """List every Video Sequencer strip in timeline order."""
    scene = _scene(params.get("scene"))
    editor = _editor(scene, create=False)
    strips = list(editor.strips_all) if editor else []
    strips.sort(key=lambda strip: (strip.left_handle, strip.channel, strip.name))
    limit = int(params.get("limit", 1000))
    return {
        "scene": scene.name,
        "count": len(strips),
        "strips": [_strip_brief(strip) for strip in strips[:limit]],
        "truncated": len(strips) > limit,
    }


@command("cinematics.add_media_strip", mutates=True)
def add_media_strip(params: dict) -> dict:
    """Add/reuse an image, movie or audio strip and optionally its movie audio."""
    scene = _scene(params.get("scene"))
    editor = _editor(scene, create=True)
    media_type = str(params["type"]).upper()
    if media_type not in {"IMAGE", "MOVIE", "SOUND"}:
        raise ValueError("type must be IMAGE, MOVIE or SOUND")
    path = bpy.path.abspath(params["path"])
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no media file at {path!r}")
    path = os.path.abspath(path)
    name = str(params.get("name") or os.path.basename(path))
    channel = int(params.get("channel", 1))
    frame_start = int(params.get("frame_start", 1))
    if not 1 <= channel <= 128:
        raise ValueError("channel must be between 1 and 128")

    existing = editor.strips.get(name)
    reused = existing is not None
    if existing is not None:
        if not bool(params.get("reuse_existing", True)):
            raise ValueError(f"strip {name!r} already exists")
        if existing.type != media_type:
            raise TypeError(
                f"strip {name!r} is {existing.type}, not requested {media_type}"
            )
        existing_source = _strip_source(existing)
        if existing_source and os.path.abspath(existing_source) != path:
            raise ValueError(
                f"strip {name!r} already uses {existing_source!r}, not {path!r}; "
                "choose a new name or remove the old strip"
            )
        strip = existing
    else:
        if media_type == "IMAGE":
            strip = editor.strips.new_image(
                name, path, channel, frame_start,
                fit_method=str(params.get("fit_method", "FIT")).upper(),
            )
        elif media_type == "MOVIE":
            strip = editor.strips.new_movie(
                name, path, channel, frame_start,
                fit_method=str(params.get("fit_method", "FIT")).upper(),
            )
        else:
            strip = editor.strips.new_sound(name, path, channel, frame_start)

    strip.channel = channel
    _set_strip_range(
        strip, start=frame_start, end=params.get("frame_end"),
        duration=params.get("duration"),
    )
    if hasattr(strip, "volume") and params.get("volume") is not None:
        strip.volume = float(params["volume"])
    if hasattr(strip, "pan") and params.get("pan") is not None:
        strip.pan = float(params["pan"])

    audio_result = None
    if media_type == "MOVIE" and bool(params.get("add_audio", False)):
        audio_name = f"{name}.Audio"
        audio = editor.strips.get(audio_name)
        if audio is None:
            audio_channel = int(params.get("audio_channel") or (channel - 1 if channel > 1 else channel + 1))
            audio = editor.strips.new_sound(audio_name, path, audio_channel, frame_start)
        _set_strip_range(audio, start=frame_start, end=params.get("frame_end"))
        if params.get("volume") is not None:
            audio.volume = float(params["volume"])
        audio_result = _strip_brief(audio)

    return {"scene": scene.name, "created": not reused, "reused": reused,
            "strip": _strip_brief(strip), "audio_strip": audio_result}


@command("cinematics.add_text_strip", mutates=True)
def add_text_strip(params: dict) -> dict:
    """Add/update a title or subtitle strip."""
    scene = _scene(params.get("scene"))
    editor = _editor(scene, create=True)
    name = str(params["name"])
    start = int(params["frame_start"])
    end = int(params["frame_end"])
    if end <= start:
        raise ValueError("frame_end must be after frame_start")
    strip = editor.strips.get(name)
    reused = strip is not None
    if strip is None:
        strip = editor.strips.new_effect(
            name, type="TEXT", channel=int(params.get("channel", 3)),
            frame_start=start, length=end - start,
        )
    elif strip.type != "TEXT":
        raise TypeError(f"strip {name!r} is {strip.type}, not TEXT")
    _set_strip_range(strip, start=start, end=end)
    strip.channel = int(params.get("channel", strip.channel))
    strip.text = str(params["text"])

    for prop in ("font_size", "wrap_width", "box_margin", "box_roundness",
                 "outline_width", "shadow_angle", "shadow_offset", "shadow_blur"):
        if params.get(prop) is not None:
            setattr(strip, prop, float(params[prop]))
    for prop in ("alignment_x", "anchor_x", "anchor_y"):
        if params.get(prop) is not None:
            setattr(strip, prop, str(params[prop]).upper())
    for prop in ("use_shadow", "use_outline", "use_box", "use_bold", "use_italic"):
        if params.get(prop) is not None:
            setattr(strip, prop, bool(params[prop]))
    for prop in ("color", "shadow_color", "outline_color", "box_color", "location"):
        if params.get(prop) is not None:
            setattr(strip, prop, tuple(float(value) for value in params[prop]))
    return {"scene": scene.name, "created": not reused, "reused": reused,
            "strip": _strip_brief(strip)}


@command("cinematics.add_color_strip", mutates=True)
def add_color_strip(params: dict) -> dict:
    """Add/update a solid-color sequencer background or flash card."""
    scene = _scene(params.get("scene"))
    editor = _editor(scene, create=True)
    name = str(params["name"])
    start = int(params["frame_start"])
    end = int(params["frame_end"])
    if end <= start:
        raise ValueError("frame_end must be after frame_start")
    strip = editor.strips.get(name)
    reused = strip is not None
    if strip is None:
        strip = editor.strips.new_effect(
            name, type="COLOR", channel=int(params.get("channel", 1)),
            frame_start=start, length=end - start,
        )
    elif strip.type != "COLOR":
        raise TypeError(f"strip {name!r} is {strip.type}, not COLOR")
    _set_strip_range(strip, start=start, end=end)
    strip.channel = int(params.get("channel", strip.channel))
    color = [float(value) for value in params.get("color", [0.0, 0.0, 0.0])]
    if len(color) != 3:
        raise ValueError("color must be [R, G, B]")
    strip.color = color
    return {"scene": scene.name, "created": not reused, "reused": reused,
            "strip": _strip_brief(strip)}


@command("cinematics.update_strip", mutates=True)
def update_strip(params: dict) -> dict:
    """Update timing, compositing, audio, text and transform fields on one strip."""
    scene = _scene(params.get("scene"))
    editor = _editor(scene, create=False)
    strip = _top_strip(editor, params["name"])
    updates = params["updates"]
    if not isinstance(updates, dict) or not updates:
        raise ValueError("updates must be a non-empty object")

    allowed = {
        "channel", "start", "end", "duration", "mute", "lock", "blend_type",
        "blend_alpha", "volume", "pan", "text", "font_size", "color", "location",
        "alignment_x", "anchor_x", "anchor_y", "use_shadow", "use_outline",
        "use_box", "use_bold", "use_italic", "wrap_width", "box_margin",
        "box_roundness", "outline_width",
    }
    unknown = sorted(set(updates) - allowed - {"transform"})
    if unknown:
        raise ValueError(f"unknown strip updates {unknown}; allowed: {sorted(allowed)}")
    _set_strip_range(
        strip, start=updates.get("start"), end=updates.get("end"),
        duration=updates.get("duration"),
    )

    for prop in allowed - {"start", "end", "duration"}:
        if prop not in updates:
            continue
        if not hasattr(strip, prop):
            raise TypeError(f"{strip.type} strip {strip.name!r} has no {prop!r} setting")
        value = updates[prop]
        current = getattr(strip, prop)
        if hasattr(current, "__len__") and not isinstance(current, str):
            value = tuple(value)
        elif isinstance(current, bool):
            value = bool(value)
        elif isinstance(current, int):
            value = int(value)
        elif isinstance(current, float):
            value = float(value)
        elif isinstance(current, str) and prop not in {"text"}:
            value = str(value).upper()
        setattr(strip, prop, value)

    transform_updates = updates.get("transform")
    if transform_updates is not None:
        if not isinstance(transform_updates, dict):
            raise TypeError("transform must be an object")
        transform = getattr(strip, "transform", None)
        if transform is None:
            raise TypeError(f"{strip.type} strip {strip.name!r} has no transform")
        valid = {"offset_x", "offset_y", "scale_x", "scale_y", "rotation"}
        bad = sorted(set(transform_updates) - valid)
        if bad:
            raise ValueError(f"unknown transform settings {bad}; allowed: {sorted(valid)}")
        for prop, value in transform_updates.items():
            setattr(transform, prop, float(value))
    return {"scene": scene.name, "strip": _strip_brief(strip)}


@command("cinematics.remove_sequencer_strips", mutates=True)
def remove_sequencer_strips(params: dict) -> dict:
    """Remove explicitly named top-level sequencer strips."""
    scene = _scene(params.get("scene"))
    editor = _editor(scene, create=False)
    names = params["names"]
    if not isinstance(names, list) or not names:
        raise ValueError("names must be a non-empty list")
    removed = []
    missing = []
    if editor is None:
        return {"scene": scene.name, "removed": [], "missing": names, "remaining": 0}
    for name in names:
        strip = editor.strips.get(name)
        if strip is None:
            missing.append(name)
            continue
        editor.strips.remove(strip)
        removed.append(name)
    return {"scene": scene.name, "removed": removed, "missing": missing,
            "remaining": len(editor.strips_all)}


@command("cinematics.render_animation")
def render_animation(params: dict) -> dict:
    """Render a final MP4 or PNG sequence from 3D, camera cuts or the sequencer."""
    scene = _scene(params.get("scene"))
    out_path = os.path.abspath(os.path.expanduser(str(params["out_path"])))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fmt = str(params.get("format", "MP4")).upper()
    if fmt not in {"MP4", "PNG"}:
        raise ValueError("format must be MP4 or PNG")
    render = scene.render
    saved = {
        "filepath": render.filepath,
        "file_format": render.image_settings.file_format,
        "start": scene.frame_start,
        "end": scene.frame_end,
        "x": render.resolution_x,
        "y": render.resolution_y,
        "percentage": render.resolution_percentage,
        "fps": render.fps,
        "fps_base": render.fps_base,
        "engine": render.engine,
        "use_file_extension": render.use_file_extension,
        "use_sequencer": getattr(render, "use_sequencer", None),
        "ffmpeg_format": render.ffmpeg.format,
        "ffmpeg_codec": render.ffmpeg.codec,
    }
    saved_cycles_samples = scene.cycles.samples if hasattr(scene, "cycles") else None
    saved_eevee_samples = None
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        saved_eevee_samples = scene.eevee.taa_render_samples
    try:
        requested_start = int(params.get("frame_start", scene.frame_start))
        requested_end = int(params.get("frame_end", scene.frame_end))
        _set_scene_range_exact(scene, requested_start, requested_end)
        if params.get("resolution") is not None:
            resolution = params["resolution"]
            if len(resolution) != 2:
                raise ValueError("resolution must be [width, height]")
            render.resolution_x, render.resolution_y = map(int, resolution)
        render.resolution_percentage = int(params.get("percentage", 100))
        if params.get("fps") is not None:
            render.fps = int(params["fps"])
        if params.get("fps_base") is not None:
            render.fps_base = float(params["fps_base"])
        if params.get("engine") is not None:
            render.engine = str(params["engine"]).upper()
        if params.get("samples") is not None:
            samples = int(params["samples"])
            if render.engine == "CYCLES":
                scene.cycles.samples = samples
            elif hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
                scene.eevee.taa_render_samples = samples
        if saved["use_sequencer"] is not None:
            render.use_sequencer = bool(params.get("use_sequencer", True))

        render.filepath = out_path
        render.use_file_extension = True
        if fmt == "MP4":
            render.image_settings.file_format = "FFMPEG"
            render.ffmpeg.format = "MPEG4"
            render.ffmpeg.codec = "H264"
        else:
            render.image_settings.file_format = "PNG"

        before = set(os.listdir(os.path.dirname(out_path)))
        bpy.ops.render.render(animation=True)
        after = set(os.listdir(os.path.dirname(out_path)))

        if fmt == "MP4":
            base = os.path.splitext(os.path.basename(out_path))[0]
            candidates = [
                os.path.join(os.path.dirname(out_path), filename)
                for filename in after
                if filename.startswith(base) and filename.lower().endswith((".mp4", ".m4v"))
            ]
        else:
            prefix = os.path.basename(out_path)
            candidates = [
                os.path.join(os.path.dirname(out_path), filename)
                for filename in after
                if filename.startswith(prefix) and filename.lower().endswith(".png")
            ]
        candidates.sort()
        produced = [path for path in candidates if os.path.isfile(path)]
        new_files = [path for path in produced if os.path.basename(path) not in before]
        return {
            "scene": scene.name,
            "format": fmt,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "frames": scene.frame_end - scene.frame_start + 1,
            "resolution": [render.resolution_x, render.resolution_y],
            "percentage": render.resolution_percentage,
            "fps": render.fps / max(render.fps_base, 1e-9),
            "use_sequencer": getattr(render, "use_sequencer", None),
            "files": produced[:200],
            "new_files": new_files[:200],
            "file_count": len(produced),
            "bytes": sum(os.path.getsize(path) for path in produced),
            "exists": bool(produced),
        }
    finally:
        render.filepath = saved["filepath"]
        render.image_settings.file_format = saved["file_format"]
        scene.frame_start, scene.frame_end = saved["start"], saved["end"]
        render.resolution_x, render.resolution_y = saved["x"], saved["y"]
        render.resolution_percentage = saved["percentage"]
        render.fps, render.fps_base = saved["fps"], saved["fps_base"]
        render.engine = saved["engine"]
        render.use_file_extension = saved["use_file_extension"]
        if saved["use_sequencer"] is not None:
            render.use_sequencer = saved["use_sequencer"]
        render.ffmpeg.format = saved["ffmpeg_format"]
        render.ffmpeg.codec = saved["ffmpeg_codec"]
        if saved_cycles_samples is not None:
            scene.cycles.samples = saved_cycles_samples
        if saved_eevee_samples is not None:
            scene.eevee.taa_render_samples = saved_eevee_samples
