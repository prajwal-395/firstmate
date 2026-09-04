# Field test: entering the pipeline from an existing rough cut

> Phase one is sections 1-11. **Phase two - what was built after the
> captain's answers - is section 12.**

Phase-one findings for the GEO Podcast field test. Everything below was
MEASURED against the live Resolve instance, the Resolve disk database and
the project on disk on 2026-09-04.

**Nothing was written to Resolve.** All three timelines in the field-test
project were fingerprinted through the API before and after this work and
are identical; see section 10.

The captain's framing: the entry point is not the top of the pipeline.
The rough cut already exists, cut by hand, and the engine has to start
from it. The deliverable is **pipeline capability that generalises**, not
a tidied-up per-episode script - a working per-episode script is what
already exists and is explicitly not what is being asked for.

## 1. Which project is which - settled by evidence, not by name

Three podcast projects exist, and two of their names are close enough to
pick wrong. The captain's instruction was to confirm from Resolve's own
list and stop if ambiguous. Resolve's list is authoritative for
addressing, and the disk databases settle the roles.

Resolve, live: one database `poetic` (Disk), current folder
`Lucie Content/Social Media/Podcast`, containing exactly:

```
GetProjectListInCurrentFolder() -> ['Podcast', 'Podcast (field test)']
GetFolderListInCurrentFolder()  -> []
current project                 -> Podcast (field test)
```

The third lives in a sibling folder, found by enumerating the database on
disk rather than by navigating the captain's project manager. Each
`Project.db` was **copied before being read, never opened in place**
(AGENTS.md section 5):

| Resolve name | internal `SM_Project.ProjectName` | timelines | role |
|---|---|---|---|
| **`Podcast (field test)`** | **`Podcast (Copy)`** | `GEO Podcast - Synced`, `..._archived_v01`, `GEO Podcast - Roughcut Backup` | **MINE** - the expendable backup copy |
| `Podcast` | `Podcast` | the same three, **identical modification timestamps** | **THE ORIGINAL - DO NOT TOUCH** |
| `Lucie Podcast test 001` | `Lucie Podcast test 001` | `GEO Podcast - Roughcut Import`, `..._archived_v01`, `GEO Podcast - Rebuilt v3` | the older version the four scripts came from |

The evidence, rather than the inference:

- The field-test project's **internal name is literally `Podcast (Copy)`**,
  and its three timelines carry modification timestamps identical to
  `Podcast`'s (`2026-06-19 18:38:41`, `2026-08-07 20:01:07`,
  `2026-08-07 20:01:08`). It is a faithful copy of `Podcast`, which is
  exactly what the captain described.
- `Lucie Podcast test 001` has a **different timeline set** including
  `GEO Podcast - Rebuilt v3`, modified `2026-08-07 20:07:18` to
  `20:33:28`. The four one-off scripts are dated `2026-08-07 17:14`
  through `2026-08-08 00:05`, bracketing those edits. "Rebuilt v3" is
  what `place_subtitles.py` produces. That correlation is what identifies
  it, not its name.

### The naming trap, and it is a real one

**The field-test project's internal name is `Podcast (Copy)`, not
`Podcast (field test)`.** So:

- Anything that identifies the project by its internal name will not find
  `Podcast (field test)`.
- Anything that does a prefix, substring or `startswith` match on
  `"Podcast"` matches **both** projects, and `Podcast` is the untouchable
  original.

Any write path must match the **exact string** `Podcast (field test)` as
returned by `GetProjectListInCurrentFolder()`, and must refuse rather
than guess if that exact string is absent. This belongs in `project.yaml`
and in the write path itself.

Related mismatch: `project.yaml` declares `resolve.folder: Lucie Content`,
but the project sits at `Lucie Content/Social Media/Podcast` - a nested
path the single-level declaration cannot express.

## 2. What the live timeline actually contains

Resolve Studio 21.0.0b.28. Open timeline: **`GEO Podcast - Synced`**.
Note `project.yaml` declares `resolve.timeline_name: Main Edit`, which
**does not exist in any of the three projects**.

- 63694 frames @ 23.976 fps = **2656.6 s = 44.3 minutes**.
- Timeline resolution **1080x1920** (vertical).
- Tracks: `V1 Akshita` / `V2 Craig`, `A1 Akshita CH1` / `A2 Craig CH1`.
  Video and audio item counts match per speaker (86 / 81), so picture and
  sound are linked per clip.

