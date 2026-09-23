"""The requirements layer changed no existing behaviour, and can say so.

The two assertions that matter most here are the boring ones:

* `derive_state_keys` reproduces `run_scope.prerequisites` EXACTLY. That
  is the proof that re-pointing the contract layer altered nothing about
  which selections are refused - the state_key kind is auto-derived from
  the same `inputs[].required` + `data_mapping` reading, so it cannot
  disagree with the refusal `run_scope` has always given or with the
  raise in `gather_step_inputs`.
* the test-only probe override never appears in production code.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.tools import requirements as R, run_scope

REPO = Path(__file__).resolve().parents[1]


# ── The derivation is the same reading, not a second one ─────────────

def test_derived_state_keys_reproduce_prerequisites_exactly():
    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)

    prereqs = run_scope.prerequisites(dag, manifests)
    derived = R.derive_state_keys(dag, manifests)

    assert len(derived) == len(prereqs), (
        f"the requirements layer derived {len(derived)} state_key "
        f"requirements from {len(prereqs)} prerequisites. They must be "
        f"one for one, or the refusal and the mid-run raise can drift.")

    expected = {(p.consumer, p.producer, p.state_key) for p in prereqs}
    got = set()
    for req in derived:
        assert len(req.consumers) == 1
        assert len(req.produced_by) == 1
        got.add((req.consumers[0], req.produced_by[0],
                 req.name.split(".", 2)[2] if req.name.count(".") >= 2
                 and not req.name.endswith(">") else ""))
    # Compare on consumer/producer pairs, which is what the closure walks.
    assert {(c, p) for c, p, _ in got} == {(c, p) for c, p, _ in expected}


def test_every_derived_requirement_is_a_state_key():
    assert all(r.kind == R.KIND_STATE_KEY for r in R.derive_state_keys())


def test_all_requirements_is_derived_plus_hand_written():
    every = R.all_requirements()
    assert len(every) == (len(R.derive_state_keys())
                          + len(R.derive_runner_injected_keys())
                          + len(R.HAND_WRITTEN))


def test_the_runner_injected_half_is_derived_and_is_a_state_key():
    """`derive_state_keys` reads EDGES; one hard input has none.

    `select_reels.timeline_transcript` is declared `required: true` and
    is written by a CLI tool, so no edge can carry it and
    `run_scope.prerequisites` cannot see it. Until this derivation
    existed `select_reels` had ZERO requirements while being the one
    step that cannot run without a file no step writes - and the runner
    raised for it mid-run instead, which is the crash this module exists
    to move earlier.

    DERIVED, not listed: the consumers come off the manifests, which is
    what let a SECOND PROCESS inherit this requirement with nothing
    added here. `build_reels` and `verify_reels` declare
    `timeline_transcript` required in their own manifests and appear
    below because of that alone - the reel path is cut from the same
    transcript, and if a manifest stops declaring it, it leaves this
    list the same way.

    `tests/test_operations.py` re-measures which inputs are in this
    shape across every process.
    """
    injected = R.derive_runner_injected_keys()
    assert [r.name for r in injected] == ["timeline_transcript.on_file"]
    only = injected[0]
    assert only.kind == R.KIND_STATE_KEY
    assert only.consumers == ("build_reels", "judge_reels", "select_reels",
                              "verify_reels")
    # Empty and load-bearing: `_producer_will_make_it` never defers a
    # requirement with no producer, and `describe_refusal` prints no
    # "run the producers" line for one. There is no step to run.
    assert only.produced_by == ()


def test_the_five_kinds_are_all_present():
    kinds = {r.kind for r in R.all_requirements()}
    assert kinds == set(R.KINDS), (
        f"expected all five kinds to be in use, got {sorted(kinds)}")


# ── The test seam stays out of production ────────────────────────────

def test_the_probe_override_is_never_used_in_production_code():
    """`_FORCE` drives an environment probe to a chosen verdict.

    It exists so an environment requirement can supply both witnesses
    without the test mutating the machine. If the runner or a step ever
    sets it, a real probe could be silenced in a real run - which would
    be a gate that cannot fail, wearing the costume of one that can.
    """
    # check=False deliberately: grep exits 1 when it finds NOTHING, and
    # finding nothing is this test's PASSING condition. check=True would
    # raise on every green run - the verdict is the captured stdout, not
    # the exit status.
    hits = subprocess.run(
        ["grep", "-rn", R._FORCE, "library/processes", "library/steps"],
        cwd=REPO, capture_output=True, encoding="utf-8",
        check=False).stdout.strip()
    assert hits == "", (
        f"the test-only probe override appears in production code:\n{hits}")


# ── Requirements are asked of the EXECUTE set ────────────────────────

def test_check_narrows_to_the_execute_set():
    """The control-audit finding, pinned.

    `--step` and `--from` narrow the step list AFTER `run_scope.resolve`
    has agreed to the selection, so a requirement asked against the plan
    is asked about steps that will never run. `check` takes the execute
    set for exactly this reason.
    """
    npx = next(r for r in R.registry() if r.name == "env.npx")
    forced_missing = R.Context(state={R._FORCE: {"npx": False}})

    # In the plan but not executing -> not asked.
    assert R.check({"scan"}, forced_missing, [npx]) == []
    # Executing -> asked, and refuses.
    unmet = R.check({"render_subtitles"}, forced_missing, [npx])
    assert len(unmet) == 1
    assert unmet[0].satisfaction.is_unsatisfied


def test_check_rebuilds_the_context_run_set():
    """A caller that passes a stale run_set must not silently win."""
    npx = next(r for r in R.registry() if r.name == "env.npx")
    stale = R.Context(run_set=frozenset({"scan"}),
                      state={R._FORCE: {"npx": False}})
    unmet = R.check({"render_subtitles"}, stale, [npx])
    assert len(unmet) == 1


# ── The music predicate reads the key the step reads ─────────────────

def test_track_path_keys_match_the_step():
    """The requirement and 2.06 must not disagree about which key names
    the track, or the refusal would be about a different value than the
    one the step goes on to use."""
    source = (REPO / "library" / "steps" / "step_2_06_music_analysis"
              / "step.py").read_text(encoding="utf-8")
    for key in R.TRACK_PATH_KEYS:
        assert f'"{key}"' in source, (
            f"requirements.TRACK_PATH_KEYS names {key!r}, which "
            f"step_2_06_music_analysis/step.py does not read")


# ── The held requirement, and the deleted ones, are recorded ─────────

def test_nothing_is_held_and_what_left_the_table_exists():
    """`UNAUTHORISED` is the table of requirements whose SHAPE is settled
    but whose BEHAVIOUR is somebody else's call.

    It is empty: `rough_cut.approved` lived there and was built once the
    ruling landed (refuse with a deliberate override,
    `data/decisions/rough-cut-gate.md`).

    The table must stay, and a name may only leave it by being BUILT -
    quietly dropping an entry would decide the question in the delete
    direction, which is exactly as strong a decision as enforcing it.
    """
    built = {r.name for r in R.all_requirements()}
    assert R.UNAUTHORISED == {}
    assert "rough_cut.approved" in built, (
        "rough_cut.approved left UNAUTHORISED without being built - a "
        "silent drop is a decision, not a neutral default")


def test_deleted_preconditions_are_recorded_with_reasons():
    """A deleted requirement with no reason reads as an oversight."""
    assert R.DELETED
    for claim, reason in R.DELETED.items():
        assert len(reason) > 40, f"{claim} was deleted without a reason"


def test_the_refusal_names_producers_and_separates_the_machine():
    """The two halves have two remedies and must not be mixed."""
    dag = run_scope.load_dag()
    unmet = R.check(
        {"plan_subtitles", "render_subtitles"},
        R.Context(run_set=frozenset({"plan_subtitles", "render_subtitles"}),
                  state={R._FORCE: {"npx": False, "remotion": False}}),
    )
    text = "\n".join(R.describe_refusal(unmet))
    assert "Refusing before the run starts" in text
    assert "This machine cannot do the work as it stands" in text
    assert "needed by: render_subtitles" in text


# ── The manifests carry the replacement, not the prose ───────────────

def test_manifests_declare_requirements_not_prose():
    steps = REPO / "library" / "steps"
    for path in sorted(steps.glob("*/manifest.json")):
        interface = json.loads(path.read_text(encoding="utf-8"))["interface"]
        assert "preconditions" not in interface
        assert "postconditions" not in interface


def test_plan_subtitles_no_longer_requires_what_it_does_not_read():
    """The captain's own example - and only the half that is mine.

    4.01's `main()` reads `audio_spine`, `brand_effect`, `brand_style`
    and `project_folder`, and names neither `speech_sequence` nor
    `rough_cut_review`. The survey reports both as UNREAD, but they are
    NOT the same job:

    * `speech_sequence` is a plain FALSE declaration with no decision
      attached. It is relaxed here, and the requirement it stood in for
      is executable as `spine.word_timings`.
    * `rough_cut_review` is the CARVE-OUT. It is the same decision as
      the captain's open `rough-cut-gate-on-reentry`: rule "refuse" and
      the declaration becomes real and must be made executable; rule
      "delete" and it goes. Relaxing it here would decide that question
      in the delete direction without them, so it is left EXACTLY as it
      was - and this test pins that it stays untouched.
    """
    manifest = json.loads(
        (REPO / "library" / "steps" / "step_4_01_plan_subtitles"
         / "manifest.json").read_text(encoding="utf-8"))
    by_name = {i["name"]: i for i in manifest["interface"]["inputs"]}
    assert by_name["audio_spine"].get("required", True) is True

    assert by_name["speech_sequence"].get("required", True) is False, (
        "4.01 declares speech_sequence required again, and its code "
        "still does not read it")

    assert by_name["rough_cut_review"].get("required", True) is True, (
        "rough_cut_review was relaxed. That is the captain's open "
        "decision `rough-cut-gate-on-reentry`, not a tidy-up: softening "
        "it decides the question in the delete direction. Leave it until "
        "they answer - see requirements.UNAUTHORISED.")

    assert "spine.word_timings" in manifest["interface"]["requirements"]


def test_state_refuses_and_environment_reports():
    """The two kinds get two different answers, and that is deliberate.

    A STATE requirement being unmet means the run is incoherent - a value
    it needs will not exist and nothing in this run will make one. Only
    changing the selection fixes that, so it refuses.

    An ENVIRONMENT requirement being unmet means the machine is not
    provisioned. That is reported, not refused: this repository already
    treats a missing `remotion-subtitles/node_modules` as an ordinary
    state and skips honestly on it in twenty-odd tests, and a run may
    legitimately never reach the renderer. Refusing every run on a box
    that has not npm-installed would forbid work that succeeds today,
    which is a gate that fails correct input.

    This pins the split, because collapsing it in either direction is a
    regression: refuse-everything forbids correct runs, report-everything
    puts the mid-run crash back.
    """
    source = (REPO / "library" / "processes" / "edit_video"
              / "run_pipeline.py").read_text(encoding="utf-8")
    assert "THIS MACHINE IS NOT READY" in source, (
        "the environment report is gone - a machine that cannot do the "
        "work must still say so at second zero")
    assert "reported, not refused" in source

    # And the state side must still be able to refuse outright.
    assert '"status": "REFUSED"' in source


def test_requirements_come_from_the_run_s_own_dag():
    """A caller may hand `run_pipeline` a reduced DAG.

    The e2e tests drive a three-node graph
    (creative_direction -> speech_sequence -> mesh_spine). Requirements
    derived from the full graph on disk refused it, because `mesh_spine`
    declares `music_analysis` required and that node does not exist in
    the reduced graph. The runner therefore derives from the dag and
    manifests it was given, and this pins that.
    """
    reduced = {
        "nodes": [
            {"id": "speech_sequence",
             "step_ref": "steps/step_2_02_speech_sequence"},
            {"id": "mesh_spine", "step_ref": "steps/step_2_05_mesh_spine"},
        ],
        "edges": [{"from": "speech_sequence", "to": "mesh_spine",
                   "data_mapping": {"speech_sequence": "speech_sequence"}}],
    }
    manifests = run_scope.load_manifests(reduced)
    derived = R.all_requirements(reduced, manifests)

    producers = {p for r in derived if r.kind == R.KIND_STATE_KEY
                 for p in r.produced_by}
    assert producers == {"speech_sequence"}, (
        f"requirements were derived from a graph other than the one "
        f"passed in; producers seen: {sorted(producers)}")

    source = (REPO / "library" / "processes" / "edit_video"
              / "run_pipeline.py").read_text(encoding="utf-8")
    assert "requirements.all_requirements(dag, manifests)" in source, (
        "the runner stopped passing its own dag to all_requirements, so "
        "a reduced graph would be judged against the full one on disk")
