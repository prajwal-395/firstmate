"""FI-14: camera and subject movement stay separate on a synthetic pan."""

import numpy as np
import pytest
from PIL import Image

from library.tools import (
    motion_evidence,
    person_regions,
    project_model_masks,
    region_stats,
    source_memory,
)
from library.tools.motion_evidence import analyze_pairs

cv2 = pytest.importorskip("cv2")


def _shift(array, dx, dy=0):
    return np.roll(np.roll(array, dy, axis=0), dx, axis=1)


def _fixture(tmp_path):
    rng = np.random.default_rng(1403)
    h, w = 100, 160
    background = rng.integers(15, 210, (h, w), dtype=np.uint8)
    subject_texture = rng.integers(30, 240, (30, 28), dtype=np.uint8)
    samples = []
    for i in range(6):
        # The background pans right two pixels per half-second sample.
        frame = _shift(background, 2 * i).copy()
        # The independently moving subject advances three additional pixels.
        x = 52 + 5 * i
        frame[35:65, x : x + 28] = subject_texture
        mask = np.zeros((h, w), dtype=bool)
        mask[35:65, x : x + 28] = True
        path = tmp_path / f"frame-{i}.png"
        Image.fromarray(frame).save(path)
        samples.append({"t": i * 0.5, "path": str(path), "masks": {"person-1": mask}})
    return samples


def test_pan_and_independently_moving_subject_are_separate(tmp_path):
    result = analyze_pairs(_fixture(tmp_path), cv2_module=cv2)

    assert result["coverage"]["sample_pairs_expected"] == 5
    assert result["coverage"]["sample_pairs_measured"] == 5
    assert result["coverage"]["decomposed_pairs"] == 5
    assert result["coverage"]["kind"] == "complete"
    assert result["coverage"]["temporal_sampling"] == "sampled; not frame-complete"
    [first] = [pair for pair in result["pairs"][:1]]
    assert first["camera"]["translation_px"][0] == pytest.approx(2, abs=1.5)
    assert first["camera"]["residual_p50_px"] >= 0
    [person] = first["subject_motion"]
    assert person["track"] == "person-1"
    assert person["relative_to_camera_px"][0] > 0
    assert first["decomposition"]["status"] == "measured"
    assert all(pair["end"] > pair["start"] for pair in result["pairs"])


def test_missing_masks_explicitly_decline_camera_subject_decomposition(tmp_path):
    samples = _fixture(tmp_path)
    for sample in samples:
        sample.pop("masks")

    result = analyze_pairs(samples, cv2_module=cv2)

    assert result["coverage"]["kind"] == "complete"
    assert result["coverage"]["decomposed_pairs"] == 0
    assert result["pairs"][0]["camera"] is None
    assert result["pairs"][0]["subject_motion"] is None
    assert result["pairs"][0]["decomposition"] == {
        "status": "unsupported",
        "reason": "no M3d masks",
    }
    assert result["pairs"][0]["novelty"] is not None


def test_failed_frame_pair_is_a_coverage_gap(tmp_path):
    samples = _fixture(tmp_path)
    samples[2]["path"] = str(tmp_path / "missing.png")

    result = analyze_pairs(samples, cv2_module=cv2)

    assert result["coverage"]["sample_pairs_expected"] == 5
    assert result["coverage"]["sample_pairs_measured"] == 3
    assert result["coverage"]["kind"] == "sampled"
    assert result["pairs"][1]["status"] == "unmeasured"
    assert result["pairs"][2]["status"] == "unmeasured"
    assert result["pairs"][1]["novelty"] is None


def test_nonpositive_source_interval_is_not_reported_as_motion(tmp_path):
    samples = _fixture(tmp_path)[:2]
    samples[1]["t"] = samples[0]["t"]

    result = analyze_pairs(samples, cv2_module=cv2)

    assert result["coverage"]["sample_pairs_measured"] == 0
    assert result["pairs"][0]["status"] == "unmeasured"
    assert result["pairs"][0]["reason"] == (
        "M2 pair has a non-positive source interval"
    )
    assert result["pairs"][0]["start"] == result["pairs"][0]["end"]


def test_a_single_visual_change_gets_an_interval_boundary(tmp_path):
    rng = np.random.default_rng(1404)
    base = rng.integers(0, 255, (60, 80), dtype=np.uint8)
    samples = []
    for i in range(6):
        frame = (base if i < 3 else 255 - base).copy()
        mask = np.zeros((60, 80), dtype=bool)
        mask[15:45, 25:55] = True
        path = tmp_path / f"cut-{i}.png"
        Image.fromarray(frame).save(path)
        samples.append({"t": i * 0.5, "path": str(path), "masks": {"person-1": mask}})

    result = analyze_pairs(samples, cv2_module=cv2)

    assert result["boundaries"]
    assert result["boundaries"][0]["time"] == pytest.approx(1.5)
    assert result["pairs"][2]["novelty"] > result["pairs"][0]["novelty"]


