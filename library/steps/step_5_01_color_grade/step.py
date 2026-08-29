#!/usr/bin/env python3
"""
Step 5.1: Define Color Grading Specification

Turn the look a brand template DECLARED into the two things that reach
the picture: an ASC CDL per clip, and the Fusion parameters
`build_effect_comp` dispatches on.

No look is defined here and none is defined in the engine at all -
`library/tools/house_look.py` reads a declaration and holds no values of
its own. A project whose template declares nothing gets nothing: no CDL,
no contrast, no glow, no grain, no vignette.

**The exposure half measures, and only normalises when a target was
declared.** It used to compare every clip against a constant 122.0 and
apply the difference, which is a decision about how bright the finished
video is, taken by nobody. Now the luma is MEASURED and recorded on every
clip, and a gain is applied only against a `exposure_reference` the
declaration carries. Where nothing measured, the record says so - it does
not report 0.0, which is what it used to do on every clip of project 001
(AGENTS.md 10.3: no field reports a default as though it were measured).

Classification: Deterministic / Specification
Input:  { "shot_list": {...} }
Output: { "color_grade_spec": { grade_pipeline, per_clip_adjustments } }
"""
import json
import os
import subprocess
import sys

from library.tools.house_look import (
    NEUTRAL_CDL,
    describe_look,
    resolve_look,
)
from library.tools.project_layout import ProjectLayout


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
        "source": "brand template style.house_look.cdl",
    },
    "node_3": {
        "type": "tonal_shaping",
        "carries": ["pivot_contrast"],
        "source": "brand template style.house_look.contrast",
    },
    "node_4": {
        "type": "creative_film_look",
        "carries": ["glow", "grain", "vignette"],
        "source": "brand template style.house_look.{glow,grain,vignette}",
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
                  "Applied with TimelineItem.SetCDL.",
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
            errors="replace", timeout=30,
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
    import math

    if reference is None or luma is None:
        return None, 1.0
    if luma <= 0 or reference <= 0:
        return None, 1.0
    offset = math.log2(reference / luma)
    return round(offset, 4), round(2.0 ** offset, 4)


def _extract_frame(video_path: str) -> str:
    """Extract a single frame from video for color analysis."""
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
        return path
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        return ""

def define_color_grade(shot_list: dict, project_folder: str = "",
                       reference_image: str = "", house_look=None) -> dict:
    """Define the colour grading specification from the declared look.

    Args:
        house_look: What the brand template wrote under
            `style.house_look` - a DECLARATION, read by
            `library/tools/house_look.resolve_look`. `None`, `""` or `{}`
            means the template declares no look, and the clips get no
            grade at all. A declaration that cannot be delivered as
            written raises `LookDeclarationError`; it is never completed
            from a default and never dropped.

    Every clip's average luma is measured either way, because a
    measurement of the footage is not a decision about it, and a reader
    asking "was this graded, and off what" needs the number whether or
    not anything acted on it.
    """
    entries = shot_list.get("entries", [])

    look = resolve_look(house_look)
    reference = look.exposure_reference if look else None

    # Identify clips that may need per-clip adjustments
    per_clip_adjustments = []
    seen_clips = set()
    clip_frames = {}

    for entry in entries:
        if entry["track"] not in ("V1", "V2"):
            continue
        if entry["clip_id"] in seen_clips:
            continue
        seen_clips.add(entry["clip_id"])

        source_file = entry.get("source_file", "")
        if source_file and project_folder:
            source_file = str(
                ProjectLayout(project_folder).resolve_project_relative(source_file))

        if reference_image:
            frame_path = _extract_frame(source_file)
            if frame_path:
                clip_frames[entry["clip_id"]] = frame_path

    # Compute matches if reference is provided
    cdl_matches = {}
    if reference_image:
        try:
            from library.tools.look_matcher import match_clips_to_reference
            cdl_matches = match_clips_to_reference(reference_image, clip_frames)
        except ImportError:
            pass

    # Cleanup temp frames
    for frame_path in clip_frames.values():
        if os.path.exists(frame_path):
            os.remove(frame_path)

    seen_clips.clear()
    unmeasured_clips = []

    for entry in entries:
        if entry["track"] not in ("V1", "V2"):
            continue
        if entry["clip_id"] in seen_clips:
            continue
        seen_clips.add(entry["clip_id"])

        source_file = entry.get("source_file", "")
        if source_file and project_folder:
            source_file = str(
                ProjectLayout(project_folder).resolve_project_relative(source_file))

        measurement = measure_luma(source_file)
        if measurement["method"] == LUMA_UNMEASURED:
            unmeasured_clips.append(entry["clip_id"])

        if reference_image and entry["clip_id"] in cdl_matches:
            # A reference frame REPLACES the declared look's CDL half -
            # the captain matched a specific still and that is the whole
            # point. The Fusion half (contrast, glow, grain, vignette)
            # still applies: a reference frame carries no grain.
            match = cdl_matches[entry["clip_id"]]
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

            if look is None:
                cdl_values = dict(NEUTRAL_CDL)
                for channel in "rgb":
                    cdl_values[f"slope_{channel}"] = round(exposure_gain, 4)
            else:
                cdl_values = look.cdl(exposure_gain=exposure_gain)

        per_clip_adjustments.append({
            "entry_id": entry.get("entry_id", ""),
            "clip_id": entry["clip_id"],
            "source_file": source_file,
            # The measurement, kept beside anything derived from it. An
            # absent luma is None with a stated reason, never 0.
            "measured_luma": measurement["luma"],
            "measured_luma_method": measurement["method"],
            "measured_luma_samples": measurement["samples"],
            "measured_luma_reason": measurement.get("reason"),
            # None when nothing normalised this clip - because nothing
            # measured it, or because no reference was declared to
            # normalise onto. `0.0` would assert a measured match.
            "exposure_offset": exposure_offset,
            "exposure_reference": reference,
            "exposure_gain": exposure_gain,
            "white_balance_override": None,
            "notes": notes,
            "cdl_values": cdl_values
        })

    look_notes = describe_look(look)
    if look is not None and reference is None:
        look_notes += (
            " It declares no `exposure_reference`, so the clips are "
            "measured and not normalised: the look sits on the footage as "
            "it was shot."
        )

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
            "house_look": look.name if look else None,
            "house_look_declared": list(look.declared) if look else [],
            "look_notes": look_notes,
            "output_color_space": "Rec.709, Gamma 2.4",
            "exposure_reference": reference,
            "unmeasured_clips": unmeasured_clips,
            "consistency_notes": _consistency_notes(
                per_clip_adjustments, reference, unmeasured_clips),
        },
    }


