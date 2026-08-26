# plan_transitions (step 4.02) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/plan_transitions.json`
(prompt 5,385 chars; context 59,802 chars; constraints 179 chars).

## 1. What I actually read

- **`cuts_toon`, all 15 rows, in full.** This is the only part of the context I needed and
  it is the best-built table in the run: one row per cut with `cut_point_position`,
  `cut_time`, the cut's `type` (speech-to-speech, transition_slot-to-speech, ...),
  `beat_near_cut` with the actual distance in seconds, and the framing/camera/tags of both
  the outgoing and incoming footage. Everything below was decided from this table.
- **`creative_direction`** - `target_mood` and `energy_arc`, to decide what NOT to draw.
- **The brand constraints block** - the permitted type list and the 200-500ms duration band.
- **`timed_spine`** - to map cut positions back to which block is which.

**Read and not needed:** `music_analysis` is **552 of the context's 750 lines** - the full
260-beat and 65-downbeat grid, in longhand. `cuts_toon` has already reduced all of it to one
`beat_near_cut` column per cut, which is the only form in which I can use it here, so
roughly three quarters of this context is a table I read once at 2.05 and do not need again.
Also re-read-and-not-used: `a_roll_assignments`, `b_roll_assignments`, `music_selection`,
`rough_cut_review`, `temporal_event_indices`.

**Observation, since I am reading these contexts in sequence:** this step was re-routed at
its own handoff in the most recent commit, and it shows - `cuts_toon` is a purpose-built
projection rather than a raw document dump, and it is the difference between deciding from
data and deciding from an impression. The `music_analysis` bulk sitting beside it is the
part that has not caught up.

**Broken field, same as at 3.02:** `Stability: unknown` on **both sides of all 15 cuts**.
The prompt's own guidance ("match energy of surrounding content") and the vocabulary's
distinction between calm and energetic cuts both want it. The underlying analysis has it -
at 2.01 the semantic documents gave me `shaky` for clip_011 and `stable` for clip_017. It is
being lost between the measurement and this table, in the same way it was lost in
`broll_candidates_toon`. Two different projections, one missing field, same value present
upstream: that reads like one defect, not two.

## 2. The rule I decided to apply, before looking at individual cuts

AGENTS.md is unambiguous that a cut the plan does not decorate is a hard cut, that the
brand's allow-list is a permission rather than an instruction, and that there are no
creative floors. The prompt agrees ("hard cuts dominate"). So the question is not "which
transition goes here" fifteen times; it is "does anything here earn a drawn transition at
all", and the honest default answer is no.

**The rule I chose: a drawn transition marks a change of PLACE, and only where a beat lands
on the cut.** Everything else is a hard cut. I picked that rule rather than deciding
cut-by-cut because a rule can be checked and a series of individual judgements cannot, and
because it happens to select exactly the moments this piece is actually about - it is a
video with three locations and the changes between them are the structure.

**What I checked before committing to restraint.** I was worried that a plan of thirteen
hard cuts and two defocuses would trip P7, the uniformity gate, so I read it
(`manifest_validator._check_no_uniform_effect`). It cannot: `CUT_TYPES` (`hard_cut`,
`jump_cut`, `match_cut`) are excluded outright because they draw nothing, and a drawn type
only fires the check when its count reaches `len(transitions)` - all fifteen. Two defocuses
out of fifteen is nowhere near it. So restraint is safe here, and I am recording that I
verified it rather than assumed it, because the opposite worry - that a sparse plan would be
read as a thin edit - is exactly the pressure that produced the creative floors AGENTS.md
had to remove.

## 3. The two drawn transitions

**Cut 14, at 48.06s - `defocus`, `medium` (333ms). The one that matters.**
This is the pivot into the climax and it is where the most things change at once in the
whole piece: the location changes (outdoors to the car interior - the only in-car shot in
the edit), the tone reverses (from a confident argument to "i've quit every single day"),
and the music drops to `silent` for the only time in the video. The vocabulary describes
`defocus` as "blur through the cut - mood shifts, soft scene changes", which is this cut
described exactly. `beat_near_cut` reads `Yes (0.00s away)` - the cut is already on a beat,
because I snapped it there at 2.05. `medium` rather than `quick` because this is the one
transition allowed to be noticed.

**Cut 9, at 38.80s - `defocus`, `quick` (200ms).**
Out of the sunset breath and into clip_012, a new location and the piece's other real change
of place. `Yes (0.00s away)`. `quick` rather than `medium` deliberately: it is the same
gesture as cut 14 and it must not compete with it. Two instances of one type at two
different lengths is also the only way this plan carries more than one parameter set.

**Cut 8, at 34.62s, considered and rejected.** This is arguably the better *emotional*
moment for a blur - it is where the sixteen-second talking head finally ends and the video
exhales into the sunset. I did not take it because `beat_near_cut` is `No`, and the prompt
asks for major creative transitions on beats. Taking it would also have put drawn
transitions on both sides of one 4.19s B-roll block, which would make a four-second breath
feel like a set piece.

