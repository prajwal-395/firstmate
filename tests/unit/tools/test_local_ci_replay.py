"""CI-09: the historical regression replay benchmark.

The defect this pins: without a replay harness the incremental policy
(CI-08) could replace the full-suite gate on argument alone, and the
failure mode it would hide first is the cross-PR interaction - each
PR green alone, the stack red together. A simulation that reuses
per-PR PASS evidence on the stack without re-keying certifies exactly
that stack green. After CI-09, the harness replays every corpus case
under both policies with tree-keyed evidence, the two-PR fixture
corpus proves the simulation goes red where the full gate goes red,
a selection that omits the interaction is RECORDED as a miss rather
than reading as green, and the report states the CI-08 bar (C4) with
a NOT YET TRUSTWORTHY verdict while no shadow data exists.

Every test runs on recorded outcomes under `tmp_path` - the harness
never shells the gate, reads no environment, and reaches no ledger -
so the suite is deterministic on any machine.
"""

import json
import shutil
from argparse import Namespace

import pytest

from library.tools.local_ci import __main__ as local_ci_cli
from library.tools.local_ci import replay


@pytest.fixture(autouse=True)
def _hermetic_ci_env(monkeypatch):
    # The harness reads no environment, but the suite must stay green
    # under whatever the host or agent exports: clear every marker a
    # neighboring CI layer derives fingerprints or paths from.
    monkeypatch.delenv("REN_CI_RESULT_CACHE", raising=False)
    monkeypatch.delenv("REN_CI_CACHE_VERSIONS", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("PIPELINE_SFX_LIBRARY", raising=False)
    monkeypatch.delenv("PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.delenv("PIPELINE_PROJECTS_ROOT", raising=False)
    monkeypatch.delenv("VEP_TEST_FAILURE_RATES_FILE", raising=False)


def _shipped_corpus(tmp_path):
    target = tmp_path / "replay_corpus.json"
    shutil.copy(replay.default_corpus_path(), target)
    return str(target)


def _one_member_case(case_id="case-1", verdict="PASS"):
    return {
        "id": case_id,
        "base": "main@fixture",
        "members": [{"pr": 1, "head": "head-1",
                     "changed": ["fixture/module_a.py"]}],
        "known": {"verdict": verdict, "kicked": [],
                  "source": "unit fixture"},
        "outcomes": {"fixture/test_a.py": {
            "base": True, "pr:1": True, "stack": True}},
        "selection": {
            "base": {"selected": []},
            "pr:1": {"selected": ["fixture/test_a.py"]},
            "stack": {"selected": ["fixture/test_a.py"]}},
    }


def _interaction_case():
    manifest = replay.load_manifest()
    return [case for case in manifest["cases"]
            if case["id"] == "cross-pr-interaction"][0]


def test_shipped_corpus_carries_the_two_pr_fixture():
    manifest = replay.load_manifest()
    assert manifest["schema_version"] == replay.SCHEMA_VERSION
    assert [case["id"] for case in manifest["cases"]] == [
        "two-green-prs", "cross-pr-interaction"]
    for case in manifest["cases"]:
        assert len(case["members"]) == 2


def test_full_gate_catches_the_planted_interaction():
    case = _interaction_case()
    verdict = replay.full_gate_verdict(case)
    assert verdict["ok"] is False
    assert verdict["failed"] == ["fixture/test_interaction.py"]


def test_incremental_simulation_catches_the_interaction_cold():
    case = _interaction_case()
    verdict = replay.incremental_verdict(case)
    assert verdict["ok"] is False
    assert verdict["failed"] == ["fixture/test_interaction.py"]
    assert "fixture/test_interaction.py" in verdict["executed"]
    assert verdict["reused"] == []


def test_per_pr_pass_evidence_never_certifies_the_stack():
    # The key-sensitivity pin: PASS rows recorded for each PR's own
    # tree must not satisfy the stack lookup, or per-PR greens would
    # certify a red stack and the benchmark would bless the miss.
    case = _interaction_case()
    evidence = {
        ("fixture/test_interaction.py",
         replay.tree_key(case, "pr:201")),
        ("fixture/test_interaction.py",
         replay.tree_key(case, "pr:202")),
        ("fixture/test_module_a.py",
         replay.tree_key(case, "pr:201")),
        ("fixture/test_module_b.py",
         replay.tree_key(case, "pr:202")),
    }
    verdict = replay.incremental_verdict(case, evidence=evidence)
    assert verdict["ok"] is False
    assert verdict["failed"] == ["fixture/test_interaction.py"]
    assert verdict["reused"] == []


def test_stack_key_evidence_is_reused_not_re_executed():
    case = _interaction_case()
    green = [case for case in replay.load_manifest()["cases"]
             if case["id"] == "two-green-prs"][0]
    key = replay.tree_key(green, "stack")
    evidence = {(test, key) for test in
                ("fixture/test_module_a.py", "fixture/test_module_b.py")}
    verdict = replay.incremental_verdict(green, evidence=evidence)
    assert verdict == {"ok": True, "executed": [], "reused": [
        "fixture/test_module_a.py", "fixture/test_module_b.py"],
        "failed": []}


def test_a_selection_that_omits_the_interaction_is_a_recorded_miss():
    # The metric working, not the harness failing: when the selection
    # cannot see the interaction test, the simulation stays green on a
    # red stack and the replay records the miss instead of reporting
    # an agreement.
    case = _interaction_case()

    def narrow_selection(a_case, tree):
        if tree == "stack":
            return ({"fixture/test_module_a.py",
                     "fixture/test_module_b.py"}, set())
        return replay.default_selection(a_case, tree)

    result = replay.replay_case(case, selection_fn=narrow_selection)
    assert result["full"]["ok"] is False
    assert result["incremental"]["ok"] is True
    assert result["missed_full"] is False
    assert result["missed_incremental"] is True


def test_incomplete_info_tests_always_execute_on_the_stack():
    case = _interaction_case()

    def unknown_selection(a_case, tree):
        selected, _ = replay.default_selection(a_case, tree)
        if tree == "stack":
            selected = set(selected) - {"fixture/test_interaction.py"}
            return selected, {"fixture/test_interaction.py"}
        return selected, set()

    verdict = replay.incremental_verdict(case,
                                         selection_fn=unknown_selection)
    assert verdict["ok"] is False
    assert verdict["failed"] == ["fixture/test_interaction.py"]


def test_hybrid_selector_path_records_a_miss_when_it_cannot_see(
        monkeypatch):
    # The same metric through the real code-path shape: a hybrid
    # selection that cannot see the interaction test stays green on a
    # red stack, and the replay records the miss.
    case = _interaction_case()
    case = dict(case, _selector="hybrid")
    monkeypatch.setattr(replay, "_hybrid_select",
                        lambda changed: ["fixture/test_module_a.py"])
    verdict = replay.incremental_verdict(case)
    assert verdict["ok"] is True
    result = replay.replay_case(case)
    assert result["full"]["ok"] is False
    assert result["missed_incremental"] is True


def test_fixture_replay_agrees_with_known_outcomes(tmp_path):
    manifest = replay.load_manifest(_shipped_corpus(tmp_path))
    result = replay.replay_corpus(manifest)
    assert result["total"] == 2
    assert result["missed_full"] == 0
    assert result["missed_incremental"] == 0
    by_id = {item["id"]: item for item in result["cases"]}
    assert by_id["two-green-prs"]["full"]["ok"] is True
    assert by_id["two-green-prs"]["incremental"]["ok"] is True
    assert by_id["cross-pr-interaction"]["full"]["ok"] is False
    assert by_id["cross-pr-interaction"]["incremental"]["ok"] is False


def test_report_states_the_c4_bar_and_not_yet_trustworthy(tmp_path):
    manifest = replay.load_manifest(_shipped_corpus(tmp_path))
    report = replay.render_report(replay.replay_corpus(manifest))
    assert "missed regressions: full-gate 0/2, incremental 0/2" in report
    assert "CI-08 promotion bar (C4" in report
    assert "incremental NOT YET TRUSTWORTHY" in report


def test_trust_verdict_is_not_yet_trustworthy_without_shadow_data():
    assert "NOT YET TRUSTWORTHY" in replay.trust_verdict(0)
    assert "NOT YET TRUSTWORTHY" in replay.trust_verdict(
        0, shadow_clean_runs=0)


def test_trust_verdict_with_shadow_data_states_but_does_not_decide():
    below = replay.trust_verdict(1, shadow_clean_runs=3)
    assert "BELOW THE RECOMMENDED BAR" in below
    assert "C4" in below
    meets = replay.trust_verdict(0, shadow_clean_runs=3)
    assert "NOT YET TRUSTWORTHY" not in meets
    assert "C4" in meets


def test_rank_candidates_puts_known_reds_first():
    candidates = [
        {"pr": 12, "known": "PASS", "tests": ["t/slow.py"]},
        {"pr": 7, "known": "FAIL", "tests": ["t/fast.py"]},
        {"pr": 9, "known": "FAIL", "tests": ["t/other.py"]},
    ]
    ranked = replay.rank_candidates(candidates)
    assert [entry["pr"] for entry in ranked] == [7, 9, 12]


def test_rank_candidates_prefers_signal_then_cheaper_cost():
    candidates = [
        {"pr": 1, "known": "PASS", "tests": ["t/a.py"]},
        {"pr": 2, "known": "PASS", "tests": ["t/b.py"]},
        {"pr": 3, "known": "PASS", "tests": ["t/c.py"]},
    ]
    durations = {"t/a.py": 10.0, "t/b.py": 30.0, "t/c.py": 20.0}
    rates = {"t/a.py": 0.0, "t/b.py": 0.9, "t/c.py": 0.1}
    ranked = replay.rank_candidates(candidates, durations, rates)
    assert [entry["pr"] for entry in ranked] == [2, 3, 1]
    # Deterministic for the same inputs.
    again = replay.rank_candidates(candidates, durations, rates)
    assert [entry["pr"] for entry in again] == [2, 3, 1]


def test_rank_candidates_sorts_unmeasured_cost_last():
    candidates = [
        {"pr": 1, "known": "PASS", "tests": ["t/new.py"]},
        {"pr": 2, "known": "PASS", "tests": ["t/old.py"]},
    ]
    ranked = replay.rank_candidates(candidates, {"t/old.py": 5.0}, {})
    assert [entry["pr"] for entry in ranked] == [2, 1]


def test_malformed_manifests_are_refused_with_the_reason(tmp_path):
    with pytest.raises(replay.ReplayRefused):
        replay.load_manifest(str(tmp_path / "missing.json"))
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(replay.ReplayRefused):
        replay.load_manifest(str(bad_json))
    bad_schema = tmp_path / "schema.json"
    bad_schema.write_text(json.dumps({"schema_version": 99, "cases": []}),
                          encoding="utf-8")
    with pytest.raises(replay.ReplayRefused) as excinfo:
        replay.load_manifest(str(bad_schema))
    assert "schema 99" in str(excinfo.value)


def test_build_manifest_refuses_bad_cases():
    assert replay.build_manifest([])["ok"] is False
    assert replay.build_manifest(
        [_one_member_case(), _one_member_case()])["ok"] is False
    no_members = _one_member_case()
    no_members["members"] = []
    assert replay.build_manifest([no_members])["ok"] is False
    no_known = _one_member_case()
    no_known["known"] = {"verdict": "PASS", "kicked": []}
    assert replay.build_manifest([no_known])["ok"] is False
    bad_tree = _one_member_case()
    bad_tree["outcomes"]["fixture/test_a.py"]["pr:9"] = True
    refused = replay.build_manifest([bad_tree])
    assert refused["ok"] is False
    assert "pr:9" in refused["reason"]
    non_bool = _one_member_case()
    non_bool["outcomes"]["fixture/test_a.py"]["stack"] = "yes"
    assert replay.build_manifest([non_bool])["ok"] is False
    no_selection = _one_member_case()
    del no_selection["selection"]
    refused = replay.build_manifest([no_selection])
    assert refused["ok"] is False
    assert "selection" in refused["reason"]
    assert replay.build_manifest(
        [_one_member_case()], selector="oracle")["ok"] is False


def test_build_manifest_accepts_a_valid_case():
    checked = replay.build_manifest([_one_member_case()])
    assert checked["ok"] is True
    manifest = checked["manifest"]
    assert manifest["schema_version"] == replay.SCHEMA_VERSION
    result = replay.replay_corpus(manifest)
    assert result["total"] == 1
    assert result["missed_full"] == 0
    assert result["missed_incremental"] == 0


def test_replay_cli_reports_the_fixture_corpus(tmp_path, capsys):
    code = local_ci_cli.cmd_replay(Namespace(
        corpus=_shipped_corpus(tmp_path), shadow_runs=0))
    assert code == 0
    output = capsys.readouterr().out
    assert "[two-green-prs] known=PASS" in output
    assert "[cross-pr-interaction] known=FAIL" in output
    assert "incremental 0/2" in output
    assert "incremental NOT YET TRUSTWORTHY" in output


def test_replay_cli_refuses_a_missing_corpus(capsys):
    code = local_ci_cli.cmd_replay(Namespace(
        corpus="/nonexistent/replay_corpus.json", shadow_runs=0))
    assert code == 2
    assert "refused" in capsys.readouterr().out


def test_replay_cli_exits_nonzero_on_a_missed_regression(tmp_path, capsys):
    case = _interaction_case()
    case = dict(case)
    selection = {tree: dict(entry) for tree, entry in
                 case["selection"].items()}
    selection["stack"] = {"selected": ["fixture/test_module_a.py",
                                       "fixture/test_module_b.py"]}
    case["selection"] = selection
    manifest = {"schema_version": replay.SCHEMA_VERSION,
                "selector": "scripted", "cases": [case]}
    target = tmp_path / "narrow.json"
    target.write_text(json.dumps(manifest), encoding="utf-8")
    code = local_ci_cli.cmd_replay(Namespace(corpus=str(target),
                                             shadow_runs=0))
    assert code == 1
    assert "incremental miss(es)" in capsys.readouterr().out
