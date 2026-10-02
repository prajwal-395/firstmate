# Caption Tilt and Height

Each graphic's Tilt is derived from ITS OWN height, never a fixed one.

The captain, 2026-09-17, on Reel 01's hand-set captions: "pan and tilt are relative to graphic frame size and video frame size". His durable point: a caption row must be declared as a PLACE in pixels and each graphic's Tilt derived from THAT graphic's own height, because one stored value places two differently sized graphics in two different places (shift_px = value * clip_dim / frame_dim - `library/tools/resolve_transform.py`, measured on 16 rendered plates).

Every caption graphic in the project today is 904x480, so a fixed 480 in the derivation would look correct everywhere and be wrong the moment one graphic differs - which is what varying tight canvases will cause.
Audited 2026-09-17: every placement call site already passes its own canvas dims - `tight_box.placement_for_box` (all four caption paths), `mg_tight_box`, and `overlay_intent.transform_for` (computed at placement time against the canvas going down) - and both builders place each segment's own `tight_box.placement` verbatim. No engine change was made; this file pins the property so the coming varying-height work cannot regress it silently.

A test that only exercises one size proves nothing about his point, so this one places THREE different heights on the same declared row and asserts all three land on the same screen centre. A fixed-480 derivation would miss by 100+px on the other two (shown in the second test), which is what makes the first one mean something.

## The 1px lift

Moved from `tests/test_subtitle_style.py`. Measured 2026-09-11: all 20 Reel 13
tight captions corrected uniformly from computed Tilt -850.0 to -870.0 - 20
units on the 480-floor canvases, exactly 10px as drawn under the measured 2x
gain - with an exported still correlation-scanning the corrected canvases onto
frame row 1155 and their ink onto the caption row. The design row was
systematically high by that 10px, so the probe props carry the corrected lift:
the safe-area profile itself (platform fact) does not move, the other three
insets do not move, and captionMaxWidth still derives from the unlifted
left/right. This supersedes the +11px Reel 09 value: that correction was read
under the pre-#960 single-gain relation, and the row it produced draws 10px
high on the current carrying.

`placement_for_box` inverts the one measured Resolve relation,
shift_y = -Tilt * canvas_h / frame_h at native scale: a 480-tall canvas moves a
QUARTER of a delivery pixel per Tilt unit, so the captain's 40-unit correction
is 10px and a canvas centred at full-frame y 1395 reads Tilt -1740.0 while the
+11-era design row (centre 1385) reads -1700.0. Those two numbers are what
Reel 13 and Reel 09 actually store, read off the live timelines. Pinned as
history at explicit gain 1.0 (HISTORY_GAIN in `test_tight_box.py`); under
today's gain the same rows store half these Tilts
(`tests/test_draw_gain_measured.py`).
