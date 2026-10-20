"""A stale ren_native build is refused, not used silently.

The defect: the Rust edit-algebra extension imported fine when it was
older than the source tree, and with the Rust backend selected a stale
extension returned `None` verdicts for ops the current algebra had
rewritten (`mask.delete`/`mask.track`) with no warning. Nothing noticed
or rebuilt it.

The fix bakes a source fingerprint into the extension at build time
(`native/ren-core/build.rs`, from `library/tools/ren_native_source.py`)
and has `edit_backend.rust()` select this tree's per-tree cache entry
first, comparing the shared venv's copy against the running tree only
as a legacy fallback. These cases pin that behaviour: a build whose
fingerprint differs from the tree is never used, a matching build is,
and the refusal is said once.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from library.tools import edit_backend, ren_native_cache, ren_native_source

REPO_ROOT = Path(__file__).resolve().parents[3]


class _FakeModule:
    """A stand-in for the ren_native extension module."""

    def __init__(self, fingerprint):
        self._fingerprint = fingerprint

    def source_fingerprint(self):
        return self._fingerprint


@pytest.fixture
def backend(monkeypatch, tmp_path):
    """A fresh edit_backend state per test, with the engine root pinned
    to this repo so the tree fingerprint is the repo's own, and the
    per-tree cache pointed at an empty dir so these cases pin the
    shared-venv fallback path rather than whatever the machine holds."""
    monkeypatch.setattr(edit_backend, "_ren_native", None)
    monkeypatch.setattr(edit_backend, "_ren_native_tried", False)
    monkeypatch.setattr(edit_backend, "_ren_native_said_stale", False)
    monkeypatch.setattr(edit_backend, "_engine_root", lambda: REPO_ROOT)
    monkeypatch.setenv(edit_backend.BACKEND_ENV, edit_backend.RUST)
    monkeypatch.setenv(ren_native_cache.CACHE_ENV, str(tmp_path / "cache"))
    yield edit_backend
    monkeypatch.delenv(edit_backend.BACKEND_ENV, raising=False)


def test_rust_is_selected_when_no_backend_is_configured(backend, monkeypatch):
    monkeypatch.delenv(edit_backend.BACKEND_ENV, raising=False)
    assert backend.use_rust()


def test_python_remains_selectable(backend, monkeypatch):
    monkeypatch.setenv(edit_backend.BACKEND_ENV, edit_backend.PYTHON)
    assert not backend.use_rust()


def test_a_build_whose_fingerprint_differs_is_never_used(
        backend, monkeypatch, capsys):
    """The defect: a stale build is refused, and the Python backend runs."""
    monkeypatch.setitem(sys.modules, "ren_native", _FakeModule("a" * 64))
    assert backend.rust() is None
    err = capsys.readouterr().err
    assert "refusing the installed build" in err
    assert "source fingerprint does not match" in err


def test_a_build_with_no_fingerprint_is_never_used(backend, monkeypatch, capsys):
    """A build that predates the fingerprint mechanism is refused too."""
    monkeypatch.setitem(sys.modules, "ren_native", _FakeModule("unknown"))
    assert backend.rust() is None
    assert "refusing the installed build" in capsys.readouterr().err


def test_a_matching_build_is_used(backend, monkeypatch):
    """A build whose fingerprint matches the tree is used."""
    current = ren_native_source.source_fingerprint(REPO_ROOT)
    fresh = _FakeModule(current)
    monkeypatch.setitem(sys.modules, "ren_native", fresh)
    assert backend.rust() is fresh


def test_the_refusal_is_said_once(backend, monkeypatch, capsys):
    """The warning is printed once per process, not on every call."""
    monkeypatch.setitem(sys.modules, "ren_native", _FakeModule("b" * 64))
    assert backend.rust() is None
    assert backend.rust() is None
    err = capsys.readouterr().err
    assert err.count("refusing the installed build") == 1


def test_a_missing_extension_falls_back(backend, monkeypatch, capsys):
    """No extension: the Python backend runs, with no refusal warning."""
    monkeypatch.setitem(sys.modules, "ren_native", None)
    assert backend.rust() is None
    assert "refusing" not in capsys.readouterr().err


def test_a_broken_extension_falls_back(backend, monkeypatch, capsys):
    """An installed copy that raises on import is no build: measured
    2026-10-10, the shared venv's ren_native/__init__.py referenced
    its own unbound name, so every fallback import raised NameError
    instead of ImportError and broke the switch. The Python backend
    runs quietly."""
    import builtins

    real_import = builtins.__import__

    def broken(name, *args, **kwargs):
        if name == "ren_native" or name.startswith("ren_native."):
            raise NameError("name 'ren_native' is not defined")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    assert backend.rust() is None
    assert "refusing" not in capsys.readouterr().err


def test_the_switch_off_never_imports(backend, monkeypatch):
    """With the switch off, the extension is never imported at all."""
    monkeypatch.setenv(edit_backend.BACKEND_ENV, edit_backend.PYTHON)
    monkeypatch.setitem(sys.modules, "ren_native", _FakeModule("a" * 64))
    assert backend.rust() is None


# ── The boundary is profiled before it is migrated (PERF-019) ────────
#
# The Python/Rust boundary's cost depends on its SHAPE: a projected
# property batch reads the clips it names, a full crossing encodes the
# whole timeline into JSON and decodes it again. The ledger recorded
# one opaque `edit_core_rust` span, so a slow boundary could not say
# which half of it was slow - and PERF-019 asks for the boundary to be
# profiled BEFORE implementation code is migrated across it.


def test_the_boundary_says_which_shape_it_crossed():
    """A property batch crosses projected; anything else crosses whole."""
    property_ops = [{"op": "clip.set_property", "unique_id": "uid-1",
                     "key": "Zoom", "value": 2}]
    mixed_ops = [{"op": "clip.set_property", "unique_id": "uid-1",
                  "key": "Zoom", "value": 2},
                 {"op": "clip.trim", "unique_id": "uid-2"}]

    assert edit_backend.projection_of(property_ops) == "property"
    assert edit_backend.projection_of(mixed_ops) == "full"
    assert edit_backend.projection_of([]) == "empty"


def test_the_boundary_says_how_much_crossed():
    snapshot = {"start_frame": 0, "end_frame": 100,
                "tracks": [
                    {"type": "video", "index": 1, "name": "V1",
                     "clips": [{"unique_id": "a"}, {"unique_id": "b"}]},
                    {"type": "audio", "index": 1, "name": "A1",
                     "clips": [{"unique_id": "c"}]},
                ]}

    shape = edit_backend.boundary_shape(
        snapshot, [{"op": "clip.set_property"}],
        projection="property")

    assert shape["snapshot_clips"] == 3
    assert shape["snapshot_tracks"] == 2
    assert shape["operations"] == 1
    assert shape["projection"] == "property"


def test_an_unknown_projection_is_not_reported_as_measured():
    """A shape nobody measured is `None`, never a default that reads as
    a measured one."""
    shape = edit_backend.boundary_shape(None, [])

    assert "projection" not in shape
    assert shape["snapshot_clips"] == 0
