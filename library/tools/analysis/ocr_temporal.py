"""Temporal OCR intelligence: change events, track linking, change-aware skip.

Covers GEO-022 (detect changes in on-screen text over time), GEO-023
(track text instances across consecutive frames) and GEO-024 (avoid
repeatedly running OCR on unchanged screen regions).

The per-frame recognizer stays the authority on WHAT the text says;
this module decides WHEN to run it and HOW its outputs link across
time. All geometry is source-normalised (0..1, top-left origin), the
same space `ocr_extractor` stores.

Glossary (ARCH-006):
- measured: frame-difference scores, IoU overlaps, linked track spans.
- observed: the recognizer's strings and boxes (carried through, not made here).
- interpreted: nothing here interprets meaning; layout relations in
  `docs/benchmarks/ocr_eval.py` stay structural (left-of, above), never semantic.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextChangeEvent:
    """One observable change in on-screen text between two samples."""

    kind: str  # appeared | disappeared | changed | moved
    track_id: str
    text_before: str
    text_after: str
    at_seconds: float
    iou: float = 0.0


@dataclass
class LinkedTrack:
    """A text instance followed across consecutive samples (GEO-023)."""

    track_id: str
    text: str
    span_start_seconds: float
    span_end_seconds: float
    sample_count: int
    mean_iou: float = 0.0
    changed: bool = False


def box_iou(a: list | tuple, b: list | tuple) -> float:
    ax, ay, aw, ah = (float(v) for v in a)
    bx, by, bw, bh = (float(v) for v in b)
    inter = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0.0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def frame_changed(prev_gray_mean: float, curr_gray_mean: float,
                  changed_fraction: float,
                  pixel_threshold: float = 2.0,
                  fraction_threshold: float = 0.002) -> bool:
    """Whether a region changed enough to deserve a fresh OCR pass.

    The caller supplies a cheap frame signal (mean absolute difference
    fraction above `pixel_threshold`, computed with numpy or cv2);
    this predicate is the single place that turns the signal into a
    run/skip decision, so the threshold lives in one spot and the
    benchmark can ablate it. Returns True (run OCR) when the signal
    is missing (`None`), fail-open rather than skipping blind.
    """
    if changed_fraction is None:
        return True
    if prev_gray_mean is None or curr_gray_mean is None:
        return changed_fraction >= fraction_threshold
    if abs(curr_gray_mean - prev_gray_mean) >= pixel_threshold:
        return True
    return changed_fraction >= fraction_threshold


def region_changed_fraction(prev_frame, curr_frame,
                             box: list | tuple | None = None,
                             pixel_threshold: float = 8.0):
    """Measured fraction of pixels in a region that changed (GEO-024 signal).

    Returns None when numpy/cv2 is unavailable so the caller runs OCR
    fail-open. `box` is an optional normalized xywh region; None means
    the whole frame.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        prev = np.asarray(prev_frame)
        curr = np.asarray(curr_frame)
    except Exception:
        return None
    if prev.shape != curr.shape:
        return 1.0
    if box is not None:
        h, w = prev.shape[:2]
        x, y, bw, bh = box
        x0, y0 = max(0, int(x * w)), max(0, int(y * h))
        x1, y1 = min(w, int((x + bw) * w)), min(h, int((y + bh) * h))
        if x1 <= x0 or y1 <= y0:
            return None
        prev = prev[y0:y1, x0:x1]
        curr = curr[y0:y1, x0:x1]
    try:
        import numpy as _np

        diff = _np.abs(prev.astype(_np.float32) - curr.astype(_np.float32))
        if diff.ndim == 3:
            diff = diff.mean(axis=-1)
        changed = (diff >= pixel_threshold).mean()
        prev_mean = float(_np.asarray(prev).mean())
        curr_mean = float(_np.asarray(curr).mean())
        return {"changed_fraction": float(changed),
                "prev_mean": prev_mean, "curr_mean": curr_mean}
    except Exception:
        return None


