# Project typeface

`prep_remotion` copies every font in this directory into Remotion's
`public/brand/`, which is where a `font_file` in the project's
`effect.timed_text_overlay` resolves.

Through the 4th Wall's declaration names `NanumPenScript-Regular.ttf`.
That file is deliberately NOT here: per-series typefaces live with the
project that owns the series, never in the engine (captain's ruling,
2026-08-20), and this fixture is inside the engine. The real project
folder carries the file and its SIL OFL 1.1 licence text beside it.

`tests/test_night_card_delivery.py` therefore renders the card with the
engine's own bundled Montserrat substituted for the typeface, changing
nothing else about the declaration, and separately asserts that the
declared file's absence FAILS the render rather than substituting.
