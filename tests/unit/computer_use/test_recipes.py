"""The recipe store: verified scripts run before computer-use planning.

Each test names the defect it catches: a route that runs where it was
not verified, a success count a failure does not reset, a planner the
loop asks when a recipe already committed.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.loop import run_loop
from library.tools.computer_use.scripts import (
    Anchor,
    RecipeStore,
    Script,
    ScriptStep,
    run_recipe,
)


def _press_media() -> Script:
    return Script(
        goal="switch to the Media page",
        steps=[
            ScriptStep(
                action="press",
                anchors=[Anchor(
                    via="ax_path",
                    params={"path": ["Workspace", "Switch to Page", "Media"]},
                )],
            ),
        ],
    )


def test_save_and_load_round_trip_the_verified_script(tmp_path):
    """A saved recipe loads back its steps, layout and a zero count -
    the store persists the route, not just its name. A store that lost
    the steps would execute an empty script and call it done."""
    store = RecipeStore(tmp_path / "recipes")
    store.save("media-page", _press_media(), layout="abc123")
    loaded = store.load("media-page")
    assert loaded is not None
    script, record = loaded
    assert script.to_dict() == _press_media().to_dict()
    assert record["layout"] == "abc123"
    assert record["successes"] == 0
    assert store.is_promoted("media-page") is False


def test_a_name_that_escapes_the_store_is_refused(tmp_path):
    """A recipe name is a filename - `../` must not write outside the
    store. A store that joined names blindly would let a recipe
    overwrite anything the runner can write."""
    store = RecipeStore(tmp_path / "recipes")
    with pytest.raises(ValueError):
        store.save("../outside", _press_media(), layout=None)


def test_an_unknown_recipe_is_the_planners_cue_not_an_error(tmp_path,
                                                            client: DriverClient):
    """No stored route loads as None, and run_recipe answers None -
    the loop plans instead. A store that raised here would turn every
    first run of a new task into a failure instead of a discovery."""
    store = RecipeStore(tmp_path / "recipes")
    assert store.load("never-saved") is None
    assert run_recipe(client, store, "never-saved") is None


def test_a_moved_layout_replans_instead_of_running(tmp_path,
                                                   client: DriverClient):
    """A recipe verified on one layout does not run on another: the
    controls may have moved, and a stale route clicks where they were.
    The run records nothing - an unrunnable route must not count
    toward promotion."""
    store = RecipeStore(tmp_path / "recipes")
    store.save("media-page", _press_media(), layout="abc123")
    assert run_recipe(client, store, "media-page", layout="moved456") is None
    assert store.load("media-page")[1]["successes"] == 0


def test_three_committed_runs_promote_and_a_failure_resets(tmp_path,
                                                           client: DriverClient):
    """Promotion counts consecutive committed runs: three in a row
    promotes, and a failure resets to zero. A counter a failure did
    not reset would promote a flaky route on its old successes."""
    store = RecipeStore(tmp_path / "recipes")
    store.save("media-page", _press_media(), layout="abc123")
    assert run_recipe(client, store, "media-page", layout="abc123").outcome == "done"
    assert store.is_promoted("media-page") is False
    assert run_recipe(client, store, "media-page", layout="abc123").outcome == "done"
    assert run_recipe(client, store, "media-page", layout="abc123").outcome == "done"
    assert store.is_promoted("media-page") is True
    assert store.record_result("media-page", False) == 0
    assert store.is_promoted("media-page") is False


def test_the_loop_runs_the_known_recipe_without_asking_the_model(
        tmp_path, client: DriverClient):
    """The plan's section 4: a known recipe executes before any
    planning. A loop that asked the model first would spend inference
    on a route already verified deterministic."""
    store = RecipeStore(tmp_path / "recipes")
    store.save("media-page", _press_media(), layout="abc123")

    def model(obs):
        raise AssertionError("the planner was asked despite the recipe")

    result = run_loop(client, model, "switch to the Media page",
                      recipe="media-page", store=store, layout="abc123")
    assert result.outcome == "done"
    assert result.final_action == "recipe:media-page"
    assert store.load("media-page")[1]["successes"] == 1


def test_the_loop_plans_where_no_recipe_runs(tmp_path, client: DriverClient):
    """An unknown recipe name falls through to the planner - the loop
    discovers the route. A loop that failed here could never learn a
    first route for a new task."""
    store = RecipeStore(tmp_path / "recipes")
    seen = []

    def model(obs):
        seen.append(obs)
        return {"action": "done"}

    result = run_loop(client, model, "switch to the Media page",
                      recipe="never-saved", store=store, layout="abc123")
    assert result.outcome == "done"
    assert seen, "the planner was never asked"
