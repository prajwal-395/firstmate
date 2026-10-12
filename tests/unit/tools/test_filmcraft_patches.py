"""The fork-patch cache contract: one key, one directory, both runtimes.

The Rust runtime install and the Python resolvers must agree on the
digest-keyed cache directory, or the Rust-built CLI lands where Python
never looks and an unpatched binary keeps running. These tests pin the
key algorithm, the directory shape, and the shared resolution - without
building anything.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import filmcraft_patches

REVISION = "0800a8b0aa4a703ba42172f415b45a61f8d8a5e1"


def _write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_patch_set_key_pins_the_cross_language_contract():
    # Golden: the same value is asserted in
    # `native/ren-filmcraft/src/lib.rs`. A patch-series change moves both
    # goldens together; a mismatch means the two runtimes resolve
    # different cache directories.
    assert (filmcraft_patches.patch_set_key()
            == "67028574e064e1ad" == filmcraft_patches.patch_set_key(
                filmcraft_patches.FILMCRAFT_PATCH_DIR))
    assert filmcraft_patches.patch_files() == [
        filmcraft_patches.FILMCRAFT_PATCH_DIR
        / "001-sequential-batch-render.patch"]


def test_empty_patch_set_keys_the_fnv_offset_basis(tmp_path):
    # Independent algorithm check: FNV-1a/64 of no bytes is the offset
    # basis, 0xcbf29ce484222325. A from-scratch reimplementation emitting
    # this proves the feed framing, not just self-consistency.
    assert filmcraft_patches.patch_set_key(tmp_path) == "cbf29ce484222325"
    assert filmcraft_patches.patch_files(tmp_path) == []


def test_key_ignores_non_patch_files_and_follows_sorted_order(tmp_path):
    _write(tmp_path, "REQUESTS.md", "# notes are inert\n")
    _write(tmp_path, "002-second.patch", "second\n")
    _write(tmp_path, "001-first.patch", "first\n")
    _write(tmp_path, "notes.txt", "also inert\n")
    assert [path.name for path in filmcraft_patches.patch_files(tmp_path)] == [
        "001-first.patch", "002-second.patch"]
    keyed = filmcraft_patches.patch_set_key(tmp_path)
    _write(tmp_path, "REQUESTS.md", "# notes are inert, edited\n")
    assert filmcraft_patches.patch_set_key(tmp_path) == keyed


def test_key_moves_when_patch_content_moves(tmp_path):
    _write(tmp_path, "001-only.patch", "original\n")
    before = filmcraft_patches.patch_set_key(tmp_path)
    _write(tmp_path, "001-only.patch", "amended\n")
    assert filmcraft_patches.patch_set_key(tmp_path) != before


def test_cache_dir_names_revision_plus_key_never_bare_revision(tmp_path):
    key = filmcraft_patches.patch_set_key()
    directory = filmcraft_patches.filmcraft_cache_dir(
        tmp_path, REVISION)
    assert directory == (
        tmp_path / ".cache" / "ren" / "filmcraft" / f"{REVISION}-p{key}")
    binary = filmcraft_patches.filmcraft_cli_path(tmp_path, REVISION)
    assert binary == directory / "bin" / "filmcraft-cli"
    # The defect this names: resolving the bare-revision directory reuses
    # the pre-patch binary the sibling lane installed.
    assert binary != (tmp_path / ".cache" / "ren" / "filmcraft"
                      / REVISION / "bin" / "filmcraft-cli")


def test_both_resolvers_refuse_naming_the_keyed_path(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from library.tools import project_model_cli, project_model_qualification

    expected = str(filmcraft_patches.filmcraft_cli_path(tmp_path, REVISION))
    with pytest.raises(project_model_cli.ProjectModelCliRefused) as cli_exc:
        project_model_cli._filmcraft_cli()
    assert expected in str(cli_exc.value)
    with pytest.raises(project_model_qualification.Pm9Refused) as qual_exc:
        project_model_qualification._filmcraft_cli()
    assert expected in str(qual_exc.value)


def test_install_cli_to_cache_lands_the_qualified_binary(tmp_path):
    source = _write(tmp_path / "build", "filmcraft-cli", "patched-bytes\n")
    home = tmp_path / "home"
    dest = filmcraft_patches.install_cli_to_cache(source, home, REVISION)
    assert dest == filmcraft_patches.filmcraft_cli_path(home, REVISION)
    assert dest.read_bytes() == b"patched-bytes\n"


def test_install_cli_to_cache_refuses_a_missing_binary(tmp_path):
    with pytest.raises(FileNotFoundError):
        filmcraft_patches.install_cli_to_cache(
            tmp_path / "no-such-binary", tmp_path / "home", REVISION)
    assert not (tmp_path / "home").exists()
