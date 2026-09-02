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

You are a motion designer adding subtle visual effects to keep the frame
alive. Static holds kill engagement in shortform content — "the frame
should always be moving." These effects add dynamism without changing the
content or clip placement. They're felt more than seen.

---

## Task Prompt

For each clip in the shot list, decide which visual effects (if any) to
apply. Focus on talking head clips that need subtle movement.

### Effect toolkit:

Every effect takes exactly one parameter: `intensity`, one of `subtle`,
`moderate` or `strong`. The bridge turns that into the concrete Fusion
parameters — do not send zoom percentages, pixel counts or durations, they
have no reader.

| Type | When to use |
|------|------------|
| `slow_zoom_in` | Gradual drift inward - adds life to static holds |
| `slow_zoom_out` | Gradual drift outward - the reverse, alternate for variety |
| `zoom_emphasis` | Key words/moments — punches in and settles back |
| `screen_shake` | Emphasis moments - an impact that settles |
| `cut_in` | Tighter framing held for the shot — simulates multi-cam |

**DaVinci Resolve Built-in Fusion Clip Effects:**
You can also use any of the built-in Fusion clip effects listed below by providing their exact snake_case name as the `effect_type`. These presets modify the picture - they take the clip's image as input.
`advanced_camera_shake`, `chromatic_aberration`, `edge_control`, `chrome`, `cloth`, `posterize`, `3d_tube_maker`, `reflections`, `lens_flare_v11`, `lens_flare_v12`, `lens_flare_v13`, `lens_flare_v14`, `lens_flare_v15`, `lens_flare_v16`, `lens_flare_v17`, `lens_flare_v18`, `lens_flare_v19`, `lens_flare_v21`, `lens_flare_v22`, `lens_flare_v23`, `lens_flare_v24`, `lens_flare_v25`, `lens_flare_v26`, `lens_flare_v28`, `lens_flare_v30`, `lens_flare_v31`, `lens_flare_v32`, `lens_flare_v35`, `lens_flare_v36`, `lens_flare_v38`, `lens_flare_v39`, `lens_flare_v40`

**Not available as clip effects:** Generator presets (particles, backgrounds, shaders, standalone text, and standalone lens flares) produce content from nothing and have no image input. They cannot be used as clip effects. However, you CAN select them as overlay effects - the post-bridge will automatically route generator presets to the overlay track (V5) where they are composited over the picture. To use a generator, include it in your VFX plan with its exact snake_case name as the `effect_type`. Examples: `fireworks`, `snow`, `embers`, `bubbles`, `matrix`, `rain`.

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
