"""No test may resolve a path under the captain's real projects root.

`tests/test_pipeline.py` used to do this at IMPORT time, so it happened on
every pytest COLLECTION rather than only when its own test ran:

    from library.tools.paths import PROJECTS_ROOT
    for entry in PROJECTS_ROOT.iterdir():
        if entry.is_dir() and (entry / "project.yaml").exists():
            PROJECT_DIR = str(entry)
            break
    OUTPUT_DIR = os.path.join(PROJECT_DIR, "pipeline_output")

`PIPELINE_TEST_PROJECT` overrode it, but the FALLBACK was the real thing
and nothing sets that variable in CI or locally.  The module then wrote
`_v2` artifacts into whatever project it had bound (line 71) and
`shutil.move`d a backup over an original (line 284).  The captain's
footage and renders cannot be re-shot and this machine has no Time
Machine destination, so there is no undo.

Three checks, because they fail for different reasons and a green from
one is not a green from another:

1. RUNTIME - the projects root a test can see is the empty sandbox
   `tests/conftest.py` installs, not the configured one.  This closes
   routes nobody has enumerated, including ones added tomorrow.
2. SOURCE - no test module reads the `PROJECTS_ROOT` constant, and none
   hardcodes an absolute path into a user's home directory.  This is what
   fails if the fallback above is pasted back in.
3. BEHAVIOUR - collect the whole suite in a subprocess against a DECOY
   projects root that looks exactly like a populated real one, with the
   sandbox deliberately disabled and `PIPELINE_TEST_PROJECT` unset.
   Nothing may bind a path under it, and not one byte of it may change.

Check 3 is the honest one: it reproduces the captain's machine with a
stand-in and asks the suite to misbehave.  It is also the slow one, which
is why 1 and 2 exist as cheap tripwires beside it.
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from library.tools import paths

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

# Two files may touch the constant, and only these two.
#
#   this module - reading it is how check 1 proves the sandbox is there.
#   tests/conftest.py - it INSTALLS the sandbox, so it has to read the
#     configured value in order to replace it.
#
# Exempting the installer does leave one door: a fallback pasted into
# conftest.py itself would not be flagged here.  Check 3 covers that
# one, because it disables the sandbox and watches a decoy.
SELF = Path(__file__).resolve()
SANDBOX_INSTALLER = (TESTS_DIR / "conftest.py").resolve()
EXEMPT = {SELF, SANDBOX_INSTALLER}

# Any user home outside the repo.  `~` expansion and the PIPELINE_* env
# vars are how a real path is supposed to reach a test.
HOME_ABSOLUTE_PREFIXES = ("/Users/", "/home/")


def _test_sources():
    """Every Python file under tests/, minus the two EXEMPT ones."""
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.resolve() in EXEMPT:
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


# ── 1. Runtime: what the suite actually sees ────────────────────────

def test_the_projects_root_a_test_sees_is_an_empty_sandbox():
    root = Path(paths.PROJECTS_ROOT)
    assert root.is_dir(), f"the sandbox should exist: {root}"
    assert root.name.startswith("pipeline-projects-sandbox-"), (
        f"tests are seeing {root}, which is not the sandbox "
        f"tests/conftest.py installs. Something imported "
        f"library.tools.paths before conftest ran, or the sandbox was "
        f"removed."
    )
    found = list(root.rglob("project.yaml"))
    assert not found, (
        f"the sandbox must stay EMPTY - a test that needs a project "
        f"builds one under tmp_path. Found: {found}"
    )


def test_the_configured_projects_root_is_not_what_tests_see():
    configured = os.environ.get(
        "PIPELINE_TESTS_CONFIGURED_PROJECTS_ROOT", "")
    if not configured:
        pytest.skip("PIPELINE_PROJECTS_ROOT was unset before pytest started")
    assert Path(configured) != Path(paths.PROJECTS_ROOT), (
        f"tests are pointed at the configured projects root {configured}. "
        f"That is where the captain's real footage lives."
    )


# ── 2. Source: the shape must not come back ─────────────────────────

def _docstring_node_ids(tree):
    """ids of the Constant nodes that are module/class/function docstrings.

    A hazard recorded in prose is documentation.  test_runner_no_fixture
    _shortcuts.py quotes a real absolute path in its module docstring for
    exactly that reason, and must not be flagged for it.
    """
    ids = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            ids.add(id(first.value))
    return ids


def test_no_test_module_reads_the_projects_root_constant():
    offenders = []
    for path in _test_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            # `paths.PROJECTS_ROOT` / `pipeline_paths.PROJECTS_ROOT`
            if isinstance(node, ast.Attribute) and node.attr == "PROJECTS_ROOT":
                offenders.append(f"{path.name}:{node.lineno}: .PROJECTS_ROOT")
            # `from library.tools.paths import PROJECTS_ROOT`
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "PROJECTS_ROOT":
                        offenders.append(
                            f"{path.name}:{node.lineno}: "
                            f"from {node.module} import PROJECTS_ROOT")
    assert not offenders, (
        "A test read the constant that names the captain's real projects "
        "root:\n  " + "\n  ".join(offenders) + "\n"
        "A test that needs a project builds one under tmp_path. "
        "Patching it by string - @patch('library.tools.paths.PROJECTS_ROOT') "
        "- is fine and is not flagged."
    )


def test_no_test_module_hardcodes_a_path_under_a_users_home():
    offenders = []
    for path in _test_sources():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, str(path))
        skip = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if not isinstance(node.value, str) or id(node) in skip:
                continue
            if node.value.startswith(HOME_ABSOLUTE_PREFIXES):
                offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
    assert not offenders, (
        "A test carries an absolute path into a home directory:\n  "
        + "\n  ".join(offenders) + "\n"
        "A path only one machine has is not a fixture. Build it under "
        "tmp_path, or reach it through a PIPELINE_* env var."
    )


# ── 3. Behaviour: collect against a decoy and check it is untouched ──

_DECOY_PLUGIN = '''
"""Reports every collected module global that points into the decoy."""
import json
import os
import sys
from pathlib import Path

DECOY = str(Path(os.environ["PIPELINE_DECOY_ROOT"]).resolve())
REPORT = os.environ["PIPELINE_DECOY_REPORT"]


def pytest_collection_finish(session):
    offenders = []
    for name, module in list(sys.modules.items()):
        if "test_" not in name or module is None:
            continue
        for key, value in list(vars(module).items()):
            if not isinstance(value, (str, Path)):
                continue
            text = str(value)
            if not text.startswith("/"):
                continue
            if os.path.realpath(text).startswith(DECOY) or text.startswith(DECOY):
                offenders.append(f"{name}.{key} = {text}")
    Path(REPORT).write_text(json.dumps(sorted(offenders)), encoding="utf-8")
'''


def _tree_digest(root: Path) -> dict:
    """Every file under `root`, by relative path, with a content digest."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return out


