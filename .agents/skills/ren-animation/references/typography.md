# Typography

Use the project's declared typeface and style. Do not apply generic
pixel sizes, weights, line lengths or letter spacing as creative
defaults. Read `library/tools/render_fonts.py` for supported fonts and
validate the actual glyphs and rendered layout.

Measure text in the target output dimensions. Wrap and fit using the
renderer's measured text layout; inspect for clipping, collisions and
readability at the intended viewing size. Keep changes to one card or
segment scoped to that element unless the request names a broader restyle.

For Ren subtitles, the implementation in
`remotion-subtitles/src/compositions/SubtitleOverlay` owns measured
layout, outline, fit and emphasis behavior. Treat those as renderer
behavior, not as a general creative style to apply elsewhere.
