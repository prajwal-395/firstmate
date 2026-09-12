"""The check on the check: no test here may be unable to fail.

`tests/skip_audit.py` states the case.  This module runs it - the source
half against this repository, the runtime half against a suite built for
the purpose in a subprocess - and tests the analyser itself, because an
audit that cannot fail is the thing it was written to stop.

Run the source half on its own:

    python3 -m pytest tests/test_no_unfailable_tests.py -q


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**A SKIPPED test must name an environment that runs it, and a test body must be able to fail.**
`tests/skip_audit.py` is the enumeration and `tests/test_no_unfailable_tests.py` runs it; the runtime half is the session hook in the repo-root `conftest.py`, which FAILS a run reporting a skip no `EnvironmentCondition` declares.
- `ENVIRONMENT_CONDITIONS` is measuring instruments and external applications only. **A condition that reads THIS REPOSITORY'S contents is not an environment.** [why](docs/RULE_EVIDENCE.md#five-tests-skipped-in-every-environment)
- The source half also fails a test whose body is `pass`, or whose whole body is a `try` swallowing every exception.
- Run it alone with `python3 -m pytest tests/test_no_unfailable_tests.py -q`.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def test_every_declared_condition_says_what_makes_it_false():
    for condition in ENVIRONMENT_CONDITIONS:
        assert condition.false_when.strip(), (
            f"{condition.pattern!r} declares no environment that runs the "
            f"test, which is the whole content of a legitimate skip")


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


def test_skipif_true_is_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        import pytest

        @pytest.mark.skipif(True, reason="later")
        def test_thing():
            assert False
    """)
    assert _kinds(findings) == ["unconditional-skip"]


def test_a_skip_on_the_straight_line_of_a_body_is_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        import pytest

        def test_thing():
            pytest.skip("not yet")
            assert False
    """)
    assert _kinds(findings) == ["unconditional-skip"]


def test_a_skipif_naming_a_real_condition_is_not_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        import shutil
        import pytest

        @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
        def test_thing():
            assert True
    """)
    assert findings == []


def test_a_skip_inside_a_branch_is_not_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        import shutil
        import pytest

        def test_thing():
            if shutil.which("ffmpeg") is None:
                pytest.skip("no ffmpeg")
            assert True
    """)
    assert findings == []


def test_a_guard_on_a_third_party_import_is_not_a_finding(tmp_path):
    """A missing package IS a fact about the machine - that is the job."""
    findings = _audit(tmp_path, """
        import pytest

        def test_thing():
            try:
                from parselmouth import Sound
            except ImportError:
                pytest.skip("parselmouth not installed")
            assert Sound
    """)
    assert findings == []


def test_a_guard_on_a_first_party_symbol_that_exists_is_not_a_finding():
    """Judged against the real tree, so the resolver is exercised."""
    findings = audit_source(
        Path(__file__).with_name("test_no_unfailable_tests.py"))
    assert findings == []


def _first_party_tree(tmp_path: Path) -> Path:
    library = tmp_path / "library" / "steps" / "step_9_99_example"
    library.mkdir(parents=True)
    for part in (tmp_path / "library", tmp_path / "library" / "steps", library):
        (part / "__init__.py").write_text("", encoding="utf-8")
    (library / "step.py").write_text(
        "def main():\n    return 1\n", encoding="utf-8")
    return tmp_path


def test_a_guard_on_a_first_party_symbol_that_does_not_exist_is_a_finding(
        tmp_path):
    """The exact shape of all five tests in issue #249."""
    _first_party_tree(tmp_path)
    findings = _audit(tmp_path, """
        import pytest

        def test_thing():
            try:
                from library.steps.step_9_99_example.step import analyze
            except ImportError:
                pytest.skip("Step 9.99 not available")
            assert analyze
    """)
    assert _kinds(findings) == ["unresolvable-guard"]
    assert "defines no 'analyze'" in findings[0].detail


def test_a_guard_on_a_first_party_module_that_does_not_exist_is_a_finding(
        tmp_path):
    _first_party_tree(tmp_path)
    findings = _audit(tmp_path, """
        import pytest

        def test_thing():
            try:
                from library.steps.step_9_99_example.bridge import pre_bridge
            except ImportError:
                pytest.skip("pre_bridge not available")
            assert pre_bridge
    """)
    assert _kinds(findings) == ["unresolvable-guard"]


def test_a_guard_on_a_first_party_symbol_that_does_exist_is_not_a_finding(
        tmp_path):
    _first_party_tree(tmp_path)
    findings = _audit(tmp_path, """
        import pytest

        def test_thing():
            try:
                from library.steps.step_9_99_example.step import main
            except ImportError:
                pytest.skip("Step 9.99 not available")
            assert main() == 1
    """)
    assert findings == []


def test_a_module_that_cannot_enumerate_its_names_is_not_accused(tmp_path):
    """A star-import means the name set is not the whole story."""
    _first_party_tree(tmp_path)
    (tmp_path / "library" / "steps" / "step_9_99_example" / "step.py"
     ).write_text("from os.path import *\n", encoding="utf-8")
    findings = _audit(tmp_path, """
        import pytest

        def test_thing():
            try:
                from library.steps.step_9_99_example.step import join
            except ImportError:
                pytest.skip("Step 9.99 not available")
            assert join
    """)
    assert findings == []


def test_an_empty_body_is_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        def test_thing():
            pass
    """)
    assert _kinds(findings) == ["cannot-fail"]


def test_a_guarded_import_followed_by_pass_is_a_finding(tmp_path):
    """`test_temporal_index`: the import RESOLVED, so it ran and asserted
    nothing - a green dot for step 1.04 on every run since it was
    written."""
    _first_party_tree(tmp_path)
    findings = _audit(tmp_path, """
        import pytest

        def test_thing():
            try:
                from library.steps.step_9_99_example.step import main
            except ImportError:
                pytest.skip("Step 9.99 not available")
            pass
    """)
    assert _kinds(findings) == ["cannot-fail"]


def test_a_body_that_swallows_every_exception_is_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        def test_thing():
            try:
                from nowhere import nothing
                assert nothing({}) == 3
            except Exception:
                pass
    """)
    assert _kinds(findings) == ["cannot-fail"]


def test_a_try_that_asserts_in_its_handler_is_not_a_finding(tmp_path):
    findings = _audit(tmp_path, """
        def test_thing():
            try:
                raise ValueError("x")
            except ValueError as e:
                assert "x" in str(e)
    """)
    assert findings == []


def test_the_audit_reads_test_modules_outside_the_tests_directory():
    """Nine always-skipping tests were hiding in library/tools/fusion."""
    paths = {p.relative_to(REPO_ROOT) for p in skip_audit.test_sources()}
    assert Path("library/tools/fusion/tests/test_parser.py") in paths
    assert Path("tests/test_no_unfailable_tests.py") in paths


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
