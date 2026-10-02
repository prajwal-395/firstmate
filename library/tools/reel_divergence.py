"""DECLARED, and CARRIED: what the reels actually have on them.

The defect this closes
----------------------
2026-09-11.  PR 995 made the logo card a declared closing element under
``effect.full_frame_elements``, planned against every reel the project
builds.  The declaration is in the captain's ``project.yaml``; the
mechanism works.  Then the captain said: *"you mentioned that the end
card animation of the logo was attached to all reels, that was infact
not true"*.

They were right.  Measured off the project's own timeline snapshots,
with no Resolve running::

    Reel 26   carries logo_reveal   - rebuilt through the new mechanism
    Reel 01, 13, 23, 28, 30, 31     - built before it landed, do not

"Every reel inherits it" was true of the ENGINE and false of the
PROJECT, and nothing anywhere distinguished the two claims.  A landed
mechanism reaches the artefacts that are rebuilt through it and no
others; the reels the captain reviews are the ones that were not.

Why this module and not a rule
------------------------------
The rule already existed in spirit - rebuild the reels when a mechanism
lands - and a rule nobody can check is this repository's dominant defect
(AGENTS.md 10.4).  So this is a MEASUREMENT, not a reminder:

* :func:`survey` reads each reel and answers, per declaration, whether
  that reel CARRIES it.  Three answers, never two: PRESENT, ABSENT, and
  UNDETERMINED with the reason.  An undetermined reading is never
  rounded to either of the other two.
* :func:`assert_reaches` REFUSES a claim that a declaration reached a
  set of reels when the measurement does not back it.  That is the half
  aimed at the reporting failure: the only way to say "every reel has
  it" is to run the survey, and the survey answers per reel.
* :func:`report_divergence` runs at build time, over EVERY approved
  reel rather than the ones being built, and PRINTS.  It never refuses -
  deciding to rebuild a stale reel is the captain's call, not a
  build's - so this fires whether or not anyone remembers, exactly the
  way ``reel_prebuild_census.report_prebuild`` does.

What a detector must say
------------------------
:data:`DETECTORS` is the whole vocabulary.  Each entry names what the
declaration puts on a reel AND what its ABSENCE looks like, because a
check that cannot fail reads as coverage (AGENTS.md 10.4);
:func:`assert_registry_is_well_formed` raises on an entry that names
neither.  An entry with no detector is recorded as such BY NAME, with
the reason and the owner - never quietly left out of the table, because
a declaration missing from a divergence report reads as a declaration
that agrees.

``caption_row`` is that case today.  Reading a caption's delivered row
back off a stored transform is under two documented contradictions
(issue 999, a sibling lane owns them) and this repository has already
recorded that a build set 1296 and a later process read 5184.  A
detector built on that reading would report divergence where there is
none, which is worse than reporting nothing - so it reports nothing,
loudly.

── Closed: 2026-09-12, the captain's seven reels ──────────────────

The divergence this module found was closed by rebuilding, and the
record is here because the survey is what made it checkable.  Before:
six of seven built reels read ABSENT on ``full_frame_elements`` (only
Reel 26 carried it).  After: all seven read CARRIED and
``--claim full_frame_elements`` is BACKED.  Reel 09 was left alone -
the captain's approved final, and its hand-placed card sits on V5
'Semantic', an overlay row, rather than the picture row.

Three things that reading the ARTEFACT settled, and a record could
not have:

* **A survey is not a rebuild's whole story.**  Both detectors read
  CARRIED on the pre-rebuild Reel 26 and on the post-rebuild one,
  while its two motion-graphic segment ids changed underneath -
  which silently retired the captain's last two live
  ``overlay_intent`` pins.  Measured either side: the drawn transform
  is identical (V5 Pan 0 / Tilt 2592, V6 Pan 1167.568 / Tilt 0), so
  the picture did not move; 19 of the 21 pins were already inert
  before the rebuild.  A pin keyed to a rendered artefact's id is
  retired by any re-render, and nothing in the survey says so.
* **The reels were NOT diverging in the direction the drift report
  read.**  ``transform_drift`` measured every picture clip on Reels
  01/23/28/30/31 at Tilt 0.25 in the build snapshot and -0.79 live.
  -0.79 is what the CURRENT engine computes - every rebuild wrote it
  again - so that column was the snapshot being stale, not the
  timeline drifting.  Judge a live value against a build of the
  current engine, not against an older snapshot.
* **A reset is not a hand edit.**  Reel 31's V2 Craig clip read
  Pan 0 / Tilt 0 live against a built -89.947 / 0.25, alone among its
  row.  No captain note asks for it, and the rebuild restored the
  engine's own framing.

``tests/unit/reels/test_reel_divergence.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))


# ── The three answers ─────────────────────────────────────────────

CARRIED = "carried"
ABSENT = "absent"
UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class Presence:
    """One reel's reading of one declaration.

    `carried` is True, False or None, and None is a FIRST-CLASS answer:
    a reel whose timeline could not be read, or a declaration with no
    detector, is undetermined and says why. Rounding it to absent would
    invent a divergence; rounding it to present would hide one.
    """

    carried: Optional[bool]
    detail: str
    detector: str = ""

    @property
    def verdict(self) -> str:
        if self.carried is None:
            return UNDETERMINED
        return CARRIED if self.carried else ABSENT

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "carried": self.carried,
                "detail": self.detail, "detector": self.detector}


def _present(detail: str, detector: str) -> Presence:
    return Presence(carried=True, detail=detail, detector=detector)


def _absent(detail: str, detector: str) -> Presence:
    return Presence(carried=False, detail=detail, detector=detector)


def _undetermined(detail: str, detector: str = "") -> Presence:
    return Presence(carried=None, detail=detail, detector=detector)


# ── Reading a reel, whichever shape it arrived in ─────────────────

def picture_names(snapshot) -> List[str]:
    """Every picture clip's NAME and source basename, lowercased.

    Accepts either a live `timeline_ingest.TimelineSnapshot` or a
    `timeline_serializer` document read off disk, because the same
    question has to be answerable with Resolve running and with it
    closed. A survey that needs Resolve is a survey nobody runs.
    """
    found: List[str] = []
    clips = getattr(snapshot, "clips", None)
    if clips is not None:                      # live snapshot
        for clip in clips:
            if getattr(clip, "track_type", "") != "video":
                continue
            found.append(str(getattr(clip, "name", "") or ""))
            found.append(os.path.basename(
                str(getattr(clip, "source_file", "") or "")))
        return [name.lower() for name in found if name]
    for track in (snapshot or {}).get("tracks") or ():   # serialized doc
        if (track or {}).get("type") != "video":
            continue
        for clip in (track or {}).get("clips") or ():
            found.append(str((clip or {}).get("name") or ""))
            found.append(os.path.basename(
                str((clip or {}).get("file_path") or "")))
    return [name.lower() for name in found if name]


def load_serialized(path) -> dict:
    """A `timeline_serializer` document off disk, as a plain dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── The detectors ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Detector:
    """One declared element, and how a reel is read for it.

    `absent_looks_like` is required of every wired detector and is not
    decoration: it is the statement that this check CAN fail, in the
    same words the test that proves it uses.
    """

    key: str
    what: str
    absent_looks_like: str = ""
    detect: Optional[Callable[[Any, Mapping], Presence]] = None
    undetectable_reason: str = ""
    owner: str = ""


