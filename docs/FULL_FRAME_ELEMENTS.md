# Full-frame elements: the architecture the graphics area builds on

*Established 2026-09-07, on the captain's four named capabilities. This lane owns
the shared path; chroma-key transitions and animated explainers build on it.*

This answers one question the other two also need: **how does a full-frame
animated element get into a reel at all?**

Everything the pipeline drew before this was an OVERLAY - a caption card, a lower
third, a counter, a timed text moment - composited over footage that keeps
playing underneath. `motion_graphics_vocabulary` owns that layer and says so in
its own words: *"a full frame of artwork is a bookend"*. On the MASTER timeline
that is true and already wired (`bookends` -> `mesh_spine` -> a V1 spine block).
On a REEL there was no equivalent, because the reels process has no
`mesh_spine`. This is that missing half.

The enumeration is `library/tools/full_frame_element.py`.
The tests are `tests/test_full_frame_element.py` and
`tests/test_reel_conformance_full_frame.py`.

---

## 1. The renderer: Remotion, not Fusion

Settled with evidence, not preference. Four measurements, each independently
sufficient.

### 1.1 Every Fusion route in this repository attaches a comp to a clip that already exists

`TimelineItem.ImportFusionComp(path)` is the ONLY way a Fusion composition
reaches a timeline here. Grepped across the tree, the call sites are
`execution/apply_fusion_comps.py` (x2), `builtin_effect_loader.py` (x2),
`fusion_macro_loader.py` and `custom_asset_bank.py` - six, and all six take a
`TimelineItem`. A full-frame element is not a treatment of a clip; it has no
carrier clip to attach to. Drawing one in Fusion would first require inventing a
placeholder clip to hang the comp on, which is the Remotion answer with an extra
step and a second renderer.

`motion_graphics_vocabulary.OUT_OF_VOCABULARY` already records the neighbouring
half of this: *"a per-clip Fusion comp sees only its own clip"*.

### 1.2 The Fusion engine here cannot draw type at all

`library/tools/fusion/effects.py` emits Background, Merge, Transform, Blur,
DirectionalBlur, Defocus, EllipseMask, BrightnessContrast, FilmGrain,
ChromaticAberration, LensDistort, ShakeTransform and friends. There is **no text
node of any kind** - no Text+, no TextPlus, no font handling. A full-frame card
is type on a ground. Adding a text vocabulary to the Fusion engine means also
adding font resolution, and `library/tools/render_fonts.py` - the module that
guarantees a declared typeface really draws the glyphs - is Remotion-side only.
A substituted face in Fusion is a valid picture of the right size that nothing
downstream can tell apart, which is the exact defect `src/fonts.ts` exists to
end.

### 1.3 AGENTS.md 5 forbids the process shape a reel build would need

> Never create a timeline and use `ImportFusionComp` in the same Python process.

`reel_build.build_reel_timeline` calls `pool.CreateEmptyTimeline(name)`. So a
Fusion-drawn card on a reel needs a SECOND process per reel, launched after the
timeline exists, re-connecting to Resolve. Nineteen reels is nineteen extra
process launches for something Remotion already produces as a file.

### 1.4 Remotion produces the carrier the timeline actually wants

