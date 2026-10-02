# Resolve-domain test history

Module docstrings moved verbatim out of the resolve-domain tests on
2026-10-02 (test-suite halving). Each test module keeps its invariant in
one or two lines and points here for the incident that earned it. Where
this file and a test disagree, the test wins.

## test_bins_one_owner

```text
One module decides every bin path; everything else asks it.

The captain's pool holds two schemes side by side - the numbered bins
(`05 - Reels`, `06 - Subtitle renders`, ...) and the unnumbered ones
(`Reels`, `Reel subtitles`, `Subtitles`) - plus a firstmate proof
timeline (`SOP Proof_...`) filed among their reels. `resolve_bin_layout`
is the single owner of every bin path the way `timeline_layout` is the
single owner of every track name; `resolve_organization` and the build
half that imports media ask it rather than declaring their own names.

Every case below has a failing side: a legacy bin that must be moved
rather than stranded, a proof timeline that must file separately, and
the two MAYBES the brief names as known unknowns - answered here, not
assumed.
```

## test_build_commits_its_own_tail

```text
A reel build leaves its project store CLEAN.

Measured 2026-09-11 on the captain's project: HEAD read `reels build:
Reel 13 - the-accounting-firm-ai-called-healthcare` and the working
tree was dirty with exactly one thing - the verify record's
`reel_verification.organised`, the bin organisation.

The cause is an ORDER, not a missing call.  The per-build commit fires
inside the promoting capability (`reel_build`, `step_7_02_verify_reels`),
and the reels runner (`library/processes/reels/run_reels.py`) writes its
own output to `pipeline_data.json` AFTER it returns.  So the last record
could never be inside the commit it belongs to, and every build ended
dirty.

A store that is dirty after every build teaches a reader to ignore its
dirtiness, and that is how the captain's hand edits went missing three
times.  The run now closes its own record, in the loop that owns the
state write.

Read off the source and exercised through `commit_build`; nothing here
reaches Resolve or a real project.
```

## test_build_sweep

```text
The sweep that runs on every build, and the four things it must not do.

The captain's complaint, verbatim: *"over several iterations there is a
lot of empty bins and clutter from multiple file references to the same
things (i thought you said you fixed this?"*. He was told wrong. What
had shipped was where NEW items get filed; nothing swept what a previous
iteration left behind, because every mechanism that could - the caption
mark/sweep, the empty-bin retirement, the pool prune - was reachable
only by hand.

Measured on geo-podcast before this landed: 308 files / 68 movs / 43
unique contents in one step directory, two empty `Reel 09 ... (j-cut)`
bins, 29 pool items filed under `Not placed on any timeline`, and 23
movs pinned LIVE by ledger entries naming a staging timeline the project
does not have.

Deleting is the dangerous half, so the gates are proven in BOTH
directions (AGENTS.md 10.4): a file that must go and a file that must
STAY on the same shape.
```

## test_captain_edits

```text
The captain's edits survive a rebuild - as deltas, not replacements.

The captain (2026-09-09): *"if i ask to remove a piece of the video and
replace it with something else, and then ask you to rebuild the timeline,
those changes should persist"*. And: *"going into the subtitles and making
corrections that i ask of you so it's there"*.

Two gaps closed here, on top of `library/tools/external_inputs.py`
(state the pipeline did not produce, CHECKED never asserted):

1. Captions had NO external route. `captain_edits` is a supportable key
   whose check verifies each edit still corresponds to speech it names,
   so a supplied caption fix cannot silently drift from the audio.
2. Supply was whole-value. An edit is a small readable DELTA anchored
   to the spoken words - the one thing that survives a rebuild, where
   frame numbers (PR 847), source timecodes and pipeline ordinals do
   not. An edit that can no longer apply is reported STALE, loudly,
   never dropped silently.

Sibling lane `transcript_corrections` (in flight) owns CORRECTIONS - a
fact about the world, everywhere and forever ("Lucie not Lucy"). This
module owns EDITS - a decision about this one piece. Both are needed;
neither subsumes the other.
```

## test_comp_source_resolution

```text
Fusion Background nodes are built at the SOURCE frame, not the delivery frame.

Every Background node `build_effect_comp` draws - the vignette, the fade,
both halves of a transition - is a solid image merged over `MediaIn`. It
therefore has to be the size of the image Fusion sees, which is the
SOURCE clip's own frame. It defaulted to 1080x1920 while project 001's
A-roll is 1920x1080, so every graded clip carried a 1080-wide,
full-height dark rectangle down the centre of the picture, measurable in
the export as a ~15-level step at source columns 419 and 1499. Nothing
warned: a Background of the wrong size is a perfectly valid comp.

`CompEngine.from_params` already had a `source_res` for exactly this
reason, with a comment explaining it - but the renderer calls
`build_effect_comp`, which did not. A correct mechanism on a path nothing
executes is the failure mode this pipeline keeps re-finding, so this file
tests the path the renderer actually takes.
```

