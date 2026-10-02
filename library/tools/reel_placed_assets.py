"""The reel build's placed files live durable, never in scratch.

The defect this closes
----------------------
`scratch/` is declared safe to throw away at any moment, including
mid-run (`Kind.SCRATCH`). Yet two things the reel builder places on
live timelines were rendered into it:

- TV-frame overlays (`reel_look.frame_overlay_segments` renders into
  `scratch/reel_look/frame_overlays/` and the builder places the path
  it returns - measured 2026-09-09 on `lucie/geo-podcast`: five
  overlays, 1.5 GB, every one of them on V2 of a live reel timeline,
  read off the live Resolve database, not off filenames);
- full-frame cards (`reel_build.card_render_dir` renders into
  `scratch/reel_cards/` and the builder places `rendered_path`).

So the declaration was a lie for those paths, and anything trusting
it - a cleaner, a re-run, a disk sweep - takes the picture off the
captain's timelines. The same class as the caption ledger that dropped
`tight_fallback`: a record that cannot be believed is worse than no
record.

The fix, and why this shape
---------------------------
The artefacts are not scratch, so the placement path stops referencing
scratch: every overlay and every card is PROMOTED into a step-owned
OUTPUT area (`Area.REEL_FRAME_OVERLAYS` /
`Area.REEL_CARDS`, both `build_reels`' own directories under
`pipeline_output/steps/`) and the durable copy is what gets imported
and placed. Moving the already-placed scratch files was the alternative
with the larger blast radius - it relinks live timelines, which is a
partial reel rebuild, and rebuilds are out of scope - so the five live
overlays stay where they are, pinned by the reachability record, and
every NEW placement lands durable.

`reel_look.py` still renders into scratch as its own build cache; this
module does not touch it (a live lane owns that file). Promotion is a
hard link where the filesystem allows it and a copy where it does not,
so it costs no extra bytes in the common case and the scratch original
may go whenever nothing references it - deleting the scratch link never
removes data the durable link still holds.

`assert_placeable` is the guard at each placement site: a path under
scratch/ raises rather than reaching Resolve. The promotion helpers
call it on what they return, so the invariant is proved at the moment
of placement, not hoped for.

`tests/unit/reels/test_promotion.py`.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from library.tools.project_layout import Area, ProjectLayout


class PlacedAssetInScratch(RuntimeError):
    """A timeline placement names a file under scratch/. Refused."""


def scratch_dir_for(project_folder: str) -> str:
    """The project's scratch directory, absolute and normalised."""
    return os.path.normpath(str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)))


def is_under_scratch(path: str, project_folder: str) -> bool:
    """Whether `path` sits inside this project's scratch area."""
    if not path:
        return False
    scratch = scratch_dir_for(project_folder)
    candidate = os.path.normpath(str(path))
    if not os.path.isabs(candidate):
        candidate = os.path.normpath(
            os.path.join(str(ProjectLayout(project_folder).root), candidate)
        )
    return candidate == scratch or candidate.startswith(scratch + os.sep)


def assert_placeable(path: str, project_folder: str) -> str:
    """Return `path` unless it is under scratch/, else raise.

    The guard every reel overlay placement calls before importing: a
    file a timeline will point at must live somewhere a cleaner may
    not throw away, and scratch/ is exactly what a cleaner throws
    away. A scratch path here is the structural defect recurring, and
    it is refused at the point of knowledge rather than placed and
    mourned later.
    """
    if is_under_scratch(str(path or ""), project_folder):
        raise PlacedAssetInScratch(
            f"{path!r} is under {scratch_dir_for(project_folder)}/, which "
            f"Kind.SCRATCH declares safe to discard at any moment. A file a "
            f"timeline places must live in a durable area - promote it with "
            f"reel_placed_assets.promote_to_durable first "
            f"(Area.REEL_FRAME_OVERLAYS for frame overlays, Area.REEL_CARDS "
            f"for full-frame cards)."
        )
    return path


