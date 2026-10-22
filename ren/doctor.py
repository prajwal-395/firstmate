"""`ren doctor`: what can this Mac do with Ren? One line per check, and the fix.

READ-ONLY. Doctor installs nothing, writes nothing, starts nothing. The
two things it has to ASK rather than look at are asked in child processes
with a timeout, so a hung or crashing dependency is a FAIL line, never a
hung or crashed doctor:

* Resolve - through a real scripting connection
  (`resolve_locale.scriptapp_preserving_locale`), because a module file on
  disk says nothing about whether the running app accepts scripts. Only
  getters are called: `GetProductName`, `GetVersionString`.
* The pipeline interpreter - resolved through `shared_environment`'s
  ladder, the same one `bin/vep` uses, and asked which of each runtime
  dependency group (`dependency_groups.RUNTIME_GROUPS`) it satisfies. A
  package's presence is read from its installed metadata; nothing heavy
  is imported.

Studio only (captain, Q10): the free edition does not accept external
scripting, and every Ren operation that places, reads or renders a
timeline is an external script. Doctor names the edition it found.

Capability-aware (punch list 22): each check is the check of one NEED
in `library/tools/machine_needs.py`, and a missing need is judged by the
capabilities it limits. A need every capability requires (`BASELINE`),
or a check that names no need, FAILs; any other missing need is MISS,
and the report says which capabilities it makes unavailable or degraded.
`--for <capability>` runs only that capability's checks.

Exit status is 1 when a required check FAILs - or, with `--for`, when
that capability is unavailable - and 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ren.engine_root import find_engine_root, require_engine_root
from ren.version import PYTHON_VERSION, REQUIRES_PYTHON

# Best effort at import: `from ren import doctor` must never raise for
# want of an engine. Every entry point re-resolves fresh below, so an
# in-process `$REN_ENGINE_ROOT` (tests, a packaged proof) takes effect.
_FALLBACK_ROOT = find_engine_root()
if _FALLBACK_ROOT is not None and str(_FALLBACK_ROOT) not in sys.path:
    sys.path.insert(0, str(_FALLBACK_ROOT))


def _engine_root() -> Path:
    """The live engine root, on `sys.path` for the `library` imports below."""
    root = require_engine_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root

MIN_NODE_MAJOR = 18
PROBE_TIMEOUT_S = 30

STUDIO_MARKER = "Studio"
RESOLVE_PROCESS = "Resolve"

FREE_EDITION_WHY = (
    "the free edition does not accept external scripting, and every Ren "
    "operation that builds, reads or renders a timeline is an external script")

MODELS = (
    # (label, what reads it, HuggingFace model id, machine_needs id)
    ("gemma-4-12b-it-4bit", "vision pass (step 1.03)",
     "mlx-community/gemma-4-12b-it-4bit", "model.vision"),
)

_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".npz", ".pt", ".pth", ".gguf")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    need: str = ""
    """The `machine_needs.NEEDS` id this line checks; "" for none."""
    undetermined: bool = False
    """The check could not determine the answer - it raised, the probe was
    refused or timed out, or the fact is unreadable from the running app.

    Three states, not two: a check that ran and said no is a FAILURE, and
    a check that could not run is not one. Reading `undetermined` as a
    failing need is what made the worker manifest advertise a capable
    machine as missing macOS (PR 1871 review)."""
    measured: dict = field(default_factory=dict)
    """The machine facts this line measured, travelling with it.

    A fact nobody measured is absent from here, which is how a reader
    tells "the Resolve build is 21.1.0.14" from "the Resolve build was
    never read".  `library/tools/capability_readiness.py` reads the
    environment from these facts, so every surface that asks the doctor
    reads ONE environment instead of probing again."""


# ── Resolve ──────────────────────────────────────────────────────────

_RESOLVE_PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from library.tools import paths
os.environ.setdefault("RESOLVE_SCRIPT_API", str(paths.RESOLVE_SCRIPT_API))
os.environ.setdefault("RESOLVE_SCRIPT_LIB", str(paths.RESOLVE_SCRIPT_LIB))
sys.path.append(os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules"))
out = {"module": False, "connected": False, "product": "", "version": ""}
try:
    import DaVinciResolveScript as dvr
    out["module"] = True
    from library.tools.resolve_lock import resolve_lease
    from library.tools.resolve_locale import scriptapp_preserving_locale
    with resolve_lease("ren doctor Resolve probe", exclusive=False):
        resolve = scriptapp_preserving_locale(dvr)
        if resolve is not None:
            out["connected"] = True
            out["product"] = resolve.GetProductName() or ""
            out["version"] = resolve.GetVersionString() or ""
except Exception as exc:
    out["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""


def resolve_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", RESOLVE_PROCESS],
                              capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe_resolve(python: str = sys.executable, timeout: float = PROBE_TIMEOUT_S) -> dict:
    """Ask the running Resolve who it is, in a child process.

    Always returns a dict: `running`, `module`, `connected`, `product`,
    `version`, and `error` when the probe itself went wrong.
    """
    result = {"running": resolve_running(), "module": False,
              "connected": False, "product": "", "version": ""}
    try:
        done = subprocess.run(
            [python, "-c", _RESOLVE_PROBE, str(_engine_root())],
            capture_output=True, encoding="utf-8", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        result["error"] = f"the scripting connection did not answer in {timeout:.0f}s"
        result["timed_out"] = True
        return result
    except OSError as exc:
        result["error"] = f"could not start the probe: {exc}"
        return result
    lines = done.stdout.strip().splitlines()
    try:
        result.update(json.loads(lines[-1]))
    except (IndexError, ValueError):
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        result["error"] = f"probe exited {done.returncode}: {tail}"
    return result


def resolve_checks(probe: dict) -> list:
    """Two lines from one probe: the connection, then the edition."""
    enable = ("DaVinci Resolve > Settings > System > General > "
              "External scripting using: Local, then restart Resolve")
    if probe.get("connected"):
        connection = Check("Resolve scripting", True,
                           "connected to the running app (External scripting is on)")
    elif probe.get("timed_out"):
        connection = Check("Resolve scripting", False, probe["error"],
                           "Resolve is busy, or another Ren process holds the "
                           "Resolve lease; run `ren doctor` again when it is idle",
                           undetermined=True)
    elif not probe.get("running"):
        connection = Check("Resolve scripting", False, "DaVinci Resolve is not running",
                           "open DaVinci Resolve Studio, then run `ren doctor` again")
    elif probe.get("error") and not probe.get("module"):
        connection = Check("Resolve scripting", False,
                           f"cannot load Resolve's scripting module ({probe['error']})",
                           "reinstall DaVinci Resolve Studio, or set RESOLVE_SCRIPT_API "
                           "and RESOLVE_SCRIPT_LIB in `ren config`")
    else:
        why = probe.get("error") or "Resolve is running but refused the connection"
        connection = Check("Resolve scripting", False, why, enable,
                           undetermined=True)

    product = probe.get("product", "")
    version = probe.get("version", "")
    if product and STUDIO_MARKER in product:
        edition = Check("Resolve edition", True, f"{product} {version}".strip())
    elif product:
        edition = Check("Resolve edition", False,
                        f"{product} {version} is the FREE edition; Ren needs Studio "
                        f"because {FREE_EDITION_WHY}".replace("  ", " "),
                        "install DaVinci Resolve Studio (a paid licence)")
    else:
        edition = Check("Resolve edition", False,
                        "unknown - the edition is read from the running app, and "
                        "there is no scripting connection (see above). The free "
                        f"edition never connects: {FREE_EDITION_WHY}",
                        "fix the Resolve scripting line; if this is the free "
                        "edition, install DaVinci Resolve Studio",
                        undetermined=True)
    connection.need, edition.need = "resolve.scripting", "resolve.studio"
    connection.measured = _resolve_facts(probe)
    return [connection, edition]


def _resolve_facts(probe: dict) -> dict:
    """The machine facts this probe measured.

    Three states, like every other check: the app answered (True), the
    app is not there or its scripting module is not loadable (False), or
    the app did not answer and nobody can say (None).  A probe that
    timed out is not a measurement of absence.
    """
    if probe.get("connected"):
        return {"resolve_connected": True,
                "resolve_version": probe.get("version", "")}
    if probe.get("timed_out"):
        return {"resolve_connected": None, "resolve_version": None}
    if not probe.get("running") or not probe.get("module"):
        return {"resolve_connected": False, "resolve_version": None}
    return {"resolve_connected": None, "resolve_version": None}


# ── Python ───────────────────────────────────────────────────────────

_PACKAGES_PROBE = r"""
import json, sys
from importlib import metadata
sys.path.insert(0, sys.argv[1])
out = {"version": list(sys.version_info[:3]), "groups": {}}
try:
    from packaging.requirements import Requirement
