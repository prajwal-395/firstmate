"""The declared look on the REELS path: frame, punch-in, power, drift.

`library/tools/tv_frame.py` declares the punched-in TV-frame look and
`library/tools/tv_power.py` the switch-on and switch-off timings.  Both
landed on the MASTER path only: `step_5_04_compile_manifest` reads them
into an assembly manifest and `resolve_build_timeline` places it.  The
reels path is a different placer - `reel_build.build_reel_timeline`
drives Resolve directly, writes no manifest, and applied no Fusion comp
at all - so a project that declared the look got it on its master video
and not on one reel cut out of that video.

Measured 2026-09-09 on the fifteen reels the captain approved before the
field-test project was reset (`approved-reels-before-reset.json`): every
picture item on every one of them carries `zoom: 1.0`, on V1 and V2
alike.  That is the whole of "otherwise its just a still shot" - the
reels placer had no route to a zoom, animated or static, and no route to
a bezel.

This module is that route, and it adds no look of its own:

- WHAT the look is comes from `tv_frame.resolve_tv_frame` - the asset,
  the punch-in factor, the power timings - which reads the project's own
  declaration first and its brand template second.  A project that
  declares nothing gets `None` here and the reel it got before.
- WHICH shots drift, HOW FAR and WHY is a model's answer, resolved by
  step 4.03's own `post_bridge.resolve_vfx`.  The captain's ruling of
  2026-09-08 - motion is never a blanket rule, and an entry with no
  per-shot rationale is dropped as `no_stated_reason` - therefore holds
  on a reel by being the same code, not by being restated here.  There
  is no default drift and no fallback direction.
- HOW a comp reaches the picture is
  `library/tools/execution/apply_fusion_comps.py`, unchanged, driven
  with a manifest built from the reel's own placements.  Process
  isolation (AGENTS.md 5) is why it runs as a subprocess: the timeline
  was created in the calling process.

Under the look the reel's footage collapses to V1
-------------------------------------------------
`tv_frame.LAYER_TRACKS` is footage V1, frame V2, captions V3, and
`tv_frame` already reasons that a reel under this look carries no B-roll
on V2 because the frame spans it.  A reel's picture normally inherits
the MASTER's track index, so a two-camera master puts one speaker on V1
and the other on V2 - sequentially, never at once.  Under the look those
placements collapse onto V1 so V2 is free for the frame, and
`assert_one_picture_at_a_time` REFUSES rather than collapsing when two
of them really do overlap: two pictures on one track is not a composite,
and silently dropping one would be the placer choosing a camera.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

FRAME_TRACK = 2
"""The reel video track the frame asset is placed on.

Named once, here, for the same reason `reel_semantic_visual.SEMANTIC_TRACK`
is named once: the placer and anything reading the built timeline back
must not disagree about it.  It is V2 because that is what
`tv_frame.LAYER_TRACKS` declares and what the captain's own reference
capture shows (`reel20-standard-zoom.json`: V1 footage at 2.30, V2
`TV 4k.png` at 1.00, V3 captions at 1.00).
"""

MOTION_PLAN_KEY = "reel_motion_plan"
"""What the model's answer file carries, as `read_answer` accepts a bare list."""

MOTION_NOT_DECLARED = "look_not_declared"
MOTION_AWAITING_ANSWER = "awaiting_model_answer"
MOTION_PLANNED_NONE = "model_planned_none"
MOTION_EVERY_ENTRY_DROPPED = "every_entry_dropped"
MOTION_PLANNED = "planned"

MOTION_BASES = (MOTION_NOT_DECLARED, MOTION_AWAITING_ANSWER,
                MOTION_PLANNED_NONE, MOTION_EVERY_ENTRY_DROPPED,
                MOTION_PLANNED)
"""Why a reel carries the drift it carries, including none."""


class ReelLookRefused(RuntimeError):
    """The look cannot be placed on this reel, and this says why."""


