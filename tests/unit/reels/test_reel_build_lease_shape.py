"""The reel build holds the placement, not the build.

`rebuild_reels_in_project` used to hold the machine-wide EXCLUSIVE
Resolve lease for its entire body - minutes of Remotion caption
renders and model-answer reads - while the cursor writes that lease
exists to protect take seconds per reel. Reel throughput was capped
at one build at a time however many lanes ran.

The shape pinned here: derivation holds NOTHING, one exclusive hold
per reel covers the carried self-read and the decision, the placement
plan is prepared holding nothing, a second exclusive hold covers the
placement and the Fusion comp pass, and handle-only gate/survey reads
run under shared holds. Cursor-moving drift and digest reads take
exclusive holds. A test that only asserts "the build takes
a lease" would pass on both shapes; these fail on the old one.

The failure mode the narrower hold must still survive is
demonstrated deliberately below: a writer that never takes the lease
(the captain editing by hand) moving the cursor mid-placement. The
per-write check re-establishes the cursor before every append, so
the placement lands where it was aimed with or without a whole-build
hold - and the control shows what the same interleaving does to
writes without the check (2026-09-04: 579 captions onto the wrong
reel while every call returned True).
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from library.tools import reel_build as rb
from library.tools import resolve_lock
from library.tools.resolve_lock import (
    assert_current_timeline, resolve_lease)
from tests.promotion_test_helpers import install_fake_timeline_snapshots

MASTER = "GEO Podcast - Synced"
ORGANISE = False


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script, monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)
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

    def GetUniqueId(self):
        if not getattr(self, "_uid", None):
            type(self)._seq = getattr(type(self), "_seq", 0) + 1
            self._uid = f"{type(self).__name__}-{type(self)._seq}"
        return self._uid


class _ResolveProject:
    """A Resolve project whose pool really creates, renames and deletes,
    and whose cursor a foreign writer can move."""

    def __init__(self, names):
        self.timelines = [_StagingTimeline(name) for name in names]
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.CreateEmptyTimeline.side_effect = self._create
        self.appends = []
        pool.AppendToTimeline.side_effect = self._append
        self._pool = pool

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

    def _create(self, name):
        timeline = _StagingTimeline(name)
        self.timelines.append(timeline)
        return timeline

    def _append(self, items):
        current = self.GetCurrentTimeline()
        self.appends.append(
            (current.GetName() if current is not None else None,
             list(items)))
        return [MagicMock()]

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

    def GetCurrentTimeline(self):
        return getattr(self, "_current", None)

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


@pytest.fixture
def mock_project_env(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "project.yaml").write_text(
        'resolve: {project_name: "Mock Project", '
        'timeline_name: "GEO Podcast - Synced"}')
    review_dir = project_dir / "pipeline_output" / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "reel_proposals_v2.json").write_text("[]")
    scratch_dir = (project_dir / "pipeline_output" / "scratch"
                   / "timeline_transcript")
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "transcript.json").write_text('{"segments": []}')
    return project_dir


def _moment(number=7, name="Reel 01"):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


def _placing(resolve_project):
    def _place(**kwargs):
        name = kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}
    return _place


@contextmanager
def _spied_leases(events):
    """Record every hold this build takes, and what is open when.

    Patches the name the build calls - `reel_build.resolve_lease` -
    so holds taken inside other modules (snapshots, promotion) stay
    out of the record: this is the build's own shape, not the
    machine's.
    """
    real_lease = resolve_lock.resolve_lease
    open_stack = []

    @contextmanager
    def spy_lease(purpose, exclusive=True, **kwargs):
        events.append(("lease-enter", purpose, exclusive))
        with real_lease(purpose, exclusive=exclusive, **kwargs):
            open_stack.append((purpose, exclusive))
            events.append(("lease-held", purpose, exclusive))
            try:
                yield
            finally:
                open_stack.pop()
        events.append(("lease-exit", purpose, exclusive))

    def snapshot():
        return list(open_stack)

    with patch.object(rb, "resolve_lease", spy_lease):
        yield snapshot


def _run_build(mock_project_env, patches, events, snapshot):
    from library.tools.reel_build import rebuild_reels_in_project

    real_subtitles = rb.reel_subtitle_segments
    real_prepare = rb.prepare_reel_timeline

    def spy_subtitles(*args, **kwargs):
        events.append(("derivation", snapshot()))
        return real_subtitles(*args, **kwargs)

    def spy_prepare(*args, **kwargs):
        events.append(("prepare-call", snapshot()))
        return real_prepare(*args, **kwargs)

    def spy_place(**kwargs):
        events.append(("place-call", snapshot()))
        return _placing(patches["project"])(**kwargs)

    def verify_side_effect(**kwargs):
        events.append(("verify-call", snapshot()))
        report_path = kwargs.get("json_path")
        if report_path:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump({"has_errors": False, "findings": []}, handle)
        return 0

    patches["verifier"].side_effect = verify_side_effect
    with patch.object(rb, "reel_subtitle_segments",
                      side_effect=spy_subtitles), \
            patch.object(rb, "prepare_reel_timeline",
                         side_effect=spy_prepare):
        with patch.object(rb, "build_reel_timeline",
                          side_effect=spy_place):
            return rebuild_reels_in_project(
                str(mock_project_env), organise=ORGANISE)


@pytest.fixture
def built_with_spies(mock_project_env):
    """One full mock build with every hold and phase transition
    recorded. The build places one reel and promotes it."""
    events = []
    project = _ResolveProject([MASTER])
    with _spied_leases(events) as snapshot:
        with patch("library.tools.resolve_locale."
                   "scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment()]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.subtitle_style."
                  "resolve_subtitle_style"), \
            patch("library.tools.reel_conformance_verifier."
                  "run_verification") as verifier:
            record = _run_build(
                mock_project_env,
                {"project": project, "verifier": verifier},
                events, snapshot)
    return {"events": events, "record": record,
            "project": project, "folder": mock_project_env}


def _holds_open(entry):
    """The holds open when a spied call ran: the stack snapshot it
    recorded."""
    return entry[1]


def test_derivation_holds_no_lease(built_with_spies):
    """The minutes parallelise: caption rendering and every other
    derivation step run with no hold open.

    Read with the inventory test below: derivation events must
    appear, and must appear outside every hold the inventory names.
    A derivation step that started taking a hold would show up here
    with the stack open.
    """
    derivations = [e for e in built_with_spies["events"]
                   if e[0] == "derivation"]
    assert derivations, "the build never derived - the spy missed"
    for entry in derivations:
        assert _holds_open(entry) == [], (
            f"derivation ran under {_holds_open(entry)}")


def test_placement_holds_one_exclusive_hold(built_with_spies):
    """The seconds serialise: the placer runs under exactly one hold,
    and it is exclusive."""
    places = [e for e in built_with_spies["events"]
              if e[0] == "place-call"]
    assert len(places) == 1
    assert _holds_open(places[0]) == [
        ("place Reel 01 (rebuild staging)", True)]


def test_placement_planning_holds_nothing(built_with_spies):
    """FREE -> RESOLVE -> FREE: the reel's placement plan - offsets,
    angle plan, freeze render, post header, track plan, promoted
    artefacts - is computed between the decision hold and the
    placement hold, holding neither. It used to run inside
    `build_reel_timeline`, under the placement's exclusive hold."""
    events = built_with_spies["events"]
    prepares = [e for e in events if e[0] == "prepare-call"]
    assert len(prepares) == 1
    assert _holds_open(prepares[0]) == []
    order = [e[1] for e in events if e[0] == "lease-enter"]
    decide = order.index("decide Reel 01 (rebuild staging)")
    place = order.index("place Reel 01 (rebuild staging)")
    assert decide < place
    assert events.index(prepares[0]) > events.index(
        ("lease-exit", "decide Reel 01 (rebuild staging)", True))


