# select_broll (step 3.02) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/select_broll.json`
(prompt 10,061 chars; context 77,580 chars; constraints 0).

## 1. What I actually read

- **`broll_candidates_toon`, all 17 rows, tabulated.** This is the first context in the run
  that is genuinely well built for its job: one row per clip with `framing`, `stability`,
  `camera_move`, `content_type`, `usable_range`, `subjects` and a time-bounded
  `description`, plus `used_as_aroll`. I made a table of it and chose from the table.
- **`timed_spine`, all 16 blocks.** I needed exact timeline bounds and durations for the
  five non-speech blocks, and the `visual_note` I wrote at 2.05 for each.
- **`creative_direction`** - re-read for the energy and mood match the prompt asks for.
- **`a_roll_assignments`** - specifically to confirm which clips are already on screen, so
  a cutaway does not repeat the clip underneath it. `used_as_aroll` says clip_011, clip_012
  and clip_017; nothing else.

**Set aside:** `semantic_analysis_documents`, `clip_catalog` and `temporal_event_indices`
are all here again - fourth, fifth and fourth appearance respectively. I used the
candidates table instead, which is a projection of the same data built for this decision.
The one thing I went to the raw documents for was the `description` prose for clips 002,
005, 008 and 014, and it was already in the candidates table. `temporal_event_indices` I
checked and abandoned: 15 of 17 clips carry a single boundary at t=0, so the post-bridge's
scene-boundary snapping has nothing to snap to on any clip I selected.

**Something this context does noticeably better than the earlier ones, worth recording since
I am reading them all in sequence:** `usable_range` states a real per-clip answer and the
prompt explains what its absence would mean (`none - whole clip excluded (...)`). Every clip
here reads `0.0-<full duration>`, i.e. all usable - the same all-ones field I flagged as
uninformative at 2.01 - but here the prompt tells me what the field would say if it had
something to say, which makes the all-ones reading trustworthy rather than suspicious.

## 2. The material I actually have

Seventeen clips, but only **seven are cutaway material**. The other ten are
`person_talking_to_camera`, and three of those are already A-roll.

| clip | dur | framing | move | what it shows |
|---|---|---|---|---|
| clip_001 | 3.57s | wide | stationary | parking lot and sidewalk, parked cars, metal railing |
| clip_002 | 17.1s | close-up | panning_right | car interior - a hand with a chain and ring, Mazda wheel |
| clip_004 | 26.8s | wide | panning_right | from inside a moving car - dashboard, speedometer, nav screen |
| clip_005 | 7.9s | wide | panning_left | from inside a moving car, **sunset lighting**, 'Northbound'/'Eastbound' signs |
| clip_006 | 22.9s | wide | panning_right | urban street, THE WORKS / SHOP & DINER signage |
| clip_008 | 9.07s | wide | stationary -> walking | outdoor sidewalk and storefront, red brick, RESTAURANT/OPEN sign |
| clip_014 | 4.7s | close-up | stationary | brick wall, window, sidewalk, small plants in mulch |

Five blocks to cover, seven candidates. That is enough for one clip per block with no reuse,
which is what the variety criterion asks for, and it is why the assignment half of this step
was comparatively easy.

**One trap I checked for and found.** Block 1 is **3.662s**. clip_001 is **3.57s** long with
a usable range of 0.0-3.5s. It is the most obvious choice for that block on content - it is
the parking lot the hook is filmed in - and it is **0.09 seconds too short to cover it**. On
a non-speech block the cutaway IS the picture, so a short clip leaves black, and an
undeclared black stretch hard-fails compilation. I caught this by comparing durations before
choosing rather than after. clip_001 went to a shorter block instead.

## 3. The five assignments, and what each rejected

