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
    params = {"project": "Ren Qualification", "source_copy": str(source),
              "timeline": "Q Workflow Test", "source_start": 0.0,
              "source_end": 15.0, "output_dir": str(output_dir),
              "output_name": "Q_Workflow_Test", "timeout_seconds": 600,
              "source_sha256": hashlib.sha256(b"fixture").hexdigest()}

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


def test_workflow_analysis_selects_only_supported_analysis_capabilities(
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
    assert seen["command"][-4:] == ["--skip", "temporal.index",
                                    "--skip", "prosody.analyse"]
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert result["evidence"]["command"][1] == "analyze"


class _ShapeProject:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.set_calls = []

    def GetSetting(self, key):
        return self.settings.get(key)

    def SetSetting(self, key, value):
        self.set_calls.append((key, value))
        return True


class _ShapeTimeline(_ShapeProject):
    def __init__(self, settings, *, refuse=(), freeze=()):
        super().__init__(settings)
        self.refuse = set(refuse)
        self.freeze = set(freeze)

    def SetSetting(self, key, value):
        self.set_calls.append((key, value))
        if key in self.refuse:
            return False
        if key not in self.freeze:
            self.settings[key] = value
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
        """Defect: the timeline inherited the project rate instead of
        carrying the media rate it was cut at."""
        timeline = _ShapeTimeline({"timelineFrameRate": "24"})

        applied = qw._apply_timeline_shape(timeline, fps=24000 / 1001)

        assert abs(float(applied["timelineFrameRate"]) - 24000 / 1001) <= 0.0001
        assert applied["timelineResolutionWidth"] == "1920"
        assert timeline.settings["useCustomSettings"] == "1"

    def test_timeline_refusing_custom_settings_fails(self):
        """Defect: a timeline that refused custom settings kept project
        settings and failed later at render QA."""
        timeline = _ShapeTimeline({}, refuse=("useCustomSettings",))

        with pytest.raises(qw.WorkflowQualificationError,
                           match="refused custom settings"):
            qw._apply_timeline_shape(timeline, fps=24.0)

    def test_timeline_ignoring_the_write_fails_by_name(self):
        """Defect: SetSetting returning True is a claim; only the
        GetSetting readback decides."""
        timeline = _ShapeTimeline({"timelineFrameRate": "24"},
                                  freeze=("timelineFrameRate",))

        with pytest.raises(qw.WorkflowQualificationError,
                           match="timelineFrameRate"):
            qw._apply_timeline_shape(timeline, fps=24000 / 1001)


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
