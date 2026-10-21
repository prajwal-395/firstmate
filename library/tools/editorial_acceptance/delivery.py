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
DIGEST that ties a score to one exact file, the evidence a judge is
shown, and the list of checks that could not run.

A check nobody declared is not a check that passed. Resolution, frame
rate and duration are graded against what the project declared, and
when nothing was declared they are named in `technical_not_checked`
rather than passed. The measurements that need no declaration - the
audio stream, the loudness, the black and frozen frames, the silence
under picture - are always run.

The harness reads the SAME declaration vocabulary the product's own gate
reads. A reel that ends on a silent end card is excused by
`render_qa.declared_ending_spans` and by step 6.02's bridge, and this
harness read that ending as undeclared silence - so the same file was
condemned by one gate and cleared by the other. `declared_silence_spans`
is that vocabulary, arriving here by the same road.

The packet a judge is shown, and why it is not three stills
------------------------------------------------------------
The demo's own packet was three stills, a digest and a loudness
observation, and four of the brief's ten criteria came back
`not_measured` because of it: three stills cannot show a pause, cannot
show which camera a turn was shot on, and cannot show an inserted
cutaway. The scorer refuses a verdict that leaves a criterion
unmeasured, so a packet that thin makes the milestone unreachable no
matter how good the file is.

So the packet carries three more things, all measured off the FILE and
none of them off the plan:

- the file's own WORDS, timed (Voz for the text, MFA for the word
  boundaries - never whisper, which left the repository with its pin),
- the gaps between those words, so a pause is a measurement rather than
  an impression,
- the file's own SHOT BOUNDARIES, and a still at each of them.

The plan is deliberately not read. A judge that is shown what the file
was supposed to say is not judging the file (`judgement.judge_input`
enforces the same boundary on the brief's half).

What it costs
-------------
MEASURED on this machine, on this project's own delivered reel (79.5s,
1080x1920): the file-only checks 45s, the transcriber 8s, the forced
aligner 43s, 137s and 231s on three runs of the same fifteen windows
(the cost is per WINDOW, not per second), the two scene passes 8s, and
the 45 stills 5s. So a packet costs between one and five minutes, and
the aligner is most of it - which is why the aligner is asked for by
name (`align_transcript`), is bounded by `MAX_ALIGNED_SECONDS`, and can
be turned off for a run that wants the cheaper timing.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - the type only
    from library.tools.delivery_spec import DeliverySpec

SCHEMA = "ren-editorial-delivery-evidence/1"

#: How often the packet draws a still along the delivered file, in
#: seconds. A still every two seconds is what makes a short cutaway, a
#: camera turn and a caption card visible to a judge; three stills of a
#: reel cannot show any of the three.
FRAME_CADENCE_SECONDS = 2.0

#: How many stills the packet may carry. A packet is read by a judge
#: and by a model, and a contact sheet nobody reads is not evidence.
MAX_EVIDENCE_FRAMES = 48

#: The shortest gap between two spoken words that counts as a pause.
#: Below this the gap is a breath, not an edit that was or was not made.
PAUSE_MIN_SECONDS = 0.5

#: How much of the timed transcript is inlined into the packet. The
#: whole transcript is always written to disk; this bounds what a judge
#: is handed in one string.
MAX_TRANSCRIPT_CHARS = 12000

#: The longest file the forced aligner is asked to time, in seconds.
#:
#: MEASURED on this machine, on a delivered 79.5s reel: the aligner
#: took 43s, 137s and 231s on three runs of the same fifteen windows,
#: and the cost is per WINDOW rather than per second - fifteen windows
#: cost what a minute of audio costs. A file long enough to carry a
#: hundred windows would cost half an hour of a judge's wait, and the
#: precision it buys - 26ms instead of 94ms on a word boundary - is not
#: what any of the criteria turn on. So the budget is a length, and a
#: file over it is timed by the transcriber's own boundaries, with the
#: reason recorded in the packet.
MAX_ALIGNED_SECONDS = 300.0


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


