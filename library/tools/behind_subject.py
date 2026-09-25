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
2. **The precomposite** - the rendered title multiplied by the
   INVERTED tracked matte, frame by frame, into a premultiplied
   ``qtrle`` overlay that Resolve places as a normal timeline clip on
   the overlay row above the picture. Where the subject is, the
   overlay's alpha is zero, so the viewer sees them; everywhere else
   the title. No Fusion, no Loaders.

   Measured 2026-09-24 on Resolve Studio 21.1, the earlier
   Loader-matte comp half did not render: a scripted Loader
   (`AddTool` + `SetMultiClip`, under `comp.Lock()`) never decodes on
   a timeline comp - seven file shapes all read back 0x0 dimensions
   with an empty Format. The native-routes scout that followed proved
   route A by render instead: per frame ``overlay_alpha = title_alpha
   x (1 - subject_matte)``, premultiplied, carried as ``qtrle``
   through `overlay_carriage.py`, placed on a track above the
   untouched picture with pool attributes Alpha mode Premultiplied
   and Data Level Full. The rendered frame shows the subject
   occluding the title, and the transparent region measured mean 1.6
   of 255 (no data-level defect). This module is that route, shipped:
   the comp and delivery halves below are the precomposite, not the
   Loader path, which is removed.

No taste lives here. The title file, its span and its pixels are the
plan's; the matte is measured. An absent layer on a plan entry reads
as above the picture (what every overlay has always done), never as
a quiet behind.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from library.tools.overlay_carriage import (
    OVERLAY_ENCODE_ARGS,
    OVERLAY_FORMAT_NAME,
    probe_overlay,
)
from library.tools.ren_refusal import RenRefusal
from library.tools.subject_grade import (
    FACE_SEED_LABEL,
    UnknownSubjectObject,
    validate_matte,
    write_subject_matte,
)


class BehindSubjectRefused(RenRefusal):
    """A behind_subject placement with no usable matte to go under."""


#: The layer value a precomposed behind title carries back into the
#: motion-graphics overlay list, so the placed clip traces to the
#: plan entry that asked for it.
PRECOMPOSED_LAYER = "behind_subject"


#: Per-clip effect keys that move the picture's pixels. A behind
#: precomp punches its holes at the source pixels, so any of these on
#: the same clip misregisters the title: the subject moves and the
#: holes do not. Photometric keys (grade, glow, grain, vignette) are
#: deliberately absent - they do not move pixels. Native transitions
#: are absent too: a dissolve blends but does not move, and wipes or
#: pushes under a behind span are a follow-up, not this refusal.
GEOMETRIC_EFFECT_KEYS = frozenset({
    "zoom_start", "zoom_mid", "zoom_end", "pan_start", "pan_end",
    "backdrop_picture_scale", "backdrop_picture_center_x",
    "backdrop_scale", "backdrop_center_x",
    "tv_power_head", "tv_power_tail",
})

#: Native speed ops that re-time the picture. The matte is written per
#: timeline frame, so a retimed clip shows a different source frame
#: under each hole - the same misregistration in time rather than in
#: space. A frozen frame holds one source still while the holes keep
#: moving, which misregisters the same way.
RETIMING_EFFECT_TYPES = frozenset({"speed_ramp", "freeze_frame"})