## test_composed_edit_refusal

```text
The condition the captain attached, and every attempt to get round it.

**A clip whose played length changes must have its Fusion comp
RE-DERIVED through the builder, never restored from the capture.**  A
per-clip comp is keyed to the window of footage the item plays, so a
trim invalidates it: restoring the captured comp verbatim across a
13-frame extension measured **wrong on 489 of 492 frames** (mean
1.09/255, max 88) on a cross-render floor of exactly 0.0000.  Nothing in
the timeline's readable state says so.  It looks right.

And the structural half: **a composed edit that changes a played length
and cannot reach the comp generator must REFUSE, not restore the old
comp.**  The first composed edit that quietly skips the re-derivation
produces a reel that is wrong on 99% of a clip's frames and looks right,
which is worse than the slow path it replaces.

Every test below is an ATTEMPT TO BYPASS the refusal, by the route a
future caller would plausibly take:

  1. run the edit with no comp generator at all
  2. hand it a generator that cannot actually be reached
  3. build the capture by hand with the comp in it
  4. mutate a legitimate capture to carry the comp
  5. reach past the accessor for the withheld artefact on disk
  6. let the generator report success and do nothing
  7. let the generator run and leave the clip with no comp
  8. wire the restore to the withheld comp in the source itself

The last one is a source-level assertion, because it is the only one of
the eight that a test driving the module cannot reach.
```

## test_composer

```text
The composer resolves a goal backwards - or refuses it by name.

The captain's intent: Ren reaches a goal without him naming the steps,
and a goal it cannot reach is refused BEFORE anything runs - "no
capability produces X" - rather than by running and failing.

Two halves, and the second is the deliverable as much as the first:

**Resolution.**  The two goals that close through capabilities alone are
pinned exactly - operations in run order, machine assumptions and
outside assumptions split.  Either half alone (operations without the
assumptions, or the split collapsed) would keep passing while the plan
stopped being runnable-or-honest.

**Refusal.**  Every other goal refuses naming what nothing produces: a
producer-less goal names itself, a goal behind a step with no operation
names the deepest requirement the traversal met and which step owns it.
`test_no_plan_names_a_blind_capability` pins the converse - the six
deliberately empty-effect operations are never selected, no matter how
many capabilities the composer learns. The ever-selected SET is
deliberately not pinned: it grows with every coverage lane by design,
and a snapshot of it would fail each remaining lane on a literal.
```

## test_content_keyed_render_cache

```text
The content-keyed render cache: one file per pixels, however many timelines place it.

The measured waste (scout `data/vep-asset-reuse-across-variants`,
captain's decision `data/vep-content-keyed-render-cache`): overlay
renders were duplicated across timelines because the artefact was NAMED
FOR THE TIMELINE it belonged to - 67 caption movs holding 23 unique
byte-contents, 12 motion-graphics movs holding 4. The captain's ruling
roots the identity in PROVENANCE instead: source footage for
subtitles, the project for motion graphics - and no timeline names a
file.

What is pinned here, end to end behind stub renderers (no Remotion, no
ffmpeg, no Resolve - the captain's CPU limiter):

- the shared hit: the same words in the same style at the same size,
  built under three variant timeline labels, render ONCE and pair back
  twice - with before/after counts, not assertions;
- placement-only differences (block ordinal, absolute timeline bounds)
  still hit: the Level-2 splitter (the reuse key hashing placement
  metadata) is gone with the Level-1 one (the timeline in the filename);
- genuinely different pixels still render: different words, different
  durations - and a reel never overwrites the master's caption, which
  is the old overwrite staying dead after the timeline left the name;
- motion graphics has a reuse path where it had none: the same graphic
  under two `vox_<reel>_<index>` placing labels renders once, and the
  filename is rooted in the project;
- the GC hazard: a file shared by two placements stays LIVE while ANY
  placement names it - one placing moving on must not unprotect the
  other - and a sweep that would remove a file any placement record
  still names REFUSES LOUDLY rather than deleting quietly.
```

## test_cursor_discipline

