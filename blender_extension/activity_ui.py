"""Visible agent presence for Blender editors.

The bridge calls :func:`begin_job` and :func:`finish_job` on Blender's main
thread.  Draw handlers render the supplied SVG cursor over the editor where the
command is operating.  ``execute_python`` replaces stdout/stderr with
:class:`LiveTextStream`, allowing prints (and the bundled ``run_terminal``
helper) to repaint a small macOS-style terminal as output arrives.

Everything is a no-op in background Blender.
"""

from __future__ import annotations

import io
import math
import pathlib
import re
import shlex
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from .activity_model import (
    editor_route,
    exit_scale,
    node_waypoints,
    smoothstep,
    spring_ease,
    typing_duration,
)


_DRAW_SPACES = {
    "VIEW_3D": bpy.types.SpaceView3D,
    "NODE_EDITOR": bpy.types.SpaceNodeEditor,
    "SEQUENCE_EDITOR": bpy.types.SpaceSequenceEditor,
    "DOPESHEET_EDITOR": bpy.types.SpaceDopeSheetEditor,
    "GRAPH_EDITOR": bpy.types.SpaceGraphEditor,
    "IMAGE_EDITOR": bpy.types.SpaceImageEditor,
    "TEXT_EDITOR": bpy.types.SpaceTextEditor,
}
_DRAW_HANDLES: dict[str, Any] = {}
_SVG_PATH = pathlib.Path(__file__).with_name("assets") / "agent_cursor.svg"
_CURSOR_POINTS: list[tuple[float, float]] = []
_CURSOR_TRIS: list[tuple[float, float]] = []
_STATE: "_Activity | None" = None
_FORCING_REDRAW = False
_LAST_FORCED_REDRAW = 0.0
_MAX_CAPTURE = 40_000
_IDLE_TILT = math.radians(18.0)
_TERMINAL_SEEK = 0.34
_TERMINAL_DRAG = 0.58
_TERMINAL_TO_WAIT = 0.34
_TERMINAL_CLICK_TRAVEL = 0.42
_TERMINAL_CLICK_PRESS = 0.16
_TERMINAL_CLOSE = 0.36
_TERMINAL_CURSOR_EXIT = 0.28


@dataclass
class _Activity:
    msg_id: str
    command: str
    area_pointer: int | None
    area_type: str | None
    tree_type: str | None
    started_at: float
    waypoints: list[tuple[str, float, float]] = field(default_factory=list)
    terminal: bool = False
    terminal_command: str = ""
    terminal_command_set_at: float = 0.0
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    step_label: str = ""
    previous_location: tuple[float, float] | None = None
    step_location: tuple[float, float] | None = None
    step_at: float = 0.0
    step_count: int = 0
    finished_at: float | None = None
    ok: bool | None = None
    elapsed_ms: float = 0.0
    demo: bool = False
    demo_index: int = 0


@dataclass
class _TerminalMotion:
    x: float
    y: float
    width: float
    height: float
    alpha: float
    scale: float
    cursor_x: float
    cursor_y: float
    cursor_rotation: float
    click: float
    typed_command: str
    typing: bool
    output_visible: bool


class LiveTextStream(io.StringIO):
    """A StringIO that mirrors writes into the visible terminal overlay."""

    def __init__(self, stream: str):
        super().__init__()
        self.stream = stream

    def write(self, text: str) -> int:
        original_count = len(text)
        # wm.redraw_timer emits its own timing diagnostic through the redirected
        # stream. It is implementation noise caused by our live repaint, not
        # agent command output, so keep it out of both the UI and API response.
        cleaned = "".join(
            line for line in text.splitlines(keepends=True)
            if "Draw window and swap:" not in line
        )
        if cleaned:
            super().write(cleaned)
            append_output(self.stream, cleaned)
        return original_count

    def flush(self) -> None:
        super().flush()
        _request_redraw(force=True)


def _prefs():
    try:
        addon = bpy.context.preferences.addons.get(__package__)
        return addon.preferences if addon else None
    except Exception:
        return None


def _pref(name: str, default: Any) -> Any:
    prefs = _prefs()
    return getattr(prefs, name, default) if prefs is not None else default


def enabled() -> bool:
    return not bpy.app.background and bool(_pref("show_activity_overlay", True))


def snapshot() -> dict[str, Any]:
    state = _STATE
    if state is None:
        return {"active": False}
    return {
        "active": True,
        "command": state.command,
        "editor": state.area_type,
        "step": state.step_label,
        "finished": state.finished_at is not None,
        "ok": state.ok,
    }


