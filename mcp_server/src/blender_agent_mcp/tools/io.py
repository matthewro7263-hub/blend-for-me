"""Import, export and .blend file tools."""

from __future__ import annotations

from typing import Optional

from ..server import call, clean


def register(mcp) -> None:

    @mcp.tool()
    def import_model(
        path: str,
        format: str = "auto",
        scale: Optional[float] = None,
        forward_axis: Optional[str] = None,
        up_axis: Optional[str] = None,
        options: Optional[dict] = None,
        timeout: float = 180.0,
    ) -> dict:
        """Import a 3D model file into the current scene.

        Args:
            path: Absolute path to the file.
            format: OBJ, STL, PLY, FBX, GLTF (also .glb), USD or ABC. The default
                `auto` infers it from the file extension.
            scale: Uniform import scale. Not supported by every format — the
                result's `ignored_options` says when it was dropped.
            forward_axis / up_axis: Axis convention of the source file, e.g.
                `NEGATIVE_Z` and `Y` for the common "Y-up" convention. Only OBJ,
                STL and PLY accept these on import.
            options: Escape hatch — raw operator parameters passed straight
                through (e.g. `{"use_split_objects": false}` for OBJ). Names that
                the operator does not have are reported in `ignored_options`
                rather than erroring, so check that field.

        Returns `created_objects`, computed by diffing the scene before and
        after — so you get the real names even when Blender renames on collision.
        Works headless.
        """
        return call("io.import_model", clean(
            path=path, format=format, scale=scale, forward_axis=forward_axis,
            up_axis=up_axis, options=options), timeout=timeout)

    @mcp.tool()
    def export_model(
        path: str,
        format: str = "auto",
        selected_only: bool = False,
        scale: Optional[float] = None,
        forward_axis: Optional[str] = None,
        up_axis: Optional[str] = None,
        apply_modifiers: Optional[bool] = None,
        options: Optional[dict] = None,
        timeout: float = 180.0,
    ) -> dict:
        """Export the scene, or just the selection, to a 3D model file.

        Args:
            path: Destination path; parent directories are created for you. The
                extension decides the format when `format` is `auto`.
            format: OBJ, STL, PLY, FBX, GLTF, USD or ABC.
            selected_only: Export only selected objects. Fails fast with a clear
                message when nothing is selected, rather than writing an empty
                file.
            scale: Uniform export scale. Ignored by GLTF and USD, which are
                fixed-unit formats — see `ignored_options`.
            forward_axis / up_axis: Axis convention to write. For game engines
                expecting Y-up, `.glb` already writes Y-up by default.
            apply_modifiers: Evaluate modifiers before export (default varies by
                format). Not available for Alembic or USD, which carry their own
                evaluation mode.
            options: Raw operator parameters passed through, e.g.
                `{"export_animations": false}` for glTF. Unknown names are
                reported in `ignored_options`, not raised.

        For `.glb` the exporter is set to GLB (single binary file) automatically;
        `.gltf` writes the separate-files variant. Works headless.
        """
        return call("io.export_model", clean(
            path=path, format=format, selected_only=selected_only, scale=scale,
            forward_axis=forward_axis, up_axis=up_axis,
            apply_modifiers=apply_modifiers, options=options), timeout=timeout)

    @mcp.tool()
    def save_blend(
        path: Optional[str] = None,
        confirm: bool = False,
        compress: bool = False,
    ) -> dict:
        """Save the .blend file.

        Args:
            path: Where to save. Omit to save over the currently-open file (which
                fails if the scene has never been saved). A missing `.blend`
                suffix is added.
            confirm: Required only when `path` names an existing file that is
                **not** the one currently open — that would overwrite someone
                else's work, so it must be explicit.
            compress: Write a compressed .blend.

        Works headless.
        """
        return call("io.save_blend", clean(path=path, confirm=confirm,
                                           compress=compress), timeout=120.0)

    @mcp.tool()
    def open_blend(path: str, confirm: bool = False) -> dict:
        """Open a .blend file, replacing the current scene. Destructive.

        Always requires `confirm=true`, because everything unsaved in the current
        session is discarded. The refusal message tells you whether the current
        scene actually has unsaved changes, so you can decide whether to call
        `save_blend` first.

        Works headless.
        """
        return call("io.open_blend", clean(path=path, confirm=confirm), timeout=120.0)

    @mcp.tool()
    def list_blend_contents(path: str, datablock_type: Optional[str] = None) -> dict:
        """List the datablocks inside another .blend file, without opening it.

        Use before `append_from_blend` to discover exact names.

        Args:
            datablock_type: Narrow to one of objects, materials, meshes,
                collections, actions, node_groups, images, worlds, armatures,
                brushes. Omit for all of them.

        Cannot inspect the .blend that is currently open — its contents are
        already in the session and addressable by name.
        """
        return call("io.list_blend_contents", clean(
            path=path, datablock_type=datablock_type), timeout=60.0)

    @mcp.tool()
    def append_from_blend(
        path: str,
        datablock_type: str = "objects",
        names: Optional[list[str]] = None,
        link: bool = False,
    ) -> dict:
        """Append or link datablocks from another .blend file.

        Args:
            path: Source .blend.
            datablock_type: objects, materials, meshes, collections, actions,
                node_groups, images, worlds, armatures or brushes.
            names: Which datablocks to bring in. Omit for all of that type. An
                unknown name is refused with the list of what is actually there.
            link: False (default) **appends** — a full independent copy. True
                **links** — a live reference to the source file, which stays
                read-only here and updates when the source changes.

        Appended objects are linked into the active collection so they appear in
        the scene. Works headless.
        """
        return call("io.append_from_blend", clean(
            path=path, datablock_type=datablock_type, names=names, link=link),
            timeout=120.0)
