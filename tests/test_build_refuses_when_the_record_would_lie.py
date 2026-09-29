"""A build refuses to report success when its own record would be wrong.

Captain's 2026-09-18 ruling, option a: "Refuse only when the record
would be wrong; report everything else." The case that made it real:
a build produced a CORRECT TIMELINE and a FALSE RECORD - promotion
completed, filing bookkeeping refused, the node failed, and the reel
was right while its record was not. Every downstream check trusts the
record, which is why that shape is the one ruled against.

What refuses (each established FROM THE CODE - a fix covering only
the prompting case would look complete and quietly leave the rest):

- a failed retirement (`promote_staged_reels` used to swallow it and
  report success with the replaced timelines still standing under
  their backup names - which the NEXT build then refuses on loudly);
- a failed comparison retirement (same family: superseded comparison
  timelines silently accumulating while the build reads as settled);
- a declared sign-off supersession that did not land (the replaced
  cut keeps carrying a live approval, and the next promotion refuses
  demanding a declaration the operator already gave);
- an unstamped round (the version record misses the promotion, and
  the hole never heals: an unchanged reel is left alone, so no later
  build re-stamps this one);
- carried build signatures that will not close (the provenance half
  of the promotion never lands);
- render-ledger bindings that cannot be re-pointed (entries keep
  naming staging timelines that no longer exist, and the sweep reads
  the ledger as a reference root);
- a conformance PASS with no report on disk: established from the
  code that `run_verification` writes the report before it returns 0
  and a write failure raises rather than returning, so there is no
  pass-without-record path - pinned below at the verifier level
  rather than re-checked at every call site.

What still REPORTS and finishes (deliberately not widened):

- a refused media-pool filing (carried honestly as
  `{"refused": ...}` on the record with the retry named);
- an unreadable carried digest (the signature stays open, which
  reads as REBUILD - fail-closed, no reader misled);
- a vacuous `--supersede` (no sign-off was ever live: nothing to
  end, nothing to refuse).
"""
import json
from unittest.mock import patch

import pytest
from tests.promotion_test_helpers import no_a_roll_track_plans

from library.tools.reel_build import (
    ReelBuildError,
    promote_staged_reels,
)

MASTER = "Podcast - Synced"
REEL_01 = "Reel 01 - hook-and-promise (final)"
STAGING_01 = REEL_01 + " (rebuild staging)"


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    return root


class FakeItem:
    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetClipEnabled(self):
        return True


class FakeTimeline:
    def __init__(self, name, video=(), audio=()):
        self._name = name
        self._rows = {"video": list(video), "audio": list(audio)}

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetTrackCount(self, kind):
        return len(self._rows[kind])

    def GetTrackName(self, kind, index):
        return self._rows[kind][index - 1][0]

    def GetItemListInTrack(self, kind, index):
        return self._rows[kind][index - 1][1]

    def GetStartFrame(self):
        return 0

    def GetMarkers(self):
        return {}


class FakeProject:
    def __init__(self, timelines, delete_ok=True):
        self.timelines = list(timelines)
        self._delete_ok = delete_ok
        from unittest.mock import MagicMock
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        self._pool = pool
        self.deleted = []

    def _delete(self, timelines):
        # A refused delete removes nothing - mirroring Resolve, where
        # a falsy return means the timelines are still in the project.
        if not self._delete_ok:
            return False
        for timeline in timelines:
            self.deleted.append(timeline.GetName())
            self.timelines.remove(timeline)
        return True

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]


def _clean_reel():
    retired = FakeTimeline(REEL_01, video=[
        ("Akshita", [FakeItem("Akshita A", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 131)]),
    ])
    staging = FakeTimeline(STAGING_01, video=[
        ("Akshita", [FakeItem("Akshita A", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 131)]),
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




def test_a_failed_retirement_refuses_the_promotion(project_dir):
    """Resolve declines the backup delete: the replaced timeline is
    still standing under its backup name, so success would trade a
    named failure now for a confusing stale-backup refusal later."""
    retired, staging = _clean_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging],
                          delete_ok=False)
    _seed_provenance(project_dir)

    with pytest.raises(ReelBuildError, match="retirement failed"):
        _promote(project_dir, resolve)

    # The reels already landed - the raise says so rather than rolling
    # back - and nothing was deleted.
    assert REEL_01 in resolve.names()
    assert resolve.deleted == []


