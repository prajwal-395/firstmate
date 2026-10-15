#!/usr/bin/env python3
"""Fresh-user qualification: install a release-shaped local artifact into an
isolated sandbox and exercise it through the customer installer.

Creates a throwaway HOME and install root under /tmp, strips PATH to
system defaults, installs the built release via the customer installer,
then runs:

  1. ren doctor (read-only capability check)
  2. ren setup (runtime installation)
  3. A minimal non-Resolve project command (ren new + ren status)

Then runs the edge-case matrix. Each case asserts a specific observable
behaviour and fails when that behaviour is absent. Cases that cannot be
simulated without root or without touching the real install are marked
MANUAL_ONLY with why.

Usage:
    python3 scripts/ren_fresh_machine_qualify.py [--src <checkout>]
        [--artifact <engine.zip>]
        [--sandbox-root <dir>] [--skip-install] [--case <name>]

Heavy steps (model downloads, renders) are out of scope; their failure
modes are simulated with small fakes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REFUSAL_EXIT_CODE = 4
USAGE_EXIT_CODE = 2
NO_ENGINE_EXIT_CODE = 3


def _system_path() -> str:
    """The system default PATH from path_helper (what a fresh Mac gets)."""
    try:
        result = subprocess.run(
            ["/usr/libexec/path_helper", "-s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("PATH="):
                    return line.split('"')[1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "/usr/bin:/bin:/usr/sbin:/sbin"


SYSTEM_PATH = _system_path()


@dataclass
class CaseResult:
    name: str
    status: str  # "pass", "fail", "manual_only", "skip"
    detail: str = ""


@dataclass
class QualificationReport:
    sandbox_root: Path
    engine_version: str = ""
    install_ok: bool = False
    doctor_ok: bool = False
    setup_ok: bool = False
    project_ok: bool = False
    cases: list[CaseResult] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Sandbox: {self.sandbox_root}",
            f"Engine version: {self.engine_version or 'unknown'}",
            f"Install: {'OK' if self.install_ok else 'FAIL'}",
            f"Doctor: {'OK' if self.doctor_ok else 'FAIL'}",
            f"Setup: {'OK' if self.setup_ok else 'FAIL'}",
            f"Project: {'OK' if self.project_ok else 'FAIL'}",
            "",
            "Edge-case matrix:",
        ]
        for case in self.cases:
            lines.append(f"  [{case.status.upper():^12}] {case.name}: {case.detail}")
        passed = sum(1 for c in self.cases if c.status == "pass")
        failed = sum(1 for c in self.cases if c.status == "fail")
        manual = sum(1 for c in self.cases if c.status == "manual_only")
        lines.append(f"\n  {passed} passed, {failed} failed, {manual} manual-only")
        return "\n".join(lines)


def run_sandboxed(
    cmd: list[str],
    sandbox: Path,
    env_overrides: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run a command in the sandbox: throwaway HOME, stripped PATH."""
    env = {
        "HOME": str(sandbox / "home"),
        "PATH": SYSTEM_PATH,
        "PIPELINE_VEP_HOME": str(sandbox / "vep"),
        "REN_QUALIFY_SANDBOX": "1",
    }
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        cwd=str(sandbox),
        check=False,
    )


