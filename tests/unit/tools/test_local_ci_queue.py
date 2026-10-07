"""The merge queue's defect cases, with scripted stack verdicts.

The load-bearing defect (three main-breaks in the brief): a PR green
alone but red on top of the queue. The queue must kick ONLY the
culprit (or serialize on an interaction), never merge the red stack and
never kick the innocent.
"""

import subprocess
from contextlib import contextmanager

import pytest

from library.tools.local_ci import config, github, hygiene, queue, runner, state


def _git(repo, *argv):
    result = subprocess.run(["git", "-C", str(repo), *argv],
                            capture_output=True, encoding="utf-8",
                            check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _member(pr, head=None):
    return {"pr": pr, "head": head or f"head-{pr}",
            "status": state.ENTRY_QUEUED, "reason": ""}


class FakeWorld(queue.World):
    """Scripted verdicts keyed by member set; scripted main and merges."""

    def __init__(self, fail_when=None, main="base"):
        super().__init__(state_dir="")
        self.fail_when = fail_when or (lambda members: False)
        self._main = main
        self.merges = []
        self.reports = []
        self.tests = 0

    def main_sha(self):
        return self._main

    def pr_head(self, pr):
        return f"head-{pr}"

    def test_stack(self, base, members):
        self.tests += 1
        if self.fail_when([m["pr"] for m in members]):
            return queue.TestResult(False, 0, 1, reason="stack red")
        return queue.TestResult(True, 3, 0)

    def merge_pr(self, pr, head):
        self.merges.append((pr, head))
        self._main = f"merge-{pr}"
        return f"merge-{pr}"

    def report(self, pr, head, url, ok, reason):
        self.reports.append((pr, ok, reason))


@pytest.mark.parametrize("ok", [True, False])
def test_report_links_to_the_pr_when_queue_event_has_no_url(monkeypatch, ok):
    statuses = []
    monkeypatch.setattr(github, "post_status",
                        lambda *args: statuses.append(args))
    monkeypatch.setattr(github, "remove_label", lambda *args: None)

    queue.World().report(42, "head", "", ok, "stack red")

    assert statuses[0][3] == github.pull_url(42)


def test_tier2_fetches_the_exact_base_before_creating_its_checkout(
        monkeypatch, tmp_path):
    events = []

    def fetch_base(mirror, base):
        events.append(("fetch", mirror, base))

    @contextmanager
    def disposable_checkout(mirror, base, state_dir):
        events.append(("checkout", mirror, base, state_dir))
        yield "checkout"

    def stack_checkout(checkout, base, heads):
        events.append(("stack", checkout, base, heads))
        return {"ok": True, "reason": ""}

    def run_gate(checkout, log):
        events.append(("gate", checkout))
        return {"ok": True, "reason": "", "requeue": False}

    monkeypatch.setattr(hygiene, "fetch_base", fetch_base)
    monkeypatch.setattr(hygiene, "disposable_checkout", disposable_checkout)
    monkeypatch.setattr(queue, "_stack_checkout", stack_checkout)
    monkeypatch.setattr(runner, "run_gate", run_gate)

    world = queue.World(mirror="mirror", state_dir=str(tmp_path))
    verdict = world.test_stack("current-main-sha", [_member(1)])

    assert verdict.ok is True
    assert events == [
        ("fetch", "mirror", "current-main-sha"),
        ("checkout", "mirror", "current-main-sha", str(tmp_path)),
        ("stack", "checkout", "current-main-sha", ["head-1"]),
        ("gate", "checkout"),
    ]


def test_a_red_stack_bisects_to_the_culprit_and_merges_the_rest():
    # C fails whenever it is in the stack; A and B always pass.
    world = FakeWorld(fail_when=lambda prs: 3 in prs)
    green, kicked, requeued = queue.resolve_batch(
        world, "base", [_member(1), _member(2), _member(3)])
    assert sorted(m["pr"] for m in green) == [1, 2]
    assert [m["pr"] for m in kicked] == [3]
    assert requeued == []
    assert "stack red" in kicked[0]["reason"]
    assert kicked[0]["status"] == state.ENTRY_KICKED


def test_green_alone_red_together_serializes_instead_of_kicking():
    # A and B pass alone; the combination fails. Neither is the
    # culprit, so nobody is kicked: A lands first, B re-tests on it.
    world = FakeWorld(fail_when=lambda prs: sorted(prs) == [1, 2])
    green, kicked, requeued = queue.resolve_batch(world, "base",
                                                  [_member(1), _member(2)])
    assert kicked == []
    assert requeued == []
    assert [m["pr"] for m in green] == [1]
    assert world.tests == 3  # whole, then each half


def test_real_cluster_stack_merges_each_head_before_the_next(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ren-ci@test")
    _git(repo, "config", "user.name", "ren-ci")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "-c", "pr-a")
    (repo / "a.txt").write_text("A\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "PR A")
    head_a = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "--detach", base)
    _git(repo, "switch", "-c", "pr-b")
    (repo / "b.txt").write_text("B\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "PR B")
    head_b = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "--detach", base)
    result = queue._stack_checkout(str(repo), base, [head_a, head_b])

    assert result["ok"] is True, result["reason"]
    assert (repo / "a.txt").is_file()
    assert (repo / "b.txt").is_file()
    stacked_head = _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "merge-base", "--is-ancestor", head_a,
                stacked_head) == ""
    assert _git(repo, "merge-base", "--is-ancestor", head_b,
                stacked_head) == ""


