"""B-roll-to-speech correspondence check (gap E3).

B-roll placed over A-roll speech should ILLUSTRATE what is said - the
cutaway's visual content should match the spoken words at that moment.
No verification step checks this today: `reel_hearing` checks audio only,
`render_watch` checks picture only, and the connection between what is
heard and what is seen at any moment is not measured.

This module measures that connection. For each placed cutaway (V2 clip
in the assembly manifest), it:
1. Takes the spoken words at the cutaway's timeline span (from the plan's
   own transcript, via `reel_hearing.planned_words`)
2. Draws frames of the cutaway's source window (via `window_frames`)
3. Asks a VLM whether the frames depict the spoken words' referents
4. Reports per-cutaway pass/fail, with the VLM verdict cached by frame hash

**This reports and does not fail** (`BROLL_CORRESPONDENCE_GATES` is False).
Whether a non-illustrating cutaway blocks delivery is a pending captain
call, the same ruling every report-only check in this pipeline carries.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from library.tools import reel_hearing, window_frames
from library.tools.render_qa import RenderQAResult

BROLL_CORRESPONDENCE_GATES = False
"""Whether a non-illustrating cutaway fails the render.

The check measures and reports; it does not fail. Promoting it is this
boolean, and it is a captain's call because whether a non-illustrating
cutaway is a delivery-blocking defect is a creative judgement, not a
technical one - the same ruling that keeps `punch_in_face` and
`caption_obscuring` report-only.
"""

METRIC = "broll_correspondence"

PROMPT_VERSION = 1

PROMPT = (
    "These are {n} frames of a video cutaway, in time order. "
    "The spoken words during this cutaway are: \"{words}\". "
    "Do these frames visually depict what those words refer to? "
    "Judge only what is visible in the frames. "
    "Reply with JSON only: "
    '{{\"illustrates\": \"yes\" or \"no\", '
    '"reason\": \"<one short sentence>\"}}'
)

MAX_TOKENS = 120

VERIFY_LOCK_OWNER = "broll-correspondence"

SAMPLE_FPS = 1.0


@dataclass
class Cutaway:
    """One placed B-roll cutaway from the assembly manifest."""

    clip_name: str
    source_file: str
    source_in: float
    source_out: float
    timeline_in: float
    timeline_out: float


def placed_cutaways(assembly_manifest: dict) -> List[Cutaway]:
    """Every V2 clip in the manifest that is a placed cutaway.

    V2 clips are B-roll. Bookend cards on V2 are not cutaways.
    """
    cutaways = []
    tracks = assembly_manifest.get("tracks", {})
    for clip in (tracks.get("V2", {}) or {}).get("clips", []) or []:
        if clip.get("bookend"):
            continue
        source_file = clip.get("source_file", "")
        if not source_file:
            continue
        cutaways.append(Cutaway(
            clip_name=clip.get("clip_name", clip.get("label", "")),
            source_file=source_file,
            source_in=float(clip.get("source_in", 0)),
            source_out=float(clip.get("source_out", 0)),
            timeline_in=float(clip.get("timeline_in_frame", 0)),
            timeline_out=float(clip.get("timeline_out_frame", 0)),
        ))
    return cutaways


def words_at_span(words: Sequence[reel_hearing.Word], start: float,
                  end: float) -> str:
    """The spoken words overlapping a timeline span, as a string."""
    overlapping = []
    for word in words:
        if word.end >= start and word.start <= end:
            overlapping.append(word.word)
    return " ".join(overlapping)


def _verdict_key(words: str, frame_times: Sequence[float],
                 model_id: str) -> str:
    """Cache key for a VLM verdict."""
    raw = json.dumps([PROMPT_VERSION, model_id, words.strip().lower(),
                      [round(t, 3) for t in frame_times]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse_verdict(text: str) -> dict:
    """Parse the VLM's reply into {illustrates, reason}."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        body = json.loads(match.group(0)) if match else None
    except ValueError:
        body = None
    answer = str((body or {}).get("illustrates", "")).strip().lower()
    if not isinstance(body, dict) or answer not in ("yes", "no"):
        return {"illustrates": None, "reason": None}
    return {"illustrates": answer == "yes", "reason": body.get("reason")}


def _cache_path(project_folder: str) -> str:
    """Where VLM verdicts are cached."""
    from library.tools.project_layout import Area, ProjectLayout
    return str(ProjectLayout(project_folder).write_path(
        Area.SCRATCH, "broll_correspondence_verdicts.json"))


def _read_cache(project_folder: str) -> dict:
    """Read cached verdicts."""
    if not project_folder:
        return {}
    path = _cache_path(project_folder)
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    return doc.get("verdicts") or {}


def _write_cache(project_folder: str, verdicts: dict) -> None:
    """Write cached verdicts."""
    if not project_folder:
        return
    path = _cache_path(project_folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"verdicts": verdicts}, handle, indent=2)


