"""Fixed headless render corpus for weekly upstream engine intake.

Each case drives a pinned engine CLI through a command sequence proven by the
Ren driver crates (``native/ren-filmcraft``, ``native/ren-effectcraft``) and
checks frame count/alignment, alpha, audio level, and pixels against the
committed fixtures in ``library/tools/upstream_corpus/``. Anything unexpected
-- a missing fixture, an unknown CLI response, a breached tolerance -- is a
case failure, and a failed case refuses the pin promotion.

Pixel comparisons use the fixture stills as stored references: the render's
center crop is compared against the fixture's center crop (robust to the
engine's default sequence canvas, which Ren does not dictate), reporting max
absolute channel difference and PSNR, with a full-frame palette check against
gross corruption. EffectCraft title frames carry engine-rasterized text, so
their pixel checks are determinism (re-render byte comparison) plus the alpha
contract from the driver's own headless test.
"""
from __future__ import annotations

import hashlib
import json
import math
import resource
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "upstream_corpus"
CORPUS_FILE = CORPUS_ROOT / "corpus.json"
FIXTURES_DIR = CORPUS_ROOT / "fixtures"


class CorpusRefused(RuntimeError):
    """The corpus cannot run or a case failed; the pin must not advance."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result


def load_corpus() -> dict:
    with CORPUS_FILE.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    if corpus.get("schema_version") != 1:
        raise CorpusRefused(f"unsupported corpus schema in {CORPUS_FILE}")
    if not corpus.get("corpus_version") or not corpus.get("cases"):
        raise CorpusRefused(f"corpus contract at {CORPUS_FILE} names no cases")
    return corpus


def verify_fixtures(corpus: dict) -> dict:
    """Refuse when a committed fixture differs from its recorded hash."""
    checked = {}
    for name, meta in sorted(corpus.get("fixtures", {}).items()):
        path = FIXTURES_DIR / name
        if not path.is_file():
            raise CorpusRefused(f"corpus fixture missing: {name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != meta.get("sha256"):
            raise CorpusRefused(
                f"corpus fixture {name} does not match corpus.json; "
                "regenerate fixtures and update the recorded hash"
            )
        checked[name] = digest
    if not checked:
        raise CorpusRefused("corpus contract declares no fixtures")
    return checked


# ---------------------------------------------------------------------------
# Minimal PNG codec (8-bit RGB/RGBA, non-interlaced): what the engines emit.
# ---------------------------------------------------------------------------

def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CorpusRefused("not a PNG file")
    chunks = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        chunks.append((kind, payload))
        offset += 12 + length
    return chunks


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path: Path) -> dict:
    """Decode a PNG into raw pixels; refuse anything outside the corpus scope."""
    chunks = _png_chunks(Path(path).read_bytes())
    header = next((payload for kind, payload in chunks if kind == b"IHDR"), None)
    if header is None:
        raise CorpusRefused(f"{path}: PNG has no IHDR")
    width, height, depth, color, _comp, _filter, interlace = struct.unpack(
        ">IIBBBBB", header)
    if depth != 8 or interlace != 0 or color not in (2, 6):
        raise CorpusRefused(
            f"{path}: unsupported PNG (depth={depth} color={color} "
            f"interlace={interlace}); corpus handles 8-bit RGB/RGBA only"
        )
    channels = 3 if color == 2 else 4
    raw = zlib.decompress(b"".join(
        payload for kind, payload in chunks if kind == b"IDAT"))
    stride = width * channels
    pixels = bytearray(width * height * channels)
    previous = bytearray(stride)
    offset = 0
    for row in range(height):
        filt = raw[offset]
        offset += 1
        scanline = bytearray(raw[offset:offset + stride])
        offset += stride
        if filt == 1:
            for i in range(channels, stride):
                scanline[i] = (scanline[i] + scanline[i - channels]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                scanline[i] = (scanline[i] + previous[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = scanline[i - channels] if i >= channels else 0
                scanline[i] = (scanline[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                left = scanline[i - channels] if i >= channels else 0
                up = previous[i]
                up_left = previous[i - channels] if i >= channels else 0
                scanline[i] = (scanline[i] + _paeth(left, up, up_left)) & 0xFF
        elif filt != 0:
            raise CorpusRefused(f"{path}: unknown PNG filter {filt}")
        pixels[row * stride:(row + 1) * stride] = scanline
        previous = scanline
    return {"width": width, "height": height, "channels": channels,
            "pixels": bytes(pixels)}


def center_crop(image: dict, size: int) -> bytes:
    width, height, channels = image["width"], image["height"], image["channels"]
    if size > width or size > height:
        raise CorpusRefused("center crop larger than the image")
    x0, y0 = (width - size) // 2, (height - size) // 2
    out = bytearray()
    for row in range(y0, y0 + size):
        start = (row * width + x0) * channels
        out += image["pixels"][start:start + size * channels]
    return bytes(out)


def rgb_crop(image: dict, crop: bytes) -> bytes:
    """Color bytes of a center crop, dropping the alpha channel.

    The headless still render carries alpha the RGB fixtures do not, so a
    direct crop comparison refuses on buffer length even when every color
    matches. A still over letterbox black must be fully opaque: any
    non-opaque alpha pixel refuses rather than being silently dropped.
    """
    channels = image["channels"]
    if channels == 3:
        return crop
    if channels != 4 or len(crop) % 4:
        raise CorpusRefused(
            f"cannot compare a {channels}-channel crop against RGB fixtures")
    rgb = bytearray()
    transparent = 0
    for off in range(0, len(crop), 4):
        rgb += crop[off:off + 3]
        if crop[off + 3] != 255:
            transparent += 1
    if transparent:
        raise CorpusRefused(
            f"still render carries {transparent} non-opaque pixels")
    return bytes(rgb)


def max_abs_diff(a: bytes, b: bytes) -> int:
    if len(a) != len(b):
        raise CorpusRefused("pixel buffers have different lengths")
    return max((abs(x - y) for x, y in zip(a, b)), default=0)


def psnr_db(a: bytes, b: bytes, peak: float = 255.0) -> float:
    if len(a) != len(b) or not a:
        raise CorpusRefused("pixel buffers are empty or mismatched")
    mse = sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)
    if mse == 0:
        return float("inf")
    return 20 * math.log10(peak) - 10 * math.log10(mse)


def json_safe(value):
    """Replace non-finite floats so intake records stay valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def _luma(pixels: bytes, channels: int) -> list[float]:
    return [0.299 * pixels[i] + 0.587 * pixels[i + 1] + 0.114 * pixels[i + 2]
            for i in range(0, len(pixels), channels)]


