"""`ren doctor`: can this Mac run Ren? One line per check, PASS or FAIL, and the fix.

READ-ONLY. Doctor installs nothing, writes nothing, starts nothing. The
two things it has to ASK rather than look at are asked in child processes
with a timeout, so a hung or crashing dependency is a FAIL line, never a
hung or crashed doctor:

* Resolve - through a real scripting connection
  (`resolve_locale.scriptapp_preserving_locale`), because a module file on
  disk says nothing about whether the running app accepts scripts. Only
  getters are called: `GetProductName`, `GetVersionString`.
* The pipeline interpreter - resolved through `shared_environment`'s
  ladder, the same one `bin/vep` uses, and asked which of
  `requirements.txt` it satisfies. A package's presence is read from its
  installed metadata; nothing heavy is imported.

Studio only (captain, Q10): the free edition does not accept external
scripting, and every Ren operation that places, reads or renders a
timeline is an external script. Doctor names the edition it found.

Exit status is 1 when any check FAILs, 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ren import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_PYTHON = (3, 12)
MIN_NODE_MAJOR = 18
PROBE_TIMEOUT_S = 30

STUDIO_MARKER = "Studio"
RESOLVE_PROCESS = "Resolve"

FREE_EDITION_WHY = (
    "the free edition does not accept external scripting, and every Ren "
    "operation that builds, reads or renders a timeline is an external script")

MODELS = (
    # (label, what reads it, how it is stored, id)
    ("gemma-4-12b-it-4bit", "vision pass (step 1.03)", "hf",
     "mlx-community/gemma-4-12b-it-4bit"),
    ("faster-whisper large-v3", "transcription (step 1.04)", "hf",
     "Systran/faster-whisper-large-v3"),
    ("wav2vec2 aligner", "word timing (step 1.04)", "torch",
     "wav2vec2_fairseq_base_ls960_asr_ls960.pth"),
    ("all-MiniLM-L6-v2", "footage search (`ren search`)", "hf",
     "sentence-transformers/all-MiniLM-L6-v2"),
)

_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".npz", ".pt", ".pth", ".gguf")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


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
            [python, "-c", _RESOLVE_PROBE, str(REPO_ROOT)],
            capture_output=True, encoding="utf-8", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        result["error"] = f"the scripting connection did not answer in {timeout:.0f}s"
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
    return [connection, edition]


# ── Python ───────────────────────────────────────────────────────────

_PACKAGES_PROBE = r"""
import json, sys
from importlib import metadata
sys.path.insert(0, sys.argv[1])
out = {"version": list(sys.version_info[:3]), "missing": [], "wrong": [], "unchecked": []}
try:
    from packaging.requirements import Requirement
except ImportError:
    Requirement = None
for raw in open(sys.argv[2], encoding="utf-8"):
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    if Requirement is None:
        out["unchecked"].append(line)
        continue
    req = Requirement(line)
    if req.marker is not None and not req.marker.evaluate():
        continue
    try:
        have = metadata.version(req.name)
    except metadata.PackageNotFoundError:
        out["missing"].append(str(req))
        continue
    if req.specifier and not req.specifier.contains(have, prereleases=True):
        out["wrong"].append(f"{req.name} {have} (needs {req.specifier})")
try:
    from library.tools import shared_environment as se
    ok, why = se.face_detector_available()
    if not ok:
        out["face_detector"] = why.splitlines()[0]
