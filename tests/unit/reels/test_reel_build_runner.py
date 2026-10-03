"""One capability's record never destroys another's.

Measured 2026-09-19 on Reel 04: `build_reels` owns two ops -
`reel.build` then `reel.ask` - and the reels runner recorded each
payload with a wholesale write into the node's one slot, so the
ask-only payload destroyed the `reel_build` record the build had just
placed. `reel.verify` then refused (`verify_reels needs reel_build from
build_reels`), leaving a staged reel with no path to promotion.

Nothing here reaches Resolve or a real project.
"""
from __future__ import annotations
import json
from library.processes.reels import run_reels
from library.tools import capability_outputs
import sys
import types
from pathlib import Path
import pytest
from unittest.mock import patch
import os
from contextlib import contextmanager
from unittest.mock import MagicMock
from library.tools import reel_build as rb
from library.tools import resolve_lock
from library.tools.resolve_lock import (
    assert_current_timeline, resolve_lease)
from tests.promotion_test_helpers import install_fake_timeline_snapshots
from tests.promotion_test_helpers import install_measured_draw_gain_probe
from library.tools.plan_provenance import read_provenance
from library.tools.reel_build import rebuild_reels_in_project
from tests.resolve_double import FakeProject
import datetime
from library.tools import reel_phase_log as phase_log
from library.tools import reel_semantic_visual as sem_vis
from types import SimpleNamespace
from library.tools import reel_prebuild_census as census
from library.tools import resolve_bin_layout as bins
from library.tools import resolve_organization as org
from library.tools.execution.organise_media_pool import plan_for_project
from library.tools.plan_provenance import current_plan_names
from library.tools.reel_proposal import reel_timeline_name
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from library.tools import reel_build
from library.tools.reel_proposal import (
    Approval,
    ReelMoment,
    proposal_path,
    write_proposal,
)


def test_the_ask_after_the_build_leaves_the_build_for_verify(tmp_path):
    project = str(tmp_path)
    run_reels.record_project_output(
        project, "reel.build", {"reel_build": {"placed": ["Reel 04"]}})
    run_reels.record_project_output(
        project, "reel.ask", {"reel_ask": {"asked": ["Reel 04"]}})

    state = json.loads((tmp_path / "pipeline_data.json").read_text(
        encoding="utf-8"))
    # What the edge to `verify_reels` carries.
    assert capability_outputs.node_output(state, "build_reels")[
        "reel_build"] == {"placed": ["Reel 04"]}


def test_a_project_recorded_before_the_records_still_loads(tmp_path):
    """A pre-migration state is read through, then written as records."""
    path = tmp_path / "pipeline_data.json"
    path.write_text(json.dumps({"step_outputs": {
        "build_reels": {"reel_build": {"v": "old"}}}}), encoding="utf-8")

    run_reels.record_project_output(str(tmp_path), "reel.ask",
                                    {"reel_ask": {}})

    state = json.loads(path.read_text(encoding="utf-8"))
    assert "step_outputs" not in state
    assert capability_outputs.read(state, "reel.build") == {
        "reel_build": {"v": "old"}}
    assert capability_outputs.read(state, "reel.ask") == {"reel_ask": {}}


# --------------------------------------------------------------------------
# From test_build_reels_skips_caller_supplied.py
#
# `build-reels` completes on a ready project instead of refusing.
#
# The defect (PR #1346): as coded, `cmd_build_reels` walked every
# operation each reels node owns, so on a ready project `reel.build`
# completed and the loop then REFUSED at `reel.touchup`
# (caller-supplied `spec` unbound) before `reel.ask`/`verify_reels`
# ever ran. The loop is not the caller that supplies those arguments,
# so it leaves caller-supplied operations out and runs the rest.
#
# The step bodies that reach Resolve are stubbed at the module the
# registry resolves - the loop, the requirement checks, the input
# gathering, the refusal and the state recording all run for real, and
# no Resolve, render or real reel build happens here.

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import manage_project  # noqa: E402
from library.tools import capabilities  # noqa: E402
from library.tools import operations  # noqa: E402
from library.tools import processes as processes_mod  # noqa: E402
from library.tools import requirements as _R  # noqa: E402
from library.tools.timeline_transcript import transcript_path  # noqa: E402


def _ready_project(root: Path) -> str:
    """A project with everything `reel.build` asks for, under tmp only."""
    (root / "pipeline_output").mkdir(parents=True, exist_ok=True)
    state = {"project_folder": str(root)}
    state[_R._FORCE] = {
        "resolve_scripting": True,
        "face_detector": True,
        "reel_build_libraries": True,
    }
    (root / "pipeline_data.json").write_text(json.dumps(state), encoding="utf-8")
    path = transcript_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "segments": [], "segment_count": 0,
        "derived_from": {"duration_seconds": 0.0}}), encoding="utf-8")
    moment = ReelMoment(
        number=1, slug="a-witness", reason="a moment to build",
        timeline_start=10.0, timeline_end=40.0, approval=Approval.APPROVED)
    write_proposal(proposal_path(root), [moment],
                   {"derived_from": {"duration_seconds": 60.0}})
    (root / "project.yaml").write_text(
        "name: fixture\nresolve:\n"
        "  project_name: Fixture Project\n"
        "  timeline_name: Fixture Timeline\n", encoding="utf-8")
    return str(root)


def _args(project: str):
    return types.SimpleNamespace(
        project=project, skip_captions=False, only_reel=[],
        name_suffix="", allow_drop=[], supersede=[], retain=[],
        rebuild_all=False)


def test_the_loop_passes_a_ready_project_and_runs_what_follows_touchup(
        tmp_path, monkeypatch, capsys):
    """The regression: no refusal at the first caller-supplied op, and
    `reel.ask` plus `reel.verify` still run after it."""
    folder = _ready_project(tmp_path)
    ran = []

    build_mod = operations.load_step_module("step_7_01_build_reels")
    verify_mod = operations.load_step_module("step_7_02_verify_reels")
    monkeypatch.setattr(
        build_mod, "build_reels",
        lambda data: (ran.append("reel.build"), {
            "reel_build": {
                "timelines_built": ["Reel 01 - a-witness"],
                "plan_path": str(proposal_path(tmp_path))}})[1])
    monkeypatch.setattr(
        build_mod, "ask_reels",
        lambda data: (ran.append("reel.ask"), {"reel_asks": {}})[1])
    monkeypatch.setattr(
        verify_mod, "verify_reels",
        lambda data: (ran.append("reel.verify"),
                      {"reel_verification": {}})[1])

    manage_project.cmd_build_reels(_args(folder))  # exits on refusal

    # What follows the skipped ops still ran - named, not counted.
    assert "reel.ask" in ran
    assert "reel.verify" in ran
    # Nothing caller-supplied was driven: the loop is not their caller.
    reels = {c.id for c in capabilities.run_order(processes_mod.REELS)}
    caller_supplied = {op.name for op in operations.all()
                       if op.caller_supplied and op.name in reels}
    assert caller_supplied, "the registry names no caller-supplied reel op"
    assert not (set(ran) & caller_supplied)
    # The run recorded each runner-driven result under its capability.
    state = json.loads(
        (tmp_path / "pipeline_data.json").read_text(encoding="utf-8"))
    assert "reel_build" in capability_outputs.read(state, "reel.build")
    assert "reel_verification" in capability_outputs.read(
        state, "reel.verify")
    out = capsys.readouterr().err
    assert "reel.touchup: skipped (caller-supplied)" in out


def test_the_skipped_op_would_still_refuse_without_a_caller(tmp_path):
    """The skip is load-bearing: driven as the old loop drove it -
    with only the loop's own arguments - `reel.touchup` refuses with
    `spec` unbound while its requirements are satisfied."""
    folder = _ready_project(tmp_path)
    op = operations.get("reel.touchup")
    assert op.unmet(folder) == []
    result = op.execute(
        folder, skip_captions=False, only_reels=None,
        timeline_name_suffix="", allow_drops=None, supersede=None,
        retain=None, rebuild_all=False)
    assert result.refused
    assert result.unsatisfied == ()
    assert op.unbound_parameters(
        op._arguments(op.gather(folder), None)) == ("spec",)


# --------------------------------------------------------------------------
# From test_reels_build_witnesses.py
#
# The reels build consults what `edit_video` already consults - print-only,
# never a gate: `report_layer_coherence`, and `sweep_all_reels_informational`
# beside the scoped refusing gate. Why: docs/evidence/reel_build_witnesses.md.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))



# ── Fixtures ──────────────────────────────────────────────────────

def _clean_project(root: Path) -> str:
    """A project whose layers agree: a transcript, no corrections."""
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        json.dumps({"segments": []}), encoding="utf-8")
    return str(root)


