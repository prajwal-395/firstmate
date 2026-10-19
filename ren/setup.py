"""Install Ren's per-user runtime and system prerequisites.

The bootstrap script installs Homebrew when needed and installs the
versioned engine. This command then owns the mutable, per-user runtime:
uv-managed CPython, the locked ML venv, the shared Node store, default
project/library folders, and explicitly requested verified model packs.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from ren import doctor
from ren.engine_root import require_engine_root

PACKS = {
    "panns": ("scripts/install_panns.sh", "INSTALL_PANNS_PYTHON"),
    "mfa": ("scripts/install_mfa.sh", "INSTALL_MFA_PYTHON"),
    "ecapa": ("scripts/install_ecapa.sh", "INSTALL_ECAPA_PYTHON"),
    "deepfilter": ("scripts/install_deepfilternet.sh", "INSTALL_DEEPFILTERNET_PYTHON"),
}

PACK_BYTES = {
    "panns": 327_428_481,        # PANNS_CHECKPOINT_SIZE (shared_environment.py)
    "mfa": 200_000_000,          # declared: acoustic + dictionary + G2P models
    "ecapa": 50_000_000,         # declared: the speechbrain ECAPA checkpoint
    "deepfilter": 150_000_000,   # declared: the binary plus its ~100 MB weights
}
"""Each pack's download footprint. PANNs is measured; the rest are
declared allowances - the install scripts verify hashes, not sizes."""

VENV_DIRNAME = "venv-py312"
PYTHON_DIRNAME = "python"
INSTALL_RECORD = "install.json"
PROFILE_START = "# >>> Ren managed PATH >>>"
PROFILE_END = "# <<< Ren managed PATH <<<"


class SetupFailure(RuntimeError):
    """A setup refusal with a user-actionable message."""


def _run(command: list[str], *, env: dict, label: str) -> None:
    print(f"\n==> {label}", flush=True)
    try:
        result = subprocess.run(command, env=env, check=False)
    except OSError as exc:
        raise SetupFailure(f"could not start {command[0]}: {exc}") from exc
    if result.returncode:
        raise SetupFailure(f"{label} failed with exit code {result.returncode}")


def _output(command: list[str], *, env: dict, label: str) -> str:
    try:
        result = subprocess.run(
            command, env=env, check=False, capture_output=True, encoding="utf-8"
        )
    except OSError as exc:
        raise SetupFailure(f"could not start {command[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise SetupFailure(f"{label} failed with exit code {result.returncode}{suffix}")
    return result.stdout.strip()


def _home() -> Path:
    from library.tools.shared_environment import vep_home

    return vep_home().expanduser().resolve()


def _brew(env: dict) -> tuple[str, str]:
    brew = shutil.which("brew")
    if not brew:
        for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                brew = candidate
                break
    if not brew:
        raise SetupFailure(
            "Homebrew is not installed. Start with the Ren installer: "
            "https://github.com/prajwal-395/video_editing_pilot/blob/main/scripts/install_ren.sh"
        )
    prefix = _output([brew, "--prefix"], env=env, label="find Homebrew")
    return brew, prefix


def _ensure_system_tools(env: dict, install: bool) -> tuple[str, str]:
    brew, prefix = _brew(env)
    if install:
        _run(
            [brew, "install", "uv", "node", "ffmpeg"],
            env=env,
            label="install uv, Node.js, and ffmpeg with Homebrew",
        )
    path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(
        part for part in (f"{prefix}/bin", f"{prefix}/sbin", path) if part
    )
    missing = [
        name
        for name in ("uv", "node", "npm", "ffmpeg", "ffprobe")
        if not shutil.which(name, path=env["PATH"])
    ]
    if missing:
        fix = (
            "Run `ren setup` with Homebrew available"
            if install
            else "install these with Homebrew, then run `ren setup` again"
        )
        raise SetupFailure(f"required tools are missing ({', '.join(missing)}); {fix}")
    return brew, prefix


def _profile_path() -> Path:
    shell = Path(os.environ.get("SHELL", "/bin/zsh")).name
    filename = {"zsh": ".zprofile", "bash": ".bash_profile"}.get(shell, ".profile")
    config_dir = Path(os.environ.get("ZDOTDIR") or Path.home()).expanduser()
    return config_dir / filename


def _profile_block(app_bin: Path, brew_prefix: str) -> str:
    entries = [str(app_bin), f"{brew_prefix}/bin", f"{brew_prefix}/sbin"]
    unique = list(dict.fromkeys(entries))
    value = ":".join(shlex.quote(entry) for entry in unique)
    return f'{PROFILE_START}\nexport PATH={value}:"$PATH"\n{PROFILE_END}'


def _write_profile(profile: Path, block: str) -> None:
    profile.parent.mkdir(parents=True, exist_ok=True)
    content = profile.read_text(encoding="utf-8") if profile.exists() else ""
    start = content.find(PROFILE_START)
    if start >= 0:
        end = content.find(PROFILE_END, start)
        if end < 0:
            raise SetupFailure(
                f"{profile} has an incomplete Ren PATH block; edit it before setup"
            )
        end += len(PROFILE_END)
        content = content[:start] + block + content[end:]
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        if content and not content.endswith("\n\n"):
            content += "\n"
        content += block + "\n"
    profile.write_text(content, encoding="utf-8")


def _launcher(home: Path, runtime_python: Path) -> str:
    return "\n".join(
        (
            "#!/bin/sh",
            "set -eu",
            f"REN_HOME={shlex.quote(str(home))}",
            'export PIPELINE_VEP_HOME="$REN_HOME"',
            f'if [ -x "$REN_HOME/{VENV_DIRNAME}/bin/python3" ]; then',
            f'  export PIPELINE_PYTHON="$REN_HOME/{VENV_DIRNAME}/bin/python3"',
            "else",
            f"  export PIPELINE_PYTHON={shlex.quote(str(runtime_python))}",
            "fi",
            'export HF_HOME="${HF_HOME:-$REN_HOME/models/huggingface}"',
            'export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"',
            'export TORCH_HOME="${TORCH_HOME:-$REN_HOME/models/torch}"',
            'export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$REN_HOME/models/cache}"',
            'export npm_config_cache="${npm_config_cache:-$REN_HOME/cache/npm}"',
            'export PIPELINE_MFA_MODELS="${PIPELINE_MFA_MODELS:-$REN_HOME/models/mfa}"',
            'exec "$REN_HOME/current/bin/ren" "$@"',
            "",
        )
    )


def _install_launcher(home: Path, runtime_python: Path, prefix: str) -> Path:
    app_bin = home / "bin"
    app_bin.mkdir(parents=True, exist_ok=True)
    launcher = app_bin / "ren"
    desired = _launcher(home, runtime_python)
    if launcher.exists() and not launcher.is_file():
        raise SetupFailure(f"refusing to replace non-file launcher path {launcher}")
    current = launcher.read_text(encoding="utf-8") if launcher.is_file() else None
    if (
        current is not None
        and current != desired
        and not current.startswith("#!/bin/sh\nset -eu\nREN_HOME=")
    ):
        raise SetupFailure(f"refusing to replace an unrecognized file at {launcher}")
    tmp = launcher.with_name(f".ren.{os.getpid()}.tmp")
    tmp.write_text(desired, encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, launcher)
    profile = _profile_path()
    _write_profile(profile, _profile_block(app_bin, prefix))
    return launcher


def _runtime_python(home: Path, uv: str, env: dict) -> Path:
    python_dir = home / PYTHON_DIRNAME
    python_dir.mkdir(parents=True, exist_ok=True)
    env["UV_PYTHON_INSTALL_DIR"] = str(python_dir)
    _run(
        [uv, "python", "install", "3.12", "--install-dir", str(python_dir)],
        env=env,
        label="install Ren's Python 3.12 runtime",
    )
    found = _output(
        [uv, "python", "find", "--managed-python", "--no-project", "3.12"],
        env=env,
        label="locate Ren's Python 3.12 runtime",
    )
    python = Path(found).expanduser().resolve()
    version = _output(
        [str(python), "--version"], env=env, label="check Ren's Python runtime"
    )
    if not version.startswith("Python 3.12."):
        raise SetupFailure(f"expected Python 3.12, found {version} at {python}")
    return python


def _setup_runtime(
    home: Path, engine: Path, uv: str, runtime_python: Path, env: dict
) -> Path:
    venv = home / VENV_DIRNAME
    venv_python = venv / "bin" / "python3"
    env["UV_CACHE_DIR"] = str(home / "cache" / "uv")
    if venv.exists():
        # A managed venv that still answers is reused, and the sync below
        # is then a fast no-op; one that does not is cleared and rebuilt,
        # so `ren setup` repairs a broken runtime instead of refusing.
        if venv_python.is_file():
            try:
                _output(
                    [str(venv_python), "--version"],
                    env=env,
                    label="check Ren's Python environment",
                )
            except SetupFailure:
                shutil.rmtree(venv)
        else:
            shutil.rmtree(venv)
    if not venv.exists():
        _run(
            [uv, "venv", "--python", str(runtime_python), str(venv)],
            env=env,
            label="create Ren's Python environment",
        )
    lock = engine / "requirements" / "lock" / "macos-arm64-py312.txt"
    if not lock.is_file():
        raise SetupFailure(
            f"Ren's verified Apple Silicon dependency lock is missing: {lock}"
        )
    _run(
        [uv, "pip", "sync", "--python", str(venv_python), str(lock)],
        env=env,
        label="install Ren's hash-locked Python dependencies",
    )
    return venv_python


def build_ren_native(engine_root: Path, venv_bin_dir: Path, env: dict) -> int | None:
    """Run the ren_native build script; return its exit code, or None
    when it could not be started.

    The build lands in the machine's per-tree cache
    (`library/tools/ren_native_cache.py`), keyed by this engine's
    source fingerprint - never in the venv, so checkouts on other
    trees keep their own entries.

    Exit 4 is the script's "keep the Python backend" (no cargo, no
    maturin, no Rust sources); any other non-zero is a build that ran
    and failed. Both are the caller's to report, never to raise: the
    Rust backend is an optimisation and the Python implementation is
    the fallback, so a build that cannot run degrades the machine, it
    does not break the install.
    """
    script = engine_root / "scripts" / "build_ren_native.sh"
    if not script.is_file():
        return None
    try:
        sys.path.insert(0, str(engine_root))
        from library.tools import ren_native_cache
        cache = str(ren_native_cache.cache_dir())
    except Exception:  # noqa: BLE001 - the script usage-errors without it
        cache = ""
    try:
        done = subprocess.run(
            ["bash", str(script), str(venv_bin_dir), cache],
            env=env, capture_output=True, encoding="utf-8",
            timeout=1800, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode


def _build_ren_native(engine: Path, venv_python: Path, env: dict) -> None:
    """Rebuild the ren_native extension from the engine's Rust sources,
    reporting the outcome. Never fails the setup: the Python edit
    backend is the fallback, and `ren doctor` names a missing build."""
    code = build_ren_native(engine, venv_python.parent, env)
    if code == 0:
        print("ren_native: built from the engine's Rust sources.")
        try:
            import sys
            sys.path.insert(0, str(engine))
            from library.tools import native_refresh
            native_refresh.record(engine, "ren_native")
        except Exception:  # noqa: BLE001 - recording never fails setup
            pass
    elif code == 4:
        print("ren_native: not built - no Rust toolchain or no Rust "
              "sources; the Python edit backend runs.")
    elif code is None:
        print("ren_native: could not run the build; the Python edit "
              "backend runs.")
    else:
        print(f"ren_native: build failed (exit {code}); the Python edit "
              "backend runs.")


def _build_fframes_runtime(engine: Path, env: dict) -> None:
    """Build the default graphics renderer's binary, reporting the outcome.

    Never fails the setup: without the binary the engine seam falls back
    to the historical default and `ren doctor` names the missing build.
    """
    script = engine / "scripts" / "build_fframes_runtime.sh"
    if not script.is_file():
        return
    try:
        done = subprocess.run(
            ["bash", str(script)],
            env=env, capture_output=True, encoding="utf-8",
            timeout=1800, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        print("fframes: could not run the build; the historical "
              "default renders.")
        return
    if done.returncode == 0:
        print("fframes: built the default renderer's binary.")
    else:
        print("fframes: build failed; the historical default renders "
              "(`ren doctor` names the missing build).")


def _make_user_folders(engine: Path) -> None:
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    from library.tools import paths

    for label, path in (
        ("projects", paths.PROJECTS_ROOT),
        ("sound-effect library", paths.SFX_LIBRARY),
        ("music library", paths.MUSIC_LIBRARY),
    ):
        Path(path).expanduser().mkdir(parents=True, exist_ok=True)
        print(f"{label}: {path}")


def _install_record(
    home: Path, prefix: str, profile: Path, runtime_python: Path
) -> None:
    record = {
        "schema_version": 1,
        "home": str(home),
        "homebrew_prefix": prefix,
        "shell_profile": str(profile),
        "python_runtime": str(runtime_python),
        "models": str(home / "models"),
    }
    path = home / INSTALL_RECORD
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _check_disk_space(free: int, needed: int, what: str) -> None:
    """Refuse before a download when its estimate would consume the reserve."""
    from library.tools import storage_lifecycle

    try:
        storage_lifecycle.require_free_space(needed, path=str(_home()))
    except storage_lifecycle.lifecycle.LifecycleRefused as refused:
        raise SetupFailure(
            f"not enough free disk space for {what}: "
            f"{doctor.gb(free)} free; {doctor.gb(needed)} estimated "
            f"plus the configured reserve; {refused}; free up that space and "
            f"re-run `ren setup`"
        ) from refused


def _run_packs(engine: Path, packs: list[str], python: Path, env: dict) -> None:
    variables = {
        "panns": "INSTALL_PANNS_PYTHON",
        "mfa": "INSTALL_MFA_PYTHON",
        "ecapa": "INSTALL_ECAPA_PYTHON",
        "deepfilter": "INSTALL_DEEPFILTERNET_PYTHON",
    }
    for name in packs:
        script, _ = PACKS[name]
        _check_disk_space(
            doctor.free_bytes(_home()), PACK_BYTES[name], f"the {name} pack"
        )
        pack_env = dict(env)
        pack_env[variables[name]] = str(python)
        pack_env["XDG_CACHE_HOME"] = str(_home() / "models" / "cache")
        _run(
            ["bash", str(engine / script)],
            env=pack_env,
            label=f"install verified {name} capability pack",
        )


def _configure_host() -> None:
    """Install the marker hook for every detected chat harness.

    The hook is disk-only and idempotent, so configuring a detected
    host is safe to do during setup: it lists pulled Resolve marker
    notes at session start and never probes a closed app. With no host
    detected, setup says so and stops - guessing a host would write a
    hook into a config the person never opens.
    """
    from ren import hosts, setup_hooks

    found = hosts.detected()
    if not found:
        print("\n==> no chat harness detected")
        print("    install Claude Code, OpenCode or Codex and sign in, then "
              "run `ren setup-hooks --list`")
        return
    print("\n==> configure the detected chat harness")
    for adapter in found:
        code = setup_hooks.install(adapter.name, write=True)
        if code:
            print(f"    {adapter.label}: re-run `ren setup-hooks "
                  f"--app {adapter.name} --write`")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren setup",
        description="Install Ren's per-user runtime and prerequisites.",
    )
    parser.add_argument(
        "--with",
        dest="packs",
        action="append",
        choices=tuple(PACKS),
        metavar="PACK",
        help="install a verified optional pack; repeatable",
    )
    parser.add_argument(
        "--skip-system-deps",
        action="store_true",
        help="do not call Homebrew; require uv, Node.js, npm, ffmpeg, and ffprobe on PATH",
    )
    args = parser.parse_args(argv)

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        print(
            "ren setup: Ren's packaged runtime currently supports Apple Silicon macOS only.",
            file=sys.stderr,
        )
        return 2

    try:
        engine = require_engine_root()
        home = _home()
        home.mkdir(parents=True, exist_ok=True)
        _check_disk_space(
            doctor.free_bytes(home),
            sum(doctor.disk_footprint(engine.joinpath(*doctor.LOCK_PATH)).values()),
            "Ren's runtime, models and scratch",
        )
        env = dict(os.environ)
        env["PIPELINE_VEP_HOME"] = str(home)
        env["UV_CACHE_DIR"] = str(home / "cache" / "uv")
        env["npm_config_cache"] = str(home / "cache" / "npm")
        env["HF_HOME"] = env.get("HF_HOME", str(home / "models" / "huggingface"))
        env["HF_HUB_CACHE"] = env.get("HF_HUB_CACHE", str(Path(env["HF_HOME"]) / "hub"))
        env["TORCH_HOME"] = env.get("TORCH_HOME", str(home / "models" / "torch"))
        env["PIPELINE_MFA_MODELS"] = env.get(
            "PIPELINE_MFA_MODELS", str(home / "models" / "mfa")
        )

        _, prefix = _ensure_system_tools(env, install=not args.skip_system_deps)
        uv = shutil.which("uv", path=env["PATH"])
        if not uv:
            raise SetupFailure(
                "uv is not available after checking Homebrew's bin directory"
            )
        runtime_python = _runtime_python(home, uv, env)
        _install_launcher(home, runtime_python, prefix)
        _install_record(home, prefix, _profile_path(), runtime_python)
        venv_python = _setup_runtime(home, engine, uv, runtime_python, env)
        env["PIPELINE_PYTHON"] = str(venv_python)
        _build_ren_native(engine, venv_python, env)
        _build_fframes_runtime(engine, env)

        installer = engine / "scripts" / "install_node_deps.sh"
        env["INSTALL_NODE_DEPS_PYTHON"] = str(venv_python)
        _run(
            ["bash", str(installer), str(engine / "remotion-subtitles")],
            env=env,
            label="install the lockfile-keyed renderer dependencies",
        )

        _make_user_folders(engine)
        _run_packs(engine, args.packs or [], venv_python, env)
        _configure_host()

        launcher = home / "bin" / "ren"
        print("\n==> verify the installed machine", flush=True)
        result = subprocess.run([str(launcher), "doctor"], env=env, check=False)
        if result.returncode:
            raise SetupFailure(
                f"ren doctor returned {result.returncode}; fix its required failures and re-run `ren setup`"
            )
        print("\nRen setup complete. Optional needs appear as MISS in `ren doctor`.")
        print(
            'Open a new terminal, then run `ren doctor` and `ren new <slug> --name "<name>"`.'
        )
        return 0
    except (SetupFailure, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ren setup: refused - {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
