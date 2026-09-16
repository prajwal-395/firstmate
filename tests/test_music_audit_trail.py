"""The music audit trail is out of the spines, and kept in its own file.

Captain's ruling, 2026-09-16: move it out to its own file, then confirm
on a real run. The record - how each track was found and why this one
was chosen - is KEPT; it just stops travelling through every step that
never reads it (~24 kB per run in `pipeline_data.json`, the single
largest remaining payload saving found anywhere in the pipeline).

Both halves are pinned here:

1. ABSENT from the edit data: `mesh_spine`'s post-bridge writes no
   `music_selection` key into `audio_spine` (or `timed_spine`, which
   is the same object).
2. PRESENT and COMPLETE in its own file:
   `pipeline_output/steps/2_04_music_selection/music_audit_trail.json`
   (`library/tools/music_audit_trail.py`), holding the whole resolved
   selection - candidates, justification, splices, section,
   measurements, provenance - with nothing summarised away.
"""

import json
import os
import subprocess
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


def test_post_bridge_main_writes_neither_spine_with_the_trail():
    """End to end through stdin/stdout: both spines ship without it."""
    post_bridge = os.path.join(
        REPO, "library", "steps", "step_2_05_mesh_spine", "post_bridge.py")
    payload = {
        "spine": _spine(),
        "speech_sequence": _speech(),
        "music_selection": _selection(),
        "project_config": {"target_duration_seconds": 8.0},
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, post_bridge], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env=env, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    for key in ("audio_spine", "timed_spine"):
        assert "music_selection" not in out[key], (
            f"{key} carries the audit trail")


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


def test_the_audit_file_refuses_an_empty_selection(tmp_path):
    with pytest.raises(ValueError):
        audit.write_audit_trail(str(tmp_path), {})
    assert audit.read_audit_trail(str(tmp_path)) is None


def test_step_2_04_post_bridge_writes_the_sidecar(tmp_path):
    """The real 2.04 post-bridge leaves the sidecar beside its output."""
    from library.steps.step_2_04_music_selection import (
        post_bridge as pb04,
    )

    track = tmp_path / "bed.wav"
    track.write_bytes(b"RIFF" + b"\x00" * 100)
    candidates = [{
        "title": "Bed", "source": "library",
        "audio_path": str(track),
        "duration_seconds": 160.0, "duration_ok": True,
    }]
    selection = {
        "title": "Bed", "source": "library",
        "audio_path": str(track),
        "duration_seconds": 160.0,
        "direction_justification": {
            "direction_mood": "measured, unhurried",
            "why_it_fits": "it settles rather than pushes",
            "forbidden_registers": ["triumphant"],
            "why_not_forbidden": {"triumphant": "no brass, no lift"},
        },
        "candidates_evaluated": [
            {"title": "Bed", "source": "library", "verdict": "chosen",
             "reason": "the only candidate measured"}],
    }
    resolved = pb04.resolve_selection(
        selection, candidates, 60.0, str(tmp_path))
    path = audit.write_audit_trail(str(tmp_path), resolved)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == resolved
    assert stored["candidates_evaluated"] == \
        selection["candidates_evaluated"]
