# plan_sfx (step 4.04) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/plan_sfx.json`
(prompt 4,162 chars; context 53,309 chars; constraints 0).

## 1. What I actually read

- **`available_sfx_types`**: `['bass_impact', 'click', 'riser', 'swell', 'swish', 'tick',
  'whoosh']`. Seven types, computed from the real 78-entry library that step 0.01 validated.
  **This is the only genuinely computed thing in this context and it is what I planned
  against.**
- **`timed_spine`**, all 16 blocks - durations, `music_behavior`, block types. This is what
  the decision is actually made from: where the music changes and where the sections turn.
- **`creative_direction`** - for the restraint question.
- **`music_analysis`** - present, and I used only the fact of the flat bed, which I measured
  myself at 2.04.

**`sfx_candidates_toon`, the table the prompt names as "the data available", has ZERO rows:**

```
sfx_candidates_toon: |
  [0]{segment_id,text,action_sfx_suggested}
```

I read `library/steps/step_4_04_plan_sfx/bridge.py` to find out why, and the cause is
mechanical and exact. The bridge builds its rows from
`data.get("a_roll_assignments", {})` - but **`a_roll_assignments` is not a declared input to
this step.** The manifest's inputs are `b_roll_assignments`, `creative_direction`,
`music_analysis`, `rough_cut_review`, `semantic_analysis_documents`,
`temporal_event_indices`, `timed_spine`, `creative_brief`, `project_fps`. A-roll is not
among them, so the `.get` returns `{}`, the loop never runs, and the table is empty on every
project. It is not empty because this edit has no candidates; it is empty by construction.

And as at 4.03, `action_sfx_suggested` is the hardcoded string `"No"` - the prompt describes
it as "pre-computed suggestion... based on audio transients", and nothing computes it. Same
stub shape in both planning bridges.

## 2. Three vocabularies that do not agree

Worth recording because a plan written against the wrong one produces silent SFX:

| source | types offered |
|---|---|
| the prompt's toolkit table | whoosh, swish, bass_impact, riser, **foley**, **ambient**, click, tick, reverse_cymbal, swell |
| the output schema | whoosh, swish, bass_impact, riser, click, tick, **reverse_cymbal**, swell |
| `available_sfx_types` (the real library) | whoosh, swish, bass_impact, riser, click, tick, swell |

`foley` and `ambient` are in the prompt and in neither the schema nor the library.
`reverse_cymbal` is in the prompt and the schema and **not in the library**. So of the ten
types the prompt offers me, three cannot be delivered, and the only list that reflects what
exists on disk is the bridge-supplied one. I planned against `available_sfx_types` and used
nothing outside it.

**`creative_brief` is a declared input to this step and did not arrive**, which is the
clearest evidence yet that no brief exists in this project's state rather than that the
routing is at fault. Ninth step running without one.

## 3. The decision: two sounds

The prompt is emphatic - "less is more", "there is no required number", "do NOT add an effect
to reach a count", "never write a rationale that justifies a sound by how many there are".
The captain's no-floors ruling covers this step too. So the question is which moments earn a
sound, and in a sixty-second piece about a self-conscious man, scored with a deliberately
inert piano bed, with one drawn transition and no VFX at all, the answer is: very few.

**I looked for moments where something happens that the ear would otherwise mistake for a
fault.** That is a stricter test than "where would a sound be nice", and it selected two.

**Block 13 - `swell`, `low`. The one I would fight for.**
Block 13 is the 1.918s pivot slot, and at the cut out of it (cut 14) the music goes to
`silent` for the only time in the video, for 8.67 seconds, under the most exposed line in
the footage. **A bed that simply stops can read as a dropout - a technical fault, not a
decision.** A swell that builds across the pivot and resolves at the cut is what tells the
ear the silence was chosen. The toolkit's own description of `swell` is "between major
sections", and block 13 is precisely the seam between the argument and the confession.

Placed on block 13 rather than block 14 deliberately: it needs to peak AT the cut and decay
into the silence, so it has to start before the cut. Placed at block 14 it would start late
and sit under his first words, which is the one thing it must not do.

**Block 9 - `swish`, `subtle`.**
Block 9 is where the piece's other drawn transition lands - the `defocus` at cut 9, out of
the sunset breath into a new location. The prompt permits at most one SFX per creative
transition and suggests layering with purpose. A blur is a soft visual event with no natural
sound, and a subtle swish under it is what makes it read as intentional rather than as a
focus hunt. `subtle` rather than `low` because it must not compete with the `prominent`
music running through that slot.

**Two sounds, on the two moments where the edit does something the ear needs told about.**
Both are at cuts I had already decided were the piece's structural seams at 4.02, which is
the consistency I want: the transitions, the music behaviour and the sound design are all
marking the same two places.

## 4. What I considered and rejected

- **A `whoosh` on cut 1, out of the cold open.** The single most conventional SFX placement
  in shortform, and the one I expect a reader to miss most. Rejected: it is a hard cut by
  design (4.02), and a whoosh is an energy gesture on a video whose direction says anything
  hard-hitting fights the footage. A whoosh after "i can feel the silent judgment of the
  people behind me" would make the piece sound like it is about to sell something.
