"""Subject-only colour grading: warm the speaker, leave the room.

The smallest build that proves the region-specification path the scout
measured (`data/vep-region-granular-editing/report.md` in the firstmate
home): name (face-seeded "speaker"), delineate+track (step 1.06 SAM 2.1
masks, already measured), apply (a Fusion Loader matte gating a
BrightnessContrast through `EffectMask` - the vignette's proven pattern
in `library/tools/fusion/effects.py`, generalised from a generated shape
to a loaded matte).

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
and travels verbatim to the comp. There is no clamp and no bound, for
the same reason step 5.01 carries none: how far a grade may travel is
the colourist's.
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
from library.tools.fusion.effects import EffectBlock
from library.tools.fusion.nodes import FusionNode

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
    return clean, None


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


_counter = {"n": 0}


def subject_grade_block(matte_file: str, *, gain: float = 1.0,
                        contrast: float = 0.0,
                        saturation: float = 1.0) -> EffectBlock:
    """A Loader matte gating a BrightnessContrast through `EffectMask`.

    The vignette's wiring generalised: where the vignette feeds a
    generated EllipseMask into `EffectMask`, this feeds a tracked matte
    sequence through a Loader. All three grade values arrive from the
    plan entry; an all-neutral grade draws nothing.
    """
    if (gain == 1.0 and contrast == 0.0 and saturation == 1.0):
        return EffectBlock(nodes=[], input_name="", output_name="")
    _counter["n"] += 1
    loader_name = f"SubjectMatte{_counter['n']}"
    grade_name = f"SubjectGrade{_counter['n']}"

    loader = FusionNode(loader_name, "Loader")
    loader.add_clip(matte_file)
    loader.pos = (220, 110)

    grade = FusionNode(grade_name, "BrightnessContrast")
    grade.set_input("Gain", float(gain))
    grade.set_input("Contrast", float(contrast))
    grade.set_input("Saturation", float(saturation))
    grade.set_input("EffectMask", loader_name, source="Mask")
    grade.pos = (330, 0)

    return EffectBlock(nodes=[loader, grade],
                       input_name=grade_name,
                       output_name=grade_name)


#: Effect-dict keys `build_effect_comp` reads for a subject grade. The
#: matte is the first frame's path; the Loader follows the numbered
#: sequence from it.
MATTE_KEY = "subject_grade_matte"
GRADE_KEYS = {"subject_grade_gain": "gain",
              "subject_grade_contrast": "contrast",
              "subject_grade_saturation": "saturation"}


def block_from_effects(effects: dict) -> EffectBlock:
    """The `build_effect_comp` dispatch: effect keys to a masked grade.

    Absent matte key, or an all-neutral grade, draws nothing.
    """
    matte = (effects or {}).get(MATTE_KEY)
    if not matte:
        return EffectBlock(nodes=[], input_name="", output_name="")
    values = {}
    for key, term in GRADE_KEYS.items():
        if key in effects:
            values[term] = effects[key]
    return subject_grade_block(
        matte,
        gain=values.get("gain", 1.0),
        contrast=values.get("contrast", 0.0),
        saturation=values.get("saturation", 1.0))


def apply_subject_grades(entries: list[dict],
                         seg_by_clip: dict[str, dict], *,
                         matte_dir: str,
                         timeline_fps: float = 30.0,
                         played_frames: dict[str, int] | None = None,
                         source_resolution: dict[str, tuple] | None = None,
                         start_samples: dict[str, int] | None = None,
                         matte_stems: dict[str, str] | None = None
                         ) -> tuple[dict[str, dict], list[dict], list[dict]]:
    """Ground plan entries, write their mattes, patch per-clip effects.

    Returns `(patch, drops, mattes)`: `patch` maps clip_id to the
    effect keys `build_effect_comp` reads; `drops` carry reason/detail
    for every entry that did not survive; `mattes` are the matte
    records, in entry order. An entry that cannot be delivered is
    dropped with the reason - never completed, never graded
    whole-frame in its place.

    `matte_stems` names the file stem per clip_id (default the
    clip_id): a clip placed twice needs two mattes over two different
    played windows, so the caller distinguishes them here.
    """
    played_frames = played_frames or {}
    source_resolution = source_resolution or {}
    start_samples = start_samples or {}
    matte_stems = matte_stems or {}
    patch: dict[str, dict] = {}
    drops: list[dict] = []
    mattes: list[dict] = []

    def _refuse(clip_id, reason, detail):
        drops.append({"clip_id": clip_id, "reason": reason,
                      "detail": detail})

    for entry in entries or []:
        clean, drop = parse_plan_entry(entry)
        if drop is not None:
            _refuse((entry or {}).get("clip_id", "?"),
                    drop["reason"], drop["detail"])
            continue
        clip_id = clean["clip_id"]
        grounded, drop = ground_entry(clean, seg_by_clip.get(clip_id))
        if drop is not None:
            _refuse(clip_id, drop["reason"], drop["detail"])
            continue
        need = played_frames.get(clip_id, 0)
        if need <= 0:
            _refuse(clip_id, "no_played_window",
                    f"Clip {clip_id}: the played window covers no "
                    f"frames, so there is no matte to write.")
            continue
        try:
            record = write_subject_matte(
                seg_by_clip[clip_id], grounded["object_id"], matte_dir,
                matte_stems.get(clip_id, clip_id),
                timeline_fps=timeline_fps, played_frames=need,
                resolution=source_resolution.get(clip_id),
                start_sample=start_samples.get(clip_id, 0))
        except UnknownSubjectObject as exc:
            _refuse(clip_id, "matte_refused", str(exc))
            continue
        errors = validate_matte(
            record, played_frames=need,
            source_resolution=source_resolution.get(clip_id))
        if errors:
            _refuse(clip_id, "matte_invalid", "; ".join(errors))
            continue
        mattes.append(record)
        eff = {MATTE_KEY: record["files"][0]}
        for key, term in GRADE_KEYS.items():
            if term in grounded["grade"]:
                eff[key] = grounded["grade"][term]
        patch[clip_id] = eff
    return patch, drops, mattes