| | V1 Akshita | V2 Craig |
|---|---|---|
| items | 86 | 81 |
| covered | 1791.3 s (67.4%) | 884.2 s (33.3%) |
| gaps | 61, 865.2 s | 61, 1772.4 s |
| shortest / longest item | 0.50 s / 67.6 s | 0.75 s / 32.2 s |

- Cross-talk (both tracks showing picture): **47.5 s, 1.8%**. A genuine
  back-and-forth, not a stacked two-shot.
- Union coverage **98.9%**. **Two uncovered holes totalling 28.6 s** -
  frames 59220-59806 and 60512-60611 - play as black. Flagged, not
  touched; question Q8.

### The speaker signal is the TRACK, and that beats diarization

Each speaker has their own picture and sound track, and the two cameras
wrote disjoint filename prefixes:

| track | source files | items |
|---|---|---|
| V1 Akshita | `LC4930/4931/4932.MXF` | 2 / 5 / 79 |
| V2 Craig | `LCATL0011/0012/0013/0014.MXF` | 2 / 7 / 62 / 10 |

Speaker identity is therefore a **fact of the edit**, not something to
infer. The engine's WhisperX use (step 1.04) has no diarization and no
speaker concept, and does not need one here: transcribing a soloed track
yields a per-speaker transcript with no pyannote and no attribution
error. This is the most valuable fact the one-off scripts discovered.

### The letterbox is a project setting, not a per-clip transform

`TimelineItem.GetProperty()` with no argument on a V1 item returns
`ZoomX: 1.0, ZoomY: 1.0, Pan: 0.0, Tilt: 0.0`, all crops zero. There is
no per-clip framing. The letterbox comes from
`project.GetSetting('timelineInputResMismatchBehavior')` -> **`scaleToFit`**,
with 3840x2160 source in a 1080x1920 timeline. It is intentional and is
left alone.

**Consequence for per-reel timelines:** the PROJECT's own resolution
setting is **3840x2160**; only the timelines override it to 1080x1920. A
timeline created through the API inherits the project default, so a new
reel timeline comes out horizontal UHD unless explicitly set to
1080x1920. That is a silent wrong answer, not an error.

## 3. Does the API expose clip-to-source ground truth? YES, completely

The highest-risk unknown. Answered, and the answer removes any need for
audio matching.

Every `TimelineItem` returns, verified by calling rather than by
`hasattr` (AGENTS.md section 5):

```
GetName()             -> 'LC4930.MXF'
GetStart()            -> 594          GetEnd()             -> 1080
GetDuration()         -> 486          GetLeftOffset()      -> 3151
GetSourceStartFrame() -> 3151         GetSourceEndFrame()  -> 3637
GetSourceStartTime()  -> 131.42295833 GetSourceEndTime()   -> 151.69320833
GetUniqueId()         -> '42fbfd06-32c0-4057-ba7e-3c4397ead495'
GetMediaPoolItem()    -> MediaPoolItem
```

and `GetMediaPoolItem().GetClipProperty('File Path')` gives the absolute
source path. All 7 source files resolve and exist on disk.

That is everything the recursive ground-truth tagging needs:

- **which raw file** - `File Path`
- **which range inside it** - `GetSourceStartTime/EndTime`
- **where it lands** - `GetStart/GetEnd`
- **relative order** - timeline order is conversation order, and source
  order within a file is recoverable independently, so "one bit of
  dialogue comes after another" is answerable on either axis
- **re-indexing one portion later** - `GetUniqueId()` is a stable
  per-item handle, so a clip is addressable by name rather than position

**One discrepancy to settle.** `GetLeftOffset()` and
`GetSourceStartFrame()` disagree by exactly 1 frame on roughly a third of
items (1960 vs 1959; 5361 vs 5360; 117 vs 116) and agree on the rest.
One must be chosen and written down, or extracted audio drifts a frame
against the transcript on a third of the clips. `GetSourceStartTime()` is
consistent with `GetSourceStartFrame()`, so the recommendation is the
`SourceStart/EndTime` pair and never `LeftOffset` - as a recorded rule,
not an accident.

## 4. The prerequisite machinery, and why it is most of the way there

