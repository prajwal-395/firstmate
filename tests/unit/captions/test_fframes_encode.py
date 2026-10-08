"""The FFrames encode step streams native-adapted raw RGBA into ffmpeg.

These tests pin the choices that change: premultiplication or flattening is
requested from the native renderer, raw bytes reach ffmpeg, truncated frames
refuse by name, ffmpeg errors survive, and the VideoToolbox fallback is named.
They do not pay for a render or an encode; subprocess is stubbed throughout.
"""

import json
import subprocess
import sys

import pytest

from library.tools import fframes_render


def _stub_run_factory(stdout=""):
    """A `subprocess.run` that reports an ffmpeg encoder list."""
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 0, stdout=stdout, stderr="")
    return _run


@pytest.fixture(autouse=True)
def _reset_vt_cache(monkeypatch):
    """Each test chooses its own VideoToolbox answer; clear the cache."""
    monkeypatch.setattr(fframes_render, "_VIDEOTOOLBOX", None)
    yield


# ── Which encoder is chosen ───────────────────────────────────────────

def test_videotoolbox_unavailable_off_apple(monkeypatch):
    monkeypatch.setattr(fframes_render.sys, "platform", "linux")
    monkeypatch.setattr(
        fframes_render.subprocess, "run",
        _stub_run_factory("prores_videotoolbox\n"))
    assert fframes_render.videotoolbox_available() is False


def test_videotoolbox_unavailable_without_the_encoder(monkeypatch):
    monkeypatch.setattr(fframes_render.sys, "platform", "darwin")
    monkeypatch.setattr(
        fframes_render.subprocess, "run", _stub_run_factory("prores_ks\n"))
    assert fframes_render.videotoolbox_available() is False


def test_videotoolbox_available_on_apple_with_the_encoder(monkeypatch):
    monkeypatch.setattr(fframes_render.sys, "platform", "darwin")
    monkeypatch.setattr(
        fframes_render.subprocess, "run",
        _stub_run_factory("  VFS... prores_videotoolbox  VideoToolbox\n"))
    assert fframes_render.videotoolbox_available() is True


def test_encoder_name_is_the_hardware_one_when_available(monkeypatch):
    monkeypatch.setattr(fframes_render, "videotoolbox_available",
                        lambda *a, **k: True)
    assert fframes_render.prores_encoder_name() == "prores_videotoolbox"


def test_encoder_name_falls_back_to_software(monkeypatch):
    monkeypatch.setattr(fframes_render, "videotoolbox_available",
                        lambda *a, **k: False)
    assert fframes_render.prores_encoder_name() == "prores_ks"


# ── The opaque encode streams raw RGBA, not PNGs ──────────────────────

