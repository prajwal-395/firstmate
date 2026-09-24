import pytest
import os
from library.tools.manifest_validator import validate_manifest

# Dummy valid manifest generator
def get_valid_manifest():
    return {
        "project": {
            "name": "Test",
            "resolution": [1080, 1920],
            "frame_rate": 30.0,
            "duration_seconds": 10.0
        },
        "tracks": {
            "V1": {
                "label": "A-Roll",
                "clips": [
                    {
                        "source_file": __file__,  # Use current file as a guaranteed existing file
                        "source_in": 17.666,
                        "source_out": 22.348,
                        "timeline_in": 0.0,
                        "timeline_out": 4.682,
                        "timeline_in_frame": 0,
                        "timeline_out_frame": 140,
                        "label": "clip_1"
                    }
                ]
            }
        },
        "subtitles": []
    }

def test_missing_source_file_fails():
    manifest = get_valid_manifest()
    manifest["tracks"]["V1"]["clips"][0]["source_file"] = "/does/not/exist.mov"
    errors = validate_manifest(manifest)
    assert len(errors) == 1
    assert "not found" in errors[0]

def test_invalid_cdl_values_fail():
    manifest = get_valid_manifest()
    manifest["color_grade"] = {
        "per_clip_adjustments": [
            {
                "clip_id": "clip_1",
                "cdl_values": {
                    "slope_r": -0.5,  # Invalid
                    "power_g": 0,     # Invalid
                    "offset_b": 2.0   # Invalid
                }
            }
        ]
    }
    errors = validate_manifest(manifest)
    assert len(errors) == 3
    assert any("slope_r" in e for e in errors)
    assert any("power_g" in e for e in errors)
    assert any("offset_b" in e for e in errors)
    
def test_subtitle_overlay_exceeds_project():
    """Uses the shape compile_manifest writes: subtitle_overlay.segments.

    The previous version fabricated tracks["subtitle_overlay"]["clips"],
    which the compiler has never emitted, so the check passed green while
    validating a structure that does not exist.
    """
    manifest = get_valid_manifest()
    manifest["subtitle_overlay"] = {
        "segments": [
            {"timeline_start": 0.0, "timeline_end": 12.0,
             "overlay_path": "/tmp/sub_block_1.mov"},
        ]
    }
    errors = validate_manifest(manifest)
    assert any("exceeds project duration" in e for e in errors), errors


def test_subtitle_overlay_overlap_is_rejected():
    manifest = get_valid_manifest()
    manifest["subtitle_overlay"] = {
        "segments": [
            {"timeline_start": 0.0, "timeline_end": 3.0},
            {"timeline_start": 2.0, "timeline_end": 4.0},
        ]
    }
    errors = validate_manifest(manifest)
    assert any("before the previous one ends" in e for e in errors), errors


def test_zero_duration_clip_is_rejected():
    """out == in used to pass: the old check only rejected out < in."""
    manifest = get_valid_manifest()
    manifest["tracks"]["V2"] = {"label": "B-Roll", "clips": [
        {"source_file": __file__, "source_in": 0, "source_out": 0,
         "timeline_in": 0, "timeline_out": 0, "label": "broll_1"},
    ]}
    errors = validate_manifest(manifest)
    assert any("zero-length" in e for e in errors), errors


def test_v2_overlap_is_rejected():
    """Overlap detection used to run on V1 only."""
    manifest = get_valid_manifest()
    manifest["tracks"]["V2"] = {"label": "B-Roll", "clips": [
        {"source_file": __file__, "source_in": 0.0, "source_out": 2.0,
         "timeline_in": 0.0, "timeline_out": 2.0, "label": "broll_1"},
        {"source_file": __file__, "source_in": 0.0, "source_out": 2.0,
         "timeline_in": 1.0, "timeline_out": 3.0, "label": "broll_2"},
    ]}
    errors = validate_manifest(manifest)
    assert any("overlaps the previous clip" in e for e in errors), errors

def test_repeated_source_audio_is_rejected():
    """Consecutive V1 clips from the same source with overlapping source ranges repeat audio."""
    manifest = get_valid_manifest()
    # Add a second clip on V1 from the same source file, overlapping source range
    manifest["tracks"]["V1"]["clips"].append(
        {
            "source_file": __file__,
            "source_in": 20.0,  # Overlaps with the first clip's 17.666-22.348
            "source_out": 25.0,
            "timeline_in": 4.682,
            "timeline_out": 9.682,
            "label": "clip_2"
        }
    )
    errors = validate_manifest(manifest)
    assert any("repeats" in e for e in errors), f"Expected overlap error, got {errors}"
