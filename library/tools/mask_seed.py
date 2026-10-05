"""Seeding a subject mask: the Ren-side seed point.

Magic Mask 2 (the Resolve Hands plan's first end-to-end proof) needs one
thing from Ren before any click happens: a point on the subject, in the
source frame's pixels, that is guaranteed to land inside the subject's
silhouette - not merely the centre of its bounding box, which on a
concave silhouette (an L-shaped body, a raised arm) falls on background.

Three pieces, in the order the driver lane uses them:

1. `seed_point_in_mask` - the interior point of one SAM 2 mask. The
   centre of the largest inscribed circle (the argmax of the Euclidean
   distance transform): inside the mask for ANY shape, deterministic on
   ties, and never the bbox centre.
2. `source_to_viewer` / `viewer_to_source` - the aspect-fit geometry
   between a source pixel and the point on the Resolve viewer where that
   pixel is drawn, letterbox, pillarbox or exact fit, Retina scale factor
   as an input. The viewer image rect is an INPUT here; measuring it on
   screen belongs to the driver lane.
3. `compute_seed` - the typed `MaskSeed` record (clip, frame, subject,
   source point, viewer point) a receipt can carry, resolved from Ren's
   existing stores: the 1.06 SAM 2 segmentation (`object_segmentation`)
   and the M3b person roster (`person_entity`). No new detector. The
   record carries BOTH the requested frame and `seed_frame` - the source
   frame of the sample's own timestamp, nearest to the request - so the
   executor parks the playhead where the mask was actually measured.

Refusals are named (`MaskSeedRefused`), never completions: a subject
that is absent or ambiguous on the frame is reported, not guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from library.tools.analysis.object_segmentation import decode_rle
from library.tools.ren_refusal import RenRefusal


class MaskSeedRefused(RenRefusal):
    """No seed point could be determined for the named subject.

    `what` is the stable named reason (`no_segmentation`,
    `unknown_subject`, `subject_absent_on_frame`, `subject_not_tracked`,
    `ambiguous_subject`, `empty_mask`); `why` carries the detail.
    """


@dataclass(frozen=True)
class Point:
    """A 2D point. Source points are pixels; viewer points are floats."""

    x: float
    y: float


@dataclass(frozen=True)
class ImageRect:
    """Where the aspect-fitted source image is drawn inside the viewer.

    In viewer coordinates (points on macOS, as Accessibility reports
    them). The driver lane measures this rect on screen; the seed module
    only consumes it.
    """

    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class MaskSeed:
    """One seed: where the subject is, in source pixels and on the viewer.

    The receipt shape the Resolve Hands plan records (`seed
    source=(x,y) viewer=(x,y)`), plus the provenance that says which
    tracked object and which segmentation sample answered.

    `frame` is the frame the caller asked about; `seed_frame` is the
    frame the seed is actually valid for - the source frame of the
    sample's own timestamp. The executor parks the playhead on
    `seed_frame` before clicking: the mask was measured there, and on a
    moving subject the requested frame's point may be background.
    """

    clip_id: str
    frame: int
    subject: str
    source: Point
    viewer: Point
    object_id: str
    sample_index: int
    seed_frame: int

    def to_receipt(self) -> dict:
        """The receipt a UI transaction can record verbatim."""
        return {
            "clip_id": self.clip_id,
            "frame": self.frame,
            "seed_frame": self.seed_frame,
            "subject": self.subject,
            "source": {"x": self.source.x, "y": self.source.y},
            "viewer": {"x": self.viewer.x, "y": self.viewer.y},
            "object_id": self.object_id,
            "sample_index": self.sample_index,
        }


# ── The viewer geometry ──────────────────────────────────────────


def fit_image_rect(
    source_width: float, source_height: float, viewer_width: float, viewer_height: float
) -> ImageRect:
    """The aspect-fit image rect: the source's aspect inside the viewer.

    `min(viewer_w/src_w, viewer_h/src_h)` - the largest scale at which
    the whole source is visible, centred. A viewer wider than the
    source's aspect letterboxes (bars top and bottom); a viewer taller
    pillarboxes (bars left and right); matching aspects fit exactly.
    """
    scale = min(viewer_width / source_width, viewer_height / source_height)
    image_w = source_width * scale
    image_h = source_height * scale
    return ImageRect(
        left=(viewer_width - image_w) / 2.0,
        top=(viewer_height - image_h) / 2.0,
        width=image_w,
        height=image_h,
    )


def source_to_viewer(
    source_point: Point,
    source_size: tuple[float, float],
    image_rect: ImageRect,
    scale_factor: float = 1.0,
) -> Point:
    """A source pixel -> the viewer screen point where it is drawn.

    `image_rect` is in viewer points; `scale_factor` is device pixels
    per point (1.0 for a non-Retina display, 2.0 for Retina), so the
    answer is in device pixels - the space a screenshot is measured in.
    The driver clicks at `point / scale_factor`.
    """
    src_w, src_h = source_size
    vx = image_rect.left + (source_point.x / src_w) * image_rect.width
    vy = image_rect.top + (source_point.y / src_h) * image_rect.height
    return Point(vx * scale_factor, vy * scale_factor)


def viewer_to_source(
    viewer_point: Point,
    source_size: tuple[float, float],
    image_rect: ImageRect,
    scale_factor: float = 1.0,
) -> Point:
    """The inverse: a viewer screen point -> the source pixel under it."""
    src_w, src_h = source_size
    vx = viewer_point.x / scale_factor
    vy = viewer_point.y / scale_factor
    sx = (vx - image_rect.left) * src_w / image_rect.width
    sy = (vy - image_rect.top) * src_h / image_rect.height
    return Point(sx, sy)


# ── The seed point inside one mask ───────────────────────────────


def seed_point_in_mask(mask: np.ndarray) -> Point:
    """The interior seed point of one binary mask.

    The centre of the largest inscribed circle: the foreground pixel
    farthest from the background (the argmax of the Euclidean distance
    transform). Inside the mask for ANY shape - including concave ones
    whose bounding-box centre falls outside, such as an L-shape's notch.
    Deterministic: ties resolve to the first pixel in row-major order,
    so the same mask always yields the same seed.
    """
    binary = np.asarray(mask) > 0
    if not binary.any():
        raise MaskSeedRefused(
            "empty_mask",
            "The mask at this frame covers no pixels, so there is no "
            "interior point to seed.",
            "Choose a frame where the subject is actually tracked, or "
            "re-run 1.06 object segmentation on this clip.",
        )
    from scipy import ndimage

    dist = ndimage.distance_transform_edt(binary)
    y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return Point(float(x), float(y))


# ── Frame and sample mapping ─────────────────────────────────────


def frame_to_sample(frame: int, source_fps: float, sample_fps: float) -> int:
    """A source frame number -> the nearest segmentation sample.

    The segmenter samples the source at `sample_fps` from frame 0, so a
    source frame sits at `frame / source_fps` seconds and the nearest
    sample is `round(time * sample_fps)`. NEAREST, not the floor: a
    floored sample can be a full sample period (0.5 s at 2 fps) older
    than the frame the executor clicks on, and a moving subject is gone
    from that point. Round half up, so the midpoint picks deterministically.
    """
    return int((frame / source_fps) * sample_fps + 0.5)


# ── Subject resolution against Ren's existing stores ─────────────


def load_segmentation(project_folder: str, clip_id: str) -> dict | None:
    """One clip's 1.06 segmentation JSON, or None when absent.

    The file lives in THAT step's directory, resolved through the
    layout - never composed by hand.
    """
    import json

    from library.tools.project_layout import ProjectLayout

    path = (
        ProjectLayout(project_folder).step_dir("object_segmentation")
        / f"{clip_id}_segmentation.json"
    )
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _mask_shape(seg_result: dict) -> tuple[int, int]:
    """The masks' (height, width), as the segmentation result carries it."""
    shape = tuple(seg_result.get("resolution") or ())
    if len(shape) != 2 or shape[0] <= 0 or shape[1] <= 0:
        raise MaskSeedRefused(
            "no_segmentation",
            f"Segmentation result carries no usable resolution: "
            f"{seg_result.get('resolution')!r}, so no mask can be decoded.",
            "Re-run 1.06 object segmentation on this clip.",
        )
    return (int(shape[0]), int(shape[1]))


