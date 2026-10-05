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
from dataclasses import asdict, dataclass
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
                           "Resolve lease; run `ren doctor` again when it is idle")
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
        connection = Check("Resolve scripting", False, why, enable)

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
                        "edition, install DaVinci Resolve Studio")
    connection.need, edition.need = "resolve.scripting", "resolve.studio"
    return [connection, edition]


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
    ml_doc = "docs/ML_ENVIRONMENT.md"
    if not interpreter:
        return [Check(python_label, False, why_not.splitlines()[0],
                      f"build the venv once per machine: {ml_doc}",
                      need="python.venv")]
    try:
        done = subprocess.run(
            [interpreter, "-c", _PACKAGES_PROBE, str(_engine_root()), *RUNTIME_GROUPS],
            capture_output=True, encoding="utf-8", timeout=120, check=False)
        report = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return [Check(python_label, False,
                      f"{interpreter} could not be asked about itself ({exc})",
                      f"rebuild the venv: {ml_doc}", need="python.venv")]
    version = tuple(report["version"])
    shown = ".".join(str(part) for part in version)
    checks = [Check(
        python_label, version[:2] == PYTHON_VERSION,
        f"{interpreter} is Python {shown}",
        "" if version[:2] == PYTHON_VERSION
        else f"rebuild the venv for {REQUIRES_PYTHON}: {ml_doc}",
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
                                f"{interpreter} -m pip install packaging", need=need))
        elif problems:
            checks.append(Check(name, False,
                                f"{len(problems)} not satisfied: " + "; ".join(problems),
                                f"{interpreter} -m pip install -r {source}", need=need))
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
                     "brew install ffmpeg", need="ffmpeg")
    return Check("ffmpeg", True, f"{found['ffmpeg']}, {found['ffprobe']}",
                 need="ffmpeg")


def node_checks() -> list:
    from ren.edition import PUBLIC, current_edition

    public = current_edition() == PUBLIC
    node = shutil.which("node")
    if not node:
        checks = [Check("Node.js", False, "node is not on PATH", "brew install node",
                        need="node")]
        if public:
            checks.append(Check("HyperFrames renderer", False,
                                "needs Node.js and npx first", "brew install node",
                                need="remotion"))
        else:
            checks.append(Check("Remotion deps", False, "needs Node.js first",
                                "brew install node", need="remotion"))
        return checks
    try:
        version = subprocess.run([node, "--version"], capture_output=True,
                                 encoding="utf-8", timeout=10, check=False).stdout.strip()
        major = int(version.lstrip("v").split(".")[0])
    except (OSError, subprocess.SubprocessError, ValueError):
        version, major = "unreadable", 0
    checks = [Check("Node.js", major >= MIN_NODE_MAJOR, f"{node} {version}",
                    "" if major >= MIN_NODE_MAJOR
                    else f"install Node {MIN_NODE_MAJOR}+: brew install node",
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
                                f"bash {shared_environment.INSTALL_SCRIPT}",
                                need="remotion"))
    return checks