def begin_job(command: str, params: dict | None = None, msg_id: str = "") -> None:
    """Start one visual activity. Must be called on Blender's main thread."""
    global _STATE
    if not enabled():
        _STATE = None
        return

    params = params or {}
    area, tree_type = _find_target_area(editor_route(command, params))
    code = str(params.get("code") or "")
    terminal = command == "execute_python"
    started_at = time.monotonic()
    _STATE = _Activity(
        msg_id=str(msg_id),
        command=str(command),
        area_pointer=int(area.as_pointer()) if area is not None else None,
        area_type=area.type if area is not None else None,
        tree_type=tree_type,
        started_at=started_at,
        waypoints=node_waypoints(params),
        terminal=terminal and bool(_pref("show_terminal_overlay", True)),
        terminal_command=_python_command_preview(code) if terminal else "",
        terminal_command_set_at=started_at,
    )
    _request_redraw(force=True)


def finish_job(response: dict, elapsed_ms: float | None = None) -> None:
    """Finish the current visual activity and populate terminal result details."""
    state = _STATE
    if state is None:
        return
    now = time.monotonic()
    state.finished_at = now
    state.elapsed_ms = float(elapsed_ms if elapsed_ms is not None else (now - state.started_at) * 1000)
    state.ok = bool(response.get("ok"))

    if state.terminal:
        if response.get("ok"):
            result = response.get("result")
            if isinstance(result, dict):
                value = result.get("result")
                error = result.get("error")
                tb = result.get("traceback")
                if error:
                    state.ok = False
                if value is not None:
                    _append_capture("stdout", f"\n=> {value}\n", redraw=False)
                if error:
                    _append_capture("stderr", f"\n{error}\n", redraw=False)
                if tb:
                    tail = "\n".join(str(tb).rstrip().splitlines()[-5:])
                    _append_capture("stderr", tail + "\n", redraw=False)
        else:
            _append_capture("stderr", f"\n{response.get('error', 'Command failed')}\n", redraw=False)
    _request_redraw(force=True)


def append_output(stream: str, text: str) -> None:
    """Append output from Python while preserving the full handler capture."""
    if not text or _STATE is None or not _STATE.terminal:
        return
    _append_capture(stream, str(text), redraw=True)


def _append_capture(stream: str, text: str, *, redraw: bool) -> None:
    state = _STATE
    if state is None:
        return
    attr = "stderr" if stream == "stderr" else "stdout"
    value = getattr(state, attr) + text
    if len(value) > _MAX_CAPTURE:
        value = "… output truncated …\n" + value[-_MAX_CAPTURE:]
    setattr(state, attr, value)
    state.output += text
    if len(state.output) > _MAX_CAPTURE:
        state.output = "… output truncated …\n" + state.output[-_MAX_CAPTURE:]
    if redraw and ("\n" in text or time.monotonic() - _LAST_FORCED_REDRAW > 0.05):
        _request_redraw(force=True)


def set_terminal_command(command: str | list[str] | tuple[str, ...]) -> None:
    """Replace the Python preview with the shell command currently running."""
    state = _STATE
    if state is None or not state.terminal:
        return
    if isinstance(command, str):
        new_command = command
    else:
        new_command = shlex.join(str(part) for part in command)
    if new_command != state.terminal_command:
        state.terminal_command = new_command
        state.terminal_command_set_at = time.monotonic()
    _request_redraw(force=True)


def step(label: str, location: list[float] | tuple[float, float] | None = None) -> None:
    """Move the agent cursor to a logical operation step.

    Graph builders call this after creating each node. Custom ``execute_python``
    scripts receive this module as ``agent_activity`` and may call it too.
    Synchronous redraws are capped so a huge graph cannot spend seconds drawing.
    """
    state = _STATE
    if state is None:
        return
    state.step_label = str(label)
    state.previous_location = state.step_location
    if isinstance(location, (list, tuple)) and len(location) == 2:
        try:
            state.step_location = (float(location[0]), float(location[1]))
        except (TypeError, ValueError):
            state.step_location = None
    state.step_at = time.monotonic()
    state.step_count += 1
    _request_redraw(force=state.step_count <= 24)


def start_demo() -> bool:
    """Preview both overlays without an MCP client, from the N-panel."""
    begin_job(
        "execute_python",
        {
            "code": "# compositor node build preview\nrun_terminal('python render_preview.py')",
            "nodes": [
                {"id": "Render Layers", "location": [-520, 180]},
                {"id": "Glare", "location": [-200, 180]},
                {"id": "Color Balance", "location": [100, 180]},
                {"id": "Composite", "location": [420, 180]},
            ],
        },
        "preview",
    )
    if _STATE is None:
        return False
    _STATE.demo = True
    _STATE.terminal_command = "python render_preview.py --scene pilot.blend"
    return True


