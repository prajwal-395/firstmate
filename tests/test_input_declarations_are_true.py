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
    UNCONSUMED_DECLARATIONS,
)


@pytest.fixture(scope="module")
def dag():
    return run_scope.load_dag()


@pytest.fixture(scope="module")
def manifests(dag):
    return run_scope.load_manifests(dag)


@pytest.fixture(scope="module")
def rows(dag, manifests):
    return input_contract.survey(dag, manifests)


# ── The survey covers the pipeline ──────────────────────────────────

def test_every_step_and_every_declared_input_is_surveyed(dag, manifests,
                                                         rows):
    surveyed = {(r.node_id, r.name) for r in rows}
    declared = {
        (node["id"], inp.get("name"))
        for node in dag["nodes"]
        for inp in ((manifests[node["id"]].get("interface") or {})
                    .get("inputs") or [])
    }
    assert surveyed == declared
    assert {r.node_id for r in rows} == {n["id"] for n in dag["nodes"]}


def test_the_survey_reads_the_route_the_runner_really_uses(dag, rows):
    """A row's route is `edge` exactly when a `data_mapping` targets that
    input name - the same reading `gather_step_inputs` makes when it
    decides whether to raise."""
    routed = input_contract.routed_inputs(dag)
    for row in rows:
        assert (row.route == input_contract.ROUTE_EDGE) == (
            row.name in routed.get(row.node_id, set())), (
            f"{row.node_id}.{row.name} routed as {row.route}")


# ── The three disagreements ─────────────────────────────────────────

def test_no_required_input_goes_unenforced(rows):
    """A requirement nothing refuses on is a claim with no teeth: the
    run proceeds without the value and the step reads whatever default
    its code carries."""
    bad = [f"{r.node_id}.{r.name} (route {r.route})"
           for r in input_contract.unenforced(rows)]
    assert not bad, (
        "declared required, and neither the runner nor the step refuses "
        f"without it: {bad}")


def test_no_optional_input_is_refused_by_the_step(rows):
    """The other direction, and the one that kills runs: a step that
    raises on an input its own manifest said it could do without."""
    bad = [f"{r.node_id}.{r.name} at {r.step_refusal}"
           for r in input_contract.optional_but_refused(rows)]
    assert not bad, (
        f"declared optional, and the step refuses without it: {bad}")


def test_every_declared_input_is_read_by_the_code_or_the_prompt(rows):
    """A declaration nothing consumes costs a scoped run its producer
    and buys nothing. The two the survey found are recorded; a third
    fails here."""
    bad = [f"{r.node_id}.{r.name}" for r in input_contract.unconsumed(rows)
           if (r.node_id, r.name) not in UNCONSUMED_DECLARATIONS]
    assert not bad, f"declared, and nothing reads it: {bad}"


def test_the_survey_is_clean(rows):
    """One assertion for the whole file, so the CLI and the suite cannot
    disagree about whether the pipeline passes."""
    assert input_contract.disagreements(rows) == []


# ── The recorded tables name real declarations ──────────────────────

def test_the_recorded_tables_name_real_declarations(rows):
    declared = {(r.node_id, r.name) for r in rows}
    for table, label in (
            (REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT,
             "REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT"),
            (UNCONSUMED_DECLARATIONS, "UNCONSUMED_DECLARATIONS")):
        for key in table:
            assert key in declared, (
                f"{label} names {key}, which is not a declared input of "
                f"that step")


def test_every_recorded_entry_carries_a_reason():
    for table in (REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT,
                  UNCONSUMED_DECLARATIONS):
        for key, reason in table.items():
            assert len(reason.split()) >= 12, (
                f"{key} is recorded with no real reason: {reason!r}")


def test_a_recorded_requirement_is_still_declared_required(rows):
    """The table is 'required THOUGH the step runs without it'. An entry
    for an input that has since been relaxed is stale."""
    required = {(r.node_id, r.name) for r in rows if r.required}
    stale = sorted(key for key in REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT
                   if key not in required)
    assert not stale, f"recorded as required, but declared optional: {stale}"


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


def test_the_step_that_reads_around_the_runner_is_the_one_measured(rows):
    """Every measured entry is `compile_manifest`'s, and that is not a
    coincidence: it is the only step that loads its inputs out of
    `pipeline_data.json` itself instead of receiving what
    `gather_step_inputs` assembled (AGENTS.md 10.1)."""
    measured = {node_id for node_id, _
                in REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT}
    assert measured == {"compile_manifest"}


# ── The reading of the code, on cases with a known answer ───────────

def test_a_direct_subscript_is_read_as_a_refusal(rows):
    by_key = {(r.node_id, r.name): r for r in rows}
    row = by_key[("assign_aroll", "audio_spine")]
    assert row.refused_by == input_contract.REFUSED_BY_STEP
    assert "input_data['audio_spine']" in row.step_refusal


def test_a_require_keys_call_is_read_as_a_refusal(rows):
    by_key = {(r.node_id, r.name): r for r in rows}
    row = by_key[("speech_sequence", "temporal_index")]
    assert row.refused_by == input_contract.REFUSED_BY_STEP
    assert "require_keys()" in row.step_refusal


def test_a_get_behind_a_guard_that_exits_is_read_as_a_refusal(rows):
    by_key = {(r.node_id, r.name): r for r in rows}
    row = by_key[("validate_sfx_library", "sfx_library")]
    assert row.refused_by == input_contract.REFUSED_BY_STEP
    assert "guard raises" in row.step_refusal


def test_a_get_passed_into_a_call_is_not_a_binding(rows):
    """`result = validate_output(..., input_data.get("project_folder"))`
    followed by `if not result...: sys.exit` is a guard on the RESULT.
    Reading it as a guard on `project_folder` reported `validate` as
    refusing an input it declares optional."""
    by_key = {(r.node_id, r.name): r for r in rows}
    row = by_key[("validate", "project_folder")]
    assert row.step_refusal == ""


def test_a_view_carries_its_source_input_to_the_prompt(rows):
    """`creative_direction` declares `prosody_analysis` and selects
    `view:prosody`, not the input's own name. A survey that read only
    dot paths called it a declaration nothing consumes."""
    assert "prosody_analysis" in input_contract.view_sources("prosody")
    by_key = {(r.node_id, r.name): r for r in rows}
    assert by_key[("creative_direction", "prosody_analysis")].prompt_reads


def test_a_shared_tool_counts_as_the_step_reading_its_input(rows):
    """`review_rough_cut` reads `project_config` through
    `library/tools/duration_targets.py`."""
    by_key = {(r.node_id, r.name): r for r in rows}
    assert by_key[("review_rough_cut", "project_config")].code_reads


def test_a_key_restored_around_the_projection_reaches_the_prompt(rows):
    """`creative_brief` is not a `context_fields` entry: it is saved and
    restored around the projection by name (AGENTS.md 10.1)."""
    by_key = {(r.node_id, r.name): r for r in rows}
    assert by_key[("plan_sfx", "creative_brief")].prompt_reads


def test_the_cli_agrees_with_the_suite():
    assert input_contract.main(["--bad"]) == 0
