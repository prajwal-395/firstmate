# What an animation-first reel is made of

A frame-by-frame read of the reference the captain sent on 2026-09-08,
`https://www.youtube.com/shorts/C-yZlQXN3rQ`, and the vocabulary it implies.

This document is a READ OF ONE PIECE, not a house style. Every number in it is a
MEASUREMENT of that piece, quoted so a later reader can check it. None of them is
a default anywhere in this engine, and none of them may become one - the captain's
rule of 2026-09-08 is "no hardcoded values, there are no house glow looks, there
are no settled house grain or anything". What generalises is the *vocabulary*: the
kinds of move the piece is made of. What does not generalise is any magnitude.

## 0. How it was measured

The URL was opened and played with `chrome-devtools-axi`; headless Chrome pinned
the adaptive stream at 240x426, which is too coarse to read type or judge focus,
so the same URL was pulled at its native size and measured directly with ffmpeg
and numpy. 1080x1920, 30 fps, 14.04 s, 421 frames. Word-level timings come from
the video's own caption track, so every "the picture leads the word by X" below is
two measured numbers subtracted, not an impression.

## 1. The spoken line, and what the picture does under it

```
0.64  A true footballer          picture: EMPTY canvas, then a card fades up
1.44  doesn't just play          picture: still. Nothing moves for two seconds.
2.56  with his feet.
3.20  He plays with his mind.    picture: card SPLITS; a second object rises between
4.48  He thinks quickly,         picture: composition scales down
5.60  has vision,                picture: an EYE, defocus-through at 5.00
6.64  and uses his
7.20  intelligence               picture: value INVERTS to black at 7.13
8.00  to control
8.40  not only his space,        picture: a light cone grows and carries the words
9.76  but the entire field.      picture: camera pulls back, a football pitch appears
11.1  (silence, 2.9s)            picture: the cone sweeps, narrows, stops
```

**Two-thirds of the picture events are illustrations of a single noun, and each
one arrives BEFORE its noun is spoken.** Measured leads:

| picture event | at | the word it illustrates | word at | lead |
|---|---|---|---|---|
| cut to blank canvas | 0.333 | "A" (speech starts) | 0.640 | 307 ms |
| card splits in two | 3.000 | "He plays" | 3.200 | 200 ms |
| defocus-through to the eye | 5.000 | "vision" | 5.839 | 839 ms |
| value inverts to black | 7.133 | "intelligence" | 7.200 | 67 ms |
| light cone starts growing | 8.333 | "not only" | 8.400 | 67 ms |

Nothing lands on the word. The picture arrives, the voice confirms it. This is the
single most transferable rule in the piece and it is the exact opposite of what a
naive "start the graphic at the word's start frame" implementation does.

The same rule governs the type. Each word of "he play with is / mind" fades up over
2 frames STARTING ~100 ms before its spoken onset, so it reaches full opacity ON the
syllable:

| run | fade starts | full | spoken |
|---|---|---|---|
| "he" | 3.233 | 3.233 | 3.20 |
| "play" | 3.300 | 3.367 | 3.36 |
| "with" | 3.500 | 3.567 | 3.60 |
| "is" | 3.700 | 3.767 | 3.84 |
| "mind" | 3.900 | 4.033 | 4.00 |

**A word completes on its syllable. It does not begin there.**

## 2. There are three shot changes in fourteen seconds, and only one is a cut

Scene-score peaks over the whole piece: 0.333 (0.49), 5.000 (0.27), 7.133 (1.00).
Everything else sits between 0.05 and 0.14 - continuous motion, not cuts. The three:

- **0.333 - hard cut, on a value change.** A held still (the finished poster, with
  annotation callouts, as its own thumbnail) cuts to an empty canvas. The frame is
  then EMPTY for 200 ms before anything appears.
- **5.000 - defocus-through.** Not a cut. The outgoing composition blurs out over
  ~5 frames and the incoming subject blurs in over ~7. Measured: sharp at 4.833,
  soft at 4.967, new subject fully soft at 5.033, sharp again by 5.400. It lands in
  the *gap* between "quickly," and "has" - on the comma, not on a word.
- **7.133 - hard cut, on a value inversion.** Mean frame luma goes 0.525 to 0.116
  in one frame: the light table becomes a black one. The incoming subject is again
  out of focus and racks in.

That is the whole transition vocabulary. No dissolves, no wipes, no whips, no
zoom-transitions, no flashes. Two hard cuts and one focus pull.

## 3. What holds the frame when nobody is on screen

