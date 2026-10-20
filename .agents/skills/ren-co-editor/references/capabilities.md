# Ren capabilities

GENERATED from `library/tools/capability_discovery.py` - do not edit; run `python3 -m library.tools.capability_discovery --emit-reference` and write the result here.

Every capability id the engine holds, what it is for, where it runs, what it costs and what it needs. `ren capabilities` answers the same questions for the machine you are on, with this machine's verdict; `ren capabilities <id>` describes one in full, and `ren doctor --for <id>` says what is missing.

58 capabilities.

## The registry's capabilities

| capability | scope | cost | what it does |
|---|---|---|---|
| `sfx_library.validate` | project | light | Check the SFX library can actually serve a run |
| `footage.scan` | project | light | Scan the project folder for raw video files |
| `footage.catalog` | project | light | Extract per-file metadata into the ordered clip catalog |
| `semantics.analyse` | project | heavy | Run the v3 vision pass over clips without a profile |
| `temporal.index` | project | heavy | Index each clip's speech, sound and motion over time |
| `prosody.analyse` | project | light | Measure pitch, pace, voice quality and intensity per clip |
| `ocr.extract` | project | light | Extract on-screen text from the footage |
| `creative.direct` | project | model | Decide the video's creative direction from the preflight reads |
| `speech.enrich` | project | model | Enrich the model's speech sequence with word timings from the temporal index |
| `music.resolve` | project | model | Resolve the model's music choice against the measured candidates |
| `duration_zone.build` | project | light | Resolve the project's target duration into the band the model is shown |
| `spine.mesh` | project | model | Mesh the model's spine against the speech and the music into the timed spine |
| `picture.plan` | project | light | Build the global picture plan selecting shots against the whole edit |
| `aroll.assign` | project | light | Map speech blocks and hook to their A-roll source files |
| `aroll.splice` | region | light | Re-assign a region's A-roll from the spine and put it back into the stored assignments |
| `broll.resolve` | project | model | Resolve B-roll selections to placed cutaways with source ranges |
| `broll.splice` | region | model | Resolve a region's re-planned cutaways and put them back into the stored selections |
| `reel.candidates` | project | light | Measure every contiguous exchange in the cut, ranked and filtered by nothing |
| `reel.select` | project | model | Check the model's chosen moments against the cut and publish them PROPOSED |
| `reel.reading_context` | project | light | Work out the words each proposed reel plays, for a reader who has not heard the episode |
| `reel.judge` | project | model | Check each reading against its reel's own words and derive the verdicts and ordering |
| `reel.build` | project | heavy | Cut every APPROVED moment onto its own Resolve timeline, bad takes removed |
| `reel.touchup` | project | light | Change one built reel's own timeline in place, instead of rebuilding it |
| `reel.entry_motion` | project | light | Animate a placed overlay element in (and out) with a Fusion fade, without rebuilding its reel |
| `reel.set_properties` | project | light | Change properties on an already-placed clip in place, without deleting and re-placing it |
| `reel.ask` | project | light | Write every APPROVED reel's three visual asks without building anything |
| `reel.verify` | project | light | Grade the built reel timelines against the plan they were built from |
| `reel.gate_stills` | project | light | Grab gate stills at named reel-relative frames off one built reel timeline |
| `music.analyse` | project | heavy | Analyse the selected track for beat grid, BPM, key and structure |
| `subtitles.plan` | project, region | light | Generate subtitle entries from the spine's own word timestamps |
| `subtitles.splice` | region | light | Put a region's re-planned captions back into the stored plan |
| `rough_cut.review` | project | light | Run the mechanical duration, continuity and source checks over the rough cut |
| `transitions.resolve` | project | model | Resolve the model's transition plan to execution specs |
| `transitions.splice` | region | model | Resolve a region's re-planned transitions and put them back into the stored plan |
| `vfx.resolve` | project | model | Resolve the model's VFX plan to execution specs |
| `vfx.splice` | region | model | Resolve a region's re-planned effects and put them back into the stored plan |
| `sfx.resolve` | project | model | Resolve the model's SFX plan to playable placements |
| `sfx.splice` | region | model | Place a region's re-planned sounds and put them back into the stored plan |
| `transcript.reindex` | region | light | Re-measure the speech in one region, back at the raw footage |
| `transcript.splice` | region | light | Put a re-measured region back into the per-clip speech index |
| `subtitles.render` | project, region | light | Render one overlay artefact per captioned spine block |
| `subtitles.render_segment` | project, region | light | Render ONE subtitle segment - the per-segment unit a region-scoped redo reaches |
| `subtitles.rerender_swap` | project | light | Re-render named caption segments and swap them onto every timeline holding the old file |
| `motion_graphics.render` | project | model | Render the planned motion graphics, bookends and timed text |
| `motion_graphics.render_segment` | project, region | light | Render ONE motion-graphics overlay segment |
| `color_grade.resolve` | project | model | Join the colourist's answer to the measured clips as one CDL each |
| `audio_mix.resolve` | project | model | Join the mix answer to the bed and speech measurements as per-window clip gain |
| `cohesion.review` | project | light | Review the planned transitions and sound against the declared direction |
| `objects.segment` | project | heavy | Segment the subjects of the clips a grade or behind-subject plan names |
| `manifest.compile` | project | light | Compile every plan the run recorded into the assembly manifest |
| `edit_graph.build` | project | light | Derive the editorial/edit graph from the run's recorded decisions |
| `render.build` | project | heavy | Build the final timeline in DaVinci Resolve and export the finished video |
| `validation.resolve` | project | model | Combine the deterministic checks and the model's reading into one verdict |

