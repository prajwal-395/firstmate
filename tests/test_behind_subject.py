"""A title composited BEHIND the subject, precomposited under its matte.

A behind_subject segment grounds against a real tracked subject and
precomposites the rendered title under its inverted matte into a
premultiplied `qtrle` overlay, placed as a normal clip on an overlay
row above the picture. Anything that cannot be grounded REFUSES by
name (`BehindSubjectRefused`) - never dropped with a reason, never
drawn on top.
"""

import os
import shutil
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
from library.tools.overlay_carriage import (
    OVERLAY_FORMAT_NAME,
    assert_transparent_region_unchanged,
)
from library.tools.ren_refusal import RenRefusal


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg and ffprobe - CI installs both")


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


def _title_sequence(directory, stem="t_behind", count=40,
                    size=(32, 24), color=(255, 255, 255, 255)):
    """A 4.06-shaped behind title: numbered RGBA PNGs plus the record."""
    from PIL import Image

    os.makedirs(directory, exist_ok=True)
    pattern = os.path.join(directory, f"{stem}_%05d.png")
    for i in range(count):
        Image.fromarray(
            np.full((size[1], size[0], 4), color,
                    dtype=np.uint8), mode="RGBA").save(pattern % i)
    return {
        "segment_id": "t1",
        "overlay_path": pattern % 0,
        "timeline_start": 2.0,
        "timeline_end": 6.0,
        "total_frames": count,
        "sequence": {"pattern": pattern, "first_frame": pattern % 0,
                     "frame_count": count},
    }


# ── Grounding ───────────────────────────────────────────────────────

def test_what_cannot_ground_says_why():
    grounded, refusal = behind_subject.ground_segment(
        {"segment_id": "t1"}, None, clip_id="c1")
    assert grounded is None
    assert refusal["reason"] == "no_segmentation"
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


# ── The refusal: no usable matte, by name ───────────────────────────

def test_what_cannot_be_precomposited_refuses_by_name(tmp_path):
    """No matte, no title file, no clip under the span, a title that runs
    dry, a matte or a title canvas at the wrong size: each raises
    `BehindSubjectRefused` (a `RenRefusal`), never silence."""
    def refuse(name, segment=None, seg=None, clips=None, meta=(32, 24)):
        segment = segment or _title_sequence(str(tmp_path / name))
        clips = [_clip()] if clips is None else clips
        with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
            behind_subject.apply_behind_subject(
                [segment], {"c1": seg},
                clip_at=lambda s, e: clips,
                matte_dir=str(tmp_path / name / "mattes"),
                precomp_dir=str(tmp_path / name / "precomp"),
                timeline_fps=10.0,
                clip_metadata={"c1": {"width": meta[0], "height": meta[1]}})
        assert isinstance(exc.value, RenRefusal)
        return str(exc.value)

    assert "no usable matte" in refuse("no_matte", seg=None)
    gone = _title_sequence(str(tmp_path / "gone_title"))
    gone["sequence"]["pattern"] = str(tmp_path / "gone" / "t_%05d.png")
    refuse("gone_title", segment=gone, seg=_seg_result())
    refuse("no_clip", seg=_seg_result(), clips=[])
    dry = _title_sequence(str(tmp_path / "dry"), count=10)
    assert "runs dry" in refuse("dry", segment=dry, seg=_seg_result())
    refuse("matte_size", seg=_seg_result(), meta=(64, 48))
    small = _title_sequence(str(tmp_path / "canvas"), size=(16, 16))
    assert "wrong pixels" in refuse("canvas", segment=small,
                                    seg=_seg_result())


# ── The precomposite ────────────────────────────────────────────────

@needs_ffmpeg
def test_a_grounded_segment_precomposites_under_the_matte(tmp_path):
    segment = _title_sequence(str(tmp_path / "titles"))
    placed, mattes, per_segment = behind_subject.apply_behind_subject(
        [segment],
        {"c1": _seg_result()},
        clip_at=lambda s, e: [_clip()],
        matte_dir=str(tmp_path / "mattes"),
        precomp_dir=str(tmp_path / "precomp"), timeline_fps=10.0,
        clip_metadata={"c1": {"width": 32, "height": 24}})
    assert len(mattes) == 1
    assert len(mattes[0]["files"]) == 40
    assert len(placed) == 1
    entry = placed[0]
    assert entry["layer"] == "behind_subject"
    assert entry["timeline_start"] == 2.0
    assert entry["timeline_end"] == 6.0
    assert entry["total_frames"] == 40
    assert entry["format"] == OVERLAY_FORMAT_NAME
    assert entry["has_alpha"] is True
    assert os.path.isfile(entry["overlay_path"])
    assert per_segment[0]["clips"] == ["c1"]
    assert per_segment[0]["precomps"][0]["overlay_path"] == \
        entry["overlay_path"]

    # The punch itself, read off the placed file: the subject's box
    # is transparent black, the title draws everywhere else.
    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", entry["overlay_path"],
         "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120, check=False)
    assert proc.returncode == 0
    frames = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(
        40, 24, 32, 4)
    mid = frames[20]
    assert (mid[6:18, 10:22, 3] == 0).all()
    assert (mid[6:18, 10:22, :3] == 0).all()
    assert (mid[0:6, :, 3] == 255).all()
    assert (mid[0:6, :, :3] == 255).all()

    # And the acceptance check from the scout report: composited over
    # a plate, the plate comes through untouched wherever the
    # overlay's own alpha is zero.
    plate = np.full((24, 32, 3), 128, dtype=np.uint8)
    alpha = mid[..., 3].astype(np.float64)
    comp = (mid[..., :3].astype(np.float64)
            + plate.astype(np.float64) * (255 - alpha)[..., None]
            / 255.0).astype(np.uint8)
    assert_transparent_region_unchanged(comp, plate, mid[..., 3],
                                        what="behind_subject precomp")


