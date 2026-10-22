"""The backend conformance check: ONE recorded request, run through EACH
execution backend, compared on content, timing and preserved state.

INFRA-005: "Run identical supported semantic operations against different
execution backends or controlled simulations. Compare resulting timing,
content, and preserved state. Distinguish equivalent operations from
merely similar operations. Make portability a verified property rather
than an assumption."

The backends compared here are the ones a person can actually reach for
the same request: the NATIVE Rust front door (`native/ren-cli`, the
`ren` binary) and the PYTHON CLI (`python -m ren.cli`). Both serve
`ren info`, `ren status` and `ren check`, and both are supposed to
answer identically - the crate's own README says the native verb
"mirrors the Python function it replaces, so its stdout, stderr and
exit code are identical".

**What this compares, and what it does not.**

- CONTENT: stdout, stderr and the exit code, byte for byte.
- PRESERVED STATE: the project tree the operation is about, before and
  after the run - every file's path and content hash. A read-only verb
  that leaves the project changed on one side and not on the other is a
  disagreement, even when the prose matches.
- TIMING: how long each backend took. Reported, never gated: the two
  sides are allowed to differ in speed, and a speed difference is not a
  conformance failure.

**Recorded, not live.** Every request is a recorded fixture under
`tests/fixtures/backend_conformance/requests/`: a descriptor file and
the project files it names. No request touches Resolve, renders
anything, or reads the captain's projects: the harness builds each
project in a scratch directory and points every path - the projects
root, the two asset libraries, the config directory, the state
directory - at that scratch. The footage a request needs is SYNTHESISED
from a recorded lavfi source, so the fixture is text and the machine
carries no binary.

**Unmeasured is never zero.** A backend that could not be run (no
native binary built, an interpreter that is not there) is reported as
`unmeasured`, never as an agreement and never as a disagreement: the
denominator of the disagreements is the number of requests where BOTH
backends ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[2]
"""The checkout this module is read from: the engine both backends serve."""

FIXTURE_ROOT = ENGINE_ROOT / "tests" / "fixtures" / "backend_conformance"
"""Where the recorded request set lives."""

REQUEST_DESCRIPTOR = "request"
"""The descriptor file's name, inside a request's directory."""

COMPARED_FIELDS = ("exit_code", "stdout", "stderr", "state")
"""What a disagreement is: the outcome fields that must match."""


# ── The recorded request ─────────────────────────────────────────────


@dataclass(frozen=True)
class Request:
    """One recorded request: the argv, the env, and the project it runs on."""

    id: str
    argv: tuple[str, ...]
    env: dict[str, str]
    files: tuple[str, ...]
    """The fixture files that become the project, relative to the root."""
    footage: tuple[tuple[str, str], ...]
    omit: tuple[str, ...]
    note: str
    directory: Path


def parse_descriptor(directory: Path, root: Path) -> Request:
    """Read one request's descriptor file.

    The descriptor is line based (`key: value`) so both this module and
    the Rust crate's own test can read it without a JSON parser, and so
    a recorded request stays readable in a diff.
    """
    path = directory / REQUEST_DESCRIPTOR
    if not path.is_file():
        raise FileNotFoundError(f"no request descriptor at {path}")
    argv: list[str] = []
    env: dict[str, str] = {}
    files: list[str] = []
    footage: list[tuple[str, str]] = []
    omit: list[str] = []
    note = ""
    identifier = directory.name
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "id":
            identifier = value
        elif key == "arg":
            argv.append(value)
        elif key == "env":
            name, _, setting = value.partition("=")
            env[name.strip()] = setting.strip()
        elif key == "file":
            files.append(value)
        elif key == "footage":
            target, _, source = value.partition("|")
            footage.append((target.strip(), source.strip()))
        elif key == "omit":
            omit.append(value)
        elif key == "note":
            note = value
        else:
            raise ValueError(f"{path}: unknown descriptor key {key!r}")
    return Request(
        id=identifier,
        argv=tuple(argv),
        env=env,
        files=tuple(files),
        footage=tuple(footage),
        omit=tuple(omit),
        note=note,
        directory=directory,
    )


def load_requests(root: Path | None = None) -> list[Request]:
    """Every recorded request, in a stable order."""
    base = Path(root) if root else FIXTURE_ROOT
    requests_dir = base / "requests"
    if not requests_dir.is_dir():
        raise FileNotFoundError(f"no recorded request set at {requests_dir}")
    requests = [
        parse_descriptor(entry, base)
        for entry in sorted(requests_dir.iterdir())
        if entry.is_dir()
    ]
    if not requests:
        raise ValueError(f"the recorded request set at {base} is empty")
    return requests


# ── The backends ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Backend:
    """One execution backend: a name and the command that runs a request."""

    name: str
    command: tuple[str, ...]
    """The argv prefix; the request's own argv is appended."""
    env: dict[str, str] = field(default_factory=dict)
    """Extra environment every run of this backend gets."""


