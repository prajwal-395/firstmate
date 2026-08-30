"""A run configuration is DECLARED, and it cannot say anything
`run_scope` would refuse.

The captain, 2026-08-30: "what if i want to setup specific breakpoints
and such for a given run and/or enable/disable specific steps because
they are not needed (ex: a podcast may not need anything but
colorgrading and transitions after the rough cut ...) and i should be
able to configure it as such".

Two things are worth testing hardest:

* the profile has NO POWER OF ITS OWN.  It composes a
  `run_scope.Selection` and hands it to the same resolver a flag does,
  so the dependency refusal, the hard/soft edge derivation and the
  pre-run failure all still apply.  The test for that is a profile that
  asks for something impossible getting the SAME refusal the flags get.
* the composition rule - a step named on the command line outranks the
  profile - because it is the only place two declarations meet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from library.tools import run_profile, run_scope
from library.tools.project_layout import Area, ProjectLayout
from library.tools.run_profile import ProfileError, RunProfile


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def dag():
    return run_scope.load_dag()


@pytest.fixture(scope="module")
def manifests(dag):
    return run_scope.load_manifests(dag)


@pytest.fixture(scope="module")
def steps(dag):
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

def test_the_engine_ships_at_least_the_podcast_profile():
    """The captain's example, expressible without a code change. Before
    this it would have meant editing `run_scope.TARGETS`."""
    catalogue = run_profile.available()
    assert "podcast" in catalogue
    assert catalogue["podcast"].source == run_profile.ENGINE


def test_every_engine_profile_loads_and_resolves(dag, manifests, steps):
    """A shipped profile that refuses on every project is not a profile.
    Each one is loaded, composed and put through the real resolver."""
    for name in sorted(run_profile.available()):
        profile = run_profile.load(name, known_steps=steps)
        selection = run_profile.compose(profile)
        scope = run_scope.resolve(selection, dag=dag, manifests=manifests,
                                  state=None, external={})
        assert scope.steps_to_run, f"{name} selects no steps"
        assert profile.description, f"{name} has no description"


def test_the_podcast_profile_says_what_the_captain_asked_for(dag, manifests,
                                                             steps):
    """Colour grade and transitions after the rough cut; no VFX, no SFX,
    no motion graphics, no cohesion review, no finished-master QA."""
    profile = run_profile.load("podcast", known_steps=steps)
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
                                                       manifests, steps):
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
    profile = run_profile.load("impossible", str(project), steps)

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


def test_a_profile_carries_only_the_words_a_selection_has(project, steps):
    """`compose` returns a `run_scope.Selection` and nothing else, so
    there is no second vocabulary for what a selection means."""
    _write_profile(_profiles_dir(project), "narrow", {
        "description": "d", "goals": ["catalog"], "skip": ["validate"],
        "with": ["ocr_extraction"],
    })
    selection = run_profile.compose(
        run_profile.load("narrow", str(project), steps))
    assert isinstance(selection, run_scope.Selection)
    assert selection.only == ("catalog",)
    assert selection.skip == ("validate",)
    assert selection.with_steps == ("ocr_extraction",)


# ── Where a profile lives ────────────────────────────────────────────

def test_a_project_may_declare_its_own(project, steps):
    _write_profile(_profiles_dir(project), "house_style",
                   {"description": "d", "goals": ["catalog"]})
    catalogue = run_profile.available(str(project))
    assert catalogue["house_style"].source == run_profile.PROJECT
    assert "podcast" in catalogue, "engine profiles are still available"


def test_a_project_profile_shadows_the_engines_and_says_so(project, steps):
    """Shadowing is legitimate - a series may want its own `podcast` -
    but a shadow nobody can see is a shadow nobody expected."""
    _write_profile(_profiles_dir(project), "podcast",
                   {"description": "this project's own", "goals": ["catalog"]})
    profile = run_profile.load("podcast", str(project), steps)
    assert profile.source == run_profile.PROJECT
    assert profile.goals == ("catalog",)
    hidden = run_profile.shadowed(str(project))
    assert "podcast" in hidden
    assert any("shadows the engine" in line
               for line in run_profile.describe_available(str(project)))


def test_the_profiles_directory_is_an_input_the_pipeline_cannot_write(project):
    """A run configuration is the captain's, the same way `raw/` is."""
    from library.tools.project_layout import ProjectLayoutViolation

    layout = ProjectLayout(project)
    with pytest.raises(ProjectLayoutViolation):
        layout.write_dir(Area.RUN_PROFILES)


