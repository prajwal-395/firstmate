"""A build grades what it placed, not the whole project.

The defect this pins
--------------------
`rebuild_reels_in_project` learned to build ONE reel add-only
(`only=[3]` deletes nothing it is not about to place), but its closing
gate - `verify_built_reels` -> `run_verification` - still enumerated
every `Reel *` timeline in the Resolve project. With 49 timelines
present, building one reel graded 49; the authorised 19-reel rebuild
would have graded 50, then 51, then 52 - quadratic, on the captain's
own machine.

Worse than slow: the untouched timelines are graded against the
CURRENT plan, which describes different reels, so a clean single-reel
build failed on findings from timelines it never touched - 94 of 121
errors in `data/vep-rebuild-verify/report.md` in the firstmate home, 3.5,
and 28
`NO-REFERENCE` + `PLAN-MISMATCH` errors on previous batches' reels in
`data/vep-harvest/report.md` in the firstmate home.

Which side is wrong
-------------------
The CHECK, not the builder. The builder's own record already promises
the scoped reading: `build_reels` returns `timelines_built`, and the
`verify_reels` node returned `timelines_verified` from that same list
while the code underneath graded everything. The record was the
contract; the verifier was not keeping it. Nothing is loosened - the
same findings fail when they are on a timeline the build placed, and
an empty scope is REFUSED rather than passed.

Both directions, per AGENTS.md 10.4: a full build still grades all it
placed, and grading nothing never passes.
"""
import io
import json

import pytest
from unittest.mock import MagicMock, patch

from library.tools.reel_build import (
    STAGING_SUFFIX,
    rebuild_reels_in_project,
)

MASTER = "GEO Podcast - Synced"
APPROVED = [f"Reel {n:02d} - moment-{n}" for n in range(1, 20)]


@pytest.fixture(autouse=True)
def mock_dvr():
    with patch.dict("sys.modules", {"DaVinciResolveScript": MagicMock()}):
        yield


@pytest.fixture
def project(tmp_path):
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
    (scratch / "transcript.json").write_text("{}", encoding="utf-8")
    return root


def _moment(number, name):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


def _timeline(name):
    timeline = MagicMock()
    timeline._name = name
    timeline.GetName.side_effect = lambda: timeline._name
    def _rename(new):
        timeline._name = new
        return True
    timeline.SetName.side_effect = _rename
    return timeline


class FakeProject:
    def __init__(self, names):
        self.timelines = [_timeline(name) for name in names]
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.CreateEmptyTimeline.side_effect = self._create
        self._pool = pool

    def _create(self, name):
        timeline = _timeline(name)
        self.timelines.append(timeline)
        return timeline

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

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


def _staging(final):
    return final + STAGING_SUFFIX


def _run_build(resolve_project, project_dir, **kwargs):
    """Drive the ONE build path with Resolve, the placer and captions faked.

    Returns the build record and the mocked verifier gate, so tests read
    what the gate was ASKED to grade rather than inferring it.
    """
    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        return resolve_project.GetMediaPool().CreateEmptyTimeline(name)

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments") as caps, \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(i + 1, name)
                                for i, name in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=0) as gate:
        caps.return_value = [{"overlay_path": "x.mov"}]
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, gate


# ── The builder hands the gate its own scope ─────────────────────────

def test_a_partial_build_grades_only_the_timeline_it_placed(project):
    """Building reel 3 of 19 must ask the verifier for exactly one name -
    the staging container it placed, never the approved timeline.

    Before the fix the gate was called with no scope at all, so it
    graded all nineteen - the call the quadratic cost and the 94
    foreign errors both came from.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, gate = _run_build(resolve_project, project, only=[3])

    assert record["timelines_built"] == ["Reel 03 - moment-3"]
    assert record["staged_timelines"] == {}
    # Two verifications: the refusing gate scoped to what was placed,
    # then the informational whole-project sweep beside it.
    assert gate.call_count == 2
    assert gate.call_args_list[0][1]["only_reels"] == [
        _staging("Reel 03 - moment-3")]
    assert gate.call_args_list[1][1]["only_reels"] is None
    # Promoted: the final name is back and no staging is left behind.
    assert sorted(resolve_project.names()) == sorted([MASTER] + APPROVED)


def test_a_full_build_grades_everything_it_placed(project):
    """The other direction: a full rebuild must not silently narrow.

    Scoping to the build's own output still grades all nineteen when
    nineteen were placed - the explicit sweep is preserved, now as a
    consequence of what was built rather than as a project-wide scan.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, gate = _run_build(resolve_project, project)

    assert record["timelines_built"] == APPROVED
    assert gate.call_count == 2
    assert gate.call_args_list[0][1]["only_reels"] == [
        _staging(name) for name in APPROVED]
    assert gate.call_args_list[1][1]["only_reels"] is None