def register() -> None:
    if bpy.app.background or _DRAW_HANDLES:
        return
    _load_cursor_svg()
    for area_type, space in _DRAW_SPACES.items():
        try:
            _DRAW_HANDLES[area_type] = space.draw_handler_add(
                _draw_overlay, (), "WINDOW", "POST_PIXEL"
            )
        except Exception as exc:
            print(f"[agent-mcp] activity overlay unavailable in {area_type}: {exc}")
    if not bpy.app.timers.is_registered(_animation_tick):
        bpy.app.timers.register(_animation_tick, first_interval=0.1, persistent=True)


def unregister() -> None:
    global _STATE
    for area_type, handle in list(_DRAW_HANDLES.items()):
        space = _DRAW_SPACES.get(area_type)
        if space is not None:
            try:
                space.draw_handler_remove(handle, "WINDOW")
            except Exception:
                pass
    _DRAW_HANDLES.clear()
    if bpy.app.timers.is_registered(_animation_tick):
        try:
            bpy.app.timers.unregister(_animation_tick)
        except ValueError:
            pass
    _STATE = None


def _find_target_area(route) -> tuple[Any | None, str | None]:
    windows = getattr(bpy.context.window_manager, "windows", ())
    for area_type, tree_type in route:
        for window in windows:
            screen = window.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type != area_type:
                    continue
                if tree_type is not None:
                    active = getattr(area.spaces, "active", None)
                    if getattr(active, "tree_type", None) != tree_type:
                        continue
                return area, tree_type
    # If the requested workspace is not open, keep the presence visible in the
    # first editor capable of hosting our overlay without changing user layout.
    fallback = []
    for window in windows:
        if window.screen is None:
            continue
        fallback.extend(area for area in window.screen.areas if area.type in _DRAW_SPACES)
    if fallback:
        area = max(fallback, key=lambda item: item.width * item.height)
        return area, None
    return None, None


def _request_redraw(*, force: bool = False) -> None:
    global _FORCING_REDRAW, _LAST_FORCED_REDRAW
    if bpy.app.background:
        return
    try:
        for window in bpy.context.window_manager.windows:
            if window.screen is None:
                continue
            for area in window.screen.areas:
                if area.type in _DRAW_SPACES:
                    area.tag_redraw()
    except Exception:
        return

    if not force or _FORCING_REDRAW:
        return
    now = time.monotonic()
    if now - _LAST_FORCED_REDRAW < 0.015:
        return
    _FORCING_REDRAW = True
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
        _LAST_FORCED_REDRAW = now
    except Exception:
        pass
    finally:
        _FORCING_REDRAW = False


def _terminal_type_start(state: _Activity) -> float:
    return max(
        state.started_at + _TERMINAL_SEEK + _TERMINAL_DRAG,
        state.terminal_command_set_at,
    )


def _terminal_type_end(state: _Activity) -> float:
    return _terminal_type_start(state) + typing_duration(len(state.terminal_command))


def _terminal_click_start(state: _Activity) -> float | None:
    if state.finished_at is None:
        return None
    pause = float(_pref("terminal_hold_seconds", 0.65))
    return max(state.finished_at + pause, _terminal_type_end(state) + 0.16)


def _terminal_close_start(state: _Activity) -> float | None:
    click_start = _terminal_click_start(state)
    if click_start is None:
        return None
    return click_start + _TERMINAL_CLICK_TRAVEL + _TERMINAL_CLICK_PRESS


def _terminal_end_time(state: _Activity) -> float | None:
    close_start = _terminal_close_start(state)
    if close_start is None:
        return None
    return close_start + _TERMINAL_CLOSE + _TERMINAL_CURSOR_EXIT


def _animation_tick() -> float:
    global _STATE
    state = _STATE
    if state is None:
        return 0.5
    now = time.monotonic()

    if state.demo and state.finished_at is None:
        schedule = (
            (0.20, "Launching Blender render helper…\n", "Render Layers", (-520, 180)),
            (0.55, "Building glare and color nodes\n", "Glare", (-200, 180)),
            (0.90, "Applying show LUT\n", "Color Balance", (100, 180)),
            (1.25, "Preview frame written: /tmp/pilot-001.png\n", "Composite", (420, 180)),
        )
        elapsed = now - state.started_at
        while state.demo_index < len(schedule) and elapsed >= schedule[state.demo_index][0]:
            _at, output, label, location = schedule[state.demo_index]
            _append_capture("stdout", output, redraw=False)
            state.step_label = label
            state.previous_location = state.step_location
            state.step_location = location
            state.step_at = now
            state.step_count += 1
            state.demo_index += 1
        if elapsed >= 1.65:
            finish_job({"ok": True, "result": {"result": "returncode=0"}}, 1650.0)

    if state.finished_at is not None:
        if state.terminal:
            expiry = _terminal_end_time(state)
        else:
            expiry = state.finished_at + 0.7 + 0.34
        if expiry is not None and now >= expiry:
            _STATE = None
            _request_redraw()
            return 0.5
    _request_redraw()
    return 1.0 / 60.0


