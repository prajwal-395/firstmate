# `library.tools.reel_ending` - the history behind its contract

This is the module docstring of `library/tools/reel_ending.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Where a reel ENDS, and what plays over its tail.

The defect this closes
----------------------
Reel 13, 2026-09-11.  The captain typed it onto the timeline himself,
at the frame where it goes wrong::

    "it cuts to craig here at the end which is a little bit too much,
     it should just end at the end of the clip of akshita. and it
     should also do the tv off animation which was reomved for some
     reason"

Two complaints, ONE cause.  An earlier lane wanted the closing line to
finish before the television switched off, and the only vocabulary it
had for "give the ending room" was the reel's last KEEP RANGE - so it
extended it, 341.27s -> 342.03s.  A keep range is master-timeline
seconds and `reel_build.placements` cuts one range against every master
clip it overlaps, so the extra 18 frames did not lengthen the shot that
was playing: they crossed the master's own cut and admitted the NEXT
shot.  Twelve frames of Craig appeared at the end of the reel.

That cut then removed the switch-off as well.  `reel_look.power_effects`
arms `tv_power_tail` on the LAST picture clip, whatever that clip turns
out to be, and the last picture clip was now Craig's 12 frames.  The
switch-off is 18 frames (`library/tools/tv_power.py`), `treatment_verify`
measured that it could not draw inside 12, and undid it - correctly, and
to stderr in a build log nobody read.  So the reel gained a cut nobody
asked for and lost an animation everybody wanted, from one edit, and
nothing said either had happened.

Why a new owner
---------------
Neither decision had anywhere to live.  "This reel ends at the end of
shot X" and "element Y plays over the tail" are not properties of a keep
range, not properties of the look, and not properties of whichever clip
happens to sort last.  `library/tools/edit_depth.py` is the map of which
layer owns which edit; this module is the `ending` row of it.

An ending TRUNCATES, never extends.  That is the whole lesson of the
defect and it is enforced here rather than documented: `apply_ending`
takes `min(current_end, shot_end)` and a declaration that would reach
past the shot is refused by name.  A reel cannot acquire a shot it did
not plan by asking for breathing room.

What a declaration says
-----------------------
`<project>/external/reel_ending.json`, checked and never asserted (the
`overlay_intent.json` / `placed_assets.json` precedent)::

    {"version": 1,
     "endings": [{"reel": "Reel 13 - the-accounting-firm-ai-called",
                  "ends_on": {"anchor_phrase": "the link's in our bio"},
                  "tail_element": "tv_power_tail",
                  "reason": "captain 2026-09-11 marker @1909"}]}

`reel` is matched against the reel's timeline name by prefix, so a
staging suffix (`(scratch ...) (rebuild staging)`) names the same reel
as the promoted timeline - one declaration serves the build and every
rebuild of it.

`ends_on.anchor_phrase` is the SPOKEN WORDS the reel ends on, anchored
into the master transcript exactly the way `span_retime` anchors a trim
(`library/tools/captain_edits.py`): words survive a re-cut, a
renumbering and a re-plan; a frame number survives none of them. The
shot carrying those words is the ending shot. Later playback ranges are
dropped, and the range carrying that shot is truncated to its own end.

`tail_element` names what DRAWS over the tail, from `TAIL_ELEMENTS`.  It
is a name, never a magnitude: how long a switch-off takes is
`library/tools/tv_power.py`'s declaration and a project's own `tv_frame`
override, and this module states no timing of its own (AGENTS.md 10.5).
`"none"` is the absence of decoration, not a choice of it, and is the
only other member.

The room the element needs is CHECKED, not hoped for.  `assert_tail_fits`
refuses a build whose ending shot is shorter than its declared tail
element, naming both counts - so the silent `treatment_verify` undo
that removed Reel 13's switch-off cannot happen behind a declaration
again.  An element that will not draw is a build that stops, because a
reel missing the thing the captain asked for is not a reel with a minor
omission.

Holding the last frame
----------------------
`tail_hold` decides WHERE the tail element draws.  `"none"` draws it
over the live tail of the ending shot, which is what an undeclared
reel has always done.  `"freeze"` HOLDS the ending shot's last frame
for exactly as long as the element needs and draws it over the held
frames, so the animation begins after the last word rather than over
it.

The captain chose the freeze for Reel 13 on 2026-09-11, over the two
alternatives, after seeing the switch-off play across the whole of
"The link's in our bio."  His words: *"do the freeze"*.

The hold's LENGTH is not a number this module states: it is the
element's own (`tail_room_frames`), so "the element plays entirely
after the words" is true by construction rather than by a value
somebody tuned.  A freeze therefore always fits, and `assert_tail_fits`
says so rather than skipping quietly.

The freeze is PICTURE ONLY.  Audio is not held: the speaker's voice
plays to its natural end and the held frames carry whatever silence
follows, because a stretched voice is a different edit and nobody
asked for one.

The freeze belongs to the CALL TO ACTION, not to a list of reels
--------------------------------------------------------------
Reel 13 was the first reel to get the freeze and it got it from a
hand-written entry.  Four more reels then arrived with the same
complaint, and the captain's instruction on two of them is what
decides the shape here: *"this change needs to be applied to all other
reels that currently also use this CTA **or will be using this CTA**"*.

Four more entries would satisfy four reels and fail that sentence, so
the freeze is INHERITED instead: `cta_default_ending` gives it to any
reel whose plan closes on a `reel_proposal.CallToAction`, and a
per-reel entry in `external/reel_ending.json` exists to OVERRIDE that,
never to supply it.  A reel planned tomorrow, on a CTA nobody has
written a pin for, closes the way Reel 13 does.

It hangs on the TYPE, not on a passage or a speaker, because measuring
the plan showed there is no single passage to hang it on.  The four
reels complained about close on THREE different call-to-action
passages and Reel 13 on a fourth; Reel 28's closer is CRAIG.  What all
five share is that each closes on a `CallToAction` - and that type's
own docstring says why it is the only home available: *"the episode's
CTAs are where they are: six of them, scattered, and every reel has to
end on one"*.  A reel whose plan declares none (three of this
episode's thirty-one moments) inherits nothing and builds exactly as
it did before.

Two things are inherited, because the captain named TWO faults
--------------------------------------------------------------
*"the last bit of akshita's audio is cut off **and also** the tv off
animation occurs while she is still talking"*.  They are separate, and
measuring the footage says so: Reel 28's closing audio is clean and
only its animation is wrong, while Reels 01, 23 and 30 end at
`call_to_action.timeline_end` 341.270s - exactly where WhisperX labels
the end of "bio." - with the sound of that word still at 1307 RMS on
the last frame they play.

So a reel closing on a call to action inherits BOTH: the freeze
(`CTA_TAIL_HOLD`), and the closing BREATH (`closing_breath_end`),
which plays the trailing silence the aligner's word boundary cut off.

`tests/test_reel_ending_cta_default.py` is the gate: it fails the
moment a newly planned reel stops inheriting the freeze.

What this module deliberately cannot say
----------------------------------------
It cannot hold a shot PAST the master's own cut to make tail room.  The
footage is there - a synced podcast's camera keeps rolling after the
rough cut leaves it - but nothing in the pipeline has judged those
frames, and reading into them would put unreviewed picture on a
delivered reel.  That is the alternative the captain declined; the
freeze is the one he chose, and it uses only a frame the reel already
plays.  A tail that does not fit inside the shot and declares no
freeze is a refusal here.

`tests/test_reel_ending.py`, `tests/test_orphan_wiring.py`.
```

## `tests/test_reel_ending_cta_default.py` module docstring (moved 2026-10-02)

```text
A reel INHERITS its freeze ending from the call to action it closes on.

The captain, 2026-09-11, on Reels 01 and 23: *"this change needs to be
applied to all other reels that currently also use this CTA **or will
be using this CTA**"*.  The second half of that sentence is what these
tests are for.  Four hand-written entries in `external/reel_ending.json`
would have satisfied the four reels he named and failed the
instruction, because a reel planned tomorrow would have closed the old
way with nothing to say so.

So the freeze hangs on `reel_proposal.CallToAction` - the thing a reel
closes ON - and `test_a_reel_nobody_declared_anything_for_inherits_the_
freeze` is the gate: it fails the moment a newly planned reel stops
inheriting it.

Measured on the field test's own plan and stated here because it is why
this cannot hang on a passage or a speaker: the four reels complained
about close on THREE different call-to-action passages and Reel 13 on a
fourth, and Reel 28's closer is CRAIG.

Synthetic under `tmp_path` (AGENTS.md 8); nothing here reaches Resolve
or a real project.
```