def resolve_look(project_folder: str, frame_width: int = 0,
                 frame_height: int = 0) -> Optional[dict]:
    """This project's TV-frame declaration, or None for no look.

    Delegates entirely to `tv_frame.resolve_tv_frame`, resolving the
    brand template the same way `reel_build.declared_cards` does, so a
    reel and its master read one declaration.

    Given the delivery frame, the declaration is also CHECKED against it
    before it is returned - `tv_frame.assert_frameable`, which refuses a
    frame with no transparent window and one that covering would upscale
    beyond its own pixels.  A mismatched ASPECT is not refused: it is
    cover-scaled (`tv_frame.cover_zoom`), which is what the captain did
    by hand on 2026-09-09.  A caller that passes no frame gets the
    declaration unchecked, and the two callers that build something both
    pass one.
    """
    from library.tools.tv_frame import assert_frameable, resolve_tv_frame

    template = None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if os.path.exists(project_yaml):
        import yaml
        from library.tools.brand_registry import resolve_project_template
        with open(project_yaml, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        named = ((config.get("pipeline") or {}).get("brand_template") or "")
        if named:
            template = resolve_project_template(named)
    look = resolve_tv_frame(project_folder, template)
    if look is not None and frame_width and frame_height:
        assert_frameable(look, frame_width, frame_height)
    return look


def _picture(placements: Sequence[dict]) -> List[dict]:
    """The video placements, in play order."""
    out = [p for p in placements
           if getattr(p["clip"], "track_type", "video") == "video"]
    out.sort(key=lambda p: p["snapped_record"])
    return out


def assert_one_picture_at_a_time(placements: Sequence[dict],
                                 fps: float) -> None:
    """Refuse a reel whose picture placements overlap in time.

    Collapsing overlapping placements onto V1 would put two pictures on
    one track, which Resolve resolves by trimming one - so the placer
    would be choosing a camera.  That is a decision this module has no
    business taking, and the refusal names the seconds.
    """
    previous = None
    for p in _picture(placements):
        start = int(p["snapped_record"])
        end = start + int(round((p["source_out"] - p["source_in"]) * fps))
        if previous is not None and start < previous[1]:
            raise ReelLookRefused(
                f"the TV-frame look collapses this reel's picture onto V1, "
                f"but two placements overlap: {previous[2]} runs to frame "
                f"{previous[1]} and {p['clip'].source_file} starts at frame "
                f"{start}. Two pictures on one track is not a composite, and "
                f"dropping one here would be the placer choosing a camera. "
                f"Redraw the keep range or build this reel without the look.")
        previous = (start, end, p["clip"].source_file)


def collapse_to_v1(placements: Sequence[dict]) -> List[dict]:
    """Every VIDEO placement moved to V1; audio untouched.

    Returns new dicts - the caller's list is left alone, because the
    record it writes to provenance must say where the master had the
    clip, not where the look put it.
    """
    out = []
    for p in placements:
        if getattr(p["clip"], "track_type", "video") == "video":
            moved = dict(p)
            moved["track_index"] = 1
            out.append(moved)
        else:
            out.append(p)
    return out


def frame_runs(placements: Sequence[dict], fps: float) -> List[Tuple[int, int]]:
    """Contiguous (start_frame, end_frame) runs of picture on the reel.

    One frame clip per run, exactly as `compile_manifest._content_runs`
    does on the master: the set dresses the show, and a gap in the
    picture is a gap the set has nothing to sit on.
    """
    runs: List[List[int]] = []
    for p in _picture(placements):
        start = int(p["snapped_record"])
        end = start + int(round((p["source_out"] - p["source_in"]) * fps))
        if runs and start <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    return [(a, b) for a, b in runs]


FRAME_OVERLAY_NAME_SHAPE = r"^tv_frame_[0-9a-f]{10}(_\d+f)?$"
"""What a rendered frame overlay is called, as a pattern.

Read by the conformance verifier the same way `CARD_NAME_SHAPE` is: an
item on the frame track shaped like this and accounted for by the
declaration is the set, and one that is NOT accounted for is an overlay
appended out of band.

The duration suffix is OPTIONAL, not absent: renders written before
2026-09-09 carry `_<frames>f` (one file per length), and live timelines
still place those files until the captain re-points them.  A shape that
dropped either half would misread the set - the old files as out-of-band
overlays, or the new shared file as one.
"""


def frame_overlay_items(video_items, look) -> set:
    """The ids of timeline items that are this look's frame overlay.

    `look` None returns the empty set, so a project that declares no
    look excludes nothing and the verifier reads exactly what it read
    before.  Matching is on the frame TRACK and the render name shape
    together.
    """
    import re

    if look is None:
        return set()
    shape = re.compile(FRAME_OVERLAY_NAME_SHAPE)
    out = set()
    for item in video_items or ():
        if getattr(item, "track_index", 0) != FRAME_TRACK:
            continue
        stem = (getattr(item, "source_file", "") or "").rsplit(
            "/", 1)[-1].rsplit(".", 1)[0]
        if shape.match(stem):
            out.add(id(item))
    return out


def declared_zoom_over(declared_crop_factor: float, look) -> float:
    """The crop factor a shot really plays at under the look.

    `tv_frame.v1_zoom_for_look` is ABSOLUTE on the conform, and the
    project's own framing decides what is being zoomed - so the picture
    a reel under the look delivers is the project's declared framing
    multiplied by the declared punch-in.  Returning the multiplied
    factor rather than teaching F12 a second geometry keeps ONE formula
    in `reel_framing.declared_picture`.
    """
    if look is None:
        return declared_crop_factor
    from library.tools.tv_frame import v1_zoom_for_look
    return float(declared_crop_factor or 1.0) * v1_zoom_for_look(
        look["punch_in"])


def screen_window_rect_for(look, frame_width: int, frame_height: int):
    """The look's screen window in timeline pixels, for readers.

    A thin pass-through to `tv_frame.screen_window_rect` so the verifier
    reaches the window through the same module it reaches every other
    part of the look through, rather than importing a second one.
    """
    from library.tools.tv_frame import screen_window_rect

    return screen_window_rect(look, frame_width, frame_height)


def uncovered_window_edges(delivered, window, tolerance: float = 1.0) -> list:
    """Which edges of the screen window this picture fails to reach.

    The one property an aimed punch-in has to keep, stated once so the
    placer's clamp and the conformance check grade the same thing.  The
    verifier cannot re-run the face measurement the aim came from, but
    it does not need to: what matters is not WHERE the picture was
    aimed, it is that the aim left no black inside the television.
    """
    edges = []
    if delivered.left > window[0] + tolerance:
        edges.append(f"left {delivered.left - window[0]:.1f}px")
    if delivered.top > window[1] + tolerance:
        edges.append(f"top {delivered.top - window[1]:.1f}px")
    if delivered.right < window[2] - tolerance:
        edges.append(f"right {window[2] - delivered.right:.1f}px")
    if delivered.bottom < window[3] - tolerance:
        edges.append(f"bottom {window[3] - delivered.bottom:.1f}px")
    return edges


def _rendered_frame_count(path: str) -> int:
    """How many frames the render on disk holds, or 0 when it is unusable.

    A missing, corrupt or unreadable file reads as 0, which renders it
    again - the caller treats "shorter than needed" and "absent" as one
    case, and a probe that raised instead would turn a stale render into
    a refusal.  `nb_frames` is what the render was asked for, so it is
    what is read back; a container that does not report it falls back to
    its duration times the requested rate.
    """
    import subprocess as _subprocess

    if not os.path.isfile(path):
        return 0
    try:
        probe = _subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames,r_frame_rate,duration",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, encoding="utf-8", check=False)
    except OSError:
        return 0
    if probe.returncode != 0:
        return 0
    fields = {}
    for line in (probe.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    try:
        if fields.get("nb_frames", "").isdigit():
            return int(fields["nb_frames"])
        num, den = fields.get("r_frame_rate", "0/1").split("/")
        return int(float(fields.get("duration", 0)) * float(num)
                   / float(den or 1))
    except (ValueError, ZeroDivisionError):
        return 0


def frame_overlay_segments(look: dict, runs: Sequence[Tuple[int, int]],
                           fps: float, width: int, height: int,
                           project_folder: str) -> List[dict]:
    """The frame asset as timed overlay segments, one per picture run.

    RENDERED rather than placed as a still, because a still cannot be
    placed for an arbitrary length through Resolve's scripting API: a
    PNG reports one frame and Resolve gives it the project's standard
    still duration, so a sixty-one second run came out five seconds long
    and the conformance check read the difference as a speaker losing
    thirty-one seconds of picture.  Stretching the placed still
    afterwards would mean duration surgery through the same API whose
    still handling already proved untrustworthy here, so the movie stays:
    what changed is its IDENTITY, not its carriage.

    ONE artefact per still, however many lengths use it - the captain's
    model, measured against on 2026-09-09 when six renders of one frame
    at six lengths held 2.8 GB.  The duration used to be part of the
    filename, so the existence check missed on every new length and
    re-rendered the whole thing.  Now the file is rendered ONCE at the
    longest run and shorter runs trim it at placement (`startFrame: 0`,
    `endFrame: total_frames` in `place_overlay_segments`), which needs
    no re-encode because a looped still is the same picture on every
    frame.  A run longer than the render on disk re-renders it, still as
    the one file; a run shorter than it renders nothing.

    Rendered at the asset's COVER size for this frame
    (`tv_frame.cover_size`), never at the delivery frame: the overlay is
    then placed at `tv_frame.cover_zoom` and Resolve draws it at exactly
    the pixels rendered here.  The first version rendered a fitted band
    padded into 1080x1920, which is the letterboxed strip the captain
    rejected - and zooming THAT to cover would have upscaled a 1080-wide
    render 3.16x.
    """
    import hashlib
    import subprocess as _subprocess

    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.tv_frame import (
        applied_rotation, cover_size, oriented_size)

    out_dir = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "reel_look", "frame_overlays")
    os.makedirs(out_dir, exist_ok=True)

    asset = look["asset"]
    from PIL import Image
    with Image.open(asset) as image:
        asset_size = image.size
    # Turned upright for this delivery FIRST, then measured: the
    # captain's frame is a landscape television and a reel is portrait,
    # and rotated it matches the delivery exactly (2160x3840 against
    # 1080x1920) instead of needing a cover zoom at all.
    rotation = applied_rotation(look, asset_size, width, height)
    drawn_width, drawn_height = cover_size(
        oriented_size(asset_size, rotation), width, height)

    stamp = hashlib.sha1(
        f"{asset}|{os.path.getmtime(asset)}|{drawn_width}x{drawn_height}"
        f"|r{rotation}|{fps}".encode("utf-8")).hexdigest()[:10]

    # `transpose=1` is a quarter turn clockwise, `2` anticlockwise, and
    # 180 is two of them. Named here rather than computed, because
    # ffmpeg's filter takes a mode and not an angle.
    transpose = {90: "transpose=1,", 180: "transpose=1,transpose=1,",
                 270: "transpose=2,"}.get(int(rotation), "")

    runs = [(int(start_frame), int(end_frame))
            for start_frame, end_frame in runs]
    if not runs:
        return []
    # The file's identity is the ASSET's - the stamp above, which is
    # already correct - and duration is not part of it.
    path = os.path.join(out_dir, f"tv_frame_{stamp}.mov")
    longest = max(end_frame - start_frame for start_frame, end_frame in runs)
    if _rendered_frame_count(path) < longest:
        # ProRes 4444 for the alpha: the bezel is largely transparent
        # and a codec without an alpha plane would put a black card
        # over the picture rather than a window onto it.
        result = _subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", asset,
            "-t", f"{longest / fps:.5f}",
            "-r", f"{fps:.6f}",
            "-vf", (f"{transpose}"
                    f"scale={drawn_width}:{drawn_height}:flags=lanczos"),
            "-c:v", "prores_ks", "-profile:v", "4444",
            "-pix_fmt", "yuva444p10le", path,
        ], capture_output=True, encoding="utf-8", check=False)
        if result.returncode != 0 or not os.path.isfile(path):
            raise ReelLookRefused(
                f"the TV frame could not be rendered to {longest} frames: "
                f"ffmpeg exited {result.returncode}. "
                f"{(result.stderr or '').strip()[-500:]}")
    segments = []
    for start_frame, end_frame in runs:
        segments.append({
            "overlay_path": path,
            "timeline_start": start_frame / fps,
            "total_frames": end_frame - start_frame,
        })
    return segments


