"""One CTA animation across the reel fleet, declared once, applied at the source.

The captain, 2026-09-20, on Reel 09's closing card (a blue clip marker on
the card itself)::

    "can we use this animation in all of the CTA's on all the reels
     where the Lucie Visibility system is being referred to"

Measured the same day: the closing overlay on the 30 final reels exists
in SIX present variants plus an absent slot - `title_lockup`/`quote_card`/
`stat_callout`/`list_build` elements, `fade`/`slide`/`scale`/`cut`
entrances, `centre` against `top_centre` anchors, a gold card against
amber, grey and pink ones. Reel 09's own card is a `title_lockup`
entering and leaving on `fade`, anchored `centre`, `#FBF0B8`.

The variants exist because every reel's closer graphic is model-planned
per reel (`reel_semantic_visual`): there is no template to flip, so the
unification is a recorded DECLARATION plus a deterministic normalization
in the reel overlay path - not a per-reel patch, and not a prompt hint
the next plan can drift off.

Two deliberate readings, both measured, not guessed:

- "The CTA" is the reel's CLOSING CARD, positionally: the latest-anchored
  entry among the card elements. The rendered closers prove the other
  reading wrong - all but Reel 09's sit on punchline words ("One is about
  position", "keyword stuffing", "I chose the first one I saw"), not on
  the CTA's own speech, so matching by CTA-word overlap would treat
  one reel out of thirteen.
- The captain's scope ("where the system is being referred to") is which
  REELS, not which entries: a reel qualifies when its own call to action
  names the scope phrase the declaration carries. Copy is never touched,
  so each reel keeps its own words under the unified treatment.

What normalization does and does not touch:

- element, entrance, exit, anchor and colour become the declared ones.
  The colour travels as the entry's stated `color` (any `colour_role`
  is removed so a brand palette cannot refine it back) - the captain
  named this card, not a role of a palette.
- copy, `hold_seconds`, `row`, `footprint`, `emphasis` and `why` are the
  plan's own and stay. The words keep their timing, so a re-rendered
  card has the same duration as the file it replaces and a swap cannot
  move a reel's length. A two-run closer stays two runs: rows stack.
- only `anchor_phrase` entries resolve to a position; an entry timed by
  declared seconds (a `beat_accent` on a cut) names no words and is
  never attributed by time.
- only the card elements (`CLOSING_CARD_ELEMENTS`) compete: emblems
  label subjects mid-reel, accents punctuate cuts, lower thirds name
  speakers - none has ever closed a reel in this fleet, and this rule
  does not promote one. An entry outside the set keeps its treatment
  and is reported, never silently converted.
- a reel with no call to action, or one whose CTA does not name the
  scope phrase, is out of scope and passes through with one line said.
- a reel named in `exclude_reels` is skipped outright, even when it
  otherwise qualifies. Exclusions match by PREFIX so a staging rebuild
  (`... (rebuild staging)`) is covered by the same entry as its final.
  Reel 08 is the reason this exists: it has no closing card, and
  unifying its early card would restyle a non-closer to satisfy a
  consistency target (firstmate 2026-09-20).

One documented limit: an in-scope reel whose plan puts NO card at its
close (Reel 08 today - its latest card illustrates an early anecdote)
has its latest card unified anyway, because "latest card" is all the
plan reveals. The report names the treated entry's anchor phrase, so a
reader sees it is not a closer. Such a reel wants a new closing card,
which is a model re-plan, not a normalization - this rule does not
invent one.

No declaration on file is a no-op: the answer passes through untouched
and nothing is printed. A declaration that exists and cannot be read
RAISES (`external_inputs` precedent: checked, never asserted).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from library.tools import motion_graphics_plan as _plan
from library.tools import motion_graphics_vocabulary as vocabulary
from library.tools import reel_ending
from library.tools import semantic_visual

#: Schema version this reader honours.
TREATMENT_VERSION = 1

#: The file basename, under the project's external-inputs area.
TREATMENT_FILENAME = "reel_cta.json"

#: The elements a reel's closing card is drawn from. Every closer on
#: every final timeline is one of these four; emblems, accents, lower
#: thirds and chrome serve other functions mid-reel and keep theirs.
#: A future closer in another element is REPORTED, not converted -
#: promoting an emblem to a title would destroy the thing it was.
CLOSING_CARD_ELEMENTS = frozenset({
    "title_lockup", "quote_card", "stat_callout", "list_build",
})


class ReelCtaTreatmentError(ValueError):
    """The declared CTA treatment cannot be honoured as written."""


_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def _tokens(text: str) -> List[str]:
    return [t for t in (semantic_visual.normalize_word(p)
                        for p in str(text or "").split()) if t]


def validate_treatment(value: Any) -> dict:
    """Structural check. Raises `ReelCtaTreatmentError` naming what is wrong."""
    if not isinstance(value, dict):
        raise ReelCtaTreatmentError(
            "reel_cta must be an object with version, scope_cta_contains, "
            "treatment and reason.")
    version = value.get("version")
    if version != TREATMENT_VERSION:
        raise ReelCtaTreatmentError(
            f"reel_cta declares version {version!r}; this reader honours "
            f"{TREATMENT_VERSION}. A schema nobody wrote a reader for is "
            f"refused rather than read optimistically.")
    scope = _tokens(str(value.get("scope_cta_contains") or ""))
    if not scope:
        raise ReelCtaTreatmentError(
            "reel_cta names no scope_cta_contains words. The captain "
            "scoped this treatment to the CTAs that refer to the system; "
            "a treatment without its scope would restyle every closer in "
            "the project.")
    treatment = value.get("treatment")
    if not isinstance(treatment, dict):
        raise ReelCtaTreatmentError(
            "reel_cta names no treatment object. A treatment says what "
            "every in-scope CTA card draws as: element, entrance, exit, "
            "anchor and color.")
    element = treatment.get("element")
    key = vocabulary.canonical_key(
        str(element) if isinstance(element, str) else "")
    if not key:
        raise ReelCtaTreatmentError(
            f"reel_cta treatment names element {element!r}: it is not in "
            f"the motion-graphics vocabulary. A treatment naming an "
            f"element nothing renders is refused, never drawn as a "
            f"neighbour.")
    if key not in _plan.DRAWABLE:
        raise ReelCtaTreatmentError(
            f"reel_cta treatment names element {key!r}: the roster "
            f"records it as unreachable. A treatment the renderer cannot "
            f"draw is a promise no build can keep.")
    if key not in CLOSING_CARD_ELEMENTS:
        raise ReelCtaTreatmentError(
            f"reel_cta treatment names element {key!r}: outside "
            f"{sorted(CLOSING_CARD_ELEMENTS)}. The treatment unifies "
            f"closing cards; naming a non-card element would convert "
            f"every closer into something no closer has ever been.")
    for side in ("entrance", "exit"):
        motion = str(treatment.get(side) or "").lower()
        if motion not in _plan.MOTION_CHARACTERS:
            raise ReelCtaTreatmentError(
                f"reel_cta treatment names {side} {treatment.get(side)!r}: "
                f"one of {list(_plan.MOTION_CHARACTERS)}. An entrance the "
                f"vocabulary never gave an axis is not an animation.")
    anchor = str(treatment.get("anchor") or "").lower()
    if anchor not in _plan.ANCHORS:
        raise ReelCtaTreatmentError(
            f"reel_cta treatment names anchor {treatment.get('anchor')!r}: "
            f"one of {list(_plan.ANCHORS)}.")
    color = treatment.get("color")
    if not isinstance(color, str) or not _HEX_COLOR.match(color.strip()):
        raise ReelCtaTreatmentError(
            f"reel_cta treatment names color {color!r}: a #RRGGBB hex. "
            f"A colour nobody can reproduce is not a treatment.")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ReelCtaTreatmentError(
            "reel_cta records no reason. A treatment outlives the marker "
            "that produced it; one nobody can date or attribute is one "
            "nobody dares remove.")
    excluded = value.get("exclude_reels", [])
    if not isinstance(excluded, list) or any(
            not isinstance(name, str) or not name.strip()
            for name in excluded):
        raise ReelCtaTreatmentError(
            f"reel_cta names exclude_reels {value.get('exclude_reels')!r}: "
            f"a list of non-empty reel names. An exclusion nobody can "
            f"match would silently restyle the reel it meant to spare.")
    return {
        "version": TREATMENT_VERSION,
        "scope_cta_contains": scope,
        "exclude_reels": [name.strip() for name in excluded],
        "treatment": {
            "element": key,
            "entrance": str(treatment.get("entrance")).lower(),
            "exit": str(treatment.get("exit")).lower(),
            "anchor": anchor,
            "color": color.strip(),
        },
        "reason": reason.strip(),
    }


def treatment_path(project_folder: str) -> str:
    """Where a project's declared CTA treatment lives."""
    from library.tools.external_inputs import declaration_path

    return str(declaration_path(project_folder or "", TREATMENT_FILENAME))


