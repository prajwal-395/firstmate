#!/usr/bin/env python3
"""Step 5.01 pre-bridge: the measurements a colourist decides from.

Two tables and a legend, because a grade is a decision about a SEQUENCE
and one of the two axes is the one the old step never produced.

* `clip_exposure` - one row per clip, in the order it first plays, with
  its measured average luma, how that was measured, how many placements
  it carries, and what the vision pass says the shot IS. The scene
  description is joined here rather than routed raw: the documents are
  keyed by FILE STEM and everything else in this step by `clip_XXX`, and
  making the model do that join is the key-name mismatch AGENTS.md 10.1
  names as this repository's dominant bug class.
* `cut_adjacency` - one row per cut where the clip changes, with the gap
  between the two shots in stops. This is the axis that decides whether a
  luma difference is a problem: on project 001 clip_011 (145.5) and
  clip_017 (53.1) are 1.45 stops apart, and it matters because they play
  either side of cuts and not because 2.7x is a large ratio.
* `shot_stills` - one representative still per graded shot, drawn from
  the seconds the colour numbers describe, so the colourist sees the
  picture beside the measurement (AGENTS.md 10.1: a step that chooses a
  picture is shown one). Undrawn clips are NAMED, never quietly absent.
* `still_colour_notes` - what the still router saw in those stills:
  asked through `library/tools/still_vision.py` (the driving LLM's own
  vision first, gemma4 fallback), one white-balance line per file plus
  which of the two answered. A run with no stills, no project folder or
  no answering vision SAYS so; the still paths above remain for the
  driver to open either way.
* `camera_match` - the measured proposal that brings two angles covering
  one set onto one balance: per-camera neutral R/B and G/B, the
  reference (nearest true neutral), and one slope triple per other
  camera (library/tools/camera_match.py). STATED, never applied: the
  colourist accepts by copying a slope into `color_correction` with a
  `why`, or overrides with their own.
* `grade_terms_legend` - what each correction term IS and how it composes
  with a declared look. The `MEASUREMENT_LEGEND` route: it defines the
  vocabulary and never says what to conclude.

The whole declared look, where the project's template declares one, is
put in front of the colourist as `declared_look` - they are correcting
UNDER it and cannot reason about that without seeing it. A project that
declares none gets `declared_look: null` and the sentence saying what
that means, which is what 001 gets.

**The measurement happens HERE and once.** It travels to `post_bridge.py`
as a bridge output the way every hybrid step's table does, so the two
halves of the step cannot disagree about what was measured, and ffprobe
is not run twice over the same nine files.
"""
import json
import os
import sys

from library.steps.step_5_01_color_grade.grade import (
    LUMA_SCOPE,
    add_scene_descriptions,
    collect_entries,
    cut_adjacency,
    measure_clips,
    resolved_source,
)
from library.tools import still_vision as still_router
from library.tools.camera_match import derive_camera_match
from library.tools.color_correction import term_legend
from library.tools.project_layout import Area, ProjectLayout
from library.tools.series_look import (
    describe_look,
    effective_series_look,
    resolve_look,
)
from library.tools.shot_colour import (
    CHROMA_SAMPLE_SECONDS,
    COLOUR_SCOPE,
    STILL_WIDTH,
    extract_still,
)


def declared_look_view(series_look) -> dict:
    """What the project declared as its look, or the absence of it.

    The declaration arrives already resolved - the project's own
    `style.series_look` wins over its brand template's
    (`effective_series_look`) - so this function only renders the view.

    An absent look is stated as a sentence rather than left as a blank:
    "no look is declared" and "a look is declared and it is quiet" are
    different facts, and only one of them means the colourist's
    correction IS the grade.
    """
    look = resolve_look(series_look)
    view = {"declared": look is not None, "notes": describe_look(look)}
    if look is None:
        view["what_that_means_for_you"] = (
            "No brand template declares a look, so every look term is "
            "identity and whatever correction you write IS the grade this "
            "video ships with. Nothing is substituted for the absence - "
            "there is no house look in this engine.")
        return view
    view["name"] = look.name
    view["intent"] = look.intent
    view["elements"] = list(look.declared)
    view["cdl"] = look.cdl() if look.has_cdl else None
    view["fusion"] = look.fusion()
    view["exposure_reference"] = look.exposure_reference
    view["what_that_means_for_you"] = (
        "This look is applied whatever you decide, and your correction is "
        "composed UNDERNEATH it in the order `grade_terms_legend` states. "
        "You are not restating it and you are not overruling it.")
    return view


