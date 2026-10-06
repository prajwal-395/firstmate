"""caption_correction.py - close the loop on a caption that obscures a face.

The captain's bar for captions is "legible, natural, grouped, not obscuring".
`render_qa.measure_caption_obscuring` (render_qa.py:2172) measures the
obscuring half: it intersects each caption's delivered ink box against the
faces `measure_face_intact`'s Haar path detects over the span.  But the
finding is REPORT-only (`CAPTION_QUALITY_GATES = False`, render_qa.py:878 -
a pending captain call) and nothing moves the caption and re-checks.  A
caption that covers the subject's face ships.

This module closes that loop.  `compute_clearance_move` is the measured
correction: given the caption's delivered ink box, the detected face boxes
and the frame height, it computes the minimal vertical move that clears
every face by the measured margin while staying inside the frame and in
the lower half where a subtitle belongs.  `CaptionCorrectionLoop` iterates
measure -> correct -> apply -> re-measure through the generic
`correction_loop.CorrectionLoop`, recording each iteration and the verdict.

The loop is CLOSED, not simulated, in three moves.  First, the corrected
placement is written to the overlay segment's `tight_box.placement` record
in the manifest the build reads (`resolve_build_timeline.py` places every
overlay through `place_overlay_segment` with that placement, and refuses
one without it) - the mutation lands on the manifest dict the pipeline
holds, so the state dump carries it to the next build.  Second, the
overlay is re-rendered offline with the correction applied
(`rerender_overlay_shifted`) and the re-measure runs the PRODUCTION
measurement chain (`_probe_overlay_ink` + `_subtitle_canvas_rect` +
`_delivered_ink_box` + `_boxes_intersect`) on the re-rendered pixels - the
loop's pass verdict and the gate's verdict cannot diverge, because they
are the same code on pixels, not a box moved in memory.  Third, the face
detections are reused from `measure_caption_obscuring` - Haar over a span
is the expensive part and running it twice is the defect this removes.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from library.tools.correction_loop import CorrectionLoop
from library.tools.qa.subtitle_qa import (
    CAPTION_TOP_LIMIT_FRACTION,
    EDGE_MARGIN_FRACTION,
)
from library.tools.render_qa import (
    DEFAULT_SAMPLE_FPS,
    OBSCURING_FACE_TOLERANCE_PX,
    _boxes_intersect,
)


def compute_clearance_move(
        caption_box: Tuple[float, float, float, float],
        face_boxes: List[Tuple[float, float, float, float]],
        frame_h: float,
        *,
        margin: float = OBSCURING_FACE_TOLERANCE_PX,
        top_limit_fraction: float = CAPTION_TOP_LIMIT_FRACTION,
        bottom_margin: float = 0.0,
) -> Tuple[float, float, Optional[str]]:
    """The minimal vertical move clearing `caption_box` of every face by `margin`.

    Returns ``(dx, dy, blocked_reason)``.  ``dx`` is always 0 - the correction
    is vertical, because a subtitle belongs in the lower half and moving it
    sideways would take it off the caption row.  ``blocked_reason`` is None
    when a move exists; otherwise it names the constraint that makes
    correction impossible (the frame bottom or the lower-half limit), which
    is the loop's honest "cannot" rather than an infinite retry.

    The caption may move down toward the frame bottom or up toward the
    lower-half limit.  Moving down clears every face when the caption's top
    reaches the lowest face's bottom plus margin; moving up clears every
    face when the caption's bottom reaches the highest face's top minus
    margin.  The smaller move that stays inside the frame and the lower half
    wins; when neither direction works the defect is named.
    """
    cl, ct, cr, cb = (float(v) for v in caption_box)
    faces = [(float(f[0]), float(f[1]), float(f[2]), float(f[3]))
             for f in face_boxes]
    frame_h = float(frame_h)
    limit_y = frame_h * float(top_limit_fraction)
    bottom_margin = float(bottom_margin)

    if not faces:
        return (0.0, 0.0, "no faces to clear")

    down = max(f[3] + margin - ct for f in faces)
    up = min(f[1] - margin - cb for f in faces)

    candidates = []
    if down > 0:
        new_bottom = cb + down
        if new_bottom <= frame_h - bottom_margin:
            candidates.append((down, "down"))
    if up < 0:
        new_top = ct + up
        if new_top >= limit_y and new_top >= 0:
            candidates.append((up, "up"))

    if not candidates:
        if down > 0 and cb + down > frame_h - bottom_margin:
            return (0.0, 0.0,
                    f"clearing the lowest face needs the caption bottom at "
                    f"y={cb + down:.0f}, past the frame bottom "
                    f"({frame_h:.0f} - {bottom_margin:.0f} margin)")
        if up < 0 and ct + up < limit_y:
            return (0.0, 0.0,
                    f"clearing the highest face needs the caption top at "
                    f"y={ct + up:.0f}, above the lower-half limit "
                    f"({limit_y:.0f})")
        return (0.0, 0.0,
                "no vertical move clears the faces within the frame")

    candidates.sort(key=lambda c: abs(c[0]))
    dy, _direction = candidates[0]
    return (0.0, dy, None)


class CaptionCorrectionLoop(CorrectionLoop):
    """Iterate a caption's position until it clears every detected face.

    Holds the caption's delivered ink box, the detected face boxes and the
    frame height, and runs the generic loop: measure the overlap, compute
    the minimal clearance move, apply it to the box, re-measure.  The
    production caller supplies `measure`/`apply` that read the render and
    write the placement; the geometry and the iteration discipline are the
    same, and are what the fixtures here pin.
    """

    def __init__(self, caption_box, face_boxes, frame_h, *,
                 margin: float = OBSCURING_FACE_TOLERANCE_PX,
                 top_limit_fraction: float = CAPTION_TOP_LIMIT_FRACTION,
                 edge_margin_fraction: float = EDGE_MARGIN_FRACTION,
                 max_iterations: int = 3):
        self._caption_box = [float(v) for v in caption_box]
        self._face_boxes = [[float(v) for v in f] for f in face_boxes]
        self._frame_h = float(frame_h)
        self._margin = float(margin)
        self._top_limit_fraction = float(top_limit_fraction)
        self._bottom_margin = float(edge_margin_fraction) * float(frame_h)
        super().__init__(
            measure=self._measure,
            correct=self._correct,
            apply=self._apply,
            max_iterations=max_iterations,
            name="caption_obscuring",
        )

    def _measure(self) -> dict:
        hits = [f for f in self._face_boxes
                if _boxes_intersect(self._caption_box, f, self._margin)]
        return {
            "passed": not hits,
            "caption_box": list(self._caption_box),
            "face_boxes": [list(f) for f in self._face_boxes],
            "overlapping_faces": len(hits),
            "frame_h": self._frame_h,
        }

    def _correct(self, measurement: dict) -> Optional[dict]:
        dx, dy, blocked = compute_clearance_move(
            measurement["caption_box"],
            measurement["face_boxes"],
            measurement["frame_h"],
            margin=self._margin,
            top_limit_fraction=self._top_limit_fraction,
            bottom_margin=self._bottom_margin,
        )
        if blocked:
            measurement["cannot_correct_reason"] = blocked
            return None
        return {"dx": dx, "dy": dy}

    def _apply(self, correction: dict) -> None:
        self._caption_box[1] += correction["dy"]
        self._caption_box[3] += correction["dy"]

    @property
    def caption_box(self) -> List[float]:
        """The caption's current delivered ink box, in frame pixels."""
        return list(self._caption_box)