def extract_frames_at(media_path: str | Path, workdir: str | Path,
                      times: list[float]) -> list[dict[str, Any]]:
    """Stills from the delivered file at the times asked for.

    The frames come out of the FILE, at the file's own times, so what a
    judge sees is what a viewer would see.
    """
    target = Path(media_path)
    out_dir = Path(workdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for index, at in enumerate(times):
        out_path = out_dir / f"frame-{index:03d}.png"
        result = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.3f}",
                       "-i", str(target), "-frames:v", "1", str(out_path)],
                      timeout=120)
        if result.returncode or not out_path.exists():
            continue
        frames.append({"kind": "frame", "artifact": str(out_path),
                       "at_seconds": round(at, 3),
                       "observation": (
                           f"still at {at:.2f}s of the delivered file")})
    return frames


def extract_frames(media_path: str | Path, workdir: str | Path, *,
                   count: int) -> list[dict[str, Any]]:
    """Stills evenly spread along the delivered file.

    Kept for a caller that asks for a number of stills and nothing
    else. The packet itself uses the cadence rule below.
    """
    total = duration_seconds(media_path)
    if total is None or total <= 0:
        return []
    wanted = max(1, int(count))
    times = []
    for index in range(wanted):
        at = (total * (index + 0.5) / wanted) if wanted > 1 else 0.0
        times.append(min(max(at, 0.0), max(total - 0.05, 0.0)))
    return extract_frames_at(media_path, workdir, times)


#: The scene score a frame has to beat to be called a cut, and the
#: lower score at which a SOFT change is called one.
#:
#: MEASURED on this project's own delivered reel (79.5s, two cameras,
#: burned-in captions): the camera changes at 37.33s score between
#: 0.10 and 0.30 - it is a dissolve, and the shared threshold does not
#: see it - while the burned-in caption changing at 3.13s scores above
#: 0.10 on its own. So the lower threshold over-reports, the higher one
#: under-reports, and NEITHER is the instrument for "did the camera
#: change": the stills in the packet are. The cut list is reported at
#: both thresholds so a judge sees the soft change and does not have to
#: guess at it.
SOFT_SHOT_SCENE_THRESHOLD = 0.1


def measure_shots(media_path: str | Path, duration: float) -> dict[str, Any]:
    """The delivered file's own shot boundaries, measured off the file.

    `ffmpeg`'s scene metric, through the engine's own detector
    (`library/tools/real_footage_evidence.detect_scenes`), so the number
    here is the same measurement the picture analysis reads and not a
    second opinion about the same thing. It is read at the shared
    threshold and at a lower one, because a dissolve is a change of
    camera that only the lower threshold sees.

    A file whose boundaries could not be measured says so. It does not
    report "no cuts", which is a different claim.
    """
    from library.tools import real_footage_evidence

    target = Path(media_path)
    if duration <= 0:
        return {"shots": [], "soft_shots": [], "measured": False,
                "reason": "the delivered file has no measurable length"}
    try:
        hard = real_footage_evidence.detect_scenes(
            str(target), 0.0, float(duration))
        soft = real_footage_evidence.detect_scenes(
            str(target), 0.0, float(duration),
            threshold=SOFT_SHOT_SCENE_THRESHOLD)
    except Exception as failure:  # noqa: BLE001 - the reason is reported
        return {"shots": [], "soft_shots": [], "measured": False,
                "reason": f"the shot boundaries could not be measured: {failure}"}
    return {
        "shots": _shot_rows(hard),
        "soft_shots": _shot_rows(soft),
        "measured": True,
        "reason": None,
    }


def _shot_rows(boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"at_seconds": round(float(row["tick_s"]), 3),
             "score": round(float(row.get("score") or 0.0), 3)}
            for row in boundaries]


