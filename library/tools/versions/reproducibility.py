"""Portable recovery evidence for historical reel revisions.

Resolve paths remain useful provenance on the machine that made a
revision.  The logical asset id is the content identity that travels
with the revision; it never replaces or rewrites those paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import uuid
from pathlib import Path

IDENTITY_REL = Path("pipeline_output/provenance/project_identity.json")
IDENTITY_FORMAT = "ren_project_identity/1"
RECOVERY_FORMAT = "ren_revision_recovery/1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_identity(project_folder: str | os.PathLike) -> dict:
    """Read or create the portable project id, independent of its location."""
    root = Path(project_folder).resolve()
    path = root / IDENTITY_REL
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"project identity exists but is unreadable: {path}") from exc
        if (isinstance(value, dict)
                and value.get("format") == IDENTITY_FORMAT
                and value.get("project_id")):
            return value
        raise ValueError(f"project identity has an unsupported format: {path}")
    value = {"format": IDENTITY_FORMAT, "project_id": str(uuid.uuid4())}
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".json.staged")
    staged.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n",
                       encoding="utf-8")
    os.replace(staged, path)
    return value


def _renderer_fingerprints() -> dict:
    """Capture renderer identities when their complete source is available."""
    from library.tools import render_cache

    repo = Path(__file__).resolve().parents[3]
    return {
        "remotion": render_cache.renderer_fingerprint(
            str(repo / "remotion-subtitles")),
        "hyperframes": render_cache.hyperframes_fingerprint(
            str(repo / "hyperframes")),
        "fframes": render_cache.fframes_fingerprint(
            str(repo / "native/ren-graphics-fframes")),
    }


def _declarations(root: Path) -> list[dict]:
    """Hash the small project declarations needed to replay the revision."""
    paths = [root / "project.yaml"]
    declarations = root / "external" / "declarations"
    if declarations.is_dir():
        paths.extend(path for path in declarations.rglob("*") if path.is_file())
    records = []
    for path in sorted(set(paths)):
        item = logical_asset(path, kind="declaration")
        item["path"] = path.relative_to(root).as_posix()
        records.append(item)
    return records


def environment_fingerprint(project_folder: str | os.PathLike) -> dict:
    """Return portable runtime facts; omit host paths and volatile details."""
    ren_version = ""
    try:
        from library.schemas.project_config import load_project_config

        ren_version = str(load_project_config(str(project_folder)).ren_version or "")
    except (OSError, ValueError, TypeError, ImportError):
        ren_version = ""
    renderers = _renderer_fingerprints()
    body = {
        "format": "ren_environment_fingerprint/1",
        "platform": platform.system().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "ren": ren_version,
        "renderers": renderers,
    }
    body["digest"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return body


def logical_asset(path: str | os.PathLike, *, kind: str,
                  sha256: str | None = None,
                  size: int | None = None) -> dict:
    """Describe one file by content id while retaining its original path."""
    original = str(path)
    candidate = Path(path)
    item = {"kind": kind, "original_path": original}
    if candidate.is_file():
        digest = sha256 or _sha256(candidate)
        item.update({"asset_ref": f"asset://sha256/{digest}",
                     "sha256": digest,
                     "bytes": (size if size is not None
                               else candidate.stat().st_size),
                     "status": "present"})
    else:
        item.update({"asset_ref": None, "sha256": None, "bytes": None,
                     "status": "missing"})
    return item


def recovery_evidence(project_folder: str | os.PathLike, final: str,
                      archive_receipt: dict, *, resolve_version: str = "",
                      required_assets: list[dict] | None = None) -> dict:
    """Build the per-revision portability and recovery contract."""
    root = Path(project_folder).resolve()
    assets = []
    for source in archive_receipt.get("assets") or []:
        source_path = Path(source["path"])
        kind = "external_asset" if not source_path.is_relative_to(root) else "media"
        assets.append(logical_asset(source["path"], kind=kind,
                                    sha256=source.get("sha256"),
                                    size=source.get("bytes")))
    for source in required_assets or []:
        if not source.get("path"):
            continue
        assets.append(logical_asset(source["path"],
                                    kind=str(source.get("kind") or "external_asset")))
    archive_original_path = str(archive_receipt.get("archive_path") or "")
    archive = (logical_asset(archive_original_path, kind="native_archive",
                             sha256=archive_receipt.get("archive_sha256"))
               if archive_original_path else {
                   "kind": "native_archive", "original_path": "",
                   "asset_ref": None, "sha256": None, "bytes": None,
                   "status": "missing"})
    # Keep this path for existing readers and add an explicitly named
    # provenance spelling for portable-aware readers.
    archive["original_path"] = archive_original_path
    return {
        "format": RECOVERY_FORMAT,
        "project_id": project_identity(root)["project_id"],
        "timeline_identity": final,
        "environment": environment_fingerprint(root),
        "declarations": _declarations(root),
        "assets": assets + [archive],
        "asset_requirements": {
            kind: [asset["asset_ref"] for asset in assets
                   if asset["kind"] == kind]
            for kind in ("font", "plugin", "model", "media", "external_asset")
        },
        "recovery_requirements": {
            "exact_native": {
                "available": archive["status"] == "present",
                "archive_ref": archive["asset_ref"],
                "resolve_version": resolve_version or None,
                "requires": ["compatible DaVinci Resolve", "all referenced media"],
            },
            "semantic_reconstruction": {
                "available": True,
                "requires": ["project declarations", "all referenced assets",
                             "compatible renderer fingerprints"],
            },
        },
    }


def assess_recovery(evidence: dict, *, project_folder: str | os.PathLike,
                    current_environment: dict | None = None,
                    current_resolve_version: str = "") -> dict:
    """Assess exact restore and semantic reconstruction before attempting either."""
    root = Path(project_folder).resolve()
    missing, missing_native, changed, changed_native = [], [], [], []
    missing_declarations, changed_declarations = [], []
    for declaration in evidence.get("declarations") or []:
        path = root / declaration["path"]
        if not path.is_file():
            missing_declarations.append(declaration["path"])
        elif _sha256(path) != declaration.get("sha256"):
            changed_declarations.append(declaration["path"])
    for asset in evidence.get("assets") or []:
        original = Path(asset.get("original_path") or "")
        if asset.get("status") != "present" or not original.is_file():
            target = (missing_native if asset.get("kind") == "native_archive"
                      else missing)
            target.append(asset.get("asset_ref") or asset.get("original_path"))
            continue
        if asset.get("sha256") and _sha256(original) != asset["sha256"]:
            target = (changed_native if asset.get("kind") == "native_archive"
                      else changed)
            target.append(asset.get("asset_ref") or str(original))
    now = current_environment or environment_fingerprint(root)
    prior = evidence.get("environment") or {}
    native_requirements = ((evidence.get("recovery_requirements") or {})
                           .get("exact_native") or {})
    recorded_resolve = native_requirements.get("resolve_version") or ""
    resolve_mismatch = not recorded_resolve or recorded_resolve != current_resolve_version
    renderer_mismatches = [name for name, fingerprint in
                           (prior.get("renderers") or {}).items()
                           if not fingerprint or fingerprint !=
                           (now.get("renderers") or {}).get(name)]
    exact = (not missing and not missing_native and not changed
             and not changed_native and not resolve_mismatch
             and bool(native_requirements.get("archive_ref")))
    equivalent = (not missing and not changed and not renderer_mismatches
                  and not missing_declarations and not changed_declarations)
    return {
        "project_id": evidence.get("project_id"),
        "reproducible": bool(exact or equivalent),
        "exact_native": {"restorable": exact, "missing_assets": missing,
                         "missing_archive": missing_native,
                         "changed_assets": changed,
                         "changed_archive": changed_native,
                         "resolve_version_mismatch": resolve_mismatch},
        "semantic_reconstruction": {
            "available": equivalent,
            "missing_assets": missing,
            "changed_assets": changed,
            "missing_declarations": missing_declarations,
            "changed_declarations": changed_declarations,
            "renderer_mismatches": renderer_mismatches,
        },
    }


def assess_revision(project_folder: str | os.PathLike, final: str,
                    version: int, *, current_environment: dict | None = None,
                    current_resolve_version: str = "") -> dict:
    """Read one recorded revision and assess it without touching Resolve."""
    from library.tools.versions import reel_versions

    entries = reel_versions.versions_of(str(project_folder), final)
    entry = next((item for item in entries
                  if int(item.get("version", 0)) == int(version)), None)
    if entry is None:
        return {"reproducible": False, "status": "revision_missing",
                "final": final, "version": int(version)}
    receipt = entry.get("recovery_archive") or {}
    evidence = receipt.get("reproducibility")
    if not isinstance(evidence, dict) or evidence.get("format") != RECOVERY_FORMAT:
        return {"reproducible": False, "status": "evidence_unavailable",
                "final": final, "version": int(version),
                "recovery_requirements": {
                    "exact_native": "unknown",
                    "semantic_reconstruction": "unknown"}}
    result = assess_recovery(
        evidence, project_folder=project_folder,
        current_environment=current_environment,
        current_resolve_version=current_resolve_version)
    result.update({"status": "assessed", "final": final,
                   "version": int(version)})
    return result
