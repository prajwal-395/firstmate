"""Composite the caption layer over a picture-only base render with ffmpeg.

INFRA-006 lane D: the caption layer composites in ffmpeg in seconds
while Resolve renders a reel of this class in 110-200 s. This module
is the instrument for that decision: given a picture-only Resolve
render and the caption segments step 4.05 already rendered, it
overlays every card at its recorded record span and canvas origin,
producing the delivered file without Resolve ever compositing a
caption. Measured 2026-10-22 the saving is not real (captions cost
the Resolve render ~0 s), so the deliver path keeps compositing in
Resolve and this module stays unwired - see
docs/evidence/2026-10-22-ffmpeg-caption-composite.md.

Fail-closed rules:

- Every value is read off the segment record or the caller's own
  arguments: record frames from ``timeline_start``/``timeline_end``
  through ``frame_utils.span_frames`` (the same per-edge rounding the
  placer uses in ``reel_caption_placement.caption_span``), the source
  window from ``source_in_frame``/``source_out_frame``, the position
  from ``tight_box.absolute_canvas_box``. Nothing here carries a row,
  a size, a font or a timing of its own.
- A segment the record cannot place in Resolve is refused here too: a
  missing overlay file, a frames-container sequence (``container ==
  "frames"`` - a second compositor, refused by name), a card with
  neither ``tight_box.absolute_canvas_box`` nor the legacy
  ``tight_box.origin`` (the position source with no fallback - the
  placement floats are gain-dependent and are never read), overlapping
  record spans on the one caption row, and an overlay whose probed
  dimensions differ from the canvas the record claims. Each refusal
  names the segment.
- The source window is the placer's own arithmetic
  (``reel_caption_placement.caption_source_range``: ``source_in`` plus
  the record span), never the ledger's ``source_out_frame``: the
  ledger's value is one past what the placer passes on real reels
  (measured on Reel 26: ledger 46 against placed 45, ledger 55
  against placed 54), and the timeline read-back confirms Resolve
  plays exactly the record span from ``source_in``. A ledger value
  that disagrees is recorded on the overlay as a note, never silently
  dropped and never a refusal.
- Full-canvas cards (``tight_box`` None) overlay at (0, 0) and must
  probe at exactly the delivery frame: a full-canvas card IS the frame,
  so any other size is a different picture, refused rather than scaled.
- The base render's probed dimensions and frame rate must match the
  declared delivery frame and fps: compositing onto any other picture
  is a different deliverable, refused rather than resampled.
- The caption files' own audio (step 4.05 renders carry a silent PCM
  track) never reaches the output: the delivered mix is the base
  render's, mapped explicitly.
- Every ``subprocess.run`` capturing text passes ``encoding="utf-8"``.

Fidelity is judged beside the composite, never assumed: whole-file
SSIM (``native_effects_composite.measure_ssim``) plus per-caption-box
crop SSIM over every placed card, so a drifted card cannot hide behind
a good whole-frame average.
"""

from __future__ import annotations

import itertools
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from library.tools import frame_utils
except ImportError:  # imported as ``tools.*`` from inside library/
    from tools import frame_utils


#: Delivery codecs this composite may write. H264 is what the pipeline
#: delivers today (``delivery_format``/``resolve_render``); the list is
#: closed so a caller cannot ask for a carriage nobody verified.
DELIVERY_VIDEO_CODECS = ("h264",)

#: How far the base render's probed fps may sit from the declared fps.
#: Caption cards render at the timeline fps exactly; a base beyond this
#: is a different timebase, refused rather than retimed.
FPS_TOLERANCE = 0.01


class CaptionCompositeRefused(RuntimeError):
    """A caption layer that cannot be composited as Resolve placed it."""


def _refused(segment_id: str, reason: str) -> dict[str, str]:
    return {"segment_id": segment_id, "reason": reason}


