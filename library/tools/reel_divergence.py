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

``tests/test_reel_divergence.py``.
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
    return _present(
        f"picture track carries "
        f"{', '.join(os.path.basename(a) for a in assets)}",
        "reel_divergence._detect_full_frame_elements")


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

def declarations(project_folder, brand_effect=None,
                 endings: Optional[Mapping[str, Any]] = None) -> List[dict]:
    """The declarations in force, with the parameters each detector needs.

    A project declaring nothing yields `[]`, which is every project
    unless someone opted in - the same shape `full_frame_element`,
    `content.bookends` and `effect.timed_text_overlay` take.

    `endings` maps reel name to the ending `reel_ending.resolve_ending`
    gave it. Omitting it does not silently drop the ending row: the
    declaration is still listed, with no reel expecting a freeze, so
    every reel reads UNDETERMINED and says the ending was never
    resolved. A row that vanished would read as agreement.
    """
    from library.tools import full_frame_element as ffe

    out: List[dict] = []
    effect = ffe.resolve_declaration(brand_effect, str(project_folder))
    declared = ffe.declared_elements(effect)
    if declared:
        assets = [d.get("asset") for d in declared if d.get("asset")]
        named = ", ".join(os.path.basename(str(a)) for a in assets)
        out.append({
            "key": KEY_FULL_FRAME,
            "declared_by": "effect.full_frame_elements",
            "params": {"assets": assets},
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
           notes: Optional[Mapping[str, str]] = None) -> dict:
    """Read every reel against every declaration. Never raises.

    `snapshots` maps reel name to its timeline - a live snapshot or a
    serialized document. A reel mapped to None, or named only in
    `notes`, is UNDETERMINED for every declaration with that note as
    the reason: an unread reel is not a reel without the card.

    Returns `{"declarations": [...], "reels": {reel: {key: {...}}},
    "divergent": {key: [reels]}, "undetermined": {key: [reels]}}`.
    """
    assert_registry_is_well_formed()
    notes = dict(notes or {})
    active = declarations(project_folder, brand_effect, endings)
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
                      notes=None) -> dict:
    """Survey, PRINT, return. Never raises, never refuses.

    Called by the build over EVERY approved reel - not just the ones it
    is about to place - so the reels a build leaves behind are named at
    the moment someone is already looking. A probe that could fail the
    build would be a warning holding a build hostage, and whether to
    rebuild a stale reel is the captain's decision.
    """
    try:
        report = survey(project_folder, snapshots, brand_effect=brand_effect,
                        endings=endings, notes=notes)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.reel_divergence",
        description="Which reels carry what the project declares.")
    parser.add_argument("project", help="path to the project folder")
    parser.add_argument("--reel", action="append", default=None,
                        dest="reels",
                        help="limit to this reel; repeatable")
    parser.add_argument("--all-containers", action="store_true",
                        help="survey every serialized timeline, not just "
                             "the reels the plan approves")
    parser.add_argument("--json", action="store_true",
                        help="print the survey as JSON")
    parser.add_argument("--claim", default="",
                        help="refuse unless this declaration reached "
                             "every surveyed reel; exits 1 when it did not")
    args = parser.parse_args(argv)

    wanted = args.reels
    if wanted is None and not args.all_containers:
        wanted = approved_reels(args.project) or None
    snapshots, notes = snapshots_from_review(args.project, wanted)
    report = survey(args.project, snapshots, notes=notes,
                    endings=endings_for(args.project,
                                        sorted(set(snapshots) | set(notes))))
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
