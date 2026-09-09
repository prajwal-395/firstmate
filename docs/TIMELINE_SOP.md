# Timeline SOP: how a timeline is laid out

A standard a human editor could hand to another human editor, and the
standard the engine obeys. If the engine builds a timeline that breaks
this document, the engine is wrong; if an editor breaks it by hand,
the conformance verifier (`library/tools/timeline_conformance.py`) says
so row by row.

## The shape of this standard

Two things are fixed and everything else is derived. The fixed things
are the row ORDER and the naming rules. The derived things are the row
COUNTS and the row OCCUPANTS, which come from the material - which
angles exist, which speakers, what the plan asks for - never from a
constant. Where a value would be taste (which music, how many layers a
piece wants, what a caption looks like), the project declares it and
the engine refuses without it.

One module owns the layout: `library/tools/timeline_layout.py`. It
takes the material and returns the track plan - for each track its
index, its media type, its role, its name, and what will occupy it.
Nothing else in the codebase may decide a track index or a track name.
(The reel builder still has its own partial layout; conforming it is a
filed follow-up, and the layout module is shaped so it can adopt it.)

## Row order (fixed)

Picture rows first, top to bottom, then sound rows:

1. A-roll, one row per camera angle, in first-seen order.
2. B-roll, one row.
3. Subtitles (the caption row), one row.
4. Motion graphics, one row per overlapping layer.
5. Generator effects, one row.
6. Timed text, one row per overlapping layer.
7. Speech, one row per angle, carrying one program stream each.
8. Music, one row, more only while a crossfade overlaps itself.
9. SFX, one row per overlapping layer.

## Why each row exists (derived, never constant)

- **A-roll per angle.** Two speakers shot on two cameras are two angles,
  and each angle is its own row. Collapsing both onto one video row was
  defect 1, and it is structurally impossible now: the plan mints one
  a-roll row per materialised angle, and a manifest that declares no
  angles builds exactly the old single-camera pair.
- **Speech per angle, one program stream each.** Two cameras mean two
  audio rows, not eight. An MXF carries four audio streams per clip and
  only the program mix supplies the audio - which stream that is comes
  from the catalog (see below), and the row is named for the angle plus
  the stream, the way the captain's own edit reads "Akshita CH1".
- **B-roll.** One row, and only when the plan asks for cutaways. B-roll
  is video-only by design and carries no link.
- **Motion-graphics rows.** One row or many depending on how layered
  the picture is - decided mechanically by overlap, the same packing
  that stacks SFX. Overlap is a fact about the plan, and the smallest
  row count that holds it is forced; nobody declares it and nobody
  tunes it.