def _draw_overlay() -> None:
    state = _STATE
    area = bpy.context.area
    region = bpy.context.region
    if state is None or area is None or region is None or region.type != "WINDOW":
        return
    if state.area_pointer is not None and int(area.as_pointer()) != state.area_pointer:
        return

    now = time.monotonic()
    alpha, scale = _visibility(state, now)
    if alpha <= 0.01 or scale <= 0.01:
        return

    gpu.state.blend_set("ALPHA")
    try:
        terminal_motion = None
        if state.terminal:
            terminal_motion = _terminal_motion(area, region, state, now)
            _draw_terminal(state, now, terminal_motion, alpha)
        if bool(_pref("show_agent_cursor", True)):
            if terminal_motion is not None:
                x = terminal_motion.cursor_x
                y = terminal_motion.cursor_y
                rotation = terminal_motion.cursor_rotation
                click = terminal_motion.click
            else:
                x, y, rotation = _cursor_pose(state, area, region, now)
                click = 0.0
            _draw_cursor(
                x,
                y,
                state,
                now,
                alpha,
                scale,
                float(region.width) - _right_inset(area),
                rotation,
                click,
                show_label=not state.terminal,
            )
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")


def _visibility(state: _Activity, now: float) -> tuple[float, float]:
    if state.finished_at is None:
        return 1.0, 1.0
    if state.terminal:
        close_start = _terminal_close_start(state)
        if close_start is None:
            return 1.0, 1.0
        cursor_exit = close_start + _TERMINAL_CLOSE
        leaving = (now - cursor_exit) / _TERMINAL_CURSOR_EXIT
        if leaving <= 0.0:
            return 1.0, 1.0
        return 1.0 - smoothstep(leaving), 1.0
    hold = 0.7
    leaving = (now - state.finished_at - hold) / 0.34
    if leaving <= 0.0:
        return 1.0, 1.0
    return 1.0 - smoothstep(leaving), exit_scale(leaving)


def _cursor_pose(state: _Activity, area, region, now: float) -> tuple[float, float, float]:
    idle = _idle_rotation(now)

    if state.step_location is not None and area.type == "NODE_EDITOR":
        location = state.step_location
        rotation = idle
        if state.previous_location is not None:
            amount_raw = max(0.0, min(1.0, (now - state.step_at) / 0.20))
            amount = spring_ease(amount_raw)
            location = _mix_point(state.previous_location, state.step_location, amount)
            moving = _travel_rotation(state.previous_location, state.step_location)
            rotation = _mix_angle(moving, idle, smoothstep((amount_raw - 0.72) / 0.28))
        point = _node_to_region(location, area, region)
        if point is not None:
            return point[0], point[1], rotation

    if state.waypoints and area.type == "NODE_EDITOR":
        elapsed = max(0.0, (min(now, state.finished_at) if state.finished_at else now)
                      - state.started_at)
        segment = elapsed / 0.20
        if len(state.waypoints) == 1 or segment >= len(state.waypoints) - 1:
            item = state.waypoints[-1]
            location = (item[1], item[2])
            state.step_label = state.step_label or item[0]
            rotation = idle
        else:
            index = max(0, int(segment))
            first = state.waypoints[index]
            second = state.waypoints[index + 1]
            amount_raw = segment - index
            location = _mix_point(
                (first[1], first[2]),
                (second[1], second[2]),
                spring_ease(amount_raw),
            )
            state.step_label = second[0]
            moving = _travel_rotation((first[1], first[2]), (second[1], second[2]))
            rotation = _mix_angle(moving, idle, smoothstep((amount_raw - 0.72) / 0.28))
        point = _node_to_region(location, area, region)
        if point is not None:
            return point[0], point[1], rotation

    effective_now = min(now, state.finished_at) if state.finished_at else now
    elapsed = max(0.0, effective_now - state.started_at)
    seed = sum((index + 1) * ord(char) for index, char in enumerate(state.command))
    phase = seed % 17 / 17.0 * math.tau
    x = region.width * (0.50 + 0.28 * math.sin(elapsed * 5.5 + phase))
    y = region.height * (0.52 + 0.22 * math.cos(elapsed * 4.1 + phase * 0.7))
    x, y = _clamp_cursor(x, y, area, region)
    if state.finished_at is not None:
        return x, y, idle
    previous_elapsed = max(0.0, elapsed - 0.016)
    previous = (
        region.width * (0.50 + 0.28 * math.sin(previous_elapsed * 5.5 + phase)),
        region.height * (0.52 + 0.22 * math.cos(previous_elapsed * 4.1 + phase * 0.7)),
    )
    return x, y, _travel_rotation(previous, (x, y))


