"""The Ren-side mask seed: viewer geometry, the interior seed point,
and subject resolution against Ren's existing stores.

Two behaviours the brief pins, plus the refusals the seed must be able
to name:

1. The aspect-fit mapping on letterbox, pillarbox and exact fit, and
   its round trip - the geometry the captain gave, as a pure function.
2. The seed lands inside a concave mask (an L-shape) where the
   bounding-box centre falls outside - the whole reason the seed is
   not the bbox centre.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.analysis.object_segmentation import encode_rle
from library.tools.mask_seed import (
    MaskSeedRefused,
    Point,
    compute_seed,
    fit_image_rect,
    frame_to_sample,
    seed_point_in_mask,
    source_to_viewer,
    viewer_to_source,
)


def l_shape():
    """An L: the bounding-box centre (1, 1) is in the notch, outside."""
    return np.array(
        [
            [1, 1, 1],
            [1, 0, 0],
            [1, 0, 0],
        ],
        dtype=np.uint8,
    )


# ─────────────────────────────────────────────────────────
# 1. The viewer geometry
# ─────────────────────────────────────────────────────────


class TestFitImageRect:
    def test_letterbox_the_captains_own_example(self):
        """Source 1920x1080 in a 1440x900 viewer: image 1440x810 with
        45 px letterbox top and bottom."""
        rect = fit_image_rect(1920, 1080, 1440, 900)
        assert rect.left == pytest.approx(0.0)
        assert rect.top == pytest.approx(45.0)
        assert rect.width == pytest.approx(1440.0)
        assert rect.height == pytest.approx(810.0)

    def test_pillarbox_a_tall_source_in_a_wide_viewer(self):
        """Source 1080x1920 in a 1440x900 viewer: the height constrains,
        bars left and right."""
        rect = fit_image_rect(1080, 1920, 1440, 900)
        assert rect.left == pytest.approx((1440 - 1080 * (900 / 1920)) / 2)
        assert rect.top == pytest.approx(0.0)
        assert rect.width == pytest.approx(1080 * (900 / 1920))
        assert rect.height == pytest.approx(900.0)

    def test_exact_fit_matching_aspects(self):
        rect = fit_image_rect(1920, 1080, 1920, 1080)
        assert rect.left == pytest.approx(0.0)
        assert rect.top == pytest.approx(0.0)
        assert rect.width == pytest.approx(1920.0)
        assert rect.height == pytest.approx(1080.0)


class TestSourceToViewer:
    def test_the_captains_example(self):
        """Source (1132, 449) of 1920x1080, letterboxed into
        1440x900: viewer (849, 381.75)."""
        rect = fit_image_rect(1920, 1080, 1440, 900)
        viewer = source_to_viewer(Point(1132, 449), (1920, 1080), rect)
        assert viewer.x == pytest.approx(1132 * 0.75)
        assert viewer.y == pytest.approx(45 + 449 * 0.75)

    def test_retina_scale_factor_doubles_the_screen_point(self):
        """The image rect is in points; scale_factor is pixels per
        point, so a Retina display doubles the device-pixel point."""
        rect = fit_image_rect(1920, 1080, 1440, 900)
        point = source_to_viewer(Point(1132, 449), (1920, 1080), rect, scale_factor=2.0)
        assert point.x == pytest.approx(1132 * 0.75 * 2)
        assert point.y == pytest.approx((45 + 449 * 0.75) * 2)

    def test_round_trip_letterbox(self):
        rect = fit_image_rect(1920, 1080, 1440, 900)
        source = Point(1132, 449)
        back = viewer_to_source(
            source_to_viewer(source, (1920, 1080), rect), (1920, 1080), rect
        )
        assert back.x == pytest.approx(source.x)
        assert back.y == pytest.approx(source.y)

    def test_round_trip_pillarbox_with_retina(self):
        rect = fit_image_rect(1080, 1920, 1440, 900)
        source = Point(200, 1500)
        back = viewer_to_source(
            source_to_viewer(source, (1080, 1920), rect, scale_factor=2.0),
            (1080, 1920),
            rect,
            scale_factor=2.0,
        )
        assert back.x == pytest.approx(source.x)
        assert back.y == pytest.approx(source.y)

    def test_round_trip_exact_fit(self):
        rect = fit_image_rect(1920, 1080, 1920, 1080)
        source = Point(7, 13)
        back = viewer_to_source(
            source_to_viewer(source, (1920, 1080), rect), (1920, 1080), rect
        )
        assert back.x == pytest.approx(source.x)
        assert back.y == pytest.approx(source.y)


# ─────────────────────────────────────────────────────────
# 2. The seed point inside one mask
# ─────────────────────────────────────────────────────────


class TestSeedPointInMask:
    def test_l_shape_seed_is_inside_where_the_bbox_centre_is_not(self):
        """The defect this picker exists for: the bbox centre of an
        L-shape falls in the notch (background), so a bbox-centre seed
        would click background. The interior seed does not."""
        mask = l_shape()
        cx = (mask.shape[1] - 1) / 2
        cy = (mask.shape[0] - 1) / 2
        assert mask[int(cy), int(cx)] == 0
        seed = seed_point_in_mask(mask)
        assert mask[int(seed.y), int(seed.x)] == 1

    def test_square_mask_seeds_at_its_centre(self):
        mask = np.zeros((9, 9), dtype=np.uint8)
        mask[2:7, 2:7] = 1
        seed = seed_point_in_mask(mask)
        assert seed.x == pytest.approx(4.0)
        assert seed.y == pytest.approx(4.0)

    def test_seed_is_deterministic(self):
        """The same mask always yields the same seed - Ren seeds
        deterministically, so a receipt can be reproduced."""
        mask = l_shape()
        assert seed_point_in_mask(mask) == seed_point_in_mask(mask)

    def test_empty_mask_refuses_with_the_named_reason(self):
        with pytest.raises(MaskSeedRefused) as exc:
            seed_point_in_mask(np.zeros((5, 5), dtype=np.uint8))
        assert exc.value.what == "empty_mask"


# ─────────────────────────────────────────────────────────
# 3. Frame to sample mapping
# ─────────────────────────────────────────────────────────


class TestFrameToSample:
    def test_source_frame_maps_to_the_covering_sample(self):
        """1.06 samples at 2 fps: source frame 60 at 30 fps is 2 s in,
        so sample 4 covers it."""
        assert frame_to_sample(60, source_fps=30.0, sample_fps=2.0) == 4

    def test_first_frame_is_sample_zero(self):
        assert frame_to_sample(0, source_fps=30.0, sample_fps=2.0) == 0

    def test_frame_between_samples_picks_the_nearest_not_the_floor(self):
        """Frame 8 at 30 fps is 0.267 s in: sample 1 (0.5 s) is nearer
        than sample 0 (0.0 s). A floored mapping would seed on a mask
        0.27 s older than the frame the executor clicks on."""
        assert frame_to_sample(8, source_fps=30.0, sample_fps=2.0) == 1
        assert frame_to_sample(7, source_fps=30.0, sample_fps=2.0) == 0


# ─────────────────────────────────────────────────────────
# 4. Subject resolution and the seed record
# ─────────────────────────────────────────────────────────


def _seg_doc(mask, *, object_id="obj_1", sample_fps=2.0, frames=(0,)):
    """One clip's 1.06 segmentation JSON, as the store writes it."""
    return {
        "video_path": "/footage/clip.mov",
        "frame_count": 200,
        "resolution": [1080, 1920],
        "sample_fps": sample_fps,
        "seed_note": "face_seeded: test",
        "objects": [
            {
                "object_id": object_id,
                "label": "face_seeded_subject",
                "category": "person",
                "frames": list(frames),
                "masks_rle": {str(f): encode_rle(mask) for f in frames},
                "bboxes": {
                    str(f): [0, 0, mask.shape[1], mask.shape[0]] for f in frames
                },
                "avg_area_ratio": 0.1,
            }
        ],
    }


