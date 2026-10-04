"""Product-level project format versioning.

The defects these pin, one per group:

- No product format version existed: only per-artifact versions
  (`project_migration.MANIFEST_VERSION`, the vision pass's
  `pipeline_version`, `timeline_serializer`'s `schema_version`), none
  governing the project as a whole. A newer Ren could silently open
  an older project, or an older Ren a newer one, with no refusal and
  no migration. `project_format_version` plus the supported range
  closes that: outside refuses with refused/why/fix, inside migrates.
- A migration that rewrites `project.yaml` through the config
  serializer would drop the captain's comments and reflow the file.
  The stamp migration is a byte-preserving text edit: every other
  byte is untouched, so a stamped project behaves exactly as it did
  unversioned.
- A migration with no backup and no revert is a one-way edit to the
  captain's project file. Every applied migration records a manifest
  under `pipeline_output/migrations/` and a pre-migration backup
  under `pipeline_output/backups/format/`, and the revert restores
  the backup byte for byte.
"""
from __future__ import annotations

import pytest

from library.schemas.project_config import load_project_config
from library.tools import project_format as pf
from library.tools.project_format import (
    MIN_SUPPORTED_FORMAT_VERSION,
    PROJECT_FORMAT_VERSION,
    ProjectFormatRefused,
)
from library.tools.project_registry import (
    create_project,
    get_project,
    scan_projects,
)
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal


def _legacy_project(tmp_path, slug="legacy"):
    """A project as it exists before format versioning: no version key."""
    create_project(slug, name="Legacy", root=tmp_path)
    yaml_path = tmp_path / slug / "project.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    assert f"{pf.FORMAT_VERSION_KEY}: {PROJECT_FORMAT_VERSION}" in text
    stripped = "".join(
        line for line in text.splitlines(keepends=True)
        if pf.FORMAT_VERSION_KEY not in line)
    yaml_path.write_text(stripped, encoding="utf-8")
    return tmp_path / slug, stripped


# ── The registry contract ────────────────────────────────────────

def test_registry_covers_the_supported_range_with_no_gaps():
    versions = [m.from_version for m in pf.MIGRATIONS]
    assert versions == list(range(
        MIN_SUPPORTED_FORMAT_VERSION, PROJECT_FORMAT_VERSION)), (
        "one registry entry per format step, in order, no gaps: "
        "a skipped step is a project that can never be migrated")
    for entry in pf.MIGRATIONS:
        assert entry.to_version == entry.from_version + 1, (
            f"{entry.migration_id} spans more than one step")


def test_pending_migrations_are_empty_at_current():
    assert pf.pending_migrations(PROJECT_FORMAT_VERSION) == []
    assert [m.migration_id for m in
            pf.pending_migrations(MIN_SUPPORTED_FORMAT_VERSION)] == [
        m.migration_id for m in pf.MIGRATIONS]


def test_current_project_apply_is_a_noop_with_an_accurate_report(tmp_path):
    config = create_project("current", name="Current", root=tmp_path)
    report = pf.migrate_project(config.project_root, apply=True)
    assert report["status"] == "current"
    assert report["applied"] is False
    assert "already current; nothing changed" in pf._render_report(report)


# ── Legacy projects open and migrate ─────────────────────────────

def test_unversioned_project_reads_as_zero_without_writing(tmp_path):
    folder, stripped = _legacy_project(tmp_path)
    assert pf.read_format_version(folder) == 0
    assert pf.check_format(folder) == 0
    config = load_project_config(
        folder / "project.yaml", migrate_format=False)
    assert config.project_format_version == 0
    # Reading never migrates: the file is untouched.
    assert (folder / "project.yaml").read_text(encoding="utf-8") == stripped
    assert pf.ensure_project_format(folder) is not None  # work is pending


def test_open_migrates_with_backup_and_manifest(tmp_path):
    folder, stripped = _legacy_project(tmp_path)
    config = get_project(str(folder))
    assert config.project_format_version == PROJECT_FORMAT_VERSION

    migrated = (folder / "project.yaml").read_text(encoding="utf-8")
    assert f"{pf.FORMAT_VERSION_KEY}: {PROJECT_FORMAT_VERSION}" in migrated
    # Nothing else changed: the stamp is appended, every prior byte kept.
    assert migrated.startswith(stripped)

    backups = sorted((folder / "pipeline_output" / "backups" / "format").glob("*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == stripped.encode("utf-8")

    manifests = sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))
    assert len(manifests) == 1

    # Listing is read-only: it reports the project without migrating it.
    folder2, stripped2 = _legacy_project(tmp_path, slug="legacy2")
    found = [c for c in scan_projects(tmp_path) if c.slug == "legacy2"]
    assert len(found) == 1 and found[0].project_format_version == 0
    assert (folder2 / "project.yaml").read_text(encoding="utf-8") == stripped2


def test_migration_is_deterministic_and_idempotent(tmp_path):
    folder, _stripped = _legacy_project(tmp_path)
    plan_a = pf.migrate_project(folder, apply=False)
    plan_b = pf.migrate_project(folder, apply=False)
    assert plan_a["actions"] == plan_b["actions"]
    assert (plan_a["from_version"], plan_a["to_version"]) == (0, 1)

    applied = pf.migrate_project(folder, apply=True)
    assert applied["applied"] is True
    # A migrated project is at rest: no pending work, no second manifest.
    assert pf.ensure_project_format(folder) is None
    manifests = sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))
    assert len(manifests) == 1
    second_open = get_project(str(folder))
    assert second_open.project_format_version == PROJECT_FORMAT_VERSION
    assert len(sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))) == 1