def _build_decoy(root: Path) -> Path:
    """A projects root that looks exactly like the captain's populated one.

    The names are the ones the old fallback looked for - a directory with
    a project.yaml, holding a pipeline_output/ with the flat step exports
    the pre-#167 layout wrote - so a restored fallback binds to it.
    """
    project = root / "captains-project"
    output = project / "pipeline_output"
    output.mkdir(parents=True)
    (project / "project.yaml").write_text(
        "name: Decoy\nslug: captains-project\n", encoding="utf-8")
    (project / "pipeline_data.json").write_text(
        '{"catalog": {"clip_catalog": []}}', encoding="utf-8")
    for step in ("2_01", "2_02", "2_04", "2_05", "3_02",
                 "4_01", "4_02", "4_03", "4_04"):
        (output / f"step_{step}.json").write_text("{}", encoding="utf-8")
    return project


def test_collecting_the_suite_binds_nothing_under_a_populated_projects_root(
        tmp_path):
    decoy_root = tmp_path / "video_projects"
    decoy_root.mkdir()
    _build_decoy(decoy_root)
    before = _tree_digest(decoy_root)

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "decoy_watch.py").write_text(_DECOY_PLUGIN, encoding="utf-8")
    report = tmp_path / "offenders.json"

    env = dict(os.environ)
    env["PIPELINE_PROJECTS_ROOT"] = str(decoy_root)
    # Disable the sandbox on purpose: this check exists to prove the
    # suite is safe WITHOUT it, so that the sandbox is a second line of
    # defence rather than the only one.
    env["PIPELINE_TESTS_SKIP_PROJECTS_ROOT_SANDBOX"] = "1"
    env.pop("PIPELINE_TEST_PROJECT", None)
    env.pop("PIPELINE_TESTS_CONFIGURED_PROJECTS_ROOT", None)
    env["PIPELINE_DECOY_ROOT"] = str(decoy_root)
    env["PIPELINE_DECOY_REPORT"] = str(report)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(plugin_dir), str(REPO_ROOT), env.get("PYTHONPATH", "")])

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q",
         "-p", "decoy_watch", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, check=False,
        encoding="utf-8", timeout=600,
    )
    assert result.returncode == 0, (
        "collecting the suite against a decoy projects root failed:\n"
        f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}")

    assert report.exists(), (
        "the decoy_watch plugin never ran, so this check proved nothing:\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    offenders = json.loads(report.read_text(encoding="utf-8"))
    assert not offenders, (
        "Collecting the suite bound module state to a path inside a "
        "populated projects root:\n  " + "\n  ".join(offenders) + "\n"
        "On the captain's machine that path is real footage.")

    after = _tree_digest(decoy_root)
    assert after == before, (
        "Collecting the suite changed a populated projects root.\n"
        f"  appeared:  {sorted(set(after) - set(before))}\n"
        f"  vanished:  {sorted(set(before) - set(after))}\n"
        f"  rewritten: {sorted(k for k in set(before) & set(after) if before[k] != after[k])}"
    )
