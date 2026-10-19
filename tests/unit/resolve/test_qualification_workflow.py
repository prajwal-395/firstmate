"""The real-footage fixture is index-led, immutable and qualification-only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import qualification_project as qp
from library.tools import qualification_workflow as qw
from library.tools.resolved.jobs import JobRefused


def _index(path, source):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "project_folder": "/read-only/index/project",
        "segments": [{"clip_id": "clip-1", "source_file": str(source)}],
    }), encoding="utf-8")


def test_indexed_source_copy_is_content_addressed_and_does_not_change_source(
        tmp_path, monkeypatch):
    source = tmp_path / "original.mxf"
    original = b"real source fixture bytes"
    source.write_bytes(original)
    index_file = tmp_path / "index" / "footage_index.json"
    _index(index_file, source)
    original_index = index_file.read_bytes()
    monkeypatch.setenv(qp.MEDIA_ROOT_ENV, str(tmp_path / "qualification"))
    monkeypatch.setattr(
        "library.tools.analysis.footage_query.FootageIndex.search_report",
        lambda self, query, **kwargs: {
            "results": [{"clip_id": "clip-1", "start": 1.0, "end": 2.0,
                         "score": 0.8, "dense": 0.7, "text": "private text"}]})
    monkeypatch.setattr(qp.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps({
                            "format": {"duration": "12"},
                            "streams": [{"codec_type": "video"},
                                        {"codec_type": "audio"}]}),
                                         stderr=""))

    copied = qp.copy_indexed_source(index_file, "find the clip")

    target = Path(copied["qualification_copy"])
    assert target.read_bytes() == original
    assert copied["sha256"] == hashlib.sha256(original).hexdigest()
    assert copied["duration_seconds"] == 12
    assert copied["search_hit"] == {"clip_id": "clip-1", "start": 1.0,
                                    "end": 2.0, "score": 0.8, "dense": 0.7}
    assert source.read_bytes() == original
    assert index_file.read_bytes() == original_index


def test_indexed_source_copy_refuses_overlong_media_without_copying(
        tmp_path, monkeypatch):
    source = tmp_path / "long.mxf"
    source.write_bytes(b"unchanged")
    index_file = tmp_path / "index" / "footage_index.json"
    _index(index_file, source)
    monkeypatch.setenv(qp.MEDIA_ROOT_ENV, str(tmp_path / "qualification"))
    monkeypatch.setattr(
        "library.tools.analysis.footage_query.FootageIndex.search_report",
        lambda self, query, **kwargs: {
            "results": [{"clip_id": "clip-1", "start": 1.0, "end": 2.0}]})
    monkeypatch.setattr(qp.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps({
                            "format": {"duration": "301"},
                            "streams": [{"codec_type": "video"},
                                        {"codec_type": "audio"}]}),
                                         stderr=""))

    with pytest.raises(qp.QualificationError, match="no readable source"):
        qp.copy_indexed_source(index_file, "find the clip")
    assert source.read_bytes() == b"unchanged"
    assert not qp.real_media_dir().exists()


def test_broker_workflow_job_is_confined_to_copied_qualification_media(
        tmp_path, monkeypatch):
    monkeypatch.setenv(qp.MEDIA_ROOT_ENV, str(tmp_path / "qualification"))
    source = qp.real_media_dir() / "source.mxf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture")
    output_dir = qp.media_dir() / "workflow-runs" / "run-1" / "exports"
    output_dir.mkdir(parents=True)
    lifecycle_project = output_dir.parent / "analysis-project"
    lifecycle_project.mkdir()
    run_stamp = "20261019T120000123456Z"
    params = {"project": "Ren Qualification", "source_copy": str(source),
              "timeline": f"Q Workflow {run_stamp} 1",
              "run_stamp": run_stamp, "source_start": 0.0,
              "source_end": 15.0, "output_dir": str(output_dir),
              "output_name": "Q_Workflow_Test", "timeout_seconds": 600,
              "source_sha256": hashlib.sha256(b"fixture").hexdigest(),
              "lifecycle_project_folder": str(lifecycle_project),
              "lifecycle_run_id": "run-1"}

    shape = qw.prepare_broker_job(params)

    assert shape["project"] == "Ren Qualification"
    assert shape["qualification"] is True
    assert shape["mode"] == "exclusive"

    with pytest.raises(JobRefused, match="only accepts 'Ren Qualification'"):
        qw.prepare_broker_job({**params, "project": "Captain Project"})
    with pytest.raises(JobRefused, match="must stay under"):
        qw.prepare_broker_job({**params, "output_dir": str(tmp_path)})
    with pytest.raises(JobRefused, match="at most 45 seconds"):
        qw.prepare_broker_job({**params, "source_end": 46.0})
    with pytest.raises(JobRefused, match="SHA-256 hex digest"):
        qw.prepare_broker_job({**params, "source_sha256": "z" * 64})


def test_successful_cleanup_preserves_evidence_and_shared_reusable_files(
        tmp_path, monkeypatch):
    monkeypatch.setenv(qp.MEDIA_ROOT_ENV, str(tmp_path / "qualification"))
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(tmp_path / "locks"))
    run_stamp = "20261019T120000123456Z"
    root = qp.media_dir() / "workflow-runs" / run_stamp
    project_file = root / "run-1" / "projects" / "analysis" / "pipeline_data.json"
    export_file = root / "exports" / "render.mp4"
    report = root / "qualification-report.json"
    perf = root / "perf_ledger.jsonl"
    shared_source = qp.real_media_dir() / "source.mxf"
    semantic = qp.media_dir() / "semantic-measurements" / "measurement.json"
    for path in (project_file, export_file, report, perf, shared_source, semantic):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("owned fixture", encoding="utf-8")
    qw._register_shared_artifact(root, run_stamp, shared_source,
                                 kind="qualification_source_copy",
                                 dependency="source-hash")
    qw._register_shared_artifact(root, run_stamp, semantic,
                                 kind="semantic_measurement",
                                 dependency="shared-cache")

    result = qw._retire_successful_run_files(root, run_stamp)

    assert result["status"] == "complete"
    assert not project_file.exists()
    assert not export_file.exists()
    assert report.is_file() and perf.is_file()
    assert shared_source.is_file() and semantic.is_file()


def test_qualification_timeline_authority_requires_exact_run_identity():
    from library.tools.proof_cleanup import (
        ProofRemovalRefused,
        qualification_workflow_timeline_authority,
    )

    run_stamp = "20261019T120000123456Z"
    row = qualification_workflow_timeline_authority(
        "Ren Qualification", f"Q Workflow {run_stamp} 1", "timeline-uid",
        run_stamp)

    assert row["item_id"] == "timeline-uid"
    with pytest.raises(ProofRemovalRefused, match="not an exact timeline"):
        qualification_workflow_timeline_authority(
            "Ren Qualification", f"Q Workflow {run_stamp} 1 copy",
            "timeline-uid", run_stamp)


def test_workflow_analysis_runs_the_full_composition_unskipped(
        tmp_path, monkeypatch):
    copy_path = tmp_path / "qualification" / "real_footage" / "source.mxf"
    copy_path.parent.mkdir(parents=True)
    copy_path.write_bytes(b"fixture")
    run_dir = tmp_path / "run"
    project = run_dir / "projects" / "analysis"
    index = project / "pipeline_output/scratch/footage_index/footage_index.json"
    index.parent.mkdir(parents=True)
    index.write_text("{}", encoding="utf-8")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "status": "complete", "project_folder": str(project)}),
            stderr="")

    monkeypatch.setattr(qw.subprocess, "run", fake_run)

    result = qw._run_analysis(copy_path, run_dir, "test-run")

    assert result["index_file"] == index
    assert "--skip" not in seen["command"]
    assert seen["command"][-1] == "--json"
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["env"]["PIPELINE_SEMANTIC_MEASUREMENT_ROOT"] == str(
        run_dir.parents[1] / "semantic-measurements")
    assert result["evidence"]["command"][1] == "analyze"


def test_workflow_requires_ledger_capture_before_copying_source(
        tmp_path, monkeypatch):
    monkeypatch.setattr(qw.qp, "media_dir", lambda: tmp_path)
    seen = {}

    def fail_ledger(*_args, **kwargs):
        seen["required"] = kwargs.get("required")
        raise OSError("ledger path is unavailable")

    monkeypatch.setattr(qw.perf_ledger, "begin", fail_ledger)

    with pytest.raises(OSError, match="ledger path is unavailable"):
        qw.run("unused-index")

    assert seen == {"required": True}
    assert list(tmp_path.glob("workflow-runs/*/perf_ledger.jsonl")) == []


class _ShapeProject:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.set_calls = []

    def GetSetting(self, key):
        return self.settings.get(key)

    def SetSetting(self, key, value):
        self.set_calls.append((key, value))
        return True


class TestTimelineShape:
    """The workflow never mutates project settings under the
    Qualification project's existing timelines."""

    def test_project_standard_is_read_and_never_written(self):
        """Defect: the job called project.SetSetting for the timeline
        rate, which Resolve refuses on a project with timelines."""
        project = _ShapeProject({"timelineFrameRate": "24",
                                 "timelineResolutionWidth": "1920",
                                 "timelineResolutionHeight": "1080"})

        standard = qw._check_project_standard(project)

        assert standard == {"timelineFrameRate": 24.0,
                            "timelineResolutionWidth": 1920,
                            "timelineResolutionHeight": 1080}
        assert project.set_calls == []

    def test_drifted_project_standard_refuses_with_reset_hint(self):
        """Defect: a drifted project standard silently carried the wrong
        rate into the export."""
        project = _ShapeProject({"timelineFrameRate": "23.976",
                                 "timelineResolutionWidth": "1920",
                                 "timelineResolutionHeight": "1080"})

        with pytest.raises(qw.WorkflowQualificationError,
                           match="ren qualification reset"):
            qw._check_project_standard(project)

    def test_timeline_shape_is_custom_with_readback(self):
        """Defect: a custom timeline rate was written and refused while
        the timeline kept the project rate, so the cut rides the
        inherited 24fps project standard instead."""
        timeline = _ShapeProject({"timelineFrameRate": "24",
                                  "timelineResolutionWidth": "1920",
                                  "timelineResolutionHeight": "1080"})

        shape = qw._check_timeline_inherits_standard(timeline)

        assert shape == {"timelineFrameRate": 24.0,
                         "timelineResolutionWidth": 1920,
                         "timelineResolutionHeight": 1080}
        assert timeline.set_calls == []

    def test_timeline_that_did_not_inherit_the_standard_refuses(self):
        """Defect: a drifted new timeline silently carried the wrong rate
        into the export."""
        timeline = _ShapeProject({"timelineFrameRate": "25",
                                  "timelineResolutionWidth": "1920",
                                  "timelineResolutionHeight": "1080"})

        with pytest.raises(qw.WorkflowQualificationError,
                           match="did not inherit"):
            qw._check_timeline_inherits_standard(timeline)

    def test_timeline_writes_come_after_the_timeline_is_current(self):
        """Defect: writes on a not-current timeline land nowhere; the
        render path asserts current first."""
        import ast
        from pathlib import Path

        tree = ast.parse(Path(qw.__file__).read_text(encoding="utf-8"))
        body = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "_run_broker_job")
        ordered = sorted(
            (node for node in ast.walk(body)
             if isinstance(node, ast.Call)
             and isinstance(node.func, (ast.Attribute, ast.Name))),
            key=lambda node: (node.lineno, node.col_offset))
        calls = [node.func.attr if isinstance(node.func, ast.Attribute)
                 else node.func.id for node in ordered]
        first = {}
        for position, name in enumerate(calls):
            first.setdefault(name, position)
        for write in ("AddTrack", "SetTrackName"):
            assert first["assert_current_timeline"] < first[write], (
                f"{write} runs before the timeline is current")