def _detect_full_frame_elements(snapshot, params: Mapping) -> Presence:
    """Does this reel carry the declared full-frame card?

    The card is placed VERBATIM - the engine renders a client's asset
    and never re-authors one (AGENTS.md 13) - so the asset's own file
    name is what lands on the picture track, and matching on it reads
    the artefact rather than a record about it.

    The name is only the FIRST half. A declared file replaced on disk
    keeps its name while every reel playing it changes picture with no
    build, no commit and no record - so a reel whose track names the
    file but whose bytes no longer match the digest recorded at build
    time reads ABSENT, with the two digests named. That is the whole
    P4 story: a name match that reported agreement while the picture
    had changed.
    """
    assets = [str(a) for a in (params or {}).get("assets") or () if a]
    if not assets:
        return _undetermined(
            "the declaration names no asset file to look for",
            "reel_divergence._detect_full_frame_elements")
    names = picture_names(snapshot)
    missing = []
    for asset in assets:
        stem = os.path.basename(asset).lower()
        if not any(stem in name for name in names):
            missing.append(os.path.basename(asset))
    if missing:
        return _absent(
            f"no picture clip names {', '.join(missing)} - this reel was "
            f"built before the declaration landed, or was not rebuilt "
            f"through it",
            "reel_divergence._detect_full_frame_elements")
    digests = (params or {}).get("asset_digests") or {}
    changed = []
    unrecorded = []
    for asset in assets:
        entry = digests.get(asset) or {}
        now, recorded = entry.get("now"), entry.get("recorded")
        if recorded is None:
            unrecorded.append(os.path.basename(asset))
        elif now != recorded:
            changed.append(
                f"{os.path.basename(asset)} (recorded "
                f"{str(recorded)[:12]}..., now "
                f"{str(now)[:12] if now else 'missing'})")
    if changed:
        return _absent(
            f"picture track names {', '.join(changed)} but the file's "
            f"bytes are not what the build recorded - the declared "
            f"asset was replaced on disk after these reels were built, "
            f"so every reel playing it changed picture with no build",
            "reel_divergence._detect_full_frame_elements")
    detail = (f"picture track carries "
              f"{', '.join(os.path.basename(a) for a in assets)}")
    if unrecorded:
        detail += (f" - {', '.join(unrecorded)} predate(s) digest "
                   f"recording, so this is a name match only; rebuild "
                   f"to record a digest the next survey can compare")
    return _present(detail, "reel_divergence._detect_full_frame_elements")


def _detect_freeze_ending(snapshot, params: Mapping) -> Presence:
    """Does this reel hold its last frame under the declared tail?

    `reel_ending.FREEZE_PREFIX` is the naming convention the build
    places a freeze under, and `reel_ending.freeze_items` is the reader
    of the same convention - so this counts the same artefact the
    ending's owner does, rather than a second opinion about it.
    """
    from library.tools.reel_ending import FREEZE_PREFIX

    if not (params or {}).get("expects_freeze"):
        return _undetermined(
            "no ending was resolved for this reel, so whether it should "
            "hold a freeze is unknown - a reel closing on no call to "
            "action correctly carries none",
            "reel_divergence._detect_freeze_ending")
    held = [name for name in picture_names(snapshot)
            if name.startswith(FREEZE_PREFIX.lower())]
    if not held:
        return _absent(
            f"this reel's ending declares a freeze hold and no picture "
            f"clip is named {FREEZE_PREFIX}* - the tail plays live "
            f"picture where it should hold",
            "reel_divergence._detect_freeze_ending")
    return _present(f"{len(held)} freeze item(s) on the picture track",
                    "reel_divergence._detect_freeze_ending")


KEY_FULL_FRAME = "full_frame_elements"
KEY_FREEZE_ENDING = "cta_freeze_ending"
KEY_CAPTION_ROW = "caption_row"


DETECTORS: Dict[str, Detector] = {
    KEY_FULL_FRAME: Detector(
        key=KEY_FULL_FRAME,
        what="the project's declared full-frame cards, placed verbatim "
             "on the reel's own picture track",
        absent_looks_like="a reel whose picture track names no clip "
                          "matching the declared asset file",
        detect=_detect_full_frame_elements,
    ),
    KEY_FREEZE_ENDING: Detector(
        key=KEY_FREEZE_ENDING,
        what="the freeze hold a reel inherits from closing on a call to "
             "action, over which the switch-off draws",
        absent_looks_like="a reel whose resolved ending holds a freeze "
                          "and whose picture track carries no "
                          "reel_freeze_* item",
        detect=_detect_freeze_ending,
    ),
    KEY_CAPTION_ROW: Detector(
        key=KEY_CAPTION_ROW,
        what="the project's declared caption row, as delivered pixels",
        undetectable_reason=(
            "reading a caption's DELIVERED row back off a stored "
            "transform is under two unresolved contradictions (issue "
            "999); this project has already measured a build setting "
            "1296 and a later process reading 5184 for the same clip. "
            "A detector on that reading would report divergence where "
            "there is none, so there is no detector until the "
            "positioning question is settled"),
        owner="issue 999 / library/tools/overlay_placement.py",
    ),
}


class MalformedDetector(ValueError):
    """A registry entry that would read as coverage without being it."""


def assert_registry_is_well_formed() -> None:
    """Every entry either DETECTS and can fail, or says why it cannot.

    The same shape `motion_graphics_vocabulary.assert_roster_is_well_formed`
    enforces on its roster: an entry that only says what a thing is
    teaches a reader to trust a column that measures nothing.
    """
    for key, entry in DETECTORS.items():
        if entry.key != key:
            raise MalformedDetector(
                f"{key!r} is filed under a key its own entry spells "
                f"{entry.key!r}")
        if entry.detect is None:
            if not entry.undetectable_reason:
                raise MalformedDetector(
                    f"{key!r} has no detector and no reason. A "
                    f"declaration silently missing from a divergence "
                    f"report reads as a declaration that agrees")
            if not entry.owner:
                raise MalformedDetector(
                    f"{key!r} is undetectable and names no owner. An "
                    f"unowned gap is one nobody closes")
            continue
        if not entry.absent_looks_like:
            raise MalformedDetector(
                f"{key!r} has a detector and does not say what ABSENT "
                f"looks like. A check that cannot state its failing "
                f"input cannot be shown to fail on one")


# ── What the project declares ─────────────────────────────────────

def _abspathish(named: str, project_folder) -> str:
    """A declared path as an absolute one, WITHOUT checking existence.

    Absolute stays; relative joins the project folder. No existence
    check: a file deleted after the build is exactly what the digest
    comparison must still name, and `hash_asset_file` already answers
    None for it. The plan-time readers (`resolve_clip_asset`,
    `resolve_project_asset`) stay the ones that refuse.
    """
    path = os.path.expanduser(str(named or "").strip())
    if not path:
        return ""
    if not os.path.isabs(path):
        path = os.path.normpath(
            os.path.join(str(project_folder or ""), path))
    return os.path.normpath(path)


def _template_content(project_folder) -> dict:
    """The `content` slots of whatever brand template this adopts.

    `{}` where it adopts none or the registry cannot be read - the
    project-file declarations (`project.yaml`, `external/`) are still
    collected, so a missing template narrows the survey rather than
    emptying it.
    """
    try:
        from library.tools.brand_registry import (
            project_template_name, query_slots, resolve_project_template)
        name = project_template_name(str(project_folder))
        template = resolve_project_template(
            name, project_folder=str(project_folder))
        return query_slots(template, "content") or {}
    except Exception:                               # noqa: BLE001
        return {}


