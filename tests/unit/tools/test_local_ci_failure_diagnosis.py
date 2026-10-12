"""CI-12: failing-test-focused failure diagnosis (replaces full-suite bisection).

Scripted-`World` proofs only: no gate runs, no daemon, no `~/.ren-ci`,
no Resolve. Every world here is hermetic (`state_dir=""`); JUnit
fixtures build under `tmp_path`. The worlds clear the host/agent
environment markers they could depend on (`VEP_COVERAGE_*`,
`REN_CI_*`) so a developer's export never flips them.
"""

import os

import pytest

from library.tools.local_ci import queue, runner, state


FAILING = "tests/unit/tools/test_speaker_identity.py"
OTHER = "tests/unit/tools/test_timeline_construction.py"


def _member(pr):
    return {"pr": pr, "head": f"head-{pr}",
            "status": state.ENTRY_QUEUED, "reason": ""}


def _clear_ci_env(monkeypatch):
    for name in ("VEP_COVERAGE_INDEX_FILE", "VEP_COVERAGE_TREE_SHA",
                 "VEP_COVERAGE_INTERPRETER", "VEP_COVERAGE_NATIVE",
                 "REN_CI_RESULT_CACHE", "REN_CI_CACHE_VERSIONS"):
        monkeypatch.delenv(name, raising=False)


class FocusedWorld(queue.World):
    """Scripted red stack with a JUnit-style failing-test read.

    `fail_when(prs)` decides red; a red FULL gate names `failing` as
    its failed tests. A focused run reproduces only when it is asked
    for an overlapping file AND its member set would fail the full
    gate - blame only on reproduction. `changed_by` + `affects`
    script the dependency map; `relevant_members` goes through the
    real pure partition (`relevant_partition_by_map`) so the
    dependency guidance itself is exercised, not stubbed.
    """

    def __init__(self, fail_when=None, failing=(FAILING,),
                 changed_by=None, affects=None):
        super().__init__(state_dir="")
        self.fail_when = fail_when or (lambda prs: False)
        self.failing = list(failing)
        self.changed_by = dict(changed_by or {})
        self.affects = affects or (lambda changed: set())
        self.full_gate_members = []
        self.focused_members = []
        self.focused_files = []
        self.main_tests = 0

    def test_main(self, base):
        self.main_tests += 1
        if self.fail_when([]):
            return queue.TestResult(False, 0, 1, reason="stack red")
        return queue.TestResult(True, 3, 0)

    def main_sha(self):
        return "base"

    def pr_head(self, pr):
        return f"head-{pr}"

    def test_stack(self, base, members):
        self.full_gate_members.append([m["pr"] for m in members])
        if self.fail_when([m["pr"] for m in members]):
            return queue.TestResult(False, 0, 1, reason="stack red",
                                    failed_tests=list(self.failing))
        return queue.TestResult(True, 3, 0)

    def test_subset(self, base, members, test_files):
        self.focused_members.append([m["pr"] for m in members])
        self.focused_files.append(list(test_files))
        prs = [m["pr"] for m in members]
        if set(test_files) & set(self.failing) and self.fail_when(prs):
            return queue.TestResult(False, 0, 1, reason="focused red",
                                    failed_tests=list(self.failing))
        return queue.TestResult(True, 1, 0)

    def relevant_members(self, base, members, failing_tests):
        changed_by = {m["pr"]: self.changed_by.get(m["pr"], [])
                      for m in members}
        relevant_ids, _ = queue.relevant_partition_by_map(
            changed_by, failing_tests, select_fn=self.affects)
        relevant = [m for m in members if m["pr"] in relevant_ids]
        irrelevant = [m for m in members if m["pr"] not in relevant_ids]
        if not relevant:
            return list(members), []
        return relevant, irrelevant

    def merge_pr(self, pr, head):
        return f"merge-{pr}"

    def report(self, pr, head, url, ok, reason):
        pass


def _twenty_members():
    return [_member(pr) for pr in range(1, 21)]


