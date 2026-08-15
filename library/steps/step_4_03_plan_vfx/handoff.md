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
| `slow_zoom_in` | Nearly always on talking head clips — makes static shots feel alive |
| `slow_zoom_out` | The same, drifting the other way; alternate for variety |
| `zoom_emphasis` | Key words/moments — punches in and settles back |
| `screen_shake` | Emphasis moments — an impact that settles. Use sparingly (max 2-3 per video) |
| `cut_in` | Tighter framing held for the shot — simulates multi-cam |
| `cut_out` | Wider framing held for the shot — creates visual variety |

**DaVinci Resolve Built-in Fusion Clip Effects:**
You can also use any of the built-in Fusion clip effects listed below by providing their exact snake_case name as the `effect_type`. These presets modify the picture - they take the clip's image as input.
`advanced_camera_shake`, `chromatic_aberration`, `edge_control`, `chrome`, `cloth`, `posterize`, `3d_tube_maker`, `reflections`, `lens_flare_v11`, `lens_flare_v12`, `lens_flare_v13`, `lens_flare_v14`, `lens_flare_v15`, `lens_flare_v16`, `lens_flare_v17`, `lens_flare_v18`, `lens_flare_v19`, `lens_flare_v21`, `lens_flare_v22`, `lens_flare_v23`, `lens_flare_v24`, `lens_flare_v25`, `lens_flare_v26`, `lens_flare_v28`, `lens_flare_v30`, `lens_flare_v31`, `lens_flare_v32`, `lens_flare_v35`, `lens_flare_v36`, `lens_flare_v38`, `lens_flare_v39`, `lens_flare_v40`

**Not available as clip effects:** Generator presets (particles, backgrounds, shaders, standalone text, and standalone lens flares) produce content from nothing and have no image input. They cannot be used as clip effects. However, you CAN select them as overlay effects - the post-bridge will automatically route generator presets to the overlay track (V5) where they are composited over the picture. To use a generator, include it in your VFX plan with its exact snake_case name as the `effect_type`. Examples: `fireworks`, `snow`, `embers`, `bubbles`, `matrix`, `rain`.

### Rules:
- You MUST plan at least 3-7 VFX items across the video. An empty list is a failure.
- Use only the type names above or an exact built-in effect name. Anything
  else is dropped, not approximated.
- Every A-roll talking head clip >3 seconds MUST have at least `slow_zoom_in`
  or `slow_zoom_out`
- Screen shake: sparingly — max 2-3 per video
- Zoom emphasis: only for genuinely important moments
- Animation timing: 100-200ms for micro-animations, never >500ms
- Easing: Bezier curves, not linear
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

1. Every talking head clip >3s has at least slow_zoom
2. Screen shake used ≤3 times
3. Parameters within style spec ranges
4. No effect changes clip in/out points or timeline position

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
