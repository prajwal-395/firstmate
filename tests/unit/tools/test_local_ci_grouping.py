"""Adaptive batch grouping by expected cost (CI-11).

Grouping is window-only on main (`plan_cluster` takes everything):
these tests pin that a fixture queue of overlapping-subsystem PRs
groups DIFFERENTLY from independent PRs with the cost terms shown,
and that a single-PR queue still verifies immediately. Hermetic: all
cost inputs (wall, base rate, now, the graph callable) are explicit,
and host/agent environment markers are cleared.
"""

import base64
import json

import pytest

from library.tools.local_ci import github, grouping, queue, state


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for marker in ("VEP_TEST_FAILURE_RATES_FILE",
                   "VEP_COVERAGE_INDEX_FILE",
                   "VEP_TEST_TIMING_FILE",
                   "REN_CI_STATE",
                   "REN_CI_TIER1_BUDGET_SECONDS"):
        monkeypatch.delenv(marker, raising=False)


def _entry(pr, files, now, age=100.0):
    return {"pr": pr, "head": f"head-{pr}",
            "status": state.ENTRY_QUEUED, "reason": "",
            "enqueued_at": now - age, "files": list(files)}


def _overlapping_queue(now):
    return [
        _entry(1, ["library/tools/local_ci/queue.py"], now),
        _entry(2, ["library/tools/local_ci/runner.py"], now),
        _entry(3, ["tests/unit/tools/test_a.py"], now),
        _entry(4, ["tests/unit/tools/test_b.py"], now),
    ]


def _independent_queue(now):
    return [
        _entry(1, ["library/tools/local_ci/queue.py"], now),
        _entry(2, ["ren/cli.py"], now),
        _entry(3, ["scripts/full_suite_gate.sh"], now),
        _entry(4, ["native/ren-core/src/lib.rs"], now),
    ]


def test_overlapping_and_independent_queues_group_differently():
    now = 1_000_000.0
    overlapping = grouping.plan_groups(
        _overlapping_queue(now), wall_s=600.0, base=0.25, now=now)
    independent = grouping.plan_groups(
        _independent_queue(now), wall_s=600.0, base=0.25, now=now)
    # Overlapping pairs split into per-subsystem risk clusters;
    # independents pool freely into one group.
    assert overlapping["groups"] == [[1, 2], [3, 4]]
    assert independent["groups"] == [[1, 2, 3, 4]]
    # The cost terms are shown on the overlapping advice.
    text = "\n".join(overlapping["advice"])
    assert "share subsystem(s) library/tools" in text
    assert "share subsystem(s) tests/unit" in text
    for term in ("verify", "diagnose", "split-risk", "merge", "wait"):
        assert term in text
    assert "-> clustered" in text
    # The independent advice pools freely and shows its terms too.
    free = "\n".join(independent["advice"])
    assert "pooling freely" in free
    assert "expected" in free


def test_every_member_is_grouped_exactly_once():
    now = 1_000_000.0
    plan = grouping.plan_groups(
        _overlapping_queue(now), wall_s=600.0, base=0.25, now=now)
    assert sorted(number for group in plan["groups"]
                  for number in group) == [1, 2, 3, 4]


def test_a_single_pr_verifies_immediately_without_grouping_tax():
    now = 1_000_000.0
    plan = grouping.plan_groups(
        [_entry(7, ["library/tools/local_ci/queue.py"], now)],
        wall_s=600.0, base=0.25, now=now)
    assert plan["groups"] == [[7]]
    assert plan["advice"] == [
        "ren-ci grouping: single-PR cluster verifies "
        "immediately - no grouping tax"]


def test_cold_pools_everything_and_says_so():
    now = 1_000_000.0
    plan = grouping.plan_groups(
        _overlapping_queue(now), wall_s=600.0, base=0.0, now=now)
    assert plan["groups"] == [[1, 2, 3, 4]]
    assert "cold" in "\n".join(plan["advice"])


def test_unknown_files_group_with_eyes_open_never_excluded():
    now = 1_000_000.0
    entries = [_entry(1, ["library/tools/local_ci/queue.py"], now),
               {"pr": 2, "head": "head-2",
                "status": state.ENTRY_QUEUED, "reason": "",
                "enqueued_at": now - 100.0}]
    plan = grouping.plan_groups(entries, wall_s=600.0, base=0.25,
                                now=now)
    assert plan["groups"] == [[1, 2]]
    assert "unknown files - assuming overlap" in \
        "\n".join(plan["advice"])


