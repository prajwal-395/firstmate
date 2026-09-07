# Chroma key transitions: what was measured, and what was built

The captain named **chroma key transitions** as a capability the pipeline should
have and does not. Read plainly that is the classic form - a graphic or a piece
of footage shot on green, keyed out, sweeping across frame to hide a cut. In
short form it is the wipes, sweeps and shape transitions that carry a cut
without a hard jump.

Measured 2026-09-07. Three questions had to be answered before anything was
built, and all three had answers that changed the design.

---

## 1. Had the existing Fusion transition path ever executed?

**Yes - on real project runs, and `fusion_transition_generator.py` is not how.**

This was worth asking: `align_sfx_to_prosody` was deleted three hours before this
lane started, for being written, declared, and never once executed.

`library/steps/step_6_01_render/fusion_transition_generator.py` is imported by
exactly one file, `library/steps/step_6_01_render/test_integration_demo.py`. No
step, no bridge, no manifest and no DAG node reaches it. It is a thin wrapper
over `library/tools/fusion/effects.py` that duplicates
`library/tools/fusion/comp_builder.build_effect_comp`'s dispatch.

But the *capability* underneath it is live. The route that executes is
`compile_manifest` -> `fusion_effects.transitions` -> `apply_fusion_comps` ->
`comp_builder.build_effect_comp` -> `fx.transition_tail`/`transition_head`.
Counted over every `.comp` file on this machine, by the node names those
builders emit:

| project | `TransDefocus` | `TransDBlur` (zoom_blur) | `TransFlash` | `BgTrans` (fade_to_black) |
|---|---|---|---|---|
| `post a day keeps the apple away/001` | 32 | 2 | 2 | 0 |
| `video_projects/.../replan-001` | 4 comps carrying transition nodes | | | |
| repo's own `step_6_01_render/fusion_comps/` (demo output) | 36 comps | | | |

Project 001's 36 are in `assets/fusion_presets/`, its real pipeline output
directory, written between 2026-08-17 21:04 and 2026-08-29 18:15. So the drawn
transition vocabulary has reached a real project. It has **never** run on the
reels path: `library/processes/reels` is two nodes, `build_reels` and
`verify_reels`, and neither builds a Fusion comp at all.

**Reproduce:**

```sh
grep -rl "TransDefocus\|BgTrans\|TransDBlur\|TransFlash" \
  --include='*.comp' ~/Documents/content_stuff | xargs -n1 dirname | sort | uniq -c
```

---

## 2. Real chroma keying, or alpha-native elements?

**Alpha-native. Nothing is keyed, and the reasons are measurable rather than
stylistic.**

### There is no green-screen source to key

Searched every asset library and every project on this machine. The only motion
elements any project owns are in the Lucie brand assets:

| file | codec | pix_fmt | shape | length |
|---|---|---|---|---|
| `transition_bumper.mov` | ProRes 4444 (`ap4h`) | `yuva444p12le` | 1080x1920 | 45 frames @ 30 |
| `logo_reveal.mov` | ProRes 4444 (`ap4h`) | `yuva444p12le` | 1080x1920 | 90 frames @ 30 |

Both already carry an **authored alpha channel**. They were rendered from the
project's own Remotion compositions (`remotion-compositions/TransitionBumper`,
`.../LogoReveal`) - the same route step 4.05 already runs for every caption and
4.06 for every motion graphic, all emitting ProRes 4444 with alpha. **An element
that is born with alpha never needs keying at all.**

The one library of non-alpha overlays that exists, `assets .../vfx/`, is eight
`yuv422p10le` ProRes clips named `(Screen)` and `(multiply)` - blend-mode
plates, not green screen. They have no alpha and no key colour, and the Fusion
blend route for them is closed anyway: `ApplyMode` in a Merge node is one of the
six things that must never appear in a `.comp` (AGENTS.md 5 - it SIGSEGVs).

### A keyer cannot recover an authored alpha, even in its best case

`transition_bumper.mov` frame 22 was flattened onto perfectly uniform
`0x00B140` - no spill, no lighting variation, lossless PNG, a cleaner plate than
any real shoot produces - and keyed back with `ffmpeg chromakey` across the
similarity range. Ground truth is the element's own alpha: 31,871 ink pixels of
2,073,600.

| `similarity` | mean abs alpha error | background left opaque | holes punched in the element |
|---|---|---|---|
| 0.01 | 253.651 | 2,041,729 | 0 |
| 0.05 | 1.615 | 1,054 | 10,426 |
| 0.10 | 0.436 | 2 | 20,980 |
| 0.20 | 0.505 | 0 | 24,729 |
| 0.30 | 1.180 | 0 | 30,501 |

