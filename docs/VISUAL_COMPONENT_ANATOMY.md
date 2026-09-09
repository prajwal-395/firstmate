# What a visual component actually is

The captain, 2026-09-09, on what the span planner produced:

> "im not trying to animate text on the screen, im trying to give
> something visual for the viewer to look at that is paired alongside the
> audio. and i think you understood that in concept, but not to the
> fidelity i want. i think in some aspect it requires creating a plan of
> action for each component you want to have animated throughout the
> video"

The span planner (PR 774) reads measured word windows and decides what
each beat SHOWS. For reel 40 it produced fourteen beats - "raised hands
asking the room", "five sources that cannot be reached", "a bad
information ecosystem" - each anchored to real spoken words with a lead.
That planning is right and is not what this document changes. What
reached the screen from it was TYPE: the noun set as words, animated in.
This document is about the other thing - the object the viewer looks at
while the voice explains - and about the per-component plan of action
that drives one.

Companion to `docs/ANIMATION_FIRST_REFERENCE.md`, which measures the
captain's reference frame by frame, and `docs/SPAN_RENDERER_CAPABILITY.md`,
whose blocker 6 is the mechanical statement of the same complaint. The
implementation is `library/tools/visual_component_plan.py`.

---

## 1. The six questions

A visual component is not "a graphic". It is a thing with a life over a
few seconds, and six answers describe it completely:

1. **What is the thing?** The object on screen, named as a noun.
2. **What is it made of?** Drawn marks, a photograph, a cut-out subject,
   a project asset - the material question, and §4 says which of those
   we can actually get.
3. **How does it enter?**
4. **What does it do while the voice continues?** This is the answer the
   captain says was missing. A component that only enters is a still.
5. **How does it leave?**
6. **What does the next one do with the space it left?**

"An icon appears" answers one of six. Every account below answers all
six, because that is the bar the captain set.

---

## 2. The reference, taken apart

Watched again at native size on 2026-09-09 with `chrome-devtools-axi`
(the 2026-09-08 read was pinned at 240x426 by headless streaming; at
480px wide the subjects are legible). Three components in 14.04 s.

### 2.1 The portrait card - 0.567 to ~4.5 s

1. **The thing.** One rectangular photograph of a footballer, masked to
   an octagon with a thin bevelled border, sitting flat on a lit canvas
   surface with a soft shadow down-right.
2. **Made of.** A photograph with its own background intact, masked and
   desaturated. This is the reference's ONLY element of that class and
   it uses it once.
3. **Enters.** Fade up from near-zero opacity while slightly oversized,
   easing to rest over ~0.45 s. Nothing slides.
4. **Does.** Nothing at all for two seconds - the stillest stretch of the
   piece, under the six words of setup. Then at 3.00 s it **splits down
   its own centre** and the two halves part continuously for 1.5 s,
   travelling until each is cropped by a frame edge. The split is not a
   cut between shots; it is a transformation inside one, and the hole it
   opens is the entrance for the next component.
5. **Leaves.** It never leaves. The halves are still there at 4.6 s,
   pushed off the sides, and the camera pulls away from them.
6. **The space.** The next component **rises into the hole the split
   made**. The space was not vacated and then filled; it was
   manufactured by the departure itself.

### 2.2 The brain - 3.0 s onward, and it survives to the end

1. **The thing.** A sculpted brain, lying on the same canvas, lit from
   the same top-left key, casting the same shadow.
2. **Made of.** A photograph **cut free of its own background**. This is
   the whole difference between a subject and a clip in a frame. Its
   pixels are photographic; its edges are the object's.
3. **Enters.** Rises from behind the parting card halves, scaling up
   from small as the composition scales down around it.
4. **Does.** It is replaced through a **defocus** at 5.00 s (the eye, §2.3)
   and comes back at 7.13 s on a **value inversion** - the light table
   becomes black, one frame, luma 0.525 to 0.116. From 8.33 s a growing
   cone of light reaches it, and at 9.6 s it is sitting in a disc of
   light on the black ground with the cone striking it from upper-left.
   Then the camera pulls back for 1.5 s until the brain is a tenth of
   its height and the black ground turns out to have football pitch
   markings on it.
