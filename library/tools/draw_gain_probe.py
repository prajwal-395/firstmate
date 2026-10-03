"""Calibrate the Pan/Tilt draw gain from the renderer, in the run.

`resolve_transform.FALLBACK_DRAW_GAIN` is a measured value, but it is
renderer STATE - it read 1.0 on 2026-09-11 and 2.0 on 2026-09-17, with
no restart and no deploy in between - so baking it into a build is a
bet on today's state. A build that can reach the renderer does not
bet: it sets a known value on a probe element it owns, renders one
still, measures where the value actually drew, and derives the gain
from that. One calibration per build; never a still per element.

The procedure is the hand calibration of 2026-09-17, mechanised (see
`resolve_transform`'s module docstring for the table it produced):
a full-frame plate carrying a band of white bars goes onto an own
scratch timeline at the delivery geometry, Tilt -200 is set and read
back, two gallery stills are taken, and the bars' centre is read off
each still directly. Full-frame, because a smaller plate is at the
mercy of input scaling - the first version of this probe placed a
904x480 plate that drew scaled and refused on weak correlation, which
is exactly the silent-confound this design refuses to have. A
full-frame clip has no mismatch to conform, so there is nothing to
scale; the red marker proves it drew native anyway.

The two stills must agree within a pixel - the known failure this
guards is a sleeping display, which returns blank or half-composited
grabs - or the probe is refused.

Whatever happens the probe NEVER raises and NEVER leaves Resolve
changed: any failure on the SCRATCH timeline - a refused grab, bars
the still does not show, a scaled plate, an out-of-range gain, a
SetProperty the read-back does not echo, a teardown that does not
verify - returns the fallback with LOUD warnings on stderr and on
the record. The one failure that does NOT fall back is the ENTRY
playhead being unreadable (past the end after a watch-through, for
one): the probe measures on its OWN scratch timeline at its own mid
frame, never at the entry position, so an entry position that cannot
be read only means it cannot be put back - it is left alone, noted
in the record's warnings, and the probe measures anyway. A run that
silently fell back is the thing this exists to stop, so the record
always says which source the run used, and a measured gain that
disagrees with the fallback is reported as a finding: that
disagreement is the first machine-readable handle on the renderer
state moving again, and it is worth more than the placement fix
itself.

Own scratch timeline, created and DELETED, own plate file, imported
and removed again; the entry current timeline is restored and read
back, and the entry playhead is restored where it was readable and
left alone where it was not. Anything left behind by a failed
teardown is named in the record's warnings - nothing gets deleted on
a guess, so the list is what makes a later cleanup safe.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import numpy as np

PROBE_TIMELINE_NAME = "ZZ Draw Gain Probe (scratch)"
"""The scratch timeline this calibration creates and deletes."""

PROBE_TILT = -200.0
"""The known value. Big enough to measure to the pixel at any sane
gain, small enough the bars stay fully on frame from 0.25x to 4x."""

PROBE_BAR_FIRST, PROBE_BAR_LAST = 283, 403
"""The white bars' file rows. Caption-ink rows, deliberately: the
centre below is the number the gate measurement uses."""

PROBE_MARKER_FIRST, PROBE_MARKER_LAST = 40, 52
"""The red marker's file rows - the native-size proof."""

MIN_BAR_ROWS = 50
"""Below this many bright rows the still does not show the bars and
the probe refuses (blank grab, wrong frame, plate missing)."""

STILL_AGREEMENT_PX = 1.0
"""The two evidence stills must agree within this on both bar edges,
or the display may have slept between them and the probe refuses."""

NATIVE_SIZE_TOLERANCE_PX = 2.0
"""How far the drawn marker may be from its file size before the
plate counts as scaled and the probe refuses."""

GAIN_SANE_MIN, GAIN_SANE_MAX = 0.25, 4.0
"""Outside this the measurement is not a gain but a mistake, and the
probe refuses rather than shipping it."""


def build_plate(path: str, width: int = 1080,
                height: int = 1920) -> str:
    """A black full-frame plate with white bars and one red marker.

    Deterministic: the same path gets the same pixels, so a plate is
    never rebuilt mid-run.
    """
    from PIL import Image, ImageDraw

    plate = Image.new("RGB", (width, height), (8, 8, 10))
    draw = ImageDraw.Draw(plate)
    y = PROBE_BAR_FIRST
    while y <= PROBE_BAR_LAST:
        draw.rectangle([100, y, 980, min(y + 7, PROBE_BAR_LAST)],
                       fill=(255, 255, 255))
        y += 12
    draw.rectangle([100, PROBE_MARKER_FIRST, 212, PROBE_MARKER_LAST],
                   fill=(255, 80, 80))
    plate.save(path)
    return path


