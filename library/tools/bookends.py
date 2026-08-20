"""Intros, outros and end cards: one enumeration, declared per template.

Captain's ruling Q7 (2026-08-16): "wire them up, but only on some videos".
So this is a general mechanism with nothing by default.  A brand template
that declares no ``content.bookends`` gets no intro, no outro and no end
card - the same opt-in shape as ``effect.motion_accents`` (P3.1).
``effect.timed_text_overlay`` was meant to be a third; it has no reader
yet and no template may declare it - see
``library.tools.timed_text_overlay.NO_READER``.

What a declaration looks like::

    content:
      bookends:
        intro:
          composition: LucieLogoAnimation          # rendered by Remotion
          source: compositions/LucieLogoAnimation.tsx   # project-owned file
          duration_seconds: 3.0
          props: {accentColor: "#FFAA4D"}
        end_card:
          asset: assets/lucie_endcard.mov          # already rendered
          duration_seconds: 5.0

Two modes, because the captain keeps premade assets in two shapes
(decision record 2026-08-17, "premade assets are reusable per-project
templates"):

``asset``
    A clip that already exists.  Nothing renders it; the path is resolved
    against the project folder and placed on the timeline as-is.

``composition``
    A Remotion composition.  ``source`` names a project-owned ``.tsx`` file
    that ``library/tools/bookend_render.py`` stages and renders verbatim -
    that is how the captain's ``LucieEndCard`` and ``LucieLogoAnimation``
    are usable by the engine while living with the project they belong to.
    Omit ``source`` and the composition must be one the engine itself
    registers (``ENGINE_BOOKEND_COMPOSITIONS``); an unknown name raises,
    the same way an unknown ``house_look`` does.

How a declaration reaches the picture, in order:

1. ``mesh_spine`` (2.05) turns each declaration into a spine block -
   ``intro`` at the head, ``outro`` then ``end_card`` at the tail - and
   resolves the file each block will play.  Everything after the intro
   shifts by its duration, because the cursor is recomputed from block
   durations.
2. ``render_motion_graphics`` (4.06) renders the composition-mode blocks
   to the path the spine block already names.
3. ``compile_manifest`` (5.04) emits one V1 clip per bookend block, so the
   card is in ``tracks``, in ``project.duration_seconds``, and inside
   ``_assert_timeline_fully_covered`` like every other clip.
4. ``resolve_build_timeline`` (6.01) places it.

That chain is the point.  The retired ``import_endcard.py`` appended a
rendered card to V1 out of band, after the manifest was written, so the
manifest, ``manifest_validator`` and the render-QA duration checks all
described a timeline that no longer existed.
"""
from __future__ import annotations

import os
from typing import Any

from library.tools.spine_contract import BOOKEND_BLOCK_TYPES

# Which slot a template may declare, where it lands, and the spine
# block_type it becomes.  Tail slots are emitted in the order they appear
# here: an outro plays before the end card.
BOOKEND_SLOTS: dict[str, dict[str, str]] = {
    "intro": {"block_type": "intro_card", "placement": "head"},
    "outro": {"block_type": "outro_card", "placement": "tail"},
    "end_card": {"block_type": "end_card", "placement": "tail"},
}

# Compositions the engine itself registers in
# remotion-subtitles/src/Root.tsx and can therefore render with no project
# file.  A declaration naming anything else must carry `source`.
ENGINE_BOOKEND_COMPOSITIONS: dict[str, str] = {
    "TimedTextOverlay": (
        "Timed text moments.  As a bookend it plays on V1, so the "
        "transparent background reads as a black title card."
    ),
}

# Where a composition-mode bookend is rendered to.  compile_manifest reads
# this path off the spine block rather than recomputing it, so the render
# step and the compiler cannot disagree about the filename.
BOOKEND_RENDER_DIRNAME = "bookends"

# A bookend is a card, not an act.  Anything longer is a segment the spine
# should be planning as content.
MAX_BOOKEND_SECONDS = 30.0


class BookendDeclarationError(ValueError):
    """A template's `content.bookends` declaration is malformed."""


def declared_bookends(brand_content: dict[str, Any] | None) -> list[dict]:
    """Normalise a template's ``content.bookends`` into ordered declarations.

    Returns ``[]`` for a template that declares nothing, which is every
    template unless someone opted in.  Raises
    :class:`BookendDeclarationError` on anything malformed - an unknown
    slot, an unknown engine composition, a missing duration - rather than
    dropping it, because a silently ignored declaration is a card the
    editor believes shipped.
    """
    declarations = (brand_content or {}).get("bookends")
    if not declarations:
        return []
    if not isinstance(declarations, dict):
        raise BookendDeclarationError(
            f"content.bookends must be a mapping of slot -> declaration, "
            f"got {type(declarations).__name__}"
        )

    unknown = sorted(set(declarations) - set(BOOKEND_SLOTS))
    if unknown:
        raise BookendDeclarationError(
            f"unknown bookend slot(s) {unknown}; known slots are "
            f"{sorted(BOOKEND_SLOTS)}"
        )

    return [
        _normalise(slot, declarations[slot])
        for slot in BOOKEND_SLOTS
        if declarations.get(slot)
    ]


