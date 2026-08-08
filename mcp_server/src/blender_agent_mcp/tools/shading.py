"""Material, shader node graph, viewport shading and texture baking tools."""

from __future__ import annotations

from typing import Any, Optional, Union

from ..server import call, clean, png_image

SocketRef = Union[str, int]


def register(mcp) -> None:

    # -- materials -----------------------------------------------------
    @mcp.tool()
    def create_material(
        name: str,
        base_color: Optional[list[float]] = None,
        metallic: Optional[float] = None,
        roughness: Optional[float] = None,
        ior: Optional[float] = None,
        alpha: Optional[float] = None,
        emission_color: Optional[list[float]] = None,
        emission_strength: Optional[float] = None,
        normal_map: Optional[str] = None,
    ) -> dict:
        """Create a Principled BSDF material from plain PBR values.

        The fastest way to get a usable material. It makes the datablock only —
        follow with `assign_material` to put it on an object.

        Args:
            name: Material name. Blender appends `.001` if it is taken; the name
                actually used comes back as `material`.
            base_color: [R, G, B, A], each **0-1** (not 0-255). Three components
                are accepted and padded with A=1.
            metallic: 0-1. Use 0 or 1; values in between are physically meaningless.
            roughness: 0-1. 0 is a mirror, 1 is chalk.
            ior: Index of refraction, ~1.45 glass, ~1.33 water. Only affects
                specular/transmission response.
            alpha: 0-1 opacity. Anything below 1 also flips the material to
                blended transparency, otherwise EEVEE renders it fully opaque.
            emission_color: [R, G, B, A] 0-1. Emission is off until
                `emission_strength` is also above 0.
            emission_strength: Radiance multiplier, 0 = no glow. Default is 0, so
                setting only `emission_color` does nothing visible.
            normal_map: Path to a tangent-space normal map image. Builds
                Image Texture (forced to **Non-Color**) → Normal Map → Normal for
                you; passing an sRGB-tagged normal map is the classic mistake this
                avoids.

        Socket names are the Blender 4.x/5.x ones: `Emission Color` (was
        `Emission`), `Specular IOR Level` (was `Specular`), `Subsurface Weight`
        (was `Subsurface`). Any value whose socket is absent in this build is
        reported in `missing_sockets` instead of failing the whole call, and
        `available_inputs` lists every socket you could set with `set_node_prop`.
        """
        return call("shading.create_material", clean(
            name=name, base_color=base_color, metallic=metallic, roughness=roughness,
            ior=ior, alpha=alpha, emission_color=emission_color,
            emission_strength=emission_strength, normal_map=normal_map,
        ), timeout=30.0)

    @mcp.tool()
    def assign_material(
        object: str,
        material: str,
        slot: Optional[int] = None,
        to_selected_faces: bool = False,
    ) -> dict:
        """Put an existing material into an object's material slot.

        Args:
            object: Object name. Must be able to hold materials (MESH, CURVE,
                SURFACE, META, FONT, VOLUME); an EMPTY or CAMERA raises.
            material: Material name — it must already exist. Use
                `create_material` first, or `material_list` to find the real name.
            slot: Zero-based slot index. Slots are created (as empty) up to this
                index if the object has fewer. Omit to let the tool choose, see below.
            to_selected_faces: Assign only to the currently selected faces rather
                than the whole object.

        Slot resolution when `slot` is omitted:
        * the material is already in a slot → that slot is reused;
        * `to_selected_faces=False` and the object has slots → the **active slot is
          overwritten**, which is what makes "give this object that material" work;
        * otherwise a new slot is appended.

        Face selection semantics: the selection is read from the mesh, so make the
        selection in Edit Mode first (`set_mode(mode="EDIT")` plus your selection
        tools). Both Edit and Object mode work — in Edit Mode the live bmesh is
        used, in Object Mode the last-flushed selection. If nothing is selected the
        call raises rather than silently doing nothing.

        Assigning to a slot does not repaint faces that point at other slots, so on
        a multi-material mesh check `slots` in the result to see the real layout.
        """
        return call("shading.assign_material", clean(
            object=object, material=material, slot=slot,
            to_selected_faces=to_selected_faces,
        ))

    @mcp.tool()
    def material_list(object: Optional[str] = None, limit: int = 1000) -> dict:
        """List materials, either every one in the file or one object's slots.

        Use this before `assign_material` or any node tool — they address
        materials by exact name and this is how you learn those names.

        Args:
            object: Restrict to this object's slots, in slot order. Empty slots
                appear as `{"slot": n, "name": null}`. Omit for the whole file.
            limit: Maximum entries returned; `count` is always exact and
                `truncated` says whether anything was cut.

        Each entry carries `users` (0 means nothing references it and it will be
        dropped on file reload unless `fake_user` is set) and a `principled`
        summary of base colour, metallic, roughness and alpha.
        """
        return call("shading.material_list", clean(object=object, limit=limit))

    # -- node graph ----------------------------------------------------
    @mcp.tool()
    def get_node_graph(material: str, limit: int = 1000) -> dict:
        """Read a material's whole shader node tree: nodes, sockets and links.

        Call this before editing nodes. Node *names* (`Principled BSDF`,
        `Image Texture.001`) are what every other node tool addresses, and they
        are not predictable — Blender auto-numbers duplicates.

        Args:
            material: Material name.
            limit: Cap on nodes and on links independently. `node_count` /
                `link_count` are exact; `truncated` says whether either list was cut.

        Each node reports `agent_id` (stable caller-owned identity, when set),
        `bl_idname` (what `add_node` takes), `type` (the RNA enum), `location` in
        node-editor units, and every input socket with its `index`, `type`,
        `is_linked` and `default_value`. A linked socket's `default_value` is
        ignored at render time — the link wins.
        """
        return call("shading.get_node_graph", clean(material=material, limit=limit),
                    timeout=30.0)

    @mcp.tool()
    def build_node_graph(
        material: str,
        nodes: list[dict[str, Any]],
        links: Optional[list[dict[str, Any]]] = None,
        clear_existing: bool = False,
        remove_unlisted: bool = False,
        active: Optional[str] = None,
    ) -> dict:
        """Build or update a complete procedural shader in one retry-safe call.

        This is the high-bandwidth alternative to dozens of `add_node`,
        `set_node_prop` and `link_nodes` round trips. Every node has a caller-owned
        `id`, stored on the Blender node: repeating the same call updates those
        nodes and reuses links instead of creating `.001` duplicates.

        Args:
            material: Existing material name.
            nodes: Node specifications. Each requires `id` and Blender `type`.
                Optional fields are `name`, `label`, `[x,y]` `location`, `mute`,
                `props`, `image`, `parent`, and `color_ramp`. `props` uses the same
                property-or-input rules as `set_node_prop`. `image` is an image
                datablock name for Image Texture nodes. `parent` is a Frame node id.
                A `color_ramp` has `elements: [{position, color}, ...]` plus optional
                `interpolation`, `color_mode`, and `hue_interpolation`.
            links: Link specs with `from`, `from_socket`, `to`, and `to_socket`.
                Endpoints prefer stable ids but may name unmanaged existing nodes.
                Sockets accept names or integer indices. A destination's old link
                is replaced by default; set that link's `replace=false` to refuse.
            clear_existing: Explicitly delete every existing node first. Default
                false adopts/updates matching ids or names and preserves user work.
            remove_unlisted: Delete previously agent-managed nodes whose ids are
                absent from this call. Unmanaged/user nodes are never removed.
            active: Stable id or node name to make active (important for baking).

        Example node: `{"id":"noise","type":"ShaderNodeTexNoise",
        "location":[-600,100],"props":{"Scale":5,"Detail":3}}`.
        Example link: `{"from":"noise","from_socket":"Fac","to":"ramp",
        "to_socket":"Fac"}`.

        Existing default nodes can be adopted by giving `name="Principled BSDF"`
        or `name="Material Output"`. A type conflict fails before edits. The whole
        bridge call is one Blender undo step, and the result maps stable ids to the
        actual Blender names plus created/reused/replaced counts.
        """
        return call("shading.build_node_graph", clean(
            material=material, nodes=nodes, links=links,
            clear_existing=clear_existing, remove_unlisted=remove_unlisted,
            active=active,
        ), timeout=60.0)

    @mcp.tool()
    def add_node(
        material: str,
        type: str,
        props: Optional[dict[str, Any]] = None,
        location: Optional[list[float]] = None,
        label: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> dict:
        """Add one shader node to a material, optionally configured in the same call.

        The node is created disconnected — wire it up with `link_nodes`.

        Args:
            material: Material name.
            type: The node's `bl_idname`, e.g. `ShaderNodeTexImage`,
                `ShaderNodeTexNoise`, `ShaderNodeMapping`, `ShaderNodeMixShader`,
                `ShaderNodeBump`, `ShaderNodeColorRamp`. Not the UI label, and not
                the short `type` enum. A wrong name raises with close matches from
                the live build, so a near-miss self-corrects in one retry.
            props: Node settings applied after creation. Each key is tried first
                as a node **attribute** (`noise_dimensions`, `projection`,
                `interpolation`, `image`) and then as an **input socket**
                (`Scale`, `Detail`, `Roughness`). See `set_node_prop` for value
                rules. A pointer attribute like `image` accepts a datablock name.
            location: [x, y] in node-editor units, +x right and +y up. Purely
                cosmetic. Omit and the node lands at the origin, which usually
                means on top of another node.
            label: Text shown on the node header. Handy for finding it again.
            agent_id: Optional caller-stable identity. Once set, every node command
                accepts this id in place of Blender's auto-numbered display name.

        Returns the assigned `node` name — pass that to `link_nodes` and
        `set_node_prop`, since it may differ from the default if one already existed.
        """
        return call("shading.add_node", clean(
            material=material, type=type, props=props, location=location, label=label,
            agent_id=agent_id,
        ))

    @mcp.tool()
    def link_nodes(
        material: str,
        from_node: str,
        from_socket: SocketRef,
        to_node: str,
        to_socket: SocketRef,
    ) -> dict:
        """Connect a node output to a node input inside one material.

        Args:
            material: Material name.
            from_node: Name of the source node (from `get_node_graph`).
            from_socket: Output socket of the source, by **name** (`Color`, `BSDF`,
                `Fac`) or zero-based **index**.
            to_node: Name of the destination node.
            to_socket: Input socket of the destination, by name (`Base Color`,
                `Roughness`, `Surface`) or zero-based index.

        Use indices when names repeat: `ShaderNodeMix` has four `A`/`B` pairs and
        four `Result` outputs, one set per data type, so only the index is
        unambiguous. `get_node_graph` prints the index next to every socket.

        Linking into an already-linked input replaces the old link — inputs take
        one connection, outputs fan out to many. Check `valid` in the result: a
        type-mismatched link is created but flagged invalid and renders as an error.
        """
        return call("shading.link_nodes", {
            "material": material, "from_node": from_node, "from_socket": from_socket,
            "to_node": to_node, "to_socket": to_socket,
        })

    @mcp.tool()
    def set_node_prop(material: str, node: str, prop: str, value: Any) -> dict:
        """Change one setting on a node — either a node attribute or an input socket.

        This is the workhorse for tweaking an existing graph. `prop` is resolved in
        two steps: a writable node attribute first, then an input socket's
        `default_value`. The result says which applied via `target`
        (`"property"` or `"socket"`).

        Args:
            material: Material name.
            node: Node name from `get_node_graph`.
            prop: Attribute name (`interpolation`, `projection`, `extension`,
                `blend_type`, `mute`, `label`, `location`, `image`) or input socket
                name/index (`Base Color`, `Roughness`, `Scale`, or `2`).
            value: Number for a VALUE socket; `[R, G, B, A]` 0-1 for RGBA (three
                components are padded with A=1); `[x, y, z]` for VECTOR; the enum
                identifier string for an enum attribute; a datablock name string
                for a pointer attribute such as `image`.

        Gotchas: setting a socket that already has a link changes a value nothing
        reads — `linked: true` in the result is your warning. Principled sockets use
        the 4.x/5.x names, so `Emission` and `Specular` raise with the new name in
        the message. Shader-type sockets have no value at all; link into them instead.
        """
        return call("shading.set_node_prop",
                    {"material": material, "node": node, "prop": prop, "value": value})

    @mcp.tool()
    def remove_node(material: str, node: str) -> dict:
        """Delete a node from a material's shader tree.

        Every link touching the node goes with it; nothing is re-routed, so a node
        removed from the middle of a chain leaves a gap you must re-link.

        Args:
            material: Material name.
            node: Node name from `get_node_graph`.

        Deleting the `Material Output` node is allowed and leaves the material
        rendering black — the result carries a `warning` and the remaining
        `material_output_nodes` when that happens.
        """
        return call("shading.remove_node", {"material": material, "node": node})

    @mcp.tool()
    def load_image_texture(
        material: str,
        path: str,
        colorspace: Optional[str] = None,
        hook_to: Optional[str] = None,
        image_name: Optional[str] = None,
    ) -> dict:
        """Load an image file into a material as an Image Texture node, optionally wired up.

        Args:
            material: Material name.
            path: Absolute path, or a `//relative` Blender path, to a PNG/JPG/EXR/
                TIFF on the machine running Blender. A missing file raises.
            colorspace: Override the colour space, normally `sRGB` for colour maps
                and `Non-Color` for data maps. Omit and it is chosen from the
                destination socket, which is the right answer nearly always.
            hook_to: Principled BSDF input to connect to — `Base Color`,
                `Roughness`, `Metallic`, `Normal`, `Alpha`, `Emission Color`, or any
                other socket name. Omit to drop the node in unconnected and wire it
                yourself with `link_nodes`.
            image_name: Rename the image datablock. Omit to keep the filename.

        `hook_to="Normal"` also inserts a **Normal Map** node between the texture
        and the BSDF, because a raw normal image plugged straight into Normal is
        wrong and looks subtly bad rather than obviously broken.

        Colour-space choice matters: hooking to a non-colour socket forces
        `Non-Color`, so roughness/metallic/normal maps are not gamma-decoded. Pass
        `colorspace` explicitly only when you know the file disagrees with its use.

        Requires a Principled BSDF in the material when `hook_to` is set. The
        result's `created_nodes` lists every node made, and `image_node` is the
        Image Texture to address later.
        """
        return call("shading.load_image_texture", clean(
            material=material, path=path, colorspace=colorspace,
            hook_to=hook_to, image_name=image_name,
        ), timeout=60.0)

    @mcp.tool()
    def create_texture_image(
        name: str,
        width: int = 1024,
        height: int = 1024,
        color: Optional[list[float]] = None,
        generated_type: str = "BLANK",
        colorspace: Optional[str] = None,
        alpha: bool = True,
        float_buffer: bool = False,
        is_data: bool = False,
        pixels: Optional[list[float]] = None,
        reuse_existing: bool = True,
    ) -> dict:
        """Create an internal texture, mask, bake target, grid, or small pixel image.

        Args:
            name: Stable image datablock name. A matching image is safely reused on
                retry; a size/precision mismatch fails instead of breaking users.
            width: Pixel width, 1-16384. Total pixels are capped at 67,108,864.
            height: Pixel height, 1-16384.
            color: Background RGBA 0-1 (RGB is padded with alpha 1).
            generated_type: `BLANK`, `UV_GRID`, or `COLOR_GRID`.
            colorspace: Usually `sRGB` for visible colour or `Non-Color` for masks,
                roughness, metallic, normals and other numeric data. Defaults from
                `is_data`.
            alpha: Allocate alpha storage.
            float_buffer: Use a 32-bit float/HDR buffer. Costs much more memory.
            is_data: Mark numeric data rather than display colour.
            pixels: Optional flat RGBA values in bottom-to-top row order, exactly
                `width*height*4` numbers. Best for small masks, pixel art, sprite
                sheets and generated lookup textures; use files for large images.
            reuse_existing: Retry-safe default. False refuses if `name` exists.

        Generated images are already stored inside the blend. Save one externally
        with `save_image`, then reference its datablock from `build_node_graph` via
        a node's `image` field.
        """
        return call("shading.create_texture_image", clean(
            name=name, width=width, height=height, color=color,
            generated_type=generated_type, colorspace=colorspace, alpha=alpha,
            float_buffer=float_buffer, is_data=is_data, pixels=pixels,
            reuse_existing=reuse_existing,
        ), timeout=60.0)

    @mcp.tool()
    def list_images(limit: int = 1000) -> dict:
        """List image datablocks with size, source, path, colorspace and packed state.

        Use this before assigning a node's `image` property. `source="GENERATED"`
        means the image lives inside Blender; `source="FILE"` means `filepath`
        identifies an external texture. `is_dirty` warns about unsaved pixel edits.
        """
        return call("shading.list_images", {"limit": limit}, timeout=30.0)

    @mcp.tool()
    def save_image(
        image: str,
        path: str,
        file_format: Optional[str] = None,
        pack: bool = False,
    ) -> dict:
        """Save an image datablock to disk as a reusable texture file.

        Args:
            image: Image name from `list_images`.
            path: Absolute or Blender `//relative` destination. Parent directories
                are created. Include the intended extension.
            file_format: Optional Blender format such as `PNG`, `JPEG`, `OPEN_EXR`,
                `TIFF`, `TARGA`, `HDR` or `WEBP`. Omit to use the image/current
                extension-derived format.
            pack: Also embed the saved external file into the blend. Generated
                images are already internal and do not need this.

        Returns the resolved path and byte count, so an agent can verify the file
        was actually written instead of assuming Blender saved it.
        """
        return call("shading.save_image", clean(
            image=image, path=path, file_format=file_format, pack=pack,
        ), timeout=60.0)

    # -- viewport ------------------------------------------------------
    @mcp.tool()
    def set_viewport_shading(
        type: Optional[str] = None,
        color_type: Optional[str] = None,
        studio_light: Optional[str] = None,
    ) -> dict:
        """Switch how the 3D viewport draws. GUI Blender only — fails under `--background`.

        Affects what you see in `viewport_screenshot`, so set this before taking one
        to check a material.

        Args:
            type: `WIREFRAME`, `SOLID`, `MATERIAL` or `RENDERED`. `SOLID` is fast
                and shows form; `MATERIAL` previews materials with EEVEE; `RENDERED`
                uses the scene's real engine and lights and is much slower.
            color_type: What tints surfaces in **SOLID shading only** — `MATERIAL`,
                `OBJECT`, `RANDOM`, `VERTEX`, `TEXTURE` or `SINGLE`. `RANDOM` is the
                quickest way to tell adjacent objects apart in a screenshot. Ignored
                in the other shading modes.
            studio_light: Name of the studio HDRI / matcap, e.g. `Default`. Valid
                names depend on the shading type and on installed studio lights, so
                read `available_studio_lights` from the result rather than guessing.

        Only the arguments you pass are changed; the rest keep their current value.
        The result reports the resulting state plus the live valid values for all
        three, so one call tells you what this build supports.

        This changes the viewport, not the render — it does not affect
        `render_frame`. `viewport_screenshot(shading_mode=...)` sets shading for a
        single shot and restores it; use this tool for a persistent change.
        """
        return call("shading.set_viewport_shading", clean(
            type=type, color_type=color_type, studio_light=studio_light,
        ))

    # -- baking --------------------------------------------------------
    @mcp.tool()
    def bake(
        object: str,
        type: str = "AO",
        image_name: Optional[str] = None,
        size: int = 1024,
        margin: int = 16,
        samples: int = 32,
        filepath: Optional[str] = None,
        return_image: bool = True,
        normal_space: Optional[str] = None,
        pass_filter: Optional[list[str]] = None,
        use_clear: bool = True,
        timeout: float = 300.0,
    ) -> Any:
        """Bake surface detail into a texture with Cycles. Slow — expect tens of seconds.

        Cycles-only: EEVEE cannot bake, so this switches the scene to Cycles (with
        Metal GPU compute on macOS when available) and restores the previous engine
        afterwards. Selection, active object and mode are restored too.

        Args:
            object: MESH object to bake. Anything else raises.
            type: `AO` (ambient occlusion, cavity darkening), `NORMAL` (tangent-space
                surface detail), `DIFFUSE` (albedo — lighting is excluded by
                default), `COMBINED` (the full shaded result, lighting included).
                `SHADOW`, `POSITION`, `UV`, `ROUGHNESS`, `EMIT`, `ENVIRONMENT`,
                `GLOSSY` and `TRANSMISSION` also work.
            image_name: Name for the baked image datablock. Defaults to
                `<object>_<type>`. An existing image of that name is reused if its
                resolution matches and replaced if it does not.
            size: Square resolution in pixels (1024 = 1024x1024). Cost scales with
                the square, so probe at 256 before committing to 2048.
            margin: Pixels of bleed painted outside each UV island, to stop seams
                showing when the texture is filtered. 16 suits 1024; scale it with
                `size`.
            samples: Cycles samples per pixel. 4-8 is enough for NORMAL, 32-128 for
                AO, more for COMBINED. This is the main time knob.
            filepath: Save the result here as PNG (the directory is created).
                Omit to keep the image inside the .blend only.
            return_image: Return the baked PNG as a viewable image. Set False for
                large bakes — a 2048 PNG costs a lot of context.
            normal_space: `TANGENT` (default, the portable choice for game assets)
                or `OBJECT`. NORMAL bakes only.
            pass_filter: For DIFFUSE/GLOSSY/TRANSMISSION, which contributions to
                include: `COLOR`, `DIRECT`, `INDIRECT`. Defaults to `["COLOR"]`,
                i.e. flat albedo with no lighting baked in.
            use_clear: Wipe the image before baking. Set False to bake several
                objects into one shared texture.
            timeout: Seconds to wait. Raise it for big sizes or high sample counts.

        Setup done for you: a UV map is created if the mesh has none (a default
        layout, **not** a real unwrap — unwrap properly first if the result looks
        smeared, and check `uv_auto_created` in the response); a material is created
        if the mesh has none; and an Image Texture node pointing at the target is
        added, selected and made active in every material, which is what Cycles
        actually bakes into. Those nodes are left in place so you can re-bake —
        `bake_target_nodes` names them, and `empty_material_slots` flags slots with
        no material, whose faces bake nothing.

        A black or blank result almost always means overlapping/absent UVs, an
        object with no thickness for AO to occlude, or a scene with no lights for
        COMBINED. Bake to a file and inspect the UVs before assuming the tool failed.
        """
        payload = call("shading.bake", clean(
            object=object, type=type, image_name=image_name, size=size,
            margin=margin, samples=samples, filepath=filepath,
            return_image=return_image, normal_space=normal_space,
            pass_filter=pass_filter, use_clear=use_clear,
        ), timeout=timeout)

        if return_image and payload.get("png_b64"):
            return png_image(payload)
        payload.pop("png_b64", None)
        return payload
