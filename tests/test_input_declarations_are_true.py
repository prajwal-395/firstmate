"""The declaration and the code must agree about an absent input.

Issue #260.  `compile_manifest` declared four specs REQUIRED while its
own code called three of them "optional enhancement specs" and read them
with defaults.  That is one instance of a class, and the known unknown
the captain named was whether any of the other twenty-five steps carried
the same defect.  This drives `library/tools/input_contract.py` over
every declared input of every step in the DAG and fails on any of the
three ways the two declarations can contradict each other.

What the survey can and cannot establish
----------------------------------------
It establishes ENFORCEMENT - who refuses when the input is absent - and
it establishes CONSUMPTION - whether the step's code or its prompt reads
it at all.  Both are mechanical.

It does not establish WARRANT.  `compile_manifest`'s four were enforced
perfectly and still wrong, and the only way to know is to run the step
without the input; `tests/test_compile_manifest_without_the_
decoration.py` does that for the one step that reads state directly
rather than taking the runner's word for it.  The survey reports the
rest as `enforced` and says so rather than calling it agreement.
"""

from __future__ import annotations

import pytest

from library.tools import input_contract, run_scope
from library.tools.input_contract import (
    REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT,
    UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT,
)


@pytest.fixture(scope="module")
def dag():
    """EVERY process's graph, merged for surveying.

    "A declaration must be true" is a rule about the repository, not
    about one pipeline, so it is asked of every step that has a node -
    which since `library/processes/reels` landed is more than
    edit_video's. A survey scoped to one graph would leave the newest
    manifests, the ones most likely to be wrong, unmeasured.
    """
    from library.tools import processes
    return processes.merged_dag()


@pytest.fixture(scope="module")
def manifests(dag):
    return run_scope.load_manifests(dag)


@pytest.fixture(scope="module")
def rows(dag, manifests):
    return input_contract.survey(dag, manifests)


# ── The three disagreements ─────────────────────────────────────────

# The graph-wide disagreement check is centralized in
# `tests/test_contract_audit.py::test_the_surveys_agree`; this file keeps
# focused cases for how the input survey classifies concrete code.

# ── The recorded tables name real declarations ──────────────────────


# ── The four that started it ────────────────────────────────────────

def test_the_four_decoration_specs_are_optional_and_nothing_refuses(rows):
    """#260's measurable consequence, read off the survey rather than
    off the manifest, so the row and the file cannot disagree."""
    by_key = {(r.node_id, r.name): r for r in rows}
    for name in ("transition_spec", "enhancement_spec", "sfx_spec",
                 "color_grade_spec"):
        row = by_key[("compile_manifest", name)]
        assert not row.required, f"{name} is still declared required"
        assert not row.step_refusal, (
            f"{name} is declared optional and {row.step_refusal} refuses "
            f"without it")
        assert row.consumed, f"{name} is optional and nothing reads it"


# ── The reading of the code, on cases with a known answer ───────────


def test_a_presence_guard_is_not_read_as_an_absence_refusal(rows):
    """`if broll_assignments or broll_interjections: raise` refuses WITH
    the value - a mutual exclusion under the declared tv_frame look, where
    the frame spans V2 and B-roll has nowhere to play - not without it.
    A run that leaves `b_roll_interjections` out compiles: the step reads
    it with a `[]` default and the guard only fires on presence. The
    declaration (OPTIONAL) is the true side; the survey misread the
    guard's polarity."""
    by_key = {(r.node_id, r.name): r for r in rows}
    row = by_key[("compile_manifest", "b_roll_interjections")]
    assert not row.required
    assert row.step_refusal == ""
    assert row.refused_by == input_contract.REFUSED_BY_NOBODY


def test_a_get_passed_into_a_call_is_not_a_binding(rows):
    """`result = validate_output(..., input_data.get("project_folder"))`
    followed by `if not result...: sys.exit` is a guard on the RESULT.
    Reading it as a guard on `project_folder` reported `validate` as
    refusing an input it declares optional."""
    by_key = {(r.node_id, r.name): r for r in rows}
    row = by_key[("validate", "project_folder")]
    assert row.step_refusal == ""


def test_a_view_carries_its_source_input_to_the_prompt(rows):
    """`creative_direction` declares `temporal_index` and selects
    `view:transcript`, not the input's own name - no `context_fields`
    entry names `temporal_index` by a dot path. A survey that read only
    dot paths called it a declaration nothing consumes.

    This was written on `prosody_analysis` / `view:prosody`, which was
    2.01's other view-only input until step 1.05 was unwired (#F5). The
    view still exists; nothing declares its source any more, so the
    property is tested on the one that does.
    """
    assert "temporal_index" in input_contract.view_sources("transcript")
    by_key = {(r.node_id, r.name): r for r in rows}
    assert by_key[("creative_direction", "temporal_index")].prompt_reads


