"""
project_format.py - the product-level version of a project folder.

A project is a PRODUCT artifact that outlives any one engine checkout:
the captain's live projects were scaffolded months ago and must open
under today's engine, and next year's engine must open today's
projects.  Nothing before this module versioned that contract.  What
existed was per-artifact versions only - `project_migration.
MANIFEST_VERSION` (one organize run's own record), the vision pass's
`pipeline_version: v3`, `timeline_serializer`'s `schema_version` -
each scoped to the artifact that wrote it, none governing the project
as a whole.

This module is the whole product contract, and it is three things:

- `project_format_version`, a top-level key in `project.yaml`.
  Absent means 0: every project that predates this module reads as
  format 0, so existing projects open rather than failing on a key
  they never declared.
- The engine's supported range, `MIN_SUPPORTED_FORMAT_VERSION` to
  `PROJECT_FORMAT_VERSION`.  Opening a project outside it REFUSES
  (`ProjectFormatRefused`, a `RenRefusal` carrying refused/why/fix):
  too new was written by a newer Ren, too old predates what this
  engine can still read.
- `MIGRATIONS`, the ONE ordered registry of format steps.  An older
  but supported project migrates on open (`ensure_project_format`),
  deterministically and with a backup, reusing the project's own
  machinery: the manifest lands in `pipeline_output/migrations/`
  (the area `project_migration` owns), the pre-migration copy of
  `project.yaml` lands under `pipeline_output/backups/` (where the
  pruner only ever touches files matching its own naming pattern,
  so a format backup is never pruned), and `revert_from_manifest`
  restores it byte for byte.

Rules
-----
AGENTS.md §8 carries the headline; this module owns the format range,
ordered migration registry and reversible migration details.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from library.tools.ren_refusal import RenRefusal

FORMAT_VERSION_KEY = "project_format_version"
"""The top-level `project.yaml` key.  Absent reads as 0 (unversioned)."""

PROJECT_FORMAT_VERSION = 1
"""The format this engine writes.  New projects are stamped with this."""

MIN_SUPPORTED_FORMAT_VERSION = 0
"""The oldest format this engine still opens.  0 is the unversioned
legacy: every project that predates format versioning."""


class ProjectFormatRefused(RenRefusal):
    """A project whose format is outside the supported range."""


def _yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        return None


_VERSION_LINE_RE = re.compile(
    r"^" + re.escape(FORMAT_VERSION_KEY) + r"\s*:.*$", re.MULTILINE)


def _project_yaml_path(project_folder) -> Path:
    from library.tools.project_layout import ProjectLayout
    return ProjectLayout(project_folder).project_config_path


def read_format_version(project_folder) -> int:
    """The project's format version: 0 when the key is absent.

    Raises `FileNotFoundError` when there is no `project.yaml`
    (a missing project is reported by the caller that looked for
    it, not by the format gate) and `ValueError` when the key is
    present but is not an integer (a malformed declaration, refused
    by name with the other malformed declarations).
    """
    path = _project_yaml_path(project_folder)
    text = path.read_text(encoding="utf-8")
    yaml = _yaml()
    if yaml is None:
        match = _VERSION_LINE_RE.search(text)
        if not match:
            return 0
        raise ValueError(
            f"project_format_version is declared in {path} but PyYAML "
            f"is unavailable to read it. Install PyYAML and retry.")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"Project config is not a YAML mapping: {path}")
    raw = data.get(FORMAT_VERSION_KEY, 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            f"project_format_version must be an integer, got "
            f"{raw!r} in {path}.")
    return raw


def check_format(project_folder) -> int:
    """The project's version when it is in range, else a refusal.

    Outside `[MIN_SUPPORTED_FORMAT_VERSION, PROJECT_FORMAT_VERSION]`
    raises `ProjectFormatRefused` with refused/why/fix: too new was
    written by a newer Ren (upgrade Ren), too old predates what this
    engine can still read.  Read-only: never writes.
    """
    return validate_format_version(
        read_format_version(project_folder), project_folder)


def validate_format_version(version: object, project_folder) -> int:
    """Refuse malformed or unsupported product format declarations."""
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(
            f"project_format_version must be an integer, got "
            f"{version!r} in {project_folder}.")
    if version > PROJECT_FORMAT_VERSION:
        raise ProjectFormatRefused(
            f"refusing to open {project_folder}: its "
            f"project_format_version is {version}",
            f"this engine understands format "
            f"{MIN_SUPPORTED_FORMAT_VERSION}..{PROJECT_FORMAT_VERSION}; "
            f"the project was written by a newer Ren",
            f"open it with a Ren build that writes format {version}, "
            f"or downgrade the project only through a migration that "
            f"registry names")
    if version < MIN_SUPPORTED_FORMAT_VERSION:
        raise ProjectFormatRefused(
            f"refusing to open {project_folder}: its "
            f"project_format_version is {version}",
            f"this engine supports format "
            f"{MIN_SUPPORTED_FORMAT_VERSION}..{PROJECT_FORMAT_VERSION}; "
            f"the project predates the oldest format it can still read",
            f"open it with the Ren build that wrote format {version} "
            f"and migrate it forward from there")
    return version


@dataclass(frozen=True)
class FormatMigration:
    """One ordered step of the registry: format N becomes format N+1."""

    from_version: int
    to_version: int
    migration_id: str
    summary: str
    transform: Callable[[str], str]


def _stamp_format_version(text: str, version: int) -> str:
    """The 0 -> 1 migration, as a byte-preserving text edit.

    Stamping goes through text, not through `project_config_to_dict`:
    re-serializing the config would drop the captain's comments and
    reflow the file, and a migration must change nothing but what it
    migrates.  A present key line is rewritten in place; an absent one
    is appended, so every other byte is untouched either way.
    """
    match = _VERSION_LINE_RE.search(text)
    if match:
        carriage_return = "\r" if match.group(0).endswith("\r") else ""
        return _VERSION_LINE_RE.sub(
            f"{FORMAT_VERSION_KEY}: {version}{carriage_return}",
            text,
            count=1)
    newline = "\r\n" if "\r\n" in text else "\n"
    if text and not text.endswith(("\n", "\r")):
        text += newline
    return (
        text
        + newline
        + f"# Product format version "
        + f"(library/tools/project_format.py). Do not edit by hand."
        + newline
        + f"{FORMAT_VERSION_KEY}: {version}"
        + newline
    )


MIGRATIONS: tuple = (
    FormatMigration(
        from_version=0,
        to_version=1,
        migration_id="stamp_project_format_version",
        summary=(
            "Projects that predate format versioning carry no version "
            "key and read as format 0. Stamping the key changes nothing "
            "else: the text edit preserves every other byte, so a "
            "stamped project behaves exactly as it did unversioned."),
        transform=lambda text: _stamp_format_version(text, 1),
    ),
)
"""The ONE ordered registry.  A future format N+1 appends exactly one
entry with `from_version` N; `_assert_registry_is_contiguous` refuses
anything else at import time."""


def _assert_registry_is_contiguous() -> None:
    expected = MIN_SUPPORTED_FORMAT_VERSION
    for entry in MIGRATIONS:
        if entry.from_version != expected:
            raise AssertionError(
                f"Format migration registry is not contiguous: "
                f"{entry.migration_id} starts at {entry.from_version}, "
                f"expected {expected}. One entry per format step, in "
                f"order, no gaps.")
        if entry.to_version != expected + 1:
            raise AssertionError(
                f"Format migration {entry.migration_id} spans "
                f"{entry.from_version}..{entry.to_version}: one entry "
                f"moves exactly one format step.")
        expected = entry.to_version
    if expected != PROJECT_FORMAT_VERSION:
        raise AssertionError(
            f"Format migration registry ends at {expected} but the "
            f"engine writes {PROJECT_FORMAT_VERSION}. A new format "
            f"needs its migration entry before it ships.")


_assert_registry_is_contiguous()


def pending_migrations(version: int) -> list:
    """The registry entries a project at `version` still needs, in order."""
    return [m for m in MIGRATIONS if m.from_version >= version]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _atomic_write(path: Path, value: bytes) -> None:
    """Replace one project file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(value)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def migrate_project(project_folder, apply: bool = False) -> dict:
    """Plan the format migration, optionally perform it, return the manifest.

    Reads only when `apply` is False.  When True and work is pending:
    the pre-migration `project.yaml` is copied under
    `pipeline_output/backups/format/` (one timestamped backup per
    application; never pruned), each pending registry entry applies in
    order, and the manifest lands in
    `pipeline_output/migrations/format_<stamp>.json`.
    `revert_from_manifest` restores the backup byte for byte.
    """
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    root = layout.root
    version = check_format(root)
    pending = pending_migrations(version)

    manifest = {
        "manifest_kind": "project_format_migration",
        "manifest_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "applied": False,
        "status": "planned",
        "project": str(root),
        "layout_owner": "library/tools/project_layout.py",
        "from_version": version,
        "to_version": pending[-1].to_version if pending else version,
        "migrations": [
            {"migration_id": m.migration_id,
             "from_version": m.from_version,
             "to_version": m.to_version,
             "summary": m.summary}
            for m in pending
        ],
        "actions": [],
        "backup_path": "",
        "backup_sha256": "",
    }

    if not pending:
        manifest["status"] = "current"
        return manifest

    yaml_path = layout.project_config_path
    before_text = yaml_path.read_bytes().decode("utf-8")
    after_text = before_text
    for entry in pending:
        after_text = entry.transform(after_text)

    if not apply:
        manifest["actions"].append({
            "action": "stamp",
            "src": str(yaml_path),
            "dest": str(yaml_path),
            "kind": "file",
            "bytes": len(after_text.encode("utf-8")),
            "reason": "; ".join(
                f"{m.migration_id}: {m.summary}" for m in pending),
        })
        return manifest

    stamp_base = time.strftime("%Y%m%dT%H%M%S")
    stamp = stamp_base
    suffix = 2
    while (
            layout.read_path(
                Area.BACKUPS, "format", f"project.yaml.{stamp}.bak").exists()
            or layout.read_path(
                Area.MIGRATIONS, f"format_{stamp}.json").exists()):
        stamp = f"{stamp_base}-{suffix}"
        suffix += 1
    backup_path = layout.backup_file(
        yaml_path, "format", f"project.yaml.{stamp}.bak")
    manifest["backup_path"] = str(backup_path)
    manifest["backup_sha256"] = _sha256(backup_path)
    manifest["actions"].append({
        "action": "copy",
        "src": str(yaml_path),
        "dest": str(backup_path),
        "kind": "file",
        "bytes": backup_path.stat().st_size,
        "digest": manifest["backup_sha256"],
        "reason": (
            "pre-migration backup of project.yaml. Restored by "
            "project_migration.revert_from_manifest; never pruned (the "
            "backup pruner only ever considers files matching its own "
            "pipeline_data naming pattern)."),
    })

    after_bytes = after_text.encode("utf-8")
    manifest["project_yaml_sha256"] = _sha256_bytes(after_bytes)
    manifest["actions"].append({
        "action": "stamp",
        "src": str(yaml_path),
        "dest": str(yaml_path),
        "kind": "file",
        "bytes": len(after_text.encode("utf-8")),
        "digest": _sha256(yaml_path),
        "reason": "; ".join(
            f"{m.migration_id} ({m.from_version}->{m.to_version}): "
            f"{m.summary}" for m in pending),
    })
    manifest["revert_actions"] = [{
        "action": "restore_backup",
        "src": str(yaml_path),
        "dest": str(backup_path),
        "bytes": backup_path.stat().st_size,
        "digest": manifest["backup_sha256"],
        "expected_src_digest": manifest["project_yaml_sha256"],
        "reason": "restore project.yaml to its exact pre-migration bytes",
    }]

    manifest_path = layout.write_path(
        Area.MIGRATIONS, f"format_{stamp}.json")
    manifest["manifest_path"] = str(manifest_path)
    manifest["status"] = "prepared"
    _atomic_write(
        manifest_path, json.dumps(manifest, indent=2).encode("utf-8"))
    _atomic_write(yaml_path, after_bytes)
    manifest["applied"] = True
    manifest["status"] = "applied"
    _atomic_write(
        manifest_path, json.dumps(manifest, indent=2).encode("utf-8"))
    return manifest


