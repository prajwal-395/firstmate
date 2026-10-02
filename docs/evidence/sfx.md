# Sound effects (step 4.04) - the incidents behind the tests

Moved out of the test module docstrings when the SFX tests were
consolidated (2026-10-02). The tests keep the invariant; this keeps the
story. Where the two disagree, the code and the tests win.

## The level that made a sound silent (`tests/unit/audio/test_sfx_level.py`)

Captain, station 9: *"a single sfx that i dont even think played the right
part of the sfx and instead it was just silent"*.

The experience is right and the mechanism is not silence. The file is
fine, the placement is fine, and it was made inaudible afterwards by two
taste values written into the engine - a track level of -12 dB annotated
*"Subtle - felt more than heard"* and a four-word ladder resolving the
model's word into -18/-14/-10/-6. Both are gone, neither is renumbered,
and `library/tools/sfx_level.py` carries the record (`WITHDRAWN_*`).

The case the captain named was `camera soft click.wav`: 0.459 s, peak
-6.4 dB, mean -36.6 dB, transient at the head (-29.7 dB at 0.00 s
decaying to -36.6 dB by 0.37 s), placed at source_in 0.0 for the full
duration - the right part played.

## Layering, and the library "built for a different kind of edit" (`tests/unit/audio/test_sfx_layering_and_build.py`)

Project 001 shipped two camera shutters - 0.46 s and 0.34 s - at two
blocks, layering nothing. The planner's own `could_not_determine`
recorded the reason: it judged the library *"built for a different kind
of edit"*. That was a judgement made without two facts, both of them
measurements rather than opinions, and both now in its context
(`library/tools/sfx_envelope.py`). The capability was never missing: two
sounds on one block survive as two placements, and a `swelling` sound is
anchored by its END so its climax lands on the measured energy peak.

`_describe_placement` used to be a private dict in the post-bridge, so the
sentence the manifest recorded and the sentence the planner needed could
never have been checked against each other.

## Atmospheric layers (captain's ruling 2026-09-08)

The pipeline may use risers, drones and crackles as a LAYER - a sound
that plays under the picture rather than marking a visible event. 001
used only literal SFX. Two mechanics make a layer different, both in
4.04's post-bridge: a layer is NOT shifted off speech by
`_avoid_speech_collision` (a drone moved into a word gap stops being a
layer), and a layer without a stated `rationale` is dropped, because it
is tied to no visible event and nothing else holds it to the moment. A
`role` nobody reads is dropped the way an unread effect type is.

## The unread `at_word` key

A probe's SFX entry carried `at_word`, nothing in step 4.04 named it, and
the whoosh landed 3.06 s early on the block start - the plan's timing
silently replaced by the block start. Unread keys now raise
`UnreadPlanKey`, which the runner hands back to the model as retry
feedback.

## Stated fades (rung 7, E3: "fade lengths in frames")

A sound's fade had no plan spelling: 4.04 measured only the de-click
floor, and a `fade_*` key was refused as unknown. `fade_in_*`/`fade_out_*`
in seconds or frames now carry the requester's number; the shipped
out-fade is the max of the stated fade and the de-click floor, and the
head ramp renders through `otio_mix.declick_curve`.

## The catalogue's advertised duration was refused by its own contract

The catalogue advertised `camera soft click.wav` (0.4591609977 s measured)
with a length formatted to two places; an edge case at 0.4599999 formatted
as 0.46 - past the measured length - so a model copying the advertised
number was refused by `resolve_played_seconds`. The catalogue now
truncates.

## The empty candidate table (#223) (`tests/unit/audio/test_plan_sfx_candidate_table.py`)

Observed on the clean run of project 001 on 2026-08-26: the table the
`plan_sfx` handoff describes column by column arrived as

    sfx_candidates_toon: |
      [0]{segment_id,text,action_sfx_suggested}

The bridge built it from `data["a_roll_assignments"]`, and: no DAG edge
routed `a_roll_assignments` into `plan_sfx`, so the `.get()` answered
`{}` and the loop never ran; `assign_aroll`'s entries are keyed
`spine_block_position`, not `segment_id`; and they carry no `text`. Same
family as `cuts_toon` in #218 (AGENTS.md 10.1, key-name mismatches).
The transient column was also the literal string "No" on every row; it
now counts the energy peaks step 1.04 measured inside the block's source
range. A non-speech block used to say `not measured (no source clip)` on
5 of 001's 13 rows while `b_roll_assignments` named the covering cutaway
in the same prompt. The bridge also used to emit its own empty output
(`{"sfx_list": [], "fairlight_preset": "default"}`) back as input, which
read as a plan that had already decided to place no sounds.

## Pacing and SFX decided from memory (`tests/unit/audio/test_pacing_and_sfx_are_not_remembered.py`)

Both recorded in the creative-decision degradation report of 2026-08-28
(F7 and F13), invisible on the run of record because one agent answered
every step:

* `mesh_spine` (2.05) sets every gap and every block's `music_behavior`,
  including the `silent` climax. No DAG edge carried the creative
  direction; the 2.05 trace justified the silence by quoting 2.01's
  `energy_arc` ("the piece resolves by getting quieter and more certain,
  not louder"), a sentence that appeared nowhere in 2.05's context.
* `plan_sfx` (4.04) is told first thing to pair sounds with transitions,
  and no DAG edge carried `transition_spec`. Both shipped sounds sat on
  the two drawn transitions, placed by an agent that had planned those
  transitions itself minutes earlier.

The deliberate boundary: `key_moments` and `rationale` (4,744 of the
direction's 6,854 bytes on project 001) do not reach the spine.

## Nothing predicted whether a sound would be heard (`tests/unit/audio/test_sfx_hears_the_bed.py`)

The one SFX project 001 shipped plays at -14 dB at 2.398 s, the exact
frame the bed goes `prominent` (-6 dB, the loudest music in the video).
Step 4.04 was routed `timed_spine`, which carries `music_behavior` per
block, so it COULD have seen that; nothing asked it to, and
`volume_level` was a four-word ladder with no relation to what the sound
measures or what is under it. The bed's own level now travels into the
candidate table as data; it states a level and never a target, because
the separation a sound should have over the bed is the same undeclared
decision `music_behavior.SEPARATION_TARGETS_DB` is empty for.