def frame_properties(look: dict, frame_width: int,
                     frame_height: int) -> Dict[str, float]:
    """The transform the frame overlay plays under: the COVER zoom.

    Derived by `tv_frame.cover_zoom` from the size the overlay was
    RENDERED at - which is the asset turned upright for this delivery
    and scaled to cover - so nothing here holds a number.  For the
    captain's 3840x2160 asset in a 1080x1920 reel the turn makes the
    aspects match exactly, and this computes 1.0: the frame plays at
    its natural size and the alignment is the rotation, not a zoom.
    """
    from library.tools.tv_frame import (
        applied_rotation, cover_size, cover_zoom, oriented_size)

    from PIL import Image
    with Image.open(look["asset"]) as image:
        asset_size = image.size
    rotation = applied_rotation(look, asset_size, frame_width, frame_height)
    drawn = cover_size(oriented_size(asset_size, rotation),
                       frame_width, frame_height)
    zoom = cover_zoom(drawn, frame_width, frame_height)
    return {"ZoomX": zoom, "ZoomY": zoom}


PUNCH_IN_REFUSED_NO_SUBJECT = "no_subject_measured"
"""Why a shot plays unpunched: nothing measured where the speaker is."""

PUNCH_IN_REFUSED_NOT_A_CLOSE_UP = "more_than_one_subject"
"""Why a shot plays unpunched: it holds more than one person.

A crop aimed at "the largest face" aims at whoever sits nearest the
camera, which in a two-shot is not reliably the speaker - and cropping
to the wrong one puts the speaker outside the frame. Refused rather than
aimed at a guess, which is the same ruling as having no measurement at
all.
"""