def test_a_failed_comparison_retirement_refuses_the_promotion(
        project_dir):
    """The comparison lifecycle is the same retirement family: a skip
    that reports success re-opens the accumulation the bound exists
    to stop."""
    retired, staging = _clean_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    _seed_provenance(project_dir)

    with patch("library.tools.comparison_retirement.collect_for_bases",
               side_effect=RuntimeError("Resolve is busy")), \
            pytest.raises(ReelBuildError,
                          match="comparison timelines"):
        _promote(project_dir, resolve)

    assert REEL_01 in resolve.names()


def test_a_failed_signoff_supersession_refuses_the_promotion(
        project_dir):
    """A declared supersession the record cannot take: the replaced
    cut would keep carrying a live approval it was never given."""
    retired, staging = _clean_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    _seed_provenance(project_dir)

    with patch("library.tools.reel_signoff.supersede",
               side_effect=OSError("disk full")), \
            pytest.raises(ReelBuildError, match="supersession"):
        _promote(project_dir, resolve, supersede=[REEL_01])

    assert REEL_01 in resolve.names()




def test_a_vanished_signoff_refuses_the_promotion(project_dir):
    """Live and declared before the rename, gone after it: something
    edited the sign-off record mid-promotion, so the reel's approval
    state cannot be vouched."""
    from library.tools import reel_signoff as signoff

    retired, staging = _clean_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    _seed_provenance(project_dir)
    signoff.sign_off(str(project_dir), REEL_01, note="ships")

    with patch("library.tools.reel_signoff.supersede",
               return_value=None), \
            pytest.raises(ReelBuildError, match="mid-promotion"):
        _promote(project_dir, resolve, supersede=[REEL_01])

    assert REEL_01 in resolve.names()


def test_an_unstamped_round_refuses_the_promotion(project_dir):
    """The version record misses the promotion - and the hole never
    heals, because an unchanged reel is left alone rather than
    re-stamped."""
    retired, staging = _clean_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    _seed_provenance(project_dir)

    with patch("library.tools.versions.rounds.stamp_promotion",
               side_effect=OSError("disk full")), \
            pytest.raises(ReelBuildError, match="not stamped"):
        _promote(project_dir, resolve)


def test_an_unclosable_signature_refuses_the_promotion(project_dir):
    """The carried digests were read but cannot be written: the
    provenance half of this promotion did not land."""
    retired, staging = _clean_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    _seed_provenance(project_dir)

    with patch("library.tools.plan_provenance.record_carried_digests",
               side_effect=OSError("disk full")), \
            pytest.raises(ReelBuildError, match="signatures did not"):
        _promote(project_dir, resolve)


def test_an_unrepointable_ledger_refuses_the_promotion(project_dir):
    """Ledger bindings keep naming staging timelines that no longer
    exist - a wrong record the sweep reads as its reference root."""
    retired, staging = _clean_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    _seed_provenance(project_dir)

    with patch("library.tools.caption_asset_gc.rename_ledger_timelines",
               side_effect=OSError("disk full")), \
            pytest.raises(ReelBuildError, match="render-ledger"):
        _promote(project_dir, resolve)

    assert REEL_01 in resolve.names()


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
    from unittest.mock import MagicMock

    from library.tools.reel_conformance_verifier import (
        ReelTimeline, run_verification)

    names = [MASTER, "Reel 01 - a"]
    timelines = []
    for name in names:
        timeline = MagicMock()
        timeline._name = name
        timeline.GetName.side_effect = lambda _t=timeline: _t._name
        timelines.append(timeline)
    fake = MagicMock()
    fake.GetTimelineCount.return_value = len(timelines)
    fake.GetTimelineByIndex.side_effect = lambda i: timelines[i - 1]
    fake.GetCurrentTimeline.return_value = timelines[0]
    timelines[0].GetSetting.side_effect = lambda key: {
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1920",
    }.get(key, "")

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
        connect.return_value.GetProjectManager.return_value = MagicMock()
        code = run_verification(
            project_name="Mock Project",
            master_name=MASTER,
            transcript={"segments": []},
            json_path=json_path,
            only_reels=["Reel 01 - a"],
            out=io.StringIO())

    assert code == 0
    report = json.loads(open(json_path, encoding="utf-8").read())
    assert [row["reel_name"] for row in report["reels"]] == ["Reel 01 - a"]
