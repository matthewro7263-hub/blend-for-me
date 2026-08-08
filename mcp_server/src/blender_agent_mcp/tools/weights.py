"""Vertex-group and weight-painting tools."""

from __future__ import annotations

from typing import Any, Optional, Union

from ..server import call, clean, png_image

VertsSpec = Union[str, list[int], dict]


def register(mcp) -> None:

    # -- mode ----------------------------------------------------------
    @mcp.tool()
    def enter_weight_paint(mesh: str, armature_for_posing: Optional[str] = None) -> dict:
        """Put a mesh into Weight Paint mode, optionally with its rig posed for bone picking.

        Almost nothing in this module actually needs Weight Paint mode — the
        assign/set/get tools write `vertex_groups` data directly from any mode and
        are the ones you should reach for. Use this when a human will take over in
        the viewport, or before `weight_gradient` / `brush_stroke`.

        Args:
            mesh: Mesh object name.
            armature_for_posing: Armature to put into Pose mode and leave selected,
                so bones can be ctrl-clicked to activate their vertex group. Order
                matters and this tool handles it: the rig enters Pose mode first,
                then the mesh becomes active and enters Weight Paint.

        Leaving Weight Paint mode drops the armature back to Object mode.
        """
        return call("weights.enter_weight_paint",
                    clean(mesh=mesh, armature_for_posing=armature_for_posing))

    # -- group bookkeeping ---------------------------------------------
    @mcp.tool()
    def vgroup_list(mesh: str, armature: Optional[str] = None) -> dict:
        """List every vertex group with its index, lock state and assigned-vertex count.

        Start here before any weight edit — group names are case-sensitive and
        must match deform bone names exactly for the armature modifier to use
        them, so guessing is expensive.

        Args:
            mesh: Mesh object name.
            armature: Armature to cross-check against. When given (or derivable
                from the mesh's armature modifier) each group reports
                `is_deform_bone`, which is how you spot a typo'd group name that
                silently deforms nothing.

        `total_weight` is the sum over all vertices, useful for spotting a group
        that exists but holds nothing.
        """
        return call("weights.vgroup_list", clean(mesh=mesh, armature=armature))

    @mcp.tool()
    def vgroup_create(mesh: str, name: str) -> dict:
        """Create an empty vertex group and make it active.

        Idempotent: if a group of that name already exists it is activated and
        returned with `created: false` rather than duplicated. Blender uniquifies
        genuinely new duplicates, so always read the returned `name` rather than
        assuming you got the one you asked for.
        """
        return call("weights.vgroup_create", {"mesh": mesh, "name": name})

    @mcp.tool()
    def vgroup_delete(mesh: str, name: Optional[str] = None, all: bool = False) -> dict:
        """Delete one vertex group, or every group on the mesh.

        Args:
            mesh: Mesh object name.
            name: Group to delete. Required unless `all` is true.
            all: Delete every vertex group. Destructive — an armature modifier
                will stop deforming the mesh entirely.

        Deleting a group shifts the indices of every group after it, so re-read
        `vgroup_list` afterwards instead of caching indices.
        """
        return call("weights.vgroup_delete", clean(mesh=mesh, name=name, all=all))

    @mcp.tool()
    def vgroup_rename(mesh: str, name: str, new_name: str) -> dict:
        """Rename a vertex group.

        This is how you re-bind weights to a different bone: an armature modifier
        matches groups to bones purely by name, so renaming `Bone.001` to
        `UpperArm.L` immediately makes those weights drive `UpperArm.L`.
        """
        return call("weights.vgroup_rename",
                    {"mesh": mesh, "name": name, "new_name": new_name})

    @mcp.tool()
    def vgroup_lock(
        mesh: str,
        name: Optional[Union[str, list[str]]] = None,
        locked: bool = True,
    ) -> dict:
        """Lock or unlock groups so normalize and auto-normalize cannot rewrite them.

        Locking is the standard way to protect hand-tuned weights while
        `normalize_all` redistributes everything else.

        Args:
            mesh: Mesh object name.
            name: One group name or a list of them. Omit to affect every group —
                note that locking *all* groups makes `normalize_all` fail with
                "All groups are locked".
            locked: True to lock, False to unlock.
        """
        return call("weights.vgroup_lock", clean(mesh=mesh, name=name, locked=locked))

    # -- reading / writing ---------------------------------------------
    @mcp.tool()
    def assign_weights(
        mesh: str,
        group: str,
        verts_spec: VertsSpec = "ALL",
        weight: float = 1.0,
        mode: str = "REPLACE",
    ) -> dict:
        """Write one weight value to a set of vertices. The workhorse write tool.

        Pure data API — no brush, no viewport, works under `--background`. Use
        this whenever every target vertex should get the *same* weight; use
        `set_weights` when each vertex needs its own value.

        Args:
            mesh: Mesh object name.
            group: Vertex group name. Must already exist (`vgroup_create` first).
            verts_spec: Which vertices. One of:
                * `"ALL"` — every vertex.
                * `"SELECTED"` — the mesh's current vertex selection (set it with
                  `select_verts_by_weight`, or in the viewport).
                * a list of integer vertex indices, e.g. `[0, 5, 12]`.
                * a bounding box `{"min": [x,y,z], "max": [x,y,z], "space": "LOCAL"}`
                  where `space` is `LOCAL` (object space, the default) or `WORLD`.
                  Units are Blender units, not pixels.
            weight: Weight value, 0.0-1.0 (not 0-100). Values outside that range
                are clamped by Blender.
            mode: `REPLACE` overwrites, `ADD` adds to the existing weight,
                `SUBTRACT` subtracts. ADD/SUBTRACT clamp into 0..1, and SUBTRACT
                down to 0 still leaves the vertex *assigned* with weight 0 — use
                `set_weights(remove_zero=true)` or `clean` to truly unassign it.
        """
        return call("weights.assign_weights",
                    clean(mesh=mesh, group=group, verts_spec=verts_spec,
                          weight=weight, mode=mode))

    @mcp.tool()
    def set_weights(
        mesh: str,
        group: str,
        weights: dict,
        remove_zero: bool = False,
    ) -> dict:
        """Bulk-write an explicit vertex-index to weight map into one group.

        Use this for computed falloffs and gradients you worked out yourself —
        it is exact, reproducible and needs no viewport, unlike `weight_gradient`.

        Args:
            mesh: Mesh object name.
            group: Vertex group name; must exist.
            weights: `{"<vertex_index>": weight}` — keys are vertex indices
                (strings or ints both work), values are 0.0-1.0.
            remove_zero: When true, a weight of 0 or less *unassigns* the vertex
                from the group instead of storing a zero. That is the difference
                between "this bone has no influence here" and "this bone has an
                influence of exactly nothing", which matters for `limit_total`
                and for game exporters.

        Out-of-range indices raise rather than being silently skipped. Keep
        batches to a few thousand entries per call; split larger writes.
        """
        return call("weights.set_weights",
                    clean(mesh=mesh, group=group, weights=weights,
                          remove_zero=remove_zero),
                    timeout=60.0)

    @mcp.tool()
    def get_weights(
        mesh: str,
        group: str,
        offset: int = 0,
        limit: int = 1000,
        include_zero: bool = False,
    ) -> dict:
        """Read a page of weights from one group. Chunked — safe on dense meshes.

        Args:
            mesh: Mesh object name.
            group: Vertex group name.
            offset: Vertex index to start scanning from. Pass back the
                `next_offset` from the previous call to continue.
            limit: Maximum entries to return (default 1000). This caps *entries*,
                not vertices scanned.
            include_zero: Also return vertices that are not in the group, as
                `weight: 0.0, assigned: false`. Off by default because on a
                sparse group it is almost all noise.

        Returns `total_vertices`, `returned`, `truncated` and `next_offset`
        (null when the scan reached the end). For a whole-mesh overview prefer
        `per_bone_weight_summary` — it is one small call instead of many pages.
        """
        return call("weights.get_weights",
                    clean(mesh=mesh, group=group, offset=offset, limit=limit,
                          include_zero=include_zero))

    @mcp.tool()
    def select_verts_by_weight(
        mesh: str,
        group: str,
        min: float = 0.0,
        max: float = 1.0,
        include_unassigned: bool = False,
        extend: bool = False,
        limit: int = 1000,
    ) -> dict:
        """Select vertices whose weight in a group falls inside a range.

        The usual way to build a `verts_spec: "SELECTED"` for `assign_weights` —
        e.g. select everything between 0.01 and 0.2 and flatten it to zero.

        Args:
            mesh: Mesh object name.
            group: Vertex group name.
            min: Lower bound, inclusive. 0.0-1.0.
            max: Upper bound, inclusive. 0.0-1.0.
            include_unassigned: Treat vertices that are not in the group as
                weight 0 and select them when 0 falls inside the range. Off by
                default, so an unassigned vertex is never matched by accident.
            extend: Add to the existing selection instead of replacing it.
            limit: Cap on the vertex list returned; `selected` is always the true
                count.

        Selection is written to the mesh data. The tool briefly drops the object
        to Object mode to write it and restores the mode afterwards, so it is
        safe to call while the mesh is in Edit or Weight Paint mode.
        """
        return call("weights.select_verts_by_weight",
                    clean(mesh=mesh, group=group, min=min, max=max,
                          include_unassigned=include_unassigned, extend=extend,
                          limit=limit))

    # -- binding -------------------------------------------------------
    @mcp.tool()
    def auto_weights(
        mesh: str,
        armature: str,
        method: str = "AUTOMATIC",
        reuse_binding: bool = False,
        keep_transform: bool = False,
        xmirror: bool = False,
        timeout: float = 180.0,
    ) -> dict:
        """Bind a mesh to an armature and generate its weights. Start of every rig.

        Args:
            mesh: Mesh object name.
            armature: Armature object name.
            method: How weights are computed.
                * `AUTOMATIC` — bone heat solve. What you want almost always.
                * `ENVELOPE` — bone envelope radii. Crude, but it works on meshes
                  where the heat solve fails ("Bone Heat Weighting: failed to find
                  solution"), typically non-manifold or self-intersecting geometry.
                * `EMPTY` — create one empty group per deform bone and assign
                  nothing, for weighting entirely by hand.
            reuse_binding: False (default) parents the mesh to the armature and
                adds the Armature modifier via `object.parent_set`. True instead
                re-runs the solver on an already-bound mesh via
                `paint.weight_from_bones`, leaving parenting and modifiers alone —
                use it to redo weights after moving bones. Requires an existing
                Armature modifier pointing at this rig, and does not support
                `EMPTY`.
            keep_transform: Preserve the mesh's world transform when parenting.
                Only applies when `reuse_binding` is false.
            xmirror: Mirror the generated groups across X for symmetric meshes.
                Only applies when `reuse_binding` is false.
            timeout: Seconds to wait. The heat solve is slow on dense meshes.

        Only bones with `use_deform` get a group. The result reports
        `bones_without_group`, which is your first check that the bind worked.
        Follow up with `report_unweighted_verts` to catch geometry the solve
        missed entirely.
        """
        return call("weights.auto_weights",
                    clean(mesh=mesh, armature=armature, method=method,
                          reuse_binding=reuse_binding, keep_transform=keep_transform,
                          xmirror=xmirror),
                    timeout=timeout)

    @mcp.tool()
    def transfer_weights(
        source: str,
        target: str,
        method: str = "POLYINTERP_NEAREST",
        name_matching: bool = True,
        layers_select_src: str = "ALL",
        mix_mode: str = "REPLACE",
        mix_factor: float = 1.0,
        max_distance: Optional[float] = None,
        use_create: bool = True,
        use_object_transform: bool = True,
        timeout: float = 180.0,
    ) -> dict:
        """Copy vertex-group weights from one mesh onto another.

        The tool for re-weighting after a retopo, or for giving clothing the
        body's weights.

        `object.vertex_group_transfer_weight` was **removed in Blender 5.x**;
        this runs `object.data_transfer(data_type='VGROUP_WEIGHTS')`, which is
        the supported replacement and is strictly more capable.

        Args:
            source: Mesh to read weights from.
            target: Mesh to write weights to. Its existing weights in matched
                groups are overwritten.
            method: Vertex mapping, i.e. how a target vertex finds its source
                value. `POLYINTERP_NEAREST` (default) interpolates across the
                nearest face — the "nearest face interpolated" behaviour, and the
                right choice when the two meshes differ in topology.
                Others: `TOPOLOGY` (identical vertex order only), `NEAREST`
                (nearest vertex, blocky), `EDGE_NEAREST`, `EDGEINTERP_NEAREST`,
                `POLY_NEAREST`, `POLYINTERP_VNORPROJ` (projects along the target
                normal — best when the target is offset, like clothing).
            name_matching: True matches destination groups by name and creates
                missing ones. False matches by index, which will scramble weights
                unless both meshes have identical group ordering.
            layers_select_src: Which source groups to send — `ALL` (default),
                `ACTIVE`, or `BONE_DEFORM` (only groups named after a deform bone).
            mix_mode: `REPLACE`, `MIX`, `ADD`, `SUB`, `MUL`, `ABOVE_THRESHOLD`,
                `BELOW_THRESHOLD`.
            mix_factor: 0.0-1.0 blend of the transferred value against what is
                already there. Only meaningful when `mix_mode` is not REPLACE.
            max_distance: Ignore source geometry further away than this, in
                Blender units (world space when `use_object_transform` is true).
                Omit for no distance limit. Use it to stop a nearby limb from
                bleeding onto the wrong part.
            use_create: Create destination groups that do not exist yet.
            use_object_transform: Account for both objects' world transforms.
                Leave true unless the meshes are deliberately co-located in local
                space.
            timeout: Seconds to wait; raise it for dense meshes.

        Both objects are left selected with `source` active, because that is what
        the operator requires.
        """
        return call("weights.transfer_weights",
                    clean(source=source, target=target, method=method,
                          name_matching=name_matching,
                          layers_select_src=layers_select_src, mix_mode=mix_mode,
                          mix_factor=mix_factor, max_distance=max_distance,
                          use_create=use_create,
                          use_object_transform=use_object_transform),
                    timeout=timeout)

    # -- whole-group maths ---------------------------------------------
    @mcp.tool()
    def normalize(mesh: str, group: Optional[str] = None,
                  only_selected: bool = False) -> dict:
        """Scale one group so its highest weight becomes exactly 1.0.

        Per-group, and it does not touch any other group — contrast
        `normalize_all`, which makes each *vertex* sum to 1 across groups. Use
        this when a group's weights are all faint and you want the same falloff
        shape at full strength.

        Args:
            mesh: Mesh object name.
            group: Group to normalize. Omit to use the currently active group.
            only_selected: True restricts the operation to the mesh's current
                vertex selection (runs in Edit mode). False (default) hits every
                vertex.
        """
        return call("weights.normalize",
                    clean(mesh=mesh, group=group, only_selected=only_selected))

    @mcp.tool()
    def normalize_all(
        mesh: str,
        lock_active: bool = True,
        group_select_mode: str = "ALL",
        only_selected: bool = False,
    ) -> dict:
        """Make each vertex's weights sum to 1.0 across groups. Run this before export.

        A vertex whose deform weights sum to more or less than 1 will deform
        wrongly, and most game engines assume normalized weights. This is the fix.

        Args:
            mesh: Mesh object name.
            lock_active: True (default) holds the active group's weights fixed and
                redistributes the rest around it — the standard way to normalize
                without undoing the group you just tuned.
            group_select_mode: Which groups take part. `ALL` (default), `ACTIVE`,
                or `BONE_DEFORM` (only groups named after a deform bone; only
                offered once the mesh has an armature). Blender rejects an invalid
                value with a message listing what this scene actually allows.
            only_selected: Restrict to the current vertex selection (Edit mode).

        Fails with "All groups are locked" if `vgroup_lock` locked everything —
        unlock at least one group first.
        """
        return call("weights.normalize_all",
                    clean(mesh=mesh, lock_active=lock_active,
                          group_select_mode=group_select_mode,
                          only_selected=only_selected))

    @mcp.tool()
    def levels(
        mesh: str,
        gain: float = 1.0,
        offset: float = 0.0,
        group: Optional[str] = None,
        group_select_mode: str = "ACTIVE",
        only_selected: bool = False,
    ) -> dict:
        """Remap weights with `(weight + offset) * gain`, clamped to 0..1.

        The contrast/brightness control for weights. `gain` above 1 sharpens the
        falloff; a positive `offset` lifts the whole group toward 1.

        Args:
            mesh: Mesh object name.
            gain: Multiplier applied after the offset. 1.0 is no change.
            offset: Added to every weight before the gain. Range roughly -1..1.
            group: Group to affect. Omit to use the active group.
            group_select_mode: `ACTIVE` (default), `ALL` or `BONE_DEFORM`.
            only_selected: Restrict to the current vertex selection (Edit mode).
        """
        return call("weights.levels",
                    clean(mesh=mesh, gain=gain, offset=offset, group=group,
                          group_select_mode=group_select_mode,
                          only_selected=only_selected))

    @mcp.tool()
    def invert(
        mesh: str,
        group: Optional[str] = None,
        auto_assign: bool = True,
        auto_remove: bool = True,
        group_select_mode: str = "ACTIVE",
        only_selected: bool = False,
    ) -> dict:
        """Replace every weight with `1 - weight`.

        Args:
            mesh: Mesh object name.
            group: Group to invert. Omit to use the active group.
            auto_assign: Add unassigned vertices to the group with weight 1
                (since their implied 0 inverts to 1). True matches Blender's
                default; set False to leave unassigned vertices alone.
            auto_remove: Unassign vertices whose weight inverts to 0, rather than
                storing an explicit zero.
            group_select_mode: `ACTIVE` (default), `ALL` or `BONE_DEFORM`.
            only_selected: Restrict to the current vertex selection (Edit mode).
        """
        return call("weights.invert",
                    clean(mesh=mesh, group=group, auto_assign=auto_assign,
                          auto_remove=auto_remove,
                          group_select_mode=group_select_mode,
                          only_selected=only_selected))

    @mcp.tool()
    def clean_weights(
        mesh: str,
        threshold: float = 0.01,
        group: Optional[str] = None,
        keep_single: bool = False,
        group_select_mode: str = "ACTIVE",
        only_selected: bool = False,
    ) -> dict:
        """Unassign weights below a threshold. Removes the dust before export.

        Auto weights leave thousands of near-zero influences that bloat exports
        and push real influences out of the top-4 on `limit_total`. Run this
        first, then `limit_total`, then `normalize_all`.

        Args:
            mesh: Mesh object name.
            threshold: Weights at or below this are removed. 0.0-1.0; 0.01 is a
                sane default, 0.001 is conservative.
            group: Group to clean. Omit to use the active group.
            keep_single: Never leave a vertex with zero groups — keeps its single
                strongest influence even if it is below the threshold. Turn this
                on when cleaning `ALL` on a rigged mesh, or you will strand
                vertices at the origin.
            group_select_mode: `ACTIVE` (default), `ALL` or `BONE_DEFORM`.
            only_selected: Restrict to the current vertex selection (Edit mode).
        """
        return call("weights.clean",
                    clean(mesh=mesh, threshold=threshold, group=group,
                          keep_single=keep_single,
                          group_select_mode=group_select_mode,
                          only_selected=only_selected))

    @mcp.tool()
    def quantize(
        mesh: str,
        steps: int = 4,
        group: Optional[str] = None,
        group_select_mode: str = "ACTIVE",
        only_selected: bool = False,
    ) -> dict:
        """Round weights onto N evenly spaced steps.

        Mostly a stylisation / debugging tool: `steps=2` gives a hard 0-or-1
        split, which makes a group's boundary obvious in `weight_heatmap`.

        Args:
            mesh: Mesh object name.
            steps: Number of discrete levels, 1 or more.
            group: Group to quantize. Omit to use the active group.
            group_select_mode: `ACTIVE` (default), `ALL` or `BONE_DEFORM`.
            only_selected: Restrict to the current vertex selection (Edit mode).
        """
        return call("weights.quantize",
                    clean(mesh=mesh, steps=steps, group=group,
                          group_select_mode=group_select_mode,
                          only_selected=only_selected))

    @mcp.tool()
    def limit_total(
        mesh: str,
        max_influences: int = 4,
        group_select_mode: str = "ALL",
        only_selected: bool = False,
    ) -> dict:
        """Keep only the N strongest influences per vertex. The game-export gate.

        Most real-time engines support 4 bone influences per vertex; anything
        beyond that is dropped by the exporter or the engine, silently and badly.
        Run `clean` first so near-zero dust does not occupy a slot, and
        `normalize_all` afterwards because dropping influences leaves the
        remaining weights summing to less than 1.

        Args:
            mesh: Mesh object name.
            max_influences: Influences to keep per vertex. 4 is the usual target.
            group_select_mode: `ALL` (default) or `BONE_DEFORM`. `ACTIVE` makes
                little sense here since the point is comparing across groups.
            only_selected: Restrict to the current vertex selection (Edit mode).

        Use `report_over_influenced` before and after to see what it did.
        """
        return call("weights.limit_total",
                    clean(mesh=mesh, max_influences=max_influences,
                          group_select_mode=group_select_mode,
                          only_selected=only_selected))

    @mcp.tool()
    def smooth_weights(
        mesh: str,
        factor: float = 0.5,
        iterations: int = 1,
        expand: float = 0.0,
        group: Optional[str] = None,
        group_select_mode: str = "ACTIVE",
        only_selected: bool = False,
        timeout: float = 120.0,
    ) -> dict:
        """Blend each weight toward its connected neighbours. Fixes hard creases.

        The cure for the faceted, blocky deformation that auto weights leave at
        joints.

        Args:
            mesh: Mesh object name.
            factor: How far each weight moves toward the neighbour average per
                iteration, 0.0-1.0. 0.5 is a good default.
            iterations: How many passes. Prefer several passes at a moderate
                factor over one pass at 1.0 — it stays stable and does not
                collapse the group.
            expand: -1.0 to 1.0. Positive grows the weighted region outward into
                unweighted neighbours; negative shrinks it. 0 keeps the current
                extent.
            group: Group to smooth. Omit to use the active group.
            group_select_mode: `ACTIVE` (default), `ALL` or `BONE_DEFORM`.
            only_selected: Restrict to the current vertex selection (Edit mode).
            timeout: Seconds to wait; raise it for many iterations on a dense mesh.

        Blender's `object.vertex_group_smooth` refuses to run in Object mode, so
        this tool enters Weight Paint mode (or Edit mode when `only_selected`) and
        restores the previous mode afterwards. It works headlessly all the same.
        """
        return call("weights.smooth_weights",
                    clean(mesh=mesh, factor=factor, iterations=iterations,
                          expand=expand, group=group,
                          group_select_mode=group_select_mode,
                          only_selected=only_selected),
                    timeout=timeout)

    @mcp.tool()
    def mirror_weights(
        mesh: str,
        axis: str = "X",
        use_topology: bool = False,
        all_groups: bool = False,
        flip_group_names: bool = True,
        group: Optional[str] = None,
        tolerance: float = 0.0001,
        timeout: float = 120.0,
    ) -> dict:
        """Mirror weights across a local axis, swapping .L/.R group names as it goes.

        Weight one side of a character, then mirror. Both the geometry and the
        group names have to be symmetric for this to be useful.

        Args:
            mesh: Mesh object name.
            axis: `X` (default), `Y` or `Z`, in the object's **local** space — not
                world space. If the object is rotated, its local X is not the
                world X.
            use_topology: Pair vertices by mesh topology instead of by mirrored
                position. Use it when the mesh is symmetric in structure but not
                in exact coordinates. **X axis only** — ignored for Y and Z.
            all_groups: Mirror every vertex group instead of just one.
            flip_group_names: Write mirrored weights into the name-flipped group
                (`Arm.L` -> `Arm.R`). Turn off to mirror a group onto itself.
            group: Group to mirror when `all_groups` is false. Omit to use the
                active group.
            tolerance: Maximum distance, in object-space Blender units, for two
                vertices to count as a mirrored pair. Y/Z axis only. Raise it for
                a slightly asymmetric mesh; the result reports
                `unpaired_vertices` so you can tell whether it was enough.
            timeout: Seconds to wait; the Y/Z path is O(vertices x groups).

        **Blender 5.2's `object.vertex_group_mirror` only mirrors along X.** For
        `axis="X"` this tool uses that operator, with its full name-flipping and
        topology support. For `Y` and `Z` it falls back to a KD-tree pairing of
        object-space coordinates written through the data API; the result reports
        `method: "kdtree"`, ignores `use_topology`, and flips names using the
        common `.L/.R`, `_L/_R` and `Left/Right` conventions only.
        """
        return call("weights.mirror_weights",
                    clean(mesh=mesh, axis=axis, use_topology=use_topology,
                          all_groups=all_groups, flip_group_names=flip_group_names,
                          group=group, tolerance=tolerance),
                    timeout=timeout)

    # -- diagnostics ---------------------------------------------------
    @mcp.tool()
    def report_unweighted_verts(
        mesh: str,
        armature: Optional[str] = None,
        threshold: float = 0.0,
        limit: int = 1000,
        timeout: float = 60.0,
    ) -> dict:
        """Find vertices with zero total deform weight — they will not follow the rig.

        The single most useful check after `auto_weights`. Unweighted vertices
        stay pinned in place while the rest of the mesh moves, which reads as the
        mesh tearing.

        Args:
            mesh: Mesh object name.
            armature: Armature defining which groups count as deform. Omit to
                take it from the mesh's Armature modifier.
            threshold: A vertex counts as unweighted when its total deform weight
                is at or below this. 0.0 finds only truly unweighted vertices;
                raise it slightly (e.g. 0.001) to also catch near-zero ones.
            limit: Cap on the vertex list returned. `unweighted_count` is always
                the true total.
            timeout: Seconds to wait; scales with vertex count.

        Each reported vertex includes its object-space `co`, so you can feed the
        coordinates straight into a bounding-box `verts_spec` for
        `assign_weights`.
        """
        return call("weights.report_unweighted_verts",
                    clean(mesh=mesh, armature=armature, threshold=threshold,
                          limit=limit),
                    timeout=timeout)

    @mcp.tool()
    def report_over_influenced(
        mesh: str,
        max_influences: int = 4,
        armature: Optional[str] = None,
        threshold: float = 0.0,
        limit: int = 1000,
        timeout: float = 60.0,
    ) -> dict:
        """Find vertices bound to more deform bones than a game engine will accept.

        Run this before any real-time export (glTF, FBX to Unity/Unreal). The
        returned `influence_histogram` maps influence count to vertex count, which
        tells you at a glance whether the mesh is already export-clean.

        Args:
            mesh: Mesh object name.
            max_influences: The engine's limit. 4 for most real-time engines,
                8 for some modern ones.
            armature: Armature defining which groups count as deform. Omit to
                take it from the Armature modifier; if the mesh has no armature at
                all, every vertex group is counted.
            threshold: Weights at or below this do not count as an influence.
                0.0 counts even an explicit zero, which is usually what an
                exporter does too.
            limit: Cap on the vertex list returned.
            timeout: Seconds to wait; scales with vertex count.

        The fix is `clean` then `limit_total` then `normalize_all`, in that order.
        """
        return call("weights.report_over_influenced",
                    clean(mesh=mesh, max_influences=max_influences,
                          armature=armature, threshold=threshold, limit=limit),
                    timeout=timeout)

    @mcp.tool()
    def per_bone_weight_summary(
        mesh: str,
        armature: Optional[str] = None,
        limit: int = 1000,
        timeout: float = 60.0,
    ) -> dict:
        """Per deform bone: vertex count, total, max and mean weight. Cheap whole-rig view.

        Prefer this over paging through `get_weights` when you want to know
        whether a bind is sane. Three fields do the diagnosing:

        * `bones_with_no_group` — deform bones with no matching vertex group at
          all, so they deform nothing.
        * `empty_bones` — groups that exist but hold no weight, the classic
          symptom of a heat solve that quietly failed on part of the mesh.
        * `groups_not_deform_bones` — vertex groups that match no deform bone,
          i.e. either non-deform helper groups or a misspelled bone name.

        Args:
            mesh: Mesh object name.
            armature: Armature to summarise against. Omit to take it from the
                mesh's Armature modifier.
            limit: Cap on the per-bone list.
            timeout: Seconds to wait; scales with vertex count.
        """
        return call("weights.per_bone_weight_summary",
                    clean(mesh=mesh, armature=armature, limit=limit),
                    timeout=timeout)

    # -- viewport-only -------------------------------------------------
    @mcp.tool()
    def weight_gradient(
        mesh: str,
        start: list[float],
        end: list[float],
        group: Optional[str] = None,
        type: str = "LINEAR",
        weight: float = 1.0,
        space: str = "WORLD",
        flip: bool = False,
    ) -> dict:
        """Paint a linear or radial weight gradient between two 3D points. GUI Blender only.

        **GUI Blender only — fails under `--background`.** `paint.weight_gradient`
        takes screen coordinates, so it needs a real 3D Viewport; this tool
        projects your 3D points into region pixels for you.

        Prefer `set_weights` when you can compute the falloff yourself: it is
        exact, headless, and does not depend on where the viewport camera happens
        to be pointing. Reach for this when you want the gradient to follow what
        is actually visible on screen.

        Args:
            mesh: Mesh object name.
            start: `[x, y, z]` where the gradient is at full `weight`.
            end: `[x, y, z]` where the gradient reaches 0. Both points must be in
                front of the viewport camera or the call fails with an explanation.
            group: Group to paint into. Omit to use the active group.
            type: `LINEAR` (a band perpendicular to start->end) or `RADIAL`
                (concentric around `start`, reaching 0 at `end`).
            weight: Value at the start point, 0.0-1.0.
            space: `WORLD` (default) or `LOCAL` — how to interpret `start`/`end`.
                Use `get_object_info` for the object's `matrix_world` if you need
                to convert.
            flip: Swap the ends, so the gradient runs 0 -> weight instead.

        The gradient only affects the parts of the mesh visible in the viewport;
        anything facing away or occluded is not painted. Take a
        `weight_heatmap` afterwards to see what actually landed.
        """
        return call("weights.weight_gradient",
                    clean(mesh=mesh, start=start, end=end, group=group, type=type,
                          weight=weight, space=space, flip=flip),
                    timeout=60.0)

    @mcp.tool()
    def brush_stroke(
        mesh: str,
        points: list[list[float]],
        group: Optional[str] = None,
        weight: float = 1.0,
        radius_px: float = 50.0,
        strength: Optional[float] = None,
        mode: str = "NORMAL",
        space: str = "WORLD",
        pressure: float = 1.0,
    ) -> dict:
        """Drag the weight brush along a path of 3D points. GUI Blender only.

        **GUI Blender only — fails under `--background`.**

        Use this last. `assign_weights` and `set_weights` are exact, headless and
        reproducible; a brush stroke depends on the viewport camera, occlusion and
        brush falloff, so its result is hard to predict and hard to repeat. It is
        here for the cases where you genuinely want painterly falloff along a
        surface path.

        Args:
            mesh: Mesh object name.
            points: Ordered `[[x, y, z], ...]` path, at least two points. Every
                point must project in front of the viewport camera.
            group: Group to paint into. Omit to use the active group.
            weight: Target weight the brush paints toward, 0.0-1.0.
            radius_px: Brush radius in **screen pixels**, not world units — so the
                world-space footprint changes with camera distance.
            strength: Brush strength 0.0-1.0, how fast each dab approaches
                `weight`. Omit to keep the brush's current strength.
            mode: `NORMAL` paints toward `weight`; `INVERT` paints away from it.
            space: `WORLD` (default) or `LOCAL` for the point coordinates.
            pressure: Simulated stylus pressure 0.0-1.0, applied to every point.

        Only geometry facing the viewport is affected. Verify with
        `weight_heatmap` rather than assuming.
        """
        return call("weights.brush_stroke",
                    clean(mesh=mesh, points=points, group=group, weight=weight,
                          radius_px=radius_px, strength=strength, mode=mode,
                          space=space, pressure=pressure),
                    timeout=120.0)

    @mcp.tool()
    def weight_heatmap(
        mesh: str,
        group: Optional[str] = None,
        max_size: int = 1024,
        show_contours: bool = False,
        use_render: bool = False,
    ) -> Any:
        """See one group's weights as the blue-to-red heatmap. Returns an image. GUI only.

        **GUI Blender only — fails under `--background`.** This is your eyes for
        weight painting: blue is 0, green is ~0.5, red is 1.0, and black means the
        vertex is not in the group at all.

        Args:
            mesh: Mesh object name.
            group: Group to display. Omit to use the active group.
            max_size: Longest edge in pixels (default 1024). Raise only to inspect
                fine detail — large images cost a lot of context.
            show_contours: Draw iso-weight contour lines over the colours, which
                makes gradients much easier to read than colour alone.
            use_render: False (default) grabs the actual viewport pixels via
                `screen.screenshot_area`, which is the only way the weight colours
                appear — they are drawn by the overlay engine. True instead does a
                clean `render.opengl` pass with no viewport chrome, but **the
                weight colours will be missing**; only useful for a plain look at
                the geometry.

        The mesh is switched into Weight Paint mode for the capture and the
        previous mode, shading and overlay settings are restored afterwards. The
        image shows the current viewport camera — orbit it first with your
        viewport tools if the area you care about is facing away.
        """
        payload = call("weights.weight_heatmap",
                       clean(mesh=mesh, group=group, max_size=max_size,
                             show_contours=show_contours, use_render=use_render),
                       timeout=90.0)
        return png_image(payload)