def test_the_gate_reads_under_a_shared_hold(built_with_spies):
    """The gate must grade a stable staging, and several such reads
    run together while no writer runs - so shared, never exclusive."""
    verifies = [e for e in built_with_spies["events"]
                if e[0] == "verify-call"]
    assert len(verifies) == 2  # the scoped gate, then the sweep
    assert _holds_open(verifies[0]) == [("verify built reels", False)]
    assert _holds_open(verifies[1]) == [("sweep all reels", False)]


def test_cursor_moving_self_reads_take_exclusive_holds(built_with_spies):
    purposes = {
        "build reels drift baseline",
        "build reels master digest",
        "build reels drift end",
    }
    leases = [(entry[1], entry[2]) for entry in built_with_spies["events"]
              if entry[0] == "lease-enter" and entry[1] in purposes]
    assert leases == [(purpose, True) for purpose in (
        "build reels drift baseline", "build reels master digest",
        "build reels drift end")]


# ── The failure mode, demonstrated deliberately ───────────────────────
#
# Two lanes ran for 92% of 2026-09-18 one at a time, so the corruption
# a narrower hold must survive cannot be observed - it is constructed
# here instead. The foreign writer below takes no lease at all, which
# is exactly what the captain editing by hand does.


def _two_timeline_project():
    project = _ResolveProject(["Reel A", "Reel B"])
    by_name = {t.GetName(): t for t in project.timelines}
    return project, by_name["Reel A"], by_name["Reel B"]


def test_guarded_writes_survive_a_foreign_cursor_move():
    """Every append re-establishes the cursor first, so a foreign
    move between writes is corrected rather than written through."""
    project, victim, foreign = _two_timeline_project()
    with resolve_lease("placement", exclusive=True):
        for _ in range(5):
            project.SetCurrentTimeline(foreign)  # the captain, by hand
            assert_current_timeline(project, victim)
            project.GetMediaPool().AppendToTimeline(
                [{"mediaPoolItem": MagicMock()}])
    assert len(project.appends) == 5
    assert {current for current, _ in project.appends} == {"Reel A"}


def test_unguarded_writes_land_wherever_the_cursor_is():
    """The control: the same interleaving without the per-write
    check lands every append on the foreign timeline while every
    call returns True - the 2026-09-04 shape this guard exists for."""
    project, victim, foreign = _two_timeline_project()
    project.SetCurrentTimeline(victim)
    with resolve_lease("placement", exclusive=True):
        for _ in range(5):
            project.SetCurrentTimeline(foreign)  # the captain, by hand
            project.GetMediaPool().AppendToTimeline(
                [{"mediaPoolItem": MagicMock()}])
    assert len(project.appends) == 5
    assert {current for current, _ in project.appends} == {"Reel B"}
