# Transition `duration_frames` uses the project's measured timebase

Tests: `tests/unit/picture/test_transition_planning.py` (moved from its module docstring, 2026-10-02).

`duration_frames` is computed from the timebase the catalog MEASURED.

The defect
----------
`step_4_02_plan_transitions/post_bridge.py` read
`data.get("frame_rate", 30.0)`.  **Nothing in this pipeline has ever
produced a key called `frame_rate` at the top level of a step's inputs**
- the catalog measures the timebase and calls it `project_fps` - and no
edge carried `project_fps` to this step either.  So the default won on
every run.

Step 4.04 was given the same edge and the same read in #124, whose
manifest line says it in as many words: *"Every consumer used to read its
own 30.0 default because no edge carried it."*  Two of the three
consumers were left behind.

What it cost, measured
----------------------
`duration_map` scales with `frame_rate / 30`, so at the wrong 30.0 a
transition gets 30-fps frame counts played at the project's real rate.
The captain's `lucie/geo-podcast` is 23.976 fps:

    frame_rate=30.0    quick=6  medium=10 slow=15 frames
                       -> played 250 / 417 / 626 ms
    frame_rate=23.976  quick=4  medium=7  slow=11 frames
                       -> played 167 / 292 / 459 ms

Every drawn transition on that project held roughly 50% longer than the
word the model wrote asked for.

Both directions, per AGENTS.md 10.4: the wrong timebase must CHANGE the
answer (or this test proves nothing), and the right one must produce the
frames the timebase implies.
