"""Persistent scene, render, output, color-management, unit and world settings."""

from __future__ import annotations

import bpy

from .. import ctx
from ..registry import command


def _enum_ids(owner, prop: str) -> list[str]:
    return [item.identifier for item in owner.bl_rna.properties[prop].enum_items]


def _set_enum(owner, prop: str, value) -> str:
    wanted = str(value).upper()
    valid = _enum_ids(owner, prop)
    matching = next((item for item in valid if item.upper() == wanted), None)
    if matching is None:
        raise ValueError(f"{prop} must be one of {valid}, got {value!r}")
    setattr(owner, prop, matching)
    return matching


def _world(name: str | None = None, *, create: bool = True):
    if name:
        world = bpy.data.worlds.get(name)
        if world is None and create:
            world = bpy.data.worlds.new(name)
        if world is None:
            raise KeyError(f"no world named {name!r}; available: {sorted(bpy.data.worlds.keys())}")
        return world
    world = bpy.context.scene.world
    if world is None and create:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    if world is None:
        raise RuntimeError("the scene has no World datablock")
    return world


def _background_node(world):
    # use_nodes remains the supported switch in 5.2, though Blender warns that
    # the compatibility property is planned to disappear in 6.0.
    if world.node_tree is None:
        world.use_nodes = True
    tree = world.node_tree
    node = next((item for item in tree.nodes if item.type == "BACKGROUND"), None)
    if node is None:
        node = tree.nodes.new("ShaderNodeBackground")
        output = next((item for item in tree.nodes if item.type == "OUTPUT_WORLD"), None)
        if output is None:
            output = tree.nodes.new("ShaderNodeOutputWorld")
        tree.links.new(node.outputs["Background"], output.inputs["Surface"])
    return node


def _settings_snapshot() -> dict:
    scene = bpy.context.scene
    render = scene.render
    image = render.image_settings
    view = scene.view_settings
    units = scene.unit_settings
    world = scene.world
    result = {
        "scene": scene.name,
        "frame": {
            "current": scene.frame_current,
            "start": scene.frame_start,
            "end": scene.frame_end,
            "step": scene.frame_step,
            "fps": render.fps,
            "fps_base": render.fps_base,
            "effective_fps": render.fps / render.fps_base,
        },
        "render": {
            "engine": render.engine,
            "available_engines": _enum_ids(render, "engine"),
            "resolution": [render.resolution_x, render.resolution_y],
            "percentage": render.resolution_percentage,
            "pixel_aspect": [render.pixel_aspect_x, render.pixel_aspect_y],
            "film_transparent": render.film_transparent,
            "use_motion_blur": render.use_motion_blur,
            "use_compositing": render.use_compositing,
            "use_sequencer": render.use_sequencer,
        },
        "output": {
            "filepath": render.filepath,
            "file_format": image.file_format,
            "available_formats": _enum_ids(image, "file_format"),
            "color_mode": image.color_mode,
            "color_depth": image.color_depth,
            "compression": image.compression,
            "quality": image.quality,
            "use_file_extension": render.use_file_extension,
            "use_overwrite": render.use_overwrite,
            "ffmpeg": {
                "format": render.ffmpeg.format,
                "codec": render.ffmpeg.codec,
                "audio_codec": render.ffmpeg.audio_codec,
                "video_bitrate": render.ffmpeg.video_bitrate,
                "audio_bitrate": render.ffmpeg.audio_bitrate,
            },
        },
        "color_management": {
            "display_device": scene.display_settings.display_device,
            "view_transform": view.view_transform,
            "look": view.look,
            "exposure": view.exposure,
            "gamma": view.gamma,
            "use_white_balance": view.use_white_balance,
            "white_balance_temperature": view.white_balance_temperature,
            "white_balance_tint": view.white_balance_tint,
        },
        "units": {
            "system": units.system,
            "system_rotation": units.system_rotation,
            "scale_length": units.scale_length,
            "length_unit": units.length_unit,
            "mass_unit": units.mass_unit,
            "time_unit": units.time_unit,
            "temperature_unit": units.temperature_unit,
            "use_separate": units.use_separate,
        },
        "world": None,
    }
    if world is not None:
        background = (next((item for item in world.node_tree.nodes
                            if item.type == "BACKGROUND"), None)
                      if world.node_tree is not None else None)
        result["world"] = {
            "name": world.name,
            "color": list(world.color),
            "use_nodes": world.node_tree is not None,
            "surface_color": list(background.inputs["Color"].default_value)
            if background else None,
            "strength": float(background.inputs["Strength"].default_value)
            if background else None,
        }
    return result


@command("settings.get")
def get_settings(params: dict) -> dict:
    """Read persistent scene, render, output, color, unit and world settings."""
    return _settings_snapshot()


