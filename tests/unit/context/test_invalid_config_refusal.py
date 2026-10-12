"""LE-02: a bad project config is a refusal, and read verbs still answer.

The defects these pin:

- One unread key in project.yaml (e.g. `deliver_preset.render_preset`)
  made every project-resolving verb exit 1 with a traceback, and `ren
  projects` silently dropped the project.
- `load_project_config` now takes `strict`: strict raises
  `ProjectConfigInvalid` (a `RenRefusal`, exit 4); lenient returns the
  config with the failures on `config.config_findings`.
- Read verbs load leniently and print one `⚠ config:` line; write
  verbs stay strict; `scan_projects` lists the project instead of
  skipping it, warning only for `ren projects`.

Every project here is built under `tmp_path`; no captain project,
Resolve timeline or broker is touched.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from library.schemas.project_config import (
    ProjectConfigInvalid,
    load_project_config,
)
from library.tools.project_registry import (
    get_project,
    list_projects,
    project_status,
    scan_projects,
    warn_config_findings,
)
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

PILOT_ROOT = Path(__file__).resolve().parents[3]

BAD_YAML = (
    "name: Bad\nslug: badproj\nproject_format_version: 2\n"
    "pipeline:\n"
    "  deliver_preset: {format: mp4, codec: H264, render_preset: X}\n"
)
GOOD_YAML = "name: Good\nslug: goodproj\nproject_format_version: 2\n"


@pytest.fixture
def two_project_root(tmp_path):
    """One invalid-config project beside one valid project."""
    bad = tmp_path / "badproj"
    bad.mkdir()
    (bad / "project.yaml").write_text(BAD_YAML, encoding="utf-8")
    good = tmp_path / "goodproj"
    good.mkdir()
    (good / "project.yaml").write_text(GOOD_YAML, encoding="utf-8")
    return tmp_path


def test_strict_load_refuses_with_what_why_fix(two_project_root):
    with pytest.raises(ProjectConfigInvalid) as caught:
        load_project_config(two_project_root / "badproj" / "project.yaml",
                            migrate_format=False, strict=True)
    refused = caught.value
    assert isinstance(refused, RenRefusal)
    assert refused.recovery_class == "needs_question"
    assert refused.code == "REN_PROJECT_CONFIG_INVALID"
    # what names the file and the key ...
    assert "badproj/project.yaml" in refused.what
    assert "pipeline.deliver_preset.render_preset" in refused.what
    # ... why carries the validation message ...
    assert "which nothing reads" in refused.why
    # ... and fix says how to resolve it.
    assert "remove `pipeline.deliver_preset.render_preset`" in refused.fix
    assert "or ask the captain" in refused.fix
    assert "REFUSAL_EXIT_CODE" not in refused.render()
    assert REFUSAL_EXIT_CODE == 4


def test_lenient_load_returns_config_with_findings(two_project_root):
    config = load_project_config(
        two_project_root / "badproj" / "project.yaml",
        migrate_format=False, strict=False)
    assert config.slug == "badproj"
    assert len(config.config_findings) == 1
    assert "render_preset" in config.config_findings[0]
    # A clean config carries no findings.
    clean = load_project_config(
        two_project_root / "goodproj" / "project.yaml",
        migrate_format=False, strict=False)
    assert clean.config_findings == []


def test_scan_lists_invalid_project_silently_by_default(two_project_root,
                                                        capsys):
    configs = scan_projects(two_project_root)
    assert {c.slug for c in configs} == {"badproj", "goodproj"}
    assert capsys.readouterr().err == ""
    bad = next(c for c in configs if c.slug == "badproj")
    assert bad.config_findings


def test_scan_warns_only_when_asked(two_project_root, capsys):
    scan_projects(two_project_root, warn=True)
    err = capsys.readouterr().err
    assert "Warning:" in err and "badproj" in err
    assert "goodproj" not in err


def test_get_project_strictness(two_project_root):
    with pytest.raises(ProjectConfigInvalid):
        get_project("badproj", root=two_project_root, strict=True)
    lenient = get_project("badproj", root=two_project_root, strict=False)
    assert lenient.config_findings
    valid = get_project("goodproj", root=two_project_root)
    assert valid.config_findings == []


def test_warn_config_findings_prints_one_line(two_project_root, capsys):
    bad = get_project("badproj", root=two_project_root, strict=False)
    warn_config_findings(bad)
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("⚠ config:")
    good = get_project("goodproj", root=two_project_root, strict=False)
    warn_config_findings(good)
    assert capsys.readouterr().err == ""


def test_project_status_lenient_reports_findings(two_project_root):
    info = project_status("badproj", root=two_project_root, strict=False)
    assert len(info["config_findings"]) == 1
    with pytest.raises(ProjectConfigInvalid):
        project_status("badproj", root=two_project_root, strict=True)


def test_list_projects_includes_invalid(two_project_root):
    configs = list_projects(root=two_project_root)
    assert {c.slug for c in configs} == {"badproj", "goodproj"}


def _run_manage(args, root, python=None):
    env = dict(os.environ)
    env["PIPELINE_PROJECTS_ROOT"] = str(root)
    return subprocess.run(
        [python or sys.executable, str(PILOT_ROOT / "manage_project.py"),
         *args],
        cwd=PILOT_ROOT, env=env, capture_output=True, text=True,
        timeout=120, check=False)


def _ml_python():
    """The ladder-resolved interpreter, or a skip naming the environment.

    `manage_project.py run` preflights the ML stack before it resolves
    the project, so the exit-4 refusal is only reachable under an
    interpreter that carries it.
    """
    from library.tools.shared_environment import python_interpreter
    path, _ = python_interpreter(PILOT_ROOT)
    if not path:
        pytest.skip("no ML interpreter on this machine "
                    "(durable venv or PIPELINE_PYTHON) - "
                    "the `run` refusal needs the preflight to pass")
    return path


def test_cli_status_answers_and_edit_refuses(two_project_root):
    status = _run_manage(["status", "badproj"], two_project_root)
    assert status.returncode == 0, status.stderr
    assert "⚠ config:" in status.stderr
    assert "render_preset" in status.stderr
    assert "Traceback" not in status.stderr

    edit = _run_manage(["run", "badproj", "--dry-run"], two_project_root,
                       python=_ml_python())
    assert edit.returncode == 4, edit.stderr
    assert "Traceback" not in edit.stderr
    assert "what:" in edit.stderr and "why:" in edit.stderr
    assert "fix:" in edit.stderr

    projects = _run_manage(["list"], two_project_root)
    assert projects.returncode == 0, projects.stderr
    assert "badproj" in projects.stdout
    assert "invalid config" in projects.stdout

    # A valid project is answered with no warning about the other one.
    good = _run_manage(["status", "goodproj"], two_project_root)
    assert good.returncode == 0, good.stderr
    assert "⚠ config:" not in good.stderr
    assert "badproj" not in good.stderr
