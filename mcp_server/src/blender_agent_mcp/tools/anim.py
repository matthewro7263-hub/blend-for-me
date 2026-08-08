"""Animation tools: frames, keyframes, interpolation, actions, NLA, playblast."""

from __future__ import annotations

from typing import Any, Optional

from ..server import call, clean


def register(mcp) -> None:

    @mcp.tool()
    def set_frame(frame: int) -> dict:
        """Move the playhead to a frame.

        Set this before reading transforms or taking a screenshot of an animated
        scene — object positions are evaluated at the current frame.
        """
        return call("anim.set_frame", {"frame": frame})

    @mcp.tool()
    def set_frame_range(
        start: Optional[int] = None,
        end: Optional[int] = None,
        step: Optional[int] = None,
    ) -> dict:
        """Set the scene's playback/render frame range.

        An end before the start is refused. Blender otherwise clamps the two
        against each other and silently accepts a range you did not ask for.
        """
        return call("anim.set_frame_range", clean(start=start, end=end, step=step))

    @mcp.tool()
    def set_fps(fps: int, fps_base: Optional[float] = None) -> dict:
        """Set the scene frame rate.

        Args:
            fps: Whole frames per second (24, 25, 30, 60).
            fps_base: Divisor for fractional rates — 30 with `fps_base` 1.001
                gives NTSC 29.97. Leave it alone for whole rates.

        Changing fps does **not** rescale existing keyframes; a 24 fps animation
        played at 48 runs twice as fast.
        """
        return call("anim.set_fps", clean(fps=fps, fps_base=fps_base))

    @mcp.tool()
    def insert_keyframe(
        data_path: str,
        frame: Optional[int] = None,
        object: Optional[str] = None,
        bone: Optional[str] = None,
        index: int = -1,
        value: Optional[Any] = None,
    ) -> dict:
        """Key a property at a frame, optionally setting its value first.

        Args:
            data_path: Property to key — `location`, `rotation_euler`, `scale`,
                `rotation_quaternion`, `hide_viewport`, etc.
            frame: Frame to key at. Defaults to the current frame.
            object: Object to key. Defaults to the active object.
            bone: Pose-bone name for an armature. The full path
                `pose.bones["name"].<data_path>` is built for you — pass the
                short `data_path` and let this handle it.
            index: Which component of a vector to key (0=x, 1=y, 2=z). -1 keys
                all of them.
            value: Set this value before keying, so the key records what you
                intend rather than whatever happened to be there. Euler
                rotations are **radians**; quaternions are [w, x, y, z].

        A bone's rotation channel depends on its `rotation_mode` — keying
        `rotation_euler` on a quaternion bone will not animate it. Check with
        `get_object_info` if a key appears to do nothing.
        """
        return call("anim.insert_keyframe", clean(
            data_path=data_path, frame=frame, object=object, bone=bone,
            index=index, value=value))

    @mcp.tool()
    def insert_keyframes_bulk(tracks: list[dict[str, Any]]) -> dict:
        """Animate many objects, bones, channels and custom properties in one call.

        Use this for a complete motion beat instead of making one MCP round trip
        per key. The whole operation is one Blender undo step.

        Each track requires:

        - `object`: exact object name;
        - `data_path`: `location`, `rotation_euler`, `scale`, `hide_render`, or a
          custom property path such as `[\"mouth_open\"]`;
        - `keys`: `[{"frame": 1, "value": [...]}, ...]`.

        Optional track fields are `bone`, `index` (-1 keys every vector component),
        `interpolation`, `easing`, and `clear_range: [start,end]`. A key may override
        `interpolation`/`easing`. Supported interpolation includes `CONSTANT`,
        `LINEAR`, `BEZIER`, and Blender easing curves. Euler values are radians;
        quaternion values are `[w,x,y,z]`.

        Custom properties must already exist (create them with
        `set_custom_property`). Existing keys at the same frame are updated.
        `clear_range` removes matching channel points first, making generated takes
        deterministic without deleting unrelated channels.

        Returns per-track action/channel details, requested keys inserted, actual
        F-Curve points touched (a vector key creates several), and points cleared.
        One call is capped at 500 tracks / 10,000 requested keys.
        """
        return call("anim.insert_keyframes_bulk", {"tracks": tracks}, timeout=120.0)

    @mcp.tool()
    def remove_keyframe(
        data_path: str,
        frame: Optional[int] = None,
        object: Optional[str] = None,
        bone: Optional[str] = None,
        index: int = -1,
    ) -> dict:
        """Delete a keyframe. Errors when there is no key at that frame."""
        return call("anim.remove_keyframe", clean(
            data_path=data_path, frame=frame, object=object, bone=bone, index=index))

    @mcp.tool()
    def list_keyframes(
        object: Optional[str] = None,
        data_path: Optional[str] = None,
        limit: int = 200,
    ) -> dict:
        """List an object's animated channels and their keyframes.

        Each channel reports its `data_path`, `array_index`, and every keyframe's
        frame, value, interpolation and easing. Use it to inspect what is
        actually animated before editing curves. Long channels are capped by
        `limit` and flagged `truncated`.
        """
        return call("anim.list_keyframes", clean(
            object=object, data_path=data_path, limit=limit))

    @mcp.tool()
    def set_interpolation(
        interpolation: Optional[str] = None,
        easing: Optional[str] = None,
        object: Optional[str] = None,
        data_path: Optional[str] = None,
        frame_range: Optional[list[int]] = None,
    ) -> dict:
        """Set interpolation and easing on existing keyframes.

        Args:
            interpolation: CONSTANT (stepped — for mechanical/robotic motion),
                LINEAR (constant speed), BEZIER (smooth, the default), or an
                easing curve: SINE, QUAD, CUBIC, QUART, QUINT, EXPO, CIRC, BACK,
                BOUNCE, ELASTIC.
            easing: AUTO, EASE_IN, EASE_OUT or EASE_IN_OUT — which end of the
                curve the easing applies to. Only meaningful for the easing
                interpolation types, not for LINEAR or CONSTANT.
            data_path: Restrict to one channel. Omit to change every channel.
            frame_range: [first, last] — only keys inside this range.

        Applies to keys that already exist; it does not create any.
        """
        return call("anim.set_interpolation", clean(
            interpolation=interpolation, easing=easing, object=object,
            data_path=data_path, frame_range=frame_range))

    @mcp.tool()
    def list_actions() -> dict:
        """List every action in the file with frame range, curve count and users.

        An action with `users: 0` is unused and will be dropped when the file is
        saved and reopened unless it has a fake user.
        """
        return call("anim.list_actions")

    @mcp.tool()
    def assign_action(
        action: str,
        object: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> dict:
        """Assign an action to an object, creating it if it does not exist.

        Use this to start a named animation clip before keying, so takes stay
        separable and can later be pushed into the NLA.
        """
        return call("anim.assign_action", clean(
            action=action, object=object, create_if_missing=create_if_missing))

    @mcp.tool()
    def nla_push_down(
        object: Optional[str] = None, track_name: Optional[str] = None,
    ) -> dict:
        """Push the active action down into a new NLA strip.

        This is how you bank a finished clip: the action becomes a strip on an
        NLA track and the object is left with no active action, ready for the
        next take. Non-destructive — the action datablock is unchanged.
        """
        return call("anim.nla_push_down", clean(object=object, track_name=track_name))

    @mcp.tool()
    def playblast(
        out_path: str,
        frame_start: Optional[int] = None,
        frame_end: Optional[int] = None,
        format: str = "MP4",
        resolution: Optional[list[int]] = None,
        fps: Optional[int] = None,
        percentage: int = 100,
        timeout: float = 600.0,
    ) -> dict:
        """Render an OpenGL preview of the animation. GUI Blender only — fails under `blender --background`.

        Fast viewport-quality playback preview, not a final render — use
        `render_frame` for quality.

        Args:
            out_path: Destination. For PNG this is a filename **prefix** and
                Blender appends frame numbers; for MP4 it is the movie file.
            frame_start / frame_end: Range to render. Defaults to the scene range.
            format: MP4 (H.264) or PNG (image sequence).
            percentage: Resolution percentage — 50 halves it for a quick check.

        Overwrites `out_path` without asking. Returns the path and frame count,
        never the video bytes. Scene render settings are restored afterwards.
        """
        return call("anim.playblast", clean(
            out_path=out_path, frame_start=frame_start, frame_end=frame_end,
            format=format, resolution=resolution, fps=fps, percentage=percentage),
            timeout=timeout)
