"""A default-selection test cannot reach the live Resolve, and the guard FAILS one that tries.

On 2026-10-01 a full-suite gate hung 17 minutes: Resolve's scripting
connection was wedged and an ordinary test worker blocked inside it,
because `tests/conftest.py` imported DaVinciResolveScript into every
worker and eight reel-build tests left `reel_build._connect_resolve_project`
unpatched. `tests/conftest.py` now makes the module unimportable outside
the `resolve_session` fixture and fails any test that asks for it.

A guard that cannot fail reads as coverage (AGENTS.md 10.4), so this runs
a two-test module under the suite's own conftest in a subprocess: one test
imports the bindings and SWALLOWS the ImportError, as every production
fallback does - the guard must still fail it, by name - and one takes the
`resolve_bindings_stand_in` fixture and must pass.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROBE = textwrap.dedent('''
    def test_tries_the_live_bindings():
        try:
            import DaVinciResolveScript  # noqa: F401
        except ImportError:
            pass  # what every production fallback does with it


    def test_takes_the_stand_in(resolve_bindings_stand_in):
        import DaVinciResolveScript
        assert DaVinciResolveScript is resolve_bindings_stand_in
''')


def test_the_guard_fails_a_test_that_reaches_for_resolve(tmp_path):
    probe = tmp_path / "test_probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest",
         "-p", "no:cacheprovider", "--rootdir", str(tmp_path),
         "-c", str(tmp_path / "pytest.ini"), "-q", "-rE", str(probe)],
        cwd=REPO_ROOT, capture_output=True, encoding="utf-8",
        timeout=120, check=False)
    output = result.stdout + result.stderr

    # The guard fails at TEARDOWN, so pytest counts both calls passed and
    # the one that reached for Resolve as the error.
    assert result.returncode == 1, output
    assert "2 passed, 1 error" in output, output
    assert "ERROR" in output and "test_takes_the_stand_in" not in [
        line.rsplit("::", 1)[-1] for line in output.splitlines()
        if line.startswith(("ERROR", "FAILED"))], output
    assert ("test_probe.py::test_tries_the_live_bindings tried to import "
            "DaVinciResolveScript") in output, output