def _detect_obscuring(seg, video_path: str,
                      sample_fps: float = DEFAULT_SAMPLE_FPS) -> Optional[dict]:
    """The obscuring measurement for one segment, read off the render.

    Reuses `render_qa`'s own detection - the Haar face path
    (`_faces_in_span`) and the overlay's delivered ink box
    (`_probe_overlay_ink` + `_delivered_ink_box`) - so a face this detector
    calls a face is a face `measure_caption_obscuring` calls a face.
    Returns None when the segment's caption clears every detected face.
    """
    from library.tools.render_qa import (
        _delivered_ink_box,
        _faces_in_span,
        _probe_overlay_ink,
        _probe_video_size,
        _subtitle_canvas_rect,
    )

    probe = _probe_overlay_ink(seg.path)
    if probe is None:
        return None
    canvas = probe["canvas"]
    size = _probe_video_size(video_path)
    if not size:
        return None
    frame_w, frame_h = size
    rect = _subtitle_canvas_rect(seg.path, canvas[0], canvas[1])
    box = _delivered_ink_box(probe["ink"], canvas, rect)
    faces = _faces_in_span(video_path, seg.start, seg.end, sample_fps)
    hits = [f for f in faces if _boxes_intersect(box, f["box"],
                                                 OBSCURING_FACE_TOLERANCE_PX)]
    if not hits:
        return None
    return {
        "passed": False,
        "caption_box": box,
        "face_boxes": [f["box"] for f in faces],
        "frame_h": float(frame_h),
        "overlapping_faces": len(hits),
    }


