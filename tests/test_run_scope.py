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

def test_a_plain_selection_runs_the_whole_dag(dag, manifests):
    scope = _resolve(Selection(), dag, manifests)
    dag_nodes = {n["id"] for n in dag["nodes"]}
    assert set(scope.steps_to_run) == dag_nodes - set(DESELECTED_BY_DEFAULT)
    assert scope.skipped == ()
    assert not scope.is_scoped


def test_the_run_order_is_topological(dag, manifests):
    scope = _resolve(Selection(), dag, manifests)
    position = {n: i for i, n in enumerate(scope.steps_to_run)}
    for edge in dag["edges"]:
        src, dst = edge["from"], edge["to"]
        if src in position and dst in position:
            assert position[src] < position[dst], f"{src} runs after {dst}"


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


def test_a_prerequisite_exists_exactly_when_the_runner_would_raise(
        dag, manifests):
    """The per-KEY form of the edge agreement above, and now the
    primitive `hard_requirements` is derived from.

    `gather_step_inputs` raises per mapped key, so the condition has to
    be read per key too: an edge carrying one required key and three
    optional ones is one prerequisite, not four and not one-per-edge.
    """
    needs = {(need.consumer, need.producer, need.state_key, need.input_name)
             for need in run_scope.prerequisites(dag, manifests)}
    expected = set()
    for edge in dag["edges"]:
        consumer, producer = edge["to"], edge["from"]
        mapping = edge.get("data_mapping") or {}
        optional = run_scope.optional_inputs(manifests[consumer])
        if not mapping:
            expected.add((consumer, producer, "", ""))
            continue
        for source_key, destination in mapping.items():
            if destination not in optional:
                expected.add((consumer, producer, source_key, destination))
    assert needs == expected


def test_hard_requirements_is_the_prerequisites_collapsed(dag, manifests):
    """Two readings of "hard", one computation. The closure walks
    producers and the refusal walks keys, and they must not drift."""
    requirements = run_scope.hard_requirements(dag, manifests)
    from_needs = {}
    for need in run_scope.prerequisites(dag, manifests):
        keys = from_needs.setdefault(need.consumer, {}).setdefault(
            need.producer, set())
        if need.input_name:
            keys.add(need.input_name)
    assert requirements == from_needs


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


def test_a_soft_parent_is_not_dragged_in_by_a_target(dag, manifests):
    """`creative_cohesion` reaches `compile_manifest` on an OPTIONAL
    input, so a target that wants the render does not have to pay for
    it."""
    optional = run_scope.optional_inputs(manifests["compile_manifest"])
    assert "cohesion_review" in optional
    scope = _resolve(Selection(target="rough_cut_subtitles"), dag, manifests)
    assert "creative_cohesion" not in scope.steps_to_run
    assert "creative_cohesion" in scope.skipped


# ── The named target ────────────────────────────────────────────────

def test_the_target_is_derived_from_the_dag_not_stored(dag, manifests):
    """A target names GOALS. Everything else is walked off the DAG, so a
    step inserted upstream is picked up without editing the table."""
    for target in TARGETS.values():
        for goal in target.goals:
            assert goal in {n["id"] for n in dag["nodes"]}, (
                f"target {target.name} names goal {goal!r}, which is not "
                f"a step in this pipeline")


def test_the_rough_cut_target_reaches_a_render_with_subtitles(dag, manifests):
    scope = _resolve(Selection(target="rough_cut_subtitles"), dag, manifests)
    for needed in ("plan_subtitles", "render_subtitles", "compile_manifest",
                   "render"):
        assert needed in scope.steps_to_run
    assert scope.is_scoped
    assert len(scope.steps_to_run) < len(scope.universe)


def test_the_target_skips_something_and_says_why(dag, manifests):
    scope = _resolve(Selection(target="rough_cut_subtitles"), dag, manifests)
    assert scope.skipped
    for node_id in scope.skipped:
        assert scope.reasons.get(node_id), f"{node_id} skipped with no reason"


def test_an_unknown_target_is_refused_by_name(dag, manifests):
    with pytest.raises(ScopeError, match="unknown target"):
        _resolve(Selection(target="ship_it"), dag, manifests)


# ── Selecting and deselecting ───────────────────────────────────────

def test_only_runs_the_step_and_what_it_cannot_run_without(dag, manifests):
    scope = _resolve(Selection(only=("plan_subtitles",)), dag, manifests)
    assert "plan_subtitles" in scope.steps_to_run
    # Its hard parents come with it.
    for parent in ("mesh_spine", "speech_sequence", "review_rough_cut"):
        assert parent in scope.steps_to_run
    # Its consumers do not.
    assert "compile_manifest" not in scope.steps_to_run
    assert "render" not in scope.steps_to_run


