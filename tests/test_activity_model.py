from __future__ import annotations

import importlib.util
import pathlib


REPO = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "blender_agent_activity_model_under_test",
    REPO / "blender_extension" / "activity_model.py",
)
assert SPEC and SPEC.loader
activity_model = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activity_model)


def test_routes_shader_and_compositor_work_to_node_editor():
    assert activity_model.editor_route("shading.build_node_graph")[0] == (
        "NODE_EDITOR",
        "ShaderNodeTree",
    )
    assert activity_model.editor_route(
        "execute_python",
        {"code": "bpy.context.scene.use_nodes = True  # compositor"},
    )[0] == ("NODE_EDITOR", "CompositorNodeTree")


def test_routes_animation_and_editorial_to_their_editors():
    assert activity_model.editor_route("anim.key_object")[0][0] == "DOPESHEET_EDITOR"
    assert activity_model.editor_route("cinematics.add_text_strip")[0][0] == "SEQUENCE_EDITOR"
    assert activity_model.editor_route("uv.unwrap")[0][0] == "IMAGE_EDITOR"


def test_node_waypoints_ignore_bad_locations_and_keep_stable_labels():
    points = activity_model.node_waypoints({
        "nodes": [
            {"id": "noise", "location": [-400, 120]},
            {"label": "Color grade", "location": [0.0, -50.0]},
            {"id": "bad", "location": [1]},
        ]
    })
    assert points == [
        ("noise", -400.0, 120.0),
        ("Color grade", 0.0, -50.0),
    ]


def test_animation_helpers_are_clamped():
    assert activity_model.smoothstep(-1) == 0.0
    assert activity_model.smoothstep(2) == 1.0
    assert activity_model.ease_out_cubic(0.5) > 0.5
    assert activity_model.exit_scale(0) == 1.0
    assert activity_model.exit_scale(1) == 0.0
    assert activity_model.spring_ease(0) == 0.0
    assert activity_model.spring_ease(1) == 1.0
    assert activity_model.spring_ease(0.35) > 1.0
    assert activity_model.typing_duration(0) == 0.32
    assert activity_model.typing_duration(10_000) == 1.35
