"""No expensive GitHub job may start without a human asking for it.

The account exhausted its 2,000 Actions minutes on 26 August 2026 and CI
was dead for the last five days of the month - 82 jobs blocked, zero
verdicts.  On 5 September it stood at 1,801 of 2,000 with 26 days to the
reset.  Both times the cause was the same: the full suite ran on triggers
nobody chose, because it was inherited from the GitHub Actions starter
template on 2026-08-09 and never revisited.

`docs/CI_LAYERS.md` has the measurement.  These checks are structural -
they read the workflow, not a run - so they cost nothing, and they fail
the moment an expensive job becomes reachable without a label again.

This is AGENTS.md 10.4's rule pointed at the workflow's TRIGGERS, the way
`tests/test_ci_can_fail.py` points it at the workflow's STEPS.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML parses the workflow; it is in requirements.txt")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LOCAL_GATE = REPO_ROOT / "scripts" / "full_suite_gate.sh"
LAYERS_DOC = REPO_ROOT / "docs" / "CI_LAYERS.md"

# The label that arms each job.  A job not listed here is a job nobody
# decided to pay for.
JOB_LABELS = {
    "clean-room-gate": "run-tests",
    "heavy-ml-suite": "run-heavy-ml",
}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict:
    # PyYAML resolves the bare key `on` to the boolean True.
    wf = _workflow()
    return wf.get("on", wf.get(True))


def _condition(job: str) -> str:
    jobs = _workflow()["jobs"]
    assert job in jobs, f"{job!r} is gone from the workflow; {sorted(jobs)} remain"
    condition = jobs[job].get("if")
    assert condition, (
        f"job {job!r} has no `if:` at all, so it runs on every event the "
        f"workflow accepts. That is how the suite came to run 2.5 times per PR."
    )
    return " ".join(str(condition).split())


def test_no_push_trigger():
    """A run against the tree the PR had just proved green buys nothing.

    Measured 1-5 September: 307 billed minutes, 17% of the spend.
    """
    assert "push" not in _triggers(), (
        "`push` is back in the workflow triggers. Re-running the suite after "
        "a merge re-tests a tree a gate has already passed; it cost 307 "
        "billed minutes in five days and produced no information."
    )


def test_no_workflow_dispatch_trigger():
    """104 billed minutes in five days reached the expensive jobs this way."""
    assert "workflow_dispatch" not in _triggers(), (
        "`workflow_dispatch` is back. It is a button that spends 13 minutes "
        "with no batch behind it. If you need a manual run, apply the label - "
        "the label is the record of who asked."
    )


def test_the_only_pull_request_event_is_a_label():
    """`synchronize` is what made the suite run 2.5 times per PR."""
    events = _triggers()["pull_request"]["types"]
    assert events == ["labeled"], (
        f"pull_request fires on {events}. `synchronize` re-runs the workflow "
        f"on every push to a branch: 588 of 1,156 PR suite-minutes in "
        f"September (51%) were repeat runs of a branch that had already run "
        f"it. One gate per BATCH, not per push."
    )


def test_the_path_filter_job_is_gone():
    """A whole billed minute to compute a boolean the label already encodes.

    GitHub's floor is one minute PER JOB, so a 7-second job costs the same
    as a 60-second one. 52 minutes in September for one boolean.
    """
    jobs = _workflow()["jobs"]
    assert "path-filter" not in jobs, "the path-filter job is back"
    uses = [
        step.get("uses", "")
        for job in jobs.values()
        for step in job.get("steps", [])
    ]
    assert not any("paths-filter" in u for u in uses), (
        "a paths-filter action is back in the workflow. Once the expensive "
        "jobs are label-gated the label IS the filter, and the filter job is "
        "a billed minute spent on something already known."
    )


def test_every_job_is_armed_by_a_label():
    """No job may be reachable without someone deciding to pay for it."""
    jobs = _workflow()["jobs"]
    assert set(jobs) == set(JOB_LABELS), (
        f"the workflow's jobs are {sorted(jobs)}, expected {sorted(JOB_LABELS)}. "
        f"A new job here is a new standing cost: add it to JOB_LABELS with the "
        f"label that arms it, or put the check in the local layer "
        f"(docs/CI_LAYERS.md)."
    )


@pytest.mark.parametrize("job,label", sorted(JOB_LABELS.items()))
def test_the_job_fires_only_on_its_own_label(job: str, label: str):
    condition = _condition(job)
    assert "github.event_name == 'pull_request'" in condition, (
        f"{job}'s condition does not pin the event to `pull_request`, so "
        f"re-adding a trigger to `on:` would silently re-open it"
    )
    assert "github.event.action == 'labeled'" in condition, (
        f"{job} does not require a `labeled` action, so any accepted "
        f"pull_request event would start it"
    )
    assert f"github.event.label.name == '{label}'" in condition, (
        f"{job} does not require the {label!r} label. The label is the "
        f"whole gate: it is what makes the run DELIBERATE and once per batch."
    )


def test_the_workflow_states_the_clean_rooms_purpose():
    """A purpose nobody wrote down is a purpose the next edit widens.

    `heavy-ml-suite` was 9 billed minutes of `pip install` to run TWO
    tests; `full-suite` came back onto every PR event on an estimate that
    understated its cost by 100%. Both were reasonable-looking widenings
    of a job whose reason to exist was never in the file.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "clean-room" in text.lower() or "clean room" in text.lower(), (
        "the workflow does not name itself a clean-room gate"
    )
    for evidence in ("httpx", "ffmpeg", "declared manifests", "docs/CI_LAYERS.md"):
        assert evidence in text, (
            f"the workflow no longer names {evidence!r}. Its header is the "
            f"only thing standing between this job and being widened back "
            f"into a general test runner."
        )


