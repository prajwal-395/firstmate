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
- **Less is more.** There is no required number of SFX. Place a sound where
  a moment earns one and nowhere else. Do NOT add an effect to reach a
  count, and never write a rationale that justifies a sound by how many
  there are.
- Volume: "subtle" or "low" for most; "medium" only for emphasis
- Never louder than speech or music
- Don't fight the music (avoid loud SFX during prominent music)
- Layering is allowed: two or more sounds at the same position is how
  real sound design works (whoosh + bass hit, riser under a textural bed).
  Each layered sound carries its own volume_level
- Match the music rhythm and energy

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `sfx_candidates_toon` table provides a summarized list of clips and events with the following fields:
- `segment_id`: The ID of the segment.
- `text`: Summary text for the segment.
- `action_sfx_suggested`: Pre-computed suggestion on whether SFX are needed based on audio transients.

Use this data to decide which sound effects to apply.

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
sound design preferences as natural language. Read it for:

- Desired SFX density (minimal and clean vs. richly layered)
- Types of sounds to favor or avoid
- Moments that deserve special sonic emphasis

Let the brief guide how many effects you place and their character. If no
creative brief is provided, let the creative direction's energy and the
moments in the spine decide the density - there is no default count.

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. Every SFX earns its place - none exists to reach a count
2. SFX timing aligns with events they accompany
3. Volume levels appropriate — mostly "subtle" or "low"
4. Layered sounds at the same position each carry a distinct purpose
5. SFX don't compete with prominent music moments

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
