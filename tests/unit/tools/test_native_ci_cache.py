from __future__ import annotations

from pathlib import Path

from library.tools import native_ci_cache


def _tree(root: Path) -> None:
    (root / "native" / "ren-core" / "src").mkdir(parents=True)
    (root / "native" / "ren-core" / "src" / "lib.rs").write_text(
        "fn native() {}\n", encoding="utf-8")
    (root / "scripts").mkdir()
    for script in ("build_fframes_runtime.sh", "build_ren_native.sh"):
        (root / "scripts" / script).write_text("build\n", encoding="utf-8")


def test_native_ci_cache_key_changes_with_source_compiler_flags_and_python(
        tmp_path, monkeypatch):
    root = tmp_path / "repo"
    _tree(root)
    versions = {"cargo": "cargo 1", "rustc": "rustc 1", "python": "3.12"}

    def version(command):
        if command[0] == "cargo":
            return versions["cargo"]
        if command[0] == "rustc":
            return versions["rustc"]
        return versions["python"]

    monkeypatch.setattr(native_ci_cache, "_version", version)
    monkeypatch.setattr(native_ci_cache.platform, "platform", lambda: "test")
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_ENCODED_RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_PROFILE", raising=False)
    monkeypatch.delenv("CARGO_BUILD_TARGET", raising=False)
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.delenv("RUSTC_LINKER", raising=False)

    original = native_ci_cache.cache_key(root, "cargo", "python3")
    versions["rustc"] = "rustc 2"
    assert native_ci_cache.cache_key(root, "cargo", "python3") != original
    versions["rustc"] = "rustc 1"
    monkeypatch.setenv("RUSTFLAGS", "-C opt-level=1")
    assert native_ci_cache.cache_key(root, "cargo", "python3") != original
    monkeypatch.delenv("RUSTFLAGS")
    versions["python"] = "3.13"
    assert native_ci_cache.cache_key(root, "ren-native", "python3") != original
    versions["python"] = "3.12"
    (root / "native" / "ren-core" / "src" / "lib.rs").write_text(
        "fn changed() {}\n", encoding="utf-8")
    assert native_ci_cache.cache_key(root, "cargo", "python3") != original


def test_native_ci_cache_excludes_target_artifacts(tmp_path):
    root = tmp_path / "repo"
    _tree(root)
    before = native_ci_cache._digest_sources(root)
    target_file = root / "native" / "ren-core" / "target" / "build-output"
    target_file.parent.mkdir()
    target_file.write_text("ignored", encoding="utf-8")
    assert native_ci_cache._digest_sources(root) == before