def link_detections(frames: list, iou_threshold: float = 0.3) -> list[LinkedTrack]:
    """Link per-frame detections into tracks across consecutive frames.

    `frames` is a list of `{"at_seconds": float, "detections":
    [{"text": str, "bbox": [x, y, w, h]}]}` in time order. A detection
    joins the open track with the best IoU above threshold when the
    normalized text also matches case-insensitively; otherwise it opens
    a new track. A track closes at the first sample where nothing joins
    it. Text edits open a change event rather than silently renaming.
    """
    from docs.benchmarks.ocr_eval import normalize_text

    open_tracks: list[dict] = []
    finished: list[LinkedTrack] = []
    counter = 0

    def _close(track: dict) -> None:
        samples = track["samples"]
        ious = track["ious"]
        finished.append(LinkedTrack(
            track_id=track["track_id"], text=track["text"],
            span_start_seconds=samples[0], span_end_seconds=samples[-1],
            sample_count=len(samples),
            mean_iou=(sum(ious) / len(ious) if ious else 0.0),
            changed=track["changed"]))

    for frame in frames:
        at = float(frame.get("at_seconds", 0.0))
        detections = frame.get("detections") or []
        claimed: set[int] = set()
        for det in detections:
            box = det.get("bbox") or [0, 0, 0, 0]
            text = str(det.get("text", ""))
            best, best_iou = None, 0.0
            for idx, track in enumerate(open_tracks):
                if idx in claimed:
                    continue
                iou = box_iou(track["box"], box)
                same = (normalize_text(track["text"])
                        == normalize_text(text))
                if same and iou >= iou_threshold and iou > best_iou:
                    best, best_iou = idx, iou
            if best is None:
                counter += 1
                open_tracks.append({
                    "track_id": f"text-{counter:04d}", "text": text,
                    "box": list(box), "samples": [at], "ious": [],
                    "changed": False})
                claimed.add(len(open_tracks) - 1)
            else:
                track = open_tracks[best]
                claimed.add(best)
                track["ious"].append(best_iou)
                track["box"] = list(box)
                track["samples"].append(at)
        # A track stays open when it was joined this sample, or when
        # the sample itself carried no detections (gap, not absence).
        # Anything idle on a non-empty sample closes here.
        still_open = []
        for idx, track in enumerate(open_tracks):
            if idx in claimed or not detections:
                still_open.append(track)
            else:
                _close(track)
        open_tracks = still_open
    for track in open_tracks:
        _close(track)
    return finished


def detect_changes(prev_detections: list, curr_detections: list,
                   at_seconds: float,
                   iou_threshold: float = 0.3) -> list[TextChangeEvent]:
    """Diff two consecutive samples into appeared/disappeared/changed/moved.

    Matching is greedy by IoU, mirroring the evaluator's box matching
    so benchmark numbers and runtime events agree on what "same box"
    means. A high-overlap pair with different normalized text is
    `changed`; a high-overlap pair with the same text but shifted
    beyond `iou_threshold` yet below 0.9 is `moved`.
    """
    from docs.benchmarks.ocr_eval import normalize_text

    candidates = []
    for li, label in enumerate(prev_detections):
        for pi, pred in enumerate(curr_detections):
            if "bbox" not in label or "bbox" not in pred:
                continue
            candidates.append(
                (box_iou(label["bbox"], pred["bbox"]), li, pi))
    used_prev: set[int] = set()
    used_curr: set[int] = set()
    pairs: list[tuple] = []
    for overlap, li, pi in sorted(candidates, reverse=True):
        if overlap < iou_threshold or li in used_prev or pi in used_curr:
            continue
        used_prev.add(li)
        used_curr.add(pi)
        pairs.append((overlap, li, pi))
    events: list[TextChangeEvent] = []
    for overlap, li, pi in pairs:
        before = str(prev_detections[li].get("text", ""))
        after = str(curr_detections[pi].get("text", ""))
        if normalize_text(before) != normalize_text(after):
            kind = "changed"
        elif overlap < 0.9:
            kind = "moved"
        else:
            continue
        events.append(TextChangeEvent(
            kind=kind, track_id=f"prev-{li:04d}-curr-{pi:04d}",
            text_before=before, text_after=after,
            at_seconds=float(at_seconds), iou=overlap))
    for li, label in enumerate(prev_detections):
        if li not in used_prev:
            events.append(TextChangeEvent(
                kind="disappeared", track_id=f"prev-{li:04d}",
                text_before=str(label.get("text", "")), text_after="",
                at_seconds=float(at_seconds), iou=0.0))
    for pi, pred in enumerate(curr_detections):
        if pi not in used_curr:
            events.append(TextChangeEvent(
                kind="appeared", track_id=f"curr-{pi:04d}",
                text_before="", text_after=str(pred.get("text", "")),
                at_seconds=float(at_seconds), iou=0.0))
    return events


@dataclass
class SkipPlan:
    """Which samples need OCR and which reuse the previous result."""

    run_index: list[int] = field(default_factory=list)
    skip_index: list[int] = field(default_factory=list)
    saved_passes: int = 0


def plan_skips(changed: list[bool]) -> SkipPlan:
    """Turn a per-sample changed/not-changed signal into a run/skip plan.

    The first sample always runs. A `True` entry runs; `False` reuses
    the previous sample's result. Pure bookkeeping, so the benchmark
    can report saved passes without re-running a recognizer.
    """
    plan = SkipPlan()
    for idx, flag in enumerate(changed):
        if idx == 0 or flag:
            plan.run_index.append(idx)
        else:
            plan.skip_index.append(idx)
    plan.saved_passes = len(plan.skip_index)
    return plan
