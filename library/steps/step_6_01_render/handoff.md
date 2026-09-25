# Step 6.1: Render Final Video — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 6.1 |
| Name | Render Final Video |
| Determinism | **Deterministic** |
| Archetype | Execution |
| Encoding Format | Tool-specific script (DaVinci Resolve API, FFmpeg, or equivalent) |
| Idempotent | Yes (same specs → same render) |
| Dependencies | Phase 5 complete (Step 5.3) |

---

## System Context

This is the encoding step — where all tool-agnostic specifications become
tool-specific commands. The executor reads the four spec documents and
builds the timeline programmatically.

This step has two companion implementation scripts (DaVinci Resolve
variant): `resolve_build_timeline.py` assembles the timeline, then
`library/tools/execution/resolve_render.py` drives the Deliver page in a
separate process and exports the file the step reports as `output_path`.
Alternative variants (FFmpeg, Remotion) can be added as additional scripts
sharing the same interface contract.

---

## Input (read from disk/stdin)

1. **Shot list** — all clip placements (V1, V2, A1, A2 entries)
2. **Enhancement specification** — subtitles, transitions, VFX, SFX
3. **Color grading specification** — node tree, per-clip adjustments
4. **Audio mix specification** — track levels, music automation

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Your answer

One key, `render_review` - the narrative verdict on the export this
step just built. It is the ONLY thing you are asked to write:
`render_output`, `render_watch_frames` and `visual_qa` are measured by
the deterministic build, not authored here.

```json
{
  "render_review": {
    "overall": "clean | concerns | not_watched",
    "notes": ["one line per span watched or concern found"]
  }
}
```

`clean` means every strip watched reads as intended; `concerns` names
what looked wrong, one line each; `not_watched` - with the reason - is
the honest answer when `render_watch_frames` is absent and the
manifest and measurements alone say nothing about the picture. The
verdict is advisory: it is printed on the run summary for the operator
(`library/tools/render_review.py`) and gates nothing. Step 6.02's
validation is the gate with teeth.

---

## Verification

- Output file exists and is non-zero bytes
- Output file is playable (valid MP4 container)
- Resolution matches target (1080x1920)
- Duration matches spine's total_duration (±0.5s)
- Frame rate matches target
- File size is reasonable for duration and bitrate

---

## Export Parameters (from style spec Section 8)

| Parameter | Value |
|-----------|-------|
| Format | MP4 |
| Codec | H.264 |
| Resolution | 1080 × 1920 (9:16) |
| Bitrate | 12,000–15,000 kbps |
| Encoding profile | High |
| Multi-pass | Enabled |
| Data levels | Full |
| Color space | Rec.709, Gamma 2.4 |

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `shot_list`, `enhancement_spec`, `color_grade_spec`, `audio_mix_spec`, `style_specification` |
| Writes | `rendered_video` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Render fails | Check error logs, identify failing step |
| Output corrupt | Re-render |
| Duration mismatch | Investigate timeline assembly errors |
| Missing source file | FAIL — trace back to shot_list entry |

---

## Implementation Notes

- This is ENTIRELY an encoding concern — tool-agnostic specs → tool-specific
  commands
- **`resolve_full_assembly.py`** (preferred) — comprehensive script that does
  EVERYTHING: clip placement + subtitles + transitions + VFX + SFX + color
  grading setup + audio mix levels. Run once, review result in Resolve UI.
- Requires DaVinci Resolve Studio v21+
- Render uses H.265, multi-pass encoding, full data levels
- Render is deterministic: same inputs → same output
- Some VFX (animated slow zoom, screen shake keyframes) and audio automation
  (music ducking) need manual refinement after the script runs



## Watching this render

`render_watch_frames` is the PICTURE: frame strips drawn off the
exported file itself - everything drawn over everything else, exactly
as a viewer sees it. It is the only thing in this context that is a
picture; everything else describes what was PLANNED. **Open every
strip it names.** A clean span is a real answer - say it is clean.

`visual_qa`, when the instructions below name its table, is the prose
half: verdicts from frame grabs and segment checks run behind
`PIPELINE_PERCEPTUAL_QA`. Read them against the strips, not instead
of them.

**When `render_watch_frames` is absent, NOTHING HAS WATCHED THIS RENDER.**
The export was built but no strip was drawn - the flag was off, or the draw failed. Say so in your answer and judge from the
manifest and the measurements alone. Do not write a visual verdict
you did not look at.

<!-- VISUAL_QA_INSTRUCTIONS -->