def _decode_at(
    obj: dict, sample_index: int, shape: tuple[int, int], *, clip_id: str, subject: str
) -> tuple[np.ndarray | None, int | None]:
    """One object's mask at one sample, held to the store's own policy.

    1.06 samples at 2 fps and a track may skip a sample whose mask came
    out empty, so a sample inside the track's span is held back to the
    nearest earlier sample that exists - the same hold
    `write_subject_matte` applies. A sample OUTSIDE the span is not
    held: the subject was not tracked there, and seeding on a mask from
    a different part of the clip would be a stale point wearing this
    frame's name.

    Returns `(mask, the sample it actually came from)` - which, after a
    hold-back, is not the sample asked for - or `(None, None)` when the
    track covers no sample near the frame.
    """
    masks = {int(k): v for k, v in (obj.get("masks_rle") or {}).items()}
    if not masks:
        return None, None
    if sample_index < min(masks) or sample_index > max(masks):
        return None, None
    idx = sample_index
    while idx not in masks and idx > min(masks):
        idx -= 1
    return decode_rle(masks[idx], shape), idx


def _object_mask_at(
    obj: dict, sample_index: int, shape: tuple[int, int], *, clip_id: str, subject: str
) -> tuple[str, np.ndarray, int]:
    """The mask of the object a detection id names, or a named refusal."""
    mask, used = _decode_at(obj, sample_index, shape, clip_id=clip_id, subject=subject)
    if mask is None:
        raise MaskSeedRefused(
            "subject_absent_on_frame",
            f"Object {subject!r} is tracked on clip {clip_id} but carries "
            f"no mask at sample {sample_index} - the subject is not on "
            f"this frame.",
            "Choose a frame inside the subject's tracked span.",
        )
    if not mask.any():
        raise MaskSeedRefused(
            "empty_mask",
            f"Object {subject!r} on clip {clip_id} has an empty mask at "
            f"sample {sample_index}, so there is no interior point to "
            f"seed.",
            "Choose a frame where the subject's mask has pixels.",
        )
    return obj["object_id"], mask, used