class PunchInLeavesBlack(ReelLookRefused):
    """The picture does not reach the edges of the television's screen."""


def punch_in_properties(look: dict, subject, source_width: int,
                        source_height: int, frame_width: int,
                        frame_height: int, window=None):
    """The transform one SHOT plays under the frame, or None to refuse.

    Returns None when `subject` is None or the shot holds more than one
    person: a crop with nothing aiming it is a guess about where the
    speaker is, and the captain's ruling is to refuse rather than guess.

    Where a subject IS measured, three numbers are decided here and each
    is derived from something measured:

    - **Zoom** must COVER THE SCREEN WINDOW the frame leaves, not the
      delivery frame.  Those are different rectangles, and using the
      frame was the defect: with the bezel cover-scaled the window is
      2491x1853 timeline pixels while a 2.30 picture is 2484x1397, so
      the television showed 228px of black above and below its own
      picture.  `tv_frame.window_cover_zoom` derives the minimum, and
      the drawn zoom is the LARGER of that and the declaration - a
      project may punch in tighter than the screen needs, never looser.
      With the frame turned upright the minimum is 2.3070 against the
      captain's declared 2.30, which is the bezel and the punch-in
      agreeing to three pixels.
    - **Pan and Tilt** aim the subject at the centre of the WINDOW, in
      timeline pixels, and are clamped so the picture still covers that
      window on every edge.  Aiming at the frame centre would aim at a
      point the viewer cannot see through the bezel.
    - Then the result is CHECKED: a transform that leaves any black
      inside the window raises `PunchInLeavesBlack` rather than being
      placed.  That is the defect the captain has had to catch twice,
      and a post-condition is what stops a third time.
    """
    from library.tools.tv_frame import v1_zoom_for_look, window_cover_zoom

    if subject is None:
        return None
    if int(getattr(subject, "others", 0)) > 0:
        return None
    if window is None:
        raise ValueError(
            "punch_in_properties needs the screen window it must cover; "
            "covering the delivery frame instead is the defect this "
            "argument exists to make impossible to repeat.")

    declared = v1_zoom_for_look(look["punch_in"])
    required = window_cover_zoom(source_width, source_height, window,
                                 frame_width, frame_height)
    zoom = max(declared, required)

    fit = min(frame_width / source_width, frame_height / source_height)
    shown_width = source_width * fit * zoom
    shown_height = source_height * fit * zoom

    window_cx = (window[0] + window[2]) / 2.0
    window_cy = (window[1] + window[3]) / 2.0
    # Put the subject at the centre of what the viewer can actually see.
    pan = window_cx - frame_width / 2.0 + shown_width * (
        0.5 - float(subject.center_x))
    tilt = window_cy - frame_height / 2.0 + shown_height * (
        0.5 - float(getattr(subject, "center_y", 0.5)))
    # And no further than the picture can go while still covering it.
    pan_low = window[2] - frame_width / 2.0 - shown_width / 2.0
    pan_high = window[0] - frame_width / 2.0 + shown_width / 2.0
    tilt_low = window[3] - frame_height / 2.0 - shown_height / 2.0
    tilt_high = window[1] - frame_height / 2.0 + shown_height / 2.0
    pan = max(pan_low, min(pan_high, pan))
    tilt = max(tilt_low, min(tilt_high, tilt))

    properties = {"ZoomX": zoom, "ZoomY": zoom,
                  "Pan": round(pan, 3), "Tilt": round(tilt, 3)}
    assert_covers_window(properties, source_width, source_height,
                         frame_width, frame_height, window)
    return properties


