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




# ── Where a profile lives ────────────────────────────────────────────



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




def test_none_declines_the_adopted_profile_for_one_run(project, steps):
    (project / "project.yaml").write_text(
        "name: test\npipeline:\n  run_profile: podcast\n", encoding="utf-8")
    profile = run_profile.resolve_for_run(str(project), "none", steps)
    assert profile is run_profile.NO_PROFILE
    assert run_profile.describe(profile) == []




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




# ── Refusals, by name ────────────────────────────────────────────────

def test_an_unknown_profile_is_refused_by_name(project):
    with pytest.raises(ProfileError) as exc:
        run_profile.load("nosuch", str(project))
    assert "nosuch" in str(exc.value)
    assert "Known profiles" in str(exc.value)








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














# ── The runner really accepts it ─────────────────────────────────────



