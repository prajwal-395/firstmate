"""Ren refreshes a stale native part exactly once, and a fresh one never.

A missing or changed source tree must trigger one rebuild through the
part's script - the failure this module exists to remove is merged Rust
work silently falling back on desks where native/target was never
built. A fresh tree must not invoke the script at all: the check runs
at every pipeline start, and a rebuild on every start would recompile
Skia for breakfast.
"""

import os

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


def test_prebuilt_binary_is_used_when_no_checkout_target_exists(tmp_path):
    _write_source(tmp_path)
    parts = _parts(tmp_path)
    binary = tmp_path / "native" / "prebuilt" / "demo"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"release-binary")
    fingerprint = native_refresh.current_fingerprint(tmp_path, "demo", parts)
    sidecar = tmp_path / "native" / "prebuilt" / ".ren-fingerprints" / "demo"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(fingerprint + "\n", encoding="utf-8")

    assert native_refresh._binary_path(tmp_path, "demo", parts) == binary
    assert native_refresh.status(tmp_path, "demo", parts) == (
        "fresh", "built from this tree")


# ── the binary that SERVES ───────────────────────────────────────────

def _write_fingerprint(binary, part, value):
    """Write a binary's recorded fingerprint beside it, as a build does."""
    if binary.parent.name in {"release", "debug"}:
        base = binary.parent.parent
    else:
        base = binary.parent
    sidecar = base / ".ren-fingerprints" / part
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(value + "\n", encoding="utf-8")


def test_a_stale_prebuilt_never_shadows_a_fresh_build(tmp_path):
    """The defect: the broker served a binary the tree had replaced.

    Measured 2026-10-20: the checkout had built `ren-resolved` from the
    tree - the fingerprint beside the build matched - and the broker
    still served the older `native/prebuilt/ren-resolved` beside it, so
    the broker ran code the tree had already replaced. `ren resolved
    stop` left its worker running (ppid 1, alive 3h10m) holding the one
    Resolve, and every later writer timed out waiting for it.

    The binary that SERVES is the one built from this tree.
    """
    _write_source(tmp_path, "v1")
    parts = _parts(tmp_path)
    stale = tmp_path / "native" / "prebuilt" / "demo"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old-binary")
    _write_fingerprint(stale, "demo", "0" * 64)

    fresh = tmp_path / "native" / "target" / "release" / "demo"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"new-binary")
    native_refresh.record(tmp_path, "demo", parts, binary=fresh)

    assert native_refresh.serving_binary(tmp_path, "demo", parts) == fresh, (
        "the stale shipped prebuilt shadowed the build from this tree")
    assert native_refresh.serving_binaries(
        tmp_path, "demo", parts)[0] == fresh
    assert native_refresh.status(tmp_path, "demo", parts) == (
        "fresh", "built from this tree")


def test_the_broker_starter_serves_the_binary_built_from_this_tree(
        tmp_path, monkeypatch):
    """The broker the engine starts is the one this tree built.

    The broker starter picked its binary by a list of its own, and the
    shipped prebuilt came before the checkout's build in it - so the
    broker served code the tree had replaced, and the worker it started
    never learned which broker it belonged to (measured 2026-10-20).
    """
    from library.tools.resolved import client

    source = tmp_path / "native" / "ren-resolved" / "src"
    source.mkdir(parents=True)
    (source / "main.rs").write_text("v1", encoding="utf-8")
    stale = tmp_path / "native" / "prebuilt" / "ren-resolved"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"old-binary")
    os.chmod(stale, 0o755)
    _write_fingerprint(stale, "ren-resolved", "0" * 64)
    fresh = tmp_path / "native" / "target" / "release" / "ren-resolved"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"new-binary")
    os.chmod(fresh, 0o755)
    native_refresh.record(tmp_path, "ren-resolved", binary=fresh)

    monkeypatch.setattr(client, "_engine_root", lambda: tmp_path)
    assert client._rust_daemon() == fresh, (
        "the broker would start from a binary this tree had replaced")


