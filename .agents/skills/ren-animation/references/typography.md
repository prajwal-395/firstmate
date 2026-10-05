# Typography

Type sizes, fitting and the rules that keep rendered text readable.
Sizes are for 1920x1080; portrait at 1080 wide uses about the same
pixel sizes. These are measured starting points, not defaults.

## Sizes

| Role | Size (px) | Weight |
|---|---|---|
| Hero | 140-200 | 600-800 |
| Title | 96-120 | 600-800 |
| Subtitle | 44-56 | 400-500 |
| Body | 44-60 | 400-500 |
| Caption | 28-34 | 400-500 |

Never below 28 px. Weights: title 600-800, body 400-500.

## Families

One family, two at most: a display face for titles, a text face for
body. Ship the font files with the project. Check they contain every
glyph the text uses - a missing glyph renders as a blank box or a
fallback that breaks the layout.

## Letter-spacing

- Big titles: -1 to -3 (tightens the display face).
- Small caps labels: +2 to +6 (opens up the small size).

## Line height and wrapping

- Line height: 1.2-1.35x font size.
- Max ~8 words per line, ~3 lines per card.
- Measure text width before laying out. Wrap with a measured
  line-breaker, not a character count. A character count has no
  relation to pixels and breaks on any font change.
- Shrink to fit with a loop over sizes, memoize the result. A single
  word wider than the usable width is an unbreakable inline block -
  shrink the whole card's font size for that card only, never clip.

## Emphasis

Emphasis is a size change, not a transform. Growing text with
`transform: scale()` grows the glyph without reserving layout width,
so an emphasised word overflows its box and collides with its
neighbours. Use `font-size` for emphasis; use `transform: scale()`
only for the entry animation, and only to grow INTO place from 0.95
- it never exceeds 1, so it cannot overlap.

## Contrast

Body text at least 4.5:1 against its background. Check suspicious
frames at full size - a gradient behind text can drop contrast below
the threshold even when the solid colour passes.

## Subtitle-specific rules

Ren's subtitle overlay (`remotion-subtitles/src/compositions/SubtitleOverlay`)
carries these measured rules:

- The caption box is centred and bounded by the safe-area insets
  from `library/tools/safe_area.py`. The horizontal padding equals
  the outline width, so the INK (not just the type) stays inside
  the usable width.
- A caption's `fitScale` shrinks the whole card so its widest word
  fits the safe area. It never changes the style's font size, only
  that one card's.
- The outline is drawn in 8 directions plus a drop shadow, so the
  text stays readable over any background.
- An emphasised word is 1.14em (font size), not a transform scale.
- The accent sweep (colour change while the word is spoken) is a
  colour tween, not a transform.