- **The caption row and where it sits.** Captions sit directly above
  the picture they annotate and below generative picture (motion
  graphics, generators, timed text), so an editor reading top-down
  meets the picture, then what the picture says, then decoration. The
  caption row carries rendered caption segments, one per speech block.
  This placement is settled (captain's ruling 2026-09-09): the caption
  row stays where the order above puts it, and if the order is ever
  revisited it changes here and only here.
- **Music.** One row for the bed; a crossfade puts two clips on the
  timeline at once, so the bed overlaps itself and takes a second lane
  for exactly that span. Same packing, same rule.
- **SFX rows.** One row or many depending on how layered the sound is -
  again the overlap packing, never a requested count.

## What a row is named, and from what

Every row is named, from the material or from the role vocabulary,
never invented at build time:

- An a-roll row carries its angle's label ("Akshita", "Craig").
- A speech row carries its angle plus its program stream ("Akshita
  CH1"). The stream half comes from the catalog's recorded selection.
- Singleton roles carry the standard name: "B-Roll", "Subtitles",
  "Motion Graphics" (then "Motion Graphics 2", ...), "Generator
  Effects", "Timed Text", "Music", "SFX" (then "SFX 2", ...).
- A row the plan did not name is a build error, not a fallback. There
  is no "V3" or "Audio 2" anywhere downstream of the plan.

## What may never be on a timeline

- **An empty row.** A row exists because something goes on it. When
  every placement for a row fails, the row is deleted and the deletion
  is on the record - not kept blank. (Measured: the engine's own reel
  timelines in the wild carry empty "Video 4" / "Video 5" rows; the
  verifier flags them.)
- **An unnamed row.** Resolve's own defaults ("Video 1", "Audio 1")
  mean nobody organised the row. The build names every row from the
  plan; the verifier flags any default that survives.
- **A second stream from one source.** Exactly one recorded program
  stream per source reaches the timeline. The catalog records EVERY
  audio stream with whatever ffprobe gives to tell them apart (index,
  channel layout, channel count, language, title, handler,
  disposition); the project declares which one is the mix; without a
  declaration the catalog refuses - naming the source and everything
  ffprobe saw - rather than defaulting to stream 0. At placement every
  audio item is read back and anything that is not the recorded program
  is deleted on the spot, on the record.
- **An unlinked a-roll pair.** A-roll picture and its speech travel
  together, linked. A caption whose span falls inside a speech span
  joins that link group - picture, speech and caption in ONE link call,
  because linking is exclusive, not additive: measured 2026-09-09, a
  three-item `SetClipsLinked` forms a true three-group, while linking a
  pair afterwards BREAKS the group instead of joining it. Every link
  call is read back, not trusted.
- **Two rows doing one row's job.** Two rows carrying one singleton
  role's name is a duplicate, and the verifier reports it.

## What "lean and clean" means for the surrounding project

- **The pool.** Media that is already pooled is not imported again.
  Measured 2026-09-09: `ImportMedia` never dedupes - re-importing one
  pooled MXF grew the pool 221 to 222 - so the builder skips paths the
  pool already holds (exact-path match only) and says what it skipped.
  Pool subfolders per role ("V1", "Subtitles", ...) keep the pool
  browsable; smart bins are not scriptable, so there are none to keep.
- **The bins.** Same rule as the pool: file from measurements, nothing
  deleted by the build except what the build itself created.
- **Stale timelines.** The build never deletes a timeline it did not
  create in its own run - a name collision is a refusal, not an
  overwrite - and it never leaves one behind either: probe and scratch
  timelines are deleted by the run that made them, and empty rows are
  deleted, not kept. A project with no empty rows, no default-named
  rows, no duplicate pool items and no timelines nobody can account for
  is what "lean and clean and organized and not cluttered and full of
  stale shit" cashes out to, and the verifier checks the timeline half
  of it on every build.

## Measurements this standard stands on

- The captain's "GEO Podcast - Synced" timeline (2026-09-09): V1
  "Akshita" (86 clips) linked pairwise with A1 "Akshita CH1" (86),
  V2 "Craig" (81) with A2 "Craig CH1" (81). Every audio item maps
  `channel_idx [1]`, mono - one program stream per source, one audio
  row per angle. The SOP writes down what that edit already does.
- The captain's MXF (LC4930.MXF): one video stream, FOUR audio streams
  (all pcm_s24le mono, no layout, no language, no title, no
  disposition - nothing tells them apart), one data stream. ffprobe
  metadata alone cannot name the program mix, so the project must
  declare it. (Content measurement: CH1/CH3/CH4 hot at roughly -36 to
  -30 dB RMS, CH2 near-silent at -70 dB - a future content-based
  disambiguator, not today's rule.)
- Explicit audio placement (`mediaType: 2` plus `trackIndex`) lands
  exactly one item on exactly the named row - measured no spill in
  either direction on 2026-09-09. Default placement is never used for
  speech. What lands is read back regardless.
- `DeleteTrack` and timeline `DeleteClips` both exist and answer True;
  `DeleteTimelines` lives on the media pool, not the project.
- `GetLinkedItems` reads link state back deterministically, which is
  what makes the verifier a real gate instead of a second opinion.

## What the engine refuses

- A multi-stream source with no declared program stream (catalog).
- A packing that disagrees with the track plan (builder).
- A row the plan did not name (builder).
- A timeline name that is already taken (builder - it builds beside,
  never over).
- A project or timeline addressed by anything but its exact name
  (every Resolve entry point).

A refusal names what was seen and what would resolve it. A silent
default is the failure this whole document exists to fix.
