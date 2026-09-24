"""A run can be scoped, and a selection that cannot be met is refused.

Issue #250, the captain on 2026-08-28: "we should have that ability to be
able to quickly deselect and select what specific steps we want to fire
off for a run".

The part worth testing hardest is the refusal.  A run that begins and
dies forty minutes in because a producer was excluded is worse than one
that refuses in a second and names the missing output, so these assert
that the refusal happens BEFORE anything is deleted, saved or executed,
and that its reading of "missing" is the same reading
`run_pipeline.gather_step_inputs` makes when it raises.

The other half is regression: `--step`, `--from` and `--rerun` predate
this and must behave exactly as they did.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import run_scope
from library.tools.run_scope import (
    DESELECTED_BY_DEFAULT,
    TARGETS,
    ScopeError,
    Selection,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def dag():
    return run_scope.load_dag()


@pytest.fixture(scope="module")
def manifests(dag):
    return run_scope.load_manifests(dag)


def _resolve(selection, dag, manifests, state=None, **kw):
    return run_scope.resolve(selection, dag=dag, manifests=manifests,
                             state=state, **kw)


def _recorded_keys(node_id):
    """Every key the DAG says this step sends to somebody."""
    keys = set()
    for edge in run_scope.load_dag()["edges"]:
        if edge["from"] == node_id:
            keys |= set(edge.get("data_mapping") or {})
    return keys or {"some_key"}


def _state_with(*node_ids):
    """A project ledger in which each named step finished and left the
    output the DAG says it produces.

    All three halves, because a prerequisite names a KEY: the ledger
    entry says the step finished, the `step_outputs` entry is what
    `gather_step_inputs` will read, and the KEY inside it is what that
    function actually raises on."""
    return {
        "edit_completed": {n: {"at": "now"} for n in node_ids},
        "step_outputs": {n: {k: True for k in _recorded_keys(n)}
                         for n in node_ids},
    }


# ── The default run is unchanged apart from what is off by default ──




# ── Hard and soft edges are read off the same two declarations ──────

def test_an_edge_is_hard_exactly_when_the_runner_would_raise(dag, manifests):
    """`gather_step_inputs` raises when a mapped key is missing and the
    consumer did not declare that input optional.  The scope's notion of
    a hard edge has to be that same condition, or the refusal and the
    crash it prevents disagree."""
    requirements = run_scope.hard_requirements(dag, manifests)
    for edge in dag["edges"]:
        consumer, producer = edge["to"], edge["from"]
        mapping = edge.get("data_mapping") or {}
        optional = run_scope.optional_inputs(manifests[consumer])
        would_raise = (not mapping) or any(dst not in optional
                                           for dst in mapping.values())
        is_hard = producer in requirements.get(consumer, {})
        assert is_hard == would_raise, (
            f"{producer} -> {consumer}: hard={is_hard} but the runner "
            f"would{'' if would_raise else ' not'} raise")






def test_a_selection_the_scope_accepts_never_raises_in_gather_step_inputs(
        dag, manifests):
    """The guarantee this module exists for, asserted against the real
    runner rather than against a model of it.

    For every step of a full run, with a state carrying exactly what the
    ledger says each producer recorded, `gather_step_inputs` must not
    raise - and with a producer's key removed, it must.
    """
    from library.processes.edit_video.run_pipeline import gather_step_inputs

    producers = [n["id"] for n in dag["nodes"]]
    state = _state_with(*producers)
    scope = _resolve(Selection(), dag, manifests, state)
    for node_id in scope.steps_to_run:
        gather_step_inputs(node_id, dag, state,
                           manifest=manifests[node_id], external={})

    for need in run_scope.prerequisites(dag, manifests):
        if not need.names_a_key:
            continue
        thinned = _state_with(*producers)
        thinned["step_outputs"][need.producer].pop(need.state_key)
        with pytest.raises(RuntimeError):
            gather_step_inputs(need.consumer, dag, thinned,
                               manifest=manifests[need.consumer],
                               external={})




# ── The named target ────────────────────────────────────────────────







# ── Selecting and deselecting ───────────────────────────────────────


def test_only_does_not_drag_in_a_producer_of_a_value_nothing_reads(
        dag, manifests):
    """`--only plan_subtitles` used to pull `review_rough_cut` and
    `speech_sequence` in as HARD parents.

    Both were declared `required: true` on 4.01 and neither is read by
    its code, which reads `audio_spine`, `brand_effect`, `brand_style`
    and `project_folder` and nothing else. The closure is derived from
    `inputs[].required`, so a false declaration became a real cost: the
    captain's own example - "regenerate just this small segment of
    subtitles" - could not be expressed at all. Following the runner's
    own remediation advice converged, in three rounds, on advising you to
    skip `plan_subtitles` itself.

    Both are now declared optional, and the requirement they were
    standing in for is executable instead
    (`requirements.spine.word_timings`). The DAG edges are unchanged, so
    the values still arrive; they are simply no longer a refusal.
    """
    hard = run_scope.hard_requirements(dag, manifests)["plan_subtitles"]

    assert "speech_sequence" not in hard, (
        "4.01 declares speech_sequence a hard parent again, for a value "
        "its code never names")

    # `review_rough_cut` IS still a hard parent, and deliberately so.
    # That declaration is the captain's open `rough-cut-gate-on-reentry`
    # decision, not a tidy-up: relaxing it here would answer them in the
    # delete direction. It stays until they rule.
    assert "review_rough_cut" in hard

    # `speech_sequence` is still in the RUN, and correctly so: it is a
    # hard parent of `mesh_spine`, which really does read it. The
    # declaration that was false was 4.01's, not 2.05's - worth pinning,
    # because "it still appears" is not the same as "the fix did
    # nothing".
    scope = _resolve(Selection(only=("plan_subtitles",)), dag, manifests)
    assert "speech_sequence" in scope.steps_to_run
    assert "speech_sequence" in run_scope.hard_requirements(
        dag, manifests)["mesh_spine"]




def test_an_unknown_step_is_refused_by_name(dag, manifests):
    with pytest.raises(ScopeError, match="not a step in this pipeline"):
        _resolve(Selection(skip=("plan_colour",)), dag, manifests)


def test_selecting_and_skipping_the_same_step_is_refused(dag, manifests):
    with pytest.raises(ScopeError, match="both selected and skipped"):
        _resolve(Selection(only=("render",), skip=("render",)), dag, manifests)


# ── The refusal ─────────────────────────────────────────────────────

def test_excluding_a_producer_refuses_and_names_the_missing_output(
        dag, manifests):
    with pytest.raises(ScopeError) as exc:
        _resolve(Selection(skip=("plan_vfx",)), dag, manifests)
    message = str(exc.value)
    assert "render_motion_graphics" in message
    assert "plan_vfx" in message
    assert "enhancement_spec" in message, (
        "the refusal must name the OUTPUT that is missing, not just the "
        "step")


def test_compile_manifest_no_longer_holds_the_decoration_planners(
        dag, manifests):
    """#260. `enhancement_spec` used to be `compile_manifest`'s too, so
    dropping `plan_vfx` stranded the compiler as well. The compiler
    compiles without it - measured in
    tests/test_compile_manifest_without_the_decoration.py - so the only
    hard consumer left is the one that really cannot draw without it."""
    requirements = run_scope.hard_requirements(dag, manifests)
    assert "plan_vfx" not in requirements.get("compile_manifest", {})
    assert "enhancement_spec" in (
        requirements["render_motion_graphics"]["plan_vfx"])




def test_a_recorded_output_missing_the_KEY_does_not_satisfy(dag, manifests):
    """A prerequisite is a condition on STATE, so the key is what has to
    be there. A step that finished and recorded something else would let
    the selection pass and die in `gather_step_inputs`, which raises on
    the key and not on the step."""
    state = {
        "edit_completed": {"plan_vfx": {"at": "now"}},
        "step_outputs": {"plan_vfx": {"vfx_candidates_toon": "..."}},
    }
    with pytest.raises(ScopeError) as exc:
        _resolve(Selection(skip=("plan_vfx",)), dag, manifests, state)
    message = str(exc.value)
    assert "enhancement_spec" in message
    assert "vfx_candidates_toon" in message, (
        "the refusal must say what the producer DID record, or the "
        "captain cannot tell a missing step from a missing key")




def test_a_rerun_target_cannot_satisfy_what_it_is_about_to_discard(
        dag, manifests):
    """`--rerun plan_vfx --skip plan_vfx` would throw the output away and
    then rely on it."""
    state = _state_with("plan_vfx")
    scope = _resolve(Selection(skip=("plan_vfx",)), dag, manifests, state)
    assert "plan_vfx" in scope.from_cache  # satisfied while it is on file
    with pytest.raises(ScopeError, match="plan_vfx"):
        _resolve(Selection(skip=("plan_vfx",)), dag, manifests, state,
                 invalidated=["plan_vfx"])


def test_every_accepted_selection_has_its_dependencies_met(dag, manifests):
    """The property, checked over every single-step exclusion: a
    selection either resolves with every hard parent present, or it
    refuses. It never resolves with a hole in it."""
    requirements = run_scope.hard_requirements(dag, manifests)
    for node_id in [n["id"] for n in dag["nodes"]]:
        try:
            scope = _resolve(Selection(skip=(node_id,)), dag, manifests)
        except ScopeError:
            continue
        run_set = set(scope.steps_to_run)
        for consumer in run_set:
            for producer in requirements.get(consumer, {}):
                assert producer in run_set, (
                    f"skipping {node_id} left {consumer} without "
                    f"{producer}")


# ── Steps that are off by default ───────────────────────────────────






def test_only_pulls_in_a_default_off_step_its_target_cannot_run_without(
        bare_project):
    """`--only` is documented as "run this step and whatever it cannot
    run without", and across a default-off producer it did not.

    `judge_reels` reads `reel_selection` from `select_reels`; both are
    off by default. Before 2026-09-06 `--only judge_reels` left the
    producer excluded and then refused the selection for the gap it had
    just made, so the step was reachable only by running the whole
    pipeline with `--with select_reels --with judge_reels`.
    """
    summary = _dry_run(bare_project, only=["judge_reels"])
    assert summary["steps_to_run"] == ["select_reels", "judge_reels"]



def test_skip_still_wins_over_being_pulled_in(dag, manifests):
    """"Run this, but not the thing it needs" is refused, not silently
    resolved one way or the other."""
    with pytest.raises(run_scope.ScopeError, match="select_reels"):
        _resolve(Selection(only=("judge_reels",), skip=("select_reels",)),
                 dag, manifests)











# ── The text-extraction step, deselected through this mechanism ─────



def test_the_default_run_leaves_the_text_extraction_step_out(dag, manifests):
    scope = _resolve(Selection(), dag, manifests)
    assert "ocr_extraction" not in scope.steps_to_run
    estimate = run_scope.estimated_seconds(
        _resolve(Selection(with_steps=("ocr_extraction",)), dag, manifests),
        dag=dag)
    baseline = run_scope.estimated_seconds(scope, dag=dag)
    assert estimate["selected"] > baseline["selected"], (
        "turning it on has to cost something, or the deselection is "
        "measuring nothing")


def test_the_reason_does_not_claim_the_vision_model_cannot_read_text():
    """The recorded justification used to say the vision model "provably
    cannot" read on-screen text and that `readable_text` was null for
    every object. Re-counted off 001's 2026-08-26 artifacts: 159 objects,
    10 filled, 149 null. The reason has to match."""
    reason = DESELECTED_BY_DEFAULT["ocr_extraction"]
    assert "provably cannot" not in reason
    assert "10 of 159" in reason

    source = (REPO_ROOT / "library" / "tools" / "run_scope.py").read_text(
        encoding="utf-8")
    assert "provably" not in source or "was wrong" in source

    layout = (REPO_ROOT / "library" / "tools" / "project_layout.py").read_text(
        encoding="utf-8")
    assert "provably cannot" not in layout, (
        "project_layout.py still carries the corrected claim")




# ── One vocabulary, two CLIs ────────────────────────────────────────





# ── The flags that predate this must behave exactly as before ───────
#
# `--step`, `--from` and `--rerun` shipped before scoping existed. The
# scope now sits in front of them, so these drive the REAL runner and
# check the step list against the algorithm as it stood.

@pytest.fixture
def bare_project(tmp_path):
    """A project with a state file and nothing done. Enough for a dry
    run, which executes no step."""
    project = tmp_path / "scoped_run_project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: Scope Test\nslug: scope-test\n", encoding="utf-8")
    (project / "pipeline_data.json").write_text(
        json.dumps({"project_folder": str(project),
                    "preflight_completed": {}, "edit_completed": {},
                    "step_outputs": {}}), encoding="utf-8")
    return project


def _ancestors(node_id, dag):
    """`--from`'s own rule, as it stood before scoping: skip the target's
    ancestors, keep everything else."""
    reverse = {}
    for edge in dag["edges"]:
        reverse.setdefault(edge["to"], set()).add(edge["from"])
    out, queue = set(), list(reverse.get(node_id, []))
    while queue:
        parent = queue.pop()
        if parent not in out:
            out.add(parent)
            queue.extend(reverse.get(parent, []))
    return out


def _dry_run(project, **kwargs):
    from library.processes.edit_video.run_pipeline import run_pipeline
    return run_pipeline(str(project), dry_run=True, **kwargs)













def test_the_refusal_happens_before_anything_is_written(bare_project):
    """A refused selection must not touch the project - not the ledger,
    not the source fingerprints, not one artifact."""
    state_path = bare_project / "pipeline_data.json"
    before = state_path.read_bytes()

    from library.processes.edit_video.run_pipeline import run_pipeline
    summary = run_pipeline(str(bare_project), skip=["plan_vfx"])

    assert summary["status"] == "REFUSED"
    assert "enhancement_spec" in summary["reason"]
    assert state_path.read_bytes() == before, (
        "a refused run wrote to pipeline_data.json")
    assert not (bare_project / "pipeline_output").exists(), (
        "a refused run created output directories")

