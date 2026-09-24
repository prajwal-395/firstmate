"""A title composited BEHIND the subject, through the Loader-matte path.

A behind_subject segment grounds against a real tracked subject and
composites the rendered title under its matte in Fusion. Anything that
cannot be grounded REFUSES by name (`BehindSubjectRefused`) - never
dropped with a reason, never drawn on top.
"""

import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import behind_subject
from library.tools.analysis.object_segmentation import (
    FACE_SEED_LABEL,
    encode_rle,
)
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal


def _seg_result(frames=4, shape=(24, 32)):
    masks_rle = {}
    for f in range(frames):
        mask = np.zeros(shape, dtype=np.uint8)
        mask[6:18, 10:22] = 1
        masks_rle[str(f)] = encode_rle(mask)
    return {
        "video_path": "/footage/clip_001.mp4",
        "frame_count": frames,
        "resolution": list(shape),
        "sample_fps": 2.0,
        "seed_note": "face_seeded: one subject target",
        "objects": [{
            "object_id": "obj_1",
            "label": FACE_SEED_LABEL,
            "category": "person",
            "frames": list(range(frames)),
            "masks_rle": masks_rle,
            "bboxes": {str(f): [10, 6, 12, 12] for f in range(frames)},
            "avg_area_ratio": 0.18,
        }],
    }


def _clip(label="clip1", cid="c1", path="/footage/c1.mp4",
          tl_in=0.0, tl_out=10.0, source_in=0.0):
    return {"label": label, "clip_id": cid, "source_file": path,
            "timeline_in": tl_in, "timeline_out": tl_out,
            "source_in": source_in}


# ── The comp ────────────────────────────────────────────────────────

def test_the_comp_merges_the_title_and_masks_the_subject_back():
    comp = build_effect_comp(
        {"behind_title_media": "/titles/t.mov",
         "behind_title_trim_in": 3,
         "behind_title_trim_out": 30,
         "behind_subject_matte": "/mattes/m_00000.png"},
        300, source_res=(1080, 1920))
    assert "Loader" in comp
    assert "/titles/t.mov" in comp
    assert "TrimIn = 3" in comp
    assert "Merge" in comp
    assert "EffectMask" in comp


def test_absent_title_or_matte_draws_nothing():
    comp = build_effect_comp({"behind_title_media": "/titles/t.mov"},
                             300, source_res=(1080, 1920))
    assert "BehindTitle" not in comp
    assert "SubjectOver" not in comp


# ── Grounding ───────────────────────────────────────────────────────

def test_no_segmentation_does_not_ground():
    grounded, refusal = behind_subject.ground_segment(
        {"segment_id": "t1"}, None, clip_id="c1")
    assert grounded is None
    assert refusal["reason"] == "no_segmentation"


def test_an_untracked_subject_does_not_ground():
    seg = _seg_result()
    seg["objects"][0]["label"] = "auto_object_1"
    grounded, refusal = behind_subject.ground_segment(
        {"segment_id": "t1"}, seg, clip_id="c1")
    assert grounded is None
    assert refusal["reason"] == "subject_not_tracked"


def test_a_tracked_subject_grounds():
    grounded, refusal = behind_subject.ground_segment(
        {"segment_id": "t1"}, _seg_result(), clip_id="c1")
    assert refusal is None
    assert grounded["object_id"] == "obj_1"


# ── The refusal ─────────────────────────────────────────────────────

def _inputs(title_file, clips):
    return {
        "segments": [{
            "segment_id": "t1", "overlay_path": title_file,
            "timeline_start": 2.0, "timeline_end": 6.0}],
        "clips": clips,
    }


def test_no_matte_refuses_by_name_not_silence():
    title = os.path.abspath(__file__)
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        behind_subject.apply_behind_subject(
            _inputs(title, [_clip()])["segments"], {"c1": None},
            clip_at=lambda s, e: [_clip()],
            matte_dir="/tmp", timeline_fps=30.0,
            clip_metadata={"c1": {"width": 32, "height": 24}})
    assert isinstance(exc.value, RenRefusal)
    assert "do not decode" in str(exc.value)


def test_a_missing_title_file_refuses():
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.apply_behind_subject(
            _inputs("/no/such/title.mov", [_clip()])["segments"],
            {"c1": _seg_result()},
            clip_at=lambda s, e: [_clip()],
            matte_dir="/tmp", timeline_fps=30.0,
            clip_metadata={"c1": {"width": 32, "height": 24}})


def test_no_clip_under_the_span_refuses():
    title = os.path.abspath(__file__)
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.apply_behind_subject(
            _inputs(title, [])["segments"], {"c1": _seg_result()},
            clip_at=lambda s, e: [],
            matte_dir="/tmp", timeline_fps=30.0,
            clip_metadata={"c1": {"width": 32, "height": 24}})


def test_a_grounded_segment_refuses_on_the_renderer_gate(tmp_path):
    # The grounding holds (ground_segment above proves it) but the
    # request refuses before patching: scripted Loaders do not decode
    # on timeline comps in Resolve Studio 21.1, measured 2026-09-24,
    # so there is no picture for the title to go under.
    title = tmp_path / "t.mov"
    title.write_bytes(b"fake")
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        behind_subject.apply_behind_subject(
            _inputs(str(title), [_clip()])["segments"],
            {"c1": _seg_result()},
            clip_at=lambda s, e: [_clip()],
            matte_dir=str(tmp_path / "mattes"), timeline_fps=30.0,
            clip_metadata={"c1": {"width": 32, "height": 24}})
    assert "do not decode" in str(exc.value)


def test_two_titles_on_one_clip_refuse():
    title = os.path.abspath(__file__)
    segments = [
        {"segment_id": "t1", "overlay_path": title,
         "timeline_start": 1.0, "timeline_end": 3.0},
        {"segment_id": "t2", "overlay_path": title,
         "timeline_start": 4.0, "timeline_end": 6.0},
    ]
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.apply_behind_subject(
            segments, {"c1": _seg_result()},
            clip_at=lambda s, e: [_clip()],
            matte_dir="/tmp", timeline_fps=30.0,
            clip_metadata={"c1": {"width": 32, "height": 24}})


# ── The validator half ──────────────────────────────────────────────

def test_a_behind_matte_missing_files_fails_validation(tmp_path):
    from library.tools import manifest_validator as mv
    record = {
        "clip_stem": "c1_clip1_behind", "object_id": "obj_1",
        "files": [str(tmp_path / "missing_00000.png")],
        "frame_count": 10, "resolution": [24, 32],
    }
    errors = mv.validate_manifest_semantics(
        {"tracks": {"V1": {"clips": []}},
         "behind_subject_mattes": [record]})
    assert any("behind_subject_mattes" in e for e in errors)
