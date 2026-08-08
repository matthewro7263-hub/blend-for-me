"""Modifier-stack tools: add, discover, tune, reorder, apply and remove."""

from __future__ import annotations

from typing import Any, Optional

from ..server import call, clean


def register(mcp) -> None:

    # -- generic stack -------------------------------------------------
    @mcp.tool()
    def add_modifier(
        object: str,
        type: str,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add any modifier by its Blender type id, with optional settings in one call.

        Use this for modifier types that have no dedicated tool (BEVEL, ARRAY,
        DECIMATE, WELD, DISPLACE, SIMPLE_DEFORM, NODES, ...). For the common ones
        prefer `add_subsurf` / `add_mirror` / `add_solidify` / `add_boolean` /
        `add_shrinkwrap` / `add_armature` / `add_multires` / `add_remesh` /
        `add_data_transfer` — they name their arguments so you cannot misspell a
        property.

        Args:
            object: Name of the object to add the modifier to. Must be a type that
                accepts modifiers (MESH, CURVE, SURFACE, FONT, LATTICE, ...);
                cameras, lights and empties are rejected with a clear error.
            type: Modifier type id, uppercase, e.g. SUBSURF, MIRROR, BEVEL, ARRAY,
                BOOLEAN, DECIMATE, SOLIDIFY, REMESH, WELD, WIREFRAME, SCREW,
                DISPLACE, CAST, SMOOTH, CORRECTIVE_SMOOTH, SURFACE_DEFORM,
                WEIGHTED_NORMAL, NORMAL_EDIT, UV_PROJECT, NODES (geometry nodes),
                VERTEX_WEIGHT_MIX. A wrong id is rejected with the full valid list.
            name: Modifier name. Omit to let Blender pick its UI default
                ("Subdivision", "Mirror", ...). Blender uniquifies duplicates.
            settings: Property values written straight onto the new modifier,
                e.g. {"levels": 2, "use_limit_surface": false}. Pointer properties
                take a datablock *name* string ({"object": "Cutter"}). Multi-select
                enums take a list. An unknown or read-only key raises an error that
                lists every settable property on that modifier, so a failed call is
                also how you discover the property names.

        The modifier is added to the *end* of the stack. Nothing is baked into the
        mesh — call `apply_modifier` for that. Adding a modifier is undoable.
        """
        return call(
            "modifiers.add",
            clean(object=object, type=type, name=name, settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def list_modifiers(
        object: str,
        include_properties: bool = True,
        limit: int = 1000,
    ) -> dict:
        """Read an object's whole modifier stack, including every tunable property.

        This is the discovery tool: for each modifier it returns every *settable*
        property with its current value, its type, and — for enums — the exact list
        of valid options in this Blender build. Read this before calling
        `set_modifier_prop` rather than guessing a property name, because several
        changed in 4.x/5.x (Mirror has no `use_mirror_x`; it has a 3-element
        `use_axis` vector. Boolean's fast solver is now `FLOAT`, not `FAST`).

        Args:
            object: Object whose stack to read.
            include_properties: Set false for just names/types/indices. The full
                property dump is large for complex modifiers (Ocean, Fluid, Cloth),
                so turn it off when you only need the stack order.
            limit: Cap on modifiers returned; `truncated` says whether it bit.

        `index` is the stack position, 0 = top = evaluated first.
        """
        return call(
            "modifiers.list",
            clean(object=object, include_properties=include_properties, limit=limit),
            timeout=30.0,
        )

    @mcp.tool()
    def set_modifier_prop(
        object: str,
        modifier: str,
        prop: Optional[str] = None,
        value: Any = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Change settings on an existing modifier.

        Args:
            object: Object that owns the modifier.
            modifier: Modifier name, or its stack index as a number ("0" or 0).
            prop: Single property to write, e.g. "levels", "thickness", "use_clip".
            value: The new value. Units follow Blender's own: distances are world
                units (metres by default), angles are RADIANS, factors are 0-1.
                Pointer properties (Boolean.object, Shrinkwrap.target,
                Mirror.mirror_object, Armature.object) take an object *name*
                string, or null to clear. Vector properties take a list of the
                right length (Mirror.use_axis wants 3 booleans). Multi-select
                enums (DataTransfer.data_types_verts) take a list of tokens.
            settings: Write several properties at once instead of prop/value,
                e.g. {"levels": 3, "render_levels": 3}. Applied in order.

        Pass either `prop` + `value`, or `settings`. An unknown property raises an
        error listing every settable property on that modifier — use
        `list_modifiers` first if you would rather look than guess.
        """
        return call(
            "modifiers.set_prop",
            clean(object=object, modifier=modifier, prop=prop, value=value,
                  settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def apply_modifier(
        object: str,
        modifier: str,
        single_user: bool = False,
        timeout: float = 300.0,
    ) -> dict:
        """Bake a modifier permanently into the mesh and remove it from the stack.

        Destructive: the evaluated result replaces the object's data. Prefer
        leaving modifiers live; apply only when you need the real geometry (before
        sculpting, exporting, or booleans that must see the subdivided surface).
        This can be slow on dense meshes, hence the long default timeout.

        The tool handles the two context requirements for you: it makes the object
        active and leaves Edit/Sculpt mode first (the previous mode is reported and
        is *not* restored).

        Args:
            object: Object that owns the modifier.
            modifier: Modifier name, or stack index as a number.
            single_user: When the object's mesh data is shared with other objects,
                Blender refuses to apply. Set true to give this object its own copy
                first — the other objects keep the unmodified data.
            timeout: Seconds to wait. Raise it for heavy Subsurf/Remesh/Boolean
                stacks on dense meshes.

        Errors you should expect rather than retry blindly:
        * shape keys — Blender will not apply any modifier to a mesh that has them;
          delete the shape keys or keep the modifier live.
        * linked library data — make the object local first.
        * multi-user data — pass `single_user=true`.

        Applying a modifier that is not at index 0 evaluates it as if it were
        first; the response says so in `note` when that happens.
        """
        return call(
            "modifiers.apply",
            clean(object=object, modifier=modifier, single_user=single_user),
            timeout=timeout,
        )

    @mcp.tool()
    def remove_modifier(object: str, modifier: str) -> dict:
        """Delete a modifier, discarding its effect (the opposite of `apply_modifier`).

        Args:
            object: Object that owns the modifier.
            modifier: Modifier name, or stack index as a number.
        """
        return call("modifiers.remove", {"object": object, "modifier": modifier})

    @mcp.tool()
    def reorder_modifier(object: str, modifier: str, index: int) -> dict:
        """Move a modifier to a different position in the stack.

        Stack order is evaluation order: index 0 runs first and feeds the next.
        This matters — Mirror before Subsurf gives a clean seam, Subsurf before
        Mirror does not.

        Args:
            object: Object that owns the modifier.
            modifier: Modifier name, or its current stack index as a number.
            index: Target position, 0-based. Negative counts from the end, so -1
                moves it to the bottom of the stack.

        Blender enforces stack rules and this surfaces the refusal verbatim:
        Multires must stay first ("Cannot move above a modifier requiring original
        data"), and a deforming modifier cannot cross a non-deforming one.
        """
        return call(
            "modifiers.reorder",
            {"object": object, "modifier": modifier, "index": index},
        )

    # -- conveniences --------------------------------------------------
    @mcp.tool()
    def add_subsurf(
        object: str,
        levels: Optional[int] = None,
        render_levels: Optional[int] = None,
        use_limit_surface: Optional[bool] = None,
        subdivision_type: Optional[str] = None,
        quality: Optional[int] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Subdivision Surface modifier — the standard way to smooth a mesh.

        Args:
            object: Mesh object to subdivide.
            levels: Viewport subdivision levels (Blender default 1). Each level
                quadruples the face count, so 3 on a 10k-face mesh is 640k faces —
                keep it at 1-2 while working.
            render_levels: Subdivision levels at render time (Blender default 2).
            use_limit_surface: True (Blender's default) evaluates the exact limit
                surface, so viewport and render agree at low levels. Set false for
                the older, faster approximation.
            subdivision_type: CATMULL_CLARK (default, smooths) or SIMPLE (just
                splits faces, keeps the silhouette).
            quality: Accuracy of the subdivision solver, 1-10, default 3.
            name: Modifier name; omit for Blender's default "Subdivision".
            settings: Any other SubsurfModifier property, e.g.
                {"use_creases": true, "uv_smooth": "PRESERVE_BOUNDARIES"}.

        For sculptable multi-level detail use `add_multires` instead — Subsurf
        smooths but stores no per-level displacement.
        """
        return call(
            "modifiers.add_subsurf",
            clean(object=object, levels=levels, render_levels=render_levels,
                  use_limit_surface=use_limit_surface,
                  subdivision_type=subdivision_type, quality=quality,
                  name=name, settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def add_mirror(
        object: str,
        axis: Optional[list[str]] = None,
        use_clip: Optional[bool] = None,
        use_mirror_merge: Optional[bool] = None,
        merge_threshold: Optional[float] = None,
        mirror_object: Optional[str] = None,
        bisect_axis: Optional[list[str]] = None,
        flip_axis: Optional[list[str]] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Mirror modifier — symmetrical modelling from one half.

        Args:
            object: Object to mirror.
            axis: Axes to mirror across, e.g. ["X"] (default) or ["X", "Z"].
                Accepts "X"/"Y"/"Z" or 0/1/2. These are the object's *local* axes,
                and mirroring happens about its origin — if the origin is not on
                the seam, move it first.
            use_clip: Stop vertices crossing the mirror plane while editing.
            use_mirror_merge: Weld vertices that land on the mirror plane
                (Blender's default is on).
            merge_threshold: Merge distance in world units, default 0.001.
            mirror_object: Name of an object to mirror about instead of this
                object's own origin — its transform defines the mirror plane.
            bisect_axis: Axes on which to cut away the far half before mirroring,
                same format as `axis`. Use this when the source geometry crosses
                the plane.
            flip_axis: Axes whose bisect keeps the other side instead. Only
                meaningful together with `bisect_axis`.
            name: Modifier name; omit for Blender's default "Mirror".
            settings: Any other MirrorModifier property, e.g.
                {"use_mirror_u": true, "mirror_offset_u": 0.5}.

        In Blender 5.2 there is no `use_mirror_x`/`use_mirror_y`/`use_mirror_z`;
        the axes live in the 3-boolean vectors `use_axis`, `use_bisect_axis` and
        `use_bisect_flip_axis`. This tool writes those for you.
        """
        return call(
            "modifiers.add_mirror",
            clean(object=object, axis=axis, use_clip=use_clip,
                  use_mirror_merge=use_mirror_merge, merge_threshold=merge_threshold,
                  mirror_object=mirror_object, bisect_axis=bisect_axis,
                  flip_axis=flip_axis, name=name, settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def add_solidify(
        object: str,
        thickness: Optional[float] = None,
        offset: Optional[float] = None,
        even_thickness: Optional[bool] = None,
        use_rim: Optional[bool] = None,
        solidify_mode: Optional[str] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Solidify modifier — give a flat surface real thickness.

        Args:
            object: Object to thicken.
            thickness: Shell thickness in world units (Blender default 0.01, i.e.
                1 cm at the default metric scale). Negative values push the shell
                the other way.
            offset: Where the original surface sits inside the new shell, -1..1.
                -1 (Blender's default) keeps the original as the outer surface,
                0 centres it, 1 makes it the inner surface.
            even_thickness: Keep thickness even across corners rather than letting
                it thin out at sharp angles. This writes `use_even_offset`, which
                is the actual property name.
            use_rim: Fill the open boundary edges so the result is closed
                (Blender's default is on).
            solidify_mode: EXTRUDE (default, fast) or NON_MANIFOLD (slower, copes
                with self-intersecting and non-manifold input).
            name: Modifier name; omit for Blender's default "Solidify".
            settings: Any other SolidifyModifier property, e.g.
                {"use_flip_normals": true, "bevel_convex": 0.3}.
        """
        return call(
            "modifiers.add_solidify",
            clean(object=object, thickness=thickness, offset=offset,
                  even_thickness=even_thickness, use_rim=use_rim,
                  solidify_mode=solidify_mode, name=name, settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def add_boolean(
        object: str,
        target: str,
        operation: str = "DIFFERENCE",
        solver: Optional[str] = None,
        use_self: Optional[bool] = None,
        use_hole_tolerant: Optional[bool] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Boolean modifier — cut, fuse or intersect with another mesh.

        Args:
            object: The object that receives the modifier and gets changed.
            target: Name of the other mesh object (the cutter). It must be a MESH.
                It is *not* deleted or hidden — hide it yourself once the result
                looks right, or the cutter will still render.
            operation: DIFFERENCE (default — subtract the target), UNION (fuse),
                or INTERSECT (keep only the overlap).
            solver: FLOAT, EXACT (Blender's default) or MANIFOLD. EXACT is robust
                and slower; FLOAT is the fast approximate solver — it was called
                `FAST` before Blender 5.x, and passing "FAST" is accepted and
                translated. MANIFOLD is the fastest but requires watertight,
                non-self-intersecting input on both meshes.
            use_self: Let the EXACT solver handle self-intersecting input. Slower.
            use_hole_tolerant: Let the EXACT solver cope with small holes in
                otherwise closed meshes. Slower.
            name: Modifier name; omit for Blender's default "Boolean".
            settings: Any other BooleanModifier property, e.g.
                {"double_threshold": 1e-05, "material_mode": "TRANSFER"}.

        Booleans need clean input. If the result is a mess, the usual causes are
        coplanar faces, flipped normals, or a non-closed cutter — nudge the cutter
        so faces are not exactly coincident, or switch solver to EXACT.
        """
        return call(
            "modifiers.add_boolean",
            clean(object=object, target=target, operation=operation, solver=solver,
                  use_self=use_self, use_hole_tolerant=use_hole_tolerant,
                  name=name, settings=settings),
            timeout=60.0,
        )

    @mcp.tool()
    def add_shrinkwrap(
        object: str,
        target: str,
        wrap_method: Optional[str] = None,
        offset: Optional[float] = None,
        wrap_mode: Optional[str] = None,
        vertex_group: Optional[str] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Shrinkwrap modifier — pull a mesh onto the surface of another.

        Typical uses: conforming clothing to a body, retopology, projecting a flat
        grid onto a sculpt.

        Args:
            object: Object to be pulled onto the target.
            target: Name of the surface object to wrap onto.
            wrap_method: NEAREST_SURFACEPOINT (Blender's default — closest point
                anywhere on the target's surface), PROJECT (ray-cast along the
                axes set by `use_project_x/y/z` in `settings`), NEAREST_VERTEX
                (snap to the closest target vertex, so the result inherits the
                target's vertex density), or TARGET_PROJECT (project along the
                object's own normals).
            offset: Distance to keep off the target surface, in world units.
                Positive floats the mesh outward along the surface normal.
            wrap_mode: ON_SURFACE (default), INSIDE, OUTSIDE, OUTSIDE_SURFACE or
                ABOVE_SURFACE — how `offset` is interpreted relative to the target.
            vertex_group: Name of a vertex group limiting the effect; weights act
                as per-vertex strength.
            name: Modifier name; omit for Blender's default "Shrinkwrap".
            settings: Any other ShrinkwrapModifier property, e.g.
                {"use_project_z": true, "use_negative_direction": true,
                 "project_limit": 0.1}. PROJECT does nothing until at least one of
                `use_project_x/y/z` is on and a direction is enabled.
        """
        return call(
            "modifiers.add_shrinkwrap",
            clean(object=object, target=target, wrap_method=wrap_method,
                  offset=offset, wrap_mode=wrap_mode, vertex_group=vertex_group,
                  name=name, settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def add_armature(
        object: str,
        armature: str,
        use_deform_preserve_volume: Optional[bool] = None,
        use_vertex_groups: Optional[bool] = None,
        use_bone_envelopes: Optional[bool] = None,
        vertex_group: Optional[str] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add an Armature modifier — bind a mesh to a skeleton for posing.

        This only creates the binding. It does not create vertex groups or weights:
        without them the mesh will not move. Either the mesh already has vertex
        groups named after the bones, or you parent with automatic weights /
        transfer weights separately.

        Args:
            object: Mesh object to deform.
            armature: Name of the ARMATURE object to deform it with.
            use_deform_preserve_volume: Use dual-quaternion skinning, which stops
                the candy-wrapper collapse at twisted joints (shoulders, wrists).
                Off by default in Blender; usually worth turning on for characters.
            use_vertex_groups: Deform from vertex groups matching bone names
                (Blender's default is on).
            use_bone_envelopes: Deform from each bone's envelope volume instead of
                weights. Off by default; useful only for quick blocking.
            vertex_group: Name of a vertex group masking the whole modifier's
                influence (not the per-bone weights).
            name: Modifier name; omit for Blender's default "Armature".
            settings: Any other ArmatureModifier property, e.g.
                {"use_multi_modifier": true, "invert_vertex_group": true}.
        """
        return call(
            "modifiers.add_armature",
            clean(object=object, armature=armature,
                  use_deform_preserve_volume=use_deform_preserve_volume,
                  use_vertex_groups=use_vertex_groups,
                  use_bone_envelopes=use_bone_envelopes,
                  vertex_group=vertex_group, name=name, settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def add_multires(
        object: str,
        render_levels: Optional[int] = None,
        quality: Optional[int] = None,
        use_creases: Optional[bool] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Multiresolution modifier — the base for multi-level sculpting.

        A fresh Multires has zero levels and changes nothing. Call
        `multires_subdivide` to build levels, then sculpt: detail is stored per
        level, so you can drop back to a coarse level, change the big forms, and
        the fine detail rides along.

        Args:
            object: Mesh object to add it to.
            render_levels: Level used at render time. Blender starts at 0 and
                raises this itself as you subdivide.
            quality: Accuracy of the subdivision solver, 1-10, default 3.
            use_creases: Respect edge crease weights.
            name: Modifier name; omit for Blender's default "Multires".
            settings: Any other MultiresModifier property, e.g.
                {"use_sculpt_base_mesh": true, "uv_smooth": "PRESERVE_BOUNDARIES"}.

        Multires must be the first modifier in the stack and Blender will refuse to
        move anything above it. Use `add_subsurf` instead if you only want smooth
        render geometry with no sculpted detail.
        """
        return call(
            "modifiers.add_multires",
            clean(object=object, render_levels=render_levels, quality=quality,
                  use_creases=use_creases, name=name, settings=settings),
            timeout=30.0,
        )

    @mcp.tool()
    def multires_subdivide(
        object: str,
        levels: int = 1,
        mode: str = "CATMULL_CLARK",
        modifier: Optional[str] = None,
        timeout: float = 300.0,
    ) -> dict:
        """Add subdivision levels to a Multires modifier.

        Each level quadruples the vertex count and the memory it costs, so go one
        level at a time and check the result. Level 6 on a 5k-face base is roughly
        20 million faces — enough to stall Blender.

        Args:
            object: Mesh object carrying the Multires modifier.
            levels: How many levels to add in this call (default 1). Applied one
                at a time; if Blender stops early the response says at which step.
            mode: CATMULL_CLARK (default — smooths the surface as it subdivides),
                SIMPLE (splits faces without smoothing, so the silhouette is
                unchanged), or LINEAR (subdivide the displaced surface itself,
                preserving the current sculpted shape exactly).
            modifier: Name of the Multires modifier. Omit to use the object's only
                one.
            timeout: Seconds to wait. Raise it for high levels on dense meshes.

        Returns the new `levels` / `sculpt_levels` / `render_levels` /
        `total_levels` and the resulting vertex count.
        """
        return call(
            "modifiers.multires_subdivide",
            clean(object=object, levels=levels, mode=mode, modifier=modifier),
            timeout=timeout,
        )

    @mcp.tool()
    def multires_unsubdivide(
        object: str,
        modifier: Optional[str] = None,
        timeout: float = 300.0,
    ) -> dict:
        """Rebuild a *lower* Multires level by reversing one subdivision.

        Use this when a mesh was subdivided destructively and you want a coarse
        base back underneath the detail. It only works when the current topology
        really is a subdivision of a coarser mesh; otherwise Blender reports
        "No valid subdivisions found to rebuild a lower level" and nothing changes.

        This is not the inverse of `multires_subdivide` on a mesh you just
        subdivided — to drop levels you added, lower `levels` with
        `set_modifier_prop` instead.

        Args:
            object: Mesh object carrying the Multires modifier.
            modifier: Name of the Multires modifier. Omit to use the object's only
                one.
            timeout: Seconds to wait.
        """
        return call(
            "modifiers.multires_unsubdivide",
            clean(object=object, modifier=modifier),
            timeout=timeout,
        )

    @mcp.tool()
    def multires_apply_base(
        object: str,
        modifier: Optional[str] = None,
        timeout: float = 300.0,
    ) -> dict:
        """Reshape the Multires base mesh to match the sculpted result.

        Moves the level-0 cage so it follows the sculpt, which keeps the base mesh
        useful for retopology and stops the displacement from being extreme. The
        sculpted levels are kept; only the base cage moves.

        Args:
            object: Mesh object carrying the Multires modifier.
            modifier: Name of the Multires modifier. Omit to use the object's only
                one.
            timeout: Seconds to wait.

        Note: the underlying Blender 5.2 operator is `object.multires_base_apply`
        (words in that order); `object.multires_apply_base` does not exist.
        """
        return call(
            "modifiers.multires_apply_base",
            clean(object=object, modifier=modifier),
            timeout=timeout,
        )

    @mcp.tool()
    def add_remesh(
        object: str,
        mode: str = "VOXEL",
        voxel_size: Optional[float] = None,
        octree_depth: Optional[int] = None,
        adaptivity: Optional[float] = None,
        use_smooth_shade: Optional[bool] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Remesh modifier — rebuild the topology as an even, uniform mesh.

        Non-destructive: the original geometry stays until you `apply_modifier`.
        Remesh throws away UVs, vertex groups and creases, so remesh first and
        unwrap/weight afterwards.

        Args:
            object: Mesh object to remesh.
            mode: VOXEL (default — the modern voxel remesher, driven by
                `voxel_size`), or the older octree modes BLOCKS (hard cubes),
                SMOOTH and SHARP, all driven by `octree_depth` and `scale`.
            voxel_size: VOXEL mode only. Edge length of one voxel in world units,
                default 0.1. Smaller means finer and much heavier: halving it
                roughly octuples the memory. On a 2 m character, 0.02 is a
                reasonable sculpting density.
            octree_depth: BLOCKS/SMOOTH/SHARP only. Resolution as a power of two,
                default 4. Each +1 doubles resolution per axis.
            adaptivity: VOXEL mode only, 0-1, default 0. Above 0 it simplifies flat
                regions, giving fewer faces at the cost of a uniform grid.
            use_smooth_shade: Output smooth-shaded faces instead of flat.
            name: Modifier name; omit for Blender's default "Remesh".
            settings: Any other RemeshModifier property, e.g.
                {"sharpness": 1.0, "use_remove_disconnected": true}.

        For a one-shot destructive voxel remesh of the mesh data itself (the Sculpt
        mode workflow, which keeps sculpt detail via `mesh.remesh_voxel_size`), use
        the sculpt-domain remesh tool instead.
        """
        return call(
            "modifiers.add_remesh",
            clean(object=object, mode=mode, voxel_size=voxel_size,
                  octree_depth=octree_depth, adaptivity=adaptivity,
                  use_smooth_shade=use_smooth_shade, name=name, settings=settings),
            timeout=120.0,
        )

    @mcp.tool()
    def add_data_transfer(
        object: str,
        source: str,
        data_types: Optional[list[str]] = None,
        vert_mapping: Optional[str] = None,
        loop_mapping: Optional[str] = None,
        edge_mapping: Optional[str] = None,
        poly_mapping: Optional[str] = None,
        mix_mode: Optional[str] = None,
        mix_factor: Optional[float] = None,
        max_distance: Optional[float] = None,
        vertex_group: Optional[str] = None,
        name: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """Add a Data Transfer modifier — copy weights, colours, UVs or normals across meshes.

        The usual reason to reach for this is transferring vertex-group weights
        from a rigged mesh onto a new one (`object.vertex_group_transfer_weight`
        was removed; this and `object.data_transfer` are the 5.x replacements).

        Args:
            object: The destination object, which receives the modifier and the data.
            source: Name of the MESH object to read the data from.
            data_types: Flat list of what to copy. Blender 5.2 stores these in four
                separate per-domain flag enums, and this tool routes each token to
                the right one and enables that domain automatically. Valid tokens:
                vertex domain — VGROUP_WEIGHTS, BEVEL_WEIGHT_VERT, COLOR_VERTEX;
                edge domain — SHARP_EDGE, SEAM, CREASE, BEVEL_WEIGHT_EDGE,
                FREESTYLE_EDGE; face-corner domain — CUSTOM_NORMAL, COLOR_CORNER,
                UV; face domain — SMOOTH, FREESTYLE_FACE. There is no single
                `data_types` property on the modifier itself.
            vert_mapping: How destination vertices find source data. TOPOLOGY (only
                valid when both meshes have identical topology), NEAREST (default,
                closest vertex), EDGE_NEAREST, EDGEINTERP_NEAREST, POLY_NEAREST,
                POLYINTERP_NEAREST (interpolated across the nearest face — the best
                choice for weights on differing topology), POLYINTERP_VNORPROJ.
            loop_mapping: Mapping for corner data (UV, CUSTOM_NORMAL, COLOR_CORNER):
                TOPOLOGY, NEAREST_NORMAL, NEAREST_POLYNOR, NEAREST_POLY,
                POLYINTERP_NEAREST, POLYINTERP_LNORPROJ.
            edge_mapping: Mapping for edge data: TOPOLOGY, VERT_NEAREST, NEAREST,
                POLY_NEAREST, EDGEINTERP_VNORPROJ.
            poly_mapping: Mapping for face data: TOPOLOGY, NEAREST, NORMAL,
                POLYINTERP_PNORPROJ.
            mix_mode: How incoming data combines with what is already there:
                REPLACE (default), ABOVE_THRESHOLD, BELOW_THRESHOLD, MIX, ADD, SUB,
                MUL.
            mix_factor: Blend strength 0-1, default 1 (full replacement).
            max_distance: Ignore source geometry further away than this, in world
                units. Setting it also switches on the `use_max_distance` gate,
                which is otherwise off and would make the value inert.
            vertex_group: Vertex group on the destination limiting where data lands.
            name: Modifier name; omit for Blender's default "DataTransfer".
            settings: Any other DataTransferModifier property, e.g.
                {"layers_vgroup_select_src": "ALL", "ray_radius": 0.01,
                 "use_object_transform": false}.

        The modifier is live: the transferred data only becomes real vertex-group
        weights (or UVs, or colours) once you `apply_modifier`.
        """
        return call(
            "modifiers.add_data_transfer",
            clean(object=object, source=source, data_types=data_types,
                  vert_mapping=vert_mapping, loop_mapping=loop_mapping,
                  edge_mapping=edge_mapping, poly_mapping=poly_mapping,
                  mix_mode=mix_mode, mix_factor=mix_factor,
                  max_distance=max_distance, vertex_group=vertex_group,
                  name=name, settings=settings),
            timeout=60.0,
        )
