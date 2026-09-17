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
                                   "scaling": 1},
       "mg_geo-podcast_a072b160": {"canvas_centre": [540.0, 312.0],
                                   "scaling": 1, "zoom": 0.88}}}

A target may also carry `zoom`: the uniform user zoom (Resolve's
`ZoomX`/`ZoomY`) the captain set by hand - Reel 01's graphic scaled
down 12% on top of its move. `scaling` genuinely cannot carry that:
it is Resolve's Scaling MODE (`1` is Crop - native pixels, centred -
`resolve_transform.NATIVE_BASE_SCALE`), not a magnification, so a
value of 0.88 there would name no mode at all. `zoom` is optional and
positive; where it is absent the clip plays at the zoom the build
gave it, exactly as before. The Pan/Tilt law does not move under a
zoom (`resolve_transform`: a Pan of 100 draws the same pixels at zoom
1.0 and 2.307), so a place and a zoom compose rather than interact.

Lookup is segment id exact, then placement label, then provenance
prefix, then kind.
`"caption"` is the kind default the 22 identical Reel 09 corrections
justify; motion graphics carry no kind default - four samples with no
shared pattern are four positions, not a rule - so each is pinned by
segment id or by placement label (below).

A subtitle pin survives a re-render that changes the filename: the id
is `sub_<speaker>_<clip>_<span>_<digest>` and only the digest is
drawing inputs, so the lookup falls back to the provenance prefix
(`subtitle_segment_id.stable_prefix` - speaker, clip, span) when no
exact key matches. A pin written under the old full filename still
resolves, by exact match where the render stands and by prefix where
it was re-rendered.

A motion-graphics pin survives a re-render through the PLACEMENT
LABEL, and for the same reason the subtitle prefix works: the label
(`vox_<reel>_<index>`, `explainer_<reel>_<index>`,
`lt_<reel>_<index>` on reels, `mg_<index>` on the master pass) names
WHICH PLACING the file serves, never which pixels it holds. It is
recorded on the segment entry beside the content-keyed `segment_id`,
so a rebuild that re-renders the same placing under a new digest
(`timeline_start` shifting 23ms on Reel 26's title lockup,
`mg_geo-podcast_589d4594` -> `mg_geo-podcast_c43f73d8`, zero pixels
moved) keeps the label while the id dies - and the pin written
against the label still binds. Motion-graphics names
(`mg_<project>_<digest>`) carry no such prefix - the whole suffix is
content - so they match exactly or not at all: stripping them to the
project would bind one pin to every graphic on it. The label is the
answer that warning demands: one label names one placing on one reel,
never every graphic on the project. Two pins naming one prefix is
refused rather than guessed between, and so is an exact pin and a
label pin both naming one segment.

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
#: delivery-frame pixels) and `scaling`. No extras required - `zoom`
#: alone may ride beside them - and no other shape accepted.
CENTRE_KEY = "canvas_centre"
PLACEMENT_KEYS = (CENTRE_KEY, "scaling")

#: The optional uniform user zoom a pin may carry (Resolve's
#: `ZoomX`/`ZoomY`, one number for both). Positive and finite: a zoom
#: of zero or less draws nothing, and a non-number is not a zoom.
ZOOM_KEY = "zoom"

#: What the resolved transform is, once a canvas is known. Not a file
#: shape - the shape `placement_for_box` returns and the placer takes,
#: plus the `zoom` a pin may have carried beside the place.
TRANSFORM_KEYS = ("scaling", "pan", "tilt", "zoom")


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
    if ZOOM_KEY in placement:
        zoom = placement[ZOOM_KEY]
        if (isinstance(zoom, bool) or not isinstance(zoom, (int, float))
                or not zoom > 0
                or zoom in (float("inf"), float("-inf"))):
            raise OverlayIntentError(
                f"intent target {key!r} carries {ZOOM_KEY}={zoom!r}: "
                f"a zoom must be a positive finite number - the "
                f"uniform magnification the clip plays at, 0.88 for "
                f"the 12% scale-down. Zero or less draws nothing.")
        values[ZOOM_KEY] = float(zoom)
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
    try:
        declared_zoom = placement.get(ZOOM_KEY)
        computed_zoom = (computed or {}).get(ZOOM_KEY, 1.0)
        if declared_zoom is not None and abs(float(declared_zoom)
                                             - float(computed_zoom)) >= 1e-9:
            parts.append(f"zoom {float(declared_zoom):g} declared vs "
                         f"{float(computed_zoom):g} computed")
    except (TypeError, ValueError):
        pass
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
    if ZOOM_KEY in target:
        # The pin's place computes against the native canvas; the zoom
        # rides beside it, applied by the placer after Pan/Tilt - the
        # two compose (`resolve_transform`: Pan draws the same pixels
        # at any zoom), so no recomputation here.
        placement[ZOOM_KEY] = target[ZOOM_KEY]
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


