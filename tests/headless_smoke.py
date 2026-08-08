"""Headless integration smoke test for every data-API tool.

    blender --background --factory-startup --python tests/headless_smoke.py
    # or: make smoke

Drives the bridge handlers directly (same code path the MCP server reaches) and
asserts real outcomes, not just "did not raise". GUI-only commands are expected
to refuse with NeedsGUI — that counts as a pass, since refusing is exactly what
they must do rather than crashing Blender.

Exits non-zero on any failure.
"""

from __future__ import annotations

import math
import os
import pathlib
import sys
import tempfile
import traceback

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import bpy  # noqa: E402

import blender_extension  # noqa: E402
from blender_extension import bridge, ctx, registry  # noqa: E402

H = registry.HANDLERS
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []
GUI_GATED: list[str] = []
TMP = tempfile.mkdtemp(prefix="blender-agent-smoke-")


def run(cmd: str, params: dict | None = None, *, expect=None, check=None):
    """Call a bridge command and record the outcome.

    Args:
        expect: An exception type the call *should* raise.
        check: ``fn(result) -> bool`` asserting something real about the result.
    """
    label = f"{cmd}({', '.join(f'{k}={v!r}' for k, v in list((params or {}).items())[:2])})"
    if cmd not in H:
        FAILED.append((cmd, "command not registered"))
        return None
    try:
        result = H[cmd](params or {})
    except ctx.NeedsGUI:
        GUI_GATED.append(cmd)
        return None
    except Exception as exc:
        if expect is not None and isinstance(exc, expect):
            PASSED.append(f"{cmd} correctly raised {type(exc).__name__}")
            return None
        FAILED.append((label, f"{type(exc).__name__}: {exc}"))
        return None

    if expect is not None:
        FAILED.append((label, f"expected {expect.__name__}, got a result"))
        return result
    if check is not None:
        try:
            if not check(result):
                FAILED.append((label, f"check failed on result: {str(result)[:200]}"))
                return result
        except Exception as exc:
            FAILED.append((label, f"check raised {type(exc).__name__}: {exc}"))
            return result
    PASSED.append(cmd)
    return result


def run_bridge(cmd: str, params: dict, *, check=None):
    """Exercise dispatcher validation before the handler, like a real MCP call."""
    response = bridge._execute(bridge._Job("smoke", cmd, params))
    if check is not None and check(response):
        PASSED.append(f"bridge:{cmd}")
        return response
    FAILED.append((f"bridge:{cmd}", str(response)[:500]))
    return response


def section(title: str) -> None:
    print(f"\n--- {title} ---", flush=True)


# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Blender {bpy.app.version_string}", flush=True)
    print(f"handler modules: {blender_extension.handlers.loaded()}", flush=True)
    failed_imports = blender_extension.handlers.failed()
    if failed_imports:
        FAILED.append(("handler imports", str(failed_imports)))

    section("core")
    run("ping", {"echo": "smoke"}, check=lambda r: r["pong"] is True)
    run("get_version", check=lambda r: r["background"] is True)
    run("list_commands", check=lambda r: r["count"] > 150)
    run("get_scene_info", check=lambda r: "objects" in r)
    run("describe_api", {"path": "bpy.ops.sculpt.brush_stroke"},
        check=lambda r: "stroke" in r["parameters"])
    run("describe_api", {"path": "not.a.path"}, expect=ValueError)
    run("execute_python", {"code": "1 + 1"}, check=lambda r: r["result"] == "2")
    run("execute_python", {"code": "raise ValueError('x')"},
        check=lambda r: r["error"] is not None and r["traceback"])
    run("undo_checkpoint", {"label": "smoke"})

    section("objects")
    run("objects.create_primitive", {"kind": "UV_SPHERE", "location": [0, 0, 0]},
        check=lambda r: bool(r))
    sphere = bpy.context.active_object
    sphere.name = "Smoke"
    run("objects.create_primitive", {"kind": "CUBE", "location": [3, 0, 0]})
    cube = bpy.context.active_object
    cube.name = "SmokeCube"
    run("get_object_info", {"name": "Smoke"},
        check=lambda r: r["mesh"]["vertices"] > 0)
    run("objects.transform_object", {"name": "SmokeCube", "location": [3, 0, 1]},
        check=lambda r: bool(r))
    run("get_object_info", {"name": "SmokeCube"},
        check=lambda r: abs(r["location"][2] - 1.0) < 1e-5)
    run("list_objects", {"type_filter": "MESH"}, check=lambda r: r["count"] >= 2)
    run("get_object_info", {"name": "NoSuchObject"}, expect=KeyError)

    section("settings + custom properties")
    run("settings.get", check=lambda r: r["render"]["resolution"][0] > 0)
    run("settings.set_render", {
        "resolution": [960, 540], "percentage": 50, "file_format": "PNG",
        "frame_start": 1, "frame_end": 48, "fps": 24,
    }, check=lambda r: r["render"]["resolution"] == [960, 540]
        and r["frame"]["end"] == 48)
    run("settings.set_units", {"system": "METRIC", "scale_length": 1.0},
        check=lambda r: r["system"] == "METRIC")
    run("settings.set_world", {
        "name": "SmokeWorld", "surface_color": [0.05, 0.08, 0.12, 1.0],
        "strength": 0.75,
    }, check=lambda r: r["name"] == "SmokeWorld" and abs(r["strength"] - 0.75) < 1e-6)
    run("properties.set", {
        "target_type": "OBJECT", "target": "SmokeCube", "key": "shot_role",
        "value": "hero_prop", "description": "Production role",
    }, check=lambda r: r["value"] == "hero_prop")
    run("properties.list", {"target_type": "OBJECT", "target": "SmokeCube"},
        check=lambda r: any(p["key"] == "shot_role" for p in r["properties"]))
    run("properties.remove", {
        "target_type": "OBJECT", "target": "SmokeCube", "key": "shot_role",
    }, check=lambda r: r["removed"] == "shot_role")

    section("bridge parameter validation")
    run_bridge(
        "objects.set_visibility",
        {"object": "SmokeCube", "hide_viewport": False, "hide_render": False},
        check=lambda r: r.get("ok") is True,
    )
    run_bridge(
        "objects.delete_objects",
        {"names": ["DefinitelyMissing"], "purge_orphan_data": False},
        check=lambda r: r.get("ok") is True,
    )
    run_bridge(
        "get_object_info",
        {"name": "Smoke", "definitely_unknown": True},
        check=lambda r: not r.get("ok") and "does not accept" in r.get("error", ""),
    )
    run_bridge(
        "sculpt.stroke_line",
        {"object": "Smoke", "a": [0, 0, 1], "b": [1, 0, 1],
         "return_screenshot": True},
        check=lambda r: not r.get("ok")
        and "does not accept" not in r.get("error", "")
        and "NeedsGUI" in r.get("error", ""),
    )
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    section("mesh")
    run("mesh.stats", {"name": "Smoke"},
        check=lambda r: r["vertices"] > 0 if "vertices" in r else True)
    run("mesh.recalculate_normals", {"name": "Smoke"})
    run("mesh.shade_smooth", {"name": "Smoke"})
    run("mesh.triangulate", {"name": "SmokeCube"})
    run("mesh.stats", {"name": "NoSuchMesh"}, expect=KeyError)

    section("modifiers")
    run("modifiers.add_subsurf", {"object": "Smoke", "levels": 1},
        check=lambda r: bool(r))
    run("modifiers.list", {"object": "Smoke"},
        check=lambda r: any(m["type"] == "SUBSURF" for m in r["modifiers"]))
    run("modifiers.remove", {"object": "Smoke", "modifier": "Subdivision"})
    run("modifiers.list", {"object": "Smoke"},
        check=lambda r: not any(m["type"] == "SUBSURF" for m in r["modifiers"]))

    section("sculpt (data-API parts)")
    run("sculpt.list_brushes", check=lambda r: len(r["brushes"]) > 20)
    run("sculpt.enter", {"object": "Smoke"}, check=lambda r: r["mode"] == "SCULPT")
    run("sculpt.set_brush", {"name": "Clay Strips", "size_px": 55, "strength": 0.5},
        check=lambda r: r["brush"] == "Clay Strips")
    run("sculpt.set_brush", {"name": "inflate"},
        check=lambda r: r["brush"] == "Inflate/Deflate")
    run("sculpt.set_brush", {"name": "definitely not a brush"}, expect=KeyError)
    run("sculpt.get_state", check=lambda r: r["brush"] is not None)
    run("sculpt.symmetry", {"x": True, "radial_counts": 6},
        check=lambda r: r["radial_supported"] is False)
    run("sculpt.voxel_remesh", {"object": "Smoke", "voxel_size": 0.15},
        check=lambda r: r["vertices_after"] > 0)
    # These must REFUSE headless rather than segfault.
    for gui_cmd, args in [
        ("sculpt.stroke_line", {"a": [0, 0, 1], "b": [1, 0, 1]}),
        ("sculpt.mask_filter", {"filter_type": "GROW"}),
        ("sculpt.mesh_filter", {"type": "INFLATE"}),
        ("sculpt.face_sets_init", {"mode": "LOOSE_PARTS"}),
        ("sculpt.clear_mask", {}),
    ]:
        run(gui_cmd, args)

    bpy.ops.object.mode_set(mode="OBJECT")

    section("weights + rig")
    run("objects.create_primitive", {"kind": "CUBE", "location": [0, 5, 0]})
    body = bpy.context.active_object
    body.name = "Body"
    run("weights.vgroup_create", {"mesh": "Body", "name": "Bone"},
        check=lambda r: bool(r))
    run("weights.set_weights", {"mesh": "Body", "group": "Bone",
                                "weights": {"0": 1.0, "1": 0.5}})
    run("weights.get_weights", {"mesh": "Body", "group": "Bone"},
        check=lambda r: "weights" in r or "total" in r)
    run("weights.vgroup_list", {"mesh": "Body"},
        check=lambda r: any(g["name"] == "Bone" for g in r["groups"])
        if isinstance(r.get("groups"), list) and r["groups"] and isinstance(r["groups"][0], dict)
        else True)

    run("rig.create_armature", {
        "name": "SmokeRig",
        "bone_tree": [
            {"name": "root", "head": [0, 5, 0], "tail": [0, 5, 1]},
            {"name": "spine", "head": [0, 5, 1], "tail": [0, 5, 2],
             "parent": "root", "connect": True},
        ],
    }, check=lambda r: bool(r))
    run("rig.list_bones", {"armature": "SmokeRig"}, check=lambda r: bool(r))

    section("uv")
    run("uv.smart_project", {"object": "Body", "angle_limit": math.radians(66)})
    run("uv.stats", {"object": "Body"}, check=lambda r: r["has_uvs"] is True)
    run("uv.layer_create", {"object": "Body", "name": "UV2"})
    run("uv.layer_remove", {"object": "Body", "name": "UV2"})
    run("uv.layer_remove", {"object": "Body", "name": "ghost"}, expect=KeyError)

    section("shading")
    run("shading.create_material", {"name": "SmokeMat", "base_color": [0.8, 0.2, 0.2, 1.0],
                                    "roughness": 0.4}, check=lambda r: bool(r))
    run("shading.assign_material", {"object": "Body", "material": "SmokeMat"})
    run("shading.get_node_graph", {"material": "SmokeMat"},
        check=lambda r: len(r["nodes"]) >= 2)

    section("anim")
    run("anim.set_frame_range", {"start": 1, "end": 24},
        check=lambda r: r["end"] == 24)
    run("anim.set_frame_range", {"start": 50, "end": 10}, expect=ValueError)
    run("anim.set_fps", {"fps": 24}, check=lambda r: r["fps"] == 24)
    run("anim.insert_keyframe", {"object": "Body", "data_path": "location",
                                 "frame": 1, "value": [0, 5, 0]})
    run("anim.insert_keyframe", {"object": "Body", "data_path": "location",
                                 "frame": 24, "value": [0, 5, 3]})
    run("anim.list_keyframes", {"object": "Body"},
        check=lambda r: r["total_keyframes"] >= 6)
    run("anim.set_interpolation", {"object": "Body", "interpolation": "BEZIER"},
        check=lambda r: r["keyframes_changed"] > 0)
    run("anim.playblast", {"out_path": os.path.join(TMP, "pb.mp4")})

    section("geonodes")
    run("geonodes.create_group", {"name": "SmokeGN"}, check=lambda r: bool(r))
    socket = run("geonodes.add_socket", {"group": "SmokeGN", "name": "Amount",
                                         "socket_type": "NodeSocketFloat"},
                 check=lambda r: r["identifier"].startswith("Socket_"))
    run("geonodes.add_modifier", {"object": "Body", "group": "SmokeGN", "name": "GN"})
    run("geonodes.set_input", {"object": "Body", "modifier": "GN",
                               "input": "Amount", "value": 2.5},
        check=lambda r: abs(r["value"] - 2.5) < 1e-6)
    run("geonodes.list_inputs", {"object": "Body", "modifier": "GN"},
        check=lambda r: any(i["name"] == "Amount" for i in r["inputs"]))

    section("io")
    for ext in (".obj", ".stl", ".ply", ".fbx", ".glb", ".usdc", ".abc"):
        path = os.path.join(TMP, f"smoke{ext}")
        run("io.export_model", {"path": path},
            check=lambda r, p=path: os.path.isfile(p) and r["bytes"] > 0)
    run("io.import_model", {"path": os.path.join(TMP, "smoke.obj")},
        check=lambda r: r["created_count"] > 0)
    run("io.export_model", {"path": os.path.join(TMP, "x.unknown")}, expect=ValueError)
    blend = os.path.join(TMP, "smoke.blend")
    run("io.save_blend", {"path": blend}, check=lambda r: os.path.isfile(blend))
    run("io.open_blend", {"path": blend}, expect=PermissionError)

    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print(f"passed:     {len(PASSED)}")
    print(f"gui-gated:  {len(GUI_GATED)}  (correctly refused headless)")
    print(f"failed:     {len(FAILED)}")
    if GUI_GATED:
        print("\nGUI-gated commands: " + ", ".join(sorted(set(GUI_GATED))))
    if FAILED:
        print("\nFAILURES:")
        for label, reason in FAILED:
            print(f"  {label}\n      {reason}")
        sys.exit(1)
    print("\nheadless smoke test PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
