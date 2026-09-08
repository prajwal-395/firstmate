"""A step that makes a craft judgement is told what craft it is.

Measured 2026-08-25: not one of the pipeline's handoffs told the model
what job it was doing. `library/tools/craft_role.py` answers that, and
these hold the three things that make it trustworthy - that a role really
reaches the assembled prompt, that a step which starts reaching a model
cannot go quiet about not having one, and that the bench mirrors it.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import craft_role  # noqa: E402
from library.tools.undetermined import DECLARING_STEPS  # noqa: E402


def test_every_model_reaching_step_says_which_side_it_is_on():
    accounted = set(craft_role.ROLES) | set(craft_role.WITHOUT_A_DECLARED_ROLE)
    assert accounted == set(DECLARING_STEPS), (
        "craft_role and undetermined.DECLARING_STEPS disagree about which "
        "steps reach a model. Every one has a declared role or a recorded "
        "reason it does not."
    )


def test_the_membership_guard_can_actually_fire(monkeypatch):
    """A gate that cannot fail reads as coverage (AGENTS.md 10.4)."""
    monkeypatch.setattr(craft_role, "_REACHES_A_MODEL",
                        frozenset(DECLARING_STEPS) | {"a_new_step"})
    with pytest.raises(RuntimeError, match="a_new_step"):
        craft_role._assert_roles_account_for_every_model_reaching_step()


def test_a_step_with_no_role_renders_nothing():
    """The call site is one unconditional line, so the empty case has to
    be empty rather than a heading with nothing under it."""
    assert craft_role.prompt_block("validate") == ""


def test_the_role_is_prepended_to_the_prompt_the_model_reads(tmp_path,
                                                             monkeypatch):
    """The regression this pins: a role module nothing calls.

    Asserted on the ARCHIVED request file rather than on the module,
    because that file is what an answering agent reads.
    """
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for

    node_id = "plan_sfx"
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()

    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nSENTINEL_HANDOFF_BODY\n", encoding="utf-8")

    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = run_pipeline.time.sleep

    def _sleep(seconds):
        (responses / f"{node_id}.json").write_text(
            json.dumps({"sfx_creative": []}))
        return original_sleep(0)

    monkeypatch.setattr(run_pipeline.time, "sleep", _sleep)

    run_pipeline.present_llm_step(
        str(handoff),
        {"project_folder": str(project)},
        node_id,
        {"interface": {"outputs": [{"name": "sfx_creative", "type": "list"}]}},
        full_auto="agy", llm_timeout=5)

    prompt = json.loads(
        (layout.read_dir(Area.LLM_REQUESTS)
         / f"{node_id}.json").read_text())["prompt"]

    role = craft_role.ROLES[node_id]
    assert role.addressed_as in prompt
    # The correction to the frozen handoff travels with the role, which is
    # the only route it has - the handoff is not ours to edit.
    for correction in role.corrects:
        assert correction in prompt
    # PREPENDED: the frame comes before the document it frames.
    assert prompt.index(role.addressed_as) < prompt.index(
        "SENTINEL_HANDOFF_BODY")


def test_the_replay_bench_mirrors_the_role():
    """A prompt contribution the bench does not mirror makes `verify`
    report every role-carrying step as an unaccounted difference."""
    source = (REPO / "library/tools/replay_bench/reconstruct.py").read_text(
        encoding="utf-8")
    assert "craft_role.prompt_block(node_id) + prompt" in source


# ── The module's own prose states the true counts ─────────────────────
#
# 2026-09-07: the four roles PR #650 added updated the tables and neither
# count-bearing sentence in this module's docstring, so it claimed "two
# are declared; ten are not" against tables holding eight and six. A
# declaration must be true (AGENTS.md 3); these pin both sentences to the
# tables, so the next role-adding change trips them instead of silently
# dating the prose. Expected values are DERIVED from the tables, never
# copied literals.

_NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty",
}


def _word(n: int) -> str:
    assert n in _NUMBER_WORDS, (
        f"more model-reaching steps than this guard has words for ({n}); "
        f"extend _NUMBER_WORDS rather than wording the prose around it")
    return _NUMBER_WORDS[n]


def test_the_prose_states_how_many_roles_are_declared():
    doc = craft_role.__doc__.lower()
    assert f"{_word(len(craft_role.ROLES))} are declared" in doc, (
        "the relocated-rules prose no longer says how many roles are "
        f"declared (tables hold {len(craft_role.ROLES)}). A declaration "
        "must be true; update the sentence with the change that moved "
        "the count."
    )


def test_the_prose_states_how_many_steps_have_none():
    doc = craft_role.__doc__.lower()
    without = len(craft_role.WITHOUT_A_DECLARED_ROLE)
    total = len(craft_role.ROLES) + without
    assert f"{_word(without)} of the {_word(total)} are there today" in doc, (
        "the WITHOUT_A_DECLARED_ROLE census no longer matches the table. "
        "A declaration must be true; update the sentence with the change "
        "that moved the count."
    )
    assert f"{_word(without)} are not" in doc, (
        "the relocated-rules prose no longer says how many steps have no "
        f"declared role (tables hold {without}). A declaration must be "
        "true; update the sentence with the change that moved the count."
    )