## The capabilities a `ren` verb serves

| capability | the command | what it does |
|---|---|---|
| `footage.search` | `ren search` | Find where in a project's footage something happens |
| `footage.search_index` | `ren search-index` | Build the footage search index (writes into the project's scratch) |
| `footage.analyse` | `ren analyze` | Analyse a folder of footage without editing: steps, per-source memory, indexes |
| `project.inspect` | `ren status / ren info / ren check` | Show a project's pipeline status; Show a project's configuration as JSON; Run a project's readiness check |
| `project.create` | `ren new` | Create a project |

## What each capability needs

| capability | required | runs without, degraded |
|---|---|---|
| `sfx_library.validate` | `macos`, `python.venv`, `python.core`, `config.sfx_library` | - |
| `footage.scan` | `macos`, `python.venv`, `python.core`, `ffmpeg` | - |
| `footage.catalog` | `macos`, `python.venv`, `python.core`, `ffmpeg` | - |
| `semantics.analyse` | `macos`, `python.venv`, `python.core`, `ffmpeg`, `python.analysis`, `python.graphics`, `model.vision` | `hardware.memory` - the vision model will swap: the vision pass runs, but slow |
| `temporal.index` | `macos`, `python.venv`, `python.core`, `ffmpeg`, `python.analysis`, `transcriber.voz`, `model.mfa`, `python.graphics` | `python.identity` - no speaker identity: voices are not told apart; `model.panns` - sound events recorded as unmeasured; an event anchor refuses by name |
| `prosody.analyse` | `macos`, `python.venv`, `python.core`, `python.analysis` | - |
| `ocr.extract` | `macos`, `python.venv`, `python.core`, `ffmpeg`, `python.analysis`, `python.graphics` | - |
| `creative.direct` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `speech.enrich` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `music.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness`, `config.music_library` | - |
| `duration_zone.build` | `macos`, `python.venv`, `python.core` | - |
| `spine.mesh` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `picture.plan` | `macos`, `python.venv`, `python.core` | - |
| `aroll.assign` | `macos`, `python.venv`, `python.core` | - |
| `aroll.splice` | `macos`, `python.venv`, `python.core` | - |
| `broll.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `broll.splice` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `reel.candidates` | `macos`, `python.venv`, `python.core` | - |
| `reel.select` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `reel.reading_context` | `macos`, `python.venv`, `python.core` | - |
| `reel.judge` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `reel.build` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio`, `python.graphics` | `ren_native` - the Python edit backend runs: correct, slower patch composition |
| `reel.touchup` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio`, `python.graphics` | `ren_native` - the Python edit backend runs: correct, slower patch composition |
| `reel.entry_motion` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio`, `python.graphics` | - |
| `reel.set_properties` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio`, `python.graphics` | `ren_native` - the Python edit backend runs: correct, slower patch composition |
| `reel.ask` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio`, `python.graphics` | - |
| `reel.verify` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio` | - |
| `reel.gate_stills` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio` | - |
| `music.analyse` | `macos`, `python.venv`, `python.core`, `python.analysis`, `config.music_library` | `model.beat_this` - downbeats fall back to the librosa every-4th-beat estimate, labelled estimated |
| `subtitles.plan` | `macos`, `python.venv`, `python.core` | - |
| `subtitles.splice` | `macos`, `python.venv`, `python.core` | - |
| `rough_cut.review` | `macos`, `python.venv`, `python.core` | - |
| `transitions.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `transitions.splice` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `vfx.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `vfx.splice` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `sfx.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness`, `config.sfx_library` | - |
| `sfx.splice` | `macos`, `python.venv`, `python.core`, `chat_harness`, `config.sfx_library` | - |
| `transcript.reindex` | `macos`, `python.venv`, `python.core`, `ffmpeg`, `python.analysis`, `transcriber.voz`, `model.mfa` | `python.identity` - no speaker identity: voices are not told apart; `model.panns` - sound events recorded as unmeasured; an event anchor refuses by name |
| `transcript.splice` | `macos`, `python.venv`, `python.core`, `ffmpeg`, `python.analysis`, `transcriber.voz`, `model.mfa` | `python.identity` - no speaker identity: voices are not told apart; `model.panns` - sound events recorded as unmeasured; an event anchor refuses by name |
| `subtitles.render` | `macos`, `python.venv`, `python.core`, `node`, `remotion` | - |
| `subtitles.render_segment` | `macos`, `python.venv`, `python.core`, `node`, `remotion` | - |
| `subtitles.rerender_swap` | `macos`, `python.venv`, `python.core`, `node`, `remotion` | - |
| `motion_graphics.render` | `macos`, `python.venv`, `python.core`, `node`, `remotion`, `chat_harness` | - |
| `motion_graphics.render_segment` | `macos`, `python.venv`, `python.core`, `node`, `remotion` | - |
| `color_grade.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `audio_mix.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness` | `deepfilter` - a deepfilternet request refuses by name and the model re-plans with voice_isolation |
| `cohesion.review` | `macos`, `python.venv`, `python.core` | - |
| `objects.segment` | `macos`, `python.venv`, `python.core`, `ffmpeg`, `python.analysis`, `python.graphics` | - |
| `manifest.compile` | `macos`, `python.venv`, `python.core` | - |
| `edit_graph.build` | `macos`, `python.venv`, `python.core` | - |
| `render.build` | `macos`, `python.venv`, `python.core`, `resolve.scripting`, `resolve.studio`, `ffmpeg` | `ren_native` - the Python edit backend runs: correct, slower patch composition |
| `validation.resolve` | `macos`, `python.venv`, `python.core`, `chat_harness` | - |
| `footage.search` | `macos`, `python.venv`, `python.core` | `python.analysis` - lexical-only search: no dense half, so paraphrases are missed; `model.search_embedding` - lexical-only search: no dense half, so paraphrases are missed |
| `footage.search_index` | `macos`, `python.venv`, `python.core` | `python.analysis` - lexical-only search: no dense half, so paraphrases are missed; `model.search_embedding` - lexical-only search: no dense half, so paraphrases are missed |
| `footage.analyse` | `macos`, `python.venv`, `python.core`, `ffmpeg`, `python.analysis`, `python.graphics`, `model.vision`, `transcriber.voz`, `model.mfa` | `python.identity` - no identity lane: faces and voices are not resolved to people; `hardware.memory` - the vision model will swap: the vision pass runs, but slow |
| `project.inspect` | `macos`, `python.venv`, `python.core` | - |
| `project.create` | `macos`, `python.venv`, `python.core`, `config.projects_root` | - |

## How each capability is run

| capability | the command |
|---|---|
| `sfx_library.validate` | `ren edit <project> --only validate_sfx_library`<br>`bin/vep -m library.tools.operations sfx_library.validate --project <project>` |
| `footage.scan` | `ren edit <project> --only scan`<br>`bin/vep -m library.tools.operations footage.scan --project <project>` |
| `footage.catalog` | `ren edit <project> --only catalog`<br>`bin/vep -m library.tools.operations footage.catalog --project <project>` |
| `semantics.analyse` | `ren edit <project> --only semantic_analysis`<br>`bin/vep -m library.tools.operations semantics.analyse --project <project>` |
| `temporal.index` | `ren edit <project> --only temporal_index`<br>`bin/vep -m library.tools.operations temporal.index --project <project>` |
| `prosody.analyse` | `ren edit <project> --only prosody_analysis`<br>`bin/vep -m library.tools.operations prosody.analyse --project <project>` |
| `ocr.extract` | `ren edit <project> --only ocr_extraction`<br>`bin/vep -m library.tools.operations ocr.extract --project <project>` |
| `creative.direct` | `ren edit <project> --only creative_direction`<br>`bin/vep -m library.tools.operations creative.direct --project <project>` |
| `speech.enrich` | `ren edit <project> --only speech_sequence`<br>`bin/vep -m library.tools.operations speech.enrich --project <project>` |
| `music.resolve` | `ren edit <project> --only music_selection`<br>`bin/vep -m library.tools.operations music.resolve --project <project>` |
| `duration_zone.build` | `ren edit <project> --only mesh_spine`<br>`bin/vep -m library.tools.operations duration_zone.build --project <project>` |
| `spine.mesh` | `ren edit <project> --only mesh_spine`<br>`bin/vep -m library.tools.operations spine.mesh --project <project>` |
| `picture.plan` | `ren edit <project> --only plan_picture`<br>`bin/vep -m library.tools.operations picture.plan --project <project>` |
| `aroll.assign` | `ren edit <project> --only assign_aroll`<br>`bin/vep -m library.tools.operations aroll.assign --project <project>` |
| `aroll.splice` | `ren edit <project> --only assign_aroll`<br>`bin/vep -m library.tools.operations aroll.splice --project <project>` |
| `broll.resolve` | `ren edit <project> --only select_broll`<br>`bin/vep -m library.tools.operations broll.resolve --project <project>` |
| `broll.splice` | `ren edit <project> --only select_broll`<br>`bin/vep -m library.tools.operations broll.splice --project <project>` |
| `reel.candidates` | `ren edit <project> --only select_reels`<br>`bin/vep -m library.tools.operations reel.candidates --project <project>` |
| `reel.select` | `ren edit <project> --only select_reels`<br>`bin/vep -m library.tools.operations reel.select --project <project>` |
| `reel.reading_context` | `ren edit <project> --only judge_reels`<br>`bin/vep -m library.tools.operations reel.reading_context --project <project>` |
| `reel.judge` | `ren edit <project> --only judge_reels`<br>`bin/vep -m library.tools.operations reel.judge --project <project>` |
| `reel.build` | `ren edit <project> --only build_reels`<br>`bin/vep -m library.tools.operations reel.build --project <project>` |
| `reel.touchup` | `ren edit <project> --only build_reels`<br>`bin/vep -m library.tools.operations reel.touchup --project <project>` |
| `reel.entry_motion` | `ren edit <project> --only build_reels`<br>`bin/vep -m library.tools.operations reel.entry_motion --project <project>` |
| `reel.set_properties` | `ren edit <project> --only build_reels`<br>`bin/vep -m library.tools.operations reel.set_properties --project <project>` |
| `reel.ask` | `ren edit <project> --only build_reels`<br>`bin/vep -m library.tools.operations reel.ask --project <project>` |
| `reel.verify` | `ren edit <project> --only verify_reels`<br>`bin/vep -m library.tools.operations reel.verify --project <project>` |
| `reel.gate_stills` | `ren edit <project> --only verify_reels`<br>`bin/vep -m library.tools.operations reel.gate_stills --project <project>` |
| `music.analyse` | `ren edit <project> --only music_analysis`<br>`bin/vep -m library.tools.operations music.analyse --project <project>` |
| `subtitles.plan` | `ren edit <project> --only plan_subtitles`<br>`bin/vep -m library.tools.operations subtitles.plan --project <project>` |
| `subtitles.splice` | `ren edit <project> --only plan_subtitles`<br>`bin/vep -m library.tools.operations subtitles.splice --project <project>` |
| `rough_cut.review` | `ren edit <project> --only review_rough_cut`<br>`bin/vep -m library.tools.operations rough_cut.review --project <project>` |
| `transitions.resolve` | `ren edit <project> --only plan_transitions`<br>`bin/vep -m library.tools.operations transitions.resolve --project <project>` |
| `transitions.splice` | `ren edit <project> --only plan_transitions`<br>`bin/vep -m library.tools.operations transitions.splice --project <project>` |
| `vfx.resolve` | `ren edit <project> --only plan_vfx`<br>`bin/vep -m library.tools.operations vfx.resolve --project <project>` |
| `vfx.splice` | `ren edit <project> --only plan_vfx`<br>`bin/vep -m library.tools.operations vfx.splice --project <project>` |
| `sfx.resolve` | `ren edit <project> --only plan_sfx`<br>`bin/vep -m library.tools.operations sfx.resolve --project <project>` |
| `sfx.splice` | `ren edit <project> --only plan_sfx`<br>`bin/vep -m library.tools.operations sfx.splice --project <project>` |
| `transcript.reindex` | `ren edit <project> --only temporal_index`<br>`bin/vep -m library.tools.operations transcript.reindex --project <project>` |
| `transcript.splice` | `ren edit <project> --only temporal_index`<br>`bin/vep -m library.tools.operations transcript.splice --project <project>` |
| `subtitles.render` | `ren edit <project> --only render_subtitles`<br>`bin/vep -m library.tools.operations subtitles.render --project <project>` |
| `subtitles.render_segment` | `ren edit <project> --only render_subtitles`<br>`bin/vep -m library.tools.operations subtitles.render_segment --project <project>` |
| `subtitles.rerender_swap` | `ren edit <project> --only render_subtitles`<br>`bin/vep -m library.tools.operations subtitles.rerender_swap --project <project>` |
| `motion_graphics.render` | `ren edit <project> --only render_motion_graphics`<br>`bin/vep -m library.tools.operations motion_graphics.render --project <project>` |
| `motion_graphics.render_segment` | `ren edit <project> --only render_motion_graphics`<br>`bin/vep -m library.tools.operations motion_graphics.render_segment --project <project>` |
| `color_grade.resolve` | `ren edit <project> --only color_grade`<br>`bin/vep -m library.tools.operations color_grade.resolve --project <project>` |
| `audio_mix.resolve` | `ren edit <project> --only audio_mix`<br>`bin/vep -m library.tools.operations audio_mix.resolve --project <project>` |
| `cohesion.review` | `ren edit <project> --only creative_cohesion`<br>`bin/vep -m library.tools.operations cohesion.review --project <project>` |
| `objects.segment` | `ren edit <project> --only object_segmentation`<br>`bin/vep -m library.tools.operations objects.segment --project <project>` |
| `manifest.compile` | `ren edit <project> --only compile_manifest`<br>`bin/vep -m library.tools.operations manifest.compile --project <project>` |
| `edit_graph.build` | `ren edit <project> --only build_edit_graph`<br>`bin/vep -m library.tools.operations edit_graph.build --project <project>` |
| `render.build` | `ren edit <project> --only render`<br>`bin/vep -m library.tools.operations render.build --project <project>` |
| `validation.resolve` | `ren edit <project> --only validate`<br>`bin/vep -m library.tools.operations validation.resolve --project <project>` |
| `footage.search` | `ren search` |
| `footage.search_index` | `ren search-index` |
| `footage.analyse` | `ren analyze` |
| `project.inspect` | `ren status / ren info / ren check` |
| `project.create` | `ren new` |

## The vocabulary

A capability id is dotted (`reel.build`). `ren capabilities <id>` accepts the dotted spelling, the underscored one and the plain one, and an id it does not hold comes back with the nearest real ids rather than a bare 'unknown'.