def test_a_suffixed_rebuild_grades_the_new_container_not_the_old(project):
    """`--only-reel 3 --name-suffix` places a NEW timeline; the old
    approved one is untouched and must not be what the gate grades."""
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, gate = _run_build(resolve_project, project, only=[3],
                               name_suffix=" (whole-take rebuild)")

    assert record["timelines_built"] == [
        "Reel 03 - moment-3 (whole-take rebuild)"]
    assert gate.call_count == 2
    assert gate.call_args_list[0][1]["only_reels"] == [
        _staging("Reel 03 - moment-3 (whole-take rebuild)")]
    assert gate.call_args_list[1][1]["only_reels"] is None


# ── The verifier honours the scope ───────────────────────────────────

def _zeroed_result(name, number):
    from library.tools.reel_conformance_verifier import ReelResult

    return ReelResult(
        reel_name=name, reel_number=number, plan_seconds=0.0,
        plan_frames=0.0, actual_frames=0, items_expected=0,
        items_actual=0, one_frame_holes=0, big_holes=[],
        captions_expected=0, captions_actual=0, speech_seconds=0.0,
        uncaptioned_seconds=0.0, uncaptioned_pct=0.0, short_captions=0,
        edge_cuts=0, bad_take_cuts=0, markers=0, findings=[])


def _run_scoped_verification(names, only_reels):
    """Drive `run_verification` against a stand-in Resolve project.

    Master plus three reels; snapshots and the per-reel check are
    faked so the test measures SCOPE - which timelines were read and
    graded - rather than any finding class.
    """
    from library.tools.reel_conformance_verifier import (
        ReelTimeline, run_verification)

    timelines = [_timeline(name) for name in names]
    fake = MagicMock()
    fake.GetTimelineCount.return_value = len(timelines)
    fake.GetTimelineByIndex.side_effect = lambda i: timelines[i - 1]

    FPS = 24000 / 1001

    def fake_snapshot(timeline, project_name):
        snap = MagicMock()
        snap._name = timeline.GetName()
        snap.fps = FPS
        snap.duration = 10.0
        snap.picture_clips.return_value = []
        return snap

    graded = []

    def fake_verify(plan, timeline, **kwargs):
        graded.append(plan.reel_name)
        number = 0
        for index, name in enumerate(names):
            if name == plan.reel_name:
                number = index
        return _zeroed_result(plan.reel_name, number)

    out = io.StringIO()
    with patch("library.tools.marker_feedback.connect_resolve") as connect, \
            patch("library.tools.timeline_ingest.resolve_project_exactly",
                  return_value=fake), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=fake_snapshot) as snap_fn, \
            patch("library.tools.timeline_ingest.snapshot_to_dict",
                  side_effect=lambda snap: {"timeline": snap._name}), \
            patch("library.tools.reel_conformance_verifier._snapshot_to_reel_timeline",
                  side_effect=lambda snap: ReelTimeline(
                      reel_name=snap._name, fps=FPS, total_frames=240,
                      video_items=(), audio_items=(), caption_items=())), \
            patch("library.tools.reel_conformance_verifier.verify_reel",
                  side_effect=fake_verify):
        connect.return_value.GetProjectManager.return_value = MagicMock()
        code = run_verification(
            project_name="Mock Project",
            master_name=MASTER,
            transcript={"segments": []},
            only_reels=only_reels,
            out=out)
    return code, graded, snap_fn, out.getvalue()


def test_scoped_verification_reads_and_grades_one_timeline():
    """The cost pin: one scoped reel snapshots master + 1, not master + 3.

    Before the fix `run_verification` took no scope, so this call
    raised `TypeError` - and without the argument the same build read
    every timeline in the project.
    """
    names = [MASTER, "Reel 01 - a", "Reel 02 - b", "Reel 03 - c"]

    code, graded, snap_fn, output = _run_scoped_verification(
        names, ["Reel 02 - b"])

    assert code == 0
    assert graded == ["Reel 02 - b"]
    # Master plus the one scoped reel, each snapshotted twice (the
    # read-only proof re-reads what was graded) - the snapshot count IS
    # the cost, and it no longer grows with the project's reel count.
    assert snap_fn.call_count == 4
    assert "Reel 02 - b" in output


def test_unscoped_verification_still_grades_everything():
    """`None` is the deliberate sweep: no scope grades every reel."""
    names = [MASTER, "Reel 01 - a", "Reel 02 - b", "Reel 03 - c"]

    code, graded, snap_fn, _ = _run_scoped_verification(names, None)

    assert code == 0
    assert sorted(graded) == ["Reel 01 - a", "Reel 02 - b", "Reel 03 - c"]
    assert snap_fn.call_count == 8


