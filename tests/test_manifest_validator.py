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

def _missing_source(m):
    m["tracks"]["V1"]["clips"][0]["source_file"] = "/does/not/exist.mov"


def _invalid_cdl(m):
    m["color_grade"] = {"per_clip_adjustments": [{
        "clip_id": "clip_1",
        "cdl_values": {"slope_r": -0.5, "power_g": 0, "offset_b": 2.0}}]}


def _subtitle_past_the_end(m):
    # The shape compile_manifest writes: subtitle_overlay.segments. A
    # fabricated tracks["subtitle_overlay"]["clips"] once passed green
    # while validating a structure the compiler never emits.
    m["subtitle_overlay"] = {"segments": [
        {"timeline_start": 0.0, "timeline_end": 12.0,
         "overlay_path": "/tmp/sub_block_1.mov"}]}


def _subtitle_overlap(m):
    m["subtitle_overlay"] = {"segments": [
        {"timeline_start": 0.0, "timeline_end": 3.0},
        {"timeline_start": 2.0, "timeline_end": 4.0}]}


def _zero_duration_clip(m):
    # out == in used to pass: the old check only rejected out < in.
    m["tracks"]["V2"] = {"label": "B-Roll", "clips": [
        {"source_file": __file__, "source_in": 0, "source_out": 0,
         "timeline_in": 0, "timeline_out": 0, "label": "broll_1"}]}


def _v2_overlap(m):
    # Overlap detection used to run on V1 only.
    m["tracks"]["V2"] = {"label": "B-Roll", "clips": [
        {"source_file": __file__, "source_in": 0.0, "source_out": 2.0,
         "timeline_in": 0.0, "timeline_out": 2.0, "label": "broll_1"},
        {"source_file": __file__, "source_in": 0.0, "source_out": 2.0,
         "timeline_in": 1.0, "timeline_out": 3.0, "label": "broll_2"}]}


def _repeated_source_audio(m):
    # Consecutive V1 clips from one source with overlapping source
    # ranges repeat audio (AGENTS.md 6).
    m["tracks"]["V1"]["clips"].append(
        {"source_file": __file__, "source_in": 20.0, "source_out": 25.0,
         "timeline_in": 4.682, "timeline_out": 9.682, "label": "clip_2"})


DEFECTS = [
    (_missing_source, ["not found"], 1),
    (_invalid_cdl, ["slope_r", "power_g", "offset_b"], 3),
    (_subtitle_past_the_end, ["exceeds project duration"], None),
    (_subtitle_overlap, ["before the previous one ends"], None),
    (_zero_duration_clip, ["zero-length"], None),
    (_v2_overlap, ["overlaps the previous clip"], None),
    (_repeated_source_audio, ["repeats"], None),
]


def test_each_manifest_defect_is_rejected_by_name():
    """The valid manifest passes; each planted defect is named."""
    assert validate_manifest(get_valid_manifest()) == []
    for plant, expected, count in DEFECTS:
        manifest = get_valid_manifest()
        plant(manifest)
        errors = validate_manifest(manifest)
        for word in expected:
            assert any(word in e for e in errors), (plant.__name__, errors)
        if count is not None:
            assert len(errors) == count, (plant.__name__, errors)
