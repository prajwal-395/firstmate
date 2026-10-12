"""Subject-only colour grading: warm the speaker, leave the room.

Name, delineate, apply. The subject is named by the plan: the
colourist's entry cites the `region_exposure` row it was sourced from
(`<clip_id>/<region_track>`, step 5.01), and a region track M3d linked
to a face track IS a person. It is delineated on every played frame by
the delivery tier (`library/tools/person_matte.py`, Apple Vision person
instance masks); a clip with no single person instance keeps step
1.06's SAM 2.1 face-seeded subject, held at its 2 fps samples
(`write_subject_matte`).

It is APPLIED as a precomposite, not a Fusion comp. The first build
gated a BrightnessContrast with a Loader matte through `EffectMask`,
and a scripted Loader never decodes on a timeline comp in Resolve
Studio 21.1 (measured 2026-09-24, `library/tools/execution/
deliver_loaders.py`): the comp imported, the matte read back 0x0, and
the grade never reached the picture. `precomposite_subject_grade`
draws the graded subject itself - the clip's own source frames, graded
by the plan's numbers, alpha = the matte, premultiplied ``qtrle`` - and
the timeline build places it on the subject-grade row directly above
the picture rows (`timeline_layout.SUBJECT_GRADE`), with the clip's own
conform and CDL, so it registers on the pixels it replaces. Inside the
subject the viewer sees the graded copy; everywhere else the clip
under it. `docs/benchmarks/2026-10-12-person-matte-delivery.md` has the
exported frames that prove it on Resolve 21.1.

A plan entry looks like::

    {"clip_id": "clip_001",
     "target": {"kind": "person", "role": "speaker"},
     "scope": "subject-only",
     "grade": {"gain": 1.17, "saturation": 1.31}}

Three refusals, never completions (AGENTS.md 10.5): a target that is not
the face-seeded speaker is dropped - generic objects ("the laptop") wait
on the open-vocabulary grounding the report names as the genuine
capability gap; a scope that is not "subject-only" is dropped; a grade
with no non-neutral term is dropped as `no_readable_parameters`.

**No grade value lives here.** Neutral (gain 1.0, contrast 0.0,
saturation 1.0) is the absence of decoration, not taste, and is the only
number this module names; every other number arrives in the plan entry
and travels verbatim to the precomposite. There is no bound on a value,
for the same reason step 5.01 carries none: how far a grade may travel
is the colourist's. The graded pixel is clipped to the 8-bit carriage
the overlay is (`overlay_carriage`), which is the file format's limit,
not a choice about the grade.
"""

from __future__ import annotations

import json
import os
from typing import Any

import numpy as np

from library.tools.analysis.object_segmentation import (
    FACE_SEED_LABEL,
    decode_rle,
)
from library.tools.overlay_carriage import (
    OVERLAY_FORMAT_NAME,
    OverlayEncodeFailed,
    encode_verified_overlay,
)
from library.tools.person_matte import (
    DELIVERY_METHOD,
    SAM_ROUTE_REASONS,
    PersonMatteUnavailable,
)

#: The one region scope that exists. "Everything except X" is the same
#: plumbing with an inverted matte and arrives when a plan asks for it;
#: until then it is refused by name rather than half-drawn.
SUBJECT_ONLY_SCOPE = "subject-only"

#: The one target this feature can ground: the face-seeded tracked
#: subject. Anything else is the open-vocabulary gap, not a plumbing
#: gap, and is dropped with the reason saying so.
GROUNDED_TARGET_KIND = "person"
GROUNDED_TARGET_ROLE = "speaker"

#: Grade axes a subject grade may move, and the neutral of each. Neutral
#: means "this axis is not moved" - it is what an absent term reads as,
#: never a look.
GRADE_TERMS = ("gain", "contrast", "saturation")
NEUTRAL_GRADE = {"gain": 1.0, "contrast": 0.0, "saturation": 1.0}


class UnknownSubjectObject(ValueError):
    """The segmentation result holds no such object to write a matte from."""


def _drop(reason: str, detail: str) -> dict[str, str]:
    return {"reason": reason, "detail": detail}


