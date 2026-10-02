"""The check on the check: no test here may be unable to fail.

`tests/skip_audit.py` states the case.  This module runs it - the source
half against this repository, the runtime half against a suite built for
the purpose in a subprocess - and tests the analyser itself, because an
audit that cannot fail is the thing it was written to stop.

Run the source half on its own:

    python3 -m pytest tests/tooling/test_no_unfailable_tests.py -q


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**A SKIPPED test must name an environment that runs it, and a test body must be able to fail.**
`tests/skip_audit.py` is the enumeration and `tests/tooling/test_no_unfailable_tests.py` runs it; the runtime half is the session hook in the repo-root `conftest.py`, which FAILS a run reporting a skip no `EnvironmentCondition` declares.
- `ENVIRONMENT_CONDITIONS` is measuring instruments and external applications only. **A condition that reads THIS REPOSITORY'S contents is not an environment.** [why](docs/RULE_EVIDENCE.md#five-tests-skipped-in-every-environment)
- The source half also fails a test whose body is `pass`, or whose whole body is a `try` swallowing every exception.
- Run it alone with `python3 -m pytest tests/tooling/test_no_unfailable_tests.py -q`.
"""
import pytest

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import skip_audit  # noqa: E402
from tests.skip_audit import (  # noqa: E402
    ENVIRONMENT_CONDITIONS,
    audit_source,
    audit_sources,
    declared_condition,
)


# ── The check, run against this repository ────────────────────────────


def test_no_test_in_this_repo_is_unfailable():
    """Every finding here is a test reporting coverage it does not have.

    Against `origin/main` at the time this landed, this reported eleven:
    the five named in issue #249, the four in `tests/test_step_runners.py`
    whose whole body was `except Exception: pass`, and two whose body was
    `pass` outright.
    """
    findings = audit_sources()
    assert not findings, (
        "these tests cannot fail:\n  "
        + "\n  ".join(str(f) for f in findings)
        + "\n\n" + skip_audit.UNDECLARED_SKIP_ADVICE)


def test_a_declaration_matches_the_reason_it_was_written_for():
    """The declarations are useless if they match nothing."""
    assert declared_condition(
        "this is the missing-dependency failure mode") is not None
    assert declared_condition("ffmpeg/ffprobe not available") is not None
    assert declared_condition("ffmpeg/ffprobe not on PATH") is not None
    assert declared_condition(
        "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"
    ) is not None
    assert declared_condition(
        "ffmpeg/ffprobe are required; CI installs them (AGENTS.md 9)"
    ) is not None
    assert declared_condition(
        "needs remotion-subtitles/node_modules and npx") is not None
    assert declared_condition(
        "needs remotion-subtitles/node_modules and npx; runs on any "
        "machine that has done `npm install` in remotion-subtitles, "
        "which is every machine that can render the pipeline's overlays"
    ) is not None
    assert declared_condition(
        "needs node and remotion-subtitles/node_modules/typescript. "
        "Runs anywhere the Remotion dev deps are installed - the same "
        "environment every other delivery test in this suite needs."
    ) is not None
    assert declared_condition(
        "needs Pillow to measure the stills") is not None
    assert declared_condition(
        "needs Pillow to draw the fixture") is not None
    assert declared_condition(
        "PyYAML parses the workflow; it is in requirements.txt"
    ) is not None
    assert declared_condition(
        "could not import 'cv2': No module named 'cv2'") is not None
    assert declared_condition("hook_1.comp not found") is None
    assert declared_condition("Step 1.05 not available") is None


# ── The analyser itself ───────────────────────────────────────────────


def _audit(tmp_path: Path, source: str, name: str = "test_sample.py"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return audit_source(path, tmp_path)


def _kinds(findings):
    return sorted(f.kind for f in findings)


def test_an_unconditional_skip_marker_is_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        import pytest

        @pytest.mark.skip(reason="later")
        def test_thing():
            assert False
    """)
    assert _kinds(findings) == ["unconditional-skip"]


def _first_party_tree(tmp_path: Path) -> Path:
    library = tmp_path / "library" / "steps" / "step_9_99_example"
    library.mkdir(parents=True)
    for part in (tmp_path / "library", tmp_path / "library" / "steps", library):
        (part / "__init__.py").write_text("", encoding="utf-8")
    (library / "step.py").write_text(
        "def main():\n    return 1\n", encoding="utf-8")
    return tmp_path


def test_an_empty_body_is_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        def test_thing():
            pass
    """)
    assert _kinds(findings) == ["cannot-fail"]


# ── The runtime half really fails a session ───────────────────────────

_SUITE = '''
import pytest

@pytest.mark.skipif({condition}, reason={reason!r})
def test_conditional():
    assert True
'''


def _run_suite(tmp_path: Path, condition: str, reason: str, extra_env=None):
    """A one-test suite, run with the repo's root conftest as a plugin.

    `-p conftest` is how a temporary suite gets the session-wide hook
    without a test ever writing a file into the repository (AGENTS.md 8).
    """
    suite = tmp_path / "test_generated.py"
    suite.write_text(
        _SUITE.format(condition=condition, reason=reason), encoding="utf-8")
    env = dict(os.environ, **(extra_env or {}))
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "conftest",
         str(suite)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(tmp_path), env=env,
    )


def test_a_declared_skip_passes_the_session(tmp_path):
    proc = _run_suite(tmp_path, "True", "ffmpeg/ffprobe not available")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout


def test_an_undeclared_skip_fails_the_session(tmp_path):
    """This is the whole point: a skip nobody has justified stops the run."""
    proc = _run_suite(tmp_path, "True", "step 9.99 not available")
    assert proc.returncode != 0, (
        "an undeclared skip passed the session:\n" + proc.stdout + proc.stderr)
    assert "undeclared skips" in proc.stdout
    assert "step 9.99 not available" in proc.stdout
    assert "must name an environment that RUNS the test" in proc.stdout


def test_a_test_that_runs_is_not_reported_as_an_undeclared_skip(tmp_path):
    proc = _run_suite(tmp_path, "False", "step 9.99 not available")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "undeclared skips" not in proc.stdout
