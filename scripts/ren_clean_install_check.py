#!/usr/bin/env python3
"""PROD-001: prove a CLEAN install on this Mac, and report what it cannot do.

The check simulates what a customer gets on a machine that has never
seen a Ren checkout:

  1. build the engine tree from this checkout and package it the way a
     release does (a ZIP plus `SHA256SUMS.txt`),
  2. install it with `scripts/install_ren.sh` into a THROWAWAY prefix and
     a THROWAWAY HOME - never the live install, never the live home,
  3. run `ren doctor --json` and `ren capabilities --check` FROM that
     install, from a working directory outside this checkout,
  4. check the properties the punchlist names: runtime installation,
     dependency resolution, Resolve detection, capability packs, one
     engine version across the installed binaries and scripts, the
     upgrade and rollback path, and - the one that bites - that no step
     of the install assumes the development checkout.

Every check prints its evidence. The report's denominators are the
checks themselves: "11 passed, 2 failed, 1 manual-only" counts checks,
never impressions.

Usage:
    python3 scripts/ren_clean_install_check.py [--src DIR] [--sandbox DIR]
        [--keep] [--json] [--no-upgrade]

The sandbox lives under /tmp by default and is removed at the end unless
`--keep` says otherwise. Nothing outside the sandbox is written: the
live install at `~/.local/share/vep` is only ever READ, and the check
refuses to run if the sandbox would land inside it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_ren.sh"
PACKAGE_ARCHIVE = REPO_ROOT / ".github" / "scripts" / "package_engine_archive.py"

#: The live install this check must never touch.
LIVE_VEP_HOME = Path(
    os.environ.get("PIPELINE_VEP_HOME")
    or (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        / "vep")
).expanduser()


@dataclass
class Check:
    name: str
    status: str  # "pass", "fail", "manual_only"
    detail: str = ""


@dataclass
class Report:
    checks: list = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, "pass" if ok else "fail", detail))

    def manual(self, name: str, why: str) -> None:
        self.checks.append(Check(name, "manual_only", why))

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def manual_only(self) -> int:
        return sum(1 for c in self.checks if c.status == "manual_only")

    def render(self) -> str:
        lines = []
        for check in self.checks:
            lines.append(f"  [{check.status.upper():^11}] {check.name}: {check.detail}")
        lines.append(
            f"\n  {self.passed} passed, {self.failed} failed, "
            f"{self.manual_only} manual-only "
            f"(of {len(self.checks)} checks)"
        )
        return "\n".join(lines)


def run(command, *, env=None, cwd=None, timeout=1800, check=False):
    """Run a command, capturing text as UTF-8 (never the locale codec)."""
    done = subprocess.run(
        [str(part) for part in command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=cwd, timeout=timeout, check=check,
    )
    return done


def build_artifact(src: Path, sandbox: Path) -> Path:
    """Package the checkout the way a release does: a ZIP + SHA256SUMS.txt."""
    engine = sandbox / "engine"
    done = run(
        [sys.executable, "-m", "ren.package_engine",
         "--src", src, "--dest", engine,
         "--channel", "stable", "--sha", "prod001-clean-install",
         "--built-at", time.strftime("%Y-%m-%d")],
        cwd=str(src), timeout=1800,
    )
    if done.returncode != 0:
        raise RuntimeError(
            "the engine tree did not build:\n"
            + (done.stderr or done.stdout))
    release = sandbox / "release"
    release.mkdir(parents=True, exist_ok=True)
    archive = release / "ren-engine-clean-install.zip"
    done = run(
        [sys.executable, PACKAGE_ARCHIVE, "--engine", engine, "--archive", archive],
        timeout=600,
    )
    if done.returncode != 0:
        raise RuntimeError("the engine tree did not archive:\n" + (done.stderr or done.stdout))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release / "SHA256SUMS.txt").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def install(archive: Path, sandbox: Path) -> subprocess.CompletedProcess:
    """Install the artifact through the customer installer, into the sandbox."""
    home = sandbox / "home"
    prefix = sandbox / "vep"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PIPELINE_VEP_HOME": str(prefix),
        # The per-tree `ren_native` build cache is machine-wide by
        # default; a simulation must not add to the live machine's, so
        # it is pointed inside the sandbox.
        "PIPELINE_REN_NATIVE_CACHE": str(prefix / "native" / "ren-native"),
    }
    return run(
        ["bash", INSTALL_SCRIPT, "--url", archive.as_uri(),
         "--home", str(prefix), "--skip-system-deps"],
        env=env, cwd=str(sandbox), timeout=3600,
    )


def sandbox_env(sandbox: Path) -> dict:
    """The environment a customer shell has after the install."""
    home = sandbox / "home"
    prefix = sandbox / "vep"
    env = {
        "HOME": str(home),
        "PATH": f"{prefix}/bin:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "PIPELINE_VEP_HOME": str(prefix),
        "PIPELINE_REN_NATIVE_CACHE": str(prefix / "native" / "ren-native"),
    }
    venv_python = prefix / "venv-py312" / "bin" / "python3"
    if venv_python.is_file():
        env["PIPELINE_PYTHON"] = str(venv_python)
    return env


def ren(sandbox: Path, args, *, timeout=900):
    """Run the INSTALLED ren - the launcher, as a customer's shell would."""
    launcher = sandbox / "vep" / "bin" / "ren"
    return run([launcher, *args], env=sandbox_env(sandbox), cwd=str(sandbox),
               timeout=timeout)