def _probe(path: Path) -> dict[str, Any]:
    """The base render's own dimensions, fps and duration, or a refusal."""
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "default=nw=1", str(path),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False)
    if completed.returncode != 0:
        raise CaptionCompositeRefused(
            f"the base render {path} could not be probed: "
            f"{completed.stderr.strip()[-300:]}")
    values: dict[str, str] = {}
    for line in (completed.stdout or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    try:
        width = int(values["width"])
        height = int(values["height"])
    except (KeyError, ValueError):
        raise CaptionCompositeRefused(
            f"the base render {path} probed with no dimensions")
    rate = values.get("avg_frame_rate") or values.get("r_frame_rate") or ""
    fps = 0.0
    if "/" in rate:
        numerator, _, denominator = rate.partition("/")
        try:
            fps = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            fps = 0.0
    else:
        try:
            fps = float(rate)
        except ValueError:
            fps = 0.0
    duration = 0.0
    for key in ("duration", "format_duration"):
        try:
            duration = float(values[key])
            break
        except (KeyError, ValueError):
            continue
    return {"width": width, "height": height, "fps": fps,
            "duration": duration}


def _probe_overlay_dims(path: Path) -> tuple[int, int]:
    """One caption file's own video dimensions, or a refusal."""
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "default=nw=1", str(path),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False)
    if completed.returncode != 0:
        raise CaptionCompositeRefused(
            f"caption file {path} could not be probed: "
            f"{completed.stderr.strip()[-300:]}")
    values: dict[str, str] = {}
    for line in (completed.stdout or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    try:
        return int(values["width"]), int(values["height"])
    except (KeyError, ValueError):
        raise CaptionCompositeRefused(
            f"caption file {path} probed with no dimensions")


def plan_caption_overlays(
        segments: Sequence[Mapping],
        *,
        fps: float,
        width: int,
        height: int,
) -> dict[str, Any]:
    """The ffmpeg overlay plan for one reel's caption layer.

    Returns ``{"overlays": [...], "refused": [...]}``: one overlay per
    segment that Resolve could place, in record order. ``overlays``
    carry ``segment_id``, ``path``, ``x``/``y`` (delivery pixels),
    ``record_start``/``record_end`` (frames, end EXCLUSIVE) and
    ``source_in``/``source_out`` (frames, end EXCLUSIVE). ``refused``
    names every segment that cannot composite as Resolve placed it, so
    a caller sees the whole layer's answer before spending a render.
    An empty layer plans zero overlays and refuses nothing: a reel
    with no captions needs no composite.
    """
    try:
        fps_value = float(fps)
    except (TypeError, ValueError):
        raise CaptionCompositeRefused(
            f"the composite needs a numeric fps, got {fps!r}")
    if fps_value <= 0:
        raise CaptionCompositeRefused(
            f"the composite needs a positive fps, got {fps!r}")
    if int(width) <= 0 or int(height) <= 0:
        raise CaptionCompositeRefused(
            f"the composite needs a positive frame, got "
            f"{width!r}x{height!r}")

    overlays: list[dict[str, Any]] = []
    refused: list[dict[str, str]] = []
    spans: list[tuple[int, int, str]] = []
    ordered = sorted(
        (segments or []),
        key=lambda segment: (float(segment.get("timeline_start", 0.0)),
                             str(segment.get("segment_id", ""))))
    for segment in ordered:
        segment_id = str(segment.get("segment_id") or "caption")
        if (segment.get("container") or "") == "frames":
            refused.append(_refused(
                segment_id,
                "frames-container caption: a PNG sequence needs its own "
                "compositor, refused rather than stitched here"))
            continue
        overlay_path = str(segment.get("overlay_path") or "")
        if not overlay_path or not Path(overlay_path).is_file():
            refused.append(_refused(
                segment_id,
                f"caption file is absent: {overlay_path or 'no path recorded'}"))
            continue
        try:
            record_start, record_end = frame_utils.span_frames(
                float(segment["timeline_start"]),
                float(segment["timeline_end"]), fps_value)
        except (KeyError, TypeError, ValueError):
            refused.append(_refused(
                segment_id, "no placeable timeline span: "
                "timeline_start/timeline_end must be numbers"))
            continue
        if record_end <= record_start:
            refused.append(_refused(
                segment_id, f"empty record span [{record_start}, "
                f"{record_end}): nothing to composite"))
            continue
        try:
            source_in = int(segment["source_in_frame"])
        except (KeyError, TypeError, ValueError):
            refused.append(_refused(
                segment_id, "no source window: source_in_frame must be "
                "an integer"))
            continue
        source_out = source_in + (record_end - record_start)
        note = ""
        try:
            ledger_out = int(segment["source_out_frame"])
        except (KeyError, TypeError, ValueError):
            ledger_out = source_out
        if ledger_out != source_out:
            note = (f"ledger source_out {ledger_out} is not the placed "
                    f"{source_out}; compositing the placed window")
        tight = segment.get("tight_box") or {}
        box = tight.get("absolute_canvas_box")
        if box is None:
            origin = tight.get("origin")
            if origin is None:
                x, y = 0, 0
                canvas_w, canvas_h = int(width), int(height)
            else:
                try:
                    x, y = int(origin[0]), int(origin[1])
                    canvas_w = int(tight["width"])
                    canvas_h = int(tight["height"])
                except (TypeError, ValueError, IndexError, KeyError):
                    refused.append(_refused(
                        segment_id, "unreadable tight_box: origin and "
                        "width/height must be integers"))
                    continue
        else:
            try:
                x, y = int(box[0]), int(box[1])
                canvas_w = int(tight["width"])
                canvas_h = int(tight["height"])
            except (TypeError, ValueError, IndexError, KeyError):
                refused.append(_refused(
                    segment_id, "unreadable tight_box: "
                    "absolute_canvas_box and width/height must be integers"))
                continue
        try:
            file_w, file_h = _probe_overlay_dims(Path(overlay_path))
        except CaptionCompositeRefused as exc:
            refused.append(_refused(segment_id, str(exc)))
            continue
        if (file_w, file_h) != (canvas_w, canvas_h):
            refused.append(_refused(
                segment_id,
                f"canvas_mismatch: the record claims {canvas_w}x{canvas_h} "
                f"but the file probes {file_w}x{file_h}; scaling it to fit "
                f"would composite what Resolve never placed"))
            continue
        overlays.append({
            "segment_id": segment_id,
            "path": overlay_path,
            "x": x, "y": y,
            "record_start": record_start,
            "record_end": record_end,
            "source_in": source_in,
            "source_out": source_out,
            "note": note,
        })
        spans.append((record_start, record_end, segment_id))
    spans.sort()
    for ((first_start, first_end, first_id),
            (second_start, _, second_id)) in itertools.pairwise(spans):
        if second_start < first_end:
            refused.append(_refused(
                second_id,
                f"overlapping_record_span: [{second_start}, ...) starts "
                f"inside {first_id}'s [{first_start}, {first_end}); one "
                f"caption row plays one card at a time"))
            overlays[:] = [overlay for overlay in overlays
                           if overlay["segment_id"] not in
                           (first_id, second_id)]
    return {"overlays": overlays, "refused": refused}


def composite_caption_layer(
        base_mp4: str | Path,
        segments: Sequence[Mapping],
        out_mp4: str | Path,
        *,
        fps: float,
        width: int,
        height: int,
        video_codec: str = "h264",
) -> dict[str, Any]:
    """Composite one reel's caption layer over its picture-only render.

    ``base_mp4`` is the Resolve render of the same timeline with its
    caption row absent; ``segments`` are the step-4.05 caption records
    Resolve placed on the full render. The output carries the base
    render's picture and mix with every card drawn at its recorded
    span and origin. Returns the receipt: ``{"out", "n_overlays",
    "seconds", "filter", "refused", "base_probe"}``. Any refusal in
    the plan refuses the composite before ffmpeg starts.
    """
    if video_codec not in DELIVERY_VIDEO_CODECS:
        raise CaptionCompositeRefused(
            f"delivery video codec must be one of "
            f"{DELIVERY_VIDEO_CODECS}, got {video_codec!r}")
    base = Path(base_mp4)
    if not base.is_file():
        raise CaptionCompositeRefused(f"base render is absent: {base}")
    try:
        fps_value = float(fps)
    except (TypeError, ValueError):
        raise CaptionCompositeRefused(
            f"the composite needs a numeric fps, got {fps!r}")
    probe = _probe(base)
    if (probe["width"], probe["height"]) != (int(width), int(height)):
        raise CaptionCompositeRefused(
            f"base render probes {probe['width']}x{probe['height']}, not "
            f"the declared {int(width)}x{int(height)}: compositing onto "
            f"any other picture is a different deliverable")
    if not probe["fps"] or abs(probe["fps"] - fps_value) > FPS_TOLERANCE:
        raise CaptionCompositeRefused(
            f"base render probes at {probe['fps']:.5f} fps, not the "
            f"declared {fps_value}: a retime is a different placement")
    plan = plan_caption_overlays(
        segments, fps=fps_value, width=int(width), height=int(height))
    if plan["refused"]:
        first = plan["refused"][0]
        raise CaptionCompositeRefused(
            f"{first['segment_id']}: {first['reason']} "
            f"({len(plan['refused'])} of "
            f"{len(plan['overlays']) + len(plan['refused'])} refused)")
    overlays = plan["overlays"]
    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    if not overlays:
        out.write_bytes(base.read_bytes())
        return {"out": str(out), "n_overlays": 0,
                "seconds": time.monotonic() - started,
                "filter": "", "refused": [],
                "base_probe": probe, "copied_base": True}

    command: list[str] = ["ffmpeg", "-v", "error", "-y",
                          "-i", str(base)]
    for overlay in overlays:
        command += ["-i", overlay["path"]]
    filters: list[str] = []
    for index, overlay in enumerate(overlays, start=1):
        filters.append(
            f"[{index}:v]trim=start_frame={overlay['source_in']}:"
            f"end_frame={overlay['source_out']},"
            f"setpts=PTS-STARTPTS+{overlay['record_start']}/{fps_value}/TB,"
            f"format=rgba[c{index}]")
    current = "[0:v]"
    for index, overlay in enumerate(overlays, start=1):
        label = f"[m{index}]"
        filters.append(
            f"{current}[c{index}]overlay=x={overlay['x']}:y={overlay['y']}:"
            f"enable='between(n\\,{overlay['record_start']}\\,"
            f"{overlay['record_end'] - 1})':eof_action=pass{label}")
        current = label
    filters.append(f"{current}format=yuv420p[vout]")
    # The delivery encode is the engine's own (``native_effects_composite.
    # FINAL_H264_ARGS``): the composite IS the delivered file when this
    # path ships, so it encodes like one. A faster or slower preset
    # here would be a different deliverable, not a faster composite.
    command += ["-filter_complex", ";".join(filters),
                "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "16",
                "-c:a", "copy", str(out)]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False)
    seconds = time.monotonic() - started
    if completed.returncode != 0:
        raise CaptionCompositeRefused(
            f"ffmpeg composite failed: "
            f"{completed.stderr.strip()[-500:]}")
    if not out.is_file() or out.stat().st_size == 0:
        raise CaptionCompositeRefused(
            f"ffmpeg reported success and wrote no file at {out}")
    return {"out": str(out), "n_overlays": len(overlays),
            "seconds": seconds,
            "filter": ";".join(filters), "refused": [],
            "base_probe": probe, "copied_base": False}