def _person_project(tmp_path, *, seg_doc_dict, person=None, spans=None):
    """A fake project folder with a segmentation file and (optionally)
    a person roster, wired the way the real stores are."""
    project = tmp_path / "project"
    seg_dir = project / "pipeline_output" / "steps" / "1_06_object_segmentation"
    seg_dir.mkdir(parents=True)
    with open(seg_dir / "clip_001_segmentation.json", "w", encoding="utf-8") as handle:
        json.dump(seg_doc_dict, handle)
    catalog = {
        "capability_outputs": {
            "footage.catalog": {
                "clip_catalog": [{"clip_id": "clip_001", "path": "/footage/clip.mov"}]
            }
        }
    }
    if person is not None:
        catalog["source_fingerprints"] = {
            "clip_001": {"content_digest": person["content_digest"]}
        }
    with open(project / "pipeline_data.json", "w", encoding="utf-8") as h:
        json.dump(catalog, h)
    return project


def _write_identity(root, *, content_digest, spans):
    """One source's M3b identity record under a memory root."""
    source_dir = root / content_digest
    source_dir.mkdir(parents=True, exist_ok=True)
    faces = []
    for i, span in enumerate(spans):
        faces.append(
            {
                "track_id": f"face_{i + 1:03d}",
                "embedding": [0.1 * (i + 1), 0.2, 0.3, 0.4],
                "spans": [span],
            }
        )
    doc = {
        "content_digest": content_digest,
        "source_file": "/footage/clip.mov",
        "status": "measured",
        "faces": faces,
        "voices": [],
        "speech_face_links": [],
    }
    with open(source_dir / "identity.json", "w", encoding="utf-8") as h:
        json.dump(doc, h)


