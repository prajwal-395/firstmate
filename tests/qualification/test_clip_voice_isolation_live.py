"""Per-clip Voice Isolation against a REAL running DaVinci Resolve.

The proof this slice reports: on Ren Qualification (the driver opened
this project inside its own borrowed() and restores the captain after
- this test never switches projects), a scratch timeline (qual picture
plus imported noisy words) renders once unisolated and once with
Voice Isolation amount 60 on the audio clip; the word-span level
drops, the write reads back equal, and scratch state is deleted
again. The room floor is reported, not asserted - isolation targets
voice, and asserting the room would credit it with work it does not
claim.

Live only: `resolve_session` skips without REN_LIVE_RESOLVE=1, and
refuses any project but Ren Qualification.
"""

from __future__ import annotations

import math
import os
import random
import struct
import sys
import uuid
import wave

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from library.tools import qualification_project as qp  # noqa: E402
from library.tools.dialogue_cleanup import (  # noqa: E402
    apply_clip_voice_isolation,
)
from library.tools.marker_feedback import connect_resolve  # noqa: E402

SR = 48000
WORDS = [(0.5, 1.5), (2.5, 3.5)]


def _noisy_words(path):
    """Two words over rumble plus a bed, with speech-free gaps: the
    room floor stays measurable while isolation has noise to remove
    from under the voice."""
    rng = random.Random(7)
    samples = []
    for i in range(int(4.0 * SR)):
        t = i / SR
        v = (0.05 * math.sin(2 * math.pi * 60.0 * t)
             + 0.003 * (rng.random() * 2.0 - 1.0))
        for start, end in WORDS:
            if start <= t < end:
                attack = min(1.0, (t - start) / 0.01)
                release = min(1.0, (end - t) / 0.01)
                v += (min(attack, release)
                      * 0.5 * math.sin(2 * math.pi * 440.0 * t))
        samples.append(v)
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(packed)


def _word_level(path):
    from library.tools import speech_loudness as _loud
    levels = []
    for start, end in WORDS:
        reading = _loud.measure_range(path, start, end)
        assert reading.get("measured"), (
            f"ffmpeg could not measure the render: {reading!r}")
        levels.append(reading["integrated_lufs"])
    return sum(levels) / len(levels)


def _floor(path):
    from library.tools import room_tone as _room
    spans = [(float(s), float(e)) for s, e in WORDS]
    try:
        return _room.measure_room_tone(path, spans).get("level_dbfs")
    except Exception as exc:
        return f"unmeasured: {exc}"[:120]


def test_clip_isolation_drops_the_word_level_live(tmp_path,
                                                  resolve_session):
    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    assert project is not None
    assert project.GetName() == qp.QUALIFICATION_PROJECT

    fixture = str(tmp_path / "words.wav")
    _noisy_words(fixture)
    scratch_name = f"pytest_vi_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    out_dir = str(tmp_path / "renders")
    os.makedirs(out_dir, exist_ok=True)
    media_pool = project.GetMediaPool()
    qual_a = str(qp.ensure_media()["qual_a"])
    imported = media_pool.ImportMedia([fixture, qual_a])
    assert imported and len(imported) == 2, (
        "Resolve declined the fixture imports")
    words = [clip for clip in imported
             if (clip.GetClipProperty("File Path") or "").endswith(
                 "words.wav")]
    assert words, "the words clip did not land in the pool"
    timeline = None
    try:
        timeline = media_pool.CreateEmptyTimeline(scratch_name)
        assert timeline, "Resolve declined the scratch timeline"
        project.SetCurrentTimeline(timeline)
        picture = [clip for clip in imported if clip not in words][0]
        placed = media_pool.AppendToTimeline([
            {"mediaPoolItem": picture, "mediaType": 1, "trackIndex": 1},
            {"mediaPoolItem": words[0], "mediaType": 2,
             "trackIndex": 1},
        ])
        assert placed and len(placed) == 2, (
            "Resolve did not place picture and audio")
        clips = timeline.GetItemListInTrack("audio", 1)
        assert clips, "the scratch timeline holds no audio clip"
        clip = clips[0]

        from library.tools.execution import resolve_render as rr
        baseline = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="vi_before", fmt="mp4", codec="H264",
            timeout_seconds=300)["output_path"]
        baseline_repeat = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="vi_before_repeat", fmt="mp4", codec="H264",
            timeout_seconds=300)["output_path"]
        level_before = _word_level(baseline)
        level_repeat = _word_level(baseline_repeat)
        print(f"control word levels: {level_before:.2f} LUFS, "
              f"{level_repeat:.2f} LUFS")
        assert level_before == level_repeat, (
            f"renders are not deterministic: {level_before:.2f} vs "
            f"{level_repeat:.2f} - no effect can be proven")
        record = apply_clip_voice_isolation(clip, 60)
        assert record["verified"] == "re-reads equal"
        isolated = rr.render_timeline(
            timeline_name=scratch_name, output_dir=out_dir,
            output_name="vi_after", fmt="mp4", codec="H264",
            timeout_seconds=300)["output_path"]
        level_after = _word_level(isolated)
        print(f"word level: {level_before:.2f} LUFS before, "
              f"{level_after:.2f} LUFS after; room floor: "
              f"{_floor(baseline)} dBFS before, "
              f"{_floor(isolated)} dBFS after")
        assert level_after < level_before, (
            f"isolation did not drop the word level: "
            f"{level_before:.2f} LUFS before, {level_after:.2f} LUFS "
            f"after")
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
