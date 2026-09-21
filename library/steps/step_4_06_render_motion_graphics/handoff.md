# Step 4.06 - Plan the motion graphics layer

You are deciding what additive graphics, if any, this video carries, when each
one is on screen, where it sits and what it says.

Until this handoff existed nobody was asked. The layer was resolved from two
booleans in a brand template, so a project that named no template got a
fully transparent overlay on every block and the run reported motion graphics
as delivered. The template is secondary. **You are the planner.**

## What a motion graphic is here

An **additive overlay carrying meaning the picture and the captions do not
already carry**. That is the boundary:

- a treatment of the picture is VFX and step 4.03 plans it;
- a treatment of the spoken word is a caption and step 4.01 plans it;
- a change between two shots is a transition and step 4.02 plans it;
- a full frame of artwork is a bookend and the project declares it.

---

## What this renderer can and cannot draw

**Read this before you plan anything.** Everything below was measured against
the composition that will actually render your plan
(`remotion-subtitles/src/compositions/MotionGraphics/`), not inferred from the
roster. A plan written past these limits is not ambitious; it is dropped by
name, and the video ships without the thing you planned.

### The shape of the roster, stated plainly

Nineteen elements. **Copy is `required` on twelve of them and `optional` on
three.** Only **four draw no copy at all**, and three of those four are chrome
that holds under the piece rather than content: `progress_bar`, `frame_accents`
and `channel_bug`. The fourth, `beat_accent`, is a burst with no semantic
content by definition.

So the genuinely non-text content this renderer can draw is a short list, and
it is worth knowing it by name rather than discovering it by elimination:

- `comparison_bars` - labelled magnitudes at proportional LENGTH. The bars are
  the argument; the labels only say what they are.
- `website_panel` - a real page capture in drawn browser chrome, composited
  with alpha. A picture, not a description of one. Its copy is optional and is
  the address bar.
- `review_panel` - a MOCKUP of a review listing: the score, its stars, the
  count and rows carrying who wrote each review and a line of it. Drawn, not
  captured, so it needs no file - but every word, number and colour on it
  comes from the entry's own `copy` and `data`, and an entry that states none
  is dropped rather than drawn blank.
- `pointer_annotation` - an arrow, a ring or an underline drawn AT a position
  in the frame. Its copy is optional; it can point and say nothing.
- `step_counter` - a positional marker saying where in a declared sequence the
  piece is. Its copy is optional too.
- `digit_counter` / `counter_roll` - a figure that MOVES. The motion is the
  content; the number alone would be a caption.
- `subject_emblem` - a large flat mark drawn from type and shapes.

**If your last plan was text graphics and small icons, this is half the
reason.** Reaching for copy is what this roster mostly offers. The other half
is yours: a graphic that restates a sentence the captions are already showing
earns nothing, and the list above is where the range actually is.

### What it CAN do that you might assume it cannot

- **It draws a project-supplied image file.** `channel_bug` and
  `website_panel` take an `asset` - a file out of the project's own
  `brand_assets/`, staged verbatim. The engine ships no artwork and states
  none (AGENTS.md 14), so the file is the project's or the entry is dropped.
- **A graphic can ARRIVE on a spoken word.** `anchor_phrase` is searched
  against the measured word timings and the element starts on the first
  anchored word. This is a real measurement, not an estimate.
- **A staged element's stages can land on the words that say each one.**
  `list_build` and its kind take per-stage offsets, so items can appear as
  they are spoken rather than on a fixed stagger.

### What it CANNOT do, and what happens if you ask

- **No element composites a video file.** There is no video node in this
  composition. A project's existing brand motion - a logo reveal, a bumper -
  cannot be played inside this layer. That capability exists in the engine
  (`library/tools/brand_motion.py`, the `BrandMotion` composition) but it
  belongs to the full-frame and bookend path, not to you. Do not plan around
  one here.
- **No per-word reveal INSIDE a run of copy.** `typewriter` reveals characters
  at a fixed rate across a fixed ramp. The element's ARRIVAL can land on a
  word; the letters crossing the screen after that cannot. A plan whose point
  is "each word lights as it is said" is asking for something this renderer
  does not have.
- **No letter-spacing control.** Tracking is a renderer constant - 3px on a
  `display` run, 2px in a few places, 0 otherwise - and nothing you write
  changes it. A tracked-out caps wordmark is not expressible. `display` as a
  `type_role` is the closest thing, and it is a weight, not a spacing.