def revert_from_manifest(manifest_path, apply: bool = False) -> dict:
    """Undo a format migration by restoring the pre-migration backup.

    The backup is restored byte for byte and verified against the
    recorded digest.  Reads only when `apply` is False.
    """
    from library.tools.project_layout import ProjectLayout

    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if data.get("manifest_kind") != "project_format_migration":
        raise ValueError(
            f"{manifest_path} is not a project-format migration "
            f"manifest. Format reverts read only what migrate_project "
            f"wrote.")
    backup = Path(data["backup_path"])
    layout = ProjectLayout(data["project"])
    yaml_path = layout.project_config_path
    report = {
        "manifest": str(manifest_path),
        "backup": str(backup),
        "restored": bool(apply),
        "project_yaml": str(yaml_path),
    }
    from library.tools.project_migration import revert_from_manifest as revert
    revert(manifest_path, apply=apply)
    if apply:
        digest = _sha256(yaml_path)
        if digest != data["backup_sha256"]:
            raise ValueError(
                f"Restored {yaml_path} does not match the recorded "
                f"backup digest: got {digest}, manifest records "
                f"{data['backup_sha256']}.")
        report["digest"] = digest
    return report


def ensure_project_format(project_folder) -> dict | None:
    """Open a project through the format gate; migrate when behind.

    Returns None when the project is already current (the common
    case: no write, no backup, no manifest) and the applied migration
    manifest when an older supported project was stamped forward.
    Raises `ProjectFormatRefused` outside the supported range.
    A folder with no `project.yaml` is not a project this gate can
    judge: None, silently - the caller that looked for the project
    reports it missing.
    """
    from library.tools.project_layout import ProjectLayout

    yaml_path = ProjectLayout(project_folder).project_config_path
    if not yaml_path.is_file():
        return None
    version = check_format(project_folder)
    if not pending_migrations(version):
        return None
    return migrate_project(project_folder, apply=True)


