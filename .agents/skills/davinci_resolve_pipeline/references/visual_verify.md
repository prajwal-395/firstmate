# Visual Verification Pipeline

## Overview
Render individual frames from the FULL composite (all tracks, Fusion effects,
color grading, subtitles, motion graphics) to disk for automated analysis.

## Workflow
```
1. Switch to Deliver page
2. Set MarkIn = MarkOut = target frame
3. AddRenderJob() + StartRendering()
4. Wait for completion
5. Convert .mov → .png with ffmpeg
6. Analyze the PNG
```

## Quality Checks
- **Black frame**: file size < 2KB → comp is broken
- **Normal video**: file size 30KB-150KB depending on content
- **Visual analysis**: view the PNG to check effects look correct

## Full Composite
The render includes ALL layers visible in the viewer:
- V1-V4 video tracks (clips, overlays, motion graphics)
- All Fusion effects on each clip
- Color grading (if applied)
- Subtitles and text overlays
- Audio waveforms are NOT included (video-only render)

## Implementation
See `scripts/render_frame.py` for the reusable implementation.

## Tips
- Always `DeleteAllRenderJobs()` after capturing to avoid clutter
- Switch back to `resolve.OpenPage("edit")` when done
- Rendering a single frame takes ~0.5-2 seconds
- Use batch rendering (multiple AddRenderJob calls) for efficiency
