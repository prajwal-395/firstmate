"""The stem plan: which audio stems a project actually has. One enumeration.

The track this module belongs to
-------------------------------
Capability 18 of the captain's complete-stack spec (2026-10-06): Delivery +
adaptation - masters, stems, captions, social crops and reframing,
platform variants, codec and loudness targets, QC. This module is the
stems half's first slice: the PLAN, derived offline from the manifest's
mix tracks. Rendering stems is a later slice on live Resolve (each stem
is a Fairlight bus solo plus a render); planning which stems exist is
pure data and lands now.

What was wrong before
--------------------
The mix knew its tracks only at render time: dialogue played at unity
on A1, the music bed curved on A2, sound effects sat static on A3 and
up - and nothing outside the render path could say which of those a
project actually HAS. An agency deliverable set wants dialogue, music
and SFX stems beside the full mix, but exporting a stem per role for
every project means silent files where a role carries no clips: a
voiceover-only video would ship an empty music stem and an empty SFX
stem, produced from scratch rather than recognised as having nothing
in them.

So this module groups the manifest's mix-track clips by the same role
rule the mix itself applies (``otio_mix.track_role`` - one owner, two
readers) and plans only the roles that carry clips. A role with no
clips is absent from the plan, never an empty job: only the true stems
are produced.

What this module does not do
----------------------------
It does not render, solo, mix or master anything, and it sets no
levels: each stem names its source clips and the mix stays the single
authority on what they play at. An agency that wants silent stems for
empty roles anyway is a declaration no project makes today, and it
lands with that declaration, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from library.tools.otio_mix import track_role


@dataclass(frozen=True)
class StemClip:
    """One clip feeding a stem: the file and what the plan calls it."""

    source_file: str
    label: str


@dataclass(frozen=True)
class StemJob:
    """One stem to export: every clip of one role.

    ``role`` is ``"dialogue"``, ``"music"`` or ``"sfx"`` - the mix
    track vocabulary, never a new one. ``clips`` is never empty: an
    empty role is absent from the plan rather than planned as silence.
    """

    role: str
    clips: Tuple[StemClip, ...]

    @property
    def clip_count(self) -> int:
        """The clips this stem exports."""
        return len(self.clips)


@dataclass(frozen=True)
class StemPlan:
    """Which stems a project has, keyed by role.

    ``stems`` holds only non-empty roles. ``roles`` is their names in
    mix order (dialogue, music, sfx) - the order a render walks them,
    not the order they were found.
    """

    stems: Dict[str, StemJob]

    @property
    def roles(self) -> List[str]:
        """The planned roles, in mix order."""
        order = ("dialogue", "music", "sfx")
        return [role for role in order if role in self.stems]


def plan_stems(tracks: Dict) -> StemPlan:
    """Group mix-track clips into export stems, skipping empty roles.

    ``tracks`` is the manifest's ``tracks`` mapping (``A1``/``A2``/...
    to ``{"clips": [...]}``). A clip without a source file names
    nothing to export and is skipped; a role left with no clips is
    absent from the plan. Anything that is not a mapping is no tracks,
    and plans no stems - an absent mix is a fact, not an error.
    """
    grouped: Dict[str, List[StemClip]] = {}
    if isinstance(tracks, dict):
        for name, track in tracks.items():
            role = track_role(name)
            if role is None:
                continue
            clips = (track or {}).get("clips", []) or []
            if not isinstance(clips, list):
                continue
            for clip in clips:
                if not isinstance(clip, dict):
                    continue
                source = (clip.get("source_file") or "").strip()
                if not source:
                    continue
                label = (clip.get("label") or "").strip() or source
                grouped.setdefault(role, []).append(
                    StemClip(source_file=source, label=label))
    return StemPlan(stems={
        role: StemJob(role=role, clips=tuple(grouped[role]))
        for role in grouped
    })