# ── The step with no prompt ─────────────────────────────────────────
#
# The survey read "reaches the prompt" off `context_fields`, and a step
# with no `handoff.md` declares none because it has no prompt to
# project.  That absence was read as "handed every byte it was routed",
# so every input of every prompt-less step surveyed as consumed.  #330
# found the class by hand: `render_motion_graphics` declares
# `creative_direction` and `enhancement_spec` REQUIRED, the DAG routes
# both, and `generate_motion_props` reads neither.

def test_a_step_with_no_prompt_reaches_no_prompt(rows):
    """`prompt_reads` is a claim about a prompt. Fifteen steps in the
    DAG have none, and the claim was True for every input of all of
    them."""
    prompt_less = [r for r in rows if not r.has_prompt]
    assert prompt_less, "the DAG has prompt-less steps; the survey found none"
    assert not [f"{r.node_id}.{r.name}" for r in prompt_less
                if r.prompt_reads]


def test_the_motion_graphics_steps_two_declarations_now_reach_a_prompt(rows):
    """The regression case, and its repair, in the survey's own output.

    `render_motion_graphics` declared `creative_direction` and
    `enhancement_spec` REQUIRED and read neither: both were named in
    `step.py` and handed to `generate_motion_props`, which never
    mentioned either parameter - the KEY read as read and the VALUE went
    nowhere. It surveyed clean because a prompt-less step's absent
    `context_fields` read as "handed every byte".

    Step 4.06 grew a `handoff.md` on 2026-09-02, and both declarations
    are now what they always claimed to be: a model reads them and plans
    the motion-graphics layer from them. So the survey's answer changed,
    and it changed the honest way - through `prompt_reads` on a step
    that really has a prompt, not by widening what counts as a read.

    The dataflow read that found the defect is unchanged and is still
    exercised, on a step built in this file, by
    `test_a_value_handed_to_a_function_that_ignores_it_is_not_read`.
    """
    by_key = {(r.node_id, r.name): r for r in rows}
    for name in ("creative_direction", "enhancement_spec"):
        row = by_key[("render_motion_graphics", name)]
        assert row.required
        assert row.has_prompt, "4.06 has a handoff now"
        assert row.prompt_reads, (
            f"{name} is declared required and reaches no prompt - the "
            f"declaration is back to claiming a read that never happens")
        assert row.consumed
        assert row not in input_contract.unread_by_a_prompt_less_step(rows)


def test_a_value_handed_to_a_function_that_ignores_it_is_not_read(tmp_path):
    """The dataflow read, on a step built here rather than on the
    pipeline's, so the property survives the pipeline being fixed."""
    (tmp_path / "helper.py").write_text(
        "def draw(kept, dropped):\n"
        "    return sorted(kept)\n", encoding="utf-8")
    (tmp_path / "step.py").write_text(
        "from helper import draw\n"
        "def main(data):\n"
        "    kept = data.get('kept', {})\n"
        "    dropped = data.get('dropped', {})\n"
        "    named_only = data['named_only']\n"
        "    return draw(kept, dropped)\n", encoding="utf-8")

    obtained, dead = input_contract.trace_step_values(tmp_path)
    assert obtained == {"kept", "dropped", "named_only"}
    assert set(dead) == {"dropped", "named_only"}
    assert "step.py:" in dead["dropped"]


def test_the_value_read_is_one_sided(tmp_path):
    """Everything it cannot follow is read as USED. A dataflow read that
    guessed would report findings the code does not have."""
    (tmp_path / "step.py").write_text(
        "from library.tools import safe_area\n"
        "def main(data):\n"
        "    to_a_tool = data.get('to_a_tool', {})\n"
        "    to_a_method = data.get('to_a_method', {})\n"
        "    aliased = data.get('aliased', {})\n"
        "    second = aliased\n"
        "    safe_area.resolve_safe_area(to_a_tool)\n"
        "    return data.render(to_a_method)\n", encoding="utf-8")

    _, dead = input_contract.trace_step_values(tmp_path)
    assert dead == {}


def test_one_use_anywhere_keeps_a_key_out_of_the_finding(tmp_path):
    (tmp_path / "step.py").write_text(
        "def ignore(x):\n"
        "    return 1\n"
        "def main(data):\n"
        "    ignore(data.get('spec', {}))\n"
        "    return data['spec']['blocks']\n", encoding="utf-8")

    _, dead = input_contract.trace_step_values(tmp_path)
    assert dead == {}


@pytest.mark.heavy
def test_the_new_findings_report_and_do_not_fail(rows):
    """Out of scope for this change: making a pre-existing finding fail
    the build. `disagreements` - the failing set - is unchanged."""
    dropped = input_contract.unread_by_a_prompt_less_step(rows)
    assert dropped, "the class the fix exists to see is empty"
    assert input_contract.disagreements(rows) == []
    assert input_contract.main(["--bad"]) == 0
