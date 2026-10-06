"""Ren refreshes a stale native part exactly once, and a fresh one never.

A missing or changed source tree must trigger one rebuild through the
part's script - the failure this module exists to remove is merged Rust
work silently falling back on desks where native/target was never
built. A fresh tree must not invoke the script at all: the check runs
at every pipeline start, and a rebuild on every start would recompile
Skia for breakfast.
"""

import pytest

from library.tools import native_refresh


def _parts(root):
    return {
        "demo": {
            "scope": ("native/demo/src",),
            "binary": ("native", "target", "release", "demo"),
            "script": ("scripts", "build_demo.sh"),
            "args": (),
        },
    }


def _write_source(root, text="v1"):
    src = root / "native" / "demo" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.rs").write_text(text, encoding="utf-8")


def _binary(root):
    return root / "native" / "target" / "release" / "demo"


class _Runner:
    """A fake build script: records argv, drops the binary, exits as told."""

    def __init__(self, root, code=0):
        self.calls = []
        self.code = code
        self.root = root

    def __call__(self, argv):
        self.calls.append(argv)
        if self.code == 0:
            binary = _binary(self.root)
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"fake-binary")
        return self.code


def test_missing_part_rebuilds_once_then_stays_fresh(tmp_path):
    _write_source(tmp_path)
    parts = _parts(tmp_path)
    runner = _Runner(tmp_path)
    first = native_refresh.ensure_current(
        tmp_path, parts=parts, runner=runner)
    assert first == {"demo": "rebuilt"}
    assert _binary(tmp_path).is_file()
    second = native_refresh.ensure_current(
        tmp_path, parts=parts, runner=runner)
    assert second == {"demo": "fresh"}
    assert len(runner.calls) == 1


def test_changed_sources_rebuild_exactly_once(tmp_path):
    _write_source(tmp_path)
    parts = _parts(tmp_path)
    runner = _Runner(tmp_path)
    native_refresh.ensure_current(tmp_path, parts=parts, runner=runner)
    _write_source(tmp_path, "v2")
    report = native_refresh.ensure_current(
        tmp_path, parts=parts, runner=runner)
    assert report == {"demo": "rebuilt"}
    assert len(runner.calls) == 2


def test_failed_build_keeps_the_previous_binary(tmp_path):
    _write_source(tmp_path)
    parts = _parts(tmp_path)
    binary = _binary(tmp_path)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"previous")
    runner = _Runner(tmp_path, code=1)
    report = native_refresh.ensure_current(
        tmp_path, parts=parts, runner=runner)
    assert binary.read_bytes() == b"previous"
    assert "failed" in report["demo"] or "failed" in str(report)
    assert len(runner.calls) == 1
    # Still stale afterwards: the next safe point retries.
    assert native_refresh.status(tmp_path, "demo", parts)[0] == "stale"


def test_fresh_tree_never_invokes_the_script(tmp_path):
    _write_source(tmp_path)
    parts = _parts(tmp_path)
    runner = _Runner(tmp_path)
    native_refresh.ensure_current(tmp_path, parts=parts, runner=runner)

    def _nope(argv):
        raise AssertionError("fresh tree must not rebuild")

    report = native_refresh.ensure_current(
        tmp_path, parts=parts, runner=_nope)
    assert report == {"demo": "fresh"}
