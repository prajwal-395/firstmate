"""Dialogue Leveler against a REAL running DaVinci Resolve.

The proof the Fairlight API slice reports: on Ren Qualification (the
driver opened this project inside its own borrowed() and restores the
captain after - this test never switches projects), a scratch
timeline carrying an imported dynamic fixture (loud and quiet bursts,
so levelling has something to level) renders once without the leveler
and once with it; the 1-second-slice loudness spread narrows (whole-file
LRA cannot resolve 2-second alternation on 4 seconds of material), the
write reads back equal, and the scratch timeline plus the imported clip
are deleted again. No EQ band is touched anywhere (the scripting API
names none).

Live only: `resolve_session` skips without REN_LIVE_RESOLVE=1, and
refuses any project but Ren Qualification. The fixture wav is stdlib
`wave` under `tmp_path`; the two renders land beside it and are
measured, then the directory goes with `tmp_path`.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import uuid
import wave

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from library.tools import qualification_project as qp  # noqa: E402
from library.tools.dialogue_cleanup import (  # noqa: E402
    apply_dialogue_leveler,
)
from library.tools.marker_feedback import connect_resolve  # noqa: E402

SR = 48000


def _bursts(path):
    """Two loud seconds, two quiet, alternating: LRA the leveler can
    shrink. Exact levels irrelevant - only the before/after direction
    is asserted."""
    samples = []
    for i in range(int(4.0 * SR)):
        t = i / SR
        loud = 0.5 if (t % 2.0) < 1.0 else 0.05
        samples.append(loud * math.sin(2 * math.pi * 220.0 * t))
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(packed)


def _spread(path):
    """Max-min integrated loudness across four 1-second slices: the
    dynamics of short material, where whole-file LRA cannot resolve
    2-second alternation (its 3-second windows straddle every block).
    Only the before/after direction is asserted."""
    from library.tools import speech_loudness as _loud
    levels = []
    for start in (0.0, 1.0, 2.0, 3.0):
        reading = _loud.measure_range(path, start, start + 1.0)
        assert reading.get("measured"), (
            f"ffmpeg could not measure the render: {reading!r}")
        levels.append(reading["integrated_lufs"])
    return max(levels) - min(levels)


def test_dialogue_leveler_narrows_the_dynamics_live(tmp_path,
                                                 resolve_session):
    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    assert project is not None
    assert project.GetName() == qp.QUALIFICATION_PROJECT

    fixture = str(tmp_path / "bursts.wav")
    _bursts(fixture)
    scratch_name = f"pytest_leveler_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    out_dir = str(tmp_path / "renders")
    os.makedirs(out_dir, exist_ok=True)
    media_pool = project.GetMediaPool()
    # Plain path strings, not FilePath dicts and not Path objects:
    # that is the measured usage against this Resolve. The qual clip
    # goes on video only (its tone never reaches the mix); the bursts
    # go on audio only (the measured signal). An audio-only timeline
    # queues no mp4 render job - Resolve needs the picture.
    qual_a = str(qp.ensure_media()["qual_a"])
    imported = media_pool.ImportMedia([fixture, qual_a])
    assert imported and len(imported) == 2, (
        "Resolve declined the fixture imports")
    bursts = [clip for clip in imported
              if (clip.GetClipProperty("File Path") or "").endswith(
                  "bursts.wav")]
    picture = [clip for clip in imported if clip not in bursts]
    assert bursts and picture, "the imports did not land as two clips"
    pool = bursts[0]
    timeline = None
    try:
        timeline = media_pool.CreateEmptyTimeline(scratch_name)
        assert timeline, "Resolve declined the scratch timeline"
        project.SetCurrentTimeline(timeline)
        placed = media_pool.AppendToTimeline([
            {"mediaPoolItem": picture[0], "mediaType": 1,
             "trackIndex": 1},
            {"mediaPoolItem": pool, "mediaType": 2, "trackIndex": 1},
        ])
        assert placed and len(placed) == 2, (
            "Resolve did not place picture and audio")
        clips = timeline.GetItemListInTrack("audio", 1)
        assert clips, "the scratch timeline holds no audio clip"
        clip = clips[0]
        # The mode enum's numeric values live only in the running
        # Resolve (the stub names them, the runtime module does not
        # export them): read the panel default off the clip - a valid
        # value by definition - and vary the shaping switches around
        # it.
        panel_mode = clip.GetProperties()["AudioDialogueLevelerMode"]

        from library.tools.execution import resolve_render as rr
        baseline = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="leveler_before", fmt="mp4",
            codec="H264",
            timeout_seconds=300)["output_path"]
        # Control: the same timeline rendered twice with no change in
        # between. A render pipeline that is not deterministic cannot
        # prove a 0.3 dB effect, so the proof refuses instead of
        # asserting one.
        baseline_repeat = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="leveler_before_repeat", fmt="mp4",
            codec="H264",
            timeout_seconds=300)["output_path"]
        spread_before = _spread(baseline)
        spread_repeat = _spread(baseline_repeat)
        print(f"panel mode reads {panel_mode}; control spreads: "
              f"{spread_before:.1f} dB, {spread_repeat:.1f} dB")
        assert spread_before == spread_repeat, (
            f"renders are not deterministic: {spread_before:.1f} dB "
            f"vs {spread_repeat:.1f} dB - no effect can be proven")
        record = apply_dialogue_leveler(clip, {
            "AudioDialogueLevelerEnabled": True,
            "AudioDialogueLevelerMode": panel_mode,
            "AudioDialogueLevelerReduceLoudDialogue": True,
            "AudioDialogueLevelerLiftSoftDialogue": True,
            "AudioDialogueLevelerBackgroundReduction": False,
            "AudioDialogueLevelerOutputGain": 0.0,
        })
        assert record["verified"] == "re-reads equal"
        levelled = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="leveler_after", fmt="mp4", codec="H264",
            timeout_seconds=300)["output_path"]
        spread_before = _spread(baseline)
        spread_after = _spread(levelled)
        print(f"leveler 1s-slice spread: {spread_before:.1f} dB before, "
              f"{spread_after:.1f} dB after")
        assert spread_after < spread_before, (
            f"leveler did not narrow the dynamics: {spread_before:.1f} "
            f"dB before, {spread_after:.1f} dB after")
    finally:
        # Scratch state dies here: the timeline first (it references
        # the clips), then both pool imports.
        try:
            if timeline is not None:
                media_pool.DeleteTimelines([timeline])
        except Exception:
            pass
        try:
            media_pool.DeleteClips(list(imported))
        except Exception:
            pass