def _node_to_region(location: tuple[float, float], area, region) -> tuple[float, float] | None:
    try:
        x, y = region.view2d.view_to_region(location[0], location[1], clip=False)
        return _clamp_cursor(float(x), float(y), area, region)
    except Exception:
        return None


def _idle_rotation(now: float) -> float:
    return _IDLE_TILT + math.radians(5.0) * math.sin(now * 4.2)


def _travel_rotation(first: tuple[float, float], second: tuple[float, float]) -> float:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    if abs(dx) + abs(dy) < 1e-6:
        return _IDLE_TILT
    return math.atan2(dy, dx) - math.pi * 0.5


def _mix_angle(first: float, second: float, amount: float) -> float:
    delta = (second - first + math.pi) % math.tau - math.pi
    return first + delta * max(0.0, min(1.0, amount))


def _mix_point(first: tuple[float, float], second: tuple[float, float],
               amount: float) -> tuple[float, float]:
    return (
        first[0] + (second[0] - first[0]) * amount,
        first[1] + (second[1] - first[1]) * amount,
    )


def _right_inset(area) -> float:
    try:
        return float(sum(
            region.width for region in area.regions
            if region.type == "UI" and region.width > 1
        ))
    except Exception:
        return 0.0


def _clamp_cursor(x: float, y: float, area, region) -> tuple[float, float]:
    right_edge = max(92.0, float(region.width) - _right_inset(area))
    return (
        max(46.0, min(right_edge - 46.0, x)),
        max(70.0, min(float(region.height) - 40.0, y)),
    )


def _draw_cursor(x: float, y: float, state: _Activity, now: float,
                 alpha: float, exit_amount: float, right_edge: float,
                 rotation: float, click: float, *, show_label: bool) -> None:
    ui_scale = float(_pref("activity_overlay_scale", 1.0))
    pulse = 1.0 + 0.06 * math.sin((now - state.started_at) * 11.0)
    size = 38.0 * ui_scale * pulse * exit_amount
    ring_radius = 13.0 + 5.0 * (0.5 + 0.5 * math.sin((now - state.started_at) * 8.0))
    _draw_circle(x, y, ring_radius, (0.24, 0.65, 1.0, 0.32 * alpha), 2.0)
    if click > 0.0:
        _draw_circle(x, y, 10.0 + click * 13.0,
                     (1.0, 0.24, 0.20, (1.0 - click * 0.45) * alpha), 2.8)

    cosine = math.cos(rotation)
    sine = math.sin(rotation)

    def transform(point):
        px, py = point
        return (
            x + (px * cosine - py * sine) * size,
            y + (px * sine + py * cosine) * size,
        )

    points = [transform(point) for point in _CURSOR_POINTS]
    tris = [transform(point) for point in _CURSOR_TRIS]
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    if tris:
        shader.bind()
        shader.uniform_float("color", (1.0, 1.0, 1.0, alpha))
        batch_for_shader(shader, "TRIS", {"pos": tris}).draw(shader)
    if points:
        gpu.state.line_width_set(max(1.0, 2.1 * ui_scale * exit_amount))
        shader.bind()
        shader.uniform_float("color", (0.025, 0.03, 0.04, alpha))
        batch_for_shader(shader, "LINE_STRIP", {"pos": points + [points[0]]}).draw(shader)

    if show_label:
        label = state.step_label or state.command
        _draw_pill(
            min(x + 18.0, right_edge - 210.0),
            max(16.0, y - 56.0),
            label,
            alpha,
            exit_amount,
        )


def _draw_pill(x: float, y: float, text: str, alpha: float, scale: float) -> None:
    text = str(text)
    if len(text) > 36:
        text = text[:33] + "…"
    width = min(280.0, max(94.0, 24.0 + len(text) * 7.2)) * scale
    height = 30.0 * scale
    _draw_round_rect(x, y, width, height, 9.0 * scale, (0.055, 0.065, 0.085, 0.92 * alpha))
    if scale > 0.25:
        _draw_text(text, x + 12.0 * scale, y + 8.0 * scale,
                   max(8, int(12 * scale)), (0.88, 0.93, 1.0, alpha))


