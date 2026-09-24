"""A title composited BEHIND the walking subject: under their matte.

The fidelity probe P2 (2026-09-24): `title_lockup` is in the roster and
SAM 2.1 held persons 10 of 10, but overlays only ever went above the
picture - no schema had "behind" - so a title the walker passes in
front of could not be planned, let alone built. The plan vocabulary now
carries it (`motion_graphics_vocabulary`'s `layer` axis,
`motion_graphics_plan.LAYER_BEHIND_SUBJECT`); this module is the other
two thirds:

1. **Grounding** - a behind_subject segment meets a real tracked
   subject. Step 1.06 segments only the clips such a segment plays
   over (the trigger is stated on the step), and compile reads those
   masks here. Anything that cannot be grounded REFUSES - a request
   for behind-subject placement with no usable matte raises
   `BehindSubjectRefused`, never silently draws on top.
 2. **The comp** - the existing Loader-matte path in Fusion,
    generalised from a grade to a graphic. Where `subject_grade` gates
    a BrightnessContrast through `EffectMask`, this merges the rendered
    title file over the picture and then merges the picture back over
    that through the matte: the title shows everywhere except where
    the subject is.

    Measured 2026-09-24 on Resolve Studio 21.1, the comp half does not
    render: a scripted Loader (`AddTool` + `SetMultiClip`, under
    `comp.Lock()`) never decodes on a timeline comp - seven file
    shapes (numbered PNG title sequence, single PNG, ProRes .mov,
    numbered PNG matte sequence, stable-path PNG sequence,
    media-pool-imported PNG, the timeline's own source .mp4) all read
    back 0x0 dimensions with an empty Format, and neither a stable
    path, a pool import, nor the Fusion page with a moved playhead
    populates them. The import half already strips the declared
    Loaders to unwired MediaIns, so a build that proceeded would ship
    either a delivery refusal or a black composite (both measured).
    `apply_behind_subject` therefore refuses every behind request up
    front, at compile time, before Resolve is ever touched - the
    grounding, comp and delivery halves below stay as the definition
    for a renderer that decodes, not as a path that runs today.

No taste lives here. The title file, its span and its pixels are the
plan's; the matte is measured. An absent layer on a plan entry reads
as above the picture (what every overlay has always done), never as
a quiet behind.
"""

from __future__ import annotations

import os
from typing import Any

from library.tools.fusion.effects import EffectBlock
from library.tools.fusion.nodes import FusionNode
from library.tools.ren_refusal import RenRefusal
from library.tools.subject_grade import (
    FACE_SEED_LABEL,
    UnknownSubjectObject,
    validate_matte,
    write_subject_matte,
)


class BehindSubjectRefused(RenRefusal):
    """A behind_subject placement with no usable matte to go under."""


#: Why `apply_behind_subject` refuses before grounding: the renderer
#: cannot deliver a file-backed Loader on this Resolve build, so no
#: behind request has a picture to go under. Kept as data (not prose
#: inside the raise) so the refusal and any future re-measurement
#: read the same sentence.
LOADER_DECODE_UNAVAILABLE = (
    "file-backed Fusion Loaders do not decode on timeline comps in "
    "Resolve Studio 21.1 (measured 2026-09-24: seven shapes - PNG "
    "sequence, single PNG, ProRes mov, matte PNG sequence, "
    "stable-path PNG, pool-imported PNG, the timeline's own source "
    "mp4 - all read back 0x0 with no Format after AddTool + "
    "SetMultiClip under comp.Lock(); a build that proceeded would "
    "refuse at delivery or render black, both measured) - so the "
    "title has no picture to go under on this renderer")


#: Effect-dict keys `build_effect_comp` reads for one behind-subject
#: composite. The title is the rendered overlay file; the trim puts
#: the comp's frame 0 on the right title frame; the matte is the
#: first frame of the per-played-window matte sequence the Loader
#: follows by numbering.
TITLE_MEDIA_KEY = "behind_title_media"
TITLE_TRIM_IN_KEY = "behind_title_trim_in"
TITLE_TRIM_OUT_KEY = "behind_title_trim_out"
MATTE_KEY = "behind_subject_matte"


