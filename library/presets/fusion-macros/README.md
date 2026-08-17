# Fusion macros

**This directory ships no `.setting` files.** It once held two title
macros, `intro_lower_third.setting` and `outro_subscribe.setting`, named
by direct path from `default_brand.yaml`. Both were deleted under Q7
(2026-08-16) along with their `.meta.json` descriptors,
`tests/test_title_macros.py` and the two template keys:

- neither was a title over the picture - each was an opaque full-frame
  slate that replaced it, one in pure red, one in black;
- both set their type in Helvetica, which appears nowhere in the brand
  planning corpus;
- `outro_subscribe` read "Subscribe!", which
  `overall_branding_creative_direction.md:100` forbids by name;
- neither could load anyway: both opened `Tools = ordered() {`, which
  AGENTS.md lists under the Fusion rules as a thing that fails;
- and the route was wrong regardless. `apply_macro_to_transition` passes
  the file to `ImportFusionComp`, which takes a `.comp`; a
  `MacroOperator` `.setting` belongs in Fusion's Templates directory.

Three transition descriptors (`glitch_transition`, `slide_left`,
`smooth_zoom_in`) were removed earlier rather than left describing
transitions the pipeline could not draw: `transition_selector` used to
select one, the loader failed, and the renderer silently substituted
`fade_to_black` - a transition nobody chose. Transitions are now drawn by
`library/tools/fusion/effects.py` from the fixed vocabulary in
`library/tools/transition_vocabulary.py`.

Intro cards, outro cards and end cards are a real capability and they are
wired - as Remotion compositions a brand template declares in
`content.bookends`, placed on the timeline through the assembly manifest.
See `library/tools/bookends.py`.

`library/tools/fusion_macro_loader.py` still exists and now has nothing
here to load. It is a generic reader rather than a route, so whether it
goes is its own decision, recorded in section 5 of
`docs/PIPELINE_PLAN.md`.
