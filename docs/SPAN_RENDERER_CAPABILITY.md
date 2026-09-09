# What the full-frame span renderer can draw, and where animation-first stops

Companion to `docs/ANIMATION_FIRST_REFERENCE.md` (the sibling lane's
frame-by-frame read of the captain's 2026-09-08 reference). That document
measures the TARGET. This one inventories the MECHANISM: what
`full_frame_span` - the one pipeline element that owns the frame instead
of decorating it - can actually put on screen today, verified against the
code, and the exact places where an animation-first picture track cannot
go through the pipeline yet. That second list is the upgrade path.

All file:line references are to this repository at the commit below.

## 1. The structural answer, confirmed independently

The sibling's headline is correct and I verified it from the other side:
every composition in `remotion-subtitles/src/compositions/` renders with
`--transparent` for compositing over footage except `FullFrameCard`,
whose own docstring states the difference
(`FullFrameCard/index.tsx:1-26`) and whose render path deliberately does
NOT pass `--transparent`
(`library/tools/full_frame_element.py:1622-1632`). A span suppresses
footage video on V1 and keeps spine audio
(`library/tools/reel_build.py:1685-1699`), so a reel whose picture is
animation is structurally representable: one `full_frame_span`
declaration, one abutting opaque segment per keep range, placed on V1.

## 2. What a span segment can draw today

Each item below is a verified capability, not a plan.

- **Opaque full-frame segments, abutting exactly.** Segment `i` covers
  keep range `i` in `int(round(end*fps)) - int(round(start*fps))` frames,
  the same arithmetic footage placement uses, so segments abut with no
  holes (`full_frame_element.py:1451-1458`). Rendered ProRes 4444, one
  file per segment, through the `FullFrameCard` composition
  (`full_frame_element.py:1578-1619`).
- **A flat declared ground.** One `background` colour for the whole span
  (a shared field). No default exists; the component throws without one
  (`FullFrameCard/index.tsx:370-377`).
- **A centred column of type.** Runs stacked with a 20px gap
  (`RUN_GAP_PX`), centre-aligned, line-height 1.18, positioned in the
  safe box or on a declared normalised `y`
  (`FullFrameCard/index.tsx:450-487`). Each run carries literal text or
  a binding, a `type_role`, a declared colour, and an optional declared
  `font_size`.
- **Speech quotation without invented copy.** `range_line` binds a
  segment's run to the words spoken in its own keep range, verbatim
  (`full_frame_element.py:444-452`). `opening_line` / `speakers` /
  `reel_number` quote the reel's own measured facts the same way.
- **Word-paced reveals off measured timings.** With `word_sync`, the
  `typewriter` / `mask` / `draw` entrances are paced off the
  transcript's own word windows, measured per segment at plan time
  (`full_frame_element.py:1420-1442`):
  `mask` rises each word out of its own mask (translateY 120% to rest,
  easeOutCubic across the word's own start-to-end window);
  `draw` resolves each word from scale 0.85 + blur 4px + opacity 0;
  `typewriter` reveals karaoke-style, stepping on word starts
  (`FullFrameCard/index.tsx:244-267`, `:317-329`, `:160-182`).
- **Current-word emphasis.** A declared `emphasis_colour` restyles the
  word whose measured window contains the frame (start-inclusive,
  end-exclusive); every other word keeps its run's colour, and an
  undeclared emphasis draws nothing
  (`FullFrameCard/index.tsx:434-440`, `:269-288`).
- **One static project still per segment.** A file from the project's
  own `brand_assets/`, staged to Remotion's `public/brand/`, drawn above
  the runs, contain-fit, at a declared width or fitted to the safe box.
  A name that resolves to nothing refuses at plan time
  (`full_frame_element.py:1532-1551`,
  `FullFrameCard/index.tsx:489-520`).
- **Block-level entrances and exits.** The full motion-character set
  (`cut fade slide scale mask draw blur typewriter glitch flip`) for
  whole-card arrival/departure on the frame clock, drawn once in
  `MotionGraphics` and imported, not respelled
  (`FullFrameCard/index.tsx:36-44`, `:441-448`).
- **Declared typeface, deliverability-checked.** The family is the
  declaration's; `render_fonts` refuses a face it cannot deliver rather
  than substituting (`full_frame_element.py:812-824`).

## 3. Where animation-first cannot go through the pipeline yet

Each item names the file where the assumption lives and what would have
to change. Ordered by how much of the reference it blocks.

1. **No model plans a span.** `reel_semantic_visual.py` builds a
   planning request only for the V6 overlay layer (`motion_graphics_plan`
   entries timed to anchor phrases). No request file, answer schema, or
   resolver exists for span copy, entrances, or emphasis. A span's
   per-beat picture today is hand-declared in the project, or bound to
   `range_line` quotations. A reel whose animation is reasoned from its
   speech has no planning step. (Reference needs: §1 - every picture
   event illustrates a noun.)
2. **One ground for the whole span.** `background` is a shared field
   (`_shared_fields`); per-segment grounds are not declarable. The
   reference inverts light-to-black mid-piece (luma 0.525 to 0.116 in
   one frame) and that inversion IS a beat. A span cannot change value
   between segments.
3. **Segments coincide with keep ranges, and only those.** A segment
   count unequal to the range count is refused by name
   (`_plan_span`, `full_frame_element.py:1473-1480`). Keep ranges are
   edit points where bad takes came out - typically one per reel. The
   reference paces picture on speech beats *inside* continuous speech;
   a span cannot subdivide a range, so a one-range reel gets a
   one-segment span: a 13-second card, not an animated reel.
4. **The reveal begins at the word; the reference completes on it.**
   `wordCuedChars` shows the last cue whose `start` has passed
   (`FullFrameCard/index.tsx:168-182`) - karaoke stepping. The reference
   fades each word up starting ~100 ms BEFORE its spoken onset so it
   reaches full opacity ON the syllable, and its picture events lead
   their nouns by 67-839 ms. No lead parameter exists anywhere in the
   span path; a lead would have to be a declared per-cue offset with a
   plan-side refusal when it pushes a cue off its word.
5. **`word_sync` admits only a quotation of the range.** Runs beside
   `word_sync` must read exactly the range's spoken words or the segment
   is refused (`full_frame_element.py:1412-1419`). Eyebrow copy, a
   payload word at 3.3x beside a grey lead-in, or any composed
   lead-in/payload optical unit cannot be cued - and the component lays
   runs out as a centred single-style column anyway (§2 above).
6. **The image slot is a still, not a subject.** One static centred
   picture above the type; no transform over time, no full-bleed
   placement, no second image, no object that splits, moves, or is
   replaced through defocus. There is no camera, no world coordinates,
   no shadow, no focus property, no shape that both draws and clips
   (sibling gaps 1, 3, 4, 5, 8 - all confirmed absent from the
   component's props and drawing code).
7. **The ground has no depth.** No texture, no vignette, no
   light-direction property. A flat `#RRGGBB` cannot answer "what holds
   the frame when nobody is on screen" (reference §3: lit surface,
   vertical vignette ratio 0.47, persistent scored line, shallow depth
   of field).
8. **No frame quantiser.** Everything renders per-frame smooth; motion
   on twos (the reference's 15-fps-in-30-fps read, one integer) is not
   expressible.
9. **Assembly and grading need Resolve.** `build_reel_timeline` places
   cards against a live Resolve project and the F22 conformance check
   grades the placed timeline. Rendering segments to files and
   concatenating them beside spine audio with ffmpeg exercises the
   pipeline's planner and renderer but NOT its placer or its verifier -
   any claim proven that way must say so.
10. **Engine-stated type magnitudes survive as fallbacks.** When a run
    omits `font_size`, the component draws `display/supporting/micro`
    at 56/36/24 px and weights 900/700/600
    (`FullFrameCard/index.tsx:139-149`). These are shared with the
    overlay roster, not a house look, but they are numbers the engine
    authored: a declaration that states every size avoids them, and the
    upgrade path should record that the fallback exists.

## 4. Renderer verdict, concurred

Remotion alone reaches the reference's bar: camera, textured ground
with vignette, whole-frame defocus, surface coordinates, clip-path
light shapes, cue leads, and a frame quantiser are all CSS transforms,
filters, clip-paths, gradients, opacity ramps, offsets, and integer
frame math - no shader, no solver, no pointer. HyperFrames is not
earned by this piece (no shader transition in it), and Fusion's one
honest contribution would be true lens defocus with bokeh rather than
gaussian blur, which the reference's matte surfaces do not visibly
need. See `docs/ANIMATION_FIRST_REFERENCE.md` §10, which this lane
concurs with after reading the component's drawing code: the hardest
single effect there (type clipped by a rotating textured cone) is a
`clip-path: polygon()` plus a masked gradient.

## 5. What was NOT changed to write this

No engine code was modified. This document is a read, not a change:
the span path (`full_frame_element.py`, `reel_build.py` span
suppression, `FullFrameCard/index.tsx`) already carries an
animation-first picture track structurally, and what it can draw is
inventoried in §2. The honest finding is proportionate: the pipeline
CAN place model-authored visuals on measured words (prior art) and CAN
carry animation as the picture (the span); what it cannot yet do is
AUTHOR that animation from speech (§3.1) or DRAW most of its vocabulary
(§3.2-3.8). Added-value work belongs in that order: a span-planning
step first, renderer primitives second.