**There is no setting that returns the element.** Every one either leaves the
plate or eats the artwork. Looked at rather than only counted: the design's soft
glow around the bulb becomes a hard opaque blob at 0.05 and disappears entirely
by 0.20, and the letterforms thin at every setting that clears the plate.

The instrument was validated first, on a synthetic fully-opaque control
(mean 255.00, 100% opaque), a half-transparent control (mean 127.50, 50/50) and
the element against itself (error exactly 0.000) - and against a deliberately
wrong comparison, the same alpha shifted 7px, which reads 1.033 rather than 0.

### Choosing between those rows is taste with no producer here

A key colour, a similarity and a blend are three numbers nobody in this pipeline
is asked for. `if similarity < 0.x` is precisely the invented threshold
AGENTS.md 10.5 and `tests/test_no_creative_floors.py` exist to keep out. An
authored alpha needs none of them: the softness is drawn, not derived.

**So the engine requires alpha and does not key.** That answer is executable, not
prose: `transition_overlay.measure_element` refuses an element with no alpha
plane by name, quoting the reasoning.

---

## 3. Why an overlay is buildable where a wipe is not

`transition_vocabulary.WITHDRAWN` withdrew `cross_dissolve`, `wipe` and
`whip_pan` for one reason, and it is still true: a per-clip Fusion comp sees only
its own clip, so nothing on that route can **mix** two pictures.

An element laid over the cut does not mix them. It **hides** the cut - an
additive overlay on its own track, reading neither neighbour. That is the whole
reason this capability exists when a wipe does not, and it is why the withdrawal
of `wipe` was amended rather than left: a wipe that *reveals through* the
outgoing shot is still undeliverable; a shape that *covers* the cut now is.

There are now two routes in the one enumeration, and
`transition_vocabulary.ROUTES` says which draws each type. `element_overlay` is
deliberately **not** in `PLANNABLE_TYPES`: step 4.02's handoff, the brand
allow-list and `apply_fusion_comps` all read that tuple, and advertising a type
there that the per-clip route cannot place would advertise a capability the
route cannot deliver. `withdrawal_reason('element_overlay')` says *misroute*
rather than *unknown type*, so a caller that arrives on the wrong route is not
told to go and build a second one.

---

## 4. The three hard questions

### Does a transition extend the reel, or consume frames from its neighbours?

**Neither. It is additive** - `transition_overlay.TIMING_IS_ADDITIVE`.

- **Consuming frames** shortens the reel, which is a delivered quality the
  captain measures. Worse: a reel is its `keep_ranges` laid end to end
  (`reel_build.reel_time`), so eating frames at a seam moves every later block's
  `timeline_start`, which is one of the five fields
  `plan_provenance.footage_binding_hash` digests. Every caption after the seam
  would have to be re-planned and re-rendered to stay bound to its footage.
- **Extending the reel** inserts picture the audio does not have, because both
  come off the same ranges.
- **Laying the element over the cut** leaves the hard cut on exactly the frame it
  was already on.

Checked, not asserted. Rendered over a real cut on a real reel, all three
versions are **96 frames**: no element, the brand bumper, and a fully opaque
element. `tests/test_transition_overlay.py` asserts the binding hash is
byte-identical either side of the overlay pass.

### What does it do to captions crossing the cut?

**Nothing to their timing or their binding**, which is the whole point of the
additive answer. What it does do is **draw over them**, because V4 sits above the
captions on V3 - an element that hides a cut hides what is on that frame.

That is the gesture, not a defect, so it is measured and reported rather than
failed: `transition_overlay.captions_covered` returns every overlap including a
single frame, and the conformance verifier emits it as **F20**, severity
`warning`. A check that failed correct output would be no more coverage than one
that cannot fail (AGENTS.md 10.4).

### Do the conformance checks understand a transition?

**They did not, and the failure was worse than either option in the question.**

`_snapshot_to_reel_timeline` sorted a timeline's clips with

```python
if video and track <= 2:   -> picture
elif video and track == 3: -> captions
elif audio:                -> audio
```

and an implicit `else` that **dropped the item on the floor**. A clip on V4 was
not read as a duplicate placement and not read as a picture hole. It was not
read at all, which is the worst of the three because it is invisible - the
gate-that-cannot-fail shape wearing a different hat.

Three checks now close it:

- **F18** grades the elements on V4 against the plan the build recorded, in both
  directions: planned-and-absent, present-and-unplanned, wrong length, two
  stacked at one frame. Paired by **record frame**, never list index - the
  mistake that turned F2 into 701 meaningless findings.
