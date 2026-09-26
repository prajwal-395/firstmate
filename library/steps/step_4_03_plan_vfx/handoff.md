# Step 4.3: Plan Visual Effects — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 4.3 |
| Name | Plan Visual Effects |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Phase 3 complete (Step 3.4) |

---

## System Context

You are a motion designer planning visual effects. These effects modify the picture without changing the content or clip placement.

---

## Task Prompt

For each clip in the shot list, decide which visual effects (if any) to apply.

### Effect toolkit:

Every effect carries a `params` object, and the VALUES in it are yours.
How far a drift travels, how hard a shake hits and how tight a reframe
sits are the decisions this step exists to make: there is no scale to
pick from, no default, and no ceiling.

What is NOT yours is the parameter NAMES. The renderer dispatches on
them, so a name it does not read draws nothing and reports nothing - the
plan would record an effect the viewer never sees. Use the names in the
table; an entry whose `params` name none of them is dropped, with the
reason, rather than passed on to draw nothing.

| Type | When to use | Parameter names |
|------|------------|-----------------|
| `slow_zoom_in` | Gradual drift inward - adds life to static holds | `zoom_start`, `zoom_end`, optional `pan_end` |
| `slow_zoom_out` | Gradual drift outward - the reverse, alternate for variety | `zoom_start`, `zoom_end`, optional `pan_end` |
| `ken_burns` | The drift move under its common name - the DIRECTION is read off your `zoom_start`/`zoom_end` (`zoom_end` above `zoom_start` pushes in, below pulls out), never defaulted | `zoom_start`, `zoom_end` (must differ), optional `pan_end` |
| `zoom_emphasis` | Key words/moments — punches in and settles back | `zoom_start`, `zoom_mid`, `zoom_end` (the mid point is the punch) |
| `screen_shake` | Emphasis moments - an impact that settles | `shake_x`, `shake_y` (a FRACTION of frame width), `shake_decay_frames` (modifies the shake; draws nothing alone) |
| `cut_in` | Tighter framing held for the shot — simulates multi-cam | `zoom_start`, `zoom_mid`, `zoom_end` (all three equal holds the reframe) |
| `speed_ramp` | A stepped speed change across the block — a montage ramp, a slow push into a moment | `segments`: a non-empty list of `percent` steps, each above 0 (e.g. `[{percent: 50}, {percent: 150}]`) |
| `freeze_frame` | A held frame - time stops on the block or its anchored span | No params: the span is what freezes; OPTIONAL `hold_seconds` / `hold_frames` run the span from the anchor for exactly that long (see below) |
| `stabilize` | A shaky block you want steadied - Resolve's own Stabilize runs on the clip and its answer is judged | No params: the span is what stabilizes. Name it only where the measured stability (`vfx_suggested` camera text, `view:stability`) says the shot needs it; nothing stabilizes unless you ask. |

A zoom value of `1.0` is the untouched frame; above it is tighter, below
it is wider.

`speed_ramp` and `freeze_frame` are TIMELINE operations, not Fusion
comps: they reach the picture through Resolve's own
`TimelineItem.SetSpeed`, one constant speed per step, each judged by a
re-read. Resolve 21.1 carries no speed-curve API, so a ramp is stepped
segments, never a curve - a `curve`/`easing`/`bezier` param refuses and
you re-plan with `segments`. A freeze is spelled `freeze_frame`, never
a 0% step. Each step lands on the timeline item spanning exactly that
step; steps that subdivide one item refuse at build time (blading an
item is a cut nobody planned), so keep one step per item or anchor the
steps to item spans.

One MECHANICAL refusal applies, and it is not a taste bound: the comp
builder rejects an ANIMATED Transform Size whose peak exceeds
<!-- MAX_ANIMATED_ZOOM --> (`library/tools/fusion/nodes.py:
MAX_ANIMATED_ZOOM`, AGENTS.md §5 - the number is rendered into this
prompt from that constant, so the two cannot disagree) because of what
it does to the .comp file, and it refuses rather than clamping. A HELD
reframe - `cut_in`, where all three points carry the same value - is
not animated and is not bounded by it.

**DaVinci Resolve Built-in Fusion Effects Vocabulary (143 presets):**
You can use any of the built-in Fusion effects listed below by providing their exact snake_case name as the `effect_type`.

**Clip Effects:**
These modify the picture and take the clip's image as input.
- **tools** (Utility effects for correcting or distorting the image): `advanced_camera_shake`, `chromatic_aberration`, `edge_control`
- **looks** (Stylized visual color filters): `posterize`
- **how_to** (Advanced compositing, tracking, and displacement techniques): `reflections`, `variblur`, `lightwrap`, `corner_position_tracking`, `ambient_occlusion`, `shine`, `glow_mask`, `vectorblur`, `coordinate_rays`, `perspective_tracking`, `displace_2d`, `displace_3d`