**Block 1 (2.398-6.060, 3.662s) - the breath after the cold open. -> clip_008.**
The hook is "i can feel the silent judgment of the people behind me" and he is on foot in a
public place. clip_008 is the only outdoor, on-foot, wide establishing shot in the set -
sidewalk, storefront, red brick - and its `stationary -> walking` move means it drifts
rather than sits, which suits a shot that has to hold for 3.7 seconds.
*Rejected:* clip_001 on duration (above). clip_006, which is wider and longer, because its
description lists a "Digital dashboard display" - it is shot from inside the car, and
opening the video's first look at the world from a car windscreen contradicts a hook about
being watched by people on foot.

**Block 4 (11.444-13.700, 2.256s) - the beat after the undercut joke. -> clip_014.**
Short, and it follows a punchline, so it wants a small quiet image rather than a vista.
clip_014 is a 4.7s close-up of a brick wall, a window and some plants in mulch: deliberately
uneventful. A joke lands better against a flat image than a busy one.
*Rejected:* clip_001, held back for block 6 where its length is not marginal.

**Block 6 (17.240-18.622, 1.382s) - the shortest gap. -> clip_001.**
1.4 seconds is a blink, not a shot. clip_001's wide parking lot reads instantly - cars, a
sidewalk, a railing - which is what a very short cut needs. It is also literally the place
he is standing, so it costs the viewer nothing to parse before the 16-second block starts.

**Block 8 (34.615-38.801, 4.186s) - the biggest breath in the piece. -> clip_005.**
This block exists to pay for the 16-second talking head before it, and it is the one place
the video is allowed to just look at something. clip_005 is the **only clip in the project
whose description mentions sunset lighting**, and 2.01's emotional landscape is explicitly
golden-hour with the sun going down across the material. It is 7.9s against a 4.19s block,
so there is room to sit inside it rather than use it end to end.
There is a second reason I like it that I want to flag as a coincidence rather than a
plan: its notable features are road signs reading "Northbound" and "Eastbound", and this
block is the hinge between the passage about paralysis and the passage about momentum. A
direction sign under a decision about which way to go is a nice accident. I did not choose
it for that and I would not defend it on that.
*Rejected:* clip_006, the other long wide - overcast and hazy where clip_005 is lit, and
this is the beat that most needs the picture to be worth looking at.

**Block 13 (46.147-48.065, 1.918s) - the tonal pivot into the climax. -> clip_002.**
This is the choice I am most pleased with and it is worth stating the mechanism. The climax
(block 14) is clip_017, which the semantic analysis describes as **"Inside a vehicle. Dimly
lit interior"** - the only in-car talking head in the edit, and a hard location change from
everything before it. clip_002 is a close-up of a hand with a chain and ring on a Mazda
steering wheel, **also inside a car**. Cutting to a car interior detail immediately before
cutting to him sitting in a car pre-loads the location change, so the climax does not open
on an unexplained jump. It is also the right emotional register: looking away from a face,
down at hands, is what an editor does a beat before someone admits something.
*Rejected:* clip_014, which is the other quiet close-up and would have served the mood, but
was already used at block 4 and does nothing to prepare the location.

## 4. Interjections: where I put them and, more importantly, where I did not

The prompt is unusually careful here - "there is no required number", "do NOT add a cutaway
to reach a count", "never write a rationale that justifies a cut by how many there are". So
the question is only where the edit needs one.

**Two, both inside block 7** (18.622-34.615, **15.993s**). That block is a quarter of the
video on one unbroken shaky close-up selfie, and it could not be split at 2.05 because the
passage duration comes from word alignment. If any block in this piece needs relief this is
it, and I said so in my own `visual_note` at 2.05. I placed them to break the block into
roughly 4.9s / 4.0s / 2.1s of talking head:

- **23.5-26.0s -> clip_006.** Under "...all the numbers and metrics and the idea of
  succeeding". An observational urban wide: commercial signage, traffic. Neutral by design -
  the speech here is abstract and the picture's job is to let the ear keep working, not to
  illustrate.
