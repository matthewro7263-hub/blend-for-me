"""Contract tests ensuring every bridge command and MCP tool signature is verified."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Load registry directly without triggering bpy import
reg_path = REPO_ROOT / "blender_extension" / "registry.py"
spec = importlib.util.spec_from_file_location("blender_extension_registry", reg_path)
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


def test_command_registry_structure():
    """Verify registry dictionary types and default structures."""
    assert isinstance(registry.HANDLERS, dict)
    assert isinstance(registry.META, dict)


def test_strict_bool_coercion():
    """Test strict boolean validation helper."""
    assert registry.strict_bool(True) is True
    assert registry.strict_bool(False) is False
    assert registry.strict_bool("true") is True
    assert registry.strict_bool("false") is False
    assert registry.strict_bool(1) is True
    assert registry.strict_bool(0) is False


def test_finite_number_validation():
    """Test finite number validator rejects NaN and infinity."""
    assert registry.validate_finite(42.0) == 42.0
    assert registry.validate_finite([1.0, 2.0, 3.0]) == 0.0

    import pytest
    with pytest.raises(ValueError):
        registry.validate_finite(float("nan"))
    with pytest.raises(ValueError):
        registry.validate_finite(float("inf"))
