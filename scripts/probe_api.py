#!/usr/bin/env python3
"""Re-verify the Blender APIs this project depends on.

    blender --background --factory-startup --python scripts/probe_api.py
    # or: make probe

Run this after upgrading Blender. It checks, against the *running* build, every
assumption recorded in docs/BLENDER_5X_API_NOTES.md and exits non-zero if one no
longer holds — so a breaking upstream change shows up here rather than as a
confusing failure mid-sculpt.
"""

from __future__ import annotations

import sys

import bpy

FAILURES: list[str] = []
NOTES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(f"{label} {detail}".strip())


def op_exists(path: str) -> bool:
    module, name = path.split(".", 1)
    try:
        getattr(getattr(bpy.ops, module), name).get_rna_type()
        return True
    except Exception:
        return False


def op_params(path: str) -> set:
    module, name = path.split(".", 1)
    rna = getattr(getattr(bpy.ops, module), name).get_rna_type()
    return {p.identifier for p in rna.properties if p.identifier != "rna_type"}


def enum_items(path: str, prop: str) -> set:
    module, name = path.split(".", 1)
    rna = getattr(getattr(bpy.ops, module), name).get_rna_type()
    return {i.identifier for i in rna.properties[prop].enum_items}


print(f"\nBlender {bpy.app.version_string}  (python {sys.version.split()[0]})")

print("\n[operators expected to EXIST]")
for path in [
    "wm.obj_import", "wm.obj_export", "wm.stl_import", "wm.stl_export",
    "wm.ply_import", "wm.ply_export", "wm.usd_import", "wm.usd_export",
    "wm.alembic_import", "wm.alembic_export", "wm.fbx_import",
    "import_scene.fbx", "export_scene.fbx", "import_scene.gltf", "export_scene.gltf",
    "sculpt.brush_stroke", "sculpt.mesh_filter", "sculpt.face_sets_init",
    "sculpt.dynamic_topology_toggle", "sculpt.detail_flood_fill", "sculpt.symmetrize",
    "brush.asset_activate", "object.voxel_remesh", "object.quadriflow_remesh",
    "paint.mask_box_gesture", "paint.mask_flood_fill", "paint.weight_gradient",
    "paint.weight_from_bones", "object.data_transfer", "object.parent_set",
    "object.shade_auto_smooth", "object.shade_smooth_by_angle",
    "object.vertex_group_normalize_all", "object.vertex_group_smooth",
    "object.vertex_group_limit_total", "render.opengl", "ed.undo_push",
]:
    check(path, op_exists(path))

print("\n[operators expected to be GONE — a pass here means our workaround is still needed]")
for path in ["paint.brush_select", "object.vertex_group_transfer_weight",
             "wm.fbx_export", "wm.gltf_import", "wm.gltf_export",
             "sculpt.mask_box_gesture"]:
    check(f"{path} absent", not op_exists(path),
          "(it came BACK — the workaround can be simplified)")

print("\n[brush asset system]")
brush_props = {p.identifier for p in bpy.types.Brush.bl_rna.properties}
check("Brush.sculpt_tool still absent", "sculpt_tool" not in brush_props,
      "(enum brush selection is back)")
check("Brush.size present", "size" in brush_props)
check("brush.asset_activate params", {"asset_library_type", "relative_asset_identifier"}
      <= op_params("brush.asset_activate"))
ups = {p.identifier for p in bpy.types.UnifiedPaintSettings.bl_rna.properties}
check("UnifiedPaintSettings.use_unified_size", "use_unified_size" in ups)

import os
brush_dir = bpy.utils.system_resource("DATAFILES", path="assets/brushes")
check("essentials brush library exists", bool(brush_dir) and os.path.isdir(brush_dir))
if brush_dir and os.path.isdir(brush_dir):
    sculpt_lib = os.path.join(brush_dir, "essentials_brushes-mesh_sculpt.blend")
    if os.path.isfile(sculpt_lib):
        with bpy.data.libraries.load(sculpt_lib, assets_only=True) as (src, _dst):
            names = set(src.brushes)
        for required in ["Clay Strips", "Draw", "Grab", "Smooth", "Inflate/Deflate",
                         "Crease Sharp", "Scrape/Fill", "Pinch/Magnify", "Pose"]:
            check(f"brush asset {required!r}", required in names)
        NOTES.append(f"{len(names)} sculpt brush assets available")

print("\n[stroke element schema]")
stroke_fields = {p.identifier for p in bpy.types.OperatorStrokeElement.bl_rna.properties
                 if p.identifier != "rna_type"}
check("OperatorStrokeElement fields",
      {"location", "mouse", "mouse_event", "pressure", "size", "is_start", "time",
       "x_tilt", "y_tilt"} <= stroke_fields,
      f"got {sorted(stroke_fields)}")
check("brush_stroke.override_location", "override_location" in op_params("sculpt.brush_stroke"))

print("\n[symmetry]")
sculpt_props = {p.identifier for p in bpy.types.Sculpt.bl_rna.properties}
check("use_symmetry_x/y/z", {"use_symmetry_x", "use_symmetry_y", "use_symmetry_z"} <= sculpt_props)
if "radial_symmetry" in sculpt_props:
    NOTES.append("radial_symmetry is BACK — sculpt_symmetry() can now support radial_counts")

print("\n[enums]")
check("mesh_filter types", {"SMOOTH", "INFLATE", "RELAX", "SHARPEN", "SCALE"}
      <= enum_items("sculpt.mesh_filter", "type"))
check("data_transfer VGROUP_WEIGHTS", "VGROUP_WEIGHTS" in enum_items("object.data_transfer", "data_type"))
check("data_transfer POLYINTERP_NEAREST", "POLYINTERP_NEAREST" in enum_items("object.data_transfer", "vert_mapping"))
check("parent_set ARMATURE_AUTO", "ARMATURE_AUTO" in enum_items("object.parent_set", "type"))
check("weight_gradient LINEAR/RADIAL", {"LINEAR", "RADIAL"} <= enum_items("paint.weight_gradient", "type"))
check("voxel_remesh takes no args", op_params("object.voxel_remesh") == set(),
      f"got {op_params('object.voxel_remesh')}")

engines = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
check("BLENDER_EEVEE engine id", "BLENDER_EEVEE" in engines, f"got {sorted(engines)}")

print("\n[core plumbing]")
check("Context.temp_override", hasattr(bpy.types.Context, "temp_override"))
check("bpy.app.timers", hasattr(bpy.app, "timers"))

print("\n" + "=" * 68)
for note in NOTES:
    print(f"note: {note}")
if FAILURES:
    print(f"\n{len(FAILURES)} ASSUMPTION(S) NO LONGER HOLD:")
    for failure in FAILURES:
        print(f"  - {failure}")
    print("\nUpdate docs/BLENDER_5X_API_NOTES.md and the affected handlers.")
    sys.exit(1)

print("\nAll documented API assumptions still hold.")