def _exposure_notes(measurement: dict, reference, offset, gain) -> str:
    """What happened to this clip's exposure, in one sentence.

    Never asserts a measurement that did not happen. The sentence this
    replaced - "Exposure within normal range, no adjustment needed" - was
    written on 10 of 10 clips of project 001 about a probe whose every
    sample had been discarded.
    """
    if measurement["method"] == LUMA_UNMEASURED:
        return (f"Exposure not measured: {measurement.get('reason')}. "
                f"No normalisation applied.")

    measured = (f"Average luma {measurement['luma']} over "
                f"{measurement['samples']} samples ({LUMA_METHOD})")
    if reference is None:
        return (f"{measured}. The brand template declares no "
                f"`exposure_reference`, so nothing normalises it and the "
                f"clip keeps the exposure it was shot at.")
    return (f"{measured}, against a declared reference of {reference}: "
            f"{offset:+.3f} stops, slope gain {gain}.")


def _consistency_notes(adjustments, reference, unmeasured_clips) -> str:
    """What the grade did across the clips, as counts with denominators."""
    total = len(adjustments)
    distinct = len({
        tuple(sorted(a["cdl_values"].items())) for a in adjustments
    })
    measured = total - len(unmeasured_clips)
    parts = [
        f"{distinct} distinct CDL value(s) across {total} clip(s); "
        f"luma measured on {measured} of {total}."
    ]
    if unmeasured_clips:
        parts.append(
            f"Unmeasured: {', '.join(unmeasured_clips)} - each carries "
            f"measured_luma null and a reason, not a zero.")
    if reference is None:
        parts.append(
            "No exposure reference is declared, so every clip's CDL is "
            "the declared look alone and one distinct value across all of "
            "them is the correct answer rather than a defect.")
    else:
        parts.append(
            f"Exposure normalised onto a declared reference of "
            f"{reference}, so the CDL slope differs per clip by exactly "
            f"the measured difference.")
    return " ".join(parts)



def main():
    input_data = json.loads(sys.stdin.read())
    project_folder = input_data.get("project_folder", "")
    brand_template = input_data.get("brand_template", {})
    style = brand_template.get("style", {})
    reference_image = style.get("reference_look_image", "")

    
    entries = []

    # A-roll assignments
    for assignment in input_data.get("a_roll_assignments", []):
        vsegs = assignment.get("video_segments", [])
        if vsegs:
            for seg_idx, seg in enumerate(vsegs):
                entries.append({
                    "track": "V1",
                    "clip_id": seg.get("clip_id", assignment.get("source_clip_id", "")),
                    "entry_id": seg.get("segment_id", seg.get("clip_id", f"{assignment.get('spine_block_position')}_seg{seg_idx}")),
                    "source_file": seg.get("source_file", assignment.get("source_file", "")),
                })
        else:
            entries.append({
                "track": "V1",
                "clip_id": assignment.get("source_clip_id", assignment.get("clip_id", "")),
                "entry_id": assignment.get("assignment_id", ""),
                "source_file": assignment.get("source_file", ""),
            })

    # B-roll assignments
    for broll in input_data.get("b_roll_assignments", []):
        assigned = broll.get("assigned_clip", broll)
        entries.append({
            "track": "V2",
            "clip_id": assigned.get("clip_id", assigned.get("source_clip_id", "")),
            "entry_id": broll.get("assignment_id", ""),
            "source_file": assigned.get("source_file", broll.get("source_file", "")),
        })

    # B-roll interjections
    for interj in input_data.get("b_roll_interjections", []):
        clip = interj.get("assigned_clip", interj)
        entries.append({
            "track": "V2",
            "clip_id": clip.get("clip_id", clip.get("source_clip_id", "")),
            "entry_id": interj.get("assignment_id", ""),
            "source_file": clip.get("source_file", interj.get("source_file", "")),
        })

    shot_list = {"entries": entries}
    # Read from `style`, where the templates actually put it. This is a
    # DECLARATION now, not a name into an engine catalogue: see
    # library/tools/house_look.py for why the catalogue is gone.
    house_look = style.get("house_look")
    result = define_color_grade(shot_list, project_folder, reference_image, house_look)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