def _overlay_frames_rgba(overlay_path: str) -> Optional[list]:
    """Every frame of one overlay file as an RGBA PIL image, or None.

    A directory of PNGs loads directly; a video file decodes through
    ffmpeg the way `_probe_overlay_ink_video` does, so the re-render
    reads the same pixels the measurement read.
    """
    from PIL import Image

    if not overlay_path or not os.path.exists(overlay_path):
        return None
    try:
        if os.path.isdir(overlay_path):
            names = sorted(n for n in os.listdir(overlay_path)
                           if n.endswith(".png"))
            if not names:
                return None
            return [Image.open(os.path.join(overlay_path, n)).convert("RGBA")
                    for n in names]
    except Exception:  # noqa: BLE001 - an unreadable overlay reports, never raises
        return None
    try:
        import numpy as np

        from library.tools.render_qa import _probe_video_size

        size = _probe_video_size(overlay_path)
        if not size:
            return None
        canvas_w, canvas_h = size
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", overlay_path,
             "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
            capture_output=True, timeout=600, check=False)
        if result.returncode != 0 or not result.stdout:
            return None
        raw = result.stdout
        frame_bytes = canvas_w * canvas_h * 4
        if frame_bytes == 0 or len(raw) % frame_bytes != 0:
            return None
        count = len(raw) // frame_bytes
        if count == 0:
            return None
        from PIL import Image as _Image
        return [_Image.fromarray(
            np.frombuffer(
                raw[i * frame_bytes:(i + 1) * frame_bytes],
                dtype=np.uint8).reshape(canvas_h, canvas_w, 4).copy(), "RGBA")
            for i in range(count)]
    except Exception:  # noqa: BLE001 - an unreadable overlay reports, never raises
        return None


def rerender_overlay_shifted(overlay_path: str, dx: float, dy: float,
                             frame_w: int, frame_h: int) -> Optional[str]:
    """Re-render one overlay file with its canvas shifted by (dx, dy).

    Pastes every overlay frame onto a transparent frame-sized image at
    the canvas rect shifted by the correction, and writes the frames as
    a PNG directory under the system temp dir.  No sidecar is written,
    so the production chain reads the re-render as full-canvas and the
    mapping is the identity.  Returns the directory, or None when the
    overlay cannot be read - the loop then says so instead of claiming
    a pixel re-measure it never ran.
    """
    from PIL import Image

    from library.tools.render_qa import _subtitle_canvas_rect

    frames = _overlay_frames_rgba(overlay_path)
    if not frames:
        return None
    canvas_w, canvas_h = frames[0].size
    rect = _subtitle_canvas_rect(overlay_path, canvas_w, canvas_h)
    ox = int(round(rect[0] + dx))
    oy = int(round(rect[1] + dy))
    try:
        out_dir = tempfile.mkdtemp(prefix="caption_rerender_")
        for index, frame in enumerate(frames):
            layer = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
            layer.paste(frame, (ox, oy), frame)
            frame.close()
            layer.save(os.path.join(out_dir, f"frame_{index:05d}.png"))
        return out_dir
    except Exception:  # noqa: BLE001 - an unwritable scratch dir reports, never raises
        return None