def _terminal_motion(area, region, state: _Activity, now: float) -> _TerminalMotion:
    ui_scale = float(_pref("activity_overlay_scale", 1.0))
    right_inset = _right_inset(area)
    available_width = max(180.0, region.width - right_inset - 48.0)
    target_width = min(620.0 * ui_scale, available_width)
    target_height = min(300.0 * ui_scale, max(36.0, region.height - 48.0))
    usable_right = region.width - right_inset
    target_x = max(24.0, (usable_right - target_width) * 0.5)
    target_y = max(36.0, (region.height - target_height) * 0.5)
    header = 38.0 * ui_scale
    started = state.started_at
    seek_end = started + _TERMINAL_SEEK
    drag_end = seek_end + _TERMINAL_DRAG
    offscreen_x = float(region.width) + 42.0
    panel_offscreen_x = float(region.width) + 24.0
    initial_cursor = (usable_right * 0.58, region.height * 0.54)
    drag_grab_final = (
        target_x + target_width * 0.22,
        target_y + target_height - header * 0.50,
    )
    wait_point = (
        min(usable_right - 30.0, target_x + target_width + 25.0),
        max(34.0, target_y - 8.0),
    )
    red_button = (
        target_x + 17.0 * ui_scale,
        target_y + target_height - header * 0.50,
    )
    idle = _idle_rotation(now)
    click = 0.0
    panel_x = panel_offscreen_x
    panel_y = target_y
    panel_scale = 1.0
    panel_alpha = 0.0

    if now < seek_end:
        amount = spring_ease((now - started) / _TERMINAL_SEEK)
        cursor = _mix_point(initial_cursor, (offscreen_x, drag_grab_final[1]), amount)
        rotation = _mix_angle(
            _travel_rotation(initial_cursor, (offscreen_x, drag_grab_final[1])),
            idle,
            smoothstep((amount - 0.82) / 0.18),
        )
    elif now < drag_end:
        amount_raw = (now - seek_end) / _TERMINAL_DRAG
        amount = spring_ease(amount_raw)
        panel_x = panel_offscreen_x + (target_x - panel_offscreen_x) * amount
        panel_alpha = smoothstep(amount_raw * 2.0)
        cursor = (
            panel_x + target_width * 0.22,
            drag_grab_final[1],
        )
        rotation = _mix_angle(
            math.pi * 0.5,
            idle,
            smoothstep((amount_raw - 0.82) / 0.18),
        )
    else:
        panel_x = target_x
        panel_alpha = 1.0
        wait_amount_raw = min(1.0, (now - drag_end) / _TERMINAL_TO_WAIT)
        wait_amount = spring_ease(wait_amount_raw)
        cursor = _mix_point(drag_grab_final, wait_point, wait_amount)
        moving = _travel_rotation(drag_grab_final, wait_point)
        rotation = _mix_angle(moving, idle, smoothstep((wait_amount_raw - 0.72) / 0.28))

    click_start = _terminal_click_start(state)
    close_start = _terminal_close_start(state)
    if click_start is not None and now >= click_start:
        travel_raw = min(1.0, (now - click_start) / _TERMINAL_CLICK_TRAVEL)
        cursor = _mix_point(wait_point, red_button, spring_ease(travel_raw))
        moving = _travel_rotation(wait_point, red_button)
        rotation = _mix_angle(moving, idle, smoothstep((travel_raw - 0.76) / 0.24))
        press_start = click_start + _TERMINAL_CLICK_TRAVEL
        if now >= press_start:
            press_raw = min(1.0, (now - press_start) / _TERMINAL_CLICK_PRESS)
            cursor = red_button
            rotation = idle
            click = math.sin(press_raw * math.pi)

    if close_start is not None and now >= close_start:
        close_raw = min(1.0, (now - close_start) / _TERMINAL_CLOSE)
        panel_scale = exit_scale(close_raw)
        panel_alpha = 1.0 - smoothstep(close_raw)
        panel_x = red_button[0] + (target_x - red_button[0]) * panel_scale
        panel_y = red_button[1] + (target_y - red_button[1]) * panel_scale
        exit_target = (usable_right * 0.60, region.height * 0.46)
        cursor_raw = min(
            1.0,
            (now - close_start) / (_TERMINAL_CLOSE + _TERMINAL_CURSOR_EXIT),
        )
        cursor = _mix_point(red_button, exit_target, spring_ease(cursor_raw))
        moving = _travel_rotation(red_button, exit_target)
        rotation = _mix_angle(moving, idle, smoothstep((cursor_raw - 0.74) / 0.26))

    type_start = _terminal_type_start(state)
    type_end = _terminal_type_end(state)
    type_raw = max(0.0, min(1.0, (now - type_start) / max(0.001, type_end - type_start)))
    count = min(len(state.terminal_command), int(math.ceil(len(state.terminal_command) * type_raw)))
    typed = state.terminal_command[:count]
    typing = now < type_end
    if typing and now >= type_start and int(now * 5.0) % 2 == 0:
        typed += "▌"

    return _TerminalMotion(
        x=panel_x,
        y=panel_y,
        width=target_width * panel_scale,
        height=target_height * panel_scale,
        alpha=panel_alpha,
        scale=panel_scale,
        cursor_x=cursor[0],
        cursor_y=cursor[1],
        cursor_rotation=rotation,
        click=click,
        typed_command=typed,
        typing=typing,
        output_visible=now >= type_end,
    )


