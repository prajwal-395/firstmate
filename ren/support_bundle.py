"""`ren support-bundle`: one redacted archive that diagnoses most failures.

READ-ONLY against the project and the machine: it reads, redacts and
zips; it installs nothing, writes nothing into the project, starts
nothing. What it collects is an allowlist, so footage, transcripts
and prompts can never slip in by default - they are never read at all:

* `ren.json` - the Ren version/build, when the bundle was made, the
  machine (macOS, arch) and the project it covers, if any.
* `config.json` - the effective machine settings as `ren config`
  reports them (secrets already reduced to `(set)` there).
* `doctor.json` - the full `ren doctor --json` report.
* `packages.json` - exact installed versions of every declared
  runtime dependency in Ren's selected Python environment.
* `models.json` - expected model ids and pinned revisions, plus which
  local model packs are present.
* `errors.jsonl` - the most recent structured error events from
  `logs/pipeline_log.jsonl` (current plus rotated backups, newest
  last), carrying run ids and refusal codes when recorded.
* `manifests.json` - `assembly_manifest.json`, `project.yaml` and
  `pipeline_run.json` when the bundle covers a project. Speech and
  prompt content fields are omitted from those records.

Every string that reaches the archive is redacted: the home directory
folds to `~`, other macOS and Linux user roots fold to a generic user
component, and token-shaped values (API keys, HuggingFace/bearer tokens,
password or secret assignments) become `<redacted>`. The bundle prints
its own member list, so what it carries is auditable before it is sent.

A project is optional: `ren support-bundle` with none covers the
machine only, which is the shape an install failure needs. Name a
slug or an absolute project path to cover a project too.
"""

from __future__ import annotations

import contextlib
import argparse
import datetime
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from ren import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ERROR_EVENT_TAIL = 200
LOG_SCAN_BYTES = 5_000_000
MANIFEST_SIZE_GUARD = 25_000_000

_TOKEN_PATTERNS = (
    re.compile(r"\b(hf_[A-Za-z0-9]+|sk-[A-Za-z0-9_-]+|sk-ant-[A-Za-z0-9_-]+)\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b(ghp_[A-Za-z0-9]+|gho_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+)\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\b(xox[bap]-[A-Za-z0-9-]+)\b"),
    re.compile(r"(?i)\b(api[_-]?key|auth[_-]?token|access[_-]?token|"
               r"client[_-]?secret|hf[_-]?token|token|password|secret|"
               r"credential|authorization|signature|sig)\b"
               r"\s*[:=]\s*['\"]?([^'\"\s,}]+)['\"]?"),
)
_USER_ROOT = os.sep + "Users" + os.sep
_HOME_ROOT = os.sep + "home" + os.sep
_USER_PATH = re.compile(re.escape(_USER_ROOT) + r"[^/\s\"']+")
_HOME_PATH = re.compile(re.escape(_HOME_ROOT) + r"[^/\s\"']+")
_SECRET_KEY = re.compile(
    r"(?i)(?:^|[_-])(api[_-]?key|auth[_-]?token|access[_-]?token|"
    r"client[_-]?secret|hf[_-]?token|token|password|secret|"
    r"credential|authorization|signature|sig)(?:$|[_-])")
_PRIVATE_CONTENT_KEY = re.compile(
    r"(?i)(transcript|prompt|speech|dialogue|narration|script|"
    r"caption|word|sentence|spoken_text|source_text)")


def redact_text(text: str) -> str:
    """Fold the home directory, other users' names and token-shaped
    values. Best-effort by design: it must never fail the bundle, and
    what it cannot recognise it leaves - the member list says what was
    collected, so the sender can check."""
    try:
        home = os.path.expanduser("~")
        if home and home != "~" and home in text:
            text = text.replace(home, "~")
        text = _USER_PATH.sub(_USER_ROOT + "<user>", text)
        text = _HOME_PATH.sub(_HOME_ROOT + "<user>", text)
        for pattern in _TOKEN_PATTERNS:
            text = pattern.sub("<redacted>", text)
        return text
    except Exception:  # noqa: BLE001 - redaction must never fail the bundle
        return text