5. **Leaves.** It does not. The piece ends on it, still, for 1.5 s.
6. **The space.** N/A - it is the last picture.

### 2.3 The eyeball - 5.0 to 7.13 s

1. **The thing.** One eyeball on the canvas, at rest, filling a third of
   the frame height.
2. **Made of.** Again a cut-out photograph, on the same surface, with
   the same shadow and the same persistent scored line running behind it.
3. **Enters.** Through a **defocus-through**: the outgoing composition
   blurs out over ~5 frames, the eyeball blurs in over ~7, racking sharp
   by 5.40 s. It lands in the gap between "quickly," and "has" - on the
   comma, not on a word.
4. **Does.** Almost nothing. It holds for two seconds - the motion trace
   sits at 18-19/1000 while the voice says "has vision, and uses his".
   The camera keeps drifting; the object does not.
5. **Leaves.** Hard cut on a value inversion at 7.13 s.
6. **The space.** The brain returns to it, on a black table instead of a
   white one. Same world, new ground.

### 2.4 What the three have in common

- **Every one of them is an object on a surface, not a picture in a
  box.** The ground, its texture, its vignette, the persistent scored
  line and one shadow direction are shared by all three, which is what
  makes them read as three views of one table.
- **The picture always leads its noun.** Measured leads: 307, 200, 839,
  67, 67 ms. Nothing lands on the word.
- **Nothing is ever removed while the camera is still.**
- **The transition vocabulary is three items**: hard cut on a value
  change, defocus-through, and transformation-in-place (the split).
- **Two of the three are drawn/composed material, none is footage.** The
  one photographic card is used once and is the weakest element of the
  piece by exactly the amount its background is still attached.

---

## 3. Our own beats, taken apart the same way

Four of reel 40's fourteen, from `/tmp/vep-new-reel-batch/span_plan.json`.
Written the way the reference's would be if a Vox editor had them.

### 3.1 `shows: "five sources that cannot be reached"`

Anchor "five of them just can't even be accessed", 38.392-40.879 s.

1. **The thing.** Five marks in a row on the surface.
2. **Made of.** Drawn marks - five discs. Nothing photographic, nothing
   sourced.
3. **Enters.** They land one at a time, left to right, each rising into
   the row and reaching full opacity in its own slice of the window; the
   first arrives 300 ms before the word "five" and the fifth is fully
   there as "just" finishes.
4. **Does.** On "can't", 300 ms early, each in turn **drops below the
   line and goes to the value of the room** - the reaching failing, one
   source at a time, completing exactly as "accessed" ends. The count is
   the spoken count and the failure is the spoken failure.
5. **Leaves.** It does not, in this beat: the next beat is 2.6 s later
   and the failed row is the thing the viewer is looking at while the
   sentence resolves.
6. **The space.** The next beat ("a bad information ecosystem") gathers
   this row inward - the five failures becoming the one thing that
   caused them.

**This is the one that was built and rendered.** §6.

### 3.2 `shows: "three sources contradicting each other"`

Anchor "three of them are contradicting each other", 34.037-36.445 s.

Same component, three parts, different acts: they land on "three of
them", then on "contradicting" they **do not fail - they move against
one another**, two drifting one way and one the other, so the picture is
disagreement rather than absence. The row it leaves is the row 3.1's
five parts join.

**What is missing to build it:** an act like `oppose` on
`countable_set`, which the roster does not have. It is a small addition
and it is deliberately not in yet - a vocabulary written from one beat
is a vocabulary written around one render.

### 3.3 `shows: "bad information going in"`

Anchor "bad information", 30.987-31.690 s.

1. **The thing.** Two places and the things crossing between them.
2. **Made of.** Drawn marks - two endpoint discs and three travellers.
3. **Enters.** Both ends arrive together before "bad", so the sentence
   has its two nouns before anything moves.
4. **Does.** Three marks cross from source to sink, staggered, and on
   arrival **the sink takes the arrived colour** - the consequence drawn
   on the thing that received it, not on the thing that sent it.