Remotion renders to a FILE. A file is a media pool item. A media pool item is a
timeline clip. That is the carrier a full-frame element needs, and the reels path
already imports files this way for captions
(`build_reel_timeline`'s `pool.ImportMedia`).

Measured on this lane, project `geo-podcast`, reel 07: 1080x1920, 53 frames at
23.976, ProRes 4444, `yuva444p12le`, **alpha min = 255 on every sampled frame**,
15.5 MB, rendered in about 6 seconds.

`data/vep-graphics-fidelity/report.md` in the firstmate home already found
the renderer is not the
limit. That holds when the element IS the frame: the only thing that changes is
that the render is **opaque**, and that is a one-flag difference (see §4).

### 1.5 What this does NOT say

It does not say Fusion is the wrong home for chroma-key transitions. A
transition is a treatment of two clips that already exist, which is exactly the
shape `ImportFusionComp` fits and exactly what AGENTS.md 5 already rules
(*"Transitions go through Fusion. Both other routes are closed."*). The boundary
is: **a treatment of picture that exists is Fusion's; picture that has to be
created is Remotion's.**

---

## 2. Replace, not overlay at full opacity

The card **replaces** picture for its duration: it is a picture item on V1, not a
layer above one. Two measurements decided it.

### 2.1 The reels path cannot silence what plays under an overlay

An overlay at full opacity hides the picture and leaves the SOUND. The footage
under it goes on talking, so the viewer watches a card while half a sentence
plays. The only repair is an audio level, and the capability probe's verdict is
recorded verbatim in AGENTS.md 5:

> The scripting API cannot set an audio level, and that is a COMPLETE
> enumeration.

(`library/steps/step_6_01_render/probe_resolve_capabilities.py`: an audio
`TimelineItem` has no property dictionary at all, so every spelling of
`SetProperty` returns False.)

So overlay is not a worse answer here. It is an **unimplementable** one - the
defect would be permanent and unfixable from the reels path. A card that occupies
its own stretch of reel time cannot run over speech, because there is no speech
at those seconds to run over. That also answers the third hard question below.

### 2.2 An overlay is invisible to every check this product has

Measured against `reel_conformance_verifier` as it stood:

| check | what it reads | an opaque card on V4 |
|---|---|---|
| `check_item_count` (F4) | items on **V1 and V2** | invisible |
| `check_delivered_framing` (F12) | items on **V1 and V2** | invisible |
| `check_picture_holes` (F1) | the UNION of all picture tracks | adds coverage where coverage already existed - no change |

A capability that ships where nothing can see it is the defect class this
repository has spent a week removing (AGENTS.md 10.4). On V1 the card is inside
all three - and each of them had to be TAUGHT what it is, which is §3.

### 2.3 The master already answered this the same way

`bookends` turns a declared card into a V1 spine block *"which is what puts it
inside the coverage assertion and the manifest duration"*. This is the
confirmation, not the argument: the reels path reached the same answer from the
audio evidence before the precedent was consulted.

---

## 3. What the checks had to learn

Three checks were written when every V1 item was a frame of the master. All three
produced a **false ERROR on a correct build** - not a miss. Each was planted and
measured on the real timeline before it was fixed (§6).

| check | what it did with a card | what it does now |
|---|---|---|
| **F4** `check_item_count` | *"planned 2 picture items, found 3"*, plus the card's seconds added to whichever speaker owns V1 | takes `cards=` and excludes them by RENDER NAME; still catches a genuinely dropped clip |
| **F12** `check_delivered_framing` | *"Framing could not be read for reel_07_card_01.mov"* - the card is not in the catalog | takes `cards=`, supplies the delivery frame as the card's source size, and grades it against **FILL** rather than the project's footage `framing_intent` |
| **PLAN-MISMATCH** `check_plan_describes_timeline` | frame-exact, and the card's frames come from no keep range, so every reel with a card refuses F4 | takes `card_frames=` |
| **F5** `check_caption_coverage`, **F17** `check_mixed_speakers` | compare timeline items (shifted by the lead) against `_clip_to_reel` (body seconds) - every second of speech reads as uncaptioned by the card's length | take `lead_seconds=` |
| **F1** `check_picture_holes` | already correct - the union sees the card | unchanged, and it caught a real one-frame bug (§6) |
| **PQ-LENGTH** | measured the keep ranges only | `plan_seconds` now includes the cards: the reel is as long as the viewer watches |

**F12 is graded, not exempted.** A card could have been skipped - it is not
footage and has no framing intent. Skipping it silently is the vacuity that
function's own docstring refuses, so instead the card's declared rectangle is the
WHOLE FRAME, which is what "full-frame" means. A card someone scaled to half size
fails F12 as an error (`test_f12_fails_a_card_that_does_not_fill_the_frame`).

**F13 is the new class, and it fails in both directions:**

- a declared card the timeline does not carry (a reel quietly starting on speech
  is indistinguishable from a project that declared nothing - how the 4th Wall
  end card survived four months);
- an item shaped like a rendered card that no declaration accounts for (the
  out-of-band append `bookends.assert_no_invented_bookends` refuses on the
  master, now refused on the reels path too);
- a card at the wrong reel FRAME, or with the wrong number of frames;
- a card **above V2**, which is the replace-versus-overlay ruling made into a
  gate.

A card is identified by its RENDER NAME (`reel_NN_card_MM.mov`), never by "a V1
item that is not in the catalog" - that heuristic would make every footage-relink
gap look like a full-frame element.

---

## 4. How a declaration reaches the picture

```
project.yaml  effect.full_frame_elements        the declaration (§5)
  -> full_frame_element.resolve_declaration     PROJECT wins over brand template
  -> full_frame_element.declared_elements       normalise, or RAISE by name
  -> reel_build.plan_cards                      resolve bindings against THIS reel
  -> full_frame_element.render_reel_cards       Remotion, OPAQUE, ProRes 4444
  -> reel_build.build_reel_timeline             place on V1 at its reel frame
  -> reel_conformance_verifier                  F13 + card-aware F1/F4/F12/F5
```

Reachable through the pipeline with no new entry point: it is the `build_reels`
node of `library/processes/reels`, and the `reel.build` operation, doing more.
`manage_project.py build-reels` reaches it; so does the DAG.

**The render is opaque, and that is the one command-line difference between this
layer and the overlay layer.** Step 4.05 and `timed_text_render` both pass
`--transparent` because they are composited over picture. A full-frame element IS
the picture, so it carries its own ground rather than relying on black showing
through an alpha channel that nothing is beneath.
`test_the_render_is_opaque_and_never_asks_for_transparency` asserts the flag is
absent rather than describing it.

### The lead, and the one clock

A HEAD card pushes everything else down by its length. That arithmetic enters in
exactly three places and nowhere else:

- `reel_build.placements(..., lead_frames=N)` - the record cursor, in whole
  FRAMES so the card and the first clip abut exactly. A lead computed in seconds
  and rounded once leaves a one-frame gap, and a one-frame gap is an F1 black
  hole.
- `reel_build.reel_time(..., lead_seconds=S)` - added to the ANSWER, never to the
  membership test. `reel_opening` leaves it at zero on purpose: a card in front
  does not change which words open the talking.
- `reel_spine.spine_for_reel(..., lead_seconds=S)` - applied ONCE to the finished
  blocks rather than threaded into a dozen `reel_time` calls. A constant shift
  preserves both the ordering and the contiguity the spine contract checks.
  **SOURCE clocks are untouched** - where a word sits in the raw clip does not
  move when something is placed in front of it (AGENTS.md 6).

**A card's own position is an integer FRAME, and seconds are derived from it.**
`PlannedCard.reel_start_frame` is the authority; `reel_start(fps)` and
`reel_end(fps)` compute seconds where something asks for them. `plan_reel_cards`
takes `body_frames` and advances its cursor by each card's `duration_frames`, and
`reel_build.plan_cards` measures the body the way `placements` does - rounding
each range EDGE, never the summed seconds. The two disagree: on reel 07's ranges
the per-edge sum is 563 frames and the summed seconds round to 562. A tail card
placed from `round(seconds * fps)` therefore lands a frame away from the last
clip - late is an F1 black hole, early is an overlap - which is the same class of
mistake as the `endFrame` bug in §6, at the other end of the reel.
`test_a_tail_card_abuts_the_last_clip_on_a_range_that_does_not_round_evenly`
goes through `plan_cards` rather than `plan_reel_cards`, because the number that
can regress is the one the CALLER computes.

---

## 5. The declaration, and where taste lives

```yaml
# <project>/project.yaml
effect:
  full_frame_elements:
    - element: full_frame_card
      placement: head             # head | tail. No default; no mid-reel.
      duration_seconds: 2.2
      background: "#000000"       # required: the card IS the picture
      entrance: blur              # a motion character; absent means `cut`
      exit: fade
      font_family: Montserrat
      opening_seconds: 3.0        # the window a bound opening_line quotes
      image: lucie-logo.png       # optional: a file in brand_assets/
      image_width: 320            # optional: pixels; absent means contained
      runs:
        - bind: opening_line      # or: text: "..."
          type_role: display
          colour: "#FFB8D4"
          font_size: 84
        - bind: speakers
          type_role: micro
          colour: "#FFFFFF"
          uppercase: true
```

**The engine states nothing.** No colour, no typeface, no duration, no motion
character and no copy has a default, for the reason `series_look.py` was emptied
(AGENTS.md 10.5). The single exception is `entrance`/`exit` defaulting to `cut` -
the value meaning "nothing is drawn", legal under that rule's own carve-out and
the same reading `transition_vocabulary.CUT_TYPES` gets.

**Copy is DECLARED or BOUND, never written here.** A run carries literal `text` -
the project's own words - or `bind`s to one of three facts the pipeline already
produces: `opening_line` (the reel's first spoken words, verbatim, through
`reel_opening.opening_words`), `speakers`, `reel_number`. A binding is a
QUOTATION, not an invention. A binding that resolves to nothing REFUSES the card
by name; it never draws an empty run and never substitutes another binding.

This matters because step 3.04's handoff says titling a reel is explicitly *"not
yours"* to the model that chooses the reel. Nothing in the reels path may author
a title, which is why there is a binding table and not a title generator. If the
captain later decides who writes reel copy, that decision plugs in as a fourth
binding and nothing here changes.

**Where a card may sit** is `head` or `tail`, and the refusal of mid-reel is
structural rather than editorial: a reel is its keep ranges laid end to end, so a
card between two of them lands inside a sentence the editor made contiguous.

**A card may draw the project's own mark.** `image` names a still in the
project's `brand_assets/` (a wordmark); the plan resolves it to the staged
`public/brand/` path through the `channel_bug` shape - lazily, so cards naming
no image stage nothing - and the composition draws it above the runs. The engine
ships no artwork, so a name that resolves to nothing REFUSES the card rather
than drawing one with a hole in it. `image_width` is the mark's width in pixels
when the declaration states one; unstated, the mark is contained to the safe box
it already sits in. Position (stacked above the runs, centred) and containment
are mechanics, not taste: no palette, typeface or motion character is invented.
Pinned by `tests/test_fullframe_card_image.py`, including a still that fails
with zero wordmark pixels on a composition without the drawing node.

**Where a span may sit** is `span` and nothing else, and it is a different
declaration rather than a relaxation of the card's rule: a card with
`placement: span` is refused by name, and a mid-reel card stays refused
exactly as before. A span states no `duration_seconds` - it lasts as long
as the body it covers, measured off the keep ranges at plan time - and
carries `segments`, one per keep range, each with its own `runs`. A
segment may bind `range_line`: the words spoken in its own range,
verbatim, the quotation the ceiling lane laid per card by hand. A segment
may also name its own `image`, resolved and drawn through the same slot
the card's mark takes - one mark per rendered segment, never one for the
whole span, because each segment is the unit that renders. Where a
span plays the footage video is suppressed and the spine audio stays, so
the reel is an animated cut over its own speech.

**A span may pace its reveal off the spoken words.** `word_sync: true`
on the span declaration measures each segment's word clock out of the
transcript at plan time - the same timings the caption path already
reads - and the composition paces its `typewriter`/`mask`/`draw`
entrance off them, so the animation lands ON words instead of across
the segment's own seconds. Each cue carries segment-local seconds plus
the cumulative characters shown through that word, so a cue boundary
always coincides with a word boundary on screen. Two refusals keep that
promise: a range with no timed words has no clock, and a segment whose
runs do not read exactly the range's words (an eyebrow beside the
quotation, literal text about it) refuses rather than landing near
words. A card may not take `word_sync` at all - it covers its own
seconds, not speech seconds - and neither may a span whose entrance is
not one of the three a word clock can pace. The exit half stays on the
frame clock: it runs after the words are spoken. Pinned by
`tests/test_fullframe_word_cues.py`, including stills that fail with
identical pixels on a composition without the cue-driven node.

