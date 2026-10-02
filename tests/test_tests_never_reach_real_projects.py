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

Two runtime proofs remain here:

1. Tests see the empty sandbox installed by `tests/conftest.py`.
2. Collecting the suite against a populated DECOY root binds nothing
   beneath it and changes no bytes.

The source policies (no test reads `PROJECTS_ROOT`, no machine-specific
home path) run in `library.tools.static_check` with the other repository
source checks. The end-to-end sandbox test is its documented exception
for the root-read rule because it must inspect the value it is testing.


Rules relocated from AGENTS.md 8
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 8
keeps the headline and points here.

**A test builds its project under `tmp_path`, or it skips. It never falls back to a real one.**
- `library.tools.paths.PROJECTS_ROOT` is the ONE constant naming where real projects live. **A test may not read that constant.**
- `tests/conftest.py` points `PIPELINE_PROJECTS_ROOT` at an empty temporary directory for the whole session.
- `tests/test_tests_never_reach_real_projects.py` asserts the guarantee: the root a test sees is the sandbox, no test source carries a real path, and collecting the suite against a populated DECOY root binds nothing.
"""

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


# ── 2. Behaviour: collect against a decoy and check it is untouched ──

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


@pytest.mark.heavy
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