except Exception as exc:
    out["face_detector"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""


def resolve_interpreter() -> tuple:
    from library.tools import shared_environment
    return shared_environment.python_interpreter(REPO_ROOT)


def python_checks() -> list:
    interpreter, why_not = resolve_interpreter()
    ml_doc = "docs/ML_ENVIRONMENT.md"
    if not interpreter:
        return [Check("Python 3.12 venv", False, why_not.splitlines()[0],
                      f"build the venv once per machine: {ml_doc}")]
    try:
        done = subprocess.run(
            [interpreter, "-c", _PACKAGES_PROBE, str(REPO_ROOT),
             str(REPO_ROOT / "requirements.txt")],
            capture_output=True, encoding="utf-8", timeout=120, check=False)
        report = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return [Check("Python 3.12 venv", False,
                      f"{interpreter} could not be asked about itself ({exc})",
                      f"rebuild the venv: {ml_doc}")]
    version = tuple(report["version"])
    shown = ".".join(str(part) for part in version)
    checks = [Check(
        "Python 3.12 venv", version[:2] == REQUIRED_PYTHON,
        f"{interpreter} is Python {shown}",
        "" if version[:2] == REQUIRED_PYTHON
        else f"rebuild the venv on Python 3.12 (requirements.txt says why): {ml_doc}")]

    problems = report["missing"] + report["wrong"]
    if report.get("face_detector"):
        problems.append(f"cv2 face detector: {report['face_detector']}")
    if report["unchecked"]:
        checks.append(Check("Python packages", False,
                            "cannot compare versions: `packaging` is not installed",
                            f"{interpreter} -m pip install packaging"))
    elif problems:
        checks.append(Check("Python packages", False,
                            f"{len(problems)} not satisfied: " + "; ".join(problems),
                            f"{interpreter} -m pip install -r requirements.txt"))
    else:
        checks.append(Check("Python packages", True,
                            "requirements.txt satisfied; cv2 face detector usable"))
    return checks


# ── Tools on PATH ────────────────────────────────────────────────────

def ffmpeg_check() -> Check:
    found = {tool: shutil.which(tool) for tool in ("ffmpeg", "ffprobe")}
    absent = [tool for tool, where in found.items() if not where]
    if absent:
        return Check("ffmpeg", False, f"not on PATH: {', '.join(absent)}",
                     "brew install ffmpeg")
    return Check("ffmpeg", True, f"{found['ffmpeg']}, {found['ffprobe']}")


def node_checks() -> list:
    node = shutil.which("node")
    if not node:
        return [Check("Node.js", False, "node is not on PATH", "brew install node"),
                Check("Remotion deps", False, "needs Node.js first", "brew install node")]
    try:
        version = subprocess.run([node, "--version"], capture_output=True,
                                 encoding="utf-8", timeout=10, check=False).stdout.strip()
        major = int(version.lstrip("v").split(".")[0])
    except (OSError, subprocess.SubprocessError, ValueError):
        version, major = "unreadable", 0
    checks = [Check("Node.js", major >= MIN_NODE_MAJOR, f"{node} {version}",
                    "" if major >= MIN_NODE_MAJOR
                    else f"install Node {MIN_NODE_MAJOR}+: brew install node")]

    from library.tools import shared_environment
    remotion = shared_environment.remotion_dir(REPO_ROOT)
    if shared_environment.dependencies_present(remotion):
        checks.append(Check("Remotion deps", True,
                            f"{shared_environment.node_modules(remotion)}"))
    else:
        checks.append(Check("Remotion deps", False,
                            f"no node_modules bound at {remotion}",
                            f"bash {shared_environment.INSTALL_SCRIPT}"))
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
    checks = []
    try:
        from library.tools import shared_environment
        remotion_ok = shared_environment.dependencies_present(
            shared_environment.remotion_dir(REPO_ROOT))
    except Exception:
        remotion_ok = False
    checks.append(Check(
        "graphics Remotion", remotion_ok,
        "the default engine (selected when nothing else is)"
        if remotion_ok else "the default engine is not installed",
        "" if remotion_ok else
        f"bash {shared_environment.INSTALL_SCRIPT}"))
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
    checks = []
    cache = hf_hub_cache()
    for label, reader, store, ident in MODELS:
        if store == "hf":
            complete, size, why = hf_model_state(ident, cache)
            fix = (f"{sys.executable} -c \"from huggingface_hub import "
                   f"snapshot_download; snapshot_download('{ident}')\"")
        else:
            path = torch_checkpoints() / ident
            complete = path.is_file()
            size = path.stat().st_size if complete else 0
            why = "not downloaded"
            fix = "downloaded by whisperx on the first transcription"
        detail = f"{reader}: {_gb(size)}" if complete else f"{reader}: {why}"
        checks.append(Check(f"model {label}", complete, detail, "" if complete else fix))

    from library.tools import shared_environment
    mfa_ok, _ = shared_environment.mfa_available()
    # Optional by design: without MFA the wav2vec2 aligner above takes over
    # (library/tools/mfa_align.py), so its absence does not stop a run.
    checks.append(Check(
        "model MFA aligner", True,
        "forced aligner (step 1.04)" if mfa_ok else
        f"not installed - optional, wav2vec2 aligns instead "
        f"(bash {shared_environment.MFA_INSTALL_SCRIPT} adds it)"))

    # beat_this weights (detected downbeats, step 2.06). Optional by
    # design like MFA above: without the checkpoint the tracker cannot
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
        "model beat_this final0", True,
        f"detected downbeats (step 2.06): {_gb(bt_path.stat().st_size)}"
        if bt_complete else
        "not downloaded - downbeats fall back to the librosa "
        "every-4th-beat estimate (labelled estimated)",
        "" if bt_complete else f"fetch it (~81 MB): {bt_fetch}"))

    # PANNs weights (timed sound events, step 1.04). Optional by
    # design like the halves above: without the checkpoint a clip
    # records `sound_event_method: unmeasured` with the reason and an
    # event anchor on it refuses by name - so a run still completes,
    # with no event layer to address.
    panns_ok, _ = shared_environment.panns_available()
    panns_path = shared_environment.panns_checkpoint()
    checks.append(Check(
        "model PANNs Cnn14-DLM", True,
        f"timed sound events (step 1.04): "
        f"{_gb(panns_path.stat().st_size)}"
        if panns_ok else
        "not downloaded - clips record unmeasured sound events and "
        "event anchors refuse by name "
        f"(bash {shared_environment.PANNS_INSTALL_SCRIPT} adds it, "
        f"~327 MB)",
        "" if panns_ok else
        f"bash {shared_environment.PANNS_INSTALL_SCRIPT}"))
    return checks


