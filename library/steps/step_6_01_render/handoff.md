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



<!-- VISUAL_QA_INSTRUCTIONS -->