- **F19** reports any video item on a track this verifier has no check for, so
  the next feature that adds a track finds out on its first run instead of
  never.
- **F20** reports the caption seconds an element covers, as a warning.

The plan F18 grades against is **read from what the build wrote**
(`pipeline_output/review/transition_overlays.json`), never re-derived: a
re-derived plan is only the build's plan while nothing changed in between, and
that assumption already produced 42 confident meaningless errors on this path
(`PLAN-MISMATCH`).

---

## 5. Rendered, and looked at

Composited over a **real reel frame at real size**, not a demo card.

Reel 01 of the field test (`geo-is-comprehension-not-position`, the latest
approved plan) has three keep ranges and two carrying seams:

```
ranges  (0.181, 11.420)  (11.980, 44.745)  (333.798, 341.270)
seam 0  frame    0  reel_head     carries nothing
seam 1  frame  270  take_removed  master 11.420s -> 11.980s
seam 2  frame 1056  closer        master 44.745s -> 333.798s
seam 3  frame 1235  reel_tail     carries nothing
reel    1235 frames = 51.510s
```

Seam 2 - the closer, a 289-second jump on the master - was rebuilt outside
Resolve from the original MXFs through the transcript's own master-to-source
mapping, letterboxed exactly as `reel_framing` measures the reels deliver
(3840x2160 fitted to 1080x1920 = a 1080x607.5 strip, 31.64% of frame), with the
project's **real rendered caption overlay** for that passage on top.

Three versions, 96 frames each:

| version | frames 42-53 mean luma | what the picture shows |
|---|---|---|
| no element (as built today) | ~30 | the cut at frame 48 is plainly visible |
| `transition_bumper.mov`, anchor `centre` | ~30 | the LUCIE lockup draws over the cut; **the cut is still visible underneath** |
| a fully opaque element, anchor `centre` | **16** (studio-swing black) on exactly frames 42-53 | the cut happens behind the element and is invisible |

The 12 covered frames are exactly `48 - 12//2 = 42` through `53`, which is where
the planner said to put them.

### The finding this produced

The mechanism works. **The artwork to use it as a cover does not exist yet, and
authoring it is the captain's.**

`AlphaMeasurement` now measures which gesture an element delivers, and it is a
definition rather than a threshold - a frame every pixel of which is fully opaque
hides what is under it:

| element | peak mean alpha (of 255) | fully opaque frames | gesture |
|---|---|---|---|
| `transition_bumper.mov` | 1.358 (0.53% of frame) | 0 | `stamps_the_cut` |
| `logo_reveal.mov` | 3.065 (1.2%) | 0 | `stamps_the_cut` |
| a real rendered caption | 6.773 (2.7%) | 0 | - |
| a fully opaque element | 255.0 | 12 | `hides_the_cut` |

The measurement predicted both pictures correctly, in both directions, and the
gesture is printed on the run that places it. Both gestures are legitimate
transitions and neither is refused: what is refused is not knowing which one a
declaration bought until it is on nineteen timelines.

So on the captain's own reels **this capability delivers a brand stamp over the
cut, not a cover of it** - because a brand bumper is the only alpha element the
project owns. A covering sweep is a project asset with a colour, a shape and a
motion in it, and those are taste this lane was told to leave alone.

**Reproduce:** every number above comes from `ffmpeg`/`ffprobe` and the module's
own CLI:

```sh
python3 -m library.tools.transition_overlay --measure <element.mov>
```

---

## 6. What is reachable, and how

No new operation and no new node. The capability rides the existing reels
process, which is what keeps it on one path:

1. A project declares `effect.transition_overlay` in its `project.yaml`. The
   project is the only declarer today: `EffectSlots` carries no
   `transition_overlay` field, so `reel_build` passes an empty brand half rather
   than reading a template for a key it cannot hold. Whether a template should
   gain the slot is a section 14 question - where the line between per-series
   parameters and artwork falls - and this lane did not answer it by implication.
2. `build_reels` (`reel.build`) resolves and **measures** the element once,
   before any timeline is created, then plans and places one per chosen seam on
   V4 and records the plan.
3. `verify_reels` (`reel.verify`) grades V4 through F18/F19/F20.

Declare nothing and get nothing - a project with no declaration gets a timeline
byte-for-byte identical to the one it got before this existed, three video
tracks and no V4.

**Assumption to reconcile.** Another lane owns the full-screen architecture and
had landed no `docs/` ruling and no open PR when this was written. So
`transition_overlay.py` **renders nothing**: it consumes an element by path.
`asset` mode needs no renderer at all; `composition` mode names the composition
and its project-owned `source`, the shape `content.bookends` already uses, so it
goes wherever bookends go. Whichever renderer that lane names, no second path
was built beside it.

