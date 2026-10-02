"""Promotion record failures remain visible after a timeline lands.

The incident history and failure matrix live in
`docs/evidence/promotion_record_consistency.md`.
"""
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from library.tools.reel_build import (
    ReelBuildError,
    promote_staged_reels,
)
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots,
    no_a_roll_track_plans,
)
from tests.resolve_double import (
    FakeProject,
    FakeResolve,
    FakeTimeline,
    TimelineItemSpec,
)

MASTER = "Podcast - Synced"
REEL_01 = "Reel 01 - hook-and-promise (final)"
STAGING_01 = REEL_01 + " (rebuild staging)"


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture(autouse=True)
def fake_preservation_snapshots(monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    return root


def _clean_reel():
    retired = FakeTimeline(REEL_01, video=[
        ("Akshita", [TimelineItemSpec("Akshita A", 0, 131)]),
        ("Subtitles", [TimelineItemSpec("card 1", 0, 131)]),
    ])
    staging = FakeTimeline(STAGING_01, video=[
        ("Akshita", [TimelineItemSpec("Akshita A", 0, 131)]),
        ("Subtitles", [TimelineItemSpec("card 1", 0, 131)]),
    ])
    return retired, staging


def _seed_provenance(project_dir):
    (project_dir / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": [STAGING_01]}), encoding="utf-8")


def _promote(project_dir, resolve, **kwargs):
    kwargs.setdefault("organise", False)
    kwargs.setdefault("track_plans", no_a_roll_track_plans(
        {REEL_01: STAGING_01}))
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve):
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER,
            {REEL_01: STAGING_01}, **kwargs)


class _RecordFailure:
    def __init__(self, patch_target, message, *, supersede=False,
                 sign_off=False, returns_none=False, retirement=False):
        self.patch_target = patch_target
        self.message = message
        self.supersede = supersede
        self.sign_off = sign_off
        self.returns_none = returns_none
        self.retirement = retirement


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(_RecordFailure(None, "retirement failed",
                                    retirement=True),
                     id="declined-backup-retirement"),
        pytest.param(_RecordFailure(
            "library.tools.comparison_retirement.collect_for_bases",
            "comparison timelines"), id="comparison-retirement"),
        pytest.param(_RecordFailure(
            "library.tools.reel_signoff.supersede", "supersession",
            supersede=True), id="declared-signoff-supersession"),
        pytest.param(_RecordFailure(
            "library.tools.reel_signoff.supersede", "mid-promotion",
            supersede=True, sign_off=True, returns_none=True),
            id="signoff-disappears-mid-promotion"),
        pytest.param(_RecordFailure(
            "library.tools.versions.rounds.stamp_promotion", "not stamped"),
            id="round-stamp"),
        pytest.param(_RecordFailure(
            "library.tools.plan_provenance.record_carried_digests",
            "signatures did not"), id="carried-signatures"),
        pytest.param(_RecordFailure(
            "library.tools.caption_asset_gc.rename_ledger_timelines",
            "render-ledger"), id="render-ledger-binding"),
    ],
)
def test_post_promotion_record_failures_raise_after_the_reel_lands(
        project_dir, failure):
    """Each row exercises a post-promotion record boundary; see the evidence table."""
    retired, staging = _clean_reel()
    resolve = FakeProject(
        [FakeTimeline(MASTER), retired, staging],
        delete_ok=not failure.retirement)
    _seed_provenance(project_dir)
    if failure.sign_off:
        from library.tools import reel_signoff as signoff

        signoff.sign_off(str(project_dir), REEL_01, note="ships")

    promotion_args = {"supersede": [REEL_01]} if failure.supersede else {}
    with ExitStack() as stack:
        if failure.patch_target:
            patch_args = ({"return_value": None} if failure.returns_none
                          else {"side_effect": OSError("disk full")})
            stack.enter_context(patch(failure.patch_target, **patch_args))
        with pytest.raises(ReelBuildError, match=failure.message):
            _promote(project_dir, resolve, **promotion_args)

    assert REEL_01 in resolve.names()
    assert STAGING_01 not in resolve.names()
    assert staging.GetName() == REEL_01
    if failure.retirement:
        assert resolve.deleted == []


def test_a_pass_records_its_verdict_before_returning(project_dir):
    """The verify-record half of the ruling, pinned at the verifier:
    a 0 from `run_verification` means the conformance report is on
    disk with the graded reel rows - so no build can promote off an
    unrecorded verdict, by construction rather than by check.

    Driven against a stand-in Resolve project (the scope harness in
    `test_verify_scopes_to_built_reels.py` measures which timelines
    are read; this one measures what a pass leaves behind).
    """
    import io

    from library.tools.reel_conformance_verifier import ReelTimeline, run_verification

    master = FakeTimeline(MASTER, settings={
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1920",
    })
    fake = FakeProject([master, FakeTimeline("Reel 01 - a")], current=master)

    fps = 24000 / 1001

    def fake_snapshot(timeline, project_name):
        from library.tools.timeline_ingest import TimelineSnapshot

        return TimelineSnapshot(
            project_name=project_name,
            timeline_name=timeline.GetName(),
            fps=fps, reported_fps=23.976,
            width=1080, height=1920,
            start_frame=0, end_frame=240, clips=())

    json_path = str(project_dir / "conformance_report.json")
    with patch("library.tools.marker_feedback.connect_resolve") as connect, \
            patch("library.tools.timeline_ingest.resolve_project_exactly",
                  return_value=fake), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=fake_snapshot), \
            patch("library.tools.timeline_ingest.snapshot_to_dict",
                  side_effect=lambda snap: {
                      "timeline": snap.timeline_name}), \
            patch("library.tools.reel_conformance_verifier._snapshot_to_reel_timeline",
                  side_effect=lambda snap, **kwargs: ReelTimeline(
                      reel_name=snap.timeline_name, fps=fps, total_frames=240,
                      video_items=(), audio_items=(),
                      caption_items=())), \
            patch("library.tools.reel_conformance_verifier.verify_reel") as verified:
        from library.tools.reel_conformance_verifier import ReelResult

        def _clean(plan, timeline, **kwargs):
            return ReelResult(
                reel_name=plan.reel_name, reel_number=1, plan_seconds=0.0,
                plan_frames=0.0, actual_frames=0, items_expected=0,
                items_actual=0, one_frame_holes=0, big_holes=[],
                captions_expected=0, captions_actual=0, speech_seconds=0.0,
                uncaptioned_seconds=0.0, uncaptioned_pct=0.0,
                short_captions=0, edge_cuts=0, bad_take_cuts=0,
                markers=0, findings=[])
        verified.side_effect = _clean
        connect.return_value = FakeResolve()
        code = run_verification(
            project_name="Mock Project",
            master_name=MASTER,
            transcript={"segments": []},
            json_path=json_path,
            only_reels=["Reel 01 - a"],
            out=io.StringIO())

    assert code == 0
    with open(json_path, encoding="utf-8") as handle:
        report = json.load(handle)
    assert [row["reel_name"] for row in report["reels"]] == ["Reel 01 - a"]
