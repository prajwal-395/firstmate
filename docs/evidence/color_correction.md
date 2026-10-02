# `library.tools.color_correction` - the history behind its contract

This is the module docstring of `library/tools/color_correction.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The grade the COLOURIST decides, and how it composes with a declared look.

Step 5.01 measured project 001's nine graded clips correctly and then did
nothing with the measurement.  Adjacent shots in the finished cut read:

    clip_011   145.495   bright outdoor plaza
    clip_013   130.691
    clip_017    53.116   dim car interior, the shot the video ends on

- a 2.7x spread the pipeline measured, wrote down, and answered with the
IDENTITY CDL on all nine clips: slope 1/1/1, offset 0/0/0, power 1/1/1,
saturation 1.0.  An identity CDL is a no-op, so Resolve drew no node and
the captain opened the colour page to find it empty.

**The step was not broken; its authority was missing.**  Normalisation
was reachable only through `exposure_reference`, and only a brand
template can declare one.  001 names no template, so nothing could act on
what had been measured.  That guard was itself a correction of a real
prior defect - the step used to compare every clip against a hardcoded
122.0, which is a decision about how bright the finished video is taken
by nobody - and reverting to a constant would reinstate it.  The fix is
not a different constant.  It is that the decision belongs to somebody,
and the only party in the loop who can look at a dim car interior and say
"that one is dark on purpose" is the model.

Captain, on the colour half specifically:

    *"i think we need to still let the LLM understand it should try to add
    some color grading if it thinks it is needed rather than saying no
    completely bc of a lack of brand template (like this is another aspect
    of the creative reasoning i was talking about that the LLM should be
    able to handle)."*

Why this is not `exposure_reference` wearing a new hat
------------------------------------------------------
`exposure_reference` stays exactly what it is: a `LookElement` a brand
TEMPLATE declares, one scalar, meaning *this series wants its clips to sit
at this luma*.  It is a per-series constant and it is a DECLARATION.

The craft layer is a different thing on three counts, and each one on its
own would be enough:

1. **It is per clip, and it is not one target.**  A colourist may bring
   two bright shots down, leave a third, and lift a dark one only part of
   the way because its darkness is the content.  One scalar cannot say
   that, and forcing it through one would make "normalise everything to
   the mean" the only expressible answer - which is the constant-122
   defect with a model's name on it.
2. **It is a JUDGEMENT, not a declaration, and the output has to be able
   to tell them apart.**  Writing a model's answer into the template slot
   would make `series_look.exposure_reference` mean two different things
   depending on who wrote it, and `resolve_look` refuses unknown keys
   precisely so that no value arrives from somewhere nobody can name.
3. **A template that declares one must keep winning.**  Keeping the two
   fields apart is what makes that precedence expressible, recordable and
   testable.

So the correction is its own field, `color_correction`, and a project
that declares a template gets that template's look with the correction
composed underneath it.

How the two compose, exactly
----------------------------
An ASC CDL is ``out = (in * slope + offset) ** power`` per channel, then a
saturation term.  Two CDLs applied in series do NOT flatten into one in
general - but they do for the order this uses, which is why this order
and not another:

    1. the correction's exposure gain ``g = 2 ** exposure_stops`` and its
       per-channel ``slope`` multiplier ``c``      (a pre-scale)
    2. the declared look's ``slope`` ``s`` and ``offset`` ``o``
    3. the correction's ``offset`` shift ``d``     (same linear stage)
    4. the declared look's ``power`` ``p``
    5. the correction's ``power`` ``q``
    6. the two saturations

    out = ( in * (g * c * s) + (o + d) ) ** (p * q)
    saturation = look_saturation * correction_saturation

Every step is exact.  ``(x ** p) ** q == x ** (p * q)``; a pre-scale folds
into the slope because ``(in * g) * s == in * (g * s)``; an offset added in
the same linear stage is addition; and the luma-preserving saturation
formula composes multiplicatively because it leaves luma alone.  Nothing
here is an approximation dressed as arithmetic, which matters because the
result is the only CDL the renderer ever sees.

Gamma is offered.  It is exact under (4)/(5) above, and lift/gamma/gain is
the vocabulary a colourist actually corrects in; withholding one of the
three would be this file deciding which moves a professional may make.

What is refused, what is dropped, and what is recorded
------------------------------------------------------
* A malformed VALUE - a slope that is not three numbers, a level that is
  not a number - RAISES.  It is a contract violation, and
  `library/tools/post_bridge_retry.py` carries a raised violation back to
  the model that caused it.  Refusing is how it gets fixed; dropping is
  how it goes quiet.
* An entry that names no clip in the cut, names no correction term, or
  names no reason is DROPPED with the reason recorded.  Same shape as
  `vfx_plan_basis` and the same reason: the plan entry is under-specified
  rather than wrong, and completing it means this file choosing a number.
  `DROP_REASONS` is the whole of what a drop can be for and a reason
  outside it is refused by name.
* **`planning_basis` says which ABSENCE an ungraded run is.**  Four
  readings, spelled differently on purpose:
  ``corrected``, ``judged_no_correction_needed`` (a decision),
  ``no_correction_decision`` (nobody decided - what a run that never
  reached a model records), and ``every_entry_dropped`` (the absence of a
  decision, not a decision to do nothing).  The old output could not tell
  the second from the third: an identity CDL read the same whether the
  colourist had looked and approved or whether no colourist existed.

There are no bounds.  How far a correction may travel is the colourist's,
the same way `series_look` has no bound on how far a declared slope may
go: an engine-supplied range is a strength nobody chose arriving one
level up (AGENTS.md 10.5).

`tests/unit/picture/test_color_correction.py`, `tests/unit/picture/test_color_grade_delivery.py`.


Rules relocated from AGENTS.md 12
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 12
keeps the headline and points here.

**A project that names no template still gets a GRADE, because a colourist decides one.**
One enumeration, `library/tools/color_correction.py`. [why - the nine measured clips and the identity CDL](docs/RULE_EVIDENCE.md#the-step-that-measured-nine-clips-and-graded-none) Step 5.01 measured 001's nine clips across a 2.7x luma spread - clip_011 at 145.495, clip_017 at 53.116 - and wrote the identity CDL on all nine, because normalisation was reachable only through an `exposure_reference` only a template declares. Captain, 2026-09-03: *"we need to still let the LLM understand it should try to add some color grading if it thinks it is needed rather than saying no completely bc of a lack of brand template."*
- **The correction is its own field, NOT `exposure_reference` reused.** That slot is a per-SERIES scalar a template DECLARES; a correction is per-clip, is a JUDGEMENT, and a template that declares one must keep winning. Writing a model's answer into a template slot would make the key mean two things depending on who wrote it.
- **The two compose EXACTLY, in a stated serial order**: `out = (in * (2**exposure_stops * slope * look_slope) + (look_offset + offset)) ** (look_power * power)`, saturations multiplied. Every step is exact - `(x**p)**q == x**(p*q)`, a pre-scale folds into slope, a same-stage offset adds - which is why the order is fixed. **A declared look with no correction is byte-for-byte `look.cdl()`.**
- **No bound and no default.** How far a correction may travel is the colourist's, the same way `series_look` bounds no declared slope. A malformed VALUE RAISES so `post_bridge_retry` carries it back to the model; an entry naming no clip, no term or no `why` is DROPPED with the reason (`DROP_REASONS`, refused if outside).
- **`correction_basis` says which absence an ungraded run is.** FOUR readings, spelled differently on purpose: `corrected`, `judged_no_correction_needed` (a decision), `no_correction_decision` (nobody looked), `every_entry_dropped`. The old output could not tell the second from the third - an identity CDL read the same either way.
- **`WITHHELD_TERMS` records what a correction may NOT say** and where it lives instead: `temperature` (no CDL term; say it as slope and offset), `contrast` (Fusion's, not the CDL's), `curve` (no reader anywhere).
- 5.01 is now HYBRID: `bridge.py` measures and builds `clip_exposure` + `cut_adjacency` (the pairs a viewer sees, in stops), `handoff.md` asks a colourist, `post_bridge.py` composes. `tests/unit/picture/test_color_correction.py`, `tests/unit/picture/test_color_grade_is_decided.py`.
```