def test_the_local_layer_exists_and_is_runnable():
    """Layer 2 is only affordable because layer 1 does the real work."""
    assert LAYERS_DOC.exists(), "docs/CI_LAYERS.md is gone"
    assert LOCAL_GATE.exists(), (
        "scripts/full_suite_gate.sh is gone. Deleting the local full-suite "
        "gate leaves the label-gated clean room as the ONLY test run in the "
        "project, which it was never meant to be."
    )
    assert os.stat(LOCAL_GATE).st_mode & stat.S_IXUSR, (
        f"{LOCAL_GATE.name} is not executable, so it is not one command"
    )
    assert str(LOCAL_GATE.relative_to(REPO_ROOT)) in WORKFLOW.read_text(encoding="utf-8"), (
        "the workflow does not point at the local gate, so a reader of the "
        "workflow cannot find the layer that does the real testing"
    )


def test_the_local_gate_cannot_report_a_pass_it_did_not_earn(tmp_path):
    """The defect this project spent two days removing, in its purest form.

    A shim standing in for the interpreter exits 0 and writes nothing.
    Every exit code the gate sees is 0. If the verdict came from exit
    codes it would read PASS while having measured nothing at all - which
    is precisely how `ruff ... || true` emitted 2,896 errors and concluded
    SUCCESS (see tests/test_ci_can_fail.py).
    """
    shim = tmp_path / "runs_nothing.sh"
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)
    result = subprocess.run(
        [str(LOCAL_GATE)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        cwd=str(REPO_ROOT),
        env={**os.environ, "FULL_SUITE_GATE_PYTHON": str(shim)},
    )

    verdicts = [
        line for line in result.stdout.splitlines()
        if line.startswith("FULL-SUITE GATE:")
    ]
    assert len(verdicts) == 1, (
        f"the gate must print exactly one verdict line; got {verdicts}"
    )
    verdict = verdicts[0]
    assert "PASS" not in verdict, (
        f"the gate reported {verdict!r} while pytest never ran. A gate that "
        f"cannot fail is worse than no gate, because it reads as coverage."
    )
    assert "DID NOT RUN" in verdict, verdict
    assert result.returncode != 0, (
        "the gate exited 0 without measuring anything, so a caller checking "
        "only the exit status is told it passed"
    )


def test_the_local_gate_refuses_a_pass_when_pytest_exits_nonzero_without_failures(
    tmp_path,
):
    """The mirror of the shim above, and the shape of the 2026-09-12 incident.

    A shim standing in for the interpreter writes a CLEAN JUnit report -
    a positive test count with zero failures and zero errors - and then
    exits 1, which is what the suite did when a runtime skip matched no
    `EnvironmentCondition` (the session hook in the repo-root conftest
    sets the exit status without recording a failure).  The gate's own
    accounting must refuse that run a pass: trusting pytest's summary
    over the exit code is the failure this repository spent two days
    removing, and "fixing" a red gate that way is loosening, not repair.
    """
    shim = tmp_path / "clean_report_dirty_exit.sh"
    shim.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do\n"
        "  case \"$a\" in\n"
        "    --junitxml=*)\n"
        "      out=\"${a#--junitxml=}\"\n"
        "      cat > \"$out\" <<'XML'\n"
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<testsuites>\n"
        "  <testsuite name=\"pytest\" tests=\"3\" skipped=\"1\" "
        "failures=\"0\" errors=\"0\">\n"
        "    <testcase name=\"test_a\" classname=\"m\"/>\n"
        "    <testcase name=\"test_b\" classname=\"m\"/>\n"
        "    <testcase name=\"test_c\" classname=\"m\">\n"
        "      <skipped message=\"needs remotion-subtitles/node_modules, "
        "npx and ffmpeg\"/>\n"
        "    </testcase>\n"
        "  </testsuite>\n"
        "</testsuites>\n"
        "XML\n"
        "      exit 1\n"
        "      ;;\n"
        "  esac\n"
        "done\n"
        'exec python3 "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    result = subprocess.run(
        [str(LOCAL_GATE), "--skip-heavy-ml"],
        capture_output=True,
        encoding="utf-8",
        check=False,
        cwd=str(REPO_ROOT),
        env={**os.environ, "FULL_SUITE_GATE_PYTHON": str(shim)},
    )

    verdicts = [
        line for line in result.stdout.splitlines()
        if line.startswith("FULL-SUITE GATE:")
    ]
    assert len(verdicts) == 1, (
        f"the gate must print exactly one verdict line; got {verdicts}"
    )
    verdict = verdicts[0]
    assert verdict.startswith("FULL-SUITE GATE: FAIL"), (
        f"the gate reported {verdict!r} for a run whose pytest exited 1. "
        f"A positive test count with zero recorded failures is required "
        f"for a pass - an exit code is not a pass."
    )
    assert "no recorded failure but pytest exited 1" in verdict, verdict
    assert result.returncode != 0, (
        "the gate exited 0 for a run whose pytest exited 1, so a caller "
        "checking only the exit status is told it passed"
    )


