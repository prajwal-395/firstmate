"""Subject-only colour grading: a plan entry scopes a grade to the tracked subject.

Failing-first contract for `library/tools/subject_grade.py` plus its three
wiring points (Loader Clip support in `fusion/nodes.py`, the masked-grade
dispatch in `fusion/comp_builder.py`, the 5.01 -> compile merge).

What this proves, per the region-granularity scout
(`data/vep-region-granular-editing/report.md` in the firstmate home):
face-seeded subject-only grading, 2 fps mattes. Generic objects
("the laptop") wait on open-vocabulary grounding and are REFUSED here,
not guessed at.

The captain's hard rule holds throughout: no grade VALUE is hardcoded.
Neutral (gain 1.0 / contrast 0.0 / saturation 1.0) is the absence of
decoration, not taste (AGENTS.md 10.5) - it is the only number this
feature may name. Every other number arrives from the plan entry.
"""
import os

import numpy as np
import pytest

from library.tools import subject_grade
from library.tools.analysis.object_segmentation import (
    FACE_SEED_LABEL,
    encode_rle,
)
from library.tools.fusion import comp_builder
from library.tools.fusion.effects import fx
from library.tools.fusion.engine import CompEngine

# ─── Fixtures ───

def _seg_result(frames=4, shape=(24, 32), hole=False):
    """A face-seeded 1.06 result dict, as the JSON file carries it."""
    objects = []
    masks_rle = {}
    for f in range(frames):
        mask = np.zeros(shape, dtype=np.uint8)
        mask[6:18, 10:22] = 1
        if hole:
            mask[11, 15] = 0  # single-pixel speckle hole
        masks_rle[str(f)] = encode_rle(mask)
    objects.append({
        "object_id": "obj_1",
        "label": FACE_SEED_LABEL,
        "category": "person",
        "frames": list(range(frames)),
        "masks_rle": masks_rle,
        "bboxes": {str(f): [10, 6, 12, 12] for f in range(frames)},
        "avg_area_ratio": 0.18,
    })
    return {
        "video_path": "/footage/clip_001.mp4",
        "frame_count": frames,
        "resolution": list(shape),
        "sample_fps": 2.0,
        "seed_note": "face_seeded: one subject target",
        "objects": objects,
    }


def _warm_entry(**over):
    entry = {
        "clip_id": "clip_001",
        "target": {"kind": "person", "role": "speaker"},
        "scope": "subject-only",
        # Deliberately odd values: passthrough must be verbatim, never
        # snapped to a rounder number the engine prefers.
        "grade": {"gain": 1.173, "saturation": 1.311},
    }
    entry.update(over)
    return entry


# ─── 1. Vocabulary: what a plan entry may say ───

class TestParsePlanEntry:
    def test_accepts_speaker_subject_only_with_values(self):
        clean, drop = subject_grade.parse_plan_entry(_warm_entry())
        assert drop is None
        assert clean["clip_id"] == "clip_001"
        # Values travel verbatim - the mechanism carries no opinion
        # about how warm warm is.
        assert clean["grade"] == {"gain": 1.173, "saturation": 1.311}

    def test_second_value_set_passes_through_unchanged(self):
        clean, drop = subject_grade.parse_plan_entry(_warm_entry(
            grade={"gain": 0.913, "contrast": 0.07, "saturation": 0.884}))
        assert drop is None
        assert clean["grade"] == {
            "gain": 0.913, "contrast": 0.07, "saturation": 0.884}

    def test_generic_object_is_dropped_not_guessed(self):
        clean, drop = subject_grade.parse_plan_entry(_warm_entry(
            target={"kind": "object", "label": "the laptop"}))
        assert clean is None
        assert "laptop" in drop["detail"]
        assert drop["reason"] == "open_vocabulary_target"
        assert "open-vocabulary" in drop["detail"]

    def test_unknown_scope_is_dropped(self):
        clean, drop = subject_grade.parse_plan_entry(
            _warm_entry(scope="everything-except"))
        assert clean is None
        assert "subject-only" in drop["detail"]

    def test_all_neutral_grade_is_dropped(self):
        clean, drop = subject_grade.parse_plan_entry(_warm_entry(
            grade={"gain": 1.0, "contrast": 0.0, "saturation": 1.0}))
        assert clean is None
        assert "no_readable_parameters" == drop["reason"]

    def test_missing_clip_is_dropped(self):
        entry = _warm_entry()
        del entry["clip_id"]
        clean, drop = subject_grade.parse_plan_entry(entry)
        assert clean is None
        assert drop["reason"] == "no_clip_named"