- **`draw` does not draw a stroke.** Despite the name, it is a scale from 0.85
  to 1 with a 4px defocus clearing - very nearly the same gesture as `blur`,
  which is an 8px defocus with a slight upward drift. There is no stroke
  reveal, no rays, no particle build and no bulb-base build on any axis. Pick
  `draw` because you want a soft scale-in, never because you want a line to be
  drawn.
- **`tracked` anchoring is not available.** The roster's nine fixed anchors
  all work; the tenth, `tracked`, follows a subject frame by frame, and **no
  per-frame subject measurement reaches this layer**. That is why
  `tracked_label` is the one element marked `needs_measurement` rather than
  `reachable_now` - the gap is the measurement, not a missing renderer node.
  An entry anchored `tracked` is dropped, and it is not pinned to a fixed
  point instead: a label that was supposed to follow a subject and does not is
  a label in the wrong place.

**These are limits, not preferences.** Widening them is a separate, declared
piece of work and is the captain's call. Naming what is missing in your
`could_not_determine` is useful; planning past it is not.

---

## The roster

`motion_elements_toon` is the whole roster. Nothing has been shortlisted for
you, and the columns are:

- `element` - the key a plan names.
- `function` - what it does in the edit.
- `what_it_is` - the element in one sentence.
- `earns_its_place` - **the question the viewer is left with if it is absent.**
  An element that answers no question here does not belong in the plan.
- `needs` - what must exist before it can be drawn.
- `never` - what it must NOT be used for, as refusals. **Read this column.**
  An entry that only says what a thing is teaches you to reach for it
  everywhere, so every one records what it must not be.
- `axes` - the dimensions a declaration must fill. None of them carries a
  value here: the magnitude is yours to choose.
- `copy` - `required` / `optional` / `none`, whether the element needs a text
  payload. Where that text comes from is not stated.
- `reachable` - `reachable_now` / `needs_renderer_work` / `needs_measurement`.
  **A report on the renderer, not a filter on the vocabulary**: the roster
  describes what an editor needs, and one written around today's renderer
  would keep its defect after the repair.

`motion_axes_toon` is the same treatment for the axes themselves.

`motion_graphics_frame.elements_the_renderer_draws_today` is the set that will
actually render. An entry naming anything else is **dropped by name**, with the
reason recorded on this step's output. It is never swapped for a neighbouring
element and never quietly rendered as nothing. Plan one deliberately if the
piece genuinely wants it and you want the gap on the record; do not plan one
casually.

## The layer has its own timebase

**A graphic is not tied to a clip, a cut or a spine block.**
`timeline_context_toon` tells you where the speech is, where the cutaways are
and what is said in each block. It is CONTEXT so that you can place a graphic
against the piece. It is **not** a grid you have to land on: a graphic may
start mid-sentence and run across three blocks, or hold for a second and a
half inside one, whichever the piece wants.

Every entry is timed one of two ways, never both in one entry. A
timed entry declares `start_seconds` and `duration_seconds` in
**timeline seconds**, measured from the start of the video. An
anchored entry (below) is timed by its words instead and OMITS both:
an entry naming an anchor phrase beside explicit seconds is dropped,
because two timings is ambiguous and nobody picks one for you.
`motion_graphics_frame.timeline_duration_seconds` is where the picture ends.
There is no default duration and no default start: a timed entry that
declares neither is dropped, because inventing one would put the
clip-boundary coupling straight back.

## Several graphics may be on screen at once, in rows

`anchor` is where in the frame an element sits - one of the nine positions in
`motion_graphics_frame.anchors`. `row` is which line **within that anchor** it
occupies, counting away from the edge.

**Overlapping in TIME is free and overlapping in a ROW is not, and the
difference matters.** Entries that overlap in time are composited together
automatically - you do not need to space graphics out to avoid that, and you
should not. But two elements at the SAME anchor in the SAME row, live at the
same moment, share one layout slot, and the second is drawn through the first.
Nothing repacks them for you: the engine MEASURES that collision and REPORTS
it, deliberately, because a full-width bar under a corner counter may be
exactly what the plan meant. So give two elements sharing an anchor different
rows.

One thing rows cannot separate: a `*_centre` anchor is given the whole usable
width, so a centred element and a corner element in the same vertical band
collide by construction. That one is reported too, and it is not a mistake if
you meant it.

## Colour, and what the brand template does

`brand_refinement` says whether this project's brand template declares a
palette.

- It does: give an entry a `colour_role` (`text`, `outline` or `accent`) and
  the palette resolves it.
