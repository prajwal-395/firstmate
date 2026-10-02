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
import pytest
from library.tools import run_scope
from library.tools.run_scope import ScopeError, Selection
from library.tools import breakpoints, operations
from library.tools.breakpoints import EVERY_STEP, BreakpointError
from pathlib import Path
import yaml
from library.tools import run_profile
from library.tools.project_layout import Area, ProjectLayout
from library.tools.run_profile import ProfileError, RunProfile
import sys
from unittest.mock import patch
import os


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


def test_a_malformed_selection_is_refused_by_name(dag, manifests):
    with pytest.raises(ScopeError, match="not a step in this pipeline"):
        _resolve(Selection(skip=("plan_colour",)), dag, manifests)
    with pytest.raises(ScopeError, match="both selected and skipped"):
        _resolve(Selection(only=("render",), skip=("render",)), dag, manifests)
    # "Run this, but not the thing it needs" is refused, not silently
    # resolved one way or the other.
    with pytest.raises(ScopeError, match="select_reels"):
        _resolve(Selection(only=("judge_reels",), skip=("select_reels",)),
                 dag, manifests)


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


# --------------------------------------------------------------------------
# From test_breakpoints.py
#
# A run stops where it was told to, and says where it will not.
#
# `--review` gated after EVERY step and there was no way to say "stop
# after the rough cut and nowhere else".  The gate machinery itself is
# unchanged - `review_gate.py` still writes the snapshot, still takes
# approve/reject/revise and still merges a revision back.  This is the
# selector it never had.
#
# Two properties are worth holding hardest:
#
# * `--review` still means what it always meant, so nothing that used it
#   regresses;
# * a breakpoint armed at a step this run will not reach is REPORTED and
#   not refused, because a pause that never happens strands nothing - but
#   a pause the captain asked for that silently never happens is exactly
#   the trap AGENTS.md section 3 exists to stop.

@pytest.fixture(scope="module")
def steps():
    return {n["id"] for n in run_scope.load_dag()["nodes"]}


OPS = set(operations.names())
ONE = sorted(OPS)[0]


def _resolve_2(steps, **kw):
    return breakpoints.resolve(known_steps=steps, **kw)


# ── What is armed ───────────────────────────────────────────────────

def test_arming_plain_review_and_per_step(steps):
    """A plain run stops nowhere; --review is the every-step case
    (wherever the run goes); --break arms that step and nothing else."""
    gates = _resolve_2(steps)
    assert not gates.any_armed
    assert not any(gates.armed_at(s) for s in steps)
    assert "none" in gates.describe()[0]

    gates = _resolve_2(steps, review_all=True)
    assert gates.every_step
    assert all(gates.armed_at(s) for s in steps)
    assert gates.armed_at("a step that does not exist")

    gates = _resolve_2(steps, break_at=("review_rough_cut",))
    assert gates.armed_at("review_rough_cut")
    assert not gates.armed_at("catalog")
    assert not gates.every_step


def test_no_break_star_disarms_the_lot(steps):
    """"Run my project's profile but do not stop anywhere" - the one
    thing that could not be said before at all."""
    gates = _resolve_2(steps, profile_breakpoints=(EVERY_STEP, "catalog"),
                     review_all=True, break_at=("scan",),
                     no_break_at=(EVERY_STEP,))
    assert not gates.any_armed
    assert not gates.every_step
    assert "disarmed every breakpoint" in gates.basis[0]


# ── What is refused ──────────────────────────────────────────────────

def test_an_unknown_address_is_refused_by_name(steps):
    """An unknown step lists the known steps; an unknown operation lists
    the known operations; a bad region is refused by `region.parse`, the
    one parser, in its own words."""
    with pytest.raises(BreakpointError) as exc:
        _resolve_2(steps, break_at=("rough_cut",))
    assert "rough_cut" in str(exc.value)
    assert "Known steps" in str(exc.value)

    with pytest.raises(BreakpointError) as exc:
        breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                            break_at=("nope.jog@1.0-2.0",))
    assert "nope.jog" in str(exc.value)
    assert ONE in str(exc.value), "the known operations are listed"

    with pytest.raises(BreakpointError) as exc:
        breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                            break_at=(f"{ONE}@notaspan",))
    assert "notaspan" in str(exc.value)


