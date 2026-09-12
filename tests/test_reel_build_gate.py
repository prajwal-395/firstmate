import os
import json
import pytest
from unittest.mock import patch, MagicMock
import sys

from library.tools.reel_build import rebuild_reels_in_project

MASTER = "GEO Podcast - Synced"

# These tests drive the builder against a STAND-IN Resolve project, which
# has no media pool to file. Organising is exercised where it can be:
# `tests/test_organise_media_pool.py` against a pool double that answers
# the way the API was measured to, and on the captain's own project.
ORGANISE = False

@pytest.fixture(autouse=True)
def mock_dvr():
    with patch.dict("sys.modules", {"DaVinciResolveScript": MagicMock()}):
        yield


class _StagingTimeline:
    """A timeline whose name really changes when renamed."""

    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True


class _ResolveProject:
    """A Resolve project whose pool really creates, renames and deletes."""

    def __init__(self, names):
        self.timelines = [_StagingTimeline(name) for name in names]
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.CreateEmptyTimeline.side_effect = self._create
        self._pool = pool

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

    def _create(self, name):
        timeline = _StagingTimeline(name)
        self.timelines.append(timeline)
        return timeline

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]


def _placing(resolve_project):
    """The placer mock, creating the container it was asked for.

    The real placer creates the timeline first and fills it after, so
    "what exists afterwards" is honest at the container level while
    every placed item stays mocked away.
    """
    def _place(**kwargs):
        name = kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        # The placer returns its build record now (the track
        # plan the timeline was placed from); the container
        # the mock creates is the half these tests grade.
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}
    return _place

@pytest.fixture
def mock_project_env(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    
    # project.yaml
    (project_dir / "project.yaml").write_text('resolve: {project_name: "Mock Project", timeline_name: "GEO Podcast - Synced"}')
    
    # pipeline_output/review/reel_proposals_v2.json
    review_dir = project_dir / "pipeline_output" / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "reel_proposals_v2.json").write_text("[]")
    
    # pipeline_output/scratch/timeline_transcript/transcript.json
    scratch_dir = project_dir / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "transcript.json").write_text("{}")
    
    return project_dir

@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
@patch("library.tools.reel_conformance_verifier.run_verification")
def test_rebuild_reels_defective_build_fails(mock_run_verif, mock_resolve_style, mock_snapshot, mock_read_prop, mock_resolve_proj, mock_scriptapp, mock_build, mock_project_env):
    """Proves that when run_verification finds defects, the reel builder fails loudly."""
    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]
    
    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    
    mock_run_verif.return_value = 1
    
    json_path = mock_project_env / "pipeline_output" / "review" / "conformance_report.json"
    json_path.write_text(json.dumps({
        "has_errors": True,
        "findings": [{"severity": "error", "finding_class": "F1", "message": "Hole found"}]
    }))
    
    with pytest.raises(RuntimeError, match="Reel build produced a defective timeline") as exc_info:
        rebuild_reels_in_project(str(mock_project_env),
                             organise=ORGANISE)
    assert "Hole found" in str(exc_info.value)
    assert "F1" in str(exc_info.value)
    # The refused staging is removed again: a failing build leaves the
    # project exactly as it found it, not one debris container up.
    assert mock_proj.names() == [MASTER]

@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
@patch("library.tools.reel_conformance_verifier.run_verification")
def test_rebuild_reels_clean_build_passes(mock_run_verif, mock_resolve_style, mock_snapshot, mock_read_prop, mock_resolve_proj, mock_scriptapp, mock_build, mock_project_env):
    """Proves that a clean build passes and writes the conformance report."""
    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]

    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    
    def side_effect(**kwargs):
        report_path = kwargs.get("json_path")
        if report_path:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump({"has_errors": False, "findings": []}, f)
        return 0

    mock_run_verif.side_effect = side_effect
    
    rebuild_reels_in_project(str(mock_project_env),
                             organise=ORGANISE)
    mock_build.assert_called_once()
    # Two verifications: the refusing gate scoped to what was placed,
    # then the informational whole-project sweep beside it.
    assert mock_run_verif.call_count == 2
    assert mock_run_verif.call_args_list[0][1]["only_reels"] == [
        "Reel 01 (rebuild staging)"]
    assert mock_run_verif.call_args_list[1][1]["only_reels"] is None
    # Promoted: the staging container now carries the final name and no
    # staging container is left behind.
    assert mock_proj.names() == [MASTER, "Reel 01"]
    
    report_file = mock_project_env / "pipeline_output" / "review" / "conformance_report.json"
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_data["has_errors"] is False