def _draw_terminal(state: _Activity, now: float, motion: _TerminalMotion,
                   global_alpha: float) -> None:
    ui_scale = float(_pref("activity_overlay_scale", 1.0))
    scale = motion.scale
    visual_scale = ui_scale * scale
    alpha = motion.alpha * global_alpha
    x, y, width, height = motion.x, motion.y, motion.width, motion.height
    if alpha <= 0.01 or width <= 2.0 or height <= 2.0:
        return
    radius = 13.0 * min(1.0, visual_scale)
    _draw_round_rect(x + 4.0 * visual_scale, y - 5.0 * visual_scale,
                     width, height, radius,
                     (0.0, 0.0, 0.0, 0.24 * alpha))
    _draw_round_rect(x, y, width, height, radius, (0.035, 0.039, 0.050, 0.97 * alpha))
    header_height = min(38.0 * visual_scale, height)
    _draw_round_rect(x, y + height - header_height, width, header_height, radius,
                     (0.105, 0.11, 0.13, 0.98 * alpha))

    if scale <= 0.22:
        return
    dot_y = y + height - header_height * 0.50
    dot_radius = max(2.0, 5.2 * visual_scale)
    red_brightness = 0.20 * motion.click
    for offset, color in (
        (17.0, (1.0, 0.37 + red_brightness, 0.34 + red_brightness, alpha)),
        (35.0, (1.0, 0.75, 0.25, alpha)),
        (53.0, (0.20, 0.80, 0.35, alpha)),
    ):
        _draw_disc(x + offset * visual_scale, dot_y, dot_radius, color)

    if motion.typing:
        status = "Agent Terminal — typing command"
        status_color = (0.84, 0.88, 0.94, alpha)
    elif state.finished_at is None:
        dots = "." * (1 + int((now - state.started_at) * 3.5) % 3)
        status = f"Agent Terminal — running{dots}"
        status_color = (0.84, 0.88, 0.94, alpha)
    elif state.ok:
        status = f"Agent Terminal — done in {state.elapsed_ms:.0f} ms"
        status_color = (0.45, 0.90, 0.58, alpha)
    else:
        status = "Agent Terminal — failed"
        status_color = (1.0, 0.48, 0.45, alpha)
    font_size = max(8, int(12 * visual_scale))
    _draw_text(status, x + 72.0 * visual_scale, dot_y - font_size * 0.36,
               font_size, status_color)

    inner_x = x + 18.0 * visual_scale
    top = y + height - header_height - 17.0 * visual_scale
    chars = max(20, int((width - 36.0 * visual_scale) / max(5.0, 7.2 * visual_scale)))
    command_lines = textwrap.wrap("$ " + (motion.typed_command or ""), width=chars)[:3]
    body_size = max(8, int(12 * visual_scale))
    line_height = max(11.0, 17.0 * visual_scale)
    cursor_y = top
    for line in command_lines:
        _draw_text(line, inner_x, cursor_y, body_size, (0.46, 0.88, 0.60, alpha))
        cursor_y -= line_height

    lines: list[str] = []
    if motion.output_visible:
        for raw in state.output.replace("\t", "    ").splitlines():
            wrapped = textwrap.wrap(raw, width=chars, replace_whitespace=False) or [""]
            lines.extend(wrapped)
    available = max(1, int((cursor_y - y - 14.0 * visual_scale) / line_height))
    lines = lines[-available:]
    for line in lines:
        cursor_y -= line_height
        _draw_text(line, inner_x, cursor_y, body_size, (0.78, 0.82, 0.88, alpha))


def _draw_text(text: str, x: float, y: float, size: int,
               color: tuple[float, float, float, float]) -> None:
    font_id = 0
    blf.size(font_id, max(1, int(size)))
    blf.color(font_id, *color)
    blf.position(font_id, float(x), float(y), 0.0)
    blf.draw(font_id, str(text))


def _draw_round_rect(x: float, y: float, width: float, height: float, radius: float,
                     color: tuple[float, float, float, float]) -> None:
    if width <= 0.0 or height <= 0.0:
        return
    radius = min(max(0.0, radius), width * 0.5, height * 0.5)
    boundary: list[tuple[float, float]] = []
    for cx, cy, start in (
        (x + width - radius, y + height - radius, 0.0),
        (x + radius, y + height - radius, math.pi * 0.5),
        (x + radius, y + radius, math.pi),
        (x + width - radius, y + radius, math.pi * 1.5),
    ):
        for index in range(5):
            angle = start + index / 4.0 * math.pi * 0.5
            boundary.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    center = (x + width * 0.5, y + height * 0.5)
    vertices: list[tuple[float, float]] = []
    for index, point in enumerate(boundary):
        vertices.extend((center, point, boundary[(index + 1) % len(boundary)]))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": vertices}).draw(shader)


