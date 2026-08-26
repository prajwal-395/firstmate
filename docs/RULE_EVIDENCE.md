# Rule evidence

This file is the history behind `AGENTS.md`.

`AGENTS.md` states rules.
This file records what produced each one: the incident, the measurement, the date, the crash log, the diagnostic path, the captain's ruling.
Every entry is reachable from a `[why]` link on the rule it belongs to.

**You do not need anything in this file to operate the pipeline.**
Read an entry when you want to overturn a rule, when you need the numbers behind it, or when you are about to do the thing it warns about and want to know how bad it was.

Entries are grouped by the `AGENTS.md` section they support and are named by anchor.
When you add a rule to `AGENTS.md` that came from an incident, add its evidence here and link it; when you withdraw a rule, leave the evidence and say it was withdrawn.

---

## Section 3 - running the pipeline

### whisperx-paid-twice

Before the split ledger, one flat `steps_completed` covered all 28 steps with one lifetime.
`--from` only trimmed the plan while the skip-if-finished check fired anyway, so the only way to redo creative work was to move `pipeline_data.json` aside.
That move is what made project 001 pay for forty minutes of WhisperX twice.
Recorded in `docs/RUN_001_END_TO_END.md`, whose Caches row says "Do not do this again".

`--rerun edit` exists so that redoing creative work never names the enrichment ledger.

### fingerprint-not-mtime

The fingerprint is deliberately not mtime.
A `cp` without `-p`, or a backup tool, rewrites mtime on untouched footage and would have destroyed forty minutes of WhisperX for nothing.
It is deliberately not a whole-file hash either: that is gigabytes of IO on every run.
Size plus a digest of the first and last mebibyte was the measured compromise.

Clip ids are assigned by sorted path, so adding a file renumbers everything after it.
The fingerprint carries the path for that reason: a renumber must invalidate the clips it renumbered.

Deliberately NOT built alongside it: a caching framework, or a content-addressed artifact store.
The whole mechanism is one declared field, one split ledger, one re-run flag and one identity check.

### per-clip-index-in-a-worktree

Step 1.04 wrote the per-clip index to `--output-dir`'s default of `./pipeline_output`, which is the runner's current working directory.
Project 001's state therefore recorded its 17-clip index inside a disposable git worktree, which was then thrown away.
It now resolves from `project_folder` and reuses any per-clip file already there instead of re-transcribing it.

---

## Section 4 - dashboard

### review-surface-is-this-dashboard

Captain's ruling of 2026-08-17 on `.lavish/video-gui-findings.html`: EXTEND THIS DASHBOARD, never author a fresh per-run review page.

Whether the review surface should be per RUN or per PROJECT is NOT decided; the captain has not ruled on it.
Keep both possible - per-run is a filter over the existing store, not a migration.

### notes-are-anchored-not-page-comments

A note whose `anchor.selector` is empty is rejected rather than degraded to a page comment, because a page comment is the thing this channel exists not to be.
The server never computes an anchor; only the browser can measure one, because only the browser has the rendered element.

### runs-are-driven-from-the-page

Captain's ruling, 2026-08-17: runs are driven from the page.

Start does not pass `--review` because forcing review gates turns one press into 26 stops; gates are an opt-in tick box.
Step resolves the step id server-side to the first topologically-unrun step so it reuses the runner rather than adding single-step machinery.

### the-handbrake-is-a-file

Killing the runner mid-step would leave `pipeline_data.json` describing a step that half happened.
The runner reads the hold at the top of each step instead, so the step in flight finishes and writes its state first, and what lands on disk is always a real step boundary.

`pipeline_run.json` is the runner's own account of itself and nothing else may write it.
The dashboard reports run state from that file, never from the fact that a launch request returned 200.

Launches use `sys.executable` rather than a bare `python3`: the dashboard runs from the pipeline's `.venv`, and a PATH interpreter has none of the ML dependencies.

---

## Section 5 - DaVinci Resolve

### fcpxml-and-drp-are-closed

Captain's ruling: the Python timeline builder plus Fusion IS the architecture, not a workaround.

**FCPXML** was deprecated because it does not recognise DaVinci's effects.
Resolve's importer silently degrades unrecognised transitions to Cross Dissolve and scrambles the audio track layout on a round trip.
Both were measured; the corrected table is at `research/drp_reverse_engineering.md:98-106`.

**DRP project-file surgery** wrote to a temp file the renderer never loaded, so it could not fire at all.

`library/tools/execution/apply_native_transitions.py` and its test remain in the tree unused.
Do not wire either route back in.

### hasattr-is-always-true

`hasattr` returns True on Resolve's scripting proxies for invented method names as readily as for real ones.
Every guard written against it passes, whatever the object actually supports.

`CreateMagicMask` returns False for every mode, which is why it is withdrawn.

Super Scale is a **MediaPoolItem** property taking an **int**, with the companion keys `SuperScale Sharpness` and `SuperScale Noise Reduction` (no space after Super).
Setting it on a TimelineItem, or passing the string `"2"`, silently returns False.

### smart-reframe-reported-success-for-months

`smart_reframe` had a reader, the reader ran, and it printed "✓ Applied Smart Reframe" on every run for months.
It guarded on `hasattr` (always True on a Resolve proxy), was handed a Timeline that exposes no such method, and discarded the return value.

The neural-directive block in `resolve_build_timeline` is the pattern to copy: judge the call by what it returns, and say so when Resolve declines.

### stabilization-oom-87gb

Eight `Stabilize()` calls on project 001 took Resolve to **87.4 GB** resident and macOS jetsam killed it.
The kill is recorded as `largestProcess: "Resolve"` in `/Library/Logs/DiagnosticReports/JetsamEvent-*.ips`.

`neural_engine_directives` is applied AFTER every clip, comp, overlay and SFX is placed.
The project database kept only the flush from before that pass, so the music, every SFX and all eight Fusion comps were gone.

Stabilization changes picture steadiness and nothing else: never structure, timing, framing, grade, captions or sound.
That is what makes it safe to pop off the in-memory manifest for a timeline meant to be scrubbed rather than shipped.

### keyframes-outside-the-played-window

A clip with 513 source frames placed as 410 frames on the timeline has a Fusion frame range of 0-512.
`clip_dur` sets the comp's frame RANGE (GlobalIn/GlobalOut) and must come from the SOURCE clip frame count, read as `int(mpi.GetClipProperty('Frames'))`, not from `clip.GetDuration()`.

Using `0..clip_dur` for keyframes when `source_in` is not zero puts all motion outside the frames that play.
A segment from frames 25-97 of a 5657-frame clip must have its zoom ramp between 25 and 97, not between 0 and 5656.

### background-sized-to-the-delivery-frame

Every Background node `build_effect_comp` draws - vignette, fade, both transition halves - is a solid image merged over `MediaIn`.
Sized to the delivery format instead of the source clip's own resolution, it paints a hard-edged rectangle in the middle of the picture, and no warning fires, because a wrong-sized Background is a valid comp.

`CompEngine.from_params` had the fix and the comment; the renderer calls `build_effect_comp`, which did not.

Read the size off the MediaPoolItem's `Resolution` and do NOT swap it for rotation - Fusion gets the stored frame.

### house-look-missed-the-broll

`compile_manifest` merges the house look onto both V1 and V2, and the pass that drew it read `tracks['V1']` alone.
Project 001's three cutaways therefore played at a different contrast with no grain and no vignette beside the A-roll.

Transitions stay on V1 because `after_clip` indexes the V1 clip LIST; replaying it elsewhere draws a transition at an unrelated cut.
The drop detection in `build_verification` now asks whether a label was PLACED, not whether it is on V1.

`tests/test_house_look_reaches_broll.py` drives the real pass against a fake Resolve.

### pan-tilt-and-volume

`SetProperty` returns False for `PanX` and `PanY` and reads back None, silently: there is no such property.
`ZoomX` and `ZoomY` are real.

`Volume` on an audio TimelineItem also returns False on Resolve 21, and the reason is now known rather than guessed: an audio `TimelineItem` has **no property dictionary at all**.

    audio TimelineItem GetProperty()  ->  {}
    video TimelineItem GetProperty()  ->  ['AnchorPointX', ..., 'Pan', ..., 'ZoomY']   (25 keys)

So `SetProperty("Volume", x)` does not fail because the name is wrong; it fails because the property system on that object is empty.
Every spelling returns False and reads back None: `Volume`, `volume`, `Gain`, `Level`, `AudioLevel`, `ClipVolume`, `Pan`.