@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
def test_verifier_unavailable_fails(mock_resolve_style, mock_snapshot, mock_read_prop, mock_resolve_proj, mock_scriptapp, mock_build, mock_project_env):
    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]
    
    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    
    original_import = __import__
    def mock_import(name, *args, **kwargs):
        if name == "library.tools.reel_conformance_verifier":
            raise ImportError("Mocked import error")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with pytest.raises(RuntimeError, match="Reel conformance verifier is unavailable"):
            rebuild_reels_in_project(str(mock_project_env),
                             organise=ORGANISE)

@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
@patch("library.tools.reel_conformance_verifier.run_verification")
def test_rebuild_reels_non_defect_classes_do_not_fail(mock_run_verif, mock_resolve_style, mock_snapshot, mock_read_prop, mock_resolve_proj, mock_scriptapp, mock_build, mock_project_env):
    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]
    
    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    
    # run_verification returns 0 if there are only warnings
    mock_run_verif.return_value = 0
    
    rebuild_reels_in_project(str(mock_project_env),
                             organise=ORGANISE)
    assert mock_run_verif.call_count == 2
    assert mock_run_verif.call_args_list[1][1]["only_reels"] is None

@patch("library.tools.reel_build.reel_subtitle_segments")
@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
@patch("library.tools.reel_conformance_verifier.run_verification")
def test_rebuild_reels_skip_captions(mock_run_verif, mock_resolve_style, mock_snapshot, mock_read_prop, mock_resolve_proj, mock_scriptapp, mock_build, mock_reel_segments, mock_project_env):
    """skip_captions skips caption generation entirely.

    It used to assert `captions is None` reached build_reel_timeline -
    which was true whether or not captions had been generated, because
    the caller passed None unconditionally and threw the computed
    captions away. It now asserts the pipeline caption path is not
    entered at all, and that None is what the builder is given.
    """
    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]

    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    
    mock_run_verif.return_value = 0
    
    rebuild_reels_in_project(str(mock_project_env),
                             skip_captions=True,
                             organise=ORGANISE)
    
    mock_reel_segments.assert_not_called()
    mock_build.assert_called_once()
    assert mock_build.call_args[1]["subtitle_segments"] is None


@patch("library.tools.project_registry.get_project")
@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
@patch("library.tools.reel_conformance_verifier.run_verification")
def test_rebuild_reels_by_slug_resolves_the_project_folder(
        mock_run_verif, mock_resolve_style, mock_snapshot, mock_read_prop,
        mock_resolve_proj, mock_scriptapp, mock_build, mock_get_project,
        mock_project_env):
    """A slug resolves through `ProjectConfig.project_root` (issue #895).

    The slug branch read `proj.root`, which `ProjectConfig` never had -
    wrong since #507 - so any slug call raised `AttributeError` while
    every caller and test passed an absolute folder and never touched
    the branch.  If this fails, the branch is reaching past the config's
    real attribute again.
    """
    from types import SimpleNamespace
    mock_get_project.return_value = SimpleNamespace(
        project_root=mock_project_env)

    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]

    mock_proj = _ResolveProject([MASTER])
    mock_resolve_proj.return_value = mock_proj
    mock_build.side_effect = _placing(mock_proj)
    mock_run_verif.return_value = 0

    rebuild_reels_in_project("some-slug", organise=ORGANISE)

    mock_get_project.assert_called_once_with("some-slug")
    mock_build.assert_called_once()
    assert mock_proj.names() == [MASTER, "Reel 01"]


def test_rebuild_reels_unknown_slug_is_refused():
    """An unknown slug raises `ValueError`, not `AttributeError`."""
    from unittest.mock import patch as _patch
    with _patch("library.tools.project_registry.get_project",
                return_value=None):
        with pytest.raises(ValueError, match="Unknown project"):
            rebuild_reels_in_project("no-such-slug", organise=ORGANISE)