```text
The cursor is settable from N places; this test is the discipline.

`SetCurrentTimeline` is the call that killed a Fusion pass: any holder
can move the instance cursor out from under a sibling lane's build.
`library/tools/resolve_lock.py` already owns the shared layer for this
(`assert_current_timeline` inside a lease, `cursor_fence` for a guarded
section, `cursor_excursion` for a deliberate move-and-return) - and
since the 2026-09-20 migration every in-pipeline establishment goes
through it, while reads that never needed the cursor stopped moving
it at all (`timeline_serializer`'s `timeline=` handle,
`timeline_sync_qa`, `reel_deliver`'s lookup). No discipline about
asserting it can be enforced while the setter set grows silently.

So this test registers every `SetCurrentTimeline` site in shipped code
(`library/`, `bin/`) with its owner and migration state. Adding a new call site
fails here with instructions: route the establishment through
`resolve_lock`, or register the site with a reason. Removing one means
deleting its registry row - a row no longer needed is a lie about what
is still owed.

This test changes no runtime behaviour; what it stops is silent growth
of the setter set. The migration order it once tracked lives in
`docs/RESOLVE_AXI_ROUND5.md`.

`tests/` is out of scope by construction: fakes there RAISE on
`SetCurrentTimeline` (reads must not move the cursor), and the nine
live `*_against_resolve` / SOP tests declare their writes against a
real session. `library/tools/resolve_axi.py` is covered by its own AST
test and must never appear here.
```

## test_drift_check

```text
The drift detector finally has a caller, and the caller is pinned.

`library/tools/drift_check.py` is the schedule `transform_drift.py`
never had: newest build snapshot per reel against a self-read of the
live timeline, printing the per-reel factor at both ends of every
build. What these pin:

- the comparison that found the 2026-09-16 halving reproduces exactly:
  halved Pan/Tilt on every clip reads as one factor of 0.5;
- the two things the measurement must handle: snapshot rounding
  (0.49996-0.50004 is one factor, not a second one) and zero-valued
  placements (undefined ratio, never 1.0, never diluting the verdict);
- matching is on track, record frame AND name: a replaced clip reads
  as missing, never as a factor;
- the self-read is load-bearing: the read happens with the reel
  current, and the cursor is back where it started afterwards;
- the build runs the check at its start AND its end, and neither call
  can fail the build it instruments.
```

## test_edit_input_digest

```text
Edit-step input digests: declared, stamped, and reported - never skipped.

Covers `library/tools/edit_input_digest.py` (the spec reader, the digest,
the comparison lines) and the `run_control` stamp plumbing it rides on.
No pipeline run, no model, no Resolve: every digest here is computed over
synthetic inputs shaped like the real ones, plus the real manifests and
real step directories for the code half.

What this pins, beyond the functions' own contracts:

* the three most re-run model steps declare exactly the inputs this task
  evidenced (see each test's docstring for the run_history count), and
  the declarations stay narrowed to what each step actually reads;
* a re-run prints identical / changed / unknown and runs the step either
  way - there is no skip path to test because none was built;
* `tests/unit/context/test_ledgers.py` still passes unchanged (run separately -
  this file touches neither the preflight ledger nor its gate).
```

## test_editor_edit_carry

```text
The editor's timeline edits are carried through a rebuild, or refused.

Reel 7 (2026-09-29): the live cut omitted Craig source 25,263-25,374 and
Akshita source 60,745-60,979 and held a disabled Semantic graphic; the
rebuild restored both passages and re-enabled the graphic, and the
promotion overwrote the edit. Step one made that promotion REFUSE. These
tests pin step two: the same shape is CARRIED - the staging timeline
loses the two passages (the first with its gap closed, as the editor
closed it) and the graphic is switched off, judged on a re-read in
source ranges - and the promoted timeline matches the edited one on
those passages. The next rebuild carries the same edits again from the
ledger with no fresh editor change on record.

Trims and moves are carried in `tests/unit/resolve/test_editor_edit_carry.py`.
```

## test_fusion_bank_reads_this_build

```text
The Fusion bank must hand back THIS build's comp, never a sibling's.

Measured on the captain's `Reel 09 - your-website-is-only-20-percent
(final)`, built 2026-09-10 11:45.  Its last picture clip carried the
repaired old-TV switch-off (`PowerBand1` - a masked black `Background`);
its FIRST picture clip carried the pre-repair switch-on (`PowerCrop1`, a
`Crop` driven through `CropTop`/`CropBottom`, names `Crop` does not have,
so the tool fell to its 1920x1080 registry defaults at offset (0, 0) and
cut a 3840x2160 source down to its bottom-left quadrant).  One timeline,
two builders.

Nothing was stale on disk about the manifest, the plan or the checkout.
The bank was keyed over the INPUTS a comp was built from and not over the
builder, so the head clip - whose inputs had not moved across the repair -
hit a comp banked at 22:07 the previous evening and imported it verbatim.
The tail's `source_in` had shifted by 2.25 s, so the tail alone missed
the bank and got the repaired recipe.

These tests pin the property that ends it: what reaches the timeline is
the comp `build_effect_comp` emits on this run, whatever the bank holds.
```