def _stale_project(root: Path) -> str:
    """A project quoting `lucy` where the correction says `Lucie`."""
    folder = _clean_project(root)
    learned = root / "learned_context"
    learned.mkdir(parents=True)
    (learned / "learnings.json").write_text(json.dumps([{
        "id": "lc-0001", "kind": "correction", "status": "active",
        "source": {"correction_type": "transcript_spelling",
                   "heard": "lucy", "correct": "Lucie"},
    }]), encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text(
        json.dumps({"moments": [{
            "timeline_name": "Reel 01 - a",
            "call_to_action": {"text": "the lucy visibility system"},
        }]}), encoding="utf-8")
    return folder


def _sweep_project(root: Path) -> str:
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    transcript = scratch / "transcript.json"
    transcript.write_text(json.dumps({"segments": []}), encoding="utf-8")
    return str(root), str(transcript)


# ── layer_coherence on the reels build ────────────────────────────

def test_coherence_names_a_stale_layer_and_is_quiet_when_layers_agree(
        tmp_path, capsys):
    """Remove the witness and a stale layer builds in silence: a wording
    the source transcript no longer speaks is said before anything is
    placed. No owned divergences, no LAYER COHERENCE line."""
    report = rb.report_layer_coherence(_stale_project(tmp_path / "stale"))
    assert "unavailable" not in report
    assert any(row["should_be"] == "Lucie" for row in report["wording"])

    capsys.readouterr()
    report = rb.report_layer_coherence(_clean_project(tmp_path / "clean"))
    owned = sum(len(report.get(key, [])) for key in
                ("wording", "pins", "assets"))
    assert owned == 0
    assert "LAYER COHERENCE" not in capsys.readouterr().err


def test_coherence_reports_rather_than_raises_when_it_cannot_run(tmp_path):
    """A witness that raised would hold the build hostage to itself."""
    with patch("library.tools.layer_coherence.check_project",
               side_effect=RuntimeError("no transcript module")):
        report = rb.report_layer_coherence(str(tmp_path))

    assert report == {"unavailable": "no transcript module"}


# ── The whole-project sweep beside the scoped gate ────────────────

def test_the_sweep_grades_every_reel_in_build_units_beside_the_gate(
        tmp_path):
    """Remove the sweep and nothing ever grades the untouched reels: it
    reaches the verifier with NO scope, reads Pan/Tilt in the build's
    calibration (F12), and writes its own report - the gate's
    `conformance_report.json` is the refusal's evidence and stays
    byte-identical."""
    folder, transcript = _sweep_project(tmp_path)
    review = Path(folder) / "pipeline_output" / "review"
    gate_report = review / "conformance_report.json"
    gate_report.write_text('{"gate": "evidence"}', encoding="utf-8")

    with patch("library.tools.reel_conformance_verifier.run_verification",
               return_value=0) as run:
        result = rb.sweep_all_reels_informational(
            project_folder=folder, resolve_project_name="Mock",
            master_timeline_name="Master", plan_path="/x/plan.json",
            transcript_path=transcript, draw_gain=4.0)

    assert run.call_count == 1
    assert run.call_args[1]["only_reels"] is None
    assert run.call_args[1]["draw_gain"] == 4.0
    assert result["exit_code"] == 0
    assert result["report"].endswith("conformance_sweep_report.json")
    assert run.call_args[1]["json_path"] == result["report"]
    assert gate_report.read_text(encoding="utf-8") == '{"gate": "evidence"}'


def test_the_sweep_reports_findings_but_never_refuses(tmp_path):
    """Findings on reels this build did not touch are news, not a gate.

    A sweep that raised on exit 1 would be the whole-project refusal
    PR #658 removed, wearing a new name.
    """
    folder, transcript = _sweep_project(tmp_path)

    with patch("library.tools.reel_conformance_verifier.run_verification",
               return_value=1):
        result = rb.sweep_all_reels_informational(
            project_folder=folder, resolve_project_name="Mock",
            master_timeline_name="Master", plan_path="/x/plan.json",
            transcript_path=transcript)
    assert result["exit_code"] == 1

    with patch("library.tools.reel_conformance_verifier.run_verification",
               side_effect=RuntimeError("Resolve closed")):
        result = rb.sweep_all_reels_informational(
            project_folder=folder, resolve_project_name="Mock",
            master_timeline_name="Master", plan_path="/x/plan.json",
            transcript_path=transcript)
    assert result == {"unavailable": "Resolve closed"}


# --------------------------------------------------------------------------
# From test_reel_build_environment.py
#
# What a reel build needs in its interpreter is DECLARED, and checked first.
#
# `library/tools/shared_environment.py` (the BUILD half) is the one owner;
# `env.face_detector` and `env.reel_build_libraries` refuse BEFORE the build
# starts, naming what is missing and what would supply it. The cv2 in each
# case is a fake in `sys.modules`. History: docs/evidence/reel_build.md.

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from library.tools import requirements as R  # noqa: E402
from library.tools import shared_environment as se  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


# ── fakes: every machine shape the row actually met ──────────────────

def _no_cv2(monkeypatch):
    """No cv2 at all: `import cv2` raises, as on a stock interpreter."""
    monkeypatch.setitem(sys.modules, "cv2", None)


def _cv2_without_classifier(monkeypatch, version="5.0.0"):
    """cv2 5.x: present, but Haar cascades are gone entirely."""
    fake = types.ModuleType("cv2")
    fake.__version__ = version
    monkeypatch.setitem(sys.modules, "cv2", fake)
    return fake


def _cv2_with_classifier(monkeypatch, tmp_path, *, loads=True,
                         version="4.14.0"):
    """A cv2 whose cascade file exists, loading or not on demand."""
    cascade_dir = tmp_path / "haarcascades"
    cascade_dir.mkdir(parents=True, exist_ok=True)
    (cascade_dir / se.FACE_CASCADE_FILENAME).write_text(
        "<opencv_storage/>", encoding="utf-8")

    class FakeCascade:
        def __init__(self, path):
            self.path = path

        def empty(self):
            return not loads

    fake = types.ModuleType("cv2")
    fake.__version__ = version
    fake.CascadeClassifier = FakeCascade
    fake.data = types.SimpleNamespace(haarcascades=str(cascade_dir))
    monkeypatch.setitem(sys.modules, "cv2", fake)
    return fake


def _requirement(name):
    return next(r for r in R.registry() if r.name == name)


# ── gap 1: the cv2 Haar cascades ─────────────────────────────────────

def test_an_absent_declaration_fails_the_pre_build_check_by_name(monkeypatch):
    """Both surviving gaps: absent detector, and a missing library.

    A declared requirement that is absent fails the pre-build check BY
    NAME - `missing` carries the requirement, not a downstream symptom.
    """
    _no_cv2(monkeypatch)

    assert se.face_detector_usable() is False
    with pytest.raises(se.FaceDetectorMissing) as raised:
        se.require_face_detector()
    assert se.FACE_DETECTOR_PIN in str(raised.value)

    requirement = _requirement("env.face_detector")
    verdict = requirement.check(R.Context())
    assert verdict.is_unsatisfied
    assert verdict.missing == "env.face_detector"

    # The second gap: a venv built for one thing, missing another (the
    # row's instance was `jsonschema` absent from a cv2 4.12 venv).
    monkeypatch.setattr(se, "REEL_BUILD_LIBRARIES", ("no_such_module_xyz",))
    assert se.missing_build_libraries() == ("no_such_module_xyz",)
    assert se.build_libraries_present() is False
    verdict = _requirement("env.reel_build_libraries").check(R.Context())
    assert verdict.is_unsatisfied
    assert verdict.missing == "env.reel_build_libraries"
    assert "no_such_module_xyz" in verdict.reason
    assert se.REQUIREMENTS_FILE in verdict.reason


def test_each_detector_shape_names_its_own_missing_half(
        monkeypatch, tmp_path):
    """"No face detector" is four different facts with four remedies.

    The message must say WHICH half is gone - no cv2, no classifier,
    no cascade directory, an unloadable file - because "install
    opencv" is the local fix that produced four different ad-hoc
    venvs.
    """
    _no_cv2(monkeypatch)
    assert "not installed" in se.face_detector_missing_message()

    _cv2_without_classifier(monkeypatch)
    message = se.face_detector_missing_message()
    assert "5.0.0" in message and "CascadeClassifier" in message

    _cv2_with_classifier(monkeypatch, tmp_path, loads=False)
    assert "fails to load" in se.face_detector_missing_message()

    for message in (
            se.face_detector_missing_message(),
            se.reel_build_environment_missing_message()):
        assert se.FACE_DETECTOR_PIN in message
        assert se.BUILD_VENV_DOC in message


# --------------------------------------------------------------------------
# From test_reel_build_lease_shape.py
#
# The reel build holds the placement, not the build.
#
# `rebuild_reels_in_project` used to hold the machine-wide EXCLUSIVE
# Resolve lease for its entire body - minutes of Remotion caption
# renders and model-answer reads - while the cursor writes that lease
# exists to protect take seconds per reel. Reel throughput was capped
# at one build at a time however many lanes ran.
#
# The shape pinned here: derivation holds NOTHING, one exclusive hold
# per reel covers the carried self-read and the decision, the placement
# plan is prepared holding nothing, a second exclusive hold covers the
# placement and the Fusion comp pass, and handle-only gate/survey reads
# run under shared holds. Cursor-moving drift and digest reads take
# exclusive holds. A test that only asserts "the build takes
# a lease" would pass on both shapes; these fail on the old one.
#
# The failure mode the narrower hold must still survive is
# demonstrated deliberately below: a writer that never takes the lease
# (the captain editing by hand) moving the cursor mid-placement. The
# per-write check re-establishes the cursor before every append, so
# the placement lands where it was aimed with or without a whole-build
# hold - and the control shows what the same interleaving does to
# writes without the check (2026-09-04: 579 captions onto the wrong
# reel while every call returned True).

MASTER = "GEO Podcast - Synced"
ORGANISE = False


@pytest.fixture
def mock_dvr(stub_resolve_script, monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)
    install_measured_draw_gain_probe(monkeypatch)
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


@pytest.mark.usefixtures("mock_dvr")
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


@pytest.mark.usefixtures("mock_dvr")
def test_placement_holds_one_exclusive_hold(built_with_spies):
    """The seconds serialise: the placer runs under exactly one hold,
    and it is exclusive."""
    places = [e for e in built_with_spies["events"]
              if e[0] == "place-call"]
    assert len(places) == 1
    assert _holds_open(places[0]) == [
        ("place Reel 01 (rebuild staging)", True)]


@pytest.mark.usefixtures("mock_dvr")
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


@pytest.mark.usefixtures("mock_dvr")
def test_the_gate_reads_under_a_shared_hold(built_with_spies):
    """The gate must grade a stable staging, and several such reads
    run together while no writer runs - so shared, never exclusive."""
    verifies = [e for e in built_with_spies["events"]
                if e[0] == "verify-call"]
    assert len(verifies) == 2  # the scoped gate, then the sweep
    assert _holds_open(verifies[0]) == [("verify built reels", False)]
    assert _holds_open(verifies[1]) == [("sweep all reels", False)]


@pytest.mark.usefixtures("mock_dvr")
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


@pytest.mark.usefixtures("mock_dvr")
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


@pytest.mark.usefixtures("mock_dvr")
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


# --------------------------------------------------------------------------
# From test_reel_build_leaves_unchanged_reels_alone.py
#
# A build does not pay a Resolve pass for a reel nothing changed about.
#
# The cost, MEASURED and already in the tree (`docs/RULE_EVIDENCE.md`,
# "what it costs"): one reel's Resolve pass is 19.4-67.1 s, of which the
# Fusion comp pass is 17.0-63.7 s and is FIXED overhead rather than
# per-comp work.  `rebuild_reels_in_project` used to place every reel it
# was asked for regardless, so a build of five reels where one changed
# re-placed four identical timelines.
#
# What these tests hold is the ROUND TRIP, not the digest arithmetic
# (`tests/unit/reels/test_reel_rebuild_need.py` holds that): build once, and the
# second build of the same state leaves every reel alone - while a reel
# whose live timeline drifted, or whose engine or plan moved, is placed
# again.  A round trip is the only shape that proves the recorded
# signature and the recomputed one are the SAME computation; two
# hand-written digests would agree by construction and prove nothing.

APPROVED = [f"Reel {n:02d} - moment-{n}" for n in (1, 2, 3)]


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
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def _moment_2(number, name):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


class FakeClip:
    """One placed item, as `carried_digest` reads it."""

    def __init__(self, pan=0.0):
        self.resolve_item_id = "id"
        self.track_type = "video"
        self.track_index = 1
        self.track_name = "Craig"
        self.speaker = "Craig"
        self.source_file = "/f/a.mov"
        self.source_in_frame = 0
        self.source_out_frame = 100
        self.source_frames = 1000
        self.timeline_start = 0.0
        self.timeline_end = 4.17
        self.name = "a.mov"
        self.transform = {"Pan": pan}
        # `placements` works in SECONDS off the master's own clips, so a
        # fake that carries only frames dies inside it - the same
        # PLAYED-versus-SOURCE distinction `TimelineClip` documents.
        self.source_in = 0.0
        self.source_out = 4.17

    @property
    def duration(self):
        return self.timeline_end - self.timeline_start


class FakeSnapshot:
    def __init__(self, name, clips):
        self.clips = tuple(clips)
        self.timeline_name = name
        self.project_name = "Mock Project"
        self.fps = 24000 / 1001
        self.reported_fps = 23.976
        self.width = 1080
        self.height = 1920
        self.start_frame = 0
        self.end_frame = 100


class World:
    """What each timeline CARRIES, so a test can move one reel's
    picture and nothing else.

    A transform read off a timeline that is NOT the current one comes
    back wrong.  That is Resolve's real behaviour, measured 2026-09-12
    (`reel_rebuild_need.carried_digest_live`): three untouched reels
    read four times, under four different current timelines, gave four
    different digests.  Modelled here with a single wrong term rather
    than the real four-way table, because what the build has to be
    immune to is the DEPENDENCE, not its exact shape.
    """

    def __init__(self, project=None):
        self.pan = {}
        self.project = project

    def snapshot(self, timeline, project_name):
        name = timeline.GetName()
        pan = self.pan.get(_final_of(name), 0.0)
        if self.project is not None:
            current = self.project.GetCurrentTimeline()
            if current is not None and current is not timeline:
                pan += MISREAD_WHEN_NOT_CURRENT
        return FakeSnapshot(name, [FakeClip(pan)])


MISREAD_WHEN_NOT_CURRENT = 1.0
"""How far a transform read off a non-current timeline lands out."""


def _final_of(name):
    """The approved name behind a container, staging suffix stripped."""
    from library.tools.reel_build import STAGING_SUFFIX
    return (name[: -len(STAGING_SUFFIX)]
            if name.endswith(STAGING_SUFFIX) else name)


@contextmanager
def _patched(resolve_project, world, moments=None):
    def _place(**kwargs):
        name = kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place) as placed, \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale."
                  "scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=moments or [_moment_2(i + 1, name)
                                           for i, name
                                           in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=world.snapshot), \
            patch("library.tools.reel_conformance_verifier."
                  "run_verification", return_value=0):
        yield placed