def assert_no_geometric_overlap(behind_by_label: dict,
                                *, per_clip_effects=None,
                                stabilized_labels=(),
                                speed_ops=()) -> None:
    """Refuse a behind composite that shares its clip with a moving picture.

    `behind_by_label` maps clip label to `[(segment_id,
    timeline_start, timeline_end)]` - the placed precomp windows.
    `per_clip_effects` maps the same labels to their Fusion effect
    keys; `stabilized_labels` are labels carrying a plan-requested
    stabilize; `speed_ops` are native speed entries with `label`,
    `effect_type`, `timeline_start` and `timeline_end`.

    Raises `BehindSubjectRefused` naming the segment, the clip and
    the effect - a misaligned title must refuse, never build.
    """
    effects = per_clip_effects or {}
    stabilized = set(stabilized_labels or ())
    for label, windows in (behind_by_label or {}).items():
        if not windows:
            continue
        keys = set((effects.get(label) or {}))
        hit = sorted(keys & GEOMETRIC_EFFECT_KEYS)
        if label in stabilized:
            hit = [*hit, "stabilize"]
        if hit:
            seg = str(windows[0][0])
            raise BehindSubjectRefused(
                "a behind_subject title would misregister",
                f"Behind-subject segment {seg} plays over clip "
                f"{label!r}, which also carries {hit[0]!r} - a "
                f"picture-moving effect under a precomposited title "
                f"moves the subject out from under its punched holes. "
                f"Not built rather than built misaligned.",
                "Re-plan the entry with layer above, or lift the "
                "geometric effect off the clip the behind span plays "
                "over.")
        for op in speed_ops or []:
            if (op.get("label") != label
                    or op.get("effect_type") not in RETIMING_EFFECT_TYPES):
                continue
            op_start = float(op.get("timeline_start", 0.0))
            op_end = float(op.get("timeline_end", op_start))
            for seg_id, start, end in windows:
                if op_start < float(end) and float(start) < op_end:
                    raise BehindSubjectRefused(
                        "a behind_subject title would misregister",
                        f"Behind-subject segment {seg_id} plays over "
                        f"clip {label!r}, which also carries "
                        f"{op.get('effect_type')!r} across "
                        f"{op_start}s to {op_end}s - a retimed clip "
                        f"shows a different source frame under each "
                        f"punched hole. Not built rather than built "
                        f"misaligned.",
                        "Re-plan the entry with layer above, or lift "
                        "the retime off the clip the behind span plays "
                        "over.")


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