## test_fusion_keyframe_range

```text
Keyframes land inside the PLAYED window, not across the whole source.

A segment cut from a 5657-frame source that plays only 73 frames must
have its zoom ramp and its transition keyframes inside those 73 frames,
not spread across the whole source.

**The window is stated in the COMP's frames, not the source's.**  This
file used to require the keyframes to sit between ``source_in`` and
``source_out`` - 25 and 97 - which is a bound on the SOURCE's numbering,
and it passed on every run while the picture was wrong: a per-clip
Fusion comp is rendered over the frames the clip plays, numbered from
zero, so a keyframe at source frame 25 is 25 frames past everything
Resolve renders for a clip that plays 73.  Two 15-frame ``defocus``
transitions on project 001 therefore drew none of their 30 planned
frames and held a full-strength blur across 331 frames instead.

``library/tools/fusion/played_window.py`` carries the measurement and
``tests/unit/picture/test_transition_placement.py`` counts the frames a transition
is drawn on, which is the check this file could not make: a keyframe in
range is necessary and not sufficient.

A test using source_in=0 would pass against either reading, so every
parametrized case here uses a non-zero source_in.
```

## test_fusion_tool_inputs

```text
An input name Fusion does not have is a SILENT no-op, and it shipped.

Fusion ignores an input it does not know without a word: the tool keeps
its registry default and the picture is whatever that default draws. A
`.comp` file is text and its reader is a closed-source application, so
nothing in this repository could catch it - and four separate defects of
exactly that shape were live on the captain's own Reel 09 timeline on
2026-09-10, three of them for months.

Read off the live comp on that timeline, with `tool.GetInput`:

* ``Ellipse1.Invert = 0.0`` while ``Ellipse1.Inverted = 1.0``. Fusion
  parks an unknown input in the comp as inert data, which is why the
  wrong one read back as set. The mask stayed solid INSIDE the ellipse,
  so the black Background it gated drew as a DISC IN THE MIDDLE of every
  graded clip. The captain found it by eye. AGENTS.md 5 stated the wrong
  name in prose and `FusionNode._validate` REQUIRED the wrong name, so
  every guard this repository had agreed with the bug.
* ``FilmGrain1.MasterStrength = 0.1`` - the registry default - while
  ``FilmGrain1.Power = 0.35``, the declared value. Every grain this
  engine has ever declared was ignored, and the node sat at a strength
  nobody chose (AGENTS.md 10.5) arriving through a NAME rather than a
  `.get`.
* ``PowerCrop1`` drove ``CropTop``/``CropBottom``; Fusion's Crop has
  ``XOffset``/``YOffset``/``XSize``/``YSize``. With those unset the tool
  took its own frame size at offset (0, 0), and Fusion's origin is
  BOTTOM-LEFT, so a 3840x2160 source came out cropped to its bottom-left
  corner on every reel's first and last picture clip. The old-TV switch
  animation never drew once.
* ``ChromaticAberration`` is not a registered tool at all, and
  ``LensDistort`` has no bare ``Distortion``.

`library/tools/fusion/tool_inputs.py` is the gate: a table of what each
tool really has, dumped from a running Resolve by
`scripts/probe_fusion_tool_inputs.py`, and `FusionNode._validate` refuses
an authored node that drives anything else.
```

## test_marker_resolution

```text
The other end of the captain's timeline notes: resolve, verify, clear.

What these tests fake, and what they do not stand in for
--------------------------------------------------------
The dict-backed `FakeTimeline` / `FakeClipItem` below fake ONLY the
deletion contract this module judges: `DeleteMarkerAtFrame` returning
True for a present frame and False for an absent one, and `GetMarkers`
reading the markers back. That contract is measured against a real
Resolve in `marker_feedback`'s docstring and in
`tests/qualification/test_marker_feedback_against_resolve.py` - nothing here re-proves
what Resolve returns. What is proved here is what THIS module does with
whatever comes back: a True it re-reads as gone is a removal, anything
else is not, and a decline or an unverifiable note never reaches the
call at all (the counting fakes fail the test if it is reached).

Everything on disk runs against real files in `tmp_path`. No test here
reaches a real project (`tests/tooling/test_tests_never_reach_real_projects.py`).
```