def matching_target(intent: Optional[dict], kind: Optional[str],
                      segment_id: Optional[str],
                      placement_label: Optional[str] = None):
    """The declared pin this overlay matches, or `(None, None)`.

    The same lookup `resolve` applies - `_match_key`, all four tiers
    in order (exact id, placement label, provenance prefix, kind) -
    without computing a transform and without saying anything: no
    `disagreement` print. The collision refusals fire here exactly as
    on the placing path (an exact pin and a label pin both naming one
    segment; two pins sharing a provenance prefix), because a quiet
    reader that answers where the placer refuses would be the two
    disagreeing about what a pin binds. A placement-time intent check
    (`library/tools/overlay_draw_intent.py`) reads this to learn what
    the overlay is FOR; the placer itself still resolves beside it, so
    the two cannot disagree about which pin won.
    """
    targets = intent or {}
    key = _match_key(targets, kind, segment_id, placement_label)
    if key is None:
        return None, None
    return key, targets[key]


def _match_key(targets: dict, kind: Optional[str],
               segment_id: Optional[str],
               placement_label: Optional[str]) -> Optional[str]:
    """Which pin key names this overlay, or None.

    The lookup order `resolve` applies, without computing a transform:
    segment id exact, then placement label, then provenance prefix,
    then kind. A dry check (`python3 -m library.tools.overlay_intent
    <project>`) reads this; the placer reads `resolve`, which is this
    plus the transform. One function so the two cannot disagree about
    what a pin binds.

    Two pins claiming one overlay REFUSE rather than guess between
    them: two keys sharing a provenance prefix (the long-standing
    rule), and - new with the label tier - an exact pin and a label
    pin both naming this segment. A label is specific and an exact id
    is specific; where both are live neither outranks the other, so
    the stale one is retired by hand instead of overruled in silence.
    A specific pin (exact or label) over a general one (prefix, kind)
    is not a conflict: the tiers already order those, deterministically.
    """
    key = None
    if segment_id is not None and segment_id in targets:
        key = segment_id
    label_key = None
    if placement_label is not None and placement_label in targets:
        label_key = placement_label
    if key is not None and label_key is not None and label_key != key:
        raise OverlayIntentError(
            f"intent targets {sorted((key, label_key))} both name the "
            f"overlay {segment_id!r} (placement label "
            f"{placement_label!r}): an exact pin and a label pin claim "
            f"one place, so neither applies. Keep the one that says "
            f"where it goes and move the other to retired_targets.")
    if key is None:
        key = label_key
    if key is None and segment_id is not None:
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
    return key


def resolve(kind: Optional[str], segment_id: Optional[str],
            computed: Optional[dict],
            intent: Optional[dict],
            canvas: Optional[tuple] = None,
            frame: Optional[tuple] = None,
            placement_label: Optional[str] = None,
            matched: Optional[list] = None) -> Tuple[Optional[dict], str]:
    """The placement to apply, and which one it is.

    Segment id exact, then placement label, then provenance prefix,
    then kind, then the computed value: a pin names what the captain
    corrected, and everything unpinned keeps the pipeline's own
    answer. The prefix half is what survives a caption re-render - a
    pin recorded against one content hash still binds the overlay
    re-rendered under the next - and the label half is what survives
    a motion-graphics re-render: a pin recorded against the placing
    (`vox_<reel>_<index>` and kin) still binds the graphic re-rendered
    under the next digest. Returns `(placement, provenance)` where
    provenance is `"declared"` or `"computed"` - the placer records
    which won, so a timeline can later say why each graphic sits
    where it does.

    `canvas` and `frame` are the sizes the pin's place is turned into a
    transform against (`transform_for`); they are only needed where a
    pin matches, and a pin that matches without them RAISES.

    `placement_label` is the segment entry's placing name where the
    caller knows it (the reel placers do; captions carry none). `None`
    skips the label tier exactly as before - nothing that never knew
    a label changes behaviour.

    `matched`, where given, collects the winning pin key on every
    declared application, so the build can report which pins applied
    (`intent_report`) instead of only which did not (`unmatched`).
    Two pins naming one prefix REFUSE rather than guess between
    them: a pin that silently does not apply is the failure `unmatched`
    exists to report, and a pin that silently applies to the wrong of
    two claimants is worse. An exact pin and a label pin both naming
    one segment refuse the same way. A declared placement that materially
    disagrees with the computation is REPORTED to stderr by
    `disagreement` on the way past. It still wins; it no longer wins
    quietly.
    """
    targets = intent or {}
    key = _match_key(targets, kind, segment_id, placement_label)
    if key is not None:
        placement = transform_for(targets[key], canvas, frame, key)
        note = disagreement(placement, computed, key)
        if note:
            import sys
            print(f"  {note}", file=sys.stderr, flush=True)
        if matched is not None:
            matched.append(key)
        return placement, "declared"
    if computed is None:
        return None, "computed"
    return dict(computed), "computed"


