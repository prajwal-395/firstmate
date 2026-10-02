"""A refused reel build must not strand its caption imports.

The defect
----------
`build_reel_timeline` puts every caption clip through
`pool.ImportMedia`, which lands in whatever bin is CURRENT - on the
field test, `Reels/Current plan`. On the pass path
`promote_staged_reels(organise=True)` files those clips afterwards;
on the refusal path nothing did. Reel 05 of the 2026-09-08 rebuild
refused its gate (F17 + F8) and left its 26 caption renders loose in
`Reels/Current plan` - the staging timeline was deleted and its
sidecar entries dropped, but the pool items stayed where ImportMedia
left them.

The fix is in `discard_staged_reels` / `discard_staged_record`: when
the master timeline is named, the pool is filed after the discard, so
a refused build's imports land in `06 - Subtitle renders/Not placed on
any timeline` instead of staying in the current bin. The gate still
refuses - only the debris handling changes.

This test drives `rebuild_reels_in_project` with a placer that stages
a timeline AND imports a caption clip into the current bin, then fails
the gate. It asserts the stray is filed, the staging is gone and the
approved reel is untouched. Against the pre-fix code the stray stays
in `Reels/Current plan` and the filing assertion fails.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from library.tools.reel_build import STAGING_SUFFIX, rebuild_reels_in_project
from tests.resolve_double import FakeProject

MASTER = "GEO Podcast - Synced"
TARGET = "Reel 05 - the-audit-that-was-eye-opening"
STAGING = TARGET + STAGING_SUFFIX


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    yield


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        f'resolve: {{project_name: "Mock Project", '
        f'timeline_name: "{MASTER}"}}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def _moment():
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = 5
    moment.timeline_name = TARGET
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


def _project():
    """Master and the approved reel, ImportMedia aimed at Current plan."""
    from library.tools import resolve_bin_layout as bins
    project = FakeProject("Mock Project", [MASTER, TARGET])
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    reels = pool.AddSubFolder(root, bins.REELS_BIN)
    current = pool.AddSubFolder(reels, bins.REEL_STATE_BINS["current"])
    subs = pool.AddSubFolder(root, bins.SUBTITLES_BIN)
    unplaced = pool.AddSubFolder(subs, bins.UNPLACED_BIN)
    pool.SetCurrentFolder(current)
    return project, current, unplaced


def test_refused_build_files_its_caption_imports(project_dir):
    """The defect: ImportMedia debris left in the current bin."""
    resolve_project, current, unplaced = _project()
    pool = resolve_project.GetMediaPool()

    stray_path = str(project_dir / "pipeline_output" / "steps"
                      / "4_05_render_subtitles" / "sub_reel-05-a.mov")

    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name == STAGING, f"expected staging, got {name!r}"
        pool.CreateEmptyTimeline(name)
        # The caption import the real placer does - lands in CURRENT.
        pool.ImportMedia([stray_path])
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    def _gate_failed_report():
        path = (project_dir / "pipeline_output" / "review"
                / "conformance_report.json")
        path.write_text(json.dumps({
            "has_errors": True,
            "findings": [
                {"severity": "error", "finding_class": "F17",
                 "message": "mixed-speaker card"},
                {"severity": "error", "finding_class": "F8",
                 "message": "end cuts mid-word"},
            ],
            # The rows name the STAGED container with its error
            # count - the shape the real gate writes - so the
            # refusal attributes to the reel that failed.
            "reels": [
                {"reel_name": STAGING,
                 "reel_number": 5,
                 "errors": 2,
                 "warnings": 0,
                 "captions": "0/0",
                 "findings": [
                     {"finding_class": "F17", "severity": "error"},
                     {"finding_class": "F8", "severity": "error"},
                 ]},
            ],
        }), encoding="utf-8")

    _gate_failed_report()

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment()]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=1):
        with pytest.raises(RuntimeError, match="defective timeline"):
            rebuild_reels_in_project(str(project_dir), only=[5])

    names = resolve_project.names()
    assert TARGET in names, "the approved reel must survive a refusal"
    assert STAGING not in names, "the refused staging must be gone"
    assert sorted(names) == sorted([MASTER, TARGET])

    # The debris is what this test is about: the imported caption clip
    # must be filed under the canonical unplaced bin, not left in the
    # current bin.
    current_names = [c.GetName() for c in current.GetClipList()]
    unplaced_names = [c.GetName() for c in unplaced.GetClipList()]
    assert "sub_reel-05-a.mov" not in current_names, (
        "a refused build stranded its caption import in the current bin")
    assert "sub_reel-05-a.mov" in unplaced_names, (
        "a refused build must file its caption imports under "
        "'Not placed on any timeline' once no timeline places them")