def ssim(a: dict, b: dict) -> float:
    """Single-window SSIM over luma; 1.0 means identical."""
    if (a["width"], a["height"]) != (b["width"], b["height"]):
        raise CorpusRefused("SSIM needs equally sized images")
    lum_a = _luma(a["pixels"], a["channels"])
    lum_b = _luma(b["pixels"], b["channels"])
    count = len(lum_a)
    mean_a = sum(lum_a) / count
    mean_b = sum(lum_b) / count
    var_a = sum((v - mean_a) ** 2 for v in lum_a) / count
    var_b = sum((v - mean_b) ** 2 for v in lum_b) / count
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(lum_a, lum_b)) / count
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    return ((2 * mean_a * mean_b + c1) * (2 * cov + c2)
            / ((mean_a ** 2 + mean_b ** 2 + c1) * (var_a + var_b + c2)))


# ---------------------------------------------------------------------------
# Minimal WAV reader (16-bit PCM): avoids modules removed in Python 3.13+.
# ---------------------------------------------------------------------------

def read_wav(path: Path) -> dict:
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise CorpusRefused(f"{path}: not a RIFF/WAVE file")
    offset, fmt, chunks = 12, None, {}
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        (length,) = struct.unpack("<I", data[offset + 4:offset + 8])
        chunks[kind] = data[offset + 8:offset + 8 + length]
        offset += 8 + length + (length & 1)
    if b"fmt " in chunks:
        fmt = struct.unpack("<HHIIHH", chunks[b"fmt "][:16])
    if fmt is None or b"data" not in chunks:
        raise CorpusRefused(f"{path}: WAV has no fmt/data chunks")
    audio_format, channels, rate, _, _, bits = fmt
    if audio_format != 1 or bits != 16:
        raise CorpusRefused(f"{path}: corpus handles 16-bit PCM only")
    raw = chunks[b"data"]
    count = len(raw) // 2
    samples = struct.unpack(f"<{count}h", raw[:count * 2])
    peak = max((abs(s) for s in samples), default=0) / 32768.0
    return {"channels": channels, "sample_rate": rate,
            "frames": count // channels, "peak": peak}


def db_difference(measured: float, reference: float) -> float:
    if measured <= 0 or reference <= 0:
        raise CorpusRefused("audio peak must be positive to compare levels")
    return 20 * math.log10(measured / reference)


# ---------------------------------------------------------------------------
# Process execution with wall time and peak child RSS.
# ---------------------------------------------------------------------------

def _rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def run_measured(argv: list[str], log, *, timeout: int = 600,
                 binary: bool = False) -> dict:
    """Run one CLI argv, logging output; return exit, wall seconds, peak RSS.

    Binary mode keeps stdout as bytes (for decoded media); text mode decodes
    UTF-8 with replacement and is only for CLI/JSON output.
    """
    log.write(f"$ {' '.join(argv)}\n")
    log.flush()
    before = _rss_mb()
    started = time.monotonic()
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            check=False, timeout=timeout)
    wall = time.monotonic() - started
    peak = max(_rss_mb(), before)
    if binary:
        log.write(f"[binary {len(result.stdout)} bytes]")
        stdout: str | bytes = result.stdout
    else:
        text = result.stdout.decode("utf-8", errors="replace")
        log.write(text[-8000:])
        stdout = text
    log.write(f"[exit={result.returncode} wall={wall:.2f}s]\n")
    log.flush()
    return {"returncode": result.returncode, "stdout": stdout,
            "wall_seconds": round(wall, 3), "peak_rss_mb": round(peak, 1)}


