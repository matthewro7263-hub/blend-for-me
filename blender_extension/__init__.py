"""Agent MCP Bridge — Blender extension entry point.

Registers preferences, a 3D-Viewport N-panel ("Agent MCP") and the operators that
start/stop the loopback bridge.
"""

from __future__ import annotations

import os

import bpy
from bpy.app.handlers import persistent

from . import activity_ui, bridge, protocol, registry
from . import handlers as _handlers  # noqa: F401  (import registers commands)


# ---------------------------------------------------------------------------
# preferences
# ---------------------------------------------------------------------------

class AgentMCPPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    port: bpy.props.IntProperty(
        name="Port",
        description="TCP port the bridge listens on (loopback only). "
                    "The BLENDER_AGENT_PORT environment variable overrides this",
        default=protocol.DEFAULT_PORT,
        min=1024,
        max=65535,
    )
    autostart: bpy.props.BoolProperty(
        name="Start bridge on file load",
        description="Automatically start the bridge when Blender finishes loading a file",
        default=False,
    )
    show_activity_overlay: bpy.props.BoolProperty(
        name="Show agent activity",
        description="Draw a visible agent cursor in the editor being changed",
        default=True,
    )
    show_agent_cursor: bpy.props.BoolProperty(
        name="Show cursor",
        description="Show the supplied Blend for me cursor while commands run",
        default=True,
    )
    show_terminal_overlay: bpy.props.BoolProperty(
        name="Show Python terminal",
        description="Show execute_python code and streaming stdout/stderr in Blender",
        default=True,
    )
    activity_overlay_scale: bpy.props.FloatProperty(
        name="Overlay scale",
        description="Scale the agent cursor and terminal window",
        default=1.0,
        min=0.65,
        max=1.6,
        subtype="FACTOR",
    )
    terminal_hold_seconds: bpy.props.FloatProperty(
        name="Terminal pause",
        description="Seconds the cursor waits after completion before clicking the red close button",
        default=0.65,
        min=0.2,
        max=8.0,
        subtype="TIME",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "port")
        layout.prop(self, "autostart")
        presence = layout.box()
        presence.label(text="Agent presence", icon="GHOST_ENABLED")
        presence.prop(self, "show_activity_overlay")
        column = presence.column(align=True)
        column.enabled = self.show_activity_overlay
        column.prop(self, "show_agent_cursor")
        column.prop(self, "show_terminal_overlay")
        column.prop(self, "activity_overlay_scale")
        column.prop(self, "terminal_hold_seconds")
        layout.label(
            text="The bridge binds 127.0.0.1 only and is never reachable off this machine.",
            icon="INFO",
        )


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def effective_port() -> int:
    """Env var wins over preferences, so tests can run on a private port."""
    env = os.environ.get(protocol.PORT_ENV_VAR)
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    prefs = _prefs()
    return prefs.port if prefs else protocol.DEFAULT_PORT


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

class AGENTMCP_OT_start(bpy.types.Operator):
    bl_idname = "agentmcp.start"
    bl_label = "Start Server"
    bl_description = "Start the loopback bridge so MCP clients can connect"

    @classmethod
    def poll(cls, context):
        return not bridge.is_running()

    def execute(self, context):
        port = effective_port()
        try:
            bridge.start_server(protocol.DEFAULT_HOST, port)
        except OSError as exc:
            if getattr(exc, "errno", None) in (48, 98):  # EADDRINUSE
                self.report({"ERROR"},
                            f"Port {port} is already in use. Change the port in the "
                            f"panel, or stop the other process.")
            else:
                self.report({"ERROR"}, f"Could not start bridge: {exc}")
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start bridge: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Agent MCP bridge listening on 127.0.0.1:{port}")
        _tag_redraw()
        return {"FINISHED"}


class AGENTMCP_OT_stop(bpy.types.Operator):
    bl_idname = "agentmcp.stop"
    bl_label = "Stop Server"
    bl_description = "Stop the loopback bridge"

    @classmethod
    def poll(cls, context):
        return bridge.is_running()

    def execute(self, context):
        bridge.stop_server()
        self.report({"INFO"}, "Agent MCP bridge stopped")
        _tag_redraw()
        return {"FINISHED"}


