#!/usr/bin/env python3
"""Step 5.01's measurements, and the spec they are assembled into.

Shared by `bridge.py` (which measures and puts the numbers in front of a
colourist) and `post_bridge.py` (which turns the colourist's answer into
the ASC CDL the renderer applies).  Neither is a second copy of the
other: the bridge measures once, and the measurement travels to the
post-bridge as a bridge output the way every other hybrid step's table
does.

**No look is defined here and none is defined in the engine at all** -
`library/tools/series_look.py` reads a declaration and holds no values of
its own.  A project whose template declares nothing gets no LOOK.

**What it does now get is a colourist.**  The exposure half used to
measure every clip and then normalise only against an `exposure_reference`
only a brand template can declare, so project 001 - which names no
template - had nine clips measured across a 2.7x luma spread and the
IDENTITY CDL written on all nine.  `library/tools/color_correction.py` is
where that decision now lives and why it is not `exposure_reference`
wearing a new hat.  The declared-reference path is unchanged and still
runs: a template that declares one still normalises onto it, and the
colourist's correction composes underneath.

Classification: Hybrid / Creative Selection
"""
import json
import math
import os
import subprocess
import sys

from library.tools.color_correction import (
    ASSESSMENT_FIELD,
    basis_record,
    compose_cdl,
    describe_correction,
    planning_basis,
)
from library.tools.series_look import (
    NEUTRAL_CDL,
    describe_look,
    resolve_look,
)
from library.tools.project_layout import ProjectLayout
from library.tools.semantic_index import build_semantic_lookup


# The designed grade, as five nodes. Only the SHAPE lives here, and the
# values live nowhere in this repository: each node names the brand
# template slot that declares it, so a template's own numbers move the
# picture without touching this step and a template that declares
# nothing leaves the node undrawn.
GRADE_PIPELINE = {
    "node_1": {
        "type": "color_space_transform",
        "from": "camera",
        "to": "davinci_wide_gamut_intermediate",
    },
    "node_2": {
        "type": "declared_look_primary",
        "carries": ["slope", "offset", "power", "saturation"],
        "source": "brand template style.series_look.cdl",
    },
    "node_3": {
        "type": "tonal_shaping",
        "carries": ["pivot_contrast"],
        "source": "brand template style.series_look.contrast",
    },
    "node_4": {
        "type": "creative_film_look",
        "carries": ["glow", "grain", "vignette"],
        "source": "brand template style.series_look.{glow,grain,vignette}",
    },
    "node_5": {
        "type": "color_space_transform",
        "to": "rec709_gamma24",
    },
}


#: What actually happens to each designed node. A node with no
#: `delivered_by` does not reach the viewer, and the reason says why -
#: four of the five used to be silently unread.
GRADE_PIPELINE_DELIVERY = {
    "node_1": {
        "delivered_by": None,
        "reason": "A camera to DaVinci Wide Gamut transform is project-level "
                  "colour management, not a per-clip grade. Setting it would "
                  "change the colour science of the whole project.",
    },
    "node_2": {
        "delivered_by": "per_clip_adjustments[].cdl_values",
        "reason": "The declared look's hue and level, as an ASC CDL: slope carries "
                  "the highlight tint, offset the shadow tint and the black "
                  "floor, power the midtones, plus one saturation term. "
                  "The colourist's own per-clip correction is composed into "
                  "the same four terms, in the exact serial order "
                  "library/tools/color_correction.py states, so the renderer "
                  "still applies ONE CDL. Applied with TimelineItem.SetCDL.",
    },
    "node_3": {
        "delivered_by": "fusion_look.grade_contrast",
        "reason": "A CDL has no contrast term - slope/offset/power cannot "
                  "pivot around mid grey. Fusion's BrightnessContrast can, "
                  "so a declared contrast is delivered there.",
    },
    "node_4": {
        "delivered_by": "fusion_look",
        "reason": "Glow, grain and the vignette are Fusion nodes the comp "
                  "engine already draws (fx.glow / fx.grain / fx.vignette). "
                  "A coloured vignette is the clearest thing a CDL cannot "
                  "express at all: it shapes falloff, not values.",
    },
    "node_5": {
        "delivered_by": None,
        "reason": "See node_1: the Rec.709 output transform is project-level "
                  "colour management. output_color_space records the intent.",
    },
}

#: How the luma is sampled. Recorded on every clip beside the number, so
#: a reader can tell a measurement from an absence (AGENTS.md 10.3).
LUMA_METHOD = "ffprobe_signalstats_yavg"

