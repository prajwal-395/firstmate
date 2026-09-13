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

A pin names a PLACE, never a transform
-------------------------------------
Version 1 recorded the captain's correction as the raw Pan/Tilt the
Inspector showed. That made every pin hostage to whatever the engine
believed the Resolve transform law was that day: when the law was
corrected on 2026-09-11, honouring this project's own pins verbatim
would have moved Reel 13's approved captions 108px. A record of where
the captain put something must not move when the engine learns
something about units.

So version 2 records the place: the canvas CENTRE in delivery-frame
pixels, and the transform is computed from it at placement time
against the canvas actually being placed
(`tight_box.placement_for_box`). The centre rather than the top-left
because it survives a re-render that changes the canvas width - a
centred caption is `540` whatever its box measures.

The file lives where other captain-supplied state lives,
`<project>/external/overlay_intent.json` (AGENTS.md 3 - checked, never
asserted), and looks like this::

    {"version": 2,
     "targets": {
       "caption": {"canvas_centre": [540.0, 1395.0], "scaling": 1},
       "mg_geo-podcast_622f69cb": {"canvas_centre": [540.0, 312.0],
                                   "scaling": 1}}}

Lookup is segment id exact, then provenance prefix, then kind.
`"caption"` is the kind default the 22 identical Reel 09 corrections
justify; motion graphics carry no kind default - four samples with no
shared pattern are four positions, not a rule - so each is pinned by
segment id.

A subtitle pin survives a re-render that changes the filename: the id
is `sub_<speaker>_<clip>_<span>_<digest>` and only the digest is
drawing inputs, so the lookup falls back to the provenance prefix
(`subtitle_segment_id.stable_prefix` - speaker, clip, span) when no
exact key matches. A pin written under the old full filename still
resolves, by exact match where the render stands and by prefix where
it was re-rendered. Motion-graphics names (`mg_<project>_<digest>`)
carry no such prefix - the whole suffix is content - so they match
exactly or not at all: stripping them to the project would bind one
pin to every graphic on it. Two pins naming one prefix is refused
rather than guessed between.

A declared target must be COMPLETE - a numeric `scaling` and a
two-number `canvas_centre`. A partial pin ("just the row") merged over
a computed value would silently change meaning when the computation
changes, so it is refused instead. A malformed file is refused LOUDLY,
never ignored: an ignored pin rebuilds the wrong positions while
reading as honoured, which is exactly the failure this exists to
prevent. A version 1 file is refused the same way, naming the
migration: its numbers cannot be reinterpreted without the canvas they
were measured on, so this reader will not guess at them.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

#: The file basename, under the project's external-inputs area.
INTENT_FILENAME = "overlay_intent.json"

#: Schema version this reader honours. 1 recorded a raw Pan/Tilt and is
#: refused - see the module docstring.
INTENT_VERSION = 2

#: The superseded version, named so the refusal can say what to do.
RETIRED_INTENT_VERSION = 1

#: The kind default Reel 09 justifies: all 22 captions pinned alike.
CAPTION_KIND = "caption"

#: A pin is a place and a carriage: `canvas_centre` (two numbers, in
#: delivery-frame pixels) and `scaling`. No extras required, no other
#: shape accepted.
CENTRE_KEY = "canvas_centre"
PLACEMENT_KEYS = (CENTRE_KEY, "scaling")

#: What the resolved transform is, once a canvas is known. Not a file
#: shape - the shape `placement_for_box` returns and the placer takes.
TRANSFORM_KEYS = ("scaling", "pan", "tilt")


class OverlayIntentError(ValueError):
    """The declared intent cannot be honoured as written."""