def promote_to_durable(src_path: str, project_folder: str, area: Area) -> str:
    """Copy `src_path` into the durable `area` and return the new path.

    Idempotent: a destination that already holds the same bytes (same
    size, and not older than the source) is reused rather than
    re-written. A hard link is preferred where the filesystem allows
    it - same bytes, no extra disk - with a copy as the fallback, so
    the scratch original and the durable placed file share nothing but
    bytes: removing either leaves the other intact.
    """
    src = str(src_path or "")
    if not src or not os.path.isfile(src):
        raise PlacedAssetInScratch(
            f"cannot promote {src!r}: it is not a file. A placement needs "
            f"bytes on disk, not a name."
        )
    if not is_under_scratch(src, project_folder):
        # Already durable - nothing to promote, but still proved.
        return assert_placeable(src, project_folder)
    layout = ProjectLayout(project_folder)
    spec = layout.spec(area)
    dest_dir = str(layout.write_dir(area, step=spec.step or "build_reels"))
    dest = os.path.join(dest_dir, os.path.basename(src))
    if os.path.isfile(dest):
        try:
            same = os.path.samefile(src, dest) or os.path.getsize(
                dest
            ) == os.path.getsize(src)
        except OSError:
            same = False
        if same and os.path.getmtime(dest) >= os.path.getmtime(src):
            return assert_placeable(dest, project_folder)
    try:
        if os.path.isfile(dest):
            os.unlink(dest)
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
    except OSError as exc:
        raise PlacedAssetInScratch(
            f"could not promote {src!r} into {dest!r} ({exc}): the "
            f"placement has nowhere durable to point."
        ) from exc
    print(
        f"  promoted {os.path.basename(src)} "
        f"({os.path.getsize(dest)} B) to {area.value}",
        file=sys.stderr,
    )
    return assert_placeable(dest, project_folder)


def promote_frame_overlays(segments: list | None, project_folder: str) -> list:
    """The frame overlay segments, re-pointed at durable copies.

    Returns new dicts - the caller's segments are left alone, because
    the renderer owns its cache paths and the timeline owns what was
    placed. Each `overlay_path` under scratch is promoted into
    `Area.REEL_FRAME_OVERLAYS`; one already durable passes through
    (proved, not copied).
    """
    out = []
    for segment in segments or []:
        promoted = dict(segment)
        if promoted.get("overlay_path"):
            promoted["overlay_path"] = promote_to_durable(
                str(promoted["overlay_path"]), project_folder, Area.REEL_FRAME_OVERLAYS
            )
        out.append(promoted)
    return out


def promote_cards(cards: list | None, project_folder: str) -> list:
    """The rendered cards, re-pointed at durable copies.

    Cards arrive as `PlannedCard` (frozen dataclass) or dicts; either
    way the return carries the same objects with `rendered_path`
    promoted into `Area.REEL_CARDS`. A card with no file passes
    through untouched - the builder's own missing-file refusal stays
    the thing that reports it, here unchanged.

    A file that is ALREADY durable passes through as it is. Promotion
    exists because `Kind.SCRATCH` is declared safe to throw away at any
    moment; a file outside scratch has no such declaration over it, and
    copying one anyway detaches the timeline from the file whoever owns
    it maintains. The case this is written for is
    `full_frame_element.full_frame_clip`: the project's own brand
    animation, which the engine places verbatim and never re-authors
    (AGENTS.md 13) - a promoted copy of it would go stale in silence
    the next time the series re-cuts its logo.
    """
    import dataclasses

    out = []
    for card in cards or []:
        if isinstance(card, dict):
            path = card.get("rendered_path") or ""
            if (path and os.path.isfile(str(path))
                    and is_under_scratch(str(path), project_folder)):
                card = dict(card)
                card["rendered_path"] = promote_to_durable(
                    str(path), project_folder, Area.REEL_CARDS
                )
            out.append(card)
            continue
        path = getattr(card, "rendered_path", "") or ""
        if (path and os.path.isfile(str(path))
                and is_under_scratch(str(path), project_folder)
                and dataclasses.is_dataclass(card)):
            out.append(
                dataclasses.replace(
                    card,
                    rendered_path=promote_to_durable(
                        str(path), project_folder, Area.REEL_CARDS
                    ),
                )
            )
        else:
            out.append(card)
    return out


def durable_copy_exists(src_path: str, project_folder: str, area: Area) -> str | None:
    """The durable copy of `src_path`, or None when there is none.

    The read-only half of promotion, for verifiers and sweeps: it
    answers where the placed file lives without creating anything.
    """
    layout = ProjectLayout(project_folder)
    dest = Path(str(layout.read_dir(area))) / os.path.basename(str(src_path))
    return str(dest) if dest.is_file() else None
