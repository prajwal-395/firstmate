"""A BUILT reel carries a durable sign-off, and promotion respects it.

Before this, approval was a state on a PROPOSED moment ruled on before
the build (`reel_proposal.Approval`), so the newest build under the
final name was the answer by construction and an approved reel could be
silently replaced. `staging_holds.py:14` states the absence in this
repository's own words.

Each test fails if its mechanism is removed:

- the refusal itself - delete `assert_declared` from the promotion loop
  and the signed-off reel promotes silently;
- the DECLARATION half - the refusal prints the exact flag, and that
  flag proceeds;
- per-reel scoping: a signed-off reel refusing never holds back a
  sibling;
- the sign-off being superseded rather than deleted;
- an unreadable sign-off file refusing rather than reading as "nobody
  approved anything".
"""
from unittest.mock import MagicMock, patch

import pytest

from library.tools import reel_signoff as signoff
from library.tools.reel_build import ReelBuildError, promote_staged_reels

REEL = "Reel 09 - your-website-is-only-20-percent"
OTHER = "Reel 13 - the-accounting-firm"
MASTER = "Podcast - Synced"


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    yield


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


# ── The store ────────────────────────────────────────────────────

def test_a_signoff_survives_the_containers_a_build_puts_a_reel_in(project):
    """A reel is staged, backed up and promoted - three names for one
    reel. A sign-off keyed on the container name would evaporate the
    moment the build touched it."""
    signoff.sign_off(str(project), REEL, note="this one ships")
    for container in (REEL, f"{REEL} (rebuild staging)",
                      f"{REEL} (pre-rebuild backup)"):
        assert signoff.signoff_for(str(project), container) is not None


def test_a_signoff_records_which_build_was_approved(project):
    """A bare "approved" flag cannot answer whether the timeline in
    front of you is still the one that was signed off."""
    rows = {"video:A": {"media_type": "video", "index": 1, "name": "A",
                        "items": [], "count": 0, "frames": 0}}
    signoff.sign_off(str(project), REEL, rows=rows)
    assert "still carries the rows that were approved" in \
        signoff.describe(str(project), REEL, rows)
    moved = {"video:A": {**rows["video:A"], "count": 1, "frames": 10}}
    assert "a later build has taken this name" in \
        signoff.describe(str(project), REEL, moved)


def test_an_unreadable_signoff_file_refuses(project):
    """An unreadable approval reads exactly like no approval, and
    promotion would then overwrite the reel the captain signed off."""
    (project / "pipeline_output" / "review"
     / signoff.SIGNOFF_FILENAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(signoff.SignOffsUnreadable):
        signoff.read_signoffs(str(project))


# ── The promotion ────────────────────────────────────────────────

class FakeItem:
    def __init__(self, name, start, end):
        self._n, self._s, self._e = name, start, end

    def GetName(self):
        return self._n

    def GetStart(self):
        return self._s

    def GetEnd(self):
        return self._e

    def GetDuration(self):
        return self._e - self._s


class FakeTimeline:
    def __init__(self, name, video=()):
        self._name = name
        self._rows = {"video": list(video), "audio": []}

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

    def AddMarker(self, *args, **kwargs):
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


def _rows(name):
    return [("Akshita", [FakeItem(f"{name} clip", 0, 100)])]


def _pair(final):
    return (FakeTimeline(final, video=_rows(final)),
            FakeTimeline(f"{final} (rebuild staging)", video=_rows(final)))


def _promote(resolve, project, staged_to_final, supersede=None):
    import json
    (project / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve):
        return promote_staged_reels(
            str(project), "Mock Project", MASTER, staged_to_final,
            organise=False, supersede=supersede)


def test_promotion_refuses_over_an_undeclared_signoff(project):
    """The whole point. Remove `assert_declared` from the promotion and
    the signed-off timeline is replaced with no word said."""
    signoff.sign_off(str(project), REEL, note="the ending is right now")
    original, staging = _pair(REEL)
    resolve = FakeProject([FakeTimeline(MASTER), original, staging])

    with pytest.raises(ReelBuildError) as refused:
        _promote(resolve, project, {REEL: staging.GetName()})

    message = str(refused.value)
    assert "SIGNED OFF" in message
    assert "the ending is right now" in message
    assert f"--supersede {REEL!r}" in message
    # Nothing was renamed: the approved timeline is still there and the
    # staging is untouched for a deliberate re-run.
    assert original.GetName() == REEL
    assert staging.GetName() == f"{REEL} (rebuild staging)"
    assert resolve.deleted == []
    assert signoff.signoff_for(str(project), REEL) is not None


def test_the_declaration_the_refusal_prints_proceeds(project):
    """Declare, then proceed - the shape `reel_replace_guard` already
    takes. A refusal that cannot be overridden is one an operator routes
    around; what must never happen is an accidental replacement."""
    signoff.sign_off(str(project), REEL, note="ships")
    original, staging = _pair(REEL)
    resolve = FakeProject([FakeTimeline(MASTER), original, staging])

    promoted = _promote(resolve, project, {REEL: staging.GetName()},
                        supersede=[REEL])

    assert promoted["promoted"] == [REEL]
    assert REEL in promoted["superseded_signoffs"]
    # Superseded, never deleted: "this reel was approved once and then
    # rebuilt" is exactly the question that had no answer before.
    assert signoff.signoff_for(str(project), REEL) is None
    history = signoff.read_signoffs(str(project))["superseded"]
    assert history[0]["ended_by"] == "superseded"
    assert history[0]["note"] == "ships"


def test_a_signed_off_reel_never_holds_back_a_sibling(project):
    """Per reel, like the guard: the 2026-09-11 round lost three
    buildable reels to one refusal, and that structure is why it cannot
    happen again."""
    signoff.sign_off(str(project), REEL)
    signed, signed_staging = _pair(REEL)
    other, other_staging = _pair(OTHER)
    resolve = FakeProject([FakeTimeline(MASTER), signed, signed_staging,
                           other, other_staging])

    with pytest.raises(ReelBuildError) as refused:
        _promote(resolve, project,
                 {REEL: signed_staging.GetName(),
                  OTHER: other_staging.GetName()})

    message = str(refused.value)
    assert f"Promoted 1 reel(s): {[OTHER]}" in message
    assert REEL in message
    # The sibling really landed, and the signed-off reel is untouched.
    assert OTHER in resolve.names()
    assert signed.GetName() == REEL
    assert signed_staging.GetName() == f"{REEL} (rebuild staging)"