def precomposite_title_under_matte(
        title_frames: list, matte_files: list[str], out_path: str, *,
        fps: float, segment_id: str = "?", clip_id: str = "?",
        timeout: int = 600) -> dict:
    """One title window multiplied by its inverted matte, as ``qtrle``.

    Per frame, ``overlay_alpha = title_alpha x (1 - subject_matte)``
    with premultiplied RGB - the scout's route A, which rendered and
    measured correct on Resolve Studio 21.1 (transparent region mean
    1.6 of 255, title lift where ink is). `title_frames` are RGBA
    arrays (or paths to RGBA PNGs) in play order, already sliced to
    the window; `matte_files` are the `write_subject_matte` L-mode
    PNGs for the same window (255 where the subject is).

    The frames travel to ffmpeg as raw RGBA on stdin - no intermediate
    sequence on disk - encoded with `OVERLAY_ENCODE_ARGS`, the same
    carriage every overlay this engine writes. The encode is verified
    by round-trip, not by return code alone: the ``qtrle`` file is
    decoded back and must match the computed bytes exactly (the codec
    is lossless over 8-bit RGBA, so anything else is a broken
    encode), and the probe must read it as an alpha-carrying
    ``qtrle`` clip - which is what `apply_clip_attributes` needs to
    set Full + Premultiplied at placement time.

    Returns `{"overlay_path", "frame_count", "width", "height"}`.
    Raises `BehindSubjectRefused` naming the segment where the title
    and the matte do not meet (count or size mismatch), and where the
    encode does not verify - a behind request is never drawn on top,
    and never drawn half-punched either.
    """
    import numpy as np
    from PIL import Image

    label = f"segment {segment_id} over clip {clip_id}"
    if len(title_frames) != len(matte_files) or not title_frames:
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            f"Behind-subject {label}: {len(title_frames)} title frame(s) "
            f"meet {len(matte_files)} matte frame(s) - so there is no "
            f"complete window to precomposite.",
            "Either segment a matte for the clip the span plays over "
            "(step 1.06 runs wherever a behind_subject plan names a "
            "clip - check its trigger record on the run), or re-plan "
            "the entry with layer above.")

    computed: list[bytes] = []
    width = height = 0
    for i, (title, matte_path) in enumerate(
            zip(title_frames, matte_files)):
        title_arr = (np.asarray(Image.open(title).convert("RGBA"))
                     if isinstance(title, str)
                     else np.asarray(title, dtype=np.uint8))
        if title_arr.ndim != 3 or title_arr.shape[2] != 4:
            raise BehindSubjectRefused(
                "a behind_subject title has no usable matte",
                f"Behind-subject {label}: title frame {i} is not an "
                f"RGBA picture - so there is nothing to punch the "
                f"matte through.",
                "Re-render the title segment (step 4.06), or re-plan "
                "the entry with layer above.")
        try:
            matte_arr = np.asarray(
                Image.open(matte_path).convert("L"), dtype=np.uint8)
        except (OSError, ValueError) as exc:
            raise BehindSubjectRefused(
                "a behind_subject title has no usable matte",
                f"Behind-subject {label}: matte frame {i} "
                f"({matte_path!r}) would not read ({exc}) - so the "
                f"title has no subject to go under.",
                "Either segment a matte for the clip the span plays "
                "over, or re-plan the entry with layer above.") from exc
        if matte_arr.shape != title_arr.shape[:2]:
            raise BehindSubjectRefused(
                "a behind_subject title has no usable matte",
                f"Behind-subject {label}: title frame {i} is "
                f"{title_arr.shape[1]}x{title_arr.shape[0]} and matte "
                f"frame {i} is {matte_arr.shape[1]}x{matte_arr.shape[0]} "
                f"- so the matte would gate the wrong pixels. Not "
                f"rescaled rather than rescaled in silence.",
                "Re-render the title at the delivery frame, or re-plan "
                "the entry with layer above.")
        if i == 0:
            height, width = (int(title_arr.shape[0]),
                             int(title_arr.shape[1]))
        title_f = title_arr.astype(np.uint32)
        matte_f = matte_arr.astype(np.uint32)
        # The punch: zero alpha (hence premultiplied black) wherever
        # the subject is, the title's own pixels everywhere else.
        # Exact zeros where nothing draws - integer arithmetic keeps
        # 0 x anything at 0, which is what the transparent-region
        # check measures.
        out_a = (title_f[..., 3] * (255 - matte_f) + 127) // 255
        out_rgb = (title_f[..., :3] * out_a[..., None] + 127) // 255
        frame = np.empty_like(title_arr)
        frame[..., :3] = out_rgb.astype(np.uint8)
        frame[..., 3] = out_a.astype(np.uint8)
        computed.append(frame.tobytes())

    os.makedirs(os.path.dirname(os.path.abspath(out_path)),
                exist_ok=True)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "rawvideo", "-pix_fmt", "rgba",
             "-s", f"{width}x{height}", "-framerate", str(float(fps)),
             "-i", "-",
             *OVERLAY_ENCODE_ARGS, out_path],
            input=b"".join(computed),
            capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            f"Behind-subject {label}: the precomposite encode would "
            f"not run ({exc}).",
            "Re-run the compile; if it refuses again, re-plan the "
            "entry with layer above.") from exc
    if proc.returncode != 0 or not os.path.isfile(out_path):
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            f"Behind-subject {label}: the precomposite encode failed "
            f"({(proc.stderr or b'').decode('utf-8', 'replace').strip()[-300:]}) "
            f"- so there is no overlay to place.",
            "Re-run the compile; if it refuses again, re-plan the "
            "entry with layer above.")

    # The verify half: lossless carriage, so the file must decode to
    # the computed bytes exactly - judged by what the encode WROTE,
    # never by its return code. A clip whose alpha did not survive
    # would place as an opaque title over the subject.
    fields = probe_overlay(out_path)
    if (fields.get("codec_name", "").lower() != "qtrle"
            or "argb" not in (fields.get("pix_fmt", "") or "").lower()):
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            f"Behind-subject {label}: the precomposite probes as "
            f"{fields.get('codec_name', '?')!r}/"
            f"{fields.get('pix_fmt', '?')!r}, not qtrle/argb - so "
            f"Resolve would not read it as a premultiplied overlay.",
            "Re-run the compile; if it refuses again, re-plan the "
            "entry with layer above.")
    try:
        back = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", out_path,
             "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
            capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            f"Behind-subject {label}: the precomposite would not "
            f"decode back ({exc}).",
            "Re-run the compile; if it refuses again, re-plan the "
            "entry with layer above.") from exc
    if (back.returncode != 0
            or (back.stdout or b"") != b"".join(computed)):
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise BehindSubjectRefused(
            "a behind_subject title has no usable matte",
            f"Behind-subject {label}: the precomposite does not "
            f"decode to the computed pixels - so placing it would "
            f"draw a different title than the plan rendered.",
            "Re-run the compile; if it refuses again, re-plan the "
            "entry with layer above.")
    return {"overlay_path": out_path, "frame_count": len(computed),
            "width": width, "height": height}


