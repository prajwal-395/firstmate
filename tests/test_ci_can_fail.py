"""CI must not report more than it checks.

Two holes, both verified against run 33667912850 on 2026-09-02, and they
are the same defect wearing two hats - the build said SUCCESS while
declining to look:

1. `ruff check ... || true`.  The trailing clause swallowed the exit
   code.  That run emitted 2,896 error annotations and passed.  Among
   them: 108 PLW1510 (`subprocess.run` with no `check=`), 51 of those
   inside the test suite itself, and 24 B023 (a closure over a loop
   variable) in library code.
2. No ffmpeg on the runner, and there never had been - `ffmpeg` appears
   nowhere in the workflow's git history.  27 library files shell out to
   it, so every audio and video measurement path skipped itself on every
   build.  The suite skips HONESTLY, which is what made it invisible.

This is AGENTS.md 10.4's rule about the build itself: a gate that cannot
fail is worse than no gate, because it reads as coverage.  The checks
below are structural - they read the workflow, not a run - so they cost
nothing and they fail the moment either hole is reopened.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE_CONFIG = REPO_ROOT / "ruff-ci-gate.toml"

# The step whose exit code is the verdict, and the one that is allowed to
# swallow it.  Matched on a substring so the names can be reworded.
GATE_STEP = "FAILS THE BUILD"
REPORT_STEP = "report only"


def _steps() -> list[dict]:
    """The workflow's steps, without depending on PyYAML being present.

    The file is small and flat; a `- name:` / `run: |` reader is enough,
    and it keeps this test running in an environment that has no yaml.
    """
    steps: list[dict] = []
    current: dict | None = None
    in_run = False
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- name:"):
            current = {"name": stripped[len("- name:"):].strip(), "run": []}
            steps.append(current)
            in_run = False
        elif stripped.startswith("run:") and current is not None:
            in_run = True
        elif in_run and current is not None:
            if stripped.startswith("- name:") or (stripped.startswith("- ") and ":" in stripped):
                in_run = False
            elif not stripped.startswith("#"):
                # A comment is not a command.  These very steps explain
                # themselves by NAMING `|| true`, so a reader that kept
                # comments would read the explanation as the defect.
                current["run"].append(stripped)
    for step in steps:
        step["run"] = "\n".join(step["run"])
    return steps


def _step_named(fragment: str) -> dict:
    matches = [s for s in _steps() if fragment in s["name"]]
    assert len(matches) == 1, (
        f"expected exactly one workflow step whose name contains "
        f"{fragment!r}, found {[s['name'] for s in matches]}"
    )
    return matches[0]


def test_the_style_gate_does_not_swallow_its_exit_code():
    """The half that decides the build may not end in `|| true`."""
    gate = _step_named(GATE_STEP)
    assert "|| true" not in gate["run"], (
        "the enforcing half of the style check ends in `|| true`, which is "
        "the exact clause that let run 33667912850 emit 2,896 errors and "
        "conclude SUCCESS. Put a cosmetic rule class in the report-only "
        "step instead of silencing the gate."
    )
    assert "ruff-ci-gate.toml" in gate["run"], (
        "the gate must run under ruff-ci-gate.toml, which is where what is "
        "enforced and what is deferred is written down"
    )


def test_the_reporting_half_still_reports_everything():
    """The cosmetic classes keep annotating, and keep not failing.

    Both halves matter.  A gate with no reporting half loses the 2,896
    findings entirely, which is the other way to stop looking.
    """
    report = _step_named(REPORT_STEP)
    assert "|| true" in report["run"], (
        "the report-only half must not fail the build - that is what makes "
        "the staged path possible"
    )
    assert "--config" not in report["run"], (
        "the reporting half must run under ruff's own config discovery, not "
        "the gate's: a finding deferred in ruff-ci-gate.toml must still be "
        "annotated on every PR rather than disappearing behind the gate"
    )


def test_ffmpeg_is_installed_before_the_suite_runs():
    """27 library files shell out to it; without it they skip themselves."""
    names = [s["name"] for s in _steps()]
    install = [i for i, n in enumerate(names) if "ffmpeg" in n.lower()]
    assert install, (
        "no workflow step installs ffmpeg. Every audio and video "
        "measurement path skips itself without it, and the build stays "
        "green while measuring nothing."
    )
    pytest_steps = [i for i, n in enumerate(names) if "pytest" in n.lower()]
    assert pytest_steps, "no step runs pytest"
    assert min(install) < min(pytest_steps), (
        "ffmpeg is installed after the suite runs, so the suite still sees "
        "a machine without it"
    )


def test_the_suite_prints_why_a_test_skipped():
    """`131 skipped` names nothing. `-rs` names each one and its reason.

    This is what made hole 2 invisible for the life of the workflow: the
    summary reported a number, and nothing said what went unmeasured.
    """
    run = _step_named("Run Pytest")["run"]
    assert " -rs" in run or run.rstrip().endswith("-rs"), (
        "pytest is not asked for skip reasons, so the build reports a skip "
        "COUNT and never says what it declined to measure"
    )


def test_no_deferral_names_a_file_that_is_gone():
    """A grandfathered file that no longer exists is a lie about the debt.

    `per-file-ignores` is silent about a pattern matching nothing, so a
    renamed file leaves an entry that reads as outstanding work and
    silences nothing.
    """
    config = tomllib.loads(GATE_CONFIG.read_text(encoding="utf-8"))
    deferred = config["lint"]["per-file-ignores"]
    assert deferred, "the deferral list is empty - say so in the file rather than leaving it bare"
    missing = sorted(p for p in deferred if not (REPO_ROOT / p).exists())
    assert not missing, (
        f"ruff-ci-gate.toml defers rules for files that no longer exist: "
        f"{missing}. Delete the lines - the list is the record of what this "
        f"repository still owes."
    )


def test_every_deferred_rule_is_one_the_gate_actually_enforces():
    """Deferring a rule the gate does not select defers nothing.

    It reads as a recorded exception and is a no-op, which is the same
    shape as the swallowed exit code one level down.
    """
    config = tomllib.loads(GATE_CONFIG.read_text(encoding="utf-8"))
    selected = set(config["lint"]["select"])
    unenforced = sorted({
        code
        for codes in config["lint"]["per-file-ignores"].values()
        for code in codes
        if code not in selected and not any(
            code.startswith(prefix) for prefix in selected
        )
    })
    assert not unenforced, (
        f"ruff-ci-gate.toml defers {unenforced}, which its `select` does not "
        f"enforce anywhere. The exception is decorative."
    )


@pytest.mark.parametrize("code", ["PLW1510", "B023"])
def test_the_two_classes_the_incident_named_are_enforced(code: str):
    """PLW1510 and B023 are why this gate exists; neither may quietly go.

    A later commit may promote more classes.  Demoting one of these two
    is a decision, and it should have to delete this test to make it.
    """
    config = tomllib.loads(GATE_CONFIG.read_text(encoding="utf-8"))
    selected = config["lint"]["select"]
    assert code in selected or any(code.startswith(p) for p in selected), (
        f"{code} is no longer enforced. It is one of the two classes run "
        f"33667912850 proved were hiding behind `|| true`: 108 PLW1510 "
        f"(51 of them in the test suite) and 24 B023 in library code."
    )
