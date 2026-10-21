"""The clean-install check must not touch the live install.

`scripts/ren_clean_install_check.py` simulates a clean installation on
this Mac: it packages a release-shaped artifact, installs it with
`scripts/install_ren.sh` into a throwaway prefix and a throwaway HOME,
and then measures what the installed engine can do. Two things make that
measurement safe to run on a machine that already has Ren installed:

* the sandbox is refused when it would land inside the live install
  (`~/.local/share/vep`) or when its HOME is the real home - the check
  must never overwrite the install it is measuring;
* the machine-wide per-tree build cache (`ren_native`) is pointed inside
  the sandbox, so a simulation adds nothing to the live machine's cache.

The fix-path scan is the check's own teeth: a fix the doctor names that
points at a file the customer does not have is a failure, not a note.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "_ren_clean_install_check",
    REPO_ROOT / "scripts" / "ren_clean_install_check.py",
)
check = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check
_SPEC.loader.exec_module(check)


def test_a_sandbox_inside_the_live_install_is_refused(tmp_path, monkeypatch):
    """The live install is read-only here."""
    live = tmp_path / "live" / "vep"
    live.mkdir(parents=True)
    monkeypatch.setattr(check, "LIVE_VEP_HOME", live)
    sandbox = tmp_path / "live" / "vep" / "sandbox"
    sandbox.mkdir(parents=True)
    assert "inside the live install" in check.refusal_for(sandbox)


def test_a_sandbox_home_that_is_the_real_home_is_refused(tmp_path, monkeypatch):
    """The captain's shell profile is not a sandbox."""
    monkeypatch.setattr(check, "LIVE_VEP_HOME", tmp_path / "elsewhere")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "home").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox / "home"))
    assert "real home" in check.refusal_for(sandbox)


def test_an_ordinary_sandbox_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "LIVE_VEP_HOME", tmp_path / "elsewhere")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    assert check.refusal_for(sandbox) == ""


def test_the_install_points_the_build_cache_inside_the_sandbox(tmp_path):
    """A simulation adds nothing to the live machine's per-tree cache."""
    prefix = tmp_path / "vep"
    prefix.mkdir()
    env = check.sandbox_env(tmp_path)
    assert env["PIPELINE_REN_NATIVE_CACHE"] == str(
        prefix / "native" / "ren-native")


def test_a_fix_naming_a_path_that_does_not_exist_fails_the_check(tmp_path):
    """The scan is the check's teeth: a fix nobody can run is a failure."""
    engine = tmp_path / "vep" / "current"
    (engine / "scripts").mkdir(parents=True)
    report = check.Report()
    doctor = {"checks": [
        {"name": "native ren-resolved", "fix": "build it with scripts/build_ren_resolved.sh"},
        {"name": "MFA aligner", "fix": "ren setup --with mfa"},
    ]}
    check._check_fixes_are_runnable(tmp_path, report, doctor)
    assert report.failed == 1
    assert "build_ren_resolved.sh" in report.checks[0].detail


def test_a_fix_naming_a_path_that_exists_passes(tmp_path):
    engine = tmp_path / "vep" / "current"
    (engine / "scripts").mkdir(parents=True)
    (engine / "scripts" / "build_ren_resolved.sh").write_text("", encoding="utf-8")
    report = check.Report()
    doctor = {"checks": [
        {"name": "native ren-resolved",
         "fix": f"build it with {engine}/scripts/build_ren_resolved.sh"},
        {"name": "MFA aligner", "fix": "ren setup --with mfa"},
    ]}
    check._check_fixes_are_runnable(tmp_path, report, doctor)
    assert report.failed == 0


def test_the_report_counts_every_check():
    """The denominators are the checks: a count nobody can audit is prose."""
    report = check.Report()
    report.add("one", True, "ok")
    report.add("two", False, "not ok")
    report.manual("three", "cannot be measured here")
    assert (report.passed, report.failed, report.manual_only) == (1, 1, 1)
    rendered = report.render()
    assert "1 passed, 1 failed, 1 manual-only (of 3 checks)" in rendered
