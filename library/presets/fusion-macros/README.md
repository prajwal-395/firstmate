# Fusion macros

**This directory ships no `.setting` files.** Every `.meta.json` here is a
descriptor whose `file_path` points at a Fusion macro that does not exist,
so `fusion_macro_loader.load_macro` returns `{}` and
`apply_macro_to_transition` returns `False` for all of them.

Three transition descriptors (`glitch_transition`, `slide_left`,
`smooth_zoom_in`) were removed rather than left describing transitions the
pipeline could not draw: `transition_selector` used to select one, the
loader failed, and the renderer silently substituted `fade_to_black` - a
transition nobody chose. Transitions are now drawn by
`library/tools/fusion/effects.py` from the fixed vocabulary in
`library/tools/transition_vocabulary.py`.

The two remaining descriptors (`intro_lower_third`, `outro_subscribe`) have
the same problem and belong to the titles path, which is not wired to them
yet. Adding a real `.setting` file next to a descriptor is all that is
needed to make it work.
