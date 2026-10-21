"""Static font instances persist across processes on disk.

Pinning a variable font (`instantiateVariableFont`) costs seconds and
gigabytes of transient objects per (file, weight) - Ren paid it on
every fresh build process even though the font file never changes.
The process-level `lru_cache` never survived past one build; the disk
cache does. A warm disk hit must return byte-identical instances
without touching fontTools at all, and an unreadable cache entry must
fall back to a fresh instantiation rather than failing the render.

The geneva face below is static, so the test never pays variable
instantiation: it exercises the cache path, not the instancer.
"""

from __future__ import annotations

import os
import threading

import pytest

FONT = "/System/Library/Fonts/Geneva.ttf"


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VEP_FONT_CACHE", str(tmp_path / "fonts"))
    from library.graphics_ir import fonts as fonts_mod
    fonts_mod._instance_bytes.cache_clear()
    fonts_mod._FONTTOOLS_VERSION = None
    yield tmp_path / "fonts"
    fonts_mod._instance_bytes.cache_clear()


def _needs_font():
    import os
    return pytest.mark.skipif(
        not os.path.isfile(FONT), reason="macOS Geneva.ttf unavailable")


@_needs_font()
def test_warm_disk_cache_returns_identical_bytes_without_fonttools(
        isolated_cache, monkeypatch):
    from library.graphics_ir import fonts as fonts_mod

    first_bytes, first_name = fonts_mod.static_instance(FONT, 400)
    assert (isolated_cache).is_dir()
    assert first_bytes[:4] in (
        b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")

    fonts_mod._instance_bytes.cache_clear()

    import fontTools.ttLib

    def _refuse(*args, **kwargs):
        raise AssertionError("a warm disk hit must not read the font")

    monkeypatch.setattr(fontTools.ttLib, "TTFont", _refuse)
    second_bytes, second_name = fonts_mod.static_instance(FONT, 400)
    assert second_bytes == first_bytes
    assert second_name == first_name


@_needs_font()
def test_unreadable_cache_entry_falls_back_to_fresh_bytes(
        isolated_cache):
    from library.graphics_ir import fonts as fonts_mod

    first_bytes, _ = fonts_mod.static_instance(FONT, 400)
    fonts_mod._instance_bytes.cache_clear()

    key = fonts_mod._disk_cache_key(
        fonts_mod._signature(FONT), 400)
    with open(isolated_cache / key, "wb") as handle:
        handle.write(b"not a font")
    (isolated_cache / (key + ".name")).write_text(
        "Bogus", encoding="utf-8")

    second_bytes, second_name = fonts_mod.static_instance(FONT, 400)
    assert second_bytes == first_bytes
    assert second_name
    # The corrupt entry is repaired, not kept.
    assert (isolated_cache / key).read_bytes() == first_bytes


@_needs_font()
def test_distinct_weights_key_distinct_entries(isolated_cache):
    from library.graphics_ir import fonts as fonts_mod

    light, _ = fonts_mod.static_instance(FONT, 300)
    bold, _ = fonts_mod.static_instance(FONT, 700)
    assert light != bold


def test_two_threads_writing_one_face_do_not_truncate_each_other(
        isolated_cache, monkeypatch):
    """The cache's temp name must be per THREAD, not just per process.

    The reel's caption cards render in waves, so two threads in one
    process can want the same face at the same weight at the same time.
    A temp name keyed on the process alone made both threads write the
    SAME file: one writer truncated the other's bytes mid-write, and
    `os.replace` published a half-written font to the next process.

    The defect is a race, so it is pinned by the interleaving rather
    than by the bytes: each thread's temp path must be its own.
    """
    from library.graphics_ir import fonts as fonts_mod

    seen = []
    barrier = threading.Barrier(2)

    real_replace = os.replace

    def record_replace(src, dst):
        # The cache publishes the face and its name separately; only the
        # FACE's temp path is the one that must be per thread.
        if not str(src).endswith(".name"):
            seen.append((threading.get_ident(), src))
            barrier.wait(timeout=5)
        return real_replace(src, dst)

    monkeypatch.setattr(fonts_mod.os, "replace", record_replace)

    errors = []

    def write(payload):
        try:
            fonts_mod._disk_cache_write("face-key", payload, "Unique")
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(b"a" * 4096,)),
               threading.Thread(target=write, args=(b"b" * 4096,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    # Two threads, two temp files: the same path twice is the defect.
    assert len(seen) == 2
    assert len({src for _ident, src in seen}) == 2
    # And the published face is one of the two whole payloads, never a
    # half-written mixture.
    published = (isolated_cache / "face-key").read_bytes()
    assert published in (b"a" * 4096, b"b" * 4096)