def parse_plan_entry(entry: dict) -> tuple[dict | None, dict | None]:
    """The syntactic half: shape, scope, target kind, and readable values.

    Returns `(clean, None)` or `(None, drop)` where `drop` carries
    `reason`/`detail`. Grounding against real masks is `ground_entry` -
    this half needs no segmentation, so step 5.01 can carry the entry
    before any matte exists.
    """
    if not isinstance(entry, dict):
        return None, _drop("not_an_entry",
                           f"A subject grade entry must be an object, "
                           f"got {type(entry).__name__}.")
    clip_id = entry.get("clip_id")
    if not clip_id:
        return None, _drop("no_clip_named",
                           "A subject grade names no clip_id, so there is "
                           "no picture to gate the grade onto.")
    scope = entry.get("scope")
    if scope != SUBJECT_ONLY_SCOPE:
        return None, _drop(
            "unknown_scope",
            f"Clip {clip_id}: scope {scope!r} is not drawable - the one "
            f"region scope that exists is {SUBJECT_ONLY_SCOPE!r}.")
    target = entry.get("target") or {}
    if (target.get("kind") != GROUNDED_TARGET_KIND
            or target.get("role") != GROUNDED_TARGET_ROLE):
        label = target.get("label") or target.get("kind") or "nothing"
        return None, _drop(
            "open_vocabulary_target",
            f"Clip {clip_id}: target {label!r} cannot be grounded - only "
            f"the face-seeded speaker (kind {GROUNDED_TARGET_KIND!r}, role "
            f"{GROUNDED_TARGET_ROLE!r}) resolves to a tracked mask. "
            f"Generic objects wait on open-vocabulary grounding, which is "
            f"a capability gap, not a value to complete.")
    grade = entry.get("grade") or {}
    if not isinstance(grade, dict):
        return None, _drop("grade_not_an_object",
                           f"Clip {clip_id}: grade must be an object of "
                           f"BrightnessContrast terms.")
    unknown = [k for k in grade if k not in GRADE_TERMS]
    if unknown:
        return None, _drop(
            "unknown_grade_terms",
            f"Clip {clip_id}: grade terms {unknown} are not readable - "
            f"the drawable axes are {list(GRADE_TERMS)}.")
    try:
        values = {k: float(grade[k]) for k in grade}
    except (TypeError, ValueError):
        return None, _drop(
            "grade_terms_not_numbers",
            f"Clip {clip_id}: every grade term must be a number, got "
            f"{grade!r}.")
    if not any(values.get(k, NEUTRAL_GRADE[k]) != NEUTRAL_GRADE[k]
               for k in GRADE_TERMS):
        return None, _drop(
            "no_readable_parameters",
            f"Clip {clip_id}: every grade term is neutral, so there is "
            f"nothing to draw. Leaving the subject alone is said by not "
            f"naming it.")
    clean = {"clip_id": clip_id,
             "target": {"kind": GROUNDED_TARGET_KIND,
                        "role": GROUNDED_TARGET_ROLE},
             "scope": SUBJECT_ONLY_SCOPE,
             "grade": values}
    # The measured row the colourist cited (5.01 post-bridge,
    # `region_stats.resolve_measured_basis`), forwarded so the spec
    # records what the grade was sourced from. Grounding never reads it.
    for key in ("measured_basis", "measured_basis_absent_because"):
        if key in entry:
            clean[key] = entry[key]
    region = named_region_track(entry.get("measured_basis"))
    if region is not None:
        clean["region_track"] = region
    return clean, None


def named_region_track(basis: Any) -> str | None:
    """The person a cited `region_exposure` row names, as its M3d region
    track - or None when the row is unassigned (no face inside it, so
    nobody) or the entry cites nothing. This is how a colourist's plan
    names a PERSON rather than only a clip: the row it was sourced from
    is one person's region (`region_stats.exposure_rows`)."""
    if not isinstance(basis, dict):
        return None
    track = basis.get("region_track")
    owner = str(basis.get("owner") or "")
    if not track or not owner or owner.startswith("unassigned"):
        return None
    return str(track)


def _face_seeded_object(seg_result: dict) -> dict | None:
    for obj in (seg_result or {}).get("objects", []) or []:
        if obj.get("label") == FACE_SEED_LABEL and obj.get("masks_rle"):
            return obj
    return None