def redact(obj):
    """`redact_text` over every string in a JSON-shaped structure."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, dict):
        return {key: ("<redacted>" if _SECRET_KEY.search(str(key))
                      else redact(value))
                for key, value in obj.items()}
    return obj


def resolve_project(ref) -> Path:
    """A slug under the projects root, or an absolute project path -
    the same addressing `ren run`, `status` and `info` accept. Raises
    `RenRefusal` (what/why/fix) when nothing there is a project."""
    from library.tools import paths as project_paths

    candidate = Path(ref).expanduser()
    if candidate.is_absolute():
        project_dir = candidate
    else:
        project_dir = project_paths.PROJECTS_ROOT / ref
    if not project_dir.is_dir() or not (project_dir / "project.yaml").is_file():
        from library.tools.ren_refusal import RenRefusal
        raise RenRefusal(
            f"no project {ref!r}",
            f"{project_dir} has no project.yaml",
            "run `ren projects` to list projects, or pass the "
            "project's absolute path")
    return project_dir


def ren_section(project: str | None) -> dict:
    """`ren.json`: which build this bundle diagnoses."""
    from ren.version import build_info, version_string

    build = build_info()
    section = {
        "ren_build": version_string(),
        "build": build,
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "machine": {
            "system": platform.system(),
            "mac_ver": platform.mac_ver()[0],
            "arch": platform.machine(),
        },
        "project": project,
    }
    return section


def config_section() -> dict:
    """`config.json`: the effective settings, secrets already reduced."""
    from ren import config as ren_config
    rows = ren_config.effective_settings(dict(os.environ))
    return {"settings": [
        {"key": key, "value": value, "source": source, "meaning": meaning}
        for key, value, source, meaning in rows]}


def doctor_section() -> dict:
    """`doctor.json`: exactly the report `ren doctor --json` prints."""
    from ren import doctor as ren_doctor
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ren_doctor.main(["--json"])
    return json.loads(output.getvalue())


def packages_section() -> dict:
    """`packages.json`: exact installed versions from Ren's ML Python."""
    from library.tools import dependency_groups
    from ren import doctor as ren_doctor

    packages: dict = {}
    names_by_group: dict[str, list[str]] = {}
    all_names: list[str] = []
    for group in dependency_groups.RUNTIME_GROUPS:
        rows = []
        for line in dependency_groups.declared_requirements(
                dependency_groups.group_file(group)):
            name = re.split(r"[<>=!~\s\[]", line.strip(), maxsplit=1)[0].strip()
            if not name or name.startswith("#"):
                continue
            if name not in all_names:
                all_names.append(name)
            if name not in [item["name"] for item in rows]:
                rows.append({"name": name, "version": None})
        names_by_group[group] = [row["name"] for row in rows]

    interpreter, why_not = ren_doctor.resolve_interpreter()
    if not interpreter:
        return {"interpreter": None, "unavailable": why_not,
                "groups": {group: [{"name": name, "version": None}
                                   for name in names]
                           for group, names in names_by_group.items()}}

    probe = """\
import importlib.metadata as metadata
import json
import sys

versions = {}
for name in sys.argv[1:]:
    try:
        versions[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        versions[name] = None
print(json.dumps(versions))
"""
    try:
        done = subprocess.run(
            [interpreter, "-c", probe, *all_names], capture_output=True,
            encoding="utf-8", timeout=30, check=False)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip() or
                               f"interpreter exited {done.returncode}")
        found = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError,
            IndexError, RuntimeError) as exc:
        return {"interpreter": str(interpreter),
                "unavailable": f"could not read package versions: {exc}",
                "groups": {group: [{"name": name, "version": None}
                                   for name in names]
                           for group, names in names_by_group.items()}}

    packages = {
        group: [{"name": name, "version": found[name]} for name in names]
        for group, names in names_by_group.items()}
    return {"interpreter": str(interpreter), "groups": packages}


def _hf_model_record(repo_id: str, purpose: str, cache: Path) -> dict:
    """Report cached HuggingFace revision ids without reading weights."""
    root = cache / ("models--" + repo_id.replace("/", "--"))
    snapshots = root / "snapshots"
    try:
        revisions = (sorted(path.name for path in snapshots.iterdir()
                             if path.is_dir())
                     if snapshots.is_dir() else [])
    except OSError:
        revisions = []
    selected = None
    ref = root / "refs" / "main"
    try:
        selected = ref.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return {"id": repo_id, "purpose": purpose,
            "cached": bool(revisions), "revision": selected,
            "cached_revisions": revisions}