#: The tool names the block builds. FIXED, not counter-suffixed: one
#: composite per clip is enforced (`apply_behind_subject` refuses a
#: second), so the names are unique within any comp - and the delivery
#: surgery (`deliver_loaders`, which re-attaches the file-backed
#: Loaders Resolve's importer strips) finds them by name. A counter
#: would make each run's names unfindable.
TITLE_LOADER_NAME = "BehindTitle"
TITLE_MERGE_NAME = "TitleOver"
MATTE_LOADER_NAME = "SubjectMatte"
SUBJECT_MERGE_NAME = "SubjectOver"


def behind_subject_block(title_file: str, matte_file: str, *,
                         title_trim_in: int = 0,
                         title_trim_out: int | None = None) -> EffectBlock:
    """Merge the title over the picture, then the picture back over that.

    The composite, in words: title everywhere, subject in front of it.
    `title_merge` draws the rendered overlay file over the upstream
    picture; `subject_merge` draws the upstream picture over that
    result, gated by the tracked matte through `EffectMask` - so where
    the subject is, the viewer sees them, and everywhere else the
    title.

    The block branches from `MediaIn1` by name, the way
    `fx.subject_backdrop` does: both the title's background and the
    subject's foreground ARE the source frame. It is therefore added
    FIRST in `build_effect_comp`, before any grade, zoom or vignette -
    those act on the composite, which is what makes the title sit IN
    the picture rather than pasted over the grade.

    `title_trim_in` is the title-file frame the comp's frame 0 shows
    (the clip starts mid-title); `title_trim_out` ends the Loader at
    the title's last frame over this clip, past which the Loader holds
    nothing and the picture shows alone. An all-absent title or matte
    draws nothing.
    """
    if not title_file or not matte_file:
        return EffectBlock(nodes=[], input_name="", output_name="")
    title_name = TITLE_LOADER_NAME
    merge_name = TITLE_MERGE_NAME
    matte_name = MATTE_LOADER_NAME
    over_name = SUBJECT_MERGE_NAME

    title = FusionNode(title_name, "Loader")
    title.add_clip(title_file)
    if title_trim_in:
        title.clips[0]["TrimIn"] = int(title_trim_in)
    if title_trim_out is not None:
        title.clips[0]["TrimOut"] = int(title_trim_out)
    title.pos = (220, 165)

    merge = FusionNode(merge_name, "Merge")
    merge.set_input("Foreground", title_name)
    merge.pos = (330, 55)

    matte = FusionNode(matte_name, "Loader")
    matte.add_clip(matte_file)
    matte.pos = (220, 110)

    over = FusionNode(over_name, "Merge")
    over.set_input("Background", merge_name)
    over.set_input("Foreground", "MediaIn1")
    over.set_input("EffectMask", matte_name, source="Mask")
    over.pos = (440, 0)

    return EffectBlock(nodes=[title, merge, matte, over],
                       input_name=merge_name,
                       input_key="Background",
                       output_name=over_name)


def block_from_effects(effects: dict) -> EffectBlock:
    """The `build_effect_comp` dispatch: effect keys to a composite.

    Absent title or matte draws nothing.
    """
    title = (effects or {}).get(TITLE_MEDIA_KEY)
    matte = (effects or {}).get(MATTE_KEY)
    if not title or not matte:
        return EffectBlock(nodes=[], input_name="", output_name="")
    trim_in = (effects or {}).get(TITLE_TRIM_IN_KEY, 0) or 0
    trim_out = (effects or {}).get(TITLE_TRIM_OUT_KEY)
    return behind_subject_block(
        title, matte, title_trim_in=int(trim_in),
        title_trim_out=(int(trim_out) if trim_out is not None else None))


# ── Delivery ───────────────────────────────────────────────────────
#
# `TimelineItem.ImportFusionComp` turns file-backed Loader nodes into
# MediaIn placeholders (measured on Resolve Studio 21.1: four file
# shapes, with and without FormatID - the node is always a MediaIn
# afterwards and the merge wirings to it go unwired). So the .comp
# text is the DECLARATION and the delivery re-attaches the files:
# AddTool a Loader, SetMultiClip its first frame, ConnectInput it to
# the merge - all inside comp.Lock()/Unlock(), which suppresses the
# file-browser dialog an unlocked AddTool opens on the operator's
# screen. `library/tools/execution/deliver_loaders.py` is that
# delivery; what follows is what it delivers, derived from the same
# effect keys the comp text was built from, so the two cannot disagree
# about which file goes where.