def _build(project_dir, world, **kwargs):
    resolve_project = kwargs.pop("resolve_project")
    with _patched(resolve_project, world) as placed:
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, placed


# ── The round trip ───────────────────────────────────────────────────

@pytest.mark.usefixtures("mock_dvr")
def test_the_first_build_places_everything_and_records_its_signature(
        project):
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    record, placed = _build(project, world,
                            resolve_project=resolve_project)

    assert sorted(record["timelines_built"]) == sorted(APPROVED)
    assert record["reels_left_alone"] == []
    assert placed.call_count == 3
    # Every decision says REBUILD, and every one says WHY.
    assert {d["action"] for d in record["rebuild_need"]} == {"rebuild"}
    assert all(d["reason"] for d in record["rebuild_need"])

    signatures = read_provenance(
        str(project / "pipeline_output" / "review"))["build_signatures"]
    assert sorted(signatures) == sorted(APPROVED)
    for entry in signatures.values():
        # BOTH halves - the derivation from the build and the carried
        # digest closed by the promotion.
        assert len(entry["derivation"]) == 64
        assert len(entry["carried"]) == 64


@pytest.mark.usefixtures("mock_dvr")
def test_the_second_build_of_the_same_state_places_nothing(project):
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)
    before = sorted(resolve_project.names())

    record, placed = _build(project, world,
                            resolve_project=resolve_project)

    assert placed.call_count == 0, (
        "the second build placed a reel nothing changed about - the "
        "Resolve pass it was meant to save is the whole point")
    assert record["timelines_built"] == []
    assert sorted(record["reels_left_alone"]) == sorted(APPROVED)
    assert {d["action"] for d in record["rebuild_need"]} == {"leave_alone"}
    # Nothing was placed, nothing was renamed, nothing was deleted.
    assert sorted(resolve_project.names()) == before


@pytest.mark.usefixtures("mock_dvr")
def test_the_gate_is_not_called_with_an_empty_scope(project):
    """`verify_built_reels` refuses a scope of zero reels on purpose -
    a gate that passes having graded nothing reads as coverage. A build
    that placed nothing because nothing needed placing must not reach
    it."""
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)
    with _patched(resolve_project, world), \
            patch("library.tools.reel_build.verify_built_reels") as gate:
        rebuild_reels_in_project(str(project), organise=False)
    gate.assert_not_called()


@pytest.mark.usefixtures("mock_dvr")
def test_only_the_reel_whose_picture_drifted_is_placed_again(project):
    """One reel of three - the realistic build the profile priced."""
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    world.pan[APPROVED[1]] = 137.0            # a hand edit on one reel
    record, placed = _build(project, world,
                            resolve_project=resolve_project)

    assert record["timelines_built"] == [APPROVED[1]]
    assert sorted(record["reels_left_alone"]) == sorted(
        [APPROVED[0], APPROVED[2]])
    assert placed.call_count == 1
    drifted = [d for d in record["rebuild_need"]
               if d["reel"] == APPROVED[1]][0]
    assert "drifted" in drifted["reason"]


@pytest.mark.usefixtures("mock_dvr")
def test_a_changed_plan_places_every_reel_again(project):
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    plan = project / "pipeline_output" / "review" / "reel_proposals_v2.json"
    plan.write_text(json.dumps([{"changed": True}]), encoding="utf-8")

    record, placed = _build(project, world,
                            resolve_project=resolve_project)
    assert placed.call_count == 3
    assert record["reels_left_alone"] == []


@pytest.mark.usefixtures("mock_dvr")
def test_a_per_reel_pin_store_does_not_place_the_other_reels(project):
    """A pin on one reel must cost one reel.

    The file is written with a pin scoped to a speaker this project
    has no caption for, so no reel's derivation changes and every one
    is still left alone - which is the half that proves the store is
    not being folded into the project-wide digest.
    """
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    external = project / "external"
    external.mkdir(exist_ok=True)
    (external / "caption_timing.json").write_text(json.dumps({
        "version": 1,
        "pins": [{"scope": {"speaker": "Nobody"},
                  "offset_frames": 3,
                  "reason": "a pin this project has no card for"}]}),
        encoding="utf-8")

    record, placed = _build(project, world,
                            resolve_project=resolve_project)
    assert placed.call_count == 0
    assert sorted(record["reels_left_alone"]) == sorted(APPROVED)