# ── Adoption, and declining it ───────────────────────────────────────

def test_a_project_adopts_a_profile_in_its_project_yaml(project, steps):
    (project / "project.yaml").write_text(
        "name: test\npipeline:\n  run_profile: podcast\n", encoding="utf-8")
    profile = run_profile.resolve_for_run(str(project), None, steps)
    assert profile.name == "podcast"
    assert profile.adopted is True
    assert any("adopted by project.yaml" in line
               for line in run_profile.describe(profile))


def test_a_run_may_name_a_different_one(project, steps):
    (project / "project.yaml").write_text(
        "name: test\npipeline:\n  run_profile: podcast\n", encoding="utf-8")
    _write_profile(_profiles_dir(project), "quick",
                   {"description": "d", "goals": ["catalog"]})
    profile = run_profile.resolve_for_run(str(project), "quick", steps)
    assert profile.name == "quick"
    assert profile.adopted is False


def test_none_declines_the_adopted_profile_for_one_run(project, steps):
    (project / "project.yaml").write_text(
        "name: test\npipeline:\n  run_profile: podcast\n", encoding="utf-8")
    profile = run_profile.resolve_for_run(str(project), "none", steps)
    assert profile is run_profile.NO_PROFILE
    assert run_profile.describe(profile) == []


def test_a_project_declaring_nothing_gets_nothing(project, steps):
    profile = run_profile.resolve_for_run(str(project), None, steps)
    assert profile is run_profile.NO_PROFILE
    assert run_profile.compose(profile) == run_scope.Selection()


def test_a_project_cannot_adopt_the_word_that_declines_one(project, steps):
    (project / "project.yaml").write_text(
        "name: test\npipeline:\n  run_profile: none\n", encoding="utf-8")
    with pytest.raises(ProfileError) as exc:
        run_profile.resolve_for_run(str(project), None, steps)
    assert "DECLINES" in str(exc.value)


# ── The command line outranks the profile ────────────────────────────

def _profile(**kw) -> RunProfile:
    return RunProfile(name="p", description="d", path="p.yaml",
                      source=run_profile.ENGINE, **kw)


def test_only_replaces_the_profiles_goals():
    selection = run_profile.compose(_profile(goals=("render",)),
                                    only=("catalog",))
    assert selection.only == ("catalog",)


def test_target_replaces_the_profiles_goals():
    selection = run_profile.compose(_profile(goals=("render",)),
                                    target="rough_cut_subtitles")
    assert selection.target == "rough_cut_subtitles"
    assert selection.only == ()


def test_skip_adds_to_the_profiles():
    selection = run_profile.compose(_profile(skip=("validate",)),
                                    skip=("creative_cohesion",))
    assert set(selection.skip) == {"validate", "creative_cohesion"}


def test_naming_a_step_takes_it_out_of_the_profiles_skip_list():
    """`run_scope` refuses a selection that both skips and selects a
    step. A profile's skip is a DEFAULT; a step on the command line is a
    STATEMENT, which is the same precedence `run_scope` already gives a
    step that is off by default."""
    selection = run_profile.compose(_profile(skip=("ocr_extraction",)),
                                    with_steps=("ocr_extraction",))
    assert selection.skip == ()
    assert selection.with_steps == ("ocr_extraction",)


def test_a_command_line_that_says_two_things_is_still_a_contradiction(
        dag, manifests):
    """Saying `--skip X --only X` is refused by `run_scope`, and nothing
    in the composition papers over it."""
    selection = run_profile.compose(run_profile.NO_PROFILE,
                                    only=("catalog",), skip=("catalog",))
    with pytest.raises(run_scope.ScopeError) as exc:
        run_scope.resolve(selection, dag=dag, manifests=manifests,
                          state=None, external={})
    assert "Say it once" in str(exc.value)


# ── Refusals, by name ────────────────────────────────────────────────

def test_an_unknown_profile_is_refused_by_name(project):
    with pytest.raises(ProfileError) as exc:
        run_profile.load("nosuch", str(project))
    assert "nosuch" in str(exc.value)
    assert "Known profiles" in str(exc.value)


