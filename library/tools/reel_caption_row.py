"""A per-reel caption row, falling back to the project value.

The defect this closes
----------------------
The caption row is one project-level fraction
(`pipeline.subtitle_position.caption_row` in `project.yaml`,
`subtitle_style.project_caption_row`): the captain wanted a different
row on one reel and there was nowhere to say it. A project-level
declaration is the series look; a reel that needs its own band is a
standing per-reel decision, and per-reel decisions live in
`external/` beside `reel_ending.json` - not as a special case in code
(the captain, 2026-08-28: no hardcoded values).

What a declaration says
-----------------------
`<project>/external/reel_caption_row.json`, checked and never
asserted::

    {"version": 1,
     "rows": [{"reel": "Reel 01 - the-cta",
               "caption_row": 0.72,
               "reason": "captain 2026-09-13: clears the lower third"}]}

`reel` is matched against the reel's timeline name by prefix - the
`reel_ending.declared_ending` convention - so a staging suffix names
the same reel as the promoted timeline. `caption_row` is the same
FRACTION of the delivery frame measured from the top the project slot
takes, under the same validation: strictly between 0 and 1, or a
refusal naming the rule. Two rows for one reel refuse rather than
guess between them.

Where it is read
----------------
Every reader of the project row takes an optional reel and prefers
the override: `subtitle_style.project_caption_row` (the render - the
Remotion props' safe area, and through it the tight-box placement the
box sidecar stamps per row, so a cross-reel reuse restores nothing
meant for another row), and `speaker_identity.placement_box` (the
floor the lower thirds clear). No `reel_name` anywhere means today's
behaviour exactly: the project value, or the engine's own row.

`edit_depth.py` `overlay_position` is the owning row; this module is
the per-reel half of its declared caption position.
"""

from __future__ import annotations

import json
import os
from typing import Optional

#: The file basename, under the project's external-inputs area.
ROWS_FILENAME = "reel_caption_row.json"

#: Schema version this reader honours.
ROWS_VERSION = 1


class ReelCaptionRowError(ValueError):
    """A declared per-reel caption row cannot be honoured as written."""


def validate_rows(value) -> list:
    """Declared per-reel rows, checked, or a refusal saying why not."""
    if not isinstance(value, list):
        raise ReelCaptionRowError(
            "reel_caption_row.rows must be a list of per-reel rows, "
            f"not {type(value).__name__}.")
    seen = set()
    checked = []
    for index, entry in enumerate(value):
        label = f"reel_caption_row.rows[{index}]"
        if not isinstance(entry, dict):
            raise ReelCaptionRowError(f"{label} is not an object")
        reel = entry.get("reel")
        if not isinstance(reel, str) or not reel.strip():
            raise ReelCaptionRowError(
                f"{label} carries no `reel`: a per-reel row names the "
                f"reel it moves, matched by prefix against the "
                f"timeline name.")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ReelCaptionRowError(
                f"{label} carries no `reason`. A row nobody justified "
                f"is a guess about what the captain wanted - say whose "
                f"decision this records.")
        if "caption_row" not in entry:
            raise ReelCaptionRowError(
                f"{label} names no `caption_row`. A per-reel entry "
                f"that states no row moves nothing, which reads as a "
                f"declaration that was honoured.")
        try:
            row = float(entry["caption_row"])
        except (TypeError, ValueError) as bad:
            raise ReelCaptionRowError(
                f"{label}.caption_row is "
                f"{entry['caption_row']!r}: it must be a number, the "
                f"fraction of the delivery frame measured from the "
                f"top.") from bad
        if not 0.0 < row < 1.0:
            raise ReelCaptionRowError(
                f"{label}.caption_row is {row}: it is a FRACTION of "
                f"the delivery frame measured from the top, so it "
                f"lies strictly between 0 and 1 - the same rule the "
                f"project slot in `pipeline.subtitle_position` holds. "
                f"A pixel row would put the captions off the frame.")
        if reel in seen:
            raise ReelCaptionRowError(
                f"{label} repeats reel {reel!r}: two rows for one reel "
                f"refuse rather than guess between them.")
        seen.add(reel)
        checked.append({"reel": reel, "caption_row": row,
                        "reason": reason.strip()})
    return checked


def parse_rows(body: dict, source: str = ROWS_FILENAME) -> list:
    """Validated per-reel rows from a decoded rows file, or a refusal."""
    if not isinstance(body, dict):
        raise ReelCaptionRowError(
            f"{source} must be a JSON object, not "
            f"{type(body).__name__}.")
    version = body.get("version")
    if version != ROWS_VERSION:
        raise ReelCaptionRowError(
            f"{source} declares version {version!r}: this reader "
            f"honours version {ROWS_VERSION}.")
    try:
        return validate_rows(body.get("rows", []))
    except ReelCaptionRowError as exc:
        raise ReelCaptionRowError(f"{source}: {exc}") from None


def rows_path(project_folder) -> str:
    """The file a project's per-reel rows live in, present or not."""
    from library.tools.external_inputs import external_dir

    return os.path.join(str(external_dir(project_folder)), ROWS_FILENAME)


def load_rows(project_folder=None) -> list:
    """Declared per-reel rows, or `[]` where the project declares none.

    A present-but-malformed file raises `ReelCaptionRowError`: a row
    the build cannot read must refuse, never build silently past it.
    """
    if not project_folder:
        return []
    path = rows_path(project_folder)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        try:
            body = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ReelCaptionRowError(
                f"{path} is not JSON: {exc}") from exc
    return parse_rows(body, source=path)


def declared_row(project_folder, reel_name: str) -> Optional[float]:
    """This reel's declared row fraction, or None where none is declared.

    None is the ordinary answer - the caller falls back to the project
    value - never an error. Matched by prefix so a staging container
    resolves to the same declaration its promoted name does.
    """
    if not reel_name:
        return None
    for entry in load_rows(project_folder):
        if reel_name == entry["reel"] or reel_name.startswith(
                entry["reel"]):
            return entry["caption_row"]
    return None
