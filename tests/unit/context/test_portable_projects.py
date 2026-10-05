"""Portable projects: a move rebinds paths without changing semantics.

The defects these pin, one per group:

- A project records machine-local absolute paths - `footage_root` in
  `project.yaml`, clip paths in `source_fingerprints` and the catalog,
  `project_folder` in `pipeline_data.json` - and the version store
  commits them verbatim.  Moving the folder left every one of those
  pointing at the old machine: the project opened onto missing media
  with no repair.  Format 2 stores declarations as `$HOME/...` /
  `$PROJECT/...` tokens (expanded at read), and the open gate rebinds
  recorded state to the new addresses, verified by content digest.
- A rebind that cannot verify is a guess.  Relinking demands size and,
  when recorded, the content digest; an unverified same-basename file
  is left alone and reported.
- A gate that writes on every open would race the runner's own state
  saves.  The rebind is a no-op - no read beyond the state file, no
  write at all - when nothing moved.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from library.tools import portable_paths as pp
from library.tools import project_format as pf
from library.tools.footage_identity import compare, enumerate_footage
from library.tools.project_registry import create_project, get_project


# ── Token expansion ───────────────────────────────────────────────

def test_tokens_expand_to_this_machine(tmp_path):
    home = os.path.expanduser("~")
    assert pp.expand("$HOME/Documents/media", tmp_path) == \
        os.path.join(home, "Documents/media")
    assert pp.expand("~/Documents/media", tmp_path) == \
        os.path.join(home, "Documents/media")
    assert pp.expand("$PROJECT/raw/clip.mp4", tmp_path) == \
        str(tmp_path / "raw/clip.mp4")
    assert pp.expand("$PROJECT", tmp_path) == str(tmp_path)
    # Untouched: relative stays relative, absolute stays absolute,
    # absent stays absent.
    assert pp.expand("creative_brief.md", tmp_path) == "creative_brief.md"
    assert pp.expand("/Volumes/SSD/media", tmp_path) == "/Volumes/SSD/media"
    assert pp.expand("", tmp_path) == ""
    assert pp.expand(None, tmp_path) is None


def test_same_tokens_resolve_under_another_home(tmp_path, monkeypatch):
    """The stored file is machine-independent: only HOME moves."""
    fake = tmp_path / "fakehome"
    media = fake / "Documents" / "media"
    media.mkdir(parents=True)
    (media / "LC0001.MXF").write_bytes(b"\x00" * 2048)
    (tmp_path / "proj").mkdir()
    yaml_path = tmp_path / "proj" / "project.yaml"
    yaml_path.write_text(
        "name: Moved\nslug: moved\nsource:\n"
        "  footage_root: $HOME/Documents/media\n"
        "pipeline: {}\nresolve: {}\n",
        encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake))
    assert pp.expand("$HOME/Documents/media",
                     tmp_path / "proj") == str(media)
    # The file itself is untouched by the move: no rewrite, no backup.
    assert "$HOME/Documents/media" in yaml_path.read_text(encoding="utf-8")


def test_portablize_prefers_project_then_home(tmp_path):
    assert pp.portablize(str(tmp_path / "raw" / "a.mp4"), tmp_path) == \
        "$PROJECT/raw/a.mp4"
    # Another user's home, joined from parts rather than written as a
    # literal: the fixture names no checkout's machine, on either OS.
    mac_home = os.path.join(
        os.sep, "Users", "someone", "Documents", "media", "a.MXF")
    assert pp.portablize(mac_home, tmp_path) == \
        "$HOME/Documents/media/a.MXF"
    linux_home = os.path.join(
        os.sep, "home", "someone", "media", "a.MXF")
    assert pp.portablize(linux_home, tmp_path) == "$HOME/media/a.MXF"
    # No token names that volume: left absolute, never rewritten.
    assert pp.portablize("/Volumes/SSD/media/a.MXF", tmp_path) == \
        "/Volumes/SSD/media/a.MXF"
    assert pp.portablize("$HOME/media/a.MXF", tmp_path) == \
        "$HOME/media/a.MXF"
    assert pp.portablize("relative/path.mp4", tmp_path) == \
        "relative/path.mp4"
    # Byte-preserving on no-match: a quoted volume path keeps its quotes.
    assert pp.portablize("'/Volumes/SSD/media/a.MXF'", tmp_path) == \
        "'/Volumes/SSD/media/a.MXF'"
    # No project root means no $PROJECT expansion: never the filesystem
    # root by accident.
    assert pp.expand("$PROJECT/raw/a.mp4", None) == "$PROJECT/raw/a.mp4"


# ── The 1 -> 2 declaration migration ──────────────────────────────

def test_declaration_text_migration_rewrites_only_declarations(tmp_path):
    home = os.path.expanduser("~")
    before = (
        "# The captain's comment stays.\n"
        "name: Example\n"
        "source:\n"
        f"  footage_root: {home}/Documents/podcast media  # trailing note\n"
        "  fps: 23.976\n"
        "pipeline:\n"
        "  sfx_library: ''\n"
        '  creative_brief: "brief.md"\n'
        "resolve:\n"
        "  timeline_name: Main Edit\n")
    after, notes = pp.portablize_declarations_text(before, tmp_path)
    assert "$HOME/Documents/podcast media" in after
    assert "# The captain's comment stays." in after
    assert "# trailing note" in after
    assert "fps: 23.976" in after
    assert 'creative_brief: "brief.md"' in after
    assert any("footage_root" in note for note in notes)


def _format_1_project(tmp_path, slug="portable-legacy"):
    """A format-1 project with an absolute footage_root, as 1673 left it."""
    config = create_project(slug, name="Legacy", root=tmp_path)
    folder = tmp_path / slug
    yaml_path = folder / "project.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    home = os.path.expanduser("~")
    text = text.replace("source:\n",
                        "source:\n"
                        f"  footage_root: {home}/Documents/field media\n",
                        1)
    text = "".join(
        line for line in text.splitlines(keepends=True)
        if pf.FORMAT_VERSION_KEY not in line)
    text += f"\n{pf.FORMAT_VERSION_KEY}: 1\n"
    yaml_path.write_text(text, encoding="utf-8")
    return folder, text


def test_format_1_to_2_migrates_with_backup_and_revert(tmp_path):
    folder, _before = _format_1_project(tmp_path)
    assert pf.read_format_version(folder) == 1
    config = get_project(str(folder))
    assert config.project_format_version == pf.PROJECT_FORMAT_VERSION
    # The declaration is portable now, and reads back as this machine.
    migrated = (folder / "project.yaml").read_text(encoding="utf-8")
    assert "$HOME/Documents/field media" in migrated
    assert config.source.footage_root == os.path.join(
        os.path.expanduser("~"), "Documents/field media")
    backups = sorted(
        (folder / "pipeline_output" / "backups" / "format").glob("*"))
    assert len(backups) == 1
    (manifest,) = sorted(
        (folder / "pipeline_output" / "migrations").glob("format_*.json"))
    # Reversible: the generic revert restores the pre-migration bytes.
    from library.tools.project_migration import revert_from_manifest
    report = revert_from_manifest(str(manifest), apply=True)
    assert report[-1]["to"] == str(folder / "project.yaml")
    assert pf.read_format_version(folder) == 1


# ── Rebinding recorded state on open ──────────────────────────────

def _project_with_state(root: object, slug="moved"):
    """A real project: media in raw/, fingerprints + catalog in state."""
    from library.tools.stable_json import write_stable

    config = create_project(slug, name="Moved", root=root)
    folder = root / slug
    raw = folder / "raw"
    for name in ("TAKE01.mp4", "TAKE02.mp4"):
        (raw / name).write_bytes(os.urandom(4096) + name.encode("ascii"))
    files, _skipped = enumerate_footage(str(folder))
    assert len(files) == 2
    from library.tools.footage_identity import fingerprints_for
    fingerprints = fingerprints_for(files)
    catalog = {"clip_catalog": [
        {"clip_id": f["clip_id"], "filename": f["filename"],
         "path": f["path"], "source_file": f["path"],
         "file_size_bytes": f["size_bytes"]}
        for f in files]}
    review_note = str(folder / "pipeline_output" / "review" / "note.json")
    state = {
        "project_folder": str(folder),
        "source_fingerprints": fingerprints,
        "capability_outputs": {
            "footage.catalog": {"footage.catalog": catalog},
            "reel.note": {"reel.note": {"path": review_note}},
        },
    }
    write_stable(folder / "pipeline_data.json", state)
    return folder, state


def test_moved_project_rebinds_state_on_open(tmp_path):
    old_root = tmp_path / "old-machine"
    old_root.mkdir()
    folder, before = _project_with_state(old_root)
    recorded = {k: dict(v) for k, v in
                before["source_fingerprints"].items()}

    new_root = tmp_path / "new-machine"
    new_root.mkdir()
    moved = new_root / folder.name
    shutil.move(str(folder), str(moved))

    manifest = pf.ensure_project_format(moved)
    assert manifest is None  # already current: only the machine gate works
    # Idempotent: the gate already rebound, so a second pass is a no-op.
    assert pp.ensure_portable_paths(moved) is None

    with open(moved / "pipeline_data.json", encoding="utf-8") as handle:
        state = json.load(handle)
    assert state["project_folder"] == str(moved)
    for clip_id, record in state["source_fingerprints"].items():
        assert record["path"].startswith(str(moved)), record
        assert os.path.isfile(record["path"]), record
    catalog = (state["capability_outputs"]["footage.catalog"]
               ["footage.catalog"]["clip_catalog"])
    for entry in catalog:
        assert os.path.isfile(entry["path"]), entry
        assert os.path.isfile(entry["source_file"]), entry
    assert state["capability_outputs"]["reel.note"]["reel.note"][
        "path"].startswith(str(moved))

    # Semantic state is unchanged: the same bytes at a new address read
    # as moved, never as changed, added or removed.
    files, _skipped = enumerate_footage(str(moved))
    from library.tools.footage_identity import fingerprints_for
    delta = compare(recorded, fingerprints_for(files))
    assert sorted(delta.moved) == sorted(recorded)
    assert delta.added == [] and delta.removed == [] and delta.changed == []

    # The rewrite is backed up and manifested, beside the format ones.
    assert sorted((moved / "pipeline_output" / "backups"
                   / "portable").rglob("pipeline_data.json"))
    assert sorted((moved / "pipeline_output" / "migrations").glob(
        "portable_*.json"))


def test_no_move_is_no_write(tmp_path):
    folder, _state = _project_with_state(tmp_path)
    before = (folder / "pipeline_data.json").read_bytes()
    assert pp.ensure_portable_paths(folder) is None
    assert (folder / "pipeline_data.json").read_bytes() == before
    assert not (folder / "pipeline_output" / "backups").exists()
    assert not (folder / "pipeline_output" / "migrations").exists()


def test_unrebindable_media_is_left_in_place(tmp_path):
    folder, _state = _project_with_state(tmp_path)
    moved = tmp_path / "elsewhere" / folder.name
    (tmp_path / "elsewhere").mkdir()
    shutil.move(str(folder), str(moved))
    # The media went away with the old machine: only the project moved.
    shutil.rmtree(moved / "raw")
    report = pp.ensure_portable_paths(moved)
    assert report is not None
    assert report["unrebound"], "missing media must be named, not dropped"
    with open(moved / "pipeline_data.json", encoding="utf-8") as handle:
        state = json.load(handle)
    # In-project references still rebound; absent media keeps its record.
    assert state["project_folder"] == str(moved)
    assert state["source_fingerprints"]["clip_001"]["path"].endswith(
        "TAKE01.mp4")
    # And opening the project itself still works: no refusal, no trace.
    config = get_project(str(moved))
    assert config.slug == folder.name