def graphics_engine_checks() -> list:
    """Which graphics engines can draw: Remotion, HyperFrames, and the pick.

    Remotion stays the default engine, so its absence is a FAIL -
    nothing renders without it. HyperFrames is the opt-in second
    engine (`PIPELINE_GRAPHICS_RENDERER=hyperframes` in
    `~/.config/ren/config.env`, or `pipeline.graphics_renderer` in a
    project's `project.yaml`); its absence is reported, never a FAIL,
    because no run needs it unless it is selected. The check itself is
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
                (f"selected public renderer; npx fetches the pinned CLI "
                 f"on the first render ({selected})") if npx else
                "selected public renderer, but npx is not on PATH",
                "install Node.js (includes npx): brew install node" if not npx else "",
                need="remotion"))
            checks.append(Check("graphics selected", selected == "hyperframes",
                                f"{selected} (public edition)",
                                "fix the public renderer selection",
                                need="remotion"))
        except Exception as exc:  # noqa: BLE001 - doctor must finish
            checks.append(Check("graphics selected", False,
                                f"the public renderer cannot be selected: {exc}",
                                "select the public HyperFrames provider",
                                need="remotion"))
        return checks
    try:
        from library.tools import shared_environment
        remotion_ok = shared_environment.dependencies_present(
            shared_environment.remotion_dir(_engine_root()))
    except Exception:  # noqa: BLE001 - doctor must finish
        remotion_ok = False
    checks.append(Check(
        "graphics Remotion", remotion_ok,
        "the default engine (selected when nothing else is)"
        if remotion_ok else "the default engine is not installed",
        "" if remotion_ok else
        f"bash {shared_environment.INSTALL_SCRIPT}", need="remotion"))
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


def _gb(size: int) -> str:
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
        detail = f"{reader}: {_gb(size)}" if complete else f"{reader}: {why}"
        checks.append(Check(f"model {label}", complete, detail,
                            "" if complete else fix, need=need))

    # Optional by design: ren search uses lexical-only search when neither
    # embedding backend can load (library/tools/analysis/footage_query.py).
    ident = "sentence-transformers/all-MiniLM-L6-v2"
    complete, size, why = hf_model_state(ident, cache)
    checks.append(Check(
        "model all-MiniLM-L6-v2", complete,
        f"footage search embedder: {_gb(size)}" if complete else
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
        f"detected downbeats (step 2.06): {_gb(bt_path.stat().st_size)}"
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
        f"{_gb(panns_path.stat().st_size)}"
        if panns_ok else
        "not downloaded - clips record unmeasured sound events and "
        "event anchors refuse by name "
        f"(bash {shared_environment.PANNS_INSTALL_SCRIPT} adds it, "
        f"~327 MB)",
        "" if panns_ok else
        f"bash {shared_environment.PANNS_INSTALL_SCRIPT}",
        need="model.panns"))
    return checks


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
        f"bash {shared_environment.MFA_INSTALL_SCRIPT}" if not mfa_ok else "",
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
    script = shared_environment.DEEPFILTER_INSTALL_SCRIPT
    if not usable:
        return Check(
            "dialogue cleanup DeepFilter", False,
            "not installed - a deepfilternet request refuses by name and "
            "the model re-plans with voice_isolation",
            f"bash {script} adds it", need="deepfilter")
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
                     f"re-run bash {script}", need="deepfilter")
    return Check("dialogue cleanup DeepFilter", True,
                 f"{version} at {binary}", need="deepfilter")


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

_OAUTH_CLAUDE_METHODS = ("claude.ai", "oauth", "oauth_token")


def harness_check() -> Check:
    """Is a chat harness signed in with a SUBSCRIPTION (OAuth)?

    OAuth only: Ren is driven from a chat harness the person is signed in
    to, never through an API key (captain's phase-one scope: no direct
    API calls). Each harness reports its own sign-in; no request is sent
    to any model.
    """
    found = []
    oauth = []

    claude = shutil.which("claude")
    if claude:
        try:
            done = subprocess.run([claude, "auth", "status"], capture_output=True,
                                  encoding="utf-8", timeout=30, check=False)
            status = json.loads(done.stdout)
            method = status.get("authMethod", "") if status.get("loggedIn") else ""
        except (OSError, subprocess.SubprocessError, ValueError):
            method = ""
        if method in _OAUTH_CLAUDE_METHODS:
            plan = status.get("subscriptionType", "")
            oauth.append(f"Claude Code ({method} OAuth{', ' + plan if plan else ''})")
        else:
            found.append(f"Claude Code ({method or 'not signed in'})")

    opencode = shutil.which("opencode")
    if opencode:
        auth = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") \
            / "opencode" / "auth.json"
        try:
            kinds = {name: (entry or {}).get("type", "")
                     for name, entry in json.loads(auth.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            kinds = {}
        oauth_providers = sorted(name for name, kind in kinds.items() if kind == "oauth")
        if oauth_providers:
            oauth.append(f"OpenCode (OAuth: {', '.join(oauth_providers)})")
        else:
            found.append("OpenCode (" + (", ".join(f"{n}: {k}" for n, k in sorted(kinds.items()))
                                          or "not signed in") + ")")

    codex = shutil.which("codex")
    if codex:
        try:
            said = subprocess.run([codex, "login", "status"], capture_output=True,
                                  encoding="utf-8", timeout=30, check=False)
            text = (said.stdout + said.stderr).strip()
        except (OSError, subprocess.SubprocessError):
            text = ""
        if "ChatGPT" in text:
            oauth.append("Codex (ChatGPT sign-in)")
        else:
            found.append(f"Codex ({text.splitlines()[0] if text else 'not signed in'})")

    others = f"; not counted: {', '.join(found)}" if found else ""
    if oauth:
        return Check("chat harness", True, ", ".join(oauth) + others,
                     need="chat_harness")
    return Check("chat harness", False,
                 ("no harness signed in with a subscription" + others)
                 if (found or claude or opencode or codex)
                 else "no chat harness installed (Claude Code, OpenCode or Codex)",
                 "install Claude Code and run `claude` to sign in with your Claude account "
                 "(an API key does not count)", need="chat_harness")


# ── Running it ───────────────────────────────────────────────────────

def macos_check() -> Check:
    if platform.system() != "Darwin":
        return Check("macOS", False, f"this is {platform.system()}; Ren runs on macOS only",
                     "run Ren on a Mac", need="macos")
    return Check("macOS", True, f"macOS {platform.mac_ver()[0]} ({platform.machine()})",
                 need="macos")


def edition_check() -> Check:
    """Report the immutable product edition and restricted-component count."""
    from ren.edition import current_edition, read_components

    edition = current_edition()
    restricted = [entry for entry in read_components().values()
                  if entry.edition == "personal-only"]
    if edition == "public":
        detail = (f"public; {len(restricted)} personal-only components are "
                  "blocked from packaging, fetching, and loading")
    else:
        detail = (f"personal; personal-only components remain available "
                  f"({len(restricted)} inventoried)")
    return Check("Ren edition", True, detail)


GROUPS = (
    # (name, the function that checks it, the needs its lines check)
    ("edition", "edition_check", ()),
    ("macOS", "macos_check", ("macos",)),
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
    ("configuration", "config_checks",
     ("config.projects_root", "config.sfx_library", "config.music_library")),
    ("chat harness", "harness_check", ("chat_harness",)),
)
"""Every check, in the order a new machine should fix them. A function is
looked up by name when it runs, so a test can stand one in."""


def _guarded(name: str, fn, needs: tuple):
    """A check that raises is a line per need naming the exception, never a traceback."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - doctor must finish
        return [Check(name, False, f"the check itself failed: {type(exc).__name__}: {exc}",
                      "report this; the rest of the checks still ran", need=need)
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
    a failure doctor cannot attribute is never quietly optional."""
    from library.tools.machine_needs import BASELINE
    return [check for check in checks
            if not check.ok and (check.need in BASELINE or not check.need)]


def capability_report(checks: list) -> dict:
    """`{capability: (verdict, missing, degraded)}` from the failing needs."""
    from library.tools import machine_needs
    failing = {check.need for check in checks if not check.ok and check.need}
    return {capability: machine_needs.availability(capability, failing)
            for capability in sorted(machine_needs.CAPABILITY_NEEDS)}


def _line(check: Check, width: int) -> list:
    from library.tools.machine_needs import BASELINE
    if check.ok:
        mark = "PASS"
    else:
        mark = "FAIL" if (check.need in BASELINE or not check.need) else "MISS"
    lines = [f"{mark}  {check.name.ljust(width)}  {check.detail}"]
    if not check.ok and check.fix:
        lines.append(f"      {' ' * width}  fix: {check.fix}")
    return lines


def render(checks: list) -> str:
    from library.tools import machine_needs
    from ren.version import build_info
    info = build_info()
    width = max(len(check.name) for check in checks)
    lines = [f"ren doctor {info['version']} "
             f"({info['edition']} edition; {info['sha'] or 'unknown commit'}, "
             f"{info['channel']}) - "
             f"what can this Mac do with Ren? (read-only: changes nothing)",
             f"Python compatibility: {info['requires_python']}",
             "FAIL: every capability needs it.  MISS: limits the capabilities below.",
             f"Engine: {_engine_root()}",
             ""]
    for check in checks:
        lines += _line(check, width)

    report = capability_report(checks)
    by_verdict = {verdict: [c for c, (v, _, _) in report.items() if v == verdict]
                  for verdict in (machine_needs.UNAVAILABLE, machine_needs.DEGRADED,
                                  machine_needs.AVAILABLE)}
    cap_width = max(len(c) for c in report)
    lines += ["", "Capabilities:"]
    for capability in by_verdict[machine_needs.UNAVAILABLE]:
        missing = report[capability][1]
        lines.append(f"  unavailable  {capability.ljust(cap_width)}  "
                     f"missing {', '.join(missing)}")
    for capability in by_verdict[machine_needs.DEGRADED]:
        degraded = report[capability][2]
        lines.append(f"  degraded     {capability.ljust(cap_width)}  "
                     + "; ".join(f"without {n}: {why}" for n, why in degraded.items()))
    if by_verdict[machine_needs.AVAILABLE]:
        lines += textwrap.wrap(", ".join(by_verdict[machine_needs.AVAILABLE]), 96,
                               initial_indent="  available    ",
                               subsequent_indent=" " * 15)

    failed = required_failures(checks)
    missing = sum(not check.ok for check in checks) - len(failed)
    lines += ["",
              (f"{sum(check.ok for check in checks)} passed, {len(failed)} required "
               f"failed, {missing} missing. Capabilities: "
               f"{len(by_verdict[machine_needs.AVAILABLE])} available, "
               f"{len(by_verdict[machine_needs.DEGRADED])} degraded, "
               f"{len(by_verdict[machine_needs.UNAVAILABLE])} unavailable."),
              "`ren doctor --for <capability>` checks one capability's needs."]
    return "\n".join(lines)


def render_for(capability: str, checks: list) -> str:
    from library.tools import machine_needs
    verdict, missing, degraded = machine_needs.availability(
        capability, {check.need for check in checks if not check.ok})
    needs = machine_needs.needs_of(capability)
    lines = [f"ren doctor --for {capability} (read-only: changes nothing)", ""]
    width = max((len(check.name) for check in checks), default=0)
    for check in checks:
        mark = "PASS" if check.ok else (
            "FAIL" if check.need in needs.requires else "MISS")
        lines.append(f"{mark}  {check.name.ljust(width)}  {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"      {' ' * width}  fix: {check.fix}")
    lines.append("")
    if missing:
        lines.append(f"{capability}: {verdict} - missing {', '.join(missing)}")
    else:
        lines.append(f"{capability}: {verdict}")
    lines += [f"  without {need}: {why}" for need, why in degraded.items()]
    return "\n".join(lines)


def main(argv=None, probe=None) -> int:
    from ren.version import build_info
    engine_root = _engine_root()
    from library.tools import machine_needs
    ren_build = {**build_info(), "engine_root": str(engine_root)}
    parser = argparse.ArgumentParser(prog="ren doctor", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Print the checks as JSON")
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
        verdict, missing, degraded = machine_needs.availability(
            args.capability, {check.need for check in checks if not check.ok})
        if args.json:
            print(json.dumps({"ren": ren_build,
                              "capability": args.capability, "verdict": verdict,
                              "missing": list(missing), "degraded": degraded,
                              "checks": [asdict(check) for check in checks]}, indent=1))
        else:
            print(render_for(args.capability, checks))
        return 1 if verdict == machine_needs.UNAVAILABLE else 0

    checks = run_checks(probe)
    failed = required_failures(checks)
    if args.json:
        print(json.dumps({
            "ren": ren_build,
            "checks": [asdict(check) for check in checks],
            "required_failures": [check.name for check in failed],
            "capabilities": {c: {"verdict": v, "missing": list(m), "degraded": d}
                             for c, (v, m, d) in capability_report(checks).items()},
        }, indent=1))
    else:
        print(render(checks))
    return 1 if failed else 0