# ── Dialogue cleanup ─────────────────────────────────────────────

def deepfilternet_check() -> Check:
    """Can a deepfilternet request stage a stem on this machine?

    Optional by design like MFA above: without the binary the request
    refuses by name and the model re-plans with voice_isolation
    (library/tools/dialogue_cleanup.py), so absence is reported, never
    a FAIL. A binary that is present but would not run IS a FAIL - the
    plan context would claim the tool and the build would refuse it.
    """
    from library.tools import shared_environment
    usable, _ = shared_environment.deepfilter_available()
    script = shared_environment.DEEPFILTER_INSTALL_SCRIPT
    if not usable:
        return Check(
            "dialogue cleanup DeepFilter", True,
            "not installed - a deepfilternet request refuses by name and "
            "the model re-plans with voice_isolation",
            f"bash {script} adds it")
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
                     f"re-run bash {script}")
    return Check("dialogue cleanup DeepFilter", True,
                 f"{version} at {binary}")


# ── Configuration ────────────────────────────────────────────────────

def config_checks() -> list:
    from library.tools import paths
    source = paths.user_config_path()
    fix = f"set it in {source} (`ren config --init` writes a starter)"
    rows = (
        ("projects root", "PIPELINE_PROJECTS_ROOT", paths.PROJECTS_ROOT,
         "`ren init` creates it, or " + fix),
        ("SFX library", "PIPELINE_SFX_LIBRARY", paths.SFX_LIBRARY, fix),
        ("music library", "PIPELINE_MUSIC_LIBRARY", paths.MUSIC_LIBRARY, fix),
    )
    checks = []
    for label, key, path, remedy in rows:
        origin = ("environment" if key not in paths.CONFIG_SOURCES and key in os.environ
                  else paths.CONFIG_SOURCES.get(key, "default"))
        exists = Path(path).is_dir()
        checks.append(Check(label, exists,
                            f"{path} (from {origin})" + ("" if exists else " does not exist"),
                            "" if exists else f"{key}: {remedy}"))
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
        return Check("chat harness", True, ", ".join(oauth) + others)
    return Check("chat harness", False,
                 ("no harness signed in with a subscription" + others)
                 if (found or claude or opencode or codex)
                 else "no chat harness installed (Claude Code, OpenCode or Codex)",
                 "install Claude Code and run `claude` to sign in with your Claude account "
                 "(an API key does not count)")


# ── Running it ───────────────────────────────────────────────────────

def macos_check() -> Check:
    if platform.system() != "Darwin":
        return Check("macOS", False, f"this is {platform.system()}; Ren runs on macOS only",
                     "run Ren on a Mac")
    return Check("macOS", True, f"macOS {platform.mac_ver()[0]} ({platform.machine()})")


def _guarded(name: str, fn):
    """A check that raises is a FAIL line naming the exception, never a traceback."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - doctor must finish
        return [Check(name, False, f"the check itself failed: {type(exc).__name__}: {exc}",
                      "report this; the rest of the checks still ran")]
    return result if isinstance(result, list) else [result]


def run_checks(probe=None) -> list:
    """Every check, in the order a new machine should fix them."""
    probe = probe or probe_resolve
    checks = []
    checks += _guarded("macOS", macos_check)
    checks += _guarded("Resolve scripting", lambda: resolve_checks(probe()))
    checks += _guarded("Python 3.12 venv", python_checks)
    checks += _guarded("ffmpeg", ffmpeg_check)
    checks += _guarded("Node.js", node_checks)
    checks += _guarded("graphics engines", graphics_engine_checks)
    checks += _guarded("models", model_checks)
    checks += _guarded("dialogue cleanup DeepFilter", deepfilternet_check)
    checks += _guarded("configuration", config_checks)
    checks += _guarded("chat harness", harness_check)
    return checks


def render(checks: list) -> str:
    width = max(len(check.name) for check in checks)
    lines = ["ren doctor - can this Mac run Ren? (read-only: changes nothing)", ""]
    for check in checks:
        lines.append(f"{'PASS' if check.ok else 'FAIL'}  {check.name.ljust(width)}  {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"      {' ' * width}  fix: {check.fix}")
    failed = sum(not check.ok for check in checks)
    lines += ["", f"{len(checks) - failed} passed, {failed} failed."]
    return "\n".join(lines)


def main(argv=None, probe=None) -> int:
    parser = argparse.ArgumentParser(prog="ren doctor", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Print the checks as JSON")
    args = parser.parse_args(argv)
    checks = run_checks(probe)
    if args.json:
        print(json.dumps([asdict(check) for check in checks], indent=1))
    else:
        print(render(checks))
    return 1 if any(not check.ok for check in checks) else 0