- It does not, or you want something else: give the entry a `color` as a hex
  value.
- Neither: the entry is dropped. **There is no house colour.** The engine
  ships no palette and will not draw in a constant.

A project with no brand template is not a project with a reduced layer. Plan
it exactly as you would with one, and state the colours yourself.

## When the visual is about what is being said

A graphic may land because of the SUBJECT of the speech, not just as
decoration over it. When a span of speech is about something the picture
does not show - money, a place, a count - name the subject and cue the
graphic to the very words that say it:

- `subject`: free text saying what the span is about, in your own words
  ("money - paid advertising budgets"). It travels with the plan as the
  record of your reasoning; the engine never reads it to decide anything.
- `anchor_phrase`: the words from `timeline_context_toon` that say it
  ("lots of money"). The engine searches the measured word timings and
  the graphic ARRIVES on the first anchored word. Quote only words the
  table actually shows - a phrase nobody said is dropped, by name, and so is
  one whose words were said but never measured. **The visual lands on its
  words or not at all**; landing near them would be decoration pretending to
  be timing.
- `hold_seconds`: how long it stays. Omit it and the graphic lives
  exactly as long as the words. An entry naming BOTH an anchor phrase
  and explicit `start_seconds`/`duration_seconds` is dropped: two timings
  is ambiguous and nobody picks one for you.

`subject_emblem` is the element for this: a large flat mark (name it in
`copy` as the display run - "$" - with a short label beside it) on a
backplate in a colour you state. The mark is drawn from type and shapes,
never fetched, so name a mark type can draw: a glyph, a digit, a short
sign - never a face, a logo or a photograph of a thing. The same
anchoring works for any element whose moment is a spoken one: a
`counter_roll` arriving on the number it counts, a `list_build` staged to
the words that enumerate it.

Plan one only where the picture does not already show what the speech
is about. A visual on every noun is clutter, not coverage - whether a
span earns one is your judgement, not a quota, and the engine states no
count either way.

## A graphic may depict its subject, not restate the sentence

The layer's job is to show what the captions and the picture do not.
A graphic that restates a sentence the captions are already showing
earns nothing - two renderings of one sentence is clutter, not
emphasis. The roster says this on `title_lockup`'s own `never` column
(a title must distill, never transcribe) and the collision rule under
Rules gives it teeth. The positive half is yours to choose: where the
speech states a magnitude, a count, a comparison or a sequence, draw
the THING - bars at proportional length, a figure that moves, a
position in a declared sequence - rather than the words that said it.

### Which payloads you may state from the speech

The `data` axis's `resolved_against` line reads as though every
payload is somebody else's to measure. For the depicting elements it
is yours to state, from the words in `timeline_context_toon`:

- `comparison_bars`: the labelled magnitudes, where the speech states
  both sides of a comparison. Labels go in `copy`, magnitudes in
  `data.values`.
- `counter_roll`: the start and end of a stated change, in
  `data.start_value` / `data.end_value`. The change must be stated -
  growth, a countdown, an elapsed quantity.
- `review_panel`: the score, the count and the rows the speech points
  at, in `data.rating` / `data.count` / `data.rows`, plus the palette
  of the place they were written on in `data.palette`. Drawn, never
  fetched: the engine never reads the web, so what is on the card is
  what you state.
- `step_counter`: the position and the total of a sequence the piece
  genuinely declares, in `data.position` / `data.total`.
- `pointer_annotation`: where in the frame the visible referent sits,
  as a plan-side declaration in `data`. It must be visible in the
  shot it is drawn over.

What you may NOT state: a per-frame track for `tracked_label`
(nothing measures one for this layer - such an entry is dropped); a
page capture or a mark file for `website_panel` / `channel_bug`
(the file is the project's, out of `brand_assets/`, or the entry is
dropped).

### Two payload shapes that drop the entry

- Equal pairs. `data.values` of [3,3], or a roll from 5 to 5, means
  nothing as a comparison and violates the roster's own `never`
  rules. Such an entry is dropped by name
  (`data_states_no_difference` in
  `library/tools/motion_graphics_plan.py`).
- Data no element draws. `data` on an entry whose element declares no
  `data` axis - every copy element - reaches no node in the
  composition. Such an entry is dropped by name
  (`data_no_element_draws`).

## Rules

- How many graphics this video gets is a creative decision, not a quota.
  An empty plan is a legitimate answer when the piece does not want any, and
  it is recorded as a decision rather than as an absence.