def test_twenty_pr_red_batch_names_the_culprit_with_focused_runs_only(
        monkeypatch):
    """Acceptance: the same 20-PR script costs full gates per node on
    main, and only failing files per node after CI-12."""
    _clear_ci_env(monkeypatch)
    culprit = 17
    failing = [FAILING]
    changed_by = {pr: [f"library/tools/module_{pr}.py"]
                  for pr in range(1, 21)}
    changed_by[culprit] = ["library/tools/speaker_identity.py"]

    def affects(changed):
        if any("speaker_identity" in path for path in changed):
            return {FAILING}
        return {OTHER}

    world = FocusedWorld(fail_when=lambda prs: culprit in prs,
                         failing=failing, changed_by=changed_by,
                         affects=affects)
    green, kicked, requeued = queue.resolve_batch(
        world, "base", _twenty_members())
    assert [m["pr"] for m in kicked] == [culprit]
    assert sorted(m["pr"] for m in green) == sorted(
        pr for pr in range(1, 21) if pr != culprit)
    assert requeued == []
    assert world.main_tests == 1  # red-main baseline check kept
    # Every bisect node ran only the failing files, never the suite.
    assert world.focused_members, "expected focused reproduction runs"
    assert all(files == failing for files in world.focused_files)
    # Full gates: the initial red stack plus the survivors'
    # revalidation - no full gate per bisect node.
    assert len(world.full_gate_members) == 2
    assert sorted(world.full_gate_members[0]) == list(range(1, 21))
    assert sorted(world.full_gate_members[-1]) == sorted(
        pr for pr in range(1, 21) if pr != culprit)
    assert "failing:" in kicked[0]["reason"] and FAILING in kicked[0]["reason"]


def test_the_same_script_paid_a_full_gate_per_bisect_node_before(
        monkeypatch):
    """The cost CI-12 removes: a report-less red pays full gates down
    the bisect (legacy fallback), so the acceptance script above is
    strictly cheaper after."""
    _clear_ci_env(monkeypatch)

    class Reportless(FocusedWorld):
        def test_stack(self, base, members):
            self.full_gate_members.append([m["pr"] for m in members])
            if self.fail_when([m["pr"] for m in members]):
                return queue.TestResult(False, 0, 1, reason="stack red")
            return queue.TestResult(True, 3, 0)

    world = Reportless(fail_when=lambda prs: 17 in prs)
    green, kicked, requeued = queue.resolve_batch(
        world, "base", _twenty_members())
    assert [m["pr"] for m in kicked] == [17]
    assert world.focused_members == []
    # Halve-and-full-gate recursion: far more than the two full gates
    # the focused path spends (initial + revalidation).
    assert len(world.full_gate_members) > 2


def test_irrelevant_prs_are_never_reproduced(monkeypatch):
    """Dependency guidance: only the PR mapped onto the failing test
    is ever asked to reproduce it."""
    _clear_ci_env(monkeypatch)
    changed_by = {1: ["library/tools/speaker_identity.py"],
                  2: ["library/tools/subtitle_renderer.py"],
                  3: ["library/tools/render_adapter.py"]}

    def affects(changed):
        if any("speaker_identity" in path for path in changed):
            return {FAILING}
        return {OTHER}

    world = FocusedWorld(fail_when=lambda prs: 1 in prs,
                         changed_by=changed_by, affects=affects)
    green, kicked, requeued = queue.resolve_batch(
        world, "base", [_member(1), _member(2), _member(3)])
    assert [m["pr"] for m in kicked] == [1]
    assert sorted(m["pr"] for m in green) == [2, 3]
    assert requeued == []
    for prs in world.focused_members:
        assert 2 not in prs and 3 not in prs
    assert all(files == [FAILING] for files in world.focused_files)


def test_survivors_revalidate_with_the_full_gate_before_merging(
        monkeypatch):
    """Focused green is reproduction evidence, never merge evidence:
    the survivors' full gate is the last full run and it is green."""
    _clear_ci_env(monkeypatch)
    world = FocusedWorld(fail_when=lambda prs: 2 in prs)
    green, kicked, requeued = queue.resolve_batch(
        world, "base", [_member(1), _member(2), _member(3)])
    assert [m["pr"] for m in kicked] == [2]
    assert sorted(m["pr"] for m in green) == [1, 3]
    assert world.full_gate_members[-1] == [1, 3] or \
        sorted(world.full_gate_members[-1]) == [1, 3]
    assert len(world.full_gate_members) == 2