- **30.0-32.5s -> clip_004.** Under "...a state of paralysis where i don't do the thing in
  the first place". **I am uneasy about this one and will say why rather than sell it:**
  clip_004 is a moving-car interior with a speedometer, and putting motion under the word
  "paralysis" is either a quiet counterpoint or a contradiction depending on how it cuts. I
  took it because it is the last unused wide with enough length, and because the alternative
  was reusing a clip. If one selection in this step is wrong, it is this one.

**Nowhere else, and specifically NOT over block 14, the climax.** My 2.05 visual note said
"talking head, no cutaway, no effect - let it be plain", and I am holding to it: the music
already drops to `silent` for those 8.67 seconds, and the whole point of that decision is
that nothing decorates the moment. A cutaway would undo the silence's work.

**And NOT over blocks 9-12**, the clip_012 triple ("it doesn't matter what i'm using to
record / if it's even edited / it just matters that it gets posted"). 2.05 said do not
separate these. The parallel construction is a rhythm and cutting away breaks it.

I also declined to use any of the six unused **talking-head** clips (003, 007, 009, 010,
013, 015, 016) as cutaways, though the prompt permits it. A second take of the same person
with his mouth moving out of sync with the audio reads as a continuity error to a viewer,
not as a technique. Six clips of usable footage sit unused for that reason and I would
rather record that than pretend the set was exhausted.

## 5. What I was missing

- **I cannot see any of it.** Every judgement above is made from prose descriptions and
  four metadata fields. I am choosing between "a brick wall with plants in mulch" and "a
  parking lot with a metal railing" as images without having seen either. For a step whose
  entire subject is what the picture looks like, that is the fundamental limit, and no
  amount of care in reading the table changes it.
- **`stability` is `unknown` for all 17 candidates.** The prompt names it as a Step B
  criterion ("`stable`/`stationary` reads calm; `unstable`... reads energetic. Match the
  creative direction's energy") and the field carries no value for any clip. Note this is
  *not* what the underlying analysis holds - at 2.01 the semantic documents gave me real
  per-clip stability (`shaky` for clip_011, `stable` for clip_017 and clip_001). So the
  value exists upstream and the candidates table is showing `unknown`. That looks like a
  projection defect rather than missing measurement, and it is the one concrete bug-shaped
  thing I have found in a context this run.
- **Scene boundaries, effectively.** 15 of 17 clips have a single boundary at t=0, so
  "clean cut points" has nothing to offer and the post-bridge will fall back to block-based
  estimation for every clip I picked.
- **`description` is truncated the same way it was at 2.01** - each clip's prose covers only
  its opening seconds. For the short clips that is the whole clip and it does not matter.
  For clip_006 (22.9s described to 23.0s) it is fine; for the ones I use only a slice of it
  means I am choosing a sub-range from a description of a different sub-range.
- **A creative brief.** Sixth step. The prompt says a brief would tell me "B-roll density and
  pacing preferences (fast-cut vs. breathing room)" - which is exactly the judgement I am
  making unaided when I decide two interjections rather than five.

## 6. Confidence

**High** on coverage and mechanics: five non-speech blocks, five distinct clips, every clip
comfortably longer than the block it covers (the 0.09s trap checked and avoided), no clip
reused, no cutaway repeating the A-roll underneath it.

**High** on block 13 -> clip_002. The car-interior match into the in-car climax is the one
choice here I would defend against an editor who disagreed.

**Medium** on blocks 1, 4 and 6. They are sensible and interchangeable; swapping clip_001
and clip_014 between blocks 4 and 6 would produce a video I could not distinguish from this
one.

**Low on the second interjection (clip_004 under "paralysis").** Flagged above. If someone
watches this and says the driving shot fights the line, they are right and it should come
out - the block survives with one interjection instead of two.

**What would change my answer: seeing the frames.** One contact sheet of the seven cutaway
clips would improve this step more than any amount of additional metadata.