5. **Leaves.** The travellers vanish on arrival; the two ends stay.
6. **The space.** Beat 12 ("pulling from that ecosystem") is the same
   picture read backwards, which is why it wants the same component with
   its endpoints swapped rather than a new one.

`flow_between` in the roster, and it has a builder.

### 3.4 `shows: "a bad information ecosystem"`

Anchor "bad information ecosystem", 43.787-45.634 s.

This is the beat the vocabulary **cannot honestly serve today**, and
saying so is more useful than serving it badly. An ecosystem is not a
count, not a flow and not one object; the reference would draw a
world - a ground with things on it at different depths - and reveal it
with a camera pull-back, exactly as it does for "the entire field".
`StagedScene` has the camera and the ground for that. What it does not
have is anything to put ON the ground that is not a disc, a rule or a
line of type. See §4.

---

## 4. Where the visual material comes from

**The plain answer: composed from primitives the renderer already has,
and only that. Everything depictive has no supply on this machine
today.**

What exists, verified 2026-09-09:

| source | what it holds | usable as a component? |
|---|---|---|
| `StagedScene` primitives | image, text, light cone, disc, rule, plus a ground with texture and vignette and a camera over all of it | **Yes.** This is the supply. |
| the project's `brand_assets/` | geo-podcast has exactly one file, `TV 4k.png` | As a `held_card` only, and it is a TV frame, not a subject |
| the captain's `assets i used/vfx/` | `Super 8 centered (multiply)`, `Film Burn 9`, `01_NOISE`, `Sparks Small`, `Stomp 3`, `DynamiXXX 37`, `Mixed Media Opener` - all ACIDBITE plates | **Grain and light, not subjects.** They are full-frame multiply/screen textures; a component cannot be made of one |
| the footage | 4K interviews of two people talking | Frames of a face, **with the background attached** |
| an illustrator, an icon set, image generation | none | - |

So the vocabulary that works today is **diagrammatic, not depictive**.
Drawn marks can carry quantity, position, direction, containment,
comparison and failure. They cannot carry a brain, an eyeball or a
football pitch. Our own beats happen to be unusually well suited to the
diagrammatic half - "three of them", "two of them", "five of them" are
literally countable nouns the speaker enumerates - which is why 3.1 and
3.3 render and 3.4 does not.

**Three ways the depictive half could be supplied, and what each costs.**
This is a finding, not a plan: none of them exists today and picking one
is the captain's call.

1. **Cut the speaker out of their own footage.** `object_segmentation`
   (step 1.06) produces masks and is wired to nothing
   (`docs/SUBJECT_MASKING_MEASURED.md`); a staged scene is a consumer for
   them. This is the cheapest by a wide margin - the capability is
   already in the repository. It buys ONE subject: the person speaking,
   cut free and lit by the staging. That fixes the "footage in a card"
   read (`ANIMATION_FIRST_REFERENCE.md` §13.2) and buys no new nouns.
2. **A project-owned illustration library.** Artwork is a project asset
   under AGENTS.md 14; a series that wants brains and eyeballs supplies
   them, declares them, and the engine stages them verbatim. Nothing to
   build beyond a `held_card`/`subject_on_surface` builder - and nobody
   has drawn the artwork.
3. **Generate them.** Not proposed. It would put an image model in the
   engine's picture path, and the first thing it would have to invent is
   taste (AGENTS.md 10.5).

Until one of those lands, `subject_on_surface` stays in the roster and
is **refused by name** as `material_not_supplied` rather than quietly
substituted with a face in a rectangle. That refusal is the honest
version of this section.

---

## 5. The plan of action

A component plan entry is what the model writes. One per beat.

```json
{
  "segment": 1,
  "shows": "five sources that cannot be reached",
  "component": "countable_set",
  "parts": 5,
  "acts": [
    {"do": "land", "parts": "all",
     "anchor_phrase": "five of them", "lead_seconds": 0.3,
     "through_phrase": "just"},
    {"do": "fail", "parts": "all",
     "anchor_phrase": "can't", "lead_seconds": 0.3,
     "through_phrase": "accessed"}
  ]
}
```