#: (merge tool, merge input, loader role, loader output). The title
#: rides Foreground's image output; the matte drives EffectMask from
#: the loader's Mask output.
DELIVERY_WIRING = (
    (TITLE_MERGE_NAME, "Foreground", "title", "Output"),
    (SUBJECT_MERGE_NAME, "EffectMask", "matte", "Mask"),
)


def loader_specs_from_effects(effects: dict) -> list[dict]:
    """The file-backed nodes the delivery must re-attach, in role order.

    Each spec carries the role (`title`/`matte`), the first frame's
    path, and the title trims the comp text declares. A matte plays
    its whole played window from frame 0, so it carries no trims.
    Empty where the effect keys are absent - nothing to deliver is not
    a refusal, it is a comp without this composite in it.
    """
    effects = effects or {}
    title = effects.get(TITLE_MEDIA_KEY)
    matte = effects.get(MATTE_KEY)
    if not title or not matte:
        return []
    return [
        {"role": "title", "first_frame": title,
         "trim_in": int(effects.get(TITLE_TRIM_IN_KEY, 0) or 0),
         "trim_out": (int(effects[TITLE_TRIM_OUT_KEY])
                      if effects.get(TITLE_TRIM_OUT_KEY) is not None
                      else None)},
        {"role": "matte", "first_frame": matte,
         "trim_in": 0, "trim_out": None},
    ]


def ground_segment(segment: dict, seg_result: dict | None, *,
                   clip_id: str) -> tuple[dict | None, dict | None]:
    """The grounding half: one behind segment meets a real tracked subject.

    `segment` is one `behind_subject_overlays` segment (overlay_path
    plus timeline span); `seg_result` is that clip's 1.06 segmentation
    JSON, as loaded. Returns `(grounded, None)` or `(None, refusal)` -
    and the refusal is terminal: `apply_behind_subject` raises it
    rather than collecting it, because a behind request that cannot be
    delivered must refuse the run, never draw on top.
    """
    label = str(segment.get("segment_id") or segment.get("index", "?"))
    if not seg_result:
        return None, {
            "reason": "no_segmentation",
            "detail": (
                f"Behind-subject segment {label} plays over clip "
                f"{clip_id}, and no 1.06 segmentation reached the "
                f"compile for it - so the subject resolves to nothing. "
                f"Not composited rather than composited on top."),
        }
    obj = next(
        (o for o in (seg_result or {}).get("objects", []) or []
         if o.get("label") == FACE_SEED_LABEL and o.get("masks_rle")),
        None)
    if obj is None:
        return None, {
            "reason": "subject_not_tracked",
            "detail": (
                f"Behind-subject segment {label} plays over clip "
                f"{clip_id}, and no {FACE_SEED_LABEL!r} object is "
                f"tracked there - so there is no subject matte to "
                f"composite under."),
        }
    return {**segment, "clip_id": clip_id,
            "object_id": obj["object_id"]}, None