def _normalise(slot: str, raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise BookendDeclarationError(
            f"bookend '{slot}' must be a mapping, got {type(raw).__name__}")

    composition = raw.get("composition")
    asset = raw.get("asset")
    source = raw.get("source")

    if bool(composition) == bool(asset):
        raise BookendDeclarationError(
            f"bookend '{slot}' must name exactly one of 'composition' (a "
            f"Remotion composition to render) or 'asset' (a clip that "
            f"already exists); got "
            f"composition={composition!r}, asset={asset!r}"
        )
    if asset and source:
        raise BookendDeclarationError(
            f"bookend '{slot}' names both 'asset' and 'source'; an asset "
            f"is already rendered, so there is nothing to build it from"
        )
    if composition and not source and composition not in ENGINE_BOOKEND_COMPOSITIONS:
        raise BookendDeclarationError(
            f"bookend '{slot}' names composition {composition!r}, which "
            f"the engine does not register. Either point 'source' at the "
            f"project's own .tsx file, or name one of "
            f"{sorted(ENGINE_BOOKEND_COMPOSITIONS)}"
        )

    duration = raw.get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        raise BookendDeclarationError(
            f"bookend '{slot}' has no numeric duration_seconds; the spine "
            f"needs it to place the block, and probing the file would make "
            f"the plan depend on a render that has not happened yet"
        )
    duration = float(duration)
    if duration <= 0:
        raise BookendDeclarationError(
            f"bookend '{slot}' has duration_seconds={duration}, which puts "
            f"nothing on screen")
    if duration > MAX_BOOKEND_SECONDS:
        raise BookendDeclarationError(
            f"bookend '{slot}' runs {duration}s, longer than the "
            f"{MAX_BOOKEND_SECONDS}s a card may hold; a longer beat is "
            f"content and belongs in the spine")

    props = raw["props"] if raw.get("props") is not None else {}
    if not isinstance(props, dict):
        raise BookendDeclarationError(
            f"bookend '{slot}' props must be a mapping, got "
            f"{type(props).__name__}")

    return {
        "slot": slot,
        "block_type": BOOKEND_SLOTS[slot]["block_type"],
        "placement": BOOKEND_SLOTS[slot]["placement"],
        "mode": "asset" if asset else "composition",
        "composition": composition or "",
        "source": source or "",
        "asset": asset or "",
        "duration_seconds": round(duration, 3),
        "props": props,
        # A card is silent unless it says otherwise. `video_only` on the
        # manifest clip keeps the renderer from appending an audio item
        # for a file that has no audio stream.
        "has_audio": bool(raw.get("has_audio", False)),
    }


def _resolve_path(path: str, project_folder: str) -> str:
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(project_folder or "", path))


def bookend_render_path(project_folder: str, slot: str) -> str:
    """Where a composition-mode bookend's rendered clip lives."""
    return os.path.join(
        project_folder or "", "pipeline_output", BOOKEND_RENDER_DIRNAME,
        f"{slot}.mov")


def resolve_bookend(declaration: dict, project_folder: str) -> dict:
    """Add the absolute paths a declaration implies.

    ``asset_path`` is what the timeline will play, whichever mode the
    declaration used, so every consumer downstream reads one key.
    """
    resolved = dict(declaration)
    if declaration["mode"] == "asset":
        resolved["asset_path"] = _resolve_path(
            declaration["asset"], project_folder)
        resolved["source_path"] = ""
    else:
        resolved["asset_path"] = bookend_render_path(
            project_folder, declaration["slot"])
        resolved["source_path"] = (
            _resolve_path(declaration["source"], project_folder)
            if declaration["source"] else "")
    return resolved


def bookend_spine_block(resolved: dict) -> dict:
    """A spine block for one resolved bookend declaration.

    Carries the full spine contract key set (see
    ``library/tools/spine_contract.py``): a bookend is not speech, so the
    source-domain keys are None and there are no word timestamps.
    """
    return {
        "position": resolved["slot"],
        "block_type": resolved["block_type"],
        "clip_id": None,
        "source_clip_id": None,
        "source_start": None,
        "source_end": None,
        "word_timestamps": [],
        "alignment_method": None,
        "duration_seconds": resolved["duration_seconds"],
        "content": {"bookend": resolved},
    }


def block_bookend(block: dict) -> dict | None:
    """The bookend a spine block plays, or None if it is not a bookend."""
    if not isinstance(block, dict):
        return None
    if block.get("block_type") not in BOOKEND_BLOCK_TYPES:
        return None
    bookend = (block.get("content") or {}).get("bookend")
    return bookend if isinstance(bookend, dict) else None


def bookend_blocks(structure: list) -> list[dict]:
    """Every spine block that plays a bookend, in timeline order."""
    return [b for b in (structure or []) if block_bookend(b)]


def insert_bookend_blocks(blocks: list, resolved: list[dict]) -> list:
    """Place the declared bookends around the spine's own blocks.

    Head slots go before everything, tail slots after everything, both in
    ``BOOKEND_SLOTS`` order.  Timeline positions are NOT set here:
    ``mesh_spine`` recomputes every block's cursor afterwards, so an intro
    shifts the whole edit by its own duration rather than overlapping it.
    """
    head = [bookend_spine_block(r) for r in resolved
            if r["placement"] == "head"]
    tail = [bookend_spine_block(r) for r in resolved
            if r["placement"] == "tail"]
    return head + list(blocks) + tail