def test_cached_m2_and_fi11_region_readings_are_reused_without_decode(
    tmp_path, monkeypatch
):
    a = np.full((20, 30), 80, dtype=np.uint8)
    b = np.full((20, 30), 120, dtype=np.uint8)
    paths = []
    for name, frame in (("m2-a.png", a), ("m2-b.png", b)):
        path = tmp_path / name
        Image.fromarray(frame).save(path)
        paths.append(str(path))
    frames = [
        {"t": 0.0, "file": "frames/a.png"},
        {"t": 0.5, "file": "frames/b.png"},
    ]
    monkeypatch.setattr(
        source_memory, "digest_for_clip", lambda *_args: ("digest", "live")
    )
    monkeypatch.setattr(source_memory, "read_m2", lambda *_args: {"frames": frames})
    monkeypatch.setattr(source_memory, "m2_frames_present", lambda *_args: True)
    monkeypatch.setattr(source_memory, "is_fresh", lambda *_args: True)
    monkeypatch.setattr(
        source_memory, "frame_abspath", lambda _d, f, _r: paths[0 if f["t"] == 0 else 1]
    )
    result = motion_evidence.read_cached_motion(
        "/unused/project",
        {"clip_id": "clip_001", "source_file": "/unused/source"},
        root=tmp_path / "memory",
    )

    assert result["coverage"]["sample_pairs_measured"] == 1
    assert result["pairs"][0]["decomposition"]["status"] == "unsupported"
    assert result["pairs"][0]["novelty"] is not None
    assert motion_evidence.M3D_CODEC == project_model_masks.REGION_RUNS_CODEC
    assert motion_evidence.M3F_METHOD == region_stats.M3F_METHOD
    assert motion_evidence._region_luma_change(
        {
            "whole_frame": {"luma_p50": 90},
            "regions": [{"track": "p1", "inside": {"luma_p50": 70}}],
        },
        {
            "whole_frame": {"luma_p50": 100},
            "regions": [{"track": "p1", "inside": {"luma_p50": 75}}],
        },
    ) == {
        "method": "region-stats-v2-m2",
        "luma_p50_delta": {"whole_frame": 10.0, "p1": 5.0},
    }


def test_cached_m3d_masks_and_m3f_readings_join_on_m2_time(tmp_path, monkeypatch):
    digest = "fi14-fixture-digest"
    root = tmp_path / "memory"
    source_dir = source_memory.source_dir(digest, root)
    frames_dir = source_dir / source_memory.SLOT_FRAMES_DIR
    frames_dir.mkdir(parents=True)
    width, height = 48, 32
    rng = np.random.default_rng(1411)
    background = rng.integers(20, 220, (height, width), dtype=np.uint8)
    encoded_masks, frame_rows, m3d_samples, m3f_samples = [], [], [], []
    for i, t in enumerate((0.0, 0.5)):
        frame = np.roll(background, i, axis=1).copy()
        x = 16 + i * 2
        frame[8:24, x : x + 12] = 245
        mask = np.zeros((height, width), dtype=bool)
        mask[8:24, x : x + 12] = True
        encoded_masks.append(person_regions.encode_runs(mask))
        filename = f"{source_memory.SLOT_FRAMES_DIR}/sample-{i}.png"
        Image.fromarray(frame).save(source_dir / filename)
        frame_rows.append({"t": t, "file": filename})
        m3d_samples.append(
            {
                "t": t,
                "instances": [{"track": "person-1", "mask": i}],
            }
        )
        m3f_samples.append(
            {
                "t": t,
                "whole_frame": {"luma_p50": 100 + i * 2},
                "outside": {"luma_p50": 110 + i * 3},
                "regions": [{"track": "person-1", "inside": {"luma_p50": 70 + i * 5}}],
            }
        )
    source_memory.write_json(
        source_dir / source_memory.SLOT_FRAMES_INDEX,
        {
            "content_digest": digest,
            "frames": frame_rows,
            "frame_count": len(frame_rows),
        },
    )
    sidecar = person_regions.write_sidecar(source_dir, encoded_masks, [width, height])
    source_memory.write_json(
        source_dir / source_memory.SLOT_REGIONS,
        {
            "content_digest": digest,
            "grid": [width, height],
            "sidecar": sidecar,
            "samples": m3d_samples,
        },
    )
    source_memory.write_json(
        source_dir / source_memory.SLOT_REGION_STATS,
        {
            "content_digest": digest,
            "samples": m3f_samples,
            "instrument": {
                "method": region_stats.M3F_METHOD,
                "m3d_sidecar_sha256": sidecar["sha256"],
            },
        },
    )
    monkeypatch.setattr(
        source_memory, "digest_for_clip", lambda *_args: (digest, "live")
    )
    monkeypatch.setattr(source_memory, "is_fresh", lambda *_args: True)

    result = motion_evidence.read_cached_motion(
        "/unused/project",
        {"clip_id": "clip_001", "source_file": "/unused/source"},
        root=root,
    )

    assert result["coverage"]["decomposed_pairs"] == 1
    pair = result["pairs"][0]
    assert pair["subject_motion"][0]["track"] == "person-1"
    assert pair["region_luma_change"] == {
        "method": region_stats.M3F_METHOD,
        "luma_p50_delta": {
            "whole_frame": 2.0,
            "outside": 3.0,
            "person-1": 5.0,
        },
    }
