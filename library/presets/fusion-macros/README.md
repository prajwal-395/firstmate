# Fusion macros

Two macros ship here, `intro_lower_third.setting` and
`outro_subscribe.setting`, and `library/templates/default_brand.yaml`
names them **by direct path** (`content.intro_template` /
`content.outro_template`). That direct path is the only route to a
timeline: `preset_indexer.py`, which used to build an index out of the
`.meta.json` descriptors, was removed along with the PowerGrade route it
served. The two descriptors that remain are checked against the assets
and the template by `tests/test_title_macros.py`, so a filename can no
longer drift apart from its descriptor unnoticed.

Three transition descriptors (`glitch_transition`, `slide_left`,
`smooth_zoom_in`) were removed rather than left describing transitions the
pipeline could not draw: `transition_selector` used to select one, the
loader failed, and the renderer silently substituted `fade_to_black` - a
transition nobody chose. Transitions are now drawn by
`library/tools/fusion/effects.py` from the fixed vocabulary in
`library/tools/transition_vocabulary.py`.

The two that remain belong to the titles path, which no pipeline step
imports into a timeline yet. The assets are real; the wiring is what is
missing.
