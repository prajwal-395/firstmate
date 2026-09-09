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

A zoom value of `1.0` is the untouched frame; above it is tighter, below
it is wider.

One MECHANICAL refusal applies, and it is not a taste bound: the comp
builder rejects an ANIMATED Transform Size whose peak exceeds 1.04
(`library/tools/fusion/nodes.py`, AGENTS.md §5) because of what it does
to the .comp file, and it refuses rather than clamping. A HELD reframe -
`cut_in`, where all three points carry the same value - is not animated
and is not bounded by it.

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
  else is dropped, not approximated.
- A static talking-head shot held for a long time is where `slow_zoom_in` /
  `slow_zoom_out` earns its place - use it where it helps and leave it off
  where it does not.
- Effects modify display, not timeline positions

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `vfx_candidates_toon` table provides a summarized list of clips with the following fields:
- `segment_id`: The ID of the segment.
- `text`: Summary text for the segment.
- `vfx_suggested`: Pre-computed suggestion on whether VFX are needed based on motion/pose data.

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
2. Parameters within style spec ranges
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