# ── An unreachable breakpoint is reported, never silent ──────────────


def test_an_unreachable_breakpoint_does_not_refuse_the_run(steps):
    """A breakpoint strands no consumer, so refusing would make
    `--profile podcast --only catalog` impossible for no gain."""
    gates = _resolve_2(steps, break_at=("render",))
    assert gates.any_armed


# ── The record a later reader gets ───────────────────────────────────


def test_the_resume_command_drops_a_rerun_that_already_happened():
    """Found by driving the loop on 001. `--rerun` clears a ledger entry,
    so carrying it into the resume clears the entry the pause just wrote,
    re-runs the step, re-arms its breakpoint and stops in the same place
    - a loop the captain cannot get out of by following the instruction
    the pipeline printed."""
    argv = ["run_pipeline.py", "--project", "/p",
            "--rerun", "scan", "--rerun", "catalog", "--break", "scan"]
    command = breakpoints.resume_command(argv, "python3")
    assert "--rerun" not in command
    assert "scan" in command, "the breakpoint that armed the pause survives"
    assert "--break scan" in command
    assert command.endswith("--resume")
    # `--rerun=scan` is the same request spelled with an equals sign.
    assert "--rerun" not in breakpoints.resume_command(
        ["run.py", "--rerun=scan", "--break", "scan"], "python3")


# ── A breakpoint may name an OPERATION, at a region ─────────────────


def test_a_breakpoint_may_be_an_operation_address():
    gates = breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                       break_at=(f"{ONE}@45.0-72.0",))
    assert gates.armed_at(f"{ONE}@45.0-72.0")
    assert not gates.armed_at("render")
    # The other direction must NOT hold, or a region breakpoint is a
    # whole-operation one wearing a disguise.
    assert not gates.armed_at(ONE)
    assert not gates.armed_at(f"{ONE}@0.0-1.0")


# --------------------------------------------------------------------------
# From test_run_profile.py
#
# A run configuration is DECLARED, and it cannot say anything
# `run_scope` would refuse.
#
# The captain, 2026-08-30: "what if i want to setup specific breakpoints
# and such for a given run and/or enable/disable specific steps because
# they are not needed (ex: a podcast may not need anything but
# colorgrading and transitions after the rough cut ...) and i should be
# able to configure it as such".
#
# Two things are worth testing hardest:
#
# * the profile has NO POWER OF ITS OWN.  It composes a
#   `run_scope.Selection` and hands it to the same resolver a flag does,
#   so the dependency refusal, the hard/soft edge derivation and the
#   pre-run failure all still apply.  The test for that is a profile that
#   asks for something impossible getting the SAME refusal the flags get.
# * the composition rule - a step named on the command line outranks the
#   profile - because it is the only place two declarations meet.

@pytest.fixture(scope="module")
def steps_2(dag):
    return {n["id"] for n in dag["nodes"]}


