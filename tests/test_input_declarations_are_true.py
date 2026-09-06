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
    fails here.

    Scoped to steps WITH a prompt, which is the scope this gate has
    always really had: every finding in the prompt-less class predates
    the fix that made it visible, and escalating a pre-existing finding
    to a build failure is a separate decision. They are reported by
    `test_the_inputs_a_prompt_less_step_never_reads` below.
    """
    bad = [f"{r.node_id}.{r.name}" for r in input_contract.unconsumed(rows)
           if r.has_prompt
           and (r.node_id, r.name) not in UNCONSUMED_DECLARATIONS]
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
                  UNCONSUMED_DECLARATIONS,
                  UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT):
        for key, reason in table.items():
            assert len(reason.split()) >= 12, (
                f"{key} is recorded with no real reason: {reason!r}")


def test_an_unrouted_record_names_no_declared_input(rows):
    """The other table's contract is the mirror image: an entry here
    says the declaration has GONE while a frozen `handoff.md` still
    documents the read. An entry naming a declared input is stale."""
    declared = {(r.node_id, r.name) for r in rows}
    stale = sorted(key for key in UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT
                   if key in declared)
    assert not stale, (
        "recorded as unrouted, but declared again: %s" % (stale,))


def test_an_unrouted_record_names_a_line_the_handoff_still_carries(rows):
    """And the other side: once the captain edits the frozen line, the
    disagreement is over and the entry must go. Read off the handoff
    file itself, not asserted."""
    assert input_contract.unrouted_but_documented(rows) == []


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


def test_the_runner_decides_which_steps_have_a_prompt(dag, rows):
    """Asked of `get_step_implementation`, not of a list kept here."""
    from library.processes.edit_video import run_pipeline

    step_ref = {node["id"]: node["step_ref"] for node in dag["nodes"]}
    for row in rows:
        step_dir = input_contract._LIBRARY_ROOT / step_ref[row.node_id]
        expected = bool(
            run_pipeline.get_step_implementation(step_dir).get("prompt"))
        assert row.has_prompt is expected, row.node_id


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


def test_the_new_findings_report_and_do_not_fail(rows):
    """Out of scope for this change: making a pre-existing finding fail
    the build. `disagreements` - the failing set - is unchanged."""
    dropped = input_contract.unread_by_a_prompt_less_step(rows)
    assert dropped, "the class the fix exists to see is empty"
    assert input_contract.disagreements(rows) == []
    assert input_contract.main(["--bad"]) == 0


def test_the_llm_steps_are_judged_exactly_as_before(rows):
    """The dataflow read is asked only where it decides something, so a
    step with a prompt is surveyed by PR 320's rule unchanged."""
    for row in rows:
        if not row.has_prompt:
            continue
        assert row.code_consumes is None
        assert row.value_evidence == ""
        assert row.consumed == (row.code_reads or row.prompt_reads)


def test_the_prompt_less_findings_are_reported(rows, capsys):
    """Reports; never fails. Fixing each is separate work."""
    dropped = input_contract.unread_by_a_prompt_less_step(rows)
    prompt_less = sorted({r.node_id for r in rows if not r.has_prompt})
    with capsys.disabled():
        print(f"\n  steps with no prompt: {len(prompt_less)}   declared "
              f"inputs their code does not read: {len(dropped)}")
        for row in dropped:
            print(f"    UNREAD  {row.node_id}.{row.name} "
                  f"({'required' if row.required else 'optional'}) - "
                  f"{input_contract.unread_basis(row)}")
        print("  (reporting only - escalating a pre-existing finding to a "
              "failure is the captain's call.)")
        print("  Blind spot, stated: a step WITH a prompt is still judged "
              "on whether")
        print("  its code NAMES the key, not on whether the value goes "
              "anywhere. And")
        print("  the value read is one-sided:")
        for limit in input_contract._UNTRACEABLE:
            print(f"    - {limit}")
    assert True