### 5.1 What the model decides

- **`component`** - which of the roster's forms carries this noun. The
  whole roster ships in the prompt; nothing is shortlisted, because
  whatever selects a shortlist becomes the chooser (AGENTS.md 10.5).
- **`parts`** - how many countable things there are. This is the number
  the speaker said, not a number that looks good.
- **`acts`** - the plan of action: an ordered list of what happens, to
  which parts, cued to which words. This is the field the captain's
  message is about. A beat with no acts is a still.

### 5.2 What it may never decide

Any magnitude. Not a colour, a size, a spacing, a distance, a ramp
length, a typeface or an opacity. An entry carrying a look key is
refused as `look_value_in_component_plan`, read **from the key, never
from the value**, the same way `reel_semantic_visual` refuses one in a
span beat.

And the engine authors none either. Every magnitude lives in a
per-project **look declaration**; the roster states which values each
component and each act cannot be drawn without, and a look that omits one
REFUSES the entry. There is no fallback because there is no number in
the engine to fall back to. `tests/test_visual_component_plan.py` fails
if a colour literal ever appears in the module, or if a builder ever
reads a value the roster did not demand.

### 5.3 Timing is by search, never by seconds

Each act names an `anchor_phrase` quoted from the segment's own measured
words and a `lead_seconds` at or above zero, and starts that far BEFORE
the phrase. `through_phrase` makes the act COMPLETE on that phrase's
end. That is `ANIMATION_FIRST_REFERENCE.md` §1 - the picture arrives, the
voice confirms it - and AGENTS.md 6's rule that a passage is anchored by
search. Explicit seconds are refused: they land near words instead of on
them, so they are not a second timing, they are no timing.

### 5.4 The roster

`library/tools/visual_component_plan.COMPONENTS`. Five entries; each
states what it carries, what one part is, its acts, its material, and -
mandatorily - what it must **never** be reached for, for the reason
`motion_graphics_vocabulary` gives (AGENTS.md 16). Reachability is
REPORTED per entry and never filters membership, so a roster written
around today's builders would show its own defect rather than hide it.

| component | made of | reachable | blocker |
|---|---|---|---|
| `countable_set` | drawn marks | yes | - |
| `flow_between` | drawn marks | yes | - |
| `subject_on_surface` | depictive subject | no | `material_not_supplied` (§4) |
| `held_card` | project asset | no | `no_builder_yet` |
| `ground_turn` | drawn marks | no | `renderer_cannot_draw` - `StagedScene.ground.colour` is a constant, not an `Animatable` |

### 5.5 What the resolver refuses

Fifteen reasons, each with prose, in `COMPONENT_DROP_REASONS`. The four
that carry the design:

- **`no_action_planned`** - the entry plans no acts. *The captain's
  complaint, mechanised.* Refusing input: any otherwise-perfect entry
  with `"acts": []`. A thing that appears and then does nothing is a
  still, and refusing it is what stops the vocabulary sliding back into
  "an icon appears".
- **`look_states_no_value`** - the look declaration omits a value the
  component or one of its acts cannot be drawn without. Refusing input:
  the exact plan above against a look with `fail_fall` deleted. Detail:
  `fail needs fail_fall`. This is the mechanism that lets a vocabulary
  exist without becoming a house look.
- **`material_not_supplied`** - refusing input: the same plan with
  `"component": "subject_on_surface"`. §4's finding, enforced.
- **`act_outside_beat`** - an act starting before its beat or ending
  after it. Not clamped: moving an act is choosing when the picture
  plays.

The rest: `entry_is_not_a_mapping`, `unknown_component`,
`component_not_reachable`, `no_parts_declared`,
`look_value_in_component_plan`, `unknown_act`,
`act_not_on_this_component`, `part_not_in_component`,
`timing_by_seconds`, `no_anchor_declared`, `anchor_phrase_not_found`,
`no_timing_declared`, `acts_out_of_order`.

---

## 6. What was built and rendered

Beat 3.1, resolved from the real span plan against the real caption
plan's word windows, built into `StagedScene` layers and rendered
through the same `npx remotion render` the pipeline uses.

