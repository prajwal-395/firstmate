"""Where an overlay goes when the captain has already said so.

A tight overlay's placement is normally COMPUTED - `tight_box` derives
the canvas from a probe render and `placement_for_box` inverts the
measured Resolve relation, so the canvas lands where the design put it.
That computation is a prediction about what looks right, and on Reel 09
the captain corrected it by hand on all 27 graphics: every caption from
the computed Tilt -1744.0 to -1700.0, four motion-graphics clips to
per-graphic positions, plus a logo card of his own.

Those corrections are ground truth we have never had before, and a
rebuild that recomputes throws them away. This module records them as
DECLARED intent - data, never taste the engine invents (AGENTS.md 10.5):
the engine declares no positions here, it only honours positions the
project declares.

The file lives where other captain-supplied state lives,
`<project>/external/overlay_intent.json` (AGENTS.md 3 - checked, never
asserted), and looks like this::

    {"version": 1,
     "targets": {
       "caption": {"pan": 0.0, "tilt": -1700.0, "scaling": 1},
       "mg_geo-podcast_19fe552d": {"pan": -1153.0, "tilt": 241.0,
                                   "scaling": 1}}}

Lookup is segment id first, then kind. `"caption"` is the kind default
the 22 identical Reel 09 corrections justify; motion graphics carry no
kind default - four samples with no shared pattern are four positions,
not a rule - so each is pinned by segment id (the render filename
stem, stable across rebuilds that reuse the render).

A declared target must be COMPLETE - numeric `scaling`, `pan` and
`tilt`. A partial pin ("just the tilt") merged over a computed value
would silently change meaning when the computation changes, so it is
refused instead. A malformed file is refused LOUDLY, never ignored: an
ignored pin rebuilds the wrong positions while reading as honoured,
which is exactly the failure this exists to prevent.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

#: The file basename, under the project's external-inputs area.
INTENT_FILENAME = "overlay_intent.json"

#: Schema version this reader honours.
INTENT_VERSION = 1

#: The kind default Reel 09 justifies: all 22 captions pinned alike.
CAPTION_KIND = "caption"

#: A placement is these three numbers, all numeric, no extras required
#: but no other shape accepted.
PLACEMENT_KEYS = ("scaling", "pan", "tilt")


class OverlayIntentError(ValueError):
    """The declared intent cannot be honoured as written."""


def _check_placement(key: str, placement) -> Dict[str, float]:
    """A declared target as floats, or a refusal saying why not."""
    if not isinstance(placement, dict):
        raise OverlayIntentError(
            f"intent target {key!r} must be a "
            f"{{scaling, pan, tilt}} mapping, not "
            f"{type(placement).__name__}.")
    missing = [k for k in PLACEMENT_KEYS if k not in placement]
    if missing:
        raise OverlayIntentError(
            f"intent target {key!r} is partial (missing "
            f"{missing}): a pin must carry all of scaling, pan and "
            f"tilt, so a changed computation cannot silently move "
            f"what the pin does not name.")
    values = {}
    for name in PLACEMENT_KEYS:
        try:
            values[name] = float(placement[name])
        except (TypeError, ValueError):
            raise OverlayIntentError(
                f"intent target {key!r} carries {name}="
                f"{placement[name]!r}, which is not a number.") from None
    return values


def parse_intent(body: dict, source: str = "overlay_intent.json") -> dict:
    """Validated targets from a decoded intent file, or a refusal.

    Returns `{}` for a file that declares no targets - "the captain
    pinned nothing" is the normal case, not an error. Anything
    malformed raises `OverlayIntentError`: see the module docstring.
    """
    if not isinstance(body, dict):
        raise OverlayIntentError(
            f"{source} must be a JSON object, not "
            f"{type(body).__name__}.")
    version = body.get("version")
    if version != INTENT_VERSION:
        raise OverlayIntentError(
            f"{source} declares version {version!r}: this reader "
            f"honours version {INTENT_VERSION}.")
    targets = body.get("targets", {})
    if not isinstance(targets, dict):
        raise OverlayIntentError(
            f"{source} carries targets={targets!r}, which is not an "
            f"object mapping segment-or-kind to placement.")
    return {key: _check_placement(key, value)
            for key, value in targets.items()}


def load_intent(project_folder=None,
                intent_file: Optional[str] = None) -> dict:
    """Validated targets for a project, or `{}` when none are declared.

    `intent_file` names the file outright (a missing one is refused -
    the caller asked for it). Otherwise the project's external-inputs
    area is read (`external/overlay_intent.json`); an absent file is
    the normal "nothing pinned" answer. A present-but-malformed file
    raises `OverlayIntentError` rather than rebuilding past it.
    """
    if intent_file:
        if not os.path.isfile(intent_file):
            raise OverlayIntentError(
                f"intent file {intent_file!r} does not exist: refusing "
                f"rather than building without the declared positions.")
        with open(intent_file, encoding="utf-8") as handle:
            try:
                body = json.load(handle)
            except json.JSONDecodeError as exc:
                raise OverlayIntentError(
                    f"intent file {intent_file!r} is not JSON: "
                    f"{exc}") from exc
        return parse_intent(body, source=intent_file)
    if not project_folder:
        return {}
    from library.tools.external_inputs import external_dir

    path = os.path.join(str(external_dir(project_folder)), INTENT_FILENAME)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        try:
            body = json.load(handle)
        except json.JSONDecodeError as exc:
            raise OverlayIntentError(
                f"{path} is not JSON: {exc}") from exc
    return parse_intent(body, source=path)


def resolve(kind: Optional[str], segment_id: Optional[str],
            computed: Optional[dict],
            intent: Optional[dict]) -> Tuple[Optional[dict], str]:
    """The placement to apply, and which one it is.

    Segment id first, then kind, then the computed value: a pin names
    what the captain corrected, and everything unpinned keeps the
    pipeline's own answer. Returns `(placement, provenance)` where
    provenance is `"declared"` or `"computed"` - the placer records
    which won, so a timeline can later say why each graphic sits
    where it does.
    """
    targets = intent or {}
    key = None
    if segment_id is not None and segment_id in targets:
        key = segment_id
    elif kind is not None and kind in targets:
        key = kind
    if key is not None:
        placement = dict(targets[key])
        return placement, "declared"
    if computed is None:
        return None, "computed"
    return dict(computed), "computed"
