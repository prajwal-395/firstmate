"""Every frame of the timeline must show a clip, or the render is black.

The shipped export carried 7.2s of black, 6.4s of it starting at 4.3s.
The rough-cut review records only negative gaps by design, the B-roll
duration invariant was satisfied by the very shortening that made the
hole, and only the final ffmpeg probe noticed - one step from the end,
with nothing left to do but fail the run.  This check is the same
observation, moved to `compile_manifest` where it can still be acted on.
"""

import pytest

from library.steps.step_5_04_compile_manifest.step import (
    _assert_timeline_fully_covered,
    _video_coverage_gaps,
)


def manifest_with(v1=(), v2=(), duration=10.0, fps=30.0, spine=None):
    def clips(spans, prefix):
        return [
            {"timeline_in": start, "timeline_out": end,
             "timeline_in_frame": int(round(start * fps)),
             "timeline_out_frame": int(round(end * fps)),
             "label": f"{prefix}_{i}"}
            for i, (start, end) in enumerate(spans)
        ]

    manifest = {
        "project": {"frame_rate": fps, "duration_seconds": duration},
        "tracks": {
            "V1": {"label": "A-Roll", "clips": clips(v1, "aroll")},
            "V2": {"label": "B-Roll", "clips": clips(v2, "broll")},
        },
    }
    if spine is not None:
        manifest["_spine_blocks"] = spine
    return manifest


def block(start, end, block_type="transition_slot", **extra):
    entry = {
        "position": 1,
        "timeline_start": start,
        "timeline_end": end,
        "block_type": block_type,
    }
    entry.update(extra)
    return entry


def test_every_uncovered_range_fails_and_is_named():
    """Including the exact shape of the shipped defect: `select_broll`'s
    post-bridge shortens a cutaway whose source cannot fill its block -
    correct over speech, black on a block with no A-roll underneath (a
    3.567s clip on a 10s block)."""
    rows = (
        (manifest_with(v1=[(0.0, 4.308), (10.741, 20.0)], duration=20.0),
         ["4.308s to 10.741s", "6.433s"]),
        (manifest_with(v1=[(2.0, 10.0)]), ["0.000s to 2.000s"]),
        (manifest_with(v1=[(0.0, 0.741), (10.741, 20.0)],
                       v2=[(0.741, 4.308)], duration=20.0),
         ["4.308s to 10.741s"]),
        (manifest_with(v1=[(0.0, 4.308), (10.741, 21.937), (22.678, 30.0)],
                       duration=30.0),
         ["2 uncovered range(s)", "4.308s to 10.741s", "21.937s to 22.678s"]),
    )
    for manifest, needles in rows:
        with pytest.raises(ValueError) as excinfo:
            _assert_timeline_fully_covered(manifest)
        assert all(n in str(excinfo.value) for n in needles), (
            needles, str(excinfo.value))


def test_a_covered_timeline_passes():
    """A non-speech block covered only by a cutaway is legitimate, and
    float noise where one clip ends and the next begins is not black."""
    manifest = manifest_with(v1=[(0.0, 4.0), (7.0, 10.0)], v2=[(4.0, 7.0)])
    assert _video_coverage_gaps(manifest) == []
    _assert_timeline_fully_covered(manifest)
    manifest = manifest_with(v1=[(0.0, 4.9999), (5.0, 10.0)])
    assert _video_coverage_gaps(manifest) == []


def _beat_manifest(gap_end, spine, gap_start=4.0):
    """A 10s timeline whose only hole runs `gap_start` to `gap_end`."""
    return manifest_with(
        v1=[(0.0, gap_start), (gap_end, 10.0)], spine=spine)


DECLARED = {"intentional_black_beat": True,
            "black_beat_reason": "hold on black before the turn"}


def test_only_a_declared_short_non_speech_black_beat_is_an_edit():
    """A hole the plan chose, short and outside speech, is an edit.
    Everything else is a hole: a beat longer than the bound (0.8s of
    black is a hole with a note attached); a bare flag with no reason (a
    rubber stamp); intent inferred from a short hole rather than
    recorded; black on speech; a gap running past the declared block's
    edge; and the shipped 6.433s gap however it is labelled."""
    manifest = _beat_manifest(4.4, [block(3.0, 5.0, **DECLARED)])
    assert _video_coverage_gaps(manifest) == [(4.0, 4.4)]
    _assert_timeline_fully_covered(manifest)
    rows = (
        (_beat_manifest(4.8, [block(3.0, 5.0, **DECLARED)]),
         "longer than the 0.5s"),
        (_beat_manifest(4.4, [block(3.0, 5.0, intentional_black_beat=True)]),
         "no black_beat_reason"),
        (_beat_manifest(4.4, [block(3.0, 5.0)]), "no spine block declares"),
        (_beat_manifest(4.4, [block(3.0, 5.0, block_type="speech",
                                    **DECLARED)]),
         "speech is never held on black"),
        (_beat_manifest(4.4, [block(3.0, 4.2, **DECLARED)]),
         "no spine block declares"),
        (manifest_with(v1=[(0.0, 4.308), (10.741, 20.0)], duration=20.0,
                       spine=[block(0.0, 20.0, **DECLARED)]),
         "longer than the 0.5s"),
    )
    for manifest, match in rows:
        with pytest.raises(ValueError, match=match):
            _assert_timeline_fully_covered(manifest)


def test_clips_without_frame_fields_fall_back_to_seconds():
    manifest = {
        "project": {"frame_rate": 30.0, "duration_seconds": 10.0},
        "tracks": {"V1": {"clips": [
            {"timeline_in": 0.0, "timeline_out": 6.0, "label": "a"},
        ]}},
    }
    gaps = _video_coverage_gaps(manifest)
    assert len(gaps) == 1
    assert gaps[0] == pytest.approx((6.0, 10.0))