class TestComputeSeed:
    def test_detection_id_seeds_inside_the_mask(self, tmp_path):
        mask = np.zeros((1080, 1920), dtype=np.uint8)
        mask[400:600, 800:1000] = 1
        project = _person_project(tmp_path, seg_doc_dict=_seg_doc(mask))
        seed = compute_seed(
            project_folder=str(project),
            clip_id="clip_001",
            frame=0,
            subject="obj_1",
            image_rect=fit_image_rect(1920, 1080, 1440, 900),
            source_fps=30.0,
        )
        assert seed.clip_id == "clip_001"
        assert seed.frame == 0
        assert seed.subject == "obj_1"
        assert seed.object_id == "obj_1"
        assert mask[int(seed.source.y), int(seed.source.x)] == 1
        expected = source_to_viewer(
            seed.source, (1920, 1080), fit_image_rect(1920, 1080, 1440, 900)
        )
        assert seed.viewer.x == pytest.approx(expected.x)
        assert seed.viewer.y == pytest.approx(expected.y)

    def test_frame_between_samples_seeds_on_the_nearest_samples_mask(self, tmp_path):
        """Frame 8 at 30 fps falls between sample 0 (0.0 s) and sample 1
        (0.5 s): sample 1 is nearer, so the seed comes from sample 1's
        mask and the record says the seed is valid for frame 15 - the
        frame the executor must park the playhead on."""
        mask_0 = np.zeros((1080, 1920), dtype=np.uint8)
        mask_0[100:200, 100:200] = 1
        mask_1 = np.zeros((1080, 1920), dtype=np.uint8)
        mask_1[400:600, 800:1000] = 1
        seg = _seg_doc(mask_0, frames=(0, 1))
        seg["objects"][0]["masks_rle"] = {
            "0": encode_rle(mask_0),
            "1": encode_rle(mask_1),
        }
        project = _person_project(tmp_path, seg_doc_dict=seg)
        seed = compute_seed(
            project_folder=str(project),
            clip_id="clip_001",
            frame=8,
            subject="obj_1",
            image_rect=fit_image_rect(1920, 1080, 1440, 900),
            source_fps=30.0,
        )
        assert seed.sample_index == 1
        assert seed.seed_frame == 15
        assert seed.frame == 8
        assert mask_1[int(seed.source.y), int(seed.source.x)] == 1
        receipt = seed.to_receipt()
        assert receipt["frame"] == 8
        assert receipt["seed_frame"] == 15

    def test_seed_record_carries_the_receipt_shape(self, tmp_path):
        mask = np.ones((1080, 1920), dtype=np.uint8)
        project = _person_project(tmp_path, seg_doc_dict=_seg_doc(mask))
        seed = compute_seed(
            project_folder=str(project),
            clip_id="clip_001",
            frame=0,
            subject="obj_1",
            image_rect=fit_image_rect(1920, 1080, 1440, 900),
            source_fps=30.0,
        )
        receipt = seed.to_receipt()
        assert receipt["clip_id"] == "clip_001"
        assert receipt["frame"] == 0
        assert receipt["seed_frame"] == 0
        assert receipt["subject"] == "obj_1"
        assert set(receipt["source"]) == {"x", "y"}
        assert set(receipt["viewer"]) == {"x", "y"}

    def test_person_identity_seeds_through_the_face_bridge(self, tmp_path):
        """A person_id resolves via the M3b roster: the face span at
        the frame places the face, and the tracked subject whose mask
        holds it answers."""
        mask = np.zeros((1080, 1920), dtype=np.uint8)
        mask[300:700, 700:1100] = 1
        project = _person_project(
            tmp_path,
            seg_doc_dict=_seg_doc(mask, frames=(0, 1, 2, 3, 4)),
            person={"content_digest": "digest1"},
        )
        root = tmp_path / "memory"
        _write_identity(
            root,
            content_digest="digest1",
            spans=[
                {
                    "start": 0.0,
                    "end": 5.0,
                    "box": [850, 450, 950, 550],
                    "det_score": 0.9,
                },
            ],
        )
        seed = compute_seed(
            project_folder=str(project),
            clip_id="clip_001",
            frame=30,
            subject="person_001",
            image_rect=fit_image_rect(1920, 1080, 1440, 900),
            source_fps=30.0,
            person_root=root,
        )
        assert seed.object_id == "obj_1"
        assert mask[int(seed.source.y), int(seed.source.x)] == 1

    def test_no_segmentation_refuses_with_the_named_reason(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        with pytest.raises(MaskSeedRefused) as exc:
            compute_seed(
                project_folder=str(project),
                clip_id="clip_001",
                frame=0,
                subject="obj_1",
                image_rect=fit_image_rect(1920, 1080, 1440, 900),
                source_fps=30.0,
            )
        assert exc.value.what == "no_segmentation"

    def test_missing_sample_fps_refuses_rather_than_guessing(self, tmp_path):
        """The segmentation always writes sample_fps; a result without
        one is refused by name, never completed with the 2.0 the engine
        happens to run at."""
        mask = np.ones((1080, 1920), dtype=np.uint8)
        seg = _seg_doc(mask)
        del seg["sample_fps"]
        project = _person_project(tmp_path, seg_doc_dict=seg)
        with pytest.raises(MaskSeedRefused) as exc:
            compute_seed(
                project_folder=str(project),
                clip_id="clip_001",
                frame=0,
                subject="obj_1",
                image_rect=fit_image_rect(1920, 1080, 1440, 900),
                source_fps=30.0,
            )
        assert exc.value.what == "no_segmentation"

    def test_unknown_subject_refuses_with_the_named_reason(self, tmp_path):
        mask = np.ones((1080, 1920), dtype=np.uint8)
        project = _person_project(tmp_path, seg_doc_dict=_seg_doc(mask))
        with pytest.raises(MaskSeedRefused) as exc:
            compute_seed(
                project_folder=str(project),
                clip_id="clip_001",
                frame=0,
                subject="obj_9",
                image_rect=fit_image_rect(1920, 1080, 1440, 900),
                source_fps=30.0,
            )
        assert exc.value.what == "unknown_subject"

    def test_subject_absent_on_the_frame_refuses_with_the_name(self, tmp_path):
        """The object is tracked, but not at the sample the frame maps
        to - the subject is not on this frame."""
        mask = np.ones((1080, 1920), dtype=np.uint8)
        project = _person_project(tmp_path, seg_doc_dict=_seg_doc(mask, frames=(0,)))
        with pytest.raises(MaskSeedRefused) as exc:
            compute_seed(
                project_folder=str(project),
                clip_id="clip_001",
                frame=60,
                subject="obj_1",
                image_rect=fit_image_rect(1920, 1080, 1440, 900),
                source_fps=30.0,
            )
        assert exc.value.what == "subject_absent_on_frame"

    def test_person_absent_on_the_frame_refuses_with_the_name(self, tmp_path):
        mask = np.ones((1080, 1920), dtype=np.uint8)
        project = _person_project(
            tmp_path, seg_doc_dict=_seg_doc(mask), person={"content_digest": "digest1"}
        )
        root = tmp_path / "memory"
        _write_identity(
            root,
            content_digest="digest1",
            spans=[
                {
                    "start": 0.0,
                    "end": 1.0,
                    "box": [850, 450, 950, 550],
                    "det_score": 0.9,
                },
            ],
        )
        with pytest.raises(MaskSeedRefused) as exc:
            compute_seed(
                project_folder=str(project),
                clip_id="clip_001",
                frame=60,
                subject="person_001",
                image_rect=fit_image_rect(1920, 1080, 1440, 900),
                source_fps=30.0,
                person_root=root,
            )
        assert exc.value.what == "subject_absent_on_frame"

    def test_person_face_outside_every_mask_refuses_with_the_name(self, tmp_path):
        """The tracked subject is a different person: the face falls
        inside no tracked mask, so there is no honest seed."""
        mask = np.zeros((1080, 1920), dtype=np.uint8)
        mask[100:200, 100:200] = 1
        project = _person_project(
            tmp_path, seg_doc_dict=_seg_doc(mask), person={"content_digest": "digest1"}
        )
        root = tmp_path / "memory"
        _write_identity(
            root,
            content_digest="digest1",
            spans=[
                {
                    "start": 0.0,
                    "end": 5.0,
                    "box": [1500, 800, 1600, 900],
                    "det_score": 0.9,
                },
            ],
        )
        with pytest.raises(MaskSeedRefused) as exc:
            compute_seed(
                project_folder=str(project),
                clip_id="clip_001",
                frame=0,
                subject="person_001",
                image_rect=fit_image_rect(1920, 1080, 1440, 900),
                source_fps=30.0,
                person_root=root,
            )
        assert exc.value.what == "subject_not_tracked"

    def test_face_inside_two_masks_refuses_as_ambiguous(self, tmp_path):
        """Two tracked objects hold the face: the frame cannot decide
        which subject is theirs, so it refuses rather than picks."""
        mask_a = np.zeros((1080, 1920), dtype=np.uint8)
        mask_a[300:700, 700:1100] = 1
        mask_b = np.zeros((1080, 1920), dtype=np.uint8)
        mask_b[300:700, 700:1100] = 1
        seg = _seg_doc(mask_a)
        seg["objects"].append(
            {
                "object_id": "obj_2",
                "label": "auto_object_2",
                "category": "unknown",
                "frames": [0],
                "masks_rle": {"0": encode_rle(mask_b)},
                "bboxes": {"0": [700, 300, 400, 400]},
                "avg_area_ratio": 0.1,
            }
        )
        project = _person_project(
            tmp_path, seg_doc_dict=seg, person={"content_digest": "digest1"}
        )
        root = tmp_path / "memory"
        _write_identity(
            root,
            content_digest="digest1",
            spans=[
                {
                    "start": 0.0,
                    "end": 5.0,
                    "box": [850, 450, 950, 550],
                    "det_score": 0.9,
                },
            ],
        )
        with pytest.raises(MaskSeedRefused) as exc:
            compute_seed(
                project_folder=str(project),
                clip_id="clip_001",
                frame=0,
                subject="person_001",
                image_rect=fit_image_rect(1920, 1080, 1440, 900),
                source_fps=30.0,
                person_root=root,
            )
        assert exc.value.what == "ambiguous_subject"