def declared_asset_paths(project_folder, brand_effect=None,
                         brand_content=None) -> Dict[str, str]:
    """{absolute asset path: declared_by} for every declared file.

    Three declaration families, each of which puts bytes on a reel
    that filename matching alone cannot watch:

    * `effect.full_frame_elements` - a `full_frame_clip` IS its file,
      and a card/span's `image` and `font_file` are drawn into it;
    * `content.bookends` - an asset-mode card IS its file, and a
      composition-mode card's `source` is the input its rendered
      pixels are staged verbatim from;
    * `external/placed_assets.json` - the captain's hand-placed cards.

    Best-effort and never raising: a malformed declaration is the
    plan-time reader's to refuse, and a collector that raised would
    take the whole survey down with it. Unresolvable entries are
    skipped - the build that placed from them refused or recorded
    them, and this survey is not a second plan-time.
    """
    from library.tools import full_frame_element as ffe

    found: Dict[str, str] = {}

    def add(named: str, declared_by: str) -> None:
        path = _abspathish(named, project_folder)
        if path:
            found.setdefault(path, declared_by)

    def add_brand_asset(named: str, declared_by: str) -> None:
        # A card/span `image` or `font_file` is a basename in the
        # project's own brand_assets/ (see `remotion_brand_linker` and
        # `full_frame_element._font_resolver`) - resolving it against
        # the project root would hash a path the build never read.
        base = os.path.basename(str(named or "").strip())
        if not base:
            return
        for sub in ("brand_assets/remotion-brand", "brand_assets"):
            candidate = os.path.normpath(os.path.join(
                str(project_folder or ""), sub, base))
            if os.path.isfile(candidate):
                found.setdefault(candidate, declared_by)
                return
        found.setdefault(os.path.normpath(os.path.join(
            str(project_folder or ""), "brand_assets", base)),
            declared_by)

    try:
        effect = ffe.resolve_declaration(brand_effect, str(project_folder))
        for element in ffe.declared_elements(effect):
            if element.get("element") == "full_frame_clip":
                add(str(element.get("asset") or ""),
                    "effect.full_frame_elements")
            else:
                add_brand_asset(str(element.get("image") or ""),
                                "effect.full_frame_elements/image")
                add_brand_asset(str(element.get("font_file") or ""),
                                "effect.full_frame_elements/font_file")
                for segment in element.get("segments") or ():
                    if isinstance(segment, dict):
                        add_brand_asset(
                            str(segment.get("image") or ""),
                            "effect.full_frame_elements/image")
                        add_brand_asset(
                            str(segment.get("font_file") or ""),
                            "effect.full_frame_elements/font_file")
    except Exception:                               # noqa: BLE001
        pass

    try:
        from library.tools import bookends as _bookends
        content = (brand_content if brand_content is not None
                   else _template_content(project_folder))
        for bookend in _bookends.declared_bookends(content):
            if bookend.get("mode") == "asset":
                add(str(bookend.get("asset") or ""),
                    "content.bookends")
            elif bookend.get("source"):
                add(str(bookend.get("source") or ""),
                    "content.bookends/source")
    except Exception:                               # noqa: BLE001
        pass

    try:
        from library.tools import placed_assets as _placed
        for asset in _placed.load_assets(str(project_folder)):
            if isinstance(asset, dict):
                add(str(asset.get("asset") or ""),
                    "external/placed_assets.json")
    except Exception:                               # noqa: BLE001
        pass

    return found


def asset_digests(project_folder, brand_effect=None,
                  brand_content=None) -> Dict[str, Optional[str]]:
    """{absolute asset path: sha256 of its bytes now}, None when absent.

    `code_identity.hash_asset_file`'s shape applied to declared media:
    hash the bytes, record the absence rather than hashing around it.
    """
    from library.tools.code_identity import hash_asset_file

    return {path: hash_asset_file(path)
            for path in declared_asset_paths(
                project_folder, brand_effect, brand_content)}


def declarations(project_folder, brand_effect=None,
                  endings: Optional[Mapping[str, Any]] = None,
                  recorded_assets: Optional[Mapping[str, Any]] = None,
                  brand_content=None) -> List[dict]:
    """The declarations in force, with the parameters each detector needs.

    A project declaring nothing yields `[]`, which is every project
    unless someone opted in - the same shape `full_frame_element`,
    `content.bookends` and `effect.timed_text_overlay` take.

    `endings` maps reel name to the ending `reel_ending.resolve_ending`
    gave it. Omitting it does not silently drop the ending row: the
    declaration is still listed, with no reel expecting a freeze, so
    every reel reads UNDETERMINED and says the ending was never
    resolved. A row that vanished would read as agreement.

    `recorded_assets` maps absolute asset paths to the digests
    `plan_provenance` recorded at build time. Beside them the
    declaration carries each asset's digest NOW, so the detector
    compares bytes rather than filenames. Omitting it does not drop
    the check either: every reel reads carried-by-name with the
    absence of a recorded digest said out loud.
    """
    from library.tools import full_frame_element as ffe

    out: List[dict] = []
    effect = ffe.resolve_declaration(brand_effect, str(project_folder))
    declared = ffe.declared_elements(effect)
    if declared:
        assets = [d.get("asset") for d in declared if d.get("asset")]
        named = ", ".join(os.path.basename(str(a)) for a in assets)
        current = asset_digests(project_folder, brand_effect,
                                brand_content)
        recorded = dict(recorded_assets or {})
        per_asset = {}
        for asset in assets:
            absolute = _abspathish(str(asset), project_folder)
            per_asset[str(asset)] = {
                "now": current.get(absolute),
                "recorded": recorded.get(absolute),
            }
        out.append({
            "key": KEY_FULL_FRAME,
            "declared_by": "effect.full_frame_elements",
            "params": {"assets": assets, "asset_digests": per_asset},
            "summary": (f"{len(declared)} card(s): "
                        f"{named or 'no asset named'}"),
        })

    expects = {reel: bool((ending or {}).get("tail_hold") == "freeze")
               for reel, ending in (endings or {}).items()}
    out.append({
        "key": KEY_FREEZE_ENDING,
        "declared_by": "reel_ending.resolve_ending (declared or inherited)",
        "params": {"expects": expects},
        "summary": (f"{sum(1 for v in expects.values() if v)} reel(s) "
                    f"resolve to a freeze hold"
                    if expects else
                    "no ending resolved - every reel reads undetermined"),
    })

    out.append({
        "key": KEY_CAPTION_ROW,
        "declared_by": "style.subtitle.caption_row",
        "params": {},
        "summary": "declared; no detector (see DETECTORS)",
    })
    return out


# ── The survey ────────────────────────────────────────────────────

def survey(project_folder, snapshots: Mapping[str, Any],
            brand_effect=None,
            endings: Optional[Mapping[str, Any]] = None,
            notes: Optional[Mapping[str, str]] = None,
            recorded_assets: Optional[Mapping[str, Any]] = None,
            brand_content=None) -> dict:
    """Read every reel against every declaration. Never raises.

    `snapshots` maps reel name to its timeline - a live snapshot or a
    serialized document. A reel mapped to None, or named only in
    `notes`, is UNDETERMINED for every declaration with that note as
    the reason: an unread reel is not a reel without the card.

    `recorded_assets` is the digest half of the asset comparison -
    absolute paths to the bytes `plan_provenance` recorded at build
    time. Without it the full-frame detector matches names only and
    says so per reel.

    Returns `{"declarations": [...], "reels": {reel: {key: {...}}},
    "divergent": {key: [reels]}, "undetermined": {key: [reels]}}`.
    """
    assert_registry_is_well_formed()
    notes = dict(notes or {})
    active = declarations(project_folder, brand_effect, endings,
                          recorded_assets=recorded_assets,
                          brand_content=brand_content)
    reels = sorted(set(snapshots or {}) | set(notes))

    table: Dict[str, Dict[str, dict]] = {}
    divergent: Dict[str, List[str]] = {}
    undetermined: Dict[str, List[str]] = {}

    for reel in reels:
        snapshot = (snapshots or {}).get(reel)
        row: Dict[str, dict] = {}
        for declaration in active:
            key = declaration["key"]
            entry = DETECTORS[key]
            if entry.detect is None:
                presence = _undetermined(
                    f"no detector: {entry.undetectable_reason} "
                    f"(owner: {entry.owner})")
            elif snapshot is None:
                presence = _undetermined(
                    notes.get(reel)
                    or "this reel's timeline was not read, so nothing "
                       "about it was measured")
            else:
                params = dict(declaration.get("params") or {})
                if key == KEY_FREEZE_ENDING:
                    params["expects_freeze"] = bool(
                        params.get("expects", {}).get(reel))
                try:
                    presence = entry.detect(snapshot, params)
                except Exception as failed:      # noqa: BLE001
                    presence = _undetermined(
                        f"the detector raised "
                        f"{type(failed).__name__}: {failed}")
            row[key] = presence.as_dict()
            if presence.carried is False:
                divergent.setdefault(key, []).append(reel)
            elif presence.carried is None:
                undetermined.setdefault(key, []).append(reel)
        table[reel] = row

    return {"declarations": active, "reels": table,
            "divergent": {k: sorted(v) for k, v in divergent.items()},
            "undetermined": {k: sorted(v) for k, v in undetermined.items()}}


