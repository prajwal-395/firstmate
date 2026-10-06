"""The agency deliverable matrix: shared work once, true variants only. One enumeration.

The track this module belongs to
-------------------------------
Capability 18 of the captain's complete-stack spec (2026-10-06): Delivery +
adaptation. The 2026-10-06 agency addition to this lane: derive a
dependency graph from master -> hooks -> aspect ratios -> languages ->
market compliance so the shared work is done once and only the true
variants are produced, instead of rendering every combination from
scratch. This module is the first slice of that graph: the
master -> aspect-ratio half, derived from the delivery-spec registry.

What was wrong before
--------------------
Requesting N platforms meant N renders. But the registry's rows show
the four vertical platforms carry byte-identical requirements -
1080x1920, 30 fps, H264/mp4, -14 LUFS, burned-in captions - so four
requested platforms meant four identical encodes of the same picture.
And every vertical deliverable is a reframe of the same 16:9 master
picture, yet nothing said so: the master render the reframe reads was
not named anywhere in the plan, so each variant looked like work from
scratch rather than shared work plus a crop.

So this module groups the requested platforms by what their renders
actually require. Platforms whose specs agree on every render-governing
field share ONE job; the 16:9 master picture every reframe reads is
named as the shared source rather than re-rendered per variant.

One enumeration
---------------
Like ``delivery_spec`` and ``delivery_format``, an unknown platform name
RAISES. A silent fallback is how a master ships to the wrong platform's
spec. Two platforms share a job exactly when their specs agree on
:data:`render_key` - the fields that determine the encoded file - never
on the platform name or the provenance string.

What this module does not do
----------------------------
It does not render, reframe, master or QC anything. It is the plan those
actions read: ``reframe.py`` is the reader of ``needs_reframe``,
``render_qa`` stays the gate on each produced file. The hooks,
languages and market-compliance axes of the agency matrix are not here
yet: no project declaration names a hook variant, a deliverable
language or a compliance profile, and a declared output with no reader
is refused - each axis lands with the declaration that feeds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from library.tools.delivery_spec import (
    DEFAULT_DELIVERY_PLATFORM,
    DeliverySpec,
    resolve_delivery_spec,
)


def render_key(spec: DeliverySpec) -> Tuple:
    """The fields that determine the encoded file, as a comparable key.

    Two platforms with equal keys are satisfied by ONE render: same
    frame, same rate, same codec and container, same loudness target
    and ceiling, same caption carriage. The platform NAME and the
    provenance string are deliberately excluded - ``tiktok`` and
    ``instagram_reels`` are different names for the same render, which
    is the defect this module exists to prevent.
    """
    return (
        spec.width, spec.height, spec.fps, spec.codec, spec.container,
        spec.loudness_target_lufs, spec.loudness_tolerance_lufs,
        spec.true_peak_ceiling_dbtp, spec.caption_format,
    )


def master_aspect() -> str:
    """The aspect the edit is authored in, read off the registry's master row.

    Every reframe starts from this picture (``reframe.py`` takes the 16:9
    master file as its input), so a job whose spec sits at this aspect IS
    the master render and every other job is derived from it. Read from
    the registry, never hardcoded: a hardcoded ``"16:9"`` beside the row
    that declares it is two constants that can drift.
    """
    return resolve_delivery_spec("master").aspect


@dataclass(frozen=True)
class DeliverableJob:
    """One render that satisfies every platform in ``serves``.

    ``spec`` is the representative registry row (the first requested
    platform in the group). ``needs_reframe`` means the job is built
    with ``reframe.py`` off the master picture; a job at the master
    aspect needs none because it IS the master picture. ``source`` says
    which: ``"master_render"`` or ``"reframe_of_master"``.
    """

    serves: Tuple[str, ...]
    spec: DeliverySpec
    needs_reframe: bool
    source: str


@dataclass(frozen=True)
class DeliverablePlan:
    """The derived dependency graph: shared work plus true variants.

    ``jobs`` holds one entry per distinct render - the count is the
    number of encodes, which is what "instead of rendering every
    combination from scratch" is measured in. ``requires_master_picture``
    names the shared work: any reframed job reads the 16:9 master
    picture, so the master is rendered even when nobody requested the
    master deliverable. When no job needs a reframe the master picture
    is only rendered if requested, and ``requires_master_picture`` is
    False.
    """

    jobs: Tuple[DeliverableJob, ...]
    requires_master_picture: bool

    @property
    def render_count(self) -> int:
        """The number of encodes this plan produces."""
        return len(self.jobs)


def plan_deliverables(platforms: Sequence[str]) -> DeliverablePlan:
    """Derive the render plan for the requested platforms.

    An empty request is not a declaration and yields the registry's
    default platform - the same rule ``resolve_delivery_spec`` applies.
    A repeated name is deduped, preserving first-seen order. An unknown
    name RAISES via ``resolve_delivery_spec``.
    """
    names: Tuple[str, ...] = (
        (DEFAULT_DELIVERY_PLATFORM,) if not platforms
        else tuple(dict.fromkeys(
            (name.strip() for name in platforms if name.strip())))
    )
    if not names:
        names = (DEFAULT_DELIVERY_PLATFORM,)
    specs = [(name, resolve_delivery_spec(name)) for name in names]

    groups: dict = {}
    for name, spec in specs:
        groups.setdefault(render_key(spec), []).append((name, spec))

    authored = master_aspect()
    jobs = []
    for key in groups:
        members = groups[key]
        representative = members[0][1]
        serves = tuple(name for name, _ in members)
        at_master = representative.aspect == authored
        jobs.append(DeliverableJob(
            serves=serves,
            spec=representative,
            needs_reframe=not at_master,
            source="master_render" if at_master else "reframe_of_master",
        ))
    return DeliverablePlan(
        jobs=tuple(jobs),
        requires_master_picture=any(job.needs_reframe for job in jobs),
    )