def _draw_circle(x: float, y: float, radius: float,
                 color: tuple[float, float, float, float], width: float) -> None:
    points = [
        (x + math.cos(index / 32.0 * math.tau) * radius,
         y + math.sin(index / 32.0 * math.tau) * radius)
        for index in range(33)
    ]
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    gpu.state.line_width_set(width)
    batch_for_shader(shader, "LINE_STRIP", {"pos": points}).draw(shader)


def _draw_disc(x: float, y: float, radius: float,
               color: tuple[float, float, float, float]) -> None:
    center = (x, y)
    ring = [
        (x + math.cos(index / 16.0 * math.tau) * radius,
         y + math.sin(index / 16.0 * math.tau) * radius)
        for index in range(16)
    ]
    vertices: list[tuple[float, float]] = []
    for index, point in enumerate(ring):
        vertices.extend((center, point, ring[(index + 1) % len(ring)]))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": vertices}).draw(shader)


def _python_command_preview(code: str) -> str:
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    if not lines:
        return "python (empty script)"
    preview = lines[0]
    if len(lines) > 1:
        preview += f"  … +{len(lines) - 1} lines"
    if len(preview) > 120:
        preview = preview[:117] + "…"
    return "python: " + preview


def _load_cursor_svg() -> None:
    global _CURSOR_POINTS, _CURSOR_TRIS
    try:
        source = _SVG_PATH.read_text(encoding="utf-8")
        match = re.search(r"\bd=\"([^\"]+)\"", source)
        if match is None:
            raise ValueError("SVG has no path data")
        points = _sample_svg_path(match.group(1))
    except Exception as exc:
        print(f"[agent-mcp] using fallback cursor geometry: {exc}")
        points = [(-0.45, -0.95), (0.0, 0.0), (0.45, -0.95), (0.10, -0.72), (0.0, -0.58), (-0.10, -0.72)]

    _CURSOR_POINTS = points
    try:
        # Blender 5.2's tessellator returns no faces for this clockwise contour
        # when it is represented by 2D vectors.  Explicit 3D vectors preserve
        # the supplied SVG winding and produce the expected concave fill.
        triangles = tessellate_polygon([[
            Vector((point[0], point[1], 0.0)) for point in points
        ]])
        _CURSOR_TRIS = [
            points[vertex] if isinstance(vertex, int)
            else (float(vertex.x), float(vertex.y))
            for tri in triangles
            for vertex in tri
        ]
    except Exception:
        _CURSOR_TRIS = []


def _sample_svg_path(path_data: str) -> list[tuple[float, float]]:
    tokens = re.findall(r"[MCZ]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", path_data)
    index = 0
    command = ""
    current = (0.0, 0.0)
    raw: list[tuple[float, float]] = []
    while index < len(tokens):
        token = tokens[index]
        if token in {"M", "C", "Z"}:
            command = token
            index += 1
            if command == "Z":
                break
            continue
        if command == "M":
            current = (float(tokens[index]), float(tokens[index + 1]))
            raw.append(current)
            index += 2
            command = "C"
            continue
        if command == "C":
            p0 = current
            p1 = (float(tokens[index]), float(tokens[index + 1]))
            p2 = (float(tokens[index + 2]), float(tokens[index + 3]))
            p3 = (float(tokens[index + 4]), float(tokens[index + 5]))
            for step_index in range(1, 9):
                amount = step_index / 8.0
                raw.append(_bezier(p0, p1, p2, p3, amount))
            current = p3
            index += 6
            continue
        raise ValueError(f"unsupported SVG path token {token!r}")
    if len(raw) < 3:
        raise ValueError("SVG cursor path produced fewer than three points")

    min_x = min(point[0] for point in raw)
    max_x = max(point[0] for point in raw)
    tip_y = min(point[1] for point in raw)
    max_y = max(point[1] for point in raw)
    span = max(max_x - min_x, max_y - tip_y, 1.0)
    center_x = (min_x + max_x) * 0.5
    return [((x - center_x) / span, -(y - tip_y) / span) for x, y in raw]


def _bezier(p0, p1, p2, p3, amount: float) -> tuple[float, float]:
    inv = 1.0 - amount
    return (
        inv ** 3 * p0[0] + 3 * inv * inv * amount * p1[0]
        + 3 * inv * amount * amount * p2[0] + amount ** 3 * p3[0],
        inv ** 3 * p0[1] + 3 * inv * inv * amount * p1[1]
        + 3 * inv * amount * amount * p2[1] + amount ** 3 * p3[1],
    )
