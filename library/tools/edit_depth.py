"""For any edit, name the deepest source that owns it.

The chain is transcript -> plan -> subtitle/MG render -> timeline
item, and every expensive failure this week is a fix applied to a
layer that only DISPLAYS the value: erased on the next regeneration,
disagreeing with its source until then. This module is the map of
that chain, one row per edit class: which layer OWNS it (with the
store and the module that enforces it) and which layers merely
DISPLAY it.

A router nobody is forced to consult is a document, not a mechanism,
so this module also REFUSES: `refuse_display_edit` raises
`EditDepthError` naming the owning layer and the deep path, for a
fix landing on a display-only layer. The pre-run coherence gate
(`library/tools/layer_coherence.py`) is the forced consultation -
it compares each layer against the one it derives from on every run
and flags divergence loudly.

The ten classes, and where each lives
-------------------------------------
1. `wording` - OWNS: the transcript root, `learned_context`
   correction enforced by `transcript_corrections.apply_to_document`
   (deterministic) plus prompt routing to model-authored copy.
   DISPLAYS: subtitle cards/files, motion-graphic payloads, reel
   plans and proposals, judge readings, timeline captions.
2. `clip_timing` - OWNS: `captain_edits` `span_retime` pins, applied
   post-placement by `retime_placements` (trims only; extensions
   refuse). The measured word timings themselves are uneditable
   signal. DISPLAYS: spine blocks, caption timings, manifest clips,
   timeline items. (Orphan until this lane: Reel 13's trims died
   twice for it.)
3. `overlay_position` - OWNS, shared: computed
   (`tight_box`/`overlay_placement`) vs declared
   (`external/overlay_intent.json`, provenance recorded per item).
   DISPLAYS: timeline Transform. No capture route: a hand move must
   be transcribed into the JSON by hand.
4. `picture_position` - OWNS, shared: computed punch-in aim
   (`subject_framing`) vs declared (`captain_edits`
   `transform_override`, capturable from the live timeline).
   DISPLAYS: timeline Transform.
5. `look_grade` - OWNS, shared: the brand template declares
   `style.series_look`; step 5.01's colourist decides the CDL
   normalisation; a captain-supplied `.drx` delivers the PowerGrade
   through `color_page_grade`. There is no house look. A hand grade
   on timeline nodes is painted over by the next build (6.01
   applies CDL + PowerGrade per clip): the deep path is the template
   or the `.drx`, never the nodes. DISPLAYS: Fusion comps, Color
   page nodes, export pixels.
6. `structure` - OWNS, shared: `select_reels` proposes; approval
   freezes approved reels; `keep_exclusion` removes seconds,
   `drop_fragment`/`redraw_closer`/`span_retime` redraw them as
   durable deltas; `reel_replace_guard` diffs every promote.
   DISPLAYS: timelines. Gap, stated: the marker routing vocabulary
   has no `select_reels`/`assign_aroll`/`build_reels` target, so a
   structural note reaches no prompt - the deltas above are the
   route until that vocabulary grows one.
7. `assets` - OWNS: template-declared bookends (`content.bookends`,
   staged verbatim); model-chosen SFX (`sfx_library` enum) and music
   (2.04); model-chosen b-roll (3.02); and, since this lane,
   captain-placed cards (`external/placed_assets.json`, carried into
   the manifest as `placed_assets`). A hand-laid asset with no
   declaration is rebuilt without. DISPLAYS: pool and timeline items.
8. `audio_levels` - OWNS, shared: `mesh_spine` declares the
   `music_behavior` word (prompt-routable); `audio_mix` turns the
   word into dB; OTIO delivers; and, since this lane, a hand fader
   is a `mix_intent` pin applied post-plan and stamped declared.
   The separation target stays undeclared (both halves say so).
   DISPLAYS: Fairlight levels, timeline markers.
9. `mg_content` - OWNS: the 4.06 plan (model-authored; the wording
   correction's prompt half reaches it) plus project-declared timed
   text (`effect.timed_text_overlay`). A re-render overwrites the
   rendered segments; authored copy can still misspell, which is
   what the coherence wording scan catches. DISPLAYS: rendered
   segments, V6 timeline items.
10. `marker_feedback` - OWNS: `marker_feedback` reads the typed notes
    off the timeline durably; `marker_routing` routes each to the
    step that owns the decision (17 routed, ambiguous/unrouted
    reported never forced); `marker_resolution` records the answer.
    DISPLAYS: marker files, ROUTED-NOTES.md.

`tests/test_edit_depth.py`.
"""