def test_an_unknown_key_is_refused_by_name(project, steps):
    _write_profile(_profiles_dir(project), "typo",
                   {"description": "d", "breakpoint": ["catalog"]})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("typo", str(project), steps)
    assert "breakpoint" in str(exc.value)


def test_an_unknown_step_is_refused_by_name(project, steps):
    _write_profile(_profiles_dir(project), "bad",
                   {"description": "d", "goals": ["not_a_step"]})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("bad", str(project), steps)
    assert "not_a_step" in str(exc.value)


def test_an_unknown_breakpoint_step_is_refused_by_name(project, steps):
    _write_profile(_profiles_dir(project), "bad",
                   {"description": "d", "breakpoints": ["not_a_step"]})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("bad", str(project), steps)
    assert "not_a_step" in str(exc.value)


def test_a_profile_with_no_description_is_refused(project, steps):
    _write_profile(_profiles_dir(project), "mute", {"goals": ["catalog"]})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("mute", str(project), steps)
    assert "description" in str(exc.value)


def test_declaring_both_a_target_and_goals_is_refused(project, steps):
    _write_profile(_profiles_dir(project), "both", {
        "description": "d", "target": "rough_cut_subtitles",
        "goals": ["catalog"]})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("both", str(project), steps)
    assert "one question" in str(exc.value)


def test_an_unknown_target_is_refused_by_name(project, steps):
    _write_profile(_profiles_dir(project), "bad",
                   {"description": "d", "target": "nosuch"})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("bad", str(project), steps)
    assert "nosuch" in str(exc.value)


def test_a_name_that_disagrees_with_the_filename_is_refused(project, steps):
    _write_profile(_profiles_dir(project), "filed_as",
                   {"name": "called", "description": "d"})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("filed_as", str(project), steps)
    assert "must agree" in str(exc.value)


def test_a_profile_may_not_be_called_none(project, steps):
    _write_profile(_profiles_dir(project), "none", {"description": "d"})
    catalogue = run_profile.available(str(project))
    assert "none" in catalogue
    with pytest.raises(ProfileError):
        run_profile._parse(catalogue["none"], steps)


def test_a_single_string_where_a_list_belongs_is_refused(project, steps):
    _profiles_dir(project).mkdir(parents=True, exist_ok=True)
    (_profiles_dir(project) / "s.yaml").write_text(
        "description: d\ngoals: render\n", encoding="utf-8")
    with pytest.raises(ProfileError) as exc:
        run_profile.load("s", str(project), steps)
    assert "must be a list" in str(exc.value)


def test_a_step_both_a_goal_and_skipped_is_refused(project, steps):
    _write_profile(_profiles_dir(project), "contra", {
        "description": "d", "goals": ["catalog"], "skip": ["catalog"]})
    with pytest.raises(ProfileError) as exc:
        run_profile.load("contra", str(project), steps)
    assert "Say it once" in str(exc.value)


def test_an_empty_profile_is_refused(project, steps):
    _profiles_dir(project).mkdir(parents=True, exist_ok=True)
    (_profiles_dir(project) / "empty.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ProfileError) as exc:
        run_profile.load("empty", str(project), steps)
    assert "empty" in str(exc.value)


# ── The runner really accepts it ─────────────────────────────────────

def test_the_runner_and_the_wrapper_register_the_same_flag():
    """`manage_project.py run` forwards to `run_pipeline.py`, so a flag
    defined twice is a flag that will eventually differ. Both take it
    from `add_profile_arguments`."""
    import argparse

    wrapper = argparse.ArgumentParser()
    runner = argparse.ArgumentParser()
    run_profile.add_profile_arguments(wrapper)
    run_profile.add_profile_arguments(runner)
    assert wrapper.parse_args(["--profile", "podcast"]).profile == "podcast"
    assert runner.parse_args(["--profile", "podcast"]).profile == "podcast"


def test_manage_project_forwards_the_configuration_to_the_runner():
    """The wrapper builds the runner's argv, so a flag it accepts and
    does not forward is a flag that silently does nothing."""
    source = (REPO_ROOT / "manage_project.py").read_text(encoding="utf-8")
    for flag in ('"--profile"', '"--break"', '"--no-break"'):
        assert f'cmd.extend([{flag}' in source, f"{flag} is not forwarded"
