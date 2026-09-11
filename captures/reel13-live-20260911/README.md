# Reel 13 - complete live state, captured 2026-09-11

The captain edited `Reel 13 - the-accounting-firm-ai-called-healthcare` by hand in
DaVinci Resolve.  A previous rebuild destroyed an earlier set of his manual edits.
**This directory is the snapshot taken before any further work touched the project.**

`reel13_live_state.json` - every item on every track (4 video-ish tracks of picture, one
Frame track, Subtitles, Semantic, and both audio tracks): source file, record in/out,
source in/out, duration, every Resolve Edit-page transform property, per-item markers,
flags, clip colour, Fusion comp count, and the media pool item's own resolution and
timecode.  Plus the timeline's own settings, frame range and markers.

`reel13_live_fusion_comps.json` - every Fusion comp attached to an item on that timeline,
tool by tool, with `TOOLI_ImageWidth`/`TOOLI_ImageHeight` and every scalar input.

`capture_timeline.py` / `capture_fusion_comps.py` - the two read-only scripts that produced
them.  Neither writes anything to Resolve; `capture_fusion_comps.py` in particular never
calls `AddTool`, which would modify the comp it is reading.

Run either as:

    REPO=$PWD RESOLVE_SCRIPT_API=... RESOLVE_SCRIPT_LIB=... \
      PYTHONPATH="$RESOLVE_SCRIPT_API/Modules" \
      python3 captures/reel13-live-20260911/capture_timeline.py "<timeline name>" out.json

A second copy lives outside the repo at
`firstmate/data/vep-lost-edits-and-the-positioning-truth/`.

## This is NOT the same as `captures/vep-captain-feedback-20260911/06_Reel 13 ...json`

That earlier capture (landed in #963) is a different, earlier moment.  Reel 13's live state
has moved since: V1@74 read `Pan 23.1705 / Tilt 0.125` there and reads `Pan -12.0 / Tilt 0.25`
here, and the Blue `feedback` marker at frame 1902 has been replaced by a Green `reply:`
marker.  Keep both; the difference between them is evidence.

## The rest of this directory

Added the same day, once the capture was safe:

- `reel13_live_audio_transcript.json` / `reel28_live_audio_transcript.json` -
  whisperX `large-v3`, word-aligned, over audio rebuilt from each capture's own
  edit decisions with ffmpeg.  This is the built reel's audio, assembled from the
  exact source spans the timeline plays.
- `reel28_live_state.json` - the same full capture, for Reel 28.
- `reel13_overlay_draw_positions.json` / `reel28_overlay_draw_positions.json` -
  produced by `measure_overlay_draw_positions.py`: for every caption and motion
  graphic, its canvas size, its stored Pan/Tilt, where its ink sits inside its own
  artefact, and where that ink therefore lands on the delivery frame - with the
  error in pixels against the intended caption row.
- `reel13_f900_caption_visible.jpg` - a still EXPORTED from Reel 13 (gallery grab,
  the gallery left at 0 stills), showing a tight caption stored at `Tilt -870`
  landing correctly on the caption row.
- `reel28_f440_no_caption.jpg` - the same grab from Reel 28, where a tight caption
  stored at `Tilt -1700` is playing and nothing is drawn: its ink is 415px below
  the frame.

The reading of all of it is [`docs/REEL_13_LOST_EDITS_AND_POSITIONING.md`](../../docs/REEL_13_LOST_EDITS_AND_POSITIONING.md).