#: What a clip carries when nothing measured it. NOT 0.0: an unmeasured
#: exposure and a perfectly-exposed one are different facts, and reading
#: one as the other is how ten of ten clips on project 001 reported
#: "Exposure within normal range, no adjustment needed" about a
#: measurement that never completed.
LUMA_UNMEASURED = "unmeasured"

#: Where the sampling stops. Both are MECHANICAL - how much of a file to
#: read and how often, not how the picture should look - and both are
#: stated so a reader knows what the number covers.
_LUMA_SAMPLE_EVERY_NTH_FRAME = 5
_LUMA_SAMPLE_SECONDS = 10

#: What the measurement covers, in the words the prompt uses. Stated in
#: one place because the colourist has to know what the number is not.
LUMA_SCOPE = (
    f"average frame luma (0-255), sampled every "
    f"{_LUMA_SAMPLE_EVERY_NTH_FRAME}th frame of the first "
    f"{_LUMA_SAMPLE_SECONDS}s of the source file"
)


def measure_luma(filepath: str) -> dict:
    """Measure a clip's average luma, or say plainly that nothing did.

    ffprobe's `signalstats` YAVG over every Nth frame of the head of the
    file. This is a MEASUREMENT of the footage; it decides nothing about
    how the footage should look.

    Returns:
        A record carrying `luma` (0-255, or None), `method`
        (`LUMA_METHOD` or `LUMA_UNMEASURED`), `samples` and, when the
        measurement did not happen, `reason`.

    A failure is REPORTED, never returned as a number. The version this
    replaced returned 0.0 for "measured, and it is fine" and for "ffprobe
    is not installed", "the file is missing", "the probe timed out" and
    "not one line parsed" alike - and it was the last of those that fired,
    on every clip of every run: `csv=p=0` emits `140.914,` with a trailing
    separator, `float()` raised on all of them, and the empty list became
    0.0. On project 001 the real spread was 0.72x to 1.41x of gain and the
    step reported one identical CDL on 10 of 10 clips.
    """
    unmeasured = {"luma": None, "method": LUMA_UNMEASURED, "samples": 0}

    if not filepath:
        return {**unmeasured, "reason": "no source file on the entry"}
    if not os.path.exists(filepath):
        return {**unmeasured, "reason": f"source file not on disk: {filepath}"}

    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet',
             '-f', 'lavfi',
             '-i', f'movie={filepath},'
                   f'select=not(mod(n\\,{_LUMA_SAMPLE_EVERY_NTH_FRAME})),'
                   f'signalstats',
             '-show_entries', 'frame_tags=lavfi.signalstats.YAVG',
             '-of', 'csv=p=0',
             '-read_intervals', f'%+{_LUMA_SAMPLE_SECONDS}',
             ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        return {**unmeasured, "reason": "ffprobe timed out after 30s"}
    except FileNotFoundError:
        return {**unmeasured, "reason": "ffprobe is not on PATH"}
    except OSError as exc:
        return {**unmeasured, "reason": f"ffprobe could not run: {exc}"}

    if result.returncode != 0:
        return {**unmeasured,
                "reason": f"ffprobe exited {result.returncode}"}

    values = []
    for line in result.stdout.splitlines():
        # `-of csv=p=0` writes one field plus its separator, so every
        # line arrives as `140.914,`. Stripping the separator is the
        # whole of the fix; float() rejected all of them before.
        cell = line.strip().strip(',').strip()
        if not cell:
            continue
        try:
            values.append(float(cell))
        except ValueError:
            continue

    if not values:
        return {**unmeasured,
                "reason": "ffprobe returned no parseable YAVG samples"}

    return {
        "luma": round(sum(values) / len(values), 3),
        "method": LUMA_METHOD,
        "samples": len(values),
    }


def exposure_gain_for(luma, reference):
    """The slope multiplier that moves `luma` onto `reference`, in stops.

    `log2(reference / luma)` is the definition of a stop, so nothing here
    is chosen: given a reference somebody declared, the offset follows.

    There is no clamp and no maximum. A bound on how far normalisation
    may push is a creative decision about how much the engine is allowed
    to overrule the footage, and the constant that used to sit here
    (0.5 stops) was picked by nobody. A template that declares an
    `exposure_reference` owns what it does to every clip, and every
    resulting gain is written into `per_clip_adjustments` so it is
    visible before the render.

    Returns:
        `(offset_in_stops, gain)`, or `(None, 1.0)` when there is no
        reference to normalise onto or nothing was measured.
    """
    if reference is None or luma is None:
        return None, 1.0
    if luma <= 0 or reference <= 0:
        return None, 1.0
    offset = math.log2(reference / luma)
    return round(offset, 4), round(2.0 ** offset, 4)


