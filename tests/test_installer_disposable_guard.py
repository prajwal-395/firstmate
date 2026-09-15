"""Both installers refuse a disposable checkout before stamping it.

The defect this exists for: both installers stamp this checkout's
absolute path into a surface that outlives the checkout (Workspace >
Scripts copies; the Workflow Integration plugin directory).  Run one
from a lane that is later reclaimed and the installed surface silently
points at a directory that no longer exists - 2026-08-31 installed
com.videoeditingpilot.vep from a .treehouse lane, and nothing detected
it until an audit on 2026-09-14.

"Disposable" is a PROPERTY of the checkout, not a path substring: a
linked git worktree (`--git-dir` differing from `--git-common-dir`)
is managed by a worktree tool and may be reclaimed, which generalises
to every worktree tool where a `.treehouse` grep would only guard the
one incident this was born from.  A checkout git cannot vouch for at
all is a third answer - `unknown` - and it refuses too, rather than
passing silently.

Per AGENTS.md section 10.4 both directions are proved: the guard FIRES
on a linked worktree and does NOT fire on a main checkout.  The
synthetic main checkout under tmp_path proves the property; the
configured durable checkout below proves it on the real target where
one is configured, reached through a PIPELINE_* env var rather than a
hardcoded path.  No test here writes to a real
install location: the Scripts installer runs with HOME redirected into
tmp_path, and the Workflow installer is driven through a copy whose
destinations are rewritten into tmp_path (a real run would write into
/Library/Application Support).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = Path(__file__).resolve().parents[1]
GUARD_LIB = REPO / "scripts" / "lib" / "guard_durable_checkout.sh"
SCRIPTS_INSTALLER = REPO / "scripts" / "install_resolve_scripts.sh"
WFI_INSTALLER = REPO / "scripts" / "install_workflow_integration.sh"
PLUGIN_ID = "com.videoeditingpilot.vep"

# A durable checkout this guard must never fire on, reached through the
# environment so no machine's path is stamped into this file.  Set
# PIPELINE_DURABLE_CHECKOUT to a real main checkout (e.g. the captain's)
# where one exists; unset and the test skips, with the synthetic main
# checkout under tmp_path still proving the does-not-fire direction.
DURABLE_CHECKOUT_ENV = "PIPELINE_DURABLE_CHECKOUT"

NO_GIT = "git is not on PATH, so no worktree harness can be built here"
NO_CONFIGURED = "no durable checkout configured via PIPELINE_DURABLE_CHECKOUT"

UTILITY = Path(
    "Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Fusion/Scripts/Utility"
)


# ── Harness ──────────────────────────────────────────────────────────


def _durability(path):
    """The guard's own answer for a checkout: durable, linked-worktree or unknown."""
    proc = subprocess.run(
        ["bash", "-c", '. "$1"; vep_checkout_durability "$2"',
         "guard", str(GUARD_LIB), str(path)],
        capture_output=True, encoding="utf-8", check=True,
    )
    return proc.stdout.strip()


def _git(*args, cwd=None):
    return subprocess.run(
        ["git", "-c", "user.name=vep-test", "-c", "user.email=t@t",
         *args],
        cwd=cwd, capture_output=True, encoding="utf-8", check=True,
    )


def _make_repo(path):
    """A fresh MAIN checkout by construction: its git dir IS its common dir."""
    path.mkdir(parents=True)
    _git("init", "-q", str(path))
    (path / "f.txt").write_text("x", encoding="utf-8")
    _git("-C", str(path), "add", "f.txt")
    _git("-C", str(path), "commit", "-qm", "init")
    return path


def _make_linked_checkout(origin, path):
    """A LINKED worktree of the harness repo: git dir differs from common dir."""
    _git("-C", str(origin), "worktree", "add", str(path))
    return path


def _plant_scripts_harness(root):
    """The smallest checkout the Scripts installer accepts: its own
    script, the shared guard, and the real entry points to stamp."""
    scripts = root / "scripts"
    (scripts / "lib").mkdir(parents=True)
    shutil.copy(SCRIPTS_INSTALLER, scripts / "install_resolve_scripts.sh")
    shutil.copy(GUARD_LIB, scripts / "lib" / "guard_durable_checkout.sh")
    resolve_scripts = root / "resolve_scripts"
    resolve_scripts.mkdir()
    for entry in (REPO / "resolve_scripts").glob("*.py"):
        shutil.copy(entry, resolve_scripts / entry.name)
    # Committed, so a linked worktree of this harness carries the files.
    _git("-C", str(root), "add", "-A")
    _git("-C", str(root), "commit", "-qm", "harness")
    return root


def _run_scripts_installer(checkout, home, *args, extra_env=None):
    env = dict(os.environ, HOME=str(home))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(checkout / "scripts" / "install_resolve_scripts.sh"),
         *args],
        env=env, capture_output=True, encoding="utf-8", check=False,
    )


# ── The property, not the path ───────────────────────────────────────


