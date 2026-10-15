"""Agent-task efficiency benchmark: golden, determinism, failure modes.

The five scripted sessions (start, reel state, footage search,
note/revision, delivery) replay mocked tool responses only. No Resolve,
no model, no render, no network; every case runs under `tmp_path`.
"""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import eval_harness


def test_agent_benchmark_matches_golden_exactly(tmp_path):
    """All five sessions yield the pinned outcomes and call/byte totals.

    Catches: a fixture edit that silently moves the efficiency baseline -
    the benchmark must fail until the golden is re-pinned on purpose.
    """
    result = eval_harness.run_agent_benchmark(tmp_path)

    assert result["measurement_version"] == \
        eval_harness.AGENT_MEASUREMENT_VERSION
    assert [task["task_id"] for task in result["tasks"]] == \
        list(eval_harness.AGENT_TASK_ORDER)
    assert result["failures"] == []
    prefix = result["request_prefix"]
    assert prefix["stable_prefix_bytes"] > 0
    assert prefix["total_request_bytes"] >= prefix["stable_prefix_bytes"]
    assert prefix["stable_prefix_ratio"] >= prefix["minimum_fixed_prefix_ratio"]
    assert prefix["legacy_baseline_ratio"] == 0
    assert prefix["legacy_baseline_meets_minimum"] is False
    for task in result["tasks"]:
        assert task["golden_match"] is True
        assert task["deltas"] == {"calls": 0, "bytes_out": 0,
                                  "bytes_in": 0, "bytes_total": 0}
        golden = eval_harness.AGENT_GOLDEN[task["task_id"]]
        for field in ("outcome", "calls", "bytes_out", "bytes_in",
                      "bytes_total", "transcript_sha256"):
            assert task[field] == golden[field]


def test_agent_scores_reproduce_on_rerun(tmp_path):
    """Running twice produces the same scores; wall is reported, not scored.

    Catches: wall time or absolute paths leaking into the score, which
    would make the benchmark flaky between machines and runs.
    """
    first = eval_harness.run_agent_benchmark(tmp_path / "run-1")
    second = eval_harness.run_agent_benchmark(tmp_path / "run-2")

    assert "wall_s" not in eval_harness.AGENT_GOLDEN["delivery"]
    for before, after in zip(first["tasks"], second["tasks"]):
        scored = {key: value for key, value in before.items()
                  if key != "wall_s"}
        rescored = {key: value for key, value in after.items()
                    if key != "wall_s"}
        assert scored == rescored
    assert first["failures"] == second["failures"] == []
    assert first["request_prefix"] == second["request_prefix"]


def test_changed_response_or_action_fails_golden():
    """A changed mocked response or tool action mismatches the golden.

    Catches: an expected tool reply drifting (or the agent taking a new
    action) without anyone noticing - the digest pins both.
    """
    tampered = copy.deepcopy(eval_harness.AGENT_TASKS)
    tampered["delivery"]["transcript"][0]["response"] = {
        "REN_RESULT": {"code": "ok", "exit": 0}}
    session = eval_harness.replay_agent_task("delivery", tasks=tampered)
    assert eval_harness.check_agent_session(session) != []

    tampered = copy.deepcopy(eval_harness.AGENT_TASKS)
    tampered["note_revision"]["transcript"][1]["expect_action"] = (
        "do_something_else")
    session = eval_harness.replay_agent_task("note_revision",
                                             tasks=tampered)
    assert eval_harness.check_agent_session(session) != []


def test_agent_cli_writes_versioned_scores_with_provenance(tmp_path):
    """`ren eval --agent --fixture <tmp>` exits 0 with score files in tmp.

    Catches: scores landing outside the fixture dir, or a score file
    without its measurement version and fixture provenance.
    """
    assert eval_harness.main(
        ["--agent", "--fixture", str(tmp_path)]) == 0

    scores = json.loads(
        (tmp_path / "agent_scores.json").read_text(encoding="utf-8"))
    assert scores["measurement_version"] == "agent-efficiency/1"
    assert len(scores["tasks"]) == 5
    for task_id in eval_harness.AGENT_TASK_ORDER:
        assert (tmp_path / "sessions" / f"{task_id}.json").is_file()
        assert (tmp_path / "fixtures" / f"{task_id}.json").is_file()
        provenance = scores["provenance"][task_id]
        assert provenance["module"].startswith("library/tools/")
        assert provenance["entry"]


def test_agent_cli_fails_on_golden_mismatch(tmp_path, monkeypatch):
    """A drifted transcript makes the CLI exit nonzero with the reason.

    Catches: the benchmark printing scores but exiting 0 after a fixture
    changed under it.
    """
    tampered = copy.deepcopy(eval_harness.AGENT_TASKS)
    tampered["reel_state"]["transcript"].append(
        {"tool": "reel_state.read",
         "args": {"project": "fixture-podcast", "reel": 1},
         "response": {"reel": 1, "state": "approved"},
         "expect_action": "report_reel_blocker:none"})
    monkeypatch.setattr(eval_harness, "AGENT_TASKS", tampered)

    assert eval_harness.main(
        ["--agent", "--fixture", str(tmp_path)]) == 1
    scores = json.loads(
        (tmp_path / "agent_scores.json").read_text(encoding="utf-8"))
    assert any("reel_state" in failure
               for failure in scores["failures"])


def test_agent_bytes_count_utf8_not_chars():
    """Scoring counts UTF-8 bytes: the note's em dash is three bytes.

    Catches: a future scorer change to `ensure_ascii` escapes (or `len`
    on str), which would undercount every non-ASCII exchange.
    """
    assert eval_harness.agent_canonical_bytes("\u2014") == \
        "\u2014".encode("utf-8").join((b'"', b'"'))
    assert len(eval_harness.agent_canonical_bytes("\u2014")) == 5
    assert len(json.dumps("\u2014").encode("utf-8")) == 8

    request = {"tool": "marker_routing.route",
               "args": {"note": "Trim the pause — keep the laugh."}}
    text = json.dumps(request, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    assert len(eval_harness.agent_canonical_bytes(request)) > len(text)


def test_agent_replay_refuses_malformed_turns():
    """An empty tool, non-object args/response or action refuses loudly.

    Catches: a fixture typo scoring as a zero-byte session instead of
    failing the task.
    """
    assert eval_harness.replay_agent_task("delivery")["calls"] == 2
    with pytest.raises(KeyError):
        eval_harness.replay_agent_task("no_such_task")
    for bad in ({"tool": "", "args": {}, "response": {},
                 "expect_action": "x"},
                {"tool": "t", "args": [], "response": {},
                 "expect_action": "x"},
                {"tool": "t", "args": {}, "response": [],
                 "expect_action": "x"},
                {"tool": "t", "args": {}, "response": {},
                 "expect_action": "  "}):
        with pytest.raises(ValueError):
            eval_harness.score_agent_transcript([bad])
