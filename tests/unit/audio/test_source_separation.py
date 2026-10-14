import numpy as np
import pytest

from library.tools.analysis.source_separation import (
    SeparatedSignal,
    bleed_db,
    cached_model_path,
    project_model_signal,
    records_for_stems,
    si_sdr,
    synthetic_c6_fixture,
    write_separated_outputs,
)


def test_c6_fixture_is_repeatable_and_labels_known_mixture_stems():
    first = synthetic_c6_fixture(duration_s=0.1)
    second = synthetic_c6_fixture(duration_s=0.1)
    assert first.keys() == second.keys()
    for key in ("dialogue", "music", "ambience", "mixture"):
        np.testing.assert_array_equal(first[key], second[key])
    np.testing.assert_allclose(
        first["mixture"], first["dialogue"] + first["music"] + first["ambience"],
        atol=1e-7,
    )


def test_si_sdr_and_bleed_report_known_fixture_quality():
    fixture = synthetic_c6_fixture(duration_s=0.1)
    desired = fixture["dialogue"]
    estimate = desired + 0.01 * fixture["music"]
    assert si_sdr(desired, desired) > 100
    assert si_sdr(desired, estimate) > 20
    interference = bleed_db(estimate, desired, {
        "music": fixture["music"], "ambience": fixture["ambience"]
    })
    assert interference < -30


def test_separated_signal_record_requires_source_lineage_and_estimated_status(tmp_path):
    source = tmp_path / "original.wav"
    source.write_bytes(b"input audio sentinel")
    original_bytes = source.read_bytes()
    output_dir = tmp_path / "separated"
    stems = {"dialogue_candidate": SeparatedSignal(
            "src-1:dialogue_candidate", "dialogue_candidate", "src-1", "estimated",
            "demucs-infer/htdemucs", "htdemucs", 44100, np.zeros((2, 8)),
        )}
    artifact_refs = write_separated_outputs(
        stems, output_dir, artifact_prefix="pipeline_output/audio/separated")
    assert (output_dir / "dialogue_candidate.wav").is_file()
    assert source.read_bytes() == original_bytes
    assert artifact_refs == {
        "dialogue_candidate": "pipeline_output/audio/separated/dialogue_candidate.wav"
    }
    rows = records_for_stems(stems, artifact_refs={
        "dialogue_candidate": "audio/derived/dialogue.wav"})
    assert rows == [{
        "id": "src-1:dialogue_candidate", "kind": "dialogue_candidate", "source_signal_id": "src-1",
        "status": "estimated", "method": "demucs-infer/htdemucs",
        "model": "htdemucs", "sample_rate": 44100,
        "samples_ref": "audio/derived/dialogue.wav",
    }]
    with pytest.raises(FileExistsError, match="refusing to replace"):
        write_separated_outputs(stems, output_dir)


def test_model_outputs_cannot_be_mislabeled_or_lack_artifact_refs():
    row = SeparatedSignal("id", "dialogue_candidate", "source", "estimated", "method",
                          "model", 44100, np.zeros((1, 4)))
    with pytest.raises(ValueError, match="estimated"):
        SeparatedSignal(**{**row.__dict__, "status": "measured"}).record(samples_ref="x.wav")
    with pytest.raises(ValueError, match="exactly one"):
        records_for_stems({"dialogue_candidate": row}, artifact_refs={})
    with pytest.raises(ValueError, match="project-relative"):
        row.record(samples_ref="../outside.wav")
    with pytest.raises(ValueError, match="project-relative"):
        row.record(samples_ref="/outside.wav")


def test_model_lookup_refuses_missing_checkpoint_without_download(tmp_path):
    with pytest.raises(FileNotFoundError, match="downloads are disabled"):
        cached_model_path(tmp_path)


def test_project_model_signal_carries_estimation_and_lineage_fields():
    signal = SeparatedSignal("src:dialogue_candidate", "dialogue_candidate", "src", "estimated",
                             "demucs-infer/htdemucs", "htdemucs", 44100,
                             np.zeros((2, 8)))
    record = project_model_signal(
        signal, asset_id="asset", clock_id="source-clock", start_tick=0,
        end_tick=8, provenance_id="prov-demucs", samples_ref="audio/derived.wav",
    )
    assert record["source_signal_id"] == "src"
    assert record["estimate_status"] == "estimated"
    assert record["samples_ref"] == "audio/derived.wav"
    assert record["samples"] == []


@pytest.mark.parametrize("reference,estimate", [
    (np.zeros(4), np.ones(4)),
    (np.ones(4), np.ones(3)),
    (np.ones(4), np.array([1, 1, np.nan, 1])),
])
def test_si_sdr_refuses_unmeasurable_inputs(reference, estimate):
    with pytest.raises(ValueError):
        si_sdr(reference, estimate)
