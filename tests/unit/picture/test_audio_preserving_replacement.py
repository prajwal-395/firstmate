import copy

import pytest

from library.tools.audio_preserving_replacement import (
    ReplacementPlanError,
    apply_replacements,
)


def _proposal():
    return {
        "spine_block_position": 7,
        "original": {
            "source_file": "/media/interview.mov",
            "source_start": 12.0,
            "source_end": 18.0,
            "timeline_start": 30.0,
            "timeline_end": 36.0,
        },
        "replacement": {
            "source_file": "/media/alt-angle.mov",
            "source_start": 42.0,
            "source_end": 48.0,
            "timeline_start": 30.0,
            "timeline_end": 36.0,
        },
        "bad_picture_evidence_ids": ["obs-soft-1"],
        "sync_evidence_ids": ["obs-sync-1"],
        "continuity": {"verified": True, "evidence_ids": ["obs-cont-1"]},
    }


def _v1():
    return [{
        "source_file": "/media/interview.mov",
        "source_in": 12.0,
        "source_out": 18.0,
        "timeline_in": 30.0,
        "timeline_out": 36.0,
        "spine_position": 7,
        "label": "speech_7",
    }]


def test_replacement_keeps_original_dialogue_source_and_span():
    clips = _v1()
    result = apply_replacements(clips, [_proposal()], fps=30.0)

    assert clips[0]["source_file"] == "/media/alt-angle.mov"
    assert clips[0]["video_only"] is True
    assert result["speech_audio_clips"] == [{
        **_v1()[0], "label": "speech_original_7",
        "replacement_audio": True,
    }]
    assert result["replacements"][0]["audio_source"] == "/media/interview.mov"
    assert result["replacements"][0]["audio_source_start"] == 12.0
    assert result["replacements"][0]["audio_source_end"] == 18.0
    assert clips[0]["replacement_evidence"] == {
        "bad_picture": ["obs-soft-1"],
        "synchronization": ["obs-sync-1"],
        "continuity": ["obs-cont-1"],
    }


@pytest.mark.parametrize("change", [
    lambda p: p.update(sync_evidence_ids=[]),
    lambda p: p.update(continuity={"verified": False,
                                  "evidence_ids": ["obs-cont-1"]}),
    lambda p: p["replacement"].update(timeline_end=35.0),
])
def test_unsupported_or_uncovered_replacement_refuses(change):
    proposal = copy.deepcopy(_proposal())
    change(proposal)
    with pytest.raises(ReplacementPlanError):
        apply_replacements(_v1(), [proposal], fps=30.0)


def test_no_replacement_preserves_legacy_plan():
    clips = _v1()
    before = copy.deepcopy(clips)
    assert apply_replacements(clips, [], fps=30.0) == {
        "replacements": [], "speech_audio_clips": []}
    assert clips == before