**Generator / Overlay Effects:**
These produce content from nothing and have no image input. The post-bridge automatically routes them to the overlay track (V5) where they are composited over the picture.
- **particles** (Atmospheric and environmental overlays to add life): `bokeh_edges`, `from_text`, `smokestack`, `matrix`, `burning_engine`, `lens_leaks`, `steam`, `blowing_leaves`, `galaxy_swarm`, `bubbles`, `portal_spawn_point`, `glitter`, `shape_drift`, `flash_bulbs`, `lava`, `snow`, `fireworks`, `bokeh_full_frame`, `embers`, `fireflies`, `bubble_bar`, `bubbles_3d`
- **motion_graphics** (Animated HUD and data elements for technical aesthetics): `radar`, `scrollbar`, `radar_2`, `crazy_circle`, `circle_values`
- **styled_text** (Structural animated titles and typography): `odometer`, `circle_layout`, `fade_rotate`, `war_games`, `shading_2d`, `flip_follower`, `scramble_modifier`, `3d_follower`, `word_level_transforms`, `swap_color`, `jiggle_follower`, `path_layout`, `alien`, `rotate_follower`, `stretch_follower`, `character_level_transforms`
- **backgrounds** (Abstract moving backdrops): `circular_cube`, `rotate_rays`, `bender`, `rectangle_flythrough`, `platform_flythrough`, `bullseye`, `plasmic`, `helix`, `waver`, `visualizer`, `blue_rays`, `spot_ground`, `optics`
- **shaders** (Procedural material textures): `carbon_fiber`, `moon`, `rusty_metal`, `brick`, `cool_metal`, `gold_bump`, `chrome`, `rock`, `cobblestone`, `tiles`, `chrome_checkerplate`, `checkerplate`, `cloth`, `sand`, `anisotropic`, `marble`, `plywood`, `brushed_metal`, `metal_grill`, `car_paint`, `steel`, `concrete`, `honeycomb`, `planet`, `wood`, `lizard`, `glass_dot`
- **generators** (Basic procedural patterns): `color_wheel`, `3d_tube_maker`, `checker`, `color_bars`
- **lens_flares** (Optical camera artifacts): 40 varieties are available. To use one, provide the name `lens_flare_vXX` where XX is a two-digit number from `01` to `40` (e.g., `lens_flare_v12`, `lens_flare_v03`).

### Rules:
- How many effects the video gets is a creative decision, not a quota. Plan
  the effects the piece needs and no others; an empty list is a legitimate
  answer for a piece that wants stillness. (Captain's ruling of 2026-08-20,
  `decision-creative-floors.md`: there are no creative floors. A floor in
  this prompt pads the edit exactly as effectively as one in the bridge -
  the range this section used to demand, together with a per-clip zoom
  requirement, is what put one zoom on each of exactly eight clips,
  alternating direction, into the shipped project 001.)
- Use only the type names above or an exact built-in effect name. Anything
  else is dropped, not approximated. (`speed_ramp` needs `segments`;
  an entry without them is dropped with the reason, and a `curve` param
  refuses - nothing rounds a curve to constants on your behalf.)
- A static talking-head shot held for a long time is where `slow_zoom_in` /
  `slow_zoom_out` earns its place - use it where it helps and leave it off
  where it does not.
- Effects modify display, not timeline positions

### Sub-block anchors (an effect smaller than its block):

Every entry names its block (`target_block_position`), and an effect
that belongs to a MOMENT inside that block - a punch on one word, a
shake on one beat - says so with `anchor` (where it starts) and
`anchor_end` (where it ends). Either alone leaves the other end on the
block boundary; neither spans the block exactly as before. A punch
that spans the word "quit" carries `anchor: {word: "quit"}` and
`anchor_end: {word: "quit", edge: end}`.

A stated freeze length (E3): when the request holds the freeze for a
count - "freeze on 'quit' for 1s 12f" - a `freeze_frame` entry carries
`anchor` (where the hold starts) and `hold_seconds` (seconds, as
requested) or `hold_frames` (whole frames, as requested), and the span
runs from the anchor for exactly that. State one form, or both
agreeing; two disagreeing numbers refuse the step. A mixed count like
"1s 12f" is one number in frames: state `hold_frames: 42`, never the
two halves beside each other. The hold must fit inside the block - a
freeze past its end refuses. No `anchor_end` beside a hold (two ends),
no hold without an `anchor` (no start), and no hold on any other
effect (it is read by nothing there and refuses).

One address per anchor (exactly one):

- `{word: "quit"}` - the word's start in this block's line
  (`occurrence: 2` for its second saying, `edge: end` for its end).
  The word must be spoken in THIS block.
- `{beat: 17}` - the 17th beat of the measured grid; `{bar: 4,
  beat: 2}` is the 2nd beat of bar 4, `{downbeat: 4}` the 4th bar
  start. Bars come from the `beatgrid` view in your context. `grid:
  detected` demands the tracker-heard grid and refuses an estimated one.
