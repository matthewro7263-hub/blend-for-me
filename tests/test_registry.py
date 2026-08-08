"""Parameter-schema inference must prefer no validation over a false allowlist."""

from __future__ import annotations

import importlib.util
import pathlib


REPO = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "blender_agent_registry_under_test", REPO / "blender_extension" / "registry.py"
)
assert SPEC and SPEC.loader
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)

LOOP_KEYS = ("alpha", "beta")


def _direct_handler(params):
    return params["required"], params.get("optional")


def _helper_handler(params):
    _consume(params)
    return params.get("visible")


def _constant_loop_handler(params):
    for key in LOOP_KEYS:
        params.get(key)


def _reused_loop_name_handler(params):
    for key, attr in (("hidden_a", "a"), ("hidden_b", "b")):
        params.get(key)
    for key in LOOP_KEYS:
        params.get(key)


def _consume(_params):
    return None


def test_literal_keys_are_extracted():
    assert registry.accepted_params(_direct_handler) == {"required", "optional"}


def test_passing_params_to_a_helper_disables_partial_validation():
    assert registry.accepted_params(_helper_handler) == set()


def test_module_constant_loop_keys_are_extracted():
    assert registry.accepted_params(_constant_loop_handler) == set(LOOP_KEYS)


def test_reused_loop_names_cannot_leak_between_scopes():
    assert registry.accepted_params(_reused_loop_name_handler) == set()