`library/tools/external_inputs.py` exists and was built for **exactly**
this captain request (#260, 2026-08-28), quoting the captain:

> "you should not be able to add transitions or effects when there exists
> no roughcut either already on the timeline manually or automated by the
> LLM"

The design is right and should not be reworked: a prerequisite is
satisfied from outside by **supplying the value** in
`<project>/external/<state_key>.json`, **verified** by a check registered
for that key, and the same verified value is what `gather_step_inputs`
hands the step. Nothing is asserted on faith.

`CHECKS` accepts four keys: `a_roll_assignments`, `audio_spine`,
`assembly_manifest`, `render_output`.

### `a_roll_assignments` already matches the timeline field-for-field

`_check_a_roll_assignments` requires per entry: `source_file` (absolute,
must exist), `video_in`, `video_out`, `timeline_start`, `timeline_end`,
with non-empty ranges. That is exactly what section 3 reads off each
timeline item. The optional `clip_id` cross-check against catalog
durations degrades honestly with no catalog on file, reporting "the
catalog is not on file so no duration could be cross-checked".

**So the timeline -> `a_roll_assignments` route needs no change to the
contract at all - only a producer that reads a timeline.** No
cataloguing, no semantic analysis, no prosody, no Gemma.

### One recorded reason is now measurably wrong

`WITHDRAWN['a Resolve timeline built by hand']` says the timeline cannot
be a prerequisite because:

> "reading it means copying that database and opening it as SQLite
> (AGENTS.md section 5), and nothing maps its clips back onto a typed
> pipeline key"

Both halves hold for a **closed** project read off disk - which is what
AGENTS.md section 5's "Reading a killed build off disk" is about, and is
what section 1 of this document had to do. Neither holds for a **live**
one: the scripting API maps every clip onto an absolute source path and a
source frame range with no database copy at all.

The module conflated live and closed. The entry should be **narrowed to
the closed case, not deleted** - the live route still supplies a checked
artifact, so the contract is unchanged.

### The gap that actually blocks

`plan_subtitles` (step 4.01) declares three **required** inputs:

| input | externally suppliable? |
|---|---|
| `audio_spine` | yes - in `CHECKS` |
| `speech_sequence` | **no** - explicitly `WITHDRAWN` |
| `rough_cut_review` | **no** - not in `CHECKS` |

`speech_sequence` is withdrawn because "no check exists that could tell a
real creative decision from a plausible-looking one". Sound reasoning -
but in this project the speech sequence is not a creative decision at
all. The captain already made it, by hand, and it **is** the timeline.
That case the withdrawal did not anticipate. Question Q9.

## 5. What the four one-off scripts DISCOVERED

Read for the facts they establish, not as an implementation to promote.
A per-episode script that works already exists; the deliverable is
pipeline capability. What follows is which of their facts survive
generalisation, and which of their behaviours must not be carried across.

### Facts worth carrying into the pipeline

- **A1 = Akshita, A2 = Craig.** Verified against the live timeline, whose
  tracks are named `Akshita CH1` and `Craig CH1`. Confirmed correct.
- **Soloing a track yields a per-speaker transcript.** The key insight,
  and the reason no diarization is needed. This is a general capability:
  *speaker identity can come from track topology*, which holds for any
  multi-mic conversation, not just this episode.
- **Restoring track-enable state after soloing** is required, and
  `_restore_tracks` shows the shape - though its implementation is wrong
  (below).
- **Per-segment Remotion renders with word timings rebased to frame 0**,
  rather than one full-length overlay. Matches how the engine's own
  subtitle path already works, so it generalises cleanly.
- **Placement via `AppendToTimeline` with `recordFrame`**, consistent
  with `resolve_build_timeline.py`.
- **Untimed WhisperX words must be interpolated, not dropped.**
  `place_subtitles.parse_transcript` interpolates from neighbours;
  `generate_podcast_subtitles.py` silently drops any word lacking
  `start`/`end`. The two scripts disagree and the later one is right.
  This is a real WhisperX behaviour the pipeline has to handle.
- **Per-speaker styling works and is legible**: Montserrat 58px, 4px
  black outline, accent `#FFB8D4` (Akshita) / `#FBF0B8` (Craig).
  Montserrat is the one bundled typeface (AGENTS.md section 11), so this
  already satisfies `render_fonts`. The *values* are the captain's taste
  and belong to the project; the *axis* - style varies by speaker - is
  the pipeline capability that does not exist today.

### What is episode-hardcoded, and what generalising it means

- `FPS = 23.976`, `WIDTH/HEIGHT = 1080/1920` - must be read from
  `timeline.GetSetting(...)`, not declared.
- `DURATION_S = 53.08` / `SCOPE_S = 53.08` / `END_FRAME = 1272`. The
  timeline is **63694 frames / 2656.6 s**. These scripts only ever
  processed **the first 2% of the episode**. Any claim they "work" is a
  claim about 53 seconds, and nothing in them has met a 44-minute
  timeline, 167 clips, or 7 source files.
- The module-level `SPEAKERS` dict - names, transcript filenames, accent
  colours, track indices - is a per-project declaration living in code.
  Per AGENTS.md section 14 those are per-series parameters and belong in
  `project.yaml`; the engine stays series-neutral.
- Output paths computed from `__file__`, bypassing
  `library/tools/project_layout.py` entirely.
- `REMOTION_DIR` found by walking three parent directories up.

### Behaviours that must NOT be carried across

1. **`place_subtitles.py` writes to the captain's master timeline.** It
   calls `AddTrack("video")` until there are 4, then
   `timeline.DeleteClips(existing)` on V3/V4 and `SetTrackName(...)`, all
   on `GetCurrentTimeline()`, with no duplication and no confirmation.
   Against the current 2-track master that is a destructive write to real
   work. It must not be run as-is.
2. **It deletes media pool clips by name pattern.** `delete_old_segs`
   walks the whole pool recursively and `DeleteClips` anything matching
   `seg_*.mov` - an unscoped destructive sweep of a shared pool.
3. **`export_audio.py` never sets `AudioCodec`.** AGENTS.md section 5:
   renders are silent unless `SetRenderSettings` sets `ExportAudio` *and*
   `AudioCodec`. It sets only the former; it happened to work.
4. **Bare `dvr.scriptapp`** in `export_audio.py` and `place_subtitles.py`
   instead of `resolve_locale.scriptapp_preserving_locale`
   (AGENTS.md section 9). Every later UTF-8 read in those processes is on
   borrowed time.
5. **`_restore_tracks` hardcodes audio tracks 1 and 2 and re-enables them
   unconditionally** - on a timeline where the captain had deliberately
   muted something, it would silently un-mute it.
6. **Bare `except:`** in the render-status poll swallows everything,
   `KeyboardInterrupt` included.
7. **`subprocess.run(..., text=True)` without `encoding="utf-8"`** in
   `render_subtitle_segments.py` - AGENTS.md section 9.
   `place_subtitles.py` gets this right.
8. `generate_podcast_subtitles.py` is superseded by `place_subtitles.py`
   on every point where they overlap, and drops untimed words. Nothing
   should be carried from it that is not also in the later script.

## 6. The three project.yaml mismatches, re-verified

All three confirmed, and each is worse than stated.

**1. Project and timeline names.** Declared `resolve.project_name: GEO Podcast`,
`resolve.timeline_name: Main Edit`, `resolve.folder: Lucie Content`.
Actual project is `Podcast (field test)` (internal `Podcast (Copy)`), in
folder `Lucie Content/Social Media/Podcast`; actual timelines are
`GEO Podcast - Synced`, `..._archived_v01`, `GEO Podcast - Roughcut Backup`.
**None of the three declared values is correct**, and the schema cannot
express the nested folder. Questions Q1, Q2.

**2. Source declaration.** Declared `type: iphone_mov, resolution:
1080x1920, fps: 30`. Measured with the pipeline's own `extract_metadata`
over all seven source files:

```
file                 dur_s         WxH      fps
LC4930.MXF          225.22   3840x2160   23.976
LC4931.MXF          287.62   3840x2160   23.976
LC4932.MXF         4941.10   3840x2160   23.976
LCATL0011.MXF       214.55   3840x2160   23.976
LCATL0012.MXF       288.95   3840x2160   23.976
LCATL0013.MXF      4099.60   3840x2160   23.976
LCATL0014.MXF       838.00   3840x2160   23.976

frame-rate spread: {23.976: 7}
resolution spread: {'3840x2160': 7}
```

**All three declared fields are wrong.** The footage is uniform, which is
the good news - no mixed-rate or mixed-resolution problem, just a stale
declaration. The 1080x1920 in the yaml is the DELIVERY frame, not the
source; AGENTS.md section 10.1 is explicit that delivery format is a
property of the product, not of the footage. Question Q6.

**3. Brand template bounds.** `library/templates/cinematic_narrative.yaml`
lines 60-62:

```yaml
  target_duration_seconds:
    min: 180
    max: 600
```

3 to 10 minutes, on a project whose entire purpose is short-form reels.
Flagged only; changing a brand template is a captain decision. Question Q7.

## 7. The readiness check refuses, and the refusal is the finding

```
$ .venv/bin/python3 manage_project.py check \
    /Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast
Refusal: No footage files found in project
Checking project: GEO Podcast (/Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast)
Checking brand template...
Checking creative_brief...
Checking environment paths...
Checking footage files with ffprobe...
```

`cmd_check` calls `footage_identity.enumerate_footage(project_dir)`,
which looks for media **inside the project directory**. `raw/` is empty.
The footage lives at
`/Users/prajwal/Documents/work_stuff/Lucie consulting/Social Media/podcast media/`,
entirely outside the project.

This is the field test's subject in one line: **the engine assumes it owns
the footage, and here Resolve owns it.** The check is not wrong to
refuse - it reports a true fact about a project laid out the way the
engine expects. What is missing is any way for a project to say "my
footage is over there, and Resolve is the index of it". Question Q10.

The frame-rate and resolution spread the check would have reported is in
section 6, measured directly with the same `extract_metadata` the check
uses.

## 8. Where the pipeline assumes it owns the whole edit

Recorded as the captain asked, beyond the four build items.

1. **`enumerate_footage` scans the project directory only.** No project
   can declare an external footage root. (section 7)
2. **`WITHDRAWN` treats a hand-built timeline as unreadable**, on
   reasoning that holds only for a closed project. (section 4)
3. **`speech_sequence` cannot be supplied** even when a human already
   made that exact decision by hand. The withdrawal reasons about taste;
   this case is not taste. (section 4)
4. **No step reads a timeline as an input.** Every Resolve tool in
   `library/tools/execution/` and step 6.01 WRITES a timeline from a
   manifest. `timeline_serializer` reads one, but for QA comparison
   against a manifest the pipeline itself produced.
5. **No speaker axis anywhere.** `subtitle_style.py` has no speaker
   concept; `plan_subtitles` mentions "speaker" only in a comment about
   character counts. Per-speaker styling has nowhere to live.
6. **`project.yaml` cannot express this project's own shape**: one
   `timeline_name` with no notion of a master plus derived reels, a
   single-level `folder` that cannot hold a nested path, and no way to
   record which Resolve project is safe to write to.
7. **One delivery format per project is assumed.** A 44-minute master and
   a set of 30-second reels are different products from one project, and
   `delivery_format.py` is not shaped for that.

## 9. Proposed approach - pipeline capability, generalised

Smallest path that is contract-honest and general. Each item names the
capability, not the episode. Nothing is built yet.

**1. Read a timeline as pipeline input.**
A new engine module - working name `library/tools/timeline_ingest.py` -
that reads any live Resolve timeline through
`resolve_locale.scriptapp_preserving_locale` and emits
`<project>/external/a_roll_assignments.json` in the shape
`_check_a_roll_assignments` already demands: `source_file`,
`video_in`/`video_out` from `GetSourceStartTime/EndTime`,
`timeline_start`/`timeline_end` from `GetStart/GetEnd`, plus
`resolve_item_id` from `GetUniqueId()` and the owning track. Read-only
against Resolve; it writes a file and nothing else.
Then narrow the `WITHDRAWN` entry to the closed-project case.
**No new trust:** the emitted artifact still goes through `verify()`, so
the contract the captain asked to keep strong is unchanged.
This generalises to any hand-built timeline, not this episode.

**2. Ground-truth positional mapping** is the same record, so it falls
out of the same module. Re-indexing one portion is addressed by
`resolve_item_id`. Settle `SourceStartTime` vs `LeftOffset` and write it
down as a rule.

**3. A speaker axis in the subtitle path.**
The general capability is *subtitle style varies by a speaker attribute
carried on the spine*, with the speaker derived from track topology where
the edit provides it. That means: a speaker field through
`plan_subtitles` and `subtitle_style.py`, per-speaker style values
declared in `project.yaml` (never in engine code, AGENTS.md section 14),
fps and frame size read from the timeline, and untimed-word interpolation
handled in the engine's WhisperX path. The one-off scripts contribute the
facts in section 5, not their structure.

**4. Master plus numbered reel timelines.**
`CreateEmptyTimeline` with resolution set **explicitly** to 1080x1920 -
the project default is 3840x2160 and would silently win. Every write
duplicates first and matches the exact project name per section 1.
Naming convention is Q3.

Sequencing: 1 and 2 together first - one module, and one of them answers
the highest-risk unknown; then 3; then 4.

## 10. The captain's timelines are untouched

All three timelines in `Podcast (field test)` were fingerprinted through
the API - every item's name, start, end, source start/end, unique id and
source file path, hashed per timeline - before and after this
investigation:

```
$ diff fingerprint_before.json fingerprint_after.json
IDENTICAL - no change to any timeline
```

| timeline | sha256 | items hashed |
|---|---|---|
| `GEO Podcast - Synced` | `a1ab9be8e17edc50...` | 338 lines |
| `GEO Podcast - Synced_archived_v01` | `de6cf21284d17682...` | 338 lines |
| `GEO Podcast - Roughcut Backup` | `e12b6cd45e1caf00...` | 338 lines |

`Podcast` and `Lucie Podcast test 001` were never opened. Their
`Project.db` files were **copied and the copies read**; the originals were
not opened in place, and their on-disk modification times
(`12:15:08` and `12:18:27`) predate this work.

Every Resolve call made was a getter. No `Set*`, no `Add*`, no
`Append*`, no `Delete*`, no `OpenPage`, no project or timeline switch.

## 11. Open questions

Every one changes the build. None is assumed.

- **Q1 - Confirm the write target.** Evidence says `Podcast (field test)`
  (internal `Podcast (Copy)`) is the expendable copy and `Podcast` is the
  untouchable original. Confirming because the names differ only by a
  parenthetical and the internal name matches neither.
- **Q2 - Which timeline is the master?** Three exist in the field-test
  project; `Main Edit` is not one of them. `GEO Podcast - Synced` is open.
  Is that the master, and are the other two the captain's own backups?
- **Q3 - Reel timeline naming and numbering?** No convention exists
  anywhere in the repo or the projects. Asking rather than inventing one.
- **Q4 - How many reels, and who picks the moments?** Captain selects, or
  pipeline proposes and captain approves? This changes the design.
- **Q5 - Duplicate takes: detect, or just support removal?** Materially
  different amounts of work.
- **Q6 - Fix the `source:` block** to `mxf` / `3840x2160` / `23.976`, and
  extend `resolve:` to carry the nested folder and the exact project
  name? Recommended, but it is the captain's declaration.
- **Q7 - Brand template bounds** are 180-600 s on a short-form project.
  Flagged per scope - does the captain want a short-form template?
- **Q8 - The 28.6 s of black** at frames 59220-59806 and 60512-60611 -
  intentional, or leftover? Untouched either way.
- **Q9 - `speech_sequence` and `rough_cut_review` are required by
  `plan_subtitles` and are not externally suppliable.** Options:
  (a) add checks for them, (b) derive `speech_sequence` from the timeline
  as a measurement rather than a judgement, (c) run the subtitle path
  under its own declared profile outside that dependency.
  **(b) is the recommendation** - the captain already made this decision
  by hand, so it is a fact to be read, not taste to be invented - but it
  edits a deliberate withdrawal and needs a ruling.
- **Q10 - Footage lives outside the project**, so `check` refuses.
  Symlink into `raw/`, or teach a project to declare an external footage
  root? The latter is the general fix; the former is one command.


## 12. Phase two - what was built

Captain's answers arrived 2026-09-04. This section records what was built
against them and what was deliberately left.

### The rough cut is now a checked prerequisite

`library/tools/timeline_ingest.py` reads a live timeline and emits
`a_roll_assignments` and `speech_sequence` into `<project>/external/`.
It never writes to Resolve - every call is a getter.

The withdrawal was NARROWED, not deleted, using the live-versus-closed
argument from section 4 as the recorded reason. A closed project still
cannot be asserted; a live one is a producer whose output is verified
like anything else. `speech_sequence` left the taste withdrawal on the
captain's ruling that a sequence cut by hand is a fact to be read - and
`_check_speech_sequence` refuses a merely well-shaped one by requiring a
self-consistent doubly-linked chain over stable Resolve item ids that
agrees with files on disk. A model emits positions; it does not emit
that.

**`rough_cut_review` was NOT satisfied**, and honestly could not be.
`plan_subtitles` requires it, and it is a JUDGEMENT - whether the cut
passes review - that nobody has made for this project. Supplying it
would be inventing a review, which is exactly what section 10.5 forbids.
The narrowest alternative, not built and offered rather than assumed: run
the subtitle path under a declared run profile that does not include
`review_rough_cut`, so the gate is DECLINED rather than faked. That is a
`library/profiles/` entry and a captain decision about whether this
project wants the gate at all.

### A clock bug the test fakes surfaced

Resolve REPORTS `timelineFrameRate` as `23.976` and COMPUTES source times
at the exact `24000/1001`. A timeline position derived from the reported
rate therefore sits on a different clock from the source times read off
the same item. `exact_frame_rate` maps the NTSC family; both rates are
recorded on the snapshot so the difference is never mistaken for a bug.
Small - 2.7ms over the full timeline - and exactly the kind of small that
survives review and then does not line up.

### Footage may live outside the project

`source.footage_root`, absolute and checked. A relative or missing root
is REFUSED rather than falling back to `raw`, because falling back turns
a typo into "this project has no footage" - the exact unhelpful refusal
this closes. Nothing was symlinked into the captain's media store.

`.mxf` joined `SUPPORTED_VIDEO_EXTENSIONS` on measurement: without it the
check reported a project of seven Sony XAVC files as having no footage.
`SourceConfig.fps` became a float, having been an int that truncated
23.976 to 23.

### The subtitle naming defect was real, and is fixed

Verified in the pipeline's own path, not only in the one-off scripts:
step 4.05 named every overlay `sub_block_<block_position>` - an ordinal
within one spine - into `Area.SUBTITLE_SEGMENTS`, which is per PROJECT.
A reel's `body_1` would have overwritten the master's, and no part of the
name said whose speech it captioned.

`library/tools/subtitle_segment_id.py` binds speaker + timeline + source
audio span, with a digest over the whole binding. Not a bigger number:
a name that is unique but means nothing is how a wrong-but-plausible
pairing stays invisible. Step 4.05 now also reads the `block_info` it was
already looking up and discarding.

The one-off scripts had hit this once already and half-fixed it -
`place_subtitles.py` carries the comment "now with unique speaker-prefixed
names" - which is why the captain remembered it.

### The two duplicate timelines were proven, then deleted

Condition met before anything was removed. Both compared identical to
`GEO Podcast - Synced` on all 334 clip records - track kind, track index,
track name, source file, source in, source out, timeline start, timeline
end - plus frame rate, resolution, frame extent and track set.

The comparison deliberately EXCLUDES `resolve_item_id`: duplicating a
timeline assigns fresh ids, so reading them would report every copy as
different and make the comparison useless for the one question it exists
to answer.

The proof was re-run immediately before each deletion, behind three
guards: the project matched exactly, the target was not the master, and
the target still compared identical. `DeleteTimelines` returned True for
both.

Afterwards: the master's fingerprint is byte-identical to the one taken
before any of this work (`a1ab9be8e17edc50...`), and the untouchable
`Podcast` still holds all three timelines with its `Project.db` mtime
unchanged - so the deleted timelines survive there.

### Q7 - the long-form bound DOES gate the reel path

Measured, not inferred:

```
brand template only                 -> (180.0, 390.0, 600.0)
a 30s reel spine                    -> below the 180s minimum: FAILS
project declares 30                 -> (27.0, 30.0, 33.0)
```

`duration_targets.get_target_duration_zone` precedence 1 is the PROJECT's
own `target_duration_seconds`, which beats the template. So the narrowest
fix needs no template change: the reel project declares its own target at
the top level of `project.yaml`, and `project_declared_config` carries it
to the gate.

Not applied - the reel length is not decided, and Q4 puts moment
selection with the captain. Offered as the concrete option.

### Not built, and why

- **Per-speaker subtitle styling end to end.** The binding now carries
  speaker, and `timeline_ingest` measures it off the track. What is still
  missing is a speaker axis in `subtitle_style.py` and a per-speaker style
  declaration in `project.yaml`. Scoped out of this change to keep the
  prerequisite work reviewable; it is the natural next piece.
- **Reel proposal and per-reel timelines.** Q4 requires the pipeline to
  propose moments and the captain to approve BEFORE any timeline is built.
  Building the timeline half first would invite exactly the unapproved
  build that answer forbids.
- **Duplicate-take detection** (Q5): support only, per the decision. It
  did not fall out of this work for free.