```
/tmp/fm-vep-vox-visual-components/five_sources.mov
1080x1920, ProRes, 87 frames, 2.900 s, reel 40 at 37.992-40.879 s
```

Resolved timings, printed by the resolver:

```
beat 37.992-40.879  'five sources that cannot be reached'
  land      38.092-39.435  parts=(1,2,3,4,5)  word_window:five of them -> just
  fail      39.456-40.879  parts=(1,2,3,4,5)  word_window:can't -> accessed
```

Five discs rise into a row one at a time, the first 300 ms ahead of
"five" and the last full as "just" ends; then from 300 ms ahead of
"can't" each in turn drops below the scored line and takes the room's own
value, the last completing as "accessed" ends. Motion is on twos
(`holdFrames: 2`); the camera pushes 1.00 to 1.06 across the beat.

### 6.1 Where every declared value came from

The declaration is the demo's, not the engine's. Nothing below is in
`visual_component_plan.py` or in `StagedScene`.

- **Ground `#0f1012`** - MEASURED. Dark decile of `LC4932.MXF` at
  1834.12 s, the frame this sentence is spoken over: RGB (15,16,18).
- **Mark `#9e7d72`** - MEASURED. Bright decile of the same frame:
  (158,125,114). The mark is lit by the light already in the room.
- **Failed mark `#302f2f`** - MEASURED. Median of the same frame:
  (48,47,47). A failed source falls to the value the room sits at.
- **`holdFrames: 2`** - MEASURED from the reference (§5 of the read).
  One integer.
- **Vignette SHAPE** - MEASURED from the reference §3.2: vertical
  falloff stronger than horizontal, so `radiusY` < `radiusX`. Its
  strength is the declaration's own.
- **Every lead, and the rule that the ramp completes on the word** -
  MEASURED. The beat's own word onsets; the rule is in the resolver.
- **Mark size, spacing, rise, fall, failed opacity, the scored line's
  position and weight, the camera push** - the declaration's own
  composition. Not measurements, and not in the engine.

### 6.2 Honest defects in this render

- **The vignette bands.** Visible concentric rings on a near-black
  ground: an 8-bit CSS radial-gradient artefact in `StagedScene`, not
  something this declaration causes. Lowering the strength to 0.55
  reduced it and did not remove it. Whoever next touches that component
  should dither the gradient.
- **Five discs are a diagram of "five", not a picture of "sources".**
  Naming what the marks ARE is the label question, and a label is type -
  which is the thing the captain said he does not want more of. The
  honest position is that a component carries the STRUCTURE of the idea
  and something else has to carry its identity; §4's supply question is
  the same question wearing a different hat.
- **One component is not a piece.** The reference makes three pictures in
  14 s. This is one picture over 2.9 s of a 61 s reel. Nothing here
  composes components into a sequence, cuts between them, or gives the
  space one leaves to the next - question 6 of §1 is answered in prose
  above and by nothing in code.

---

## 7. What this does and does not settle

**Settled.** A component is describable as six answers, and the fourth
of them - what it does while the voice continues - is a data structure a
model can write and a resolver can refuse. Two forms are buildable
today, from primitives that already exist, with no asset and no new
renderer.

**Not settled, and named rather than guessed:**

- **Whether the vocabulary generalises.** It generalises across
  *diagrammatic* nouns - counts, flows, containments - and does not
  reach depictive ones at all. Whether that is most beats or a few
  depends on the piece; reel 40 is unusually countable.
- **Whether `shows` is the right input.** It carried enough for 3.1 and
  3.3 and not enough for 3.4: "a bad information ecosystem" names an
  idea with no structural reading, and no component follows from it.
  A planner asked for the beat's STRUCTURE ("a count of five, three of
  which fail") rather than its noun would resolve straight through. That
  is a change to the span planner's handoff, not to this module.
- **Who composes components into a piece.** Nothing does. Fourteen beats
  and no cadence, no ground changes, no camera continuity between them -
  which is the same gap `ANIMATION_FIRST_REFERENCE.md` §13.3 names, one
  layer further down now that the components themselves exist.
