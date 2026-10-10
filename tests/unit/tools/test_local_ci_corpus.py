"""The upstream engine render corpus: fixtures, pixel math, and case gates."""
from __future__ import annotations

import json
import shutil
import struct
import wave
import zlib
from pathlib import Path

import pytest

from library.tools.local_ci import corpus
from library.tools.upstream_corpus import generate_fixtures


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def write_png(path, width, height, rgba, filt=0):
    """Write an 8-bit RGBA PNG with a correctly encoded filter per row."""
    channels = 4
    raw_rows = [bytearray(bytes(rgba) * width) for _ in range(height)]
    encoded = bytearray()
    previous = bytearray(width * channels)
    for row in raw_rows:
        encoded.append(filt)
        for i in range(len(row)):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            if filt == 0:
                value = row[i]
            elif filt == 1:
                value = (row[i] - left) & 0xFF
            elif filt == 2:
                value = (row[i] - up) & 0xFF
            elif filt == 3:
                value = (row[i] - ((left + up) >> 1)) & 0xFF
            else:
                up_left = previous[i - channels] if i >= channels else 0
                value = (row[i] - _paeth(left, up, up_left)) & 0xFF
            encoded.append(value)
        previous = row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(bytes(encoded)))
                     + chunk(b"IEND", b""))


def test_corpus_contract_and_fixtures_verify():
    loaded = corpus.load_corpus()
    assert loaded["corpus_version"]
    assert len(loaded["cases"]) == 3
    assert corpus.verify_fixtures(loaded)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    raise AssertionError("cannot locate the repository root")


def test_corpus_fixtures_are_git_tracked():
    """A gitignored fixture passes locally and fails intake in CI."""
    import subprocess

    root = _repo_root()
    tracked = subprocess.run(["git", "ls-files", "library/tools/upstream_corpus"],
                             cwd=root, capture_output=True, encoding="utf-8",
                             check=True).stdout.splitlines()
    loaded = corpus.load_corpus()
    for name in loaded["fixtures"]:
        assert f"library/tools/upstream_corpus/fixtures/{name}" in tracked, (
            f"{name} is not git-tracked; .gitignore may be swallowing it")


def test_fixture_tamper_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(corpus, "FIXTURES_DIR", tmp_path)
    (tmp_path / "still-red.png").write_bytes(b"tampered")
    loaded = corpus.load_corpus()
    with pytest.raises(corpus.CorpusRefused):
        corpus.verify_fixtures(loaded)


def test_committed_stills_decode_to_solid_colors():
    for name, rgb in generate_fixtures.STILLS.items():
        image = corpus.read_png(corpus.FIXTURES_DIR / name)
        assert (image["width"], image["height"], image["channels"]) == (64, 36, 3)
        assert set(bytes(image["pixels"][i:i + 3])
                   for i in range(0, len(image["pixels"]), 3)) == {bytes(rgb)}


def test_png_filters_all_decode(tmp_path):
    for filt in (0, 1, 2, 3, 4):
        path = tmp_path / f"f{filt}.png"
        write_png(path, 8, 4, (10, 20, 30, 40), filt=filt)
        image = corpus.read_png(path)
        assert set(tuple(image["pixels"][i:i + 4])
                   for i in range(0, len(image["pixels"]), 4)) == {(10, 20, 30, 40)}


def test_pixel_math_reference_values():
    black = bytes(64 * [0, 0, 0])
    white = bytes(64 * [255, 255, 255])
    assert corpus.max_abs_diff(black, bytes(black)) == 0
    assert corpus.max_abs_diff(black, white) == 255
    assert corpus.psnr_db(black, bytes(black)) == float("inf")
    assert corpus.psnr_db(black, white) == pytest.approx(0.0)
    gray_a = bytes(64 * [128, 128, 128])
    gray_b = bytes(64 * [129, 129, 129])
    assert corpus.psnr_db(gray_a, gray_b) == pytest.approx(48.13, abs=0.01)
    image = {"width": 8, "height": 8, "channels": 3, "pixels": black}
    same = dict(image)
    assert corpus.ssim(image, same) == pytest.approx(1.0)
    other = dict(image, pixels=white)
    assert corpus.ssim(image, other) < 0.01


def test_wav_fixture_reads_as_stereo_unity():
    info = corpus.read_wav(corpus.FIXTURES_DIR / "stereo-1s.wav")
    assert info["channels"] == 2
    assert info["sample_rate"] == 44100
    assert info["frames"] == 44100
    assert info["peak"] == pytest.approx(0.5, abs=0.001)
    assert corpus.db_difference(info["peak"], 0.5) == pytest.approx(0.0, abs=0.01)