**Cut 15, at 56.73s, considered and rejected, and this one was close.** It is a change of
place (car back to outdoors), it has a beat 0.04s away, and by my own rule it qualifies. I
left it a hard cut for two reasons. First, the music already carries this transition - it
returns from `silent` to `background` exactly here, and drawing a blur on top of that is
doubling one idea. Second, the direction says the piece ends by stopping rather than
resolving, and a slightly abrupt return to his face for "even if it's bad, even if i hate
it, i will post it" is the right amount of unsmoothed. This is the decision in this step I
am least sure of.

## 4. The rest, and the labels I used

Thirteen hard cuts, of which two are labelled `jump_cut`. Both `jump_cut` and `hard_cut` are
`CUT_TYPES` and draw nothing, so this is **description, not decoration** - it costs the
picture nothing and it makes the plan readable:

- **Cut 3 (8.74s)** - `jump_cut`. Same clip (clip_011), 20.348s to 24.171s: a ~4-second skip
  in one continuous take. That is the literal definition the toolkit gives ("same subject,
  different moment, implies time skip").
- **Cut 10 (41.18s)** - `jump_cut`. Same clip (clip_012), 24.178s to 30.140s: a ~6-second
  skip.

Cuts **11 and 12** are also same-clip, but the gaps are 31.878->31.898s and
33.661->33.941s - twenty and two hundred and eighty milliseconds. Those are not time skips,
they are the natural spaces between three sentences he said consecutively, so calling them
jump cuts would be wrong. `hard_cut`, and they should be invisible.

Everything else - cuts 1, 2, 4, 5, 6, 7, 8, 13, 15 - is a hard cut into or out of a B-roll
block, or the ending. The prompt's "never repeat the same creative transition type
consecutively" holds: the two defocuses are separated by cuts 10-13, and the two jump cuts
by cuts 4-9.

**So: 15 planned cuts, 2 of them drawn.** I want to state plainly that this is a restrained
plan and that I think it is right rather than lazy. This is a sixty-second piece about a man
who is embarrassed to be filming himself, scored with a flat piano bed, and its own creative
direction says anything hard-hitting will fight the footage. `flash` and `zoom_blur` are
energy transitions and there is no energy spike in this edit to put one on.
`fade_to_black` is for chapter breaks and this piece has no chapters - and a dip to black
inside a 60s video would also have to be declared as an intentional black beat, which it is
not. Drawing something on twelve of fifteen cuts would be decoration applied to a video
that does not want decorating.

## 5. What I was missing

- **`Stability` on every cut, both sides** - section 1. The single field the toolkit's
  calm-versus-energetic distinction most depends on.
- **Any sight of the frames.** `defocus` blurs through a cut; whether that reads as elegant
  or as a smear depends on what is in the two shots, and I have `Framing: close-up | Camera:
  panning_right` and a tag list.
- **Which side of the cut the effect is drawn on.** No per-clip Fusion comp can mix two
  clips, so a `defocus` is drawn on one clip's head or the other's tail, and the prompt does
  not say which. For cut 14 I am assuming it resolves *into* clip_017 - the picture settling
  as he begins to confess - which is the version I want. If it is drawn on the outgoing
  clip_002 instead, the effect is a blur out of a car-interior detail, which is weaker but
  not wrong.
- **A creative brief.** Eighth step. The prompt says a brief would give "preferred transition
  feel" and "overall pacing philosophy", which is precisely the judgement I made in section 2
  by inventing a rule for myself.

## 6. Confidence

**High** on cut 14. If exactly one cut in this video should be decorated it is that one, and
the reasons converge - location, tone and music all change there and it is on a beat.

**High** on the thirteen hard cuts, and on the two `jump_cut` labels being accurate rather
than ornamental.

**Medium** on cut 9. It follows my rule and it is on a beat, but I could defend a version of
this plan with a single drawn transition in the whole piece, and that version might be
better - one blur in sixty seconds is a stronger gesture than two.

**Low-to-medium on cut 15 staying hard**, as described in section 3. It qualifies under my
own rule and I overrode the rule on taste. That is the kind of exception worth flagging
rather than burying, and it is a one-line change if someone disagrees.

**What would change my answer:** seeing the two shots at cut 9. If clip_005's sunset and
clip_012's walking selfie are tonally close, the blur between them is doing nothing and
should come out.

---

## POSTSCRIPT - written AFTER the answer, and marked as such

**The plan above failed the run.** `compile_manifest` (5.04) raised:

```
ValueError: Transition trans_009 at 38.801s does not sit at the end of any V1 clip
```

Both of my drawn transitions were invalid, and the reason is structural rather than a
mistake of taste.

### What I got wrong, and why nothing told me

A drawn transition is rendered as a tail on the outgoing clip and a head on the incoming
one, and `compile_manifest._v1_index_ending_at` requires the cut to sit at the end of a **V1**
clip. Meanwhile every B-roll assignment - including the ones filling `transition_slot`
blocks - is placed on **V2**, unconditionally (`step.py` ~line 1318, "V2: B-Roll clips"). So
V1 is not continuous: it holds the eleven A-roll blocks with gaps where the transition slots
are, and those gaps are covered on V2.

The consequence is a hard rule the handoff never states: **a cut whose OUTGOING block is a
transition_slot cannot carry a drawn transition**, because the outgoing picture is not on V1.
I measured it against the real spine:

| cut | time | beat near? | can carry a drawn transition? |
|---|---|---|---|
| 1 | 2.40 | No | **yes** |
| 2 | 6.06 | Yes | no - outgoing is V2 |
| 3 | 8.74 | Yes | **yes** |
| 4 | 11.44 | Yes | **yes** |
| 5 | 13.70 | Yes | no - outgoing is V2 |
| 6 | 17.24 | No | **yes** |
| 7 | 18.62 | Yes | no - outgoing is V2 |
| 8 | 34.62 | No | **yes** |
| 9 | 38.80 | Yes | no - outgoing is V2 |
| 10-13 | 41.18-46.15 | No | **yes** |
| 14 | 48.06 | Yes | no - outgoing is V2 |
| 15 | 56.73 | Yes | **yes** |

I put both defocuses on cuts 9 and 14 - and those are two of the five cuts that cannot have
one. **This was not bad luck.** The prompt says "prefer placing major creative transitions on
cuts with a nearby beat", and in this spine the beat-aligned cuts are overwhelmingly the
`transition_slot-to-speech` ones (five of the six), because those are the boundaries I
snapped to the beat grid at 2.05 - a transition slot's duration is the only free variable
there, so the cut OUT of a slot is exactly where a beat can be placed. **Following the
prompt's beat-alignment guidance steers directly at the cuts that cannot carry the effect.**

Nothing in the context distinguishes them. `cuts_toon` gives me `type`
(`transition_slot-to-speech`, `speech-to-speech`, ...), which is precisely the information
needed to derive this - but the handoff never says the distinction matters, never mentions
V1 or V2, and lists all fifteen cuts as equally available. AGENTS.md 5 states the rule
("Transitions stay on V1: `after_clip` indexes the V1 clip LIST"), so it is known and
written down - it just does not reach the step that has to obey it.

### What I changed, and what it cost

I moved both defocuses to the **other side of the same two seams**, which are V1-outgoing:

- **cut 9 (38.80s) -> cut 8 (34.62s).** Same seam. The blur now happens going INTO the
  sunset breath rather than coming out of it - out of the sixteen-second talking head, which
  is what I wrote in section 3 above was "arguably the better EMOTIONAL moment for a blur -
  it is where the video finally exhales". So the constraint pushed me to the choice I had
  already said I preferred and had rejected only on beat grounds.
- **cut 14 (48.06s) -> cut 13 (46.15s).** Same seam. The blur now carries out of "it just
  matters that it gets posted" into the car-interior pivot cutaway, and the cut into the
  climax itself becomes a hard cut. Slightly different in feel: the softening now prepares
  the pivot rather than delivering it. I think this is a small loss - the ideal version
  blurs directly into his face in the car - but it is a real alternative rather than a
  degradation, and the pivot is still marked.

**The cost is beat alignment.** Neither cut 8 nor cut 13 has a beat near it. I accepted that
deliberately: a `defocus` is a gradual 200-333ms blur, not a percussive hit, so landing it
off the grid is far less audible than an off-beat `flash` would be, and marking the right
structural seam matters more than marking the beat. Cuts 3, 4 and 15 are both beat-aligned
AND V1-capable, and I considered moving a defocus onto one of them purely to keep the beat -
rejected, because none of those three is a seam, and putting an effect where it does not
belong to satisfy a guideline is the exact failure mode the rest of this log is about.

### Was this a code fix?

No, and I want to be explicit. The `compile_manifest` assertion is correct and protective -
AGENTS.md 5 says replaying a transition against the wrong list "draws a transition at an
unrelated cut". Making transitions work across a V2 gap would be an architectural change to
the track model, which is not mine to make and not what the run needed. **The plan was
wrong, not the code.** The fix was to re-plan, which cost one extra LLM round trip and no
change to the repository.

**What I would report as worth fixing** (and did not do): `cuts_toon` already knows each
cut's `type`, so it could carry one more column - whether the cut can take a drawn
transition - or the handoff could say the rule in one sentence. As it stands the step is
asked to choose from fifteen cuts, five of which will fail compilation, with the beat
guidance pointing at those five.