from __future__ import annotations

import sys


class EditDepthError(ValueError):
    """A fix applied to a layer that only displays the value."""


#: The owning layer for each class: the store that keeps the decision
#: and the module that enforces it at rebuild time. `shared` means the
#: class has a computed half and a declared half and the declared half
#: wins (provenance recorded where the renderer can carry it).
OWNERS = {
    "wording": {
        "layer": "transcript root",
        "store": "learned_context correction (transcript_spelling)",
        "module": "library/tools/transcript_corrections.py",
        "shared": False,
    },
    "clip_timing": {
        "layer": "captain_edits span_retime pins, applied post-placement",
        "store": "external/captain_edits.json",
        "module": "library/tools/captain_edits.py",
        "shared": False,
    },
    "overlay_position": {
        "layer": "declared overlay_intent vs computed tight_box",
        "store": "external/overlay_intent.json",
        "module": "library/tools/overlay_intent.py",
        "shared": True,
    },
    "picture_position": {
        "layer": "declared transform_override vs computed punch-in aim",
        "store": "external/captain_edits.json",
        "module": "library/tools/captain_edits.py",
        "shared": True,
    },
    "look_grade": {
        "layer": "brand template series_look + 5.01 CDL + captain .drx",
        "store": "brand template / color_page_grade asset",
        "module": "library/tools/color_page_grade.py",
        "shared": True,
    },
    "structure": {
        "layer": "select_reels proposal + approval + keep/delta pins",
        "store": "learned_context + external/captain_edits.json",
        "module": "library/tools/transcript_corrections.py",
        "shared": True,
    },
    "assets": {
        "layer": "template bookends + external placed_assets pins",
        "store": "brand template / external/placed_assets.json",
        "module": "library/tools/placed_assets.py",
        "shared": True,
    },
    "audio_levels": {
        "layer": "music_behavior words + audio_mix dB + mix_intent pins",
        "store": "spine plan / external/mix_intent.json",
        "module": "library/tools/mix_intent.py",
        "shared": True,
    },
    "mg_content": {
        "layer": "4.06 plan + project timed-text declaration",
        "store": "step 4.06 output / effect.timed_text_overlay",
        "module": "library/tools/motion_graphics_plan.py",
        "shared": True,
    },
    "marker_feedback": {
        "layer": "marker_feedback read + marker_routing route",
        "store": "marker_feedback/*.markers.json + routing ledger",
        "module": "library/tools/marker_routing.py",
        "shared": False,
    },
}

#: Layers that merely display each class. A fix landing on one of
#: these is refused (or loudly flagged where raising would break a
#: live run) and routed to the owner above.
DISPLAYS = {
    "wording": ("subtitle cards", "subtitle files", "motion-graphic "
                "payloads", "reel plans", "reel proposals",
                "judge readings", "timeline captions"),
    "clip_timing": ("spine blocks", "caption timings",
                    "manifest clips", "timeline items"),
    "overlay_position": ("timeline Transform", "overlay clip props"),
    "picture_position": ("timeline Transform", "punch-in props"),
    "look_grade": ("Fusion comps", "Color page nodes",
                   "timeline clip grades"),
    "structure": ("timelines", "promoted reel cuts"),
    "assets": ("pool items", "timeline items"),
    "audio_levels": ("Fairlight levels", "timeline markers",
                     "rendered mix"),
    "mg_content": ("rendered segments", "V6 timeline items"),
    "marker_feedback": ("marker files", "ROUTED-NOTES.md"),
}