def test_merge_in_order_chains_freshness_and_counts_landed_shas():
    world = FakeWorld()
    green = [queue._tested(_member(1), "base"),
             queue._tested(_member(2), "base")]
    merged, requeued = queue.merge_in_order(world, "base", green)
    assert [m["pr"] for m in merged] == [1, 2]
    assert requeued == []
    # The second merge was chained on the first one's landed SHA, not
    # on the tested base: main must have read merge-1 in between.
    assert world.merges == [(1, "head-1"), (2, "head-2")]
    assert merged[0]["landed_as"] == "merge-1"


def test_a_moved_main_requeues_the_rest_unmerged():
    world = FakeWorld()
    green = [queue._tested(_member(1), "base"),
             queue._tested(_member(2), "base")]
    real_main = world.main_sha
    calls = {"n": 0}

    def moving_main():
        calls["n"] += 1
        if calls["n"] > 1:
            return "outsider"
        return real_main()

    world.main_sha = moving_main
    merged, requeued = queue.merge_in_order(world, "base", green)
    assert [m["pr"] for m in merged] == [1]
    assert [m["pr"] for m in requeued] == [2]
    assert "outsider" in requeued[0]["reason"]


def test_a_moved_head_requeues_it_and_every_later_member():
    world = FakeWorld()
    first = queue._tested(_member(1), "base")
    stale = queue._tested(_member(2), "base")
    stale["head"] = "head-2-new"  # pushed after the test
    later = queue._tested(_member(3), "base")
    merged, requeued = queue.merge_in_order(
        world, "base", [first, stale, later])
    assert [m["pr"] for m in merged] == [1]
    assert [m["pr"] for m in requeued] == [2, 3]
    assert "head moved" in requeued[0]["reason"]
    assert "earlier batch member" in requeued[1]["reason"]


def test_a_refused_merge_requeues_it_and_every_later_member():
    class Refusing(FakeWorld):
        def merge_pr(self, pr, head):
            if pr == 1:
                raise RuntimeError("409 head moved")
            return super().merge_pr(pr, head)

    world = Refusing()
    green = [queue._tested(_member(1), "base"),
             queue._tested(_member(2), "base")]
    merged, requeued = queue.merge_in_order(world, "base", green)
    assert merged == []
    assert [m["pr"] for m in requeued] == [1, 2]
    assert "409" in requeued[0]["reason"]
    assert "earlier batch member" in requeued[1]["reason"]
    assert world.merges == []


def test_a_cluster_ripens_when_the_window_closes_not_before():
    import time
    now = time.time()
    fresh = [dict(_member(1), enqueued_at=now),
             dict(_member(2), enqueued_at=now)]
    ripe, waiting = queue.plan_cluster(fresh, now + 10, window=90)
    assert ripe == [] and len(waiting) == 2  # window open: group, don't run
    ripe, waiting = queue.plan_cluster(fresh, now + 91, window=90)
    assert [m["pr"] for m in ripe] == [1, 2] and waiting == []