## test_marker_routing

```text
Routing the captain's typed timeline notes to the steps that own them.

The shapes here are REAL.  `CLIP_NOTE`, `MOMENT_NOTE` and `AMBIGUOUS_NOTE`
are the three notes the captain typed onto 001's built timeline, copied
verbatim out of the pull file `marker_feedback` wrote on 2026-08-28 -
same fields, same frames, same clip lists, same words.  A routing test
written against invented notes proves the router agrees with whoever
wrote the fixture.

The clip lists were re-checked against that pull file on 2026-08-29, when
`timeline_decisions` began matching them back to the placements of 001's
own manifest.  Three transcription errors came out: the moment note's V1
clip read source 2457..2529 where the pull file says 25..97, its caption
read 15..87 where the file says 0..72, and the music bed - a fourth clip
under all three notes - had been dropped, which is why the first test
below said "four clips" over a list of three.

No Resolve, and no fake of one: everything here is the disk half, the
same line `tests/unit/resolve/test_markers.py` draws.  Nothing in this
file reaches a real project - the project is built under `tmp_path`.
```

## test_marker_writes_hold_lease

```text
Marker writes hold the Resolve instance; reads were already leased.

`library/tools/marker_feedback.py` carried exactly one lease construct
and it was on the read (`pull`). Every function that MUTATES a marker -
reply placement, clip-marker removal, resolution deletes, promotion
carry, decision stamping, master marking - wrote with no lease held
anywhere in its own chain. A lane holding an EXCLUSIVE lease to place
clips could run concurrently with another writer adding or deleting
markers under no lease at all.

This pins both halves: every mutation takes an EXCLUSIVE lease, and a
write attempted while another process holds the instance refuses instead
of proceeding - with the marker TEXT and colour read back FROM A
SEPARATE PROCESS, never a count and never an in-script read.
```

## test_no_default_as_measured_sweep

```text
The widened default-as-measured sweep (WP1, principle 3).

`tests/unit/picture/test_picture_view.py` sweeps exactly
one producer - `compute_deterministic_assessment`. The defect family it
names spans the codebase ("assume another exists until the sweep says
otherwise"), and the 177-site `audit_p3b.py` pass plus its classification
(`docs/WP1_P3B_CLASSIFICATION.md`) proved it: 16 sites where a swallowed
item let an unmeasured value publish as measured.

This file is the widened gate. For every tranche-1 fix it pins the
admitted absence directly against the fixed function, beiden directions
bound (AGENTS.md 10.4):

- it FAILS on the defect: revert any one fix and the matching test goes
  red (a gate that cannot fail is worse than no gate);
- it does NOT fail on legitimate optional swallows: the last two tests
  pin swallows the classification cleared, so a future "fix" that turns
  an admitted absence into a refusal - or a gate that flags every
  `except: continue` - fails here first (a gate that fails correct
  output is no more coverage than one that cannot fail).

Tranche 2 (`docs/WP1_P3B_CLASSIFICATION.md`, eleven sites in the
reel/render/captain-edits subsystems) is pinned the same way below:
one defect-direction test per site, each with the mirror proving the
measured case still reads measured.
```

## test_pool_stream_meta_refresh

```text
A pool item whose cached stream metadata disagrees with its file is
refreshed at the build's safe rebinding point, not reused.

THE INPUT THAT BREAKS THIS: Reel 26, 2026-09-13 - a pool item caching
`866x480`/ProRes for a file that is `904x480`/qtrle on disk. The
artefact was rewritten in place under a stable path (a re-render under
an unchanged drawing digest, then `transcode_in_place`'s ProRes-to-qtrle
carriage), while Resolve caches dimensions and codec at import where
the scripting API cannot refresh them. Reusing that item renders a
ProRes decode of qtrle bytes at the wrong dimensions: media offline on
exactly the frames the clip covers.

The fix reuses `deliver-reel`'s comparison (`pool_stream_meta`, the one
its preflight refuses on) at `import_pool_item`'s lookup hit: a
disagreeing hit is left pooled and a fresh item is imported beside it,
so the staging timeline binds the fresh item and promotion carries it.
Deletion is never the repair - the approved timeline still plays the
stale item, and promotion retires that timeline to Archive rather than
deleting it (`orphan_removal.assert_removable` refuses placed items for
exactly this reason).

A test builds its project under `tmp_path`, or it skips. It never falls
back to a real one. The disk probe is stubbed: no ffmpeg, no Resolve.
```