def apply_behind_subject(segments: list[dict],
                         seg_by_clip: dict[str, dict | None], *,
                         clip_at: Any,
                         matte_dir: str,
                         timeline_fps: float,
                         clip_metadata: dict,
                         matte_stems: dict[str, str] | None = None,
                         ) -> tuple[dict[str, dict], list[dict]]:
    """Ground behind_subject segments and patch per-clip effects.

    `clip_at(start, end)` names the placed V1/V2 clips a span plays
    over - each as a dict with `label`, `clip_id`, `source_file`,
    `timeline_in`, `timeline_out` and `source_in`. `clip_metadata`
    carries source dimensions per clip_id for the matte-size refusal.

    Returns `(patch, mattes, per_segment)`: `patch` maps clip label
    to the effect keys `build_effect_comp` reads; `mattes` are the
    matte records, in segment order; `per_segment` records which clips
    each segment composited onto. Raises `BehindSubjectRefused` naming
    the first segment that cannot be delivered - no usable matte, no
    tracked subject, no clip under the span, a matte at the wrong
    size, or a title file disk does not have. A behind request is
    never dropped with a reason and never drawn on top: both would be
    a different placement wearing this one's name.

    Renderer gate: before any grounding, a non-empty request refuses
    with `LOADER_DECODE_UNAVAILABLE`. The grounding refusals below
    stay for a renderer that decodes; on this one every request
    refuses here, at compile time, rather than building a comp the
    delivery must refuse inside a Resolve run.
    """
    matte_stems = matte_stems or {}
    patch: dict[str, dict] = {}
    mattes: list[dict] = []
    per_segment: list[dict] = []

    def _refuse(detail: str) -> None:
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            detail,
            "Either segment a matte for the clip the span plays over "
            "(step 1.06 runs wherever a behind_subject plan names a "
            "clip - check its trigger record on the run), or re-plan "
            "the entry with layer above.")

    if segments:
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            LOADER_DECODE_UNAVAILABLE,
            "Re-plan the entry with layer above; the behind_subject "
            "vocabulary, the 1.06 matte trigger and the grounding "
            "below stay for a renderer with a measured decoding "
            "Loader delivery.")

    for segment in segments or []:
        label = str(segment.get("segment_id")
                    or segment.get("index", "?"))
        title_file = segment.get("overlay_path", "")
        hit_clips: list[str] = []
        if not title_file or not os.path.exists(title_file):
            _refuse(
                f"Behind-subject segment {label} names title file "
                f"{title_file!r} and disk does not have it - so there "
                f"is nothing to composite under the subject.")
        try:
            fps = float(timeline_fps)
        except (TypeError, ValueError):
            fps = 30.0
        start = float(segment.get("timeline_start", 0.0))
        end = float(segment.get("timeline_end", start))
        clips = clip_at(start, end) or []
        if not clips:
            _refuse(
                f"Behind-subject segment {label} spans {start}s to "
                f"{end}s and no placed picture clip covers that span - "
                f"so there is no picture to composite the title into.")
        for clip in clips:
            cid = clip.get("clip_id", "")
            grounded, refusal = ground_segment(
                segment, seg_by_clip.get(cid), clip_id=cid)
            if refusal is not None:
                _refuse(refusal["detail"])
            meta = (clip_metadata or {}).get(cid, {})
            width, height = meta.get("width"), meta.get("height")
            dur = max(int(round((clip.get("timeline_out", 0.0)
                                 - clip.get("timeline_in", 0.0)) * fps)),
                      0)
            if dur <= 0:
                _refuse(
                    f"Behind-subject segment {label} meets clip "
                    f"{cid} over no frames - so there is no played "
                    f"window to write a matte for.")
            seg = seg_by_clip.get(cid) or {}
            sample_fps = float(seg.get("sample_fps") or 2.0)
            stem = matte_stems.get(cid, f"{cid}_{clip.get('label')}")
            try:
                record = write_subject_matte(
                    seg, grounded["object_id"], matte_dir,
                    f"{stem}_behind",
                    timeline_fps=fps, played_frames=dur,
                    resolution=((height, width)
                                if width and height else None),
                    start_sample=int(float(
                        clip.get("source_in", 0.0)) * sample_fps))
            except UnknownSubjectObject as exc:
                _refuse(str(exc))
            errors = validate_matte(
                record, played_frames=dur,
                source_resolution=((height, width)
                                   if width and height else None))
            if errors:
                _refuse("; ".join(errors))
            mattes.append(record)
            # Which title frame the comp's frame 0 shows: the clip
            # starts this far into the title file.
            trim_in = max(0, int(round(
                (clip.get("timeline_in", 0.0) - start) * fps)))
            if clip.get("label", "") in patch:
                _refuse(
                    f"Behind-subject segment {label} meets clip "
                    f"{cid}, which already carries another "
                    f"behind_subject composite - one composite per "
                    f"clip is what the Fusion path draws.")
            eff = patch.setdefault(clip.get("label", ""), {})
            eff[TITLE_MEDIA_KEY] = title_file
            eff[TITLE_TRIM_IN_KEY] = trim_in
            eff[TITLE_TRIM_OUT_KEY] = trim_in + dur - 1
            eff[MATTE_KEY] = record["files"][0]
            hit_clips.append(cid)
        per_segment.append({
            "segment_id": label,
            "timeline_start": start,
            "timeline_end": end,
            "clips": hit_clips,
        })
    return patch, mattes, per_segment
