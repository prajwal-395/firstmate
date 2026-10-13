"""Nested collection proof that tests cannot bind a populated projects root.

This runs in the full suite and on targeted changes to pytest setup, fixtures,
imports, or the project-root safeguards. The cheap sandbox assertions live in
`test_tests_never_reach_real_projects.py` and remain routine.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_DIR.parent

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


@pytest.mark.nested
@pytest.mark.serial(reason="Recollects the test suite against a decoy project root.")
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
