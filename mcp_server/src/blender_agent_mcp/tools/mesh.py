"""Mesh editing tools: selection, topology edits and shading."""

from __future__ import annotations

from typing import Optional

from ..server import call, clean


def register(mcp) -> None:

    # -- inspection ----------------------------------------------------
    @mcp.tool()
    def mesh_stats(name: str, limit: int = 1000) -> dict:
        """Measure a mesh: counts, triangle count, topology problems, bounding box.

        Call this before and after any edit — every editing tool reports its own
        before/after counts, but this is how you check the *quality* of the
        result: whether the mesh is still watertight, whether n-gons crept in,
        whether normals ended up inside out.

        Reads the object's own mesh data with modifiers NOT applied. In Edit Mode
        it reads the live edit-mesh, so the numbers match what the user sees.

        Returns:
            counts (vertices/edges/faces), `triangles` (the render-time triangle
            count, not the number of triangular faces), `faces_by_kind`
            (tris/quads/ngons), `selected` counts per domain, `diagnostics`
            (loose_vertices, wire_edges, boundary_edges, non_manifold_edges,
            non_manifold_vertices, is_watertight, normals_point_inward),
            `surface_area` and `volume_signed` in world-scale-free OBJECT-space
            units, `bounds_local` and `bounds_world` (each min/max/size/center),
            uv_layers, material_slots, shape_keys, and `samples` with the actual
            indices of each problem class so you can feed them straight back into
            `mesh_select_geometry(indices=...)`.

        Gotchas:
            * `volume_signed` is only meaningful when `is_watertight` is true —
              check `volume_reliable`. A negative volume on a watertight mesh
              means the normals point inward; fix with
              `mesh_recalculate_normals`.
            * `bounds_local` is in OBJECT space (ignores the object's transform);
              `bounds_world` accounts for location/rotation/scale.

        Args:
            name: Mesh object name, from `get_scene_info`.
            limit: Cap on each index list in `samples`. `truncated` says whether
                any list was cut short; the counts are always exact.
        """
        return call("mesh.stats", {"name": name, "limit": limit}, timeout=60.0)

    # -- selection -----------------------------------------------------
    @mcp.tool()
    def mesh_select_geometry(
        name: str,
        domain: str = "VERT",
        mode: str = "SET",
        indices: Optional[list[int]] = None,
        box_min: Optional[list[float]] = None,
        box_max: Optional[list[float]] = None,
        normal: Optional[list[float]] = None,
        normal_angle: Optional[float] = None,
        material_index: Optional[int] = None,
        linked_from: Optional[list[int]] = None,
        random_percent: Optional[float] = None,
        random_seed: int = 0,
        select_all: bool = False,
        invert: bool = False,
        space: str = "OBJECT",
        limit: int = 1000,
    ) -> dict:
        """Choose which vertices/edges/faces the next edit will act on.

        This is the gateway tool for the whole mesh domain: `mesh_extrude`,
        `mesh_inset`, `mesh_bevel`, `mesh_subdivide`, `mesh_delete_geometry`,
        `mesh_bridge_edge_loops` and `mesh_proportional_transform` all refuse to
        run without a selection, and they tell you so rather than guessing.

        Works in Object Mode as well as Edit Mode — you do not need to change
        mode first.

        Selecting by geometry beats selecting by index: indices shift every time
        topology changes, so after an extrude or a subdivide the old numbers point
        somewhere else. Prefer `normal`, `box_min`/`box_max` or `linked_from`, and
        re-derive indices from a fresh `mesh_stats` when you do need them.

        Criteria (supply at least one; several are combined with AND):
            indices: Explicit element indices in `domain`. Out-of-range raises.
            box_min / box_max: Axis-aligned box. Either bound may be omitted for
                a half-space. A vertex qualifies on its position, an edge on its
                midpoint, a face on its median center.
            normal: Direction vector [x, y, z] (need not be unit length). Selects
                elements whose normal is within `normal_angle` of it. Edge normals
                are the sum of their adjacent face normals.
            normal_angle: Tolerance in RADIANS, not degrees. Default 0.785 (45°).
                Use ~0.1 to grab exactly one flat face of a box.
            material_index: 0-based material slot. On VERT/EDGE domains this
                selects elements belonging to faces with that slot.
            linked_from: Seed indices; expands to each seed's whole connected
                component. This is Blender's "Select Linked" and is how you grab
                one loose part of a multi-part mesh.
            random_percent: 0-100 (a percentage, not a 0-1 fraction). Deterministic
                for a given `random_seed`.
            select_all: Select every element in `domain`.

        Args:
            name: Mesh object name.
            domain: VERT, EDGE or FACE. Face tools need FACE, edge tools need EDGE.
                Selecting faces also selects their verts and edges (and vice
                versa where appropriate), so you rarely need two calls.
            mode: SET replaces the selection, ADD unions with it, SUBTRACT removes
                the matched elements from it.
            invert: Applied last, after `mode`. `select_all=True, invert=True`
                deselects everything.
            space: OBJECT (default) or WORLD, for `box_min`/`box_max` and
                `normal`. OBJECT ignores the object's transform — that is what you
                want unless the object is moved/rotated/scaled and you are
                reasoning about world coordinates.
            random_seed: Seed for `random_percent`.
            limit: Cap on the returned `indices` list; `matched` is always exact.
        """
        return call(
            "mesh.select_geometry",
            clean(name=name, domain=domain, mode=mode, indices=indices,
                  box_min=box_min, box_max=box_max, normal=normal,
                  normal_angle=normal_angle, material_index=material_index,
                  linked_from=linked_from, random_percent=random_percent,
                  random_seed=random_seed, select_all=select_all, invert=invert,
                  space=space, limit=limit),
            timeout=90.0,
        )

    # -- topology edits ------------------------------------------------
    @mcp.tool()
    def mesh_extrude(
        name: str,
        offset: Optional[list[float]] = None,
        normal_offset: Optional[float] = None,
        space: str = "OBJECT",
    ) -> dict:
        """Extrude the current selection and push the new geometry out.

        Extruding faces adds a cap plus side walls; extruding edges adds a ribbon
        of faces; extruding vertices adds edges. Whatever you had selected is
        replaced by the newly created cap, so consecutive calls walk a shape
        outward without re-selecting.

        Use `offset` for a fixed direction (a chimney straight up). Use
        `normal_offset` for "extrude along normals", where each new vertex moves
        along its own normal — that is what you want on a curved surface, since a
        single `offset` would shear it.

        Args:
            name: Mesh object name.
            offset: [x, y, z] translation in WORLD UNITS applied to the new
                geometry. Interpreted in `space`. Default [0, 0, 0], which
                extrudes in place and leaves zero-area side faces — pass an
                offset or a normal_offset.
            normal_offset: Extra distance in WORLD UNITS along each new vertex's
                own normal. Combines with `offset`. Negative pushes inward.
            space: OBJECT (default) or WORLD for `offset`. OBJECT is the mesh's
                own axes and ignores the object's rotation/scale.

        Requires a selection — call `mesh_select_geometry` first.
        """
        return call(
            "mesh.extrude_selection",
            clean(name=name, offset=offset, normal_offset=normal_offset, space=space),
            timeout=60.0,
        )

    @mcp.tool()
    def mesh_inset(
        name: str,
        thickness: float = 0.1,
        depth: float = 0.0,
        individual: bool = False,
        use_boundary: Optional[bool] = None,
        use_even_offset: Optional[bool] = None,
        use_relative_offset: Optional[bool] = None,
        use_outset: Optional[bool] = None,
    ) -> dict:
        """Inset the selected faces, creating a border ring around them.

        The classic setup for panel lines, button faces and hard-surface detail:
        inset to make a smaller face, then `mesh_extrude` it in or out. The inset
        faces are left selected so that chain works with no extra selection call.

        Args:
            name: Mesh object name.
            thickness: Border width in WORLD UNITS (not a 0-1 fraction), measured
                inward from the original face edge. Too large for the face and the
                inset collapses to a point.
            depth: Displacement of the inset faces along their normal, in WORLD
                UNITS. Positive raises, negative sinks. 0 keeps them flat — the
                usual choice, since you can extrude afterwards for more control.
            individual: False (default) insets the selection as one region, with a
                single border around the whole patch. True insets every face
                separately, giving each its own border — the "grid of panels" look.
            use_boundary: Inset open mesh boundaries too. Default true.
            use_even_offset: Keep the border an even width around corners.
                Default true; turn off only if corners misbehave.
            use_relative_offset: Scale `thickness` by each face's size instead of
                treating it as an absolute distance. Default false.
            use_outset: Grow the border outward instead of inward. Default false.
                Ignored when `individual` is true.

        Requires a face selection.
        """
        return call(
            "mesh.inset",
            clean(name=name, thickness=thickness, depth=depth, individual=individual,
                  use_boundary=use_boundary, use_even_offset=use_even_offset,
                  use_relative_offset=use_relative_offset, use_outset=use_outset),
            timeout=90.0,
        )

    @mcp.tool()
    def mesh_bevel(
        name: str,
        width: float = 0.1,
        segments: int = 1,
        affect: str = "EDGES",
        profile: float = 0.5,
        offset_type: str = "OFFSET",
        clamp_overlap: bool = True,
        loop_slide: bool = True,
        mark_seam: bool = False,
        mark_sharp: bool = False,
        harden_normals: bool = False,
        material: int = -1,
        miter_outer: str = "SHARP",
        miter_inner: str = "SHARP",
        spread: Optional[float] = None,
    ) -> dict:
        """Round off the selected edges (or corners) so they catch light.

        Destructive: this bakes real geometry, unlike a Bevel modifier. Use it
        when you want the bevel welded into the mesh; use a modifier when you
        still want to tweak the width later.

        Face count grows fast — roughly `segments` new faces per beveled edge.
        Check `mesh_stats` afterwards before beveling again.

        Args:
            name: Mesh object name.
            width: Bevel size in WORLD UNITS, interpreted per `offset_type`.
                Typical hard-surface values are small (0.005-0.05).
            segments: Cross-section subdivisions. 1 is a chamfer (a single flat
                cut); 2-4 reads as a rounded edge; above ~6 rarely pays for itself.
            affect: EDGES (default) bevels selected edges. VERTICES bevels
                selected corner vertices instead, which needs a VERT selection.
            profile: Cross-section shape, 0.0-1.0. 0.5 is a circular arc, below
                0.5 is concave, 1.0 is a sharp outward chamfer.
            offset_type: How `width` is measured — OFFSET (distance from the
                original edge, the default), WIDTH (across the new face), DEPTH
                (perpendicular), PERCENT (percent of adjacent edge length),
                ABSOLUTE.
            clamp_overlap: Stop the bevel from eating past neighbouring geometry
                and self-intersecting. Default true; leave it on.
            loop_slide: Prefer sliding along existing edge loops. Default true.
            mark_seam / mark_sharp: Tag the new edges as UV seams / sharp edges.
            harden_normals: Adjust custom normals so the flat faces stay flat.
                Only meaningful on smooth-shaded meshes.
            material: Material slot index for the new faces; -1 (default) inherits
                from the adjacent faces.
            miter_outer / miter_inner: Corner treatment where bevels meet.
                Outer accepts SHARP, PATCH, ARC; inner accepts SHARP, ARC.
            spread: Distance used by ARC miters, in WORLD UNITS. Default 0.1.

        Requires an EDGE selection (or a VERT selection when `affect='VERTICES'`).
        """
        return call(
            "mesh.bevel",
            clean(name=name, width=width, segments=segments, affect=affect,
                  profile=profile, offset_type=offset_type, clamp_overlap=clamp_overlap,
                  loop_slide=loop_slide, mark_seam=mark_seam, mark_sharp=mark_sharp,
                  harden_normals=harden_normals, material=material,
                  miter_outer=miter_outer, miter_inner=miter_inner, spread=spread),
            timeout=180.0,
        )

    @mcp.tool()
    def mesh_subdivide(
        name: str,
        cuts: int = 1,
        smoothness: float = 0.0,
        smooth_falloff: str = "SMOOTH",
        fractal: Optional[float] = None,
        along_normal: Optional[float] = None,
        seed: int = 0,
        quad_corner_type: str = "STRAIGHT_CUT",
        use_grid_fill: bool = True,
    ) -> dict:
        """Add resolution by cutting the selected edges into segments.

        Use this to densify a region before sculpting or deforming it. To add a
        single control loop around a shape instead, use
        `mesh_edge_ring_subdivide` — that is the loop-cut operation, and it keeps
        the mesh all-quads.

        Args:
            name: Mesh object name.
            cuts: New vertices per selected edge. 1 halves each edge, 2 thirds it.
            smoothness: 0.0 (default) keeps the surface exactly where it was;
                1.0 rounds the new geometry outward like a subdivision surface.
                Values above 1 exaggerate the bulge.
            smooth_falloff: Curve used by `smoothness` — SMOOTH, SPHERE, ROOT,
                SHARP, LINEAR, INVERSE_SQUARE.
            fractal: Random displacement of the new vertices, in WORLD UNITS.
                Handy for quick terrain; 0 (default) is off.
            along_normal: 0.0-1.0, biases `fractal` displacement toward each
                vertex's normal instead of a random direction.
            seed: Seed for `fractal`, so results are reproducible.
            quad_corner_type: How a quad with two adjacent cut edges is resolved —
                STRAIGHT_CUT (default), INNER_VERT, PATH, FAN.
            use_grid_fill: Fill fully-cut quads with a clean grid rather than a
                fan. Default true; keeps quads quads.

        Requires an EDGE selection.
        """
        return call(
            "mesh.subdivide",
            clean(name=name, cuts=cuts, smoothness=smoothness,
                  smooth_falloff=smooth_falloff, fractal=fractal,
                  along_normal=along_normal, seed=seed,
                  quad_corner_type=quad_corner_type, use_grid_fill=use_grid_fill),
            timeout=180.0,
        )

    @mcp.tool()
    def mesh_edge_ring_subdivide(
        name: str,
        seed_edges: Optional[list[int]] = None,
        cuts: int = 1,
        smoothness: float = 0.0,
        smooth_falloff: str = "SMOOTH",
        quad_corner_type: str = "STRAIGHT_CUT",
        limit: int = 1000,
    ) -> dict:
        """Loop cut: add edge loops running around the mesh, perpendicular to a seed edge.

        This is Blender's Ctrl+R done headlessly. From each seed edge it walks the
        quad edge *ring* (hopping to the opposite edge of each quad) and subdivides
        that whole ring, which produces a continuous loop crossing them. The
        interactive `mesh.loopcut_slide` operator is modal and cannot run without a
        GUI, so this is the equivalent, not a wrapper around it.

        Use this rather than `mesh_subdivide` when you want a control loop —
        tightening a bevel on a subsurf cage, adding a waistline, adding a bend
        joint — because it keeps the mesh all-quads and adds geometry only where
        needed.

        The new loop edges are left selected, ready for `mesh_proportional_transform`
        or `mesh_bevel`.

        Gotchas:
            * The ring stops at triangles, n-gons, non-manifold junctions and open
              boundaries. On a triangulated mesh the "ring" is just the seed edge,
              and `ring_edges` in the result will say so — check it.
            * The loop runs perpendicular to the seed edge. If the cut came out
              the wrong way round, seed from an edge at 90° to the one you picked.

        Args:
            name: Mesh object name.
            seed_edges: Edge indices to grow rings from. Omit to use the current
                edge selection instead.
            cuts: Number of parallel loops to insert. 1 gives one loop at the
                midpoint, 2 gives two evenly spaced, and so on.
            smoothness: 0.0 (default) puts the loop exactly on the surface; higher
                values round it outward.
            smooth_falloff: Curve for `smoothness` — SMOOTH, SPHERE, ROOT, SHARP,
                LINEAR, INVERSE_SQUARE.
            quad_corner_type: STRAIGHT_CUT (default), INNER_VERT, PATH, FAN.
            limit: Cap on the returned `ring_edge_indices` list.
        """
        return call(
            "mesh.edge_ring_subdivide",
            clean(name=name, seed_edges=seed_edges, cuts=cuts, smoothness=smoothness,
                  smooth_falloff=smooth_falloff, quad_corner_type=quad_corner_type,
                  limit=limit),
            timeout=180.0,
        )

    @mcp.tool()
    def mesh_merge_by_distance(
        name: str,
        threshold: float = 0.0001,
        use_connected: bool = False,
    ) -> dict:
        """Weld vertices that sit closer together than a threshold.

        The standard repair for a mesh that looks solid but behaves as separate
        shells: duplicated vertices along a seam break shading, break subdivision
        and make `mesh_stats` report boundary edges through the middle of the
        surface. Run this, then re-check `is_watertight`.

        Also the cleanup step after `mesh_symmetrize` or a mirrored duplicate,
        where two halves meet at the centre line.

        Args:
            name: Mesh object name.
            threshold: Maximum distance in WORLD UNITS between vertices that get
                merged. Default 0.0001 removes exact duplicates only. Raise it
                cautiously — a value near your smallest real feature will collapse
                genuine detail, and the operation is not reversible except by undo.
            use_connected: Only merge vertices that share an edge. Slower, but it
                cannot weld two surfaces that merely pass close to each other.

        Applies to the selected vertices, or to the whole mesh when nothing is
        selected; the result reports `used_selection` and the removed counts.
        """
        return call(
            "mesh.merge_by_distance",
            clean(name=name, threshold=threshold, use_connected=use_connected),
            timeout=180.0,
        )

    @mcp.tool()
    def mesh_delete_geometry(name: str, domain: str = "VERT") -> dict:
        """Delete the selected geometry, choosing how much goes with it.

        The mode matters more than it looks: deleting faces normally takes their
        vertices and edges too, which is rarely what you want when you are cutting
        a hole to fill later.

        Args:
            name: Mesh object name.
            domain: What to delete, using the current selection of that kind.
                * VERT — delete selected vertices and everything using them.
                * EDGE — delete selected edges and their faces, keeping vertices.
                * FACE — delete selected faces plus vertices/edges left unused.
                * FACE_ONLY — delete only the face polygons, leaving the vertex and
                  edge cage intact. This is the one to use before
                  `mesh_fill_holes` or `mesh_bridge_edge_loops`.
                * EDGE_FACE — delete selected edges and adjacent faces, keep verts.
                * FACE_KEEP_BOUNDARY — delete faces but keep the boundary edges.

        Requires a selection in the matching domain.
        """
        return call("mesh.delete_geometry", {"name": name, "domain": domain}, timeout=60.0)

    @mcp.tool()
    def mesh_fill_holes(name: str, sides: int = 0) -> dict:
        """Cap open boundary loops with new faces.

        Closes a mesh so it becomes watertight — required before boolean
        operations, 3D printing, remeshing or a meaningful volume reading. Check
        `mesh_stats` first: `boundary_edges` tells you whether there is anything
        to fill, and the `samples.boundary_edges` indices tell you where.

        The generated faces are n-gons. If you need clean quads across a large
        opening, `mesh_bridge_edge_loops` between two loops usually gives better
        topology than filling.

        Args:
            name: Mesh object name.
            sides: Maximum number of edges a hole may have to be filled. 0
                (default) means no limit. Set it to something like 4 to plug only
                small pinholes while leaving a large deliberate opening alone.

        Uses the selected edges when there is an edge selection, otherwise every
        boundary edge in the mesh. Returns `filled_faces: 0` with a note when the
        mesh is already closed rather than failing.
        """
        return call("mesh.fill_holes", {"name": name, "sides": sides}, timeout=90.0)

    @mcp.tool()
    def mesh_bridge_edge_loops(
        name: str,
        use_pairs: bool = False,
        use_cyclic: bool = False,
        use_merge: bool = False,
        merge_factor: float = 0.5,
        twist_offset: int = 0,
    ) -> dict:
        """Connect two open edge loops with a tube of faces.

        Use it to join separate parts (a limb to a torso), to close a gap left by
        deleted faces, or to build a tube between two profiles. Gives far better
        topology than `mesh_fill_holes` when the opening has two clear ends.

        Both loops must be in the SAME object and both must be open boundary
        loops — select them together with `mesh_select_geometry(domain='EDGE')`.
        If you need two separate objects joined, merge them into one object first.

        Args:
            name: Mesh object name.
            use_pairs: Bridge loops in matched pairs rather than treating the whole
                selection as one chain. Use when bridging several loop pairs at once.
            use_cyclic: Force the bridge to close into a loop.
            use_merge: Collapse the two loops into one instead of building faces
                between them.
            merge_factor: 0.0-1.0, where along the span the merged result sits.
                Only used when `use_merge` is true. 0.5 is the midpoint.
            twist_offset: Rotate the vertex correspondence by this many steps.
                Fix a bridge that comes out visibly twisted by nudging this ±1.

        Fails with an explanation if the selection does not resolve into loops that
        can be bridged.
        """
        return call(
            "mesh.bridge_edge_loops",
            clean(name=name, use_pairs=use_pairs, use_cyclic=use_cyclic,
                  use_merge=use_merge, merge_factor=merge_factor,
                  twist_offset=twist_offset),
            timeout=90.0,
        )

    @mcp.tool()
    def mesh_symmetrize(name: str, axis: str = "-X", threshold: float = 0.0001) -> dict:
        """Mirror one half of the mesh onto the other, discarding the old half.

        Destructive and immediate, unlike a Mirror modifier. Use it to make an
        asymmetric sculpt symmetric again, or to model one side and copy it over.

        The mirror plane passes through the object's LOCAL ORIGIN, not the world
        origin and not the mesh's bounding-box centre. If the object's origin is
        off to one side, the result will be wrong — move the origin to the
        intended centre line first.

        Args:
            name: Mesh object name.
            axis: Which half is KEPT and copied across.
                '-X' (default) keeps the negative-X half and writes it onto +X;
                '+X' keeps the positive half and writes it onto -X. Same for
                Y and Z. Blender's own spellings NEGATIVE_X / POSITIVE_X are
                accepted too.
            threshold: Distance in WORLD UNITS within which vertices sitting on
                the mirror plane are welded rather than duplicated. Raise it if
                the centre seam ends up doubled; follow with
                `mesh_merge_by_distance` if a seam survives.

        Symmetrizes the selection when there is one, otherwise the whole mesh.
        Since a partial selection is usually a leftover, check `used_selection` in
        the result.
        """
        return call(
            "mesh.symmetrize",
            clean(name=name, axis=axis, threshold=threshold),
            timeout=180.0,
        )

    @mcp.tool()
    def mesh_recalculate_normals(name: str, inside: bool = False) -> dict:
        """Make face winding consistent so the surface faces outward.

        The fix for a mesh that renders with black patches, shades inconsistently,
        or behaves backwards in a boolean. `mesh_stats` flags the condition as
        `normals_point_inward`, or as a negative `volume_signed` on a watertight
        mesh.

        Prefer this over `mesh_flip_normals`: it works out the correct orientation
        per connected shell, so it repairs a mesh where only some faces are wrong.
        `mesh_flip_normals` blindly reverses everything, which just moves the
        problem around on a partially-broken mesh.

        Args:
            name: Mesh object name.
            inside: False (default) points normals outward, the normal case. True
                points them inward — occasionally wanted for interior/skybox
                geometry.

        Needs a closed shell to decide which way is "out"; on an open surface the
        result is consistent but the outward direction is a guess. Operates on the
        selected faces when there is a face selection, otherwise all of them —
        check `used_selection` and the reported before/after signed volume.
        """
        return call("mesh.recalculate_normals", {"name": name, "inside": inside}, timeout=90.0)

    @mcp.tool()
    def mesh_flip_normals(name: str) -> dict:
        """Reverse face winding unconditionally.

        Use only when you know the whole surface (or the whole selection) is
        uniformly backwards. For a mesh with mixed-up normals, use
        `mesh_recalculate_normals` instead — flipping everything on a partly-wrong
        mesh leaves it just as wrong.

        Args:
            name: Mesh object name.

        Flips the selected faces when there is a face selection, otherwise every
        face; the result reports `used_selection`.
        """
        return call("mesh.flip_normals", {"name": name}, timeout=90.0)

    @mcp.tool()
    def mesh_triangulate(
        name: str,
        quad_method: str = "BEAUTY",
        ngon_method: str = "BEAUTY",
    ) -> dict:
        """Convert quads and n-gons into triangles.

        Do this last. Triangles break edge loops, so loop cuts, bevels and
        subdivision all behave badly afterwards — finish modelling, then
        triangulate for export to a game engine or a renderer that needs it.

        Args:
            name: Mesh object name.
            quad_method: How each quad is split — BEAUTY (default, picks the
                better-shaped diagonal), FIXED, FIXED_ALTERNATE,
                SHORTEST_DIAGONAL, LONGEST_DIAGONAL. Blender's internal spellings
                (ALTERNATE, SHORT_EDGE, LONG_EDGE) are accepted as well.
            ngon_method: How n-gons are split — BEAUTY (default, better shaped) or
                CLIP (ear-clipping, faster and more predictable on concave faces).

        Faces that are already triangles are skipped; `converted_faces` reports how
        many were actually changed. Applies to the selected faces when there is a
        selection, otherwise the whole mesh.
        """
        return call(
            "mesh.triangulate",
            clean(name=name, quad_method=quad_method, ngon_method=ngon_method),
            timeout=180.0,
        )

    # -- shading -------------------------------------------------------
    @mcp.tool()
    def mesh_shade_smooth(
        name: str,
        selected_only: bool = False,
        clear_sharp_edges: bool = False,
    ) -> dict:
        """Shade faces smooth, interpolating normals across them.

        Right for organic and curved surfaces. On a hard-surface model it smears
        the edges into mush — use `mesh_shade_auto_smooth` there instead, which
        keeps creases crisp and only smooths the shallow angles.

        Args:
            name: Mesh object name.
            selected_only: False (default) shades the WHOLE object, matching
                Blender's Object ▸ Shade Smooth. True restricts it to the current
                face selection and requires one. The default is deliberate: a
                leftover selection would otherwise silently shade only part of
                the mesh.
            clear_sharp_edges: Also remove every "sharp edge" mark, undoing a
                previous `mesh_shade_auto_smooth`.
        """
        return call(
            "mesh.shade_smooth",
            clean(name=name, selected_only=selected_only,
                  clear_sharp_edges=clear_sharp_edges),
            timeout=60.0,
        )

    @mcp.tool()
    def mesh_shade_flat(
        name: str,
        selected_only: bool = False,
        clear_sharp_edges: bool = False,
    ) -> dict:
        """Shade faces flat, so every face reads as a distinct plane.

        Right for boxy and faceted geometry, and the way to undo an unwanted
        `mesh_shade_smooth`.

        Args:
            name: Mesh object name.
            selected_only: False (default) shades the WHOLE object, matching
                Blender's Object ▸ Shade Flat. True restricts it to the current
                face selection and requires one.
            clear_sharp_edges: Also remove every "sharp edge" mark.
        """
        return call(
            "mesh.shade_flat",
            clean(name=name, selected_only=selected_only,
                  clear_sharp_edges=clear_sharp_edges),
            timeout=60.0,
        )

    @mcp.tool()
    def mesh_shade_auto_smooth(
        name: str,
        angle: float = 0.5235987755982988,
        keep_sharp_edges: bool = True,
        use_modifier: bool = False,
    ) -> dict:
        """Smooth-shade only where faces meet at a shallow angle, keeping creases sharp.

        The right default for almost any hard-surface model: curved regions read as
        curved, corners stay crisp, and you do not have to mark edges by hand.

        The legacy `Mesh.use_auto_smooth` / `auto_smooth_angle` properties were
        REMOVED in Blender 4.1 and do not exist in 5.2. This tool uses the two
        live replacements instead:
          * `use_modifier=False` (default) runs `object.shade_smooth_by_angle`,
            which bakes a `sharp_edge` attribute into the mesh. Destructive, but
            the result travels with the mesh through export and further editing.
          * `use_modifier=True` runs `object.shade_auto_smooth`, which adds a
            non-destructive "Smooth by Angle" geometry-nodes modifier you (or the
            user) can retune later. Note that the modifier must be applied or
            evaluated for the effect to appear in exported data.

        Args:
            name: Mesh object name.
            angle: Threshold in RADIANS, not degrees. Faces meeting at less than
                this angle are smoothed; sharper joins stay faceted. Default
                0.5236 (30°). Common alternatives: 0.5236 (30°), 0.7854 (45°),
                1.0472 (60°). Pass `math.radians(deg)` values, not raw degrees —
                passing 30 would smooth everything.
            keep_sharp_edges: Preserve edges already marked sharp. Default true.
                Only applies when `use_modifier` is false.
            use_modifier: Choose the non-destructive modifier path described above.

        Requires the object to be in Object Mode — call `set_mode(mode='OBJECT')`
        first if it is in Edit Mode.
        """
        return call(
            "mesh.shade_auto_smooth",
            clean(name=name, angle=angle, keep_sharp_edges=keep_sharp_edges,
                  use_modifier=use_modifier),
            timeout=60.0,
        )

    # -- whole-object --------------------------------------------------
    @mcp.tool()
    def mesh_decimate(
        name: str,
        ratio: float = 0.5,
        decimate_type: str = "COLLAPSE",
        use_collapse_triangulate: bool = False,
        iterations: Optional[int] = None,
        angle_limit: Optional[float] = None,
        use_dissolve_boundaries: Optional[bool] = None,
        vertex_group: Optional[str] = None,
        invert_vertex_group: bool = False,
        vertex_group_factor: Optional[float] = None,
        symmetry_axis: Optional[str] = None,
        timeout: float = 300.0,
    ) -> dict:
        """Reduce polygon count in one shot: add a Decimate modifier and apply it.

        Destructive and immediate — there is no modifier left behind to retune, so
        check `mesh_stats` first and pick a ratio deliberately. Ignores the
        selection: this always applies to the whole object.

        Decimation destroys edge loops and UV quality. Do it at the END of a
        workflow, after modelling and UV work, not before.

        Args:
            name: Mesh object name.
            ratio: Target fraction of faces to KEEP, 0.0-1.0 (not a percentage).
                0.5 halves the mesh, 0.1 leaves a tenth. COLLAPSE only. The result
                reports `achieved_face_ratio`, which often exceeds `ratio` because
                collapse works on triangles and the mesh gets triangulated.
            decimate_type:
                * COLLAPSE (default) — ratio-driven edge collapse. Triangulates.
                  Best general-purpose reduction.
                * UNSUBDIV — undoes subdivision levels. Only sensible on a mesh
                  that was actually subdivided; preserves quads.
                * DISSOLVE — merges faces that are nearly coplanar. Best for
                  hard-surface and CAD-like meshes; leaves flat areas as n-gons
                  and does nothing on curved surfaces.
            use_collapse_triangulate: Keep the triangles COLLAPSE produces instead
                of letting Blender rebuild quads.
            iterations: UNSUBDIV only — how many subdivision levels to undo.
                Default 1.
            angle_limit: DISSOLVE only — in RADIANS, not degrees. Faces meeting at
                less than this angle get merged. Default 0.0873 (5°).
            use_dissolve_boundaries: DISSOLVE only — also dissolve open boundary
                edges. Off by default because it can eat silhouette detail.
            vertex_group: COLLAPSE only — restrict decimation to a vertex group,
                so you can keep detail where it matters (a face) and shed it
                elsewhere. Must already exist on the object.
            invert_vertex_group: Decimate everything EXCEPT the group.
            vertex_group_factor: How strongly the group weights bias the
                reduction. Default 1.0.
            symmetry_axis: X, Y or Z — decimate symmetrically about that axis.
                Omit for no symmetry.
            timeout: Seconds to wait. Raise it for very dense meshes.

        Requires Object Mode. On any failure the temporary modifier is removed, so
        the object is never left with a stray Decimate on it.
        """
        return call(
            "mesh.decimate",
            clean(name=name, ratio=ratio, decimate_type=decimate_type,
                  use_collapse_triangulate=use_collapse_triangulate,
                  iterations=iterations, angle_limit=angle_limit,
                  use_dissolve_boundaries=use_dissolve_boundaries,
                  vertex_group=vertex_group, invert_vertex_group=invert_vertex_group,
                  vertex_group_factor=vertex_group_factor, symmetry_axis=symmetry_axis),
            timeout=timeout,
        )

    @mcp.tool()
    def mesh_proportional_transform(
        name: str,
        translate: Optional[list[float]] = None,
        proportional_size: float = 1.0,
        falloff: str = "SMOOTH",
        space: str = "OBJECT",
        seed: int = 0,
    ) -> dict:
        """Move the selected vertices and drag nearby ones along with a soft falloff.

        Blender's proportional editing (the "O" key), for shaping a surface without
        creating a hard crease: pull a few vertices and the neighbourhood follows,
        smoothly fading out. Use it for organic bulges, dents and gentle bends.

        Compare with `mesh_extrude`, which adds geometry and moves it rigidly, and
        with a plain vertex move, which affects only the selection and leaves a
        visible pinch.

        Args:
            name: Mesh object name.
            translate: [x, y, z] displacement in WORLD UNITS applied at full
                strength to the selected vertices. Interpreted in `space`.
            proportional_size: Radius of influence in WORLD UNITS. Unselected
                vertices within this straight-line distance of the nearest
                selected vertex move by a fraction of `translate`; anything
                further away does not move at all. This is a Euclidean distance,
                not a topological one, so a nearby but disconnected surface WILL
                be dragged too — a common surprise on folded geometry.
            falloff: Weight curve, where f goes from 1 at a selected vertex to 0 at
                `proportional_size`:
                  SMOOTH (default, 3f²-2f³ — the usual soft bell),
                  SPHERE (bulges, √(2f-f²)), ROOT (√f, wide and soft),
                  INVERSE_SQUARE (f(2-f)), SHARP (f², tight and pointy),
                  LINEAR (f), CONSTANT (1 — everything in radius moves fully,
                  giving a hard edged shift), RANDOM (scattered, for noise).
            space: OBJECT (default) or WORLD for `translate`.
            seed: Seed for `falloff='RANDOM'`, so the result is reproducible.

        Requires a vertex selection. The result reports `moved_selected` and
        `moved_by_falloff` so you can tell whether the radius actually caught
        anything — if `moved_by_falloff` is 0, raise `proportional_size`.
        """
        return call(
            "mesh.proportional_transform",
            clean(name=name, translate=translate, proportional_size=proportional_size,
                  falloff=falloff, space=space, seed=seed),
            timeout=180.0,
        )
