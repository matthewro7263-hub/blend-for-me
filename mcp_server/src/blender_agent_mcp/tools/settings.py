"""Scene, render, output, color-management, unit and world controls."""

from __future__ import annotations

from typing import Optional

from ..server import call, clean


def register(mcp) -> None:

    @mcp.tool()
    def get_blender_settings() -> dict:
        """Read the persistent production settings for the current scene.

        Returns timeline/FPS, render engine and dimensions, output encoding,
        color management, units, and World background in one call. Use this before
        changing production settings so a prompt does not accidentally turn a
        portrait scene into landscape or overwrite an established output path.
        """
        return call("settings.get")

    @mcp.tool()
    def set_render_settings(
        engine: Optional[str] = None,
        resolution: Optional[list[int]] = None,
        percentage: Optional[int] = None,
        pixel_aspect: Optional[list[float]] = None,
        samples: Optional[int] = None,
        film_transparent: Optional[bool] = None,
        use_motion_blur: Optional[bool] = None,
        use_compositing: Optional[bool] = None,
        use_sequencer: Optional[bool] = None,
        filepath: Optional[str] = None,
        file_format: Optional[str] = None,
        color_mode: Optional[str] = None,
        color_depth: Optional[str] = None,
        compression: Optional[int] = None,
        quality: Optional[int] = None,
        ffmpeg_format: Optional[str] = None,
        video_codec: Optional[str] = None,
        audio_codec: Optional[str] = None,
        video_bitrate: Optional[int] = None,
        audio_bitrate: Optional[int] = None,
        frame_start: Optional[int] = None,
        frame_end: Optional[int] = None,
        frame_step: Optional[int] = None,
        fps: Optional[int] = None,
        fps_base: Optional[float] = None,
        use_overwrite: Optional[bool] = None,
        use_file_extension: Optional[bool] = None,
    ) -> dict:
        """Persist render, output and timeline settings for stills or animation.

        Args:
            engine: BLENDER_EEVEE or CYCLES. CYCLES enables Metal GPU when available.
            resolution: [width, height] pixels; percentage scales both at render time.
            filepath: Blender output path/prefix. This configures future renders;
                it does not render or overwrite a file itself.
            file_format: PNG, OPEN_EXR, JPEG, FFMPEG, etc.
            ffmpeg_format: MPEG4, MATROSKA, QUICKTIME, etc. Used with FFMPEG.
            video_codec / audio_codec: Blender FFmpeg enum ids such as H264 / AAC.
            frame_start / frame_end / frame_step: Animation output range.
            fps_base: 1.001 with fps=30 produces 29.97 fps.

        Call `get_blender_settings` afterwards when building a reusable render
        recipe. `render_frame` intentionally restores temporary overrides; this
        tool is the persistent counterpart.
        """
        return call("settings.set_render", clean(
            engine=engine, resolution=resolution, percentage=percentage,
            pixel_aspect=pixel_aspect, samples=samples,
            film_transparent=film_transparent, use_motion_blur=use_motion_blur,
            use_compositing=use_compositing, use_sequencer=use_sequencer,
            filepath=filepath, file_format=file_format, color_mode=color_mode,
            color_depth=color_depth, compression=compression, quality=quality,
            ffmpeg_format=ffmpeg_format, video_codec=video_codec,
            audio_codec=audio_codec, video_bitrate=video_bitrate,
            audio_bitrate=audio_bitrate, frame_start=frame_start,
            frame_end=frame_end, frame_step=frame_step, fps=fps,
            fps_base=fps_base, use_overwrite=use_overwrite,
            use_file_extension=use_file_extension,
        ), timeout=60.0)

    @mcp.tool()
    def set_color_management(
        display_device: Optional[str] = None,
        view_transform: Optional[str] = None,
        look: Optional[str] = None,
        exposure: Optional[float] = None,
        gamma: Optional[float] = None,
        use_white_balance: Optional[bool] = None,
        white_balance_temperature: Optional[float] = None,
        white_balance_tint: Optional[float] = None,
    ) -> dict:
        """Set the scene's display transform, look, exposure and white balance.

        Enum availability depends on the active OpenColorIO configuration. Read
        `get_blender_settings` first and use Blender's exact spelling (commonly
        AgX with a Medium/High Contrast look). Exposure is measured in stops.
        """
        return call("settings.set_color_management", clean(
            display_device=display_device, view_transform=view_transform, look=look,
            exposure=exposure, gamma=gamma, use_white_balance=use_white_balance,
            white_balance_temperature=white_balance_temperature,
            white_balance_tint=white_balance_tint,
        ))

    @mcp.tool()
    def set_unit_settings(
        system: Optional[str] = None,
        system_rotation: Optional[str] = None,
        scale_length: Optional[float] = None,
        length_unit: Optional[str] = None,
        mass_unit: Optional[str] = None,
        time_unit: Optional[str] = None,
        temperature_unit: Optional[str] = None,
        use_separate: Optional[bool] = None,
    ) -> dict:
        """Set measurement units without changing existing object coordinates.

        `system` is NONE, METRIC or IMPERIAL. `scale_length=1` means one Blender
        unit represents one metre in a metric scene. This affects display and many
        simulations/exporters; it does not rescale existing geometry.
        """
        return call("settings.set_units", clean(
            system=system, system_rotation=system_rotation,
            scale_length=scale_length, length_unit=length_unit,
            mass_unit=mass_unit, time_unit=time_unit,
            temperature_unit=temperature_unit, use_separate=use_separate,
        ))

    @mcp.tool()
    def set_world_settings(
        name: Optional[str] = None,
        create: bool = True,
        make_active: bool = True,
        color: Optional[list[float]] = None,
        use_nodes: Optional[bool] = None,
        surface_color: Optional[list[float]] = None,
        strength: Optional[float] = None,
    ) -> dict:
        """Create/select a World and tune its viewport and shader background.

        Args:
            color: [r,g,b] viewport World color.
            surface_color: [r,g,b] or [r,g,b,a] Background shader color.
            strength: Background shader strength; 1 is neutral, 0 is black.
            make_active: Assign this World to the current scene.

        This is the fast environment-lighting control. For HDRI lighting, build a
        World node graph with `execute_python` until the dedicated world-graph API
        lands; material shader graphs already have dedicated tools.
        """
        return call("settings.set_world", clean(
            name=name, create=create, make_active=make_active, color=color,
            use_nodes=use_nodes, surface_color=surface_color, strength=strength,
        ))