- `{section: "chorus"}` - the first downbeat of the chorus
  (`occurrence: 2` for the second chorus span, `edge: end` for a
  span's end). Labels come from the `sectiongrid` view in your
  context; a label the grid did not measure refuses with what it has.
- `{motion_peak: 1}` - the block's first measured motion APEX, where
  the action peaks (`occurrence: 2` for the second apex).
  `{action_onset: 1}` - the block's first action ONSET, where the
  action starts (`occurrence: 2` for the second). Peaks come from the
  `motion` view in your context; a peak is a point, so there is never
  an `edge` on these forms.
- `{event: "Laughter"}` - the block's first measured laugh
  (`occurrence: 2` for the second span, `edge: end` for a span's
  end - default start is the onset). Spans come from the
  `soundevents` view in your context; a label the clip did not
  measure refuses with what it carries.
- `{frame: 343}` - timeline frame 343. It must fall inside the block.

Any form takes `offset_seconds` / `offset_frames` (applied after, and
still inside the block). An anchor that names nothing placeable - a
word the block does not say, a bar past the grid, a section the grid
did not measure, an event the clip did not measure, a frame outside the
block, or an end at or before its start - REFUSES the step with the
fix, and you re-plan; it never falls back to the block span.

Word emphasis: the `emphasis` view in your context names, per block,
the three most emphasized spoken words as MEASURED from pitch,
loudness and duration - with the formula stated there. Punch the word
the brief names, or the block's most emphasized word; span it with
`anchor` and `anchor_end` exactly as above. The score is context, not
an order - you still decide.

Motion: the `motion` view in your context names, per block, the
measured motion of the picture it plays - the clip's dominant
direction and kind, and the action onsets and apexes inside the block
with their timeline seconds - and `vfx_shot_stills` shows you one
representative frame per candidate block, drawn at the block's first
measured motion apex where one exists. OPEN the still for any block
you are considering before you decide it: the prose says what happens
in a clip and cannot say what it looks like. Place the effect ON the
action: a shake lands on the apex through `anchor: {motion_peak: 1}`,
a ramp leads into it through `{action_onset: 1}`. The peaks are
context, not an order - you still decide. A motion anchor on a block
whose motion is unmeasured refuses with the fix, and you re-plan; it
never falls back to the block span. `still_motion_notes` carries what
the still router saw in those stills - one motion line per file - as
a second reading beside your own; where nothing answered, the absence
is stated and the still paths above remain for you to open.

Sound events: the `soundevents` view in your context names, per
block, the non-speech sounds the block's own clip measured -
laughter, impacts, music entrances - with their timeline spans and
confidences. Place the effect ON the sound: a hit lands on the laugh
through `anchor: {event: "Laughter"}`, a punch spans it with
`anchor_end: {event: "Laughter", edge: end}`. Labels are the model's
own AudioSet words - a label no clip measured stays absent, never
guessed. The spans are context, not an order - you still decide. An
event anchor on a block whose sound is unmeasured refuses with the
fix, and you re-plan; it never falls back to the block span.

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `vfx_candidates_toon` table provides a summarized list of clips with the following fields:
- `segment_id`: The ID of the segment.
- `text`: Summary text for the segment.
- `vfx_suggested`: Pre-computed suggestion on whether VFX are needed based on motion/pose data.

Two more columns are DERIVED from where the picture is PLACED. Each says
what it measures; neither says what to conclude.

- `picture_track`: Which video track carries the picture this block shows -
  `V1`, `V2` or `none`. A speech, hook or bookend block puts a clip on V1;
  every other block - a `transition_slot` above all - shows a B-roll
  cutaway, and every B-roll placement goes on V2. An effect is a per-clip
  Fusion comp and the renderer builds them on V1 and V2 alike, so a block
  reading `V1` or `V2` can carry one. **A block reading `none` has no clip
  at all**: an effect planned there is dropped with that reason and never
  reaches the picture.
- `track_basis`: Which clip the effect would draw on, and what else is on
  screen over it. An effect on a V2 cutaway modifies the CUTAWAY's own
  frames - a zoom zooms the cutaway, a shake shakes it - because a per-clip
  comp reads the clip it sits on and nothing else; it cannot read or alter
  the A-roll on V1. *"behind the cutaway for that stretch"* means a cutaway
  is placed over this block, so an effect here draws on a picture the viewer
  does not see while the cutaway plays.

Use this data to decide which effects to apply.

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
VFX and motion design preferences as natural language. Read it for:

- Desired motion intensity (subtle and minimal vs. aggressive and dynamic)
- Specific effect types to use or avoid
- How visually restrained or energetic the final video should feel

Let the brief guide how many effects you apply and how aggressive the
parameters are. If no creative brief is provided, match the VFX intensity
to the creative direction's energy profile.

---


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

**CRITICAL RULE:** Use exactly these key names - do not rename or restructure. DaVinci Resolve reads these exact fields.

---

## Evaluation Criteria

1. Every effect earns its place - it is there because the moment wants it,
   not to satisfy a count
2. Every effect's `params` use names the renderer dispatches on, and the
   VALUES in them are the plan's own. There is no style-spec range, no
   scale the engine offers and no ceiling - only the one MECHANICAL
   refusal above (an ANIMATED Transform Size peaking over
   <!-- MAX_ANIMATED_ZOOM -->)
3. No effect changes clip in/out points or timeline position

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `shot_list`, `style_specification` |
| Writes | `enhancement_spec` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| No applicable clips | Valid — proceed with empty vfx_plan |
| Already-dynamic footage | Skip effects — footage doesn't need additional movement |