def stops_between(a, b):
    """`log2(b / a)`, or None where either end was not measured.

    The gap between two shots, in the unit exposure is discussed in. Not
    a verdict: whether a gap is a fault or the content is the colourist's
    (library/tools/color_correction.py).
    """
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return round(math.log2(b / a), 3)


def _extract_frame(video_path: str) -> str:
    """Extract a single frame from video for color analysis.

    Returns "" when the capture did not happen - including a zero-byte
    file, which ffmpeg can leave behind with a zero exit (see
    `marker_capture`'s "WHEN THE ROUTE FAILS").  The caller skips such
    clips rather than matching them to an identity CDL.
    """
    import tempfile
    if not os.path.exists(video_path):
        return ""
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-v', 'quiet', '-i', video_path, '-vframes', '1', '-q:v', '2', path],
            check=True
        )
        if not os.path.exists(path) or os.path.getsize(path) <= 0:
            raise ValueError(f"ffmpeg left no frame at {path}")
        return path
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        return ""


def collect_entries(input_data: dict) -> list:
    """Every V1 and V2 placement, in the order it plays.

    One entry per PLACEMENT, not per clip: a clip used twice is two
    entries, because what sits either side of a cut is a placement and
    the neighbour table is built off this order.
    """
    entries = []

    for assignment in input_data.get("a_roll_assignments", []):
        start = assignment.get("timeline_start")
        vsegs = assignment.get("video_segments", [])
        if vsegs:
            for seg_idx, seg in enumerate(vsegs):
                entries.append({
                    "track": "V1",
                    "clip_id": seg.get("clip_id", ""),
                    "entry_id": seg.get("segment_id", seg.get("clip_id", f"{assignment.get('spine_block_position')}_seg{seg_idx}")),
                    "source_file": seg.get("source_file", ""),
                    "timeline_start": start,
                })
        else:
            entries.append({
                "track": "V1",
                "clip_id": assignment.get("clip_id", ""),
                "entry_id": assignment.get("clip_id", str(assignment.get("spine_block_position", 0))),
                "source_file": assignment.get("source_file", ""),
                "timeline_start": start,
            })

    for broll in input_data.get("b_roll_assignments", []):
        assigned = broll
        entries.append({
            "track": "V2",
            "clip_id": broll.get("clip_id", ""),
            "entry_id": broll.get("clip_id", ""),
            "source_file": broll.get("source_file", ""),
            "timeline_start": broll.get("timeline_start"),
        })

    for interj in input_data.get("b_roll_interjections", []):
        assigned = interj.get("assigned_clip") or {}
        entries.append({
            "track": "V2",
            "clip_id": assigned.get("clip_id", ""),
            "entry_id": assigned.get("clip_id", ""),
            "source_file": assigned.get("source_file", ""),
            "timeline_start": interj.get("timeline_start"),
        })

    return entries


def resolved_source(entry: dict, project_folder: str) -> str:
    source_file = entry.get("source_file", "")
    if source_file and project_folder:
        return str(
            ProjectLayout(project_folder).resolve_project_relative(source_file))
    return source_file


def _placement_order(entries: list) -> list:
    """The entries in play order, unmeasured starts left where they are."""
    return sorted(
        range(len(entries)),
        key=lambda i: (entries[i].get("timeline_start") is None,
                       entries[i].get("timeline_start") or 0.0, i),
    )


def measure_clips(entries: list, project_folder: str) -> list:
    """One measured row per CLIP, in the order the clip first plays.

    Every clip is measured whether or not anything acts on it, because a
    measurement of the footage is not a decision about it and a reader
    asking "was this graded, and off what" needs the number either way.
    """
    rows, seen = [], {}
    for index in _placement_order(entries):
        entry = entries[index]
        clip_id = entry["clip_id"]
        if clip_id in seen:
            seen[clip_id]["placements"] += 1
            continue
        measurement = measure_luma(resolved_source(entry, project_folder))
        row = {
            "clip_id": clip_id,
            "track": entry["track"],
            "first_plays_at_seconds": entry.get("timeline_start"),
            "placements": 1,
            "luma": measurement["luma"],
            "luma_method": measurement["method"],
            "luma_samples": measurement["samples"],
        }
        if measurement.get("reason"):
            row["luma_unmeasured_because"] = measurement["reason"]
        seen[clip_id] = row
        rows.append(row)
    return rows