## test_relinker_destination_guard

```text
H12 - relink_project rewrites the media pool of whatever project is open.

The function's project_slug argument was used only to build path
mappings - it was never checked against the open project's name.  Run
the relinker for project A while project B is open and B's media pool
is rewritten.

These tests prove:
- correct project passes verification
- wrong project is REFUSED (DestinationMismatchError)
- no project open is refused when expected_project is set
- backward compatibility: empty expected_project does not refuse
- re-verification happens immediately before the first mutation
- DestinationMismatchError documents the hazard

All tests use mock objects - no Resolve writes.
```

## test_render_borrow_restore

```text
H4 - render settings and the render queue are project-global.

Neither renderer used to restore them. A render that changes project-
global settings and leaves them changed means the NEXT render inherits
them, and the captain's own manual render inherits them too.

These tests prove:
- format/codec is saved before mutation and restored in finally
- only the job this process created is deleted (not DeleteAllRenderJobs)
- restore happens on the success path
- restore happens when the body RAISES (the path that is always missing)
- the page is restored on both paths
- RenderSettingsError is importable and documents the hazard

All tests use mock objects - no Resolve writes.
```

## test_render_watch_in_render

```text
Render 6.01 wires the frames into its own review call.

Captain's ruling on vep-llm-context-audit-decision-render-qa-llm-role:
"Wire the frames in" - the same route step 6.02 took. Step 6.01's model
call could ask for visual verdicts with `visual_qa` empty, because the
grabs run only behind `PIPELINE_PERCEPTUAL_QA` (off by default) while
the handoff unconditionally instructed the model to use the table.

So the deterministic half now draws render-watch strips off its own
export behind that same flag and emits them as `render_watch_frames` -
6.02's key, 6.02's module, 6.02's confess-when-absent handoff language -
and the `VISUAL_QA_INSTRUCTIONS` marker is always replaced: with the
table's description when the grabs ran, with the record of their
absence when they did not.

Nothing here renders, opens Resolve, or needs ffmpeg: the draw is
stubbed and only the wiring is exercised. A test builds its project
under `tmp_path`, or it skips. It never falls back to a real one.
```

## test_renderer_tooling_imports

```text
The renderer's verification layer must load when it is run as a SCRIPT.

`resolve_build_timeline.py` is invoked as a script by the pipeline, so
`sys.path[0]` is its own directory and the repository root is NOT on the
path. `visual_qa_router` imports `library.tools.*` absolutely, so it
raised ModuleNotFoundError there - and because all four import groups
shared one try/except, that single failure set every timeline QA station,
both neural-engine wrappers and both Fairlight helpers to None.

The whole verification layer was therefore dead in every scripted run.
The only signal was one line on stderr reading "Timeline QA script not
loaded", and `verification_passed: true` was still reported.

These tests run the import the way the pipeline does - a subprocess with
the repo root deliberately absent from the environment - because that is
the only way to reproduce it. Importing the module from a test process
that already has the repo root on sys.path cannot fail, which is why
nothing caught this.
```

## test_resolve_guard_wiring

```text
The guard is WIRED, and this is what stops it drifting back to zero.

`resolve_placement_lock` was removed on 2026-09-12 with zero callers in
library, tests, docs or scripts, while the check it was meant to protect
had 22. A guard nobody enters reads as coverage (AGENTS.md 10.4), so the
count is not a detail - it IS the property.

Three claims are pinned here:

1. Every `RESOLVE_CURSOR` operation in `concurrency_routing.OPERATIONS`
   really takes the lease.
2. Every module that connects to Resolve is accounted for in that table,
   so a new Resolve caller cannot appear unrouted.
3. Nothing under `library/` turns the refusal off.
```

## test_retime_drift

```text
A recorded trim whose anchor re-times must be loud before a build proceeds.

Reel 17, 2026-09-21: a recorded `span_retime` head pin ("So for small
business", edge=head) re-derives against transcript word starts on
every build (`captain_edits.match_span_retimes`). The wave
re-transcribed the reel, the anchor word "So" moved 1407.830 ->
1407.970 (+0.14s, +3.4 frames), and the reel head followed it. Nothing
reported lost, nothing reported at all: a caption-only change shipped
a reel seven frames shorter than its baseline, and only a recorded
frame baseline caught it.

So the enumerable pre-build state needs TWO outcomes, not one:

1. an anchor that no longer RESOLVES (stale - loud today, kept loud
   and enumerable here), and
2. an anchor that resolves SILENTLY TO A DIFFERENT PLACE (drifted -
   silent by construction today, the Reel 17 case).

`match_span_retimes` returning 1407.97 against the re-timed transcript
is the offline reproduction: deterministic, no Resolve, no build. The
freshness check built on it is what would have caught the build.

No Resolve, no real project: every fixture is synthetic under
`tmp_path` (AGENTS.md 8).
```

