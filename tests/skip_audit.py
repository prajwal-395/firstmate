"""A skipped test must name an environment that runs it.

Five tests in this suite skipped in EVERY environment, on every machine,
in CI and locally, and had done since they were written, because the
symbol each guarded its import with does not exist (issue #249).  pytest
reported them as skips; nothing ever objected.  That is the shape
AGENTS.md 10.4 names - a gate that cannot fail is worse than no gate,
because it reports coverage that is not there - and a skip is the version
of it that hides in plain sight, because a skip LOOKS like a considered
decision.

This module is the check that stops it coming back.  It has two halves,
and they fail for different reasons:

1. SOURCE (`audit_sources`).  Findings that are decidable from the tree
   alone: a skip nothing guards, a skip guarded by a first-party import
   that does not resolve against this repository, and a test body that
   cannot fail.  This is what catches a test that never even runs, so no
   run can report it.
2. RUNTIME (`declared_condition`, driven from `tests/conftest.py`).
   Every skip a run actually reports must match a declaration in
   `ENVIRONMENT_CONDITIONS` below, which names the condition and states
   what makes it false.  One environment cannot prove "skipped in every
   environment"; requiring the author to NAME the environment that runs
   the test can, and it puts the answer somewhere a reader can check.

Both FAIL rather than report.  Reporting is what the old behaviour
already did - pytest printed all five skips in its summary every single
run for months - so a check that only reports is the defect wearing a
badge.  There is nothing to scramble over: the suite is clean under this
check, so the first thing it fails on will be a new one.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Packages whose source is IN this repository. An import of one of these
# either resolves here or does not resolve anywhere, so a skip guarded on
# it is a statement about the repo and not about the machine.
FIRST_PARTY_ROOTS = ("library", "tests", "manage_project", "fusion")


# ── The environments this suite adapts to ─────────────────────────────


@dataclass(frozen=True)
class EnvironmentCondition:
    """One reason a test may legitimately not run here.

    `pattern` matches the reason pytest reports.  `false_when` says what
    makes the condition false - which is the whole point: a skip that
    cannot name an environment that runs the test is not a skip, it is a
    deletion nobody performed.

    `capability`, when set, names the environment capability whose absence
    causes this skip.  A skip matching a condition WITH a capability means
    the run is NARROWER than a full environment would produce - the gate
    must not report an unqualified PASS.  Conditions without a capability
    are complementary pairs (one side always fires) or are not narrowing.

    `install_hint` tells the reader how to obtain the missing capability.
    """

    pattern: str
    false_when: str
    capability: str = ""
    install_hint: str = ""
    _regex: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_regex", re.compile(self.pattern))

    def matches(self, reason: str) -> bool:
        return bool(self._regex.search(reason))


# Every entry is a MEASURING INSTRUMENT or an EXTERNAL APPLICATION that
# this repository does not ship. Nothing here is a fact about the repo:
# a skip conditioned on the repository's own contents is exactly the
# defect, and is deliberately absent so that it fails.
#
# `tests/test_no_unfailable_tests.py` asserts both directions - that the
# suite's real skips are covered, and that an undeclared one fails.
ENVIRONMENT_CONDITIONS = (
    # ── Complementary pairs: one side always fires, so neither narrows ──
    EnvironmentCondition(
        pattern=r"parselmouth is present, so measurement succeeds",
        false_when="praat-parselmouth is NOT installed - the run then "
                   "exercises the missing-dependency half instead",
    ),
    EnvironmentCondition(
        pattern=r"this is the missing-dependency failure mode",
        false_when="praat-parselmouth IS installed, as `requirements.txt` "
                   "asks for - the .venv this pipeline runs in has it",
    ),
    EnvironmentCondition(
        pattern=r"tiktoken is installed here; the absent path is elsewhere",
        false_when="tiktoken is NOT installed - the replay bench then "
                   "reports an absent token count rather than estimating",
    ),
    # ── Capabilities: a skip here means the run is narrower ────────────
    EnvironmentCondition(
        pattern=r"ffmpeg(/ffprobe)? (is )?not (available|installed|on this machine|on PATH)",
        false_when="ffmpeg and ffprobe are on PATH (AGENTS.md 9 requires "
                   "them for a real run)",
        capability="ffmpeg",
        install_hint="brew install ffmpeg",
    ),
    EnvironmentCondition(
        pattern=r"face sampling is an ffmpeg pipeline",
        false_when="ffmpeg and ffprobe are on PATH",
        capability="ffmpeg",
        install_hint="brew install ffmpeg",
    ),
    EnvironmentCondition(
        pattern=r"ffmpeg cannot encode a fixture here",
        false_when="the local ffmpeg build carries the encoder the "
                   "fixture needs",
        capability="ffmpeg_encoder",
        install_hint="brew install ffmpeg  (with the encoder the fixture needs)",
    ),
    EnvironmentCondition(
        pattern=r"this ffmpeg cannot write display-rotation side data",
        false_when="the local ffmpeg build supports `-display_rotation`",
        capability="ffmpeg_rotation",
        install_hint="upgrade ffmpeg to a build that supports -display_rotation",
    ),
    # Same absent-ffmpeg fact in the words the overlay-mode and delivery
    # markers use.  Each names CI because CI installs ffmpeg (AGENTS.md
    # 10.4 pins that); matched no declaration, so a run without ffmpeg
    # exited 1 with zero failures.
    EnvironmentCondition(
        pattern=r"(needs ffmpeg; runs in CI"
                r"|ffmpeg/ffprobe are required; CI installs them)",
        false_when="ffmpeg and ffprobe are on PATH (AGENTS.md 9 requires "
                   "them for a real run; CI installs them)",
        capability="ffmpeg",
        install_hint="brew install ffmpeg",
    ),
    EnvironmentCondition(
        pattern=r"needs remotion-subtitles/node_modules, npx and ffmpeg",
        false_when="the Node dependencies are installed and this checkout "
                   "is bound to them (scripts/install_node_deps.sh), "
                   "and npx and ffmpeg are on PATH",
        capability="remotion",
        install_hint="scripts/install_node_deps.sh",
    ),
    EnvironmentCondition(
        pattern=r"remotion-subtitles/node_modules/typescript",
        false_when="the Node dependencies are installed and this checkout "
                   "is bound to them (scripts/install_node_deps.sh), "
                   "which brings the TypeScript dev dependency the "
                   "node harness type-checks against",
        capability="remotion",
        install_hint="scripts/install_node_deps.sh",
    ),
    # The stills-gated markers: the render needs ffmpeg, a still only
    # needs the installed Remotion deps and npx.  Split out when the
    # delivery tests grew a stills_available marker beside
    # remotion_available; the two-part reason matched no declaration, so
    # a run without node_modules exited 1 with zero failures.
    EnvironmentCondition(
        pattern=r"needs remotion-subtitles/node_modules and npx",
        false_when="the Node dependencies are installed and this checkout "
                   "is bound to them (scripts/install_node_deps.sh), "
                   "and npx is on PATH",
        capability="remotion",
        install_hint="scripts/install_node_deps.sh",
    ),
    EnvironmentCondition(
        pattern=r"needs node and remotion-subtitles/node_modules/typescript",
        false_when="the Node dependencies are installed and this checkout "
                   "is bound to them (scripts/install_node_deps.sh, "
                   "which brings the TypeScript dev dependency) and "
                   "`node` is on PATH",
        capability="remotion",
        install_hint="scripts/install_node_deps.sh",
    ),
    EnvironmentCondition(
        pattern=r"could not import ['\"](?:cv2|yaml|tiktoken)['\"]",
        false_when="the package is installed - all three are in "
                   "`requirements.txt` and present in the pipeline .venv",
        capability="python_packages",
        install_hint="pip install -r requirements.txt",
    ),
    EnvironmentCondition(
        pattern=r"PyYAML not installed",
        false_when="PyYAML is installed, as `requirements.txt` asks",
        capability="python_packages",
        install_hint="pip install -r requirements.txt",
    ),
    # `pytest.importorskip` with an explicit reason reports the reason
    # instead of "could not import ...", so these bypass the import
    # pattern above.  Pillow and PyYAML are both in `requirements.txt`;
    # a machine without them is narrower, not broken.
    EnvironmentCondition(
        pattern=r"needs Pillow to (draw the fixture|measure (a rendered frame|the stills))",
        false_when="Pillow is installed, as `requirements.txt` asks",
        capability="python_packages",
        install_hint="pip install -r requirements.txt",
    ),
    EnvironmentCondition(
        pattern=r"PyYAML parses the workflow",
        false_when="PyYAML is installed, as `requirements.txt` asks",
        capability="python_packages",
        install_hint="pip install -r requirements.txt",
    ),
    EnvironmentCondition(
        pattern=r"the SFX library is not resolvable here",
        false_when="the bridge subprocess has a project_folder to write "
                   "the SFX catalogue into - the test harness does not "
                   "provide one, so this always skips in the suite",
        # No capability: these tests skip because the bridge has no
        # project_folder in the test harness, not because
        # PIPELINE_SFX_LIBRARY is absent.  The skip fires in EVERY
        # test run regardless of environment, so it is the baseline,
        # not a narrowing gap.
    ),
    EnvironmentCondition(
        pattern=r"(Templates\.drfx not found"
                r"|Could not extract setting from drfx)",
        false_when="DaVinci Resolve is installed, so its own "
                   "Templates.drfx is on disk to parse",
        capability="resolve_installed",
        install_hint="install DaVinci Resolve",
    ),
    EnvironmentCondition(
        pattern=r"DaVinci Resolve is not running with a project whose media "
                r"pool has a video clip",
        false_when="DaVinci Resolve is running with a project open whose "
                   "media pool holds a video clip long enough to trim - the "
                   "state AGENTS.md 5 requires for any Resolve-dependent "
                   "step, and the state the captain reviews a timeline in",
        capability="resolve_running",
        install_hint="open DaVinci Resolve with a project whose media pool "
                     "has a video clip",
    ),
    EnvironmentCondition(
        pattern=r"node is not on PATH, so the plugin's JavaScript cannot "
                r"be run here",
        false_when="Node.js is installed and `node` is on PATH - the same "
                   "runtime remotion-subtitles already needs, and what the "
                   "Workflow Integration's own JavaScript is checked "
                   "against `clip_context` with",
        capability="node",
        install_hint="brew install node",
    ),
    EnvironmentCondition(
        pattern=r"DaVinci Resolve Studio's bundled Electron and Workflow "
                r"Integration SDK are not installed here",
        false_when="DaVinci Resolve Studio is installed - it ships both, "
                   "and Workflow Integrations are a Studio-only feature, "
                   "so a machine that can load the plugin can run this",
        capability="resolve_studio",
        install_hint="install DaVinci Resolve Studio",
    ),
    EnvironmentCondition(
        pattern=r"PIPELINE_PROJECTS_ROOT was unset before pytest started",
        false_when="PIPELINE_PROJECTS_ROOT is configured, which it is on "
                   "any machine that has run the pipeline",
        capability="projects_root",
        install_hint="export PIPELINE_PROJECTS_ROOT=/path/to/projects",
    ),
    EnvironmentCondition(
        pattern=r'could not import "mlx_vlm"',
        false_when="mlx and mlx_vlm are installed - they are macOS-only "
                   "Apple Silicon dependencies and unavailable on Linux CI",
        capability="mlx_vlm",
        install_hint="pip install mlx mlx_vlm  (Apple Silicon only)",
    ),
    EnvironmentCondition(
        pattern=r"needs the captain's project stills at",
        false_when="the firstmate home holds "
                   "data/vep-grade-variants-to-choose-from/ with the "
                   "rendered grade-variant stills - the measured reference "
                   "the pixel proofs compare against, present on the "
                   "machine that ran the grade lane",
        # The stills are the calibration target, not repo contents: a
        # lane's rendered frames that this repository must not carry.
        # The skip fires only where the lane output is absent, so it is
        # narrowing, and the pixel proofs still run - and can still
        # fail - where the reference is present.
        capability="grade_reference_stills",
        install_hint="run the grade-variants lane, or copy its "
                     "data/vep-grade-variants-to-choose-from/ stills into "
                     "the firstmate home",
    ),
)


def declared_condition(reason: str) -> EnvironmentCondition | None:
    """The declaration covering this skip reason, or None."""
    for condition in ENVIRONMENT_CONDITIONS:
        if condition.matches(reason):
            return condition
    return None


@dataclass(frozen=True)
class MissingCapability:
    """A capability the environment does not have, with how to get it."""

    name: str
    install_hint: str
    skip_count: int


def missing_capabilities_from_reasons(
    reasons: list[str],
) -> list[MissingCapability]:
    """Which capabilities were absent, given a list of skip reasons.

    Groups by capability name and deduplicates install hints.  Only
    conditions with a non-empty `capability` are reported - complementary
    pairs are not narrowing.
    """
    caps: dict[str, dict] = {}
    for reason in reasons:
        cond = declared_condition(reason)
        if cond is None or not cond.capability:
            continue
        entry = caps.setdefault(
            cond.capability, {"hint": cond.install_hint, "count": 0})
        entry["count"] += 1
    return sorted(
        (MissingCapability(name=k, install_hint=v["hint"],
                           skip_count=v["count"])
         for k, v in caps.items()),
        key=lambda mc: mc.name,
    )


def missing_capabilities_from_junit(xml_path: str) -> list[MissingCapability]:
    """Extract missing capabilities from a JUnit XML file pytest wrote.

    Reads the `<skipped message="...">` elements and returns the
    capabilities whose absence caused skips.  Used by the gate script to
    detect when a run is narrower than a full environment would produce.
    """
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(xml_path)
    except Exception:
        return []

    reasons: list[str] = []
    for skipped in tree.iter("skipped"):
        msg = skipped.get("message", "")
        if msg:
            reasons.append(msg)
    return missing_capabilities_from_reasons(reasons)


UNDECLARED_SKIP_ADVICE = (
    "A skip must name an environment that RUNS the test.\n"
    "Either fix the test so it runs here, or add an EnvironmentCondition "
    "to tests/skip_audit.py stating what makes the condition false.\n"
    "A condition that depends on this repository's own contents rather "
    "than on the machine is not an environment: it is the always-skip "
    "this check exists to catch."
)


# ── Half 1: what the source alone decides ─────────────────────────────


@dataclass(frozen=True)
class Finding:
    path: Path
    lineno: int
    scope: str
    kind: str
    detail: str

    def __str__(self) -> str:
        try:
            where = self.path.relative_to(REPO_ROOT)
        except ValueError:  # audited tree is elsewhere - a test's tmp_path
            where = self.path
        return f"{where}:{self.lineno} {self.scope} [{self.kind}] {self.detail}"


def _module_source(dotted: str, root: Path) -> Path | None:
    """The file a first-party dotted module name resolves to, if any."""
    parts = dotted.split(".")
    base = root.joinpath(*parts)
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


def _module_level_names(path: Path) -> set | None:
    """Names a module binds at module level, or None if it cannot say.

    A star-import or a module `__getattr__` means the set is not the
    whole story, and a check that cannot be sure must not accuse.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
            if node.name == "__getattr__":
                return None
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and any(
                    a.name == "*" for a in node.names):
                return None
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            # Conditionally bound: assume it can be bound.
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef)):
                    names.add(sub.name)
                elif isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add(alias.asname or alias.name.split(".")[0])
    return names