def evidence_frame_times(duration: float,
                         shots: list[dict[str, Any]] | None = None
                         ) -> list[float]:
    """When the packet draws a still.

    A still every `FRAME_CADENCE_SECONDS` of the file, plus one at
    every measured shot boundary - the places where the picture is
    different from the still before it, which is exactly where a camera
    turn or an inserted cutaway lives.

    A file with more boundaries than the packet may carry is sampled
    evenly rather than truncated: a judge that sees the first forty
    cuts of a hundred reads the wrong thing about the last sixty.
    """
    if duration <= 0:
        return []
    times: list[float] = []
    step = FRAME_CADENCE_SECONDS
    at = step / 2
    while at < duration:
        times.append(round(at, 3))
        at += step
    for shot in shots or []:
        moment = float(shot.get("at_seconds") or 0.0)
        if 0.0 < moment < duration:
            times.append(round(moment, 3))
    unique = sorted(set(times))
    if len(unique) > MAX_EVIDENCE_FRAMES:
        step = len(unique) / float(MAX_EVIDENCE_FRAMES)
        unique = [unique[min(len(unique) - 1, int(index * step))]
                  for index in range(MAX_EVIDENCE_FRAMES)]
        unique = sorted(set(unique))
    return unique


def _word_rows(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The words, in the file's own timebase, and in order."""
    rows = []
    for word in words:
        try:
            start = float(word.get("start"))
            end = float(word.get("end"))
        except (TypeError, ValueError):
            continue
        text = str(word.get("word") or "").strip()
        if not text:
            continue
        rows.append({"word": text, "start": round(start, 3),
                     "end": round(end, 3)})
    rows.sort(key=lambda row: (row["start"], row["end"]))
    return rows


def _lines_from_words(words: list[dict[str, Any]],
                      sentences: list[dict[str, Any]] | None
                      ) -> list[dict[str, Any]]:
    """The timed text, in lines a reader can follow.

    The transcriber's own sentences are used when it published them;
    otherwise the words are grouped at the gaps, because a line that
    runs across a long silence is not a line.
    """
    lines: list[dict[str, Any]] = []
    for sentence in sentences or []:
        text = str(sentence.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(sentence.get("start"))
            end = float(sentence.get("end"))
        except (TypeError, ValueError):
            continue
        lines.append({"start": round(start, 3), "end": round(end, 3),
                      "text": text})
    if lines:
        return lines
    group: list[dict[str, Any]] = []
    for word in words:
        if group and word["start"] - group[-1]["end"] > PAUSE_MIN_SECONDS:
            lines.append(_line_of(group))
            group = []
        group.append(word)
    if group:
        lines.append(_line_of(group))
    return lines


def _line_of(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {"start": round(words[0]["start"], 3),
            "end": round(words[-1]["end"], 3),
            "text": " ".join(word["word"] for word in words)}


def pause_spans(words: list[dict[str, Any]],
                minimum: float = PAUSE_MIN_SECONDS) -> list[dict[str, Any]]:
    """The gaps between the spoken words, measured off the file.

    A pause is a gap in the speech, and this is where a judge looks to
    find out whether one was removed: the file's own words, and the
    silence between them.
    """
    spans = []
    for before, after in itertools.pairwise(words):
        gap = round(after["start"] - before["end"], 3)
        if gap >= minimum:
            spans.append({"at_seconds": round(after["start"], 3),
                          "seconds": gap})
    return spans


def _transcriber_transcript(media_path: str | Path) -> dict[str, Any]:
    """The file's own words, timed by the transcriber alone.

    This is the half that always works when the on-device transcriber
    is installed, and the half every other hearing in this repository
    starts with.
    """
    from library.tools import heard_speech

    spoken = heard_speech.transcribe(str(media_path))
    words = _word_rows([{"word": w.word, "start": w.start, "end": w.end}
                         for w in spoken.words])
    sentences = [{"text": s.text, "start": s.start, "end": s.end}
                 for s in spoken.sentences]
    return {
        "measured": True,
        "reason": None,
        "aligner": "transcriber",
        "engine": dict(spoken.engine),
        "words": words,
        "lines": _lines_from_words(words, sentences),
        "anomalies": list(spoken.anomalies),
    }


def _mfa_transcript(media_path: str | Path) -> dict[str, Any]:
    """The file's own words, timed by the forced aligner.

    The engine's own hybrid: the on-device transcriber writes the text
    and the aligner places the word boundaries. Whisper is not in this
    path - it left the repository with its pin - so a packet built on
    it would be a packet this machine cannot rebuild.
    """
    from library.tools import hybrid_transcription, mfa_align, timeline_transcript

    total = duration_seconds(media_path)
    if total is None or total <= 0:
        raise DeliveryError("the delivered file has no measurable length")
    if total > MAX_ALIGNED_SECONDS:
        raise DeliveryError(
            f"the delivered file is {total:.1f}s long, over the "
            f"{MAX_ALIGNED_SECONDS:.0f}s alignment budget, so the "
            f"transcriber's own boundaries are used")
    with tempfile.TemporaryDirectory(prefix="ren-delivery-audio-") as scratch:
        wav = timeline_transcript.extract_span(
            str(media_path), 0.0, float(total), Path(scratch))
        aligner = hybrid_transcription.Aligner(
            covers=mfa_align.covers, align=mfa_align.align)
        hybrid = hybrid_transcription.transcribe_and_align(
            str(wav), aligner, label=Path(media_path).name)
    segments = hybrid.aligned.get("segments") or []
    words: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    for segment in segments:
        for word in segment.get("words") or []:
            words.append({"word": word.get("word"),
                          "start": word.get("start"),
                          "end": word.get("end")})
        text = str(segment.get("text") or "").strip()
        if text:
            lines.append({"start": segment.get("start"),
                          "end": segment.get("end"), "text": text})
    rows = _word_rows(words)
    return {
        "measured": True,
        "reason": None,
        "aligner": "mfa",
        "engine": dict(hybrid.record.get("engine") or {}),
        "words": rows,
        "lines": _lines_from_words(rows, lines),
        "anomalies": [],
    }


def transcribe_delivered_file(media_path: str | Path, *,
                               align: bool = True) -> dict[str, Any]:
    """What the delivered file SAYS, timed.

    Two halves, in the order the engine runs them everywhere else: the
    on-device transcriber writes the words, the forced aligner places
    their boundaries. When the aligner cannot run - no model for the
    language, no MFA on the machine - the transcriber's own timings are
    used and the packet says which of the two placed them.

    The plan is not read here. This is what the FILE says, and a judge
    that is shown what the file was supposed to say is not judging the
    file.
    """
    target = Path(media_path)
    if not target.is_file():
        raise DeliveryError(f"no delivered file at {target}")
    reason = None
    if align:
        try:
            return _mfa_transcript(target)
        except Exception as failure:  # noqa: BLE001 - the reason is kept
            reason = (f"the forced aligner did not place this file's "
                      f"words, so the transcriber's own timings are used: "
                      f"{type(failure).__name__}: {failure}")
    else:
        reason = "the aligner was not asked for"
    try:
        transcript = _transcriber_transcript(target)
    except Exception as failure:  # noqa: BLE001 - the reason is reported
        return {"measured": False,
                "reason": (f"the delivered file could not be transcribed: "
                           f"{type(failure).__name__}: {failure}"),
                "aligner": None, "engine": {}, "words": [], "lines": [],
                "anomalies": [], "aligner_reason": reason}
    transcript["aligner_reason"] = reason
    return transcript


def transcript_evidence(media_path: str | Path, workdir: str | Path,
                        transcript: Mapping[str, Any]) -> dict[str, Any]:
    """The packet's transcript item: the file's own words, timed.

    The timed text is written to disk as well as inlined, so a judge
    that wants the whole of it has somewhere to read it from.
    """
    target = Path(media_path)
    lines = list(transcript.get("lines") or [])
    words = list(transcript.get("words") or [])
    body = "\n".join(
        f"{line['start']:8.2f} - {line['end']:8.2f}  {line['text']}"
        for line in lines)
    path = Path(workdir) / "transcript.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"delivered file: {target}\n"
        f"words: {len(words)}\n"
        f"aligned by: {transcript.get('aligner')}\n"
        f"{'-' * 60}\n{body}\n",
        encoding="utf-8")
    inlined = body if len(body) <= MAX_TRANSCRIPT_CHARS else (
        body[:MAX_TRANSCRIPT_CHARS]
        + f"\n... the rest of the transcript is in {path}")
    return {
        "kind": "transcript",
        "artifact": str(path),
        "observation": (
            f"what the delivered file says, {len(words)} words timed by "
            f"{transcript.get('aligner')}; the whole transcript is in "
            f"{path}\n{inlined}"),
    }


def pause_evidence(media_path: str | Path, workdir: str | Path,
                    transcript: Mapping[str, Any]) -> dict[str, Any] | None:
    """The gaps between the file's spoken words, measured."""
    words = list(transcript.get("words") or [])
    if not words:
        return None
    spans = pause_spans(words)
    if not spans:
        observation = (
            f"the delivered file's {len(words)} spoken words leave no gap "
            f"of {PAUSE_MIN_SECONDS:.2f}s or more between them")
    else:
        listed = ", ".join(
            f"{span['at_seconds']:.2f}s ({span['seconds']:.2f}s)"
            for span in spans[:60])
        more = ("" if len(spans) <= 60
                else f", and {len(spans) - 60} more")
        observation = (
            f"the delivered file's {len(words)} spoken words leave "
            f"{len(spans)} gap(s) of {PAUSE_MIN_SECONDS:.2f}s or more "
            f"between them: {listed}{more}")
    return {"kind": "pause", "artifact": str(media_path),
            "observation": observation}


def shot_evidence(media_path: str | Path, measured: Mapping[str, Any]
                   ) -> dict[str, Any]:
    """The file's own cut structure, measured off the file."""
    from library.tools import real_footage_evidence

    target = Path(media_path)
    if not measured.get("measured"):
        return {"kind": "shot", "artifact": str(target),
                "observation": (
                    f"the delivered file's shot boundaries could not be "
                    f"measured: {measured.get('reason')}")}
    shots = list(measured.get("shots") or [])
    soft = list(measured.get("soft_shots") or [])
    parts = []
    if shots:
        listed = ", ".join(
            f"{shot['at_seconds']:.2f}s" for shot in shots[:60])
        more = ("" if len(shots) <= 60 else f", and {len(shots) - 60} more")
        parts.append(
            f"{len(shots)} cut(s) measured by the scene metric at "
            f"{real_footage_evidence.SCENE_SCORE_THRESHOLD:.2f}, at "
            f"{listed}{more}")
    else:
        parts.append(
            "no cut was measured by the scene metric at "
            f"{real_footage_evidence.SCENE_SCORE_THRESHOLD:.2f}")
    only_soft = [shot for shot in soft
                 if not any(abs(shot["at_seconds"] - other["at_seconds"])
                            < 0.25 for other in shots)]
    if only_soft:
        listed = ", ".join(
            f"{shot['at_seconds']:.2f}s" for shot in only_soft[:60])
        parts.append(
            f"and {len(only_soft)} softer change(s) at "
            f"{SOFT_SHOT_SCENE_THRESHOLD:.2f}, at {listed} - a dissolve "
            f"between two cameras scores under the higher threshold, and "
            f"a caption changing on its own scores over the lower one, so "
            f"read the stills at those times to see which it was")
    return {"kind": "shot", "artifact": str(target),
            "observation": "; ".join(parts)}


def technical_checks(media_path: str | Path, *,
                     expected_resolution: tuple[int, int] | None = None,
                     expected_fps: float | None = None,
                     expected_duration: float | None = None,
                     declared_black_spans: list | None = None,
                     declared_silence_spans: list | None = None,
                     target_lufs: float | None = None,
                     lufs_tolerance: float | None = None,
                     true_peak_ceiling: float | None = None) -> dict[str, Any]:
    """The file-only checks, and the ones that could not be run.

    Returns `{"checks": {name: bool}, "not_checked": [{"name", "reason"}],
    "details": {name: str}}`. The checks dict holds only the checks that
    RAN, so a caller that reads it as a verdict reads a truth.

    `declared_silence_spans` are the `(start, end)` seconds of the
    delivered file where silence under picture is DELIBERATE - the
    silent end card, the TV-switch-on tail. They come from
    `render_qa.declared_ending_spans`, which is where the engine reads
    them from for its own gate; a harness that held its own idea of a
    declared ending would grade the same file two ways.
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
    loudness: dict[str, float] = {}
    if target_lufs is not None:
        loudness["target_lufs"] = float(target_lufs)
    if lufs_tolerance is not None:
        loudness["tolerance"] = float(lufs_tolerance)
    if true_peak_ceiling is not None:
        loudness["true_peak_ceiling"] = float(true_peak_ceiling)
    record(render_qa.measure_lufs(str(target), **loudness))
    record(render_qa.detect_black_frames(
        str(target), declared_beats=declared_black_spans))
    record(render_qa.detect_freeze_frames(str(target)))
    record(render_qa.measure_silence_under_picture(
        str(target), declared_spans=declared_silence_spans))
    return {"checks": checks, "not_checked": not_checked, "details": details}


def declared_ending_spans(assembly_manifest: Mapping[str, Any] | None
                           ) -> dict[str, list]:
    """The ending the manifest will draw, in the harness's vocabulary.

    A thin pass-through to `render_qa.declared_ending_spans` on purpose:
    the harness and the product's own gate must read ONE declaration, or
    a reel that ends on a silent end card is excused by one and
    condemned by the other. Returns `{"black": [...], "silence": [...]}`
    - `(start, end)` seconds on the delivered file's clock.
    """
    from library.tools import render_qa

    return render_qa.declared_ending_spans(assembly_manifest)


def build_delivery_section(media_path: str | Path, *,
                            spec: "DeliverySpec | None" = None,
                            timeline_fps: float | None = None,
                            timeline_frames: int | None = None,
                            expected_duration: float | None = None,
                            declared_black_spans: list | None = None,
                            declared_silence_spans: list | None = None,
                            assembly_manifest: Mapping[str, Any] | None = None,
                            ) -> dict[str, Any]:
    """The record's `delivery` section, produced from the FILE.

    The section used to be typed by hand into a run's steps file, which
    is how a delivery report came to carry three checks nobody ran and
    a verdict nobody measured. This is the producer: a rendered file, the
    delivery spec it is delivered against, and the timeline's own clock
    in, and the record's own `delivery` section out - the digest, the
    length, the technical checks that ran and the ones that could not.

    `spec` is a `library.tools.delivery_spec.DeliverySpec`: it declares
    the delivered frame and the loudness target. `timeline_fps` and
    `timeline_frames` are the timeline's own clock: the frame rate is
    the timeline's MEASURED rate, not the spec's recommendation, because
    a master is rendered at the rate the edit was made at.

    Nothing here declares an ending. A silent end card is the
    captain's call about his own reel, so the spans are passed in - from
    `declared_ending_spans` or from the caller - and never invented.
    """
    target = Path(media_path)
    if not target.is_file():
        raise DeliveryError(f"no delivered file at {target}")

    black = list(declared_black_spans or [])
    silence = list(declared_silence_spans or [])
    if assembly_manifest is not None:
        spans = declared_ending_spans(assembly_manifest)
        black += list(spans.get("black") or [])
        silence += list(spans.get("silence") or [])

    duration = expected_duration
    if duration is None and timeline_frames is not None and timeline_fps:
        duration = float(timeline_frames) / float(timeline_fps)

    resolution = None
    target_lufs = None
    tolerance = None
    ceiling = None
    if spec is not None:
        resolution = (int(spec.width), int(spec.height))
        target_lufs = float(spec.loudness_target_lufs)
        tolerance = float(spec.loudness_tolerance_lufs)
        ceiling = float(spec.true_peak_ceiling_dbtp)

    technical = technical_checks(
        target,
        expected_resolution=resolution,
        expected_fps=timeline_fps,
        expected_duration=duration,
        declared_black_spans=black or None,
        declared_silence_spans=silence or None,
        target_lufs=target_lufs,
        lufs_tolerance=tolerance,
        true_peak_ceiling=ceiling,
    )
    return {
        "path": str(target),
        "sha256": sha256_of(target),
        "duration_seconds": duration_seconds(target),
        "bytes": target.stat().st_size,
        "technical_verification": technical["checks"],
        "technical_not_checked": technical["not_checked"],
        "technical_details": technical["details"],
    }


def build_render_evidence(media_path: str | Path, *,
                           workdir: str | Path | None = None,
                           frame_count: int | None = None,
                           transcript: list[dict[str, Any]] | None = None,
                           align_transcript: bool = True,
                           **checks: Any) -> dict[str, Any]:
    """The `render` a judge is given: the digest, the evidence, the checks.

    The digest is what ties a score to one exact file: a score without
    it describes no particular deliverable, and two runs of the same
    brief would be indistinguishable.

    The packet carries the file's own WORDS, the gaps between them and
    the file's own cut structure, and a still every
    `FRAME_CADENCE_SECONDS` of the file plus one at every measured cut.
    Three stills and no transcript made four of the brief's criteria
    unmeasurable, and a criterion nobody could measure is a milestone
    nobody can reach.

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
    total = duration_seconds(target)
    shots = measure_shots(target, total or 0.0)
    times = (evidence_frame_times(total or 0.0,
                                  list(shots.get("shots") or [])
                                  + list(shots.get("soft_shots") or []))
             if frame_count is None
             else [round((total or 0.0) * (index + 0.5) / max(1, frame_count),
                         3) for index in range(max(1, frame_count))])
    evidence: list[dict[str, Any]] = []
    heard = transcribe_delivered_file(target, align=align_transcript)
    if heard.get("measured"):
        evidence.append(transcript_evidence(target, directory, heard))
        gaps = pause_evidence(target, directory, heard)
        if gaps is not None:
            evidence.append(gaps)
    else:
        evidence.append({
            "kind": "transcript_unavailable",
            "artifact": str(target),
            "observation": str(heard.get("reason"))})
    evidence.append(shot_evidence(target, shots))
    evidence.extend(extract_frames_at(target, directory, times))
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
        "duration_seconds": total,
        "evidence": evidence,
        "technical_verification": technical["checks"],
        "technical_not_checked": technical["not_checked"],
        "technical_details": technical["details"],
        "transcript": {
            "measured": bool(heard.get("measured")),
            "reason": heard.get("reason"),
            "aligner": heard.get("aligner"),
            "aligner_reason": heard.get("aligner_reason"),
            "word_count": len(heard.get("words") or []),
        },
        "shots": {
            "measured": bool(shots.get("measured")),
            "reason": shots.get("reason"),
            "count": len(shots.get("shots") or []),
            "soft_count": len(shots.get("soft_shots") or []),
        },
        "frame_count": sum(1 for item in evidence
                           if item.get("kind") == "frame"),
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
