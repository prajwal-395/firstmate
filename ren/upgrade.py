"""Side-by-side engine upgrades and rollback for an installed Ren.

An upgrade publishes a complete immutable version first. It runs that
version's doctor and checks every configured project's format with the
candidate engine. Only after both checks pass does it atomically switch
`current`; `previous` records the engine that was active immediately
before the switch. Neither operation opens or writes a project.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from library.tools.ren_refusal import RenRefusal
from ren.engine_root import (
    current_link,
    previous_link,
    vep_home,
    version_at_pointer,
    versions_root,
)
from ren.package_engine import (
    stage_versioned,
    switch_current,
    switch_previous,
)
from ren.version import format_version

_DOCTOR_TIMEOUT_SECONDS = 300
_FORMAT_TIMEOUT_SECONDS = 120


def _refused(what: str, why: str, fix: str) -> RenRefusal:
    return RenRefusal(what, why, fix)


@contextmanager
def _update_lock(home: Path):
    """Serialize upgrades and rollbacks for one installation home."""
    try:
        home.mkdir(parents=True, exist_ok=True)
        lock_path = home / ".ren-update.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise _refused(
            "Ren could not lock its installation",
            f"the application support folder is not writable: {exc}",
            "check permissions on the Ren application support folder") from exc
    with os.fdopen(fd, "a", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise _refused(
                "Ren could not lock its installation",
                f"the update lock could not be acquired: {exc}",
                "close any interrupted Ren update and retry") from exc
        yield


def _candidate_env(engine_root: Path, home: Path) -> dict:
    env = dict(os.environ)
    env["REN_ENGINE_ROOT"] = str(engine_root)
    env["PIPELINE_VEP_HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(engine_root), env.get("PYTHONPATH", "")) if part)
    env["PATH"] = os.pathsep.join(
        (str(engine_root / "bin"), env.get("PATH", "")))
    return env


def _candidate_python(env: dict) -> str:
    """Choose the same interpreter `bin/ren` would choose for doctor."""
    explicit = env.get("PIPELINE_PYTHON", "")
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    from_path = shutil.which("python3", path=env.get("PATH"))
    return from_path or sys.executable


def _verify_doctor(engine_root: Path, home: Path) -> None:
    """Run the candidate's doctor and require a valid, passing report."""
    command = [str(engine_root / "bin" / "ren"), "doctor", "--json"]
    try:
        result = subprocess.run(
            command, cwd=str(engine_root), env=_candidate_env(engine_root, home),
            capture_output=True, encoding="utf-8",
            timeout=_DOCTOR_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"candidate doctor could not run ({type(exc).__name__}: {exc})") from exc
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"candidate doctor exited {result.returncode}"
            + (f": {detail}" if detail else "."))
    try:
        report = json.loads(result.stdout)
        identity = report["ren"]
        reported_root = Path(identity["engine_root"]).resolve()
        reported_build = format_version(identity)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise RuntimeError(
            f"candidate doctor returned no valid Ren build identity: {exc}") from exc
    if reported_root != engine_root.resolve():
        raise RuntimeError(
            f"candidate doctor used {reported_root}, expected {engine_root.resolve()}")
    if reported_build != engine_root.name:
        raise RuntimeError(
            f"candidate doctor reported build {reported_build}, "
            f"but its directory is named {engine_root.name}")
    required_failures = report["required_failures"]
    if required_failures:
        failed_names = {str(item) for item in required_failures}
        failed_checks = [check for check in report["checks"]
                         if check["name"] in failed_names]
        fixes = "; ".join(dict.fromkeys(
            check["fix"] for check in failed_checks if check.get("fix")))
        raise RenRefusal(
            "candidate doctor found required failures: "
            + ", ".join(sorted(failed_names)),
            "the candidate did not pass Ren's required environment checks",
            fixes or "run `ren doctor` and fix every required failure before retrying")
    if result.returncode != 0:
        raise RuntimeError(
            f"candidate doctor exited {result.returncode} without reporting "
            "the required checks that failed")


_PROJECT_FORMAT_CHECK = r"""
import json
import sys
from pathlib import Path

from library.tools.project_format import check_format
from library.tools.project_registry import project_config_paths
from library.tools.ren_refusal import RenRefusal

root = Path(sys.argv[1]).expanduser()
projects = [path.parent for path in project_config_paths(root)]

reports = []
for project in projects:
    try:
        reports.append({"project": str(project),
                        "format_version": check_format(project),
                        "compatible": True})
    except (RenRefusal, ValueError, OSError) as exc:
        reports.append({"project": str(project), "compatible": False,
                        "reason": str(exc),
                        "what": getattr(exc, "what", ""),
                        "why": getattr(exc, "why", ""),
                        "fix": getattr(exc, "fix", "")})
    except Exception as exc:
        reports.append({"project": str(project), "compatible": False,
                        "reason": f"{type(exc).__name__}: {exc}"})
print(json.dumps(reports))
"""