def unmatched(intent: Optional[dict], seen_ids,
              seen_labels=()) -> list:
    """Declared target keys that matched NO overlay on this build.

    A pin nobody matched is not honoured and not refused - it simply
    does nothing, and does it silently. Nineteen of them sat in this
    project's `overlay_intent.json` encoding a superseded caption
    position while every caption on every reel was placed by the
    computation; nothing said the file had stopped applying.

    Matching is prefix-aware AND label-aware, the same lookup `resolve`
    applies: a pin recorded against yesterday's filename counts as
    matched where today's re-render of the same overlay played, and a
    pin recorded against a placing counts as matched where that
    placing played under a new digest. What is reported here is
    genuinely unbound - no current overlay shares its provenance or
    its placing - never a pin the re-key already saved.

    `CAPTION_KIND` is excluded: a kind default that matched nothing on
    one reel is not stale, it just had no caption to place there.
    """
    from library.tools.subtitle_segment_id import stable_prefix

    targets = intent or {}
    seen = set(seen_ids or ())
    seen_prefixes = {stable_prefix(sid) for sid in seen
                     if sid is not None}
    labels = {label for label in (seen_labels or ()) if label}
    return sorted(key for key in targets
                  if key != CAPTION_KIND
                  and key not in seen
                  and key not in labels
                  and stable_prefix(key) not in seen_prefixes)


def report_unmatched(intent: Optional[dict], seen_ids,
                     source: str = "overlay_intent.json",
                     seen_labels=()) -> list:
    """Pins that matched NO overlay, said aloud. Returns them.

    The build calls this once per reel after placing, with every
    overlay id it laid down and every placing label it placed under. A
    pin for a segment no reel carries is an ordinary thing - but an
    ordinary thing said plainly, with the pin named, not a line in a
    file nobody re-reads. Either the overlay still plays somewhere and
    the pin names a stale id (re-key it to the current render, or to
    the placing's label so the next re-render needs no re-key), or the
    overlay is gone (move the pin to `retired_targets`, saying why).
    Silence here is the defect this module exists to stop, so a
    non-empty answer always prints.
    """
    missed = unmatched(intent, seen_ids, seen_labels)
    if missed:
        import sys
        print(f"  {source}: {len(missed)} declared pin(s) matched no "
              f"overlay on this build - {', '.join(missed)}. A pin "
              f"that matches nothing places nothing: re-key it to "
              f"the overlay's current id or placing label, or retire it.",
              file=sys.stderr, flush=True)
    return missed


def intent_report(intent: Optional[dict], applied_keys,
                  seen_ids, seen_labels=(),
                  source: str = "overlay_intent.json") -> dict:
    """Which declared pins this build honoured, as durable data.

    The sentence this defect hid for months - "24 of 26 applied" -
    belongs on the build's own record, not only on stderr: a later
    reader (firstmate consolidating the reels, the captain asking why
    a graphic moved) must be able to find whether a pin applied
    without re-running the build. Reporting only, never a gate: a
    build must not start failing because a pin went stale.

    `applied_keys` is every winning pin key `resolve` recorded (via
    `matched`) across the reels this report covers; `seen_ids` and
    `seen_labels` are what was placed. Returns `{"source", "declared",
    "applied", "unmatched", "kind_defaults"}` - all sorted lists but
    the counts' source - and SAYS the one-line sentence on stderr.
    `declared`/`applied` count PIN keys, never applications: one
    caption pin fanning out over three karaoke cards of its block is
    one pin honoured, not three.
    """
    import sys

    targets = intent or {}
    pins = sorted(key for key in targets if key != CAPTION_KIND)
    applied = sorted({key for key in (applied_keys or ())
                      if key in targets and key != CAPTION_KIND})
    missed = unmatched(intent, seen_ids, seen_labels)
    kinds = sorted(key for key in targets if key == CAPTION_KIND)
    report = {"source": source,
              "declared": pins,
              "applied": applied,
              "unmatched": missed,
              "kind_defaults": kinds}
    print(f"  {source}: overlay intent {len(applied)} of {len(pins)} "
          f"pin(s) applied"
          + (f" - unmatched: {', '.join(missed)}" if missed else ""),
          file=sys.stderr, flush=True)
    return report


