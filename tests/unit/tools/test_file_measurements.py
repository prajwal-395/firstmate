import hashlib
import os
import subprocess

from library.tools import file_measurements


def test_sha256_reuses_same_file_across_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = tmp_path / "media.bin"
    source.write_bytes(b"first contents")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    assert file_measurements.cached_sha256(source) == expected
    assert file_measurements.cached_sha256(source) == expected

    source.write_bytes(b"other contents")
    assert (
        file_measurements.cached_sha256(source)
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )


def test_sha256_invalidates_same_size_mtime_content_change(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = tmp_path / "media.bin"
    source.write_bytes(b"aaaa")
    first_stat = source.stat()
    first = file_measurements.cached_sha256(source)
    source.write_bytes(b"bbbb")
    os.utime(source, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
    # ctime remains a kernel-maintained content-change signal when callers
    # restore mtime after an in-place write.
    assert file_measurements.cached_sha256(source) != first


def test_ffprobe_cache_invalidation_and_query_separation(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = tmp_path / "clip.mov"
    source.write_bytes(b"first")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"value":1}', "")

    monkeypatch.setattr(file_measurements.subprocess, "run", fake_run)
    args = ["-v", "error", "-show_format", "-print_format", "json"]
    a = file_measurements.cached_ffprobe(source, args)
    b = file_measurements.cached_ffprobe(source, args)
    assert a.stdout == b.stdout
    assert len(calls) == 1
    file_measurements.cached_ffprobe(source, ["-v", "error", "-show_streams"])
    assert len(calls) == 2

    source.write_bytes(b"changed size")
    file_measurements.cached_ffprobe(source, args)
    assert len(calls) == 3