def test_a_full_box_runs_before_the_window_closes():
    import time
    now = time.time()
    full = [dict(_member(n), enqueued_at=now)
            for n in range(config.BATCH_MAX + 1)]
    ripe, waiting = queue.plan_cluster(full, now + 1, window=600)
    assert len(ripe) == config.BATCH_MAX  # oldest runs now, rest waits
    assert len(waiting) == 1


def test_an_unjudgeable_cluster_requeues_instead_of_kicking():
    class Narrowed(FakeWorld):
        def test_stack(self, base, members):
            return queue.TestResult(False, reason="narrowed: no ffmpeg",
                                    requeue=True)

    world = Narrowed()
    green, kicked, requeued = queue.resolve_batch(world, "base",
                                                  [_member(1), _member(2)])
    assert green == [] and kicked == []  # nobody judged, nobody blamed
    assert sorted(m["pr"] for m in requeued) == [1, 2]
    assert all(m["status"] == state.ENTRY_QUEUED for m in requeued)


def test_process_once_merges_green_in_order_and_kicks_the_culprit(tmp_path):
    world = FakeWorld(fail_when=lambda prs: 12 in prs)
    world.state_dir = str(tmp_path)
    state.write_queue([_member(11), _member(12)], str(tmp_path))
    summary = queue.process_once(world, window=0)  # ripe immediately
    assert summary["ripe"] is True
    assert summary["merged"] == [11]
    assert summary["kicked"] == [12]
    entries = {e["pr"]: e for e in state.read_queue(str(tmp_path))}
    assert entries[11]["status"] == state.ENTRY_MERGED
    assert entries[12]["status"] == state.ENTRY_KICKED
    assert any(r == (12, False, "stack red") for r in world.reports)


def test_process_once_waits_out_an_unripe_window(tmp_path):
    import time
    world = FakeWorld()
    world.state_dir = str(tmp_path)
    now = time.time()
    state.write_queue([dict(_member(11), enqueued_at=now)], str(tmp_path))
    summary = queue.process_once(world, window=600)
    assert summary["ripe"] is False
    assert summary["waiting"] == 1
    assert world.tests == 0  # the timebox exists to group: no run yet


def test_process_once_persists_verdicts_when_reporting_fails(tmp_path):
    class BrokenReporter(FakeWorld):
        def report(self, pr, head, url, ok, reason):
            raise RuntimeError("gh-axi has no repository context")

    world = BrokenReporter(fail_when=lambda prs: 12 in prs)
    world.state_dir = str(tmp_path)
    state.write_queue([_member(11), _member(12)], str(tmp_path))

    summary = queue.process_once(world, window=0)

    assert summary["merged"] == [11]
    assert summary["kicked"] == [12]
    entries = {entry["pr"]: entry
               for entry in state.read_queue(str(tmp_path))}
    assert entries[11]["status"] == state.ENTRY_MERGED
    assert entries[12]["status"] == state.ENTRY_KICKED


def test_process_once_requeues_if_the_tier2_runner_raises(tmp_path):
    class BrokenRunner(FakeWorld):
        def test_stack(self, base, members):
            raise RuntimeError("worktree gate could not start")

    world = BrokenRunner()
    world.state_dir = str(tmp_path)
    state.write_queue([_member(11), _member(12)], str(tmp_path))

    summary = queue.process_once(world, window=0)

    assert summary["requeued"] == [11, 12]
    assert summary["kicked"] == [] and summary["merged"] == []
    entries = state.read_queue(str(tmp_path))
    assert all(entry["status"] == state.ENTRY_QUEUED for entry in entries)
    assert all("runner interrupted" in entry["reason"] for entry in entries)


def test_enqueue_supersedes_a_moved_head_but_not_a_rerun(tmp_path):
    first = state.enqueue(7, "head-a", str(tmp_path))
    same = state.enqueue(7, "head-a", str(tmp_path))
    assert same == first
    assert len(state.read_queue(str(tmp_path))) == 1
    state.enqueue(7, "head-b", str(tmp_path))
    entries = state.read_queue(str(tmp_path))
    assert [e["head"] for e in entries] == ["head-b"]
