"""The evidence taken off the DELIVERED file, not off the timeline.

A timeline can be right and the file wrong. The render can carry the
wrong frame, lose its audio, clip, or hold a black frame nobody
declared. The punch list says the exported video or audio is what gets
evaluated, so this module reads the FILE and says what is measurably
true of it.

Every measurement here comes from the engine's own file-only gate
(`library/tools/render_qa.py`), so the acceptance report and the
delivery report grade a render by the same rules. What this module adds
is the part the acceptance run needs and the gate does not have: the
DIGEST that ties a score to one exact file, the FRAMES a judge is shown,
and the list of checks that could not run.

A check nobody declared is not a check that passed. Resolution, frame
rate and duration are graded against what the project declared, and
when nothing was declared they are named in `technical_not_checked`
rather than passed. The measurements that need no declaration - the
audio stream, the loudness, the black and frozen frames, the silence
under picture - are always run.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "ren-editorial-delivery-evidence/1"

#: How many frames a judge is shown by default. Three is enough to see a
#: reel's opening, its middle and its end without turning the evidence
#: into a contact sheet nobody reads.
DEFAULT_FRAME_COUNT = 3


class DeliveryError(RuntimeError):
    """The delivered file could not be read, or is not there."""


def sha256_of(path: str | Path) -> str:
    """The digest of the delivered file, so a score names one exact file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(argv: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          check=False)


def probe(media_path: str | Path) -> dict[str, Any]:
    """What ffprobe says about the delivered file."""
    target = Path(media_path)
    if not target.is_file():
        raise DeliveryError(f"no delivered file at {target}")
    result = _run(["ffprobe", "-v", "quiet", "-show_streams", "-show_format",
                    "-of", "json", str(target)], timeout=60)
    if result.returncode:
        raise DeliveryError(
            f"ffprobe could not read {target}: {result.stderr[-400:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeliveryError(
            f"ffprobe returned no readable probe for {target}") from exc


def duration_seconds(media_path: str | Path) -> float | None:
    """The delivered file's own duration, read off the file."""
    data = probe(media_path)
    raw = (data.get("format") or {}).get("duration")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def video_geometry(media_path: str | Path) -> tuple[int, int] | None:
    """The delivered file's own frame, read off the file."""
    data = probe(media_path)
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            width = stream.get("width")
            height = stream.get("height")
            if width and height:
                return int(width), int(height)
    return None


def extract_frames(media_path: str | Path, workdir: str | Path, *,
                   count: int = DEFAULT_FRAME_COUNT) -> list[dict[str, Any]]:
    """Stills from the delivered file, for the judge to look at.

    The frames come out of the FILE, at the file's own times, so what a
    judge sees is what a viewer would see.
    """
    target = Path(media_path)
    out_dir = Path(workdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = duration_seconds(target)
    if total is None or total <= 0:
        return []
    wanted = max(1, int(count))
    frames: list[dict[str, Any]] = []
    for index in range(wanted):
        at = (total * (index + 0.5) / wanted) if wanted > 1 else 0.0
        at = min(max(at, 0.0), max(total - 0.05, 0.0))
        out_path = out_dir / f"frame-{index:02d}.png"
        result = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.3f}",
                       "-i", str(target), "-frames:v", "1", str(out_path)],
                      timeout=120)
        if result.returncode or not out_path.exists():
            continue
        frames.append({"kind": "frame", "artifact": str(out_path),
                       "at_seconds": round(at, 3),
                       "observation": (
                           f"frame at {at:.2f}s of the delivered file")})
    return frames


