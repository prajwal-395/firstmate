"""Two SFX functions that were deleted, and the reasons they stay deleted.

Nothing here is called and nothing here should be re-added.  The module
is kept because a deletion with no record invites the next reader to
write the same thing again: both functions read plausible, both state
taste as fact, and both were dead for years without anyone noticing.

`align_sfx_to_prosody` - DELETED 2026-09-07, never once fired
--------------------------------------------------------------
It took the creative plan and a `prosody_analysis` dict and claimed to
snap whoosh/transition sounds to a pause boundary and impact sounds to
an emphasis peak.  `step_4_04_plan_sfx/post_bridge.py` called it behind
`if prosody_analysis:`.  It could not fire, for FIVE independent
reasons at once - the first three were found by the output survey
(#598), the last two by following the value to the end:

1. **Nothing routed the input.**  No edge in
   `library/processes/edit_video/dag.json` maps `prosody_analysis` into
   `plan_sfx` (1.05 routes to `creative_direction` and
   `speech_sequence` only), and 4.04's manifest declares no such input.
   A post-bridge's stdin is `gather_step_inputs`' dict, so the key was
   absent on every run and the guard was never true.
2. **The shape was wrong.**  It read `pauses` and `emphasis_peaks` at
   the TOP LEVEL; step 1.05 emits
   `{available, profiles: {clip_id: ...}, total_clips, error}`.
3. **The measurement does not exist.**  `analysis/speech_advanced_pipeline.analyze_prosody`
   has never emitted either key under any name: it produces
   `pitch_stats`, `pitch_contour_10ms`, `voice_quality`,
   `speaking_rate`, `intensity_contour_50ms` and `duration_s`.  This is
   the REAL reason - 1 and 2 are routing, and there was nothing to
   route.
4. **It branched on a vocabulary the step withdrew.**  The branches
   tested `sfx["type"]` for "whoosh"/"transition"/"impact".  A plan
   entry is `{spine_block_position, sfx_id, volume_db,
   duration_seconds, rationale}` - there is no `type`.  The abstract
   type words went when placement was re-keyed onto the sound's own
   MEASURED envelope, because "a strategy selected by a distinction
   that does not exist in the library is not a strategy".  Project
   001's plan of record carries no `type` key at all.
5. **It wrote a key nothing reads.**  It set `sfx["start_time"]`.
   `post_bridge._locate_sfx` positions an entry from
   `spine_block_position` / `target_block_position` / `timeline_start`
   / `timeline_in`; `start_time` appears nowhere else in the step.
   Even a fired alignment would have been discarded.

**The capability is not lost, because the step already delivers it from
stronger measurements.**  Three lines below the call it deleted:

* `find_sfx_placement` snaps a `punchy` sound onto a measured audio
  ONSET within +/-200 ms, falling back to a measured energy peak - the
  "impact on an emphasis peak" idea, keyed on the sound's measured
  envelope rather than on a word in its name;
* `_avoid_speech_collision` moves a sound that would land on speech
  into the nearest GAP BETWEEN WORDS, computed from WhisperX word end
  times in the timeline domain - the "snap to a pause boundary" idea,
  at ~10 ms precision, and for every sound rather than for two name
  prefixes.

On project 001's run of record the one planned sound records
`placement_method: "scene-boundary/block-edge -> onset-snap (+/-100ms)"`,
which is that mechanism reporting which route it took.

**Cost of resurrecting it instead, ranked LAST.**  A pause detector and
an emphasis-peak detector would both have to be written (neither
exists), a threshold would have to be invented to call a contour sample
a "peak" - which AGENTS.md 10.5 forbids - a clip-time to timeline-time
mapping would have to be added, and the result would compete with two
mechanisms already reading better signals.  It buys nothing the step
does not already do.

`scale_sfx_density` - DELETED 2026-08-20
-----------------------------------------
It took the plan and an energy word and returned a SHORTER plan: on
"calm" it kept only transition, whoosh and ambient entries; on
"moderate" it dropped every second impact.  The energy word it judged
by was read from a key `creative_direction` does not have, so in
practice the constant "moderate" decided it on every run.

How many sound effects a piece gets is the creative direction's call
(captain's ruling 2026-08-20).  A function that deletes half of them
because a constant says the piece is "moderate" is a creative floor in
the other direction, and leaving it here uncalled would state that
taste as fact for the next reader.

Both deletions are guarded by the creative-code rules in
`library/tools/static_check.py`.
"""