# ─── 2. Grounding: the entry meets a real tracked subject ───

class TestGroundEntry:
    def test_face_seeded_subject_grounds(self):
        clean, _ = subject_grade.parse_plan_entry(_warm_entry())
        grounded, drop = subject_grade.ground_entry(clean, _seg_result())
        assert drop is None
        assert grounded["object_id"] == "obj_1"

    def test_no_face_seeded_object_does_not_ground(self):
        seg = _seg_result()
        seg["objects"][0]["label"] = "auto_object_1"
        seg["objects"][0]["category"] = "unknown"
        clean, _ = subject_grade.parse_plan_entry(_warm_entry())
        grounded, drop = subject_grade.ground_entry(clean, seg)
        assert grounded is None
        assert "face_seeded_subject" in drop["detail"]

    def test_missing_segmentation_does_not_ground(self):
        clean, _ = subject_grade.parse_plan_entry(_warm_entry())
        grounded, drop = subject_grade.ground_entry(clean, None)
        assert grounded is None
        assert drop["reason"] == "no_segmentation"


# ─── 3. Matte writer: RLE JSON to timeline-rate PNGs ───

class TestWriteSubjectMatte:
    def test_holds_2fps_masks_over_timeline_frames(self, tmp_path):
        seg = _seg_result(frames=4, shape=(24, 32))
        record = subject_grade.write_subject_matte(
            seg, "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=60, resolution=(24, 32))
        assert record["frame_count"] == 60
        assert len(record["files"]) == 60
        assert all(os.path.exists(f) for f in record["files"])
        # 2 fps over a 2-second span: mask 0 holds frames 0-14,
        # mask 1 holds 15-29, and so on.
        from PIL import Image
        first = np.array(Image.open(record["files"][0]))
        mid_span = np.array(Image.open(record["files"][20]))
        assert first.max() > 0
        assert mid_span.max() > 0
        # Provenance: which run, which object, which frames.
        assert record["provenance"]["object_id"] == "obj_1"
        assert record["provenance"]["sample_fps"] == 2.0
        assert record["resolution"] == [24, 32]

    def test_hole_fill_repairs_speckle(self, tmp_path):
        seg = _seg_result(frames=2, hole=True)
        record = subject_grade.write_subject_matte(
            seg, "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=30, resolution=(24, 32))
        from PIL import Image
        frame = np.array(Image.open(record["files"][0]))
        # The single-pixel hole inside the body is filled: the body
        # rectangle reads solid.
        assert frame[11, 15] > 0

    def test_unknown_object_refuses(self, tmp_path):
        with pytest.raises(subject_grade.UnknownSubjectObject):
            subject_grade.write_subject_matte(
                _seg_result(), "obj_9", str(tmp_path), "clip_001",
                timeline_fps=30.0, played_frames=30, resolution=(24, 32))


# ─── 4. Comp block: Loader matte gates a BrightnessContrast ───

class TestSubjectGradeBlock:
    def test_masked_grade_wires_effect_mask_to_loader(self):
        block = subject_grade.subject_grade_block(
            matte_file="/m/matte_00000.png",
            gain=1.173, contrast=0.0, saturation=1.311)
        comp = (CompEngine(clip_dur=60)
                .add(fx.grade(gain=1.0, contrast=0.0, saturation=1.0))
                .add(block).serialize())
        assert "Loader" in comp
        assert "matte_00000.png" in comp
        assert "EffectMask" in comp
        assert "1.173" in comp
        assert "1.311" in comp

    def test_neutral_grade_draws_nothing(self):
        block = subject_grade.subject_grade_block(
            matte_file="/m/matte_00000.png",
            gain=1.0, contrast=0.0, saturation=1.0)
        assert block.nodes == []

    def test_comp_builder_dispatches_subject_grade_keys(self):
        comp = comp_builder.build_effect_comp(
            {"subject_grade_matte": "/m/matte_00000.png",
             "subject_grade_gain": 1.173,
             "subject_grade_saturation": 1.311},
            clip_dur=60, source_res=(32, 24))
        assert "Loader" in comp
        assert "EffectMask" in comp

    def test_comp_builder_without_keys_draws_no_loader(self):
        comp = comp_builder.build_effect_comp(
            {"grade_gain": 1.1}, clip_dur=60, source_res=(32, 24))
        assert "Loader" not in comp


# ─── 5. Validator: a named matte must exist and cover the window ───

class TestValidateMatte:
    def test_good_matte_passes(self, tmp_path):
        record = subject_grade.write_subject_matte(
            _seg_result(frames=4), "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=60, resolution=(24, 32))
        assert subject_grade.validate_matte(record) == []

    def test_missing_files_fail(self, tmp_path):
        record = subject_grade.write_subject_matte(
            _seg_result(frames=4), "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=60, resolution=(24, 32))
        os.remove(record["files"][0])
        errors = subject_grade.validate_matte(record)
        assert len(errors) == 1
        assert "missing" in errors[0]

    def test_short_coverage_fails(self, tmp_path):
        record = subject_grade.write_subject_matte(
            _seg_result(frames=4), "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=60, resolution=(24, 32))
        record["files"] = record["files"][:30]
        errors = subject_grade.validate_matte(record, played_frames=60)
        assert len(errors) == 1
        assert "cover" in errors[0]

    def test_resolution_mismatch_fails(self, tmp_path):
        record = subject_grade.write_subject_matte(
            _seg_result(frames=4), "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=60, resolution=(24, 32))
        errors = subject_grade.validate_matte(
            record, source_resolution=(1080, 1920))
        assert len(errors) == 1
        assert "resolution" in errors[0]


# ─── 6. Compile merge: entries land on their clip's effects ───

class TestApplySubjectGrades:
    def test_grounded_entry_patches_clip_effects(self, tmp_path):
        patch, drops, mattes = subject_grade.apply_subject_grades(
            [_warm_entry()], {"clip_001": _seg_result(frames=4)},
            matte_dir=str(tmp_path), timeline_fps=30.0,
            played_frames={"clip_001": 60},
            source_resolution={"clip_001": (24, 32)})
        assert drops == []
        assert len(mattes) == 1
        eff = patch["clip_001"]
        assert eff["subject_grade_gain"] == 1.173
        assert eff["subject_grade_saturation"] == 1.311
        assert os.path.exists(eff["subject_grade_matte"])
        assert subject_grade.validate_matte(mattes[0]) == []

    def test_ungrounded_entry_is_a_drop_not_a_patch(self, tmp_path):
        patch, drops, mattes = subject_grade.apply_subject_grades(
            [_warm_entry(target={"kind": "object", "label": "the laptop"})],
            {"clip_001": _seg_result(frames=4)},
            matte_dir=str(tmp_path), timeline_fps=30.0,
            played_frames={"clip_001": 60},
            source_resolution={"clip_001": (24, 32)})
        assert patch == {}
        assert mattes == []
        assert len(drops) == 1
        assert "laptop" in drops[0]["detail"]

    def test_no_entries_no_mattes_no_drops(self, tmp_path):
        assert subject_grade.apply_subject_grades(
            [], {}, matte_dir=str(tmp_path)) == ({}, [], [])


# ─── 7. The plan-to-manifest path: 5.01 carries, compile grounds ───

def _compile_inputs(source, subject_grades):
    return {
        "color_grade_spec": {
            "per_clip_adjustments": [], "series_look": None,
            "fusion_look": {},
            "subject_grades": subject_grades,
            "subject_grade_drops": [],
        },
        "audio_spine": {
            "structure": [{
                "block_type": "speech", "position": 1, "clip_id": "c1",
                "source_start": 2.417, "source_end": 12.417,
                "timeline_start": 0.0, "timeline_end": 10.0,
                "content": {"clip_id": "c1"},
            }],
            "frame_rate": 30.0,
        },
        "clip_catalog": [{"clip_id": "c1", "path": source,
                          "width": 32, "height": 24}],
        "a_roll_assignments": [{
            "spine_block_position": 1, "clip_id": "c1",
            "source_file": source, "video_in": 2.417, "video_out": 12.417,
            "timeline_start": 0.0, "timeline_end": 10.0,
        }],
        "b_roll_assignments": [], "transition_spec": [],
        "enhancement_spec": [], "sfx_spec": [], "audio_mix_spec": {},
    }


class TestCompileMerge:
    def test_grounded_entry_lands_on_its_clips_effects(self, tmp_path):
        import json as _json
        from unittest.mock import patch as _patch

        from library.steps.step_5_04_compile_manifest.step import compile_manifest

        seg_dir = tmp_path / "1_06_object_segmentation"
        seg_dir.mkdir()
        seg = _seg_result(frames=4, shape=(24, 32))
        (seg_dir / "c1_segmentation.json").write_text(_json.dumps(seg))

        source = os.path.abspath(__file__)
        entry = {"clip_id": "c1",
                 "target": {"kind": "person", "role": "speaker"},
                 "scope": "subject-only",
                 "grade": {"gain": 1.173, "saturation": 1.311}}
        inputs = _compile_inputs(source, [entry])
        with _patch(
                "library.steps.step_5_04_compile_manifest.step.load",
                side_effect=lambda out_dir, filename: inputs):
            manifest = compile_manifest(str(tmp_path))

        per_clip = manifest["fusion_effects"]["per_clip"]
        assert per_clip, "the subject grade reached no clip"
        eff = next(iter(per_clip.values()))
        assert eff["subject_grade_gain"] == 1.173
        assert eff["subject_grade_saturation"] == 1.311
        assert os.path.exists(eff["subject_grade_matte"])
        assert manifest["subject_grade_drops"] == []
        assert len(manifest["subject_mattes"]) == 1

    def test_ungrounded_entry_is_a_manifest_drop(self, tmp_path):
        from unittest.mock import patch as _patch

        from library.steps.step_5_04_compile_manifest.step import compile_manifest

        source = os.path.abspath(__file__)
        entry = {"clip_id": "c1",
                 "target": {"kind": "person", "role": "speaker"},
                 "scope": "subject-only",
                 "grade": {"gain": 1.173}}
        inputs = _compile_inputs(source, [entry])
        with _patch(
                "library.steps.step_5_04_compile_manifest.step.load",
                side_effect=lambda out_dir, filename: inputs):
            manifest = compile_manifest(str(tmp_path))

        # No 1.06 dir: nothing grounds, nothing is graded whole-frame.
        assert manifest["fusion_effects"]["per_clip"] == {}
        assert manifest["subject_mattes"] == []
        assert len(manifest["subject_grade_drops"]) == 1
        assert manifest["subject_grade_drops"][0]["reason"] == (
            "no_segmentation")


class TestFiveOhOneCarries:
    def test_define_color_grade_keeps_subject_grades(self):
        from unittest.mock import patch as _patch

        from library.steps.step_5_01_color_grade.grade import (
            LUMA_METHOD,
            define_color_grade,
        )

        measured = {"luma": 122.0, "method": LUMA_METHOD, "samples": 40}
        with _patch(
                "library.steps.step_5_01_color_grade.grade.measure_luma",
                return_value=dict(measured)):
            spec = define_color_grade(
                {"entries": [{"track": "V1", "clip_id": "c1",
                              "entry_id": "e1", "source_file": "f1.mov"}]},
                project_folder="proj",
                subject_grades=[_warm_entry()],
            )["color_grade_spec"]
        assert len(spec["subject_grades"]) == 1
        assert spec["subject_grades"][0]["grade"]["gain"] == 1.173
        assert spec["subject_grade_drops"] == []

    def test_define_color_grade_without_entries_stays_empty(self):
        from unittest.mock import patch as _patch

        from library.steps.step_5_01_color_grade.grade import (
            LUMA_METHOD,
            define_color_grade,
        )

        measured = {"luma": 122.0, "method": LUMA_METHOD, "samples": 40}
        with _patch(
                "library.steps.step_5_01_color_grade.grade.measure_luma",
                return_value=dict(measured)):
            spec = define_color_grade(
                {"entries": [{"track": "V1", "clip_id": "c1",
                              "entry_id": "e1", "source_file": "f1.mov"}]},
                project_folder="proj",
            )["color_grade_spec"]
        assert spec["subject_grades"] == []
        assert spec["subject_grade_drops"] == []
