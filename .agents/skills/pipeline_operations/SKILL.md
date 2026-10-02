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
| `footage.scan` | `scan` | project | Scan the project folder for raw video files |
| `footage.catalog` | `catalog` | project | Extract per-file metadata into the ordered clip catalog |
| `semantics.analyse` | `semantic_analysis` | project | Run the v3 vision pass over clips without a profile |
| `temporal.index` | `temporal_index` | project | Index each clip's speech, sound and motion over time |
| `prosody.analyse` | `prosody_analysis` | project | Measure pitch, pace, voice quality and intensity per clip |
| `ocr.extract` | `ocr_extraction` | project | Extract on-screen text from the footage |
| `creative.direct` | `creative_direction` | project | Decide the video's creative direction from the preflight reads |
| `speech.enrich` | `speech_sequence` | project | Enrich the model's speech sequence with word timings from the temporal index |
| `music.resolve` | `music_selection` | project | Resolve the model's music choice against the measured candidates |
| `duration_zone.build` | `mesh_spine` | project | Resolve the project's target duration into the band the model is shown |
| `spine.mesh` | `mesh_spine` | project | Mesh the model's spine against the speech and the music into the timed spine |
| `aroll.assign` | `assign_aroll` | project | Map speech blocks and hook to their A-roll source files |
| `aroll.splice` | `assign_aroll` | region | Re-assign a region's A-roll from the spine and put it back into the stored assignments |
| `broll.resolve` | `select_broll` | project | Resolve B-roll selections to placed cutaways with source ranges |
| `broll.splice` | `select_broll` | region | Resolve a region's re-planned cutaways and put them back into the stored selections |
| `reel.candidates` | `select_reels` | project | Measure every contiguous exchange in the cut, ranked and filtered by nothing |
| `reel.select` | `select_reels` | project | Check the model's chosen moments against the cut and publish them PROPOSED |
| `reel.reading_context` | `judge_reels` | project | Work out the words each proposed reel plays, for a reader who has not heard the episode |
| `reel.judge` | `judge_reels` | project | Check each reading against its reel's own words and derive the verdicts and ordering |
| `reel.build` | `build_reels` | project | Cut every APPROVED moment onto its own Resolve timeline, bad takes removed |
| `reel.touchup` | `build_reels` | project | Change one built reel's own timeline in place, instead of rebuilding it |
| `reel.entry_motion` | `build_reels` | project | Animate a placed overlay element in (and out) with a Fusion fade, without rebuilding its reel |
| `reel.set_properties` | `build_reels` | project | Change properties on an already-placed clip in place, without deleting and re-placing it |
| `reel.ask` | `build_reels` | project | Write every APPROVED reel's three visual asks without building anything |
| `reel.verify` | `verify_reels` | project | Grade the built reel timelines against the plan they were built from |
| `reel.gate_stills` | `verify_reels` | project | Grab gate stills at named reel-relative frames off one built reel timeline |
| `music.analyse` | `music_analysis` | project | Analyse the selected track for beat grid, BPM, key and structure |
| `subtitles.plan` | `plan_subtitles` | project, region | Generate subtitle entries from the spine's own word timestamps |
| `subtitles.splice` | `plan_subtitles` | region | Put a region's re-planned captions back into the stored plan |
| `rough_cut.review` | `review_rough_cut` | project | Run the mechanical duration, continuity and source checks over the rough cut |
| `transitions.resolve` | `plan_transitions` | project | Resolve the model's transition plan to execution specs |
| `transitions.splice` | `plan_transitions` | region | Resolve a region's re-planned transitions and put them back into the stored plan |
| `vfx.resolve` | `plan_vfx` | project | Resolve the model's VFX plan to execution specs |
| `vfx.splice` | `plan_vfx` | region | Resolve a region's re-planned effects and put them back into the stored plan |
| `sfx.resolve` | `plan_sfx` | project | Resolve the model's SFX plan to playable placements |
| `sfx.splice` | `plan_sfx` | region | Place a region's re-planned sounds and put them back into the stored plan |
| `transcript.reindex` | `temporal_index` | region | Re-measure the speech in one region, back at the raw footage |
| `transcript.splice` | `temporal_index` | region | Put a re-measured region back into the per-clip speech index |
| `subtitles.render` | `render_subtitles` | project, region | Render one overlay artefact per captioned spine block |
| `subtitles.render_segment` | `render_subtitles` | project, region | Render ONE subtitle segment - the per-segment unit a region-scoped redo reaches |
| `subtitles.rerender_swap` | `render_subtitles` | project | Re-render named caption segments and swap them onto every timeline holding the old file |
| `motion_graphics.render` | `render_motion_graphics` | project | Render the planned motion graphics, bookends and timed text |
| `motion_graphics.render_segment` | `render_motion_graphics` | project, region | Render ONE motion-graphics overlay segment |
| `color_grade.resolve` | `color_grade` | project | Join the colourist's answer to the measured clips as one CDL each |
| `audio_mix.resolve` | `audio_mix` | project | Join the mix answer to the bed and speech measurements as per-window clip gain |
| `cohesion.review` | `creative_cohesion` | project | Review the planned transitions and sound against the declared direction |
| `objects.segment` | `object_segmentation` | project | Segment the subjects of the clips a grade or behind-subject plan names |
| `manifest.compile` | `compile_manifest` | project | Compile every plan the run recorded into the assembly manifest |
| `render.build` | `render` | project | Build the final timeline in DaVinci Resolve and export the finished video |
| `validation.resolve` | `validate` | project | Combine the deterministic checks and the model's reading into one verdict |

## Calling one

```
python3 -m library.tools.operations --list
python3 -m library.tools.operations <name> --project <path> [--region 45.0-72.0 | --clip clip_007] [--set name=<json>|@file.json]
```

