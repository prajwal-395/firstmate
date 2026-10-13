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

The routine runtime proofs remain here:

1. Tests see the empty sandbox installed by `tests/conftest.py`.
The nested collection proof lives in
`test_nested_project_collection.py`; it binds nothing beneath a populated
DECOY root and changes no bytes.

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
- `tests/tooling/test_tests_never_reach_real_projects.py` asserts the guarantee: the root a test sees is the sandbox, no test source carries a real path, and collecting the suite against a populated DECOY root binds nothing.
"""

import os
from pathlib import Path

import pytest

from library.tools import paths

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
