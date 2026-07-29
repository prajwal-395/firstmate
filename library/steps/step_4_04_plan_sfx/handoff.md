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

### Precision tool: Onset detection (embedded)

To find natural SFX placement points (claps, impacts, sharp sounds), run
librosa onset detection on the extracted audio:

```python
import librosa
import numpy as np

y, sr = librosa.load("audio.wav", sr=22050)
onset_frames = librosa.onset.detect(y=y, sr=sr, units='frames')
onset_times = librosa.frames_to_time(onset_frames, sr=sr)
# onset_times = [0.5, 1.2, 3.4, ...] — timestamps of transients
```

Use onset times to validate SFX placement: SFX paired with a natural
audio transient (clap, impact) will feel more organic than SFX placed
at arbitrary positions.

### Precision tool: RMS energy contour (embedded)

To identify prominent music moments (where SFX should be avoided):

```python
rms = librosa.feature.rms(y=y)[0]
rms_times = librosa.frames_to_time(range(len(rms)), sr=sr)
# High RMS = loud/prominent music — avoid SFX here
```

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
| Writes | `sfx_plan` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| SFX asset not found | Skip and flag for human to source |
| Too many SFX placed | Reduce — restraint is key |