def window_zoom_for(look, source_size, frame_width: int,
                    frame_height: int) -> float:
    """The minimum zoom this look's screen window needs, for reporting."""
    from library.tools.tv_frame import screen_window_rect, window_cover_zoom

    window = screen_window_rect(look, frame_width, frame_height)
    return window_cover_zoom(source_size[0], source_size[1], window,
                             frame_width, frame_height)


def assert_covers_window(properties, source_width: int, source_height: int,
                         frame_width: int, frame_height: int,
                         window, tolerance: float = 1.0) -> None:
    """Raise unless the picture reaches every edge of the screen window.

    The check the captain should not have had to make: black inside a
    television's screen is the most visible defect this look can have,
    and it survived two reviews because nothing measured it.  One pixel
    of tolerance, for the same reason `reel_framing.PIXEL` allows one -
    two roundings of one real number.
    """
    from library.tools.reel_framing import delivered_picture

    picture = delivered_picture(source_width, source_height,
                                frame_width, frame_height, properties)
    bands = uncovered_window_edges(picture, window, tolerance)
    if bands:
        raise PunchInLeavesBlack(
            f"the punch-in leaves black inside the television's screen: "
            f"{', '.join(bands)}. The picture is "
            f"{picture.rect} and the screen window is "
            f"({window[0]:.0f}, {window[1]:.0f}, {window[2]:.0f}, "
            f"{window[3]:.0f}). A picture that does not reach the edges "
            f"of the screen shows the set's own background through it, "
            f"which is what a viewer reads as a broken render.")