#: What the still router is asked about each shot. White balance ONLY:
#: exposure already has its own numbers, and a prompt that asks about
#: everything is answered about nothing.
STILL_COLOUR_PROMPT = (
    "Judge the WHITE BALANCE of each still image, one line per file. "
    "Name the file, the cast you see (warm, cool, green, magenta or "
    "neutral), and the neutral surface you judged it on (wall, shirt, "
    "sky, ...). Where a still carries no neutral surface, say so "
    "instead of judging the cast off coloured content. Do not judge "
    "exposure, framing or content.")


def attach_sources(rows: list, entries: list, project_folder: str) -> list:
    """The source file beside each measured row, for grouping and stills.

    First placement wins: a clip used twice is measured once, and the
    still is of one of its sources. A row with no source says so - the
    camera-match groups by stem and a stemless row would group wrong.
    """
    source_by_clip = {}
    for entry in entries:
        clip_id = entry.get("clip_id")
        if clip_id and clip_id not in source_by_clip:
            source_by_clip[clip_id] = resolved_source(
                entry, project_folder)
    for row in rows:
        row["source_file"] = source_by_clip.get(row.get("clip_id"), "")
    return rows


def draw_shot_stills(rows: list, project_folder: str) -> tuple:
    """One representative still per measured shot. Returns (block, paths).

    The still is drawn from the seconds the colour numbers describe
    (`shot_colour.extract_still`), at `STILL_WIDTH` wide, into the
    colour grade's own stills area - so the layout answers which step
    wrote it. A drawn still is reused; a clip whose still could not be
    drawn is NAMED in the block, never quietly absent.
    """
    if not project_folder:
        print("  No project_folder: no shot stills drawn",
              file=sys.stderr)
        return "", []
    directory = ProjectLayout(project_folder).write_dir(
        Area.SHOT_STILLS, step="color_grade")

    drawn, missing, paths = [], [], []
    for row in rows:
        clip_id = row.get("clip_id", "?")
        source = row.get("source_file", "")
        name = f"{clip_id}__shot.jpg"
        path = str(directory / name)
        if (os.path.exists(path) and os.path.getsize(path) > 0) or (
                source and extract_still(source, path)):
            drawn.append({"clip_id": clip_id, "file": name})
            paths.append(path)
        else:
            missing.append(
                f"{clip_id} (no still: "
                f"{'no source file' if not source else 'ffmpeg drew nothing'})")

    print(f"  {len(drawn)} shot still(s) at {directory}"
          + (f"; {len(missing)} not drawn" if missing else ""),
          file=sys.stderr)
    if not drawn and not missing:
        return "", []
    lines = [
        (f"Shot stills - one representative frame per graded shot, drawn "
         f"from the middle of the {CHROMA_SAMPLE_SECONDS:g}s span the "
         f"colour numbers describe, {STILL_WIDTH}px wide, at:"),
        f"  directory: {directory}",
    ]
    for still in drawn:
        lines.append(f"  {still['clip_id']}: {still['file']}")
    for name in missing:
        lines.append(f"  NOT DRAWN: {name}")
    return "\n".join(lines), paths


