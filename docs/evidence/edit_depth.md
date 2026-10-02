# `library.tools.edit_depth` - the history behind its contract

This is the module docstring of `library/tools/edit_depth.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
For any edit, name the deepest source that owns it.

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

The twelve classes, and where each lives
----------------------------------------
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
   A pin may carry a `zoom` beside the place - Resolve's `ZoomX`/
   `ZoomY`, which `scaling` (the Scaling MODE, Crop) cannot say -
   and a reel may declare its own caption row
   (`external/reel_caption_row.json`) over the project fraction.
   DISPLAYS: timeline Transform. No capture route: a hand move must
   be transcribed into the JSON by hand.
4. `picture_position` - OWNS, shared: computed punch-in aim
   (`subject_framing`) vs declared (`captain_edits`
   `transform_override`, capturable from the live timeline). An
   override may carry `reel`: the same words with a reel scope hold
   on that reel alone, so a Pan on a shot four reels share is
   expressible. DISPLAYS: timeline Transform.
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
   text (`effect.timed_text_overlay`) - and a captain's deletion
   (`external/do_not_draw.json`), which suppresses the placement
   while the plan keeps the record of what was intended. A re-render
   overwrites the rendered segments; authored copy can still
   misspell, which is what the coherence wording scan catches.
   DISPLAYS: rendered segments, V6 timeline items.
10. `marker_feedback` - OWNS: `marker_feedback` reads the typed notes
    off the timeline durably; `marker_routing` routes each to the
    step that owns the decision (17 routed, ambiguous/unrouted
    reported never forced); `marker_resolution` records the answer.
    DISPLAYS: marker files, ROUTED-NOTES.md.
11. `ending` - OWNS: `external/reel_ending.json`, read by
    `library/tools/reel_ending.py` and applied at the reel's RANGES
    seam. Where a reel stops and what draws over its tail were
    properties of nothing: a tail extension was the only vocabulary
    for "give the ending room", and on Reel 13 it crossed a master
    cut, admitted twelve frames of the next speaker, and took the
    switch-off with it. An ending TRUNCATES and never extends.
    DISPLAYS: the reel's last timeline items, the tail comp.
12. `caption_timing` - OWNS: `external/caption_timing.json`, read by
    `library/tools/caption_timing.py` and applied to the rendered
    caption segments before they are placed. `clip_timing` above
    silently meant PICTURE timing: `span_retime` holds keep-range
    edges and has no word for a card moved off the picture under it.
    Beside it rather than inside it, for the reasons that module's
    docstring measures. DISPLAYS: caption timings on the timeline.

`tests/unit/resolve/test_composer.py`.
```