def power_effects(look: dict, first_label: str,
                  last_label: str) -> Dict[str, dict]:
    """The switch-on / switch-off comp keys, per clip label.

    The same two keys `compile_manifest` sets on the master, resolved the
    same way: the module's declared timings, overridden by whatever the
    declaration states.  The animation runs on the picture, not on the
    set - so it lands on the first and last FOOTAGE clip, never on the
    frame asset.
    """
    from library.tools.tv_power import switch_off_frames, switch_on_frames

    declared = (look or {}).get("power", {}) or {}
    head = dict(switch_on_frames())
    head.update(declared.get("switch_on", {}) or {})
    tail = dict(switch_off_frames())
    tail.update(declared.get("switch_off", {}) or {})

    out: Dict[str, dict] = {}
    out.setdefault(first_label, {}).update(
        {"tv_power_head": True, "tv_power_head_timing": head})
    out.setdefault(last_label, {}).update(
        {"tv_power_tail": True, "tv_power_tail_timing": tail})
    return out


def clip_label(index: int) -> str:
    """What a reel's picture clip is called in the Fusion manifest.

    Positional, because a reel's picture is a sequence of keep ranges and
    nothing else names them.  The label is only ever used to join this
    module's own effects to its own manifest clips, so it never leaves.
    """
    return f"reel_picture_{index:02d}"


def motion_spine(placements: Sequence[dict], fps: float) -> dict:
    """The reel's picture as a SPINE step 4.03's resolver can read.

    One block per picture placement, positions counted from zero, with
    the reel's own timeline seconds.  Built so the drift plan for a reel
    goes through `post_bridge.resolve_vfx` unchanged - which is what
    makes `no_stated_reason` and `ken_burns_without_direction` true here
    without either being restated.
    """
    structure = []
    for index, p in enumerate(_picture(placements)):
        start = int(p["snapped_record"])
        end = start + int(round((p["source_out"] - p["source_in"]) * fps))
        # The MASTER seconds this shot was cut from travel too, because
        # the words are recorded against the master timeline and a shot
        # described with no words is a shot the model cannot reason
        # about (AGENTS.md 10.1: a table the prompt names arriving with
        # zero rows is reported, not shipped empty).
        master_start = (getattr(p["clip"], "timeline_start", 0.0)
                        + (p["source_in"]
                           - getattr(p["clip"], "source_in", 0.0)))
        structure.append({
            "position": index,
            "timeline_start": round(start / fps, 3),
            "timeline_end": round(end / fps, 3),
            "master_start": round(master_start, 3),
            "master_end": round(master_start
                                + (p["source_out"] - p["source_in"]), 3),
            "source_file": p["clip"].source_file,
            "speaker": p.get("speaker") or "",
        })
    return {"structure": structure}