## test_staging_scratch_containment

```text
Staging scratches cannot sit beside the deliverables (2026-09-11).

Reconstruction of the incident: the captain's project held twelve
timelines, three of them ours - restore scratches sitting in
`05 - Reels` one level SHALLOWER than their own Reel 13 in
`Earlier plans` - and the captain placed feedback on a throwaway:
"im a little confused why there are 3 timelines for reel 13". Their
fourth leftover-timelines report wearing a new shape.

The gap is not the sweep: the lane that made these scratches
deliberately did not run it, correctly, rather than risk the
captain's hand edits. The gap is that a staging scratch could be
CREATED in a deliverable bin at all, and that nothing said it was
still there on the next run. The containment is two mechanical
properties, each pinned below in both directions (AGENTS.md 10.4):

1. A staging or scratch timeline is created in the dedicated scratch
   bin (`resolve_bin_layout.SCRATCH_BIN`), outside every bin the
   captain reviews - and the organiser files one found anywhere else
   back there. A staging timeline landing in the reels bins fails.
2. A scratch that outlives its promotion is REPORTED BY NAME on the
   next run - held ones as pending, unheld ones as outlived - never
   deleted silently and never swept past a pending promotion.

The three fixture names are the real ones from the captain's pool,
slugs verified against the repo's own records (Reel 13's final name
in `test_pending_promotion_hold`, Reel 28's in
`docs/REEL_REBUILD_RUN_20260908_R2.md`).

No test here reaches Resolve: the pool is fakes, the project is
`tmp_path`, and the sibling lane owns the live session and the real
deletions.
```

## test_timeline_decisions

```text
Stamping each clip with the decision that produced it.

The producer side of the captain's note loop.  `marker_feedback` reads
their typed notes, `marker_routing` sends each to the step that owns it,
and this is the half that lets the marker itself carry the answer instead
of the routing having to infer one.

THE SHAPES HERE ARE REAL.  `MANIFEST` is 001's own assembly manifest,
trimmed to the placements the captain's three notes actually sit on, with
its real labels, frames and source ranges; the three notes are imported
verbatim from `test_marker_routing`, where they were copied out of the
pull file `marker_feedback` wrote on 2026-08-28.  A stamping test written
against invented clips proves the stamp agrees with whoever wrote the
fixture.

No Resolve.  The one call this module makes into Resolve -
`UpdateMarkerCustomData` - is driven against a fake that records what it
was asked to do and REFUSES to add a marker, because never adding one is
the property that keeps the captain's timeline looking the way they left
it.  Nothing here reaches a real project: everything is under `tmp_path`.
```

## test_timeline_ingest

```text
A rough cut that already exists, read off a LIVE Resolve timeline.

`external_inputs.WITHDRAWN` used to rule this out, and its reason -
"reading it means copying that database and opening it as SQLite ... and
nothing maps its clips back onto a typed pipeline key" - is true of a
CLOSED project and false of a live one.  This module is the producer that
distinction allows, so the tests hold it to the standard the withdrawal
was protecting: what it emits must PASS the real checks in
`external_inputs`, not merely look right.

Resolve is faked here rather than driven.  The fakes return what the real
proxies were measured to return on the GEO Podcast field test
(2026-09-04), including the one-frame disagreement between
`GetLeftOffset()` and `GetSourceStartFrame()` that
`test_source_times_are_used_never_left_offset` exists to pin.
```

## test_timeline_oracle

```text
The timeline is the oracle: a hand edit reads as intent, never drift.

Each test fails if its mechanism is removed:

- a hand edit HE made (a clip trimmed by hand, a cutaway added, a row
  gone) is detected off the LIVE rows and described in his terms;
- the description never says drift, correct, fix, or reconcile:
  detecting and describing is this lane, deciding is his call;
- identity never joins on `unique_id`: two reads with churned ids but
  identical names and spans report nothing moved;
- the live reading moves no cursor: reaching for `SetCurrentTimeline`
  raises rather than reading;
- the precondition is evaluable against the LIVE timeline: records
  claiming no cut while the timeline shows one answer from the screen,
  not the paperwork;
- a DECLARED name the plans speak evaluates too: the live-readable one
  (`state.verify_reels.reel_build`) from the screen, every other
  requirement name via its own check - and a name in neither the
  vocabulary nor the legacy one raises rather than answering.
```

