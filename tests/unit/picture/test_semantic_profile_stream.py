import importlib.util
import json
import os
from pathlib import Path

import pytest

from library.tools import semantic_profile_stream as stream
from library.tools.project_layout import Area, ProjectLayout
from library.tools.vision_schema_adapter import adapt_semantic_document

REPO_ROOT = Path(__file__).resolve().parents[3]
_STEP_PATH = (REPO_ROOT / "library" / "steps"
              / "step_1_03_semantic_analysis" / "step.py")
_SPEC = importlib.util.spec_from_file_location(
    "semantic_step_stream_test", _STEP_PATH)
semantic_step = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(semantic_step)


def _profile(source_path):
    return {
        "analysis_metadata": {"pipeline_version": "3"},
        "file_path": str(source_path),
        "scene": [],
        "camera": [],
        "actions": [],
        "objects": [],
        "assessment": {},
    }


def _write_profile(project, source_path, profile):
    analysis_dir = ProjectLayout(str(project)).write_dir(
        Area.VISION_ANALYSIS, step="semantic_analysis")
    path = analysis_dir / f"clip_profile_{Path(source_path).stem}_v3.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path


def test_semantic_aggregate_stays_unchanged_while_publishing_catalog_record(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "raw" / "cam" / "take.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    profile = _profile(source)
    _write_profile(project, source, profile)
    monkeypatch.setattr(
        semantic_step.code_identity, "current_code_hash",
        lambda _step_dir: "semantic-method")
    monkeypatch.setattr(
        semantic_step.code_identity, "read_code_stamp",
        lambda _analysis_dir: "semantic-method")

    result = semantic_step.analyse_semantics(
        [{"path": str(source), "clip_id": "clip_001"}],
        project_folder=str(project),
        clip_catalog=[{"clip_id": "clip_001", "path": str(source)}],
        stream_run_id="run-test",
    )

    expected_document = adapt_semantic_document(profile)
    expected_document["clip_id"] = "take_v3"
    assert result == {
        "semantic_analysis_documents": [expected_document],
        "total_clips_analyzed": 1,
    }
    record = stream.read_record(
        str(project), clip_id="clip_001", source_path=str(source),
        run_id="run-test")
    assert record["profile"] == expected_document
    assert record["schema_version"] == stream.SCHEMA_VERSION


def test_semantic_profile_is_visible_before_the_next_clip_finishes(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    sources = [project / "raw" / "a.mov", project / "raw" / "b.mov"]
    sources[0].parent.mkdir(parents=True)
    for source in sources:
        source.write_bytes(b"source")
    monkeypatch.setattr(
        semantic_step.code_identity, "current_code_hash",
        lambda _step_dir: "semantic-method")
    monkeypatch.setattr(
        semantic_step.code_identity, "read_code_stamp",
        lambda _analysis_dir: "semantic-method")

    def fake_analyse_missing(_paths, _cmd, analysis_dir, on_profile=None,
                             **_kwargs):
        first = _write_profile(project, sources[0], _profile(sources[0]))
        assert first.parent == Path(analysis_dir)
        on_profile()
        ready = stream.read_record(
            str(project), clip_id="clip_001", source_path=str(sources[0]),
            run_id="run-stream")
        assert ready is not None
        assert ready["profile"]["file_path"] == str(sources[0])

        _write_profile(project, sources[1], _profile(sources[1]))
        on_profile()

    monkeypatch.setattr(semantic_step, "_analyse_missing", fake_analyse_missing)
    result = semantic_step.analyse_semantics(
        [{"path": str(source), "clip_id": f"clip_{i:03d}"}
         for i, source in enumerate(sources, start=1)],
        project_folder=str(project),
        clip_catalog=[
            {"clip_id": f"clip_{i:03d}", "path": str(source)}
            for i, source in enumerate(sources, start=1)
        ],
        stream_run_id="run-stream",
    )

    assert result["total_clips_analyzed"] == 2
    assert stream.read_record(
        str(project), clip_id="clip_002", source_path=str(sources[1]),
        run_id="run-stream") is not None


def test_consumer_ignores_staged_record_until_atomic_publish(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "raw" / "take.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    layout = ProjectLayout(str(project))
    stream_dir = layout.write_path(
        Area.VISION_ANALYSIS, "temporal_profile_stream_v1", "probe.json",
        step="semantic_analysis").parent
    (stream_dir / ".clip_001.json.partial.tmp").write_text(
        '{"schema_version":', encoding="utf-8")
    assert stream.read_record(
        str(project), clip_id="clip_001", source_path=str(source)) is None

    replace = os.replace

    def inspect_then_replace(staged, target):
        assert stream.read_record(
            str(project), clip_id="clip_001", source_path=str(source)) is None
        replace(staged, target)

    monkeypatch.setattr(os, "replace", inspect_then_replace)
    stream.publish_record(
        str(project), clip_id="clip_001", source_path=str(source),
        source_stem="take", profile={"actions": []}, run_id="run-test",
        producer_code_hash="method")

    record = stream.read_record(
        str(project), clip_id="clip_001", source_path=str(source),
        run_id="run-test")
    assert record["profile"] == {"actions": []}


def test_failed_clip_publish_retries_without_changing_other_clip(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    source_a = project / "raw" / "a.mov"
    source_b = project / "raw" / "b.mov"
    source_a.parent.mkdir(parents=True)
    source_a.write_bytes(b"a")
    source_b.write_bytes(b"b")

    first = stream.publish_record(
        str(project), clip_id="clip_001", source_path=str(source_a),
        source_stem="a", profile={"actions": ["first"]}, run_id="run-test",
        producer_code_hash="method")
    first_bytes = first.read_bytes()
    replace = os.replace

    def fail_second_clip(staged, target):
        if Path(target).name == "clip_002.json":
            raise OSError("simulated per-clip disk failure")
        replace(staged, target)

    monkeypatch.setattr(os, "replace", fail_second_clip)
    with pytest.raises(OSError, match="simulated per-clip"):
        stream.publish_record(
            str(project), clip_id="clip_002", source_path=str(source_b),
            source_stem="b", profile={"actions": ["second"]},
            run_id="run-test", producer_code_hash="method")
    assert first.read_bytes() == first_bytes
    assert stream.read_record(
        str(project), clip_id="clip_002", source_path=str(source_b)) is None

    monkeypatch.setattr(os, "replace", replace)
    stream.publish_record(
        str(project), clip_id="clip_002", source_path=str(source_b),
        source_stem="b", profile={"actions": ["second"]}, run_id="run-test",
        producer_code_hash="method")
    assert stream.read_record(
        str(project), clip_id="clip_001", source_path=str(source_a)
    )["profile"] == {"actions": ["first"]}
    assert stream.read_record(
        str(project), clip_id="clip_002", source_path=str(source_b)
    )["profile"] == {"actions": ["second"]}


def test_failed_temporal_index_write_preserves_cache_and_retries(
        tmp_path, monkeypatch):
    from library.steps.step_1_04_temporal_index import step as temporal_step

    first = tmp_path / "clip_001.json"
    failed = tmp_path / "clip_002.json"
    temporal_step._atomic_json_write(first, {"clip_id": "clip_001"})
    temporal_step._atomic_json_write(failed, {"clip_id": "clip_002"})
    first_bytes = first.read_bytes()
    failed_bytes = failed.read_bytes()
    replace = os.replace

    def fail_one_clip(staged, target):
        if Path(target) == failed:
            raise OSError("simulated per-clip index write failure")
        replace(staged, target)

    monkeypatch.setattr(os, "replace", fail_one_clip)
    with pytest.raises(OSError, match="per-clip index write"):
        temporal_step._atomic_json_write(
            failed, {"clip_id": "clip_002", "retry": True})
    assert first.read_bytes() == first_bytes
    assert failed.read_bytes() == failed_bytes

    monkeypatch.setattr(os, "replace", replace)
    temporal_step._atomic_json_write(
        failed, {"clip_id": "clip_002", "retry": True})
    assert json.loads(first.read_text(encoding="utf-8")) == {
        "clip_id": "clip_001"}
    assert json.loads(failed.read_text(encoding="utf-8")) == {
        "clip_id": "clip_002", "retry": True}


def test_profile_join_uses_exact_path_and_refuses_ambiguous_stem(tmp_path):
    source_a = tmp_path / "one" / "scene.mov"
    source_b = tmp_path / "two" / "scene.mov"
    catalog = [
        {"clip_id": "clip_001", "path": str(source_a)},
        {"clip_id": "clip_002", "path": str(source_b)},
    ]
    source_index = stream.catalog_source_index(catalog)

    assert stream.clip_for_profile(
        {"file_path": str(source_b)}, "clip_profile_scene_v3.json",
        source_index) == ("clip_002", os.path.realpath(source_b))
    assert stream.clip_for_profile(
        {"file_path": str(tmp_path / "elsewhere" / "scene.mov")},
        "clip_profile_scene_v3.json", source_index) is None
    with pytest.raises(ValueError, match="matches multiple clip ids"):
        stream.clip_for_profile(
            {}, "clip_profile_scene_v3.json", source_index)


def test_missing_profile_wait_ends_when_coordinator_fails_producer(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "raw" / "take.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    states = iter((
        {"run_id": "run-test", "status": "pending"},
        {"run_id": "run-test", "status": "failed"},
    ))
    assert stream.wait_for_record(
        str(project), clip_id="clip_001", source_path=str(source),
        run_id="run-test", status_reader=lambda _project: next(states),
        sleep=lambda _seconds: None) is None
