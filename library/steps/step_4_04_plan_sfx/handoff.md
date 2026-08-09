# Step 4.4: Plan Sound Effects — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 4.4 |
| Name | Plan Sound Effects |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Phase 3 complete (Step 3.4) |

---

## System Context

You are a sound designer adding texture, weight, and polish to the edit.
SFX are the "bass guitar" — felt more than heard. They complement
transitions, emphasize moments, and create a professional sound design
layer. Less is more: not every cut needs a sound effect.

SFX serves MULTIPLE purposes — not just pairing with transitions:
- **Movement definition**: whooshes to guide attention
- **Emotional weight**: bass impacts for emphasis
- **Tension building**: risers spanning multiple clips
- **Texture**: foley/ambient establishing mood
- **Punctuation**: clicks on text appearances

---

## Task Prompt

Select and place sound effects at appropriate moments in the timeline.

### SFX toolkit:

| Type | When to use |
|------|------------|
| `whoosh` / `swish` | On cuts, transitions, camera movements |
| `bass_impact` | Reveals, title drops, emphasis moments |
| `riser` | Before a reveal or punchline — can span MULTIPLE clips (5-10s) |
| `foley` / `ambient` | Establishing scenes, adding texture |
| `click` / `tick` | Subtitle appearances, small visual elements |
| `reverse_cymbal` / `swell` | Between major sections |

### Rules:
- **Less is more** — max 5-10 SFX for a 30-60 second video
- Volume: "subtle" or "low" for most; "medium" only for emphasis
- Never louder than speech or music
- Don't fight the music (avoid loud SFX during prominent music)
- No two SFX overlap at the same position
- Layer with purpose (whoosh + bass hit for important transitions)
- Match the music rhythm and energy

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `sfx_candidates_toon` table provides a summarized list of clips and events with the following fields:
- `segment_id`: The ID of the segment.
- `text`: Summary text for the segment.
- `action_sfx_suggested`: Pre-computed suggestion on whether SFX are needed based on audio transients.

Use this data to decide which sound effects to apply.

---

## Output Format

```json
[
  {
    "sfx_id": "sfx_001",
    "sfx_type": "whoosh",
    "timeline_start": 5.0,
    "timeline_end": 5.3,
    "duration_seconds": 0.3,
    "volume_level": "subtle | low | medium",
    "paired_with": "trans_001 (or null if standalone)",
    "source_asset": "string (path to SFX file or asset ID)",
    "rationale": "string",
    "target_track": "A3"
  }
]
```

---

## Evaluation Criteria

1. SFX count ≤ 10 for a 30-60s video
2. Every creative transition has at most one SFX
3. SFX timing aligns with events they accompany
4. Volume levels appropriate — mostly "subtle" or "low"
5. SFX don't overlap
6. SFX don't compete with prominent music moments

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `shot_list`, `audio_spine`, `transition_plan` (optional), `subtitle_entries` (optional), `vfx_plan` (optional), `style_specification` |
| Writes | `sfx_spec` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| SFX asset not found | Skip and flag for human to source |
| Too many SFX placed | Reduce — restraint is key |