### What it costs the captain's existing reels: nothing

Run read-only against the live `Podcast (field test)`, 48 reels graded against
the plan the provenance says built them. **The plan must be named**: the
verifier refuses a plan whose content hash does not match provenance
(`PLAN-MISMATCH`, "every finding below would be noise"), and with no `--plan` at
all it derives an EMPTY one from the master, which grades no captions and turns
every reel into `F4: planned 0 picture items`. That derived-plan run reports 192
errors and is not comparable to this one - a smaller number from a blinder
instrument.

```sh
python3 -m library.tools.reel_conformance_verifier \
  --project "Podcast (field test)" --master "GEO Podcast - Synced" \
  --plan   <project>/pipeline_output/review/reel_proposals_v2.json \
  --transcript <project>/pipeline_output/scratch/timeline_transcript/transcript.json
```

```
   538  F14              error      caption planned and never placed
   434  F2               error      caption card duration
   150  F17              error      caption card mixes speakers
    29  PLAN-MISMATCH    error
    28  NO-REFERENCE     error
    29  QB-NOT-FOLLOWABLE warning
    29  QB-CTA-SHARED    warning
   ... (F5, F6, F7, F8, F15, PQ-LENGTH, QB-NO-TAKEAWAY)
FAILED: 1193 error(s), 83 warning(s) across 48 reels.
```

Re-run on 2026-09-07 after rebasing onto `main`. An earlier run of the same
command on the same 48 reels read 1192/83, with F14 at 526 and F2 at 446. The
totals moved by one error because other lanes rebuild these reels between runs;
the classes and their order are unchanged, and no class appeared or vanished.

Every one of those classes predates this lane. **F18, F19 and F20 appear zero
times**, which is the correct answer and not a silent one: no project on this
machine declares `effect.transition_overlay`, so V4 is empty on all 48, F18 runs
only when either side has something, and F19 has no unclassified track to find.
Declare nothing and get nothing, all the way through to the gate.

One transient failure worth recording rather than fixing: an earlier run of the
same command died in the after-hash re-read with `timelineFrameRate read back as
None`, and did not reproduce. Resolve is one shared instance across lanes
(AGENTS.md 5), so the likeliest cause is a timeline being created by another
lane between the verifier's two passes. It is not this change and not
investigated here.

## 7. Two things found by running the documented syntax

Both would have shipped on a reading of the code alone.

- **`on_cuts:`, never `on:`.** YAML 1.1 - which PyYAML implements - parses a bare
  `on` as the boolean `True`, so `on: [closer]` becomes `{True: ['closer']}`.
  The declaration then refuses, loudly and correctly, while telling the declarer
  they named no seams as they look at the line where they did. The first
  end-to-end smoke test wrote the documented syntax and hit it.
- **`startFrame`/`endFrame` are the POOL ITEM's own frames.** AGENTS.md 5 says
  Resolve pool items report their own rate regardless of the timeline. The Lucie
  bumper is 30fps on a 23.976 timeline: 36 timeline frames of it is 45 of its
  own, and passing the timeline count would have taken 1.2s of a 1.5s element.
  The placement carries the element's length in SECONDS and converts once, at
  the pool item's rate, rather than round-tripping through timeline frames -
  which rounds twice and drifts a frame at 25fps.

## 8. What is NOT built, and why

- **No keyer.** Section 2. If green-screen source ever arrives, the honest change
  is to key it once at ingest into an alpha element, not to key at placement
  time - the thresholds stay one decision made once by whoever owns the plate.
- **No sweep, no shape, no colour, no default motion.** Artwork is a project
  asset (AGENTS.md 14) and the taste is the captain's.
- **No default anchor and no default seam selection.** Where an element sits on a
  cut, and which cuts get one, are the gesture; an engine-supplied default would
  be a creative choice arriving one level up.
- **No reels-side renderer for `composition` mode.** `content.bookends`'
  composition mode works because step 4.06 exists on the edit_video graph to
  render it; the reels process is `build_reels` and `verify_reels` and neither
  renders anything. So a composition declaration REFUSES by name, saying that
  and naming the migration - render it yourself and declare it with `asset:`,
  or put the file where the declaration says it goes and it is measured and
  placed like any other element. Accepting it and placing nothing would be a
  vocabulary entry that does not draw, which is the defect this whole module
  was written under. Both directions are tested.
- **No `element_overlay` on the edit_video path.** `reel_build` places on a track
  it owns; `compile_manifest`'s V-track allocation is a separate question with
  its own coverage assertion, and answering it here would have been a second
  mechanism rather than one.