def _is_first_party(dotted: str) -> bool:
    return dotted.split(".")[0] in FIRST_PARTY_ROOTS


def unresolved_first_party_import(node, root: Path) -> str:
    """Why this import cannot resolve against `root`, or "".

    Only first-party modules are judged.  A third-party import failing is
    a fact about the machine, which is what a skip is FOR.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _is_first_party(alias.name) and not _module_source(alias.name, root):
                return f"no module {alias.name!r} in this tree"
        return ""
    if not isinstance(node, ast.ImportFrom) or node.level:
        return ""
    module = node.module or ""
    if not _is_first_party(module):
        return ""
    source = _module_source(module, root)
    if source is None:
        return f"no module {module!r} in this tree"
    names = _module_level_names(source)
    if names is None:
        return ""
    for alias in node.names:
        if alias.name in names:
            continue
        if _module_source(f"{module}.{alias.name}", root):
            continue
        return (f"{module!r} defines no {alias.name!r} - the guard can "
                f"only ever fail")
    return ""


def _is_skip_call(node) -> bool:
    """`pytest.skip(...)`, `skip(...)` or `self.skipTest(...)`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in ("skip", "skipTest")
    return isinstance(func, ast.Name) and func.id in ("skip", "skipTest")


def _contains_skip(body) -> ast.Call | None:
    for statement in body:
        for node in ast.walk(statement):
            if _is_skip_call(node):
                return node
    return None


