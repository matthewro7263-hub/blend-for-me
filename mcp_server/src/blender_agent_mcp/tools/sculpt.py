"""Sculpting tools: brushes, strokes, remeshing, masks, face sets, filters."""

from __future__ import annotations

import json
from typing import Any, Optional

from ..server import call, clean, png_image

def _with_shot(result: dict, summary_keys: tuple[str, ...]) -> Any:
    """Return [summary text, Image] when a screenshot came back, else the dict.

    Splitting it this way means the agent both sees the picture and gets the
    numbers, without the base64 blob landing in its context.
    """
    shot = result.pop("screenshot", None)
    summary = {k: result[k] for k in summary_keys if k in result}
    summary.update({k: v for k, v in result.items()
                    if k not in summary_keys and k != "screenshot_note"})
    if not shot:
        note = result.get("screenshot_note")
        if note:
            summary["screenshot"] = f"unavailable: {note}"
        return summary
    return [json.dumps(summary, default=str), png_image(shot)]


def register(mcp) -> None:

    # -- setup ---------------------------------------------------------
    @mcp.tool()
    def enter_sculpt(object: Optional[str] = None) -> dict:
        """Make an object active and switch to Sculpt Mode.

        Call this before any other sculpt tool. Sculpting needs a MESH; convert
        curves/text first. A dense mesh sculpts better — `voxel_remesh` an
        object first if it is a low-poly primitive.
        """
        return call("sculpt.enter", clean(object=object))

    @mcp.tool()
    def get_sculpt_state() -> dict:
        """Current brush, size/strength, dyntopo, symmetry, mask and face-set state.

        Read this before changing settings — it also reports whether *unified*
        size/strength is on, which decides where a size change has to be written.
        """
        return call("sculpt.get_state")

    @mcp.tool()
    def sculpt_list_brushes() -> dict:
        """Every available sculpt brush asset name, plus the friendly-name aliases.

        Blender 5.x brushes are assets with compound names (`Inflate/Deflate`,
        `Scrape/Fill`, `Crease Sharp`). `sculpt_set_brush` accepts the friendly
        short names too; this is the authoritative list when one is not matching.
        """
        return call("sculpt.list_brushes")

    @mcp.tool()
    def sculpt_set_brush(
        name: Optional[str] = None,
        size_px: Optional[int] = None,
        strength: Optional[float] = None,
        direction: Optional[str] = None,
        hardness: Optional[float] = None,
        auto_smooth: Optional[float] = None,
        normal_radius: Optional[float] = None,
        falloff_shape: Optional[str] = None,
        use_frontface: Optional[bool] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Activate a sculpt brush and set its parameters.

        Args:
            name: Brush name. Friendly names are resolved to the real 5.x asset
                (`Inflate` → `Inflate/Deflate`, `Crease` → `Crease Sharp`,
                `Scrape` → `Scrape/Fill`, `Elastic Deform` → `Elastic Grab`).
                An unknown name returns the full list of valid ones.
            size_px: Brush radius in **screen pixels**, not world units. Its world
                footprint therefore depends on how far the viewport is zoomed —
                40-80 is a normal working range.
            strength: 0-1.
            direction: ADD or SUBTRACT — this is how you carve instead of build
                (e.g. Draw with SUBTRACT digs in).
            hardness: 0-1, falloff sharpness at the brush edge.
            auto_smooth: 0-1 smoothing blended in with every dab.
            normal_radius: 0-1, how much surrounding area informs the surface
                normal the brush works along.
            falloff_shape: SPHERE (default) or PROJECTED.
            use_frontface: Affect only front-facing geometry.

        Size and strength are written to the unified paint settings when those
        are enabled, because writing `brush.size` is silently ignored in that
        case. The result reports which field was actually written.
        """
        return call("sculpt.set_brush", clean(
            name=name, size_px=size_px, strength=strength, direction=direction,
            hardness=hardness, auto_smooth=auto_smooth, normal_radius=normal_radius,
            falloff_shape=falloff_shape, use_frontface=use_frontface, object=object))

    @mcp.tool()
    def sculpt_symmetry(
        x: Optional[bool] = None,
        y: Optional[bool] = None,
        z: Optional[bool] = None,
        feather: Optional[bool] = None,
        radial_counts: Optional[int] = None,
    ) -> dict:
        """Set mirror symmetry axes for sculpting.

        Mirroring is about the object's **local** origin, so an off-centre origin
        mirrors to the wrong place — check with `get_object_info` first.

        `radial_counts` is accepted but radial symmetry was removed in Blender
        5.x; the result says `radial_supported: false` and nothing is silently
        applied. Use `radial_strokes` for radial patterns instead.
        """
        return call("sculpt.symmetry", clean(x=x, y=y, z=z, feather=feather,
                                             radial_counts=radial_counts))

    # -- strokes -------------------------------------------------------
    @mcp.tool()
    def sculpt_stroke(
        points: list[dict],
        space: str = "OBJECT",
        mode: str = "NORMAL",
        size_px: Optional[int] = None,
        return_screenshot: bool = True,
        object: Optional[str] = None,
    ) -> Any:
        """Apply a brush stroke through a list of 3D points. GUI Blender only — fails under `blender --background`.

        The low-level stroke primitive; `stroke_line`, `stroke_curve`,
        `stroke_on_surface` and `radial_strokes` are conveniences over it.

        Args:
            points: At least two `{"location": [x, y, z], "pressure": 0-1,
                "size": px}` entries. `pressure` and `size` are optional per
                point and default to 1.0 and the active brush size.
            space: OBJECT (default) or WORLD — which space `location` is in.
                Get an object's matrix from `get_object_info` to convert.
            mode: NORMAL, or INVERT to flip the brush direction for this stroke.
            size_px: Override the brush size for this stroke only.
            return_screenshot: Returns a viewport image alongside the result
                (default true) so you can see what the stroke actually did.

        The stroke is applied by the real brush engine, so the active brush,
        strength, symmetry and dyntopo settings all apply. Points that project
        behind the camera are reported in `dropped_points` rather than silently
        skipped — if you see those, orbit the view to face the work.
        """
        result = call("sculpt.stroke", clean(
            points=points, space=space, mode=mode, size_px=size_px,
            return_screenshot=return_screenshot, object=object), timeout=90.0)
        return _with_shot(result, ("object", "points_applied", "vertices"))

    @mcp.tool()
    def stroke_line(
        a: list[float],
        b: list[float],
        steps: int = 12,
        space: str = "OBJECT",
        mode: str = "NORMAL",
        size_px: Optional[int] = None,
        return_screenshot: bool = True,
        object: Optional[str] = None,
    ) -> Any:
        """Straight brush stroke from point a to point b. GUI Blender only — fails under `blender --background`.

        Args:
            a, b: [x, y, z] endpoints.
            steps: Points sampled along the line. More steps means a smoother,
                more continuous stroke; below ~8 it reads as separate dabs.

        The everyday tool for laying in a ridge, limb or crease.
        """
        result = call("sculpt.stroke_line", clean(
            a=a, b=b, steps=steps, space=space, mode=mode, size_px=size_px,
            return_screenshot=return_screenshot, object=object), timeout=90.0)
        return _with_shot(result, ("object", "points_applied", "vertices"))

    @mcp.tool()
    def stroke_curve(
        control_points: list[list[float]],
        steps: int = 24,
        space: str = "OBJECT",
        mode: str = "NORMAL",
        size_px: Optional[int] = None,
        return_screenshot: bool = True,
        object: Optional[str] = None,
    ) -> Any:
        """Smooth brush stroke along a spline through control points. GUI Blender only — fails under `blender --background`.

        Args:
            control_points: Two or more [x, y, z] points. The stroke is a
                Catmull-Rom spline that passes **through** every one of them.
            steps: Samples along the whole curve.

        Use for organic shapes — spines, tails, muscle flow — where a straight
        `stroke_line` would look mechanical.
        """
        result = call("sculpt.stroke_curve", clean(
            control_points=control_points, steps=steps, space=space, mode=mode,
            size_px=size_px, return_screenshot=return_screenshot, object=object),
            timeout=90.0)
        return _with_shot(result, ("object", "points_applied", "vertices"))

    @mcp.tool()
    def stroke_on_surface(
        view_path_2d: list[list[float]],
        normalized: bool = True,
        mode: str = "NORMAL",
        size_px: Optional[int] = None,
        return_screenshot: bool = True,
        object: Optional[str] = None,
    ) -> Any:
        """Draw a stroke by tracing a 2D path across the viewport. GUI Blender only — fails under `blender --background`.

        Each 2D point is raycast into the scene and the stroke follows wherever
        it lands on the surface — the closest thing to drawing on the model with
        a pen, and much easier than computing 3D coordinates yourself.

        Args:
            view_path_2d: [[x, y], ...] points across the viewport.
            normalized: When true (default) coordinates are 0-1 fractions of the
                viewport, so [0.5, 0.5] is the centre regardless of window size.
                Set false to use raw region pixels.

        Take a `viewport_screenshot` first to see what you are aiming at. Points
        whose ray misses the model are reported in `missed_rays`.
        """
        result = call("sculpt.stroke_on_surface", clean(
            view_path_2d=view_path_2d, normalized=normalized, mode=mode,
            size_px=size_px, return_screenshot=return_screenshot, object=object),
            timeout=90.0)
        return _with_shot(result, ("object", "points_applied", "vertices"))

    @mcp.tool()
    def radial_strokes(
        center: list[float],
        radius: float,
        count: int = 6,
        steps: int = 8,
        axis: str = "Z",
        inward: bool = False,
        space: str = "OBJECT",
        mode: str = "NORMAL",
        size_px: Optional[int] = None,
        return_screenshot: bool = True,
        object: Optional[str] = None,
    ) -> Any:
        """Strokes radiating out from a centre point, evenly spaced. GUI Blender only — fails under `blender --background`.

        The practical replacement for radial symmetry, which Blender 5.x removed.
        Good for spikes, petals, frills and crown shapes.

        Args:
            center: [x, y, z] hub of the pattern.
            radius: Length of each stroke, in **world/object units** (unlike
                brush size, which is pixels).
            count: Number of strokes around the circle.
            axis: X, Y or Z — the axis the circle is perpendicular to.
            inward: Stroke from the rim toward the centre instead of outward.
                Matters for directional brushes like Snake Hook and Grab.
        """
        result = call("sculpt.radial_strokes", clean(
            center=center, radius=radius, count=count, steps=steps, axis=axis,
            inward=inward, space=space, mode=mode, size_px=size_px,
            return_screenshot=return_screenshot, object=object), timeout=180.0)
        return _with_shot(result, ("object", "strokes", "points_applied", "vertices"))

    # -- topology ------------------------------------------------------
    @mcp.tool()
    def voxel_remesh(
        voxel_size: float = 0.05,
        preserve_volume: bool = True,
        adaptivity: Optional[float] = None,
        preserve_attributes: Optional[bool] = None,
        fix_poles: Optional[bool] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Rebuild the mesh as an even voxel grid. The core sculpting workflow step.

        Use it to give a shape uniform density before sculpting, and again
        whenever strokes have stretched the topology. It welds intersecting
        parts into one surface, which is how you build a creature from blobs.

        Args:
            voxel_size: Cell size in **world units** — smaller means more detail
                and far more vertices. Cost scales roughly cubically: on a
                2-unit object, 0.05 is a sane start, 0.02 is dense, 0.01 may
                produce millions of vertices and take minutes.
            preserve_volume: Keep the original volume rather than shrinking.
            adaptivity: >0 reduces polygons in flat regions.

        Destroys UVs and vertex groups. Remesh first, then unwrap and weight.
        Works headless.
        """
        return call("sculpt.voxel_remesh", clean(
            voxel_size=voxel_size, preserve_volume=preserve_volume,
            adaptivity=adaptivity, preserve_attributes=preserve_attributes,
            fix_poles=fix_poles, object=object), timeout=300.0)

    @mcp.tool()
    def quadriflow_remesh(
        target_faces: int = 5000,
        mode: str = "FACES",
        target_ratio: Optional[float] = None,
        target_edge_length: Optional[float] = None,
        preserve_sharp: Optional[bool] = None,
        preserve_boundary: Optional[bool] = None,
        smooth_normals: Optional[bool] = None,
        use_mesh_symmetry: Optional[bool] = None,
        seed: Optional[int] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Retopologise into clean, evenly-flowing quads. Slow but high quality.

        Use for a final production mesh after sculpting; use `voxel_remesh`
        during sculpting, since this takes tens of seconds to minutes.

        Args:
            mode: FACES (use `target_faces`), RATIO (`target_ratio`, a fraction
                of the current count) or EDGE (`target_edge_length`).
            target_faces: Desired quad count in FACES mode.
            preserve_sharp: Keep sharp edges — important for hard-surface models.

        Fails on non-manifold meshes; `voxel_remesh` first if it errors.
        Works headless. Allow a generous timeout.
        """
        return call("sculpt.quadriflow_remesh", clean(
            target_faces=target_faces, mode=mode, target_ratio=target_ratio,
            target_edge_length=target_edge_length, preserve_sharp=preserve_sharp,
            preserve_boundary=preserve_boundary, smooth_normals=smooth_normals,
            use_mesh_symmetry=use_mesh_symmetry, seed=seed, object=object),
            timeout=600.0)

    @mcp.tool()
    def dyntopo_enable(
        detail: Optional[float] = None,
        mode: str = "RELATIVE",
        refine_method: Optional[str] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Turn on dynamic topology — the mesh subdivides under the brush as you sculpt.

        Best for rough shaping where you do not know where detail will be needed.
        Unlike `voxel_remesh` it adds detail only where you sculpt.

        Args:
            mode: RELATIVE (detail relative to screen size), CONSTANT (a fixed
                world resolution), BRUSH (relative to brush size), or MANUAL.
            detail: Meaning depends on mode — `detail_size` for RELATIVE,
                `constant_detail_resolution` for CONSTANT, a percentage for BRUSH.
            refine_method: SUBDIVIDE, COLLAPSE or SUBDIVIDE_COLLAPSE.

        Dyntopo discards vertex colours and multires, and does not coexist with a
        Multires modifier.
        """
        return call("sculpt.dyntopo_enable", clean(
            detail=detail, mode=mode, refine_method=refine_method, object=object),
            timeout=60.0)

    @mcp.tool()
    def dyntopo_disable(object: Optional[str] = None) -> dict:
        """Turn dynamic topology off, keeping the current geometry."""
        return call("sculpt.dyntopo_disable", clean(object=object), timeout=60.0)

    @mcp.tool()
    def dyntopo_flood_fill(object: Optional[str] = None) -> dict:
        """Re-tessellate the whole mesh to the current dyntopo detail level. GUI Blender only — fails under `blender --background`.

        Applies the detail setting everywhere at once instead of only where you
        have brushed. Requires dyntopo to be enabled.
        """
        return call("sculpt.dyntopo_flood_fill", clean(object=object), timeout=300.0)

    # -- masks ---------------------------------------------------------
    @mcp.tool()
    def mask_box(
        xmin: int, xmax: int, ymin: int, ymax: int,
        mode: str = "VALUE",
        value: float = 1.0,
        front_faces_only: bool = False,
        object: Optional[str] = None,
    ) -> dict:
        """Mask a rectangular screen region. GUI Blender only — fails under `blender --background`.

        Coordinates are **region pixels** with the origin bottom-left. Take a
        `viewport_screenshot` first — note its top-left origin means you must
        flip y. Masked areas are protected from sculpting; invert the mask to
        work only inside the box instead.
        """
        return call("sculpt.mask_box", clean(
            xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax, mode=mode, value=value,
            front_faces_only=front_faces_only, object=object))

    @mcp.tool()
    def mask_from_selection(
        value: float = 1.0, invert: bool = False, object: Optional[str] = None,
    ) -> dict:
        """Build the sculpt mask from the mesh's selected vertices.

        Writes the mask attribute directly, so unlike every other mask tool this
        one **works headless**. Select vertices with `select_geometry` first.

        Args:
            invert: Mask the *unselected* vertices instead — usually what you
                want, since it protects everything except your selection.
        """
        return call("sculpt.mask_from_selection", clean(
            value=value, invert=invert, object=object), timeout=60.0)

    @mcp.tool()
    def mask_by_cavity(
        mix_mode: Optional[str] = None,
        mix_factor: Optional[float] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Mask by surface cavity — automatically finds creases and crevices. GUI Blender only — fails under `blender --background`.

        Handy for aging/wear passes: mask the cavities, invert, and sculpt only
        the raised areas (or vice versa).
        """
        return call("sculpt.mask_by_cavity", clean(
            mix_mode=mix_mode, mix_factor=mix_factor, object=object))

    @mcp.tool()
    def invert_mask(object: Optional[str] = None) -> dict:
        """Invert the sculpt mask — protected becomes editable and vice versa. GUI Blender only — fails under `blender --background`."""
        return call("sculpt.invert_mask", clean(object=object))

    @mcp.tool()
    def clear_mask(value: float = 0.0, object: Optional[str] = None) -> dict:
        """Clear the sculpt mask so the whole surface is editable again. GUI Blender only — fails under `blender --background`."""
        return call("sculpt.clear_mask", clean(value=value, object=object))

    @mcp.tool()
    def mask_filter(
        filter_type: str = "SMOOTH",
        iterations: int = 1,
        auto_iteration_count: bool = False,
        object: Optional[str] = None,
    ) -> dict:
        """Grow, shrink, smooth or sharpen the existing mask. GUI Blender only — fails under `blender --background`.

        Args:
            filter_type: GROW, SHRINK, SMOOTH, SHARPEN, CONTRAST_INCREASE or
                CONTRAST_DECREASE.
            iterations: Repeat count — GROW with 5 spreads much further than 1.

        SMOOTH after `mask_from_selection` softens hard mask borders so sculpting
        blends instead of leaving a visible step.
        """
        return call("sculpt.mask_filter", clean(
            filter_type=filter_type, iterations=iterations,
            auto_iteration_count=auto_iteration_count, object=object), timeout=60.0)

    # -- face sets -----------------------------------------------------
    @mcp.tool()
    def face_sets_create(mode: str = "MASKED", object: Optional[str] = None) -> dict:
        """Create a face set from the mask, visible geometry, all, or selection. GUI Blender only — fails under `blender --background`.

        Face sets are named regions you can hide or isolate — the practical way
        to work on one limb without disturbing the rest.

        Args:
            mode: MASKED, VISIBLE, ALL or SELECTION.
        """
        return call("sculpt.face_sets_create", clean(mode=mode, object=object))

    @mcp.tool()
    def face_sets_init(
        mode: str = "LOOSE_PARTS",
        threshold: Optional[float] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Generate face sets automatically from mesh structure. GUI Blender only — fails under `blender --background`.

        Args:
            mode: LOOSE_PARTS (separate shells — the usual choice after joining
                objects), MATERIALS, NORMALS, UV_SEAMS, CREASES, BEVEL_WEIGHT,
                SHARP_EDGES or FACE_SET_BOUNDARIES.
            threshold: Angle sensitivity for the NORMALS mode.
        """
        return call("sculpt.face_sets_init", clean(
            mode=mode, threshold=threshold, object=object), timeout=120.0)

    @mcp.tool()
    def face_set_visibility(
        mode: str = "TOGGLE",
        active_face_set: Optional[int] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Show or hide face sets: TOGGLE, SHOW_ACTIVE or HIDE_ACTIVE. GUI Blender only — fails under `blender --background`.

        Hidden geometry is excluded from sculpting entirely. Call `reveal_all`
        when finished — it is easy to forget geometry is hidden and conclude a
        brush is broken.
        """
        return call("sculpt.face_set_visibility", clean(
            mode=mode, active_face_set=active_face_set, object=object))

    @mcp.tool()
    def reveal_all(object: Optional[str] = None) -> dict:
        """Unhide all geometry hidden by face-set visibility. GUI Blender only — fails under `blender --background`."""
        return call("sculpt.reveal_all", clean(object=object))

    # -- filters -------------------------------------------------------
    @mcp.tool()
    def sculpt_mesh_filter(
        type: str = "SMOOTH",
        strength: float = 1.0,
        iterations: int = 1,
        deform_axis: Optional[str] = None,
        orientation: Optional[str] = None,
        return_screenshot: bool = False,
        object: Optional[str] = None,
    ) -> Any:
        """Apply a filter to the whole mesh at once, no brushing. GUI Blender only — fails under `blender --background`.

        Args:
            type: SMOOTH, SCALE, INFLATE, SPHERE, RANDOM, RELAX, RELAX_FACE_SETS,
                SURFACE_SMOOTH, SHARPEN, ENHANCE_DETAILS or ERASE_DISPLACEMENT.
            strength: Effect amount; negative values reverse it (INFLATE at -1
                deflates).
            iterations: Repeat count.

        Respects the current mask, so this is how you inflate exactly one region:
        mask everything else, then INFLATE. `strength` above ~1 with several
        iterations can blow the mesh apart — step up gradually and screenshot.
        """
        result = call("sculpt.mesh_filter", clean(
            type=type, strength=strength, iterations=iterations,
            deform_axis=deform_axis, orientation=orientation,
            return_screenshot=return_screenshot, object=object), timeout=180.0)
        return _with_shot(result, ("type", "strength", "iterations", "object"))

    @mcp.tool()
    def multires_set_level(
        sculpt_levels: Optional[int] = None,
        levels: Optional[int] = None,
        render_levels: Optional[int] = None,
        object: Optional[str] = None,
    ) -> dict:
        """Set which Multires subdivision level you sculpt and display at.

        Multires keeps detail on separate levels so you can adjust broad form at
        a low level without destroying fine detail stored higher up — sculpt
        large shapes at level 1-2, fine detail at the top level.

        Args:
            sculpt_levels: Level used while sculpting.
            levels: Viewport display level.
            render_levels: Render level.

        Requires a Multires modifier (`add_multires`) that has been subdivided
        (`multires_subdivide`); setting a level above `total_levels` is refused.
        """
        return call("sculpt.multires_set_level", clean(
            sculpt_levels=sculpt_levels, levels=levels,
            render_levels=render_levels, object=object), timeout=60.0)