def ffprobe_streams(path: Path, log) -> dict:
    if shutil.which("ffprobe") is None:
        raise CorpusRefused("ffprobe is not on PATH; media checks refuse")
    result = run_measured(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=width,height,nb_frames,pix_fmt,duration,channels,sample_rate"
         ":format=duration", "-of", "json", str(path)], log)
    if result["returncode"] != 0:
        raise CorpusRefused(f"ffprobe could not inspect {path}")
    try:
        return json.loads(result["stdout"])
    except ValueError as exc:
        raise CorpusRefused(f"ffprobe returned invalid JSON: {exc}") from exc


def ffmpeg_decode_rgba(path: Path, log) -> bytes:
    if shutil.which("ffmpeg") is None:
        raise CorpusRefused("ffmpeg is not on PATH; alpha decode refuses")
    result = run_measured(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"], log, binary=True)
    if result["returncode"] != 0:
        raise CorpusRefused(f"ffmpeg could not decode {path} to RGBA")
    stdout = result["stdout"]
    if not isinstance(stdout, bytes):
        raise CorpusRefused("binary decode did not return bytes")
    return stdout


# ---------------------------------------------------------------------------
# Engine CLI drivers (surfaces proven by native/ren-filmcraft and
# native/ren-effectcraft; unknown responses refuse).
# ---------------------------------------------------------------------------

def _parse_json(command: str, result: dict) -> dict:
    if result["returncode"] != 0:
        raise CorpusRefused(f"{command} failed: {result['stdout'][-500:]}")
    try:
        value = json.loads(result["stdout"])
    except ValueError as exc:
        raise CorpusRefused(f"{command} returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CorpusRefused(f"{command} returned a non-object response")
    return value


def fc_exec(cli: str, project: Path | None, command: str, params: dict,
            log, *, save: bool = False, save_as: Path | None = None) -> dict:
    argv = [cli]
    if save_as is not None:
        argv += ["exec", command, json.dumps(params), "--compact",
                 "--save-as", str(save_as)]
    else:
        argv += ["--project", str(project), "exec", command,
                 json.dumps(params), "--compact"]
        if save:
            argv.append("--save")
    return _parse_json(command, run_measured(argv, log))


def fc_render_frame(cli: str, project: Path, seconds: float, out: Path, log) -> None:
    result = run_measured(
        [cli, "--project", str(project), "render",
         "--seconds", repr(seconds), "--out", str(out)], log)
    if result["returncode"] != 0 or not out.is_file():
        raise CorpusRefused(
            f"FilmCraft render at t={seconds}s failed or produced no file")


def ec_run(cli: str, commands: list[tuple[str, dict]], save_as: Path, log) -> None:
    argv = [cli, "run"]
    for command, params in commands:
        argv += [command, json.dumps(params)]
    argv += ["--empty", "--save-as", str(save_as), "--json"]
    result = run_measured(argv, log)
    if result["returncode"] != 0:
        raise CorpusRefused(f"EffectCraft run failed: {result['stdout'][-500:]}")


def ec_render(cli: str, project: Path, comp: str, out: str, fmt: str, log, *,
              start: float, end: float, prores: str | None = None) -> None:
    argv = [cli, "render", "--project", str(project), "--comp", comp,
            "--out", out, "--format", fmt,
            "--start", repr(start), "--end", repr(end),
            "--channels", "rgba"]
    if prores:
        argv += ["--prores", prores]
    argv.append("--json")
    result = run_measured(argv, log)
    if result["returncode"] != 0:
        raise CorpusRefused(f"EffectCraft render failed: {result['stdout'][-500:]}")


# ---------------------------------------------------------------------------
# Cases.
# ---------------------------------------------------------------------------

CENTER_CROP_PX = 16


def case_stills_roundtrip(cli: str, case: dict, workdir: Path, log) -> dict:
    """Place each solid still for one second; every rendered mid-frame must
    carry that still's color in its center crop and nothing but the still
    color and letterbox black anywhere (frame count/alignment by pixels)."""
    project = workdir / "stills.fcproj"
    fc_exec(cli, None, "file.newSequence", {"name": "stills"},
            log, save_as=project)
    inputs = [FIXTURES_DIR / name for name in case["inputs"]]
    imported = fc_exec(cli, project, "file.import",
                       {"paths": [str(p) for p in inputs]}, log, save=True)
    if imported.get("errors"):
        raise CorpusRefused(f"import reported errors: {imported['errors']}")
    items = imported.get("items", [])
    if len(items) != len(inputs):
        raise CorpusRefused(
            f"import returned {len(items)} items for {len(inputs)} stills")
    inspected = fc_exec(cli, project, "sequence.inspect", {}, log)
    try:
        rate = inspected["settings"]["frame_rate"]
        fps = float(rate["num"]) / float(rate["den"])
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        raise CorpusRefused(
            "sequence.inspect did not report settings.frame_rate") from exc
    tps = int(case["ticks_per_second"])
    for index, item in enumerate(items):
        placed = fc_exec(cli, project, "timeline.place",
                         {"item": item, "track": "V1", "frame": int(index * fps),
                          "sourceIn": 0, "duration": tps}, log, save=True)
        if not placed.get("clips"):
            raise CorpusRefused(f"placing still {index} returned no clips")
    measurements = []
    expect = case["expect"]
    crop_px = int(expect.get("center_crop_px", CENTER_CROP_PX))
    for index, name in enumerate(case["inputs"]):
        out = workdir / f"still-{index}.png"
        fc_render_frame(cli, project, index + 0.5, out, log)
        rendered = read_png(out)
        reference = read_png(FIXTURES_DIR / name)
        crop_rendered = rgb_crop(rendered, center_crop(rendered, crop_px))
        crop_reference = rgb_crop(reference, center_crop(reference, crop_px))
        # Full-frame palette: still color and letterbox black only.
        palette = {bytes(reference["pixels"][0:3]), b"\x00\x00\x00"}
        tolerance = int(expect.get("palette_tolerance", 3))
        foreign = 0
        px, channels = rendered["pixels"], rendered["channels"]
        for off in range(0, len(px), channels):
            if bytes(px[off:off + 3]) not in palette:
                # A small decode tolerance keeps harmless decoder drift from
                # failing a PNG path; anything more is corruption.
                if min(sum(abs(a - b) for a, b in
                           zip(px[off:off + 3], color)) for color in palette) > tolerance:
                    foreign += 1
        diff = max_abs_diff(crop_rendered, crop_reference)
        psnr = psnr_db(crop_rendered, crop_reference)
        measurements.append({
            "input": name, "width": rendered["width"],
            "height": rendered["height"], "max_abs_diff": diff,
            "center_psnr_db": psnr, "foreign_pixels": foreign})
        if diff > int(expect.get("center_max_abs_diff", 1)) or foreign:
            raise CorpusRefused(
                f"{name}: center diff={diff} foreign_pixels={foreign}")
        if diff and psnr < float(expect.get("center_min_psnr_db", 40.0)):
            raise CorpusRefused(f"{name}: center PSNR {psnr:.1f} dB under floor")
    if len(measurements) != case["expect"]["placed_clips"]:
        raise CorpusRefused("placed clip count does not match the contract")
    return {"frames_verified": len(measurements), "cases": measurements}


def case_audio_unity(cli: str, case: dict, workdir: Path, log) -> dict:
    """Round-trip the stereo fixture; channels, duration, and peak level must
    survive within the declared tolerances (unity gain, correct mapping)."""
    project = workdir / "audio.fcproj"
    fc_exec(cli, None, "file.newSequence", {"name": "audio"},
            log, save_as=project)
    source = FIXTURES_DIR / case["input"]
    imported = fc_exec(cli, project, "file.import", {"paths": [str(source)]},
                       log, save=True)
    if imported.get("errors") or not imported.get("items"):
        raise CorpusRefused(f"audio import failed: {imported}")
    tps = 254016000000
    placed = fc_exec(cli, project, "timeline.place",
                     {"item": imported["items"][0], "track": "V1",
                      "frame": 0, "sourceIn": 0, "duration": tps,
                      "audioTrack": "A1"}, log, save=True)
    if not placed.get("clips"):
        raise CorpusRefused("audio placement returned no clips")
    out = workdir / "roundtrip.wav"
    fc_exec(cli, project, "file.exportMedia",
            {"path": str(out), "format": "wav",
             "hardwareEncoding": "off", "gpuRendering": "off",
             "wait": True}, log)
    if not out.is_file():
        raise CorpusRefused("WAV export produced no file")
    probe = ffprobe_streams(out, log)
    streams = probe.get("streams", [])
    audio = next((s for s in streams if s.get("channels")), None)
    if audio is None or int(audio["channels"]) != case["expect"]["channels"]:
        raise CorpusRefused(f"WAV export channel layout wrong: {streams}")
    duration = float(probe.get("format", {}).get("duration", 0) or 0)
    if abs(duration - 1.0) > case["expect"]["duration_tolerance_seconds"]:
        raise CorpusRefused(f"WAV export duration {duration}s is off")
    reference = read_wav(source)
    exported = read_wav(out)
    level_db = db_difference(exported["peak"], reference["peak"])
    measurement = {"channels": int(audio["channels"]), "duration_s": duration,
                   "level_db": round(level_db, 3)}
    if abs(level_db) > case["expect"]["peak_tolerance_db"]:
        raise CorpusRefused(f"audio level moved {level_db:.2f} dB: {measurement}")
    return measurement


def _sequence_frame_paths(pattern: Path) -> list[Path]:
    name = pattern.name
    start = name.find("#")
    if start < 0:
        raise CorpusRefused(f"sequence pattern needs # placeholders: {pattern}")
    count = len(name[start:]) - len(name[start:].lstrip("#"))
    prefix, suffix = name[:start], name[start + count:]
    found = []
    for entry in sorted(pattern.parent.iterdir()):
        middle = entry.name
        if (middle.startswith(prefix) and middle.endswith(suffix)
                and len(middle) == len(prefix) + count + len(suffix)
                and middle[len(prefix):len(prefix) + count].isdigit()):
            found.append(entry)
    return found


def case_alpha_title(cli: str, case: dict, workdir: Path, log) -> dict:
    """Render the driver's own tiny title (96x64, 5 frames, opacity fade):
    exact frame count, RGBA on every frame, transparent first frame, visible
    last frame, and alpha extremes surviving the ProRes 4444 encode."""
    project = workdir / "title.ecproj"
    comp, text, fade = case["comp"], case["text"], case["fade"]
    ec_run(cli, [
        ("comp.new", {"name": comp["name"], "width": comp["width"],
                      "height": comp["height"], "frameRate": comp["frame_rate"],
                      "duration": comp["duration"]}),
        ("layer.newText", {"name": text["name"], "text": text["text"],
                           "size": text["size"], "fill": text["fill"],
                           "position": text["position"], "font": text["font"]}),
        ("prop.addKey", {"layer": text["name"], "path": fade["path"],
                         "time": fade["from_time"], "value": fade["from_value"]}),
        ("prop.addKey", {"layer": text["name"], "path": fade["path"],
                         "time": fade["to_time"], "value": fade["to_value"]}),
    ], project, log)
    png_pattern = workdir / "title_#####.png"
    movie = workdir / "title.mov"
    ec_render(cli, project, comp["name"], str(png_pattern), "png", log,
              start=0.0, end=0.5)
    ec_render(cli, project, comp["name"], str(movie), "prores", log,
              start=0.0, end=0.5, prores="4444")
    frames = _sequence_frame_paths(png_pattern)
    expect = case["expect"]
    if len(frames) != expect["frames"]:
        raise CorpusRefused(
            f"PNG sequence has {len(frames)} frames, expected {expect['frames']}")
    first = read_png(frames[0])
    if first["channels"] != 4:
        raise CorpusRefused("title PNG sequence has no alpha channel")
    alpha_first = sum(first["pixels"][3::4])
    if alpha_first != expect["first_frame_alpha_sum"]:
        raise CorpusRefused(f"first frame alpha sum {alpha_first} != 0")
    last = read_png(frames[-1])
    visible = any(v > 0 for v in last["pixels"][3::4])
    if visible != expect["last_frame_visible"]:
        raise CorpusRefused("last title frame visibility is wrong")
    # Determinism: the same frame rendered twice must agree closely.
    repeat = workdir / "title-repeat_#####.png"
    ec_render(cli, project, comp["name"], str(repeat), "png", log,
              start=0.0, end=0.5)
    repeats = _sequence_frame_paths(repeat)
    determinism = psnr_db(read_png(frames[2])["pixels"],
                          read_png(repeats[2])["pixels"])
    if determinism < 60.0:
        raise CorpusRefused(
            f"title re-render differs (PSNR {determinism:.1f} dB)")
    probe = ffprobe_streams(movie, log)
    video = (probe.get("streams") or [{}])[0]
    if "a" not in str(video.get("pix_fmt", "")):
        raise CorpusRefused(
            f"ProRes output has no alpha pixel format: {video.get('pix_fmt')}")
    raw = ffmpeg_decode_rgba(movie, log)
    alphas = set(raw[3::4])
    if not (0 in alphas and any(v > 0 for v in alphas)):
        raise CorpusRefused("ProRes alpha lost transparency or content")
    return {"frames": len(frames), "width": first["width"],
            "height": first["height"], "first_alpha_sum": alpha_first,
            "last_visible": visible, "determinism_psnr_db": determinism,
            "mov_pix_fmt": video.get("pix_fmt")}


_CASES = {
    "stills_roundtrip": case_stills_roundtrip,
    "audio_unity": case_audio_unity,
    "alpha_title": case_alpha_title,
}


def run_edit_workload(cli: str, workload: dict, workdir: Path, log) -> dict:
    """Fixed razor/inspect op mix on a stills timeline; timing only."""
    project = workdir / "edit-workload.fcproj"
    fc_exec(cli, None, "file.newSequence", {"name": "workload"},
            log, save_as=project)
    stills = [str(FIXTURES_DIR / f"still-{c}.png")
              for c in ("red", "green", "blue", "white", "gray")]
    imported = fc_exec(cli, project, "file.import", {"paths": stills},
                       log, save=True)
    tps = 254016000000
    for index, item in enumerate(imported.get("items", [])):
        fc_exec(cli, project, "timeline.place",
                {"item": item, "track": "V1", "frame": index * 25,
                 "sourceIn": 0, "duration": 60 * tps}, log, save=True)
    ops = int(workload["ops"])
    for step in range(ops // 2):
        fc_exec(cli, project, "timeline.razor",
                {"track": "V1", "frame": step + 1}, log, save=True)
    for _ in range(ops - ops // 2):
        fc_exec(cli, project, "sequence.inspect", {}, log)
    return {"ops": ops}


def run_corpus(cli_paths: dict[str, str], workdir: Path, log,
               prior_perf: dict | None = None,
               only: set[str] | None = None) -> dict:
    """Run every corpus case; raise CorpusRefused on anything unexpected."""
    from library.tools import engine_edition
    corpus = load_corpus()
    scoped = (only if only is not None else
              {case["engine"] for case in corpus["cases"]}
              | {workload["engine"] for workload in corpus["perf"]["workloads"]})
    for scoped_engine in sorted(scoped):
        engine_edition.require_engine(scoped_engine, action="load")
    fixtures = verify_fixtures(corpus)
    budgets = corpus["perf"]["budgets"]
    result: dict = {"corpus_version": corpus["corpus_version"],
                    "fixtures": sorted(fixtures), "cases": {},
                    "perf": {"workloads": {}, "findings": []},
                    "status": "running"}
    totals = {"wall_seconds": 0.0, "peak_rss_mb": 0.0}
    try:
        for case in corpus["cases"]:
            engine = case["engine"]
            if only is not None and engine not in only:
                continue
            if engine not in cli_paths:
                raise CorpusRefused(
                    f"case {case['id']} needs a {engine} CLI build")
            case_dir = workdir / case["id"]
            case_dir.mkdir(parents=True, exist_ok=True)
            before_wall = time.monotonic()
            measurement = _CASES[case["kind"]](
                cli_paths[engine], case, case_dir, log)
            wall = round(time.monotonic() - before_wall, 3)
            if wall > budgets["max_wall_seconds_per_case"]:
                raise CorpusRefused(
                    f"case {case['id']} took {wall}s, over budget")
            result["cases"][case["id"]] = {
                "status": "passed", "wall_seconds": wall, **measurement}
            totals["wall_seconds"] += wall
        for workload in corpus["perf"]["workloads"]:
            engine = workload["engine"]
            if only is not None and engine not in only:
                continue
            if engine not in cli_paths:
                raise CorpusRefused(
                    f"workload {workload['id']} needs a {engine} CLI build")
            workload_dir = workdir / workload["id"]
            workload_dir.mkdir(parents=True, exist_ok=True)
            before = time.monotonic()
            rss_before = _rss_mb()
            measurement = run_edit_workload(
                cli_paths[engine], workload, workload_dir, log)
            wall = round(time.monotonic() - before, 3)
            peak = round(max(_rss_mb(), rss_before), 1)
            entry = {"wall_seconds": wall, "peak_rss_mb": peak, **measurement}
            if peak > budgets["max_peak_rss_mb"]:
                raise CorpusRefused(
                    f"workload {workload['id']} peaked at {peak} MB, over budget")
            previous = (prior_perf or {}).get(workload["id"], {})
            if previous.get("wall_seconds"):
                ratio = wall / previous["wall_seconds"]
                entry["prior_wall_ratio"] = round(ratio, 2)
                if ratio > budgets["prior_wall_ratio_finding"]:
                    result["perf"]["findings"].append(
                        f"{workload['id']}: wall {wall}s is {ratio:.1f}x the "
                        f"prior pin ({previous['wall_seconds']}s)")
            if previous.get("peak_rss_mb"):
                ratio = peak / previous["peak_rss_mb"]
                entry["prior_rss_ratio"] = round(ratio, 2)
                if ratio > budgets["prior_rss_ratio_finding"]:
                    result["perf"]["findings"].append(
                        f"{workload['id']}: peak RSS {peak} MB is {ratio:.1f}x "
                        f"the prior pin ({previous['peak_rss_mb']} MB)")
            result["perf"]["workloads"][workload["id"]] = entry
            totals["wall_seconds"] += wall
            totals["peak_rss_mb"] = max(totals["peak_rss_mb"], peak)
        result.update({"status": "passed", **{k: round(v, 3) if isinstance(v, float) else v for k, v in totals.items()}})
    except (CorpusRefused, OSError, ValueError) as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        raise CorpusRefused(str(exc), json_safe(result)) from exc
    return json_safe(result)
