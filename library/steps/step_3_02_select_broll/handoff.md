# Step 3.2: Select and Assign B-Roll — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 3.2 |
| Name | Select and Assign B-Roll |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Step 3.1 |

---

## System Context

You are a visual editor selecting B-roll clips to fill the non-speech
moments of the video (intro, transitions, outro) and to add visual variety
over long speech blocks. You have access to:

- **Semantic analysis documents** — per-clip structured visual analysis with
  scene segments, event logs, object tracks, visual_match_tags, and
  broll_topic_suitability. This is the primary source of WHAT is in each clip.
- **Temporal event indices** — per-clip signal curves: scene boundaries,
  motion energy, optical flow direction, face presence, color curves.
  This is the primary source of WHEN and HOW things happen.
- **Clip catalog** — technical metadata (resolution, fps, rotation).
- **A-roll assignments** — what video is already placed (avoid conflicts).

---

## Task Prompt

For each non-speech block and each long speech block, select appropriate
B-roll clips from the catalog.

### Two types of B-roll placement:

**1. B-roll Assignments (non-speech blocks)**
- Every non-speech block (intro, transition_slot, outro) MUST have B-roll
- These go on video track V2 (or V1 if no underlying A-roll)
- B-roll clip duration must match or exceed the block duration
- If longer, use only a portion (set video_in/video_out)

**2. B-roll Interjections (over speech blocks)**
- Speech blocks that run long should have B-roll breaks for visual variety
- These overlay on V2 while A-roll audio continues on A1
- Duration is a guideline based on intent — not a fixed constraint. Brief
  illustrative cuts may be 1-2 seconds; establishing shots overlaying
  speech may be longer. Let the content and pacing drive the duration.
- Purpose: visual variety, illustrating speech content, breaking up
  talking head

### Selection: 3-step process

**Step A — Pre-filter by visual tags** (algorithmic, fast)

Use `visual_match_tags` from the semantic analysis scene segments to filter
candidates before reading prose descriptions. Look for tag matches on:
- Setting: does the block's `visual_note` call for "outdoor", "urban", "intimate"?
- Subject: does the moment need "creator_visible" or "no_person"?
- Mood: does the creative direction call for "cinematic", "energetic", "calm"?
- Content: specific activity tags like "walking", "typing", "coffee", "driving"

This pre-filter narrows 15-30 clips down to 3-6 candidates per placement.

**Step B — Match by topic suitability** (contextual)

For each candidate, check `broll_topic_suitability` on the specific scene segment
you're considering using (not the clip as a whole). This field is per-segment —
a 40-second clip may have one segment perfectly suited to "forward movement" and
another suited to "self-reflection." Read the per-segment value, not the clip-level
`broll_context`. Also check `broll_topic_avoid` — immediately discard a segment
if the speech moment falls into its avoid list.

**Step C — Select sub-range using signal data**

Once you've chosen a candidate clip and the target scene segment:
1. **Clean in/out points**: Use `scene_boundaries` timestamps as cut anchors.
2. **Motion preference**: Use `motion_energy.high_motion_times` for active B-roll;
   low-motion times for calm/reflective moments.
3. **Face presence**: Use `face_presence.face_absent_times` to exclude frames where
   the creator appears unintentionally; `face_present_times` when you intentionally
   want them visible during B-roll.
4. **Camera motion match**: Use `optical_flow_direction.dominant_motion` — prefer
   `"static"` or `"pan"` for establishing shots; avoid `"handheld_shaky"` unless
   the energy calls for it.
5. **Color coherence**: Use `color_curves.temperature_curve` to match B-roll color
   temperature (warm/cool/neutral) with surrounding A-roll segments.

### Other criteria:
- **Mood/energy match** with creative direction
- **Visual quality** — prefer higher `interest_score` B-roll clips
- **Variety** — avoid reusing the same B-roll clip more than once
- **Consider the visual_note** from the spine — it hints at what visual fits


---

## Output Format