def test_json_safe_replaces_infinite_psnr():
    assert corpus.json_safe({"psnr": float("inf")}) == {"psnr": None}
    json.dumps(corpus.json_safe({"psnr": float("inf")}))


class Log:
    def __init__(self):
        self.text = ""

    def write(self, message):
        self.text += str(message)

    def flush(self):
        pass


def write_quiet_wav(path, peak=0.25):
    frames = bytearray()
    for _ in range(4410):
        frames += struct.pack("<hh", int(peak * 32767), int(peak * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(bytes(frames))


def fake_fc_runner(monkeypatch, tmp_path, fixture_map=None, import_errors=None):
    """Simulate the FilmCraft CLI argv shapes the drivers prove."""
    fixture_map = fixture_map or {}

    def run(argv, log, **kwargs):
        log.write(f"$ {' '.join(argv)}\n")
        text = ""
        code = 0
        if "exec" in argv:
            command = argv[argv.index("exec") + 1]
            if command == "file.newSequence":
                text = json.dumps({"sequence": 1})
            elif command == "file.import":
                if import_errors:
                    text = json.dumps({"items": [], "errors": import_errors})
                else:
                    text = json.dumps({"items": [11, 12, 13, 14, 15], "errors": []})
            elif command == "sequence.inspect":
                text = json.dumps({"settings": {"frame_rate": {"num": 25, "den": 1}}})
            elif command == "timeline.place":
                text = json.dumps({"clips": [101]})
            elif command == "timeline.razor":
                text = json.dumps({"cuts": 1})
            elif command == "file.exportMedia":
                params = json.loads(argv[argv.index("exec") + 2])
                source = fixture_map.get("wav", corpus.FIXTURES_DIR / "stereo-1s.wav")
                if fixture_map.get("quiet_wav"):
                    write_quiet_wav(Path(params["path"]))
                else:
                    shutil.copy(source, params["path"])
                text = json.dumps({"job": 7})
            else:
                code = 1
                text = "unknown command"
        elif "render" in argv:
            seconds = float(argv[argv.index("--seconds") + 1])
            out = argv[argv.index("--out") + 1]
            index = int(seconds - 0.5)
            names = ["still-red.png", "still-green.png", "still-blue.png",
                     "still-white.png", "still-gray.png"]
            shutil.copy(corpus.FIXTURES_DIR / names[index], out)
            text = "rendered"
        else:
            code = 1
        return {"returncode": code, "stdout": text,
                "wall_seconds": 0.01, "peak_rss_mb": 10.0}

    monkeypatch.setattr(corpus, "run_measured", run)
    return run


def test_stills_roundtrip_passes_on_proven_shapes(monkeypatch, tmp_path):
    fake_fc_runner(monkeypatch, tmp_path)
    loaded = corpus.load_corpus()
    case = next(c for c in loaded["cases"] if c["kind"] == "stills_roundtrip")
    measurement = corpus.case_stills_roundtrip(
        "filmcraft-cli", case, tmp_path, Log())
    assert measurement["frames_verified"] == 5
    assert all(m["max_abs_diff"] == 0 for m in measurement["cases"])


def test_stills_roundtrip_refuses_import_errors(monkeypatch, tmp_path):
    fake_fc_runner(monkeypatch, tmp_path, import_errors=["gone.png"])
    loaded = corpus.load_corpus()
    case = next(c for c in loaded["cases"] if c["kind"] == "stills_roundtrip")
    with pytest.raises(corpus.CorpusRefused):
        corpus.case_stills_roundtrip("filmcraft-cli", case, tmp_path, Log())


def test_audio_unity_passes_and_breaches(monkeypatch, tmp_path):
    monkeypatch.setattr(corpus, "ffprobe_streams", lambda path, log: {
        "streams": [{"channels": 2}], "format": {"duration": "1.0"}})
    loaded = corpus.load_corpus()
    case = next(c for c in loaded["cases"] if c["kind"] == "audio_unity")
    fake_fc_runner(monkeypatch, tmp_path)
    measurement = corpus.case_audio_unity("filmcraft-cli", case, tmp_path, Log())
    assert measurement["level_db"] == pytest.approx(0.0)
    fake_fc_runner(monkeypatch, tmp_path, fixture_map={"quiet_wav": True})
    with pytest.raises(corpus.CorpusRefused):
        corpus.case_audio_unity("filmcraft-cli", case, tmp_path, Log())


def fake_ec_runner(monkeypatch):
    def run(argv, log, **kwargs):
        log.write(f"$ {' '.join(argv)}\n")
        if argv[1] == "run":
            return {"returncode": 0, "stdout": "{}", "wall_seconds": 0.01,
                    "peak_rss_mb": 10.0}
        assert argv[1] == "render"
        out = argv[argv.index("--out") + 1]
        if "--prores" in argv:
            Path(out).write_bytes(b"prores-placeholder")
            return {"returncode": 0, "stdout": "{}", "wall_seconds": 0.01,
                    "peak_rss_mb": 10.0}
        prefix = out.split("#")[0]
        parent = out.rsplit("/", 1)[0] or "."
        for frame in range(5):
            alpha = 0 if frame == 0 else 200
            write_png(Path(parent) / f"{prefix.split('/')[-1]}{frame:05d}.png",
                      96, 64, (255, 255, 255, alpha))
        return {"returncode": 0, "stdout": "{}", "wall_seconds": 0.01,
                "peak_rss_mb": 10.0}

    monkeypatch.setattr(corpus, "run_measured", run)


def test_alpha_title_passes_on_proven_shapes(monkeypatch, tmp_path):
    fake_ec_runner(monkeypatch)
    monkeypatch.setattr(corpus, "ffprobe_streams", lambda path, log: {
        "streams": [{"width": 96, "height": 64, "pix_fmt": "yuva444p12le"}]})
    monkeypatch.setattr(corpus, "ffmpeg_decode_rgba", lambda path, log:
                        b"".join(bytes((255, 255, 255, a))
                                 for a in [0] * 10 + [200] * 10))
    loaded = corpus.load_corpus()
    case = next(c for c in loaded["cases"] if c["kind"] == "alpha_title")
    measurement = corpus.case_alpha_title("effectcraft-cli", case, tmp_path, Log())
    assert measurement["frames"] == 5
    assert measurement["first_alpha_sum"] == 0
    assert measurement["last_visible"] is True


def test_alpha_title_refuses_wrong_frame_count(monkeypatch, tmp_path):
    def run(argv, log, **kwargs):
        if argv[1] == "run":
            return {"returncode": 0, "stdout": "{}", "wall_seconds": 0.01,
                    "peak_rss_mb": 10.0}
        return {"returncode": 1, "stdout": "render failed",
                "wall_seconds": 0.01, "peak_rss_mb": 10.0}

    monkeypatch.setattr(corpus, "run_measured", run)
    loaded = corpus.load_corpus()
    case = next(c for c in loaded["cases"] if c["kind"] == "alpha_title")
    with pytest.raises(corpus.CorpusRefused):
        corpus.case_alpha_title("effectcraft-cli", case, tmp_path, Log())


def test_run_corpus_records_perf_findings_without_blocking(monkeypatch, tmp_path):
    fake_fc_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(corpus, "ffprobe_streams", lambda path, log: {
        "streams": [{"channels": 2}], "format": {"duration": "1.0"}})
    prior = {"filmcraft-edit-50": {"wall_seconds": 0.0001, "peak_rss_mb": 0.01}}
    result = corpus.run_corpus({"filmcraft": "filmcraft-cli"}, tmp_path,
                               Log(), prior_perf=prior, only={"filmcraft"})
    assert result["status"] == "passed"
    assert set(result["cases"]) == {"filmcraft-solids-roundtrip", "filmcraft-stereo-unity"}
    workload = result["perf"]["workloads"]["filmcraft-edit-50"]
    assert workload["ops"] == 50
    assert any("filmcraft-edit-50" in finding
               for finding in result["perf"]["findings"])
    json.dumps(result)


def test_run_corpus_refusal_carries_partial_result(monkeypatch, tmp_path):
    fake_fc_runner(monkeypatch, tmp_path, import_errors=["gone.png"])
    with pytest.raises(corpus.CorpusRefused) as excinfo:
        corpus.run_corpus({"filmcraft": "filmcraft-cli"}, tmp_path, Log(),
                          only={"filmcraft"})
    assert excinfo.value.result["status"] == "failed"
    json.dumps(corpus.json_safe(excinfo.value.result))


def test_probe_refusals_without_toolchain(monkeypatch, tmp_path):
    monkeypatch.setattr(corpus.shutil, "which", lambda _name: None)
    with pytest.raises(corpus.CorpusRefused):
        corpus.ffprobe_streams(tmp_path / "x.mov", Log())
    with pytest.raises(corpus.CorpusRefused):
        corpus.ffmpeg_decode_rgba(tmp_path / "x.mov", Log())
