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

`motion_elements_toon` is the whole roster, with `motion_elements_legend`
defining every column. Nothing has been shortlisted for you. Read the `never`
column: an entry that only says what a thing is teaches you to reach for it
everywhere, so each one records what it must not be used for.

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
occupies, counting away from the edge. Two elements anchored `bottom_centre`
over the same seconds with `row: 0` and `row: 1` stack; two with the same row
would sit on top of each other, so give them different rows.

Overlapping entries are composited together automatically. You do not need to
avoid overlaps in time, and you should not space graphics out to avoid them.

## What the renderer can draw today

`motion_graphics_frame.elements_the_renderer_draws_today` names them. The rest
of the roster is in front of you because the roster describes what an editor
needs, not what this renderer happens to support - but an entry naming an
element outside that set **will be dropped**, by name, with the reason
recorded on this step's output. Plan one deliberately if the piece genuinely
wants it and you want the gap on the record; do not plan one casually.

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
  table actually shows - a phrase nobody said is dropped, by name.
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

## Rules

- How many graphics this video gets is a creative decision, not a quota.
  An empty plan is a legitimate answer when the piece does not want any, and
  it is recorded as a decision rather than as an absence.
- Every entry must earn its place against the `earns_its_place` column: name
  the question the viewer is left with if it is not there. If you cannot,
  it is decoration - and decoration belongs in the `persist` register,
  declared as chrome, not smuggled in as meaning.
- Do not restate words the captions are already showing at the same moment.
  Two renderings of one sentence is clutter, not emphasis.
- `copy` is what the graphic SAYS. Nothing else in this pipeline produces it,
  so an element the roster marks `copy: required` with no copy is dropped.
- Keep every element inside the safe area. `motion_graphics_frame.safe_area_px`
  is the platform's keep-clear band, and the anchors already respect it.

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
  "color": "#RRGGBB",
  "colour_role": "text | outline | accent",
  "entrance": "cut | fade | slide | scale | mask | draw",
  "exit": "cut | fade | slide | scale | mask | draw",
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
  "color": "#RRGGBB",
  "colour_role": "text | outline | accent",
  "entrance": "cut | fade | slide | scale | mask | draw",
  "exit": "cut | fade | slide | scale | mask | draw",
  "footprint": 1.0,
  "emphasis": 1.0,
  "why": "the question this answers for the viewer"
}
```

`copy` is omitted for an element the roster marks `copy: none`. `footprint`
and `emphasis` are optional; omit them and the element is drawn as the
composition draws it, which is not a size anybody chose for this video.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->
