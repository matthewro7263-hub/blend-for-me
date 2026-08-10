"""Manual GUI acceptance preview for the agent cursor and terminal overlays.

Run in a disposable Blender window:

    /Applications/Blender.app/Contents/MacOS/Blender \
      --factory-startup --python tests/gui_activity_demo.py

The window closes itself after twelve seconds. It exercises the real bridge,
Python capture and subprocess-streaming path; the N-panel Preview button offers
a lighter in-session preview without changing the current editor.
"""

from __future__ import annotations

import pathlib
import sys

import bpy


REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import blender_extension  # noqa: E402
from blender_extension import bridge  # noqa: E402


def main() -> None:
    bpy.context.preferences.view.show_splash = False
    blender_extension.register()
    def start_preview():
        for window in bpy.context.window_manager.windows:
            if window.screen is None:
                continue
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.type = "NODE_EDITOR"
                    area.spaces.active.tree_type = "CompositorNodeTree"
                    break
        code = """\
# compositor activity preview
import time
for label, location in [
    ("Render Layers", [-520, 180]),
    ("Glare", [-200, 180]),
    ("Color Balance", [100, 180]),
    ("Composite", [420, 180]),
]:
    agent_activity.step(label, location)
    print(f"Created {label}")
    time.sleep(0.08)
run_terminal("for line in preparing assets lighting rendering writing; do echo $line; sleep 0.55; done")
"""
        response = bridge._execute(bridge._Job("gui-preview", "execute_python", {"code": code}))
        print("GUI_ACTIVITY_RESULT", response.get("ok"), flush=True)
        return None

    def close_preview():
        blender_extension.unregister()
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(start_preview, first_interval=4.0)
    bpy.app.timers.register(close_preview, first_interval=15.0)


main()
