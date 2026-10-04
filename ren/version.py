"""Ren's version: the ONE source of what build this is.

`pyproject.toml` takes its version from here (`version = {attr =
"ren.version.__version__"}`), `ren --version` prints it, `ren doctor`
reports it, and every created project stamps it into `project.yaml` -
so an executable, a project and a support report all name the same
build. Nothing else in the repo states a version number.

Build metadata (the commit, when it was built, which channel) is baked
in at packaging time by `ren/package_engine.py`, which writes the
generated sibling `ren/_build.py` into the BUILT tree only - never
into this checkout. A checkout run reads as an unbuilt `+dev` build
with the live git sha when git can answer, so `ren --version` off a
developer checkout still identifies its commit.

Standard library only: `ren --version` must work before the ML
environment exists, so nothing here imports the engine.
"""

from __future__ import annotations

import os
import re
import subprocess

__version__ = "0.1.0"

#: The supported Python minor. `pyproject.toml`, `ren doctor` and its
#: JSON build record derive their compatibility declaration from this.
PYTHON_VERSION = (3, 12)
REQUIRES_PYTHON = (
    f">={PYTHON_VERSION[0]}.{PYTHON_VERSION[1]},"
    f"<{PYTHON_VERSION[0]}.{PYTHON_VERSION[1] + 1}"
)

_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$",
    re.ASCII,
)
_BUILD_PART = re.compile(r"^[0-9A-Za-z-]+$", re.ASCII)


def _generated_build() -> dict:
    """The packaged build record, or {} on a plain checkout.

    Imported lazily so this module stays importable where the
    generated sibling was never written.
    """
    try:
        from ren._build import BUILD_CHANNEL, BUILD_DATE, BUILD_SHA
    except ImportError:
        return {}
    return {
        "sha": BUILD_SHA,
        "built_at": BUILD_DATE,
        "channel": BUILD_CHANNEL,
    }


def _git_sha() -> str:
    """The checkout's own commit, or "" when git cannot answer."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True, encoding="utf-8", timeout=10, check=False,
            cwd=os.path.dirname(os.path.abspath(__file__)))
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def build_info() -> dict:
    """Build identity and the Python compatibility range.

    A packaged tree reports its baked record. A checkout reports
    `channel: dev`, the live git sha (or "" with no git), and no
    build date - it was never built.
    """
    generated = _generated_build()
    if generated:
        return {"version": __version__, **generated,
                "requires_python": REQUIRES_PYTHON}
    return {
        "version": __version__,
        "sha": _git_sha(),
        "built_at": "",
        "channel": "dev",
        "requires_python": REQUIRES_PYTHON,
    }


def version_string() -> str:
    """`ren --version`'s one line: SemVer plus build metadata.

    `0.1.0+abc123def456.dev` off a checkout at that commit,
    `0.1.0+abc123def456.stable.2026-10-04` off a packaged build.
    """
    return format_version(build_info())


def format_version(info: dict) -> str:
    """Format the same version identity for a packaged build record."""
    version = info["version"]
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        raise ValueError(f"Ren version is not SemVer: {version!r}")
    parts = [version]
    metadata = []
    for field in ("sha", "channel", "built_at"):
        value = info[field]
        if not value:
            continue
        if not isinstance(value, str) or _BUILD_PART.fullmatch(value) is None:
            raise ValueError(
                f"Ren build metadata {field} must contain only letters, "
                f"numbers and hyphens: {value!r}")
        metadata.append(value)
    suffix = ".".join(metadata)
    if suffix:
        parts.append("+" + suffix)
    return "".join(parts)