def test_revert_restores_the_pre_migration_file(tmp_path):
    folder, stripped = _legacy_project(tmp_path)
    get_project(str(folder))
    (manifest,) = sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))
    report = pf.revert_from_manifest(str(manifest), apply=True)
    assert report["restored"] is True
    assert (folder / "project.yaml").read_bytes() == stripped.encode("utf-8")
    assert pf.read_format_version(folder) == 0


def test_revert_of_a_foreign_manifest_is_refused(tmp_path):
    folder, _stripped = _legacy_project(tmp_path)
    foreign = folder / "pipeline_output" / "migrations" / "other.json"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text('{"manifest_kind": "something_else"}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a project-format migration"):
        pf.revert_from_manifest(str(foreign), apply=True)


# ── Outside the range refuses ────────────────────────────────────

def test_newer_format_refuses_with_what_why_and_fix(tmp_path):
    folder, stripped = _legacy_project(tmp_path)
    (folder / "project.yaml").write_text(
        stripped + f"\n{pf.FORMAT_VERSION_KEY}: "
        f"{PROJECT_FORMAT_VERSION + 1}\n",
        encoding="utf-8")
    with pytest.raises(ProjectFormatRefused) as refused:
        get_project(str(folder))
    assert isinstance(refused.value, RenRefusal)
    assert refused.value.what and refused.value.why and refused.value.fix
    assert REFUSAL_EXIT_CODE == 4
    # A refusal writes nothing: no backup, no manifest, file untouched.
    assert not (folder / "pipeline_output" / "backups").exists()
    assert not (folder / "pipeline_output" / "migrations").exists()


def test_older_than_minimum_refuses(tmp_path):
    folder, stripped = _legacy_project(tmp_path)
    (folder / "project.yaml").write_text(
        stripped + f"\n{pf.FORMAT_VERSION_KEY}: "
        f"{MIN_SUPPORTED_FORMAT_VERSION - 1}\n",
        encoding="utf-8")
    with pytest.raises(ProjectFormatRefused):
        pf.check_format(folder)


@pytest.mark.parametrize("bad", ["1", 1.0, True, [1], {"v": 1}])
def test_malformed_version_is_refused_by_name(tmp_path, bad):
    create_project("malformed", name="M", root=tmp_path)
    folder = tmp_path / "malformed"
    config = load_project_config(folder / "project.yaml")
    config.project_format_version = bad
    assert any("project_format_version" in error
               for error in config.validate()), bad


# ── New projects are stamped ─────────────────────────────────────

def test_new_projects_stamp_the_current_format(tmp_path):
    config = create_project("fresh", name="Fresh", root=tmp_path)
    assert config.project_format_version == PROJECT_FORMAT_VERSION
    reread = load_project_config(tmp_path / "fresh" / "project.yaml")
    assert reread.project_format_version == PROJECT_FORMAT_VERSION
    assert reread.validate() == []


def test_direct_config_open_migrates_and_records_a_revert(tmp_path):
    folder, stripped = _legacy_project(tmp_path)
    config = load_project_config(folder / "project.yaml")
    assert config.project_format_version == PROJECT_FORMAT_VERSION
    (manifest,) = sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))

    from library.tools.project_migration import revert_from_manifest
    report = revert_from_manifest(str(manifest), apply=True)
    assert report[-1]["to"] == str(folder / "project.yaml")
    assert (folder / "project.yaml").read_bytes() == stripped.encode("utf-8")


def test_revert_refuses_if_project_yaml_changed_after_migration(tmp_path):
    folder, _stripped = _legacy_project(tmp_path)
    get_project(str(folder))
    (manifest,) = sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))
    yaml_path = folder / "project.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8") + "# captain edit\n",
        encoding="utf-8")

    from library.tools.project_migration import revert_from_manifest
    with pytest.raises(ValueError, match="changed after migration"):
        revert_from_manifest(str(manifest), apply=True)
    assert "# captain edit" in yaml_path.read_text(encoding="utf-8")


def test_crlf_project_migration_preserves_every_existing_byte(tmp_path):
    create_project("crlf", name="CRLF", root=tmp_path)
    folder = tmp_path / "crlf"
    yaml_path = folder / "project.yaml"
    original = yaml_path.read_bytes()
    legacy = b"".join(
        line for line in original.splitlines(keepends=True)
        if not line.startswith(f"{pf.FORMAT_VERSION_KEY}:".encode("ascii")))
    legacy = legacy.replace(b"\n", b"\r\n")
    yaml_path.write_bytes(legacy)

    config = load_project_config(yaml_path)
    assert config.project_format_version == PROJECT_FORMAT_VERSION
    migrated = yaml_path.read_bytes()
    assert migrated.startswith(legacy)
    version_line = (
        f"\r\n{pf.FORMAT_VERSION_KEY}: "
        f"{PROJECT_FORMAT_VERSION}\r\n").encode("ascii")
    assert version_line in migrated

    (manifest,) = sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))
    pf.revert_from_manifest(str(manifest), apply=True)
    assert yaml_path.read_bytes() == legacy


def test_stamp_replaces_a_version_line_without_changing_crlf():
    before = "project_format_version: 0\r\nname: Example\r\n"
    after = pf._stamp_format_version(before, 1)
    assert after == "project_format_version: 1\r\nname: Example\r\n"