def _person_mask_at(
    person: dict,
    objects: dict,
    shape: tuple[int, int],
    *,
    clip_id: str,
    sample_index: int,
    frame_time: float,
) -> tuple[str, np.ndarray, int]:
    """The mask of the tracked subject whose silhouette holds a person's
    face, or a named refusal.

    The person store (M3b) records face spans per clip in source pixels;
    the 1.06 segmentation tracks the subject those faces seed. The bridge
    is geometric, not a label: the person's face centre at this frame
    must fall inside a tracked object's mask. Exactly one object may
    contain it - none means the tracked subject is someone else, more
    than one means the frame cannot decide between them.

    Returns `(object_id, mask, the sample the mask actually came from)`.
    """
    person_id = person.get("person_id", "?")
    spans = [
        s
        for s in person.get("face_spans", [])
        if isinstance(s, dict)
        and s.get("clip_id") == clip_id
        and s.get("start", 0.0) <= frame_time <= s.get("end", 0.0)
    ]
    if not spans:
        raise MaskSeedRefused(
            "subject_absent_on_frame",
            f"Person {person_id!r} has no face detection on clip "
            f"{clip_id} at {frame_time:.3f}s, so the subject is not on "
            f"this frame.",
            "Choose a frame where the person's face is detected.",
        )
    if len(spans) > 1:
        raise MaskSeedRefused(
            "ambiguous_subject",
            f"Person {person_id!r} has {len(spans)} face spans covering "
            f"clip {clip_id} at {frame_time:.3f}s, so the frame cannot "
            f"decide which detection is theirs.",
            "Disambiguate the person store, or seed by detection id.",
        )
    box = spans[0].get("box") or []
    if len(box) != 4:
        raise MaskSeedRefused(
            "subject_not_tracked",
            f"Person {person_id!r}'s face span on clip {clip_id} at "
            f"{frame_time:.3f}s carries no usable box, so the face cannot "
            f"be placed inside a mask.",
            "Rebuild the M3b identity store for this source.",
        )
    cx = (float(box[0]) + float(box[2])) / 2.0
    cy = (float(box[1]) + float(box[3])) / 2.0
    h, w = shape
    candidates = []
    for obj in objects.values():
        mask, used = _decode_at(
            obj, sample_index, shape, clip_id=clip_id, subject=obj.get("object_id", "?")
        )
        if mask is None:
            continue
        if not (0 <= int(cy) < h and 0 <= int(cx) < w):
            continue
        if mask[int(cy), int(cx)] > 0:
            candidates.append((obj, mask, used))
    if not candidates:
        raise MaskSeedRefused(
            "subject_not_tracked",
            f"Person {person_id!r}'s face at ({cx:.0f}, {cy:.0f}) on "
            f"clip {clip_id} falls inside no tracked subject's mask - "
            f"the tracked subject is a different person (or no one).",
            "Seed the tracked subject by its detection id, or segment "
            "the clip with this person's face as the seed.",
        )
    if len(candidates) > 1:
        raise MaskSeedRefused(
            "ambiguous_subject",
            f"Person {person_id!r}'s face at ({cx:.0f}, {cy:.0f}) on "
            f"clip {clip_id} falls inside {len(candidates)} tracked "
            f"objects' masks, so the frame cannot decide which subject "
            f"is theirs.",
            "Seed one of the candidate objects by its detection id.",
        )
    obj, mask, used = candidates[0]
    return obj["object_id"], mask, used