def _verify_project_formats(engine_root: Path, home: Path) -> None:
    """Check project format compatibility using the candidate's range.

    The check is read-only. It visits the same flat and one-level client
    layouts as the project registry and calls `check_format`, never the
    migration function, so no project metadata or media changes here.
    """
    from library.tools.paths import PROJECTS_ROOT

    projects_root = Path(PROJECTS_ROOT)
    env = _candidate_env(engine_root, home)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(engine_root), env.get("PYTHONPATH", "")) if part)
    command = [_candidate_python(env), "-c", _PROJECT_FORMAT_CHECK,
               str(projects_root)]
    try:
        result = subprocess.run(
            command, cwd=str(engine_root), env=env, capture_output=True,
            encoding="utf-8", timeout=_FORMAT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"candidate project-format check could not run "
            f"({type(exc).__name__}: {exc})") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"candidate project-format check exited {result.returncode}"
            + (f": {detail}" if detail else "."))
    try:
        reports = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(
            f"candidate project-format check returned invalid JSON: {exc}") from exc
    incompatible = [row for row in reports if not row["compatible"]]
    if incompatible:
        projects = ", ".join(row["project"] for row in incompatible)
        reasons = "; ".join(
            row["why"] or row["reason"] for row in incompatible)
        fixes = "; ".join(dict.fromkeys(
            row["fix"] or "Correct the named project.yaml and retry the upgrade."
            for row in incompatible))
        raise RenRefusal(
            f"Ren cannot upgrade while these projects are outside the "
            f"candidate format range: {projects}",
            reasons,
            fixes)


def _restore_previous(home: Path, version: str | None) -> None:
    pointer = previous_link(home)
    if version is None:
        if pointer.is_symlink():
            pointer.unlink()
    else:
        switch_previous(home, version)


def _discard_unactivated(engine_root: Path, home: Path) -> None:
    """Remove a version created by this attempt after verification fails."""
    candidate = engine_root.resolve()
    versions = versions_root(home).resolve()
    try:
        candidate.relative_to(versions)
    except ValueError:
        return
    if candidate == (home / "current").resolve():
        return
    previous = previous_link(home)
    if previous.is_symlink() and previous.resolve() == candidate:
        return
    shutil.rmtree(candidate)


def upgrade(source: str | Path, home: Path | None = None,
            channel: str | None = None) -> Path:
    """Install, verify and activate a candidate engine version.

    `source` is a checkout or an already built engine tree. The previous
    active engine stays pointed to by `current` until all checks pass.
    """
    home = (Path(home).expanduser() if home is not None else vep_home()).resolve()
    source = Path(source).expanduser().resolve()
    with _update_lock(home):
        old_version = version_at_pointer(current_link(home), home)
        if old_version is None:
            raise _refused(
                "Ren has no active installed version to upgrade",
                "the current pointer does not name a complete versioned engine",
                "install Ren with the installer, then run `ren upgrade <engine-source>`")
        old_previous = version_at_pointer(previous_link(home), home)
        candidate = None
        try:
            candidate = stage_versioned(home, source, channel=channel)
            _verify_doctor(candidate, home)
            _verify_project_formats(candidate, home)
            active_now = version_at_pointer(current_link(home), home)
            if active_now != old_version:
                raise RenRefusal(
                    "Ren's active version changed during the upgrade",
                    f"it was {old_version} before candidate checks and is "
                    f"now {active_now}",
                    "retry `ren upgrade <engine-source>` against the current version")
            switch_previous(home, old_version)
            try:
                switch_current(home, candidate.name)
            except Exception:
                _restore_previous(home, old_previous)
                raise
            return candidate
        except RenRefusal:
            if candidate is not None:
                _discard_unactivated(candidate, home)
            raise
        except Exception as exc:
            if candidate is not None:
                _discard_unactivated(candidate, home)
            raise _refused(
                "Ren did not activate the candidate engine",
                f"verification or installation failed: {type(exc).__name__}: {exc}",
                "fix the reported issue and retry `ren upgrade <engine-source>`") from exc


def rollback(home: Path | None = None) -> Path:
    """Switch current and previous atomically, without opening projects."""
    home = (Path(home).expanduser() if home is not None else vep_home()).resolve()
    with _update_lock(home):
        current = version_at_pointer(current_link(home), home)
        previous = version_at_pointer(previous_link(home), home)
        if current is None:
            raise _refused(
                "Ren has no active installed version to roll back",
                "the current pointer does not name a complete versioned engine",
                "repair the Ren installation before rolling it back")
        if previous is None:
            raise _refused(
                "Ren has no previous version to restore",
                "the previous pointer does not name a complete versioned engine",
                "install a newer Ren version before using `ren rollback`")
        if previous == current:
            raise _refused(
                "Ren has no different previous version to restore",
                "the previous pointer already names the active engine",
                "install a newer Ren version before using `ren rollback`")
        try:
            switch_current(home, previous)
        except Exception as exc:
            raise _refused(
                "Ren could not activate the rollback version",
                f"the current pointer was left on {current}: {exc}",
                "check permissions in the Ren application support folder "
                "and retry `ren rollback`") from exc
        try:
            switch_previous(home, current)
        except Exception as exc:
            try:
                switch_current(home, current)
            except Exception as restore_exc:
                raise _refused(
                    "Ren could not complete rollback",
                    f"the previous pointer failed ({exc}); restoring the "
                    f"current pointer also failed ({restore_exc})",
                    "inspect the current and previous symlinks under "
                    f"{home} before retrying") from restore_exc
            raise _refused(
                "Ren could not record the rollback target",
                f"the previous pointer failed: {exc}; current was restored "
                f"to {current}",
                "check permissions in the Ren application support folder "
                "and retry `ren rollback`") from exc
        return versions_root(home) / previous


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren upgrade",
        description="Install, verify and activate a Ren engine version.")
    parser.add_argument(
        "engine_source",
        help="Ren checkout or built engine tree to install alongside the active version")
    parser.add_argument(
        "--channel", default=None,
        help="Build channel (defaults to the source build's channel or stable)")
    args = parser.parse_args(argv)
    candidate = upgrade(args.engine_source, channel=args.channel)
    print(f"Ren upgraded to {candidate.name}. Run `ren rollback` to restore the previous version.")
    return 0


def rollback_main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren rollback",
        description="Restore the previously active Ren engine version.")
    parser.parse_args(argv)
    restored = rollback()
    print(f"Ren rolled back to {restored.name}.")
    return 0