@pytest.mark.usefixtures("mock_dvr")
def test_a_reel_reads_the_same_however_the_run_entered(project):
    """Whichever timeline a run enters on, the same reels are skipped.

    A transform does not read back the same way twice: what Resolve
    returns depends on which timeline is CURRENT at the moment of the
    read (measured 2026-09-12 - three untouched reels, four current
    timelines, four digests: `reel_rebuild_need.carried_digest_live`).
    Promotion closed every record with the LAST promoted reel current
    and the next build compared them with the ENTRY timeline current,
    so every reel but a coincidentally-matching one read as drifted and
    was placed again - the fail-closed direction, and exactly the
    saving the decision exists for.

    So the read is taken with the reel ITSELF current, at both ends,
    and the run's entry point stops mattering.
    """
    resolve_project = FakeProject([MASTER])
    world = World(resolve_project)
    _build(project, world, resolve_project=resolve_project)

    for entry in list(resolve_project.timelines):
        resolve_project.SetCurrentTimeline(entry)
        record, placed = _build(project, world,
                                resolve_project=resolve_project)
        assert placed.call_count == 0, (
            f"entering on {entry.GetName()!r} placed a reel nothing "
            f"changed about")
        assert sorted(record["reels_left_alone"]) == sorted(APPROVED)


# --------------------------------------------------------------------------
# From test_reel_phase_log.py
#
# The lane writes a per-reel phase log, so a stall has a recorded cause.
#
# Each event carries its OWN timestamp taken at the moment, never inferred
# from file times. Runs on `tmp_path` only, no Resolve, no model. History
# (the M05 85-minute stall): docs/evidence/reel_phase_log.md.

def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    return str(project)


def _at(event):
    return datetime.datetime.fromisoformat(event["at"])


def test_events_carry_their_own_timestamps_in_filed_order(tmp_path):
    project = _project(tmp_path)
    first = phase_log.log_event(project, 5, "Reel 05", phase_log.PLAN_ASKED,
                                detail="semantic ask written")
    second = phase_log.log_event(project, 5, "Reel 05",
                                 phase_log.ANSWERS_ARRIVED,
                                 detail="semantic=planned span=planned")
    events = phase_log.read_events(project)
    assert [e["phase"] for e in events] == [
        phase_log.PLAN_ASKED, phase_log.ANSWERS_ARRIVED]
    # Own timestamps: parseable ISO, taken at the call, monotonic.
    assert _at(second) >= _at(first)
    for event in events:
        assert event["format"] == phase_log.FORMAT
        assert event["reel_number"] == 5
        assert event["reel"] == "Reel 05"


def test_latest_event_filters_to_one_plan_channel(tmp_path):
    project = _project(tmp_path)
    motion = phase_log.log_event(
        project, 5, "Reel 05", phase_log.PLAN_ASKED,
        detail="motion ask written: reel_motion_05.json")
    semantic = phase_log.log_event(
        project, 5, "Reel 05", phase_log.PLAN_ASKED,
        detail="semantic ask written: reel_semantic_05.json")

    selected = phase_log.latest_event(
        project, 5, phase_log.PLAN_ASKED,
        detail_prefix="motion ask written: reel_motion_05.json")

    assert selected == motion
    assert selected != semantic


def test_the_log_holds_only_well_formed_events(tmp_path):
    """An unknown phase is refused, not filed; malformed lines on disk
    do not take the log down."""
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        phase_log.log_event(project, 5, "Reel 05", "vibes",
                            detail="not a phase")
    assert phase_log.read_events(project) == []
    phase_log.log_event(project, 5, "Reel 05", phase_log.PLAN_ASKED)
    path = os.path.join(project, "pipeline_output", "review",
                        phase_log.FILENAME)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
        handle.write(json.dumps({"no": "phase here"}) + "\n")
    assert len(phase_log.read_events(project)) == 1


def test_an_unanswered_span_logs_its_ask_and_its_wait(tmp_path):
    """The waiter (`span_record_for_build`) files the ask line with its
    own timestamp - the request FILE's mtime is the last rewrite and must
    never be read as the ask - and the wait line, so a missing answer is
    never a silence."""
    project = _project(tmp_path)

    class Moment:
        number = 9
        timeline_name = "Reel 09"

    transcript = {"segments": [{
        "words": [
            {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
            {"word": "mind", "start": 12.5, "end": 13.0, "timed": True},
        ]}]}
    before = datetime.datetime.now(datetime.timezone.utc)
    record = sem_vis.span_record_for_build(
        Moment(), transcript, [(10.0, 14.0)], project, fps=30.0,
        timeline_name="Reel 09")
    assert record["basis"] == sem_vis.SPAN_NOT_PLANNED
    events = phase_log.read_events(project)
    asks = [e for e in events if e["phase"] == phase_log.PLAN_ASKED]
    assert len(asks) == 1
    assert "span" in asks[0]["detail"]
    assert _at(asks[0]) >= before
    assert _at(asks[0]) <= datetime.datetime.now(datetime.timezone.utc)
    assert phase_log.WAIT in [e["phase"] for e in events]


def test_build_summary_absent_rather_than_estimated():
    """No pieces, no fiction: every field the caller could not supply
    is None or empty, never a zero that reads as measured."""
    payload = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_LEFT_ALONE,
        staging="Reel 05 (staging)", final="Reel 05 - slug",
        decision="leaving alone: digests match")
    assert payload["answers"] == {"semantic": None, "span": None,
                                  "motion": None}
    assert payload["dropped"] == {"semantic": [], "span": [],
                                  "motion": []}
    assert payload["draw_gain"] == {"gain": None, "source": None,
                                    "disagrees_with_fallback": None}
    assert payload["captions"] == {"planned": None, "linked": None,
                                   "link_warnings": None}
    assert payload["verify"] is None
    assert payload["drift_end"] is None
    assert payload["retired_to"] is None
    assert "placement_profile" not in payload
    assert payload["cards"] == []
    assert payload["mic_bleed_audio_suppressions"] == []
    assert payload["answers_owed"] == []


def test_placement_profile_round_trips_through_build_summary(tmp_path):
    project = _project(tmp_path)
    profile = {"timeline_import_s": 1.2,
               "exclusive_context_wall_s": 4.5,
               "counts": {"recorded_items": 24}}
    payload = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_PROMOTED, placement_profile=profile)

    phase_log.file_build_summary(project, 5, "Reel 05", payload)
    slot = phase_log.summarize(phase_log.read_events(project))["Reel 05"]

    assert slot["build_summary"]["placement_profile"] == profile


def test_drops_cap_bounds_a_pathological_line():
    record = {"basis": "planned",
              "dropped": [{"element": f"e{i}", "reason": "r"}
                          for i in range(30)]}
    drops = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_PROMOTED)["dropped"]
    assert drops["semantic"] == []
    payload = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_PROMOTED,
        semantic_record=record)["dropped"]["semantic"]
    assert len(payload) == phase_log.MAX_DROPS_PER_CHANNEL + 1
    assert payload[-1] == "(+10 more)"


def test_filing_never_fails_the_build(tmp_path):
    """No project folder, an unwriteable project and a garbage payload
    all return unfiled lines instead of raising."""
    assert phase_log.log_event(
        "", 5, "Reel 05", phase_log.PLAN_ASKED).get("unfiled") is True
    project = _project(tmp_path)
    review = os.path.join(project, "pipeline_output", "review")
    os.rmdir(review)
    with open(review, "w", encoding="utf-8") as handle:
        handle.write("a file where the review dir belongs")
    event = phase_log.file_build_summary(
        project, 5, "Reel 05", {"outcome": "promoted"})
    assert event.get("unfiled") is True
    event = phase_log.file_build_summary(
        project, 5, "Reel 05", "not a dict")  # type: ignore[arg-type]
    assert event.get("unfiled") is True


def test_summarize_surfaces_the_summary_on_both_names():
    """Staging and final are two slots for one reel; the summary filed
    under the final name attaches to both, by reel number."""
    base = datetime.datetime(2026, 9, 18, 12, 0, 0,
                             tzinfo=datetime.timezone.utc)

    def at(minutes_after):
        return (base + datetime.timedelta(
            minutes=minutes_after)).isoformat()

    def line(reel, phase, minute, summary=None):
        event = {"format": phase_log.FORMAT, "reel_number": 5,
                 "reel": reel, "phase": phase, "at": at(minute),
                 "detail": ""}
        if summary is not None:
            event["summary"] = summary
        return event

    first = {"outcome": "promoted", "final": "Reel 05 - slug"}
    second = {"outcome": "promoted", "final": "Reel 05 - slug",
              "verify": {"passed": True, "errors": 0}}
    events = [
        line("Reel 05 (staging)", phase_log.ANSWERS_ARRIVED, 0),
        line("Reel 05 (staging)", phase_log.BUILD_FINISHED, 8,
             summary=None),
        line("Reel 05 - slug", phase_log.BUILD_SUMMARY, 9,
             summary=first),
        line("Reel 05 - slug", phase_log.BUILD_SUMMARY, 10,
             summary=second),
        line("Reel 05 - slug", phase_log.CONSOLIDATED, 11),
    ]
    summary = phase_log.summarize(events)
    # Latest filed wins, on every slot sharing the number.
    assert summary["Reel 05 (staging)"]["build_summary"] == second
    assert summary["Reel 05 - slug"]["build_summary"] == second
    assert summary["Reel 05 - slug"]["build_summary_at"] == at(10)


def test_conformance_rows_slice_the_report_the_gate_wrote(tmp_path):
    project = _project(tmp_path)
    assert phase_log.conformance_rows(project) == {}
    report = {
        "reels": [{
            "reel_name": "Reel 05 (staging)",
            "reel_number": 5,
            "plan_seconds": 84.3,
            "actual_frames": 2016,
            "captions": "57/57",
            "uncaptioned_seconds": 0.0,
            "errors": 0, "warnings": 1,
            "findings": [{"finding_class": "F7",
                          "reel": "Reel 05 (staging)",
                          "message": "m", "severity": "warning"}],
        }],
    }
    path = os.path.join(project, "pipeline_output", "review",
                        phase_log.CONFORMANCE_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)
    rows = phase_log.conformance_rows(project)
    assert rows["Reel 05 (staging)"] == {
        "errors": 0, "warnings": 1, "finding_classes": ["F7"],
        "captions_expected": 57, "captions_actual": 57,
        "uncaptioned_seconds": 0.0, "plan_seconds": 84.3,
        "actual_frames": 2016}
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("not json")
    assert phase_log.conformance_rows(project) == {}


