"""The ren_native build is selected per tree, never fought over.

Measured 2026-10-08: every checkout's refresh rebuilt the one shared
venv copy (551 stale-rebuild lines in the shared broker log, zero
completions - the record step crashed on `joinpath(*None)`), each
attempt holding the machine heavy-work slot for a full compile. These
tests pin the other half of that incident: the importer loads the
cache entry matching its own tree, a shared-venv copy from another
tree never triggers a rebuild, and two trees keep two entries.
"""

import sys
from importlib.machinery import EXTENSION_SUFFIXES
from types import SimpleNamespace

import pytest

from library.tools import edit_backend, native_refresh, ren_native_cache
from library.tools.ren_native_source import source_fingerprint

#: The extension filename the RUNNING interpreter imports as
#: `ren_native`: fixtures must write this tag, not a hardcoded one, so
#: the suite pins selection on whatever interpreter runs it.
THIS_INTERPRETER_SO = "ren_native" + EXTENSION_SUFFIXES[0]

#: A tag no running interpreter ever imports: the foreign-interpreter
#: build below. `cpython-99` is not a released interpreter, and the
#: only suffix of this file any interpreter recognises is the generic
#: `.so` - which leaves the module named
#: `ren_native.cpython-99-darwin`, never `ren_native`.
FOREIGN_INTERPRETER_SO = "ren_native.cpython-99-darwin.so"

REN_NATIVE_PART = {"ren_native": native_refresh.PARTS["ren_native"]}


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """A private machine cache: no test touches the real one."""
    directory = tmp_path / "cache"
    monkeypatch.setenv(ren_native_cache.CACHE_ENV, str(directory))
    return directory


@pytest.fixture
def save_sys_modules():
    """Restore `sys.modules["ren_native"]` afterwards: selection tests
    load and refuse modules by that one name."""
    before = sys.modules.get("ren_native")
    had = "ren_native" in sys.modules
    yield
    if had:
        sys.modules["ren_native"] = before
    else:
        sys.modules.pop("ren_native", None)


@pytest.fixture
def save_backend():
    """A fresh switch state for the test, restored afterwards.

    Reset (not just saved): collection-time imports elsewhere in the
    session may already have resolved the switch to the machine's real
    build, and these cases must pin selection from a clean slate."""
    tried = edit_backend._ren_native_tried
    module = edit_backend._ren_native
    said = edit_backend._ren_native_said_stale
    edit_backend._ren_native_tried = False
    edit_backend._ren_native = None
    edit_backend._ren_native_said_stale = False
    yield
    edit_backend._ren_native_tried = tried
    edit_backend._ren_native = module
    edit_backend._ren_native_said_stale = said


def _write_native_tree(root, text="v1"):
    """A checkout carrying ren_native build sources."""
    src = root / "native" / "ren-core" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "lib.rs").write_text(text, encoding="utf-8")
    (root / "native" / "ren-core" / "Cargo.toml").write_text(
        "[package]\nname = \"ren-core\"\n", encoding="utf-8")
    (root / "native" / "ren-core" / "build.rs").write_text(
        "fn main() {}\n", encoding="utf-8")
    (root / "native" / "ren-core" / "pyproject.toml").write_text(
        "[project]\nname = \"ren-native\"\n", encoding="utf-8")
    proto = root / "native" / "ren-protocol" / "src"
    proto.mkdir(parents=True, exist_ok=True)
    (proto / "lib.rs").write_text(text, encoding="utf-8")
    (root / "native" / "ren-protocol" / "Cargo.toml").write_text(
        "[package]\nname = \"ren-protocol\"\n", encoding="utf-8")
    (root / "native" / "Cargo.toml").write_text(
        "[workspace]\n", encoding="utf-8")
    return root


def _recorded_entry(cache_dir, fingerprint):
    """A cache entry with a recorded build for `fingerprint`."""
    entry = ren_native_cache.entry_dir(fingerprint, cache_dir)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / THIS_INTERPRETER_SO).write_bytes(b"fake-so")
    ren_native_cache.write_recorded(entry, fingerprint)
    return entry