- Every entry must earn its place against the `earns_its_place` column: name
  the question the viewer is left with if it is not there. If you cannot,
  it is decoration - and decoration belongs in the `persist` register,
  declared as chrome, not smuggled in as meaning.
- Do not restate words the captions are already showing at the same moment.
  Two renderings of one sentence is clutter, not emphasis. **This one has
  teeth**: an element that draws copy, anchored in the vertical band this
  project's captions occupy, over a span the caption plan puts a card on, is
  DROPPED as a collision. Put a copy-bearing graphic somewhere the captions
  are not.
- `copy` is what the graphic SAYS. Nothing else in this pipeline produces it,
  so an element the roster marks `copy: required` with no copy is dropped.
- **Every run of copy names a `type_role`** - `display`, `supporting` or
  `micro`. It is a typographic tier, not a size; it is how a figure and its
  unit are distinguishable without either naming pixels. A copy run naming no
  tier the vocabulary knows is dropped.
- An element whose roster entry declares the `asset` axis must name a file the
  project really has. No file, or a file that is not on disk, and the entry is
  dropped rather than drawn as an empty box.
- `data` belongs on an element whose roster axes include `data`, and a
  payload whose values are all equal states no relation and no change.
  Both drop the entry by name - `data_no_element_draws` and
  `data_states_no_difference` in
  `library/tools/motion_graphics_plan.py` - rather than drawing junk.
- Keep every element inside the safe area. `motion_graphics_frame.safe_area_px`
  is the platform's keep-clear band, and the anchors already respect it. If
  `motion_graphics_frame` says the frame is unknown, the delivery format was
  never declared: plan nothing that needs pixel positions until it is.

## Your answer

One entry per graphic, in one of two shapes - a timed entry is timed by
seconds, an anchored entry by its words, and one entry never carries
both timings:

```json
{
  "element": "a key from motion_elements_toon",
  "start_seconds": 0.0,
  "duration_seconds": 0.0,
  "anchor": "one of motion_graphics_frame.anchors",
  "row": 0,
  "copy": {"display": "...", "supporting": "...", "micro": "..."},
  "data": {"values": [...]},
  "asset": "a file in the project's brand_assets/",
  "color": "#RRGGBB",
  "colour_role": "text | outline | accent",
  "entrance": "cut | fade | slide | scale | mask | draw | blur | typewriter | glitch | flip",
  "exit": "cut | fade | slide | scale | mask | draw | blur | typewriter | glitch | flip",
  "footprint": 1.0,
  "emphasis": 1.0,
  "why": "the question this answers for the viewer"
}
```

```json
{
  "element": "subject_emblem",
  "subject": "free text, what this span is about - your reasoning, on the record",
  "anchor_phrase": "words from timeline_context_toon this lands on - INSTEAD of start/duration, which are omitted",
  "hold_seconds": 2.0,
  "anchor": "one of motion_graphics_frame.anchors",
  "row": 0,
  "copy": {"display": "...", "supporting": "...", "micro": "..."},
  "data": {"values": [...]},
  "asset": "a file in the project's brand_assets/",
  "color": "#RRGGBB",
  "colour_role": "text | outline | accent",
  "entrance": "cut | fade | slide | scale | mask | draw | blur | typewriter | glitch | flip",
  "exit": "cut | fade | slide | scale | mask | draw | blur | typewriter | glitch | flip",
  "footprint": 1.0,
  "emphasis": 1.0,
  "why": "the question this answers for the viewer"
}
```

`data` is carried only by an element whose roster axes include `data`,
and `asset` only by one that declares `asset` (`channel_bug`,
`website_panel`). Both are omitted otherwise. `copy` is omitted for an
element the roster marks `copy: none`. `footprint`
and `emphasis` are optional; omit them and the element is drawn as the
composition draws it, which is not a size anybody chose for this video.

A depicting entry, in full - the bars are the argument, the labels
only say what they are:

```json
{
  "element": "comparison_bars",
  "subject": "the cost gap between paid ads and organic reach",
  "anchor_phrase": "three times what we spent",
  "hold_seconds": 3.0,
  "anchor": "centre",
  "row": 0,
  "copy": {"display": "AD SPEND", "supporting": "ORGANIC"},
  "data": {"values": [3000, 1000]},
  "color": "#RRGGBB",
  "entrance": "slide",
  "exit": "fade",
  "why": "the speech compares two quantities and the relation is the point - a ratio is hard to hear and immediate to see"
}
```

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->