def bar_extent(still_array) -> tuple:
    """The (first, last) rows carrying the bars' white.

    Near-white by min-channel, so coloured highlights never vote;
    more than a stray count per row, so noise never votes either.
    Returns (0, -1) where the still shows no bars.
    """
    arr = np.asarray(still_array)
    white = (arr[..., :3].min(axis=2) > 200).sum(axis=1)
    rows = [i for i, v in enumerate(white) if v > 20]
    if not rows:
        return (0, -1)
    return (rows[0], rows[-1])


def red_marker_extent(still_array) -> tuple:
    """The (first, last) rows where the plate's red marker drew.

    Red is (255, 80, 80): high red channel, low green/blue. The
    still is a dark plate on a black timeline, so anything matching
    is the marker. Returns (0, -1) where nothing matches.
    """
    arr = np.asarray(still_array)
    rgb = arr[..., :3].astype(int)
    mask = (rgb[..., 0] > 200) & (rgb[..., 1] < 140) & (rgb[..., 2] < 140)
    rows = [i for i in range(mask.shape[0]) if mask[i].sum() > 10]
    if not rows:
        return (0, -1)
    return (rows[0], rows[-1])


def derive_gain(measured_shift_px: float, tilt_units: float,
                drawn_h: float, frame_h: float) -> float:
    """The gain the measured shift needs under the law's geometry.

    `measured_shift_px` is signed delivery pixels the bars moved
    (down positive); the law's shift for the DRAWN size is
    `tilt * drawn_h / frame_h` with the same sign convention
    (positive Tilt moves up, so a negative Tilt moves down). For the
    full-frame plate drawn_h == frame_h and the law's shift is the
    Tilt itself.
    """
    law_shift = -float(tilt_units) * (float(drawn_h) / float(frame_h))
    if law_shift == 0:
        raise ValueError("cannot derive a gain from a zero shift")
    return float(measured_shift_px) / law_shift


def _say(message: str) -> None:
    print(f"  draw-gain probe: {message}", file=sys.stderr, flush=True)


def _fallback_record(warnings, probe=None) -> dict:
    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN

    return {
        "gain": FALLBACK_DRAW_GAIN,
        "source": "fallback",
        "disagrees_with_fallback": False,
        "warnings": list(warnings),
        "probe": probe or {},
    }


