"""Command registry shared by every handler module.

Handlers register with the ``@command`` decorator and are executed **on Blender's
main thread** by the bridge's timer pump. A handler receives the request's
``params`` dict and returns any JSON-serialisable value.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Callable, Dict

#: cmd name -> handler callable
HANDLERS: Dict[str, Callable[[dict], object]] = {}

#: cmd name -> metadata used by ``list_commands``
META: Dict[str, dict] = {}


def accepted_params(fn) -> set:
    """Infer the parameter keys a handler reads, by parsing its source.

    Handlers read arguments ad-hoc (``params["mesh"]``, ``params.get("object")``)
    rather than declaring a schema, so the accepted keys are recovered from the
    source at registration time. This exists to make an unknown key a hard error:
    without it, ``uv.layer_list{"name": ...}`` — where the handler reads
    ``object`` — silently falls back to the active object and reports on the
    wrong one, which is far worse than failing.

    Returns an empty set when nothing can be extracted (a handler that builds key
    names dynamically, or whose source is unavailable); the dispatcher then skips
    validation for that command rather than rejecting valid calls.
    """
    try:
        source = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError, IndentationError):
        return set()

    keys: set = set()
    dynamic = False

    # Handlers commonly loop `for key in SOME_CONSTANT: params.get(key)`. The
    # constant lives in the handler module's globals, so those keys are
    # recoverable rather than an unknowable dynamic access.
    loop_vars: dict = {}
    namespace = getattr(fn, "__globals__", {})
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) \
                and isinstance(node.iter, ast.Name):
            candidate = namespace.get(node.iter.id)
            if isinstance(candidate, (set, frozenset, list, tuple)) \
                    and candidate and all(isinstance(x, str) for x in candidate):
                loop_vars[node.target.id] = set(candidate)

    for node in ast.walk(tree):
        # params["key"] and params.get("key")
        if isinstance(node, ast.Subscript) and _is_params(node.value):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
            elif isinstance(node.slice, ast.Name) and node.slice.id in loop_vars:
                keys |= loop_vars[node.slice.id]
            else:
                dynamic = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and _is_params(node.func.value):
            if node.func.attr in {"get", "pop", "setdefault"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
                elif isinstance(first, ast.Name) and first.id in loop_vars:
                    keys |= loop_vars[first.id]
                else:
                    dynamic = True
            elif node.func.attr in {"keys", "items", "values", "update"}:
                dynamic = True

    return set() if dynamic else keys


def _is_params(node) -> bool:
    return isinstance(node, ast.Name) and node.id == "params"


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
            "params": sorted(accepted_params(fn)),
        }
        return fn

    return deco


def clear() -> None:
    """Drop every registration (used on add-on unregister/reload)."""
    HANDLERS.clear()
    META.clear()