def technical_checks(media_path: str | Path, *,
                     expected_resolution: tuple[int, int] | None = None,
                     expected_fps: float | None = None,
                     expected_duration: float | None = None,
                     declared_black_spans: list | None = None,
                     target_lufs: float | None = None) -> dict[str, Any]:
    """The file-only checks, and the ones that could not be run.

    Returns `{"checks": {name: bool}, "not_checked": [{"name", "reason"}],
    "details": {name: str}}`. The checks dict holds only the checks that
    RAN, so a caller that reads it as a verdict reads a truth.
    """
    from library.tools import render_qa

    target = Path(media_path)
    if not target.is_file():
        raise DeliveryError(f"no delivered file at {target}")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return {
            "checks": {},
            "not_checked": [{"name": "file_only_gate",
                             "reason": "ffmpeg/ffprobe are not on PATH"}],
            "details": {},
        }
    checks: dict[str, bool] = {}
    not_checked: list[dict[str, str]] = []
    details: dict[str, str] = {}

    def record(result: Any) -> None:
        checks[result.metric] = bool(result.passed)
        details[result.metric] = str(result.detail)

    if expected_resolution is not None:
        record(render_qa.verify_resolution(
            str(target), expected_width=int(expected_resolution[0]),
            expected_height=int(expected_resolution[1])))
    else:
        not_checked.append({
            "name": "resolution",
            "reason": "no delivery frame was declared, so the render was "
                      "not graded against one"})
    if expected_fps is not None:
        record(render_qa.verify_framerate(str(target), float(expected_fps)))
    else:
        not_checked.append({
            "name": "framerate",
            "reason": "no frame rate was declared, so the render was not "
                      "graded against one"})
    if expected_duration is not None:
        record(render_qa.verify_duration(str(target),
                                         float(expected_duration)))
    else:
        not_checked.append({
            "name": "duration",
            "reason": "no planned duration was declared, so the render was "
                      "not graded against one"})
    record(render_qa.verify_audio_streams(str(target)))
    if target_lufs is None:
        record(render_qa.measure_lufs(str(target)))
    else:
        record(render_qa.measure_lufs(str(target), target_lufs=float(target_lufs)))
    record(render_qa.detect_black_frames(
        str(target), declared_beats=declared_black_spans))
    record(render_qa.detect_freeze_frames(str(target)))
    record(render_qa.measure_silence_under_picture(str(target)))
    return {"checks": checks, "not_checked": not_checked, "details": details}


def build_render_evidence(media_path: str | Path, *,
                           workdir: str | Path | None = None,
                           frame_count: int = DEFAULT_FRAME_COUNT,
                           transcript: list[dict[str, Any]] | None = None,
                           **checks: Any) -> dict[str, Any]:
    """The `render` a judge is given: the digest, the evidence, the checks.

    The digest is what ties a score to one exact file: a score without
    it describes no particular deliverable, and two runs of the same
    brief would be indistinguishable.

    The frames stay on disk, because a judge has to be able to LOOK at
    them. A directory made here is temporary and is named as such, so
    the caller can remove it with ``cleanup_evidence`` once the judge
    has answered.
    """
    target = Path(media_path)
    if not target.is_file():
        raise DeliveryError(f"no delivered file at {target}")
    owned_dir = workdir is None
    directory = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix="ren-editorial-delivery-"))
    technical = technical_checks(target, **checks)
    evidence = extract_frames(target, directory, count=frame_count)
    audio = _audio_observation(target)
    if audio:
        evidence.append(audio)
    for item in (transcript or []):
        evidence.append(dict(item))
    if not evidence:
        raise DeliveryError(
            f"the delivered file at {target} produced no evidence a "
            "judge could look at")
    return {
        "schema": SCHEMA,
        "sha256": sha256_of(target),
        "path": str(target),
        "bytes": target.stat().st_size,
        "duration_seconds": duration_seconds(target),
        "evidence": evidence,
        "technical_verification": technical["checks"],
        "technical_not_checked": technical["not_checked"],
        "technical_details": technical["details"],
        "evidence_directory": str(directory),
        "evidence_directory_is_temporary": owned_dir,
    }


def cleanup_evidence(render: Mapping[str, Any]) -> bool:
    """Remove a temporary evidence directory; True when it is gone.

    Only a directory this module made is removed, and only one that is
    still the one the evidence names.
    """
    if not render.get("evidence_directory_is_temporary"):
        return False
    directory = Path(str(render.get("evidence_directory") or ""))
    if not directory.is_dir():
        return True
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()


def _audio_observation(media_path: str | Path) -> dict[str, Any] | None:
    """What the delivered file sounds like, measured."""
    from library.tools import render_qa

    if shutil.which("ffmpeg") is None:
        return None
    measured = render_qa.measure_lufs(str(media_path))
    value = measured.value
    if isinstance(value, dict):
        observation = (f"integrated {float(value.get('input_i', 0.0)):.2f} "
                       f"LUFS, true peak {float(value.get('input_tp', 0.0)):+.2f} "
                       "dBTP")
    else:
        observation = str(measured.detail)
    return {"kind": "audio", "artifact": str(media_path),
            "observation": observation}