def rekey_targets(targets: dict, label_map: dict) -> tuple:
    """Re-key digest pins onto placement labels, dropping nothing.

    `label_map` is `{old segment id: placement label}` - what
    `collect_placement_labels` reads off the build's own records. Every
    pin key found in it is rewritten to its label, carrying its value;
    every key NOT found stays verbatim. Returns `(new_targets,
    report)` where report names `mapped` (`{old: new}`), `unmapped`
    (kept as-is: the overlay left no record to re-key against, so a
    hand re-key or a retirement is owed) and `collisions` (`{label:
    [keys]}` - two digests claiming one placing, left verbatim rather
    than guessed between, the same refusal the resolver applies at
    build time).

    Lossless by construction: the output holds every input key or its
    mapped label, and a collision never resolves itself by deleting a
    claimant. A migration that silently drops a pin is the failure
    this exists to remove.
    """
    mapped: dict = {}
    unmapped: list = []
    collisions: dict = {}
    by_label: dict = {}
    for key in (targets or {}):
        label = (label_map or {}).get(key)
        if label is None:
            continue
        by_label.setdefault(label, []).append(key)
    for label, keys in by_label.items():
        if len(keys) > 1 or label in (targets or {}):
            rivals = sorted(set(keys) | ({label} if label in (targets or {})
                                          else set()))
            collisions[label] = rivals
    for key in (targets or {}):
        label = (label_map or {}).get(key)
        if label is None:
            unmapped.append(key)
        elif label in collisions:
            unmapped.append(key)
        else:
            mapped[key] = label
    new_targets = {}
    for key, value in (targets or {}).items():
        if key in mapped:
            new_targets[mapped[key]] = value
        else:
            new_targets[key] = value
    return new_targets, {"mapped": mapped,
                         "unmapped": sorted(unmapped),
                         "collisions": collisions}


def _segment_id_of_overlay_path(overlay_path: str) -> str:
    """The intent id for a recorded overlay file, or "".

    The record stores the path; the id is the file stem, exactly as
    `_overlay_segment_id` in the reel placer reads it off a segment.
    """
    import os

    base = os.path.basename(overlay_path or "")
    stem, _ext = os.path.splitext(base)
    return stem


def collect_placement_labels(project_folder: str) -> dict:
    """`{rendered segment id: placement label}` off the build's records.

    What `rekey_targets` maps stale digest pins through: the last
    build wrote which placing each file served, per reel, into the
    review records - explainer plans and lower-third plans carry the
    overlay path (the id) in render order, so the label is recomputed
    with the same `segment_name` the renderer was given. Records that
    already carry both fields explicitly (written since the label
    started riding the record) are read directly and win over the
    recomputation.

    Semantic-visual records written before the label started riding
    the record carry NO file identity (only spans and element names),
    so those segments cannot be mapped exactly and are skipped rather
    than guessed at: pins for them stay `unmapped` until re-keyed by
    hand. Caption segments need no map - the provenance prefix already
    survives their re-renders.

    Reads project files only; never Resolve, never a render. One bad
    record file skips; a missing module skips; the map stands on what
    could be read.
    """
    found: dict = {}

    def _take(record_reel: str, segments, namer) -> None:
        for index, entry in enumerate(segments or []):
            if not isinstance(entry, dict):
                continue
            label = entry.get("placement_label") or None
            seg_id = entry.get("segment_id") or None
            if seg_id is None:
                seg_id = _segment_id_of_overlay_path(
                    entry.get("overlay_path") or "")
            if label is None and namer is not None:
                try:
                    label = namer(record_reel, index)
                except Exception:  # noqa: BLE001 - this entry maps no pin
                    label = None
            if seg_id and label:
                found.setdefault(seg_id, label)

    sources = []
    try:
        from library.tools import explainer_plan as _explainer
        sources.append((_explainer.read_plans, _explainer.segment_name))
    except Exception:  # noqa: BLE001 - records unreadable, map stands
        pass
    try:
        from library.tools import speaker_identity as _lower_thirds
        sources.append((_lower_thirds.read_plans, _lower_thirds.segment_name))
    except Exception:  # noqa: BLE001 - records unreadable, map stands
        pass
    try:
        from library.tools import reel_semantic_visual as _semantic
        sources.append((_semantic.read_records, _semantic.segment_name))
    except Exception:  # noqa: BLE001 - records unreadable, map stands
        pass
    for read, namer in sources:
        try:
            stored = read(project_folder) or {}
        except Exception:  # noqa: BLE001 - one bad record skips, not all
            continue
        for record in (stored.get("plans") or []):
            if not isinstance(record, dict):
                continue
            _take(str(record.get("reel") or ""), record.get("segments"),
                  namer)
    return found


