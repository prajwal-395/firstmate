"""A table arriving with zero rows is reported on the run that sent it.

Two occurrences of the same defect, both found by an audit weeks later:
`cuts_toon` (#218) and `sfx_candidates_toon` (#223). The guard exists so
the third one surfaces at the moment it happens.

These tests pin what it catches, what it deliberately does not catch,
and that it never fails a run: an empty table can be the honest answer,
and a gate that fires on correct output is not coverage (AGENTS.md
10.4).
"""
import json
from pathlib import Path

from library.tools.empty_table_guard import (
    EmptyTable,
    find_empty_tables,
    format_report,
    report_step_context,
    scan_archived_requests,
)
from library.tools.toon_serializer import json_to_toon


# ── What it catches ───────────────────────────────────────────────────


def test_it_catches_the_defect_that_shipped_twice():
    """The literal bytes out of project 001's archived plan_sfx request."""
    context = (
        "available_sfx_types:\n"
        "  [0] whoosh\n"
        "  [1] bass_impact\n"
        "sfx_candidates_toon: |\n"
        "  [0]{segment_id,text,action_sfx_suggested}\n"
        "\n"
        "project_folder: /tmp/001\n"
    )
    prompt = "The `sfx_candidates_toon` table provides a summarized list."

    found = find_empty_tables(context, prompt)
    assert [t.key for t in found] == ["sfx_candidates_toon"]
    assert found[0].columns == ("segment_id", "text", "action_sfx_suggested")
    assert found[0].named_in_prompt is True


def test_the_fixed_table_is_not_flagged():
    context = (
        "sfx_candidates_toon: |\n"
        "  [2]{segment_id,text,action_sfx_suggested}\n"
        "  hook\ti can feel the silent judgment.\t0 audio transients\n"
        "  1\tThe breath after the hook.\tnot measured (no source clip)\n"
    )
    assert find_empty_tables(context, "read sfx_candidates_toon") == []


def test_an_empty_list_input_is_caught_too():
    """`json_to_toon` writes `[]`, not a header, for an empty list.

    It is the same silence in a different spelling: a step told to read
    `b_roll_assignments` is given nothing and no note saying so.
    """
    context = json_to_toon({"b_roll_assignments": [], "project_fps": 30.0})
    found = find_empty_tables(context, "consider each b_roll_assignments entry")
    assert [(t.key, t.form) for t in found] == [("b_roll_assignments", "[]")]
    assert found[0].columns == ()
    assert found[0].named_in_prompt is True


def test_a_prompt_that_never_names_the_key_is_recorded_as_such():
    context = json_to_toon({"visual_qa": []})
    found = find_empty_tables(context, "Render the timeline.")
    assert found[0].named_in_prompt is False


def test_several_keys_are_all_reported():
    context = json_to_toon({"a_table": [], "kept": [{"x": 1}], "b_table": []})
    assert sorted(t.key for t in find_empty_tables(context, "")) == [
        "a_table", "b_table",
    ]


# ── What it deliberately does not catch ───────────────────────────────


def test_a_nested_empty_list_is_not_reported():
    """`violations: []` inside a passing check is not a missing table.

    Project 001's ten archived contexts carry 42 `[]` markers at some
    depth and 2 at the top level. Reporting all of them buries the one
    that matters.
    """
    context = json_to_toon(
        {"rough_cut_review": {"checks": {"duration": {"passed": True,
                                                      "violations": []}}}}
    )
    assert find_empty_tables(context, "read rough_cut_review") == []


def test_rows_that_are_present_but_hollow_are_not_caught():
    """The stated blind spot, and the first occurrence of the defect.

    `cuts_toon` had thirteen rows and every one read
    `unknown-to-unknown`. This guard sees a table with rows and says
    nothing. It catches the zero-row half of the family only, and the PR
    that added it says so.
    """
    context = (
        "cuts_toon: |\n"
        "  [2]{cut,outgoing,incoming}\n"
        "  1\tunknown\tunknown\n"
        "  2\tunknown\tunknown\n"
    )
    assert find_empty_tables(context, "read cuts_toon") == []


def test_a_scalar_key_is_not_a_table():
    context = json_to_toon({"project_fps": 30.0, "notes": "none"})
    assert find_empty_tables(context, "") == []


def test_an_empty_dict_is_not_a_table():
    context = json_to_toon({"music_analysis": {}})
    assert find_empty_tables(context, "read music_analysis") == []


# ── The report ────────────────────────────────────────────────────────


def test_the_report_is_empty_when_there_is_nothing_to_say():
    assert format_report("plan_sfx", []) == ""


def test_the_named_table_is_reported_first():
    empties = [
        EmptyTable("zzz_unnamed", "[]", (), False),
        EmptyTable("aaa_named", "[0]{a}", ("a",), True),
    ]
    lines = format_report("plan_sfx", empties).splitlines()
    assert "2 context table(s) arrived with zero rows, 1 named" in lines[0]
    assert "aaa_named" in lines[1]
    assert "zzz_unnamed" in lines[2]


def test_reporting_writes_to_the_run_log_and_raises_nothing(capsys):
    """It is a report, never a gate."""
    context = "t: |\n  [0]{a,b}\n"
    found = report_step_context("plan_sfx", context, "read the t table")
    assert len(found) == 1
    assert "[empty-table] plan_sfx" in capsys.readouterr().err