def test_selection_overlap_links_across_subsystems():
    now = 1_000_000.0
    entries = [_entry(1, ["library/tools/local_ci/queue.py"], now),
               _entry(2, ["ren/cli.py"], now)]

    def select(files):
        return ["tests/contracts/test_shared.py"]

    plan = grouping.plan_groups(entries, wall_s=600.0, base=0.25,
                                now=now, select=select)
    assert plan["graph"][1][2].startswith("share selected test(s)")
    assert plan["groups"] == [[1, 2]]


def test_groups_order_oldest_first():
    now = 1_000_000.0
    entries = [_entry(1, ["ren/cli/a.py"], now, age=500.0),
               _entry(2, ["ren/cli/b.py"], now, age=500.0),
               _entry(3, ["library/tools/local_ci/queue.py"], now,
                       age=100.0),
               _entry(4, ["library/tools/local_ci/runner.py"], now,
                       age=100.0)]
    plan = grouping.plan_groups(entries, wall_s=600.0, base=0.25,
                                now=now)
    assert plan["groups"][0] == [1, 2]


class _FilesWorld(queue.World):
    """Scripted verdicts plus scripted diffs, fully hermetic."""

    def __init__(self, entries, **kwargs):
        super().__init__(**kwargs)
        self._files = {entry["pr"]: list(entry.get("files") or [])
                       for entry in entries}
        self._main = "base"
        self.tests = 0

    def main_sha(self):
        return self._main

    def pr_head(self, pr):
        return f"head-{pr}"

    def pr_files(self, pr):
        return list(self._files.get(pr, []))

    def test_stack(self, base, members):
        self.tests += 1
        return queue.TestResult(True, 3, 0)

    def merge_pr(self, pr, head):
        self._main = f"merge-{pr}"
        return f"merge-{pr}"

    def report(self, pr, head, url, ok, reason):
        pass


def _select_nothing(files):
    return []


def test_process_once_prints_advice_and_still_verifies_whole(
        tmp_path, capsys, monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr("time.time", lambda: now)
    monkeypatch.setenv("VEP_TEST_TIMING_FILE",
                       str(tmp_path / "no-telemetry.json"))
    world = _FilesWorld(_overlapping_queue(now),
                        state_dir=str(tmp_path))
    state.write_queue(_overlapping_queue(now), str(tmp_path))
    summary = queue.process_once(world, window=0,
                                 select=_select_nothing)
    assert summary["ripe"] is True
    assert summary["merged"] == [1, 2, 3, 4]
    assert world.tests == 1  # advice never splits the verify
    out = capsys.readouterr().out
    assert "ren-ci grouping: PR #1 x PR #2" in out
    assert "split-risk" in out


def test_process_once_single_pr_verifies_with_one_gate(
        tmp_path, capsys, monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr("time.time", lambda: now)
    monkeypatch.setenv("VEP_TEST_TIMING_FILE",
                       str(tmp_path / "no-telemetry.json"))
    entries = [_entry(7, ["library/tools/local_ci/queue.py"], now)]
    world = _FilesWorld(entries, state_dir=str(tmp_path))
    state.write_queue(entries, str(tmp_path))
    summary = queue.process_once(world, window=0,
                                 select=_select_nothing)
    assert summary["merged"] == [7]
    assert world.tests == 1
    assert "no grouping tax" in capsys.readouterr().out


def test_advise_for_cluster_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("VEP_TEST_TIMING_FILE",
                       str(tmp_path / "no-telemetry.json"))
    lines = grouping.advise_for_cluster(
        [{"pr": 1}], state_dir=str(tmp_path), now=1_000_000.0,
        select=_select_nothing)
    assert lines and all(isinstance(line, str) for line in lines)


def test_pr_files_shapes_filenames(monkeypatch):
    records = [{"filename": "library/tools/local_ci/queue.py"},
               {"filename": "tests/unit/tools/test_x.py"}]
    payload = "\n".join(
        base64.b64encode(
            json.dumps(record).encode("utf-8")).decode("ascii")
        for record in records)
    monkeypatch.setattr(github, "_api", lambda *args, **kwargs: payload)
    assert github.pr_files(41) == [
        "library/tools/local_ci/queue.py",
        "tests/unit/tools/test_x.py"]