def resolve_subject_mask(
    seg_result: dict,
    subject: str,
    *,
    clip_id: str,
    sample_index: int,
    frame_time: float,
    project_folder: str | None = None,
    person_root=None,
) -> tuple[str, np.ndarray, int]:
    """The tracked object, its mask, and the sample the mask came from
    for one subject reference - or a named refusal.

    A detection id (the segmentation's `object_id`, e.g. `obj_1`)
    answers directly. A person identity (a `person_id` or declared name
    from the M3b roster) answers through the face-silhouette bridge in
    `_person_mask_at`. Anything that matches neither store is refused by
    name, never guessed.
    """
    objects = {
        o.get("object_id"): o
        for o in (seg_result or {}).get("objects", []) or []
        if isinstance(o, dict) and o.get("object_id")
    }
    if subject in objects:
        return _object_mask_at(
            objects[subject],
            sample_index,
            _mask_shape(seg_result),
            clip_id=clip_id,
            subject=subject,
        )
    if project_folder:
        from library.tools.person_entity import find_person

        person = find_person(project_folder, subject, person_root)
        if person is not None:
            return _person_mask_at(
                person,
                objects,
                _mask_shape(seg_result),
                clip_id=clip_id,
                sample_index=sample_index,
                frame_time=frame_time,
            )
        roster_note = "and no person in the project roster matches it"
    else:
        roster_note = (
            "and the person roster was not consulted - no project folder was given"
        )
    raise MaskSeedRefused(
        "unknown_subject",
        f"Subject {subject!r} matches no tracked object in clip "
        f"{clip_id}'s segmentation, {roster_note} - so there is no mask "
        f"to seed from.",
        "Name a detection id (object_id) from the clip's 1.06 "
        "segmentation, or a person_id / declared name from the "
        "project's person roster.",
    )


# ── The seed ────────────────────────────────────────────────────


def compute_seed(
    *,
    project_folder: str,
    clip_id: str,
    frame: int,
    subject: str,
    image_rect: ImageRect,
    source_fps: float,
    scale_factor: float = 1.0,
    seg_result: dict | None = None,
    person_root=None,
) -> MaskSeed:
    """The seed for one subject on one frame: source point + viewer point.

    `frame` is a source frame number at `source_fps`; `image_rect` is the
    viewer's measured image rect (the driver lane's measurement, an input
    here). `seg_result` may carry an already-loaded 1.06 segmentation
    JSON; otherwise it is read from the project's own steps directory.
    """
    if seg_result is None:
        seg_result = load_segmentation(project_folder, clip_id)
    if not seg_result:
        raise MaskSeedRefused(
            "no_segmentation",
            f"Clip {clip_id} has no 1.06 segmentation in this project, so "
            f"the subject resolves to nothing.",
            "Run 1.06 object segmentation on the clip (a subject grade "
            "or behind-subject plan triggers it), then retry the seed.",
        )
    sample_fps_raw = seg_result.get("sample_fps")
    if not sample_fps_raw or float(sample_fps_raw) <= 0:
        raise MaskSeedRefused(
            "no_segmentation",
            f"Clip {clip_id}'s segmentation carries no usable "
            f"sample_fps ({sample_fps_raw!r}), so no source frame can be "
            f"placed on the sample grid.",
            "Re-run 1.06 object segmentation on the clip.",
        )
    sample_fps = float(sample_fps_raw)
    sample_index = frame_to_sample(frame, source_fps, sample_fps)
    object_id, mask, seed_sample = resolve_subject_mask(
        seg_result,
        subject,
        clip_id=clip_id,
        sample_index=sample_index,
        frame_time=frame / source_fps,
        project_folder=project_folder,
        person_root=person_root,
    )
    source = seed_point_in_mask(mask)
    shape = _mask_shape(seg_result)
    seed_frame = int((seed_sample / sample_fps) * source_fps + 0.5)
    viewer = source_to_viewer(source, (shape[1], shape[0]), image_rect, scale_factor)
    return MaskSeed(
        clip_id=clip_id,
        frame=frame,
        subject=subject,
        source=source,
        viewer=viewer,
        object_id=object_id,
        sample_index=seed_sample,
        seed_frame=seed_frame,
    )