def test_a_step_with_nothing_to_report_prints_nothing(capsys):
    report_step_context("plan_sfx", "t: |\n  [2]{a}\n  1\n  2\n", "t")
    assert capsys.readouterr().err == ""


# ── The same reader, over an archived run ─────────────────────────────


def _write_request(project: Path, step_id: str, prompt: str, context: str):
    requests = project / "pipeline_output" / "llm_requests"
    requests.mkdir(parents=True, exist_ok=True)
    (requests / f"{step_id}.json").write_text(
        json.dumps({"step_id": step_id, "prompt": prompt,
                    "context": context}),
        encoding="utf-8",
    )


def test_it_scans_a_run_that_already_happened(tmp_path):
    project = tmp_path / "project"
    _write_request(project, "plan_sfx",
                   "read `sfx_candidates_toon`",
                   "sfx_candidates_toon: |\n  [0]{segment_id,text}\n")
    _write_request(project, "select_broll",
                   "read `broll_candidates_toon`",
                   "broll_candidates_toon: |\n  [1]{clip_id}\n  clip_001\n")

    results = {step: empties
               for step, _path, empties, _err in scan_archived_requests(project)}
    assert results["select_broll"] == []
    assert [t.key for t in results["plan_sfx"]] == ["sfx_candidates_toon"]
    assert results["plan_sfx"][0].named_in_prompt is True


def test_an_unreadable_request_is_reported_not_skipped(tmp_path):
    project = tmp_path / "project"
    _write_request(project, "plan_sfx", "p", "c")
    requests = project / "pipeline_output" / "llm_requests"
    (requests / "broken.json").write_text("{not json", encoding="utf-8")

    errors = {step: err
              for step, _path, _empties, err in scan_archived_requests(project)}
    assert errors["broken"], "a request that could not be read said nothing"


def test_the_cli_reports_and_exits_zero(tmp_path, capsys):
    """It never fails the caller: an empty table can be correct."""
    from library.tools.empty_table_guard import main

    project = tmp_path / "project"
    _write_request(project, "plan_sfx", "read `sfx_candidates_toon`",
                   "sfx_candidates_toon: |\n  [0]{segment_id}\n")
    assert main([str(project)]) == 0
    out = capsys.readouterr().out
    assert "sfx_candidates_toon" in out
    assert "1 of 1 archived request(s)" in out


# ── The runner really calls it ────────────────────────────────────────


def test_the_runner_reports_the_empty_table_before_it_asks(tmp_path, capsys):
    """Driven through `present_llm_step`, not asserted off the source.

    That function is where the context and the prompt are both in hand,
    and it is the one place every LLM step passes through. Mock mode
    reads a canned answer off disk, so this exercises the real call
    without an LLM.
    """
    from library.processes.edit_video.run_pipeline import present_llm_step

    project = tmp_path / "project"
    bak = project / "pipeline_output" / "llm_responses_bak"
    bak.mkdir(parents=True)
    (bak / "plan_sfx.json").write_text(
        json.dumps({"sfx_creative": [{"spine_block_position": 1,
                                      "sfx_type": "whoosh",
                                      "volume_db": -18,
                                      "rationale": "marks the cut"}]}),
        encoding="utf-8",
    )
    prompt = tmp_path / "handoff.md"
    prompt.write_text("Read the `sfx_candidates_toon` table.\n",
                      encoding="utf-8")

    present_llm_step(
        str(prompt),
        {"project_folder": str(project),
         "sfx_candidates_toon": "[0]{segment_id,text,action_sfx_suggested}\n"},
        "plan_sfx",
        {"interface": {"llm_outputs": [{"name": "sfx_creative",
                                        "type": "list"}]},
         "context_fields": ["sfx_candidates_toon"]},
        full_auto="mock",
        bridge_supplied={"sfx_candidates_toon"},
    )

    err = capsys.readouterr().err
    assert "[empty-table] plan_sfx" in err, (
        f"the runner sent an empty named table without saying so:\n{err}"
    )
    assert "sfx_candidates_toon" in err
    assert err.count("[empty-table]") == 1, (
        "the report fired more than once for one step"
    )


def test_a_full_table_makes_the_runner_say_nothing(tmp_path, capsys):
    from library.processes.edit_video.run_pipeline import present_llm_step

    project = tmp_path / "project"
    bak = project / "pipeline_output" / "llm_responses_bak"
    bak.mkdir(parents=True)
    (bak / "plan_sfx.json").write_text(
        json.dumps({"sfx_creative": [{"spine_block_position": 1,
                                      "sfx_type": "whoosh",
                                      "volume_db": -18,
                                      "rationale": "marks the cut"}]}),
        encoding="utf-8",
    )
    prompt = tmp_path / "handoff.md"
    prompt.write_text("Read the `sfx_candidates_toon` table.\n",
                      encoding="utf-8")

    present_llm_step(
        str(prompt),
        {"project_folder": str(project),
         "sfx_candidates_toon":
             "[1]{segment_id,text,action_sfx_suggested}\nhook\ta line\t0 audio transients\n"},
        "plan_sfx",
        {"interface": {"llm_outputs": [{"name": "sfx_creative",
                                        "type": "list"}]},
         "context_fields": ["sfx_candidates_toon"]},
        full_auto="mock",
        bridge_supplied={"sfx_candidates_toon"},
    )

    assert "[empty-table]" not in capsys.readouterr().err