def _foreign_recorded_entry(cache_dir, fingerprint):
    """A cache entry whose only build is for another interpreter.

    The 2026-10-10 abort: a 3.14 test process loaded its own tree's
    `cpython-312` build (the source fingerprint matched, so nothing
    refused it) and died in `judge_conditions` on the first FFI call.
    """
    entry = ren_native_cache.entry_dir(fingerprint, cache_dir)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / FOREIGN_INTERPRETER_SO).write_bytes(b"fake-so")
    ren_native_cache.write_recorded(entry, fingerprint)
    return entry


def test_select_returns_the_entry_matching_this_tree(cache, tmp_path):
    root = _write_native_tree(tmp_path / "tree")
    fingerprint = source_fingerprint(root)
    entry = _recorded_entry(cache, fingerprint)

    seen = {}

    def load(path):
        seen["path"] = path
        return SimpleNamespace(source_fingerprint=lambda: fingerprint)

    assert ren_native_cache.select(root, load=load) is not None
    assert seen["path"] == ren_native_cache.extension_file(entry)


def test_select_refuses_a_build_for_another_interpreter(cache, tmp_path):
    """A build for another interpreter is never loaded: the loader is
    not reached, so a foreign ABI cannot abort this process."""
    root = _write_native_tree(tmp_path / "tree")
    fingerprint = source_fingerprint(root)
    entry = _foreign_recorded_entry(cache, fingerprint)

    assert ren_native_cache.extension_file(entry) is None

    def _nope(path):
        raise AssertionError("a foreign build must never load")

    assert ren_native_cache.select(root, load=_nope) is None


def test_select_loads_this_interpreters_build_beside_a_foreign_one(cache, tmp_path):
    """An entry may hold builds for several interpreters; selection
    loads this one's."""
    root = _write_native_tree(tmp_path / "tree")
    fingerprint = source_fingerprint(root)
    entry = _foreign_recorded_entry(cache, fingerprint)
    (entry / THIS_INTERPRETER_SO).write_bytes(b"fake-so")

    seen = {}

    def load(path):
        seen["path"] = path
        return SimpleNamespace(source_fingerprint=lambda: fingerprint)

    assert ren_native_cache.select(root, load=load) is not None
    assert seen["path"] == entry / THIS_INTERPRETER_SO


def test_load_extension_refuses_a_foreign_interpreter_build(tmp_path):
    foreign = tmp_path / FOREIGN_INTERPRETER_SO
    foreign.write_bytes(b"fake-so")
    with pytest.raises(ImportError, match="another interpreter"):
        ren_native_cache.load_extension(foreign)


def test_select_refuses_a_build_baked_from_another_tree(
        cache, tmp_path, save_sys_modules):
    root = _write_native_tree(tmp_path / "tree")
    fingerprint = source_fingerprint(root)
    _recorded_entry(cache, fingerprint)
    other = SimpleNamespace(source_fingerprint=lambda: "other-tree")

    def load(path):
        sys.modules["ren_native"] = other
        return other

    assert ren_native_cache.select(root, load=load) is None
    # A refused load must not linger: a later plain `import ren_native`
    # would otherwise inherit another tree's build.
    assert sys.modules.get("ren_native") is not other


def test_select_is_none_where_this_tree_has_no_entry(cache, tmp_path):
    root = _write_native_tree(tmp_path / "tree")

    def _nope(path):
        raise AssertionError("nothing is built - nothing may load")

    assert ren_native_cache.select(root, load=_nope) is None


def test_select_is_none_without_sources(cache, tmp_path):
    assert ren_native_cache.select(tmp_path, load=lambda p: None) is None
    assert ren_native_cache.select(None) is None


def test_two_trees_keep_two_entries(cache, tmp_path):
    first = _write_native_tree(tmp_path / "first", "v1")
    second = _write_native_tree(tmp_path / "second", "v2")
    first_fp = source_fingerprint(first)
    second_fp = source_fingerprint(second)
    assert first_fp != second_fp
    _recorded_entry(cache, first_fp)
    _recorded_entry(cache, second_fp)

    def load(path):
        fingerprint = (first_fp if first_fp in str(path) else second_fp)
        return SimpleNamespace(source_fingerprint=lambda: fingerprint)

    assert ren_native_cache.select(first, load=load) is not None
    assert ren_native_cache.select(second, load=load) is not None
    assert (ren_native_cache.entry_dir(first_fp, cache)
            != ren_native_cache.entry_dir(second_fp, cache))


