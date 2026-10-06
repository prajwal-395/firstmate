"""The deliverable set shares one stem set across all video variants.

Covers library/tools/deliverable_set.py: the matrix's video jobs beside
the stem plan's roles, with the stem count independent of the aspect
count - two aspects with dialogue and music are two video renders
sharing one stem set, never four stem renders.
"""
from __future__ import annotations

from library.tools.deliverable_set import plan_deliverable_set


def _tracks(**roles):
    tracks = {}
    for name, files in roles.items():
        tracks[name] = {"clips": [
            {"source_file": f, "label": f} for f in files]}
    return tracks


def test_stems_are_shared_across_video_variants():
    """The defect this pins: aspects multiplying stems. Master plus a
    vertical is two video renders; dialogue and music plan once for the
    whole set, not once per aspect."""
    aset = plan_deliverable_set(
        ["master", "tiktok"],
        _tracks(A1=["vo.wav"], A2=["bed.mp3"]))
    assert aset.video_render_count == 2
    assert aset.stem_roles == ["dialogue", "music"]
    assert aset.audio.stems["dialogue"].clip_count == 1
    assert aset.audio.stems["music"].clip_count == 1


def test_identical_platforms_share_one_video_job_and_one_stem_set():
    """Both halves dedupe: four identical verticals are one video job,
    and the stems are stated once beside it."""
    aset = plan_deliverable_set(
        ["tiktok", "instagram_reels", "youtube_shorts", "linkedin"],
        _tracks(A1=["vo.wav"]))
    assert aset.video_render_count == 1
    assert aset.video.jobs[0].serves == (
        "tiktok", "instagram_reels", "youtube_shorts", "linkedin")
    assert aset.stem_roles == ["dialogue"]


def test_an_empty_mix_plans_video_only():
    """No audio tracks is a fact that plans no stems: the video
    variants stand alone rather than carrying empty stem jobs."""
    aset = plan_deliverable_set(["tiktok"], {})
    assert aset.video_render_count == 1
    assert aset.stem_roles == []
    assert aset.audio.stems == {}