def add_scene_descriptions(rows: list, documents, clip_catalog) -> list:
    """What each measured clip IS, joined off the vision pass.

    Average luma does not know whether a shot is a midday plaza or a car
    at dusk, and that is the difference between a fault and the content.
    The join is `library/tools/semantic_index.py` because the documents
    are keyed by FILE STEM and everything else here by `clip_XXX`
    (AGENTS.md 10.1).

    A clip the vision pass did not describe SAYS so rather than carrying
    a blank cell.
    """
    by_clip = {}
    try:
        by_clip = build_semantic_lookup(documents or [], clip_catalog or [])
    except Exception:  # noqa: BLE001 - a failed join is an absence, not a stop
        by_clip = {}
    for row in rows:
        document = by_clip.get(row["clip_id"]) or {}
        scene = ((document.get("analysis") or {}).get("scene") or "").strip()
        row["scene"] = scene or "not described by the vision pass"
    return rows


def cut_adjacency(entries: list, rows: list) -> list:
    """One row per cut where the CLIP changes, with the gap in stops.

    The pairs a viewer can actually see. A clip that never touches
    another is not in this table and that is the point: two shots two
    stops apart that never meet cost nothing.
    """
    luma = {row["clip_id"]: row["luma"] for row in rows}
    order = _placement_order(entries)
    pairs = []
    for previous, current in zip(order, order[1:]):
        outgoing = entries[previous]["clip_id"]
        incoming = entries[current]["clip_id"]
        if outgoing == incoming:
            continue
        gap = stops_between(luma.get(outgoing), luma.get(incoming))
        pairs.append({
            "at_seconds": entries[current].get("timeline_start"),
            "outgoing_clip": outgoing,
            "incoming_clip": incoming,
            "outgoing_luma": luma.get(outgoing),
            "incoming_luma": luma.get(incoming),
            "stops_between": (
                "unmeasured" if gap is None else gap),
        })
    return pairs