def render_survey(report: Mapping) -> str:
    """The printable table: one line per reel, one column per declaration."""
    active = list((report or {}).get("declarations") or ())
    reels = dict((report or {}).get("reels") or {})
    lines = [f"── Declared-vs-carried survey "
             f"({len(reels)} reel(s), {len(active)} declaration(s)) ──"]
    if not active:
        lines.append("  this project declares no reel-level element - "
                     "nothing to diverge from.")
        return "\n".join(lines)
    if not reels:
        lines.append("  no reel timeline was read - nothing measured.")
        return "\n".join(lines)
    for declaration in active:
        key = declaration["key"]
        lines.append(f"  {key} <- {declaration['declared_by']}: "
                     f"{declaration.get('summary', '')}")
        for reel in sorted(reels):
            cell = (reels[reel] or {}).get(key) or {}
            verdict = cell.get("verdict", UNDETERMINED)
            mark = {CARRIED: "carries", ABSENT: "ABSENT ",
                    UNDETERMINED: "unknown"}[verdict]
            lines.append(f"      {mark}  {reel}")
            if verdict != CARRIED:
                lines.append(f"                {cell.get('detail', '')}")
        stale = (report.get("divergent") or {}).get(key) or []
        if stale:
            lines.append(f"    -> {len(stale)} reel(s) DIVERGE from this "
                         f"declaration. Rebuilding them is the captain's "
                         f"call; this report is not a refusal.")
    return "\n".join(lines)


# ── Refusing a claim the artefacts do not back ────────────────────

class ClaimNotBackedByArtefacts(AssertionError):
    """A claim that a declaration reached reels the survey says it did not.

    The 2026-09-11 failure in one exception: the engine's capability was
    reported as the project's state. Engine capability and project state
    are different claims, and this is the one call that can tell them
    apart - so a claim about REELS goes through a measurement of reels.
    """


def assert_reaches(report: Mapping, key: str,
                   reels: Optional[Sequence[str]] = None) -> List[str]:
    """Refuse unless every named reel CARRIES `key`. Returns those reels.

    `reels` defaults to every reel the survey read. An UNDETERMINED
    reading refuses just as an absent one does, and says which it was:
    "we did not look" has never been evidence that something is there,
    and this is the exact step at which the round-4 claim was made.
    """
    if key not in DETECTORS:
        raise ClaimNotBackedByArtefacts(
            f"{key!r} is not a declaration this module detects; known: "
            f"{', '.join(sorted(DETECTORS))}")
    table = dict((report or {}).get("reels") or {})
    wanted = list(reels) if reels is not None else sorted(table)
    if not wanted:
        raise ClaimNotBackedByArtefacts(
            f"cannot claim {key!r} reached any reel: the survey read no "
            f"reel at all. A claim about zero measurements is not a "
            f"claim that passed")
    failures = []
    for reel in wanted:
        cell = (table.get(reel) or {}).get(key)
        if cell is None:
            failures.append(f"{reel}: not in the survey")
            continue
        if cell.get("verdict") != CARRIED:
            failures.append(f"{reel}: {cell.get('verdict')} - "
                            f"{cell.get('detail', '')}")
    if failures:
        raise ClaimNotBackedByArtefacts(
            f"{key!r} did NOT reach {len(failures)} of {len(wanted)} "
            f"reel(s):\n  " + "\n  ".join(failures))
    return list(wanted)


# ── The build-time report ─────────────────────────────────────────

def report_divergence(project_folder, snapshots: Mapping[str, Any],
                       brand_effect=None, endings=None,
                       notes=None, recorded_assets=None,
                       brand_content=None) -> dict:
    """Survey, PRINT, return. Never raises, never refuses.

    Called by the build over EVERY approved reel - not just the ones it
    is about to place - so the reels a build leaves behind are named at
    the moment someone is already looking. A probe that could fail the
    build would be a warning holding a build hostage, and whether to
    rebuild a diverged reel is the captain's decision.

    `recorded_assets` is the build-time digest half (absolute paths to
    sha256, as `plan_provenance` records under `asset_hashes`) - with
    it the survey names a declared file replaced on disk after the
    build; without it the detector matches names only and says so.
    """
    try:
        report = survey(project_folder, snapshots, brand_effect=brand_effect,
                        endings=endings, notes=notes,
                        recorded_assets=recorded_assets,
                        brand_content=brand_content)
    except Exception as failed:                  # noqa: BLE001
        print(f"  divergence survey unavailable ({failed}) - building "
              f"without it", file=sys.stderr)
        return {"unavailable": str(failed)}
    print(render_survey(report), flush=True)
    return report


def snapshots_for(project, reels: Sequence[str], snapshot_fn=None) -> tuple:
    """Read each reel's live timeline, read-only. `(snapshots, notes)`.

    A reel with no timeline of that exact name, or one that will not
    read, lands in `notes` and takes no part as a measurement - the
    same treatment `reel_prebuild_census.census_for_build` gives an
    absent final, and for the same reason: an unread reel must not read
    as a reel that lacks the card.
    """
    if snapshot_fn is None:
        from library.tools.timeline_ingest import snapshot_timeline
        snapshot_fn = snapshot_timeline
    by_name = {}
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None:
            by_name[timeline.GetName()] = timeline
    snapshots: Dict[str, Any] = {}
    notes: Dict[str, str] = {}
    for reel in reels:
        timeline = by_name.get(reel)
        if timeline is None:
            notes[reel] = ("no timeline of that exact name in the project - "
                           "this reel has not been built")
            continue
        try:
            snapshots[reel] = snapshot_fn(timeline, project.GetName())
        except Exception as unreadable:           # noqa: BLE001
            notes[reel] = f"could not be read ({unreadable}) - not measured"
    return snapshots, notes


# ── CLI ───────────────────────────────────────────────────────────

def snapshots_from_review(project_folder, reels=None) -> tuple:
    """Every serialized reel timeline under the project's review area.

    `timeline_serializer` writes one per reel and the build refreshes
    them, so the survey runs with Resolve CLOSED - which is what makes
    it cheap enough to run before making a claim rather than after
    being contradicted. A snapshot is a record, not the artefact: its
    own `timestamp` is printed so a stale reading is visible as one.
    """
    review = Path(project_folder) / "pipeline_output" / "review"
    snapshots: Dict[str, Any] = {}
    notes: Dict[str, str] = {}
    if not review.is_dir():
        return snapshots, {"(project)": f"no review directory at {review}"}
    for path in sorted(review.glob("*.timeline.json")):
        try:
            doc = load_serialized(path)
        except Exception as unreadable:           # noqa: BLE001
            notes[path.name] = f"unreadable ({unreadable})"
            continue
        name = str(((doc.get("metadata") or {}).get("name")) or "").strip()
        if not name:
            # Only worth saying when nobody asked for a specific roster:
            # a document that names no timeline cannot be one of the
            # reels `reels` lists, so it is out of scope rather than
            # unmeasured.
            if reels is None:
                notes[path.name] = "the document names no timeline"
            continue
        if reels is not None and name not in set(reels):
            continue
        snapshots[name] = doc
    if reels is not None:
        for reel in reels:
            if reel not in snapshots:
                notes[reel] = ("no serialized timeline under "
                               "pipeline_output/review/ - not measured")
    return snapshots, notes