@needs_ffmpeg
def test_partial_alpha_and_partial_matte_multiply(tmp_path):
    from PIL import Image

    title = np.zeros((2, 2, 4), dtype=np.uint8)
    title[..., :] = (200, 100, 50, 128)
    title_path = str(tmp_path / "title.png")
    Image.fromarray(title, mode="RGBA").save(title_path)
    matte = np.array([[0, 128], [255, 0]], dtype=np.uint8)
    matte_path = str(tmp_path / "matte.png")
    Image.fromarray(matte, mode="L").save(matte_path)
    out = str(tmp_path / "precomp.mov")
    record = behind_subject.precomposite_title_under_matte(
        [title_path, title_path], [matte_path, matte_path], out,
        fps=10.0, segment_id="t", clip_id="c")
    assert record["frame_count"] == 2

    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", out,
         "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120, check=False)
    got = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(2, 2, 2, 4)
    # (200,100,50,128) over no matte: alpha stays 128, RGB scaled.
    assert got[0, 0, 0].tolist() == [100, 50, 25, 128]
    # Half matte: alpha 128 -> ~64, RGB follows the punched alpha.
    assert got[0, 0, 1, 3] == 64
    assert got[0, 0, 1, 0] == 50
    # Full matte: exact zeros - what the transparent check measures.
    assert got[0, 1, 0].tolist() == [0, 0, 0, 0]


@needs_ffmpeg
def test_two_titles_on_one_clip_punch_their_own_holes(tmp_path):
    first = _title_sequence(str(tmp_path / "titles"), stem="first")
    first["timeline_start"] = 1.0
    first["timeline_end"] = 3.0
    second = _title_sequence(str(tmp_path / "titles"), stem="second",
                             count=40)
    second["segment_id"] = "t2"
    second["timeline_start"] = 5.0
    second["timeline_end"] = 7.0
    placed, mattes, per_segment = behind_subject.apply_behind_subject(
        [first, second], {"c1": _seg_result()},
        clip_at=lambda s, e: [_clip()],
        matte_dir=str(tmp_path / "mattes"),
        precomp_dir=str(tmp_path / "precomp"), timeline_fps=10.0,
        clip_metadata={"c1": {"width": 32, "height": 24}})
    assert len(placed) == 2
    assert placed[0]["overlay_path"] != placed[1]["overlay_path"]
    assert all(os.path.isfile(p["overlay_path"]) for p in placed)
    assert [p["timeline_start"] for p in placed] == [1.0, 5.0]
    assert [s["segment_id"] for s in per_segment] == ["t1", "t2"]


# ── The registration guard ────────────────────────────────────────

def _behind_windows():
    return {"clip1": [("t1", 2.0, 6.0)]}


def test_a_geometric_op_on_the_behind_clip_refuses_by_name():
    """A zoom, a backdrop reframe or a stabilize moves the picture off
    the registered matte."""
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(),
            per_clip_effects={"clip1": {"zoom_start": 1.0,
                                        "zoom_end": 1.06}})
    assert "t1" in str(exc.value)
    assert "clip1" in str(exc.value)
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(),
            per_clip_effects={"clip1": {
                "backdrop_picture_scale": 0.8}})
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(), per_clip_effects={},
            stabilized_labels=["clip1"])
    assert "stabilize" in str(exc.value)


def test_an_overlapping_speed_ramp_refuses_but_a_disjoint_one_passes():
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(), per_clip_effects={},
            speed_ops=[{"label": "clip1", "effect_type": "speed_ramp",
                        "timeline_start": 5.0, "timeline_end": 8.0}])
    behind_subject.assert_no_geometric_overlap(
        _behind_windows(), per_clip_effects={},
        speed_ops=[{"label": "clip1", "effect_type": "speed_ramp",
                    "timeline_start": 6.0, "timeline_end": 8.0}])
    behind_subject.assert_no_geometric_overlap(
        _behind_windows(), per_clip_effects={},
        speed_ops=[{"label": "other", "effect_type": "speed_ramp",
                    "timeline_start": 2.0, "timeline_end": 6.0}])


def test_photometric_keys_do_not_trip_the_guard():
    behind_subject.assert_no_geometric_overlap(
        _behind_windows(),
        per_clip_effects={"clip1": {"grade_gain": 1.1,
                                    "glow_gain": 0.5,
                                    "subject_grade_matte": "/m.png"}})
    behind_subject.assert_no_geometric_overlap({})


# ── No comp path anymore ────────────────────────────────────────────

def test_stale_behind_keys_draw_no_loader_comp():
    comp = build_effect_comp(
        {"behind_title_media": "/titles/t.mov",
         "behind_title_trim_in": 3,
         "behind_title_trim_out": 30,
         "behind_subject_matte": "/mattes/m_00000.png"},
        300, source_res=(1080, 1920))
    assert "Loader" not in comp
    assert "BehindTitle" not in comp
    assert "SubjectOver" not in comp


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