def test_skip_removes_a_step_nothing_needs(dag, manifests):
    scope = _resolve(Selection(skip=("validate",)), dag, manifests)
    assert "validate" not in scope.steps_to_run
    assert scope.reasons["validate"] == "excluded by --skip"


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


def test_the_refusal_says_how_to_fix_it(dag, manifests):
    with pytest.raises(ScopeError) as exc:
        _resolve(Selection(skip=("plan_vfx",)), dag, manifests)
    message = str(exc.value)
    assert "--skip" in message and "--only" in message


def test_a_recorded_output_satisfies_an_excluded_dependency(dag, manifests):
    """The scoped-re-run case: plan_vfx already ran, so leaving it out
    strands nobody."""
    state = _state_with("plan_vfx")
    scope = _resolve(Selection(skip=("plan_vfx",)), dag, manifests, state)
    assert "plan_vfx" not in scope.steps_to_run
    assert "render_motion_graphics" in scope.from_cache["plan_vfx"]


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


def test_a_ledger_entry_with_no_output_does_not_satisfy(dag, manifests):
    """A ledger entry says the step finished; the output is what
    `gather_step_inputs` will actually read. Accepting the first without
    the second moves the crash back into the run."""
    state = {"edit_completed": {"plan_vfx": {"at": "now"}}, "step_outputs": {}}
    with pytest.raises(ScopeError, match="plan_vfx"):
        _resolve(Selection(skip=("plan_vfx",)), dag, manifests, state)


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

def test_the_default_off_list_names_real_dag_nodes(dag):
    node_ids = {n["id"] for n in dag["nodes"]}
    for node_id in DESELECTED_BY_DEFAULT:
        assert node_id in node_ids, (
            f"{node_id} is deselected by default but is not in the DAG. "
            f"A step with no node is UNWIRED - see "
            f"project_layout.STEPS - and this is not that list.")


def test_nothing_hard_depends_on_a_step_that_is_off_by_default(dag, manifests):
    """Otherwise every default run would refuse."""
    requirements = run_scope.hard_requirements(dag, manifests)
    for node_id in DESELECTED_BY_DEFAULT:
        consumers = [c for c, parents in requirements.items()
                     if node_id in parents]
        assert not consumers, (
            f"{node_id} is off by default but {consumers} hard-depend on "
            f"it, so a default run would refuse")


def test_every_default_off_step_carries_a_reason(dag, manifests):
    for node_id, reason in DESELECTED_BY_DEFAULT.items():
        assert len(reason.strip()) > 40, (
            f"{node_id} is off by default with a thin reason: {reason!r}")


def test_a_default_run_reports_what_is_off_by_default(dag, manifests):
    """A step that exists and silently never runs is the trap AGENTS.md
    section 3 exists to stop."""
    scope = _resolve(Selection(), dag, manifests)
    assert set(scope.default_off) == set(DESELECTED_BY_DEFAULT)
    printed = "\n".join(run_scope.describe(scope))
    for node_id in DESELECTED_BY_DEFAULT:
        assert node_id in printed


def test_with_turns_a_default_off_step_back_on(dag, manifests):
    scope = _resolve(Selection(with_steps=("ocr_extraction",)), dag, manifests)
    assert "ocr_extraction" in scope.steps_to_run
    assert scope.default_off == ()


def test_naming_a_default_off_step_in_only_selects_it(dag, manifests):
    scope = _resolve(Selection(only=("ocr_extraction",)), dag, manifests)
    assert "ocr_extraction" in scope.steps_to_run


def test_step_names_a_default_off_step_outright(dag, manifests):
    """`--step ocr_extraction` reaches here as `always_include`. Naming a
    step is a stronger statement than any default."""
    scope = _resolve(Selection(), dag, manifests,
                     always_include=["ocr_extraction"])
    assert "ocr_extraction" in scope.steps_to_run


# ── The text-extraction step, deselected through this mechanism ─────

def test_the_text_extraction_step_is_wired_and_deselected(dag, manifests):
    """#245: "finish flushing it out and then simply deselect it". So it
    has a node, and the deselection is expressed here rather than by a
    bespoke flag."""
    assert "ocr_extraction" in {n["id"] for n in dag["nodes"]}
    assert "ocr_extraction" in DESELECTED_BY_DEFAULT

    from library.tools.project_layout import STEPS
    step = next(s for s in STEPS if s.node_id == "ocr_extraction")
    assert step.wired, "the step is wired; it is the RUN that declines it"


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


