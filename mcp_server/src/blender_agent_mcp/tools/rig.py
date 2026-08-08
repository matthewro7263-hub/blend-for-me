"""Rigging: armatures, bones, posing, constraints, shape keys and drivers.

Every tool here runs through Blender's data API and works in headless Blender
too — none of them need a 3D Viewport.
"""

from __future__ import annotations

from typing import Optional

from ..server import call, clean


def register(mcp) -> None:

    # -- armature + rest pose -----------------------------------------
    @mcp.tool()
    def create_armature(
        name: str = "Armature",
        bone_tree: Optional[list[dict]] = None,
        location: Optional[list[float]] = None,
    ) -> dict:
        """Create an armature and build its whole bone hierarchy in one call.

        Prefer this over creating an empty armature and calling `add_bones`
        repeatedly: the tree is built in a single EDIT-mode session, and parents
        may be forward references to bones defined later in the same list.

        Args:
            name: Name for both the object and its armature data.
            bone_tree: List of bone dicts. Omit to get one default bone `Bone`
                running from (0,0,0) to (0,0,1). Each dict takes:
                  * `name` (required)
                  * `head`, `tail`: [x, y, z] in ARMATURE-OBJECT LOCAL space, not
                    world space. They coincide only while `location` is (0,0,0)
                    and the object is unrotated/unscaled.
                  * `roll`: twist about the bone axis, in RADIANS (default 0).
                  * `parent`: name of another bone, in this list or already in the
                    armature.
                  * `connect`: true glues this bone's head onto its parent's tail,
                    *moving* the head to get there. Needs a parent.
                  * `use_deform`: whether the bone gets a vertex group when a mesh
                    is bound (default true). Set false for control/IK bones.
            location: Armature object origin in WORLD space (default origin).

        head == tail is rejected: Blender silently deletes zero-length bones when
        leaving edit mode. Names collide silently — Blender appends `.001` — so
        read the returned `bones[].name`, which is what was actually created.

        Name bones `.L`/`.R` if you ever intend to call `symmetrize_bones`.
        """
        return call(
            "rig.create_armature",
            clean(name=name, bone_tree=bone_tree, location=location),
            timeout=60.0,
        )

    @mcp.tool()
    def add_bones(armature: str, bones: list[dict]) -> dict:
        """Add bones to an existing armature.

        Same bone dict format as `create_armature`'s `bone_tree` (head/tail in
        armature-local space, roll in RADIANS, `connect` moves the head onto the
        parent's tail). Parents may reference bones created in this same call.

        Use `create_armature` when you are building a rig from nothing; use this
        to extend one, e.g. adding a tail chain or a control bone.
        """
        return call("rig.edit_bones_add", {"armature": armature, "bones": bones},
                    timeout=60.0)

    @mcp.tool()
    def remove_bones(armature: str, bones: list[str]) -> dict:
        """Delete bones from an armature, re-parenting their children upward.

        Children of a deleted bone are attached to its parent (or unparented if it
        was a root) and disconnected, so the hierarchy never breaks. Vertex groups
        on bound meshes are NOT removed — the mesh keeps a now-inert group of the
        same name.
        """
        return call("rig.edit_bones_remove", {"armature": armature, "bones": bones},
                    timeout=60.0)

    @mcp.tool()
    def edit_bone(
        armature: str,
        bone: str,
        head: Optional[list[float]] = None,
        tail: Optional[list[float]] = None,
        roll: Optional[float] = None,
        parent: Optional[str] = None,
        use_connect: Optional[bool] = None,
        use_deform: Optional[bool] = None,
        name: Optional[str] = None,
    ) -> dict:
        """Edit one bone's REST geometry (edit mode), not its pose.

        This is the tool for changing how the skeleton is built. To move a bone
        while animating, use `pose_bone` instead — that leaves the rest pose alone
        and is what keyframes record.

        Args:
            armature: Armature object name.
            bone: Bone to edit.
            head, tail: [x, y, z] in ARMATURE-OBJECT LOCAL space.
            roll: Twist about the bone axis, in RADIANS.
            parent: New parent bone name. Pass an empty string `""` to unparent
                (which also clears use_connect).
            use_connect: True snaps this bone's head onto the parent's tail and
                keeps it there; requires a parent.
            use_deform: Whether the bone deforms bound meshes.
            name: Rename the bone. Vertex groups on bound meshes are NOT renamed,
                so a rename silently breaks existing skinning — rename before you
                bind, not after.

        Omitted arguments are left untouched. Setting head == tail is rejected.
        """
        return call(
            "rig.edit_bone_set",
            clean(armature=armature, bone=bone, head=head, tail=tail, roll=roll,
                  parent=parent, use_connect=use_connect, use_deform=use_deform,
                  name=name),
            timeout=60.0,
        )

    @mcp.tool()
    def list_bones(armature: str, space: str = "AUTO", limit: int = 1000) -> dict:
        """List an armature's bones with parents, heads and tails.

        Call this before any other rig tool so you address bones by their real
        names — `create_armature` and `symmetrize_bones` both rename on collision.

        Args:
            armature: Armature object name.
            space: Which view of each bone to report.
              * `DATA` — rest pose, read-only. head/tail are `head_local`/
                `tail_local` in armature-local space. Also lists children.
              * `EDIT` — rest pose *editable* fields: head, tail, `roll`
                (RADIANS), use_connect, use_deform. Reading this briefly enters
                and leaves edit mode; it is the only place roll is visible.
              * `POSE` — the posed result: head/tail in armature space after
                constraints, plus location / rotation_euler (RADIANS) /
                rotation_quaternion ([w,x,y,z]) / scale and each bone's
                constraints.
              * `AUTO` (default) — matches the armature's current mode, falling
                back to `DATA`.
            limit: Max bones returned. `count` is always the true total and
                `truncated` says whether the list was cut.
        """
        return call("rig.list_bones",
                    clean(armature=armature, space=space, limit=limit), timeout=60.0)

    @mcp.tool()
    def symmetrize_bones(
        armature: str,
        direction: str = "NEGATIVE_X",
        bones: Optional[list[str]] = None,
    ) -> dict:
        """Mirror side-suffixed bones across the X axis, copying constraints.

        THE GOTCHA: this only mirrors bones whose names carry a side suffix —
        `.L`/`.R`, `_L`/`_R`, `left`/`right`. A bone named `arm` produces nothing
        and the call still succeeds; check the returned `created` list, which is
        empty in exactly that case. Build the left side as `upper_arm.L` etc. and
        this does the rest.

        Args:
            armature: Armature object name.
            direction: Which half is the source. Only two values exist in Blender
                5.2: `NEGATIVE_X` (default; +X bones are copied to -X) or
                `POSITIVE_X`.
            bones: Restrict to these bones. Omit to symmetrize the whole armature.

        Existing mirrored bones are overwritten rather than duplicated, so this is
        safe to re-run after editing the source side.
        """
        return call("rig.symmetrize_bones",
                    clean(armature=armature, direction=direction, bones=bones),
                    timeout=120.0)

    # -- posing --------------------------------------------------------
    @mcp.tool()
    def pose_bone(
        armature: str,
        bone: str,
        location: Optional[list[float]] = None,
        rotation_euler: Optional[list[float]] = None,
        rotation_quaternion: Optional[list[float]] = None,
        scale: Optional[list[float]] = None,
        space: str = "LOCAL",
        mode: str = "absolute",
        rotation_mode: Optional[str] = None,
    ) -> dict:
        """Move, rotate or scale a bone's POSE — the transform animation records.

        Use this for animating and for test poses. Use `edit_bone` instead to
        change the skeleton itself.

        Args:
            armature: Armature object name.
            bone: Pose bone name.
            location: [x, y, z] offset. In `LOCAL` space this is the bone's own
                location channel (along the bone's axes, relative to its rest
                position) — it is NOT a world offset.
            rotation_euler: [x, y, z] in RADIANS. Passing this while the bone is
                in quaternion mode switches the bone to `XYZ`, because the euler
                channels are otherwise ignored; the response reports the mode.
            rotation_quaternion: [w, x, y, z] — w FIRST, which is Blender's order
                and the opposite of many other tools.
            scale: [x, y, z] multipliers, 1.0 = unscaled.
            space: `LOCAL` (default, the bone's own channels), `POSE` (armature
                space, i.e. relative to the armature object's origin), or `WORLD`.
                `POSE`/`WORLD` compose a matrix, so components you omit are taken
                from the bone's current position rather than left as channels.
            mode: `absolute` (default) sets the value; `delta` adds to it —
                location adds, rotation multiplies, scale multiplies.
            rotation_mode: Force `QUATERNION`, `XYZ`…`ZYX`, or `AXIS_ANGLE`.

        Nothing here is keyframed. Call `keyframe_pose` afterwards to record it,
        or the value is lost the moment the frame changes on an animated bone.
        """
        return call(
            "rig.pose_bone_transform",
            clean(armature=armature, bone=bone, location=location,
                  rotation_euler=rotation_euler,
                  rotation_quaternion=rotation_quaternion, scale=scale,
                  space=space, mode=mode, rotation_mode=rotation_mode),
            timeout=60.0,
        )

    @mcp.tool()
    def keyframe_pose(
        armature: str,
        bones: Optional[list[str]] = None,
        frame: Optional[int] = None,
        channels: Optional[list[str]] = None,
    ) -> dict:
        """Keyframe bone poses at a frame.

        Args:
            armature: Armature object name.
            bones: Bones to key. Omit to key every bone in the armature.
            frame: Frame number. Omit for the scene's current frame.
            channels: What to record. Shorthands: `LOC`, `ROT`, `SCALE`,
                `LOCROT`, `LOCROTSCALE` (the default), `ALL`. `ROT` resolves per
                bone to whichever rotation channel that bone's rotation_mode
                actually uses, which is what you want — keying
                `rotation_quaternion` on a bone in XYZ mode records nothing
                visible. Exact property names such as `location` also work.

        Keys land in a group named after the bone. Set the pose with `pose_bone`
        first; this records the current state, it does not set one.
        """
        return call(
            "rig.pose_bone_keyframe",
            clean(armature=armature, bones=bones, frame=frame, channels=channels),
            timeout=60.0,
        )

    @mcp.tool()
    def reset_pose(armature: str, bones: Optional[list[str]] = None) -> dict:
        """Snap bones back to their rest pose (clears location, rotation, scale).

        Args:
            armature: Armature object name.
            bones: Bones to clear. Omit to reset the entire armature.

        This clears the pose *channels* only. It does not remove keyframes, so on
        an animated rig the pose returns as soon as the frame changes, and it does
        not disable constraints — an IK-driven bone snaps straight back.
        """
        return call("rig.reset_pose", clean(armature=armature, bones=bones))

    @mcp.tool()
    def apply_pose_as_rest(
        armature: str, bones: Optional[list[str]] = None
    ) -> dict:
        """Freeze the current pose as the new rest pose. Destructive — read this.

        Args:
            armature: Armature object name.
            bones: Only apply these bones. Omit for the whole armature.

        Three things bite here:
          * Bound meshes are NOT re-fitted. They keep the vertex positions they
            have now, so the result only looks right if the mesh was already
            deformed into this pose. The response lists `deformed_meshes` so you
            can check what is affected.
          * Blender skips meshes that have shape keys entirely.
          * Existing keyframes are now measured from the new rest pose, so any
            animation on this armature shifts.

        Take a `viewport_screenshot` before and after. Typical safe use is
        fixing a bad rest pose on a rig that is not yet bound or animated.
        """
        return call("rig.apply_pose_as_rest", clean(armature=armature, bones=bones),
                    timeout=120.0)

    # -- constraints and IK --------------------------------------------
    @mcp.tool()
    def add_bone_constraint(
        armature: str,
        bone: str,
        type: str,
        settings: Optional[dict] = None,
        name: Optional[str] = None,
    ) -> dict:
        """Add any bone constraint type and set its properties generically.

        For IK specifically, prefer `setup_ik` — it creates the target and pole
        objects and solves the pole angle for you. Use this tool for everything
        else: COPY_ROTATION, COPY_LOCATION, COPY_TRANSFORMS, DAMPED_TRACK,
        LIMIT_ROTATION, STRETCH_TO, CHILD_OF, ARMATURE, TRANSFORM, and so on.

        Args:
            armature: Armature object name.
            bone: Pose bone that gets the constraint.
            type: Constraint type id. Pass a wrong one and the error lists every
                valid type for this Blender build — that is the intended way to
                discover them.
            settings: Property name -> value, applied after creation. Object
                pointers (`target`, `pole_target`, `space_object`) take an object
                NAME as a string. `subtarget` is a bone name string, and is
                required whenever `target` is an armature. Angles are in RADIANS,
                `influence` is 0-1 (not 0-100). An unknown key raises an error
                listing every writable setting for that constraint type.
            name: Constraint name; defaults to Blender's own.

        The response includes `is_valid` (false usually means a missing subtarget)
        and `writable_settings`, so a first call with `settings` omitted is a
        cheap way to see what this constraint type accepts.
        """
        return call(
            "rig.add_bone_constraint",
            clean(armature=armature, bone=bone, type=type, settings=settings,
                  name=name),
            timeout=60.0,
        )

    @mcp.tool()
    def setup_ik(
        armature: str,
        chain_tip: str,
        chain_length: int = 2,
        target: Optional[str] = None,
        target_bone: Optional[str] = None,
        target_type: str = "EMPTY",
        auto_pole: bool = False,
        pole_target: Optional[str] = None,
        pole_bone: Optional[str] = None,
        pole_angle: Optional[float] = None,
        pole_distance: Optional[float] = None,
        use_tail: bool = True,
        use_stretch: bool = False,
        name: Optional[str] = None,
    ) -> dict:
        """Build a working IK setup: constraint plus the target and pole it needs.

        The one-call way to make an arm or leg IK. Everything it creates is listed
        in the response's `created` array.

        Args:
            armature: Armature object name.
            chain_tip: The LAST bone of the chain — the one the IK constraint sits
                on. For a classic arm (upper_arm -> forearm -> hand) that is
                `forearm`, not `hand`.
            chain_length: How many bones the solver owns, counting up from the tip.
                2 = tip plus its parent, the usual arm/leg value. 0 means "all the
                way to the root", which will swing the whole spine — rarely what
                you want.
            target: Existing object to reach for. Omit to create one at the tip's
                current tail, so the chain does not jump when the constraint is
                attached.
            target_bone: Bone name, required when `target` is an armature.
            target_type: What to create when `target` is omitted: `EMPTY` (default,
                a plain-axes empty in the scene) or `BONE` (an unparented
                non-deforming bone inside this armature).
            auto_pole: Create a pole target so the elbow/knee bends predictably.
                It is placed in the plane the chain already bends in, on the side
                it already points — so a pre-bent rest pose gives the right
                answer and a perfectly straight one does not (straighten-then-bend
                your chain slightly in edit mode first).
            pole_target, pole_bone: Use an existing pole instead of creating one.
            pole_angle: Rotation of the chain about the root-to-target axis, in
                RADIANS. Omit it together with `auto_pole` and the correct angle
                is SOLVED numerically — the value that leaves the chain exactly
                where it already was. That takes a moment but removes the usual
                "the arm flipped 90 degrees" problem. Supply it explicitly only to
                override.
            pole_distance: How far from the joint to place a created pole, in
                armature-local units. Defaults to the chain's length.
            use_tail: Whether the tip bone's tail (default) or head reaches the
                target.
            use_stretch: Let the chain stretch to reach. Off by default.
            name: Constraint name.

        Check `is_valid` in the response, then take a `viewport_screenshot` — IK
        is the single easiest thing to get subtly wrong.
        """
        return call(
            "rig.setup_ik",
            clean(armature=armature, chain_tip=chain_tip, chain_length=chain_length,
                  target=target, target_bone=target_bone, target_type=target_type,
                  auto_pole=auto_pole, pole_target=pole_target, pole_bone=pole_bone,
                  pole_angle=pole_angle, pole_distance=pole_distance,
                  use_tail=use_tail, use_stretch=use_stretch, name=name),
            timeout=180.0,
        )

    # -- skinning ------------------------------------------------------
    @mcp.tool()
    def parent_mesh_to_armature(
        mesh: str,
        armature: str,
        mode: str = "AUTOMATIC",
        keep_transform: bool = False,
    ) -> dict:
        """Bind a mesh to an armature so the bones deform it.

        Adds an Armature modifier plus vertex groups named after the deforming
        bones, and parents the mesh to the armature.

        Args:
            mesh: Mesh object name.
            armature: Armature object name.
            mode: How initial weights are produced.
              * `AUTOMATIC` (default) — bone-heat weighting. The usual choice.
                Slow on dense meshes, and it fails outright on non-manifold
                geometry or when bones sit outside the mesh volume.
              * `ENVELOPE` — weights from each bone's envelope radius. Fast and
                crude; useful when automatic weights fail.
              * `EMPTY` — creates the vertex groups but leaves every weight at
                zero. Nothing deforms until you paint or transfer weights. Pick
                this when you intend to author weights yourself.
            keep_transform: Preserve the mesh's current world transform when
                parenting. Leave false unless the mesh was already moved.

        Only bones with `use_deform` get a vertex group — that is why IK targets
        and control bones should be created with `use_deform: false`.
        """
        return call(
            "rig.parent_mesh_to_armature",
            clean(mesh=mesh, armature=armature, mode=mode,
                  keep_transform=keep_transform),
            timeout=300.0,
        )

    # -- shape keys ----------------------------------------------------
    @mcp.tool()
    def shapekey_create(
        object: str,
        name: str = "Key",
        from_mix: bool = False,
        value: Optional[float] = None,
    ) -> dict:
        """Add a shape key to a mesh, curve, surface or lattice.

        Args:
            object: Object to add the key to.
            name: Key name.
            from_mix: False (default) captures the current base shape, giving you
                an identical copy to sculpt into. True captures the current blend
                of all active keys — see `shapekey_from_mix`.
            value: Initial value, 0-1 by default.

        If the object has no shape keys yet, a `Basis` reference key is created
        first and the response says `created_basis: true`. That matters: without
        it your first key would silently become the rest shape and deform nothing.

        A new key holds the base shape. To make it do something, move vertices
        while it is the active key — use `set_mode` plus mesh editing tools.
        """
        return call("rig.shapekey_create",
                    clean(object=object, name=name, from_mix=from_mix, value=value))

    @mcp.tool()
    def shapekey_from_mix(object: str, name: str = "Mix") -> dict:
        """Snapshot the current blend of all shape keys into one new key.

        The way to bake a combination — set several keys with `shapekey_set_value`,
        then capture the result as a single key. Identical to `shapekey_create`
        with `from_mix=true`.
        """
        return call("rig.shapekey_from_mix", clean(object=object, name=name))

    @mcp.tool()
    def shapekey_set_value(
        object: str,
        key: str,
        value: Optional[float] = None,
        slider_min: Optional[float] = None,
        slider_max: Optional[float] = None,
        mute: Optional[bool] = None,
        vertex_group: Optional[str] = None,
        relative_key: Optional[str] = None,
    ) -> dict:
        """Set a shape key's value and its slider range, mute and masking.

        Args:
            object: Object that owns the key.
            key: Shape key name.
            value: How much the key is applied. 0-1 by default. Values outside
                [slider_min, slider_max] are CLAMPED — the response adds a
                `clamped` note when that happened, so check it if the shape did
                not move as far as you expected.
            slider_min, slider_max: Widen the range first if you want to
                over-drive or invert a key (e.g. slider_max 2.0). These are
                applied before `value`, so both can be sent in one call.
            mute: True disables the key without changing its value.
            vertex_group: Restrict the key's effect to this vertex group,
                weighted by the group's weights. Pass `""` to remove the
                restriction.
            relative_key: The key this one is measured against (normally `Basis`).
                Change it to chain corrective shapes.
        """
        return call(
            "rig.shapekey_set_value",
            clean(object=object, key=key, value=value, slider_min=slider_min,
                  slider_max=slider_max, mute=mute, vertex_group=vertex_group,
                  relative_key=relative_key),
        )

    @mcp.tool()
    def shapekey_keyframe(
        object: str,
        keys: Optional[list[str]] = None,
        frame: Optional[int] = None,
        value: Optional[float] = None,
    ) -> dict:
        """Keyframe shape key values at a frame.

        Args:
            object: Object that owns the keys.
            keys: Key names. Omit to key every key except the Basis.
            frame: Frame number. Omit for the scene's current frame.
            value: Set this value on each key before keying. Omit to key whatever
                the values already are.

        Shape key animation lives on the Key datablock, NOT on the object, so it
        is a separate action from the object's own animation. The response names
        both the Key datablock and its action.
        """
        return call(
            "rig.shapekey_keyframe",
            clean(object=object, keys=keys, frame=frame, value=value),
        )

    @mcp.tool()
    def shapekey_list(object: str, limit: int = 1000) -> dict:
        """List an object's shape keys: values, slider ranges, masks and order.

        Start here before touching shape keys — it reports the real names, the
        current value of each key, which key is the Basis, and each key's
        slider range, which is what silently clamps `shapekey_set_value`.
        Returns `has_shape_keys: false` for an object that has none yet.
        """
        return call("rig.shapekey_list", clean(object=object, limit=limit))

    # -- drivers -------------------------------------------------------
    @mcp.tool()
    def add_driver(
        object: str,
        data_path: str,
        expression: str = "var",
        variables: Optional[list[dict]] = None,
        index: Optional[int] = None,
        host: str = "OBJECT",
        driver_type: str = "SCRIPTED",
    ) -> dict:
        """Drive a property from an expression over other properties or bones.

        The tool for corrective shape keys ("bulge the bicep as the elbow bends"),
        for wiring a control bone to anything, and for any relationship that is
        computed rather than keyframed.

        Args:
            object: Object the driven property belongs to.
            data_path: RNA path of the property, relative to `host`. Examples:
                `"location"`, `"rotation_euler"`, `'pose.bones["hand.L"].scale'`,
                and with host=SHAPE_KEYS, `'key_blocks["smile"].value'`.
            expression: Python expression using the variable names below. Angles
                arriving from TRANSFORMS variables are in RADIANS.
            variables: Driver inputs. Each dict has `name` (the identifier your
                expression uses), `type`, and target fields:
                  * `SINGLE_PROP` — read any property. Fields: `id` (datablock
                    name), `id_type` (default `OBJECT`), `data_path`.
                  * `TRANSFORMS` — read an object or bone transform. Fields: `id`
                    (object name), `bone` (optional bone name), `transform_type`
                    (`LOC_X`…`ROT_X`…`SCALE_AVG`), `transform_space`
                    (`WORLD_SPACE`, `TRANSFORM_SPACE`, `LOCAL_SPACE`),
                    `rotation_mode`. Rotations come through in RADIANS.
                  * `ROTATION_DIFF` / `LOC_DIFF` — need TWO targets; pass
                    `targets: [{...}, {...}]` instead of the flat fields.
            index: Component of a vector property (0=X, 1=Y, 2=Z). Omit to drive
                every component with the same expression.
            host: Which datablock owns the property: `OBJECT` (default),
                `DATA` (the mesh/armature), or `SHAPE_KEYS`. Shape key values are
                NOT on the object — they need `host="SHAPE_KEYS"`.
            driver_type: `SCRIPTED` (default, uses `expression`), or `AVERAGE`,
                `SUM`, `MIN`, `MAX`, which ignore the expression.

        Re-running on the same property REPLACES the driver's variables rather
        than accumulating duplicates, so it is safe to iterate. Check `is_valid`
        in the response: false means the expression failed to compile or a target
        is unresolved, and the driven property will just read zero.
        """
        return call(
            "rig.add_driver",
            clean(object=object, data_path=data_path, expression=expression,
                  variables=variables, index=index, host=host,
                  driver_type=driver_type),
            timeout=60.0,
        )

    # -- bone collections ----------------------------------------------
    @mcp.tool()
    def bone_collection_create(
        armature: str,
        name: str = "Bones",
        parent: Optional[str] = None,
        bones: Optional[list[str]] = None,
    ) -> dict:
        """Create a bone collection — the 4.x+ replacement for bone layers.

        Bone collections are named, nestable and unlimited, unlike the 32 fixed
        bone layers they replaced. Use them to hide the deform skeleton while
        animating the controls.

        Args:
            armature: Armature object name.
            name: Collection name.
            parent: Nest under this existing collection. Hiding a parent hides its
                children's bones too.
            bones: Bones to assign immediately.

        A bone may belong to several collections at once.
        """
        return call("rig.bone_collection_create",
                    clean(armature=armature, name=name, parent=parent, bones=bones))

    @mcp.tool()
    def bone_collection_assign(
        armature: str,
        collection: str,
        bones: list[str],
        unassign: bool = False,
    ) -> dict:
        """Add bones to a bone collection, or remove them from it.

        Args:
            armature: Armature object name.
            collection: Target collection name.
            bones: Bone names.
            unassign: True removes the bones instead of adding them.

        Membership is many-to-many, so assigning does not remove a bone from any
        other collection. Per-bone `changed` in the response is false when the
        bone was already in (or already out of) the collection.
        """
        return call(
            "rig.bone_collection_assign",
            clean(armature=armature, collection=collection, bones=bones,
                  unassign=unassign),
        )

    @mcp.tool()
    def bone_collections_list(
        armature: str, include_bones: bool = True, limit: int = 1000
    ) -> dict:
        """List an armature's bone collections, their nesting and membership.

        Reports `is_visible` (this collection's own flag) alongside
        `is_visible_effectively` (which also accounts for hidden ancestors and any
        active solo) — when bones are mysteriously invisible, compare those two.
        Set `include_bones=false` for a compact structural overview of a big rig.
        """
        return call(
            "rig.bone_collections_list",
            clean(armature=armature, include_bones=include_bones, limit=limit),
        )

    @mcp.tool()
    def bone_collection_visibility(
        armature: str,
        collection: str,
        is_visible: Optional[bool] = None,
        is_solo: Optional[bool] = None,
        toggle: bool = False,
    ) -> dict:
        """Show, hide or solo a bone collection.

        Args:
            armature: Armature object name.
            collection: Collection name.
            is_visible: True shows the collection's bones, false hides them.
            is_solo: True hides every collection that is not soloed. While ANY
                collection is soloed, `is_visible` on the others has no effect —
                the response's `is_solo_active` tells you when you are in that
                state, which is the usual reason a "visible" collection stays
                hidden.
            toggle: Flip `is_visible` instead of setting it. Ignored when
                `is_visible` is supplied.

        Hiding a parent collection also hides its children's bones. This affects
        display only; hidden bones still deform and still evaluate constraints.
        """
        return call(
            "rig.bone_collection_set_visibility",
            clean(armature=armature, collection=collection, is_visible=is_visible,
                  is_solo=is_solo, toggle=toggle),
        )