def main(argv=None) -> int:
    """Check pins against the build's records, or re-key them, by hand.

    `python3 -m library.tools.overlay_intent <project>`: loads the
    project's `overlay_intent.json` (absent is "nothing pinned" and
    exits 0 saying so; malformed REFUSES, exactly as the build does),
    collects the last build's `{segment id: placing}` map off the
    review records, and reports each pin as EXACT (its render still
    stands - and REKEYABLE where its placing is on record), LABEL
    (already placing-keyed and live), PREFIX (a caption pin the build
    resolves by provenance - checked live, not here) or UNMAPPED (no
    record names it: hand re-key or retire).

    `--rekey` rewrites the file through `rekey_targets`: mapped keys
    become labels carrying their values, everything else stays
    verbatim, collisions stay verbatim and are named. Prints the
    report either way. Reads and (with `--rekey`) writes project
    files only; never Resolve, never a render, never a gate.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Check overlay_intent pins against the build's "
                    "records, or re-key stale digest pins onto placing "
                    "labels.")
    parser.add_argument("project_folder",
                        help="project root (reads external/overlay_intent.json"
                             " and pipeline_output/review/)")
    parser.add_argument("--rekey", action="store_true",
                        help="rewrite mapped digest pins as label pins")
    args = parser.parse_args(argv)

    from library.tools.external_inputs import external_dir

    path = os.path.join(str(external_dir(args.project_folder)),
                        INTENT_FILENAME)
    if not os.path.isfile(path):
        print(f"{path}: no intent file - nothing pinned.", flush=True)
        return 0
    try:
        targets = load_intent(intent_file=path)
    except OverlayIntentError as exc:
        print(f"overlay intent cannot be honoured: {exc}", file=sys.stderr)
        return 2
    label_map = collect_placement_labels(args.project_folder)
    live_ids = set(label_map)
    live_labels = set(label_map.values())
    print(f"{path}: {len(targets)} pin(s), "
          f"{len(label_map)} placed segment(s) on record.")
    for key in sorted(targets):
        if key == CAPTION_KIND:
            state = "KIND (caption default - applies wherever captions play)"
        elif key in live_ids:
            label = label_map.get(key)
            state = ("EXACT (its render still stands)"
                     + (f" - REKEYABLE to placing {label}" if label else
                        " (placing unrecorded - stays exact)"))
        elif key in live_labels:
            state = f"LABEL (live as placing {key})"
        else:
            from library.tools.subtitle_segment_id import is_segment_id
            state = ("PREFIX (caption pin - the build resolves it by "
                     "provenance)" if is_segment_id(key)
                     else "UNMAPPED (no record names it - re-key by hand "
                          "to the placing's current label, or retire it)")
        print(f"  {key}: {state}")
    if not args.rekey:
        return 0
    with open(path, encoding="utf-8") as handle:
        body = json.load(handle)
    new_targets, report = rekey_targets(body.get("targets", {}), label_map)
    body["targets"] = new_targets
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2)
        handle.write("\n")
    print(f"re-keyed {len(report['mapped'])} pin(s): "
          f"{report['mapped'] or '{}'}")
    if report["unmapped"]:
        print(f"left {len(report['unmapped'])} pin(s) verbatim: "
              f"{', '.join(report['unmapped'])}")
    if report["collisions"]:
        print(f"REFUSED {len(report['collisions'])} colliding placing(s): "
              + "; ".join(f"{label} <- {', '.join(keys)}"
                          for label, keys in sorted(
                              report["collisions"].items()))
              + " - retire the stale claimant(s) by hand.")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