def test_no_step_consumes_the_text_extraction_output(dag):
    """The reason says nothing reads it. If that stops being true the
    reason is stale, and this is what notices."""
    consumers = [e["to"] for e in dag["edges"] if e["from"] == "ocr_extraction"]
    assert consumers == [], (
        f"{consumers} now consume ocr_extraction - revisit "
        f"DESELECTED_BY_DEFAULT, the step is no longer free to leave out")


# ── One vocabulary, two CLIs ────────────────────────────────────────

def test_both_clis_register_the_same_scope_flags():
    """`manage_project.py run` forwards to `run_pipeline.py`, so a flag
    one accepts and the other does not is a broken command."""
    import argparse

    wrapper = argparse.ArgumentParser()
    run_scope.add_scope_arguments(wrapper)
    runner = argparse.ArgumentParser()
    run_scope.add_scope_arguments(runner)

    def options(parser):
        return {opt for action in parser._actions
                for opt in action.option_strings}

    assert options(wrapper) == options(runner)
    assert {"--target", "--only", "--skip", "--with"} <= options(wrapper)


def test_manage_project_forwards_every_scope_flag():
    """The wrapper builds an argv for the runner. A flag it parses and
    does not forward is silently ignored."""
    source = (REPO_ROOT / "manage_project.py").read_text(encoding="utf-8")
    for flag in ("--target", "--only", "--skip", "--with"):
        assert f'"{flag}"' in source, f"manage_project.py never sends {flag}"


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


def test_step_still_runs_exactly_one_step(bare_project, dag):
    for node_id in [n["id"] for n in dag["nodes"]]:
        summary = _dry_run(bare_project, single_step=node_id)
        assert summary["steps_to_run"] == [node_id], (
            f"--step {node_id} no longer runs exactly that step")


def test_step_reaches_a_step_that_is_off_by_default(bare_project):
    summary = _dry_run(bare_project, single_step="ocr_extraction")
    assert summary["steps_to_run"] == ["ocr_extraction"]


def test_from_still_runs_the_target_and_everything_not_upstream_of_it(
        bare_project, dag, manifests):
    """The pre-scoping rule, recomputed here and compared against the
    real runner. The universe is the DAG minus what is off by default,
    which is what a default run has always been."""
    universe = run_scope.resolve(Selection(), dag=dag,
                                 manifests=manifests).universe
    for node_id in universe:
        expected = [n for n in universe if n not in _ancestors(node_id, dag)]
        summary = _dry_run(bare_project, from_step=node_id)
        assert summary["steps_to_run"] == expected, (
            f"--from {node_id} changed shape")


def test_a_plain_dry_run_lists_the_whole_pipeline(bare_project, dag,
                                                  manifests):
    universe = run_scope.resolve(Selection(), dag=dag,
                                 manifests=manifests).universe
    summary = _dry_run(bare_project)
    assert summary["steps_to_run"] == list(universe)


def test_rerun_still_reports_its_targets_and_does_not_narrow_the_run(
        bare_project, dag, manifests):
    universe = run_scope.resolve(Selection(), dag=dag,
                                 manifests=manifests).universe
    summary = _dry_run(bare_project, rerun=["temporal_index"])
    assert summary["steps_to_run"] == list(universe)


def test_a_rerun_target_that_names_nothing_is_still_refused_by_the_ledger(
        bare_project):
    from library.tools.step_ledger import LedgerError

    with pytest.raises(LedgerError):
        _dry_run(bare_project, rerun=["not_a_step"])


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


def test_a_target_run_reports_what_it_skipped(bare_project):
    summary = _dry_run(bare_project, target="rough_cut_subtitles")
    assert "validate" in summary["skipped"]
    assert summary["skip_reasons"]["validate"]
    assert summary["estimated_seconds"]["skipped"] > 0


# ── The dashboard drives a default run ──────────────────────────────

def test_the_dashboard_step_button_does_not_advance_into_a_default_off_step():
    """`Step` resolves the first topologically-unrun step server-side.
    Reading the raw DAG would park it forever on a step the pipeline
    declined, on every project that has never run one."""
    from library.dashboard.server import _step_order

    order = _step_order()
    for node_id in DESELECTED_BY_DEFAULT:
        assert node_id not in order


def test_a_target_whose_own_goal_is_skipped_is_refused(dag, manifests):
    """`--target rough_cut_subtitles --skip render` would otherwise
    resolve happily to a run that never reaches the target."""
    with pytest.raises(ScopeError, match="cannot be reached"):
        _resolve(Selection(target="rough_cut_subtitles", skip=("render",)),
                 dag, manifests)
