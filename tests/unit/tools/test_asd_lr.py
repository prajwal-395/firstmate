"""The LR-ASD adapter validates before it measures and never fetches.

No test here loads weights: the loader tests exercise refusal paths only.
A future test that runs the real checkpoint must carry
``pytestmark = pytest.mark.real_model`` (lane_routing) so it leaves the
parallel lane.
"""

import pytest

from library.tools.asd_lr import (
    face_crop_rect,
    load_checkpoint,
    scores_to_observations,
    select_device,
)


def test_face_crop_replicates_calibration_geometry():
    assert face_crop_rect(
        (680.5, 166.0, 786.0, 322.4),
        sample_size=(1408, 792),
        source_size=(3840, 2160),
    ) == (1616, 282, 768)


def test_face_crop_clamps_to_frame_origin():
    x, y, side = face_crop_rect(
        (10.0, 10.0, 60.0, 60.0),
        sample_size=(1408, 792),
        source_size=(3840, 2160),
    )
    assert (x, y) == (0, 0)
    assert side > 0


def test_face_crop_refuses_degenerate_boxes():
    with pytest.raises(ValueError):
        face_crop_rect((10.0, 10.0, 10.0, 60.0),
                       sample_size=(1408, 792),
                       source_size=(3840, 2160))
    with pytest.raises(ValueError):
        face_crop_rect((10.0, float("nan"), 60.0, 60.0),
                       sample_size=(1408, 792),
                       source_size=(3840, 2160))


def test_explicit_cpu_never_needs_torch():
    assert select_device("cpu") == "cpu"


def test_unknown_device_refuses():
    with pytest.raises(ValueError):
        select_device("tpu")


def test_missing_checkpoint_refuses_before_torch(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_checkpoint(checkout_dir=tmp_path,
                        checkpoint=tmp_path / "absent.model")


def test_missing_checkout_refuses(tmp_path):
    checkpoint = tmp_path / "pretrain_AVA.model"
    checkpoint.write_bytes(b"not a checkpoint")
    with pytest.raises(FileNotFoundError):
        load_checkpoint(checkout_dir=tmp_path / "absent-checkout",
                        checkpoint=checkpoint)


def _observations():
    return scores_to_observations(
        scores=[0.9, 0.1, 0.5], fps=25, start_tick=40000,
        clock_id="clock-a", asset_id="asset-a", voice_track="voice-0",
        face_track="face-0", provenance_id="prov-asd",
        observation_id_prefix="obs-asd",
        voice_asset_id="asset-voice",
        frame_scores_path="/tmp/scores.json")


def test_per_frame_intervals_cover_each_frame_once():
    rows = _observations()
    assert [row["id"] for row in rows] == [
        "obs-asd-000000", "obs-asd-000001", "obs-asd-000002"]
    assert [row["interval"] for row in rows] == [
        {"clock_id": "clock-a", "start": 40000, "end": 40040},
        {"clock_id": "clock-a", "start": 40040, "end": 40080},
        {"clock_id": "clock-a", "start": 40080, "end": 40120},
    ]


def test_raw_scores_stay_uncalibrated_evidence():
    rows = _observations()
    assert [row["payload"]["raw_score"] for row in rows] == [0.9, 0.1, 0.5]
    assert {row["payload"]["score_kind"] for row in rows} == {"uncalibrated"}
    assert {row["confidence"] for row in rows} == {None}
    assert rows[0]["payload"]["voice_asset_id"] == "asset-voice"
    assert rows[0]["payload"]["frame_scores_path"] == "/tmp/scores.json"
    assert {row["modality"] for row in rows} == {"active_speaker_score"}


def test_non_finite_score_refuses():
    with pytest.raises(ValueError):
        scores_to_observations(
            scores=[0.9, float("nan")], fps=25, start_tick=0,
            clock_id="clock-a", asset_id="asset-a", voice_track="voice-0",
            face_track="face-0", provenance_id="prov-asd",
            observation_id_prefix="obs-asd")


def test_empty_scores_refuse():
    with pytest.raises(ValueError):
        scores_to_observations(
            scores=[], fps=25, start_tick=0,
            clock_id="clock-a", asset_id="asset-a", voice_track="voice-0",
            face_track="face-0", provenance_id="prov-asd",
            observation_id_prefix="obs-asd")