class _StubStdout:
    """A readable stdout carrying the binary's header + raw frames."""

    def __init__(self, data):
        self._data = data
        self._pos = 0

    def read(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass


class _StubStdin:
    def __init__(self):
        self.data = bytearray()

    def write(self, blob):
        self.data.extend(blob)
        return len(blob)

    def close(self):
        pass


class _StubPopen:
    """Stubs the binary (readable stdout) and ffmpeg (writable stdin).

    `binary_data` is what the renderer's ``--raw`` stdout carries; `rc` and
    `stderr_text` let a test make either process fail.
    """

    def __init__(self, argv, binary_data=b"", rc=0, stderr_text="", **kwargs):
        self.argv = argv
        self.stderr = kwargs.get("stderr")
        if argv[0] == "ffmpeg":
            self._rc = rc
            self.stdin = _StubStdin()
            if rc != 0 and self.stderr is not None:
                self.stderr.write(stderr_text.encode("utf-8"))
            else:
                with open(argv[-1], "wb") as handle:
                    handle.write(b"dummy")
        else:
            # the renderer binary succeeds; only ffmpeg is made to fail
            self._rc = 0
            self.stdout = _StubStdout(binary_data)

    def wait(self, timeout=None):
        return self._rc

    def poll(self):
        return self._rc


def _scene_json(tmp_path, width=8, height=8):
    scene = {"version": 1, "fps": 30, "width": width, "height": height,
             "duration": 0, "nodes": []}
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    return str(path)


def _raw_bytes(width, height, frames):
    """Header + `frames` straight-alpha RGBA frames."""
    import struct
    out = bytearray(struct.pack("<II", width, height))
    for _ in range(frames):
        out.extend(bytes([0, 0, 0, 0]) * (width * height))
    return bytes(out)


def _run_opaque(monkeypatch, tmp_path, *, vt=True, binary_data=None,
                ffmpeg_rc=0, ffmpeg_stderr=""):
    """Run _encode_opaque with stubbed Popen; return (stubs, out_path).

    ``stubs`` is the list of _StubPopen objects in the order they were
    created: the renderer binary first, then ffmpeg.
    """
    monkeypatch.setattr(fframes_render, "videotoolbox_available",
                        lambda *a, **k: vt)
    monkeypatch.setattr(fframes_render, "prores_encoder_name",
                        lambda *a, **k: "prores_videotoolbox" if vt
                        else "prores_ks")
    monkeypatch.setattr(fframes_render, "fframes_binary",
                        lambda: "/fake/ren-graphics-fframes")
    monkeypatch.setattr("ren.edition.require_component",
                        lambda *a, **k: None)
    scene_path = _scene_json(tmp_path)
    if binary_data is None:
        binary_data = _raw_bytes(8, 8, 2)
    out = tmp_path / "card.mov"
    stubs = []

    def _popen(argv, **kwargs):
        stub = _StubPopen(argv, binary_data=binary_data, rc=ffmpeg_rc,
                          stderr_text=ffmpeg_stderr, **kwargs)
        stubs.append(stub)
        return stub

    monkeypatch.setattr(fframes_render.subprocess, "Popen", _popen)
    fframes_render._encode_opaque(
        scene_path, str(out), 30.0, "#102030", 60, sys.stderr)
    return stubs, out


def _values(argv, flag):
    """Every value that follows *flag* (a flag may appear more than once)."""
    out = []
    for i, token in enumerate(argv):
        if token == flag and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


def _ffmpeg_stub(stubs):
    return next(s for s in stubs if s.argv[0] == "ffmpeg")


def test_opaque_encode_streams_raw_rgba_through_videotoolbox(monkeypatch,
                                                             tmp_path):
    stubs, _ = _run_opaque(monkeypatch, tmp_path, vt=True)
    argv = _ffmpeg_stub(stubs).argv
    assert argv[argv.index("-c:v") + 1] == "prores_videotoolbox"
    assert argv[argv.index("-profile:v") + 1] == "4"
    # raw RGBA in, 12-bit alpha-carrying ProRes out
    assert _values(argv, "-pix_fmt") == ["rgba", "yuva444p12le"]
    # raw RGBA on stdin, not a PNG sequence
    assert argv[argv.index("-f") + 1] == "rawvideo"
    assert argv[argv.index("-i") + 1] == "-"
    assert argv[argv.index("-s") + 1] == "8x8"


def test_opaque_encode_requests_native_flattening(monkeypatch, tmp_path):
    stubs, _ = _run_opaque(monkeypatch, tmp_path, vt=True)
    binary = next(s for s in stubs if s.argv[0] != "ffmpeg")
    written = bytes(_ffmpeg_stub(stubs).stdin.data)
    assert written == _raw_bytes(8, 8, 2)[8:]
    assert "--background" in binary.argv
    assert binary.argv[binary.argv.index("--background") + 1] == "#102030"


def test_opaque_encode_falls_back_to_software_and_names_it(monkeypatch,
                                                           tmp_path, capsys):
    stubs, _ = _run_opaque(monkeypatch, tmp_path, vt=False)
    argv = _ffmpeg_stub(stubs).argv
    assert argv[argv.index("-c:v") + 1] == "prores_ks"
    # the fallback is named on the run's stderr
    assert "prores_ks" in capsys.readouterr().err


def test_opaque_encode_refuses_a_truncated_frame(monkeypatch, tmp_path):
    """A short final frame must refuse by name, never ship a shorter card."""
    full = _raw_bytes(8, 8, 2)
    truncated = full[:-4]  # drop 4 bytes from the last frame
    with pytest.raises(fframes_render.FFramesRenderError, match="truncated"):
        _run_opaque(monkeypatch, tmp_path, binary_data=truncated)


def test_opaque_encode_survives_ffmpeg_failure(monkeypatch, tmp_path):
    """ffmpeg's own error must reach the caller, not a ValueError from a
    closed TemporaryFile."""
    with pytest.raises(fframes_render.FFramesRenderError,
                       match="could not encode"):
        _run_opaque(monkeypatch, tmp_path, ffmpeg_rc=1,
                    ffmpeg_stderr="boom: no such filter")


# ── The overlay codec and pixel contract are unchanged ────────────────

def test_overlay_encode_still_uses_qtrle(monkeypatch, tmp_path):
    """Overlays keep the qtrle/argb carriage that Resolve reads."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "frame_000001.png").write_bytes(b"x")
    (frames_dir / "frame_000002.png").write_bytes(b"x")
    out = tmp_path / "overlay.mov"
    out.write_bytes(b"dummy")
    monkeypatch.setattr(
        fframes_render, "_perf_ledger",
        lambda: type("L", (), {"run": staticmethod(
            _stub_run_factory())})())
    result = fframes_render._encode_overlay(
        [str(frames_dir / "frame_000001.png"),
         str(frames_dir / "frame_000002.png")],
        str(out), 30.0, 60)
    assert result == str(out)


# ── The dispatcher routes by opacity ──────────────────────────────────

def test_encode_frames_routes_opaque_to_the_stream(monkeypatch, tmp_path):
    called = {}

    def _fake_opaque(scene_path, out_path, fps, background, timeout, stream,
                     encoder=None, expected=0):
        called["scene_path"] = scene_path
        called["expected"] = expected
        return out_path

    monkeypatch.setattr(fframes_render, "_encode_opaque", _fake_opaque)
    scene = tmp_path / "scene.json"
    scene.write_text("{}", encoding="utf-8")
    out = str(tmp_path / "card.mov")
    result = fframes_render.encode_frames(
        str(scene), out, fps=30.0, opaque=True, expected=7)
    assert result == out
    assert called["scene_path"] == str(scene)
    assert called["expected"] == 7


def test_encode_frames_routes_overlay_to_qtrle(monkeypatch, tmp_path):
    called = {}

    def _fake_overlay(frames, out_path, fps, timeout):
        called["frames"] = frames
        return out_path

    monkeypatch.setattr(fframes_render, "_encode_overlay", _fake_overlay)
    out = str(tmp_path / "overlay.mov")
    result = fframes_render.encode_frames(
        ["a.png", "b.png"], out, fps=30.0, opaque=False)
    assert result == out
    assert called["frames"] == ["a.png", "b.png"]