class TestProbeSourceFrames:
    """Frame math comes from ffprobe on the hashed copy, never from the
    pool item's scripting methods."""

    def _run(self, monkeypatch, stdout, returncode=0):
        from types import SimpleNamespace

        monkeypatch.setattr(
            qw.subprocess, "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=returncode, stdout=stdout, stderr=""))

    def test_fractional_rate_and_total_frames(self, tmp_path, monkeypatch):
        """Defect: the MXF essence item exposes no GetLeftOffset, so the
        broker job died calling it; ffprobe reads the same file."""
        self._run(monkeypatch, json.dumps({
            "format": {"duration": "214.547667"},
            "streams": [{"codec_type": "video",
                         "avg_frame_rate": "24000/1001"},
                        {"codec_type": "audio",
                         "avg_frame_rate": "0/0"}]}))

        fps, total = qw._probe_source_frames(tmp_path / "copy.MXF")

        assert abs(fps - 24000 / 1001) < 1e-9
        assert total == round(214.547667 * 24000 / 1001)

    def test_failed_probe_refuses(self, tmp_path, monkeypatch):
        """Defect: an unreadable copy proceeded with a guessed rate."""
        self._run(monkeypatch, "", returncode=1)

        with pytest.raises(qw.WorkflowQualificationError,
                           match="ffprobe could not read"):
            qw._probe_source_frames(tmp_path / "copy.MXF")

    def test_probe_without_a_video_stream_refuses(self, tmp_path, monkeypatch):
        """Defect: an audio-only probe produced a zero rate and an
        empty source span."""
        self._run(monkeypatch, json.dumps({
            "format": {"duration": "10"},
            "streams": [{"codec_type": "audio",
                         "avg_frame_rate": "0/0"}]}))

        with pytest.raises(qw.WorkflowQualificationError,
                           match="no usable rate"):
            qw._probe_source_frames(tmp_path / "copy.MXF")


class TestExportSeamBinding:
    """The broker calls the render seam exactly as it declares."""

    def test_render_seam_accepts_the_broker_resolve_handle(self, monkeypatch):
        """Defect: the job passed resolve_override= to a seam taking
        resolve=, so every export died before rendering."""
        from library.tools import resolve_lock
        from library.tools.execution import resolve_render

        monkeypatch.setattr(resolve_lock, "exclusive_held", lambda: False)
        with pytest.raises(resolve_render.RenderError,
                           match="exclusive Resolve lease"):
            resolve_render.render_timeline_under_existing_lease(
                timeline_name="Q Workflow Test",
                output_dir="exports",
                output_name="Q_Workflow_Test",
                resolve=object())
