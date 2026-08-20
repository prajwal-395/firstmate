# The asset library: what is GENERAL, what is PROJECT

**Status: RATIFIED, 2026-08-20.** The captain ratified the three-question
test in section 3 as written on 2026-08-20. Failing any one question is
sufficient to stop an asset - the questions are not scored together.

> "the end card honestly is something that we should just remove
> (technically its one of those things that should be a template asset
> that exists for the project, but it was something made in a previous
> trial run and is a pretty shoddy asset, so lets just get rid of it and
> make sure to create actual really good general assets and project
> assets)"
> - captain, 2026-08-17

The removal shipped with this document. What follows is the test that
keeps stranded assets out, the roster of what the ratified rule implies,
and the decisions the captain settled on 2026-08-20.

---

## 1. The one thing this settles

**An asset is GENERAL if all three of these hold. It is PROJECT if the
first fails. It is STRANDED - the state the end card was in - if the
first fails and it is stored in the engine anyway.**

**Q1. Substitution.** Swap in any of the other seven series' content.
Is the asset still correct? An asset that encodes one series' name,
numbering, ritual copy, palette or typeface fails. General assets carry
*mechanism*; series identity is a parameter passed to them, never a
literal inside them.

**Q2. Timing and geometry independence.** Does the asset assume a
particular episode's length or a particular picture geometry? Absolute
frame numbers, an assumed total duration, and normalised coordinates
authored against a frame the delivery format does not actually produce
all fail. A general asset is positioned by something the pipeline
*computes per episode* - a spine block, a safe area, a beat - not by a
number an editor typed once while looking at one finished cut.

**Q3. Reader.** Does a step in `library/steps/` read the asset and place
it, with a test asserting the placement changes the picture? This is
`AGENTS.md`'s standing rule - "A capability is only real where the
renderer reads it" - applied to assets. An asset with no reader is not a
shipped capability; it is a file.

Q1 decides *where it lives*. Q2 and Q3 decide *whether it is finished*.
A project asset that fails Q2 or Q3 is equally unshippable - it just
fails in the project folder rather than in `library/`.

---

## 2. How the 4th Wall end card scores, and why any one question stops it

The asset: `effect.timed_text_overlay` formerly in `library/templates/fourth_wall.yaml`
three timed text moments plus the `FourthWallOverlay` composition
registered in the engine's `Root.tsx`. Lifted out of a previous manual
trial run of the series (`compositions/FourthWallOverlay.tsx`, still in
the `4th-wall` project folder) and checked into the engine as the series
*defaults*.

**Q1 - Substitution: FAIL.** The three lines are
`"Night 1 - Through the 4th Wall"`, `"It's 2:16."` and
`"Night 1. Attack the day tomorrow."` - the series' name, its nocturnal
episode numbering and its closing ritual. The colours `#D4A34A` /
`#00BFFF` are the 4th Wall's Faded Brass and Ice Blue. The typeface is
Nanum Pen Script, which the captain's own direction locks to this series
and this series alone: *"Every series gets its own typeface. Typography
is not a unified system across the channel"*
(`overall_branding_creative_direction.md`). Not one of the three moments
survives substitution into PR or ER. **This is a project asset.**

**Q2 - Timing and geometry: FAIL, and measurably.** The moments start at
frames 162, 1725 and 1770 inside a composition declared
`durationInFrames={1800}`. At 30fps that is 5.400s, 57.500s and 59.000s
in a 60.000s video - one previous trial run's timeline, to the frame.
The template it sat in declares `target_duration_seconds: {min: 45, max:
90}`, so it was already wrong for most of its own series' legal range.
Against the only finished render on disk - project 001, `Pipeline_Edit.mp4`,
**54.869s** at 1080x1920:

| moment | start frame | start time | lands |
| --- | --- | --- | --- |
| "Night 1 - Through the 4th Wall" | 162 | 5.400s | in frame, but see geometry below |
| "It's 2:16." | 1725 | 57.500s | **2.63s after the video ends** |
| "Night 1. Attack the day tomorrow." | 1770 | 59.000s | **4.13s after the video ends** |