def test_build_side_helpers_shape_slices_and_file(tmp_path):
    """The rebuild's own helpers, without Resolve: the verify slice
    keeps the verdict when the report is unreadable, the owed lookup
    tries every spelling, and the filing helper never raises."""
    from library.tools import reel_build

    row = {"errors": 0, "warnings": 2, "finding_classes": ["F7"],
           "captions_expected": 9, "captions_actual": 9,
           "uncaptioned_seconds": 0.4, "plan_seconds": 29.6,
           "actual_frames": 707}
    payload = reel_build._verify_payload(row, passed=True)
    assert payload["passed"] is True
    assert payload["finding_classes"] == ["F7"]
    assert payload["report"] == phase_log.CONFORMANCE_REPORT_REL
    assert payload["refusal"] == ""
    blind = reel_build._verify_payload(None, passed=False,
                                       refusal="F17+F8")
    assert blind["errors"] is None
    assert blind["refusal"] == "F17+F8"

    awaiting = {"reels": [
        {"reel": "Reel 05 (staging)", "layers": ["motion"]}]}
    assert reel_build._owed_layers(
        awaiting, "Reel 05 - slug", "Reel 05 (staging)") == ["motion"]
    assert reel_build._owed_layers(awaiting, "Reel 99") == []

    project = _project(tmp_path)
    facts = {"staging": "Reel 05 (staging)", "final": "Reel 05 - slug",
             "number": 5,
             "decision": {"reason": "rebuilt: the plan changed"},
             "answers": {"semantic": "planned", "span": "planned",
                         "motion": "awaiting_model_answer"}}
    reel_build._file_reel_summary(
        project, number=5, name="Reel 05 - slug", facts=facts,
        outcome="promoted", verify=payload,
        gain_record={"gain": 1.0, "source": "fallback",
                     "disagrees_with_fallback": False})
    slot = phase_log.summarize(
        phase_log.read_events(project))["Reel 05 - slug"]
    assert slot["build_summary"]["outcome"] == "promoted"
    assert slot["build_summary"]["draw_gain"]["source"] == "fallback"
    # Garbage facts still never raise.
    reel_build._file_reel_summary(
        project, number=5, name="Reel 05 - slug", facts=None,  # type: ignore[arg-type]
        outcome="promoted")


def test_lease_waits_add_up_to_a_contention_reading(tmp_path):
    """The reader answers the queue question either way it comes out.

    An uncontended placement still files (absent and zero differ), so
    near-zero contention reads as near-zero, not as missing data.
    """
    project = _project(tmp_path)
    event = phase_log.log_lease_wait(
        project, 1, "Reel 01", purpose="place Reel 01",
        wait_seconds=0.0, waited_on="")
    assert event["phase"] == phase_log.WAIT
    assert "uncontended" in event["detail"]
    assert event["summary"]["kind"] == phase_log.LEASE_WAIT_KIND
    assert event["summary"]["contended"] is False
    held = phase_log.log_lease_wait(
        project, 2, "Reel 02", purpose="place Reel 02",
        wait_seconds=2.5, waited_on="lane-7: place Reel 01")
    assert "2.5s" in held["detail"] and "lane-7" in held["detail"]
    assert held["summary"]["contended"] is True
    phase_log.log_lease_wait(project, 3, "Reel 03", purpose="place Reel 03",
                             wait_seconds=1.0,
                             waited_on="lane-7: place Reel 01")
    report = phase_log.summarize_lease_waits(
        phase_log.read_events(project))
    assert report["acquisitions"] == 3
    assert report["contended"] == 2
    assert report["fraction_contended"] == 0.667
    assert report["total_wait_seconds"] == 3.5
    assert report["mean_wait_seconds"] == 1.167
    assert report["max_wait_seconds"] == 2.5
    assert report["by_holder"]["lane-7: place Reel 01"] == {
        "acquisitions": 2, "total_wait_seconds": 3.5}
    assert phase_log.summarize_lease_waits([])["acquisitions"] == 0


def test_a_fully_cached_render_reads_as_cached_not_as_silence(tmp_path):
    """The line carries the cached/cold split, not just a wall (the
    3 s-vs-556 s gap), and a zero-fresh render still files: absent and
    cached differ."""
    project = _project(tmp_path)
    event = phase_log.log_cards_render(
        project, 5, "Reel 05", cards_total=2, cards_cached=2,
        cards_rendered=0, wall_seconds=0.4)
    assert event["phase"] == phase_log.CARDS_RENDERED
    assert event["summary"]["cards_cached"] == 2
    assert event["summary"]["cards_rendered"] == 0
    assert "2 cached" in event["detail"]
    phase_log.log_cards_render(
        project, 6, "Reel 06", cards_total=3, cards_cached=0,
        cards_rendered=3, wall_seconds=556.0)
    report = phase_log.summarize_cards_renders(
        phase_log.read_events(project))
    assert report["renders"] == 2
    assert report["cold_renders"] == 1
    assert report["fraction_cold"] == 0.5
    assert report["cards_total"] == 5
    assert report["cards_cached"] == 2
    assert report["cards_rendered"] == 3
    assert report["total_wall_seconds"] == 556.4
    assert report["max_wall_seconds"] == 556.0
    assert report["max_render"]["reel"] == "Reel 06"
    assert phase_log.summarize_cards_renders([])["renders"] == 0


def test_the_timed_wrapper_returns_the_renderer_output_untouched(tmp_path):
    """Timing only: the wrapper must hand back exactly what the renderer
    returned, and files nothing for an empty reel - a wrapper that reorders, drops or rebuilds cards is a
    content change wearing an instrument's clothes."""
    from library.tools import full_frame_element as cards_mod
    from library.tools import reel_build

    project = _project(tmp_path)
    planned = [
        {"render_name": "reel_05_head", "rendered_path": "/disk/head.mov"},
        {"render_name": "reel_05_tail", "rendered_path": ""},
    ]
    drawn = [dict(card, rendered_path=f"/disk/{card['render_name']}.mov")
             for card in planned]

    def fake_render(cards, remotion_dir, output_dir, project_folder=""):
        assert list(cards) == planned
        return drawn

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cards_mod, "render_reel_cards", fake_render)
    try:
        out = reel_build.render_reel_cards_timed(
            project, 5, "Reel 05", planned, "/remotion")
    finally:
        monkeypatch.undo()
    assert out == drawn
    assert out is drawn
    events = phase_log.read_events(project)
    assert len(events) == 1
    summary = events[0]["summary"]
    assert summary["cards_total"] == 2
    assert summary["cards_cached"] == 1
    assert summary["cards_rendered"] == 1
    assert summary["wall_seconds"] >= 0.0
    # No cards: no line, no wall - zeros for work that never happened
    # would be another silence.
    assert reel_build.render_reel_cards_timed(
        project, 6, "Reel 06", [], "/remotion") == []
    assert len(phase_log.read_events(project)) == 1


# --------------------------------------------------------------------------
# From test_reel_prebuild_census.py
#
# Measure before you build: the census runs first, prints, never refuses.
#
# Disagreement flags, unreadable and fresh reels degrade to notes, and the
# rebuild calls it on a multi-reel build. The 2026-09-11 round behind it:
# docs/evidence/reel_prebuild_census.md.

REELS = ["Reel 01 - hook (final)", "Reel 23 - pricing (final)",
         "Reel 30 - seam (final)", "Reel 31 - phrase (final)",
         "Reel 28 - closer (final)"]


def _clip(row, tilt, pan=0.0):
    return SimpleNamespace(track_type="video", track_name=row,
                           transform={"Pan": pan, "Tilt": tilt})


def _per_reel(tilts, row="Subtitles"):
    """Five reels' caption rows at the given median tilts."""
    return {final: {row: {"pan": 0.0, "tilt": tilt, "n": 4}}
            for final, tilt in zip(REELS, tilts)}


def test_the_round_s_numbers_disagree(capsys):
    """Four reels at -425..-436 against one at -870: flagged, by row."""
    report = census.compare_reel_rows(
        _per_reel([-425.0, -430.0, -433.0, -436.0, -870.0]))

    assert report["disagreements"] == ["Subtitles"]
    assert report["rows"]["Subtitles"]["disagree"] is True
    text = census.render_census(report, REELS)
    assert "DISAGREES" in text and "Subtitles" in text
    print(text)


def test_characteristics_read_the_median_of_caption_rows_only():
    """One odd card must not move a reel (the median over readable clips),
    and picture/b-roll rows are dropped before anything is compared - they
    legitimately differ per reel, even by 800 units."""
    clips = [_clip("Subtitles", -430.0), _clip("Subtitles", -432.0),
             _clip("Subtitles", -870.0)]
    assert census.row_characteristics(clips)["Subtitles"]["tilt"] == -432.0

    per_reel = {
        REELS[0]: census.row_characteristics(
            [_clip("Craig", -100.0), _clip("Craig", -102.0),
             _clip("B-Roll", 500.0)]),
        REELS[1]: census.row_characteristics(
            [_clip("Craig", -900.0), _clip("Craig", -902.0),
             _clip("B-Roll", -500.0)]),
    }
    report = census.compare_reel_rows(per_reel)
    assert report["disagreements"] == []
    assert report["rows"] == {}