MOTION_HANDOFF = """Plan this reel's picture MOTION, shot by shot.

Every shot in this reel is a locked-off frame of somebody talking.  Under
the TV-frame look each one plays punched in behind a bezel, and a punched-in
still frame is still a still frame.  You are deciding which of these shots
drift, in which direction, how far, and - for each one - WHY THAT SHOT.

Rules that are not yours to change:

- There is no default and no blanket.  A shot you say nothing about gets no
  motion, and that is a legitimate answer for every shot in the reel.
- `rationale` is per shot and about THAT shot.  An entry whose rationale is
  missing or blank is dropped as `no_stated_reason` (captain's ruling,
  2026-09-08).  "adds movement" is not a reason; what the shot is doing at
  that moment is.
- `ken_burns` reads its DIRECTION off your own values: `zoom_end` above
  `zoom_start` pushes in, below pulls out.  Equal or missing is dropped as
  `ken_burns_without_direction`.  Nothing is defaulted.
- The magnitude is yours.  The engine offers no scale, no intensity map and
  no bounds.

`shots` lists the reel's picture blocks with the seconds they play and what
is being said across them.  `target_block_position` is a shot's position.
"""

MOTION_EXPECTED_SCHEMA = (
    '{"reel_motion_plan": [{"target_block_position": 0, '
    '"effect_type": "ken_burns", "params": {"zoom_start": 1.0, '
    '"zoom_end": 1.08}, "rationale": "why THIS shot drifts"}]}. '
    'An empty list plans no motion, which leaves every shot still.'
)


def motion_request_stem(reel_number: int) -> str:
    """The file stem this reel's motion ask and answer share."""
    return f"reel_motion_{int(reel_number):02d}"


def write_motion_request(reel_number: int, reel_name: str,
                         spine: dict, says: Sequence[dict],
                         project_folder: str) -> str:
    """Write the ask, and return where it went.

    Same three-part file interface the reel's V6 overlay ask uses
    (`reel_semantic_visual.write_request`): the engine writes the
    question, a model writes the answer beside it, and an unanswered ask
    builds the reel without motion and SAYS so.
    """
    from library.tools.project_layout import Area, ProjectLayout

    rows = []
    for block in spine.get("structure", []):
        # Selected on the MASTER seconds the shot was cut from, which is
        # the timebase the transcript rows are recorded against.
        spoken = [s for s in says
                  if s.get("timeline_start", 0.0) < block["master_end"]
                  and s.get("timeline_end", 0.0) > block["master_start"]]
        rows.append({
            "target_block_position": block["position"],
            "timeline_start": block["timeline_start"],
            "timeline_end": block["timeline_end"],
            "seconds": round(block["timeline_end"] - block["timeline_start"], 2),
            "speaker": block.get("speaker", ""),
            "says": " ".join(" ".join(str(s.get("text", "")).split())
                             for s in spoken)[:900],
        })
    empty = [r["target_block_position"] for r in rows if not r["says"]]
    if empty:
        # SAID on the run that sends it. A shot described to the planner
        # with no words is a shot it must answer about blind, and the
        # answer would read as a reasoned one.
        print(f"  reel motion ask: shots {empty} carry NO WORDS - the "
              f"transcript has nothing over the master seconds they were "
              f"cut from, so any motion planned for them is planned blind",
              file=sys.stderr)
    payload = {
        "step_id": "reel_motion",
        "reel_number": int(reel_number),
        "reel_name": reel_name,
        "prompt": MOTION_HANDOFF,
        "context": {"shots": rows},
        "expected_schema": MOTION_EXPECTED_SCHEMA,
        "project_folder": project_folder,
    }
    directory = str(ProjectLayout(project_folder).read_dir(Area.LLM_REQUESTS))
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, motion_request_stem(reel_number) + ".json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def read_motion_answer(project_folder: str,
                       reel_number: int) -> Optional[list]:
    """The model's motion answer, or None when unanswered.

    A file that will not parse reads as UNANSWERED rather than as an
    empty plan, for the reason `reel_semantic_visual.read_answer` gives:
    a malformed answer is not a decision for no motion.
    """
    from library.tools.project_layout import Area, ProjectLayout

    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.LLM_RESPONSES)),
        motion_request_stem(reel_number) + ".json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(
            payload.get(MOTION_PLAN_KEY), list):
        return payload[MOTION_PLAN_KEY]
    return None