def _skip_marker(decorator) -> str | None:
    """"skip", "skipif" or None for a pytest skip marker decorator."""
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    while isinstance(node, ast.Attribute):
        if node.attr in ("skip", "skipif"):
            return node.attr
        node = node.value
    return None


def _cannot_fail(func) -> str:
    """Why this test body can never fail, or "".

    Two shapes, both of which shipped here: a body that is only a
    docstring and `pass`, and a body that is one `try` whose handler
    swallows everything.  Both report a green dot for code nothing
    touched.
    """
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(
            body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    # A leading import guard is not the test. `test_temporal_index` was
    # `try: import ... except ImportError: pytest.skip(...)` followed by
    # `pass`: the import RESOLVED, so it never skipped - it ran, asserted
    # nothing, and printed a green dot for step 1.04 on every run.
    guarded = list(body)
    while guarded and isinstance(guarded[0], ast.Try) and _contains_skip(
            [h for handler in guarded[0].handlers for h in handler.body]):
        guarded = guarded[1:]
    if not guarded or all(isinstance(s, ast.Pass) for s in guarded):
        return ("the body asserts nothing" if len(guarded) != len(body)
                else "the body is empty")
    if len(body) == 1 and isinstance(body[0], ast.Try):
        handlers = body[0].handlers
        if handlers and all(
                all(isinstance(s, ast.Pass) for s in h.body) for h in handlers):
            return ("the whole body is a try whose handler swallows "
                    "everything - it passes when nothing works")
    return ""


def audit_source(path: Path, root: Path = REPO_ROOT) -> list:
    """Findings for one test module."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    findings = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue

        for decorator in node.decorator_list:
            marker = _skip_marker(decorator)
            if marker == "skip":
                findings.append(Finding(
                    path, decorator.lineno, node.name, "unconditional-skip",
                    "@pytest.mark.skip names no condition, so no "
                    "environment runs this test"))
            elif marker == "skipif" and isinstance(decorator, ast.Call):
                for arg in decorator.args:
                    if isinstance(arg, ast.Constant) and arg.value is True:
                        findings.append(Finding(
                            path, decorator.lineno, node.name,
                            "unconditional-skip",
                            "skipif(True) is a skip with a reason attached"))

        why = _cannot_fail(node)
        if why:
            findings.append(Finding(
                path, node.lineno, node.name, "cannot-fail", why))

        # A skip reached without passing through a branch, a loop or an
        # exception handler runs every time.
        for statement in node.body:
            call = _contains_skip([statement])
            if call is None:
                continue
            if isinstance(statement, (ast.If, ast.For, ast.While, ast.Try,
                                      ast.With)):
                continue
            findings.append(Finding(
                path, call.lineno, node.name, "unconditional-skip",
                "this skip is reached on every run"))

    # A skip in the handler of an import that cannot resolve here.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            call = _contains_skip(handler.body)
            if call is None:
                continue
            for statement in node.body:
                detail = unresolved_first_party_import(statement, root)
                if detail:
                    findings.append(Finding(
                        path, statement.lineno, _enclosing(tree, statement),
                        "unresolvable-guard", detail))

    return findings


def _enclosing(tree, target) -> str:
    """The nearest enclosing function name, for a readable finding."""
    best = "<module>"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= target.lineno <= end:
                best = node.name
    return best


def test_sources(root: Path = REPO_ROOT):
    """Every test module in the tree, wherever it lives.

    `library/tools/fusion/tests/` is collected by the suite too, and it
    is where nine always-skipping tests were hiding.
    """
    seen = set()
    for path in sorted(root.rglob("test_*.py")):
        if any(part in (".git", ".venv", "node_modules", "__pycache__")
               for part in path.parts):
            continue
        if path.resolve() not in seen:
            seen.add(path.resolve())
            yield path


def audit_sources(root: Path = REPO_ROOT) -> list:
    """Every source finding in the tree, in file order."""
    findings = []
    for path in test_sources(root):
        findings.extend(audit_source(path, root))
    return findings