def _write_profile(directory: Path, name: str, body: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    """A project folder with nothing in it but a project.yaml."""
    (tmp_path / "project.yaml").write_text("name: test\n", encoding="utf-8")
    return tmp_path


def _profiles_dir(project) -> Path:
    return ProjectLayout(project).read_dir(Area.RUN_PROFILES)


# ── The engine's own profiles are real ───────────────────────────────


def test_the_podcast_profile_says_what_the_captain_asked_for(dag, manifests,
                                                             steps_2):
    """Colour grade and transitions after the rough cut; no VFX, no SFX,
    no motion graphics, no cohesion review, no finished-master QA."""
    profile = run_profile.load("podcast", known_steps=steps_2)
    scope = run_scope.resolve(run_profile.compose(profile), dag=dag,
                              manifests=manifests, state=None, external={})
    assert "color_grade" in scope.steps_to_run
    assert "plan_transitions" in scope.steps_to_run
    assert "review_rough_cut" in scope.steps_to_run
    for left_out in ("plan_vfx", "plan_sfx", "render_motion_graphics",
                     "creative_cohesion", "validate", "validate_sfx_library"):
        assert left_out in scope.skipped, f"{left_out} should be left out"
    assert profile.breakpoints == ("review_rough_cut",)


# ── A profile has no power of its own ────────────────────────────────

def test_a_profile_gets_the_same_refusal_the_flags_get(project, dag,
                                                       manifests, steps_2):
    """The whole point. A profile that excludes a hard producer is
    refused BEFORE the run, by `run_scope`, in the same words - so a
    declared configuration cannot express a selection the flags could
    not."""
    _write_profile(_profiles_dir(project), "impossible", {
        "description": "Asks for the render without the creative direction "
                       "eleven steps declare required.",
        "goals": ["render"],
        "skip": ["creative_direction"],
    })
    profile = run_profile.load("impossible", str(project), steps_2)

    with pytest.raises(run_scope.ScopeError) as declared:
        run_scope.resolve(run_profile.compose(profile), dag=dag,
                          manifests=manifests, state=None, external={})
    with pytest.raises(run_scope.ScopeError) as by_flag:
        run_scope.resolve(
            run_scope.Selection(only=("render",), skip=("creative_direction",)),
            dag=dag, manifests=manifests, state=None, external={})

    assert str(declared.value) == str(by_flag.value)
    assert "Refusing before the run starts" in str(declared.value)
    assert "creative_direction" in str(declared.value)


# ── Where a profile lives ────────────────────────────────────────────


def test_a_project_profile_shadows_the_engines_and_says_so(project, steps_2):
    """Shadowing is legitimate - a series may want its own `podcast` -
    but a shadow nobody can see is a shadow nobody expected."""
    _write_profile(_profiles_dir(project), "podcast",
                   {"description": "this project's own", "goals": ["catalog"]})
    profile = run_profile.load("podcast", str(project), steps_2)
    assert profile.source == run_profile.PROJECT
    assert profile.goals == ("catalog",)
    hidden = run_profile.shadowed(str(project))
    assert "podcast" in hidden
    assert any("shadows the engine" in line
               for line in run_profile.describe_available(str(project)))


# ── Adoption, and declining it ───────────────────────────────────────

def test_a_project_adopts_a_profile_in_its_project_yaml(project, steps_2):
    (project / "project.yaml").write_text(
        "name: test\npipeline:\n  run_profile: podcast\n", encoding="utf-8")
    profile = run_profile.resolve_for_run(str(project), None, steps_2)
    assert profile.name == "podcast"
    assert profile.adopted is True
    assert any("adopted by project.yaml" in line
               for line in run_profile.describe(profile))

    # `none` declines the adopted profile for one run.
    profile = run_profile.resolve_for_run(str(project), "none", steps_2)
    assert profile is run_profile.NO_PROFILE
    assert run_profile.describe(profile) == []


# ── The command line outranks the profile ────────────────────────────

def _profile(**kw) -> RunProfile:
    return RunProfile(name="p", description="d", path="p.yaml",
                      source=run_profile.ENGINE, **kw)


def test_the_command_line_outranks_the_profile():
    """--only and --target replace the profile's goals; --skip adds to
    its skip list; naming a step takes it out of the profile's skip list
    (a profile's skip is a DEFAULT, a step on the command line is a
    STATEMENT - the precedence `run_scope` gives a default-off step)."""
    selection = run_profile.compose(_profile(goals=("render",)),
                                    only=("catalog",))
    assert selection.only == ("catalog",)

    selection = run_profile.compose(_profile(goals=("render",)),
                                    target="rough_cut_subtitles")
    assert selection.target == "rough_cut_subtitles"
    assert selection.only == ()

    selection = run_profile.compose(_profile(skip=("validate",)),
                                    skip=("creative_cohesion",))
    assert set(selection.skip) == {"validate", "creative_cohesion"}

    selection = run_profile.compose(_profile(skip=("ocr_extraction",)),
                                    with_steps=("ocr_extraction",))
    assert selection.skip == ()
    assert selection.with_steps == ("ocr_extraction",)


# ── Refusals, by name ────────────────────────────────────────────────

def test_a_malformed_profile_is_refused_by_name(project, steps_2):
    with pytest.raises(ProfileError) as exc:
        run_profile.load("nosuch", str(project))
    assert "nosuch" in str(exc.value)
    assert "Known profiles" in str(exc.value)

    _write_profile(_profiles_dir(project), "mute", {"goals": ["catalog"]})
    with pytest.raises(ProfileError, match="description"):
        run_profile.load("mute", str(project), steps_2)

    _write_profile(_profiles_dir(project), "both", {
        "description": "d", "target": "rough_cut_subtitles",
        "goals": ["catalog"]})
    with pytest.raises(ProfileError, match="one question"):
        run_profile.load("both", str(project), steps_2)

    (project / "project.yaml").write_text(
        "name: test\npipeline:\n  run_profile: none\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="DECLINES"):
        run_profile.resolve_for_run(str(project), None, steps_2)


# ── The runner really accepts it ─────────────────────────────────────


# --------------------------------------------------------------------------
# From test_full_auto_agent_alias.py
#
# The `--full-auto agy` mode is named `agent`: the mechanism (the pipeline
# writes a request file, an agent answers it) rather than the vendor that
# used to supply the agent.
#
# `agy` stays working as a deprecated alias: scripts, briefs, saved commands
# and muscle memory all pass it today.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.processes.edit_video.run_pipeline import (
    build_parser,
    present_llm_step,
    LLMError,
)


def test_runner_help_hides_compatibility_spellings_but_still_parses_them():
    parser = build_parser()
    help_text = parser.format_help()
    assert "agy" not in help_text
    assert "api" not in help_text
    assert "--full-auto BACKEND" in help_text

    agy = parser.parse_args(["--project", "/tmp/project", "--full-auto", "agy"])
    api = parser.parse_args(["--project", "/tmp/project", "--full-auto", "api"])
    assert agy.full_auto == "agy"
    assert api.full_auto == "api"


def _write_handoff(project_dir: Path) -> str:
    prompt_path = project_dir / "handoff.md"
    prompt_path.write_text("Test prompt")
    return str(prompt_path)


def test_full_auto_agy_alias_still_writes_request(tmp_path, capsys):
    """The deprecated alias reaches the same mechanism, and says so."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    inputs = {"project_folder": str(project_dir)}

    with patch("library.tools.model_task._agent_sleep"), patch(
        "library.tools.model_task._agent_clock", side_effect=[0, 0, 0, 10, 10, 10, 10, 10]
    ):
        with pytest.raises(LLMError, match="Timeout"):
            present_llm_step(
                _write_handoff(project_dir), inputs, "test_step",
                full_auto="agy", llm_timeout=1)

    assert (project_dir / "pipeline_output"
            / "llm_requests" / "test_step.json").exists()
    err = capsys.readouterr().err
    assert "deprecated" in err
    assert "agent" in err


def test_full_auto_api_is_refused_with_a_fix(tmp_path):
    """`--full-auto api` is gone: provider API calls are out of scope.
    Passing the removed backend refuses in the refusal shape - naming
    the fix - rather than calling anything."""
    from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    inputs = {"project_folder": str(project_dir), "some_data": 123}
    manifest = {"interface": {"outputs": [{"name": "test_out"}]}}
    with pytest.raises(RenRefusal) as refused:
        present_llm_step(_write_handoff(project_dir), inputs, "test_step",
                         manifest, full_auto="api")
    assert refused.value.fix == "re-run with --full-auto agent"
    assert REFUSAL_EXIT_CODE == 4


# --------------------------------------------------------------------------
# From test_optional_edges.py
#
# Optional edges: the requirement layer can say what may or may not travel.
#
# Optionality is its own requirement kind (captain's ruling 2026-09-23,
# "add-optional"), never a loosened requirement. Both mechanisms are pinned
# by name: A, the edge is marked NOT REQUIRED (`creative_cohesion`,
# `prosody_analysis`, `color_grade`, `ocr_extraction`); B, the producer
# declares the key for a DIFFERENT consumer (`plan_transitions`, `plan_sfx`,
# `plan_vfx`). A repair covering only A leaves three nodes broken.

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from library.tools import composer as C
from library.tools import operations as O

def test_every_optional_edge_composes_to_its_producer():
    """Each optional goal plans the capability that produces it - which
    also means none of the producers derives an empty effect (the
    composer works backwards through `effect`). Mechanism A, mechanism B,
    and the seventh node, `cohesion.review`, whose goal closes once the
    capability is registered."""
    rows = {
        "optional.creative_direction.prosody_analysis": "prosody.analyse",
        "optional.speech_sequence.prosody_analysis": "prosody.analyse",
        "optional.compile_manifest.color_grade_spec": "color_grade.resolve",
        "optional.ocr_extraction.ocr_extraction": "ocr.extract",
        "optional.compile_manifest.transition_spec": "transitions.resolve",
        "optional.compile_manifest.sfx_spec": "sfx.resolve",
        "optional.compile_manifest.enhancement_spec": "vfx.resolve",
    }
    for goal, producer in rows.items():
        comp = C.compose(goal)
        assert producer in comp.operations, (goal, comp.operations)
        assert O.get(producer).effect, producer
    assert not set(rows.values()) & set(O.EMPTY_EFFECT_REASONS)

    comp = C.compose("optional.compile_manifest.cohesion_review")
    assert comp.completed
    assert comp.operations[-1] == "cohesion.review"


# --------------------------------------------------------------------------
# From test_may_be_empty.py
#
# Tests for the may_be_empty manifest flag.
#
# A step must be able to declare that an empty output is a legitimate
# answer, and the pipeline must stop treating that as a defect. At the
# same time, a step that has NOT declared the flag must still fail on
# empty output - the check does real work everywhere else.

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from library.processes.edit_video.run_pipeline import validate_step_output


# ── Real manifests: vfx and sfx declare the flag, others do not ─────

STEPS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "library", "steps"
)


def _load_manifest(step_dir_name: str) -> dict:
    path = os.path.join(STEPS_DIR, step_dir_name, "manifest.json")
    with open(path) as f:
        return json.load(f)


def test_plan_vfx_empty_list_passes_qa():
    """An empty vfx_creative passes the same validation the QA loop runs."""
    manifest = _load_manifest("step_4_03_plan_vfx")
    llm_manifest = dict(manifest)
    llm_manifest["interface"] = dict(manifest["interface"])
    llm_manifest["interface"]["outputs"] = manifest["interface"]["llm_outputs"]
    issues = validate_step_output("plan_vfx", {"vfx_creative": []}, llm_manifest)
    assert not issues, (
        f"An empty VFX plan is a correct creative answer and must not be "
        f"rejected as semantically empty: {issues}"
    )


# Steps that must NOT have the flag - verify the safe direction

_STEPS_WITHOUT_MAY_BE_EMPTY = [
    ("step_2_05_mesh_spine", "structure"),
]


@pytest.mark.parametrize("step_dir,key", _STEPS_WITHOUT_MAY_BE_EMPTY)
def test_steps_without_flag_still_reject_empty(step_dir, key):
    """Steps that must produce output still fail on an empty list."""
    manifest = _load_manifest(step_dir)
    llm_outputs = manifest["interface"]["llm_outputs"]
    spec = next(o for o in llm_outputs if o["name"] == key)
    assert not spec.get("may_be_empty"), (
        f"{step_dir}.{key} should NOT have may_be_empty"
    )
    # Build the same validation context the QA loop uses
    llm_manifest = dict(manifest)
    llm_manifest["interface"] = dict(manifest["interface"])
    llm_manifest["interface"]["outputs"] = llm_outputs
    issues = validate_step_output(step_dir, {key: []}, llm_manifest)
    assert any("semantically empty" in i for i in issues), (
        f"Expected {step_dir}.{key} to fail on empty output, got: {issues}"
    )
