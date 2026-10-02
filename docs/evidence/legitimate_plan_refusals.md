# Legitimate plans that `compile_manifest` refused - history

Moved verbatim from the module docstring of `tests/scenarios/test_a_legitimate_plan_is_not_refused.py`
(test-suite halving, 2026-10-02). The tests keep the invariants; this keeps the incident.

```text
Two refusals in `compile_manifest` that killed runs over correct plans.

Both fire on output the planner is explicitly invited to produce, and both
are the same class of mistake: a legitimate plan met a refusal where the
right answer was a drop or nothing at all.

**A visual effect on a B-roll block.**  Step 4.03's candidate table lists
every spine block, cutaway blocks included, with their camera and
stability measured, under a handoff that says *"For each clip in the shot
list"*.  Nothing told it an effect could only land on V1.  It planned two
on cutaways - three of the four blocks measuring `stationary` + `stable`
ARE cutaways - and the compile answered::

    ValueError: 2 VFX entries do not overlap any V1 clip:
        slow_zoom_in@41.322s, slow_zoom_out@55.001s

The renderer builds per-clip comps on V1 **and** V2
(`execution/fusion_tracks.FUSION_COMP_TRACKS`), and `compile_manifest`
merges the house look onto both thirty lines above the refusal, so the
capability was never missing.  `library/tools/vfx_carriers.py` now states
the fact per block in the table the planner reads, and an entry over a
stretch with no clip on either track is DROPPED with the reason rather
than raised (AGENTS.md §10.5).

**Two layered sounds at one span.**  The overlap check exempts A3 because
it is a logical bucket - the builder spreads overlapping SFX across A3,
A4, ... - but the duplicate-POSITION check sat one indent out and ran for
every track, defeating that exemption whenever two layers resolved to the
same span::

    ValueError: Track A3: sfx_005 and sfx_004 both occupy (55.001, 58.001)
        - only one would be visible

`manifest_validator._check_sfx_distributed` has always read a shared
position as layering and refused only a collapse, so the two halves of
the pipeline disagreed about the same manifest.
```
