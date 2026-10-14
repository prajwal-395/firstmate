"""The source fingerprint detects a change to the native/ sources.

The whole stale-build refusal depends on this: the fingerprint the
build bakes into the extension must move when the sources move, and
hold still when they do not, or a stale build would validate against a
tree it does not match - the defect this change exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from library.tools import ren_native_source


def _tree(root: Path) -> None:
    """A minimal native/ tree covering the build scope."""
    ren_core = root / "native" / "ren-core"
    ren_proto = root / "native" / "ren-protocol"
    ren_filmcraft = root / "native" / "ren-filmcraft"
    (ren_core / "src").mkdir(parents=True)
    (ren_core / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (ren_core / "build.rs").write_text("fn main() {}\n", encoding="utf-8")
    (ren_core / "pyproject.toml").write_text("[tool.maturin]\n", encoding="utf-8")
    (ren_core / "src" / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    (ren_proto / "src").mkdir(parents=True)
    (ren_proto / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (ren_proto / "src" / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    (ren_filmcraft / "src").mkdir(parents=True)
    (ren_filmcraft / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (ren_filmcraft / "src" / "adapter.rs").write_text(
        "fn adapter() {}\n", encoding="utf-8")
    (root / "native" / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")


def test_the_fingerprint_moves_when_a_covered_source_changes(tmp_path):
    """A one-byte change to a covered source changes the fingerprint."""
    _tree(tmp_path)
    before = ren_native_source.source_fingerprint(tmp_path)
    assert before is not None

    (tmp_path / "native" / "ren-core" / "src" / "lib.rs").write_text(
        "fn main() { }\n", encoding="utf-8")
    assert ren_native_source.source_fingerprint(tmp_path) != before


def test_the_fingerprint_moves_when_a_build_manifest_changes(tmp_path):
    """A change to a build manifest (Cargo.toml, build.rs) is covered."""
    _tree(tmp_path)
    before = ren_native_source.source_fingerprint(tmp_path)

    (tmp_path / "native" / "ren-core" / "Cargo.toml").write_text(
        "[package]\nversion = \"0.2.0\"\n", encoding="utf-8")
    assert ren_native_source.source_fingerprint(tmp_path) != before


def test_the_fingerprint_moves_when_the_linked_filmcraft_adapter_changes(tmp_path):
    """The Python extension links FilmCraft, so its adapter is in scope."""
    _tree(tmp_path)
    before = ren_native_source.source_fingerprint(tmp_path)

    (tmp_path / "native" / "ren-filmcraft" / "src" / "adapter.rs").write_text(
        "fn adapter() { true; }\n", encoding="utf-8")
    assert ren_native_source.source_fingerprint(tmp_path) != before


def test_the_fingerprint_ignores_uncovered_entries(tmp_path):
    """A change outside the build scope does not move the fingerprint:
    the generated schemas, the fixtures and the README never reach the
    extension."""
    _tree(tmp_path)
    (tmp_path / "native" / "ren-protocol" / "fixtures").mkdir()
    (tmp_path / "native" / "ren-protocol" / "schemas").mkdir()
    before = ren_native_source.source_fingerprint(tmp_path)

    (tmp_path / "native" / "ren-protocol" / "fixtures" / "x.json").write_text(
        "{}\n", encoding="utf-8")
    (tmp_path / "native" / "ren-protocol" / "schemas" / "x.schema.json").write_text(
        "{}\n", encoding="utf-8")
    (tmp_path / "native" / "ren-core" / "README.md").write_text(
        "# changed\n", encoding="utf-8")
    assert ren_native_source.source_fingerprint(tmp_path) == before


def test_a_tree_with_no_native_sources_fingerprints_none(tmp_path):
    """An engine tree predating the Rust core has nothing to tie a build
    to, so any extension is unverifiable."""
    (tmp_path / "library").mkdir()
    assert ren_native_source.source_fingerprint(tmp_path) is None