def measure_caption_boxes(
        full_mp4: str | Path,
        composite_mp4: str | Path,
        boxes: Sequence[Sequence[int]],
        *,
        frame: int = 0,
) -> dict[str, Any]:
    """Per-caption-box SSIM between the Resolve render and the composite.

    ``boxes`` are ``(x0, y0, x1, y1)`` delivery-pixel boxes, one per
    placed card. The whole-file SSIM can pass while one card drifts -
    a box a card never reaches still matches - so every box is cropped
    out of both files and scored on its own. Returns ``{"boxes":
    [{"box", "ssim_y"}], "min_y", "mean_y", "n"}``. Both files must
    exist; ``frame`` is reserved for a windowed comparison and stays 0
    (the whole file) until a caller needs one.
    """
    first, second = Path(full_mp4), Path(composite_mp4)
    for path in (first, second):
        if not path.is_file():
            raise FileNotFoundError(
                f"box comparison input is absent: {path}")
    if frame != 0:
        raise ValueError(
            f"windowed box comparison is not implemented, got frame={frame!r}")
    rows: list[dict[str, Any]] = []
    for box in boxes:
        x0, y0, x1, y1 = (int(value) for value in box)
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"caption box has no area: {list(box)!r}")
        crop = f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}"
        command = [
            "ffmpeg", "-v", "info",
            "-i", str(first), "-i", str(second),
            "-filter_complex",
            (f"[0:v]{crop},setpts=PTS-STARTPTS[b];"
             f"[1:v]{crop},setpts=PTS-STARTPTS[c];[b][c]ssim"),
            "-f", "null", "-",
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False)
        combined = ((completed.stderr or "") + "\n"
                    + (completed.stdout or ""))
        score: float | None = None
        for line in combined.splitlines():
            if "SSIM" in line and "Y:" in line:
                tail = line.split("Y:", 1)[1].strip().split()[0]
                try:
                    score = float(tail)
                except ValueError:
                    continue
        if score is None:
            raise RuntimeError(
                f"ffmpeg printed no box SSIM Y score for box "
                f"{list(box)!r}: {combined.strip()[-300:]}")
        rows.append({"box": [x0, y0, x1, y1], "ssim_y": score})
    values = [row["ssim_y"] for row in rows]
    return {"boxes": rows,
            "min_y": min(values) if values else None,
            "mean_y": (sum(values) / len(values)) if values else None,
            "n": len(rows)}


def composite_receipt(
        *,
        base: str,
        out: str,
        n_overlays: int,
        seconds: float,
        ssim_all: Mapping[str, float] | None,
        boxes: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The run's own account of one caption composite and its parity."""
    return {
        "base": base,
        "out": out,
        "n_overlays": n_overlays,
        "seconds": seconds,
        "ssim_y": (dict(ssim_all).get("Y") if ssim_all else None),
        "ssim_all": dict(ssim_all) if ssim_all else None,
        "box_min_y": (boxes or {}).get("min_y"),
        "box_mean_y": (boxes or {}).get("mean_y"),
        "box_n": (boxes or {}).get("n"),
    }
