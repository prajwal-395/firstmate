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
                        "source_in": 0.0,
                        "source_out": 5.0,
                        "timeline_in": 0.0,
                        "timeline_out": 5.0,
                        "timeline_in_frame": 0,
                        "timeline_out_frame": 150,
                        "label": "clip_1"
                    }
                ]
            }
        },
        "subtitles": []
    }

def test_valid_manifest_passes():
    manifest = get_valid_manifest()
    errors = validate_manifest(manifest)
    assert not errors, f"Expected valid manifest to pass, got {errors}"

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

def test_negative_duration_fails():
    manifest = get_valid_manifest()
    manifest["tracks"]["V1"]["clips"][0]["timeline_out"] = -5.0
    errors = validate_manifest(manifest)
    assert len(errors) == 1
    assert "Negative duration" in errors[0]

def test_schema_violation_fails():
    manifest = get_valid_manifest()
    del manifest["project"]  # Required field
    errors = validate_manifest(manifest)
    assert any("Schema validation error" in e for e in errors)
