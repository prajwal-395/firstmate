"""Privacy-safe agent-cost counters: one session, one aggregate row.

Defect caught: `perf_ledger` could report run wall and bytes but not
tool-call/read-byte cost by task class, so agent UX work could not be
ranked; old session counts predate the LE changes. The aggregate row
carries numbers only - no prompt, tool argument or response content -
and `ren profile --agent` summarizes just those rows, naming what was
never measured instead of guessing zero.
"""

from __future__ import annotations

import json

from library.tools import agent_cost, perf_ledger


def test_five_call_session_writes_exactly_one_aggregate_row(tmp_path):
    ledger = tmp_path / "perf_ledger.jsonl"
    events = [
        {"input_bytes": 120, "output_bytes": 340, "wall_s": 0.5},
        {"input_bytes": 200, "output_bytes": 100, "wall_s": 1.25},
        {"input_bytes": 80, "output_bytes": 400, "wall_s": 0.75},
        {"input_bytes": 150, "output_bytes": 150, "wall_s": 2.0},
        {"input_bytes": 50, "output_bytes": 10, "wall_s": 0.5},
    ]
    row = agent_cost.commit_call_events(
        ledger, host="synthetic", task_class="reel_state", events=events)

    assert row["tool_calls"] == 5
    assert row["input_bytes"] == 600
    assert row["output_bytes"] == 1000
    assert row["wall_s"] == 5.0

    rows = perf_ledger.read_rows(tmp_path, ledger)
    assert len(rows) == 1
    assert rows[0]["kind"] == agent_cost.AGENT_KIND
    assert rows[0]["host"] == "synthetic"
    assert rows[0]["task_class"] == "reel_state"


def test_agent_profile_json_reproduces_row_and_omits_content(tmp_path, capsys):
    ledger = tmp_path / "perf_ledger.jsonl"
    with agent_cost.session(ledger, host="synthetic",
                            task_class="reel_state") as tracked:
        tracked.record_call(input_bytes=120, output_bytes=340, wall_s=0.5)
        tracked.record_call(input_bytes=200, output_bytes=100, wall_s=1.25)
        tracked.record_call(input_bytes=80, output_bytes=400, wall_s=0.75)
        tracked.record_call(input_bytes=150, output_bytes=150, wall_s=2.0)
        tracked.record_call(input_bytes=50, output_bytes=10, wall_s=0.5)

    assert perf_ledger.main(
        ["--ledger", str(ledger), "--agent", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"
    assert summary["sessions"] == 1
    (group,) = summary["groups"]
    assert (group["host"], group["task_class"],
            group["tool_calls"], group["input_bytes"],
            group["output_bytes"], group["wall_s"]) == (
        "synthetic", "reel_state", 5, 600, 1000, 5.0)
    blob = json.dumps(summary)
    for banned in ("prompt", "response", "argument", "content", "tool_name"):
        assert banned not in blob


def test_agent_profile_names_unmeasured_for_absent_host_source(tmp_path, capsys):
    ledger = tmp_path / "perf_ledger.jsonl"
    ledger.write_text(json.dumps({
        "kind": "capability", "run_id": "r1",
        "capability": "catalog", "wall_s": 1.0,
    }) + "\n", encoding="utf-8")

    assert perf_ledger.main(
        ["--ledger", str(ledger), "--agent", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "unmeasured"
    assert summary["groups"] == []

    assert perf_ledger.main(["--ledger", str(ledger), "--agent"]) == 0
    assert "unmeasured" in capsys.readouterr().out


def test_agent_adapter_refuses_content_bearing_fields(tmp_path):
    ledger = tmp_path / "perf_ledger.jsonl"
    try:
        agent_cost.commit_session(
            ledger, host="synthetic", task_class="reel_state",
            tool_calls=1, input_bytes=10, output_bytes=10, wall_s=1.0,
            prompt="what reels exist?")
    except TypeError as exc:
        assert "aggregates only" in str(exc)
    else:  # pragma: no cover - the adapter must refuse content
        raise AssertionError("content-bearing field was accepted")
    try:
        agent_cost.commit_call_events(
            ledger, host="synthetic", task_class="reel_state",
            events=[{"input_bytes": 1, "output_bytes": 1, "wall_s": 0.1,
                     "tool": "reels", "arguments": {"reel": 1}}])
    except TypeError as exc:
        assert "aggregates only" in str(exc)
    else:  # pragma: no cover - the adapter must refuse content
        raise AssertionError("content-bearing event was accepted")
    assert perf_ledger.read_rows(tmp_path, ledger) == []