def test_both_installers_share_one_guard():
    """One classifier, read from one file by both installers - not two
    copies of the same check that can drift apart."""
    assert GUARD_LIB.is_file()
    for path in (SCRIPTS_INSTALLER, WFI_INSTALLER):
        source = path.read_text(encoding="utf-8")
        assert "lib/guard_durable_checkout.sh" in source, path.name
        assert "vep_checkout_durability" in source, path.name
        assert "vep_refuse_disposable_checkout" in source, path.name


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_the_path_alone_never_fires(tmp_path):
    """The incident was a `.treehouse` lane, but the SIGNAL is the
    linked worktree, not the string.  A main checkout that happens to
    live under a `.treehouse` path is durable - proved here, not
    promised in a comment."""
    lane = tmp_path / ".treehouse" / "lane"
    assert _durability(_make_repo(lane)) == "durable"


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_a_linked_worktree_is_not_durable(tmp_path):
    origin = _make_repo(tmp_path / "origin")
    linked = _make_linked_checkout(origin, tmp_path / "linked")
    assert _durability(linked) == "linked-worktree"


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_a_main_checkout_is_durable(tmp_path):
    assert _durability(_make_repo(tmp_path / "origin")) == "durable"


def test_a_checkout_git_cannot_vouch_for_is_unknown(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _durability(plain) == "unknown"
    assert _durability(tmp_path / "does-not-exist") == "unknown"


def test_the_configured_durable_checkout_is_durable():
    """The negative direction, on a real durable checkout where one is
    configured.  It runs the classifier only: no installer, no write.
    Where no checkout is configured this skips, and the synthetic main
    checkout under tmp_path still proves the does-not-fire direction."""
    raw = os.environ.get(DURABLE_CHECKOUT_ENV, "").strip()
    if not raw:
        pytest.skip(NO_CONFIGURED)
    target = Path(raw)
    if not target.is_dir():
        pytest.skip(f"{DURABLE_CHECKOUT_ENV} does not point at a directory: {raw!r}")
    assert _durability(target) == "durable"


# ── The Scripts installer, end to end with HOME redirected ───────────


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_scripts_install_from_a_linked_worktree_is_refused(tmp_path):
    origin = _plant_scripts_harness(_make_repo(tmp_path / "origin"))
    linked = _make_linked_checkout(origin, tmp_path / "linked")
    home = tmp_path / "home"
    home.mkdir()

    result = _run_scripts_installer(linked, home)

    assert result.returncode != 0
    assert "refusing to install" in result.stderr
    assert "--allow-disposable" in result.stderr
    assert not (home / UTILITY).exists()


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_scripts_install_from_a_main_checkout_is_not_refused(tmp_path):
    """The same installer, the same files, run from the harness repo's
    own main checkout: the guard stays silent and the stamp lands."""
    origin = _plant_scripts_harness(_make_repo(tmp_path / "origin"))
    home = tmp_path / "home"
    home.mkdir()

    result = _run_scripts_installer(origin, home)

    assert result.returncode == 0, result.stderr
    installed = sorted((home / UTILITY).glob("*.py"))
    assert installed, "nothing was installed from a durable checkout"
    assert f"REPO_ROOT = {str(origin)!r}" in installed[0].read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_the_override_is_explicit_and_per_invocation(tmp_path):
    origin = _plant_scripts_harness(_make_repo(tmp_path / "origin"))
    linked = _make_linked_checkout(origin, tmp_path / "linked")
    home = tmp_path / "home"
    home.mkdir()

    result = _run_scripts_installer(linked, home, "--allow-disposable")

    assert result.returncode == 0, result.stderr
    assert "explicit --allow-disposable" in result.stderr
    assert sorted((home / UTILITY).glob("*.py")), "the override installed nothing"


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
@pytest.mark.parametrize("var", ["VEP_ALLOW_DISPOSABLE", "VEP_ALLOW_WORKTREE", "ALLOW_DISPOSABLE"])
def test_no_environment_variable_disables_the_guard(tmp_path, var):
    """The override is a per-invocation flag ON PURPOSE: an env var
    could be exported once in a shell profile and silently disarm the
    guard forever.  Each of these must still refuse."""
    origin = _plant_scripts_harness(_make_repo(tmp_path / "origin"))
    linked = _make_linked_checkout(origin, tmp_path / "linked")
    home = tmp_path / "home"
    home.mkdir()

    result = _run_scripts_installer(linked, home, extra_env={var: "1"})

    assert result.returncode != 0
    assert not (home / UTILITY).exists()


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_scripts_dry_run_reports_and_writes_nothing(tmp_path):
    origin = _plant_scripts_harness(_make_repo(tmp_path / "origin"))
    linked = _make_linked_checkout(origin, tmp_path / "linked")
    home = tmp_path / "home"
    home.mkdir()

    result = _run_scripts_installer(linked, home, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "would be REFUSED (linked-worktree)" in result.stdout
    assert not (home / UTILITY).exists()


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_scripts_uninstall_is_never_blocked(tmp_path):
    """Removing a stale install must work from anywhere - including the
    lane that caused it.  A planted copy is taken out, with no flag."""
    origin = _plant_scripts_harness(_make_repo(tmp_path / "origin"))
    linked = _make_linked_checkout(origin, tmp_path / "linked")
    home = tmp_path / "home"
    dest = home / UTILITY
    dest.mkdir(parents=True)
    planted = dest / "VEP Pipeline Panel.py"
    planted.write_text("# stale", encoding="utf-8")

    result = _run_scripts_installer(linked, home, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert not planted.exists()


# ── The Workflow installer, through a redirected copy ────────────────
#
# Its destinations are machine-level (/Library/...), so the installer
# itself is NEVER run here.  A copy with its destinations rewritten
# into tmp_path exercises the real guard on the real install path.


def _plant_wfi_harness(root, dest_root):
    """Copy the Workflow installer with PLUGIN_ROOT and the SDK node
    aimed at tmp; the guard and stamp logic are untouched."""
    scripts = root / "scripts"
    (scripts / "lib").mkdir(parents=True)
    shutil.copy(WFI_INSTALLER, scripts / "install_workflow_integration.sh")
    shutil.copy(GUARD_LIB, scripts / "lib" / "guard_durable_checkout.sh")
    plugin = root / "resolve_workflow_integration" / PLUGIN_ID
    shutil.copytree(REPO / "resolve_workflow_integration" / PLUGIN_ID, plugin)
    dest_root.mkdir(parents=True, exist_ok=True)
    fake_sdk = dest_root / "WorkflowIntegration.node"
    fake_sdk.write_text("fake-sdk", encoding="utf-8")

    text = (scripts / "install_workflow_integration.sh").read_text(encoding="utf-8")
    text = text.replace(
        'PLUGIN_ROOT="/Library/Application Support/Blackmagic Design/DaVinci Resolve'
        '/Workflow Integration Plugins"',
        f'PLUGIN_ROOT="{dest_root}/plugins"',
    )
    text = text.replace(
        'SDK_NODE="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer'
        '/Workflow Integrations/Examples/SamplePlugin/WorkflowIntegration.node"',
        f'SDK_NODE="{fake_sdk}"',
    )
    (scripts / "install_workflow_integration.sh").write_text(text, encoding="utf-8")
    # Committed, so a linked worktree of this harness carries the files.
    _git("-C", str(root), "add", "-A")
    _git("-C", str(root), "commit", "-qm", "harness")
    return root


def _run_wfi_installer(checkout, *args):
    return subprocess.run(
        ["bash", str(checkout / "scripts" / "install_workflow_integration.sh"),
         *args],
        capture_output=True, encoding="utf-8", check=False,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_workflow_install_from_a_linked_worktree_is_refused(tmp_path):
    origin = _make_repo(tmp_path / "origin")
    _plant_wfi_harness(origin, tmp_path / "redir")
    linked = _make_linked_checkout(origin, tmp_path / "linked")

    result = _run_wfi_installer(linked)

    assert result.returncode != 0
    assert "refusing to install" in result.stderr
    assert "--allow-disposable" in result.stderr
    assert not (tmp_path / "redir" / "plugins").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_workflow_install_from_a_main_checkout_is_not_refused(tmp_path):
    origin = _make_repo(tmp_path / "origin")
    _plant_wfi_harness(origin, tmp_path / "redir")

    result = _run_wfi_installer(origin)

    assert result.returncode == 0, result.stderr
    stamped = tmp_path / "redir" / "plugins" / PLUGIN_ID / "main.js"
    assert stamped.is_file()
    assert f'const REPO_ROOT = "{origin}";' in stamped.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_workflow_override_installs_deliberately(tmp_path):
    origin = _make_repo(tmp_path / "origin")
    _plant_wfi_harness(origin, tmp_path / "redir")
    linked = _make_linked_checkout(origin, tmp_path / "linked")

    result = _run_wfi_installer(linked, "--allow-disposable")

    assert result.returncode == 0, result.stderr
    stamped = tmp_path / "redir" / "plugins" / PLUGIN_ID / "main.js"
    assert f'const REPO_ROOT = "{linked}";' in stamped.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_workflow_dry_run_reports_and_writes_nothing(tmp_path):
    origin = _make_repo(tmp_path / "origin")
    _plant_wfi_harness(origin, tmp_path / "redir")
    linked = _make_linked_checkout(origin, tmp_path / "linked")

    result = _run_wfi_installer(linked, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "would be REFUSED (linked-worktree)" in result.stdout
    assert not (tmp_path / "redir" / "plugins").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason=NO_GIT)
def test_workflow_uninstall_is_never_blocked(tmp_path):
    origin = _make_repo(tmp_path / "origin")
    _plant_wfi_harness(origin, tmp_path / "redir")
    linked = _make_linked_checkout(origin, tmp_path / "linked")

    result = _run_wfi_installer(linked, "--uninstall")

    assert result.returncode == 0, result.stderr