def models_section() -> dict:
    """`models.json`: model identifiers and on-disk revision/pin state."""
    from ren import doctor as ren_doctor
    from library.tools import shared_environment as se

    cache = ren_doctor.hf_hub_cache()
    hf_models: dict[str, str] = {
        ident: reader for _label, reader, ident, _need in ren_doctor.MODELS}
    hf_models.setdefault("sentence-transformers/all-MiniLM-L6-v2",
                         "footage search embedding")
    hf_models.setdefault("facebook/sam2.1-hiera-small",
                         "subject segmentation")
    hf_models.setdefault("openai/clip-vit-large-patch14",
                         "frame ranking")
    server_model = os.environ.get("GEMMA_SERVER_MODEL")
    if server_model:
        hf_models.setdefault(server_model, "configured Gemma server")
    records = [_hf_model_record(repo_id, purpose, cache)
               for repo_id, purpose in sorted(hf_models.items())]

    panns = se.panns_checkpoint()
    deepfilter = se.deepfilter_binary()
    mfa = se.mfa_binary()
    ecapa = se.ecapa_model_dir()
    insightface = (se.insightface_model_dir() / "models"
                   / se.INSIGHTFACE_PACK_NAME)
    return {
        "huggingface": records,
        "machine_packs": [
            {"name": "PANNs", "version": se.PANNS_CHECKPOINT_NAME,
             "expected_md5": se.PANNS_CHECKPOINT_MD5,
             "available": panns.is_file()},
            {"name": "DeepFilterNet", "version": se.DEEPFILTER_VERSION,
             "available": deepfilter.is_file()},
            {"name": "MFA acoustic model",
             "version": f"{se.MFA_ACOUSTIC_MODEL} "
                        f"{se.MFA_ACOUSTIC_MODEL_VERSION}",
             "available": mfa.is_file() and se.mfa_models_dir().is_dir()},
            {"name": "ECAPA", "version": se.ECAPA_REVISION,
             "available": all((ecapa / name).is_file()
                              for name in se.ECAPA_REQUIRED_FILES)},
            {"name": "InsightFace", "version": se.INSIGHTFACE_PACK_NAME,
             "available": all((insightface / name).is_file()
                              for name in se.INSIGHTFACE_REQUIRED_FILES)},
        ],
    }


