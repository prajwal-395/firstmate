"""The promotion files the media pool without ever failing the build.

`promote_staged_reels(organise=True)` runs the idempotent by-reference
filing pass (`library/tools/execution/organise_media_pool.py`) after the
renames land. Two properties make that safe to call from a build, and
both are proven here on fakes - nothing reaches Resolve or a real
project:

- a filing pass that errors takes a good build down with it, which
  would be a bad trade. A refusal is said on stderr and carried on the
  record as `{"refused": ...}`, and the promoted reels stand.
- a filing pass with nothing to do is quiet: one line instead of the
  full Filed/unplaced/scratch trio. A pass that moved something
  reports what it moved.
"""

import json
from unittest.mock import patch

import pytest

from library.tools.reel_build import promote_staged_reels
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots,
    no_a_roll_track_plans,
)
from tests.resolve_double import (
    FakeProject,
    FakeTimeline,
)
from tests.resolve_double import (
    TimelineItemSpec as FakeItem,
)

MASTER = "Podcast - Synced"
FINAL = "Reel 31 - is-there-a-way-to-game-ai"


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture(autouse=True)
def fake_preservation_snapshots(monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    return root


def _clean_reel(final):
    retired = FakeTimeline(
        final,
        video=[
            ("Akshita", [FakeItem("Akshita A", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 131)]),
        ],
    )
    staging = FakeTimeline(
        final + " (rebuild staging)",
        video=[
            ("Akshita", [FakeItem("Akshita A", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 131)]),
        ],
    )
    return retired, staging


def _idle_organised():
    return {
        "journal": {"moves": [], "journal_path": "/tmp/never.json"},
        "unplaced": {
            "count": 0,
            "bins": (),
            "on_disk": 0,
            "missing": 0,
            "bytes_on_disk": 0,
            "shared_with_placed": (),
            "bytes_shared": 0,
        },
        "scratch": {
            "present": [],
            "held": [],
            "outlived": [],
            "misplaced": [],
            "holds_unreadable": "",
        },
        "retirement": {"retired": []},
    }


def _active_organised(moves=2):
    record = _idle_organised()
    record["journal"] = {
        "moves": [{"name": f"clip {n}"} for n in range(moves)],
        "journal_path": "/tmp/placements.json",
    }
    return record


def _swept():
    return {
        "applied": True,
        "pool": {"removed": 0, "counts": {}},
        "files": {"areas": []},
        "bins": {},
        "refused": [],
        "journal_path": "",
    }


def _promote(project, project_dir, staged_to_final):
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    with (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly", return_value=project),
    ):
        return promote_staged_reels(
            str(project_dir),
            "Mock Project",
            MASTER,
            staged_to_final,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )


def test_filing_refusal_never_fails_promotion(project_dir, capsys):
    """A filing pass that errors is said, recorded, and does not take
    the promoted reels down with it."""
    retired, staging = _clean_reel(FINAL)
    project = FakeProject([FakeTimeline(MASTER), retired, staging])
    staged_to_final = {FINAL: staging.GetName()}
    with (
        patch(
            "library.tools.execution.organise_media_pool.organise_project",
            side_effect=RuntimeError("MoveClips returned False"),
        ),
        patch("library.tools.build_sweep.sweep_build", return_value=_swept()),
    ):
        result = _promote(project, project_dir, staged_to_final)
    assert result["promoted"] == [FINAL]
    assert result["organised"] == {"refused": "MoveClips returned False"}
    assert "media-pool filing refused" in capsys.readouterr().err
    assert FINAL in [t.GetName() for t in project.timelines]


def test_idle_filing_is_quiet(project_dir, capsys):
    """Nothing to file: one line, and no Filed/unplaced/scratch trio."""
    retired, staging = _clean_reel(FINAL)
    project = FakeProject([FakeTimeline(MASTER), retired, staging])
    staged_to_final = {FINAL: staging.GetName()}
    with (
        patch(
            "library.tools.execution.organise_media_pool.organise_project",
            return_value=_idle_organised(),
        ),
        patch("library.tools.build_sweep.sweep_build", return_value=_swept()),
    ):
        result = _promote(project, project_dir, staged_to_final)
    assert result["promoted"] == [FINAL]
    out = capsys.readouterr().out
    assert "already organised" in out
    assert "Filed" not in out
