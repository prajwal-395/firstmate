# The safe area and the caption fitter that had never run

Moved from `tests/unit/captions/test_caption_layout.py`.

Two halves of one defect, tested together because they are one number seen
from two sides. `plan_subtitles.text_fits_on_screen` measured real glyph widths
with PIL and was switched on by `audio_spine["subtitle_style"]["font_path"]`.
No producer anywhere wrote `subtitle_style` into the spine, so `fits_fn` was
None on every run and grouping fell back to a literal `max_chars = 18`. That
literal was written for a 58px caption; at the 160px style the same step
resolves from the brand template it is not close - measured on the shipped
export, caption ink spanned **columns 0-1079 of a 1080px frame**, with the single
word "announcement" clipped at both edges (cards like 'brand template and' drew
1707px of ink; 'announcement' alone is 1303px).

The width it should have been fitting inside did not exist either:
`grep -rni "safe.area"` over `library/`, `remotion-subtitles/src/` and `tests/`
returned one comment and no code, and caption placement was the literal
`bottom: 200px` - 10.4% of a 1920-row frame, inside the band the platform
paints its own caption and audio bar over. So: one enumeration
(`library/tools/safe_area.py`), four consumers, and a fitter that actually runs.

What size captions should be is an open captain decision; the tests assert only
that whatever size is chosen ends up inside the frame.
