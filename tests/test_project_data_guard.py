"""Pin the project-data guard's refusal (captain's ruling 2026-09-10).

PR 968 committed 23 files of live Resolve state under `captures/` and
nothing refused it.  The guard in `library/tools/project_data_guard.py`
is keyed on captured-timeline SHAPES and on the `captures/` path
convention - never on filenames - and these tests prove each refusal,
prove a rename evades nothing, and prove the pipeline tree itself is
clean.  If a future lane recommits project data, the repo test below
is what fails.
"""

import json
from pathlib import Path

from library.tools.project_data_guard import capture_shape_reason, scan_repo

REPO_ROOT = Path(__file__).resolve().parents[1]


def _full_state():
    return {"timeline_settings": {"timelineResolutionWidth": "1080"},
            "track_counts": {"video": 5}, "tracks": []}


def _item_capture():
    return {"index": 1, "name": "Reel 13", "start_frame": 0, "end_frame": 9,
            "markers": [],
            "items": [{"track": "video1", "source_file": "/x/y.MXF",
                       "clip_markers": {}}]}


def _inventory():
    return {"captured_at": "20260911T030842Z",
            "timelines": [{"name": "GEO", "pull_file": "/p/markers.json"}]}


def _overlay_measurement():
    return {"timeline": "Reel 13", "frame": [1080, 1920],
            "draw_gain": 2.0, "overlays": []}


def _transcript():
    return {"segments": [{"text": "hi"}],
            "word_segments": [{"word": "hi"}]}


def _fusion_dump():
    return [{"track": "video1", "item": "clip",
             "comps": [{"index": 1, "tools": [{"img_w": 3840}]}]}]


class TestEachCaptureShapeIsRefused:
    def test_full_live_state(self):
        assert "timeline-state" in capture_shape_reason(_full_state())

    def test_per_timeline_item_capture(self):
        assert "item capture" in capture_shape_reason(_item_capture())

    def test_pull_inventory(self):
        assert "inventory" in capture_shape_reason(_inventory())

    def test_overlay_measurement(self):
        assert "overlay" in capture_shape_reason(_overlay_measurement())

    def test_word_aligned_transcript(self):
        assert "transcript" in capture_shape_reason(_transcript())

    def test_fusion_dump(self):
        assert "fusion" in capture_shape_reason(_fusion_dump())


class TestARenameEvadesNothing:
    def test_shape_caught_under_an_innocent_name(self, tmp_path):
        # A filename blocklist would miss this; the shape does not.
        (tmp_path / "notes").mkdir()
        victim = tmp_path / "notes" / "helper.json"
        victim.write_text(json.dumps(_full_state()), encoding="utf-8")
        hits = scan_repo(tmp_path)
        assert [(r, v) for r, v in hits] != []
        assert hits[0][0] == "notes/helper.json"

    def test_inventory_caught_under_an_innocent_name(self, tmp_path):
        victim = tmp_path / "config.json"
        victim.write_text(json.dumps(_inventory()), encoding="utf-8")
        assert scan_repo(tmp_path) != []


class TestLegitimatePipelineJsonPasses:
    def test_step_output_with_tracks_but_no_timeline_read(self, tmp_path):
        # `tracks` alone is not a timeline read - the conjunction is.
        doc = {"step": "compile", "tracks": [{"index": 1}]}
        (tmp_path / "out.json").write_text(json.dumps(doc),
                                           encoding="utf-8")
        assert scan_repo(tmp_path) == []

    def test_built_timeline_fixture_shape_passes(self, tmp_path):
        # items keyed by `file` (the builder's shape), not by
        # `source_file` + `clip_markers` (the capture's shape).
        doc = {"items": [{"track": "v1", "file": "clip.mp4"}]}
        (tmp_path / "built.json").write_text(json.dumps(doc),
                                             encoding="utf-8")
        assert scan_repo(tmp_path) == []

    def test_segments_without_word_alignment_pass(self, tmp_path):
        doc = {"segments": [{"text": "hi"}]}
        (tmp_path / "s.json").write_text(json.dumps(doc), encoding="utf-8")
        assert scan_repo(tmp_path) == []

    def test_code_mentioning_capture_keys_is_not_parsed(self, tmp_path):
        # The guard reads JSON documents, never source: a .py naming
        # track_counts or draw_gain is tooling, not a capture.
        (tmp_path / "tool.py").write_text(
            'TRACK_COUNTS = {"video": 1}\nDRAW_GAIN = 2.0\n',
            encoding="utf-8")
        assert scan_repo(tmp_path) == []

    def test_broken_json_is_left_alone(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json",
                                           encoding="utf-8")
        assert scan_repo(tmp_path) == []


class TestTheDropZoneIsGone:
    def test_no_captures_directory_in_the_pipeline(self):
        assert not (REPO_ROOT / "captures").exists(), (
            "captures/ is the project-data drop zone - tooling lives in "
            "library/tools/, records live in the project repo")

    def test_a_new_drop_is_refused_by_path(self, tmp_path):
        (tmp_path / "captures").mkdir()
        (tmp_path / "captures" / "still.jpg").write_bytes(b"\xff\xd8fake")
        hits = scan_repo(tmp_path)
        assert len(hits) == 1 and "drop zone" in hits[0][1]


class TestThePipelineTreeIsClean:
    def test_no_project_data_shapes_anywhere_in_this_repo(self):
        hits = scan_repo(REPO_ROOT)
        assert hits == [], (
            "project data in the pipeline repo:\n"
            + "\n".join(f"  {rel}: {reason}" for rel, reason in hits))