def approved_reels(project_folder) -> List[str]:
    """The APPROVED reels the plan names, which is what a reel is.

    Not a name shape. `(baseline scratch)`, `(rebuild staging)` and
    every hand-made container look like reels and are not ones the
    captain reviews; the plan is the only list that says which are.
    Returns `[]` when there is no readable plan, and the caller then
    surveys whatever it was given rather than inventing a roster.
    """
    try:
        from library.tools.reel_proposal import (proposal_path,
                                                 read_proposal)
        moments = read_proposal(str(proposal_path(project_folder)))
    except Exception:                             # noqa: BLE001
        return []
    return [m.timeline_name for m in moments
            if str(getattr(m.approval, "value", m.approval)) == "approved"]


def endings_for(project_folder, reels: Sequence[str]) -> Dict[str, Any]:
    """Each reel's resolved ending, declared or inherited, or absent.

    Reads the plan for the moment the CTA default is inherited from, so
    the freeze column measures what the build would give this reel
    rather than reading undetermined everywhere. A reel whose ending
    cannot be resolved is simply left out, which is what makes it read
    undetermined instead of falsely absent.
    """
    try:
        from library.tools.reel_ending import resolve_ending
        from library.tools.reel_proposal import (proposal_path,
                                                 read_proposal)
        moments = {m.timeline_name: m
                   for m in read_proposal(str(proposal_path(project_folder)))}
    except Exception:                             # noqa: BLE001
        return {}
    out: Dict[str, Any] = {}
    for reel in reels:
        try:
            ending = resolve_ending(str(project_folder), reel,
                                    moments.get(reel))
        except Exception:                         # noqa: BLE001
            continue
        if ending is not None:
            out[reel] = ending
    return out


# ── Record-vs-reel: git diff for a reel ───────────────────────────
#
# The captain's version-control judgement (2026-09-21): the version is
# stamped PER REEL, the commit is per round, the project is the repo -
# a FILE is the unit of revert and a COMMIT is the unit of change. What
# that needs, and what this section is: a NON-REFUSING printed diff of
# each reel's live projection against its last committed
# `.timeline.json`. A diff that refuses is not a diff, so every reel
# answers conflicts, agreements and undetermined, and no reel raises.
#
# The SET is the unit, not the reel: it takes any number of reels and
# prints a cross-reel summary first ("these six changed, these
# twenty-four did not"), because triage across several reels is the
# thing the captain named. A single reel is a set of one. Detail per
# reel is capped (`RECORD_DETAIL_LIMIT`) so a 39-row reel does not bury
# the summary - run by hand on Reel 13 this found 39 unclaimed
# differences, which is what set the cap's shape.
#
# What is compared: the serializer-shaped documents
# (`timeline_serializer`), clip by clip - record and source windows,
# enabled, file, transform, crop, composite, retime and (on audio
# tracks) the clip's own audio block. NOT compared, and why: `unique_id`
# (a rebuild mints new ones, so it is not an identity), `duration` and
# the offsets (derived from the windows), pool ids and clip colour
# (re-import noise), markers (owned by `marker_feedback`), colour and
# Fusion (the look rides the versioned `.comp` files, which are becoming
# plain Lua text in a sibling lane - this diff sees more without
# changes when they do, and depends on nothing of it).
#
# Matching is by (track type, track index, record_in, name) first, then
# by name within the track for what moved: a clip the live reel no
# longer has, or one the commit never saw, is a CONFLICT ("unclaimed"),
# never a drop. Floats compare within 1e-3, the same tolerance
# `transform_drift.UNMOVED` measures Resolve read-back noise at - a hand
# edit is orders above it.

AGREEMENT = "agreement"
CONFLICT = "conflict"

#: How many conflict rows one reel may print before the overflow line.
RECORD_DETAIL_LIMIT = 10

#: Scalar clip fields compared verbatim (floats within tolerance).
_COMPARED_CLIP_FIELDS = ("record_out", "source_in", "source_out",
                         "enabled", "file_path")

#: Mapping clip fields compared key by key.
_COMPARED_MAPPING_FIELDS = ("transform", "crop", "composite", "retime",
                            "audio")

#: Floats this close are the same reading, not a move.
FLOAT_CLOSE = 1e-3


def _floats_close(first, second) -> bool:
    try:
        return abs(float(first) - float(second)) <= FLOAT_CLOSE
    except (TypeError, ValueError):
        return False


def _values_match(record_value, live_value) -> bool:
    """Whether two recorded values are the same reading."""
    if isinstance(record_value, bool) or isinstance(live_value, bool):
        return record_value is live_value
    if isinstance(record_value, (int, float)) and isinstance(
            live_value, (int, float)):
        return _floats_close(record_value, live_value)
    if isinstance(record_value, Mapping) and isinstance(live_value, Mapping):
        if set(record_value) != set(live_value):
            return False
        return all(_values_match(record_value[key], live_value[key])
                   for key in record_value)
    if isinstance(record_value, list) and isinstance(live_value, list):
        return (len(record_value) == len(live_value)
                and all(_values_match(old, new)
                        for old, new in zip(record_value, live_value)))
    return record_value == live_value


def _compact(value) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _clip_entries(document):
    """`(track_key, track_label, clip)` in timeline order.

    Raises `TypeError` on a document with no `tracks` list - an empty
    reading of an unreadable file is the silent pass, here as in
    `drift_check.keyed_transforms`.
    """
    tracks = (document or {}).get("tracks") if isinstance(document,
                                                         Mapping) else None
    if not isinstance(tracks, list):
        raise TypeError(
            "this is not a serialized timeline: it carries no `tracks` "
            "list, so there is nothing to compare.")
    out = []
    for track in tracks:
        if not isinstance(track, Mapping):
            continue
        track_type = str(track.get("type") or "")
        try:
            index = int(track.get("index"))
        except (TypeError, ValueError):
            continue
        label = (f"{track_type} {index} "
                 f"'{track.get('name') or '#' + str(index)}'")
        for clip in track.get("clips") or ():
            if isinstance(clip, Mapping):
                out.append(((track_type, index), label, clip))
    return out


def _field_changes(record_clip, live_clip) -> List[str]:
    """`["record_out 1069 -> 1079", ...]` for every compared field."""
    changes = []
    for field_name in _COMPARED_CLIP_FIELDS:
        old, new = record_clip.get(field_name), live_clip.get(field_name)
        if old is None and new is None:
            continue
        if not _values_match(old, new):
            changes.append(
                f"{field_name} {_compact(old)} -> {_compact(new)}")
    for field_name in _COMPARED_MAPPING_FIELDS:
        old, new = record_clip.get(field_name), live_clip.get(field_name)
        if old is None and new is None:
            continue
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            for key in sorted(set(old) | set(new)):
                if not _values_match(old.get(key), new.get(key)):
                    changes.append(
                        f"{field_name}.{key} {_compact(old.get(key))} -> "
                        f"{_compact(new.get(key))}")
        elif not _values_match(old, new):
            changes.append(
                f"{field_name} {_compact(old)} -> {_compact(new)}")
    return changes


def _span(clip) -> str:
    return f"@{clip.get('record_in')}..{clip.get('record_out')}"