def _check_placement(key: str, placement) -> Dict[str, object]:
    """A declared target as numbers, or a refusal saying why not."""
    if not isinstance(placement, dict):
        raise OverlayIntentError(
            f"intent target {key!r} must be a "
            f"{{{CENTRE_KEY}, scaling}} mapping, not "
            f"{type(placement).__name__}.")
    missing = [k for k in PLACEMENT_KEYS if k not in placement]
    if missing:
        raise OverlayIntentError(
            f"intent target {key!r} is partial (missing "
            f"{missing}): a pin must carry both {CENTRE_KEY} and "
            f"scaling, so a changed computation cannot silently move "
            f"what the pin does not name.")
    centre = placement[CENTRE_KEY]
    if not isinstance(centre, (list, tuple)) or len(centre) != 2:
        raise OverlayIntentError(
            f"intent target {key!r} carries {CENTRE_KEY}="
            f"{centre!r}: it must be [x, y] in delivery-frame pixels.")
    try:
        values = {CENTRE_KEY: [float(centre[0]), float(centre[1])],
                  "scaling": float(placement["scaling"])}
    except (TypeError, ValueError):
        raise OverlayIntentError(
            f"intent target {key!r} carries a non-numeric value in "
            f"{ {k: placement[k] for k in PLACEMENT_KEYS} !r}.") from None
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
    if version == RETIRED_INTENT_VERSION:
        raise OverlayIntentError(
            f"{source} declares version {RETIRED_INTENT_VERSION}, "
            f"which recorded a raw Pan/Tilt. Those numbers cannot be "
            f"reinterpreted without the canvas they were measured on, "
            f"and honouring them verbatim moves approved work whenever "
            f"the transform law is corrected - which is what happened "
            f"on 2026-09-11. Re-express each target as "
            f"{{{CENTRE_KEY}: [x, y], scaling: 1}}, the canvas centre "
            f"in delivery-frame pixels, and declare version "
            f"{INTENT_VERSION}.")
    if version != INTENT_VERSION:
        raise OverlayIntentError(
            f"{source} declares version {version!r}: this reader "
            f"honours version {INTENT_VERSION}.")
    targets = body.get("targets", {})
    if not isinstance(targets, dict):
        raise OverlayIntentError(
            f"{source} carries targets={targets!r}, which is not an "
            f"object mapping segment-or-kind to placement.")
    retired = body.get("retired_targets") or {}
    if not isinstance(retired, dict):
        raise OverlayIntentError(
            f"{source} carries retired_targets={retired!r}, which is not "
            f"an object.")
    if retired:
        # A pin that does nothing silently is the failure this module
        # exists to stop, so a retired one SAYS it is retired every
        # time the file is read. They are kept rather than deleted:
        # they are the captain's record of where he put something, and
        # the artefact that would carry it may come back.
        import sys
        print(f"  {source}: {len(retired)} retired target(s) recorded and "
              f"NEVER applied - {', '.join(sorted(retired))}. Each names "
              f"a place that could not be re-expressed; see its 'why'.",
              file=sys.stderr, flush=True)
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


#: How far a declared pin may sit from the computation before the
#: disagreement is REPORTED, in stored Pan/Tilt units.
#:
#: Not a veto and not a tolerance on correctness - a pin exists to
#: disagree, and it still wins. This is the threshold past which it may
#: not win in SILENCE. One unit is a QUARTER of a delivery pixel on a
#: 480-tall tight canvas, so 8 units is ~2px: below that the two
#: answers are the same place and saying so is noise.
INTENT_DISAGREEMENT_UNITS = 8.0


def disagreement(placement: Optional[dict],
                 computed: Optional[dict],
                 key: str = "") -> str:
    """Why a declared placement differs from the computed one, or "".

    The defect this closes, 2026-09-11: nineteen caption pins recorded
    during one rebuild encoded a position that a later fix superseded.
    They outranked the computation by design and said nothing, so the
    only way to discover that a pin had gone stale was to look at the
    picture and disbelieve it. Measured on this project they turned out
    to be inert as well - no current caption id matched them - which is
    the other half of the same silence: a pin that matches NOTHING is
    as quiet as one that matches wrongly.

    A pin may still win. It may not win without saying what it
    overruled.
    """
    if not placement or not computed:
        return ""
    parts = []
    for name in ("pan", "tilt", "scaling"):  # the resolved TRANSFORM
        try:
            declared, derived = float(placement[name]), float(computed[name])
        except (KeyError, TypeError, ValueError):
            continue
        gap = declared - derived
        if abs(gap) >= INTENT_DISAGREEMENT_UNITS:
            parts.append(f"{name} {declared:g} declared vs {derived:g} "
                         f"computed ({gap:+g})")
    if not parts:
        return ""
    return (f"declared intent{f' {key!r}' if key else ''} OVERRULES the "
            f"computation: {'; '.join(parts)}. The pin wins - that is "
            f"what a pin is for - but a pin recorded from a state a "
            f"later fix superseded looks exactly like this, so check it "
            f"is still the position you want.")


def transform_for(target: dict, canvas: Optional[tuple],
                  frame: Optional[tuple], key: str = "") -> dict:
    """The `{scaling, pan, tilt}` that puts this canvas on the pin.

    The pin names a place; the transform that reaches it depends on the
    canvas being placed, so it is computed HERE, at placement time,
    against the canvas actually going down. That is the whole point of
    the version 2 shape: the next correction to the transform law moves
    nothing, because the pin never encoded the law.

    A pin this cannot compute - no canvas, no frame - RAISES rather
    than falling back to the computed placement. A pin that silently
    does not apply is the failure `unmatched` exists to report, and a
    pin the placer cannot honour is worse: it reads as honoured.
    """
    from library.tools.tight_box import placement_for_box

    if not canvas or not frame:
        raise OverlayIntentError(
            f"intent target {key!r} names a place "
            f"({target[CENTRE_KEY]}) but the placer passed no "
            f"{'canvas' if not canvas else 'frame'} size, so the "
            f"transform that reaches it cannot be computed. A pin that "
            f"cannot be honoured must not read as honoured.")
    centre_x, centre_y = target[CENTRE_KEY]
    placement = placement_for_box(float(canvas[0]), float(canvas[1]),
                                  float(centre_x), float(centre_y),
                                  int(frame[0]), int(frame[1]))
    placement["scaling"] = target["scaling"]
    return placement


