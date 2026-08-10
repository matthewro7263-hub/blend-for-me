"""Unit tests ensuring version consistency across all project declarations."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

version_py_path = REPO_ROOT / "blender_extension" / "version.py"
spec = importlib.util.spec_from_file_location("blender_extension_version", version_py_path)
version_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version_mod)
VERSION = version_mod.VERSION


def test_manifest_version_matches_authoritative():
    manifest_path = REPO_ROOT / "blender_extension" / "blender_manifest.toml"
    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)
    assert data["version"] == VERSION, f"manifest version {data['version']!r} != {VERSION!r}"


def test_pyproject_version_matches_authoritative():
    pyproject_path = REPO_ROOT / "mcp_server" / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["version"] == VERSION, f"pyproject version {data['project']['version']!r} != {VERSION!r}"


def test_server_version_matches_authoritative():
    server_py = (REPO_ROOT / "mcp_server" / "src" / "blender_agent_mcp" / "server.py").read_text()
    assert f'version="{VERSION}"' in server_py or f"version = '{VERSION}'" in server_py


def test_handlers_core_version_matches_authoritative():
    core_py = (REPO_ROOT / "blender_extension" / "handlers" / "core.py").read_text()
    assert f'"extension_version": "{VERSION}"' in core_py


def test_bridge_handshake_version_matches_authoritative():
    bridge_py = (REPO_ROOT / "blender_extension" / "bridge.py").read_text()
    assert f'"extension_version": "{VERSION}"' in bridge_py