**How long it may hold** is the declaration's. The engine bounds it only where
the bound is mechanical: at least one frame, and at most
`MAX_CARD_SECONDS = 30.0` - the same number and the same reading as
`bookends.MAX_BOOKEND_SECONDS`, *"a card, not an act"*. **A card can never run
over speech**, because it occupies its own reel seconds; that is a property of
the replace decision, not a threshold anyone chose. Its seconds are counted into
`plan_seconds`, so PQ-LENGTH reports the length a viewer actually watches.

---

## 6. What was rendered, and what looking at it found

All evidence for this lane is in `data/vep-fullscreen-animation/` in the
firstmate home.

**The card, on real reel data.** Project `geo-podcast`, reel 07
(`number-one-on-google-invisible-to-ai`), the opening line quoted verbatim from
the ranges the build actually plays: *"So ranking number one on Google, but being
completely invisible to AI,"*. Frames in `card_hold.png`,
`card_entrance_blur.png` and `card_then_picture_f*.png`. Measured: 1080x1920,
alpha 255 everywhere, ink rows 597..1134 (centred in the safe box, which sits
above the frame's centre because the bottom inset is 320px of platform UI rail).

**The one-frame bug the read-back caught.** The first build placed the card with
`endFrame: duration_frames - 1`, on the assumption that Resolve's `endFrame` is
inclusive. Read back off the built timeline it was `[0..52)` - 52 frames for a
53-frame plan - and the first clip started at 53. **F1 reported a 1-frame black
hole at frame 52 and F13 reported "runs 52 frames, planned 53"**, on a real
timeline, before anything else noticed. `endFrame` is EXCLUSIVE in
`AppendToTimeline`, the same reading the footage placement already used. This is
AGENTS.md 5's rule paying for itself: judge a Resolve call by what it RETURNS.

**The reel, read back off Resolve after the fix:**

```
1080x1920 @ 23.9760  total_frames=2023
V1 [    0..   53) dur=  53  reel_07_card_01.mov
V1 [   53..  163) dur= 110  LC4932.MXF
V2 [  163..  600) dur= 437  LCATL0013.MXF
...
V3 [   53..  126)            sub_..._akshita_0_927518-930622_....mov
A1 [   53..  163)
```

Picture, captions and sound all start at frame 53. F1 clean, F13 clean, F12 clean
when told about the card - and F12 produces its false *"framing could not be
read"* warning when NOT told, which is the defect and its fix measured on the
same timeline.

---

## 7. The finding for the lane that owns overlays

Asked for by firstmate: PR #602 built typewriter, glitch and a per-digit counter
and composited them over a **generated fractal**. Composited instead over a real
reel frame at real size (`data/vep-fullscreen-animation/pr602_over_real_*.png`
in the firstmate home),
they read very differently:

| frame | what is on screen | ink ON PICTURE |
|---|---|---|
| 15 | title_lockup mid-typewriter, frame_accents, progress_bar | **0.0%** |
| 45 | title_lockup, digit_counter | 45.4% |
| 145 | quote_card glitch entrance | 76.3% |
| 200 | quote_card, chrome | 45.3% |

The reason is not the elements. This project declares
`style.framing_intent: 0.0`, so a reel delivers the 3840x2160 source fitted into
1080x1920 - **1080x607.5, rows 656..1263, 31.6% of the frame** and black
elsewhere (`library/tools/reel_framing.py`). Every overlay anchored to the
DELIVERY frame's safe area therefore lands in the letterbox: the corner accents
frame the black, the progress bar sits 300 rows below the picture, and a
`top_centre` title floats in a void.

The elements are fine. What is missing is a **picture-area enumeration** -
`timed_text_overlay` already records that there is none and makes each moment
state its own `y` to work around it. That is the owed work, and it belongs to
whoever owns the overlay layer, not here. It is recorded so the next lane does
not re-derive it from a fractal.

---

## 8. What the next two lanes inherit

- **The renderer boundary** (§1.5): picture that has to be CREATED is Remotion's;
  a treatment of picture that EXISTS is Fusion's.
- **The placement shape**: a full-frame thing is a picture item on V1 occupying
  its own reel seconds, and the lead arithmetic lives in exactly three functions.
- **The roster shape**: `full_frame_element.ROSTER` is two entries: a
  card (`full_frame_card`, head-or-tail, declared duration capped at
  30s) and a span (`full_frame_span`, one segment per keep range,
  covering the body's own seconds - the word the seven-back-to-back-cards
  build of `docs/ANIMATED_REEL_CEILING.md` was missing).
  `assert_roster_is_well_formed` forces any new entry to declare its refusals
  (`never`), its axes and its reachability. **`reachable_now` is set only once an
  entry genuinely renders and has been read back** - the card's was
  flipped after §6; the span's after `tests/test_full_frame_span.py`
  rendered two segments and read per-segment ink back off the frames.
- **The declaration shape**: project.yaml wins, the engine states no taste, and a
  malformed declaration RAISES rather than being dropped.
- **F13**: the class for "this element is missing, extra, mis-placed or
  mis-sized", already wired into `verify_reel` and already failing both ways.

A chroma-key transition and an animated explainer are both *picture that has to
be created*. If either one takes the whole frame for a stretch, it is a
full-frame element and belongs in this roster; if it sits over footage that keeps
playing, it belongs in `motion_graphics_vocabulary`. The line between the two
enumerations is exactly that question, and `OUT_OF_VOCABULARY` in each module
names the other.