def test_unreadable_and_fresh_reels_become_notes_not_refusals(capsys):
    """A timeline that will not read, and a final with no timeline
    yet, take no part in the comparison - and the build still gets
    its table."""
    good = SimpleNamespace(
        clips=[_clip("Subtitles", -430.0), _clip("Subtitles", -432.0)])

    class Project:
        def GetName(self):
            return "Mock Project"

        def GetTimelineCount(self):
            return 2

        def GetTimelineByIndex(self, index):
            return [SimpleNamespace(GetName=lambda: REELS[0]),
                    SimpleNamespace(GetName=lambda: REELS[1])][index - 1]

    def snapshot_fn(timeline, _project):
        if timeline.GetName() == REELS[0]:
            raise RuntimeError("Resolve is busy")
        return good

    report = census.report_prebuild(Project(), [REELS[0], REELS[1], REELS[2]],
                                    snapshot_fn=snapshot_fn)

    assert report["disagreements"] == []
    assert "could not be read" in report["notes"][REELS[0]]
    assert "no existing timeline" in report["notes"][REELS[2]]
    assert "census" in capsys.readouterr().out.lower()


# ── The wiring: the build path calls it, or it is prose ──────────

@pytest.fixture
def build_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        'resolve: {project_name: "Mock Project", '
        'timeline_name: "Master"}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def _moment_3(number, name):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


class _FakeTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        if not getattr(self, "_uid", None):
            type(self)._seq = getattr(type(self), "_seq", 0) + 1
            self._uid = f"{type(self).__name__}-{type(self)._seq}"
        return self._uid


class _FakeProject:
    def __init__(self, names):
        self.timelines = [_FakeTimeline(name) for name in names]
        pool = MagicMock()
        pool.CreateEmptyTimeline.side_effect = self._create
        pool.DeleteTimelines.side_effect = self._delete
        self._pool = pool

    def _create(self, name):
        timeline = _FakeTimeline(name)
        self.timelines.append(timeline)
        return timeline

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

    def GetMediaPool(self):
        return self._pool

    def GetName(self):
        return "Mock Project"

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    # A Resolve project HAS a cursor, and `resolve_lock`'s guard reads
    # it back by unique id - a fake without one cannot model the guard.
    def GetCurrentTimeline(self):
        # None until something sets it: a project that has not been
        # pointed anywhere has no cursor, and inventing one here would
        # hand the entry-unit guard a timeline nobody opened.
        return getattr(self, "_current", None)

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


def test_a_multi_reel_build_reports_before_placing(build_project,
                                                   monkeypatch):
    """The census fires on the build path whether or not anyone
    remembers: two reels build, the census is called with both finals
    before anything is placed."""
    from library.tools.reel_build import rebuild_reels_in_project
    install_measured_draw_gain_probe(monkeypatch)

    resolve_project = _FakeProject(["Master"])
    moments = [_moment_3(1, "Reel 01 - hook"),
               _moment_3(2, "Reel 02 - promise")]

    def _place(**place_kwargs):
        resolve_project.GetMediaPool().CreateEmptyTimeline(
            place_kwargs.get("timeline_name"))
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.reel_build._connect_resolve"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=moments), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_prebuild_census.report_prebuild",
                  return_value={"disagreements": []}) as prebuild:
        rebuild_reels_in_project(str(build_project), organise=False,
                                 verify=False)

    assert prebuild.call_count == 1
    _project, finals = prebuild.call_args[0][:2]
    assert finals == ["Reel 01 - hook", "Reel 02 - promise"]


# --------------------------------------------------------------------------
# From test_current_plan_means_the_plan.py
#
# Current plan means the PLAN, not the last build call.
#
# Measured 2026-09-18 on the captain's project: building Reel 02 alone
# filed eight accepted reels into Earlier plans. The plan hash had rotated
# under them, so `write_provenance` dropped every entry but the reel that
# build placed - and the organiser filed by the provenance record's
# `built_reels`, reading a partial build as a plan change.
#
# The plan source is the proposals file (`reel_proposals_v2.json`): it is
# what the builder builds from (`reel_build` reads it through
# `reel_proposal.read_proposal`) and what the captain rules on, while the
# provenance record only says which plan a build consumed. Filing by the
# plan keeps both properties below; filing by provenance cannot.

MASTER_2 = "Master Timeline"
CURRENT_BIN = (bins.REELS_BIN, bins.REEL_STATE_BINS[org.CURRENT])
EARLIER_BIN = (bins.REELS_BIN, bins.REEL_STATE_BINS[org.EARLIER])


def _name(number, slug):
    return reel_timeline_name(number, slug)


def _moment_4(number, slug, approval="approved"):
    return {
        "number": number,
        "slug": slug,
        "reason": "a moment the captain ruled on",
        "timeline_start": 10.0,
        "timeline_end": 40.0,
        "approval": approval,
    }


def _write_live_plan(review_dir, moments):
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "reel_proposals_v2.json").write_text(
        json.dumps({"format": "reel_proposal/1",
                    "moment_count": len(moments),
                    "moments": moments}),
        encoding="utf-8")


def _write_archive(review_dir, stamp, names):
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / f"reel_proposals_v2_{stamp}.json").write_text(
        json.dumps({"moments": [{"timeline_name": n} for n in names]}),
        encoding="utf-8")


def _write_provenance(review_dir, built, plan_hash="7" * 64):
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "plan_provenance.json").write_text(
        json.dumps({"plan_content_hash": plan_hash,
                    "built_at": "2026-09-18T01:08:46+00:00",
                    "built_reels": list(built)}),
        encoding="utf-8")


class _Clip:
    def __init__(self, name, uid, kind="Timeline"):
        self._name = name
        self._uid = uid
        self._kind = kind

    def GetUniqueId(self):
        return self._uid

    def GetName(self):
        return self._name

    def GetClipProperty(self, key):
        if key == "Type":
            return self._kind
        return ""

    def GetMetadata(self, key):
        return ""


class _Folder:
    def __init__(self, name, subs=(), clips=()):
        self._name = name
        self._subs = list(subs)
        self._clips = list(clips)

    def GetName(self):
        return self._name

    def GetSubFolderList(self):
        return list(self._subs)

    def GetClipList(self):
        return list(self._clips)


class _Timeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetTrackCount(self, kind):
        return 0


class _Pool:
    def __init__(self, root):
        self._root = root

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._root


class _Project:
    """A pool holding timeline clips in bins, and nothing else."""

    def __init__(self, placed):
        # placed: {timeline name: bin path tuple}; MASTER sits at root.
        self._timelines = [MASTER_2, *placed]
        clips_by_bin: dict[tuple, list] = {}
        for uid, name in enumerate(self._timelines):
            clips_by_bin.setdefault(
                placed.get(name, ()), []).append(_Clip(name, f"id-{uid}"))
        current = _Folder(
            "Current plan",
            clips=clips_by_bin.get(CURRENT_BIN, []))
        earlier = _Folder(
            "Earlier plans",
            clips=clips_by_bin.get(EARLIER_BIN, []))
        reels = _Folder(bins.REELS_BIN, subs=[current, earlier])
        self._root = _Folder("Master", subs=[reels],
                             clips=clips_by_bin.get((), []))

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        return _Pool(self._root)

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return _Timeline(self._timelines[index - 1])


NINE = [(1, "geo-is-comprehension-not-position"),
        (2, "keyword-stuffing-now-costs-you"),
        (9, "your-website-is-only-20-percent"),
        (13, "the-accounting-firm-ai-called-healthcare"),
        (23, "why-small-business-wins-on-ai"),
        (26, "write-for-the-question-your-customer-ask"),
        (28, "the-nail-salon-query-google-cant-answer"),
        (30, "your-google-business-profile-and-the-map"),
        (31, "is-there-a-way-to-game-ai")]


def _review(tmp_path):
    return tmp_path / "pipeline_output" / "review"


def test_building_one_reel_leaves_every_other_planned_reel_where_it_was(
        tmp_path):
    """The regression: nine approved, one placed, none moved.

    The provenance record names only the reel the last call placed (the
    plan hash rotated under the other eight, so the merge dropped them),
    while the archived plans still name all nine. Filing must follow the
    live plan - all nine stay Current, no timeline moves at all."""
    names = [_name(n, s) for n, s in NINE]
    placed = {n: CURRENT_BIN for n in names}
    review = _review(tmp_path)
    _write_live_plan(review, [_moment_4(n, s) for n, s in NINE])
    _write_archive(review, "20260917T000000Z", names)
    _write_provenance(review, [names[1]], plan_hash="2" * 64)

    assert current_plan_names(str(tmp_path), None) == set(names)

    plan, artefacts, _, _ = plan_for_project(
        _Project(placed), str(tmp_path), MASTER_2)
    states = {v.name: v.state for v in plan.verdicts
              if v.kind == "timeline"}
    assert states == {n: org.CURRENT for n in names}
    assert [v for v in plan.moves if v.kind == "timeline"] == []


def test_a_reel_the_plan_no_longer_names_still_demotes(tmp_path):
    """The intent that survives: a genuinely superseded reel moves to
    Earlier plans - moved and relabelled, never deleted."""
    dropped = _name(1, "geo-is-comprehension-not-position")
    kept = _name(2, "keyword-stuffing-now-costs-you")
    review = _review(tmp_path)
    _write_live_plan(review, [_moment_4(2, "keyword-stuffing-now-costs-you")])
    _write_archive(review, "20260917T000000Z", [dropped, kept])
    _write_provenance(review, [kept], plan_hash="2" * 64)

    plan, _, _, _ = plan_for_project(
        _Project({dropped: CURRENT_BIN, kept: CURRENT_BIN}),
        str(tmp_path), MASTER_2)
    verdicts = {v.name: v for v in plan.verdicts if v.kind == "timeline"}
    assert verdicts[dropped].state == org.EARLIER
    assert verdicts[dropped].destination == EARLIER_BIN
    assert verdicts[kept].state == org.CURRENT
    moves = {v.name: v for v in plan.moves if v.kind == "timeline"}
    assert set(moves) == {dropped}
    assert moves[dropped].destination == EARLIER_BIN
    stamps = {s["name"]: s for s in plan.stamps}
    assert stamps[dropped]["state"] == org.EARLIER
    # Moved and relabelled: the verdict and the stamp are the relabelling,
    # and the timeline is still filed somewhere - nothing deletes.
    assert dropped in verdicts