## test_timeline_sop_conformance

```text
The master builder obeys the SOP, and the verifier reads it back.

Defects covered here (fake Resolve, no live connection):
  2. two cameras mean TWO audio rows, one program stream each,
  3. a non-program stream is stopped at placement, never leaking on,
  4. every track created gets occupied (empties are deleted, not kept),
  5. a-roll picture links to its speech; a caption inside a speech span
     joins that link group in ONE call (linking is exclusive, not
     additive - a second pair-call would break the first),
  6. every row is named from the plan.

`library/tools/timeline_conformance.py` reads a built timeline back and
reports every SOP violation it finds. It is deterministic and it can
fail, so it is a real gate.
```

## test_tv_power

```text
The old-TV switch is ONE shape, played in two directions.

The captain's Reel 20 marker invites the interpretation ("you can create
some animations for this"), and `library/tools/tv_power.py` is the
proposal: every timing states what it is and why, and all of them are
changeable through the project's own `tv_frame` declaration.

His Reel 09 marker of 2026-09-11 is what makes the shape single:

    "also the tv on animation should start from fully black just like
     the reverse of how the tv off animation goes to fully black"

Until then there were two animations - `line/expand/bloom` at 4/6/8 with
no dot phase and a lit first frame, against `collapse/dot/decay` at
6/3/9 ending at black.  `test_switch_on_is_the_switch_off_reversed` is
the gate that keeps them one: it evaluates BOTH comps frame by frame and
fails the moment either direction is re-timed on its own.
```

## test_uncarried_blue_back

```text
Uncarried notes get their Blue back, at the seam, byte-identical.

Found 2026-09-20: a promotion cut the picture out from under one of
the captain's Blue notes, the uncarried report went to stderr, a Green
reply quoting his words went onto the timeline - and from where he
sits the note was simply gone, because he scans for BLUE. A green
marker containing his words reads as us talking.

Proven here on fixtures shaped like Reel 08's hand-verified fix: the
note sat at frame 640 duration 81 over LC4932.MXF source 31017-31060;
the rebuild runs 30808-30826 and then jumps to 31097, so the seam is
timeline frame 639. The promotion must come back with his Blue at
639 byte-identical and our reply BESIDE it - and a promotion whose
picture still plays must carry unchanged, exactly as today.

Every assertion below is on note TEXT and colour. A count-only
assertion has already destroyed a note on this project.
```

## test_uncarried_notes

```text
A dropped captain's note is an obligation, not a log line.

The defect this pins
--------------------
The promotion path reported a note it was about to lose, twice, and
both copies were discarded before anything could act:
`marker_carry.report` named it on stderr, and `step_7_02` read two
sibling keys off `promoted` while dropping `promoted["markers"]` - the
same losses as structured data. Measured 2026-09-20: a reel's blue was
correctly identified as unresolvable and named with the captain's
words, no durable record survived, and the note was gone for roughly
forty minutes.

Each test fails if its mechanism is removed:

- the durable record - delete the `record` call from the step helper
  and a dropped note leaves nothing on disk;
- the CONTENT - the record names the reel and quotes the captain's
  words verbatim (asserted on content, never on a count);
- the obligation - remove the gate from `sign_off` and the reel signs
  off with its note still unaccounted for;
- the discharge - without it the reel stays unsigned; a reasonless
  discharge is refused rather than waving the obligation through;
- the clean path - a promotion that carries everything files nothing
  and signs off exactly as before;
- our own words are never filed as his - stranded and independent
  replies stay reported, never re-filed as Blue notes of his.
```

## test_vfx_stills

```text
4.03 sees the picture its effects land on.

The motion designer plans effects onto shots it has only read about:
`vfx_suggested` carries camera prose, and the `motion` view carries
peaks - neither says what the shot LOOKS like. So the 4.03 pre-bridge
draws one still per candidate block (at the block's first measured
motion apex where one exists, else the middle of its range) and asks
the still router what moves in it - the same route step 5.01 takes
for colour (`shot_stills` / `still_colour_notes`).

These tests drive the bridge helpers with a stubbed extractor (no
ffmpeg): apex selection prefers the first apex in range, the middle
is the fallback, a block with no range or no source is NAMED rather
than quietly absent, and the router observation states whose vision
answered or why nothing did.
```
