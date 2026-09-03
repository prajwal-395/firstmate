"""The bootstrap of each Workspace > Scripts entry point, run the way
Resolve runs it.

**Resolve's own script host does not define `__file__`.**  Every earlier
test executed these files as FILES - `importlib` and `exec_module` both
set `__file__` - so the one path the captain actually uses was the one
path nothing exercised.  What shipped was a `_repo_root()` that built its
candidate tuple EAGERLY, so the `__file__` fallback raised `NameError`
before the stamped `REPO_ROOT` beside it was ever looked at, and both
menu entries died silently into Resolve's log.

So this file executes the bootstrap with **no `__file__` in the
namespace**, in a subprocess, for every entry point in
`resolve_scripts/`.  It is one test over BOTH files on purpose: the
helper is duplicated across them - it must be, because its whole job is
to find the repository the shared code would have to be imported from -
so the guarantee is held by one test rather than one function.

`__name__` is NOT set to `"__main__"` here.  The captain's log proves
Resolve sets it (the capture button's traceback runs through its
`if __name__ == "__main__":` line), but under that name these scripts
connect to Resolve, and one of them leaves through `os._exit`.  The
defect is in the module body and in `_repo_root`, and both are reachable
without that.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

**`__file__` IS NOT DEFINED there, and an entry point verified by running it as a FILE has not been verified.**
Both entry points in `resolve_scripts/` are launched by Resolve's own script host, which defines `__name__` as `"__main__"` but does NOT define `__file__` - and `importlib`, `exec_module` and `python3 the_file.py` all define it, so every route a test or a screenshot takes hides this. [why - the two menu entries that did nothing at all](docs/RULE_EVIDENCE.md#the-menu-entries-that-did-nothing)
Resolve's script host defines `__name__` as `"__main__"` but NOT `__file__`. [why](docs/RULE_EVIDENCE.md#the-menu-entries-that-did-nothing)
- **Read `__file__` in ONE place per entry point, inside a helper that answers `""` when it is absent.** Nowhere else, and `tests/test_resolve_scripts_bootstrap.py` fails on a second reader.
- **A fallback chain is evaluated LAZILY, or the last candidate can kill the first.** `_repo_root()` built its candidates as a tuple, so the `__file__` fallback raised before the stamped `REPO_ROOT` beside it - correct, and pointing at a directory that existed - was ever tested.
- **A bootstrap failure must reach the SCREEN.** `print` is the floor (reaches Resolve's Console), and a window built from `fusion`/`bmd` MAY NOT RAISE. Failures are held in `BOOTSTRAP_ERROR`.
- **Verify from the MENU.** `tests/test_resolve_scripts_bootstrap.py` executes each entry point's bootstrap the way the host does - `exec(compile(...))` into a namespace with no `__file__` - and that is the substitute for a click, not a replacement for one.
- **The two copies of `_repo_root` stay two.** Its whole job is to find the repository a shared copy would have to be imported from. The duplication is held by ONE test over BOTH files rather than by one function.
- `sys.argv` and the working directory are not relied on by either file, and the same test keeps it that way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO / "resolve_scripts"

# Executed in a subprocess.  The namespace deliberately carries no
# `__file__`, which is the whole point: `compile()` plus `exec()` with a
# bare namespace is what Resolve's host does, and it is the only way to
# reach this defect from a test.
HOST = r'''
import json, sys
path, root_override = sys.argv[1], sys.argv[2]
source = open(path, encoding="utf-8").read()
if root_override != "-":
    needle = 'REPO_ROOT = ""'
    assert needle in source, path
    source = source.replace(needle, "REPO_ROOT = " + repr(root_override), 1)
namespace = {"__name__": "vep_entry_point"}
exec(compile(source, path, "exec"), namespace)
assert "__file__" not in namespace, "the module body defined __file__ itself"
print("@@" + json.dumps({
    "repo_root": namespace["_repo_root"](),
    "module_root": namespace.get("ROOT"),
}))
'''


def _entry_points():
    return sorted(SOURCE_DIR.glob("*.py"))


def _run_as_resolve_does(path, repo_root="-", env=None):
    """Execute one entry point's module body with no `__file__`."""
    result = subprocess.run(
        [sys.executable, "-c", HOST, str(path), repo_root],
        capture_output=True, encoding="utf-8", check=False,
        env=dict(os.environ, **(env or {})),
        cwd=str(REPO),
    )
    return result