def define_color_grade(shot_list: dict, project_folder: str = "",
                       reference_image: str = "", series_look=None,
                       *, measured_clips=None, corrections=None,
                       dropped=None, decided: bool = False,
                       assessment: str = "",
                       subject_grades: list = None) -> dict:
    """Assemble the colour grade spec.

    Args:
        series_look: What the brand template wrote under
            `style.series_look` - a DECLARATION, read by
            `library/tools/series_look.resolve_look`. `None`, `""` or `{}`
            means the template declares no look, and the clips get no
            LOOK. A declaration that cannot be delivered as written
            raises `LookDeclarationError`; it is never completed from a
            default and never dropped.
        measured_clips: rows from `measure_clips`, so the measurement
            happens once in the bridge rather than twice. When None the
            clips are measured here, which is the path the unit tests
            and any direct caller take.
        corrections: the colourist's per-clip corrections, already read
            by `library/tools/color_correction.read_corrections`.
        dropped: the entries that answer produced which did not survive.
        decided: did a colourist answer this step at all? The ONLY thing
            that separates `judged_no_correction_needed` from
            `no_correction_decision`, and it is a fact about the run
            rather than something inferred from an empty list.
        assessment: the colourist's account of the cut as a whole.
    """
    entries = [e for e in shot_list.get("entries", [])
               if e.get("track") in ("V1", "V2")]

    look = resolve_look(series_look)
    reference = look.exposure_reference if look else None

    if measured_clips is None:
        measured_clips = measure_clips(entries, project_folder)
    measurement_by_clip = {row["clip_id"]: row for row in measured_clips}

    correction_by_clip = {c.clip_id: c for c in (corrections or [])}
    dropped = list(dropped or [])

    # A reference frame REPLACES the declared look's CDL half.
    clip_frames = {}
    cdl_matches = {}
    reference_match_error = ""
    if reference_image:
        for entry in entries:
            source_file = resolved_source(entry, project_folder)
            frame_path = _extract_frame(source_file)
            if frame_path:
                clip_frames[entry["clip_id"]] = frame_path
        try:
            from library.tools.look_matcher import match_clips_to_reference
            cdl_matches = match_clips_to_reference(reference_image, clip_frames)
        except Exception as exc:
            # The reference replaces the declared look's CDL, so grading
            # without it is a different grade. Fall back to the declared
            # look (the pre-existing direction) but SAY SO on the record -
            # a silent fallback ships the captain's reference nowhere.
            reference_match_error = f"{type(exc).__name__}: {exc}"
            print(f"WARNING: reference look match failed "
                  f"({reference_match_error}); grading from the declared "
                  f"look instead.", file=sys.stderr)
        for frame_path in clip_frames.values():
            if os.path.exists(frame_path):
                os.remove(frame_path)

    per_clip_adjustments = []
    seen_clips = set()
    unmeasured_clips = []

    for entry in entries:
        clip_id = entry["clip_id"]
        if clip_id in seen_clips:
            continue
        seen_clips.add(clip_id)

        source_file = resolved_source(entry, project_folder)
        measurement = measurement_by_clip.get(clip_id) or {
            "luma": None, "luma_method": LUMA_UNMEASURED, "luma_samples": 0,
            "luma_unmeasured_because": "no measurement reached this step",
        }
        if measurement["luma_method"] == LUMA_UNMEASURED:
            unmeasured_clips.append(clip_id)

        correction = correction_by_clip.get(clip_id)

        if reference_image and clip_id in cdl_matches:
            # The captain matched a specific still and that is the whole
            # point. The Fusion half (contrast, glow, grain, vignette)
            # still applies: a reference frame carries no grain.
            match = cdl_matches[clip_id]
            notes = "AI Look Match CDL applied (replaces the declared look's CDL)"
            cdl_values = {
                "slope_r": match["slope"][0],
                "slope_g": match["slope"][1],
                "slope_b": match["slope"][2],
                "offset_r": match["offset"][0],
                "offset_g": match["offset"][1],
                "offset_b": match["offset"][2],
                "power_r": match["power"][0],
                "power_g": match["power"][1],
                "power_b": match["power"][2],
                "saturation": match["saturation"]
            }
            exposure_offset, exposure_gain = None, 1.0
        else:
            exposure_offset, exposure_gain = exposure_gain_for(
                measurement["luma"], reference)
            notes = _exposure_notes(measurement, reference,
                                    exposure_offset, exposure_gain)
            correction_note = describe_correction(correction)
            if correction_note:
                notes = f"{notes} {correction_note}"
            elif decided:
                notes = (f"{notes} The colourist left this clip alone.")

            # The declared look, the declared reference and the
            # colourist's own correction, flattened into the ONE CDL the
            # renderer applies. The exposure reference is a slope
            # multiplier the same way the correction's stops are, so it
            # folds in at the same stage (library/tools/color_correction).
            cdl_values = compose_cdl(look, correction, NEUTRAL_CDL)
            if exposure_gain != 1.0:
                for channel in "rgb":
                    key = f"slope_{channel}"
                    cdl_values[key] = round(cdl_values[key] * exposure_gain, 4)

        per_clip_adjustments.append({
            "entry_id": entry.get("entry_id", ""),
            "clip_id": clip_id,
            "source_file": source_file,
            # The measurement, kept beside anything derived from it. An
            # absent luma is None with a stated reason, never 0.
            "measured_luma": measurement["luma"],
            "measured_luma_method": measurement["luma_method"],
            "measured_luma_samples": measurement["luma_samples"],
            "measured_luma_reason": measurement.get("luma_unmeasured_because"),
            # None when nothing normalised this clip against a DECLARED
            # reference - the template's half. `0.0` would assert a
            # measured match.
            "exposure_offset": exposure_offset,
            "exposure_reference": reference,
            "exposure_gain": exposure_gain,
            # The colourist's half, kept apart from the template's so a
            # reader can always tell which party moved this clip.
            "correction_stops": (
                correction.exposure_stops if correction else None),
            "correction_terms": (
                list(correction.declared) if correction else []),
            "correction_reason": correction.why if correction else "",
            "white_balance_override": None,
            "notes": notes,
            "cdl_values": cdl_values
        })

    basis = planning_basis(decided, list(corrections or []), dropped)

    # Subject-scoped grades: the colourist's region entries, parsed
    # here and grounded later. 5.01 carries no segmentation, so this
    # half is syntactic only (scope, target kind, readable values);
    # compile_manifest grounds each entry against the clip's 1.06
    # masks, writes the matte, and drops what does not ground - see
    # library/tools/subject_grade.py.
    from library.tools.subject_grade import parse_plan_entry
    subject_kept, subject_drops = [], []
    for raw in (subject_grades or []):
        clean, drop = parse_plan_entry(raw)
        if drop is not None:
            subject_drops.append({
                "clip_id": (raw or {}).get("clip_id", "?")
                if isinstance(raw, dict) else "?",
                "reason": drop["reason"], "detail": drop["detail"]})
        else:
            subject_kept.append(clean)

    look_notes = describe_look(look)
    if look is not None and reference is None:
        look_notes += (
            " It declares no `exposure_reference`, so nothing normalises "
            "the clips onto a series target; the look sits on the footage "
            "as it was shot, with whatever correction the colourist made "
            "underneath it."
        )
    if reference_match_error:
        look_notes += (
            " The captain's reference frame could not be matched "
            f"({reference_match_error}), so no clip below carries a "
            "reference-match CDL: every clip is graded from the declared "
            "look instead.")

    return {
        "color_grade_spec": {
            "grade_pipeline": GRADE_PIPELINE,
            "grade_pipeline_delivery": GRADE_PIPELINE_DELIVERY,
            "per_clip_adjustments": per_clip_adjustments,
            # nodes 3 and 4, as Fusion parameters. compile_manifest merges
            # this onto every V1/V2 clip's effects. `{}` when no look is
            # declared, and `{}` means no comp is drawn for the look's
            # sake at all - not a comp with quiet values in it.
            "fusion_look": look.fusion() if look else {},
            "series_look": look.name if look else None,
            "series_look_declared": list(look.declared) if look else [],
            "look_notes": look_notes,
            "output_color_space": "Rec.709, Gamma 2.4",
            "exposure_reference": reference,
            "unmeasured_clips": unmeasured_clips,
            # WHICH absence an ungraded run is. Four readings, spelled
            # differently on purpose - see library/tools/color_correction.
            # The clip list travels too, so the assessment's checkable
            # claims are held against what shipped (D11).
            "correction_basis": basis_record(
                basis, list(corrections or []), dropped, assessment,
                clips_in_the_cut=[row["clip_id"] for row in measured_clips]),
            "consistency_notes": _consistency_notes(
                per_clip_adjustments, reference, unmeasured_clips, basis),
            # Region-scoped grades, still ungrounded: compile_manifest
            # resolves each against its clip's tracked subject, writes
            # the matte, and records what did not survive beside them.
            "subject_grades": subject_kept,
            "subject_grade_drops": subject_drops,
        },
    }