def _render_report(manifest: dict) -> str:
    lines = [
        f"Project format: {manifest['from_version']} -> "
        f"{manifest['to_version']}",
    ]
    for entry in manifest["migrations"]:
        lines.append(
            f"  {entry['migration_id']} "
            f"({entry['from_version']}->{entry['to_version']}): "
            f"{entry['summary']}")
    if not manifest["migrations"]:
        lines.append("The project is already current; nothing changed.")
    elif manifest["applied"]:
        lines.append(f"  backup: {manifest['backup_path']}")
        lines.append(f"  manifest: {manifest.get('manifest_path', '')}")
    else:
        lines.append("  Nothing was changed. Pass --apply to perform it.")
    return "\n".join(lines)


def main(argv=None) -> int:
    """`python3 -m library.tools.project_format <project> [--apply]`.

    Plans the format migration by default and changes nothing; with
    `--apply` stamps the project forward with a backup.  With
    `--revert <manifest.json> [--apply]` restores the pre-migration
    backup.  Outside the supported range it refuses in the same shape
    every `ren` verb prints.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate a project forward through the format registry.")
    parser.add_argument("project",
                        help="Project directory or project.yaml path")
    parser.add_argument("--apply", action="store_true",
                        help="Perform the migration (default plans only)")
    parser.add_argument("--revert", metavar="MANIFEST",
                        help="Restore the pre-migration backup named by a manifest")
    args = parser.parse_args(argv)

    from library.tools.ren_refusal import REFUSAL_EXIT_CODE

    try:
        if args.revert:
            report = revert_from_manifest(args.revert, apply=args.apply)
            verb = "Restored" if args.apply else "Would restore"
            print(f"{verb} {report['project_yaml']} from {report['backup']}"
                  + ("" if args.apply else " (pass --apply to perform it)"))
            return 0
        # A filesystem path only, never a slug: resolving a slug needs
        # the project registry, and this module stays importable
        # without it (and out of the preflight cache map's transitive
        # scan through it).
        candidate = Path(args.project).expanduser()
        if candidate.is_file() and candidate.name == "project.yaml":
            folder = str(candidate.parent)
        elif (candidate.is_dir()
              and (candidate / "project.yaml").is_file()):
            folder = str(candidate)
        else:
            print(f"no project at {args.project}: expected a project "
                  f"directory or its project.yaml")
            return 2
        manifest = migrate_project(folder, apply=args.apply)
    except RenRefusal as refused:
        print(refused.render())
        return REFUSAL_EXIT_CODE
    print(_render_report(manifest))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
