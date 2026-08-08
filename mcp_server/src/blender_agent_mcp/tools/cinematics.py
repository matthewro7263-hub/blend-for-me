"""Shot planning, camera cuts, Video Sequencer editing and final rendering."""

from __future__ import annotations

from typing import Any, Optional

from ..server import call, clean


def register(mcp) -> None:

    @mcp.tool()
    def list_timeline_markers(scene: Optional[str] = None) -> dict:
        """List timeline markers and derive the effective camera-cut shot ranges.

        Camera-bound markers are sorted into `shots`; each shot runs from its
        marker through the frame before the next camera marker, with the final
        shot ending at the scene end. Plain markers remain useful for beats,
        dialogue cues, VFX notes and editorial sync points.
        """
        return call("cinematics.list_timeline_markers", clean(scene=scene))

    @mcp.tool()
    def build_camera_cuts(
        cuts: list[dict[str, Any]],
        scene: Optional[str] = None,
        clear_existing: bool = False,
        set_scene_range: bool = False,
        frame_end: Optional[int] = None,
    ) -> dict:
        """Build a retry-safe multi-camera shot plan from named cut markers.

        Args:
            cuts: Ordered or unordered specs like
                `{"name":"SHOT_010","frame":1,"camera":"Camera_Wide"}`.
                Names and frames must be unique; camera objects must exist.
            scene: Scene name; defaults to the current scene.
            clear_existing: Remove existing camera-bound markers first. Plain
                production markers survive. False updates cuts by name and refuses
                an ambiguous second camera at an occupied frame.
            set_scene_range: Move scene start to the first cut. `frame_end` is
                applied when supplied.
            frame_end: End of the final shot/render range. Must not precede the
                final camera cut.

        Blender evaluates the bound camera automatically as the playhead crosses
        each marker. The scene camera is also set immediately to the cut active at
        the current frame. Repeating the same named plan updates rather than
        duplicates it, and the whole build is one undo step.
        """
        return call("cinematics.build_camera_cuts", clean(
            cuts=cuts, scene=scene, clear_existing=clear_existing,
            set_scene_range=set_scene_range, frame_end=frame_end,
        ))

    @mcp.tool()
    def remove_timeline_markers(
        names: Optional[list[str]] = None,
        scene: Optional[str] = None,
        camera_only: bool = False,
        remove_all: bool = False,
    ) -> dict:
        """Remove named markers, all camera cuts, or explicitly every marker.

        At least one selector is required; a blank call refuses to do anything.
        `camera_only=true` preserves plain beat/dialogue markers. `remove_all=true`
        is the explicit destructive option.
        """
        return call("cinematics.remove_timeline_markers", clean(
            names=names, scene=scene, camera_only=camera_only, all=remove_all,
        ))

    @mcp.tool()
    def list_sequencer_strips(
        scene: Optional[str] = None, limit: int = 1000,
    ) -> dict:
        """List Video Sequencer strips in timeline order with edit-relevant state.

        Entries include type, channel, visible start/end/duration, source path,
        mute/lock/blend state, audio volume/pan, text settings and transforms where
        applicable. It reads nested Meta contents too, though mutation tools only
        edit top-level strips to avoid hidden destructive changes.
        """
        return call("cinematics.list_sequencer_strips", clean(
            scene=scene, limit=limit,
        ), timeout=30.0)

    @mcp.tool()
    def add_media_strip(
        type: str,
        path: str,
        name: Optional[str] = None,
        scene: Optional[str] = None,
        channel: int = 1,
        frame_start: int = 1,
        frame_end: Optional[int] = None,
        duration: Optional[int] = None,
        fit_method: str = "FIT",
        volume: Optional[float] = None,
        pan: Optional[float] = None,
        add_audio: bool = False,
        audio_channel: Optional[int] = None,
        reuse_existing: bool = True,
    ) -> dict:
        """Add a still image, movie or sound to Blender's Video Sequencer.

        Args:
            type: `IMAGE`, `MOVIE`, or `SOUND`.
            path: Existing local media file on the Blender machine.
            name: Stable strip name. Defaults to the filename. A same-name/type/
                source strip is safely reused on retry.
            channel: Sequencer track, 1-128. Higher channels composite over lower.
            frame_start: First visible frame.
            frame_end: Exclusive visible end. Pass this or `duration`, not both.
            duration: Visible frame count; especially useful for still images.
            fit_method: Image/movie sizing such as `FIT`, `FILL`, or `STRETCH`.
            volume / pan: Audio controls when the strip supports them.
            add_audio: For MOVIE, also create/reuse `<name>.Audio` from its audio
                stream. Blender video strips do not include sound automatically.
            audio_channel: Channel for paired movie audio. Defaults adjacent.
            reuse_existing: Retry-safe default. False refuses duplicate names.

        Source mismatch under an existing name is refused rather than silently
        replacing an edit. Use `remove_sequencer_strips` or choose a new name.
        """
        return call("cinematics.add_media_strip", clean(
            type=type, path=path, name=name, scene=scene, channel=channel,
            frame_start=frame_start, frame_end=frame_end, duration=duration,
            fit_method=fit_method, volume=volume, pan=pan, add_audio=add_audio,
            audio_channel=audio_channel, reuse_existing=reuse_existing,
        ), timeout=60.0)

    @mcp.tool()
    def add_text_strip(
        name: str,
        text: str,
        frame_start: int,
        frame_end: int,
        scene: Optional[str] = None,
        channel: int = 3,
        font_size: Optional[float] = None,
        color: Optional[list[float]] = None,
        location: Optional[list[float]] = None,
        alignment_x: Optional[str] = None,
        anchor_x: Optional[str] = None,
        anchor_y: Optional[str] = None,
        wrap_width: Optional[float] = None,
        use_shadow: Optional[bool] = None,
        shadow_color: Optional[list[float]] = None,
        shadow_angle: Optional[float] = None,
        shadow_offset: Optional[float] = None,
        shadow_blur: Optional[float] = None,
        use_outline: Optional[bool] = None,
        outline_color: Optional[list[float]] = None,
        outline_width: Optional[float] = None,
        use_box: Optional[bool] = None,
        box_color: Optional[list[float]] = None,
        box_margin: Optional[float] = None,
        box_roundness: Optional[float] = None,
        use_bold: Optional[bool] = None,
        use_italic: Optional[bool] = None,
    ) -> dict:
        """Create or update a title, lower-third, caption or subtitle strip.

        Frames use an exclusive `frame_end`. Colours are RGBA 0-1; `location` is
        normalized render space. Repeating a TEXT strip name updates it, making a
        generated subtitle pass safe to rerun. Shadow, outline and box controls
        provide readable broadcast-style captions without hand-editing Blender UI.
        """
        return call("cinematics.add_text_strip", clean(
            name=name, text=text, frame_start=frame_start, frame_end=frame_end,
            scene=scene, channel=channel, font_size=font_size, color=color,
            location=location, alignment_x=alignment_x, anchor_x=anchor_x,
            anchor_y=anchor_y, wrap_width=wrap_width, use_shadow=use_shadow,
            shadow_color=shadow_color, shadow_angle=shadow_angle,
            shadow_offset=shadow_offset, shadow_blur=shadow_blur,
            use_outline=use_outline, outline_color=outline_color,
            outline_width=outline_width, use_box=use_box, box_color=box_color,
            box_margin=box_margin, box_roundness=box_roundness,
            use_bold=use_bold, use_italic=use_italic,
        ))

    @mcp.tool()
    def add_color_strip(
        name: str,
        frame_start: int,
        frame_end: int,
        color: Optional[list[float]] = None,
        scene: Optional[str] = None,
        channel: int = 1,
    ) -> dict:
        """Create/update a solid RGB background, slate, flash or transition card.

        `frame_end` is exclusive. Higher-channel image/text strips composite over
        the color. Reusing the same COLOR name updates its timing and colour.
        """
        return call("cinematics.add_color_strip", clean(
            name=name, frame_start=frame_start, frame_end=frame_end, color=color,
            scene=scene, channel=channel,
        ))

    @mcp.tool()
    def update_strip(
        name: str, updates: dict[str, Any], scene: Optional[str] = None,
    ) -> dict:
        """Update timing, compositing, audio, text or 2D transform on one strip.

        Supported keys: `channel`, `start`, `end`, `duration`, `mute`, `lock`,
        `blend_type`, `blend_alpha`, `volume`, `pan`, `text`, `font_size`, `color`,
        `location`, caption alignment/style fields, and `transform` containing
        `offset_x`, `offset_y`, `scale_x`, `scale_y`, `rotation`. Unsupported fields
        fail with an allowlist. Pass `end` or `duration`, not both.
        """
        return call("cinematics.update_strip", clean(
            name=name, updates=updates, scene=scene,
        ))

    @mcp.tool()
    def remove_sequencer_strips(
        names: list[str], scene: Optional[str] = None,
    ) -> dict:
        """Remove explicitly named top-level Video Sequencer strips.

        There is no implicit clear-all. Missing names are reported separately so
        cleanup calls are safely retryable. Nested Meta-strip contents are refused.
        """
        return call("cinematics.remove_sequencer_strips", clean(
            names=names, scene=scene,
        ))

    @mcp.tool()
    def render_animation(
        out_path: str,
        scene: Optional[str] = None,
        format: str = "MP4",
        frame_start: Optional[int] = None,
        frame_end: Optional[int] = None,
        resolution: Optional[list[int]] = None,
        percentage: int = 100,
        fps: Optional[int] = None,
        fps_base: Optional[float] = None,
        engine: Optional[str] = None,
        samples: Optional[int] = None,
        use_sequencer: bool = True,
        timeout: float = 3600.0,
    ) -> dict:
        """Render the final camera-cut animation or edited sequencer to MP4/PNGs.

        Unlike GUI-only `playblast`, this is the real render pipeline and works
        headless. With `use_sequencer=true` it outputs the Video Sequencer edit;
        otherwise it renders the active camera and timeline-bound camera cuts.

        `MP4` configures MPEG-4/H.264. `PNG` writes a numbered image sequence using
        `out_path` as the prefix. Optional range/resolution/fps/engine/samples are
        temporary and restored afterward. Rendering is intentionally allowed to be
        long; set `timeout` for the production workload. The result verifies files,
        byte count and frame count but never returns video bytes.
        """
        return call("cinematics.render_animation", clean(
            out_path=out_path, scene=scene, format=format,
            frame_start=frame_start, frame_end=frame_end, resolution=resolution,
            percentage=percentage, fps=fps, fps_base=fps_base, engine=engine,
            samples=samples, use_sequencer=use_sequencer,
        ), timeout=timeout)
