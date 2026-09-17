"""What a `review_panel` draws, and what it refuses to draw without.

One enumeration: the payload the roster's `review_panel` element needs
before the composition can put a review listing on a frame.

**Why the payload is not the engine's.**  A review panel is a MOCKUP of
a review listing - the visual language a viewer already recognises: an
overall score, a star rating, a count, and rows carrying who wrote a
review and what they wrote.  Every one of those is content somebody
supplied:

* the rows are words attributed to a named author about a named place,
  which is copy the viewer READS - artwork, and artwork belongs with the
  project that owns the series (AGENTS.md 14);
* the score and the count are claims about that place;
* the palette is the LOOK of the place the reviews were written on, and
  a look the engine states is a house look (AGENTS.md 12, and the whole
  reason `series_look.py` was emptied).

So this module states none of them.  It states what must be PRESENT, and
:func:`unusable_reason` is the refusal when it is not:
`motion_graphics_plan.resolve_plan` drops such an entry by name rather
than completing it from a constant (AGENTS.md 10.5).

**The refusal that matters most.** A panel with no rows is not an empty
panel, it is a panel with nothing to say; a panel with no palette is not
a neutral panel, it is the engine choosing one.  Both are drops.

What this module deliberately does NOT hold
-------------------------------------------
* **Any colour.** :data:`PALETTE_ROLES` names the roles a declaration
  fills and fixes no value for any of them, the way
  `motion_graphics_vocabulary.AXES` names dimensions and fixes no
  magnitude.
* **Any copy.** No author, no body, no place name and no platform label.
  A panel that named a real business and carried reviews nobody wrote
  would be a fabricated record; a project supplies either real reviews it
  holds or plainly generic placeholders, and that choice is the
  project's.
* **Any geometry.** How wide the card is and how a row is laid out is
  the composition's drawing
  (`remotion-subtitles/src/compositions/MotionGraphics/index.tsx`),
  mirrored for measurement in `library/tools/mg_tight_box.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: The roles a declaration must fill for the panel to be drawn, and what
#: each one draws.  A ROLE, never a colour: the panel is a mockup of a
#: place the reviews were written on, so which place that is - and
#: therefore what it looks like - is the declaring project's to say.
PALETTE_ROLES: Dict[str, str] = {
    "surface": "the card itself, behind everything else on it",
    "ink": "the primary text - the place, the score, an author's name",
    "muted": "the secondary text, and the disc an author's initial sits on",
    "star": "a filled star of a rating",
    "rule": "the divider between the header and each row",
}

#: What one review row carries.  `author` and `body` are required - a row
#: without either is not a review - and `stars` is the rating that row
#: gave.  `when` is how long ago it was written, as the listing's own
#: words, and is optional because not every listing shows one.
ROW_REQUIRED = ("author", "body")
ROW_OPTIONAL = ("stars", "when")

#: What the header carries, beside the copy runs: the overall score and
#: how many reviews it is over.
HEADER_REQUIRED = ("rating", "count")


class ReviewPanelError(ValueError):
    """A payload that cannot be drawn, raised where a caller asked for it."""


def _rows(data: Any) -> List[dict]:
    if not isinstance(data, dict):
        return []
    rows = data.get("rows")
    return [row for row in rows if isinstance(row, dict)] \
        if isinstance(rows, list) else []


def _text(raw: Any) -> str:
    return str(raw).strip() if isinstance(raw, (str, int, float)) else ""


def _number(raw: Any) -> Any:
    if isinstance(raw, bool):
        return None
    return raw if isinstance(raw, (int, float)) else None


def unusable_reason(data: Any) -> str:
    """Why this payload cannot be drawn as a review panel, or ``""``.

    The whole refusal, in one place, so the drop record carries the
    reason rather than the caller restating it.  Nothing here repairs a
    payload: a missing role is not filled in, a missing row is not
    invented, and a rating the declaration did not state is not derived
    from the rows - deriving it would be the engine deciding what the
    place scores.
    """
    if not isinstance(data, dict):
        return ("the entry carries no `data`, and a review panel's whole "
                "content is its data: the score, the count, the rows and "
                "the palette they are set in")

    missing_header = [key for key in HEADER_REQUIRED
                      if _number(data.get(key)) is None]
    if missing_header:
        return (f"`data` states no {', '.join(missing_header)}. A panel "
                f"that shows a listing without its own score and count is "
                f"not the thing it is a mockup of, and neither is the "
                f"engine's to supply.")

    rows = _rows(data)
    if not rows:
        return ("`data.rows` carries no review. A panel with no rows is "
                "not an empty panel, it is a panel with nothing to say.")
    for index, row in enumerate(rows):
        absent = [field for field in ROW_REQUIRED
                  if not _text(row.get(field))]
        if absent:
            return (f"row {index} states no {', '.join(absent)}. A review "
                    f"is somebody's words about somewhere; a row missing "
                    f"either is not one.")

    palette = data.get("palette")
    if not isinstance(palette, dict):
        return ("`data.palette` is absent. The panel is a mockup of a "
                "place the reviews were written on, and what that place "
                "looks like is the declaring project's to state - an "
                "engine-supplied palette here would be a house look.")
    unfilled = [role for role in PALETTE_ROLES
                if not _text(palette.get(role))]
    if unfilled:
        return (f"`data.palette` fills no {', '.join(unfilled)}. Every "
                f"role is drawn: {'; '.join(PALETTE_ROLES[r] for r in unfilled)}.")
    return ""


def assert_drawable(data: Any) -> None:
    """Raise :class:`ReviewPanelError` where the payload cannot be drawn."""
    reason = unusable_reason(data)
    if reason:
        raise ReviewPanelError(reason)


def rows(data: Any) -> List[dict]:
    """The review rows of a payload, in the order the declaration wrote them."""
    return _rows(data)