def load_treatment(project_folder: str) -> Optional[dict]:
    """The declared treatment, or None where the project declares none.

    A file that exists and cannot be read RAISES: a declaration the
    build cannot parse must refuse, never build silently past it.
    """
    if not project_folder:
        return None
    path = treatment_path(project_folder)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise ReelCtaTreatmentError(
            f"{path} cannot be read ({unreadable}). A declared treatment "
            f"the build cannot parse is refused, never ignored.") \
        from unreadable
    return validate_treatment(document)


def check_treatment(project_folder: str) -> list:
    """The declared treatment as a checkable list, for `external_inputs`.

    `check_declaration` reports `len(value)` entries, and the treatment
    is one ruling rather than N keys - so it travels as a one-item
    list, `[]` where the project declares none. A declaration the
    check cannot parse refuses through `load_treatment`, unchanged.
    """
    treatment = load_treatment(project_folder)
    return [] if treatment is None else [treatment]


def _contains_run(haystack: List[str], needle: List[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[i:i + len(needle)] == needle
               for i in range(len(haystack) - len(needle) + 1))


def is_excluded(moment: Any, excluded: Sequence[str]) -> Optional[str]:
    """The exclusion entry covering this reel, or None.

    Matched by PREFIX so a staging container resolves to the same
    exclusion its promoted name does - the `reel_ending.declared_ending`
    precedent, for the same reason: one declaration, every build of
    that reel.
    """
    name = str(getattr(moment, "timeline_name", "") or "")
    for entry in excluded or ():
        if name and (name == entry or name.startswith(entry)):
            return entry
    return None


def in_scope(moment: Any, scope: Sequence[str]) -> bool:
    """Whether the moment's own CTA names the declaration's scope phrase."""
    cta = reel_ending.call_to_action_of(moment)
    if cta is None:
        return False
    return _contains_run(_tokens(str(cta.get("text") or "")), list(scope))


def _entry_start(entry: dict,
                 windows: Sequence[Dict[str, Any]]) -> Optional[float]:
    """An answer entry's anchored start, or None when it names none.

    Only `anchor_phrase` entries resolve here: an entry timed by
    declared seconds names no words, and a beat accent on a cut is not
    CTA copy whatever frame it sits on. `resolve_plan` drops what does
    not resolve with its own reasons; this reader only positions.
    """
    if not isinstance(entry, dict):
        return None
    if (entry.get("start_seconds") is not None
            or entry.get("duration_seconds") is not None):
        return None
    phrase = str(entry.get(semantic_visual.ANCHOR_PHRASE_KEY) or "").strip()
    if not phrase:
        return None
    try:
        start, _ = semantic_visual.find_phrase_window(windows, phrase)
        return float(start)
    except semantic_visual.SemanticVisualError:
        return None


def closing_card_indexes(answer: Sequence[Any],
                         windows: Sequence[Dict[str, Any]]) -> List[int]:
    """Answer indexes competing for the closing card, latest anchored wins.

    Among the card elements, the entry anchored latest in the reel's own
    word order is the closing card - the thing the captain points at when
    he points at the end of a reel. A tie (two entries on the same words)
    treats both: rows stack rather than collide.
    """
    positioned: List[Tuple[float, int]] = []
    for index, entry in enumerate(answer or ()):
        if not isinstance(entry, dict):
            continue
        key = vocabulary.canonical_key(
            str(entry.get("element") or ""))
        if key not in CLOSING_CARD_ELEMENTS:
            continue
        start = _entry_start(entry, windows or [])
        if start is None:
            continue
        positioned.append((start, index))
    if not positioned:
        return []
    latest = max(start for start, _ in positioned)
    return sorted(index for start, index in positioned if start == latest)


def apply(answer: Optional[list], moment: Any,
          windows: Sequence[Dict[str, Any]],
          project_folder: str) -> Tuple[Optional[list], dict]:
    """Normalise the closing card of one reel's answer to the declaration.

    Returns `(answer, report)` where report is
    `{"applied": [...], "stale": [...], "treatment": {...} | None}`.
    The answer list is normalised IN PLACE and returned; every other
    entry is byte-identical. No declaration, no call to action, an
    out-of-scope CTA, or no positionable card entry passes the answer
    through - and says which absence it was.
    """
    empty: Dict[str, Any] = {"applied": [], "stale": [],
                             "treatment": None}
    if not isinstance(answer, list) or not answer:
        return answer, empty
    treatment = load_treatment(project_folder)
    if treatment is None:
        return answer, empty
    hit = is_excluded(moment, treatment.get("exclude_reels") or [])
    if hit is not None:
        empty["stale"].append({
            "kind": "cta_treatment",
            "reel": getattr(moment, "timeline_name",
                            getattr(moment, "number", "")),
            "reason": (f"explicitly excluded by the declaration ({hit!r})"),
        })
        return answer, empty
    cta = reel_ending.call_to_action_of(moment)
    if cta is None:
        empty["stale"].append({
            "kind": "cta_treatment",
            "reel": getattr(moment, "timeline_name",
                            getattr(moment, "number", "")),
            "reason": "the reel declares no call to action",
        })
        return answer, empty
    if not in_scope(moment, treatment["scope_cta_contains"]):
        empty["stale"].append({
            "kind": "cta_treatment",
            "reel": getattr(moment, "timeline_name",
                            getattr(moment, "number", "")),
            "reason": ("the call to action does not name "
                       f"{' '.join(treatment['scope_cta_contains'])!r} - "
                       "out of the declared scope"),
        })
        return answer, empty
    indexes = closing_card_indexes(answer, windows or [])
    if not indexes:
        empty["stale"].append({
            "kind": "cta_treatment",
            "reel": getattr(moment, "timeline_name",
                            getattr(moment, "number", "")),
            "reason": ("no card entry resolves to a position in the "
                       "reel's word timings"),
        })
        return answer, empty
    applied: List[dict] = []
    slot = treatment["treatment"]
    for index in indexes:
        entry = answer[index]
        was = {key: entry.get(key) for key in
               ("element", "entrance", "exit", "anchor", "color",
                "colour_role")}
        entry["element"] = slot["element"]
        entry["entrance"] = slot["entrance"]
        entry["exit"] = slot["exit"]
        entry["anchor"] = slot["anchor"]
        entry["color"] = slot["color"]
        entry.pop("colour_role", None)
        entry.pop("colour", None)
        applied.append({
            "index": index,
            "anchor_phrase": str(entry.get(
                semantic_visual.ANCHOR_PHRASE_KEY) or ""),
            "was": was,
        })
    return answer, {"applied": applied, "stale": [],
                    "treatment": slot}


def report(reel_name: str, result: dict) -> None:
    """Print one treatment verdict the way the pin owners print theirs."""
    import sys
    slot = result.get("treatment") or {}
    becomes = (f"{slot.get('element')}/{slot.get('entrance')}/"
               f"{slot.get('exit')}@{slot.get('anchor')}")
    for row in result.get("applied", ()):
        was = row.get("was") or {}
        print(f"  CTA treatment: {reel_name} entry {row['index']} "
              f"{row.get('anchor_phrase', '')!r} "
              f"{was.get('element')}/{was.get('entrance')}/"
              f"{was.get('exit')}@{was.get('anchor')} -> "
              f"{becomes} (declared)",
              flush=True)
    for row in result.get("stale", ()):
        print(f"  CTA treatment: {reel_name} untreated - "
              f"{row.get('reason', '')}", file=sys.stderr, flush=True)