def test_green_alone_red_together_serializes_with_revalidation(
        monkeypatch):
    """Combinations included: halves pass the failing files alone, so
    the first half revalidates (full gate) and lands while the second
    re-tests on the new main."""
    _clear_ci_env(monkeypatch)
    world = FocusedWorld(
        fail_when=lambda prs: 1 in prs and 2 in prs)
    green, kicked, requeued = queue.resolve_batch(
        world, "base", [_member(1), _member(2)])
    assert kicked == []
    assert [m["pr"] for m in green] == [1]
    assert [m["pr"] for m in requeued] == [2]
    assert any("interaction" in m.get("reason", "")
               for m in requeued)
    # The landed half was revalidated with the full gate, not just
    # the focused files.
    assert world.full_gate_members[-1] == [1]


def test_an_unjudgeable_focused_node_requeues_instead_of_kicking(
        monkeypatch):
    """A focused run that judges nothing poisons the diagnosis: the
    whole batch waits rather than kicking on partial evidence."""
    _clear_ci_env(monkeypatch)

    class Unjudgeable(FocusedWorld):
        def test_subset(self, base, members, test_files):
            self.focused_members.append([m["pr"] for m in members])
            self.focused_files.append(list(test_files))
            return queue.TestResult(False, requeue=True,
                                    reason="focused run timed out")

    world = Unjudgeable(fail_when=lambda prs: 1 in prs)
    green, kicked, requeued = queue.resolve_batch(
        world, "base", [_member(1), _member(2)])
    assert green == [] and kicked == []
    assert sorted(m["pr"] for m in requeued) == [1, 2]


def test_unknown_prs_stay_relevant_and_never_exonerate(monkeypatch):
    """Fail open: a PR with no changed files, a failing selector, and
    an empty failing list all keep the PR relevant."""
    _clear_ci_env(monkeypatch)
    relevant, irrelevant = queue.relevant_partition_by_map(
        {1: ["a.py"], 2: []}, [FAILING],
        select_fn=lambda changed: {FAILING})
    assert relevant == [1, 2] and irrelevant == []

    def broken(changed):
        raise RuntimeError("selector down")

    relevant, irrelevant = queue.relevant_partition_by_map(
        {1: ["a.py"]}, [FAILING], select_fn=broken)
    assert relevant == [1] and irrelevant == []

    relevant, irrelevant = queue.relevant_partition_by_map(
        {1: ["a.py"]}, [], select_fn=lambda changed: {OTHER})
    assert relevant == [1] and irrelevant == []

    relevant, irrelevant = queue.relevant_partition_by_map(
        {1: ["a.py"], 2: ["b.py"]}, [FAILING],
        select_fn=lambda changed: {FAILING} if changed == ["a.py"]
        else {OTHER})
    assert relevant == [1] and irrelevant == [2]


def test_test_result_defaults_to_no_failing_tests():
    """Old scripted worlds (no failed_tests) keep the legacy shape:
    empty, so resolve_batch falls back to the full-gate bisect."""
    verdict = queue.TestResult(False, 0, 1, reason="stack red")
    assert verdict.failed_tests == []


def _write_junit(path, cases):
    header = ('<?xml version="1.0" encoding="utf-8"?><testsuite>'
              '<testsuites><testsuite name="pytest">')
    footer = '</testsuite></testsuites></testsuite>'
    body = "".join(cases)
    path.write_text(header + body + footer, encoding="utf-8")


def test_failed_tests_from_junit_names_only_failed_files(tmp_path,
                                                         monkeypatch):
    """The 'which tests failed' read: failures/errors named, passes
    and skips excluded, missing/unparseable reports empty."""
    _clear_ci_env(monkeypatch)
    assert runner.failed_tests_from_junit("") == []
    assert runner.failed_tests_from_junit(
        str(tmp_path / "missing.xml")) == []
    broken = tmp_path / "broken.xml"
    broken.write_text("not xml at all <", encoding="utf-8")
    assert runner.failed_tests_from_junit(str(broken)) == []

    report = tmp_path / "report.xml"
    _write_junit(report, [
        f'<testcase classname="unit.tools.test_a" name="t1" '
        f'file="{FAILING}"/>',
        f'<testcase classname="unit.tools.test_b" name="t1" '
        f'file="{OTHER}"><failure message="x">boom</failure></testcase>',
        f'<testcase classname="unit.tools.test_c" name="t1" '
        f'file="tests/unit/tools/test_skipped.py">'
        f'<skipped message="s"/></testcase>',
    ])
    assert runner.failed_tests_from_junit(str(report)) == [OTHER]
    assert os.path.isfile(str(report))
