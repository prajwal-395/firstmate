"""Session-wide hooks. Test fixtures and the projects-root sandbox live
in `tests/conftest.py`; this file exists for what has to see the WHOLE
session, including the test modules under `library/tools/fusion/tests/`
that a conftest inside `tests/` never reaches.

Right now that is one thing: every skip a run reports must name an
environment that RUNS the test. See `tests/skip_audit.py` for why, and
`tests/test_no_unfailable_tests.py` for the check on the check.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests import skip_audit  # noqa: E402

# Undeclared skips seen this session, as (nodeid, reason). There is no
# environment variable that turns this off: an escape hatch on a gate is
# how the gate stops being one.
_undeclared = []


def _reason(report) -> str:
    """The skip reason pytest recorded, however it was raised.

    A skip during setup or collection arrives as a `(path, lineno,
    reason)` triple; one raised in the body arrives as `wasxfail` or as
    the `skipped` keyword entry.
    """
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        text = str(longrepr[2])
        return text.removeprefix("Skipped: ")
    for _mark, name, value in getattr(report, "user_properties", []) or []:
        if name == "skip_reason":
            return str(value)
    return str(longrepr or "")


def pytest_runtest_logreport(report):
    if not report.skipped:
        return
    reason = _reason(report)
    if skip_audit.declared_condition(reason) is None:
        _undeclared.append((report.nodeid, reason))


def pytest_collectreport(report):
    if report.skipped:
        reason = _reason(report)
        if skip_audit.declared_condition(reason) is None:
            _undeclared.append((report.nodeid, reason))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _undeclared:
        return
    write = terminalreporter.write_line
    write("")
    write("=" * 30 + " undeclared skips " + "=" * 30, red=True, bold=True)
    seen = set()
    for nodeid, reason in _undeclared:
        if (nodeid, reason) in seen:
            continue
        seen.add((nodeid, reason))
        write(f"SKIPPED (undeclared) {nodeid}: {reason}", red=True)
    write("")
    for line in skip_audit.UNDECLARED_SKIP_ADVICE.splitlines():
        write(line, red=True)


def pytest_sessionfinish(session, exitstatus):
    if _undeclared:
        session.exitstatus = 1


import sys
import pytest



def pytest_collection_modifyitems(session, config, items):
    """Fail immediately if a test module globally mocked sys.modules during import."""
    import sys
    import inspect
    leaked = []
    for k, v in sys.modules.items():
        if v is not None and not inspect.ismodule(v) and "Mock" in type(v).__name__:
            leaked.append(f"{k} ({type(v).__name__})")
    
    if leaked:
        raise RuntimeError(f"Global module-level mock leak detected during collection: {', '.join(leaked)}. Never assign Mocks to sys.modules at the module level.")

import pytest
@pytest.fixture(autouse=True, scope="function")
def guard_sys_modules_against_test_leaks():
    """Fail if a test leaks a mock into sys.modules during execution."""
    yield
    import sys
    import inspect
    leaked = []
    for k, v in list(sys.modules.items()):
        if v is not None and not inspect.ismodule(v) and "Mock" in type(v).__name__:
            leaked.append(f"{k} ({type(v).__name__})")
            del sys.modules[k]
    
    if leaked:
        pytest.fail(f"Test leaked mock objects into sys.modules: {', '.join(leaked)}")