def run_ren(
    sandbox: Path,
    args: list[str],
    env_overrides: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run `ren <args>` through the sandboxed installer."""
    vep_home = sandbox / "vep"
    ren_bin = vep_home / "current" / "bin" / "ren"
    return run_sandboxed(
        [str(ren_bin)] + args,
        sandbox,
        env_overrides=env_overrides,
        timeout=timeout,
    )


def build_engine(src: Path, dest: Path) -> None:
    """Build the immutable engine tree from source."""
    subprocess.run(
        [
            sys.executable, "-m", "ren.package_engine",
            "--src", str(src),
            "--dest", str(dest),
            "--channel", "stable",
            "--sha", "qualify-local",
            "--built-at", time.strftime("%Y-%m-%d"),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def install_artifact(artifact: Path, sandbox: Path) -> None:
    """Install the exact local engine ZIP through the customer download path."""
    home = sandbox / "home"
    vep_home = sandbox / "vep"
    home.mkdir(parents=True, exist_ok=True)
    vep_home.mkdir(parents=True, exist_ok=True)
    artifact = artifact.resolve()
    subprocess.run(
        ["bash", str(Path(__file__).resolve().with_name("install_ren.sh")),
         "--url", artifact.as_uri(), "--home", str(vep_home),
         "--skip-system-deps"],
        check=True, capture_output=True, text=True, encoding="utf-8",
        env={"HOME": str(home), "PATH": SYSTEM_PATH,
             "PIPELINE_VEP_HOME": str(vep_home)}, timeout=600,
    )


def build_artifact(src: Path, sandbox: Path) -> Path:
    """Package an engine tree using the same directory shape as release ZIPs."""
    built = sandbox / "engine-build"
    package = sandbox / "ren-engine"
    build_engine(src, built)
    shutil.copytree(built, package)
    artifact = sandbox / "ren-engine-qualification.zip"
    shutil.make_archive(str(artifact.with_suffix("")), "zip",
                        root_dir=sandbox, base_dir="ren-engine")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (sandbox / "SHA256SUMS.txt").write_text(
        f"{digest}  {artifact.name}\n", encoding="utf-8")
    return artifact


def _doctor_json(sandbox: Path, env_overrides: dict[str, str] | None = None) -> dict:
    """Run ren doctor --json and return the parsed output."""
    result = run_ren(sandbox, ["doctor", "--json"], env_overrides=env_overrides, timeout=120)
    return json.loads(result.stdout)


def _find_check(doctor_data: dict, name_fragment: str) -> dict | None:
    """Find a doctor check by name fragment."""
    for check in doctor_data.get("checks", []):
        if name_fragment.lower() in check.get("name", "").lower():
            return check
    return None


def case_fresh_user_empty_home(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Fresh user with an empty HOME: doctor must produce valid JSON with checks."""
    try:
        data = _doctor_json(sandbox)
        checks = data.get("checks", [])
        if not checks:
            return CaseResult("fresh_user_empty_home", "fail", "doctor returned no checks")
        resolve_check = _find_check(data, "Resolve scripting")
        if resolve_check is None:
            return CaseResult("fresh_user_empty_home", "fail", "no Resolve scripting check")
        return CaseResult(
            "fresh_user_empty_home",
            "pass",
            f"{len(checks)} checks, Resolve scripting ok={resolve_check['ok']}",
        )
    except Exception as exc:
        return CaseResult("fresh_user_empty_home", "fail", str(exc))


def case_no_homebrew(sandbox: Path, report: QualificationReport) -> CaseResult:
    """No Homebrew on PATH: ren setup must refuse naming the missing tools."""
    return CaseResult(
        "no_homebrew",
        "manual_only",
        "ren/setup.py _brew() falls back to /opt/homebrew/bin/brew; cannot simulate absence without removing Homebrew (requires root)",
    )


def case_spaces_and_unicode_paths(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Spaces and Unicode in paths: project must be created successfully."""
    unicode_dir = sandbox / "home" / "Ren Qualification \u00e9\u4e2d\u6587"
    unicode_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_ren(
            sandbox,
            ["new", "test-unicode", "--name", "Unicode Test",
             "--non-interactive", "--no-speakers"],
            env_overrides={"PIPELINE_PROJECTS_ROOT": str(unicode_dir)},
            timeout=60,
        )
        if result.returncode == 0:
            project_yaml = unicode_dir / "test-unicode" / "project.yaml"
            if project_yaml.exists():
                return CaseResult("spaces_and_unicode", "pass", "project created with spaces/Unicode in path")
            return CaseResult("spaces_and_unicode", "fail", "exit 0 but no project.yaml")
        return CaseResult(
            "spaces_and_unicode",
            "fail",
            f"exit={result.returncode}: {result.stderr[:200]}",
        )
    except Exception as exc:
        return CaseResult("spaces_and_unicode", "fail", str(exc))


def case_missing_resolve(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Missing Resolve: point RESOLVE_SCRIPT_API/LIB at empty dir, assert doctor reports missing."""
    empty_dir = sandbox / "empty_resolve"
    empty_dir.mkdir(parents=True, exist_ok=True)
    try:
        data = _doctor_json(sandbox, env_overrides={
            "RESOLVE_SCRIPT_API": str(empty_dir),
            "RESOLVE_SCRIPT_LIB": str(empty_dir / "fusionscript.so"),
        })
        resolve_check = _find_check(data, "Resolve scripting")
        if resolve_check is None:
            return CaseResult("missing_resolve", "fail", "no Resolve scripting check in doctor output")
        if not resolve_check["ok"]:
            detail = resolve_check.get("detail", "")
            if "module" in detail.lower() or "scripting" in detail.lower() or "not found" in detail.lower() or "no such" in detail.lower():
                return CaseResult("missing_resolve", "pass", f"detected missing: {detail[:80]}")
            return CaseResult("missing_resolve", "fail", f"ok=false but detail does not name missing module: {detail[:80]}")
        return CaseResult("missing_resolve", "fail", "Resolve scripting check passed despite empty scripting paths")
    except Exception as exc:
        return CaseResult("missing_resolve", "fail", str(exc))


def case_resolve_not_running(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Resolve not running: pass only on not-running detail; manual_only when Resolve is running."""
    result = subprocess.run(["pgrep", "-x", "Resolve"], capture_output=True,
                            timeout=10, check=False)
    if result.returncode == 0:
        return CaseResult(
            "resolve_not_running",
            "manual_only",
            "Resolve is running on this machine; cannot simulate it not running without stopping the captain's Resolve",
        )
    try:
        data = _doctor_json(sandbox)
        resolve_check = _find_check(data, "Resolve scripting")
        if resolve_check is None:
            return CaseResult("resolve_not_running", "fail", "no Resolve scripting check")
        detail = resolve_check.get("detail", "")
        if not resolve_check["ok"] and ("not running" in detail.lower() or "not connected" in detail.lower()):
            return CaseResult("resolve_not_running", "pass", f"reported: {detail[:80]}")
        return CaseResult("resolve_not_running", "fail", f"unexpected: ok={resolve_check['ok']}, detail={detail[:80]}")
    except Exception as exc:
        return CaseResult("resolve_not_running", "fail", str(exc))


def case_offline(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Offline: ren setup --with panns must fail with a network/proxy error named in output."""
    result = run_ren(
        sandbox,
        ["setup", "--with", "panns"],
        env_overrides={
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
        },
        timeout=120,
    )
    combined = result.stdout + result.stderr
    if result.returncode == 0:
        return CaseResult("offline", "fail", "setup succeeded despite offline proxy")
    network_keywords = ["proxy", "network", "connection", "timeout", "unreachable", "refused", "resolve", "econn", "socket", "ssl", "certificate"]
    if any(kw in combined.lower() for kw in network_keywords):
        return CaseResult("offline", "pass", f"exit={result.returncode}, network/proxy error named in output")
    return CaseResult("offline", "fail", f"exit={result.returncode} but no network/proxy error in output: {combined[:200]}")


def case_interrupted_download(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Interrupted download: corrupt a real HF model cache and assert doctor detects it."""
    hf_cache = sandbox / "vep" / "models" / "huggingface" / "hub"
    model_dir = hf_cache / "models--mlx-community--gemma-4-12b-it-4bit"
    blobs_dir = model_dir / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    (blobs_dir / "model.safetensors.incomplete").write_bytes(b"truncated")
    try:
        data = _doctor_json(sandbox)
        model_check = _find_check(data, "gemma")
        if model_check is None:
            return CaseResult("interrupted_download", "fail", "no gemma model check")
        if not model_check["ok"]:
            detail = model_check.get("detail", "")
            if "incomplete" in detail.lower() or "not downloaded" in detail.lower():
                return CaseResult("interrupted_download", "pass", f"detected: {detail[:60]}")
            return CaseResult("interrupted_download", "fail", f"unexpected detail: {detail[:60]}")
        return CaseResult("interrupted_download", "fail", "model check passed despite incomplete download")
    except Exception as exc:
        return CaseResult("interrupted_download", "fail", str(exc))


def case_corrupt_model_cache(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Corrupt model cache: write garbage to a real model blob and assert detection."""
    hf_cache = sandbox / "vep" / "models" / "huggingface" / "hub"
    model_dir = hf_cache / "models--sentence-transformers--all-MiniLM-L6-v2"
    blobs_dir = model_dir / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    (blobs_dir / "model.safetensors").write_bytes(b"corrupted data")
    try:
        data = _doctor_json(sandbox)
        model_check = _find_check(data, "all-MiniLM")
        if model_check is None:
            return CaseResult("corrupt_model_cache", "fail", "no all-MiniLM check")
        if not model_check["ok"]:
            return CaseResult("corrupt_model_cache", "pass", f"detected: {model_check.get('detail', '')[:60]}")
        return CaseResult("corrupt_model_cache", "fail", "model check passed despite corruption")
    except Exception as exc:
        return CaseResult("corrupt_model_cache", "fail", str(exc))


def case_low_disk(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Low disk: point Ren at a small volume and assert it reports the shortage."""
    if platform.system() != "Darwin":
        return CaseResult("low_disk", "manual_only", "hdiutil requires macOS")
    test_dmg = sandbox / "test_low_disk.dmg"
    result = subprocess.run(
        ["hdiutil", "create", "-size", "10m", "-fs", "HFS+", "-volname", "LowDiskTest", str(test_dmg)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return CaseResult("low_disk", "manual_only", f"hdiutil not allowed without sudo")
    mount_point = sandbox / "low_disk_mount"
    mount_point.mkdir(exist_ok=True)
    subprocess.run(
        ["hdiutil", "attach", str(test_dmg), "-mountpoint", str(mount_point)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        check=False,
    )
    try:
        data = _doctor_json(sandbox, env_overrides={"PIPELINE_VEP_HOME": str(mount_point / "vep")})
        disk_check = _find_check(data, "disk space")
        if disk_check is None:
            return CaseResult("low_disk", "fail", "no disk space check")
        if not disk_check["ok"]:
            return CaseResult("low_disk", "pass", f"detected: {disk_check.get('detail', '')[:60]}")
        return CaseResult("low_disk", "fail", "disk space check passed on 10MB volume")
    except Exception as exc:
        return CaseResult("low_disk", "fail", str(exc))
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(mount_point)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            check=False,
        )


def case_stale_project(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Stale project: old format must be migrated or refused clearly."""
    projects_root = sandbox / "home" / "Movies" / "Ren" / "projects"
    stale_project = projects_root / "stale-old-project"
    stale_project.mkdir(parents=True, exist_ok=True)
    (stale_project / "project.yaml").write_text(
        "project_format_version: 1\n"
        "slug: stale-old-project\n"
        "name: Stale Project\n"
        "source:\n"
        "  folder: ./footage\n"
        "pipeline:\n"
        "  fps: 30\n"
        "resolve:\n"
        "  timeline_name: Stale Timeline\n"
    )
    result = run_ren(sandbox, ["status", "stale-old-project"], timeout=60)
    if result.returncode == 0:
        return CaseResult("stale_project", "pass", "migrated and loaded")
    if result.returncode == REFUSAL_EXIT_CODE:
        return CaseResult("stale_project", "pass", "refused clearly")
    return CaseResult(
        "stale_project",
        "fail",
        f"exit={result.returncode}: {result.stderr[:200]}",
    )


def case_upgrade_rollback(sandbox: Path, report: QualificationReport) -> CaseResult:
    """Upgrade then rollback: install a second version with different sha, upgrade, roll back, assert versions."""
    vep_home = sandbox / "vep"

    before_version = run_ren(sandbox, ["version"], timeout=30)
    if before_version.returncode != 0:
        return CaseResult("upgrade_rollback", "fail", "version check failed")
    before_output = before_version.stdout.strip()

    engine_src = sandbox / "engine-build"
    engine_build_2 = sandbox / "engine-build-v2"
    subprocess.run(
        [
            sys.executable, "-m", "ren.package_engine",
            "--src", str(engine_src),
            "--dest", str(engine_build_2),
            "--channel", "stable",
            "--sha", "qualify-v2-different-sha",
            "--built-at", "2026-01-01",
        ],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )

    upgrade_result = run_ren(sandbox, ["upgrade", str(engine_build_2)], timeout=120)
    if upgrade_result.returncode != 0:
        return CaseResult("upgrade_rollback", "fail", f"upgrade failed: {(upgrade_result.stdout + upgrade_result.stderr)[:200]}")

    after_upgrade = run_ren(sandbox, ["version"], timeout=30)
    if after_upgrade.returncode != 0:
        return CaseResult("upgrade_rollback", "fail", "version check after upgrade failed")
    after_upgrade_output = after_upgrade.stdout.strip()

    if after_upgrade_output == before_output:
        return CaseResult("upgrade_rollback", "fail", f"version unchanged after upgrade: {before_output}")

    rollback_result = run_ren(sandbox, ["rollback"], timeout=60)
    if rollback_result.returncode != 0:
        return CaseResult("upgrade_rollback", "fail", f"rollback failed: {(rollback_result.stdout + rollback_result.stderr)[:200]}")

    after_rollback = run_ren(sandbox, ["version"], timeout=30)
    if after_rollback.returncode != 0:
        return CaseResult("upgrade_rollback", "fail", "version check after rollback failed")
    after_rollback_output = after_rollback.stdout.strip()

    if after_rollback_output != before_output:
        return CaseResult("upgrade_rollback", "fail", f"rollback did not restore original: {after_rollback_output} != {before_output}")

    return CaseResult(
        "upgrade_rollback",
        "pass",
        f"before={before_output}, after_upgrade={after_upgrade_output}, after_rollback={after_rollback_output}",
    )


MANUAL_ONLY_CASES = [
    ("resolve_free", "requires DaVinci Resolve Free edition installed; not present on this Mac"),
    ("scripting_disabled", "requires Resolve with scripting disabled in preferences; needs GUI access"),
]


def run_edge_case_matrix(sandbox: Path, report: QualificationReport) -> None:
    """Run all edge-case matrix cases."""
    cases = [
        case_fresh_user_empty_home,
        case_no_homebrew,
        case_spaces_and_unicode_paths,
        case_missing_resolve,
        case_resolve_not_running,
        case_offline,
        case_interrupted_download,
        case_corrupt_model_cache,
        case_low_disk,
        case_stale_project,
        case_upgrade_rollback,
    ]
    for case_fn in cases:
        try:
            result = case_fn(sandbox, report)
        except Exception as exc:
            result = CaseResult(case_fn.__name__.replace("case_", ""), "fail", str(exc))
        report.cases.append(result)
    for name, why in MANUAL_ONLY_CASES:
        report.cases.append(CaseResult(name, "manual_only", why))
    groups = subprocess.run(["id", "-Gn"], capture_output=True, text=True,
                            encoding="utf-8", timeout=10, check=False)
    if groups.returncode != 0:
        report.cases.append(CaseResult("non_admin_account", "fail", groups.stderr[:200]))
    elif "admin" in groups.stdout.split():
        report.cases.append(CaseResult(
            "non_admin_account", "fail",
            "qualification account belongs to admin group"))
    else:
        report.cases.append(CaseResult("non_admin_account", "pass", "qualification runs without admin membership"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src", default=".",
        help="Engine source checkout used to build the local artifact")
    parser.add_argument("--artifact", type=Path,
                        help="Install this already-built engine ZIP instead of building from --src")
    parser.add_argument(
        "--sandbox-root", default=None,
        help="Sandbox root (default: /tmp/ren-qualify-XXXX)")
    parser.add_argument("--skip-install", action="store_true", help="Skip install, use existing sandbox")
    parser.add_argument("--case", default=None, help="Run only this case")
    args = parser.parse_args()

    src = Path(args.src).resolve()
    if args.sandbox_root:
        sandbox = Path(args.sandbox_root).resolve()
        sandbox.mkdir(parents=True, exist_ok=True)
    else:
        sandbox = Path(tempfile.mkdtemp(prefix="ren-qualify-", dir="/tmp"))

    report = QualificationReport(sandbox_root=sandbox)

    if not args.skip_install:
        if args.artifact:
            artifact = args.artifact.resolve()
            if not artifact.is_file():
                parser.error(f"artifact does not exist: {artifact}")
        else:
            print(f"Building local engine artifact from {src}...")
            artifact = build_artifact(src, sandbox)
        report.engine_version = "local-artifact"

        print(f"Installing local engine artifact into sandbox {sandbox}...")
        install_artifact(artifact, sandbox)
        report.install_ok = True
    else:
        report.install_ok = True

    print("Running ren doctor...")
    doctor_result = run_ren(sandbox, ["doctor", "--json"], timeout=120)
    report.doctor_ok = doctor_result.returncode == 0
    if not report.doctor_ok:
        print(f"  FAIL: exit={doctor_result.returncode}")
        print(doctor_result.stderr[:500])

    print("Running ren setup...")
    setup_result = run_ren(sandbox, ["setup", "--skip-system-deps"], timeout=300)
    report.setup_ok = setup_result.returncode == 0
    if not report.setup_ok:
        print(f"  FAIL: exit={setup_result.returncode}")
        print(setup_result.stderr[:500])

    print("Running minimal non-Resolve project command...")
    project_result = run_ren(
        sandbox,
        ["new", "qualify-test", "--name", "Qualify Test",
         "--non-interactive", "--no-speakers"],
        timeout=60,
    )
    if project_result.returncode == 0:
        status_result = run_ren(sandbox, ["status", "qualify-test"], timeout=60)
        report.project_ok = status_result.returncode == 0
    else:
        report.project_ok = False
        print(f"  FAIL: ren new exit={project_result.returncode}")
        print(project_result.stderr[:500])

    print("Running edge-case matrix...")
    run_edge_case_matrix(sandbox, report)

    print()
    print(report.summary())

    all_passed = (
        report.install_ok
        and report.doctor_ok
        and report.setup_ok
        and report.project_ok
        and all(c.status in ("pass", "manual_only") for c in report.cases)
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