The geometry is no better. The first moment sits at `y: 0.15`, i.e. row
288 of 1920. Measured off that render at t=9.0s, the letterboxed picture
occupies rows **656..1264**; everything above 656 is the ruled black bar.
The moment would have been typeset in dead black, above the picture, and
the ruling that put the bars there (Q1, 2026-08-20) means that is the
*intended* delivery geometry, not a bug to wait out. The coordinates were
authored against a full-bleed vertical frame that the pipeline does not
produce.

**Q3 - Reader: FAIL.** Nothing in `library/steps/` imports
`generate_timed_text_overlay_props`. Step 4.06 renders MotionGraphics
segments and composition-mode bookends and nothing else. The declaration
reached no frame of any render, and every run still reported SUCCESS.
`docs/PIPELINE_PLAN.md` section 5 recorded the gap as CLOSED on the
strength of the slot existing; this PR corrects that entry.

**Three failures out of three, and each one alone is sufficient.** Q1
would have routed the artwork to `video_projects/4th-wall/` and it would
have reached the timeline the way the Lucie cards do - declared with
`content.bookends` and a project-owned `source:`, staged verbatim by
`bookend_render.py`, which is a route that already works. Q2 would have
refused the frame numbers and forced the moments to be anchored to spine
blocks, which is the only form that survives an episode of a different
length. Q3 would have refused the merge outright, because there was
nothing to demonstrate it against. The asset was shoddy *and* stranded,
and the two are the same failure: it was finished artwork for one episode
filed as engine configuration, where nothing was going to read it and
nothing was going to notice.

---

## 3. Where each kind lives, and how it reaches the picture

**GENERAL - `library/` and `remotion-subtitles/src/`.** One enumeration
per capability, an unknown name raises, a withdrawn entry records why:
`house_look.py`, `transition_vocabulary.py`, `delivery_format.py`,
`bookends.py`, `subtitle_style.py`, `music_selection_contract.py`. Engine
Remotion compositions - today `SubtitleOverlay`, `MotionGraphics`,
`TimedTextOverlay` - are general components with no series' copy inside
them. Bundled typefaces live in `remotion-subtitles/public/fonts/` with
their licence text beside them (`AGENTS.md` section 11).

**PROJECT - the project folder, outside this repo.** `compositions/*.tsx`,
`assets/*`, `brand_assets/*`. It reaches the timeline through
`content.bookends` with a `source:` path; `bookend_render.py` stages and
renders it **verbatim**, so the engine never edits a client's or a series'
artwork. This route already exists and is exercised by `lucie_client.yaml`.

**THE GREY ZONE - `library/templates/*.yaml`.** A brand template is
per-series configuration that lives in the engine, so on Q1 it is
series-specific by definition. That is fine and should stay - but it is
exactly where the end card hid, so the boundary needs a rule:

> **Rule (ratified 2026-08-20).** A brand template may name general
> components and set per-series **parameters**: a `house_look` name, a
> palette, a font name, durations, densities, a `caption_case`, a
> `delivery_format`. It may not contain **artwork** - copy the viewer
> reads on screen, or coordinates and frame numbers that describe one
> finished episode. Artwork is a project asset and is declared by
> reference, never inlined.

That single sentence is what the end card violated.

---

## 4. What exists today, and what is missing

**General assets that are real** - declared, read, and asserted:
four house looks (`house_look.py`), the transition vocabulary, the
delivery-format enumeration, the bookend mechanism, subtitle styling,
`SubtitleOverlay` and `MotionGraphics`, and Montserrat with its OFL text.

**General mechanism that is NOT real yet:** the timed-text overlay.
`TimedTextOverlay` exists and is correct; `generate_timed_text_overlay_props`
exists and is deterministic; `effect.timed_text_overlay` exists in the
schema. There is no reader, so the capability is a promise. The gap is
recorded as `library.tools.timed_text_overlay.NO_READER` and the slot is
held empty by `tests/test_timed_text_overlay.py` until it is built. All
eight series need this - intro cards and episode text overlays are Parts
2 and 3 of the captain's Series Identifier System - so it is the highest-
value general asset outstanding.

**A sketch of the missing reader, for costing only - not a design to
approve here.** Step 4.06 already renders per-spine-block overlay clips
and already renders composition-mode bookends, so it is the natural
caller. Two things have to change in the declaration for Q2 to pass: a
moment must anchor to a **spine block plus an offset**, not an absolute
frame, and its position must resolve against the **picture area the
delivery format actually produces**, not the raw frame. Then
`compile_manifest` emits the rendered clip as a V2 overlay segment like
every other overlay, and a `tests/test_vfx_delivery.py`-style assertion
proves the moments draw.

