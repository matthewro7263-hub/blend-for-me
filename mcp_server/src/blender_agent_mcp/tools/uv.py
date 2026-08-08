"""UV tools: seams, unwrapping, packing and diagnostics."""

from __future__ import annotations

from typing import Optional, Union

from ..server import call, clean


def register(mcp) -> None:

    @mcp.tool()
    def mark_seams(
        edges: Union[list[int], str],
        object: Optional[str] = None,
        clear: bool = False,
        angle: Optional[float] = None,
    ) -> dict:
        """Mark (or clear) UV seams — the cuts `unwrap` opens the mesh along.

        Args:
            edges: A list of edge indices, or the string `"SHARP"` to use edges
                already marked sharp (falling back to edges steeper than
                `angle`).
            clear: Remove seams from these edges instead of adding them.
            angle: Threshold in **radians** for the `"SHARP"` mode. Default is
                30° ≈ 0.524.

        Seams decide how the mesh unfolds. Without them `unwrap` produces one
        stretched island. If you do not want to author seams, use
        `smart_uv_project` instead — it decides the cuts for you.
        """
        return call("uv.mark_seams", clean(
            edges=edges, object=object, clear=clear, angle=angle), timeout=60.0)

    @mcp.tool()
    def unwrap(
        object: Optional[str] = None,
        method: str = "ANGLE_BASED",
        margin: Optional[float] = None,
        fill_holes: Optional[bool] = None,
        correct_aspect: Optional[bool] = None,
        margin_method: Optional[str] = None,
    ) -> dict:
        """Unwrap the mesh along its marked seams.

        Args:
            method: ANGLE_BASED (default, best general results), CONFORMAL
                (faster, more distortion) or MINIMUM_STRETCH.
            margin: Space between islands, 0-1.

        Requires seams — `mark_seams` first, or use `smart_uv_project`. The
        result reports the seam count and warns when it is zero, since that is
        the usual cause of a single distorted island.

        Works headless. Sets Edit Mode internally and restores your mode after.
        """
        return call("uv.unwrap", clean(
            object=object, method=method, margin=margin, fill_holes=fill_holes,
            correct_aspect=correct_aspect, margin_method=margin_method), timeout=120.0)

    @mcp.tool()
    def smart_uv_project(
        object: Optional[str] = None,
        angle_limit: Optional[float] = None,
        island_margin: Optional[float] = None,
        area_weight: Optional[float] = None,
        correct_aspect: Optional[bool] = None,
        scale_to_bounds: Optional[bool] = None,
    ) -> dict:
        """Automatic UVs — splits the mesh by angle, no seams needed.

        The right first choice after sculpting or remeshing, when there are no
        seams and you just need usable UVs for baking or texturing.

        Args:
            angle_limit: Split threshold in **radians** (66° ≈ 1.152 is
                Blender's default). Lower values make more, flatter islands.
            island_margin: Gap between islands, 0-1. Leave at least 0.02 when
                baking, or neighbouring islands bleed into each other.
            scale_to_bounds: Expand the layout to fill the whole 0-1 UV space.

        Produces many small islands, which is fine for baking but awkward for
        hand-painting — author seams and `unwrap` for that. Works headless.
        """
        return call("uv.smart_project", clean(
            object=object, angle_limit=angle_limit, island_margin=island_margin,
            area_weight=area_weight, correct_aspect=correct_aspect,
            scale_to_bounds=scale_to_bounds), timeout=180.0)

    @mcp.tool()
    def pack_islands(
        object: Optional[str] = None,
        margin: Optional[float] = None,
        rotate: Optional[bool] = None,
        scale: Optional[bool] = None,
        merge_overlap: Optional[bool] = None,
        shape_method: Optional[str] = None,
        margin_method: Optional[str] = None,
    ) -> dict:
        """Repack existing UV islands to use the 0-1 space efficiently.

        Run after unwrapping to raise texel density.

        Args:
            margin: Gap between islands, 0-1.
            rotate: Allow rotating islands for a tighter fit. Turn off when the
                texture direction matters (wood grain, text).
            shape_method: CONCAVE (tightest, slowest), CONVEX, or AABB (fastest).

        Requires existing UVs; errors clearly when there are none. Works headless.
        """
        return call("uv.pack_islands", clean(
            object=object, margin=margin, rotate=rotate, scale=scale,
            merge_overlap=merge_overlap, shape_method=shape_method,
            margin_method=margin_method), timeout=180.0)

    @mcp.tool()
    def uv_stats(object: Optional[str] = None, uv_layer: Optional[str] = None) -> dict:
        """Diagnose a UV layout before you rely on it.

        Reports island count, UV-space coverage, bounds, how many loops fall
        outside 0-1, and a texel-density hint.

        Read it like this: `coverage_percent` well under 100 means wasted
        texture space (repack); `overlap_likely` true means islands overlap and
        a bake will produce artefacts; `loops_outside_0_1` above zero means
        geometry is tiled outside the main UV square, which is intentional only
        if you are using UDIMs.
        """
        return call("uv.stats", clean(object=object, uv_layer=uv_layer), timeout=60.0)

    @mcp.tool()
    def uv_layer_list(object: Optional[str] = None) -> dict:
        """List an object's UV layers, showing which is active for editing and render.

        `active` and `active_render` can differ — a common cause of a bake going
        to the wrong layer.
        """
        return call("uv.layer_list", clean(object=object))

    @mcp.tool()
    def uv_layer_create(
        name: str = "UVMap", object: Optional[str] = None, active: bool = True,
    ) -> dict:
        """Add a UV layer.

        A second layer is the usual setup for lightmaps or baked AO, keeping the
        original layout for texturing.
        """
        return call("uv.layer_create", clean(name=name, object=object, active=active))

    @mcp.tool()
    def uv_layer_remove(name: str, object: Optional[str] = None) -> dict:
        """Remove a UV layer by name. Cannot be undone by re-adding — UVs are lost."""
        return call("uv.layer_remove", clean(name=name, object=object))

    @mcp.tool()
    def uv_layer_set_active(
        name: str, object: Optional[str] = None, for_render: bool = False,
    ) -> dict:
        """Make a UV layer active for editing, and optionally for rendering.

        Args:
            for_render: Also make it the layer materials and bakes use. Set this
                when switching layers for a bake — the editing-active layer alone
                does not affect rendering.
        """
        return call("uv.layer_set_active", clean(
            name=name, object=object, for_render=for_render))