def remeasure_rerendered(rerendered_dir: str,
                         face_boxes: List[List[float]],
                         margin: float = OBSCURING_FACE_TOLERANCE_PX,
                         ) -> Optional[dict]:
    """Re-run the production obscuring measurement on re-rendered pixels.

    The SAME chain `measure_caption_obscuring` runs - `_probe_overlay_ink`
    off the re-rendered files, `_subtitle_canvas_rect` (no sidecar, so
    full-canvas), `_delivered_ink_box`, `_boxes_intersect` against the
    reused face detections - so a pass here is the gate's pass, not the
    loop's geometry agreeing with itself.  None when the re-render
    cannot be decoded.
    """
    from library.tools.render_qa import (
        _delivered_ink_box,
        _probe_overlay_ink,
        _subtitle_canvas_rect,
    )

    probe = _probe_overlay_ink(rerendered_dir)
    if probe is None:
        return None
    canvas = probe["canvas"]
    box = _delivered_ink_box(
        probe["ink"], canvas,
        _subtitle_canvas_rect(rerendered_dir, canvas[0], canvas[1]))
    hits = [f for f in face_boxes
            if _boxes_intersect(box, f, margin)]
    return {
        "passed": not hits,
        "remeasured_box": [float(v) for v in box],
        "remeasured_overlapping_faces": len(hits),
    }