def diff_record_vs_live(record_doc, live_doc, reel: str = "") -> dict:
    """One reel's committed record against its live projection.

    Pure: both sides are serializer-shaped documents, so this runs with
    Resolve closed - fixtures and recorded projections in, rows out.
    Never raises for a reel: an unreadable side is one UNDETERMINED row
    naming which side and why, not an exception and not silence.

    Returns `{"reel", "compared", "reason", "agreements", "conflicts",
    "undetermined", "counts", "line"}`. A conflict is a clip that moved,
    was trimmed, retimed, reframed, toggled, replaced, added or removed;
    an agreement is a clip identical in every compared field; anything
    that cannot be keyed (no name, no record_in) is undetermined.
    """
    name = reel or str(((live_doc or {}).get("metadata") or {}).get(
        "name") or ((record_doc or {}).get("metadata") or {}).get(
            "name") or "(unnamed)")
    report = {"reel": name, "compared": False, "reason": "",
              "agreements": [], "conflicts": [], "undetermined": [],
              "counts": {"agreements": 0, "conflicts": 0,
                         "undetermined": 0}, "line": ""}
    try:
        record_entries = _clip_entries(record_doc)
    except (TypeError, ValueError) as unreadable:
        report["reason"] = f"the committed record cannot be read: {unreadable}"
        report["undetermined"].append(
            {"kind": UNDETERMINED, "detail": report["reason"]})
        report["counts"]["undetermined"] = 1
        report["line"] = f"{name}: not comparable - {report['reason']}"
        return report
    try:
        live_entries = _clip_entries(live_doc)
    except (TypeError, ValueError) as unreadable:
        report["reason"] = f"the live projection cannot be read: {unreadable}"
        report["undetermined"].append(
            {"kind": UNDETERMINED, "detail": report["reason"]})
        report["counts"]["undetermined"] = 1
        report["line"] = f"{name}: not comparable - {report['reason']}"
        return report

    record_tracks = {key for key, _, _ in record_entries}
    live_tracks = {key for key, _, _ in live_entries}
    for key in sorted(live_tracks - record_tracks):
        label = next(label for candidate, label, _ in live_entries
                     if candidate == key)
        held = sum(1 for candidate, _, _ in live_entries
                   if candidate == key)
        report["conflicts"].append({
            "kind": CONFLICT, "track": label, "name": "",
            "detail": (f"track {label} is unclaimed by the commit: only "
                       f"on the live reel ({held} clip(s) arrived with "
                       f"it)")})
    skip_tracks = (live_tracks ^ record_tracks)
    for key in sorted(record_tracks - live_tracks):
        label = next(label for candidate, label, _ in record_entries
                     if candidate == key)
        lost = sum(1 for candidate, _, _ in record_entries
                   if candidate == key)
        report["conflicts"].append({
            "kind": CONFLICT, "track": label, "name": "",
            "detail": (f"track {label} is unclaimed by the live reel: "
                       f"only in the commit ({lost} clip(s) went with "
                       f"it)")})

    def keyed(entries):
        keyed_entries: Dict[tuple, list] = {}
        for track_key, label, clip in entries:
            if track_key in skip_tracks:
                continue
            try:
                start = int(clip.get("record_in"))
            except (TypeError, ValueError):
                report["undetermined"].append({
                    "kind": UNDETERMINED, "track": label,
                    "name": str(clip.get("name") or ""),
                    "detail": ("a clip with no numeric record_in cannot "
                               "be keyed to the other side, so it is "
                               "neither agreement nor conflict")})
                continue
            clip_name = str(clip.get("name") or "")
            if not clip_name:
                report["undetermined"].append({
                    "kind": UNDETERMINED, "track": label,
                    "name": "", "detail": ("a clip with no name cannot "
                                           "be keyed to the other side")})
                continue
            keyed_entries.setdefault(
                (track_key, start, clip_name), []).append((label, clip))
        return keyed_entries

    record_keyed, live_keyed = keyed(record_entries), keyed(live_entries)
    for key in sorted(set(record_keyed) & set(live_keyed)):
        (track_key, start, clip_name) = key
        pairs = zip(sorted(record_keyed[key], key=lambda pair: _span(pair[1])),
                    sorted(live_keyed[key], key=lambda pair: _span(pair[1])))
        for (label, old), (_, new) in pairs:
            changes = _field_changes(old, new)
            if not changes:
                report["agreements"].append({
                    "kind": AGREEMENT, "track": label, "name": clip_name,
                    "record_in": start})
            else:
                report["conflicts"].append({
                    "kind": CONFLICT, "track": label, "name": clip_name,
                    "record_in": start,
                    "detail": f"{label} {_span(old)} {clip_name}: "
                              f"{'; '.join(changes)}"})
        leftovers = len(record_keyed[key]) - len(live_keyed[key])
        if leftovers > 0:
            report["conflicts"].append({
                "kind": CONFLICT, "track": label, "name": clip_name,
                "record_in": start,
                "detail": (f"{label} {clip_name} @{start}: the commit "
                           f"holds {leftovers} more placement(s) here "
                           f"than the live reel - removed or renamed")})

    # The leftovers of the exact pass, repooled by (track, name): a
    # clip the live reel moved still carries its name, so it pairs
    # here and reads as moved rather than as removed-plus-added. FIFO
    # within a repeated name - a detail the row states out loud via
    # both spans, so a human can tell a pairing from a fact.
    record_only_keys = set(record_keyed) - set(live_keyed)
    live_only_keys = set(live_keyed) - set(record_keyed)
    record_pool: Dict[tuple, list] = {}
    for (track_key, _start, _clip_name), pairs in record_keyed.items():
        if (track_key, _start, _clip_name) in record_only_keys:
            for label, clip in pairs:
                record_pool.setdefault(
                    (track_key, str(clip.get("name") or "")),
                    []).append((label, clip))
    live_pool: Dict[tuple, list] = {}
    for (track_key, _start, _clip_name), pairs in live_keyed.items():
        if (track_key, _start, _clip_name) in live_only_keys:
            for label, clip in pairs:
                live_pool.setdefault(
                    (track_key, str(clip.get("name") or "")),
                    []).append((label, clip))
    for key in sorted(set(record_pool) & set(live_pool)):
        olds = sorted(record_pool[key],
                      key=lambda pair: _span(pair[1]))
        news = sorted(live_pool[key],
                      key=lambda pair: _span(pair[1]))
        for (label, old), (_, new) in zip(olds, news):
            changes = [change for change in _field_changes(old, new)
                       if not change.startswith("record_in ")]
            try:
                moved = int(old.get("record_in")) != int(new.get(
                    "record_in"))
            except (TypeError, ValueError):
                moved = True
            head = (f"moved {_span(old)} -> {_span(new)}"
                    if moved else f"re-cut {_span(old)} -> {_span(new)}")
            tail = f": {'; '.join(changes)}" if changes else ""
            report["conflicts"].append({
                "kind": CONFLICT, "track": label,
                "name": str(new.get("name") or ""),
                "record_in": new.get("record_in"),
                "detail": f"{label} {new.get('name')}: {head}{tail}"})
        if len(olds) != len(news):
            report["conflicts"].append({
                "kind": CONFLICT, "track": olds[0][0] if olds else
                news[0][0], "name": key[1],
                "detail": (f"{key[1]}: the commit holds {len(olds)} "
                           f"placement(s) on this track, the live reel "
                           f"{len(news)} - the difference is unclaimed "
                           f"on one side")})
    for key in sorted(set(record_pool) - set(live_pool)):
        for label, clip in record_pool[key]:
            report["conflicts"].append({
                "kind": CONFLICT, "track": label,
                "name": str(clip.get("name") or ""),
                "record_in": clip.get("record_in"),
                "detail": (f"{label} {_span(clip)} {clip.get('name')}: "
                           f"only in the commit - removed from the live "
                           f"reel or renamed there, unclaimed by it")})
    for key in sorted(set(live_pool) - set(record_pool)):
        for label, clip in live_pool[key]:
            report["conflicts"].append({
                "kind": CONFLICT, "track": label,
                "name": str(clip.get("name") or ""),
                "record_in": clip.get("record_in"),
                "detail": (f"{label} {_span(clip)} {clip.get('name')}: "
                           f"only on the live reel - added after the "
                           f"commit or renamed, unclaimed by it")})

    report["compared"] = True
    counts = report["counts"]
    counts["agreements"] = len(report["agreements"])
    counts["conflicts"] = len(report["conflicts"])
    counts["undetermined"] = len(report["undetermined"])
    if counts["conflicts"] or counts["undetermined"]:
        report["line"] = (
            f"{name}: {counts['conflicts']} difference(s) vs the commit, "
            f"{counts['agreements']} agreement(s)")
        if counts["undetermined"]:
            report["line"] += f", {counts['undetermined']} undetermined"
    else:
        report["line"] = (
            f"{name}: {counts['agreements']} placement(s) hold exactly "
            f"what the commit recorded")
    return report


