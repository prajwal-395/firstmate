# Offline timeline compilation through OTIO - measured

**Status: MEASURED, 2026-10-02. OTIO wins on exclusive hold, about 10x, and
loses nothing the reel build cannot put back in a short restore step.**
The compiler is `library/tools/otio_compile.py`. The opt-in builder path
that uses it is a separate change.

This is the single-Resolve plan's "offline timeline compilation" item. The
question: can a base timeline be built once as data and imported in a
single call, instead of hundreds of scripting calls each made while
holding the one Resolve?

## What was built, and how

**The path.** The reel build (`reel_build.build_reel_timeline`) makes the
most per-item scripting calls of the three build paths:

- Step 6.01 makes fewer calls and already round-trips through OTIO for the
  mix (`otio_mix`).
- `composed_edit` edits an existing timeline rather than building one.

**The plan.** Reel 09 as Resolve itself exported it after a real build:

- File: `geo-podcast/pipeline_output/review/Reel_09_-_your-website-is-only-20-percent_rebuild_staging_pre-cleanup-20260924.otio`.
- Rows: 63 items on 8 rows. Video rows: Akshita, Craig, Frame, Subtitles,
  Semantic, Motion Graphics. Audio rows: Akshita CH1 (stereo) and Craig CH1
  (mono).
- Content: two MXF angles with punch-in transforms, 44 tight caption
  overlays, link groups, and per-row program channels.
- Substitutions: 36 of the caption renders it named had since been
  superseded on disk. Each was replaced by an existing 480-tall caption
  render long enough for its span. The call pattern, geometry and
  transforms are unchanged.

**The setup.** Both builds ran in a disposable project, `Ren OTIO
Scratch`, at 1080x1920 and 23.976:

- The captain's project was saved and left first, then reopened on the
  exact timeline found (by unique id, read back). The scratch project was
  then deleted.
- Every section ran as its own process under its own exclusive lease. All
  leases were granted by a serving `ren-resolved`, so each one's hold is a
  broker receipt.
- The two MXFs were in the pool before either build, as a master's footage
  is.

**The two builds:**

- **A - the builder's per-item pattern.** Create the timeline, set its
  custom resolution, add and name the rows. Then, per item:
  - a pool lookup (`pool_item_for`) or an import (`import_pool_item`);
  - `GetClipProperty("FPS")`;
  - a cursor check;
  - a speech-row read before and after each audio append;
  - `AppendToTimeline`;
  - `SetProperty` plus a `GetProperty` read-back for each transform key.

  Finally, `SetClipsLinked` and a read-back per link group. This is a lower
  bound on the real builder: it leaves out `sweep_placed_audio`'s deletes,
  the grade and the punch-in subject probe, and the scratch pool held 2
  items where the captain's holds hundreds.
- **B - one import.** The same plan written offline by
  `otio_compile.compile_timeline` (no Resolve call), then one
  `ImportTimelineFromFile`. A restore pass follows: it verifies the timeline
  settings, every row name and every planned transform against the plan,
  and repairs whatever differs.

B ran first, so it paid the cold-media cost.

## Hold time

| | exclusive hold (broker receipt) | Resolve calls |
|---|---|---|
| A - builder per-item pattern | **13.35 s** | 457 |
| B - import + restore | **1.26 s** (import 0.40 s, restore 0.83 s) | 101, of which 1 writes the timeline |

`ren resolved kpi` for the window: 27 jobs, all `done`, queue wait p50
0.00 s. Of the remaining sections:

- borrowing took 1.9 s;
- giving back took 6.9 s (the project reload);
- each read-back took about 0.4 s.

The restore pass is almost all verification. It made 80 transform reads
and repaired none. It wrote only the three timeline settings below.

## A vs B, field by field

`timeline_serializer` was run on both timelines, plus link membership,
audio row subtype and each audio item's `GetSourceAudioChannelMapping`.
The comparison covered every field of every item: source in/out, record
in/out, row and row name, transform, crop, composite, retime, enabled,
clip colour, markers, Fusion comps, links, channel mapping and node count.

**All 63 planned items are identical on every field.** The differences:

- **A placed two items the plan does not have.** These are channel-2
  copies of Akshita's MXF on Craig's speech row (A2@132, A2@643). They are
  the stereo spill that the builder's `sweep_placed_audio` exists to
  delete after each audio append. B placed none, because OTIO names the
  program channel (`Channels` -> `Source Channel ID`), so there is nothing
  to sweep.
- **B lost the timeline's custom resolution** (`useCustomSettings`, width,
  height). The restore pass set all three back.

## What the format itself drops

To find this independently of any compiler, timeline A was decorated with
Resolve-only state:

- a Fusion comp and a CDL;
- an item marker and a timeline marker, both with `customData`;
- a clip colour;
- a disabled clip, Opacity 50, CompositeMode 3, CropLeft 40, Rotation 5
  and RetimeProcess 3.

A was then exported with `Timeline.Export(EXPORT_OTIO)` and re-imported as
C.

| | survives an OTIO round-trip |
|---|---|
| placement, row, row name, audio subtype | yes |
| Pan/Tilt/Zoom, crop, rotation, composite mode, opacity | yes |
| enabled / disabled | yes |
| retime process | yes |
| link groups, program channel | yes |
| markers (item and timeline): frame, colour, name, note, duration | yes |
| marker `customData` | **dropped** |
| clip colour | **dropped** |
| Fusion comps | **dropped** |
| CDL grade | not read back: `GetCDL` returned `{}` even on A after a `SetCDL` that returned True; `otio_mix` records the grade as lost |
| timeline custom resolution | **dropped** |
| source in/out | **off by one frame** on 32 of 65 items through Resolve's OWN export: all 8 `LCATL0013.MXF` items and 24 renders. Record in/out are unchanged; source out is one frame early on 30 items and source in on 2. The compiled file writes integer frames and had no drift |

**Why these losses are restorable for the reel build:**

- Comps are already applied after placement, in their own process
  (`reel_look.apply_comps`).
- The grade is already applied after placement (`reel_look.apply_grade`).
- The build writes no clip colour and no marker during placement.

So only the timeline resolution belongs to the importer, and the B restore
pass shows it costs three `SetSetting` calls. One finding reaches beyond this path. Re-importing Resolve's own export
moved source frames by one where the compiled file moved none. Step
6.01's `deliver_audio_mix` re-imports exactly such an export, so it is
exposed to the same drift. That path itself was not measured here.

## The laws the compiler rests on

These are measured, and recorded in `otio_compile`'s docstring:

1. **Source frames count from the media's start timecode at the nominal
   rate.** `00:08:32:08` is frame 12296. API `startFrame` 34305 is OTIO
   46601.
2. **Pan is a fraction of the frame width, Tilt a fraction of the frame
   height, and Zoom is unchanged.** Pan -35 is -0.032407 and Tilt -1836 is
   -0.95625 at 1080x1920. This was measured with project and timeline at
   one resolution only, so an importer verifies every transform after the
   import rather than trusting the law.
3. **Program channel 1 is `Source Channel ID` 0.** It is the only channel
   any reel uses, and the only one measured.
4. **A missing file makes the whole import return None, silently.** The
   compiler refuses first and names the file.

## What the import does to the pool

`importSourceClips: True` reused the two pooled MXFs; it did not duplicate
them. It filed the 35 overlay renders, plus the imported timeline's own
pool entry, into the ROOT bin. The builder's `import_pool_item` files each
render into its kind's bin (`overlay_import_bin`), so the opt-in path's
restore step must move the renders there.
