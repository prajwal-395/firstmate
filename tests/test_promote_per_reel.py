"""Promotion is per reel: one refusal must not discard its siblings.

The 2026-09-11 round built Reels 01/23/30/31, one refusal fired (the
lc-0004 join the guard could not read), and all-or-nothing promotion
discarded all four - the three that were fine were rebuilt from
scratch an hour later. Promotion now diffs and renames per reel: a
reel that passes promotes, a reel that refuses stays staged with its
baselines and hold intact, and the refusal names only itself.

Proven here on fixtures carrying the round's real names: four reels
promote together, one with the cutaway loss shape, and the other
three land while the refusal names just the one.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from tests.promotion_test_helpers import no_a_roll_track_plans

from library.tools.reel_build import (
    ReelBuildError,
    promote_staged_reels,
)

MASTER = "Podcast - Synced"

REEL_01 = "Reel 01 - hook-and-promise (final)"
REEL_23 = "Reel 23 - pricing-truth (final)"
REEL_30 = "Reel 30 - audio-seam (final)"
REEL_31 = "Reel 31 - kept-phrase (final)"


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
        return dict(getattr(self, "_markers", {}))

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        self.added_markers = getattr(self, "added_markers", [])
        self.added_markers.append((frame, color, name, note))
        return True


class FakeProject:
    def __init__(self, timelines):
        self.timelines = list(timelines)
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        self._pool = pool
        self.deleted = []

    def _delete(self, timelines):
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


def _clean_reel(final):
    """A reel whose rebuild changes nothing the guard cares about."""
    retired = FakeTimeline(final, video=[
        ("Akshita", [FakeItem("Akshita A", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 131)]),
    ])
    staging = FakeTimeline(final + " (rebuild staging)", video=[
        ("Akshita", [FakeItem("Akshita A", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 131)]),
    ])
    return retired, staging


def _refused_reel():
    """Reel 31 with the cutaway loss shape: V1 3 items to 2, the cover
    gone and nothing declaring it."""
    retired = FakeTimeline(REEL_31, video=[
        ("Akshita", [FakeItem("Craig A", 0, 55),
                      FakeItem("LC4932 cover", 574, 598),
                      FakeItem("Craig B", 598, 657)]),
        ("Subtitles", [FakeItem("card 1", 0, 60),
                        FakeItem("card 2", 60, 131)]),
    ])
    staging = FakeTimeline(REEL_31 + " (rebuild staging)", video=[
        ("Akshita", [FakeItem("Craig A", 0, 67),
                      FakeItem("Craig B", 67, 138)]),
        ("Subtitles", [FakeItem("card 1", 0, 60),
                        FakeItem("card 2", 60, 131)]),
    ])
    return retired, staging


def test_one_refusal_promotes_its_siblings(project_dir):
    retired_01, staging_01 = _clean_reel(REEL_01)
    retired_23, staging_23 = _clean_reel(REEL_23)
    retired_30, staging_30 = _clean_reel(REEL_30)
    retired_31, staging_31 = _refused_reel()
    resolve = FakeProject([FakeTimeline(MASTER), retired_01, retired_23,
                           retired_30, retired_31, staging_01, staging_23,
                           staging_30, staging_31])
    staged_to_final = {
        REEL_01: staging_01.GetName(),
        REEL_23: staging_23.GetName(),
        REEL_30: staging_30.GetName(),
        REEL_31: staging_31.GetName(),
    }
    (project_dir / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")

    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve):
        with pytest.raises(ReelBuildError) as refused:
            promote_staged_reels(
                str(project_dir), "Mock Project", MASTER,
                staged_to_final, organise=False,
                track_plans=no_a_roll_track_plans(staged_to_final))

    message = str(refused.value)
    # The promoted line says what landed; the refusal body names only
    # itself - no sibling is implicated in another reel's refusal.
    assert f"Promoted 3 reel(s): {[REEL_01, REEL_23, REEL_30]}" in message
    _header, refusal_body = message.split(
        f"REFUSING to promote 1 reel(s): {[REEL_31]}.")
    assert REEL_31 in refusal_body
    assert "video:Akshita" in refusal_body
    assert "LC4932 cover" in refusal_body
    assert REEL_01 not in refusal_body
    assert REEL_23 not in refusal_body
    assert REEL_30 not in refusal_body
    # ...the three passing reels landed under their final names with
    # no staging or backup debris left for them (each final appears
    # exactly once - the replaced originals were backed up and then
    # DELETED, the stagings renamed onto the final names;
    # `library/tools/reel_retirement.py`)...
    names = resolve.names()
    assert REEL_01 in names and REEL_23 in names and REEL_30 in names
    assert names.count(REEL_01) == 1
    assert names.count(REEL_23) == 1
    assert names.count(REEL_30) == 1
    assert not [name for name in names
                if name.endswith(("(rebuild staging)",
                                   "(pre-rebuild backup)"))
                and name != staging_31.GetName()]
    # ...and the refused reel is exactly as it was: approved timeline
    # untouched, staging still in the project for a deliberate re-run.
    assert retired_31 in resolve.timelines
    assert staging_31 in resolve.timelines
    assert REEL_31 in names and staging_31.GetName() in names
    # The three passing reels deleted exactly their own backups - one
    # timeline per reel - and nothing was renamed into the archive
    # (the captain, 2026-09-18: no leftovers by default; there is no
    # earlier generation here, so nothing is collected either).
    assert sorted(resolve.deleted) == sorted(
        [f"{REEL_01} (pre-rebuild backup)",
         f"{REEL_23} (pre-rebuild backup)",
         f"{REEL_30} (pre-rebuild backup)"])
    from library.tools import reel_retirement
    archived = [name for name in names
                if reel_retirement.is_archived_timeline(name)]
    assert archived == []


def test_unlinked_aroll_staging_is_refused_before_promotion(project_dir):
    """Promotion reads the same a-roll link verdict as verify_timeline."""
    from library.tools.timeline_layout import A_ROLL, SPEECH, TrackSpec

    final = "Reel 11 - your-website-is-your-resume"
    staging_name = final + " (rebuild staging)"
    retired = FakeTimeline(final, video=[
        ("Craig", [FakeItem("LCATL0013.MXF", 0, 138)]),
    ], audio=[
        ("Craig CH1", [FakeItem("LCATL0013.MXF", 0, 138)]),
    ])
    staging = FakeTimeline(staging_name, video=[
        ("Craig", [FakeItem("LCATL0013.MXF", 7, 138)]),
    ], audio=[
        ("Craig CH1", [FakeItem("LCATL0013.MXF", 0, 138)]),
    ])
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    staged_to_final = {final: staging_name}
    raw_plan = {
        "video_tracks": [vars(TrackSpec(
            1, "video", A_ROLL, "Craig", "2"))],
        "audio_tracks": [vars(TrackSpec(
            1, "audio", SPEECH, "Craig CH1", "2"))],
        "material": {},
    }

    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve), \
            pytest.raises(ReelBuildError,
                          match="failed `aroll_linked`"):
        promote_staged_reels(
            str(project_dir), "Mock Project", MASTER,
            staged_to_final, organise=False,
            track_plans={staging_name: raw_plan})

    assert retired.GetName() == final
    assert staging.GetName() == staging_name
    assert retired in resolve.timelines
    assert staging in resolve.timelines


def test_promotion_refuses_when_staging_track_plan_is_missing(project_dir):
    final = "Reel 11 - your-website-is-your-resume"
    staging_name = final + " (rebuild staging)"
    retired, staging = _clean_reel(final)
    staging._name = staging_name
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve), \
            pytest.raises(ReelBuildError, match="has no track plan"):
        promote_staged_reels(
            str(project_dir), "Mock Project", MASTER,
            {final: staging_name}, organise=False)

    assert retired.GetName() == final
    assert staging.GetName() == staging_name
    assert retired in resolve.timelines
    assert staging in resolve.timelines
