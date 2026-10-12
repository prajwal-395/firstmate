"""FI-01 harness tests: each names the defect it pins.

Every test builds its memory root under ``tmp_path`` and clears
``PIPELINE_SOURCE_MEMORY_ROOT`` so no host or agent environment leaks
in. No test reads a real project (§8), runs a model, or touches
Resolve.
"""

import json
from pathlib import Path

import pytest

from library.tools.analysis import bench_footage_intelligence as bench


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    monkeypatch.delenv("PIPELINE_SOURCE_MEMORY_ROOT", raising=False)
    monkeypatch.delenv("VEP_HEAVY_WORK_OWNER", raising=False)


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _transcript(digest, texts):
    utts = []
    t = 0.0
    for text in texts:
        words = [{"word": w, "start": round(t, 2),
                  "end": round(t + 0.3, 2)}
                 for w in text.split()]
        t += len(words) * 0.4
        utts.append({"start": words[0]["start"], "end": words[-1]["end"],
                     "text": text, "words": words})
        t += 0.5
    return {"content_digest": digest, "status": "transcribed",
            "utterances": utts}


def _memory_root(tmp_path: Path) -> Path:
    """Two digests: A+B share an M6 group, C is a loner with refusals."""
    root = tmp_path / "memory"
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    for digest, name in ((a, "CAM_A"), (b, "CAM_B"), (c, "LONER")):
        _write(root / digest / "source.json",
               {"content_digest": digest, "duration_seconds": 120.0,
                "size_bytes": 1000,
                "observed_paths": [f"/media/{name}.MXF"]})
    _write(root / a / "clock.json",
           {"content_digest": a, "group_id": "group-1"})
    _write(root / b / "clock.json",
           {"content_digest": b, "group_id": "group-1"})
    _write(root / c / "clock.json",
           {"content_digest": c, "group_id": "group-2"})
    shared_texts = ["the quick brown fox jumps over",
                    "the quick red fox leaps high",
                    "unrelated filler words here now"]
    _write(root / a / "transcript.words.json",
           _transcript(a, shared_texts))
    _write(root / b / "transcript.words.json",
           _transcript(b, ["the quick brown fox jumps over",
                           "something entirely different today"]))
    _write(root / c / "transcript.words.json",
           {"content_digest": c, "status": "untranscribed-no-speech",
            "utterances": []})
    events = {"content_digest": a, "status": "built",
              "frame_count": 100, "faces_assigned": 90,
              "faces_unassigned": 10,
              "predicates": {
                  "on_screen": {"spans": [{"face_track": "face_001",
                                           "start": 0.0, "end": 60.0,
                                           "frames": 50}]},
                  "speaking": {"spans": [
                      {"voice_track": "voice_001",
                       "face_track": "face_001",
                       "start": 10.0, "end": 20.0},
                      {"voice_track": "voice_002",
                       "face_track": None,
                       "start": 30.0, "end": 40.0}]}},
              "candidates": {"hand_near_mouth": {"spans": [
                  {"face_track": "face_001", "start": 10.0,
                   "end": 20.0},
                  {"face_track": "face_001", "start": 50.0,
                   "end": 55.0}]}}}
    _write(root / a / "events.json", events)
    _write(root / a / "verdicts.json",
           {"content_digest": a,
            "verdicts": {
                "v1": {"answer": "yes", "frames": [11.0, 12.0],
                       "statement": "covers mouth", "seconds": 2.0},
                "v2": {"answer": "no", "frames": [99.0],
                       "statement": "covers mouth", "seconds": 1.0}}})
    _write(root / a / "identity.json",
           {"content_digest": a, "status": "measured",
            "faces": [{"track_id": "face_001",
                       "embedding": [0.1, 0.2]}],
            "voices": [{"track_id": "voice_001"},
                       {"track_id": "voice_002"}],
            "speech_face_links": [{"voice_track": "voice_001",
                                   "face_track": "face_001"}]})
    _write(root / c / "identity.json",
           {"content_digest": c, "status": "no-faces-detected",
            "faces": [], "voices": []})
    return root


def _manifest_for(root: Path, *digests) -> dict:
    manifest = bench.empty_manifest(head_sha="0" * 40)
    rows = [{"clip_id": d[:8], "digest": d, "duration_seconds": 120.0,
             "size_bytes": 1000, "path": f"/media/{d[:8]}.MXF",
             "m6_group": "group-1"} for d in digests]
    bench.cover_category(manifest, "podcast", rows,
                         reason="fixture")
    bench.cover_category(manifest, "multicam_podcast", rows,
                         reason="fixture")
    return manifest


def test_absent_categories_reported_by_name_not_skipped(tmp_path):
    """Defect: a harness that silently skips the 5.5 missing classes."""
    root = _memory_root(tmp_path)
    manifest = _manifest_for(root, "a" * 64)
    report = bench.run_corpus(manifest, root)
    names = [c["key"] for c in report["absent_categories"]]
    assert set(names) == {"static_classroom", "street", "product",
                          "cinematic_broll", "noisy_audio",
                          "complex_action"}
    assert all(c["reason"] for c in report["absent_categories"])


def test_unmeasured_is_not_measured_absent(tmp_path):
    """Defect: a missing slot file counted as a measured refusal."""
    root = _memory_root(tmp_path)
    digest = "a" * 64
    assert bench.slot_state(
        root / digest, "world_model.json", digest)["state"] == (
        "unmeasured")
    assert bench.slot_state(
        root / digest, "transcript.words.json",
        digest)["state"] == "measured"
    loner = "c" * 64
    assert bench.slot_state(
        root / loner, "transcript.words.json",
        loner)["state"] == "measured-absent"
    assert bench.slot_state(
        root / loner, "identity.json", loner)["state"] == (
        "measured-absent")