def _exposure_notes(measurement: dict, reference, offset, gain) -> str:
    """What happened to this clip's exposure, in one sentence.

    Never asserts a measurement that did not happen. The sentence this
    replaced - "Exposure within normal range, no adjustment needed" - was
    written on 10 of 10 clips of project 001 about a probe whose every
    sample had been discarded.
    """
    if measurement["luma_method"] == LUMA_UNMEASURED:
        return (f"Exposure not measured: "
                f"{measurement.get('luma_unmeasured_because')}. "
                f"No normalisation applied.")

    measured = (f"Average luma {measurement['luma']} over "
                f"{measurement['luma_samples']} samples ({LUMA_METHOD})")
    if reference is None:
        return (f"{measured}. The brand template declares no "
                f"`exposure_reference`, so nothing normalises it onto a "
                f"series target.")
    return (f"{measured}, against a declared reference of {reference}: "
            f"{offset:+.3f} stops, slope gain {gain}.")


def _consistency_notes(adjustments, reference, unmeasured_clips,
                       basis) -> str:
    """What the grade did across the clips, as counts with denominators."""
    total = len(adjustments)
    distinct = len({
        tuple(sorted(a["cdl_values"].items())) for a in adjustments
    })
    measured = total - len(unmeasured_clips)
    corrected = sum(1 for a in adjustments if a["correction_terms"])
    parts = [
        f"{distinct} distinct CDL value(s) across {total} clip(s); "
        f"luma measured on {measured} of {total}; the colourist corrected "
        f"{corrected} of {total}."
    ]
    if unmeasured_clips:
        parts.append(
            f"Unmeasured: {', '.join(unmeasured_clips)} - each carries "
            f"measured_luma null and a reason, not a zero.")
    if reference is None:
        parts.append(
            "No exposure reference is declared by any brand template, so "
            "nothing normalises onto a series target; what moved a clip, "
            "where anything did, is the colourist's own correction.")
    else:
        parts.append(
            f"Exposure normalised onto a declared reference of "
            f"{reference}, so the CDL slope differs per clip by the "
            f"measured difference, with any correction composed under it.")
    parts.append(f"correction_basis: {basis}.")
    return " ".join(parts)


def read_json_stdin(stream) -> dict:
    return json.loads(stream.read())
