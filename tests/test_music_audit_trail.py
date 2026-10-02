"""The music audit trail is out of the spines and kept whole in its own file.

Absent from `audio_spine`; present and complete in
`2_04_music_selection/music_audit_trail.json`; a missing or unparseable
sidecar refuses. History: docs/evidence/music_tests.md#music-audit-trail.
"""

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from library.steps.step_2_05_mesh_spine.post_bridge import (  # noqa: E402
    enrich_spine,
)
from library.tools import music_audit_trail as audit  # noqa: E402


def _speech():
    return {
        "body_sequence": [
            {
                "clip_id": "clip_001",
                "source_start": 0.0,
                "source_end": 4.0,
                "text": "First passage",
                "alignment_method": "whisperx",
                "word_timestamps": [
                    {"word": "First", "source_start": 0.0,
                     "source_end": 1.0}
                ],
            },
            {
                "clip_id": "clip_002",
                "source_start": 10.0,
                "source_end": 14.0,
                "text": "Second passage",
                "alignment_method": "whisperx",
                "word_timestamps": [
                    {"word": "Second", "source_start": 10.0,
                     "source_end": 11.0}
                ],
            },
        ]
    }


def _spine():
    return {
        "structure": [
            {"position": 1, "block_type": "speech",
             "content": {"passage_ref": 1}, "duration_seconds": 4.0,
             "music_behavior": "background"},
            {"position": 2, "block_type": "speech",
             "content": {"passage_ref": 2}, "duration_seconds": 4.0,
             "music_behavior": "background"},
        ]
    }


def _selection():
    """A resolved selection WITH the full audit record attached."""
    return {
        "title": "Bed",
        "source": "library",
        "audio_path": "/music/bed.wav",
        "duration_seconds": 160.0,
        "bpm": None,
        "key": None,
        "direction_justification": {
            "direction_mood": "measured, unhurried",
            "why_it_fits": "it settles rather than pushes",
            "forbidden_registers": ["triumphant"],
            "why_not_forbidden": {"triumphant": "no brass, no lift"},
        },
        "candidates_evaluated": [
            {"title": "Bed", "source": "library", "verdict": "chosen",
             "reason": "the only candidate measured"},
            {"title": "Anthem", "source": "library",
             "verdict": "rejected", "reason": "names the forbidden lift"},
        ],
        "splices": [
            {"intended_use": "the settled tail", "source_in": 100.0,
             "source_out": 160.0},
        ],
        "section": {"source_in": 60.0, "why": "the rising middle"},
        "tracks": [],
        "measurements": {"measured": False,
                         "measurement_note": "no readable file here"},
        "target_duration_seconds": 60.0,
        "catalogue_size": 2,
        "provenance": {"found_by": "catalogue_scan"},
    }


# ── Half one: absent from the edit data ─────────────────────────────

def test_enrich_spine_writes_no_music_selection_into_the_spine():
    result = enrich_spine(_spine(), _speech(), _selection(),
                          {"project_config":
                           {"target_duration_seconds": 8.0}})
    spine = result["audio_spine"]
    assert "music_selection" not in spine, (
        "the audit trail is back in the spine - the carrier move "
        "regressed")
    # The conducting survives the move: blocks still carry the bed's
    # behaviour words, which live on the structure rows, not in the
    # removed copy.
    assert [b["music_behavior"] for b in spine["structure"]] == [
        "background", "background"]


# ── Half two: present and complete in its own file ───────────────────

def test_the_audit_file_holds_the_whole_selection(tmp_path):
    selection = _selection()
    path = audit.write_audit_trail(str(tmp_path), selection)
    assert path.name == audit.AUDIT_FILENAME
    # In the chooser's own directory - where "why was this track
    # chosen" is looked up.
    assert path.parent.name == "2_04_music_selection"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == selection, (
        "the audit file is not the whole record - it was summarised, "
        "truncated or reshaped")
    # The audit keys in particular: the essays a prompt never needs
    # and a later question needs most.
    assert stored["candidates_evaluated"] == \
        selection["candidates_evaluated"]
    assert stored["direction_justification"] == \
        selection["direction_justification"]
    assert audit.read_audit_trail(str(tmp_path)) == selection


# ── The other half of 2.04's warn-and-continue write ──────────────
#
# The post-bridge only WARNS on a failed sidecar write; the pre-render
# check's `assert_audit_trail_present` is what refuses.

def test_a_missing_or_unparseable_sidecar_refuses(tmp_path):
    selection = _selection()
    with pytest.raises(audit.AuditTrailMissing,
                       match="audit sidecar is missing"):
        audit.assert_audit_trail_present(str(tmp_path), selection)

    path = audit.write_audit_trail(str(tmp_path), selection)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(audit.AuditTrailMissing, match="does not parse"):
        audit.assert_audit_trail_present(str(tmp_path), selection)