def _prefix_hits(targets: dict, segment_id: str) -> list:
    """Pin keys naming the same overlay as `segment_id`, by provenance.

    A subtitle pin written under yesterday's filename shares its
    provenance prefix with today's re-render
    (`subtitle_segment_id.stable_prefix`); a pin written as the bare
    prefix matches the same way. Anything else matches only itself,
    exactly - which keeps a content-keyed `mg_` pin from binding a
    graphic it never named.
    """
    from library.tools.subtitle_segment_id import stable_prefix

    want = stable_prefix(segment_id)
    return sorted(key for key in targets
                  if key != CAPTION_KIND and stable_prefix(key) == want)


def resolve(kind: Optional[str], segment_id: Optional[str],
            computed: Optional[dict],
            intent: Optional[dict],
            canvas: Optional[tuple] = None,
            frame: Optional[tuple] = None) -> Tuple[Optional[dict], str]:
    """The placement to apply, and which one it is.

    Segment id exact, then provenance prefix, then kind, then the
    computed value: a pin names what the captain corrected, and
    everything unpinned keeps the pipeline's own answer. The prefix
    half is what survives a re-render - a pin recorded against one
    content hash still binds the overlay re-rendered under the next.
    Returns `(placement, provenance)` where provenance is `"declared"`
    or `"computed"` - the placer records which won, so a timeline can
    later say why each graphic sits where it does.

    `canvas` and `frame` are the sizes the pin's place is turned into a
    transform against (`transform_for`); they are only needed where a
    pin matches, and a pin that matches without them RAISES.

    Two pins naming one prefix REFUSE rather than guess between
    them: a pin that silently does not apply is the failure `unmatched`
    exists to report, and a pin that silently applies to the wrong of
    two claimants is worse. A declared placement that materially
    disagrees with the computation is REPORTED to stderr by
    `disagreement` on the way past. It still wins; it no longer wins
    quietly.
    """
    targets = intent or {}
    key = None
    if segment_id is not None and segment_id in targets:
        key = segment_id
    elif segment_id is not None:
        hits = _prefix_hits(targets, segment_id)
        if len(hits) > 1:
            raise OverlayIntentError(
                f"intent targets {hits} all name the overlay "
                f"{segment_id!r} by provenance prefix: two pins "
                f"claim one place, so neither applies. Keep the one "
                f"that says where it goes and move the other to "
                f"retired_targets.")
        if hits:
            key = hits[0]
    if key is None and kind is not None and kind in targets:
        key = kind
    if key is not None:
        placement = transform_for(targets[key], canvas, frame, key)
        note = disagreement(placement, computed, key)
        if note:
            import sys
            print(f"  {note}", file=sys.stderr, flush=True)
        return placement, "declared"
    if computed is None:
        return None, "computed"
    return dict(computed), "computed"


def unmatched(intent: Optional[dict], seen_ids) -> list:
    """Declared target keys that matched NO overlay on this build.

    A pin nobody matched is not honoured and not refused - it simply
    does nothing, and does it silently. Nineteen of them sat in this
    project's `overlay_intent.json` encoding a superseded caption
    position while every caption on every reel was placed by the
    computation; nothing said the file had stopped applying.

    Matching is prefix-aware, the same lookup `resolve` applies: a
    pin recorded against yesterday's filename counts as matched where
    today's re-render of the same overlay played. What is reported
    here is genuinely unbound - no current overlay shares its
    provenance - never a pin the re-key already saved.

    `CAPTION_KIND` is excluded: a kind default that matched nothing on
    one reel is not stale, it just had no caption to place there.
    """
    from library.tools.subtitle_segment_id import stable_prefix

    targets = intent or {}
    seen = set(seen_ids or ())
    seen_prefixes = {stable_prefix(sid) for sid in seen
                     if sid is not None}
    return sorted(key for key in targets
                  if key != CAPTION_KIND
                  and key not in seen
                  and stable_prefix(key) not in seen_prefixes)


def report_unmatched(intent: Optional[dict], seen_ids,
                     source: str = "overlay_intent.json") -> list:
    """Pins that matched NO overlay, said aloud. Returns them.

    The build calls this once per reel after placing, with every
    overlay id it laid down. A pin for a segment no reel carries is
    an ordinary thing - but an ordinary thing said plainly, with the
    pin named, not a line in a file nobody re-reads. Either the
    overlay still plays somewhere and the pin names a stale id (re-key
    it to the current render), or the overlay is gone (move the pin
    to `retired_targets`, saying why). Silence here is the defect
    this module exists to stop, so a non-empty answer always prints.
    """
    missed = unmatched(intent, seen_ids)
    if missed:
        import sys
        print(f"  {source}: {len(missed)} declared pin(s) matched no "
              f"overlay on this build - {', '.join(missed)}. A pin "
              f"that matches nothing places nothing: re-key it to "
              f"the overlay's current id, or retire it.",
              file=sys.stderr, flush=True)
    return missed