def native_binary() -> Path | None:
    """The built native front door, or None when it was never built.

    `REN_NATIVE_BIN` names it outright; otherwise the workspace's own
    build output is looked for. A backend that is not there is
    `unmeasured`, never an agreement.
    """
    explicit = os.environ.get("REN_NATIVE_BIN", "").strip()
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    for profile in ("release", "debug"):
        candidate = ENGINE_ROOT / "native" / "target" / profile / "ren"
        if candidate.is_file():
            return candidate
    return None


def default_backends() -> list[Backend]:
    """The two backends this check compares: native Rust, and Python."""
    backends = []
    binary = native_binary()
    if binary is not None:
        backends.append(Backend(
            name="native",
            command=(str(binary),),
            env={"REN_ENGINE_ROOT": str(ENGINE_ROOT)},
        ))
    backends.append(Backend(
        name="python",
        command=(os.environ.get("PIPELINE_PYTHON") or "python3", "-m", "ren.cli"),
        env={
            "REN_ENGINE_ROOT": str(ENGINE_ROOT),
            "PYTHONPATH": str(ENGINE_ROOT),
        },
    ))
    return backends


# ── Running one request ──────────────────────────────────────────────


@dataclass
class Outcome:
    """What one backend answered for one request."""

    backend: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    state: dict[str, str] = field(default_factory=dict)
    error: str = ""
    """Set when the backend could not be run at all."""

    @property
    def ran(self) -> bool:
        """Whether this backend answered. An error is `unmeasured`."""
        return not self.error


def state_fingerprint(root: Path) -> dict[str, str]:
    """Every file under `root`, by relative path, with its content hash.

    The preserved state of an operation is what it left behind: the
    same paths, the same bytes. A file that appeared, vanished or
    changed is a difference the prose never shows.
    """
    fingerprint: dict[str, str] = {}
    if not root.is_dir():
        return fingerprint
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        fingerprint[relative] = digest
    return fingerprint