except ImportError:
    Requirement = None
from library.tools.dependency_groups import declared_requirements, group_file
for group in sys.argv[2:]:
    found = out["groups"][group] = {"missing": [], "wrong": [], "unchecked": []}
    for line in declared_requirements(group_file(group)):
        if Requirement is None:
            found["unchecked"].append(line)
            continue
        req = Requirement(line)
        if req.marker is not None and not req.marker.evaluate():
            continue
        try:
            have = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            found["missing"].append(str(req))
            continue
        if req.specifier and not req.specifier.contains(have, prereleases=True):
            found["wrong"].append(f"{req.name} {have} (needs {req.specifier})")
try:
    from library.tools import shared_environment as se
    ok, why = se.face_detector_available()
    if not ok:
        out["face_detector"] = why.splitlines()[0]
except Exception as exc:
    out["face_detector"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""

FACE_DETECTOR_GROUP = "graphics"
"""The group that carries cv2, whose Haar cascade the face detector loads."""


def resolve_interpreter() -> tuple:
    _engine_root()
    from library.tools import shared_environment
    return shared_environment.python_interpreter(_engine_root())


def python_checks() -> list:
    """The interpreter, then one line per runtime dependency group."""
    from library.tools.dependency_groups import RUNTIME_GROUPS
    python_label = f"Python {PYTHON_VERSION[0]}.{PYTHON_VERSION[1]} venv"
    interpreter, why_not = resolve_interpreter()
    setup_command = "ren setup"
    if not interpreter:
        return [Check(python_label, False, why_not.splitlines()[0],
                      f"run {setup_command}",
                      need="python.venv")]
    try:
        done = subprocess.run(
            [interpreter, "-c", _PACKAGES_PROBE, str(_engine_root()), *RUNTIME_GROUPS],
            capture_output=True, encoding="utf-8", timeout=120, check=False)
        report = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return [Check(python_label, False,
                      f"{interpreter} could not be asked about itself ({exc})",
                      f"run {setup_command} to repair it", need="python.venv")]
    version = tuple(report["version"])
    shown = ".".join(str(part) for part in version)
    checks = [Check(
        python_label, version[:2] == PYTHON_VERSION,
        f"{interpreter} is Python {shown}",
        "" if version[:2] == PYTHON_VERSION
        else f"run {setup_command} to repair it for {REQUIRES_PYTHON}",
        need="python.venv")]

    for group in RUNTIME_GROUPS:
        found = report["groups"][group]
        name, need = f"Python {group}", f"python.{group}"
        source = f"requirements/{group}.txt"
        problems = found["missing"] + found["wrong"]
        if group == FACE_DETECTOR_GROUP and report.get("face_detector"):
            problems.append(f"cv2 face detector: {report['face_detector']}")
        if found["unchecked"]:
            checks.append(Check(name, False,
                                "cannot compare versions: `packaging` is not installed",
                                f"{interpreter} -m pip install packaging", need=need,
                                undetermined=True))
        elif problems:
            checks.append(Check(name, False,
                                f"{len(problems)} not satisfied: " + "; ".join(problems),
                                f"run ren setup to install {source}", need=need))
        else:
            usable = ("; cv2 face detector usable"
                      if group == FACE_DETECTOR_GROUP else "")
            checks.append(Check(name, True, f"{source} satisfied{usable}", need=need))
    return checks


# ── Tools on PATH ────────────────────────────────────────────────────

def ffmpeg_check() -> Check:
    found = {tool: shutil.which(tool) for tool in ("ffmpeg", "ffprobe")}
    absent = [tool for tool, where in found.items() if not where]
    if absent:
        return Check("ffmpeg", False, f"not on PATH: {', '.join(absent)}",
                     "ren setup", need="ffmpeg")
    return Check("ffmpeg", True, f"{found['ffmpeg']}, {found['ffprobe']}",
                 need="ffmpeg")


def node_checks() -> list:
    from ren.edition import PUBLIC, current_edition

    public = current_edition() == PUBLIC
    node = shutil.which("node")
    if not node:
        checks = [Check("Node.js", False, "node is not on PATH", "ren setup",
                        need="node")]
        if public:
            checks.append(Check("HyperFrames renderer", False,
                                "needs Node.js and npx first", "brew install node",
                                need="remotion"))
        else:
            checks.append(Check("Remotion deps", False, "needs Node.js first",
                                "ren setup", need="remotion"))
        return checks
    try:
        version = subprocess.run([node, "--version"], capture_output=True,
                                 encoding="utf-8", timeout=10, check=False).stdout.strip()
        major = int(version.lstrip("v").split(".")[0])
    except (OSError, subprocess.SubprocessError, ValueError):
        version, major = "unreadable", 0
    checks = [Check("Node.js", major >= MIN_NODE_MAJOR, f"{node} {version}",
                    "" if major >= MIN_NODE_MAJOR
                    else f"run ren setup to install Node {MIN_NODE_MAJOR}+",
                    need="node")]

    if public:
        npx = shutil.which("npx")
        from library.tools.hyperframes_render import HYPERFRAMES_VERSION_PIN

        checks.append(Check(
            "HyperFrames renderer", bool(npx),
            (f"public provider; npx fetches hyperframes@"
             f"{HYPERFRAMES_VERSION_PIN} on the first render")
            if npx else "npx is not on PATH",
            "install Node.js (includes npx): brew install node" if not npx else "",
            need="remotion"))
    else:
        from library.tools import shared_environment
        remotion = shared_environment.remotion_dir(_engine_root())
        if shared_environment.dependencies_present(remotion):
            checks.append(Check("Remotion deps", True,
                                f"{shared_environment.node_modules(remotion)}",
                                need="remotion"))
        else:
            checks.append(Check("Remotion deps", False,
                                f"no node_modules bound at {remotion}",
                                "ren setup",
                                need="remotion"))
    return checks


def graphics_engine_checks() -> list:
    """Which graphics engines can draw: FFrames, Remotion, HyperFrames, and the pick.

    FFrames is the default engine, so a missing binary is a FAIL -
    nothing renders without it. Remotion is the selectable fallback
    and parity reference (`PIPELINE_GRAPHICS_RENDERER=remotion` in
    `~/.config/ren/config.env`, or `pipeline.graphics_renderer` in a
    project's `project.yaml`); its absence is reported, never a FAIL,
    because no run needs it unless it is selected. HyperFrames stays
    opt-in the same way. The check itself is
    read-only: `npx --no-install` answers only when the pinned release
    is already resolvable, so asking never downloads anything.
    """
    from library.tools import graphics_renderer as engines
    from library.tools import hyperframes_render as hf
    from ren.edition import PUBLIC, current_edition
    checks = []
    if current_edition() == PUBLIC:
        try:
            selected = engines.resolve_engine()
            checks.append(Check("graphics Remotion policy", True,
                                "personal-only; excluded from the public engine",
                                need="remotion"))
            npx = shutil.which("npx")
            checks.append(Check(
                "graphics HyperFrames", bool(npx),
                (f"opt-in renderer; npx fetches the pinned CLI "
                 f"on the first render ({selected})") if npx else
                "opt-in renderer, but npx is not on PATH",
                "install Node.js (includes npx): brew install node" if not npx else "",
                need="remotion"))
            checks.append(Check("graphics selected", selected == "fframes",
                                f"{selected} (public edition)",
                                "fix the public renderer selection",
                                need="remotion"))
        except Exception as exc:  # noqa: BLE001 - doctor must finish
            checks.append(Check("graphics selected", False,
                                f"the public renderer cannot be selected: {exc}",
                                "select the public FFrames provider",
                                need="remotion"))
        return checks
    try:
        from library.tools import shared_environment
        remotion_ok = shared_environment.dependencies_present(
            shared_environment.remotion_dir(_engine_root()))
    except Exception:  # noqa: BLE001 - doctor must finish
        remotion_ok = False
    try:
        from library.tools import fframes_render as ff
        ff_usable, ff_detail = ff.fframes_available()
    except Exception as exc:  # noqa: BLE001 - doctor must finish
        ff_usable, ff_detail = False, f"the check itself failed: {exc}"
    checks.append(Check(
        "graphics FFrames", ff_usable,
        "the default engine (selected when nothing else is)"
        if ff_usable else f"the default engine cannot render: {ff_detail}",
        "" if ff_usable else
        "build the binary with scripts/build_fframes_runtime.sh", need="remotion"))
    checks.append(Check(
        "graphics Remotion", True,
        "selectable fallback and parity reference"
        if remotion_ok else "not installed - fallback only (nothing needs it unless selected)",
        ""))
    try:
        usable, detail = hf.hyperframes_available()
    except Exception as exc:  # noqa: BLE001 - doctor must finish
        usable, detail = False, f"the check itself failed: {exc}"
    checks.append(Check(
        "graphics HyperFrames", True,
        detail if usable else f"not installed - opt-in only ({detail})"))
    try:
        selected = engines.resolve_engine()
        where = "default"
        if engines.project_engine():
            where = "project"
        elif engines.user_engine():
            where = "user setting"
        checks.append(Check(
            "graphics selected", True,
            f"{selected} (from {where}; "
            f"{engines.USER_SETTING_KEY} or pipeline.graphics_renderer)"))
    except Exception as exc:  # noqa: BLE001 - doctor must finish
        checks.append(Check(
            "graphics selected", False,
            f"the selection cannot be read: {exc}",
            "fix the value named by the error; valid engines are "
            f"{list(engines.ENGINES)}"))
    return checks


# ── Models ───────────────────────────────────────────────────────────

def hf_hub_cache() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(home).expanduser() / "hub"


def torch_checkpoints() -> Path:
    home = os.environ.get("TORCH_HOME") or str(Path.home() / ".cache" / "torch")
    return Path(home).expanduser() / "hub" / "checkpoints"


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def gb(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e8 else f"{size / 1e6:.0f} MB"


def hf_model_state(repo_id: str, cache: Path) -> tuple:
    """`(complete, bytes, why)` for one model in the HuggingFace cache.

    Complete means a snapshot holds at least one weights file that
    resolves to a real blob, and no download is left half-finished.
    """
    root = cache / ("models--" + repo_id.replace("/", "--"))
    if not root.is_dir():
        return False, 0, "not downloaded"
    blobs = root / "blobs"
    size = _size(blobs) if blobs.is_dir() else 0
    if blobs.is_dir() and any(blobs.glob("*.incomplete")):
        return False, size, "download left incomplete"
    for weights in (root / "snapshots").glob("*/**/*"):
        if weights.suffix in _WEIGHT_SUFFIXES and weights.exists():
            return True, size, ""
    return False, size, "no weights in the snapshot"


def model_checks() -> list:
    from library.tools import shared_environment

    checks = []
    cache = hf_hub_cache()
    for label, reader, ident, need in MODELS:
        complete, size, why = hf_model_state(ident, cache)
        fix = (f"{sys.executable} -c \"from huggingface_hub import "
               f"snapshot_download; snapshot_download('{ident}')\"")
        detail = f"{reader}: {gb(size)}" if complete else f"{reader}: {why}"
        checks.append(Check(f"model {label}", complete, detail,
                            "" if complete else fix, need=need))

    # Optional by design: ren search uses lexical-only search when neither
    # embedding backend can load (library/tools/analysis/footage_query.py).
    ident = "sentence-transformers/all-MiniLM-L6-v2"
    complete, size, why = hf_model_state(ident, cache)
    checks.append(Check(
        "model all-MiniLM-L6-v2", complete,
        f"footage search embedder: {gb(size)}" if complete else
        f"not cached - optional; lexical-only search remains available "
        f"when no embedder can load ({why})",
        "" if complete else
        (f"{sys.executable} -c \"from huggingface_hub import "
         f"snapshot_download; snapshot_download('{ident}')\""),
        need="model.search_embedding"))

    # beat_this weights (detected downbeats, step 2.06). Optional by
    # design like the search embedder above: without the checkpoint the tracker cannot
    # load and the grid falls back to librosa's every-4th-beat estimate
    # - labelled "estimated" wherever it travels - so a run still
    # completes, on a bar grid that can sit a beat off.
    bt_path = torch_checkpoints() / "beat_this-final0.ckpt"
    bt_complete = bt_path.is_file()
    interpreter, _ = resolve_interpreter()
    bt_fetch = (
        f"{interpreter} -c \"from beat_this.inference import load_model; "
        f"load_model('final0', device='cpu')\"" if interpreter
        else "from beat_this.inference import load_model; "
             "load_model('final0', device='cpu')")
    checks.append(Check(
        "model beat_this final0", bt_complete,
        f"detected downbeats (step 2.06): {gb(bt_path.stat().st_size)}"
        if bt_complete else
        "not downloaded - downbeats fall back to the librosa "
        "every-4th-beat estimate (labelled estimated)",
        "" if bt_complete else f"fetch it (~81 MB): {bt_fetch}",
        need="model.beat_this"))

    # PANNs weights (timed sound events, step 1.04). Optional by
    # design like the halves above: without the checkpoint a clip
    # records `sound_event_method: unmeasured` with the reason and an
    # event anchor on it refuses by name - so a run still completes,
    # with no event layer to address.
    panns_ok, _ = shared_environment.panns_available()
    panns_path = shared_environment.panns_checkpoint()
    checks.append(Check(
        "model PANNs Cnn14-DLM", panns_ok,
        f"timed sound events (step 1.04): "
        f"{gb(panns_path.stat().st_size)}"
        if panns_ok else
        "not downloaded - clips record unmeasured sound events and "
        "event anchors refuse by name "
        "(ren setup --with panns adds it, "
        "~327 MB)",
        "" if panns_ok else
        "ren setup --with panns",
        need="model.panns"))
    return checks


# ── Disk space ───────────────────────────────────────────────────────

VENV_BYTES_PER_PACKAGE = 15_000_000
"""Mean installed bytes per distribution in the locked stack.

The lock file pins the distributions but not their installed size, so
the venv's footprint is a derivation: the pinned count is read from the
lock at call time and multiplied by this mean, measured 2026-10-05
against the shipped venv (2.0 GB installed across the locked
distributions)."""

ENGINE_BYTES = 1_000_000_000
"""The versioned engine tree and its shared Node store: 585 MB of
node_modules (library/tools/shared_environment.py), the CPython 3.12
runtime, and the engine sources - one copy per machine."""

MODEL_BYTES = (
    6_300_000_000    # gemma-4-12b-it-4bit weights (docs/GEMMA_SERVER.md)
    + 327_428_481    # PANNs Cnn14 checkpoint (shared_environment.PANNS_CHECKPOINT_SIZE)
    + 100_000_000    # DeepFilterNet weights (docs/DOWNLOAD_INVENTORY.md)
    + 81_000_000     # beat_this final0 checkpoint (the fetch line above)
    + 3_000_000_000  # the run-time table's unsized models: SAM 2.1, CLIP,
                     # MiniLM, ECAPA, MFA, Audio Flamingo, All-In-One,
                     # Demucs, EasyOCR (docs/DOWNLOAD_INVENTORY.md)
)
"""Every model Ren fetches, sized: the documented sizes plus a declared
allowance for the run-time table's unsized rows."""

SCRATCH_BYTES = 2_000_000_000
"""Project scratch: pipeline_output trees, renders and the footage index
(a measured output tree is 2 GB - docs/STEP_REPLAY_BENCH.md)."""

LOCK_PATH = ("requirements", "lock", "macos-arm64-py312.txt")
"""The locked stack the venv footprint is derived from."""


def _pinned_packages(lock: Path) -> int:
    """Distributions a uv lock file pins: top-level `name==version` lines,
    never `--hash` continuations or `# via` comments."""
    count = 0
    for line in lock.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and not line.startswith("#") \
                and "==" in line:
            count += 1
    return count


def disk_footprint(lock: Path) -> dict:
    """Ren's footprint in bytes, by component, each traced to its source."""
    return {
        "venv": _pinned_packages(lock) * VENV_BYTES_PER_PACKAGE,
        "engine": ENGINE_BYTES,
        "models": MODEL_BYTES,
        "scratch": SCRATCH_BYTES,
    }


def free_bytes(path: Path) -> int:
    """Free bytes on the disk holding `path` - one statvfs call."""
    return shutil.disk_usage(path).free


def disk_space_verdict(free: int, footprint: dict) -> tuple:
    """`(ok, detail)` for free bytes against a footprint."""
    total = sum(footprint.values())
    if free >= total:
        return True, f"{gb(free)} free of {gb(total)} Ren needs"
    return False, f"{gb(free)} free of {gb(total)} Ren needs - short {gb(total - free)}"


def disk_space_check() -> Check:
    """Free disk space against the declared footprint.

    Informational by design: a full disk stops no capability by itself,
    so this line reports the gap before a download hits it - it never
    fails the doctor, and `ren setup` is the gate that refuses.
    """
    from library.tools import shared_environment

    home = shared_environment.vep_home()
    lock = _engine_root().joinpath(*LOCK_PATH)
    ok, detail = disk_space_verdict(free_bytes(home), disk_footprint(lock))
    return Check("disk space", ok, detail,
                 "" if ok else "free up disk space, then re-run `ren doctor`",
                 need="disk.space")


def transcription_checks() -> list:
    """Check the required Voz transcriber and MFA aligner."""
    from library.tools import heard_speech, shared_environment

    da_ok, da_detail = heard_speech.available()
    checks = [Check(
        "transcriber Voz (da)", da_ok, da_detail,
        "install the `da` CLI and ensure it is on PATH" if not da_ok else "",
        need="transcriber.voz")]

    mfa_ok, mfa_detail = shared_environment.mfa_available()
    checks.append(Check(
        "MFA aligner", mfa_ok, mfa_detail,
        "ren setup --with mfa" if not mfa_ok else "",
        need="model.mfa"))
    return checks


# ── Dialogue cleanup ─────────────────────────────────────────────

def deepfilternet_check() -> Check:
    """Can a deepfilternet request stage a stem on this machine?

    Optional by design: without the binary the request refuses by name
    and the model re-plans with voice_isolation
    (library/tools/dialogue_cleanup.py), so absence only degrades the
    capabilities `machine_needs` says it does. A binary that is present
    but would not run is missing too - the plan context would claim the
    tool and the build would refuse it.
    """
    from library.tools import shared_environment
    usable, _ = shared_environment.deepfilter_available()
    if not usable:
        return Check(
            "dialogue cleanup DeepFilter", False,
            "not installed - a deepfilternet request refuses by name and "
            "the model re-plans with voice_isolation",
            "ren setup --with deepfilter", need="deepfilter")
    binary = str(shared_environment.deepfilter_binary())
    try:
        done = subprocess.run([binary, "--version"], capture_output=True,
                              encoding="utf-8", timeout=PROBE_TIMEOUT_S,
                              check=False)
        version = ((done.stdout.strip().splitlines() or [""])[0].strip()
                   if done.returncode == 0 else "")
    except (OSError, subprocess.SubprocessError):
        version = ""
    if not version:
        return Check("dialogue cleanup DeepFilter", False,
                     f"{binary} is installed but would not run",
                     "run ren setup --with deepfilter", need="deepfilter")
    return Check("dialogue cleanup DeepFilter", True,
                 f"{version} at {binary}", need="deepfilter")


# ── The ren_native Rust edit backend ─────────────────────────────────


def ren_native_check() -> Check:
    """Is the ren_native extension built, and from this tree?

    The Rust edit backend is an optimisation with the Python
    implementation as the fallback, so a missing or stale build is a
    MISS, never a FAIL: the run keeps the Python backend. The per-tree
    cache (`library/tools/ren_native_cache.py`) is checked first; the
    shared venv's copy is the legacy fallback, accepted only where its
    baked-in fingerprint matches. A stale build - its baked-in source
    fingerprint does not match this engine's `native/` sources - is
    named as such: it predates the algebra the current `library/tools`
    speaks and would answer ops with verdicts that no longer hold, so
    `edit_backend.rust()` refuses it.
    """
    from library.tools import ren_native_cache, ren_native_source

    root = _engine_root()
    if root is not None:
        current = ren_native_source.source_fingerprint(root)
        if current is not None:
            entry = ren_native_cache.entry_dir(current)
            if (ren_native_cache.extension_file(entry) is not None
                    and ren_native_cache.read_recorded(entry) == current):
                return Check(
                    "ren_native Rust backend", True,
                    f"built for this tree (source fingerprint {current[:12]})",
                    need="ren_native")
    try:
        import ren_native
    except Exception:  # noqa: BLE001 - doctor must finish
        return Check(
            "ren_native Rust backend", False,
            "not built - the Python edit backend runs",
            "run ren setup to build it from the engine's Rust sources "
            "(needs cargo on PATH)",
            need="ren_native")
    baked = getattr(ren_native, "source_fingerprint", lambda: None)()
    current = ren_native_source.source_fingerprint(root) if root else None
    if baked is not None and baked == current:
        return Check("ren_native Rust backend", True,
                     f"built from this tree (source fingerprint {baked[:12]})",
                     need="ren_native")
    if current is None:
        detail = ("built, but this engine tree carries no native/ sources "
                  "to verify it against")
    else:
        detail = (f"stale - built from a different tree (fingerprint "
                  f"{(baked or 'none')[:12]} vs this tree {current[:12]}); "
                  f"the Python edit backend runs")
    return Check("ren_native Rust backend", False, detail,
                 "run ren setup to rebuild it from this engine's Rust sources "
                 "(needs cargo on PATH)",
                 need="ren_native")


def _foreign_broker_note(serving_command: str, own_binary) -> str:
    """The sentence that says the broker serving is NOT this engine's.

    The broker in service and the broker THIS engine would run are two
    binaries. On a machine with one engine they are the same file; where
    they are not, the machine is serving another engine's broker, and
    saying "Rust broker is built from this tree" about it is a false
    claim about the binary that is actually running (measured
    2026-10-21: the broker in service was another tree's build, while
    the doctor called it this tree's).

    Empty when the two are the same file, or when either is unknown.
    """
    if not serving_command or own_binary is None:
        return ""
    serving_path = serving_command.split()[0] if serving_command.split() else ""
    if not serving_path:
        return ""
    try:
        same = (os.path.realpath(serving_path)
                == os.path.realpath(str(own_binary)))
    except OSError:
        same = False
    if same:
        return ""
    return (
        "the broker serving this machine was built from ANOTHER tree - "
        f"this engine's own broker is {own_binary}")


def native_freshness_checks() -> list:
    """Are the binary native parts built from this tree?

    `ren_native` has its own baked-fingerprint check above; every binary
    part in native_refresh gets an explicit presence and freshness row.
    """
    from library.tools import native_refresh
    from ren.engine_root import require_engine_root

    try:
        root = require_engine_root()
    except Exception:  # noqa: BLE001 - doctor must finish
        return []
    checks = []
    try:
        broker_state, broker_detail = native_refresh.status(root, "ren-resolved")
        # The binary that SERVES, not the build output: the shipped
        # prebuilt is what a machine without a toolchain runs, and the
        # doctor must not call a broker missing while it is in service.
        broker_binary = native_refresh.serving_binary(root, "ren-resolved")
        serving_fallback = os.environ.get("REN_RESOLVED_DAEMON", "").lower() == "python"
        from library.tools.resolved.client import ping
        serving = ping()
        serving_binary = "no broker serving"
        serving_command = ""
        if serving is not None:
            command = subprocess.run(
                ["ps", "-p", str(serving["pid"]), "-o", "command="],
                capture_output=True, encoding="utf-8", timeout=3,
                check=False).stdout.strip()
            serving_binary = command or "broker process no longer listed"
            serving_command = command
        if broker_state == "fresh":
            detail = f"{serving_binary}; Rust broker is built from this tree"
            foreign = _foreign_broker_note(serving_command, broker_binary)
            if foreign:
                detail = f"{serving_binary}; {foreign}"
        elif (broker_binary is not None and broker_binary.is_file()) or serving_fallback:
            detail = (f"{serving_binary}; rebuild pending ({broker_state}: "
                      f"{broker_detail}); current binary remains in service")
        else:
            detail = (f"{serving_binary}; Rust broker missing ({broker_detail}); "
                      "Python fallback is available until a safe point builds it")
        checks.append(Check("native ren-resolved", True, detail))
    except Exception as exc:  # noqa: BLE001 - doctor must finish
        checks.append(Check("native ren-resolved", True,
                            f"freshness unavailable ({exc})"))
    for part, need in (("ren-graphics-fframes", "renderer.fframes"),
                       ("resolve-control", "resolve.control"),
                       ("ren-resolved", "resolve.broker"),
                       ("resolve-activity-overlay", "resolve.activity_overlay")):
        try:
            state, detail = native_refresh.status(root, part)
        except Exception as exc:  # noqa: BLE001 - doctor must finish
            state, detail = "missing", f"could not be checked ({exc})"
        if state == "fresh":
            checks.append(Check(
                f"native {part}", True, "built from this tree", need=need))
        elif state == "stale":
            checks.append(Check(
                f"native {part}", False,
                f"stale - {detail}; it would run code that predates this tree",
                "run ren setup, or the next safe point rebuilds it",
                need=need))
        else:
            checks.append(Check(
                f"native {part}", False,
                f"missing - {detail}",
                f"build it with {root}/scripts/build_{part.replace('-', '_')}.sh",
                need=need))
    return checks


# ── Configuration ────────────────────────────────────────────────────

def config_checks() -> list:
    from library.tools import paths
    source = paths.user_config_path()
    fix = f"set it in {source} (`ren config --init` writes a starter)"
    rows = (
        ("projects root", "PIPELINE_PROJECTS_ROOT", paths.PROJECTS_ROOT,
         "`ren init` creates it, or " + fix, "config.projects_root"),
        ("SFX library", "PIPELINE_SFX_LIBRARY", paths.SFX_LIBRARY, fix,
         "config.sfx_library"),
        ("music library", "PIPELINE_MUSIC_LIBRARY", paths.MUSIC_LIBRARY, fix,
         "config.music_library"),
    )
    checks = []
    for label, key, path, remedy, need in rows:
        origin = ("environment" if key not in paths.CONFIG_SOURCES and key in os.environ
                  else paths.CONFIG_SOURCES.get(key, "default"))
        exists = Path(path).is_dir()
        checks.append(Check(label, exists,
                            f"{path} (from {origin})" + ("" if exists else " does not exist"),
                            "" if exists else f"{key}: {remedy}", need=need))
    return checks


# ── The chat harness ─────────────────────────────────────────────────


def gemma_shim_check() -> list:
    """The on-demand Gemma shim agent: installed, and answering.

    Without the LaunchAgent nothing holds :8080, so every Gemma fallback
    loads its own private ~7 GB in-process copy - on 2026-10-22 three of
    them, force-quit by the captain. With it, launchd holds :8080 and
    starts the shim per connection (docs/GEMMA_SERVER.md §6). A missing
    agent is a WARNING line (the pipeline still runs, via the louder
    in-process fallback), never a failure of a need - so `need` is "".
    """
    import json as _json
    import urllib.request as _request

    from library.tools.gemma_shim import AGENT_LABEL
    from library.tools.gemma_shim import agent_plist_path

    installed = agent_plist_path().exists()
    try:
        with _request.urlopen("http://127.0.0.1:8080/_shim/status",
                               timeout=5) as resp:
            status = _json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - doctor reports, never raises
        status = None
    if status is not None:
        return [Check(
            "gemma shim agent", True,
            f"shim answering on :8080 (backend {status.get('backend')}, "
            f"agent {'installed' if installed else 'not installed'}; "
            f"nothing resident when the backend is stopped)",
            "")]
    if installed:
        return [Check(
            "gemma shim agent", True,
            f"LaunchAgent {AGENT_LABEL} installed; shim idle (launchd "
            f"starts it on the first request - nothing resident is the "
            f"healthy state)",
            "")]
    return [Check(
        "gemma shim agent", False,
        "shim agent not installed and nothing answering on :8080: every "
        "Gemma fallback loads a private ~7 GB in-process copy",
        "hand :8080 to launchd: ./scripts/gemma install-agent "
        "(docs/GEMMA_SERVER.md §6.2)")]


def harness_check() -> list:
    """One line per detected chat harness, or the install fix.

    The records live in `ren/hosts.py` - one adapter per supported host
    with its minimum version, its detection, its read-only auth check and
    where Ren installs its skills and marker hook. OAuth only: Ren is
    driven from a chat harness the person is signed in to, never through
    an API key (captain's phase-one scope: no direct API calls).
    """
    from ren.hosts import harness_checks

    return harness_checks()


# ── Hardware ─────────────────────────────────────────────────────────

def _total_memory_gb() -> float | None:
    """Total unified memory in GB, or None when it cannot be read.

    `sysctl hw.memsize` is the macOS spelling, and Ren runs on macOS only
    (`macos_check`). A machine that cannot answer is a MISS line, never a
    crashed doctor - `_guarded` wraps the check itself.
    """
    try:
        done = subprocess.run(["sysctl", "-n", "hw.memsize"],
                              capture_output=True, encoding="utf-8",
                              timeout=PROBE_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        return int(done.stdout.strip()) / 2**30
    except ValueError:
        return None


def memory_check() -> Check:
    """Can this machine hold the resident vision model plus the pipeline?

    The Gemma server measures 7.3-7.9 GB resident on the captain's 24 GB
    MacBook (docs/GEMMA_SERVER.md §5). Below the recommended floor the
    model swaps and the vision pass runs slow; below the minimum it cannot
    be held at all. The floors are declared in
    `library/tools/machine_needs.py`, not here.
    """
    from library.tools import machine_needs

    total = _total_memory_gb()
    if total is None:
        return Check("memory", False,
                     "could not read this machine's unified memory",
                     "run Ren on a Mac", need="hardware.memory")
    gemma = machine_needs.GEMMA_RESIDENT_GB
    if total >= machine_needs.MEMORY_RECOMMENDED_GB:
        return Check("memory", True,
                     f"{total:.1f} GB unified memory; the vision model "
                     f"resides at ~{gemma:.1f} GB, leaving "
                     f"{total - gemma:.1f} GB headroom",
                     need="hardware.memory",
                     measured={"total_memory_gb": total})
    if total >= machine_needs.MEMORY_MINIMUM_GB:
        return Check("memory", False,
                     f"{total:.1f} GB unified memory - below the "
                     f"{machine_needs.MEMORY_RECOMMENDED_GB} GB recommended "
                     f"floor; the vision model (~{gemma:.1f} GB resident) "
                     f"will swap: the vision pass runs, but slow",
                     "a machine with "
                     f"{machine_needs.MEMORY_RECOMMENDED_GB} GB or more is "
                     "the target",
                     need="hardware.memory",
                     measured={"total_memory_gb": total})
    return Check("memory", False,
                 f"{total:.1f} GB unified memory - below the "
                 f"{machine_needs.MEMORY_MINIMUM_GB} GB minimum; the vision "
                 f"model needs ~{gemma:.1f} GB resident and cannot be held",
                 "the vision pass cannot run on this machine",
                 need="hardware.memory",
                 measured={"total_memory_gb": total})


# ── Running it ───────────────────────────────────────────────────────

def macos_check() -> Check:
    if platform.system() != "Darwin":
        return Check("macOS", False, f"this is {platform.system()}; Ren runs on macOS only",
                     "run Ren on a Mac", need="macos")
    return Check("macOS", True, f"macOS {platform.mac_ver()[0]} ({platform.machine()})",
                 need="macos")


def edition_check() -> Check:
    """Report product edition and model implementation availability."""
    from library.tools.model_registry import installed_capability_report
    from ren.edition import current_edition, read_components

    edition = current_edition()
    model_edition = "commercial" if edition == "public" else "personal"
    report = installed_capability_report(
        set(), purpose=os.environ.get("REN_WORKLOAD_PURPOSE", "commercial_client"),
        edition_name=model_edition)
    counts = {status: sum(row["status"] == status
                          for row in report["implementations"])
              for status in ("available", "missing", "blocked", "unqualified")}
    restricted = [entry for entry in read_components().values()
                  if entry.edition == "personal-only"]
    if edition == "public":
        detail = (f"public; {len(restricted)} personal-only components are "
                  "blocked from packaging, fetching, and loading; model "
                  f"routes available={counts['available']}, missing={counts['missing']}, "
                  f"blocked={counts['blocked']}, unqualified={counts['unqualified']}")
    else:
        detail = (f"personal; personal-only components remain available "
                  f"({len(restricted)} inventoried); model routes "
                  f"available={counts['available']}, missing={counts['missing']}, "
                  f"blocked={counts['blocked']}, unqualified={counts['unqualified']}")
    return Check("Ren edition", True, detail)


def shadow_watcher_check() -> Check:
    """Report the shadow watcher's state: alive, running but not recording,
    or dead.

    The watcher is hosted by the broker - it starts when the broker
    starts and stops when the broker stops. It writes a heartbeat only
    after a successful observation; a stale heartbeat means ticks are
    failing (broker gone, Resolve closed). A dead watcher means captain
    edits and stray changes go unrecorded.
    """
    from library.tools import shadow_watcher
    heartbeat = shadow_watcher.read_heartbeat()
    if heartbeat is not None:
        if not heartbeat.get("project_folder"):
            detail = (f"watching '{heartbeat['project']}' - not a Ren project, "
                      f"nothing recorded")
            return Check("shadow watcher", False, detail,
                         "open a Ren project", need="shadow_watcher")
        detail = (f"alive (pid {heartbeat['pid']} watching "
                  f"{heartbeat['project'] or 'unknown'} at "
                  f"{heartbeat['project_folder']}, "
                  f"every {heartbeat['cadence']:.0f}s)")
        return Check("shadow watcher", True, detail, need="shadow_watcher")

    try:
        heartbeat_path = shadow_watcher.heartbeat_path()
        if heartbeat_path.exists():
            record = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            since = float(record.get("since", 0) or 0)
            pid = int(record.get("pid", 0) or 0)
            project = str(record.get("project", ""))
            age = time.time() - since if since else 0
            detail = (f"running but not recording - last successful "
                      f"observation {age:.0f}s ago (pid {pid} was watching "
                      f"{project or 'unknown'}); captain edits go unrecorded")
            return Check("shadow watcher", False, detail,
                         "check the broker and Resolve",
                         need="shadow_watcher")
    except (OSError, ValueError, TypeError):
        pass

    from library.tools.resolved import client
    if client.ping() is not None:
        return Check("shadow watcher", False,
                     "running but not recording - no successful observation yet",
                     "check the broker and Resolve", need="shadow_watcher")
    return Check("shadow watcher", False,
                 "dead - the broker is not serving; captain edits go unrecorded",
                 "start the broker (it starts automatically on the next ren command)",
                 need="shadow_watcher")


GROUPS = (
    # (name, the function that checks it, the needs its lines check)
    ("edition", "edition_check", ()),
    ("macOS", "macos_check", ("macos",)),
    ("disk space", "disk_space_check", ("disk.space",)),
    ("memory", "memory_check", ("hardware.memory",)),
    ("Resolve scripting", "resolve_checks", ("resolve.scripting", "resolve.studio")),
    ("Python 3.12 venv", "python_checks",
     ("python.venv", "python.core", "python.graphics", "python.analysis",
      "python.identity")),
    ("ffmpeg", "ffmpeg_check", ("ffmpeg",)),
    ("Node.js", "node_checks", ("node", "remotion")),
    ("graphics engines", "graphics_engine_checks", ("remotion",)),
    ("models", "model_checks",
     ("model.vision", "model.search_embedding", "model.beat_this", "model.panns")),
    ("transcription", "transcription_checks", ("transcriber.voz", "model.mfa")),
    ("dialogue cleanup DeepFilter", "deepfilternet_check", ("deepfilter",)),
    ("ren_native Rust backend", "ren_native_check", ("ren_native",)),
    ("native builds freshness", "native_freshness_checks",
     ("renderer.fframes", "resolve.control", "resolve.broker",
      "resolve.activity_overlay")),
    ("configuration", "config_checks",
     ("config.projects_root", "config.sfx_library", "config.music_library")),
    ("chat harness", "harness_check", ("chat_harness",)),
    ("gemma shim agent", "gemma_shim_check", ()),
    ("shadow watcher", "shadow_watcher_check", ("shadow_watcher",)),
)
"""Every check, in the order a new machine should fix them. A function is
looked up by name when it runs, so a test can stand one in."""


def _guarded(name: str, fn, needs: tuple):
    """A check that raises is a line per need naming the exception, never
    a traceback - and marked undetermined: the check could not determine
    the answer, which is not the same as the worker lacking the need."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - doctor must finish
        return [Check(name, False, f"the check itself failed: {type(exc).__name__}: {exc}",
                      "report this; the rest of the checks still ran", need=need,
                      undetermined=True)
                for need in needs or ("",)]
    return result if isinstance(result, list) else [result]


def run_checks(probe=None, needs=None) -> list:
    """Every check - or, given `needs`, only the lines checking one of them."""
    probe = probe or probe_resolve
    checks = []
    for name, attr, covers in GROUPS:
        if needs is not None and not set(covers) & set(needs):
            continue
        fn = globals()[attr]
        if attr == "resolve_checks":
            fn = (lambda check=fn: check(probe()))
        lines = _guarded(name, fn, covers)
        if needs is not None:
            lines = [line for line in lines if line.need in needs]
        checks += lines
    return checks


def required_failures(checks: list) -> list:
    """Failing lines every capability needs, or that name no need at all -
    a failure doctor cannot attribute is never quietly optional.

    An undetermined line is not here: a check that could not determine
    its answer is not a measured failure, and counting it as one is what
    made `ren doctor` report a capable machine as broken."""
    from library.tools.machine_needs import BASELINE
    return [check for check in checks
            if not check.ok and not check.undetermined
            and (check.need in BASELINE or not check.need)]


def capability_report(checks: list) -> dict:
    """`{capability: (verdict, missing, degraded, undetermined)}` from the
    checks - the needs that fail, and the needs the checks could not
    determine, which is what makes a verdict UNKNOWN rather than
    UNAVAILABLE."""
    from library.tools import machine_needs
    failing = {check.need for check in checks
               if not check.ok and not check.undetermined and check.need}
    undetermined = {check.need for check in checks
                    if check.undetermined and check.need}
    return {capability: machine_needs.availability(
        capability, failing, undetermined)
        for capability in sorted(machine_needs.CAPABILITY_NEEDS)}


def readiness(checks: list, capability: str | None = None) -> dict:
    """What this machine can actually perform, per capability.

    The join of three axes, each read from the registry that owns it
    (`library/tools/capability_readiness.py`): the DEPENDENCY axis the
    checks above already judged, the EXECUTOR axis (the qualification
    registry: can this machine run the capability's edit operations in
    production) and the RESOURCE axis (the scheduler's reservations).

    The doctor keeps its own reading of the dependency axis - a need no
    check answered is not a failure here - so the readiness row and the
    Capabilities table above it never disagree.
    """
    from library.tools import capability_readiness
    failing, undetermined, covered = capability_readiness.need_states(checks)
    environment = capability_readiness.environment_from_checks(checks)
    return capability_readiness.report(
        failing_needs=failing, undetermined_needs=undetermined,
        covered_needs=covered, environment=environment,
        capability_ids=[capability] if capability else None,
        require_measured=False)


def _readiness_lines(report: dict, limit: int = 12) -> list:
    """The readiness of every capability that is not simply available.

    Bounded on purpose: the JSON carries every row, and the prose
    carries the ones a reader can act on.
    """
    from library.tools import machine_needs
    rows = report["capabilities"]
    counts = report["summary"]["states"]
    order = (machine_needs.AVAILABLE, machine_needs.DEGRADED,
             machine_needs.UNKNOWN, machine_needs.UNAVAILABLE)
    width = max((len(row["id"]) for row in rows.values()), default=4)
    lines = ["", "Readiness (what this Mac can actually perform):"]
    lines.append("  " + ", ".join(f"{counts.get(state, 0)} {state}"
                                   for state in order)
                 + f" of {len(rows)} capabilities.")
    not_ready = [row for row in rows.values()
                 if row["state"] != machine_needs.AVAILABLE]
    for row in not_ready[:limit]:
        lines.append(f"  {row['state'].ljust(11)}  {row['id'].ljust(width)}  "
                     f"{row['reason']}")
    if len(not_ready) > limit:
        lines.append(f"  ... and {len(not_ready) - limit} more "
                     "(`ren doctor --json` names every one)")
    return lines


def _readiness_for(row: dict) -> list:
    """One capability's readiness, in full."""
    from library.tools import capability_readiness
    lines = [f"  readiness: {row['state']}"]
    if row["reason"]:
        lines.append(f"    {row['reason']}")
    features = row["features"]
    if features["operations"]:
        lines.append(f"    features: {len(features['ready'])} of "
                     f"{len(features['operations'])} declared edits can run "
                     "in production here")
        if features["unavailable"]:
            lines.append("    unavailable here: "
                         + ", ".join(features["unavailable"]))
        if features["unknown"]:
            lines.append("    undetermined here: "
                         + ", ".join(features["unknown"]))
        if features["conditions"]:
            lines.append("    timeline conditions (a property of the "
                         "timeline, not of this Mac; `resolve-axi support "
                         "<operation> --conditions ...` answers them):")
            grouped: dict = {}
            for condition in features["conditions"]:
                values = grouped.setdefault(condition["field"], [])
                if condition["value"] not in values:
                    values.append(condition["value"])
            for field, values in grouped.items():
                lines.append(f"      {field}: "
                             + ", ".join(str(value) for value in values))
    qualification = row["qualification"]
    if qualification["required_resolve_version"]:
        lines.append("    qualification: earned on Resolve "
                     + ", ".join(qualification["required_resolve_version"])
                     + "; this machine runs "
                     + (qualification["installed_resolve_version"]
                        or "an unread Resolve"))
    for reservation in row["resources"]:
        headroom = reservation["headroom_gb"]
        lines.append(f"    reservation: {reservation['profile']} holds "
                     f"{reservation['ram_gb']} GB; this machine keeps "
                     f"{'an unmeasured' if headroom is None else headroom} "
                     "GB above the scheduler's floor")
    if features["state"] == capability_readiness.NOT_APPLICABLE:
        lines.append("    features: the capability declares no edit "
                     "operation, so no executor runs here")
    return lines


def _line(check: Check, width: int) -> list:
    from library.tools.machine_needs import BASELINE
    if check.ok:
        mark = "PASS"
    elif check.undetermined:
        mark = "UNKNOWN"
    else:
        mark = "FAIL" if (check.need in BASELINE or not check.need) else "MISS"
    lines = [f"{mark}  {check.name.ljust(width)}  {check.detail}"]
    if not check.ok and check.fix:
        lines.append(f"      {' ' * width}  fix: {check.fix}")
    return lines


def summary_line(checks: list) -> str:
    """The one line the budgeted default keeps: counts, nothing else."""
    from library.tools import machine_needs
    report = capability_report(checks)
    by_verdict = {verdict: [c for c, (v, _, _, _) in report.items() if v == verdict]
                  for verdict in (machine_needs.UNAVAILABLE, machine_needs.UNKNOWN,
                                  machine_needs.DEGRADED, machine_needs.AVAILABLE)}
    failed = required_failures(checks)
    undetermined = sum(check.undetermined for check in checks)
    missing = sum(not check.ok and not check.undetermined for check in checks) - len(failed)
    return (f"{sum(check.ok for check in checks)} passed, {len(failed)} required "
            f"failed, {missing} missing, {undetermined} undetermined. Capabilities: "
            f"{len(by_verdict[machine_needs.AVAILABLE])} available, "
            f"{len(by_verdict[machine_needs.DEGRADED])} degraded, "
            f"{len(by_verdict[machine_needs.UNKNOWN])} unknown, "
            f"{len(by_verdict[machine_needs.UNAVAILABLE])} unavailable.")


def _readiness_brief(checks: list) -> str:
    """One line: how many capabilities this Mac can actually deliver.

    Empty when every capability can: a healthy machine says nothing
    about a thing that is not broken, and the budgeted default keeps
    only what needs acting on.
    """
    from library.tools import machine_needs
    report = readiness(checks)
    counts = report["summary"]["states"]
    total = len(report["capabilities"])
    ready = counts.get(machine_needs.AVAILABLE, 0)
    if ready == total:
        return ""
    parts = [f"{counts.get(state, 0)} {state}"
             for state in (machine_needs.UNAVAILABLE, machine_needs.UNKNOWN,
                           machine_needs.DEGRADED) if counts.get(state)]
    return (f"Readiness: {ready} of {total} capabilities can be delivered "
            "here (" + ", ".join(parts) + ").")


def render_brief(checks: list) -> str:
    """The budgeted default: only FAIL/WARN rows plus the summary line.

    LE-12: `ren doctor` defaulted to the full ledger (7,497 bytes
    measured) - every PASS line, the capabilities table and the help
    footer. The brief keeps what needs acting on (FAIL, MISS and
    UNKNOWN lines with their fixes) plus `summary_line`. `--all`
    restores `render`.
    """
    width = max((len(check.name) for check in checks), default=0)
    lines: list[str] = []
    for check in checks:
        if check.ok:
            continue
        lines += _line(check, width)
    if not lines:
        lines = ["All checks passed."]
    lines.append(summary_line(checks))
    brief = _readiness_brief(checks)
    if brief:
        lines.append(brief)
    lines.append("(`ren doctor --all` prints every check; `--json` prints the report.)")
    return "\n".join(lines)


def render(checks: list) -> str:
    from library.tools import machine_needs
    from library.tools.runtime_backends import doctor_line
    from ren.version import build_info
    info = build_info()
    width = max(len(check.name) for check in checks)
    lines = [f"ren doctor {info['version']} "
             f"({info['edition']} edition; {info['sha'] or 'unknown commit'}, "
             f"{info['channel']}) - "
             f"what can this Mac do with Ren? (read-only: changes nothing)",
             f"Python compatibility: {info['requires_python']}",
             doctor_line(),
             "FAIL: every capability needs it.  MISS: limits the capabilities below.  "
             "UNKNOWN: the check could not determine the answer.",
             f"Engine: {_engine_root()}",
             ""]
    for check in checks:
        lines += _line(check, width)

    report = capability_report(checks)
    by_verdict = {verdict: [c for c, (v, _, _, _) in report.items() if v == verdict]
                  for verdict in (machine_needs.UNAVAILABLE, machine_needs.UNKNOWN,
                                  machine_needs.DEGRADED, machine_needs.AVAILABLE)}
    cap_width = max(len(c) for c in report)
    lines += ["", "Capabilities:"]
    for capability in by_verdict[machine_needs.UNAVAILABLE]:
        missing = report[capability][1]
        lines.append(f"  unavailable  {capability.ljust(cap_width)}  "
                     f"missing {', '.join(missing)}")
    for capability in by_verdict[machine_needs.UNKNOWN]:
        undetermined = report[capability][3]
        lines.append(f"  unknown      {capability.ljust(cap_width)}  "
                     f"could not determine {', '.join(undetermined)}")
    for capability in by_verdict[machine_needs.DEGRADED]:
        degraded = report[capability][2]
        lines.append(f"  degraded     {capability.ljust(cap_width)}  "
                     + "; ".join(f"without {n}: {why}" for n, why in degraded.items()))
    if by_verdict[machine_needs.AVAILABLE]:
        lines += textwrap.wrap(", ".join(by_verdict[machine_needs.AVAILABLE]), 96,
                               initial_indent="  available    ",
                               subsequent_indent=" " * 15)

    failed = required_failures(checks)
    lines += ["", summary_line(checks),
              "`ren doctor --for <capability>` checks one capability's needs."]
    lines += _readiness_lines(readiness(checks))
    return "\n".join(lines)


def render_for(capability: str, checks: list) -> str:
    from library.tools import machine_needs
    failing = {check.need for check in checks
               if not check.ok and not check.undetermined and check.need}
    undetermined = {check.need for check in checks
                    if check.undetermined and check.need}
    verdict, missing, degraded, undetermined_missing = \
        machine_needs.availability(capability, failing, undetermined)
    needs = machine_needs.needs_of(capability)
    lines = [f"ren doctor --for {capability} (read-only: changes nothing)", ""]
    width = max((len(check.name) for check in checks), default=0)
    for check in checks:
        if check.ok:
            mark = "PASS"
        elif check.undetermined:
            mark = "UNKNOWN"
        else:
            mark = "FAIL" if check.need in needs.requires else "MISS"
        lines.append(f"{mark}  {check.name.ljust(width)}  {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"      {' ' * width}  fix: {check.fix}")
    lines.append("")
    if missing:
        lines.append(f"{capability}: {verdict} - missing {', '.join(missing)}")
    else:
        lines.append(f"{capability}: {verdict}")
    lines += [f"  without {need}: {why}" for need, why in degraded.items()]
    lines += [f"  could not determine {need}" for need in undetermined_missing]
    lines += _readiness_for(readiness(checks, capability)["capabilities"][capability])
    return "\n".join(lines)


def render_for_brief(capability: str, checks: list) -> str:
    """The budgeted `--for` default: only FAIL/WARN rows plus the verdict."""
    from library.tools import machine_needs
    failing = {check.need for check in checks
               if not check.ok and not check.undetermined and check.need}
    undetermined = {check.need for check in checks
                    if check.undetermined and check.need}
    verdict, missing, degraded, undetermined_missing = \
        machine_needs.availability(capability, failing, undetermined)
    needs = machine_needs.needs_of(capability)
    width = max((len(check.name) for check in checks), default=0)
    lines: list[str] = []
    for check in checks:
        if check.ok:
            continue
        if check.undetermined:
            mark = "UNKNOWN"
        else:
            mark = "FAIL" if check.need in needs.requires else "MISS"
        lines.append(f"{mark}  {check.name.ljust(width)}  {check.detail}")
        if check.fix:
            lines.append(f"      {' ' * width}  fix: {check.fix}")
    if not lines:
        lines = ["All checks passed."]
    if missing:
        lines.append(f"{capability}: {verdict} - missing {', '.join(missing)}")
    else:
        lines.append(f"{capability}: {verdict}")
    lines += [f"  without {need}: {why}" for need, why in degraded.items()]
    lines += [f"  could not determine {need}" for need in undetermined_missing]
    row = readiness(checks, capability)["capabilities"][capability]
    lines.append(f"  readiness: {row['state']}"
                 + (f" - {row['reason']}" if row["reason"] else ""))
    return "\n".join(lines)


def main(argv=None, probe=None) -> int:
    from ren.version import build_info
    engine_root = _engine_root()
    from library.tools import machine_needs
    from library.tools.runtime_backends import snapshot as runtime_backend_snapshot
    ren_build = {**build_info(), "engine_root": str(engine_root)}
    runtime_backends = runtime_backend_snapshot()
    parser = argparse.ArgumentParser(prog="ren doctor", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Print the checks as JSON")
    parser.add_argument("--all", action="store_true",
                        help="Print every check (today's default); without "
                             "it, print only FAIL/WARN rows plus one summary line")
    parser.add_argument(
        "--for", dest="capability", metavar="CAPABILITY",
        help="check only what one capability needs, and exit 1 if it is "
             "unavailable (`python3 -m library.tools.machine_needs` lists them)")
    args = parser.parse_args(argv)

    if args.capability:
        try:
            needs = machine_needs.needs_of(args.capability)
        except machine_needs.UnknownCapability as exc:
            parser.error(exc.args[0])
        checks = run_checks(probe, needs=set(needs.requires) | set(needs.degrades))
        failing = {check.need for check in checks
                   if not check.ok and not check.undetermined and check.need}
        undetermined = {check.need for check in checks
                        if check.undetermined and check.need}
        verdict, missing, degraded, undetermined_missing = \
            machine_needs.availability(args.capability, failing, undetermined)
        readiness_report = readiness(checks, args.capability)
        if args.json:
            print(json.dumps({"ren": ren_build,
                              "runtime_backends": runtime_backends,
                              "capability": args.capability, "verdict": verdict,
                              "missing": list(missing), "degraded": degraded,
                              "undetermined": list(undetermined_missing),
                              "readiness": readiness_report,
                              "checks": [asdict(check) for check in checks]}, indent=1))
        else:
            print(render_for(args.capability, checks) if args.all
                  else render_for_brief(args.capability, checks))
        # A capability this machine cannot DELIVER is not available, even
        # when every need it names is satisfied: the executor is what
        # does the work, and an executor that cannot run here is the
        # answer a caller needs.
        state = readiness_report["capabilities"][args.capability]["state"]
        return 1 if state == machine_needs.UNAVAILABLE else 0

    checks = run_checks(probe)
    failed = required_failures(checks)
    if args.json:
        print(json.dumps({
            "ren": ren_build,
            "runtime_backends": runtime_backends,
            "checks": [asdict(check) for check in checks],
            "required_failures": [check.name for check in failed],
            "capabilities": {c: {"verdict": v, "missing": list(m), "degraded": d,
                                "undetermined": list(u)}
                             for c, (v, m, d, u) in capability_report(checks).items()},
            "readiness": readiness(checks),
        }, indent=1))
    else:
        print(render(checks) if args.all else render_brief(checks))
    return 1 if failed else 0
