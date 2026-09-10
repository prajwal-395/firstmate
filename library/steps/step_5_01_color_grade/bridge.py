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
import sys

from library.steps.step_5_01_color_grade.grade import (
    LUMA_SCOPE,
    add_scene_descriptions,
    collect_entries,
    cut_adjacency,
    measure_clips,
)
from library.tools.color_correction import term_legend
from library.tools.series_look import (
    describe_look,
    effective_series_look,
    resolve_look,
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

    style = (data.get("brand_template") or {}).get("style", {})

    print(json.dumps({
        "clip_exposure": rows,
        "cut_adjacency": cut_adjacency(entries, rows),
        "declared_look": declared_look_view(effective_series_look(
            style, data.get("project_folder", ""))),
        "grade_terms_legend": {
            "luma": (
                f"{LUMA_SCOPE}. It does not know where the light is in the "
                f"frame, whether the subject is the lit part, or what "
                f"colour the light was - read it with the `scene` column. "
                f"A null luma with a `luma_unmeasured_because` is an "
                f"admitted absence and never a measurement of darkness."),
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
            **term_legend(),
        },
    }))


if __name__ == "__main__":
    main()