def test_status_is_fresh_from_the_cache_despite_a_stale_venv(
        cache, tmp_path, save_sys_modules):
    """A shared-venv copy from another tree neither reads as fresh nor
    provokes a rebuild: only this tree's own entry is consulted."""
    root = _write_native_tree(tmp_path / "tree")
    fingerprint = source_fingerprint(root)
    _recorded_entry(cache, fingerprint)
    sys.modules["ren_native"] = SimpleNamespace(
        source_fingerprint=lambda: "another-tree")

    assert native_refresh.status(
        root, "ren_native", REN_NATIVE_PART) == ("fresh", "built from this tree")

    def _nope(argv):
        raise AssertionError("a fresh tree must not rebuild")

    assert native_refresh.ensure_current(
        root, parts=REN_NATIVE_PART, runner=_nope) == {"ren_native": "fresh"}


def test_rebuild_records_so_the_next_check_is_fresh(cache, tmp_path):
    """The 2026-10-08 storm: a successful rebuild crashed before
    recording (`joinpath(*None)` for the binary-less part), so every
    safe point rebuilt again. A rebuild must land its record."""
    root = _write_native_tree(tmp_path / "tree")
    calls = []

    def runner(argv):
        calls.append(argv)
        assert argv[1].endswith("build_ren_native.sh")
        assert argv[-1] == str(cache)
        entry = ren_native_cache.entry_dir(source_fingerprint(root), cache)
        entry.mkdir(parents=True, exist_ok=True)
        (entry / THIS_INTERPRETER_SO).write_bytes(b"fake-so")
        return 0

    report = native_refresh.ensure_current(
        root, parts=REN_NATIVE_PART, runner=runner)
    assert report == {"ren_native": "rebuilt"}
    assert len(calls) == 1

    def _nope(argv):
        raise AssertionError("a recorded tree must not rebuild")

    assert native_refresh.ensure_current(
        root, parts=REN_NATIVE_PART, runner=_nope) == {"ren_native": "fresh"}
    assert len(calls) == 1


def test_missing_entry_reports_missing_not_stale(cache, tmp_path):
    root = _write_native_tree(tmp_path / "tree")
    state, detail = native_refresh.status(root, "ren_native", REN_NATIVE_PART)
    assert state == "missing"
    assert "no build for this tree" in detail


def test_foreign_only_entry_reports_missing_for_this_interpreter(cache, tmp_path):
    """An entry holding only another interpreter's build is not fresh
    and not merely stale: the next safe point must rebuild for THIS
    interpreter rather than reuse or refuse it."""
    root = _write_native_tree(tmp_path / "tree")
    fingerprint = source_fingerprint(root)
    _foreign_recorded_entry(cache, fingerprint)

    state, detail = native_refresh.status(root, "ren_native", REN_NATIVE_PART)
    assert state == "missing"
    assert "this interpreter" in detail

    calls = []

    def runner(argv):
        calls.append(argv)
        entry = ren_native_cache.entry_dir(fingerprint, cache)
        (entry / THIS_INTERPRETER_SO).write_bytes(b"fake-so")
        return 0

    assert native_refresh.ensure_current(
        root, parts=REN_NATIVE_PART, runner=runner) == {"ren_native": "rebuilt"}
    assert len(calls) == 1
    assert native_refresh.status(
        root, "ren_native", REN_NATIVE_PART) == ("fresh", "built from this tree")


def test_backend_prefers_the_cache_entry_over_the_venv(
        cache, tmp_path, save_sys_modules, save_backend, monkeypatch):
    """The switch loads this tree's entry even where the venv holds
    another tree's build."""
    root = _write_native_tree(tmp_path / "tree")
    fingerprint = source_fingerprint(root)
    _recorded_entry(cache, fingerprint)
    cached = SimpleNamespace(source_fingerprint=lambda: fingerprint)
    sys.modules["ren_native"] = SimpleNamespace(
        source_fingerprint=lambda: "another-tree")
    monkeypatch.setattr(ren_native_cache, "load_extension",
                        lambda path: cached)
    monkeypatch.setattr(edit_backend, "_engine_root", lambda: root)

    assert edit_backend.rust() is cached


def test_backend_falls_back_to_python_without_an_entry(
        cache, tmp_path, save_sys_modules, save_backend, monkeypatch):
    root = _write_native_tree(tmp_path / "tree")
    sys.modules.pop("ren_native", None)
    monkeypatch.setattr(edit_backend, "_engine_root", lambda: root)

    assert edit_backend.rust() is None