Nothing in this piece is a talking head, and the frame is never bare. Four things
are doing that work, and they are all properties of the GROUND rather than of any
element on it:

1. **One lit surface, for the whole piece.** Every subject sits on the same
   textured plane, lit from the top-left, casting a soft shadow down-right. The
   light section measures RGB ~(205,202,201) at centre; it is neutral to within
   3/255, and the visible texture is a fine canvas grain, not a gradient.
2. **A vignette that is much stronger vertically than horizontally.** Measured on
   the empty frame at 0.45 s, down the centre column: 93 at the top edge, ~197 in
   the middle band, 108 at the bottom - a ratio of 0.47. Across the centre row:
   162 at the left edge, ~220 mid - a ratio of 0.74. The frame reads as a *table
   under a lamp*, not as a page. This is what makes an empty 200 ms feel composed.
3. **A persistent graphic that survives every scene change.** A thin scored guide
   line runs across the surface and is present in all three sections, in the same
   world position, at the same weight. It is the evidence that the eye at 5.0 s and
   the brain at 7.2 s are on the *same table*.
4. **Shallow depth of field.** Top and bottom of frame are softer than the middle
   in every shot. The subject is the only thing fully sharp.

## 4. The camera never stops

There is one camera and it moves continuously for 14 seconds: scale, rotate,
translate, and focus. Every "shot" is a camera STATE, not a cut. The clearest
stretch is 9.5-11.0 s, where a single continuous pull-back takes the frame from a
disc filling half the height to a disc a tenth of that, and in doing so reveals
that the black ground has football-pitch markings on it - which is the payoff of
"but the entire field".

Motion energy (mean absolute inter-frame difference, x1000, per 0.5 s, duplicate
frames excluded):

```
 0.0- 0.5   75.2  #####################################   cut + card fades up
 0.5- 1.0   13.7  ######
 1.0- 1.5    5.1  ##
 1.5- 2.0    4.0  ##                                      REST - two seconds
 2.0- 2.5    3.6  #                                       of near-stillness
 2.5- 3.0   33.3  ################                        the split
 3.0- 3.5   25.9  ############
 3.5- 4.0   17.2  ########                                the type builds
 4.0- 4.5   27.0  #############
 4.5- 5.0   50.1  #########################               accelerating into the pull
 5.0- 5.5   24.7  ############
 5.5- 6.0   17.1  ########
 6.0- 6.5   18.9  #########                               REST on the eye
 6.5- 7.0   18.9  #########
 7.0- 7.5   82.4  #########################################  the inversion
 7.5- 8.0   18.7  #########
 8.0- 8.5   28.2  ##############
 8.5- 9.0   29.9  ##############                          the cone grows
 9.0- 9.5   29.7  ##############
 9.5-10.0   35.2  #################
10.0-10.5   44.6  ######################                  PEAK - the pull-back
10.5-11.0   38.9  ###################
11.0-11.5   15.3  #######
11.5-12.0    9.9  ####
12.0-12.5    5.5  ##                                      a long decelerating
12.5-13.0    2.8  #                                       settle to a full stop
13.0-13.5    0.9
13.5-14.0    0.9
```

Three things to take from that curve:

- **It rests where the voice is densest.** Six words of setup ("doesn't just play
  with his feet") play over the stillest two seconds in the piece. The picture does
  not decorate the setup; it waits for it.
- **It peaks 0.3 s after the last word and holds for three seconds of silence.**
  A fifth of the runtime has no voice at all. That silence is where the picture
  finishes its sentence.
- **It ends at zero.** The last 1.5 s are a still frame. The reel resolves to an
  image you could print.

Frame luma traces the same arc independently: 0.116 at the inversion, rising to
0.234 at 9.5 s (the brightest moment of the dark section, exactly on "space"),
falling to 0.048 by 13.0 s. Brightness is doing what a music swell does.

## 5. Motion on twos

**51% of consecutive frame pairs are identical, and 205 of the 214 duplicates fall
on the same parity.** The piece is animated at 15 fps inside a 30 fps container.
That is what makes it read as *animation* rather than as a moving photograph, and
it is one integer, not a look.

## 6. How objects enter and leave

- **Entrances are fade + settle-from-larger.** The first card appears at 0.567 at
  near-zero opacity and slightly oversized, and reaches full opacity and final size
  by ~1.0 s: a ~0.45 s ramp with the scale easing IN toward rest, not out of it.
- **An object leaves by being replaced through a defocus, or by being cut away
  from on a value change.** Nothing slides off, nothing pops out.
- **One object becomes two.** The photo card splits down its centre at 3.0 s and
  the halves part continuously for the next 1.5 s, opening a hole that the next
  subject rises into. The split is not a transition between shots - it is a
  transformation *within* one.
- **Nothing is ever removed while the camera is still.** Every departure happens
  under a move.

## 7. How text behaves

- **One typeface, two weights, and contrast carries the emphasis.** A lead-in run
  is set in a medium weight at mid-grey (measured ~89-110/255); the payload word is
  set in a black weight at near-0 and roughly 3.3x the lead-in's x-height.
- **The pair is composed, not stacked.** "mind" starts under "with", not at the
  lead-in's left margin, and the dot of its `i` rises into the lead-in's row -
  negative leading, set as one optical unit.
- **The lead-in stays.** It is not replaced by the payload; the sentence accumulates.
- **Type is masked by a moving light shape.** From 8.5 s the words sit inside a
  growing cone of light. The type stays upright while the cone rotates around its
  apex, and the cone's edge SLICES the letterforms - at 9.43 s the reader sees
  "spac", at 9.53 s "sp", and by 9.63 s the words are gone. The type is not
  animated out; the light leaves it.
- **Type never touches an edge, and never sits on a bar or a plate.** It sits on
  the lit ground with the same shadow the objects have.

## 8. What the palette is doing

The piece is **monochrome for 97% of its runtime**. Mean per-pixel saturation is
0.008-0.027 from 0.33 s to the end. The only colour anywhere is in the first
0.33 s - the held thumbnail, whose one coloured object measures RGB (212,133,113),
a desaturated coral, against a neutral ground. Mean saturation of that frame:
0.098, an order of magnitude above everything after it.

So colour is used exactly once, in the frame whose job is to be a thumbnail, and
then withdrawn. The rest of the piece separates its elements by VALUE - light table
then black table, grey lead-in then black payload, dark subject on light ground
then light subject on dark ground. The mid-piece inversion (0.525 to 0.116 in one
frame) is only available because nothing else is competing on colour.

## 9. The gap this opens against what we render today

`remotion-subtitles/src/compositions/` has five compositions. Four of them
(`SubtitleOverlay`, `MotionGraphics`, `TimedTextOverlay`, `BrandMotion`) are
overlays by construction - `MotionGraphics`'s own docstring says "every element
this composition draws is decoration at an edge, so every one of them is positioned
from here [the safe-area insets]". The fifth, `FullFrameCard`, does own the whole
frame, but what it owns is a *card*: one flat background colour, a stack of text
runs, and one optional centred still.

Against the read above, the missing vocabulary is:

| # | gap | what exists today | why it matters |
|---|---|---|---|
| 1 | **A camera.** Continuous zoom / pan / roll / focus over the whole segment, keyframed. | Nothing. Element entrances are per-element ramps at fixed anchors. | §4. Every shot change in the reference is a camera state. Without one, "animation" can only be objects appearing and disappearing - which is what "3 small components" looked like. |
| 2 | **A ground with depth.** Declared surface colour + optional texture + declared vignette. | `FullFrameCard.background`, one flat colour. | §3. This is the whole answer to "what holds the frame when nobody is on screen". |
| 3 | **Whole-frame focus.** A defocus that applies to the picture, so a transition can happen THROUGH it. | A `blur` *entrance* on one element (8px). | §2. The only non-cut transition the reference uses. |
| 4 | **A world, not a grid.** Subjects placed at surface coordinates, sharing one camera, casting one direction of shadow. | Nine anchors and a row index, measured from the safe area. | §3, §4. An anchor grid cannot express "the eye and the brain are on the same table". |
| 5 | **A shape that both draws and clips.** A light form that is rendered AND used as the mask for content inside it. | Nothing. `mask` entrance is an axis-aligned `inset()` wipe on one element. | §7. The reference's signature move. |
| 6 | **Word-leading reveal.** Text whose ramp COMPLETES on the spoken word. | `FullFrameCard.wordCues` shows the cues whose `start` has passed - i.e. it begins at the word. | §1. Measured 100 ms lead on every run in the reference. |
| 7 | **A frame quantiser.** Render motion at a declared step. | Nothing; everything is per-frame smooth. | §5. One integer, and it is the difference between animation and a moving photograph. |
| 8 | **A subject that transforms.** One object splitting into parts that then move independently. | Nothing. | §6. |

`MotionGraphics` already has the *element* vocabulary this does not duplicate:
comparison bars, digit counters and counter rolls, beat accents, list builds, step
counters, progress bars, pointer annotations, eight entrance characters
(`fade`/`slide`/`scale`/`mask`/`draw`/`blur`/`typewriter`/`glitch`/`flip`) and a
typewriter with per-character cursor. None of that is missing and none of it is
re-implemented. What it lacks is a picture to sit in.

## 10. Which renderer this needs

**Remotion alone reaches this bar.** Every primitive in §1-§8 is a CSS transform,
a `filter: blur()`, a `clip-path`, a gradient, an opacity ramp, or
`Math.floor(frame / n) * n`. Nothing in the reference needs a shader, a physics
solver, a pointer or a scroll.

- **HyperFrames is not earned by this piece.** `data/vep-remotion-hyperframes-as-tools/report.md`
  (2026-09-08) found it drivable and Apache 2.0, and named shader transitions as
  the thing Remotion has no equivalent for. The reference contains no shader
  transition. Its hardest single effect - type clipped by a rotating textured cone
  of light - is a `clip-path: polygon()` plus a masked gradient, built here in
  about forty lines. The report's recommendation ("revisit only when a graphic
  exists that Remotion cannot draw") stands; this is not that graphic.
- **Fusion is not needed either, and there is exactly one thing it would improve.**
  CSS `blur()` is a gaussian; a real lens defocus has circular bokeh. At 15 fps on
  a matte surface the difference is not visible, and every §2 defocus measured here
  is a soft matte surface. If a future piece defocuses through specular highlights,
  Fusion's `Defocus` node is the honest answer and the pipeline already has that
  route (AGENTS.md 5). Today it would be a second renderer earning nothing.

## 11. What this piece is NOT evidence for

- It is a graphic-design short about building a poster, so its guide lines and its
  "here is the finished thing first" opening are that GENRE's conceits. What
  transfers is "a persistent graphic survives every scene change" (§3.3) and "the
  first frame is the thumbnail" - not scored construction lines specifically.
- Its transcript is loosely and incorrectly spelt on screen ("he play with is
  mind"). That is not a technique.
- Every magnitude quoted here - 100 ms lead, 15 fps step, 0.47 vignette ratio,
  0.45 s entrance, 3.3x type ratio - belongs to THIS piece. They are recorded so a
  declaration can be argued against a measurement, not so anything can default to
  them.

## 12. What was built against this read, and what it rendered

`remotion-subtitles/src/compositions/StagedScene/index.tsx` answers seven of the
eight gaps in §9. The eighth needs no feature: one object splitting into parts is
two `image` layers with the same `src`, complementary `clipPath`s and diverging
`x` tracks. `tests/test_staged_scene.py` pins it, in both halves - a free source
half that fails if any magnitude in this document is ever baked in as a default,
and a delivery half that renders through the same `npx remotion render` the
pipeline uses and measures the frames.

The demo passage: **001, `clip_017` 30.073-40.120**, the captain's own voice -
"i almost didn't do this again ... i've quit every single day ... this is the last
shot i got". 1080x1920, 30 fps, 346 frames, 11.53 s. The picture is one card of
the captain that becomes a cascade of nine on a lit surface, a camera that travels
down the cascade, and a collapse back to one card under a spotlight.

Measured on the delivered file, against the three claims this document makes:

| claim | reference | the render |
|---|---|---|
| motion on twos | 51% identical pairs, 205/214 one parity | 59%, 173/203 one parity |
| the camera ends at zero | last 1.5 s a still | last 45 frames, 0.059/1000 |
| brightness arcs to the payoff | peak luma 0.234 | peak 0.213, on the last card |

"Identical pairs" counts consecutive frames whose mean absolute luma difference
is under 0.0008 at 180x320; a looser threshold reads higher (firstmate measured
64% on the same file, and the same 173 on one parity). "Peak luma" is the MEAN
of the frame, not its brightest pixel.

**Where every declared value came from.** Nothing below is in the renderer; all of
it is in the demo's own declaration, which is where taste is allowed to live.

- **Ground colour** - MEASURED. Median RGB of `clip_017` at 34.5 s is (27,25,18);
  the ground is that colour, lifted to (24,22,16) so the key light reads against it.
- **Key light and spotlight colour** - MEASURED. The footage's brightest decile is
  (158,138,118): the light on the surface is the light already in the captain's shot.
- **Type colour** - MEASURED, from the same two bands: a grey lead-in and a payload
  near the footage's own highlight.
- **Vignette SHAPE** - MEASURED from the reference (§3.2): vertical falloff stronger
  than horizontal, so `radiusY` < `radiusX`. Its strength is the declaration's own.
- **`holdFrames: 2`** - MEASURED from the reference (§5). One integer.
- **Every reveal time** - MEASURED. The passage's own word onsets from 001's
  `speech_sequence`, rebased to zero. The declaration states the word's onset and
  the RULE inside the renderer puts the ramp before it, which is §1.
- **Typeface** - Montserrat, the one font this repository bundles (AGENTS.md 11),
  at weights 500 and 900.
- **Nine cards, the cascade angle, the camera track, the two silences the big moves
  sit in** - the declaration's own composition. Not measurements, and not in the
  engine.

**Honest defects in this render.** The reference rests for two full seconds under
its setup clause; this one never fully rests early (0.5-2.5 s measures 8-16 against
the reference's 4-14 falling to 3.6). And the biggest picture move here lands on the
collapse at 8.0 s rather than inside the first silence at 6.3-7.3 s, where the
reference would put it - the pull-back happens there but reads smaller than intended
because a wide dark frame produces small pixel deltas.

## 13. The next gap, and it is not in the renderer

Firstmate's read of the §12 render, 2026-09-08: *"the picture is still talking-head
footage arranged in cards rather than drawn material carrying the frame, and there
is one gesture across 11.5 s where the reference makes three pictures in fourteen
seconds."* That is right, and it is a finding about the vocabulary rather than
about `StagedScene`, which can stage any number of subjects on any number of
grounds. Nothing in the composition limits a piece to one picture. What limits it
sits upstream, and it has three parts.

**13.1 Subject supply. The picture can only restate one object, because that is
all the material there is.** The reference gets three pictures out of three
subjects, and each subject was chosen because it illustrates a different noun -
a brain for "mind", an eyeball for "vision", a pitch for "field". The engine's
only picture material today is frames of the person speaking, so a staged scene
can compose them, multiply them and light them, but it cannot show a second idea.
Nine copies of one face is one idea repeated, which is exactly what §12 renders.

**13.2 Isolation, which is the specific thing that reads as "footage in a card".**
Worth being precise, because "drawn versus photographic" is not the line: the
reference's subjects are photographs too. Its DRAWN elements are the ground, the
guide line, the light cone and the type - the same set §12 draws. The difference is
that its brain and its eyeball are **cut out and composited onto the staged
surface**, casting the staging's own shadow in the staging's own light, so they are
objects on the table. Its portrait card, by contrast, is a rectangular photograph
with its background intact under an octagon mask - the same class as §12's cards,
and the reference uses it exactly once, as one element of one picture.

A subject that keeps its own background and its own lighting reads as a clip in a
frame no matter how well the camera moves around it. The capability that would cut
one out of the captain's own footage is `object_segmentation` (step 1.06), which is
WIRED to nothing - see `docs/SUBJECT_MASKING_MEASURED.md`. Its being unwired says
nothing consumes the masks, not that the capability is gone (AGENTS.md 3). A staged
scene is a consumer.

**13.3 Cadence, and this half is a planning gap rather than a supply one.**
Measured on the reference, after the hook still: three staged pictures across
13.71 s, at 4.67 s, 2.13 s and 6.91 s - **one new picture every 4.57 s**. The §12
render is one picture across 11.53 s, 2.5x slower. Some of that is 13.1: with one
object there is less to cut to. But not all of it. A second ground state, or a
wholly different staging of the same subject, was available and was not declared -
the reference changes its ground once (light table to black pitch) and gets a whole
picture out of that alone. Whatever plans a staged scene has to be asked for a
NUMBER of pictures over a passage, not one scene per passage, or it will keep
producing a single gesture however good the camera is.

`docs/VISUAL_COMPONENT_ANATOMY.md` takes 13.1 and 13.2 further: it pulls the
three components above apart into what each thing IS, what it is made of, how it
enters, what it does, how it leaves and what the next one does with the space -
and answers the supply question with an inventory of what is on this machine
today. `library/tools/visual_component_plan.py` is the per-component plan of
action that follows from it.

**Where this is NOT fixed.** Not in `StagedScene`, and not by giving it a
transition vocabulary - the reference's shot changes are two hard cuts and a
defocus, all three of which it already draws (§9 gaps 1 and 3, both built). It is
fixed by having more than one thing to show, cut free of its own background, and by
a planner that is asked for several pictures rather than one.
