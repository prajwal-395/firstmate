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


# ── What it deliberately does not catch ───────────────────────────────


# ── The report ────────────────────────────────────────────────────────


# ── The same reader, over an archived run ─────────────────────────────


def _write_request(project: Path, step_id: str, prompt: str, context: str):
    requests = project / "pipeline_output" / "llm_requests"
    requests.mkdir(parents=True, exist_ok=True)
    (requests / f"{step_id}.json").write_text(
        json.dumps({"step_id": step_id, "prompt": prompt,
                    "context": context}),
        encoding="utf-8",
    )


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