def observe_shot_stills(paths: list, project_folder: str) -> dict:
    """What the still router saw in the shot stills.

    Asked through `library/tools/still_vision.py`: the driving LLM's
    own vision first, gemma4 where no host drives. The text is a second
    reading beside the deterministic numbers, not a replacement for
    them - and where nothing answered, the absence is STATED with the
    still paths left in `shot_stills` for the driver to open.
    """
    if not paths:
        return {"observed_by": "none",
                "reason": ("no stills were drawn, so there was nothing "
                           "to look at"),
                "text": ""}
    if not project_folder:
        return {"observed_by": "none",
                "reason": ("no project_folder: the still-vision request "
                           "has nowhere to live - open the stills in "
                           "`shot_stills` with your own vision"),
                "text": ""}
    try:
        text = still_router.inspect_stills(
            STILL_COLOUR_PROMPT, list(paths),
            project_folder=project_folder,
            step_id="step_5_01_color_grade",
            label="colour stills")
    except Exception as exc:  # noqa: BLE001 - an absence with the reason
        return {"observed_by": "none",
                "reason": (f"{type(exc).__name__}: {exc} - open the "
                           f"stills in `shot_stills` with your own vision"),
                "text": ""}
    driving = still_router.resolve_harness()
    if driving is None:
        observed_by = "gemma4 fallback (no host drives this run)"
    else:
        observed_by = (f"host ({driving}) - the driving LLM's own vision")
    return {"observed_by": observed_by, "text": text}


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001 - the runner reads this
        print(json.dumps({"error": str(exc), "step": "5.01_bridge"}))
        sys.exit(1)

    project_folder = data.get("project_folder", "")
    entries = collect_entries(data)
    rows = measure_clips(entries, project_folder)
    add_scene_descriptions(rows, data.get("semantic_analysis_documents"),
                           data.get("clip_catalog"))
    attach_sources(rows, entries, project_folder)

    stills_block, still_paths = draw_shot_stills(rows, project_folder)

    style = (data.get("brand_template") or {}).get("style", {})

    print(json.dumps({
        "clip_exposure": rows,
        "cut_adjacency": cut_adjacency(entries, rows),
        "declared_look": declared_look_view(effective_series_look(
            style, data.get("project_folder", ""))),
        "shot_stills": stills_block,
        "still_colour_notes": observe_shot_stills(
            still_paths, project_folder),
        "camera_match": derive_camera_match(rows),
        "grade_terms_legend": {
            "luma": (
                f"{LUMA_SCOPE}. It does not know where the light is in the "
                f"frame, whether the subject is the lit part, or what "
                f"colour the light was - read it with the `scene` column. "
                f"A null luma with a `luma_unmeasured_because` is an "
                f"admitted absence and never a measurement of darkness."),
            "mean_rgb": (
                f"{COLOUR_SCOPE}. The whole-frame balance (`rb_all`, "
                f"`gb_all`) mixes the cast with the content - a warm face "
                f"on a neutral set reads warm here even when the camera "
                f"is balanced."),
            "neutral_rb": (
                "R/B on the DETECTED NEUTRAL region - the grey the shot "
                "carries, where a ratio off 1.0 IS the cast. Read it "
                "before `rb_all`: the whole frame says what the picture "
                "contains, the neutral says what the camera did. "
                "A null neutral with a `colour_unmeasured_because` means "
                "no grey cleared the floor - balance nothing off it."),
            "neutral_gb": (
                "G/B on the same detected neutral region. `neutral_rgb` "
                "is the region's mean triple, `neutral_fraction` how "
                "much of the sampled picture voted for it."),
            "stops_between": (
                "log2(incoming_luma / outgoing_luma) - the gap across one "
                "cut, in stops. `unmeasured` means one of the two ends was "
                "not measured. It is a measurement and not a verdict: "
                "nothing here says how large a gap is too large."),
            "first_plays_at_seconds": (
                "where this clip first appears on the timeline, in "
                "seconds. `placements` is how many times it is used."),
            "scene": (
                "the vision pass's own description of where this clip is "
                "and how it is lit, joined onto the measurement by "
                "library/tools/semantic_index.py."),
            "camera_match": (
                "a STATED, MEASURED proposal that brings the angles "
                "together - per-camera neutral balances, the reference "
                "(nearest true neutral), one slope triple per other "
                "camera, and the predicted after. The engine applies "
                "nothing: accept by copying a slope into "
                "`color_correction` with a `why`, override with your "
                "own. Verify the angles share the set first."),
            **term_legend(),
        },
    }))


if __name__ == "__main__":
    main()
