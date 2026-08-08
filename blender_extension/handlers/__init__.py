"""Handler modules. Importing this package registers every bridge command.

Each module mirrors an MCP server tool module of the same name.
"""

from __future__ import annotations

# Import order is irrelevant (registration is by name) but keep it alphabetical
# so a duplicate-name collision is easy to trace.
from . import core  # noqa: F401

_OPTIONAL = (
    "objects", "mesh", "modifiers", "sculpt", "weights",
    "rig", "shading", "uv", "anim", "geonodes", "settings",
    "properties", "io",
)

_LOADED = ["core"]
_FAILED = {}

for _name in _OPTIONAL:
    try:
        __import__(f"{__name__}.{_name}", fromlist=["*"])
        _LOADED.append(_name)
    except ImportError:
        # Module not written yet — the bridge still serves everything else.
        pass
    except Exception as exc:  # a real error inside a handler module
        _FAILED[_name] = f"{type(exc).__name__}: {exc}"


def loaded() -> list:
    return list(_LOADED)


def failed() -> dict:
    return dict(_FAILED)