def ground_entry(clean: dict,
                 seg_result: dict | None) -> tuple[dict | None,
                                                      dict | None]:
    """The grounding half: the entry meets a real tracked subject.

    `seg_result` is one clip's 1.06 segmentation JSON, as loaded. An
    entry whose clip holds no face-seeded tracked object is dropped -
    the grade would have no matte, and a whole-frame grade in its place
    would be a different decision wearing this one's name.
    """
    clip_id = clean["clip_id"]
    if not seg_result:
        return None, _drop(
            "no_segmentation",
            f"Clip {clip_id}: no segmentation reached the compile, so "
            f"the speaker resolves to nothing. Not graded rather than "
            f"graded whole-frame.")
    obj = _face_seeded_object(seg_result)
    if obj is None:
        return None, _drop(
            "subject_not_tracked",
            f"Clip {clip_id}: no {FACE_SEED_LABEL!r} object is tracked, "
            f"so there is no subject matte to gate the grade onto.")
    return {**clean, "object_id": obj["object_id"]}, None


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill interior holes (SAM's CUDA hole-filling has no Apple build).

    Flood-fill the background from every border pixel; what is not
    reached and not foreground is a hole. Pure numpy, no cv2: the
    report's cheap fix, spelled plainly. Operates on 0/1 uint8.
    """
    binary = (mask > 0).astype(np.uint8)
    h, w = binary.shape
    background = 1 - binary
    reached = np.zeros_like(background, dtype=bool)
    stack = ([(0, x) for x in range(w)] + [(h - 1, x) for x in range(w)]
             + [(y, 0) for y in range(h)] + [(y, w - 1) for y in range(h)])
    reached[[y for y, _ in stack], [x for _, x in stack]] = (
        background[[y for y, _ in stack], [x for _, x in stack]] > 0)
    stack = [(y, x) for y, x in stack if reached[y, x]]
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if (0 <= ny < h and 0 <= nx < w and background[ny, nx]
                    and not reached[ny, nx]):
                reached[ny, nx] = True
                stack.append((ny, nx))
    holes = (background > 0) & ~reached
    out = binary.copy()
    out[holes] = 1
    return out


def write_subject_matte(seg_result: dict, object_id: str, out_dir: str,
                        stem: str, *, timeline_fps: float,
                        played_frames: int,
                        resolution: tuple | None = None,
                        start_sample: int = 0) -> dict[str, Any]:
    """Decode one tracked object into a timeline-rate matte PNG sequence.

    1.06 samples at 2 fps; a grade holds fine under a held matte (the
    report's per-use frame-rate policy), so each sampled mask is held
    over its whole span - no interpolation, no 15x bill. Frames past
    the last sample hold the last mask rather than going black.

    Args:
        seg_result: one clip's 1.06 result, as the JSON file carries it
            (`masks_rle` keyed by sample index as strings).
        object_id: which tracked object to write.
        out_dir: directory the PNGs and the provenance sidecar land in.
        stem: filename stem (`<stem>_subject_matte_%05d.png`).
        timeline_fps: the timeline rate the frames are held at.
        played_frames: how many timeline frames the played window needs.
        resolution: expected (height, width); the masks' own shape when
            None. A mismatch with the masks is refused - a matte at the
            wrong size would gate the wrong pixels.
        start_sample: first 1.06 sample the played window covers (the
            played window rarely starts at the source's frame 0).

    Returns a matte record: `files`, `frame_count`, `resolution`,
    `provenance` (which object, which samples, which rate).
    """
    objects = {o.get("object_id"): o
               for o in (seg_result or {}).get("objects", []) or []}
    obj = objects.get(object_id)
    if obj is None or not obj.get("masks_rle"):
        raise UnknownSubjectObject(
            f"No tracked object {object_id!r} with masks in this "
            f"segmentation result.")
    sample_fps = float(seg_result.get("sample_fps") or 2.0)
    shape = tuple(seg_result.get("resolution") or ())
    if len(shape) != 2 or shape[0] <= 0 or shape[1] <= 0:
        raise UnknownSubjectObject(
            f"Segmentation result carries no usable resolution: "
            f"{seg_result.get('resolution')!r}.")
    if resolution is not None and tuple(resolution) != tuple(shape):
        raise UnknownSubjectObject(
            f"Mask resolution {list(shape)} does not match the expected "
            f"source resolution {list(resolution)} - refusing rather "
            f"than gating the wrong pixels.")
    masks = {int(k): v for k, v in obj["masks_rle"].items()}
    samples = sorted(masks)
    if not samples:
        raise UnknownSubjectObject(
            f"Object {object_id!r} carries no sampled masks.")

    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    files = []
    held_from = []
    for t in range(played_frames):
        time_s = (start_sample / sample_fps) + (t / float(timeline_fps))
        idx = int(time_s * sample_fps)
        idx = min(max(idx, samples[0]), samples[-1])
        while idx not in masks and idx > samples[0]:
            idx -= 1
        mask = _fill_holes(decode_rle(masks[idx], shape))
        frame = (mask * 255).astype(np.uint8)
        path = os.path.join(out_dir, f"{stem}_subject_matte_{t:05d}.png")
        Image.fromarray(frame, mode="L").save(path)
        files.append(path)
        held_from.append(idx)

    record = {
        "clip_stem": stem,
        "object_id": object_id,
        "files": files,
        "frame_count": played_frames,
        "resolution": [int(shape[0]), int(shape[1])],
        "provenance": {
            "created_by": "subject_grade.write_subject_matte",
            "object_id": object_id,
            "object_label": obj.get("label"),
            "sample_fps": sample_fps,
            "timeline_fps": float(timeline_fps),
            "start_sample": start_sample,
            "held_samples": sorted(set(held_from)),
            "seed_note": seg_result.get("seed_note"),
        },
    }
    sidecar = os.path.join(out_dir, f"{stem}_subject_matte.json")
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in record.items() if k != "files"}
                  | {"file_count": len(files),
                     "first_file": files[0] if files else None,
                     "last_file": files[-1] if files else None},
                  f, indent=2)
    return record


def validate_matte(record: dict, *, played_frames: int | None = None,
                   source_resolution: tuple | None = None) -> list[str]:
    """A named matte must exist on disk and cover the played window.

    The compile-time refusal: an overlay segment the manifest names and
    disk does not have refuses the compile (AGENTS.md 10.2), and a
    subject matte is held to the same.
    """
    errors = []
    files = (record or {}).get("files", []) or []
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        errors.append(
            f"Subject matte for {record.get('clip_stem', '?')!r} names "
            f"{len(missing)} file(s) disk does not have (first: "
            f"{missing[0]}).")
    need = played_frames if played_frames is not None else record.get(
        "frame_count", 0)
    if len(files) < (need or 0):
        errors.append(
            f"Subject matte for {record.get('clip_stem', '?')!r} covers "
            f"{len(files)} frame(s) and the played window needs {need}.")
    if source_resolution is not None:
        have = tuple((record or {}).get("resolution") or ())
        if tuple(source_resolution) != have:
            errors.append(
                f"Subject matte resolution {list(have)} does not match "
                f"the source resolution {list(source_resolution)}.")
    return errors


def matte_route(record: dict) -> str:
    """Which route drew a matte record: `person_instance` (the delivery
    tier, every played frame) or `sam2_held` (step 1.06, 2 fps held)."""
    method = ((record or {}).get("provenance") or {}).get("method")
    return "person_instance" if method == DELIVERY_METHOD else "sam2_held"


def grade_rgb(rgb: np.ndarray, grade: dict) -> np.ndarray:
    """The plan's BrightnessContrast terms over float RGB in 0..1.

    In Fusion's order and meaning, so a value means what it meant when
    the colourist wrote it against the comp: gain multiplies, contrast
    scales about mid grey (``(x - 0.5) * (1 + contrast) + 0.5``),
    saturation pulls each pixel from its Rec.709 luma. An absent term is
    its neutral. Clipped to 0..1 last: the carriage is 8-bit.
    """
    gain = float(grade.get("gain", NEUTRAL_GRADE["gain"]))
    contrast = float(grade.get("contrast", NEUTRAL_GRADE["contrast"]))
    saturation = float(grade.get("saturation",
                                 NEUTRAL_GRADE["saturation"]))
    out = rgb * gain
    if contrast != 0.0:
        out = (out - 0.5) * (1.0 + contrast) + 0.5
    if saturation != 1.0:
        luma = (out @ np.array([0.2126, 0.7152, 0.0722],
                               dtype=out.dtype))[..., None]
        out = luma + (out - luma) * saturation
    return np.clip(out, 0.0, 1.0)


class SubjectGradeRefused(ValueError):
    """A grounded subject grade whose precomposite could not be made."""


def precomposite_subject_grade(source_file: str, matte_files: list[str],
                               out_path: str, *, source_in: float,
                               timeline_fps: float, grade: dict,
                               label: str = "?",
                               timeout: int = 1200) -> dict:
    """The graded subject over one played window, as a ``qtrle`` overlay.

    Per played frame: the clip's own source frame (decoded at its own
    size, by index - `person_matte.played_source_frames`, the frame the
    build places), graded by `grade_rgb`, with alpha = the matte and RGB
    premultiplied. Outside the matte the overlay is exact zeros, so the
    picture under it comes through untouched; inside it is the graded
    copy of the same pixels. Streamed to the encoder frame by frame and
    verified by round trip (`overlay_carriage.encode_verified_overlay`).

    Returns `{"overlay_path", "frame_count", "width", "height"}`; raises
    `SubjectGradeRefused` naming the window.
    """
    import tempfile

    from PIL import Image

    from library.tools import person_matte

    try:
        probe = person_matte.probe_video(source_file)
    except PersonMatteUnavailable as exc:
        raise SubjectGradeRefused(f"{label}: {exc.detail}") from exc
    width, height = probe["width"], probe["height"]
    shown = person_matte.played_source_frames(
        source_in, len(matte_files), timeline_fps, probe["fps"])
    with tempfile.TemporaryDirectory(prefix="ren-subject-grade-") as tmp:
        try:
            paths = person_matte.decode_frames(
                source_file, shown[0], shown[-1] - shown[0] + 1, probe, tmp)
        except PersonMatteUnavailable as exc:
            raise SubjectGradeRefused(f"{label}: {exc.detail}") from exc

        def frames():
            for matte_path, index in zip(matte_files, shown):
                rgb = np.asarray(Image.open(paths[index - shown[0]])
                                 .convert("RGB"), dtype=np.float32) / 255.0
                matte = np.asarray(Image.open(matte_path).convert("L"),
                                   dtype=np.uint32)
                if matte.shape != rgb.shape[:2]:
                    raise SubjectGradeRefused(
                        f"{label}: matte {matte.shape[1]}x{matte.shape[0]} "
                        f"against source {rgb.shape[1]}x{rgb.shape[0]} - "
                        f"the matte would gate the wrong pixels")
                graded = np.rint(grade_rgb(rgb, grade) * 255.0
                                 ).astype(np.uint32)
                frame = np.empty((height, width, 4), dtype=np.uint8)
                frame[..., :3] = ((graded * matte[..., None] + 127) // 255
                                  ).astype(np.uint8)
                frame[..., 3] = matte.astype(np.uint8)
                yield frame.tobytes()

        try:
            encoded = encode_verified_overlay(
                frames(), width, height, timeline_fps, out_path,
                timeout=timeout)
        except OverlayEncodeFailed as exc:
            raise SubjectGradeRefused(
                f"{label}: the precomposite did not verify "
                f"({exc.stage}: {exc.detail})") from exc
    return {"overlay_path": out_path,
            "frame_count": encoded["frame_count"],
            "width": width, "height": height}


#: The layer a subject-grade precomposite carries, and the manifest key
#: the build places from.
SUBJECT_GRADE_LAYER = "subject_grade"
OVERLAY_KEY = "subject_grade_overlay"


def apply_subject_grades(entries: list[dict], seg: dict | None, *,
                         placement: dict,
                         windows: list[tuple[float, float]],
                         matte_dir: str, precomp_dir: str,
                         timeline_fps: float,
                         source_resolution: tuple | None = None,
                         person_matte: Any = None,
                         ) -> tuple[list[dict], list[dict], list[dict]]:
    """Ground one placement's subject grades and precomposite each window.

    `entries` are the plan entries naming the placement's clip;
    `placement` is the placed clip (`label`, `clip_id`, `source_file`,
    `timeline_in`, `source_in`); `windows` are the timeline spans
    `[start, end)` where that clip is the picture on top - compile cuts
    away the spans a clip on a higher picture row covers, because a
    subject drawn there would draw over the cutaway. `seg` is the clip's
    1.06 segmentation, and `person_matte` the delivery tier's callback
    (`behind_subject.apply_behind_subject` documents its shape).

    Returns `(segments, drops, mattes)`: overlay segments for the build's
    subject-grade row, drops with reason/detail for every entry that did
    not survive, and the matte records. Never a whole-frame grade in a
    subject's place.
    """
    cid = placement["clip_id"]
    stem = f"{cid}_{placement.get('label', '')}"
    segments: list[dict] = []
    drops: list[dict] = []
    mattes: list[dict] = []

    def _drop(reason, detail):
        drops.append({"clip_id": cid, "reason": reason, "detail": detail})

    for e_index, entry in enumerate(entries or []):
        clean, drop = parse_plan_entry(entry)
        if drop is not None:
            _drop(drop["reason"], drop["detail"])
            continue
        if not windows:
            _drop("no_played_window",
                  f"Clip {cid}: no frame of placement "
                  f"{placement.get('label')!r} is the picture on top, so "
                  f"there is no subject to show graded.")
            continue
        # One entry is one decision: a window that cannot be drawn
        # drops the whole entry, never half of it.
        entry_segments: list[dict] = []
        entry_mattes: list[dict] = []
        failed = False
        for w_index, (start, end) in enumerate(windows):
            frames = max(round((end - start) * timeline_fps), 0)
            if frames <= 0:
                continue
            window_in = (float(placement.get("source_in", 0.0))
                         + start - float(placement.get("timeline_in", 0.0)))
            window_stem = f"{stem}_g{e_index}_w{w_index}"
            record = None
            region = clean.get("region_track")
            if person_matte is not None:
                try:
                    record = person_matte(
                        placement, stem=window_stem, source_in=window_in,
                        played_frames=frames, resolution=source_resolution,
                        region_track=region)
                except PersonMatteUnavailable as exc:
                    if region is not None or (
                            exc.reason not in SAM_ROUTE_REASONS):
                        _drop("person_not_drawable",
                              f"Clip {cid}: "
                              f"{region or 'the subject'} cannot be drawn "
                              f"frame by frame ({exc.reason}: "
                              f"{exc.detail}). Not graded rather than "
                              f"graded on someone else.")
                        failed = True
                        break
            if record is None:
                grounded, drop = ground_entry(clean, seg)
                if drop is not None:
                    _drop(drop["reason"], drop["detail"])
                    failed = True
                    break
                sample_fps = float((seg or {}).get("sample_fps") or 2.0)
                try:
                    record = write_subject_matte(
                        seg, grounded["object_id"], matte_dir, window_stem,
                        timeline_fps=timeline_fps, played_frames=frames,
                        resolution=source_resolution,
                        start_sample=int(window_in * sample_fps))
                except UnknownSubjectObject as exc:
                    _drop("matte_refused", str(exc))
                    failed = True
                    break
            errors = validate_matte(record, played_frames=frames,
                                    source_resolution=source_resolution)
            if errors:
                _drop("matte_invalid", "; ".join(errors))
                failed = True
                break
            out_path = os.path.join(precomp_dir,
                                    f"{window_stem}_subject_grade.mov")
            try:
                precomposite_subject_grade(
                    placement["source_file"], record["files"], out_path,
                    source_in=window_in, timeline_fps=timeline_fps,
                    grade=clean["grade"], label=f"Clip {cid} {window_stem}")
            except SubjectGradeRefused as exc:
                _drop("precomposite_refused", str(exc))
                failed = True
                break
            entry_mattes.append(record)
            entry_segments.append({
                "segment_id": window_stem,
                "layer": SUBJECT_GRADE_LAYER,
                "clip_id": cid,
                "clip_label": placement.get("label", ""),
                "source_file": placement["source_file"],
                "overlay_path": out_path,
                "timeline_start": float(start),
                "timeline_end": float(end),
                "total_frames": frames,
                "format": OVERLAY_FORMAT_NAME,
                "has_alpha": True,
                "fps": float(timeline_fps),
                "grade": dict(clean["grade"]),
                "matte_route": matte_route(record),
                "region_track": record.get("region_track"),
            })
        if not failed:
            segments.extend(entry_segments)
            mattes.extend(entry_mattes)
    return segments, drops, mattes