def _project_git(project_folder, *args):
    """`git -C <project>` that never raises; callers judge returncode."""
    import subprocess

    return subprocess.run(
        ["git", "-C", str(project_folder), *args],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=120, check=False)


def committed_document(project_folder, reel: str) -> dict:
    """This reel's last COMMITTED `.timeline.json`, or why there is none.

    The record side never comes from the worktree file: it is
    `git show HEAD:<path>`, so uncommitted builds cannot read as the
    record. The path is found from the on-disk snapshot naming this
    reel first (its `metadata.name`, never its filename - promotion
    sanitises names into filenames), falling back to scanning HEAD's
    own blobs for a document naming the reel, which also covers a reel
    whose worktree file was deleted. Returns `{"reel", "path",
    "document" (or None), "reason"}` - never raises.
    """
    found: dict = {"reel": reel, "path": None, "document": None,
                   "reason": ""}
    try:
        review = Path(project_folder) / "pipeline_output" / "review"
        on_disk = None
        if review.is_dir():
            candidates = []
            for path in review.glob("*.timeline.json"):
                try:
                    document = load_serialized(path)
                except Exception:                   # noqa: BLE001
                    continue
                if str(((document or {}).get("metadata") or {}).get(
                        "name") or "").strip() == reel:
                    try:
                        candidates.append((path.stat().st_mtime, path))
                    except OSError:
                        continue
            if candidates:
                on_disk = max(candidates)[1]
        if on_disk is not None:
            relative = str(on_disk.relative_to(project_folder))
            shown = _project_git(project_folder, "show",
                                 f"HEAD:{relative}")
            if shown.returncode == 0:
                try:
                    found["document"] = json.loads(shown.stdout)
                    found["path"] = relative
                    return found
                except ValueError as unreadable:
                    found["reason"] = (
                        f"the committed {relative} cannot be parsed "
                        f"({unreadable}) - the record is damaged, and a "
                        f"damaged record is not an agreement")
                    return found
            tail = (shown.stderr or "").strip().splitlines()
            found["reason"] = (
                "no committed snapshot for this reel"
                + (f" ({tail[-1][-160:]})" if tail and tail[-1].strip()
                   else "")
                + " - the worktree file is uncommitted, or this "
                  "project is not a git repo")
            found["path"] = relative
            return found
        listed = _project_git(project_folder, "ls-tree", "-r", "--name-only",
                              "HEAD", "--", "pipeline_output/review")
        if listed.returncode != 0:
            found["reason"] = (
                "no snapshot file names this reel and the committed "
                "history cannot be listed - this project may not be a "
                "git repo")
            return found
        for relative in listed.stdout.splitlines():
            relative = relative.strip()
            if not relative.endswith(".timeline.json"):
                continue
            shown = _project_git(project_folder, "show",
                                 f"HEAD:{relative}")
            if shown.returncode != 0:
                continue
            try:
                document = json.loads(shown.stdout)
            except ValueError:
                continue
            if str(((document or {}).get("metadata") or {}).get(
                    "name") or "").strip() == reel:
                found["document"] = document
                found["path"] = relative
                return found
        found["reason"] = ("no committed snapshot under "
                           "pipeline_output/review/ names this reel")
        return found
    except Exception as failed:                       # noqa: BLE001
        found["reason"] = (f"the committed record could not be looked up "
                           f"({failed}) - not measured, never agreement")
        return found


def diff_records(project_folder, reels=None, live_by_reel=None,
                 live_notes=None) -> dict:
    """Diff a SET of reels: each live projection vs its commit.

    `reels` defaults to every reel with a worktree snapshot. The live
    side is `live_by_reel[reel]` where supplied - a serializer-shaped
    document, from a fixture, a recorded projection or (later) a live
    Resolve read - and the reel's worktree snapshot otherwise, so the
    diff works today on what is already recorded: uncommitted builds
    read as differences against the commit, which is exactly what a
    `git diff` for reels should say. `live_notes[reel]` forces that
    reel undetermined with the note as the reason (an unreadable
    `--live-doc` file, a failed live read).

    Never raises for a reel, and never refuses: the report carries
    `changed`, `unchanged` and `undetermined_reels`, and the render
    prints the cross-reel summary FIRST so one loud reel cannot bury
    twenty-nine quiet ones.
    """
    live_by_reel = dict(live_by_reel or {})
    live_notes = dict(live_notes or {})
    try:
        worktree, worktree_notes = snapshots_from_review(
            project_folder, list(reels) if reels is not None else None)
    except Exception as failed:                       # noqa: BLE001
        return {"reels": {}, "changed": [], "unchanged": [],
                "undetermined_reels": [],
                "reason": f"snapshots cannot be listed ({failed})",
                "totals": {"conflicts": 0, "agreements": 0,
                           "undetermined": 0}}
    wanted = list(reels) if reels is not None else sorted(worktree)
    table: Dict[str, dict] = {}
    for reel in wanted:
        try:
            committed = committed_document(project_folder, reel)
            if reel in live_notes:
                per = {"reel": reel, "compared": False,
                       "reason": str(live_notes[reel]),
                       "agreements": [], "conflicts": [],
                       "undetermined": [{"kind": UNDETERMINED,
                                         "detail": str(
                                             live_notes[reel])}],
                       "counts": {"agreements": 0, "conflicts": 0,
                                  "undetermined": 1},
                       "line": f"{reel}: not comparable - "
                               f"{live_notes[reel]}"}
            elif committed["document"] is None:
                per = {"reel": reel, "compared": False,
                       "reason": committed["reason"],
                       "agreements": [], "conflicts": [],
                       "undetermined": [{"kind": UNDETERMINED,
                                         "detail": committed["reason"]}],
                       "counts": {"agreements": 0, "conflicts": 0,
                                  "undetermined": 1},
                       "line": f"{reel}: not comparable - "
                               f"{committed['reason']}"}
            else:
                live = live_by_reel.get(reel, worktree.get(reel))
                if live is None:
                    reason = (worktree_notes.get(reel)
                              or "no live projection was supplied and no "
                                 "worktree snapshot carries this reel - "
                                 "not measured")
                    per = {"reel": reel, "compared": False,
                           "reason": reason, "agreements": [],
                           "conflicts": [],
                           "undetermined": [{"kind": UNDETERMINED,
                                             "detail": reason}],
                           "counts": {"agreements": 0, "conflicts": 0,
                                      "undetermined": 1},
                           "line": f"{reel}: not comparable - {reason}"}
                else:
                    per = diff_record_vs_live(
                        committed["document"], live, reel=reel)
                    per["commit_path"] = committed["path"]
            table[reel] = per
        except Exception as failed:                   # noqa: BLE001
            table[reel] = {
                "reel": reel, "compared": False,
                "reason": f"the diff raised {type(failed).__name__}: "
                          f"{failed} - reported, never refused",
                "agreements": [], "conflicts": [],
                "undetermined": [{"kind": UNDETERMINED,
                                  "detail": f"the diff raised "
                                            f"{type(failed).__name__}: "
                                            f"{failed}"}],
                "counts": {"agreements": 0, "conflicts": 0,
                           "undetermined": 1},
                "line": f"{reel}: not comparable - the diff itself "
                        f"failed ({failed})"}
    changed = sorted(reel for reel, per in table.items()
                     if per.get("compared")
                     and per.get("counts", {}).get("conflicts"))
    unchanged = sorted(reel for reel, per in table.items()
                       if per.get("compared")
                       and not per.get("counts", {}).get("conflicts"))
    undetermined_reels = sorted(set(table) - set(changed) - set(unchanged))
    return {
        "reels": table, "changed": changed, "unchanged": unchanged,
        "undetermined_reels": undetermined_reels,
        "totals": {
            "conflicts": sum(per.get("counts", {}).get("conflicts", 0)
                             for per in table.values()),
            "agreements": sum(per.get("counts", {}).get("agreements", 0)
                              for per in table.values()),
            "undetermined": sum(per.get("counts", {}).get(
                "undetermined", 0) for per in table.values())}}


