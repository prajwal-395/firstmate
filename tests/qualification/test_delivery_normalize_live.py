"""Delivery normalization against a REAL running DaVinci Resolve.

The proof this slice reports: on Ren Qualification (the driver opened
this project inside its own borrowed() and restores the captain after
- this test never switches projects), a scratch timeline (qual
picture plus imported tone) renders once unmastered and once after
`NormalizeAudioLevel` to the delivery spec's own numbers; Ren judges
the second render with `audio_verify.verify_mix` against an intent
carrying those same numbers, and the loudness finding passes inside
the spec's own tolerance. The true-peak finding is reported, not
asserted - peak limiting belongs to the limiter slice, and asserting
it here would credit normalization with a limiter's work. Scratch
state is deleted again.

Live only: `resolve_session` skips without REN_LIVE_RESOLVE=1, and
refuses any project but Ren Qualification.
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
from library.tools.audio_chain import (  # noqa: E402
    AudioChainIntent,
    AudioTargets,
)
from library.tools.audio_verify import verify_mix  # noqa: E402
from library.tools.delivery_normalize import (  # noqa: E402
    normalize_items,
    normalize_options_for_spec,
)
from library.tools.delivery_spec import (  # noqa: E402
    resolve_delivery_spec_for_project,
)
from library.tools.marker_feedback import connect_resolve  # noqa: E402

SR = 48000


def _tone(path):
    """Four seconds of constant tone: normalization moves its level
    to the target; nothing dynamic for it to misunderstand."""
    samples = [0.3 * math.sin(2 * math.pi * 440.0 * t / SR)
               for t in range(int(4.0 * SR))]
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(packed)


def test_normalize_masters_to_the_spec_live(tmp_path, resolve_session):
    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    assert project is not None
    assert project.GetName() == qp.QUALIFICATION_PROJECT

    spec = resolve_delivery_spec_for_project()
    current = project.GetCurrentTimeline()
    assert current is not None
    modes = current.GetNormalizeAudioModes() or []
    # No mode name carries "loudness" - the standards are named by
    # spec. BS.1770-4 is the revision Ren's own loudnorm measurement
    # implements, so master and verifier read the same ruler; newest
    # revision first.
    bs_modes = sorted((m for m in modes if "BS.1770" in str(m)),
                      reverse=True)
    assert bs_modes, (
        f"this Resolve answers no BS.1770 normalize mode: {modes}")
    mode = bs_modes[0]
    options = normalize_options_for_spec(spec, mode, modes)

    fixture = str(tmp_path / "tone.wav")
    _tone(fixture)
    scratch_name = f"pytest_norm_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    out_dir = str(tmp_path / "renders")
    os.makedirs(out_dir, exist_ok=True)
    media_pool = project.GetMediaPool()
    qual_a = str(qp.ensure_media()["qual_a"])
    imported = media_pool.ImportMedia([fixture, qual_a])
    assert imported and len(imported) == 2, (
        "Resolve declined the fixture imports")
    tone = [clip for clip in imported
            if (clip.GetClipProperty("File Path") or "").endswith(
                "tone.wav")]
    assert tone, "the tone clip did not land in the pool"
    timeline = None
    try:
        timeline = media_pool.CreateEmptyTimeline(scratch_name)
        assert timeline, "Resolve declined the scratch timeline"
        project.SetCurrentTimeline(timeline)
        picture = [clip for clip in imported if clip not in tone][0]
        placed = media_pool.AppendToTimeline([
            {"mediaPoolItem": picture, "mediaType": 1, "trackIndex": 1},
            {"mediaPoolItem": tone[0], "mediaType": 2, "trackIndex": 1},
        ])
        assert placed and len(placed) == 2, (
            "Resolve did not place picture and audio")
        clips = timeline.GetItemListInTrack("audio", 1)
        assert clips, "the scratch timeline holds no audio clip"

        from library.tools.execution import resolve_render as rr
        baseline = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="norm_before", fmt="mp4", codec="H264",
            timeout_seconds=300)["output_path"]
        record = normalize_items(timeline, list(clips), options, modes)
        assert record["normalized"] is True
        mastered = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="norm_after", fmt="mp4", codec="H264",
            timeout_seconds=300)["output_path"]
        intent = AudioChainIntent(targets=AudioTargets(
            integrated_lufs=spec.loudness_target_lufs,
            lufs_tolerance=spec.loudness_tolerance_lufs,
            true_peak_ceiling_dbTP=spec.true_peak_ceiling_dbtp))
        before = {f.name: f for f in verify_mix(intent, baseline)}
        after = {f.name: f for f in verify_mix(intent, mastered)}
        print(f"before: LUFS {before['lufs'].measured} "
              f"(target {spec.loudness_target_lufs}), peak "
              f"{before['true_peak'].measured}; after: LUFS "
              f"{after['lufs'].measured}, peak "
              f"{after['true_peak'].measured}")
        assert after["lufs"].passed, (
            f"mastered mix misses the spec: {after['lufs'].detail}")
    finally:
        try:
            if timeline is not None:
                media_pool.DeleteTimelines([timeline])
        except Exception:
            pass
        try:
            media_pool.DeleteClips(list(imported))
        except Exception:
            pass