def calibrate(resolve, project, frame_wh: tuple,
              workdir: str = "") -> dict:
    """Measure the draw gain off the live renderer, or fall back loud.

    `frame_wh` is the geometry placements are computed for (the reel
    delivery frame). Returns the record - `gain`, `source`
    (`"measured"` or `"fallback"`), `disagrees_with_fallback`, the
    `probe` detail and `warnings`. Never raises; never leaves Resolve
    changed (entry timeline restored and read back, the entry playhead
    restored where it was readable and left alone where it was not,
    the scratch timeline and the imported plate removed, temp files
    gone).
    """
    from library.tools import marker_capture as mc
    from library.tools.marker_capture import grab_still
    from library.tools.resolve_lock import assert_current_timeline
    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN

    warnings = []
    entry_tl = project.GetCurrentTimeline()
    try:
        entry_page = (resolve.GetCurrentPage()
                      if hasattr(resolve, "GetCurrentPage") else None)
    except Exception:  # noqa: BLE001 - page unreadable, proceed unowned
        entry_page = None
    # The playhead only reads on the Edit page; the page is put back
    # at teardown, read back where possible.
    resolve.OpenPage("edit")
    try:
        entry_tc = mc.read_playhead(entry_tl).timecode if entry_tl else None
    except Exception as exc:  # noqa: BLE001 - entry position unreadable
        # The entry playhead is where the captain left it - past the end
        # after a watch-through, for one - and the probe measures on its
        # OWN scratch timeline at its own mid frame, never at the entry
        # position. So an unreadable entry position is not a reason to
        # fall back (2026-10-02: a playhead past the end fell back to
        # gain 2.0 against a measured 4.0 and halved every overlay
        # transform on the build); it only means the entry playhead
        # cannot be put back, so it is left alone and the probe measures
        # anyway. The teardown still restores the entry TIMELINE and the
        # entry page - only the SetCurrentTimecode is skipped.
        warnings.append(
            f"entry playhead unreadable ({exc!r}): measuring anyway; "
            f"the entry playhead is left where it is")
        entry_tc = None

    pool = project.GetMediaPool()
    tmpdir = tempfile.mkdtemp(prefix="vep_gain_probe_",
                              dir=workdir or None)
    scratch = None
    imported = []
    try:
        # A stale probe timeline is a crashed probe's, and only ever
        # that - this name is owned by this calibration.
        for i in range(1, project.GetTimelineCount() + 1):
            tl = project.GetTimelineByIndex(i)
            if tl and tl.GetName() == PROBE_TIMELINE_NAME:
                warnings.append(
                    f"stale {PROBE_TIMELINE_NAME!r} present - removing "
                    f"another run's unfinished probe")
                pool.DeleteTimelines([tl])
        scratch = pool.CreateEmptyTimeline(PROBE_TIMELINE_NAME)
        if not scratch:
            return _fallback_record(
                ["CreateEmptyTimeline refused: probe cannot run"])
        # Sized before it becomes current (the 2026-10-02 Fusion
        # render-lock race hangs these writes on a just-made-current
        # timeline; https://github.com/prajwal-395/video_editing_pilot/pull/1594),
        # each under a deadline: a hang falls
        # back loudly after the deadline
        # (`library/tools/resolve_deadline.py`).
        from library.tools import resolve_deadline as _deadline
        try:
            _deadline.apply_timeline_resolution(
                scratch, frame_wh[0], frame_wh[1])
        except (_deadline.ResolveCallTimeout,
                _deadline.ResolutionNotApplied) as exc:
            return _fallback_record(
                [f"scratch sizing failed ({exc}): probe cannot run"])

        plate_path = build_plate(os.path.join(tmpdir, "gain_probe.png"),
                                 frame_wh[0], frame_wh[1])
        storage = resolve.GetMediaStorage()
        imported = storage.AddItemListToMediaPool([plate_path]) or []
        if not imported:
            return _fallback_record(
                ["plate import refused: probe cannot run"])
        # Current BEFORE appending: AppendToTimeline lands on
        # whichever timeline is current, and that must be the
        # scratch by construction rather than by implementation
        # accident in any Resolve build. Through the guard: the probe
        # runs inside its own exclusive hold, and a direct set would
        # bypass the lease refusal.
        resolve.OpenPage("edit")
        assert_current_timeline(project, scratch)
        appended = pool.AppendToTimeline(
            [{"mediaPoolItem": imported[0]}])
        if not appended:
            return _fallback_record(
                ["AppendToTimeline refused: probe cannot run"])
        clip = appended[0]
        try:
            duration = int(scratch.GetEndFrame()) - int(
                scratch.GetStartFrame())
        except Exception as exc:  # noqa: BLE001 - unreadable bounds
            return _fallback_record(
                [f"scratch bounds unreadable ({exc!r}): probe cannot run"])
        if duration < 3:
            return _fallback_record(
                ["probe clip too short to still: probe cannot run"])

        assert_current_timeline(project, scratch)
        mid = duration // 2
        fps = mc.timeline_fps(scratch)
        start_tc = scratch.GetStartTimecode()
        h, m, s, f = [int(x) for x in
                      start_tc.replace(";", ":").split(":")]
        total = f + mid
        frame_roll = 24 if round(fps) == 24 else int(round(fps))
        tc = (f"{h:02d}:{m:02d}:{s + total // frame_roll:02d}:"
              f"{total % frame_roll:02d}")
        scratch.SetCurrentTimecode(tc)
        back = mc.read_playhead(scratch)
        if back.marker_key != mid:
            return _fallback_record(
                [f"playhead reads key {back.marker_key}, want {mid}: "
                 f"probe cannot run"])

        clip.SetProperty("Tilt", PROBE_TILT)
        read_back = clip.GetProperty("Tilt")
        if read_back != PROBE_TILT:
            return _fallback_record(
                [f"Tilt set {PROBE_TILT} reads back {read_back}: "
                 f"probe cannot run"])

        from pathlib import Path

        from PIL import Image

        file_centre = (PROBE_BAR_FIRST + PROBE_BAR_LAST) / 2.0
        edges = []
        for tag in ("a", "b"):
            dest = Path(tmpdir) / f"gain_probe_{tag}.png"
            grab_still(scratch, project, dest)
            still = Image.open(dest).convert("RGB")
            if still.size != (frame_wh[0], frame_wh[1]):
                return _fallback_record(
                    [f"still is {still.size}, not the "
                     f"{frame_wh} frame: probe cannot run"])
            first, last = bar_extent(np.asarray(still))
            if last - first + 1 < MIN_BAR_ROWS:
                return _fallback_record(
                    [f"bars span {last - first + 1} rows: the still "
                     f"does not show the plate - probe refused"])
            marker_first, marker_last = red_marker_extent(
                np.asarray(still))
            marker_h = marker_last - marker_first + 1
            file_marker_h = PROBE_MARKER_LAST - PROBE_MARKER_FIRST + 1
            if abs(marker_h - file_marker_h) > NATIVE_SIZE_TOLERANCE_PX:
                return _fallback_record(
                    [f"red marker draws {marker_h}px tall, file "
                     f"{file_marker_h}px: plate is scaled - probe "
                     f"refused rather than calibrating wrong"])
            edges.append((first, last))

        (first_a, last_a), (first_b, last_b) = edges
        if (abs(first_a - first_b) > STILL_AGREEMENT_PX
                or abs(last_a - last_b) > STILL_AGREEMENT_PX):
            return _fallback_record(
                [f"evidence stills disagree "
                 f"({first_a}..{last_a} vs {first_b}..{last_b}): the "
                 f"display may have slept - probe refused"])
        centre = (first_a + last_a + first_b + last_b) / 4.0
        measured_shift = centre - file_centre
        gain = derive_gain(measured_shift, PROBE_TILT, frame_wh[1],
                           frame_wh[1])
        _say(f"bars draw at rows {first_a}..{last_a} "
             f"(file {PROBE_BAR_FIRST}..{PROBE_BAR_LAST})")
        if not (GAIN_SANE_MIN <= gain <= GAIN_SANE_MAX):
            return _fallback_record(
                [f"derived gain {gain:.3f} outside "
                 f"[{GAIN_SANE_MIN}, {GAIN_SANE_MAX}]: probe refused"])
        disagrees = abs(gain - FALLBACK_DRAW_GAIN) > 0.05
        record = {
            "gain": gain,
            "source": "measured",
            "disagrees_with_fallback": disagrees,
            "warnings": warnings,
            "probe": {
                "timeline": PROBE_TIMELINE_NAME,
                "frame_wh": list(frame_wh),
                "set_tilt": PROBE_TILT,
                "read_back_tilt": read_back,
                "bar_rows_a": [first_a, last_a],
                "bar_rows_b": [first_b, last_b],
                "measured_shift_px": round(measured_shift, 2),
                "fallback_gain": FALLBACK_DRAW_GAIN,
            },
        }
        _say(f"measured gain {gain:.4f} "
             f"({'DIFFERS from fallback '
                f'{FALLBACK_DRAW_GAIN}' if disagrees else
                'matches fallback'})")
        return record
    except Exception as exc:  # noqa: BLE001 - probe never raises
        warnings.append(f"probe raised {exc!r}: falling back")
        return _fallback_record(warnings)
    finally:
        try:
            if scratch is not None:
                if entry_tl is not None:
                    assert_current_timeline(project, entry_tl)
                pool.DeleteTimelines([scratch])
            if imported:
                try:
                    pool.DeleteClips(imported)
                except Exception as exc:  # noqa: BLE001 - named, not lost
                    warnings.append(
                        f"imported plate not removed ({exc!r}): "
                        f"delete it by hand")
            names = [project.GetTimelineByIndex(i).GetName()
                     for i in range(1, project.GetTimelineCount() + 1)]
            if PROBE_TIMELINE_NAME in names:
                warnings.append(
                    f"{PROBE_TIMELINE_NAME!r} still present after "
                    f"teardown: delete it by hand")
            if entry_tl is not None:
                assert_current_timeline(project, entry_tl)
                if entry_tc is not None:
                    entry_tl.SetCurrentTimecode(entry_tc)
            if entry_page:
                try:
                    resolve.OpenPage(entry_page)
                except Exception as exc:  # noqa: BLE001 - named, not lost
                    warnings.append(
                        f"page not restored to {entry_page!r} ({exc!r})")
        except Exception as exc:  # noqa: BLE001 - teardown best effort
            warnings.append(f"teardown raised {exc!r}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


def log_record(record: dict) -> None:
    """Say on stderr which source the run used - never silent."""
    if record.get("source") == "measured":
        _say(f"run uses MEASURED gain {record['gain']:.4f} "
             f"(bars {record['probe'].get('bar_rows_a')})")
        if record.get("disagrees_with_fallback"):
            _say(f"DISAGREEMENT: measured {record['gain']:.4f} differs "
                 f"from fallback "
                 f"{record['probe'].get('fallback_gain')}: the renderer "
                 f"state moved again")
    else:
        _say("run uses FALLBACK gain "
             f"{record.get('gain')} (probe refused: "
             f"{'; '.join(record.get('warnings', [])) or 'no reason'})")
    for warning in record.get("warnings", []):
        _say(f"warning: {warning}")