def _title_window(segment: dict, trim_in: int, dur: int) -> list[str]:
    """The title PNGs this window plays, in order, or a refusal reason.

    Step 4.06 sequences each behind title into numbered PNGs and
    records the pattern on `sequence`; the window starts `trim_in`
    frames into the title and needs `dur`. Returns `(paths, None)` or
    `(None, detail)`.
    """
    label = str(segment.get("segment_id") or segment.get("index", "?"))
    seq = segment.get("sequence") or {}
    pattern = seq.get("pattern", "")
    have = int(seq.get("frame_count", 0)
               or segment.get("total_frames", 0) or 0)
    if not pattern:
        first = segment.get("overlay_path", "")
        return None, (
            f"Behind-subject segment {label} names title file "
            f"{first!r} with no frame sequence - so the window "
            f"cannot be read frame by frame.")
    paths = [pattern % (trim_in + i) for i in range(dur)]
    if trim_in < 0 or trim_in + dur > have:
        return None, (
            f"Behind-subject segment {label} needs title frames "
            f"{trim_in} to {trim_in + dur - 1} and the rendered title "
            f"holds {have} - so the title runs dry mid-span.")
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return None, (
            f"Behind-subject segment {label} names title frame "
            f"{missing[0]!r} and disk does not have it - so there is "
            f"nothing to composite under the subject.")
    return paths, None


