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

    visitor = _ParamVisitor(getattr(fn, "__globals__", {}))
    visitor.visit(tree)
    return set() if visitor.dynamic else visitor.keys


class _ParamVisitor(ast.NodeVisitor):
    """Collect literal ``params`` keys without claiming incomplete coverage.

    A handler that passes the whole params dict to a helper is dynamic from this
    function's point of view. Validation must be disabled for that command: a
    partial allowlist rejects valid calls before the helper gets to read them.

    Loop bindings are tracked lexically. The previous global map confused two
    different loops that both happened to name their variable ``key``; in
    ``objects.set_visibility`` that made ray-visibility names stand in for
    ``hide_viewport`` / ``hide_render`` and rejected both real arguments.
    """

    def __init__(self, namespace: dict):
        self.namespace = namespace
        self.keys: set[str] = set()
        self.dynamic = False
        self._loop_scopes: list[dict[str, set[str] | None]] = []

    def _constant_strings(self, node) -> set[str] | None:
        if not isinstance(node, ast.Name):
            return None
        candidate = self.namespace.get(node.id)
        if isinstance(candidate, (set, frozenset, list, tuple)) \
                and candidate and all(isinstance(x, str) for x in candidate):
            return set(candidate)
        return None

    def _bound_names(self, node) -> set[str]:
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, (ast.Tuple, ast.List)):
            return set().union(*(self._bound_names(item) for item in node.elts))
        return set()

    def _loop_values(self, name: str) -> set[str] | None:
        for scope in reversed(self._loop_scopes):
            if name in scope:
                return scope[name]
        return None

    def _record_key(self, node) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self.keys.add(node.value)
            return
        if isinstance(node, ast.Name):
            values = self._loop_values(node.id)
            if values is not None:
                self.keys |= values
                return
        self.dynamic = True

    @staticmethod
    def _contains_params(node) -> bool:
        return any(_is_params(child) for child in ast.walk(node))

    def visit_For(self, node: ast.For) -> None:
        names = self._bound_names(node.target)
        values = self._constant_strings(node.iter) if isinstance(node.target, ast.Name) else None
        scope = {name: (values if len(names) == 1 else None) for name in names}
        self.visit(node.iter)
        self._loop_scopes.append(scope)
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)
        self._loop_scopes.pop()

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_params(node.value):
            self._record_key(node.slice)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and _is_params(node.func.value):
            if node.func.attr in {"get", "pop", "setdefault"} and node.args:
                self._record_key(node.args[0])
            else:
                # Iteration, copying, updating, or an unknown dict method exposes
                # keys this parser cannot prove complete.
                self.dynamic = True
        else:
            # ``helper(params)`` and ``helper(options=params)`` are the critical
            # cases: helpers may read keys absent from this handler's own body.
            values = list(node.args) + [kw.value for kw in node.keywords]
            if any(self._contains_params(value) for value in values):
                self.dynamic = True
        self.generic_visit(node)


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
