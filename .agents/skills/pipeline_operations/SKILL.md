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
- `region` - an interval of the TIMELINE, in seconds (45.0-72.0) - the axis nothing had, and the one the captain's regenerate-this-segment example needs

## Operations

| operation | owning node | scopes | what it does |
|---|---|---|---|
| `sfx_library.validate` | `validate_sfx_library` | project | Check the SFX library can actually serve a run |
| `semantics.analyse` | `semantic_analysis` | project | Run the v3 vision pass over clips without a profile |
| `prosody.analyse` | `prosody_analysis` | project | Measure pitch, pace, voice quality and intensity per clip |
| `ocr.extract` | `ocr_extraction` | project | Extract on-screen text from the footage |
| `duration_zone.build` | `mesh_spine` | project | Resolve the project's target duration into the band the model is shown |
| `music.analyse` | `music_analysis` | project | Analyse the selected track for beat grid, BPM, key and structure |
| `subtitles.plan` | `plan_subtitles` | project | Generate subtitle entries from the spine's own word timestamps |
| `subtitles.render` | `render_subtitles` | project, region | Render one ProRes 4444 overlay per captioned spine block |
| `subtitles.render_segment` | `render_subtitles` | project, region | Render ONE subtitle segment - the per-segment unit a region-scoped redo reaches |
| `motion_graphics.render` | `render_motion_graphics` | project | Render the planned motion graphics, bookends and timed text |
| `color_grade.resolve` | `color_grade` | project | Join the colourist's answer to the measured clips as one CDL each |
| `validation.resolve` | `validate` | project | Combine the deterministic checks and the model's reading into one verdict |

## Calling one

```
python3 -m library.tools.operations --list
python3 -m library.tools.operations <name> --project <path> [--region 45.0-72.0 | --clip clip_007]
```