def materialize(request: Request, project_dir: Path, root: Path,
                media_dir: Path | None = None,
                placeholders: dict[str, str] | None = None) -> None:
    """Build the request's project in `project_dir`, from the fixture.

    The fixture files are copied verbatim, except for the placeholders a
    recorded path carries (`{{media}}` is the scratch media folder, whose
    path cannot be known before the run); the footage is synthesized
    from the recorded lavfi source; the omitted paths are removed, so a
    request can describe a project that has no `raw/` folder at all.

    A footage path under `media/` is written into the scratch media
    folder instead of the project - that is the folder a project
    DECLARES as its footage root, and it lives beside the project
    rather than inside it.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    for relative in request.files:
        source_path = root / relative
        if not source_path.is_file():
            raise FileNotFoundError(f"no fixture file at {source_path}")
        text = source_path.read_text(encoding="utf-8")
        for token, value in (placeholders or {}).items():
            text = text.replace(token, value)
        (project_dir / source_path.name).write_text(text, encoding="utf-8")
    for relative, source in request.footage:
        if relative.startswith("media/"):
            target = (media_dir or project_dir) / relative[len("media/"):]
        else:
            target = project_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source,
             "-pix_fmt", "yuv420p", str(target), "-y"],
            capture_output=True, encoding="utf-8", check=False,
        )
        if encoded.returncode != 0:
            raise RuntimeError(
                f"ffmpeg could not encode the fixture footage {relative}: "
                f"{encoded.stderr.strip()}"
            )
    for relative in request.omit:
        target = project_dir / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def base_environment(workdir: Path, request: Request) -> dict[str, str]:
    """The environment one request runs under, in a scratch directory.

    Every path the backends read is pointed at the scratch: the projects
    root, the two asset libraries, the per-user config directory and the
    state directory the shadow store lives in. The captain's projects
    and the machine's own records are never in reach.
    """
    projects = workdir / "projects"
    assets = workdir / "assets"
    (projects / "demo").mkdir(parents=True, exist_ok=True)
    (assets / "sfx").mkdir(parents=True, exist_ok=True)
    (assets / "music").mkdir(parents=True, exist_ok=True)
    (workdir / "config").mkdir(parents=True, exist_ok=True)
    (workdir / "state").mkdir(parents=True, exist_ok=True)
    env = {
        "PIPELINE_PROJECTS_ROOT": str(projects),
        "PIPELINE_SFX_LIBRARY": str(assets / "sfx"),
        "PIPELINE_MUSIC_LIBRARY": str(assets / "music"),
        "XDG_CONFIG_HOME": str(workdir / "config"),
        "XDG_STATE_HOME": str(workdir / "state"),
        "REN_ENGINE_ROOT": str(ENGINE_ROOT),
        "PYTHONPATH": str(ENGINE_ROOT),
        # The machine-readable result line is ON for every request, so
        # the two backends are compared on the whole answer a host
        # reads. A request that wants the prose only says so.
        "REN_OUTPUT": "json",
    }
    env.update(request.env)
    return env


def run_backend(backend: Backend, request: Request, workdir: Path,
                root: Path) -> Outcome:
    """Run one request through one backend, in its own scratch copy."""
    outcome = Outcome(backend=backend.name)
    project_dir = workdir / "projects" / "demo"
    media_dir = workdir / "media"
    shutil.rmtree(project_dir, ignore_errors=True)
    try:
        materialize(
            request, project_dir, root,
            media_dir=media_dir,
            placeholders={
                "{{media}}": str(media_dir),
                "{{projects_root}}": str(workdir / "projects"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - a fixture that cannot be built is unmeasured
        outcome.error = f"the fixture could not be built: {exc}"
        return outcome

    before = state_fingerprint(project_dir)
    env = dict(os.environ)
    env.update(base_environment(workdir, request))
    env.update(backend.env)
    command = [*backend.command, *request.argv]
    started = time.monotonic()
    try:
        done = subprocess.run(
            command, capture_output=True, encoding="utf-8",
            env=env, check=False, cwd=str(ENGINE_ROOT),
        )
    except OSError as exc:
        outcome.error = f"the backend could not be started: {exc}"
        return outcome
    outcome.seconds = time.monotonic() - started
    outcome.exit_code = done.returncode
    outcome.stdout = done.stdout or ""
    outcome.stderr = done.stderr or ""
    after = state_fingerprint(project_dir)
    outcome.state = {
        "before": json.dumps(before, sort_keys=True),
        "after": json.dumps(after, sort_keys=True),
    }
    return outcome


# ── Comparing two answers ────────────────────────────────────────────


@dataclass
class Comparison:
    """One request, answered by every backend, compared."""

    request: Request
    outcomes: dict[str, Outcome]
    differences: list[str] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        """Whether every backend answered. A missing one is unmeasured."""
        return bool(self.outcomes) and all(o.ran for o in self.outcomes.values())

    @property
    def agreed(self) -> bool:
        """Whether the backends answered the same thing."""
        return self.measured and not self.differences

    @property
    def outcome_class(self) -> str:
        """The request's own class: completed, refused, disagreed, unmeasured.

        The four are kept apart on purpose: a request both backends
        refused is not a request both backends completed, and neither is
        a disagreement.
        """
        if not self.measured:
            return "unmeasured"
        if self.differences:
            return "disagreed"
        codes = {o.exit_code for o in self.outcomes.values()}
        if codes == {0}:
            return "completed"
        if codes == {4}:
            return "refused"
        return "other"


def compare(request: Request, outcomes: dict[str, Outcome]) -> Comparison:
    """Compare the backends' answers to one request."""
    comparison = Comparison(request=request, outcomes=outcomes)
    names = sorted(outcomes)
    for field_name in COMPARED_FIELDS:
        values = {}
        for name in names:
            outcome = outcomes[name]
            if field_name == "exit_code":
                values[name] = outcome.exit_code
            elif field_name == "stdout":
                values[name] = outcome.stdout
            elif field_name == "stderr":
                values[name] = outcome.stderr
            else:
                values[name] = json.dumps(outcome.state, sort_keys=True)
        if len(set(values.values())) > 1:
            comparison.differences.append(field_name)
    return comparison


def preserved_state_changed(outcome: Outcome) -> bool:
    """Whether the run changed the project it was about."""
    before = outcome.state.get("before")
    after = outcome.state.get("after")
    if before is None or after is None:
        return False
    return before != after


# ── The report ───────────────────────────────────────────────────────