The rest of the surface was enumerated with `dir()` across every proxy, which is truthful where `hasattr` is not.
The complete set of audio-adjacent methods anywhere in the API is `GetFairlightPresets`, `ApplyFairlightPresetToCurrentTimeline`, `InsertAudioToCurrentTrackAtPlayhead`, `AutoSyncAudio`, `GetAudioMapping`, `PerformAudioClassification`, `ClearAudioClassification`, `TranscribeAudio`, the `Timeline` track calls (`AddTrack`, `SetTrackEnable`, `SetTrackLock`, ...), `GetSourceAudioChannelMapping`, `GetTrackTypeAndIndex` and `GetVoiceIsolationState`.
There is no fader, no pan, no solo, no automation mode, no bus, no FairlightFX.
`Fusion.ActionManager.GetActions()` resolves to 199 unique action ids and exactly three match `audio|volume|gain|level|fader|fairlight|mix|bus|pan|mute|solo|automat` - `Fusion_Zone_Expand`, `Player_Gain`, `Viewer_Show_GainGamma`, all image controls - and the set does not grow on the Fairlight page.

**This is a complete enumeration, not a failed search.**
Levels go through OTIO instead; see [the-mix-goes-through-otio](#the-mix-goes-through-otio).

`TimelineItem.GetProperty()` with no argument returns the whole dict.
Read the truth off it before trusting any property name.

### audio-pool-items-report-24fps

Resolve audio pool items report 24fps regardless of the timeline's frame rate, and `AppendToTimeline`'s `startFrame`/`endFrame` are in the SOURCE timebase.
Computing audio in/out against the timeline fps stretched the music to 125% and padded the export with trailing black.

### renders-are-silent-by-default

`SetRenderSettings` must set `ExportAudio` and `AudioCodec` explicitly or the render has no audio at all.
`resolve_render.py` now also probes the output for an audio stream before reporting success, because a silent render is otherwise indistinguishable from a good one.

### the-mix-goes-through-otio

Until this landed, the renderer turned every `audio_mix.music_automation` entry into a cyan timeline marker reading "Target Level: -96dB (silent)" and set no level at all.
On project 001 the two blocks the spine planned `silent` played music at full level in the finished video, and the marker made it look handled.

`Timeline.Export(path, resolve.EXPORT_OTIO)` writes plain JSON in which each audio clip carries a `Fairlight Clip Volume and Fades` effect, and its `volume` parameter is **dB directly** - no fader law, no taper.
`Key Frames` maps a frame number to a dB value and Resolve interpolates linearly between them, so a pair of keys at one value is a plateau and the gap between two plateaus is a ramp.

**What survives an OTIO round trip.**
Established by exporting 001's built timeline, importing it back and comparing the two:

| | survives | how it was established |
|---|---|---|
| clip placement, durations, track structure and names | yes, frame-exact | `GetItemListInTrack` on both |
| transform: `Pan`/`Tilt`/`ZoomX`/`ZoomY` (`_apply_conform`) | yes, exact | `GetProperty` on both; OTIO carries it as a normalised `transformationPan` |
| timeline markers, with colour and name | yes, all 11 | `GetMarkers()` on both |
| native transitions | yes | authored a `Transition.1`, imported, re-exported unchanged |
| Fairlight clip volume and keyframes | yes | the point; measured below |
| **Fusion comps** | **no** | `GetFusionCompNameList()` empty on all 8 V1 clips |
| **CDL grades** | **no** | `GetNodeGraph().GetToolsInNode(1)` returns `None` after import and `['Primary Balance', 'Saturation, Hue & Lum Mix']` before - and the getter was proved truthful by calling `SetCDL` on one imported clip and watching it change |

That is why the round trip runs at PLACEMENT time: the two things it destroys are the two the renderer had not applied yet.

**Where a keyframe's frame number is measured from.**
Two renders settled it.
A music clip was placed at timeline frame 150 with keys at 300 and 450, first with source in-point 0 and then with source in-point 200.
Both times the ducked floor landed at 15.0-20.0s in the rendered file, which is timeline frames 450-600 - the keys plus the clip's own start.
Timeline-absolute would have put it at 10.0-15.0s; source-relative at 8.3-13.3s.
So the numbers are **clip-relative**, and the earlier scouting note that called them "timeline frame number" was true only because that clip began at frame 0.

**Three ways the route answers None and says nothing.**
`ImportTimelineFromFile` returns None, with no diagnostic and no partial import, when any referenced media file is missing, when the path is relative, or when the timeline name is already taken.
The first is checked before the call, the second by passing an absolute path, the third by renaming the placement timeline to `<name>__premix` for the length of the import.

A fourth trap is in the export rather than the import: Resolve writes `"Parameters": []` on the clip-volume effect whenever every value is at its default, so a patcher that looks for an existing `"Parameter ID": "volume"` finds nothing, changes nothing, and the render comes back unchanged.
The parameter has to be inserted.

**Measured on 001.**
The bed was rendered twice from the same build - once as mixed, once from the pre-mix export at unity - with A1 and A3 muted so the music could be measured on its own, and differenced per second:

    time     mixed(mean)  unity(mean)  delivered   planned
     0-2s        -91.0       -45.5      floor      silent  (-96)
       3s        -57.8       -45.8      -12.0      fade_in (-12)
    6-17s        -64.6       -46.6      -18.0      background (-18)
      19s        -37.6       -31.6       -6.0      prominent (-6)
      34s        -27.2       -21.2       -6.0      prominent (-6)
    36-43s       -34.6       -17.6      -18.0      background (-18)
    44-54s        -91.0      -20.2      floor      silent  (-96)

Every plateau is exact to 0.1 dB.
The silences read as the file's own -91 dB floor rather than -96 because that is as low as a 16-bit AAC master goes; against the unmixed bed they are 44 to 71 dB down.
The ramps land on the planned frames: out of the second `prominent` block, keys at frame 1061 (-6 dB) and 1091 (-18 dB) measured -6.0 dB through frame 1050, -8.6 at 1065, -14.9 at 1080, and -18.0 from 1095 on.

**Routes not taken.**
DRT blob surgery reaches the same data through a supported import and preserves Fusion comps, but only a static gain is demonstrated on it: automation is stored zstd-compressed under a different flag byte, in 64-byte records that were characterised and never decoded.
It also depends on an undocumented binary layout that Blackmagic can change in any release.
FCP7 XML works, keyframes included, but its round trip is not level-transparent - an untouched export and reimport comes back a constant **3.1 dB quieter**, and every level then sits on top of that tax.
AAF carries no gain entries. EDL is video only. Control surfaces and UI automation were never needed.

### media-pool-name-collisions

Overlay segments from different sources often share generic filenames like `seg_000.mov`.
Importing them into the same media pool makes basename lookups silently pick the wrong clip.

### reading-a-killed-build

Resolve is the captain's application and a crashed build is not licence to start it.

`~/Library/Preferences/Blackmagic Design/DaVinci Resolve/dblist.conf` names the active database.
Each project under it is plain SQLite at `<db>/Resolve Projects/Users/guest/Projects/<folder>/<name>/Project.db`.
**Copy it before opening; never open it in place.**

The join is `Sm2Timeline` -> `Sm2Sequence.Sm2Timeline_id` -> `Sm2SequenceContainer.Sm2Sequence_id` -> `Sm2TiTrack.Sm2SequenceContainer_id` -> `Sm2TiItem_Sm2TiTrack`, in which **`DbOwner` is the TRACK and `DbAssociate` is the ITEM** - the reverse of what the column names suggest.
`Sm2TiCompositionTable` holds the Fusion comps.

It shows only what was FLUSHED, which is exactly what makes it useful: the gap between it and the manifest is where the build died.

It cannot answer everything.
Conform geometry (`Pan`/`Tilt`/`Zoom`) sits in a binary `FieldsBlob`, so proving the picture band still needs Resolve running.

---

## Section 6 - the spine contract

### get-clip-id-disabled-beat-alignment

A guard reading `block.get("clip_id", "")` on blocks that only had `content.clip_id` silently disabled beat-aligned cutting for four audits.
That is why a missing key must raise rather than fall back.

### overlapping-source-ranges-play-twice

Two body passages cut from one clip that claim overlapping source ranges make the overlap play twice across the cut.

The drift threshold is a secondary aid only: the shipped case drifted 0.76s and sailed under it.

This is fixed in code and covered by tests, but the export at the reference project was NOT regenerated.
The mp4 on disk still repeats "to post" at ~10.4s, so the fix is not verified in a render.

---

## Section 8 - project management

### nothing-owned-the-project-folder

`library/tools/paths.py` has owned the repo side since the beginning and is well made.
Measured on 2026-08-25: **zero of the twenty-one steps imported it**, and **fifteen of them composed project output paths independently**.

Nothing owned the layout of a project folder, so every step invented its own answer and no two had to agree.
What that produced, all of it real:

- The scaffold in `project_registry.PROJECT_DIRS` created `pipeline_output/subtitles` and `pipeline_output/motion_graphics`.
  Step 4.05 writes `subtitle_segments` and step 4.06 writes `motion_graphics_segments`.
  Two directories nothing ever opened, and two the scaffold never made.
- Step 1.03 wrote its vision profiles to `raw/analysis/` and step 1.07 wrote OCR to `raw/analysis/ocr/` - inside the captain's own footage directory, which was therefore not read-only in any enforceable sense.
- Steps 4.05 and 4.06 fell back to `<repo>/pipeline_output/` when handed no `project_folder`.
  In a disposable worktree that means the render is gone with the worktree.
  It is the same shape as the `--output-dir ./pipeline_output` default that banked project 001's seventeen-clip WhisperX index inside a treehouse checkout.
- Step 2.04 downloaded chosen music into `music/`, mixing a pipeline product into the captain's library.
- `ProjectConfig` carried its own `raw_dir` / `pipeline_output_dir` / `exports_dir` properties, a second definition of the same answer.

`vision_pipeline_v3.load_temporal_index` still reads `raw/analysis/temporal_index/`, a directory step 1.04 has never written to, so that read has never fired.
Deliberately left as it is: repointing it would change what step 1.03 computes, which was out of scope for the layout work.

### nine-hand-made-backups

Project 001's root carried nine hand-made copies of `pipeline_data.json`, 25 MB in total:
`.bak`, `.bak2`, `.bak3`, `.bak4`, `.bak2_migrated`, `.bak_phase1_landscape`, `.bak_phase2_baseline`, `.bak_pre_trans_rerun`, `.bak-before-index-move`.
They span 2026-08-10 to 2026-08-21.

They were not junk - something real was being protected, and the run history says what: before the split ledger existed, moving `pipeline_data.json` aside was the only way to redo creative work.
What they lacked was a policy. No retention, no naming, no way to tell which one mattered, and they sat beside the file they were backing up.

Ten was chosen because nine spanned twelve days at the captain's real cadence, and because ten copies at ~4 MB is ~40 MB against a 6.8 GB project - bounded, and small enough that the bound never has to be argued about again.

One per run rather than one per save, because `save_pipeline_state` runs after every step: a per-save policy would spend the whole retention window inside a single run and lose exactly the thing these files were keeping.

### the-folder-could-not-be-read-back

The layout landed and the folder was tidy, and the captain said the point was not tidiness:

> "i meant for you to clean up the actual pipeline output folder so we know what everything is and what step it relates to and all that ... i think for me to be able to work with you on auditing all the steps manually, we need to have everything able to be properly traced and be able to read that traceback of steps and whatnot. and then the lavish docs can be rewritten just referencing them instead of trying to copy things into them"

Ruling of 2026-08-25. The requirement is provenance and legibility.

What was already recorded, measured on project 001 before any of this was built:

- The two ledgers carried `completed_at` and `elapsed_s` for every step, plus `failed_steps` and `step_errors`. Real facts about a real run.
- `dag.json` carried the consumption graph in its 99 edges, so "what did this step read" was answerable at the step level.
- Several artifacts already named their own source: `temporal_index/clip_001.json` carries `source_file`, a vision profile carries `file_path`, a prosody profile carries `audio_file`.
- `source_fingerprints` linked each `clip_XXX` to a path and a digest.

What was not recorded at all: which step wrote which FILE, and any notion of a run. `pipeline_log.jsonl` is append-only across every run with no run id, and its 50 `step_end` events all name the step `unknown_step`.

So the change is narrow: a run identity, an observed artifact ledger, and two generated documents. The `declared` fallback exists because project 001's runs already happened and re-running them was explicitly out of scope - reconstruction is legitimate, and pretending it was observed is not.

The three files `organize` could not attribute - `001.mov`, `fully loaded demo v0.mov`, `001.PNG` - were re-checked with the provenance machinery in hand. Nothing names them: not `pipeline_data.json`, not any per-step export, not any QA report, and not the archived pre-rerun state. They remain unknown, and `unsorted/` is `Kind.UNSORTED` precisely so that `organize` having MOVED them is not reported as `organize` having PRODUCED them.

### by-kind-was-the-wrong-axis

The first layout grouped by KIND - `pipeline_output/prosody/`, `pipeline_output/temporal_index/`, `pipeline_output/subtitle_segments/`.
That was a correct answer to the brief it was given, and the brief was wrong.
The captain, on being shown it:

> "i wanted the pipeline output to be organized by step so i could step through it as well, why did you decide to organize it like this"

Ruling of 2026-08-26. The folder structure IS the traceback. Sidecar metadata and an index do not give a reader that; the directory listing does.

Two things hid the step from the reader, and the content was already almost step-shaped: the directory names said what the files WERE rather than which step made them, and they sorted alphabetically rather than in run order.

The multi-writer audit that decided the shape, measured before any of it was built:

- `Area.TEMPORAL_INDEX` has exactly ONE writer, step 1.04. Steps 1.07 and 2.02 reach it only through `read_dir`, `os.listdir` and reads. So by-step forced no compromise there and no shared area was needed.
- Steps 4.05 and 4.06 appeared to write the output root. Both assignments were dead - the variable was assigned and never used.
- `exports/` is genuinely written by two steps, 6.01 the render and 6.02 the QA report. It stays at project level, because it is the deliverable rather than any step's workspace, and both writers are declared.
- Step 6.02 wrote `qa_report.json` via `os.path.dirname(video_path)` - a sixteenth inline path composition, missed by the first pass because it composes from a path rather than from `project_folder`.

### a-declaration-that-went-stale

`classification.per_clip_artifacts` used to spell the path out: `pipeline_output/temporal_index/{clip_id}.json`.

When the layout moved the vision profiles out of `raw/analysis/`, the declarations in steps 1.03 and 1.07 were left behind pointing at the old location.
Nothing failed. `--rerun semantic_analysis:clip_007` deleted nothing, so the step's own "already on disk?" check found the profile still there and re-ran nothing - silently, and reporting success.

A declaration that can go stale is the exact failure the layout owner exists to remove, so the prefix is now the owner's to state (`{area:vision_analysis}/`) and only the filename is the step's.

---

### tests-bound-to-the-captains-project

`tests/test_pipeline.py` walked the real projects root at IMPORT time, so it happened on every pytest COLLECTION rather than only when its own test ran:

    from library.tools.paths import PROJECTS_ROOT
    for entry in PROJECTS_ROOT.iterdir():
        if entry.is_dir() and (entry / "project.yaml").exists():
            PROJECT_DIR = str(entry)
            break
    OUTPUT_DIR = os.path.join(PROJECT_DIR, "pipeline_output")

`PIPELINE_TEST_PROJECT` overrode it, but the FALLBACK was the real thing and nothing sets that variable in CI or locally.
The module then wrote `step_2_05_v2.json`, `step_3_01_v2.json`, `step_4_01_v2.json`, `assembly_manifest_v2.json` and `timeline_v2.xml` into whatever project it had bound (line 71), and `shutil.move`d a `.bak` back over `step_2_05.json` and `step_4_01.json` in a `finally` (line 284).

**This is not known to have destroyed anything.**
It was found on 2026-08-26 while investigating three exports that vanished on 2026-08-25, and the captain has since accounted for those separately.
It is a live hazard on its own account, and the reason it is priority zero rather than tidiness is that there is no undo: this machine has no Time Machine destination configured.

Two accidents are why nothing had been written recently, and neither is a control:

- On this machine the walk aborts on `.DS_Store`, which `iterdir` returns first and which `entry.iterdir()` raises `NotADirectoryError` on - caught by the `except (ImportError, FileNotFoundError, OSError)`. Delete the `.DS_Store` and it binds to the first real project instead. Reproduced: with the `.DS_Store` skipped it resolves `video_projects/4th-wall`.
- In CI the root does not exist at all, so the walk raises `FileNotFoundError` and the test skips. CI has therefore never run this; only a local machine could.

**The test could not have passed anyway, and it asserted nothing.**
Judged against a temporary fixture as the task asked:

- Zero `assert` statements in 363 lines. It prints and it writes.
- Line 295 does `from xmeml_generator import write_xmeml_file`. That module was deleted when the FCPXML route was closed (AGENTS.md section 5, [fcpxml-and-drp-are-closed](#fcpxml-and-drp-are-closed)), so the run raises `ImportError` at phase 6 even with every input present.
- It reads flat `pipeline_output/step_2_01.json` names. Since PR #167 exports go to `pipeline_output/steps/<step>/output.json`, so its own `skipif` guard can only fire on a project predating that layout and never migrated.

So it was a pre-pytest orchestrator script with a `@pytest.mark.skipif` bolted on, whose only remaining effect was writing into whatever project it found. It was deleted rather than rebuilt on a fixture: preserving a green that was never real would have meant fabricating a fully populated project to feed a route the repo has withdrawn.

**The audit of the rest of `tests/`.** Every route into a real project, not just this one:

| Where | Shape | Verdict |
|---|---|---|
| `test_pipeline.py` | walks `PROJECTS_ROOT`, binds at import, writes and `shutil.move`s | **Deleted.** The only instance. |
| `test_dashboard_api.py:10` | `@patch("library.tools.paths.PROJECTS_ROOT")` | **Left.** A string target, patched to a `MagicMock`. Never reads the value. |
| `library/dashboard/server.py` `_get_project_dir` | raises `HTTPException` when no project is set | **Left.** No fallback to scan; answers the "do the dashboard tests reach a real project another way" question with no. |
| `test_dashboard_smoke.py`, `test_e2e_dashboard.py`, `test_e2e_pipeline_run.py`, `test_dashboard_run_control.py`, `test_run_traceback.py` | build a project under `tmp_path`/`TemporaryDirectory` and copy `tests/fixtures/` state into it | **Left.** Already correct. |
| `test_runner_no_fixture_shortcuts.py` | quotes `/Users/prajwal/.../001/...` in its module docstring | **Left.** Documentation of the hazard it guards. The source scan is docstring-aware for this reason. |
| `test_night_card_delivery.py` `CARD_PROJECT` | `tests/fixtures/night_card_project` | **Left.** In-repo fixture, read-only. |
| `test_color_grade_delivery.py`, `test_integration.py` | `project_folder="proj"`, a relative path | **Left.** Nothing on that path is opened, verified by a clean `git status` after a full run. Not a route to a real project either way. |
| `test_runner_library_paths.py`, `test_music_selection_contract.py`, `test_dotenv_encoding.py`, `test_validate_sfx_library.py` | set `PIPELINE_SFX_LIBRARY`/`PIPELINE_MUSIC_LIBRARY` | **Left.** Set to `tmp_path`, or named in prose. These are the shared libraries, not a project. |
| every `subprocess.run` in `tests/` | inherits `os.environ` | **Left, and now covered.** The conftest sandbox is an env var, so a child process inherits the sandbox rather than the real root. |

**What was measured.** A full `python -m pytest tests/ -q` with `PIPELINE_TEST_PROJECT` unset, bracketed by a sha256-per-file snapshot of all 210 entries under the real projects root: identical before and after.

**Why `paths.py` and not `project_layout.py`.**
The task asked whether the by-step layout owner could be the enforcement point. It cannot: `ProjectLayout` is constructed from a folder passed in, and has no way to judge whether that folder is the captain's or a fixture's - which is the whole reason it is parameterised. `PROJECTS_ROOT` is the only thing in the repo that names the real root, so that is where the door is.


## Section 9 - environment

### text-true-decodes-with-the-locale-codec

The pipeline writes UTF-8 status glyphs.
A check-mark in a child process's stderr failed a render under an ASCII locale, because `text=True` decodes with the locale codec rather than UTF-8.

### the-dashboard-could-not-be-opened

2026-08-26. The captain tried to open the review dashboard on the day the footage search landed, and could not.
Three defects, each sufficient on its own. Verbatim:

    ❯ cd ~/Documents/content_stuff/video_editing_pilot
      source .venv/bin/activate
      python3 manage_project.py dashboard 001
    source: no such file or directory: .venv/bin/activate
    ERROR: Missing ML dependencies: whisperx
    The pipeline must be run from its virtual environment.
    Run this to activate it:
        source .venv/bin/activate
    ❯ source .venv/bin/activate
    source: no such file or directory: .venv/bin/activate

**The check ran at import time, for every subcommand.**
`_preflight_check()` was called at module scope, before argparse had seen the word `dashboard`, and it required `mlx_vlm`, `whisperx`, `easyocr` and `torch`.
The dashboard needs none of them: measured on this machine with `whisperx` absent, `from library.dashboard.server import start_server` imports and the server serves.
Every ML import in the repository is inside a pipeline step or under `library/tools/analysis/`, and `run` is the only command that reaches one - it launches `run_pipeline.py` with `sys.executable`, which is what makes checking the parent interpreter a real check of the child rather than a guess about it.
`torch` inside `footage_query` is imported lazily inside a function, so the dashboard's footage search does not pull it in at start.

**The message named a path that does not exist.**
It printed a bare `source .venv/bin/activate`.
The venv is per checkout and gitignored; the captain's checkout has none, so obeying the instruction produced a second and more confusing error than the first.
The workflow is real - every worktree that runs the pipeline has one, made by hand - so the message now names the activate script by absolute path when the checkout really has one, and gives the `python3 -m venv` / `pip install -r requirements.txt` pair when it does not.

**The slug could not have resolved either, and the reason was masked.**
`PROJECTS_ROOT` is `~/Documents/content_stuff/video_projects`; project 001 lives at `~/Documents/content_stuff/post a day keeps the apple away/001`.
`get_project` has accepted a path since #79 and has said so in its error since then, but the import-time check killed the process before argparse ran, so that message was never printed.
Fixing the first defect is what made the third one visible.

**`PROJECTS_ROOT` is not exclusive, and this is not a layout bug.**
`resolve_project_path` deliberately loads a project living anywhere on disk, and §8 documents it.
The captain's main project sitting outside the root is a configuration choice, not a fault; what was missing was discoverability, so the lookup failure now names the root it searched on its own line, lists what it found, and shows the path form of the command that was just typed.

Measured after the fix, with `whisperx` absent from the interpreter:

    ❯ python3 manage_project.py dashboard 001
      Error: No project with slug '001'.
        Searched: /Users/prajwal/Documents/content_stuff/video_projects
        Found there:
          4th-wall
          geo-podcast
          podcast-roughcut
          test-proof
        A project kept outside that root is addressed by its path instead of its slug, for example:
          python3 manage_project.py dashboard /path/to/001

    ❯ python3 manage_project.py dashboard "/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001" --port 8461

      Review Dashboard
      Project: /Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001
      URL:     http://127.0.0.1:8461
      Press Ctrl+C to stop

    INFO:     Started server process [79136]
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://127.0.0.1:8461 (Press CTRL+C to quit)

`/api/project` answered `{"slug":"001", ..., "raw_footage_count":17, "steps_completed":25, "total_steps":26}` and `/api/footage/search/status` answered 200 - the footage search this unblocked.

`tests/test_cli_ml_preflight.py` holds all three, and blocks the ML packages in a CHILD interpreter rather than reloading `library.dashboard` in process - the first attempt did reload it, and handed the rest of the session a second copy of the module the other dashboard tests key their global state off, turning 27 unrelated tests red.

---

## Section 10 - cross-cutting rules

### key-name-mismatches

Steps disagree about what a field is called, the reader `.get()`s a default, and the pipeline reports success over empty data.
This is the dominant bug class in this repository.

Known disagreements, kept as the worked examples:

- B-roll assignments carry `video_in`/`video_out` plus `timeline_start`/`timeline_end`.
- SFX carry `timeline_in`/`timeline_out`.
- Semantic documents are keyed by FILE STEM while the catalog uses `clip_XXX`.

`tests/test_dashboard_captured_state.py` is the standing guard, and it runs against `tests/fixtures/captured_run/`, a 77KB capture from a real broken run.

### compile-manifest-read-an-empty-catalog

The per-step `*.json` files in `pipeline_output/` are a best-effort dashboard export.
A step that ran before that export existed leaves none, and a missing file reads as `{}`.

`compile_manifest` spent entire runs compiling against an empty catalog for exactly that reason.

### empty-llm-schema

`present_llm_step` builds the injected schema from `interface.outputs` minus anything the bridge already produced.
`music_selection` therefore asked its model for nothing at all and got `{}` back for months.

The same hole in the QA loop demanded post-bridge outputs from the LLM, so every attempt at `mesh_spine` "failed": its LLM writes `structure`, while the post-bridge computes `audio_spine` and `timed_spine`.

### the-brand-reached-no-planning-step

`TemplateLoader.get_brand_constraints` branched on `step_id == "step_2_01_creative_direction"` and two siblings - the steps' manifest ids.
Its only caller, `present_llm_step`, passes `node_id`, and the DAG's ids are `creative_direction`, `plan_transitions`, `plan_vfx`.
No branch could match, so the constraints string was `""` for every step, every template and every project from the day it was written.
`plan_transitions` chose transitions with no knowledge of which ones its brand permits.

Measured both ways against `default_brand`: 382 B / 179 B / 41 B with the directory name, `""` with the node id.
The whole block costs about 250 tokens across the three steps.

The one existing test called the function with the identifier the FUNCTION wanted, which is why nothing caught it for the life of the code.
Every assertion in `tests/test_brand_constraints_reach_the_prompt.py` therefore starts from `dag.json` and the templates on disk.

A second half was found while fixing it: the `agy` request file wrote `prompt` alone, while `constraints` was concatenated only into the API path's `full_prompt`.
In the mode this pipeline actually runs, a working `get_brand_constraints` would still have reached nobody.

### two-steps-had-no-projection

`mesh_spine` and `review_rough_cut` declared no `context_fields`, so each was handed `temporal_index` in full: a 5 Hz per-clip stream of camera-motion decomposition, optical flow, energy curves and face presence, JSON-escaped inside TOON table cells.

Measured on project 001, `o200k_base` over the archived `llm_requests/*.json`:

| step | before | after | share that was `temporal_index` |
|---|---|---|---|
| `mesh_spine` | 720,067 tok | 9,857 tok | 98.2% |
| `review_rough_cut` | 729,809 tok | 9,353 tok | 97.2% |

Neither step's own code reads it: `grep -rn temporal_index` over each step directory finds only the manifest, the preconditions and the handoff's "Reads" table.
It is still a declared input, and the post-bridge and `step.py` still receive it unprojected - the drop is prompt-side only.

`mesh_spine` also received no vision at all, so it sized `transition_slot` blocks - non-speech picture - before anything had checked that usable non-speech picture existed.
The footage cards that fix it cost 2,389 tokens, 0.3% of what the call cost before.
`clip_catalog` is routed with them because the vision documents are keyed by file stem and everything else in that prompt is keyed `clip_XXX`; a card nothing can be joined to is not delivered.

`word_timestamps` came out of the four `timed_spine` projections in the same pass: about 5,400 tokens a step, read by `spine_contract`, `bookends`, `plan_subtitles` and three post-bridges, all of which receive the unprojected inputs.

Fleet total across the eleven LLM calls: 1,725,098 tok to 233,733 tok, -86.5%.

**Do not read a token figure the pipeline reports about itself.** `pipeline_log.jsonl` records `len(s.split()) * 1.3` against the RAW pre-projection inputs; measured errors run 0.3x to 9.9x in both directions. Measure from the archived request files.

### thirty-three-thousand-tokens-for-three-bytes

`semantic_analysis` (1.03) is deterministic, and its own `handoff.md` says so: "No model is asked to author the per-clip analysis document here."
The file sits beside `step.py`, which is what `get_step_implementation` classifies on, so the runner made an LLM call anyway - with `expected_schema` of `[]`, because `already_have` had removed every declared output.
The response was the three bytes `{}`, after 33,051 tokens of vision documents, every run.

The skip is a property of the SCHEMA, not of the step's name: `present_llm_step` returns `{}` when the model is asked for no keys at all.
`render` and `validate` are the same classification accident but not the same call - both ask for something, and what they should ask for is an open captain decision.

### vision-schema-two-views

`vision_pipeline_v3.py` emits `scene[]`/`camera[]`/`actions[]`/`objects[]`/`assessment{}`.
Consumers historically read `analysis.*` and `blocks`.

An absent field warns in `context_projector`; a fabricated one silently misleads the model.
That asymmetry is why the adapter derives only what v3 actually measured.

### brand-template-never-reached-the-run

`pipeline.brand_template` in a project.yaml was read by `delivery_format` and by nothing else in the run.
`load_pipeline_state` populated `state["brand_template"]` only from the process manifest's `default`, which has none, so `gather_step_inputs` resolved the in-code `_get_default_template()` for every step of every project.

A project naming `cinematic_narrative` got vfx_intensity 0.0 instead of 0.3, the full transition vocabulary instead of its four, no `subtitle_style` and no house look at all - silently, with the run reporting SUCCESS.

### delivery-format-is-not-the-source-resolution

The catalog's `source_resolution` describes the footage.
Under the name `project_resolution` it was used as a render target, and project 001 shipped a 1920x1080 master with the framing mechanism idle (target == source means nothing to fit) and the vertical overlays banded down the middle.

Nothing carries the value as a key because `project_resolution` was mapped by no DAG edge at all, and every `.get(..., [1080, 1920])` in the tree silently read its own fallback.
`project_fps` had the identical hole and now has edges.

### the-beat-grid-does-not-start-at-zero

`music_pipeline.analyze_music` emits `tempo.beats` and `tempo.downbeats`.
There has never been a `beat_grid` key, though two steps asked for one and got `[]`.

No track's first beat lands at 0.000s, so a synthesised `[i * 60/bpm ...]` grid is offset from the music by the whole lead-in.

The times are in the MUSIC file's clock and are used as timeline times, which holds only while music is placed at `source_in` 0.
`compile_manifest` asserts that.

### building-is-not-high

Two readers bucketed the same free-text `target_energy` field differently and only one acted.

`transition_selector` did not match "building", which was the correct outcome.
`creative_cohesion.map_energy` substring-matched it to "high" and cut three 500ms defocus transitions to 333ms - on a direction whose own rationale chose "building" OVER "high" because high "would fight the source".

"Building" names a TRAJECTORY, not a level.

`WITHDRAWN_HIGH_WORDS` records why "dynamic" and "fast" are out too.
Widening the high bucket decides which transitions get drawn on every project using the word, so it is a decision, not drift.

### end-time-is-a-source-timestamp

`speech_sequence`'s `end_time` is a SOURCE timestamp.
Reading it as the timeline duration made `creative_cohesion` report 001's 54.77s timeline as "40.1s ... below the minimum target zone" one step after `review_rough_cut` had measured it correctly.

`0.0` means "no evidence" - say so rather than warn about a length nothing measured.

### unread-parameter-names

The renderer dispatches on parameter NAMES in `apply_fusion_comps.build_effect_comp`.
A planner emitting a name nothing reads produces a comp without that effect, and no warning.

`zoom_percent`, `intensity_px` and `scale_factor` killed three of five VFX types that way, and four of the colour grade's five nodes had no reader at all.

Withdrawal is a legitimate outcome and a silent unread key is not, which is why the reason is recorded where the design lives: `GRADE_PIPELINE_DELIVERY` in `step_5_01_color_grade/step.py`, `WITHDRAWN` in `transition_vocabulary.py`.

`tests/test_manifest_readers.py` discovers every top-level key from `compile_manifest`'s manifest literal and requires a named reader that really contains `manifest[key]`, plus one sentence saying what that reader does to the picture or the sound.
Writing the sentence is the check the AST cannot do for you.

### overlays-that-draw-nothing

Step 4.06 ran Remotion eight times on project 001 for 53.8 MB of ProRes 4444 in which no pixel is ever opaque, and placed all eight on V4 - so `render.json` said "V4: 8".

The output carries NO `available` key when nothing draws, because `available: false` anywhere fails the run.

### manifest-validator-semantic-half

The regression fixtures in `tests/fixtures/captured_run/` come from a real broken run.
Replacing them with empty-list fixtures removes the only thing that proves the assertions fire.

Correction of record: an earlier version of this entry claimed the validator checks ducking curves.
Nothing in the module ever has.

### undeclared-black

The rough-cut review records only negative gaps by design.
Before `_assert_timeline_fully_covered` existed, the only thing that noticed 6.4s of black was the ffmpeg probe in step 6.02, one step from the end of the run.

Both gates bound a declared beat by `MAX_DECLARED_BLACK_BEAT_SECONDS` from the spine contract.
Keep that bound in one place, or a beat passes compilation, burns a render, and fails at the last step.

### safe-area-and-the-caption-grouper

Captain's ruling of 2026-08-25: one master serves Reels, TikTok and Shorts and obeys the strictest of them.
The vertical profile is the published short-form map - top 120, bottom 320, right 120, left 90 at 1080x1920.

`plan_subtitles.text_fits_on_screen` measured real glyph widths and had NEVER RUN.
It was switched on by `audio_spine["subtitle_style"]["font_path"]`, which no producer ever wrote, so grouping fell back to `max_chars = 18` - correct for a 58px caption, and 1663px of ink in a 1080px frame at the 160px style templates resolve today.

A safe area applied only on the render side lifts the captions clear of the UI and leaves them clipped left and right, which is why all four consumers read the one enumeration.

Measuring a VARIABLE font requires setting its weight axis: Montserrat-Variable defaults to Thin.

A group split cannot fix one over-wide word, so such a card carries `fit_scale` and the render draws THAT CARD smaller.
The style's font size is untouched, because caption size is an open captain decision.

### the-squashed-face-frame

`compute_face_presence` extracted at `scale=320:180`, so a rotated iPhone clip - 1080x1920 after ffmpeg's autorotate - reached the frontal cascade squashed about 5.3x horizontally.

On project 001 that split the index exactly along `rotation`: 1,886 detections over 3,578 landscape samples, 1 over 461 rotated ones, and `subject_center_x` was None for every portrait clip.

Size is a trade, and 480 is where it was measured: bigger keeps buying recall AND false positives, and 0.34 is where `subject_framing` starts believing a track.

Note what the fix does NOT fix.
The cascade fires on trees and dashboards, and on 001's face-free B-roll a 3s window crosses 0.34 at every size above 240.
The squash was suppressing those by suppressing everything.

`tests/test_face_sample_aspect.py` pins the aspect, not a detection count, which would rot with the OpenCV build.

Face detection needs Haar cascades, so `opencv-python` is pinned `<5`; OpenCV 5 removed them.

### subject-centers-by-clip-read-only-a-mapping

Step 1.04's real output shape is `{"temporal_event_indices": [...], "full_indices": [...]}` - a LIST of per-clip dicts, not a mapping.

`subject_centers_by_clip` read only a mapping for the life of P1.2, so it returned `{}` on every real project while its tests, which all fed invented mappings, passed.

### the-letterbox-default

`_conform_fields` used to have a second branch that ran whenever nothing declared a framing intent.
Its rule was *letterbox if the primary subject is visible during this clip's range*, which on a talking head is every clip.

Project 001 shipped its A-roll in rows 656..1263 of 1920 with 61.6% of every frame below luma 12, and the whole `framing_intent`/`subject_center_x` mechanism in the other branch never executed anywhere.

The thing that branch protected - a blind centre crop beheading a speaker - is protected by the pan now, not by refusing to crop.

This reverses the letterbox-default half of Q1 (2026-08-16); the tracking half stands.
A series that wants bars declares 0.0, and `cinematic_narrative.yaml` does.

### hollow-prosody-files-cached

`speech_advanced_pipeline` wrote `{"method": null, "error": "parselmouth not installed"}` to `<clip>_prosody.json` like any other result.

Step 1.05 counted seventeen files as seventeen profiles, reported `available: true` in 0.1s, and 4.2 KB of identical error records went into the creative-direction prompt.
The files CACHED, so the next run skipped the clips.

`praat-parselmouth` is in `requirements.txt`; CI filters it, because there is no cp314 wheel.

### semantic-analysis-triggers-a-vision-run

Any clip whose id is not already a `raw/analysis/clip_profile_<clip_id>.json` triggers a full local vision run when `step_1_03_semantic_analysis/step.py` is invoked.

### gates-that-cannot-fail

`timeline_qa.verify_fusion_comps` had `pass` as its only loop body and reported the Fusion pass healthy whatever the timeline held.

`creative_cohesion`'s engagement check tested `isinstance(engagement, (int, float))` on the DICT that `engagement_scorer` emits, so it read every real passage as unscored.
Read `engagement["composite"]`.

A gate that cannot fail is worse than no gate, because it reads as coverage.

### gates-that-fail-correct-output

`subtitle_qa` sampled two fixed instants of a transparent overlay, which on an ordinary caption pause are blank.

gemma-4-12b passed those blanks on one run and failed them on the next, then failed four demonstrably clean caption frames three times out of three with invented defects - "cut off by the bottom edge" of type with 150 clear rows beneath it.

Its mechanical half - ink exists, ink is inside the frame, ink is bottom-positioned - now decides, and the model's typography opinion is recorded rather than enforced.

### baseline-craft-properties

Source: `data/vep-craft-reference-decomposition/report.md`; its Appendix A is the reproducible method.
Test: `tests/test_baseline_craft_properties.py`.

The true-peak half of `measure_lufs` used to print +1.85 dBTP inside a detail string and drop it whenever the LUFS check had already failed.
It sets `passed = False` now.

Chroma and the mix REPORT A NUMBER and pass.
The chroma floor is an open captain decision and the mix has no delivery route, and a gate that must fail teaches everyone to ignore the report.

`min_sat: 10` is GONE, replaced and not supplemented: frame-mean saturation cannot be the statistic, because the captain's two reference frames differ 9.3x in it and that floor would have rejected the one they chose for Punch Card.

### no-creative-floors

A B-roll minimum and an SFX minimum both existed.
The captain removed them outright on 2026-08-20, declining warnings, a reconciled range and per-template minimums by name.

The accepted consequence is that a thin edit is no longer caught mechanically.

A floor in the PROMPT pads just as effectively as one in the bridge.
"You MUST plan exactly 5-15" is what put two cutaways and one sound into the shipped edit with rationales that said so.

`tests/test_no_creative_floors.py` originally guarded only the two steps the ruling was written about.
That is how `plan_vfx`'s "at least 3-7 VFX items" plus "every talking head clip MUST have at least a slow zoom" (001: eight effects on eight clips, one each, alternating) and `speech_sequence`'s "strictly select exactly 10-15" survived it for five days.
It now guards every step in `CREATIVE_PLANNING_STEPS`.

`_assert_sfx_distributed` catches a collapse - every SFX on one frame - not a sparse plan, which is why it is not a floor.

### silence-lost-in-the-two-word-vocabulary

Test: `tests/test_music_behavior_vocabulary.py`.
Enumeration: `library/tools/music_behavior.py`.

`mesh_spine` plans what the bed does under each block in five words - `prominent`, `background`, `fade_in`, `fade_out`, `silent`.
From the initial commit (2026-07-29) until 2026-08-25, `compile_manifest._spine_block_entry` threw that word away and recomputed a different one:

    "music_behavior": "full" if block.get("block_type") in
        ("transition_slot",) + BOOKEND_BLOCK_TYPES else "ducked",

Two words, neither of them in the spine's vocabulary, derived from `block_type` rather than read from the plan.
There is no word for silence in it, so a block the spine planned `silent` reached the manifest saying the bed plays.
It was not even self-consistent by its own logic: an `intro` block carries no speech either and got `ducked`.

**The trace, made before the change** - `_spine_block_entry` has exactly ONE caller, `compile_manifest` step.py, and the value it produced had NO reader:

- `manifest["_spine_blocks"]` is read in three places: `compile_manifest._assert_timeline_fully_covered`, `step_6_02`'s `declared_black_beat_ranges`, and `render_qa.measure_speech_above_bed` (block_type by position). All three read `position`, `timeline_start`, `timeline_end`, `block_type` and the black-beat keys. None reads `music_behavior`.
- The plan that DOES carry a level takes the other branch: `audio_mix` (5.02) reads `audio_spine.structure[*].music_behavior` straight from `mesh_spine`, not from `_spine_blocks`, and maps `silent` to -96 dB in `music_automation`.

So the docstring in `step_6_02` that reported this was right about the narrowing and about nothing reading it.
The stronger reading - that silence was destroyed before the delivery hop - is NOT what the trace shows: silence survives into `music_automation` intact, and 001's silent blocks played music because the delivery hop set no level at all, which is a separate finding.

What the narrowing cost was the manifest's own record of the plan contradicting the plan, in the place a reader would naturally look for it, in a vocabulary that could not express the thing the creative direction most wanted to say.

The remedy is not to delete the field - that is the `unread-decisions` remedy, and it is a different one.
The field carries the word now, resolved through one enumeration that both halves read, so they cannot disagree about what a word means or about which words exist.
The one true thing the reduction knew - a block with no speech under it has nothing to duck for - survives as the DEFAULT for a block that planned nothing (a bookend card, which `bookends.py` assembles and which never passes through the spine's LLM). A block that planned something keeps what it planned.

`full` and `ducked` are recorded as withdrawn, and asking for either raises and says which real word to use.

**A second instance on the same field, reported and not fixed here**: `library/tools/transition_selector.py` selects a flash when `music_behavior == "step_up"`.
`step_up` is not in the vocabulary and no producer emits it - the only occurrences in the repo are that branch and two tests that hand-write the word - so the flash branch is unreachable. That is a reader against a word that does not exist, not a narrowing, and it wants its own decision about which real word (if any) should mean "the music steps up here".

---

## Section 11 - third-party assets

### the-unlicensed-powergrade

The repository previously carried one PowerGrade, `cinematic_warm.drx` - a free gift from Zay's Aesthetics with no written terms of any kind, and so no commercial usage clause for a repository that produces commercial video.

It was removed along with the whole PowerGrade route: `build_powergrade.py`, the `luts/` and `dctls/` preset directories, and `preset_indexer.py`.
`tests/test_color_grade_delivery.py` fails if any `.drx` reappears.

### the-webfont-race

Montserrat is bundled rather than fetched because `@import url('https://fonts.googleapis.com/...')` with no `delayRender` made typography a race with the network.
A lost race rendered captions in Chromium's fallback sans at a different width, with nothing downstream able to tell.

### timed-text-drew-in-the-wrong-face

`TimedTextOverlay` loaded NO font at all until 2026-08-20 while naming a family in CSS.
Every card it rendered was already in the wrong face, and the frames were still valid pictures of the right size.

---

## Section 12 - the house look

### where-the-look-values-come-from

The values are authored from the captain's planning docs at `PLAN/series portfolio '26 planning/`, which are READ-ONLY and live outside this repo.

Nothing depends on a file inside a Resolve installation.

---

## Sections 13 and 14 - bookends, timed text and asset ownership

### bookends-only-on-some-videos

Captain's Q7, 2026-08-16: "wire them up, but only on some videos".
A template that declares nothing gets nothing.

A malformed declaration raises rather than being dropped, because a dropped declaration is a card the editor believes shipped.

`library/tools/execution/import_endcard.py` appended one out of band after compilation and is deleted; see the row in `docs/PIPELINE_PLAN.md` section 5.

### the-4th-wall-end-card

`docs/ASSET_LIBRARY_PLAN.md` was ratified 2026-08-20 and is the standing test.

The case that motivated it: the 4th Wall end card failed all three questions and was removed on the captain's ruling.
It was one previous trial run's finished artwork - series copy, absolute frame numbers from a 60.000s cut, an unbundled typeface - filed in the now-deleted `library/templates/fourth_wall.yaml` as series defaults, where no step ever read it.

The template itself was deleted on 2026-08-20 on the captain's ruling: the series template arrives later, whole, with authorisation.

Per-series typefaces live per project, not in the engine (captain's ruling, 2026-08-20).
Accepted cost: each project folder carries its own fonts and licences, and `bookend_render.py` must stage them.

### the-night-card-y-band

Geometry is normalised against the whole delivery frame, not the picture area inside letterbox bars, because there is no picture-area enumeration to resolve against.

Measured on the only finished render on disk - project 001, `Pipeline_Edit.mp4`, 1080x1920, 16:9 landscape source - the picture occupies rows **656..1263** and the burnt-in captions rows ~1699..1765.
So a `y` in 0.35..0.65 is over picture whether the source letterboxes or fills.

`tests/test_night_card_delivery.py` asserts the real card's ink lands in that band.
Put the removed end card's `y: 0.15` back and it fails at rows 260-327.

`effect.timed_text_overlay` had no reader at all, and carried the marker `NO_READER` in `tests/test_manifest_readers.py`.
That marker is gone: the reader is `library/tools/timed_text_overlay.py`, which is what makes a timed-text declaration legal under question three of the asset test.

`tests/test_timed_text_delivery.py` renders a 24-frame fixture through the real path and asserts the declared colours are in the declared rows at the declared frames - the delivery half, without which "a reader exists" is the same empty claim `smart_reframe` made for months.

The worked example of a project-side declaration is `tests/fixtures/night_card_project/project.yaml` - Through the 4th Wall's Night card, the second item in the captain's ratified build order.

### the-caption-box-is-not-one-line

The measured caption fitter landed with `fits(text)` meaning "fits on ONE line", and grouping used it.
`SubtitleOverlay/index.tsx` draws the words in a `flexWrap: "wrap"` box bounded by `captionMaxWidth`, so a card wider than one line becomes two and is drawn in full - `CaptionFitter.widest_word_width`'s own docstring says exactly that, and `fit_scale` exists only for the one word that cannot be wrapped.
So grouping was stricter than the render.

At the 160px `default_subtitles` style, 816px of usable ink holds roughly eight characters on one line.
The pre-measurement `max_chars = 18` grouping was about seventeen characters, which is two lines' worth - so switching to one-line fitting HALVED the words on every card without anyone choosing it.

A card is on screen until the NEXT card's first word, so halving the words halved the display times.
Measured on project 001, 2026-08-26: **76 of 96 cards under 0.5s**, the shortest 0.080s - 2.4 frames at 30fps, the single word `'day.'`.
`manifest_validator`'s P6 hard-fails on any card under 0.5s, so no render of any project was possible.

Two changes, and the second is the one that mattered:

* Group against the BOX (`fits_in_box`, `MAX_CAPTION_LINES`), not one line. `MAX_CAPTION_LINES` is 3, and the number is measured: at 2 the same run still lands 11 of 52 cards under half a second, because a two-line card at 160px holds about 17 characters and this speaker delivers 17 characters in well under half a second several times. Three lines is 576px of a 1920-row frame on a 320px bottom inset, so a card stays in the lower third and clear of the speaker.
* Split each block's words BALANCED rather than greedy. A greedy fill packs each card to the width limit and leaves the remainder as the next card, and the remainder is the card that flashes. `split_into_groups` now solves, per block, for the partition with the fewest cards below the floor - a small dynamic program over the words, ties broken towards the longest shortest card and then towards ending cards on punctuation. It models the real display duration, including the extension the per-block pass applies and the block end the last card is clamped to; optimising anything else optimises a number nobody renders.

Result on 001: **96 cards to 41, and 76 flashing cards to 2.**

The two survivors are the one case grouping cannot reach, and P6 now reports them rather than failing:
a card is on screen until the next card's first word, and the LAST card of a block has no next word - it leaves when the block does.
Block 2 is "today is march 25th, 2026.", its final word is spoken for 0.21s, and block 3's captions begin in the same frame.
No partition of those five words, at any width, makes that card longer.
The exemption needs the card to be last in its block AND to end at the block's end, both provable from the manifest, because a floor that exempts the general case is a gate that cannot fail.

`tests/test_caption_safe_area.py` pins all of it, including a greedy-versus-balanced fixture - project 001's own opening line, where greedy leaves `'me.'` alone for 7 frames.

### the-default-that-outvoted-the-plan

The captain's ruling of 2026-08-20 removed the creative floors.
`tests/test_no_creative_floors.py` guarded the PROMPTS, and two floors in the VFX post-bridge survived it by being code:

* `inject_default_ken_burns` added a `slow_zoom_in`/`slow_zoom_out` to every speech block over three seconds that the plan had left alone, "because the style spec requires subtle motion on all A-roll clips >3s".
* An empty plan exited 1 with `You MUST plan at least 3-7 VFX items`, contradicting the step's own handoff, which says an empty list is a legitimate answer for a piece that wants stillness.

Observed on the run of 2026-08-26: a deliberate three-effect plan came out of the bridge with seven, three of them on blocks the spine had marked "no effect" - including the closing eleven seconds, where the creative direction says the admission must not be decorated.
It is also where the previous run's `VFX family 'slow_zoom' covers all 8 V1 clips` P7 failure came from: the padding created the uniformity the check exists to catch.

Both are gone, and `tests/test_no_creative_floors.py` now drives the post-bridge as well as reading the prompt.

### hard-cuts-are-not-an-effect-on-everything

P7 failed a build for `Transition type 'hard_cut' covers all 8 cuts`. Two things were wrong.

**`hard_cut` draws nothing.** `transition_vocabulary.CUT_TYPES` says so in as many words - "Instantaneous transitions. Nothing is drawn" - and the transition handoff asks for them to dominate ("hard cuts dominate - use `hard_cut` as the default for most cuts").
A video whose every cut is a hard cut is the ABSENCE of decoration, not decoration applied to everything, and failing it is the creative ceiling P7's own note forbids it from becoming.
`CUT_TYPES` are now excluded from the check.

**The denominator counted the wrong thing.** `cuts` was `len(v1_clips) - 1`, but `transitions` carries an entry for every spine-block boundary, including the transition slots whose picture is B-roll on V2.
Project 001 plans 13 transitions across 9 V1 clips, so 8 hard cuts - 62% of what was planned - were counted as "all 8 of them".
The denominator is now the transitions the plan actually wrote.

### the-mix-target-is-not-a-separation

Issue #183 asked whether a render should FAIL when speech sits under the music bed, and the captain's answer was "turn it on, but only after the next full run of 001 confirms it passes cleanly with the mix in place".

That run was performed on 2026-08-26. **It does not pass cleanly, so `SPEECH_ABOVE_BED_GATES` stays False.**

The delivery route works. Two windows planned `silent` measured -68.6 and -69.3 dBFS against a -36.6 dBFS median of the non-silent windows, and the OTIO round trip wrote a 22-keyframe curve spanning -96..-6 dB.
6 of 9 speech-bearing windows met the margin.

Three `background` windows did not: +12.6, +13.6 and +12.5 dB where the plan asks for +18.
What fails is the TARGET, not the delivery.
`background` means -18 dB and `audio_mix` applies that as an absolute clip gain, while the check reads it as the separation between the speech and the bed.
Those agree only when the music file's own level is at or below the speech's.
On 001 the music sits about **8.6 dB hotter** than the iPhone speech - raw music -10.5 dBFS against speech -19.1 dBFS in the worst window - so -18 dB of gain buys about 12.5 dB of separation and no mix setting reaches 18.

Turning the gate on today would fail every project whose bed is mastered louder than its dialogue, which is most of them.
Promoting it needs one of: a loudness-relative bed level in `audio_mix`, or a target here that is the planned dB minus the measured source difference.
Either is a decision, not a fix.

### the-bridge-table-that-was-projected-away

Found by the 001 A/B run of 2026-08-26, the run that measured whether #194's context cleanup changed the creative decisions.

`context_fields` is an allow-list, and a pre-bridge's output is merged into the inputs BEFORE it runs.
So a table the bridge computed and the handoff names is deleted unless somebody also thought to add it to the list, and on four of the five hybrid steps nobody had:

| step | table the bridge builds | in `context_fields` |
|---|---|:-:|
| `speech_sequence` | `transcripts_toon`, `topics_toon` | no |
| `select_broll` | `broll_candidates_toon` | **yes** |
| `plan_transitions` | `cuts_toon` | no |
| `plan_vfx` | `vfx_candidates_toon` | no |
| `plan_sfx` | `sfx_candidates_toon` | no |

The symptom is invisible from inside the step.
The bridge logs success, the handoff still says "The `cuts_toon` table provides a summarized list of cut points... Use this value for `cut_point_position` in your response", and there is no `cuts_toon` in the prompt.
Measured on the live run: `plan_transitions` was asked for one entry per cut, keyed by `cut_point_position`, with the table that carries those ids absent - along with `beat_near_cut`, the bridge's own answer to "is a musical beat within 100 ms of this cut", which is the data the step's beat-alignment rule depends on.
It was answerable only because `timed_spine` was separately routed and the beat grid could be recomputed by hand from `music_analysis`.

The fix restores by NAME in `present_llm_step` rather than adding four allow-list entries, and only where the projection dropped the key entirely - so a manifest that names its table may still narrow it with sub-paths.
Four more list entries would have fixed these four steps and left the fifth new one to be found the same way.

`tests/test_llm_context_routing.py` pins it two ways: every table a bridge builds is mentioned by its handoff, and every table reaches the prompt.
Both fail on the parent revision for exactly the four steps above and pass for `select_broll`.

### the-crop-was-narrower-than-the-face

**2026-08-26.** Two consecutive full runs of project 001 - PRs #192 and #201 - independently
named the same defect as the biggest picture problem in the finished video, and neither run
caused it. At **t=44.0 s of `exports/Pipeline_Edit.mp4`**, the emotional floor of the edit, the
frame shows roughly half a face, soft from the upscale, on a source frame that is a clean
landscape close-up with his whole head and a full sunset behind him.

**What the numbers say.** The clip is `IMG_1818.MOV`, stored 1920x1080 with rotation 0. At
t=44.0 s the timeline is 2.257 s into the V1 clip `speech_15_seg0`, which is source
20.646 s. Running the pipeline's own cascade at its own sample size on that source frame:

```
Haar frontal face box   x 285..1008 of 1920   (37.7% of the source width)
conform crop window     x 450..1056 of 1920   (31.6%, fill_zoom 3.1605)
```

The crop's left edge sits **165 px inside** the face box. The `framing_pan_x` of 366.59 px in
the manifest is correct - it aims at the clip's measured subject centre of 0.3926, and the
median face centre over the clip's range measures 0.3926 - so the pan was computed right,
applied right, and could not have helped. **The window was smaller than the thing it was
aiming at.**

**Why nothing caught it.** `compute_face_presence` receives (x, y, w, h) from the cascade and
recorded only `(x + w/2) / sample_w`. The size was measured and thrown away, so no consumer
could ask whether the crop was wide enough. Every gate passed: `measure_frame_occupancy` asks
whether the picture fills the frame and it does, completely.

**The arithmetic is closed.** A width-limited fill keeps exactly `1 / zoom` of the source
width. Holding a 37.7% face box with 15% of its own width clear on each side needs 49.0% of the
width, which is zoom 2.04 - and at zoom 2.04 the source's full height maps to 1241 of 1920
rows, so 35% of the frame is bars. There is no zoom that both fills the frame and holds this
subject, and there was never going to be: the source is already a tight selfie.

So the missing picture is synthesised rather than the subject sacrificed. `_conform_fields`
emits a `framing_backdrop` - the source scaled to 0.6463 and centred on the subject, over the
same frame at scale 1.0, blurred - and `fx.subject_backdrop` draws it in the clip's Fusion comp,
upstream of Resolve's own transform. Frame occupancy stays 100%, the geometry stays constant
across the video, and the face is whole.

**Seen on a frame, twice.** The before and after were both rendered through DaVinci Resolve
21 on a scratch timeline built from this manifest's own values (probe timelines created and
deleted; the captain's project untouched), and reproduced independently with ffmpeg from the
same `_conform_fields` output. The comp imports, `GetFusionCompNameList` reports it, and the
delivered frame shows the whole head, both ears, the cap and the sunset.

**Two things the probe established that are not obvious.**

* A `framing_pan_x` applied *on top of* a backdrop comp slides the whole composition sideways
  and reintroduces the crop. That is why the backdrop route emits no pan at all.
* `TimelineItem.DeleteFusionCompByName` returned **False** for a comp that
  `GetFusionCompNameList` was reporting, and the comp stayed on the clip - the first render of
  the "before" case came out with the backdrop still applied. Judge it by its return value like
  every other Resolve call (§5).

**Why the render-side gate is the weaker half.** The obvious check - Haar on the master, fail a
box touching the frame edge - does not catch this, and the measurement says so. Over 118
sampled frames of 001's shipped master the cascade found **9** subject-sized faces and **none**
of them touched an edge, on a video whose A-roll is cut through the middle of the speaker's
face throughout: a face cropped that hard, softened by a 1.78x upscale and overlaid with
caption cards, stops being detectable at all. `measure_face_intact` is kept as a backstop for
the case where the plan was right and the render was not, and the verdict is carried by
`manifest_validator`'s P8, which asks the same question of the plan where it has an exact
answer and asks it before a 40-minute render rather than after one.

**What this was.** Not a missing default and not a working feature failing to reach these
clips: subject-aware framing reached them and did its job. It was a **missing measurement** -
the subject's size - and therefore a missing capability, because a conform that cannot fit the
subject had nowhere to go.

Related: [the-squashed-face-frame](#the-squashed-face-frame) fixed the sampling that made
`face_center_x` available on this footage at all; [the-letterbox-default](#the-letterbox-default)
is why the frame fills in the first place.
