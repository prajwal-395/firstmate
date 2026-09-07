---
name: pipeline_operations
description: >-
  The named things the video pipeline can be asked to do, and the
  scope each runs at. GENERATED from library/tools/operations.py -
  do not edit; run `python3 -m library.tools.operations --emit-skill`.
---

# Pipeline operations

An operation is a named entry point into an existing step's own code,
run at a declared scope. It owns no logic of its own.

## Scopes

- `project` - the whole project, which is what every step does today and what a run with no scope means
- `clip` - one clip of one step, addressed by its catalog id (clip_007) - the granularity `--rerun step:clip` already has
- `region` - an interval of ONE timeline, in seconds (45.0-72.0, or reel_03@45.0-72.0) - the axis nothing had, and the one the captain's regenerate-this-segment example needs
- `reel` - one reel: its keep ranges in play order, all on the reel's own timeline. NOT a region - a reel is a LIST of spans and its time base is its kept ranges laid end to end

## Operations

| operation | owning node | scopes | what it does |
|---|---|---|---|
| `sfx_library.validate` | `validate_sfx_library` | project | Check the SFX library can actually serve a run |
| `semantics.analyse` | `semantic_analysis` | project | Run the v3 vision pass over clips without a profile |
| `prosody.analyse` | `prosody_analysis` | project | Measure pitch, pace, voice quality and intensity per clip |
| `ocr.extract` | `ocr_extraction` | project | Extract on-screen text from the footage |
| `duration_zone.build` | `mesh_spine` | project | Resolve the project's target duration into the band the model is shown |
| `reel.candidates` | `select_reels` | project | Measure every contiguous exchange in the cut, ranked and filtered by nothing |
| `reel.select` | `select_reels` | project | Check the model's chosen moments against the cut and publish them PROPOSED |
| `reel.build` | `build_reels` | project | Cut every APPROVED moment onto its own Resolve timeline, bad takes removed |
| `reel.verify` | `verify_reels` | project | Grade the built reel timelines against the plan they were built from |
| `music.analyse` | `music_analysis` | project | Analyse the selected track for beat grid, BPM, key and structure |
| `subtitles.plan` | `plan_subtitles` | project, region | Generate subtitle entries from the spine's own word timestamps |
| `subtitles.splice` | `plan_subtitles` | region | Put a region's re-planned captions back into the stored plan |
| `transcript.reindex` | `temporal_index` | region | Re-measure the speech in one region, back at the raw footage |
| `transcript.splice` | `temporal_index` | region | Put a re-measured region back into the per-clip speech index |
| `subtitles.render` | `render_subtitles` | project, region | Render one ProRes 4444 overlay per captioned spine block |
| `subtitles.render_segment` | `render_subtitles` | project, region | Render ONE subtitle segment - the per-segment unit a region-scoped redo reaches |
| `motion_graphics.render` | `render_motion_graphics` | project | Render the planned motion graphics, bookends and timed text |
| `motion_graphics.render_segment` | `render_motion_graphics` | project, region | Render ONE motion-graphics overlay segment |
| `color_grade.resolve` | `color_grade` | project | Join the colourist's answer to the measured clips as one CDL each |
| `validation.resolve` | `validate` | project | Combine the deterministic checks and the model's reading into one verdict |

## Calling one

```
python3 -m library.tools.operations --list
python3 -m library.tools.operations <name> --project <path> [--region 45.0-72.0 | --clip clip_007] [--set name=<json>|@file.json]
```

