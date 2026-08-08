"""Object creation, transforms, hierarchy, collections and alignment tools."""

from __future__ import annotations

from typing import Optional, Union

from ..server import call, clean


def register(mcp) -> None:

    # -- creation ------------------------------------------------------
    @mcp.tool()
    def create_primitive(
        kind: str = "cube",
        size: Optional[float] = None,
        location: Optional[list[float]] = None,
        rotation: Optional[list[float]] = None,
        scale: Optional[list[float]] = None,
        name: Optional[str] = None,
        collection: Optional[str] = None,
        vertices: Optional[int] = None,
        segments: Optional[int] = None,
        ring_count: Optional[int] = None,
        subdivisions: Optional[int] = None,
        depth: Optional[float] = None,
        radius2: Optional[float] = None,
        minor_radius: Optional[float] = None,
        x_subdivisions: Optional[int] = None,
        y_subdivisions: Optional[int] = None,
        fill_type: Optional[str] = None,
    ) -> dict:
        """Add a mesh primitive to the scene. The normal way to create geometry.

        Args:
            kind: cube, plane, grid, monkey, uv_sphere, ico_sphere, cylinder,
                cone, circle, torus.
            size: The primitive's primary dimension, in WORLD UNITS (metres by
                default). What it means depends on `kind` — this trips people up:
                  * cube / plane / grid / monkey -> full edge length (a size=2 cube
                    spans -1..+1, which is why Blender's default cube is size 2)
                  * uv_sphere / ico_sphere / cylinder / circle -> RADIUS
                  * cone -> base radius (use `radius2` for the tip radius)
                  * torus -> major radius (use `minor_radius` for the tube)
                Omit to take Blender's own default for that primitive.
            location: [x, y, z] world position of the object origin. Default [0,0,0]
                — NOT the 3D cursor. New objects are always world-aligned here,
                regardless of the user's "Align new objects to" preference.
            rotation: [rx, ry, rz] Euler angles in RADIANS (not degrees).
            scale: [sx, sy, sz] object scale multipliers. Applied to the object
                transform, so it shows up in `scale`, not in the mesh data — call
                `apply_transforms` afterwards if you need the mesh baked at that size.
            name: Rename the new object. Blender enforces unique names, so you may
                get "Cube.001"; the response reports the name you actually got.
            collection: Put the object in this collection (created if missing)
                instead of the currently active one.
            vertices: Radial segment count for cylinder, cone and circle.
            segments: Longitudinal segments for uv_sphere (default 32).
            ring_count: Latitudinal rings for uv_sphere (default 16).
            subdivisions: ico_sphere subdivision level. Grows fast — 5 is ~10k tris,
                7 is ~160k. Stay at or below 4 for blockouts.
            depth: Height along Z for cylinder and cone.
            radius2: Tip radius of a cone. 0 gives a point, >0 gives a frustum.
            minor_radius: Tube radius of a torus.
            x_subdivisions / y_subdivisions: Grid resolution (vertices per side).
            fill_type: Cap style for circle, cylinder and cone: NOTHING (open ends),
                NGON (one n-gon per cap) or TRIFAN (a fan of triangles). Blender
                names this `fill_type` on circle and `end_fill_type` on cylinder and
                cone; pass `fill_type` for all three and it is routed correctly.

        Returns the created object's real name plus its dimensions and poly count.
        For empties, cameras and lights use `add_empty` / `add_camera` / `add_light`.
        """
        return call("objects.create_primitive", clean(
            kind=kind, size=size, location=location, rotation=rotation, scale=scale,
            name=name, collection=collection, vertices=vertices, segments=segments,
            ring_count=ring_count, subdivisions=subdivisions, depth=depth,
            radius2=radius2, minor_radius=minor_radius, x_subdivisions=x_subdivisions,
            y_subdivisions=y_subdivisions, fill_type=fill_type,
        ), timeout=30.0)

    @mcp.tool()
    def add_empty(
        display_type: str = "PLAIN_AXES",
        size: float = 1.0,
        location: Optional[list[float]] = None,
        rotation: Optional[list[float]] = None,
        name: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> dict:
        """Create an Empty — a transform with no geometry, for rigging and pivots.

        Args:
            display_type: PLAIN_AXES, ARROWS, SINGLE_ARROW, CIRCLE, CUBE, SPHERE,
                CONE or IMAGE. Purely a viewport visual; it has no effect on renders
                or on anything parented to the empty.
            size: Viewport display size in WORLD UNITS. Also cosmetic — it does not
                scale children. To scale children, use `transform_object` on the empty.
            location: [x, y, z] world position.
            rotation: [rx, ry, rz] Euler angles in RADIANS.
            name: Object name. May be suffixed if taken.
            collection: Target collection (created if missing).

        Empties have a zero-size bounding box, so `snap_to_ground` and
        `object_bounds` on one are meaningless — the response of `object_bounds`
        flags this as `degenerate`.
        """
        return call("objects.add_empty", clean(
            display_type=display_type, size=size, location=location,
            rotation=rotation, name=name, collection=collection,
        ))

    @mcp.tool()
    def add_camera(
        location: Optional[list[float]] = None,
        rotation: Optional[list[float]] = None,
        lens: Optional[float] = None,
        type: str = "PERSP",
        clip_start: Optional[float] = None,
        clip_end: Optional[float] = None,
        make_active: bool = True,
        name: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> dict:
        """Create a camera, by default making it the scene's render camera.

        Args:
            location: [x, y, z] world position.
            rotation: [rx, ry, rz] Euler angles in RADIANS. A camera with zero
                rotation looks straight DOWN (-Z). To look horizontally along -Y
                from the front, use rotation [1.5708, 0, 0] (90 degrees about X).
            lens: Focal length in MILLIMETRES on a 36mm sensor (default 50).
                Smaller is wider: 18-24 wide, 50 normal, 85+ telephoto.
            type: PERSP, ORTHO, PANO or CUSTOM.
            clip_start / clip_end: Near and far clip in world units. Geometry
                outside this range is invisible — raise clip_end for large scenes.
            make_active: Set `scene.camera`, so `render_frame` and camera-view
                screenshots use this camera. Set false to add a spare camera.
            name: Object name.
            collection: Target collection (created if missing).

        This does not aim the camera at anything. To frame an object, read its
        centre from `object_bounds` and compute the rotation, or add a Track To
        constraint via `execute_python`.
        """
        return call("objects.add_camera", clean(
            location=location, rotation=rotation, lens=lens, type=type,
            clip_start=clip_start, clip_end=clip_end, make_active=make_active,
            name=name, collection=collection,
        ))

    @mcp.tool()
    def add_light(
        type: str = "POINT",
        energy: Optional[float] = None,
        color: Optional[list[float]] = None,
        size: Optional[float] = None,
        location: Optional[list[float]] = None,
        rotation: Optional[list[float]] = None,
        shape: Optional[str] = None,
        size_y: Optional[float] = None,
        spot_size: Optional[float] = None,
        spot_blend: Optional[float] = None,
        name: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> dict:
        """Create a light. Units differ per light type — read the notes below.

        Args:
            type: POINT, SUN, SPOT or AREA.
            energy: Power. For POINT/SPOT/AREA this is WATTS (Blender's default
                point light is 1000 W at ~1 m; 10 W will look black). For SUN it is
                irradiance in W/m^2, where sensible values are around 1-10 — passing
                1000 to a SUN blows the whole render out.
            color: [r, g, b] each 0.0-1.0 (NOT 0-255). Default [1, 1, 1].
            size: The softness/extent control, which maps to a different property
                per type — the response reports which one was written:
                  * POINT / SPOT -> `shadow_soft_size`, emitter radius in WORLD UNITS
                  * AREA -> `size`, panel edge length in WORLD UNITS
                  * SUN -> `angle`, angular diameter in RADIANS (~0.00918 = the real
                    sun; larger gives softer shadows)
                Bigger always means softer shadows.
            location: [x, y, z] world position. Ignored for SUN's brightness — a
                SUN is directional, so only its ROTATION matters, not where it sits.
            rotation: [rx, ry, rz] Euler angles in RADIANS. Lights point down -Z
                at zero rotation, same as cameras.
            shape: AREA only — SQUARE, RECTANGLE, DISK or ELLIPSE.
            size_y: AREA only, second edge length for RECTANGLE/ELLIPSE.
            spot_size: SPOT only, full cone angle in RADIANS (default ~0.785 = 45deg).
            spot_blend: SPOT only, 0.0-1.0 edge softness of the cone.
            name: Object name.
            collection: Target collection (created if missing).
        """
        return call("objects.add_light", clean(
            type=type, energy=energy, color=color, size=size, location=location,
            rotation=rotation, shape=shape, size_y=size_y, spot_size=spot_size,
            spot_blend=spot_blend, name=name, collection=collection,
        ))

    # -- lifecycle -----------------------------------------------------
    @mcp.tool()
    def delete_objects(names: list[str], purge_orphan_data: bool = False) -> dict:
        """Delete objects by name. Undoable via `undo`.

        Args:
            names: Object names. Names that do not exist are reported in `missing`
                rather than raising, so a partially-stale list still works.
            purge_orphan_data: Also delete mesh/curve/light/camera/armature
                datablocks left with zero users. Off by default because a
                zero-user datablock is still recoverable until the file is saved.

        Children of a deleted object are NOT deleted; they simply lose their
        parent. Delete a hierarchy leaf-first, or list every member in `names`.
        """
        return call("objects.delete_objects",
                    clean(names=names, purge_orphan_data=purge_orphan_data))

    @mcp.tool()
    def duplicate_object(
        name: str,
        linked: bool = False,
        new_name: Optional[str] = None,
        location: Optional[list[float]] = None,
        collection: Optional[str] = None,
    ) -> dict:
        """Copy one object, with or without copying its mesh data.

        Args:
            name: Object to copy.
            linked: False (default) gives a full copy with its own mesh — edits to
                the copy do not affect the original. True gives an instance that
                SHARES the mesh datablock, so sculpting or editing either one
                changes both, and `apply_transforms` will refuse to run on it
                unless you pass `isolate_users=true`. Use linked=True for repeated
                props (cheap memory), linked=False when you will edit the copy.
            new_name: Name for the copy; may be suffixed if taken.
            location: [x, y, z] world position for the copy. Omit to leave it
                exactly on top of the original.
            collection: Put the copy here. Omit to mirror the original's collections.

        Modifiers, materials and constraints come along. CHILDREN DO NOT — the
        response lists the source's children under `children_not_duplicated` so you
        can duplicate and re-parent them yourself.
        """
        return call("objects.duplicate_object", clean(
            name=name, linked=linked, new_name=new_name, location=location,
            collection=collection,
        ))

    # -- transforms ----------------------------------------------------
    @mcp.tool()
    def transform_object(
        name: str,
        location: Optional[list[float]] = None,
        rotation: Optional[list[float]] = None,
        scale: Optional[list[float]] = None,
        mode: str = "absolute",
        rotation_mode: Optional[str] = None,
    ) -> dict:
        """Move, rotate or scale an object. The primary way to place things.

        Writes the object's transform directly rather than running a modal
        transform operator, so it is exact and needs no viewport.

        Args:
            name: Object to transform.
            location: [x, y, z] in WORLD UNITS, in the object's PARENT space. For an
                unparented object that is world space; for a parented one it is
                relative to the parent, so it will not match the world position you
                see. Check `matrix_world` from `get_object_info` if unsure.
            rotation: [rx, ry, rz] Euler angles in RADIANS. Degrees will silently
                spin the object many turns — 90 degrees is 1.5708.
            scale: [sx, sy, sz] multipliers. 1.0 is unchanged.
            mode: "absolute" (default) sets each value outright. "delta" treats the
                values as offsets: location and rotation are ADDED, scale is
                MULTIPLIED. Note this is a relative edit of the normal channels —
                it does not touch Blender's separate delta_location /
                delta_rotation_euler / delta_scale properties.
            rotation_mode: Switch the object to XYZ, XZY, YXZ, YZX, ZXY, ZYX,
                QUATERNION or AXIS_ANGLE first. Required if the object is currently
                on QUATERNION or AXIS_ANGLE and you want to pass Euler `rotation` —
                otherwise the call fails with that explanation rather than writing a
                value Blender would ignore. May be passed on its own to change only
                the rotation mode.

        Rotation and scale act around the object's ORIGIN, not its visual centre.
        If something rotates about the wrong point, fix the origin first with
        `set_origin`.
        """
        return call("objects.transform_object", clean(
            name=name, location=location, rotation=rotation, scale=scale,
            mode=mode, rotation_mode=rotation_mode,
        ))

    @mcp.tool()
    def apply_transforms(
        names: list[str],
        location: bool = False,
        rotation: bool = True,
        scale: bool = True,
        isolate_users: bool = False,
    ) -> dict:
        """Bake an object's transform into its mesh, resetting the transform to identity.

        Do this before sculpting, remeshing, or adding modifiers whose result
        depends on real-world size (Bevel, Solidify, Remesh) — a non-uniform object
        scale makes those produce visibly wrong widths. Also do it before exporting
        to engines that ignore object scale.

        Args:
            names: Objects to bake.
            location: Also zero the location, moving the object origin to the world
                origin. Off by default because it usually is not what you want.
            rotation: Bake rotation, leaving rotation_euler at 0. On by default.
            scale: Bake scale, leaving scale at 1,1,1. On by default.
            isolate_users: Blender REFUSES to apply a transform to a mesh shared by
                several objects (linked duplicates), failing with "Cannot apply to a
                multi user". Set this true to give each object its own copy of the
                mesh first. That breaks the instancing link and costs memory, so it
                is off by default.

        Applying scale bakes it into the vertices, so the object looks identical but
        `dimensions` now come from the mesh. Children keep their world positions.
        """
        return call("objects.apply_transforms", clean(
            names=names, location=location, rotation=rotation, scale=scale,
            isolate_users=isolate_users,
        ), timeout=60.0)

    @mcp.tool()
    def set_origin(names: list[str], type: str = "ORIGIN_GEOMETRY",
                   center: str = "MEDIAN") -> dict:
        """Move an object's origin — the pivot that rotation, scale and parenting use.

        Args:
            names: Objects to act on.
            type: What to move:
                  * ORIGIN_GEOMETRY - move the ORIGIN to the geometry (most common;
                    geometry stays put in world space)
                  * GEOMETRY_ORIGIN - the inverse: move the GEOMETRY to the origin,
                    so the object visibly jumps. Easy to confuse with the above.
                  * ORIGIN_CURSOR - move the origin to the 3D cursor; set the cursor
                    first with `snap_cursor_to`
                  * ORIGIN_CENTER_OF_MASS - surface-area-weighted centroid
                  * ORIGIN_CENTER_OF_VOLUME - enclosed-volume centroid; needs a
                    watertight mesh to mean anything
            center: Only affects ORIGIN_GEOMETRY. MEDIAN averages vertex positions,
                so it is pulled toward dense areas. BOUNDS uses the bounding-box
                centre, which is what you want for "centre this on its shape".

        A classic use: `set_origin(names, "ORIGIN_GEOMETRY", "BOUNDS")` then
        `snap_to_ground` to seat a prop on the floor.
        """
        return call("objects.set_origin",
                    clean(names=names, type=type, center=center), timeout=60.0)

    @mcp.tool()
    def snap_to_ground(
        names: list[str],
        ground_z: float = 0.0,
        together: bool = False,
        use_evaluated: bool = False,
    ) -> dict:
        """Drop objects straight down so they rest on a horizontal plane.

        Moves each object along Z only until the bottom of its world-space bounding
        box touches `ground_z`. X and Y are untouched, and the object is never
        rotated, so a tilted object rests on its lowest corner.

        Args:
            names: Objects to seat.
            ground_z: World Z of the floor plane. Default 0.0.
            together: False (default) seats each object individually. True computes
                one shared offset from the lowest object and moves the whole set by
                it, preserving their relative heights — use this for an assembled
                group that must not come apart.
            use_evaluated: Measure the bounds of the MODIFIER RESULT rather than the
                base mesh. Off by default because it is slower. Turn it on when a
                Subdivision, Displace or Solidify modifier changes the silhouette,
                otherwise the object will float or sink by the modifier's difference.

        Works on any object type, but an Empty has a zero-size bounding box so it
        will simply be moved to `ground_z`.
        """
        return call("objects.snap_to_ground", clean(
            names=names, ground_z=ground_z, together=together,
            use_evaluated=use_evaluated,
        ), timeout=60.0)

    @mcp.tool()
    def align_objects(
        names: list[str],
        axis: Union[str, list[str]] = "Z",
        mode: str = "CENTERS",
        relative_to: str = "SELECTION",
        active: Optional[str] = None,
        bb_quality: bool = True,
    ) -> dict:
        """Line objects up on one or more axes.

        Args:
            names: At least two objects.
            axis: "X", "Y", "Z", or a list like ["X", "Y"] to align on several at once.
            mode: Which part of each object gets aligned:
                  * CENTERS (default) - bounding-box centres line up
                  * NEGATIVE - the low sides line up (e.g. all left edges flush)
                  * POSITIVE - the high sides line up
            relative_to: What they align TO:
                  * SELECTION (default) - the combined bounds of all the objects
                  * ACTIVE - the `active` object below stays put and the rest come
                    to it
                  * CURSOR - the 3D cursor; set it with `snap_cursor_to` first
                  * SCENE_ORIGIN - world (0, 0, 0)
            active: Which object is the anchor when relative_to="ACTIVE". Defaults
                to the first name.
            bb_quality: Use the accurate (slower) bounding box. Turn off only for
                very heavy meshes.

        These friendly names map onto Blender's opaque OPT_1..OPT_4 enums for you,
        so do not pass OPT_* here.

        This moves objects. To only READ their positions, use `object_bounds`.
        """
        return call("objects.align_objects", clean(
            names=names, axis=axis, mode=mode, relative_to=relative_to,
            active=active, bb_quality=bb_quality,
        ), timeout=60.0)

    @mcp.tool()
    def snap_cursor_to(
        target: str = "WORLD_ORIGIN",
        location: Optional[list[float]] = None,
        object: Optional[str] = None,
        use_bounds: bool = False,
    ) -> dict:
        """Move the 3D cursor, which several other operations pivot around.

        The cursor is what `set_origin(type="ORIGIN_CURSOR")` and
        `align_objects(relative_to="CURSOR")` read, so this is usually a setup step
        rather than an end in itself.

        Args:
            target: Where to put it:
                  * WORLD_ORIGIN (default) - (0, 0, 0)
                  * LOCATION - the explicit `location` below
                  * OBJECT - the named object
                  * SELECTED - the average of the currently selected objects; call
                    `select_objects` first or this fails
            location: [x, y, z] in WORLD UNITS, required when target="LOCATION".
            object: Object name, required when target="OBJECT".
            use_bounds: For OBJECT and SELECTED, use the bounding-box CENTRE instead
                of the object ORIGIN. These differ whenever the origin is off-centre,
                which is exactly when you tend to care.

        Unlike Blender's Shift+S menu this needs no viewport and works headless.
        """
        return call("objects.snap_cursor_to", clean(
            target=target, location=location, object=object, use_bounds=use_bounds,
        ))

    @mcp.tool()
    def object_bounds(name: str, use_evaluated: bool = False) -> dict:
        """Measure one object: bounding box in local AND world space, centre, size.

        Read-only. Use this before placing something — it answers "how tall is it",
        "where is its actual middle" and "is its origin off-centre" in one call.

        Args:
            name: Object to measure.
            use_evaluated: Measure the MODIFIER RESULT instead of the base mesh.
                Off by default (faster). A Subdivision Surface shrinks a cube by
                about 16%, so the two answers genuinely differ.

        Returns `local` and `world` blocks, each with min, max, center, size and the
        8 corners. `local` is before the object's transform; `world` is after it, so
        `world.size` accounts for scale and rotation while `dimensions` (also
        returned) is the axis-aligned local size times scale.

        Compare `world.center` against `location` to see how far the origin sits
        from the visual centre. Objects with no geometry (Empty, Camera, Light)
        report `degenerate: true` and all-zero bounds.
        """
        return call("objects.object_bounds", clean(name=name, use_evaluated=use_evaluated))

    # -- hierarchy -----------------------------------------------------
    @mcp.tool()
    def parent_objects(
        child: Union[str, list[str]],
        parent: str,
        type: str = "OBJECT",
        keep_transform: bool = True,
        bone: Optional[str] = None,
        xmirror: bool = False,
    ) -> dict:
        """Parent objects to another object, including all the armature-deform variants.

        Args:
            child: One object name or a list of them.
            parent: The object they become children of.
            type: The relationship:
                  * OBJECT - plain transform parenting
                  * ARMATURE - add an Armature modifier but create NO vertex groups,
                    so nothing deforms until you make groups yourself
                  * ARMATURE_NAME - add the modifier and create EMPTY vertex groups
                    named after the bones, ready for you to weight-paint
                  * ARMATURE_AUTO - add the modifier and compute automatic weights
                    from bone heat. The usual choice for a first bind. Can fail on
                    non-manifold or self-intersecting meshes with "Bone Heat
                    Weighting: failed to find solution" — clean the mesh or use
                    ARMATURE_NAME and paint by hand.
                  * ARMATURE_ENVELOPE - deform from bone envelope volumes, no
                    weights. Fast and crude.
                  * BONE - rigid-parent the whole object to ONE bone (props, weapons)
                  * BONE_RELATIVE - as BONE, but offsets stay relative to the bone
                  * Also available: CURVE, FOLLOW, PATH_CONST, LATTICE, VERTEX,
                    VERTEX_TRI
            keep_transform: True (default) preserves each child's WORLD position by
                compensating in its parent inverse matrix — the child does not jump.
                False lets the child's local transform be reinterpreted relative to
                the parent, which usually makes it jump.
            bone: Required for BONE / BONE_RELATIVE unless the armature already has
                an active bone. Names the bone to attach to.
            xmirror: For ARMATURE_AUTO, mirror the computed weights across X for a
                symmetric character.

        The ARMATURE* and BONE types require `parent` to actually be an armature;
        the call fails with that message rather than doing something odd.
        """
        return call("objects.parent_objects", clean(
            child=child, parent=parent, type=type, keep_transform=keep_transform,
            bone=bone, xmirror=xmirror,
        ), timeout=120.0)

    @mcp.tool()
    def join_objects(names: list[str], target: Optional[str] = None) -> dict:
        """Merge several objects into one. Destructive — the others are consumed.

        Args:
            names: Objects to merge. Every one must be the same type as the target
                (all meshes, or all curves, etc.); the call fails listing the odd
                ones out rather than half-merging.
            target: The survivor, which keeps its name, origin, transform and
                modifiers. Defaults to the first name. The others' geometry is
                brought into its local space and they are deleted.

        Materials from all inputs are combined into the target's material slots, and
        vertex groups are merged by name. `separate` is the inverse.
        """
        return call("objects.join_objects", clean(names=names, target=target),
                    timeout=120.0)

    @mcp.tool()
    def separate(name: str, by: str = "LOOSE") -> dict:
        """Split one mesh into several objects. The inverse of `join_objects`.

        Args:
            name: The mesh object to split. Must be a MESH.
            by: How to split:
                  * LOOSE (default) - one object per disconnected island. The usual
                    way to break up an imported or joined blob.
                  * MATERIAL - one object per material slot in use.
                  * SELECTED - split off whatever vertices are currently selected in
                    the mesh. That selection lives in the mesh data, so you must
                    have made it first (in the mesh tools or the UI); with nothing
                    selected the call fails with "Nothing selected". Careful: a
                    freshly created primitive has ALL of its vertices selected, so
                    SELECTED on one moves the entire mesh out and leaves an empty
                    source object behind. Check `source_vertices` in the response.

        New objects inherit the source's transform and modifiers and are named
        "<source>.001", "<source>.002", ... The response lists exactly what was
        created. The source keeps whatever geometry was left behind.
        """
        return call("objects.separate", clean(name=name, by=by), timeout=120.0)

    # -- selection -----------------------------------------------------
    @mcp.tool()
    def select_objects(
        names: list[str],
        mode: str = "SET",
        deselect_others: Optional[bool] = None,
        active: Optional[str] = None,
    ) -> dict:
        """Set the object selection and the active object.

        Most tools here take object names directly, so you rarely need this. It
        matters for the few things that read the current selection — `snap_cursor_to`
        with target="SELECTED", and any `execute_python` that uses selection.

        Args:
            names: Objects to act on.
            mode: SET (select exactly these), ADD (add to the selection), REMOVE
                (deselect these).
            deselect_others: Override the default, which is True for SET and False
                for ADD and REMOVE.
            active: Which object becomes active. Defaults to the last name for
                SET/ADD. The active object is distinct from the selection: mode
                changes and many operators act on the ACTIVE one.

        Hidden objects cannot be selected — the call fails naming the object rather
        than silently doing nothing, which is Blender's own behaviour.
        """
        return call("objects.select_objects", clean(
            names=names, mode=mode, deselect_others=deselect_others, active=active,
        ))

    @mcp.tool()
    def set_active(name: str, select: bool = True) -> dict:
        """Make one object the active object.

        The active object is what `set_mode` switches, what sculpt and edit tools
        operate on, and what `join_objects` merges into. It is separate from the
        selection: an object can be active without being selected.

        Args:
            name: Object to activate. Must be in the current view layer.
            select: Also select it (default true). Set false to make it active
                without disturbing an existing selection.
        """
        return call("objects.set_active", clean(name=name, select=select))

    # -- collections ---------------------------------------------------
    @mcp.tool()
    def collection_create(
        name: str,
        parent: Optional[str] = None,
        allow_duplicate_name: bool = False,
    ) -> dict:
        """Create a collection, optionally nested inside an existing one.

        Args:
            name: Collection name.
            parent: Nest inside this collection. Omit to sit at the top of the
                scene. The parent must already exist.
            allow_duplicate_name: By default the call fails if the name is taken,
                because two collections called "Props" are a debugging nightmare.
                Set true to create a suffixed second one anyway.

        Collections are the scene's organisational units and can also be linked into
        several parents at once. To fill one, use `collection_move` or
        `collection_link`.
        """
        return call("objects.collection_create", clean(
            name=name, parent=parent, allow_duplicate_name=allow_duplicate_name,
        ))

    @mcp.tool()
    def collection_link(names: list[str], collection: str, create: bool = True) -> dict:
        """Add objects to a collection WITHOUT removing them from their current ones.

        This is Blender's Shift+M "Link to Collection". An object can belong to any
        number of collections at once, which is how you build overlapping sets (all
        the props, and separately everything in room 3). Use `collection_move` when
        you want an object to live in exactly one place.

        Args:
            names: Objects to link.
            collection: Target collection.
            create: Create the collection at the scene root if it does not exist
                (default true).
        """
        return call("objects.collection_link",
                    clean(names=names, collection=collection, create=create))

    @mcp.tool()
    def collection_move(names: list[str], collection: str, create: bool = True) -> dict:
        """Move objects into a collection, unlinking them from every other one.

        This is Blender's M "Move to Collection": afterwards each object belongs to
        exactly one collection. Use `collection_link` to add membership instead of
        replacing it.

        Args:
            names: Objects to move.
            collection: Destination collection.
            create: Create it at the scene root if missing (default true).

        The response records each object's previous membership under `was_in`, so
        you can put things back if this was not what you meant.
        """
        return call("objects.collection_move",
                    clean(names=names, collection=collection, create=create))

    @mcp.tool()
    def collection_list(limit: int = 1000, names_per_collection: int = 50) -> dict:
        """The scene's collection tree, with object counts and nesting depth.

        Use this before `collection_move` / `collection_link` so you target a
        collection that exists and spell it the way Blender does.

        Args:
            limit: Maximum collections returned; `count` is always exact and
                `truncated` says whether anything was cut.
            names_per_collection: Cap on the object names listed per collection.
                Counts (`objects`, `objects_recursive`) are always exact.

        `objects` counts direct members; `objects_recursive` includes nested
        collections. `unlinked_collections` lists collections that exist in the file
        but are not part of this scene's tree — objects in them will not render.
        """
        return call("objects.collection_list",
                    clean(limit=limit, names_per_collection=names_per_collection))