- **Anything on block 8**, the 4.19s big breath. It is the largest gap and the obvious place
  for a sound. Rejected on the prompt's own rule - music is `prominent` there, and that gap
  exists so the viewer can rest. Filling a rest is not sound design.
- **`click` or `tick` on subtitle appearances.** The toolkit offers it and step 4.01 produced
  **45 caption cards**. Forty-five ticks in sixty seconds is a texture, not punctuation, and
  it would also breach "no two SFX overlap at the same position" repeatedly. Rejected
  without much thought - this was easy.
- **A `riser` into the climax instead of the swell.** Closer than it sounds. Rejected on
  duration: the toolkit says a riser spans 5-10 seconds and block 13 is 1.918s, so a riser
  would have to start back inside block 12 - which is the third beat of the "it doesn't
  matter / it doesn't matter / it just matters" construction, and putting a rising tone
  under that would telegraph the turn several seconds before it happens. The whole effect of
  cut 14 is that it is not telegraphed.
- **A `bass_impact` on "even if it's bad, even if i hate it, i will post it."** Genuinely
  tempting - it is the strongest line in the piece and an impact would land it. Rejected
  because the direction says the ending resolves by getting quieter and more certain rather
  than louder, and a bass hit is the loudest thing in the toolkit. The line is already
  carried by the music returning underneath it after eight seconds of silence; that IS the
  emphasis, and adding a second one would split it.
- **A sound on cut 15**, marking the return from silence. Same reasoning - the music's return
  is the event, and marking an event that is already audible is clutter.

## 5. What I was missing

- **The whole candidates table** (section 1). Every row that should have told me where the
  audio transients are, absent by construction. The prompt's stated method for this step -
  read `action_sfx_suggested`, place sounds where transients suggest them - was not
  available to me at all, and I planned from the spine's structure instead.
- **The transition plan.** The manifest's State Interaction table lists `transition_plan
  (optional)`, `subtitle_entries (optional)` and `vfx_plan (optional)` as reads, and none of
  them are in this context. I know where the two drawn transitions are because I planned them
  myself twenty minutes ago at 4.02. **An agent without that memory could not place a sound
  under a transition, because it cannot see the transitions.** That is the single most
  consequential gap at this step: pairing SFX with transitions is the first purpose the
  prompt lists, and the transition plan is not routed here.
- **Any way to hear the library.** I chose `swell` and `swish` from type names. I do not know
  what the 78 files sound like, how long they are, or whether the `swell` in that library
  rises over half a second or four.
- **A creative brief.** Declared as an input to this step and absent.

## 6. Confidence

**High on the swell at block 13.** It is solving a concrete problem - marking an 8.67-second
music dropout as deliberate - rather than adding polish, and if only one sound survives
review it should be this one.

**Medium on the swish at block 9.** It is defensible and conventional, and it is also the
kind of sound that is there because a transition is there. If the defocus at cut 9 comes out
(which I flagged at 4.02 as my medium-confidence call), this swish should come out with it.

**Medium-low on the total being two.** I can argue for one and I can argue for four. What I
cannot argue for is a number chosen to look right, so I applied a test - does the ear need
telling - and reported what it selected. A sound designer with the audio in front of them
would probably find one or two more in the ambience.

**What would change my answer:** hearing the two candidate library files. If this library's
`swell` is a big cinematic riser rather than a soft lift, it is wrong for block 13 and the
right answer is a quieter type or nothing at all - and the silence would then have to be
marked some other way, or accepted as a risk.

---

## POSTSCRIPT - written AFTER the answer, and marked as such

`compile_manifest` failed on the transition plan (see
`reasoning/plan_transitions.md`, postscript): a drawn transition cannot sit at a cut whose
outgoing block is a `transition_slot`, because that picture is on V2 and the transition
machinery indexes V1. Both defocuses moved - cut 9 -> cut 8, cut 14 -> cut 13 - and
`plan_transitions` and `plan_sfx` were both re-run.

**The swish moves with it, from block 9 to block 8.** My own rationale above said so in as
many words: *"It is placed here because a drawn transition is here - if the defocus at cut 9
is removed on review, this swish should be removed with it."* The defocus did not go away,
it moved, so the sound moves with it and stays paired to the blur it exists to sell.

**The swell at block 13 does not move, and is now better placed than it was.** The defocus
that used to be at cut 14 is now at cut 13 - which is the boundary the swell was already
sitting on. So the pivot into the climax now carries the blur and the swell together, which
is the "layer with purpose" the prompt asks for, arrived at by accident rather than design.
The rule "every creative transition has at most one SFX" still holds: cut 8 has the swish,
cut 13 has the swell, one each.

Nothing about the reasoning in sections 3 and 4 changes. Two sounds, on the two structural
seams, for the same reasons - the seams simply moved 4.2 and 1.9 seconds earlier.