def resolve_motion(plan: Optional[list], spine: dict,
                   fps: float) -> Tuple[list, dict]:
    """(resolved specs, record) for a reel's drift plan.

    The resolver is step 4.03's, imported by path for the reason
    `reel_semantic_visual._step_4_06_bridge` gives: it is a step script,
    and there must not be a second copy of the drop rules.
    """
    record: Dict[str, Any] = {"basis": MOTION_AWAITING_ANSWER,
                              "proposed": 0, "resolved": 0, "dropped": []}
    if plan is None:
        return [], record
    record["proposed"] = len(plan)
    if not plan:
        record["basis"] = MOTION_PLANNED_NONE
        return [], record

    post_bridge = _step_4_03_post_bridge()
    dropped: list = []
    resolved = post_bridge.resolve_vfx(list(plan), spine, frame_rate=fps,
                                       dropped=dropped)
    record["resolved"] = len(resolved)
    record["dropped"] = [
        {"target_block_position": d.target_block_position,
         "effect_type": d.effect_type, "reason": d.reason,
         "detail": d.detail}
        for d in dropped]
    record["basis"] = (MOTION_PLANNED if resolved
                       else MOTION_EVERY_ENTRY_DROPPED)
    return resolved, record


def _step_4_03_post_bridge():
    """Step 4.03's post-bridge module, imported by path."""
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "steps", "step_4_03_plan_vfx",
                        "post_bridge.py")
    spec = importlib.util.spec_from_file_location(
        "step_4_03_post_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fusion_manifest(placements: Sequence[dict], look: dict,
                    motion: Sequence[dict], fps: float) -> dict:
    """The manifest `apply_fusion_comps` reads for one reel.

    Only the keys that pass actually reads: `tracks.V1.clips` in placed
    order (matched to timeline items by full source path) and
    `fusion_effects.per_clip` keyed by label.  Building a whole assembly
    manifest here would be a second compile_manifest.
    """
    picture = _picture(placements)
    clips = []
    for index, p in enumerate(picture):
        clips.append({
            "label": clip_label(index),
            "source_file": p["clip"].source_file,
            "source_in": p["source_in"],
            "source_out": p["source_out"],
        })

    per_clip: Dict[str, dict] = {}
    if picture:
        for label, effects in power_effects(
                look, clip_label(0), clip_label(len(picture) - 1)).items():
            per_clip.setdefault(label, {}).update(effects)

    # The drift, joined to the clip it covers by the shot position the
    # plan targeted - the same join `compile_manifest` makes by seconds,
    # done by index here because a reel's shots ARE its picture clips.
    for spec in motion or ():
        position = int(spec.get("target_block_position", -1))
        if not (0 <= position < len(picture)):
            continue
        effect = per_clip.setdefault(clip_label(position), {})
        effect["_preset"] = spec["effect_type"]
        effect.update(spec.get("params", {}))

    return {
        "tracks": {"V1": {"clips": clips}},
        "fusion_effects": {"per_clip": per_clip, "transitions": []},
    }


def apply_comps(manifest: dict, project_folder: str,
                resolve_project_name: str, timeline_name: str,
                python_executable: Optional[str] = None,
                step_id: str = "build_reels") -> bool:
    """Run the Fusion pass over the reel's own timeline, in its own process.

    AGENTS.md 5: never create a timeline and use `ImportFusionComp` in
    the same Python process.  The reel's timeline was created by the
    caller, so this is a subprocess, and it is handed the project and
    timeline it must find current - `apply_fusion_comps` refuses every
    mutation on a mismatch rather than writing comps onto the captain's
    rough cut.
    """
    if not (manifest.get("fusion_effects", {}).get("per_clip")):
        return True
    from library.tools.project_layout import Area, ProjectLayout

    scratch = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "reel_look")
    os.makedirs(scratch, exist_ok=True)
    manifest_path = os.path.join(
        scratch, f"{_slug(timeline_name)}_fusion_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    module = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "execution", "apply_fusion_comps.py")
    result = subprocess.run(
        [python_executable or sys.executable, module, manifest_path,
         "--project-folder", project_folder,
         "--expected-project", resolve_project_name,
         "--expected-timeline", timeline_name,
         "--step-id", step_id],
        capture_output=True, encoding="utf-8", check=False)
    if result.stdout:
        print(result.stdout, file=sys.stderr)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
