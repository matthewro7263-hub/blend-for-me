"""Tool modules, one per handler domain.

Each module exposes ``register(mcp)`` which declares its tools. Modules are
optional: a domain that is not written yet simply contributes no tools.
"""

from __future__ import annotations

import importlib

MODULES = (
    "core",
    "objects",
    "mesh",
    "modifiers",
    "sculpt",
    "weights",
    "rig",
    "shading",
    "uv",
    "anim",
    "cinematics",
    "geonodes",
    "settings",
    "properties",
    "io",
    "docs",
)

LOADED: list[str] = []
FAILED: dict[str, str] = {}


def load_all(mcp) -> None:
    for name in MODULES:
        try:
            module = importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as exc:
            # Only swallow "this module does not exist yet", never a missing
            # third-party dependency inside a module that does exist.
            if exc.name == f"{__name__}.{name}":
                continue
            FAILED[name] = f"{type(exc).__name__}: {exc}"
            continue
        except Exception as exc:
            FAILED[name] = f"{type(exc).__name__}: {exc}"
            continue

        register = getattr(module, "register", None)
        if register is None:
            FAILED[name] = "module has no register(mcp)"
            continue
        try:
            register(mcp)
            LOADED.append(name)
        except Exception as exc:
            FAILED[name] = f"{type(exc).__name__}: {exc}"