def measure_broll_correspondence(
        video_path: str,
        assembly_manifest: dict,
        timeline: Dict[str, Any],
        transcript: Dict[str, Any],
        model=None,
        project_folder: str = "",
        gate: bool = BROLL_CORRESPONDENCE_GATES) -> RenderQAResult:
    """Measure B-roll-to-speech correspondence for each placed cutaway.

    For each V2 cutaway in the manifest, takes the spoken words at its
    timeline span, draws frames of its source window, and asks a VLM
    whether the frames depict the spoken words' referents.

    **This reports and does not fail** (`BROLL_CORRESPONDENCE_GATES` is
    False). Whether a non-illustrating cutaway blocks delivery is a
    pending captain call.

    `model` is the VLM to call. None uses the default
    (`vision_model.get_model()`). Tests pass a stub.
    """
    cutaways = placed_cutaways(assembly_manifest)
    if not cutaways:
        return RenderQAResult(
            METRIC, True, {"cutaways": 0, "verdicts": []},
            {"gates": gate}, "info",
            "No B-roll cutaways placed - correspondence not measured")

    try:
        planned, _ = reel_hearing.planned_words(timeline, transcript)
    except Exception as e:
        return RenderQAResult(
            METRIC, True, {"cutaways": len(cutaways), "error": str(e)},
            {"gates": gate}, "warning",
            f"Could not compute planned words: {e}")

    if not planned:
        return RenderQAResult(
            METRIC, True, {"cutaways": len(cutaways), "verdicts": [],
                            "note": "no planned words in transcript"},
            {"gates": gate}, "info",
            "No planned words in transcript - correspondence not measured")

    if model is None:
        from library.tools import vision_model
        model = vision_model.get_model()
    model_id = getattr(model, "MODEL_ID", "unknown")

    cache = _read_cache(project_folder)

    verdicts = []
    non_illustrating = []
    unparsed = []

    with tempfile.TemporaryDirectory(prefix="broll_corr_") as tmpdir:
        for cutaway in cutaways:
            words = words_at_span(planned, cutaway.timeline_in,
                                  cutaway.timeline_out)
            if not words:
                verdicts.append({
                    "clip_name": cutaway.clip_name,
                    "timeline_start": round(cutaway.timeline_in, 3),
                    "timeline_end": round(cutaway.timeline_out, 3),
                    "words": "",
                    "illustrates": None,
                    "reason": "no speech at this cutaway's span",
                    "cached": False,
                    "frames": 0,
                })
                continue

            times = window_frames.sample_times(
                cutaway.source_in, cutaway.source_out, SAMPLE_FPS)
            strip_path = os.path.join(tmpdir, f"cutaway_{len(verdicts)}.jpg")
            if not window_frames.draw_strip(cutaway.source_file, times,
                                             strip_path):
                verdicts.append({
                    "clip_name": cutaway.clip_name,
                    "timeline_start": round(cutaway.timeline_in, 3),
                    "timeline_end": round(cutaway.timeline_out, 3),
                    "words": words,
                    "illustrates": None,
                    "reason": "could not draw frames",
                    "cached": False,
                    "frames": 0,
                })
                continue

            key = _verdict_key(words, times, model_id)
            cached = cache.get(key)
            if cached is not None:
                verdicts.append({
                    "clip_name": cutaway.clip_name,
                    "timeline_start": round(cutaway.timeline_in, 3),
                    "timeline_end": round(cutaway.timeline_out, 3),
                    "words": words,
                    "illustrates": cached.get("illustrates"),
                    "reason": cached.get("reason", ""),
                    "cached": True,
                    "frames": len(times),
                })
                if cached.get("illustrates") is False:
                    non_illustrating.append(verdicts[-1])
                continue

            prompt = PROMPT.format(n=len(times), words=words)
            try:
                reply = model.analyze_images([strip_path], prompt,
                                             max_tokens=MAX_TOKENS)
            except Exception as e:
                verdicts.append({
                    "clip_name": cutaway.clip_name,
                    "timeline_start": round(cutaway.timeline_in, 3),
                    "timeline_end": round(cutaway.timeline_out, 3),
                    "words": words,
                    "illustrates": None,
                    "reason": f"VLM error: {e}",
                    "cached": False,
                    "frames": len(times),
                })
                unparsed.append(verdicts[-1])
                continue

            parsed = _parse_verdict(reply)
            cache[key] = parsed
            verdicts.append({
                "clip_name": cutaway.clip_name,
                "timeline_start": round(cutaway.timeline_in, 3),
                "timeline_end": round(cutaway.timeline_out, 3),
                "words": words,
                "illustrates": parsed["illustrates"],
                "reason": parsed["reason"] or "",
                "cached": False,
                "frames": len(times),
            })
            if parsed["illustrates"] is False:
                non_illustrating.append(verdicts[-1])
            elif parsed["illustrates"] is None:
                unparsed.append(verdicts[-1])

    if project_folder and cache:
        _write_cache(project_folder, cache)

    failed = bool(gate and non_illustrating)
    detail = (f"{len(non_illustrating)} of {len(cutaways)} cutaway(s) "
              f"do not illustrate the speech they cover")
    if failed:
        detail += " - the cutaway does not illustrate the speech"
    elif non_illustrating:
        detail += " - REPORTED ONLY; see BROLL_CORRESPONDENCE_GATES"

    return RenderQAResult(
        metric=METRIC,
        passed=not failed,
        value={
            "cutaways": len(cutaways),
            "verdicts": verdicts,
            "non_illustrating": non_illustrating,
            "unparsed": unparsed,
        },
        threshold={"gates": gate},
        severity="error" if failed else (
            "warning" if non_illustrating else "info"),
        detail=detail,
    )