def _is_error_event(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    event = str(entry.get("event_type", ""))
    return (event.endswith("_error") or event == "error"
            or entry.get("error") not in (None, ""))


_ERROR_FIELDS = (
    "timestamp", "run_id", "project_id", "ren_build", "step_id",
    "event_type", "code", "error_type", "error", "gate_decision",
    "backend", "duration_ms", "latency",
)


def _support_error_event(entry: dict) -> dict:
    """Keep structured diagnostics, dropping arbitrary detail payloads.

    `detail` can carry model context, captions or source text. Error
    messages are limited to their first line so a captured traceback or
    multi-line payload cannot ride along in the support archive.
    """
    result = {key: entry[key] for key in _ERROR_FIELDS if key in entry}
    if isinstance(result.get("error"), str):
        result["error"] = result["error"].splitlines()[0][:1000]
    return redact(result)


def errors_section(project_dir: Path | None, tail: int) -> dict:
    """`errors.jsonl`: recent structured errors, newest last, with the
    run id and refusal code each one carried. Absent logs are an empty
    list, not a failure - a project that never ran has no errors."""
    if project_dir is None:
        return {"events": [], "note": "no project: machine-only bundle"}
    tail = max(int(tail), 0)
    from library.tools.project_layout import Area, ProjectLayout
    layout = ProjectLayout(project_dir)
    logs: list = []
    try:
        current = layout.read_path(Area.LOGS, "pipeline_log.jsonl")
        candidates = ([Path(f"{current}.{n}") for n in (3, 2, 1)]
                      + [Path(current)])
    except Exception as exc:  # noqa: BLE001 - layout questions refuse
        return {"events": [], "note": f"project layout unreadable: {exc}"}
    for candidate in candidates if tail else ():
        try:
            if not candidate.is_file():
                continue
            with candidate.open("rb") as log:
                size = os.fstat(log.fileno()).st_size
                start = max(0, size - LOG_SCAN_BYTES)
                if start:
                    log.seek(start)
                    log.readline()  # discard the partial first JSONL row
                text = log.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if _is_error_event(entry):
                logs.append(entry)
    events = [_support_error_event(event) for event in logs[-tail:]]
    try:
        from library.tools import pipeline_logger
        instance = pipeline_logger.get_logger()
        logging_health = {
            "write_failures_this_process": (
                instance.write_failures if instance is not None else 0),
            "last_write_error": (
                instance.last_write_error if instance is not None else None),
            "rotation_failures_this_process": (
                instance.rotation_failures if instance is not None else 0),
            "last_rotation_error": (
                instance.last_rotation_error if instance is not None else None),
        }
    except Exception:  # noqa: BLE001 - health is a bonus, not the bundle
        logging_health = {}
    return {"events": redact(events), "logging_health": logging_health}


def manifests_section(project_dir: Path | None) -> dict:
    """Small project files the bundle may carry, each redacted. Big or
    missing files become a stub saying so - the bundle never grows
    without bound and never fails for a file that is not there."""
    if project_dir is None:
        return {}
    from library.tools.project_layout import Area, ProjectLayout
    layout = ProjectLayout(project_dir)
    try:
        wanted = {
            "assembly_manifest.json": layout.read_path(
                Area.ASSEMBLY_MANIFEST, "assembly_manifest.json"),
            "project.yaml": project_dir / "project.yaml",
            "pipeline_run.json": project_dir / "pipeline_run.json",
        }
    except Exception as exc:  # noqa: BLE001 - layout questions refuse
        return {"_note": f"project layout unreadable: {exc}"}
    out: dict = {}
    for name, path in wanted.items():
        try:
            path = Path(path)
            if not path.is_file():
                out[name] = {"_absent": True}
                continue
            size = path.stat().st_size
            if size > MANIFEST_SIZE_GUARD:
                out[name] = {"_too_large": True, "bytes": size,
                             "note": "omitted: over the manifest size guard"}
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                out[name] = _private_manifest_content(
                    redact(json.loads(text)))
            except ValueError:
                try:
                    import yaml
                    parsed = yaml.safe_load(text)
                except Exception:  # noqa: BLE001 - manifests are optional
                    out[name] = {"_omitted": "manifest could not be parsed"}
                else:
                    out[name] = _private_manifest_content(redact(parsed))
        except OSError as exc:
            out[name] = {"_unreadable": f"{type(exc).__name__}: {exc}"}
    return out


def _private_manifest_content(value):
    """Keep manifest shape while omitting speech, caption and prompt text."""
    if isinstance(value, list):
        return [_private_manifest_content(item) for item in value]
    if isinstance(value, dict):
        return {
            key: ("<omitted: user content>"
                  if _PRIVATE_CONTENT_KEY.search(str(key))
                  else _private_manifest_content(item))
            for key, item in value.items()}
    return value


def build_bundle(project: str | None, out_dir: Path,
                 tail: int) -> tuple[Path, list]:
    """Collect the sections, redact them, zip them. Returns the archive
    and its member list."""
    project_dir = resolve_project(project) if project else None
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    slug = project_dir.name if project_dir is not None else "machine"
    archive = out_dir / f"ren-support-{slug}-{stamp}.zip"
    err_section = errors_section(project_dir, tail)
    error_rows = []
    if err_section.get("note"):
        error_rows.append({"_note": redact_text(err_section["note"])})
    if err_section.get("logging_health"):
        error_rows.append({"event_type": "logger_health",
                           **redact(err_section["logging_health"])})
    error_rows.extend(err_section["events"])
    err_lines = "".join(json.dumps(row) + "\n" for row in error_rows)
    members = {
        "ren.json": json.dumps(
            redact(ren_section(project)), indent=1) + "\n",
        "config.json": json.dumps(
            redact(config_section()), indent=1) + "\n",
        "doctor.json": json.dumps(
            redact(doctor_section()), indent=1) + "\n",
        "packages.json": json.dumps(
            redact(packages_section()), indent=1) + "\n",
        "models.json": json.dumps(
            redact(models_section()), indent=1) + "\n",
        "errors.jsonl": err_lines,
        "manifests.json": json.dumps(
            redact(manifests_section(project_dir)), indent=1) + "\n",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, text in members.items():
            bundle.writestr(name, text)
            names.append(name)
    return archive, names


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren support-bundle", description=__doc__.splitlines()[0])
    parser.add_argument("project", nargs="?",
                        help="project slug, or the absolute path of a "
                             "project outside the projects root; omit "
                             "for a machine-only bundle")
    parser.add_argument("--out", default="",
                        help="directory for the archive (default: a fresh "
                             "temporary directory)")
    parser.add_argument("--errors", type=int, default=ERROR_EVENT_TAIL,
                        help="how many recent error events to carry "
                             f"(default {ERROR_EVENT_TAIL})")
    args = parser.parse_args(argv)

    out_dir = (Path(args.out).expanduser() if args.out
               else Path(tempfile.mkdtemp(prefix="ren-support-")))
    archive, names = build_bundle(args.project, out_dir, max(args.errors, 0))
    print(f"wrote {archive}")
    for name in names:
        print(f"  {name}")
    print("redacted: home paths fold to ~, other user roots to a generic "
          "user component, token-shaped values to <redacted>; footage, "
          "transcripts and prompts are never collected")
    return 0