def correct_caption_obscuring(
        video_path: str,
        overlay_segments: List[Any],
        *,
        obscuring_result: Optional[dict] = None,
        detect: Callable[[Any, str], Optional[dict]] = _detect_obscuring,
        write_placement: Optional[Callable[[Any, dict], None]] = None,
        re_render: Callable[[str, float, float, int, int], Optional[str]
                            ] = rerender_overlay_shifted,
        max_iterations: int = 3) -> List[dict]:
    """Close the loop on every caption the render shows obscuring a face.

    For each subtitle overlay segment whose caption box overlaps a detected
    face, run the `CaptionCorrectionLoop`: measure the overlap, compute
    the minimal clearance move, apply it to the box, re-measure, until the
    box clears every face or the loop names why it cannot.  Returns one
    record per obscuring segment carrying the loop's verdict, its full
    iteration history, the corrected ink box and the pixel re-measure.

    `obscuring_result` is the `measure_caption_obscuring` VALUE dict -
    the `RenderQAResult.value` the scoped re-check passes straight
    through - whose `obscuring` rows carry the face detections.  Reusing
    them avoids running Haar over the same spans twice; when it is None
    the detections are read from the render via `detect` (the bridge
    path).  Rows are keyed the way the measurement keys them, by the
    overlay file's basename.

    The frame size is probed ONCE from the render, up front, and drives
    both the placement and the re-render.  Reels are vertical - a shape
    literal here would place the correction for a frame nobody renders.
    When the size cannot be probed the segment is refused explicitly,
    never corrected against a guessed frame.

    `write_placement` is called with the segment and the corrected
    placement dict; it writes the placement where the build reads it
    (the manifest segment's `tight_box.placement`, which
    `place_overlay_segment` reads and refuses when absent).  `re_render`
    re-renders the overlay file shifted by the loop's total correction
    and returns its path; the re-measure runs the production chain on
    those pixels.  When the overlay cannot be re-rendered the record
    says `remeasured: False` and keeps the geometry verdict - an
    in-memory pass is reported as one, never as a pixel pass.
    """
    from library.tools.render_qa import (
        _probe_video_size,
        _subtitle_overlay_segments,
    )
    from library.tools.tight_box import placement_for_box

    records: List[dict] = []
    segments = _subtitle_overlay_segments(overlay_segments)

    detections: Dict[str, dict] = {}
    if obscuring_result is not None and isinstance(obscuring_result, dict):
        for row in (obscuring_result.get("obscuring") or []):
            if not isinstance(row, dict):
                continue
            detections[str(row.get("segment", ""))] = {
                "passed": False,
                "caption_box": row.get("caption_box"),
                "face_boxes": row.get("face_boxes", []),
                "frame_h": row.get("frame_h"),
                "overlapping_faces": row.get("overlapping_faces", 0),
            }

    frame_size = _probe_video_size(video_path)

    for seg in segments:
        seg_name = getattr(seg, "segment_id",
                           getattr(seg, "path", str(seg)))
        seg_key = str(getattr(seg, "path", seg_name))
        found = (detections.get(seg_key)
                 or detections.get(seg_name)
                 or detections.get(os.path.basename(seg_key)))
        if found is None:
            found = detect(seg, video_path)
        if not found:
            continue
        if found.get("caption_box") is None:
            continue

        if frame_size is None:
            records.append({
                "segment": seg_name,
                "verdict": "cannot_correct",
                "passed": False,
                "detail": (f"the frame size could not be probed from "
                           f"{video_path}, so no placement can be computed "
                           f"for a frame nobody measured"),
                "iterations": [],
                "corrected_box": None,
                "corrected_placement": None,
                "remeasured": False,
            })
            continue
        frame_w, frame_h = int(frame_size[0]), float(frame_size[1])

        face_boxes = found["face_boxes"]
        caption_box = found["caption_box"]

        loop = CaptionCorrectionLoop(
            caption_box, face_boxes, frame_h,
            max_iterations=max_iterations)
        result = loop.run()

        corrected_box = loop.caption_box
        bw = float(corrected_box[2] - corrected_box[0])
        bh = float(corrected_box[3] - corrected_box[1])
        placement = placement_for_box(
            bw, bh,
            (float(corrected_box[0]) + float(corrected_box[2])) / 2,
            (float(corrected_box[1]) + float(corrected_box[3])) / 2,
            frame_w, int(frame_h)) if result.passed else None

        if placement is not None and write_placement is not None:
            write_placement(seg, placement)

        corrections = result.corrections_applied()
        total_dx = sum(c["dx"] for c in corrections)
        total_dy = sum(c["dy"] for c in corrections)
        rerendered_path = (
            re_render(seg_key, total_dx, total_dy, frame_w, int(frame_h))
            if result.passed and re_render is not None else None)
        remeasured = None
        if rerendered_path is not None:
            remeasured = remeasure_rerendered(
                rerendered_path, [list(f) for f in face_boxes])

        if remeasured is None:
            verdict, passed, detail = (
                result.verdict, result.passed, result.detail)
            if result.passed:
                detail += (" - the overlay could not be re-rendered, so "
                           "this pass is the loop's geometry, not pixels")
        elif not remeasured["passed"]:
            verdict, passed = "cannot_correct", False
            detail = (f"the re-rendered overlay still overlaps "
                      f"{remeasured['remeasured_overlapping_faces']} "
                      f"face(s) - the geometry pass did not survive "
                      f"pixels")
        else:
            verdict, passed = result.verdict, True
            detail = result.detail

        records.append({
            "segment": seg_name,
            "verdict": verdict,
            "passed": passed,
            "detail": detail,
            "iterations": result.as_dict()["iterations"],
            "corrected_box": corrected_box,
            "corrected_placement": placement,
            "remeasured": remeasured is not None,
            "rerendered_path": rerendered_path,
            "remeasured_box": (remeasured or {}).get("remeasured_box"),
            "remeasured_overlapping_faces": (remeasured or {}).get(
                "remeasured_overlapping_faces"),
        })
    return records
