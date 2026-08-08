"""Command registry shared by every handler module.

Handlers register with the ``@command`` decorator and are executed **on Blender's
main thread** by the bridge's timer pump. A handler receives the request's
``params`` dict and returns any JSON-serialisable value.
"""

from __future__ import annotations

from typing import Callable, Dict

#: cmd name -> handler callable
HANDLERS: Dict[str, Callable[[dict], object]] = {}

#: cmd name -> metadata used by ``list_commands``
META: Dict[str, dict] = {}


def command(name: str, *, mutates: bool = False, needs_gui: bool = False):
    """Register ``name`` as a bridge command.

    Args:
        name: Wire-level command name, e.g. ``"objects.create_primitive"``.
        mutates: When true the dispatcher pushes an undo step before running the
            handler, so the agent can always roll a change back.
        needs_gui: When true the command requires a real ``VIEW_3D`` area and will
            fail under ``blender --background``. Recorded so ``list_commands`` can
            tell an agent up front instead of failing at call time.
    """

    def deco(fn: Callable[[dict], object]) -> Callable[[dict], object]:
        if name in HANDLERS:
            raise RuntimeError(f"duplicate bridge command: {name}")
        HANDLERS[name] = fn
        META[name] = {
            "mutates": mutates,
            "needs_gui": needs_gui,
            "doc": (fn.__doc__ or "").strip().split("\n")[0],
        }
        return fn

    return deco


def clear() -> None:
    """Drop every registration (used on add-on unregister/reload)."""
    HANDLERS.clear()
    META.clear()