def render_record_diff(report: Mapping,
                       detail_limit: int = RECORD_DETAIL_LIMIT) -> str:
    """The printable diff: cross-reel summary first, capped detail after."""
    reels = dict((report or {}).get("reels") or {})
    if report.get("reason") and not reels:
        return (f"── Record-vs-reel diff ──\n  unavailable: "
                f"{report['reason']}")
    changed = list((report or {}).get("changed") or ())
    unchanged = list((report or {}).get("unchanged") or ())
    undetermined_reels = list((report or {}).get(
        "undetermined_reels") or ())
    totals = (report or {}).get("totals") or {}
    lines = [
        f"── Record-vs-reel diff ({len(reels)} reel(s)): "
        f"{len(changed)} changed, {len(unchanged)} unchanged, "
        f"{len(undetermined_reels)} undetermined ──"]
    if changed:
        parts = []
        for reel in changed:
            counts = reels[reel].get("counts", {})
            parts.append(f"{reel} ({counts.get('conflicts', 0)})")
        lines.append(f"  changed: {', '.join(parts)}")
    if unchanged:
        lines.append(f"  unchanged: {', '.join(unchanged)}")
    for reel in undetermined_reels:
        lines.append(f"  undetermined: {reel} - "
                     f"{reels[reel].get('reason', '')}")
    for reel in changed:
        per = reels[reel]
        counts = per.get("counts", {})
        lines.append(
            f"  {reel}: {counts.get('conflicts', 0)} conflict(s), "
            f"{counts.get('agreements', 0)} agreement(s)"
            + (f", {counts.get('undetermined', 0)} undetermined"
               if counts.get("undetermined") else ""))
        conflicts = list(per.get("conflicts") or ())
        for row in conflicts[:max(detail_limit, 0)]:
            lines.append(f"    CONFLICT {row.get('detail', '')}")
        if len(conflicts) > max(detail_limit, 0):
            lines.append(f"    ... and "
                         f"{len(conflicts) - max(detail_limit, 0)} more - "
                         f"the full rows are in the report, not the "
                         f"summary")
        for row in per.get("undetermined") or ():
            lines.append(f"    UNDETERMINED {row.get('detail', '')}")
    if totals:
        lines.append(
            f"  totals: {totals.get('conflicts', 0)} conflict(s), "
            f"{totals.get('agreements', 0)} agreement(s), "
            f"{totals.get('undetermined', 0)} undetermined")
    return "\n".join(lines)


def report_record_diff(project_folder, reels=None, live_by_reel=None,
                       live_notes=None) -> dict:
    """Diff the set, PRINT it, return it. Never raises, never refuses.

    The build-time shape `report_divergence` takes for declared-vs-
    carried, applied to record-vs-reel: the captain's triage surface
    for "what did the last commit hold, and what does the reel hold
    now". A probe that could fail a caller would be a diff declining
    to answer, which is what this must never be.
    """
    try:
        report = diff_records(project_folder, reels,
                              live_by_reel=live_by_reel,
                              live_notes=live_notes)
    except Exception as failed:                       # noqa: BLE001
        print(f"  record-vs-reel diff unavailable ({failed}) - "
              f"continuing without it", file=sys.stderr)
        return {"unavailable": str(failed)}
    print(render_record_diff(report), flush=True)
    return report


def _parse_live_doc_specs(specs) -> tuple:
    """`--live-doc REEL=PATH` into `(live_by_reel, live_notes)`.

    An unreadable file is a NOTE, never an exception: that reel diffs
    as undetermined with the reason, because a diff that refuses over
    one bad file is not a diff.
    """
    live_by_reel: Dict[str, Any] = {}
    live_notes: Dict[str, str] = {}
    for spec in specs or ():
        reel, separator, path = str(spec).partition("=")
        reel, path = reel.strip(), path.strip()
        if not separator or not reel or not path:
            live_notes[reel or spec] = (
                f"--live-doc {spec!r} is not REEL=PATH - no live "
                f"projection was read for this reel")
            continue
        try:
            live_by_reel[reel] = load_serialized(path)
        except Exception as unreadable:               # noqa: BLE001
            live_notes[reel] = (
                f"the live document at {path} cannot be read "
                f"({unreadable}) - not measured")
    return live_by_reel, live_notes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.reel_divergence",
        description="Which reels carry what the project declares, and "
                    "how each reel's live projection differs from its "
                    "last committed .timeline.json.")
    parser.add_argument("project", help="path to the project folder")
    parser.add_argument("--reel", action="append", default=None,
                        dest="reels",
                        help="limit to this reel; repeatable")
    parser.add_argument("--all-containers", action="store_true",
                        help="survey every serialized timeline, not just "
                             "the reels the plan approves")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    parser.add_argument("--claim", default="",
                        help="refuse unless this declaration reached "
                             "every surveyed reel; exits 1 when it did not")
    parser.add_argument("--record-diff", action="store_true",
                        help="diff each reel's live projection against its "
                             "last committed .timeline.json and print "
                             "conflicts/agreements/undetermined - never "
                             "refusing, over the whole set at once")
    parser.add_argument("--live-doc", action="append", default=None,
                        dest="live_docs",
                        help="REEL=PATH to a serializer-shaped live "
                             "projection for that reel (fixture, recorded "
                             "projection); repeatable. Without it the "
                             "reel's worktree snapshot is the live side")
    args = parser.parse_args(argv)

    if args.record_diff and args.claim:
        parser.error("--claim is a declared-vs-carried claim and does not "
                     "apply with --record-diff, which never refuses")
    if args.record_diff:
        wanted = args.reels
        if wanted is None and not args.all_containers:
            wanted = approved_reels(args.project) or None
        live_by_reel, live_notes = _parse_live_doc_specs(args.live_docs)
        if args.json:
            report = diff_records(args.project, wanted,
                                  live_by_reel=live_by_reel,
                                  live_notes=live_notes)
            print(json.dumps(report, indent=2))
        else:
            report_record_diff(args.project, wanted,
                               live_by_reel=live_by_reel,
                               live_notes=live_notes)
        return 0

    wanted = args.reels
    if wanted is None and not args.all_containers:
        wanted = approved_reels(args.project) or None
    snapshots, notes = snapshots_from_review(args.project, wanted)
    try:
        from library.tools.plan_provenance import read_provenance
        recorded = (read_provenance(os.path.join(
            args.project, "pipeline_output", "review")) or {}).get(
                "asset_hashes") or {}
    except Exception:                               # noqa: BLE001
        recorded = {}
    report = survey(args.project, snapshots, notes=notes,
                    endings=endings_for(args.project,
                                        sorted(set(snapshots) | set(notes))),
                    recorded_assets=recorded)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_survey(report))
    if args.claim:
        try:
            reached = assert_reaches(report, args.claim)
        except ClaimNotBackedByArtefacts as refused:
            print(f"\nCLAIM REFUSED: {refused}", file=sys.stderr)
            return 1
        print(f"\nCLAIM BACKED: {args.claim!r} is carried by all "
              f"{len(reached)} surveyed reel(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
