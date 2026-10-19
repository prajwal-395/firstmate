# Composition

Break an animation into scenes and lay out elements according to the
content, delivery format and declared direction. Do not impose generic
scene lengths, grids, margins or color counts.

## Scene decomposition

Give each scene a clear job. Split or combine material according to
what the viewer needs to follow. Derive holds from the copy, speech and
pacing instead of a fixed duration.

## Layout and safe areas

Read the delivery format and use `library/tools/safe_area.py` for the
measured platform-safe area. Resolve dimensions against the actual
output frame. Keep text and key elements inside that area, then inspect
the rendered pixels for clipping or collisions.

Align elements where it improves hierarchy. Use spacing, type and color
values from the project or brief; when those values are absent, do not
invent a house style. Ask if the missing choice affects the creative
result materially.

## Scene flow

Choose cuts, transitions or carried elements from the scene relationship
and declared intent. Read `transitions.md` when transition design is a
material part of the work.