@command("settings.set_render", mutates=True)
def set_render(params: dict) -> dict:
    """Configure render engine, dimensions, output encoding and timeline."""
    scene = bpy.context.scene
    render = scene.render
    image = render.image_settings
    gpu = None

    if params.get("engine") is not None:
        engine = str(params["engine"]).upper()
        if engine == "CYCLES":
            gpu = ctx.enable_cycles_metal()
        _set_enum(render, "engine", engine)
        if engine == "CYCLES" and gpu and gpu.get("device_type") == "METAL":
            scene.cycles.device = "GPU"

    resolution = params.get("resolution")
    if resolution is not None:
        if len(resolution) != 2 or int(resolution[0]) < 1 or int(resolution[1]) < 1:
            raise ValueError("resolution must be [width, height] with both values >= 1")
        render.resolution_x, render.resolution_y = int(resolution[0]), int(resolution[1])
    if params.get("percentage") is not None:
        render.resolution_percentage = int(params["percentage"])
    if params.get("pixel_aspect") is not None:
        aspect = params["pixel_aspect"]
        if len(aspect) != 2:
            raise ValueError("pixel_aspect must be [x, y]")
        render.pixel_aspect_x, render.pixel_aspect_y = float(aspect[0]), float(aspect[1])

    simple_render = {
        "film_transparent": bool,
        "use_motion_blur": bool,
        "use_compositing": bool,
        "use_sequencer": bool,
        "use_overwrite": bool,
        "use_file_extension": bool,
    }
    for key, cast in simple_render.items():
        if params.get(key) is not None:
            setattr(render, key, cast(params[key]))
    if params.get("filepath") is not None:
        render.filepath = str(params["filepath"])

    for key in ("file_format", "color_mode", "color_depth"):
        if params.get(key) is not None:
            _set_enum(image, key, params[key])
    if params.get("compression") is not None:
        image.compression = int(params["compression"])
    if params.get("quality") is not None:
        image.quality = int(params["quality"])

    ffmpeg_fields = {
        "ffmpeg_format": ("format", str),
        "video_codec": ("codec", str),
        "audio_codec": ("audio_codec", str),
        "video_bitrate": ("video_bitrate", int),
        "audio_bitrate": ("audio_bitrate", int),
    }
    for key, (attr, cast) in ffmpeg_fields.items():
        if params.get(key) is not None:
            value = cast(params[key])
            if isinstance(value, str):
                _set_enum(render.ffmpeg, attr, value)
            else:
                setattr(render.ffmpeg, attr, value)

    frame_fields = {
        "frame_start": ("frame_start", int),
        "frame_end": ("frame_end", int),
        "frame_step": ("frame_step", int),
    }
    for key, (attr, cast) in frame_fields.items():
        if params.get(key) is not None:
            setattr(scene, attr, cast(params[key]))
    if scene.frame_end < scene.frame_start:
        raise ValueError("frame_end must be >= frame_start")
    if params.get("fps") is not None:
        render.fps = int(params["fps"])
    if params.get("fps_base") is not None:
        render.fps_base = float(params["fps_base"])

    samples = params.get("samples")
    sample_note = None
    if samples is not None:
        if render.engine == "CYCLES" and hasattr(scene, "cycles"):
            scene.cycles.samples = int(samples)
        elif hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = int(samples)
        else:
            sample_note = f"samples is not exposed for render engine {render.engine}"

    result = _settings_snapshot()
    result["gpu"] = gpu
    if sample_note:
        result["note"] = sample_note
    return result


@command("settings.set_color_management", mutates=True)
def set_color_management(params: dict) -> dict:
    """Set display transform, look, exposure, gamma and white balance."""
    scene = bpy.context.scene
    view = scene.view_settings
    if params.get("display_device") is not None:
        _set_enum(scene.display_settings, "display_device", params["display_device"])
    for key in ("view_transform", "look"):
        if params.get(key) is not None:
            _set_enum(view, key, params[key])
    for key in ("exposure", "gamma", "white_balance_temperature", "white_balance_tint"):
        if params.get(key) is not None:
            setattr(view, key, float(params[key]))
    if params.get("use_white_balance") is not None:
        view.use_white_balance = bool(params["use_white_balance"])
    return _settings_snapshot()["color_management"]


@command("settings.set_units", mutates=True)
def set_units(params: dict) -> dict:
    """Set scene measurement units and scale."""
    units = bpy.context.scene.unit_settings
    for key in ("system", "system_rotation", "length_unit", "mass_unit",
                "time_unit", "temperature_unit"):
        if params.get(key) is not None:
            _set_enum(units, key, params[key])
    if params.get("scale_length") is not None:
        units.scale_length = float(params["scale_length"])
    if params.get("use_separate") is not None:
        units.use_separate = bool(params["use_separate"])
    return _settings_snapshot()["units"]


@command("settings.set_world", mutates=True)
def set_world(params: dict) -> dict:
    """Create/select a World and set viewport color plus shader background."""
    world = _world(params.get("name"), create=bool(params.get("create", True)))
    if params.get("make_active", True):
        bpy.context.scene.world = world
    if params.get("color") is not None:
        color = params["color"]
        if len(color) < 3:
            raise ValueError("color must contain at least [r, g, b]")
        world.color = [float(v) for v in color[:3]]

    wants_nodes = any(params.get(key) is not None for key in ("surface_color", "strength"))
    if params.get("use_nodes") is not None:
        world.use_nodes = bool(params["use_nodes"])
    if wants_nodes:
        world.use_nodes = True
        background = _background_node(world)
        if params.get("surface_color") is not None:
            color = list(params["surface_color"])
            if len(color) == 3:
                color.append(1.0)
            if len(color) != 4:
                raise ValueError("surface_color must be [r, g, b] or [r, g, b, a]")
            background.inputs["Color"].default_value = [float(v) for v in color]
        if params.get("strength") is not None:
            background.inputs["Strength"].default_value = float(params["strength"])
    return _settings_snapshot()["world"]
