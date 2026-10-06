"""The deliverable set: video variants plus the ONE shared stem set. One enumeration.

The track this module belongs to
-------------------------------
Capability 18 of the captain's complete-stack spec (2026-10-06): Delivery +
adaptation, agency addition - derive a dependency graph so the shared
work is done once and only the true variants are produced. This module
is the graph's top: the video-variant plan
(``deliverable_matrix.plan_deliverables``) beside the audio-stem plan
(``stem_plan.plan_stems``), composed so the stems are shared across
every video variant rather than multiplied by them.

What was wrong before
--------------------
Video variants and audio stems were planned on separate paths with
nothing saying how they combine - so a project shipping two aspects
with dialogue and music could read as four stem renders (one dialogue
and one music stem per aspect) instead of two video renders sharing
one stem set. Audio does not change with the crop: the dialogue and
music that play under the 9:16 reframe are the same files that play
under the master, so stems are aspect-independent and rendered once,
muxed per variant by the later render slice.

So this module returns both plans together, with the stem set stated
once. The video job count grows with distinct aspects; the stem count
does not grow with aspects at all - that independence is the behavior
this module exists to pin.

What this module does not do
----------------------------
It does not render, mux, master or QC anything, and it adds no variant
axes: platforms come from the matrix, roles from the stem plan. Hooks,
languages and compliance land on the matrix when a project declares
them, and reach this set through it, not around it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence

from library.tools.deliverable_matrix import (
    DeliverablePlan,
    plan_deliverables,
)
from library.tools.stem_plan import StemPlan, plan_stems


@dataclass(frozen=True)
class DeliverableSet:
    """The full export set: video variants plus the shared stem set.

    ``video`` holds one job per distinct render. ``audio`` holds the
    stem roles that carry clips, stated ONCE for the whole set - every
    video job muxes from this same set, so two aspects never mean two
    dialogue stems.
    """

    video: DeliverablePlan
    audio: StemPlan

    @property
    def video_render_count(self) -> int:
        """The video encodes this set produces."""
        return self.video.render_count

    @property
    def stem_roles(self) -> list:
        """The stem roles this set exports, in mix order."""
        return self.audio.roles


def plan_deliverable_set(platforms: Sequence[str],
                         tracks: Dict) -> DeliverableSet:
    """Plan the video variants and the shared stem set together.

    ``platforms`` are the requested platform names (the matrix groups
    identical specs, so four identical verticals are one job);
    ``tracks`` is the manifest's mix-track mapping (roles with no clips
    plan no stems). Neither argument is defaulted: an undeclared
    platform list and an undeclared mix are different facts from the
    defaults, and the two plans own their own "declares nothing" rules.
    """
    return DeliverableSet(
        video=plan_deliverables(platforms),
        audio=plan_stems(tracks),
    )