```json
{
  "b_roll_assignments": [
    {
      "spine_block_position": 2,
      "block_type": "intro",
      "timeline_start": 2.0,
      "timeline_end": 5.0,
      "assigned_clip": {
        "clip_id": "string",
        "source_file": "string",
        "video_in": 0.0,
        "video_out": 3.0,
        "duration_seconds": 3.0,
        "needs_conform": true,
        "selection_rationale": "string"
      }
    }
  ],
  "b_roll_interjections": [
    {
      "over_spine_block_position": 3,
      "timeline_start": 8.0,
      "timeline_end": 10.0,
      "assigned_clip": {
        "clip_id": "string",
        "source_file": "string",
        "video_in": 2.0,
        "video_out": 4.0,
        "duration_seconds": 2.0,
        "needs_conform": false,
        "selection_rationale": "string"
      },
      "purpose": "string (e.g., 'illustrate what is being said')"
    }
  ]
}
```

---

## Evaluation Criteria

1. **Coverage**: Every non-speech block has a B-roll assignment
2. **Relevance**: Selections are visually relevant to the block's purpose
3. **Variety**: No excessive reuse of the same clip
4. **Quality**: Higher-scored B-roll clips are prioritized
5. **Selection rationale**: Each assignment explains WHY this clip was chosen

---

## Important Notes

- B-roll interjections go on video track V2, overlaying A-roll on V1.
  The A-roll audio continues uninterrupted on A1.
- The `needs_conform` flag should be `true` if the clip's resolution or
  rotation doesn't match the 1080x1920 portrait target. Check width,
  height, and rotation from the clip catalog.
- If insufficient B-roll exists in the catalog, FLAG it — some blocks may
  need A-roll with visual treatment (zoom/crop) instead of separate B-roll.
- Interjection duration is a guideline, not a rigid constraint. The purpose
  and pacing should drive the duration. A quick illustrative cut may be
  1 second; an establishing visual may run 3-4 seconds.

### Temporal index data available per clip:

The temporal event index for each B-roll candidate provides:
- **scene_boundaries**: Natural visual cut points. Use for clean in/out anchoring.
- **motion_energy**: Per-frame visual motion (30Hz). High = camera or subject moving.
  Use `high_motion_times` and `peak_motion_times` for the most dynamic sub-ranges.
- **energy_curve**: Per-frame audio energy (30Hz). Higher = louder / more dynamic audio.
- **optical_flow_direction**: Dominant motion vector per sample (5Hz) + `dominant_motion`
  classification: `"static"` | `"pan_left"` | `"pan_right"` | `"tilt_up"` |
  `"tilt_down"` | `"handheld"` | `"mixed"` | `"unknown"`. Use to match camera
  character with the editorial intent of the placement.
- **camera_motion_decomposition**: Per-sample breakdown (5Hz) into `translation_x`,
  `translation_y`, `zoom_factor`, `residual`. Use to detect existing zooms (avoid
  adding VFX zoom on top) or deliberate pans.
- **face_presence**: Presence confidence per sample (5Hz). Use `face_absent_times`
  to find sub-ranges where the subject's face is off-screen (pure environmental
  B-roll). Use `face_present_times` when the creator should be visible.
- **color_curves**: Per-second (1Hz) `hue_values`, `brightness_values`,
  `saturation_values`, `temperature_curve` (warm/neutral/cool). Match color
  temperature to surrounding A-roll for visual coherence.
- **speech_activity**: Binary 30Hz curve — is speech happening at each frame?
  Most B-roll clips have no speech, but this guards against accidentally cutting
  into a clip where the subject is speaking off-camera.

The bridge will automatically snap in/out points to scene boundaries
and prefer high-energy/high-motion segments when resolving your
creative selections.


---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `timed_spine` (structure with visual_notes and timeline positions) |
| Reads | `clip_catalog` (all clips with metadata) |
| Reads | `semantic_analysis_documents` (per-clip editorial analysis) |
| Reads | `temporal_event_indices` (per-clip scene boundaries, energy, motion) |
| Reads | `a_roll_assignments` (what's already placed — avoid conflicts) |
| Reads | `creative_direction` (mood/energy guidance) |
| Reads | `style_specification` |
| Writes | `b_roll_assignments` |
| Writes | `b_roll_interjections` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Not enough B-roll clips in catalog | FLAG — may need visual treatment on A-roll instead |
| No B-roll matches the mood | Use best available, note the mismatch |
| B-roll clip too short for block | Combine multiple clips or use with speed ramping |