def _payload(result):
    for line in result.stdout.splitlines():
        if line.startswith("@@"):
            return json.loads(line[2:])
    raise AssertionError(f"no payload\nstdout:\n{result.stdout}\n"
                         f"stderr:\n{result.stderr}")


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_the_bootstrap_runs_with_no_dunder_file(path):
    """Clicking the menu entry must not raise before anything is decided.

    The module body AND `_repo_root()`, because the defect landed in a
    different place in each file: the panel resolves the root at import
    (`ROOT = _repo_root()`), the capture button from inside `main()`.
    One harness reaches both.
    """
    result = _run_as_resolve_does(path)
    assert result.returncode == 0, (
        f"{path.name} died with no __file__ in the namespace - which is "
        f"exactly how Resolve runs it:\n{result.stderr}"
    )
    assert "NameError" not in result.stderr, result.stderr


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_the_stamped_root_wins_before_the_file_fallback_is_evaluated(path):
    """Laziness, proved by a later candidate that CANNOT be evaluated.

    With no `__file__`, the third candidate raises if it is computed at
    all.  A stamped `REPO_ROOT` that resolves must therefore be returned
    without it ever being reached.
    """
    result = _run_as_resolve_does(path, repo_root=str(REPO))
    assert result.returncode == 0, result.stderr
    assert _payload(result)["repo_root"] == str(REPO)


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_a_stamped_path_that_has_gone_away_is_answered_not_raised(path):
    """The installed copies pointed at a disposable worktree.

    When that path goes, the bootstrap must come back with "I could not
    find it" - which each script turns into a stated error - rather than
    dying into Resolve's log.
    """
    result = _run_as_resolve_does(path, repo_root="/nonexistent/worktree")
    assert result.returncode == 0, result.stderr
    assert _payload(result)["repo_root"] == ""


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_the_environment_override_answers_with_no_dunder_file(path):
    """VEP_REPO_ROOT is the second candidate and must survive too."""
    result = _run_as_resolve_does(path, repo_root="/nonexistent/worktree",
                                  env={"VEP_REPO_ROOT": str(REPO)})
    assert result.returncode == 0, result.stderr
    assert _payload(result)["repo_root"] == str(REPO)


# ── The class, not just the instance ────────────────────────────────


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_dunder_file_is_read_only_where_its_absence_is_handled(path):
    """`__file__` may be named ONCE per entry point, inside the helper
    that catches its absence.

    The bug was one name, used in a place that could not survive it not
    being there.  Reading it anywhere else re-opens exactly this defect,
    and the failure is silent, so grep is the cheap guard.
    """
    source = path.read_text(encoding="utf-8")
    code = [line for line in source.splitlines()
            if "__file__" in line and not line.lstrip().startswith("#")
            and "**Resolve's own script host" not in line]
    assert code == ["        here = __file__"], (
        f"{path.name} reads __file__ outside _beside_this_file():\n  "
        + "\n  ".join(code) + "\n"
        "Resolve's script host does not define it. Route the question "
        "through _beside_this_file(), which answers \"\" rather than raising."
    )


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_nothing_else_assumes_an_ordinary_interpreter(path):
    """The other names an interpreter provides and a script host may not.

    Audited while there was a way to execute in that context.
    `__name__` IS provided - the captain's log carries a traceback
    running through the capture button's `if __name__ == "__main__":`
    line, which is only reachable when it equals `"__main__"`.
    `sys.argv` and the working directory are not relied on by either
    file, and this keeps it that way; the one subprocess that needs a
    directory is given one explicitly by `frame_attach.call_site`.
    """
    source = path.read_text(encoding="utf-8")
    offenders = [line for line in source.splitlines()
                 if ("sys.argv" in line or "os.getcwd" in line)
                 and not line.lstrip().startswith("#")]
    assert not offenders, (
        f"{path.name} assumes a plain interpreter run:\n  "
        + "\n  ".join(offenders)
    )