def test_only_approved_moments_are_current(tmp_path):
    """Proposed and rejected moments are the captain's undecided and
    refused - neither is the plan, so neither keeps a reel current."""
    review = _review(tmp_path)
    _write_live_plan(review, [
        _moment_4(1, "kept-slug", "approved"),
        _moment_4(2, "undecided-slug", "proposed"),
        _moment_4(3, "refused-slug", "rejected"),
    ])
    assert current_plan_names(str(tmp_path), None) == {_name(1, "kept-slug")}


def test_an_unreadable_plan_falls_back_to_provenance_instead_of_demoting(
        tmp_path, capsys):
    """No plan file is 'nothing here can say' - the filing keeps what the
    provenance record names instead of emptying Current plan. Said loudly,
    because a filing by a stale record is a guess being kept quiet."""
    kept = _name(2, "keyword-stuffing-now-costs-you")
    review = _review(tmp_path)
    review.mkdir(parents=True)
    _write_provenance(review, [kept], plan_hash="2" * 64)

    assert current_plan_names(
        str(tmp_path), {"built_reels": [kept]}) == {kept}
    assert "provenance" in capsys.readouterr().err

    plan, _, _, _ = plan_for_project(
        _Project({kept: CURRENT_BIN}), str(tmp_path), MASTER_2)
    assert {v.name: v.state for v in plan.verdicts
            if v.kind == "timeline"} == {kept: org.CURRENT}
    assert [v for v in plan.moves if v.kind == "timeline"] == []


def test_a_reel_the_captain_placed_by_hand_stays_where_they_put_it(
        tmp_path):
    """The existing rule survives the new source: a plan-named timeline
    in a bin outside the managed layout is where the captain put it."""
    kept = _name(2, "keyword-stuffing-now-costs-you")
    review = _review(tmp_path)
    _write_live_plan(review, [_moment_4(2, "keyword-stuffing-now-costs-you")])
    _write_provenance(review, [kept], plan_hash="2" * 64)

    project = _Project({})
    hand_bin = _Folder("Fully approved", clips=[_Clip(kept, "id-hand")])
    reels = next(s for s in project._root.GetSubFolderList()
                 if s.GetName() == bins.REELS_BIN)
    reels._subs.append(hand_bin)

    plan, _, _, _ = plan_for_project(project, str(tmp_path), MASTER_2)
    assert [v for v in plan.verdicts if v.name == kept] == []
    assert [v for v in plan.moves if v.name == kept] == []
    assert [s for s in plan.stamps if s["name"] == kept] == []
    assert kept in {name for name, _ in plan.left_alone}


# --------------------------------------------------------------------------
# From test_only_reel_concurrent_writes.py
#
# Single-reel lanes keep their own planning and verification records.
#
# All project data is under ``tmp_path``. The planning calls use the real
# ``write_reel_asks_for_project`` implementation with Resolve getters stubbed;
# the process tests exercise the same locked writers used by the real runner.

def _record_reel_lane(project: str, reel: int, barrier) -> None:
    """Process target for simultaneous pipeline state read/merge/writes."""
    from library.processes.reels.run_reels import record_project_output

    barrier.wait(timeout=20)
    name = f"Reel {reel:02d} - lane-{reel}"
    record_project_output(
        project, "reel.ask",
        {
            "reel_ask": {
                "reel_asks": [{"number": reel, "reel": name,
                               "request": f"ask-{reel}"},
                              {"number": 99, "reel": "Reel 99 - foreign",
                               "request": "must be filtered"}],
            },
        },
        only_reels=[reel],
    )
    record_project_output(
        project, "reel.build",
        {
            "reel_build": {
                "reels_requested": [reel],
                "timelines_built": [f"{name} (staging)",
                                    "Reel 99 - foreign (staging)"],
                "staged_timelines": {name: f"{name} (staging)"},
                "caption_hashes": {name: f"hash-{reel}",
                                   "Reel 99 - foreign": "must be filtered"},
                "track_plans": {name: {"source": reel},
                                "Reel 99 - foreign": {"source": 99}},
                "awaiting_model_answers": {
                    "reels": [{"reel": name, "layers": ["semantic"]}],
                    "outstanding_answers": 1,
                },
                "proof_scope": {"stills": [name]},
                "pending_promotions": [{
                    "staging": f"{name} (staging)", "awaiting": name}],
                "coherence_summary": {"status": "skipped"},
            },
        },
        only_reels=[reel],
    )


def _write_conformance_lane(path: str, reel: int, barrier) -> None:
    """Process target for simultaneous scoped report replacement."""
    from library.tools.reel_conformance_verifier import _write_scoped_report

    name = f"Reel {reel:02d} (staging)"
    row = {"reel_name": name, "reel_number": reel, "errors": 0,
           "warnings": 0, "findings": []}
    incoming = {
        "project": "fixture",
        "master_timeline": "Master",
        "summary": {"reels_checked": 1, "total_errors": 0,
                    "total_warnings": 0, "passed": True},
        "by_class": {},
        "reels": [row],
        "read_only_proof": {
            "all_identical": True,
            "timelines_checked": 2,
            "timelines": {
                "Master": {"identical": True},
                name: {"identical": True},
            },
        },
        "motion_graphics_tightness": {"measured": "lane-local overwrite"},
        "quality_bar": {
            "verdicts": [{"reel": reel, "verdict": "pass"}],
            "not_read": [], "judged": True,
        },
    }
    barrier.wait(timeout=20)
    _write_scoped_report(path, incoming, [name])


def _moment_5(number: int) -> ReelMoment:
    return ReelMoment(
        number=number,
        slug=f"lane-{number}",
        reason="a complete exchange",
        timeline_start=number * 10.0,
        timeline_end=number * 10.0 + 8.0,
        approval=Approval.APPROVED,
    )


def _ask_project(root: Path) -> tuple[str, list[ReelMoment]]:
    root.mkdir(parents=True)
    (root / "project.yaml").write_text(
        "name: fixture\nresolve:\n"
        "  project_name: Fixture\n"
        "  timeline_name: Master\n", encoding="utf-8")
    moments = [_moment_5(21), _moment_5(25)]
    write_proposal(proposal_path(root), moments, {"derived_from": {}})
    return str(root), moments