def apply_behind_subject(segments: list[dict],
                         seg_by_clip: dict[str, dict | None], *,
                         clip_at: Any,
                         matte_dir: str,
                         precomp_dir: str,
                         timeline_fps: float,
                         clip_metadata: dict,
                         matte_stems: dict[str, str] | None = None,
                         ) -> tuple[list[dict], list[dict], list[dict]]:
    """Ground behind_subject segments and precomposite each under its matte.

    `clip_at(start, end)` names the placed V1/V2 clips a span plays
    over - each as a dict with `label`, `clip_id`, `source_file`,
    `timeline_in`, `timeline_out` and `source_in`. `clip_metadata`
    carries source dimensions per clip_id for the matte-size refusal.

    Returns `(placed, mattes, per_segment)`: `placed` are
    motion-graphics-overlay-shaped segment dicts pointing at the
    precomposed ``qtrle`` files (full canvas, `layer`
    behind_subject) for compile to append to the overlay list, where
    the timeline build places them as normal clips on the overlay
    rows above the picture; `mattes` are the matte records, in
    segment order; `per_segment` records which clips each segment
    composited onto and which precomp each produced. Raises
    `BehindSubjectRefused` naming the first segment that cannot be
    delivered - no usable matte, no tracked subject, no clip under
    the span, a matte at the wrong size, a title disk does not have,
    or a precomposite that does not verify. A behind request is
    never dropped with a reason and never drawn on top: both would be
    a different placement wearing this one's name.
    """
    matte_stems = matte_stems or {}
    placed: list[dict] = []
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

    for seg_index, segment in enumerate(segments or []):
        label = str(segment.get("segment_id")
                    or segment.get("index", "?"))
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
        seg_precomps: list[dict] = []
        hit_clips: list[str] = []
        for clip in clips:
            cid = clip.get("clip_id", "")
            grounded, refusal = ground_segment(
                segment, seg_by_clip.get(cid), clip_id=cid)
            if refusal is not None:
                _refuse(refusal["detail"])
            meta = (clip_metadata or {}).get(cid, {})
            width, height = meta.get("width"), meta.get("height")
            overlap_in = max(float(clip.get("timeline_in", 0.0)), start)
            overlap_out = min(float(clip.get("timeline_out", 0.0)), end)
            dur = max(int(round((overlap_out - overlap_in) * fps)), 0)
            if dur <= 0:
                _refuse(
                    f"Behind-subject segment {label} meets clip "
                    f"{cid} over no frames - so there is no played "
                    f"window to write a matte for.")
            seg = seg_by_clip.get(cid) or {}
            sample_fps = float(seg.get("sample_fps") or 2.0)
            clip_label = str(clip.get("label", ""))
            # One window, one matte: the stem carries the placement
            # label and the segment, so two behind titles over one
            # clip - or one source placed twice - punch their own
            # holes instead of sharing (and overwriting) one file.
            stem = matte_stems.get(
                (cid, clip_label),
                matte_stems.get(cid, f"{cid}_{clip_label}"))
            window_stem = f"{stem}_behind_s{seg_index}"
            clip_offset = max(0.0, overlap_in
                              - float(clip.get("timeline_in", 0.0)))
            try:
                record = write_subject_matte(
                    seg, grounded["object_id"], matte_dir,
                    window_stem,
                    timeline_fps=fps, played_frames=dur,
                    resolution=((height, width)
                                if width and height else None),
                    start_sample=int(float(
                        clip.get("source_in", 0.0) + clip_offset)
                        * sample_fps))
            except UnknownSubjectObject as exc:
                _refuse(str(exc))
            errors = validate_matte(
                record, played_frames=dur,
                source_resolution=((height, width)
                                   if width and height else None))
            if errors:
                _refuse("; ".join(errors))
            mattes.append(record)
            # Which title frame the window's first frame shows: the
            # overlap starts this far into the title file.
            trim_in = max(0, int(round((overlap_in - start) * fps)))
            title_paths, title_refusal = _title_window(
                segment, trim_in, dur)
            if title_refusal is not None:
                _refuse(title_refusal)
            precomp_path = os.path.join(
                precomp_dir, f"{window_stem}_precomp.mov")
            precomposite_title_under_matte(
                title_paths, record["files"], precomp_path,
                fps=fps, segment_id=label, clip_id=cid)
            placed.append({
                "segment_id": label,
                "layer": PRECOMPOSED_LAYER,
                "overlay_path": precomp_path,
                "timeline_start": overlap_in,
                "timeline_end": overlap_out,
                "total_frames": dur,
                "format": OVERLAY_FORMAT_NAME,
                "has_alpha": True,
                "fps": fps,
            })
            seg_precomps.append({
                "clip_id": cid,
                "label": clip_label,
                "overlay_path": precomp_path,
                "timeline_start": overlap_in,
                "timeline_end": overlap_out,
            })
            hit_clips.append(cid)
        per_segment.append({
            "segment_id": label,
            "timeline_start": start,
            "timeline_end": end,
            "clips": hit_clips,
            "precomps": seg_precomps,
        })
    return placed, mattes, per_segment