#: The deep path, in one line each: what the fixer does instead.
DEEP_PATH = {
    "wording": "record a transcript_spelling correction "
               "(transcript_corrections.record_spelling); the "
               "deterministic pass respells every regeneration.",
    "clip_timing": "record a span_retime pin (captain_edits "
                   "record-retime --anchor ... --edge head|tail); "
                   "retime_placements moves the edge and closes up.",
    "overlay_position": "declare it in external/overlay_intent.json "
                        "(segment id, or kind default for captions); "
                        "the placer honours declared over computed.",
    "picture_position": "capture it from the live timeline "
                        "(captain_edits capture-transform) or record it "
                        "(record-transform); the build holds it "
                        "post-aim.",
    "look_grade": "declare it in the brand template's "
                  "style.series_look, or supply the look as a .drx "
                  "through color_page_grade; never grade the nodes.",
    "structure": "record a keep exclusion, drop_fragment, "
                 "redraw_closer or span_retime pin; approve the reel "
                 "so the guard freezes it.",
    "assets": "declare template cards in content.bookends; declare "
              "hand-placed cards in external/placed_assets.json; the "
              "compile carries both onto V1.",
    "audio_levels": "change the spine's music_behavior word, or pin "
                    "the hand level in external/mix_intent.json; "
                    "apply_mix_intent holds it post-plan.",
    "mg_content": "re-plan 4.06 (the wording correction's prompt half "
                  "reaches authored copy); declare timed text in "
                  "effect.timed_text_overlay; never edit the render.",
    "marker_feedback": "type the note on the timeline and let "
                       "marker_routing deliver it to the owning step; "
                       "never hand-apply what a step decides.",
}


def classes() -> list:
    """The ten edit classes, in canonical order."""
    return list(OWNERS)


def owner_of(edit_class: str) -> dict:
    """The owning layer for an edit class, or a refusal."""
    try:
        return OWNERS[edit_class]
    except KeyError:
        raise EditDepthError(
            f"unknown edit class {edit_class!r}: one of "
            f"{', '.join(OWNERS)}. An edit nobody classified is an "
            f"edit with no owner.") from None


def classify(edit_class: str, layer: str) -> str:
    """`owning`, `display`, or a refusal for an unknown class.

    The answer to "this fix landed on L - does L own class C".
    """
    owner = owner_of(edit_class)
    if layer == owner["layer"]:
        return "owning"
    return "display"


def refuse_display_edit(edit_class: str, layer: str,
                        detail: str = "") -> None:
    """Refuse a fix applied to a display-only layer. Raises, always.

    Names the owning layer and the deep path, so the refusal is a
    routing, not a wall. Unknown classes refuse too - an unclassified
    edit has no proven owner, so accepting it anywhere is the shallow
    fix by default.
    """
    owner = owner_of(edit_class)
    if classify(edit_class, layer) == "owning":
        return
    lines = [f"REFUSED: {edit_class} is owned by {owner['layer']}",
             f"({owner['store']}; enforced in {owner['module']}).",
             f"A fix on {layer} only displays the value - the next "
             f"regeneration erases it, and until then the layers "
             f"disagree.",
             f"Deep path: {DEEP_PATH[edit_class]}"]
    if detail:
        lines.append(f"Context: {detail}")
    raise EditDepthError("\n".join(lines))


def flag_display_edit(edit_class: str, layer: str,
                      detail: str = "") -> str:
    """The loud flag for contexts where raising breaks a live run.

    Same verdict as `refuse_display_edit`, printed to stderr and
    returned: the pre-run coherence gate flags rather than refusing,
    because a stale display must not stop an unrelated build - but it
    must SAY so, every run, until it is routed or retired.
    """
    try:
        refuse_display_edit(edit_class, layer, detail)
    except EditDepthError as exc:
        message = f"SHALLOW FIX: {exc}"
        print(message, file=sys.stderr)
        return message
    return ""


def main(argv=None) -> int:
    """`python3 -m library.tools.edit_depth <verb>`:

    `list` - the ten classes with owners and displays.
    `route <class> <layer>` - owning or display (exit 2 on display).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="library.tools.edit_depth",
        description="Name the deepest source that owns an edit.")
    parser.add_argument("verb", nargs="?", default="list",
                        choices=["list", "route"])
    parser.add_argument("edit_class", nargs="?", default="")
    parser.add_argument("layer", nargs="?", default="")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.verb == "list":
        for name in classes():
            owner = OWNERS[name]
            print(f"{name}: OWNS {owner['layer']} "
                  f"[{owner['store']}]")
            print(f"    displays: {', '.join(DISPLAYS[name])}")
        return 0
    try:
        verdict = classify(args.edit_class, args.layer)
    except EditDepthError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    print(verdict)
    if verdict == "display":
        print(f"owner: {OWNERS[args.edit_class]['layer']}")
        print(f"deep path: {DEEP_PATH[args.edit_class]}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