def test_concurrent_only_reel_asks_run_only_the_selected_reel(
        tmp_path, monkeypatch):
    """The real reel.ask step snaps, derives and asks for N only."""
    project, _moments = _ask_project(tmp_path / "project")
    monkeypatch.setattr(reel_build, "_connect_resolve_project",
                        lambda name: FakeProject("Fixture", ["Master"]))
    monkeypatch.setattr(
        "library.tools.timeline_ingest.snapshot_timeline",
        lambda timeline, project_name: SimpleNamespace(
            clips=[], fps=24000 / 1001))
    monkeypatch.setattr(reel_build, "reel_resolution",
                        lambda folder: (1080, 1920))
    monkeypatch.setattr(
        "library.tools.reel_look.resolve_look",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(reel_build, "declared_cards", lambda folder: {})
    monkeypatch.setattr(
        "library.tools.transcript_corrections.keep_exclusions",
        lambda folder: [])
    monkeypatch.setattr(
        "library.tools.transcript_corrections.keep_insistences",
        lambda folder: [])
    monkeypatch.setattr(
        reel_build, "moment_cuts_and_insistences",
        lambda *args, **kwargs: ([], []))

    snapped = []
    derived = []
    asked = []

    def snap(moment, transcript, **kwargs):
        snapped.append(int(moment.number))
        return moment, []

    def derive(moment, *args, **kwargs):
        derived.append(int(moment.number))
        time.sleep(0.01)
        return [], [], None

    def write(moment, *args, **kwargs):
        asked.append(int(moment.number))
        return {"reel_semantic": "semantic", "reel_span": "span",
                "reel_motion": "motion"}

    monkeypatch.setattr("library.tools.reel_proposal.snap_moment_to_speech",
                        snap)
    monkeypatch.setattr(reel_build, "derive_reel_ranges_and_cards", derive)
    monkeypatch.setattr(reel_build, "write_visual_asks", write)

    from library.tools import reel_ledger
    monkeypatch.setattr(reel_ledger, "stored_windows",
                        lambda moment: ((moment.timeline_start,
                                         moment.timeline_end), None))
    monkeypatch.setattr(reel_ledger, "audit_ranges", lambda **kwargs: {
        "ranges": [], "reel": kwargs["final"]})

    from library.tools.operations import load_step_module
    step = load_step_module("step_7_01_build_reels", "step.py")

    def plan(number):
        return step.ask_reels({
            "project_folder": project,
            "timeline_transcript": {"segments": []},
            "only_reels": [number],
        })["reel_ask"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(plan, (21, 25)))

    assert sorted(snapped) == [21, 25]
    assert sorted(derived) == [21, 25]
    assert sorted(asked) == [21, 25]
    assert [result["reel_asks"][0]["number"] for result in results] == [21, 25]
    ledger = json.loads(reel_ledger.ledger_path(project).read_text(
        encoding="utf-8"))
    assert set(ledger["reels"]) == {"Reel 21 - lane-21", "Reel 25 - lane-25"}


def test_single_reel_build_suppresses_sibling_override_notices(
        tmp_path, monkeypatch):
    from library.tools import captain_edits

    edit = {"kind": "transform_override", "anchor_phrase": "a phrase",
            "property": "Pan", "value": -5}
    sibling_edit = {**edit, "reel": "Reel 21 - sibling"}
    sibling = {**edit, "scope": "reel",
               "reason": "override belongs to Reel 21"}
    lost = {**edit, "scope": "transcript",
            "reason": "anchor no longer occurs"}
    notices = []
    lost_inputs = []
    matcher_edits = []
    monkeypatch.setattr(captain_edits, "load_edits",
                        lambda project: [sibling_edit, edit])

    def match(places, transcript, edits, **kwargs):
        matcher_edits.extend(edits)
        return [], [sibling, lost]

    monkeypatch.setattr(captain_edits, "match_transform_overrides", match)
    monkeypatch.setattr(captain_edits, "report_stale",
                        lambda rows: notices.append(list(rows)))

    def lost_overrides(rows):
        lost_inputs.append(list(rows))
        return [row for row in rows if row.get("scope") == "transcript"]

    monkeypatch.setattr(captain_edits, "lost_overrides", lost_overrides)
    reel_build.apply_transform_overrides(
        "Reel 25 - target", None, {}, [], None, {"segments": []},
        str(tmp_path), 1080, 1920, report_sibling_stale=False)

    assert matcher_edits == [edit]
    assert notices == [[lost]]
    assert lost_inputs == [[lost]]


def test_pending_notice_identifies_unowned_sibling_staging(tmp_path):
    from library.tools import staging_holds

    project = str(tmp_path / "project")
    staging_holds.take_hold(
        project, "Reel 21 - sibling (rebuild staging)",
        awaiting="Reel 21 - sibling",
        reason="other lane", taken_by="lane-21")
    staging_holds.take_hold(
        project, "Reel 25 - target (rebuild staging)",
        awaiting="Reel 25 - target",
        reason="this lane", taken_by="lane-25")

    notice = staging_holds.report_pending(
        project,
        owned_staging_names={"Reel 25 - target (rebuild staging)"})

    assert ("Reel 21 - sibling (rebuild staging)" in notice
            and "another reel's staging; this build does not own it"
            in notice)
    target_line = next(line for line in notice.splitlines()
                       if "Reel 25 - target (rebuild staging)" in line)
    assert "another reel's staging" not in target_line

    stale = staging_holds.report_pending(
        project, timeline_names=[],
        owned_staging_names={"Reel 25 - target (rebuild staging)"})
    stale_line = next(line for line in stale.splitlines()
                      if "Reel 21 - sibling (rebuild staging)" in line)
    assert "another reel's staging; this build does not own it" in stale_line


def test_concurrent_runner_records_preserve_each_reels_entries(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state_path = project / "pipeline_data.json"
    seed = {
        "project_folder": str(project),
        "capability_outputs": {
            "reel.ask": {
                "reel_ask": {"reel_asks": [
                    {"number": 3, "reel": "Reel 03", "request": "old"}]},
            },
            "reel.build": {
                "reel_build": {
                    "reels_requested": [3],
                    "timelines_built": ["Reel 03 (staging)"],
                    "staged_timelines": {"Reel 03": "Reel 03 (staging)"},
                    "caption_hashes": {"Reel 03": "hash-3"},
                    "track_plans": {"Reel 03": {"source": 3}},
                    "awaiting_model_answers": {
                        "reels": [{"reel": "Reel 03", "layers": ["span"]}],
                        "outstanding_answers": 1,
                    },
                    "proof_scope": {"stills": ["Reel 03"]},
                    "pending_promotions": [
                        {"staging": "Reel 03 (staging)",
                         "awaiting": "Reel 03"}],
                    "coherence_summary": {"status": "whole-project",
                                          "owned_total": 8},
                },
            },
        },
    }
    state_path.write_text(json.dumps(seed), encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    workers = [
        context.Process(target=_record_reel_lane,
                        args=(str(project), reel, barrier))
        for reel in (21, 25)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert [worker.exitcode for worker in workers] == [0, 0]

    stored = json.loads(state_path.read_text(encoding="utf-8"))
    node = capability_outputs.node_output(stored, "build_reels")
    assert {row["number"] for row in node["reel_ask"]["reel_asks"]} == {
        3, 21, 25}
    assert 99 not in {
        row["number"] for row in node["reel_ask"]["reel_asks"]}
    build = node["reel_build"]
    assert set(build["reels_requested"]) == {3, 21, 25}
    assert {"Reel 03 (staging)", "Reel 21 - lane-21 (staging)",
            "Reel 25 - lane-25 (staging)"} == set(build["timelines_built"])
    assert set(build["caption_hashes"]) == {
        "Reel 03", "Reel 21 - lane-21", "Reel 25 - lane-25"}
    assert set(build["track_plans"]) == set(build["caption_hashes"])
    assert "Reel 99 - foreign" not in build["caption_hashes"]
    assert {row["reel"] for row in
            build["awaiting_model_answers"]["reels"]} == {
        "Reel 03", "Reel 21 - lane-21", "Reel 25 - lane-25"}
    assert build["awaiting_model_answers"]["outstanding_answers"] == 3
    assert set(build["proof_scope"]["stills"]) == {
        "Reel 03", "Reel 21 - lane-21", "Reel 25 - lane-25"}
    assert {row["staging"] for row in build["pending_promotions"]} == {
        "Reel 03 (staging)", "Reel 21 - lane-21 (staging)",
        "Reel 25 - lane-25 (staging)"}
    assert build["coherence_summary"] == {
        "status": "whole-project", "owned_total": 8}


def test_verify_node_filters_aggregated_build_record_to_its_lane(monkeypatch):
    from library.tools.operations import load_step_module

    module = load_step_module("step_7_02_verify_reels", "step.py")
    final_21 = "Reel 21 - lane-21"
    final_25 = "Reel 25 - lane-25"
    staged_21 = f"{final_21} (staging)"
    staged_25 = f"{final_25} (staging)"
    data = {
        "project_folder": "/tmp/project",
        "only_reels": [25],
        "timeline_transcript": {"segments": []},
        "reel_build": {
            "timelines_built": [staged_21, staged_25],
            "staged_timelines": {final_21: staged_21,
                                 final_25: staged_25},
            "track_plans": {final_21: {"reel": 21},
                            final_25: {"reel": 25}},
            "allow_drops": {final_21: [], final_25: []},
            "supersede": [final_21, final_25],
            "retain": [final_21, final_25],
            "resolve_project_name": "Fixture",
            "master_timeline_name": "Master",
            "plan_path": "/tmp/project/plan.json",
        },
    }

    import unittest.mock

    with (unittest.mock.patch.object(module, "record_uncarried_notes",
                                      return_value=None),
          unittest.mock.patch(
              "library.tools.reel_build.verify_built_reels") as gate,
          unittest.mock.patch(
              "library.tools.reel_build.promote_staged_reels",
              return_value={"promoted": [final_25], "organised": None}
          ) as promote,
          unittest.mock.patch(
              "library.tools.reel_build.sweep_all_reels_informational"),
          unittest.mock.patch(
              "library.tools.timeline_transcript.transcript_path",
              return_value="/tmp/project/transcript.json")):
        result = module.verify_reels(data)

    assert gate.call_args.kwargs["only_reels"] == [staged_25]
    assert promote.call_args.args[3] == {final_25: staged_25}
    assert promote.call_args.kwargs["supersede"] == [final_25]
    assert promote.call_args.kwargs["retain"] == [final_25]
    assert result["reel_verification"]["timelines_verified"] == [final_25]


def test_concurrent_scoped_conformance_writes_preserve_sibling_reports(
        tmp_path):
    path = tmp_path / "review" / "conformance_report.json"
    path.parent.mkdir(parents=True)
    old_rows = [
        {"reel_name": f"Reel {number:02d} - final", "reel_number": number,
         "errors": 0, "warnings": 0, "findings": []}
        for number in (3, 21, 25)
    ]
    seed = {
        "project": "fixture", "master_timeline": "Master",
        "motion_graphics_tightness": {"measured": "project-wide"},
        "reels": old_rows,
        "read_only_proof": {
            "all_identical": True, "timelines_checked": 4,
            "timelines": {
                "Master": {"identical": True},
                **{row["reel_name"]: {"identical": True}
                   for row in old_rows},
            },
        },
        "quality_bar": {
            "verdicts": [{"reel": number, "verdict": "pass"}
                         for number in (3, 21, 25)],
            "not_read": [],
        },
    }
    path.write_text(json.dumps(seed), encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    workers = [
        context.Process(target=_write_conformance_lane,
                        args=(str(path), reel, barrier))
        for reel in (21, 25)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert [worker.exitcode for worker in workers] == [0, 0]

    report = json.loads(path.read_text(encoding="utf-8"))
    assert {row["reel_name"] for row in report["reels"]} == {
        "Reel 03 - final", "Reel 21 (staging)", "Reel 25 (staging)"}
    assert set(report["read_only_proof"]["timelines"]) == {
        "Master", "Reel 03 - final", "Reel 21 (staging)",
        "Reel 25 (staging)"}
    assert {row["reel"] for row in report["quality_bar"]["verdicts"]} == {
        3, 21, 25}
    assert report["motion_graphics_tightness"] == {
        "measured": "project-wide"}