class AGENTMCP_OT_clear_log(bpy.types.Operator):
    bl_idname = "agentmcp.clear_log"
    bl_label = "Clear Log"
    bl_description = "Clear the bridge activity log"

    def execute(self, context):
        bridge.LOG.clear()
        _tag_redraw()
        return {"FINISHED"}


class AGENTMCP_OT_preview_activity(bpy.types.Operator):
    bl_idname = "agentmcp.preview_activity"
    bl_label = "Preview Agent UI"
    bl_description = "Preview the cursor and live terminal overlays in the current workspace"

    @classmethod
    def poll(cls, context):
        return not bpy.app.background

    def execute(self, context):
        if not activity_ui.start_demo():
            self.report({"WARNING"}, "Enable Show agent activity first")
            return {"CANCELLED"}
        self.report({"INFO"}, "Agent activity preview started")
        return {"FINISHED"}


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        if window.screen is None:
            continue
        for area in window.screen.areas:
            area.tag_redraw()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class AGENTMCP_PT_panel(bpy.types.Panel):
    bl_label = "Blend for me"
    bl_idname = "AGENTMCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Agent MCP"

    def draw(self, context):
        layout = self.layout
        prefs = _prefs()
        running = bridge.is_running()

        box = layout.box()
        row = box.row()
        row.label(
            text=f"Connected on port {bridge.current_port()}" if running else "Stopped",
            icon="LINKED" if running else "UNLINKED",
        )

        row = box.row(align=True)
        row.operator("agentmcp.start", icon="PLAY")
        row.operator("agentmcp.stop", icon="PAUSE")

        if prefs is not None:
            col = box.column(align=True)
            col.enabled = not running
            col.prop(prefs, "port")
            col.prop(prefs, "autostart")
            if os.environ.get(protocol.PORT_ENV_VAR):
                box.label(text=f"{protocol.PORT_ENV_VAR} overrides the port", icon="INFO")

        presence = layout.box()
        row = presence.row()
        row.label(text="Agent presence", icon="GHOST_ENABLED")
        row.operator("agentmcp.preview_activity", text="Preview", icon="PLAY")
        if prefs is not None:
            presence.prop(prefs, "show_activity_overlay")
            controls = presence.column(align=True)
            controls.enabled = prefs.show_activity_overlay
            controls.prop(prefs, "show_agent_cursor")
            controls.prop(prefs, "show_terminal_overlay")
            controls.prop(prefs, "activity_overlay_scale")
            controls.prop(prefs, "terminal_hold_seconds")
        activity = activity_ui.snapshot()
        if activity.get("active"):
            editor = str(activity.get("editor") or "editor").replace("_", " ").title()
            presence.label(text=f"{activity['command']} · {editor}", icon="REC")
            if activity.get("step"):
                presence.label(text=f"step: {activity['step']}")

        stats = bridge.STATS
        info = box.row()
        info.label(text=f"{stats['commands']} cmds · {stats['errors']} errors")
        if stats["last_cmd"]:
            box.label(text=f"last: {stats['last_cmd']} ({stats['last_ms']:.0f} ms)")

        if not getattr(bpy.app, "online_access", True):
            box.label(text="Online access is off (fine: bridge is loopback)", icon="INFO")

        layout.separator()
        header = layout.row()
        header.label(text=f"Log · {len(registry.HANDLERS)} commands")
        header.operator("agentmcp.clear_log", text="", icon="TRASH")

        log_box = layout.box()
        lines = bridge.recent_log(20)
        if not lines:
            log_box.label(text="no activity yet")
        for line in lines:
            log_box.label(text=line)


# ---------------------------------------------------------------------------
# load handler / registration
# ---------------------------------------------------------------------------

@persistent
def _on_load_post(_dummy):
    prefs = _prefs()
    if prefs is not None and prefs.autostart and not bridge.is_running():
        try:
            bridge.start_server(protocol.DEFAULT_HOST, effective_port())
        except Exception as exc:
            print(f"[agent-mcp] autostart failed: {exc}")


_CLASSES = (
    AgentMCPPreferences,
    AGENTMCP_OT_start,
    AGENTMCP_OT_stop,
    AGENTMCP_OT_clear_log,
    AGENTMCP_OT_preview_activity,
    AGENTMCP_PT_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    activity_ui.register()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    try:
        bridge.stop_server()
    except Exception:
        pass
    activity_ui.unregister()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