def test_a_scoped_name_matching_nothing_is_fatal_not_a_smaller_pass():
    """A misspelled scope that quietly graded the rest would be the gate
    that cannot fail wearing a filter. It returns 2 and grades nothing."""
    names = [MASTER, "Reel 01 - a", "Reel 02 - b"]

    code, graded, _, _ = _run_scoped_verification(
        names, ["Reel 09 - typo"])

    assert code == 2
    assert graded == []


# ── An empty scope never passes ──────────────────────────────────────

def test_verifying_zero_timelines_is_refused_not_passed():
    """`verify_built_reels` with an empty scope must raise rather than
    report success on nothing graded."""
    from library.tools.reel_build import verify_built_reels

    with pytest.raises(RuntimeError, match="zero reel timelines"):
        verify_built_reels(
            project_folder="/nonexistent",
            resolve_project_name="Mock Project",
            master_timeline_name=MASTER,
            plan_path="/nonexistent/plan.json",
            transcript_path="/nonexistent/transcript.json",
            only_reels=[])


def _verify_step_module():
    from library.tools.operations import load_step_module

    return load_step_module("step_7_02_verify_reels", "step.py")


def test_the_step_refuses_a_build_record_naming_nothing():
    """The `verify_reels` node with an empty `timelines_built` refuses
    instead of falling back to grading the whole project."""
    module = _verify_step_module()

    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value="/tmp/project/transcript.json"):
        with pytest.raises(module.ReelVerifyRefused, match="no timelines_built"):
            module.verify_reels({
                "project_folder": "/tmp/project",
                "reel_build": {
                    "timelines_built": [],
                    "resolve_project_name": "Mock Project",
                    "master_timeline_name": MASTER,
                    "plan_path": "/tmp/project/plan.json",
                },
                "timeline_transcript": {"segments": []},
            })
        assert not gate.called


def test_the_step_forwards_the_build_record_as_its_scope():
    """The node's own return record already claimed `timelines_verified`
    from `timelines_built` - this pins the code to the same promise."""
    module = _verify_step_module()

    built = ["Reel 03 - moment-3"]
    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.reel_build.promote_staged_reels") as promote, \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value="/tmp/project/transcript.json"):
        record = module.verify_reels({
            "project_folder": "/tmp/project",
            "reel_build": {
                "timelines_built": built,
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": "/tmp/project/plan.json",
            },
            "timeline_transcript": {"segments": []},
        })

    assert gate.call_args[1]["only_reels"] == built
    assert not promote.called
    assert record["reel_verification"]["timelines_verified"] == built


def test_the_step_promotes_staged_timelines_only_after_the_gate_passes():
    """The DAG path: the build staged, this node grades the staging and
    promotes on a pass - returning the final names, not the staging."""
    module = _verify_step_module()

    final = "Reel 03 - moment-3"
    staged = _staging(final)
    with patch("library.tools.reel_build.verify_built_reels") as gate, \
            patch("library.tools.reel_build.promote_staged_reels",
                  return_value={"promoted": [final],
                                "organised": None}) as promote, \
            patch("library.tools.reel_build."
                  "sweep_all_reels_informational") as sweep, \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value="/tmp/project/transcript.json"):
        record = module.verify_reels({
            "project_folder": "/tmp/project",
            "reel_build": {
                "timelines_built": [staged],
                "staged_timelines": {final: staged},
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": "/tmp/project/plan.json",
            },
            "timeline_transcript": {"segments": []},
        })

    assert gate.call_args[1]["only_reels"] == [staged]
    assert promote.call_args[0][1:4] == (
        "Mock Project", MASTER, {final: staged})
    assert record["reel_verification"]["timelines_verified"] == [final]
    # The whole-project sweep runs after promotion, on the promoted
    # project - and it is informational, so it is a separate call the
    # gate's scope never narrows.
    assert sweep.call_count == 1
    assert sweep.call_args[1]["project_folder"] == "/tmp/project"
    assert sweep.call_args[1]["plan_path"] == "/tmp/project/plan.json"


def test_the_step_discards_staging_when_the_gate_refuses():
    """A refused gate on the DAG path still removes the staging before
    the refusal propagates - the approved timelines are never named."""
    module = _verify_step_module()

    final = "Reel 03 - moment-3"
    staged = _staging(final)
    with patch("library.tools.reel_build.verify_built_reels",
               side_effect=RuntimeError("F8 end cuts mid-word")), \
            patch("library.tools.reel_build.discard_staged_record") as discard, \
            patch("library.tools.reel_build.promote_staged_reels") as promote, \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value="/tmp/project/transcript.json"), \
            pytest.raises(RuntimeError, match="F8 end cuts mid-word"):
        module.verify_reels({
            "project_folder": "/tmp/project",
            "reel_build": {
                "timelines_built": [staged],
                "staged_timelines": {final: staged},
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": "/tmp/project/plan.json",
            },
            "timeline_transcript": {"segments": []},
        })

    assert discard.call_args[0][1:] == ("Mock Project", [staged], MASTER)
    assert not promote.called