def test_a_shipped_prebuilt_from_older_sources_reads_as_stale(tmp_path):
    """A prebuilt built from older sources says so.

    The shipped prebuilt is the binary a machine with no Rust toolchain
    runs. Where the tree has moved on, it is stale - and the engine has
    to say so rather than serve it quietly (measured 2026-10-20: the
    status said "fresh" while the broker serving was the older copy).
    It is still the binary that runs where there is nothing fresher,
    because a stale broker beats no broker; the stale reading is what
    makes the engine rebuild it.
    """
    _write_source(tmp_path, "v1")
    parts = _parts(tmp_path)
    stale = tmp_path / "native" / "prebuilt" / "demo"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old-binary")
    _write_fingerprint(stale, "demo", "0" * 64)

    assert native_refresh.serving_binary(tmp_path, "demo", parts) == stale
    assert native_refresh.status(tmp_path, "demo", parts) == (
        "stale", "the sources changed since it was built")


def test_refresh_progress_does_not_pollute_stdout(tmp_path, capsys):
    _write_source(tmp_path)
    native_refresh.ensure_current(
        tmp_path, parts=_parts(tmp_path), runner=_Runner(tmp_path))

    output = capsys.readouterr()
    assert output.out == ""
    assert "native: demo is missing" in output.err
    assert "native: demo rebuilt from this tree" in output.err


def test_busy_heavy_work_slot_defers_rebuild(tmp_path, monkeypatch):
    _write_source(tmp_path)
    from library.tools import heavy_work_lock

    def busy(*args, **kwargs):
        raise heavy_work_lock.resource_scheduler.Cancelled("busy")

    monkeypatch.setattr(heavy_work_lock, "heavy_work_lock", busy)
    runner = _Runner(tmp_path)
    report = native_refresh.ensure_current(
        tmp_path, parts=_parts(tmp_path), runner=runner, lock_timeout=0.01)
    assert report == {"demo": "rebuild pending (heavy-work slot busy)"}
    assert runner.calls == []


def test_native_build_demand_is_bounded_and_per_binary():
    """One writer per native build directory; no static CPU/RAM/disk
    budget gates the build (live OS memory headroom does)."""
    from library.tools import resource_scheduler

    demand = resource_scheduler.demand_for(
        "native_refresh:ren-graphics-fframes")
    assert demand == {"native_ren_graphics_fframes": 1}


def test_native_rebuild_exclusion_waits_for_an_active_render(tmp_path):
    import fcntl

    from library.tools.native_part_lock import native_part_lock

    lock_path = (tmp_path / "native" / "target" / ".ren-locks"
                 / "ren-graphics-fframes")
    with native_part_lock(tmp_path, "ren-graphics-fframes", shared=True):
        with open(lock_path, "a+b") as handle:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_rebuild_profile_matches_the_scheduler_registry(tmp_path, monkeypatch):
    """The heavy-work profile a rebuild asks for must be one the
    resource scheduler declares. A dash/underscore mismatch made every
    stale part report 'refresh skipped (unknown profile)' and no
    rebuild ever fired (measured 2026-10-08).
    """
    from contextlib import contextmanager

    from library.tools import heavy_work_lock, resource_scheduler

    seen = {}

    @contextmanager
    def fake_lock(owner, profile=heavy_work_lock.MACHINE, **kwargs):
        seen["owner"] = owner
        seen["profile"] = profile
        yield

    monkeypatch.setattr(heavy_work_lock, "heavy_work_lock", fake_lock)
    src = tmp_path / "native" / "demo" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.rs").write_text("v1", encoding="utf-8")
    for name, want in (("ren-resolved", "native_refresh:ren-resolved"),
                       ("ren_native", "native_refresh:ren-native")):
        seen.clear()

        def runner(argv, _name=name):
            binary = tmp_path / "native" / "target" / "release" / _name
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"fake-binary")
            return 0

        parts = {name: {"scope": ("native/demo/src",),
                        "binary": ("native", "target", "release", name),
                        "script": ("scripts", "build_demo.sh"), "args": ()}}
        report = native_refresh.ensure_current(
            tmp_path, parts=parts, runner=runner)
        assert report == {name: "rebuilt"}
        assert seen["profile"] == want, seen
        resource_scheduler.demand_for(seen["profile"])