**Project assets each series owes** (from the captain's planning docs):
an intro card design, a display typeface, a caption typeface, and where
the series shows numbers a data typeface, plus a palette. Locked so far:
Through the 4th Wall (Nanum Pen Script), Moneyball (Archivo Black /
Roboto Mono / Barlow), Punch Card (Black Ops One / IBM Plex Mono /
Barlow Semi Condensed Bold). Five series unlocked. **Nothing here is
authorised by this document.**

---

## 5. What "really good" has to mean mechanically

Taste is the captain's. These are the properties an asset must have
before taste is even a question, and each one is a failure this
repository has already paid for:

1. **Its typeface is bundled, with the licence recorded.** An unbundled
   family renders on the machine that added it and substitutes silently
   everywhere else - the webfont race that `AGENTS.md` section 11 exists
   to prevent. The removed overlay named an unbundled Google font and
   `tests/test_bundled_fonts.py` never saw it, because that guard
   inspected `style.typography.font` alone. It now walks the whole
   template.
2. **It is positioned inside the picture the delivery format produces.**
   Letterbox bars are ruled behaviour, not a defect to be ignored.
3. **It is timed from the spine.** No absolute frames, no assumed total.
4. **It is declared, and a malformed declaration raises.** The bookend
   rule: a dropped declaration is a card the editor believes shipped.
5. **It has a reader and a test that asserts the picture changes.**
6. **It lives on the side of the line Q1 puts it on.**

---

## 6. The next instance of the same defect, already visible

`Root.tsx` still gives `MotionGraphics` the default props
`title: "Post A Day Challenge"`, `subtitle: "Day 001 • Getting Started"`,
`accentColor: "#00D4FF"` - one project's copy, in the engine's
composition registry. It is much less severe than the end card, because
these are Remotion Studio preview defaults that the pipeline overwrites
on every real render rather than declared series defaults that nothing
overwrote. But it is the same instinct - reach for the episode you have
in front of you when a component needs an example - and it is what the
rule in section 3 is for. Left alone here deliberately: it is a change to
what a general component previews as, which is a taste call, and this
document is not the place to make one.

---

## 7. Captain's decisions, 2026-08-20

1. **Artwork/parameter rule: RATIFIED AS WRITTEN.** The three-question test
   in section 3 is the standing bar for every new asset. Failing any one
   question is sufficient to stop an asset - the questions are not scored
   together. The rule in section 3 is now authority, not a proposal.
2. **`fourth_wall.yaml`: DELETED.** With the overlay gone it was a
   parameters-only series template no project points at, and authoring a
   `through_the_4th_wall` template was explicitly not authorised on
   2026-08-17. The captain chose deletion over keeping it as a parameter
   carrier: the series template arrives later, whole, with their
   authorisation.
3. **Per-series typefaces: PER PROJECT, not in the engine.** Font files and
   their licences belong with the project that owns the series; the engine
   stays series-neutral. Accepted cost: each project folder carries its
   own fonts and licences, and `bookend_render.py` must stage them.
4. **Build order.** The reader for timed text first, then ONE series' intro
   card as the proof, and the remaining seven only after the captain has seen
   that card in a finished render. The two follow-up tasks for this
   (`vep-timed-text-reader` and `vep-intro-card-proof`) already exist.

---

## 8. What shipped with this document

Mechanical only, all of it the captain's ruling of 2026-08-17:

- The 4th Wall end card is gone: the `effect.timed_text_overlay`
  declaration, the `FourthWallOverlay` composition and its `Root.tsx`
  registration and baked default props.
- The missing reader is recorded rather than left silent
  (`timed_text_overlay.NO_READER`), and the empty slot is held empty.
- `tests/test_bundled_fonts.py` now walks the whole template for fonts,
  so the declaration that slipped past it would not slip past it again.
- `docs/PIPELINE_PLAN.md` section 5's "CLOSED" claim is corrected.

Two triage decisions the captain no longer needs to answer are retired
with the asset: **fourth-wall-copy** (the "Day 1" vs locked "Night X"
mismatch and the baseless "1 / 100" counter) and
**overlay-generalize-vs-move**, which was a question about this asset.