def test_the_local_gate_names_what_it_declined_to_measure():
    """A pass must not read wider than it is.

    The two Resolve-driving files are excluded because they switch the
    running application's current timeline. That exclusion is correct and
    it must be VISIBLE, the same way pytest is run with `-rs` so that
    `131 skipped` names something.
    """
    source = LOCAL_GATE.read_text(encoding="utf-8")
    for excluded in (
        "tests/test_marker_capture_against_resolve.py",
        "tests/test_marker_feedback_against_resolve.py",
    ):
        assert excluded in source, f"{excluded} is no longer named in the gate"
    assert "not measured by this gate" in source, (
        "the gate no longer prints what it excluded, so its PASS reads as "
        "wider coverage than it has"
    )


def test_the_local_gate_runs_the_heavy_ml_tier_the_hosted_job_gave_up():
    """Demoting `heavy-ml-suite` is only honest if something else runs it.

    The hosted job spent 9 billed minutes installing dependencies to run
    two tests, and could not exercise `mlx_vlm` at all - it is darwin-only.
    Those tests moved to the local layer, where the Apple-Silicon
    dependencies are the real ones.
    """
    source = LOCAL_GATE.read_text(encoding="utf-8")
    assert "-m heavy_ml" in source, (
        "the local gate no longer runs the heavy_ml selection, so demoting "
        "the hosted heavy-ml-suite job drops that coverage entirely rather "
        "than moving it"
    )
    assert "NOT MEASURED" in source, (
        "the gate must be able to say the heavy_ml tier did not run, rather "
        "than folding an unmeasured tier into a pass"
    )


def test_the_heavy_ml_tier_is_refused_rather_than_failed_when_it_cannot_run():
    """An absent dependency is not a defect in the code under test.

    The heavy_ml tests ASSERT that a real measurement happened instead of
    skipping (AGENTS.md 10.3, "a file on disk is not a measurement"), so
    an interpreter without `whisperx` fails them for a reason that has
    nothing to do with the diff. That is a gate failing correct output,
    which AGENTS.md 10.4 says is no more coverage than a gate that cannot
    fail - and a permanently red gate is one everybody learns to ignore.

    So the tier must be REFUSED, by name, rather than mis-reported. The
    precondition is importability and nothing else: if the dependencies
    import and a test then fails, that is a real FAIL.
    """
    source = LOCAL_GATE.read_text(encoding="utf-8")
    assert "HEAVY_DEPS=" in source, (
        "the gate no longer checks that the heavy_ml dependencies are "
        "importable, so a machine without whisperx reports FAIL for an "
        "environment fault and the verdict stops meaning anything"
    )
    for module in ("parselmouth", "whisperx"):
        assert module in source, (
            f"{module} is not named in the gate's heavy_ml precondition, so "
            f"its absence is reported as a test failure rather than as an "
            f"unmeasured tier"
        )
    assert "not importable by" in source, (
        "the refusal does not name the interpreter that could not import "
        "the dependency, so the verdict says a tier did not run without "
        "saying what would make it run"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
