import os
import json
import pytest
from unittest.mock import patch, MagicMock
import sys

from library.tools.reel_build import rebuild_reels_in_project

@pytest.fixture(autouse=True)
def mock_dvr():
    with patch.dict("sys.modules", {"DaVinciResolveScript": MagicMock()}):
        yield

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
    
    mock_proj = MagicMock()
    mock_proj.GetName.return_value = "Mock Project"
    
    mock_timeline = MagicMock()
    mock_timeline.GetName.return_value = "GEO Podcast - Synced"
    mock_proj.GetTimelineCount.return_value = 1
    mock_proj.GetTimelineByIndex.return_value = mock_timeline
    mock_resolve_proj.return_value = mock_proj
    
    mock_run_verif.return_value = 1
    
    json_path = mock_project_env / "pipeline_output" / "review" / "conformance_report.json"
    json_path.write_text(json.dumps({
        "has_errors": True,
        "findings": [{"severity": "error", "finding_class": "F1", "message": "Hole found"}]
    }))
    
    with pytest.raises(RuntimeError, match="Reel build produced a defective timeline") as exc_info:
        rebuild_reels_in_project(str(mock_project_env))
    assert "Hole found" in str(exc_info.value)
    assert "F1" in str(exc_info.value)

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

    mock_proj = MagicMock()
    mock_proj.GetName.return_value = "Mock Project"
    mock_timeline = MagicMock()
    mock_timeline.GetName.return_value = "GEO Podcast - Synced"
    mock_proj.GetTimelineCount.return_value = 1
    mock_proj.GetTimelineByIndex.return_value = mock_timeline
    mock_resolve_proj.return_value = mock_proj
    
    def side_effect(**kwargs):
        report_path = kwargs.get("json_path")
        if report_path:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump({"has_errors": False, "findings": []}, f)
        return 0

    mock_run_verif.side_effect = side_effect
    
    rebuild_reels_in_project(str(mock_project_env))
    mock_build.assert_called_once()
    mock_run_verif.assert_called_once()
    
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
    
    mock_proj = MagicMock()
    mock_proj.GetName.return_value = "Mock Project"
    mock_timeline = MagicMock()
    mock_timeline.GetName.return_value = "GEO Podcast - Synced"
    mock_proj.GetTimelineCount.return_value = 1
    mock_proj.GetTimelineByIndex.return_value = mock_timeline
    mock_resolve_proj.return_value = mock_proj
    
    original_import = __import__
    def mock_import(name, *args, **kwargs):
        if name == "library.tools.reel_conformance_verifier":
            raise ImportError("Mocked import error")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with pytest.raises(RuntimeError, match="Reel conformance verifier is unavailable"):
            rebuild_reels_in_project(str(mock_project_env))

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
    
    mock_proj = MagicMock()
    mock_proj.GetName.return_value = "Mock Project"
    mock_timeline = MagicMock()
    mock_timeline.GetName.return_value = "GEO Podcast - Synced"
    mock_proj.GetTimelineCount.return_value = 1
    mock_proj.GetTimelineByIndex.return_value = mock_timeline
    mock_resolve_proj.return_value = mock_proj
    
    # run_verification returns 0 if there are only warnings
    mock_run_verif.return_value = 0
    
    rebuild_reels_in_project(str(mock_project_env))
    mock_run_verif.assert_called_once()

@patch("library.tools.reel_subtitles.reel_captions")
@patch("library.tools.reel_build.build_reel_timeline")
@patch("library.tools.resolve_locale.scriptapp_preserving_locale")
@patch("library.tools.reel_build.resolve_project_exactly")
@patch("library.tools.reel_proposal.read_proposal")
@patch("library.tools.timeline_ingest.snapshot_timeline")
@patch("library.tools.subtitle_style.resolve_subtitle_style")
@patch("library.tools.reel_conformance_verifier.run_verification")
def test_rebuild_reels_skip_captions(mock_run_verif, mock_resolve_style, mock_snapshot, mock_read_prop, mock_resolve_proj, mock_scriptapp, mock_build, mock_reel_captions, mock_project_env):
    """Proves that passing skip_captions skips caption generation and passes None to build_reel_timeline."""
    moment = MagicMock()
    moment.approval = "approved"
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    mock_read_prop.return_value = [moment]

    mock_proj = MagicMock()
    mock_proj.GetName.return_value = "Mock Project"
    mock_timeline = MagicMock()
    mock_timeline.GetName.return_value = "GEO Podcast - Synced"
    mock_proj.GetTimelineCount.return_value = 1
    mock_proj.GetTimelineByIndex.return_value = mock_timeline
    mock_resolve_proj.return_value = mock_proj
    
    mock_run_verif.return_value = 0
    
    rebuild_reels_in_project(str(mock_project_env), skip_captions=True)
    
    mock_reel_captions.assert_not_called()
    mock_build.assert_called_once()
    assert mock_build.call_args[1]["captions"] is None