@dataclass
class Report:
    """What the run measured, with the denominators kept apart."""

    comparisons: list[Comparison]
    backends: list[str]

    @property
    def requests(self) -> int:
        return len(self.comparisons)

    @property
    def measured(self) -> list[Comparison]:
        return [c for c in self.comparisons if c.measured]

    @property
    def agreed(self) -> list[Comparison]:
        return [c for c in self.comparisons if c.agreed]

    @property
    def disagreed(self) -> list[Comparison]:
        return [c for c in self.comparisons
                if c.measured and c.differences]

    @property
    def unmeasured(self) -> list[Comparison]:
        return [c for c in self.comparisons if not c.measured]

    def classification(self) -> dict[str, int]:
        """The four classes, counted separately. Never combined."""
        counts = {"completed": 0, "refused": 0, "disagreed": 0,
                  "unmeasured": 0, "other": 0}
        for comparison in self.comparisons:
            counts[comparison.outcome_class] = counts.get(
                comparison.outcome_class, 0) + 1
        return counts

    def timing(self) -> dict[str, dict[str, float]]:
        """How long each backend took, per request and in total."""
        report: dict[str, dict[str, float]] = {}
        for name in self.backends:
            seconds = [o.seconds for c in self.comparisons
                       if (o := c.outcomes.get(name)) is not None and o.ran]
            if not seconds:
                continue
            ordered = sorted(seconds)
            middle = len(ordered) // 2
            median = (ordered[middle] if len(ordered) % 2
                      else (ordered[middle - 1] + ordered[middle]) / 2)
            report[name] = {
                "runs": float(len(seconds)),
                "total_seconds": round(sum(seconds), 3),
                "median_seconds": round(median, 3),
            }
        return report

    def to_dict(self) -> dict:
        """The report as data, for a machine reader."""
        return {
            "backends": self.backends,
            "requests": self.requests,
            "measured": len(self.measured),
            "agreed": len(self.agreed),
            "disagreed": len(self.disagreed),
            "unmeasured": len(self.unmeasured),
            "classification": self.classification(),
            "timing": self.timing(),
            "disagreements": [
                {
                    "id": c.request.id,
                    "argv": list(c.request.argv),
                    "fields": c.differences,
                    "note": c.request.note,
                }
                for c in self.disagreed
            ],
            "unmeasured_requests": [
                {
                    "id": c.request.id,
                    "argv": list(c.request.argv),
                    "errors": {name: o.error for name, o in c.outcomes.items()
                               if not o.ran},
                }
                for c in self.unmeasured
            ],
        }


def reset_scratch(scratch: Path) -> None:
    """Empty a scratch directory WITHOUT moving it.

    The path is part of what a verb prints, so both backends must run
    at the SAME path: two scratch directories would differ in the very
    output that is being compared.
    """
    if scratch.exists():
        for child in scratch.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    scratch.mkdir(parents=True, exist_ok=True)


def run_suite(requests: list[Request], backends: list[Backend],
              workdir: Path, root: Path | None = None) -> Report:
    """Run every recorded request through every backend, and compare."""
    base = Path(root) if root else FIXTURE_ROOT
    comparisons = []
    for request in requests:
        outcomes = {}
        for backend in backends:
            # The SAME scratch path for every backend: the path is in the
            # output, so two directories would differ in the very thing
            # being compared. The contents are emptied before each run,
            # so each backend starts from the same recorded state.
            scratch = workdir / request.id
            reset_scratch(scratch)
            outcomes[backend.name] = run_backend(
                backend, request, scratch, base)
        comparisons.append(compare(request, outcomes))
    return Report(
        comparisons=comparisons,
        backends=[b.name for b in backends],
    )


def render(report: Report) -> str:
    """The report as text, with the denominators stated."""
    counts = report.classification()
    lines = [
        (
            f"backend conformance: {report.requests} recorded requests, "
            f"{len(report.backends)} backends ({', '.join(report.backends)})"
        ),
        (
            f"  measured {len(report.measured)}/{report.requests}, "
            f"agreed {len(report.agreed)}, disagreed {len(report.disagreed)}, "
            f"unmeasured {len(report.unmeasured)}"
        ),
        (
            f"  outcomes: completed {counts.get('completed', 0)}, "
            f"refused {counts.get('refused', 0)}, "
            f"disagreed {counts.get('disagreed', 0)}, "
            f"unmeasured {counts.get('unmeasured', 0)}"
        ),
    ]
    timing = report.timing()
    for name in sorted(timing):
        numbers = timing[name]
        lines.append(
            f"  timing {name}: {numbers['runs']} runs, "
            f"median {numbers['median_seconds']}s, "
            f"total {numbers['total_seconds']}s"
        )
    for comparison in report.disagreed:
        lines.append(
            f"  DISAGREED {comparison.request.id}: "
            f"{', '.join(comparison.differences)}"
        )
    for comparison in report.unmeasured:
        errors = ", ".join(
            f"{name}: {o.error}" for name, o in comparison.outcomes.items()
            if not o.ran
        )
        lines.append(f"  UNMEASURED {comparison.request.id}: {errors}")
    return "\n".join(lines)