def _literal_assignments(path: Path) -> dict:
    """The module-level literal assignments of a generated record.

    Read, not executed: a build record is data, and a check that runs
    it would run whatever the tree it is reading happens to contain.
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return {}
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                try:
                    values[target.id] = ast.literal_eval(value)
                except (ValueError, TypeError):
                    continue
    return values


def _check_versions(sandbox: Path, report: Report, doctor: dict) -> None:
    """The installed binaries and scripts must name ONE engine version."""
    native = ren(sandbox, ["--version"], timeout=120)
    python = ren(sandbox, ["version"], timeout=120)
    native_text = native.stdout.strip()
    python_text = python.stdout.strip()
    doctor_version = (doctor.get("ren") or {}).get("version", "")
    build_record = {}
    for path in (sandbox / "vep" / "versions").glob("*/ren/_build.py"):
        build_record = _literal_assignments(path)
        break
    same = native_text == python_text and native_text.startswith(
        f"ren {doctor_version}")
    detail = (f"--version and version agree: {native_text!r}"
              if same else
              f"--version={native_text!r} version={python_text!r} "
              f"doctor={doctor_version!r}")
    report.add("one_engine_version_across_binaries_and_scripts", same, detail)
    report.add(
        "the_packaged_build_record_names_its_build",
        bool(build_record.get("BUILD_SHA")),
        f"the packaged build record names sha "
        f"{build_record.get('BUILD_SHA')!r}, channel "
        f"{build_record.get('BUILD_CHANNEL')!r}",
    )


def _check_no_checkout_assumption(sandbox: Path, report: Report, src: Path,
                                  doctor_text: str) -> None:
    """No path under the development checkout may appear in the install."""
    needles = [str(src)]
    hits = [needle for needle in needles if needle in doctor_text]
    # The install's own record and launcher are read too: they are what
    # a customer's shell carries.
    for name in ("install.json", "bin/ren"):
        path = sandbox / "vep" / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            hits += [f"{name}: {needle}" for needle in needles if needle in text]
    report.add(
        "the_install_assumes_no_development_checkout",
        not hits,
        "no path under the checkout appears in the install's outputs"
        if not hits else f"the install names the checkout: {hits}",
    )


def _check_fixes_are_runnable(sandbox: Path, report: Report, doctor: dict) -> None:
    """Every fix the doctor names must be a command or a path that exists.

    A fix that names a path inside a development checkout is a fix a
    customer cannot run: the engine's own tree is where those files
    live, and the doctor must name them there.
    """
    problems = []
    named = 0
    for check in doctor.get("checks", []):
        fix = (check.get("fix") or "").strip()
        if not fix:
            continue
        named += 1
        for token in fix.split():
            token = token.strip("(),.;:`'\"")
            if "/" not in token or "(" in token or "://" in token:
                continue
            if not re.search(r"\.[A-Za-z0-9]{1,5}$", token):
                continue
            if token.startswith(("/", "~/")):
                candidate = Path(token).expanduser()
            else:
                # A relative path in a fix is not runnable: the reader's
                # working directory is not the engine's tree.
                problems.append(f"{check.get('name')}: {token} (relative)")
                continue
            if not candidate.exists():
                problems.append(f"{check.get('name')}: {token}")
    report.add(
        "every_fix_the_doctor_names_is_runnable",
        not problems,
        f"{named} fixes name paths that exist" if not problems
        else f"fixes naming a path that does not exist: {problems}",
    )


def _check_install_record(sandbox: Path, report: Report, version_text: str) -> None:
    """The install record must name the engine version it installed."""
    record_path = sandbox / "vep" / "install.json"
    if not record_path.is_file():
        report.add("the_install_record_names_the_engine_version", False,
                   f"there is no install record at {record_path}")
        return
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        report.add("the_install_record_names_the_engine_version", False,
                   f"the install record is unreadable: {exc}")
        return
    recorded = record.get("engine_version", "")
    installed = version_text.split(" ", 1)[-1].split(" ", 1)[0]
    report.add(
        "the_install_record_names_the_engine_version",
        bool(recorded) and recorded == installed,
        f"the record names {recorded!r}; the installed engine reports "
        f"{installed!r}",
    )


def _check_upgrade(sandbox: Path, report: Report) -> None:
    """The upgrade and rollback path, on the installed engine."""
    before = ren(sandbox, ["version"], timeout=120).stdout.strip()
    candidate = sandbox / "engine-build-v2"
    if candidate.exists():
        shutil.rmtree(candidate)
    done = run(
        [sys.executable, "-m", "ren.package_engine",
         "--src", sandbox / "engine", "--dest", candidate,
         "--channel", "stable", "--sha", "prod001-upgrade",
         "--built-at", "2026-01-01"],
        cwd=str(REPO_ROOT), timeout=1800,
    )
    if done.returncode != 0:
        report.add("upgrade_and_rollback", False,
                   f"the candidate engine did not build: {done.stderr[:200]}")
        return
    upgraded = ren(sandbox, ["upgrade", str(candidate)], timeout=900)
    after = ren(sandbox, ["version"], timeout=120).stdout.strip()
    rolled = ren(sandbox, ["rollback"], timeout=900)
    restored = ren(sandbox, ["version"], timeout=120).stdout.strip()
    ok = (upgraded.returncode == 0 and rolled.returncode == 0
          and after != before and restored == before)
    report.add(
        "upgrade_and_rollback",
        ok,
        f"before={before!r} after_upgrade={after!r} after_rollback={restored!r}",
    )


def refusal_for(sandbox: Path) -> str:
    """Why this sandbox may not be used, or "" when it is safe.

    The live install is READ-ONLY here. A sandbox inside it would
    overwrite the very install this check is meant to protect, and a
    sandbox HOME that is the real home would write into the captain's
    shell profile.
    """
    prefix = (sandbox / "vep").resolve()
    if prefix == LIVE_VEP_HOME or LIVE_VEP_HOME in prefix.parents:
        return (f"the sandbox prefix {prefix} is inside the live install "
                f"{LIVE_VEP_HOME}")
    if (sandbox / "home").resolve() == Path.home().resolve():
        return "the sandbox HOME is the real home"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=str(REPO_ROOT),
                        help="the checkout to package (default: this repo)")
    parser.add_argument("--sandbox", default=None,
                        help="sandbox root (default: a fresh directory in /tmp)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the sandbox for inspection")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    parser.add_argument("--no-upgrade", action="store_true",
                        help="skip the upgrade and rollback check")
    args = parser.parse_args()

    src = Path(args.src).resolve()
    sandbox = Path(args.sandbox).resolve() if args.sandbox else Path(
        tempfile.mkdtemp(prefix="ren-clean-install-", dir="/tmp"))
    sandbox.mkdir(parents=True, exist_ok=True)

    # The live install is READ-ONLY here. A sandbox inside it would
    # overwrite the very install this check is meant to protect.
    refusal = refusal_for(sandbox)
    if refusal:
        print(f"refusing: {refusal}", file=sys.stderr)
        return 2
    prefix = (sandbox / "vep").resolve()

    report = Report()
    try:
        print(f"Sandbox: {sandbox}")
        print("Building the release-shaped artifact...")
        archive = build_artifact(src, sandbox)
        print(f"Installing into {prefix} (throwaway HOME)...")
        installed = install(archive, sandbox)
        report.add(
            "runtime_installation",
            installed.returncode == 0,
            f"install_ren.sh exited {installed.returncode}"
            + ("" if installed.returncode == 0
               else f": {(installed.stderr or installed.stdout)[-400:]}"),
        )
        if installed.returncode != 0:
            print(report.render())
            return 1

        doctor_run = ren(sandbox, ["doctor", "--json"], timeout=900)
        doctor = {}
        if doctor_run.returncode == 0:
            try:
                doctor = json.loads(doctor_run.stdout)
            except ValueError as exc:
                report.add("the_doctor_answers_json", False, f"unreadable: {exc}")
        else:
            report.add(
                "the_doctor_answers_json", False,
                f"`ren doctor --json` exited {doctor_run.returncode}: "
                f"{(doctor_run.stderr or '')[-300:]}",
            )

        checks = doctor.get("checks", [])
        required = doctor.get("required_failures") or []
        report.add(
            "the_doctor_reports_no_required_failure",
            not required,
            f"{len(checks)} checks, {len(required)} required failures"
            + (f": {required}" if required else ""),
        )
        groups = [c for c in checks if c.get("name", "").startswith("Python ")]
        report.add(
            "dependency_resolution",
            bool(groups) and all(c.get("ok") for c in groups),
            f"{len(groups)} requirement groups checked: "
            + "; ".join(f"{c['name']}={'ok' if c.get('ok') else 'MISS'}"
                        for c in groups),
        )
        resolve_check = next(
            (c for c in checks if c.get("name") == "Resolve scripting"), None)
        if resolve_check is None:
            report.add("resolve_detection", False,
                       "the doctor has no Resolve scripting check")
        else:
            report.add(
                "resolve_detection", True,
                f"{'ok' if resolve_check.get('ok') else 'not available'}: "
                f"{resolve_check.get('detail', '')[:120]}",
            )

        capabilities = ren(sandbox, ["capabilities", "--check", "--json"],
                           timeout=900)
        measured = {}
        if capabilities.returncode == 0:
            try:
                measured = json.loads(capabilities.stdout)
            except ValueError as exc:
                report.add("capability_packs", False, f"unreadable: {exc}")
        else:
            report.add(
                "capability_packs", False,
                f"`ren capabilities --check` exited {capabilities.returncode}: "
                f"{(capabilities.stderr or '')[-300:]}",
            )
        rows = measured.get("capabilities", [])
        verdicts = {}
        unexplained = []
        for row in rows:
            verdicts[row.get("verdict")] = verdicts.get(row.get("verdict"), 0) + 1
            if row.get("verdict") == "unknown" and not row.get("undetermined"):
                unexplained.append(row.get("id"))
        report.add(
            "capability_packs",
            bool(rows) and not unexplained,
            f"{measured.get('count', len(rows))} capabilities measured: "
            + ", ".join(f"{name}={count}" for name, count in sorted(verdicts.items()))
            + ("" if not unexplained
               else f"; unknown without a reason: {unexplained}"),
        )

        if doctor:
            _check_versions(sandbox, report, doctor)
            _check_no_checkout_assumption(
                sandbox, report, src, json.dumps(doctor))
            _check_fixes_are_runnable(sandbox, report, doctor)
            _check_install_record(
                sandbox, report,
                ren(sandbox, ["version"], timeout=120).stdout.strip())

        # The installed binary must run BY ITSELF: the environment the
        # installer set is deliberately NOT inherited - the engine has to
        # find its own runtime, the way it does on a customer's machine
        # where the launcher is the only thing on PATH.
        alone_env = {
            "HOME": str(sandbox / "home"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        alone = run(
            [sandbox / "vep" / "current" / "bin" / "ren", "--version"],
            env=alone_env, cwd=str(sandbox), timeout=120,
        )
        report.add(
            "the_installed_binary_runs_by_itself",
            alone.returncode == 0 and bool(alone.stdout.strip()),
            f"`current/bin/ren --version` exited {alone.returncode}: "
            f"{alone.stdout.strip() or (alone.stderr or '').strip()[:200]}",
        )
        alone_doctor = run(
            [sandbox / "vep" / "current" / "bin" / "ren", "doctor", "--json"],
            env=alone_env, cwd=str(sandbox), timeout=900,
        )
        report.add(
            "the_installed_binary_finds_its_own_runtime",
            alone_doctor.returncode == 0,
            f"`current/bin/ren doctor` exited {alone_doctor.returncode}: "
            f"{(alone_doctor.stderr or '').strip()[:200] or 'it said nothing'}",
        )

        if not args.no_upgrade:
            _check_upgrade(sandbox, report)
    finally:
        if args.json:
            print(json.dumps(
                {"sandbox": str(sandbox),
                 "checks": [vars(c) for c in report.checks],
                 "passed": report.passed, "failed": report.failed,
                 "manual_only": report.manual_only},
                indent=2))
        else:
            print(report.render())
        if not args.keep:
            shutil.rmtree(sandbox, ignore_errors=True)
        else:
            print(f"\nSandbox kept at {sandbox}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