def test_no_hardcoded_recall_bar(tmp_path):
    """Defect (C1): a promotion tolerance baked in as a constant.

    The harness reports deltas; only the caller-supplied mechanical
    check judges, so run_corpus carries no verdict and no bar.
    """
    root = _memory_root(tmp_path)
    manifest = _manifest_for(root, "a" * 64, "b" * 64)
    report = bench.run_corpus(manifest, root)
    assert "reproduces" not in report
    assert "promotion" not in json.dumps(report)
    assert report["tolerance"]["rel"] == bench.DEFAULT_REL_TOL


def test_probes_come_from_data_not_constants(tmp_path):
    """Defect: probe queries fixed in code instead of the transcripts."""
    root = _memory_root(tmp_path)
    docs = {d: json.loads((root / d / "transcript.words.json")
                          .read_text(encoding="utf-8"))
            for d in ("a" * 64, "b" * 64)}
    probes = bench.derive_probes(docs, per_source=3)
    assert probes["a" * 64] != probes["b" * 64]
    assert "quick" in probes["a" * 64]


def test_unjudged_spans_not_counted_wrong(tmp_path):
    """Defect: M7 spans no verdict ever judged scored as failures."""
    root = _memory_root(tmp_path)
    manifest = _manifest_for(root, "a" * 64)
    report = bench.run_corpus(manifest, root)
    events = report["fidelity"]["a" * 64]["events"]
    # Two M7 candidate spans; v1 says yes on the first, v2 answers off
    # every span: precision 1/1 over matched verdicts, coverage 1/2.
    assert events["m7_candidate_spans"] == 2
    assert events["m7_precision_vs_m8"] == 1.0
    assert events["m8_judgment_coverage_of_m7"] == pytest.approx(0.5)


def test_orphan_verdicts_counted_not_dropped(tmp_path):
    """Defect: verdicts outside every span vanishing from the report."""
    root = _memory_root(tmp_path)
    manifest = _manifest_for(root, "a" * 64)
    report = bench.run_corpus(manifest, root)
    events = report["fidelity"]["a" * 64]["events"]
    assert events["m8_verdicts"] == 2
    assert events["m8_orphan_verdicts"] == 1


def test_reproducibility_detects_real_change(tmp_path):
    """Defect: silent numeric drift between two measurement runs."""
    same = bench.reproducibility([1.0, 2.0], [1.0, 2.0])
    assert same["max_abs_diff"] == 0.0
    drifted = bench.reproducibility([1.0, 2.0], [1.0, 2.2])
    assert drifted["max_abs_diff"] == pytest.approx(0.2)
    report = {"fidelity": {"d": {"events": {"mean_boundary_error_s":
                                            0.5}}}, "retrieval": {},
              "coverage": {}, "efficiency": {}}
    baseline = {"fidelity": {"d": {"events": {"mean_boundary_error_s":
                                              0.9}}}, "retrieval": {},
                "coverage": {}, "efficiency": {}}
    verdict = bench.check_within_tolerance(report, baseline, 1e-9,
                                           1e-12)
    assert verdict["reproduces"] is False


def test_untestable_probes_not_counted_as_hits(tmp_path):
    """Defect: loner sources flattering cross-camera recall."""
    root = _memory_root(tmp_path)
    manifest = _manifest_for(root, "c" * 64)
    report = bench.run_corpus(manifest, root)
    retrieval = report["retrieval"]
    assert retrieval["cross_camera_recall"] is None
    assert retrieval["untestable_probes"] >= 0
    assert retrieval["testable_probes"] == 0


def test_manifest_rejects_wrong_category_set(tmp_path):
    """Defect: a manifest drifting from the plan §9 eight."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"categories": [{"key": "podcast"}]}),
                   encoding="utf-8")
    with pytest.raises(SystemExit):
        bench._load_manifest(bad)


def test_check_reproduces_within_manifest_tolerance(tmp_path):
    """Acceptance shape: the same root twice reproduces exactly."""
    root = _memory_root(tmp_path)
    manifest = _manifest_for(root, "a" * 64, "b" * 64)
    first = bench.run_corpus(manifest, root)
    second = bench.run_corpus(manifest, root)
    verdict = bench.check_within_tolerance(
        second, first, bench.DEFAULT_REL_TOL, bench.DEFAULT_ABS_TOL)
    assert verdict["reproduces"] is True


def test_cli_names_absent_categories_on_stdout(tmp_path, capsys):
    """Acceptance: --corpus reports each absent category by name."""
    root = _memory_root(tmp_path)
    manifest = _manifest_for(root, "a" * 64)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert bench.main(["--corpus", str(path), "--memory-root",
                       str(root)]) == 0
    out = capsys.readouterr().out
    for key in ("static_classroom", "street", "product",
                "cinematic_broll", "complex_action"):
        assert key in out


def test_no_learned_estimator_imports():
    """Defect (plan §12F): a learned estimator behind a metric."""
    source = Path(bench.__file__).read_text(encoding="utf-8")
    for name in ("sklearn", "torch", "tensorflow", "mlx_lm",
                 "sentence_transformers"):
        assert name not in source


def test_cover_rejects_unknown_category():
    """Defect: a typo'd category silently entering the manifest."""
    manifest = bench.empty_manifest(head_sha="0" * 40)
    with pytest.raises(KeyError):
        bench.cover_category(manifest, "podcast_interviews", [],
                             reason="typo")
