"""A BUILT reel carries a durable sign-off, and promotion respects it.

A signed-off reel refuses promotion unless the promotion declares
`--supersede`, per reel (a refusal never holds back a sibling); an
unreadable sign-off file refuses rather than reading as "nobody approved
anything". The declare-then-proceed half is pinned in
`tests/test_reel_retirement.py::test_a_signed_off_backup_retires_on_the_default_path`.
"""

from unittest.mock import patch

import pytest

from library.tools import reel_signoff as signoff
from library.tools.reel_build import ReelBuildError, promote_staged_reels
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

REEL = "Reel 09 - your-website-is-only-20-percent"
OTHER = "Reel 13 - the-accounting-firm"
MASTER = "Podcast - Synced"


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script, monkeypatch):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    install_fake_timeline_snapshots(monkeypatch)
    yield


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


# ── The store ────────────────────────────────────────────────────


def test_a_signoff_survives_its_containers_and_records_which_build(project):
    """A reel is staged, backed up and promoted - three names for one
    reel. A sign-off keyed on the container name would evaporate the
    moment the build touched it."""
    signoff.sign_off(str(project), REEL, note="this one ships")
    for container in (
        REEL,
        f"{REEL} (rebuild staging)",
        f"{REEL} (pre-rebuild backup)",
    ):
        assert signoff.signoff_for(str(project), container) is not None
    # And it records WHICH build was approved: a bare flag cannot answer
    # whether the timeline in front of you is still the signed one.
    rows = {
        "video:A": {
            "media_type": "video",
            "index": 1,
            "name": "A",
            "items": [],
            "count": 0,
            "frames": 0,
        }
    }
    signoff.sign_off(str(project), OTHER, rows=rows)
    assert "still carries the rows that were approved" in signoff.describe(
        str(project), OTHER, rows
    )
    moved = {"video:A": {**rows["video:A"], "count": 1, "frames": 10}}
    assert "a later build has taken this name" in signoff.describe(
        str(project), OTHER, moved
    )


def test_an_unreadable_signoff_file_refuses(project):
    """An unreadable approval reads exactly like no approval, and
    promotion would then overwrite the reel the captain signed off."""
    (project / "pipeline_output" / "review" / signoff.SIGNOFF_FILENAME).write_text(
        "{broken", encoding="utf-8"
    )
    with pytest.raises(signoff.SignOffsUnreadable):
        signoff.read_signoffs(str(project))


# ── The promotion ────────────────────────────────────────────────


def _rows(name):
    return [("Akshita", [FakeItem(f"{name} clip", 0, 100)])]


def _pair(final):
    return (
        FakeTimeline(final, video=_rows(final)),
        FakeTimeline(f"{final} (rebuild staging)", video=_rows(final)),
    )


def _promote(resolve, project, staged_to_final, supersede=None):
    import json

    (project / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    with (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly", return_value=resolve),
    ):
        return promote_staged_reels(
            str(project),
            "Mock Project",
            MASTER,
            staged_to_final,
            organise=False,
            supersede=supersede,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )


def test_promotion_refuses_over_an_undeclared_signoff(project):
    """Remove `assert_declared` from the promotion and the signed-off
    timeline is replaced with no word said. Per reel, like the guard: the
    refusal never holds back a sibling (the 2026-09-11 round lost three
    buildable reels to one refusal)."""
    signoff.sign_off(str(project), REEL, note="the ending is right now")
    original, staging = _pair(REEL)
    other, other_staging = _pair(OTHER)
    resolve = FakeProject(
        [FakeTimeline(MASTER), original, staging, other, other_staging]
    )

    with pytest.raises(ReelBuildError) as refused:
        _promote(
            resolve,
            project,
            {REEL: staging.GetName(), OTHER: other_staging.GetName()},
        )

    message = str(refused.value)
    assert "SIGNED OFF" in message
    assert "the ending is right now" in message
    assert f"--supersede {REEL!r}" in message
    assert f"Promoted 1 reel(s): {[OTHER]}" in message
    assert OTHER in resolve.names()
    # Nothing of the signed reel was renamed: the approved timeline is
    # still there and the staging is untouched for a deliberate re-run.
    assert original.GetName() == REEL
    assert staging.GetName() == f"{REEL} (rebuild staging)"
    assert resolve.deleted == [f"{OTHER} (pre-rebuild backup)"]
    assert signoff.signoff_for(str(project), REEL) is not None
