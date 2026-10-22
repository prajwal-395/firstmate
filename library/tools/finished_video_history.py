"""QA-010b: the four tracked numbers, read off a project's OWN records.

WHAT WAS MISSING
----------------
`library/tools/finished_video_benchmark.py` measures four numbers the
punch list names - first-pass acceptance, revision count, human
correction time and preserved work - each over its own denominator.
In the committed baseline all three of the last four had a
denominator of ZERO, and the benchmark said so: `not_measured`, never
a zero.

The reason was not that the numbers are unmeasurable.  It was that
nothing read them off the records the engine already keeps.  A run
leaves behind exactly the evidence these four numbers are made of:

- the VERSION LEDGER (`reel_versions.json`) - every build and touch the
  reel went through, with its rows and its rows digest;
- the CREATIVE COMMIT LEDGER (`creative_revisions.json`) - every
  recorded revision, with its author and its time;
- the UNIFIED EDIT HISTORY (`plan_provenance.json`'s
  `reel_edit_history`) - the captain's hand corrections, with their
  times;
- the DELIVERY ACCEPTANCE (`reel_acceptance`) - that the captain took
  a delivered reel.

So this module reads those records and DERIVES the history block the
benchmark scores.  Nothing here asks a person to type a number into a
JSON file: a corpus entry says which project and which reel the video
came from, and the numbers come from what that project recorded.

THE RULES, AND WHY EACH IS WRITTEN THE WAY IT IS
-------------------------------------------------
- **An unmeasured number is `None`, never `0`.**  A reel nobody
  corrected did not spend zero seconds being corrected; nothing was
  measured, and the benchmark counts an unmeasured number on neither
  side of its denominator.
- **First-pass acceptance is `True`, `False` or `None`.**  `True` when
  the project records an acceptance and no correction after it; `False`
  when a correction is recorded (a reel that was taken and THEN changed
  was not accepted first-pass, which is the rule `edit_outcome` already
  follows); `None` when nothing recorded decides it.  `None` is NOT
  `False`: a reel nobody looked at is not a reel that was refused.
- **The attempt count is the number of recorded ACTS on the reel** -
  the version ledger's builds and touches, or the creative commit
  ledger's revisions where the version ledger holds none.  Each act is
  one attempt at the edit, so `attempts - 1` is the number of
  revisions, which is the reading the benchmark already uses.
- **The correction time is a WINDOW, and it says so.**  It is the
  elapsed time between the last recorded Ren act before the captain's
  correction and the correction itself.  That window also holds the
  time the captain watched the reel, so it is an UPPER BOUND on the
  time spent correcting, and the basis travels with the number.
- **Preserved work is a set of named yes/no checks**, read off the
  version ledger's own rows: for each row, did the row survive the last
  recorded revision unchanged, and did any item the build placed go
  missing.  A reel with no recorded rows has no preserved work to
  report, and says so instead of reporting an empty success.

WHAT THIS IS NOT
----------------
It does not judge, does not touch Resolve, and does not decide what
"accepted" means: it reads what was recorded.  A record that cannot be
read is a refusal, not an empty ledger - an unreadable ledger would
make every number on this reel read as `None`, and `None` reads as
"nobody looked", which is a different claim from "the record is
broken".
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

#: The four numbers the finished-video benchmark tracks, in the order
#: the punch list names them.
TRACKED_NUMBERS = (
    "first_attempt_accepted",
    "attempts",
    "human_correction_seconds",
    "preserved_work",
)


class HistoryError(RuntimeError):
    """A project's records could not be read as this module needs."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _act_time(act: Mapping[str, Any]) -> Any:
    """When a recorded act happened.

    The two ledgers name it differently - the version ledger says
    `at`, the creative commit ledger says `created_at` - and both are
    read here, so an act's time is never lost to a key name.
    """
    for key in ("at", "created_at", "updated_at"):
        if act.get(key):
            return act.get(key)
    return None


def _parse_time(value: Any) -> datetime | None:
    """An ISO timestamp, or None when the record carries none."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _elapsed_seconds(later: Any, earlier: Any) -> float | None:
    """The seconds between two timestamps, or None when one is absent."""
    start = _parse_time(earlier)
    end = _parse_time(later)
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds >= 0 else None


# ── the records ──────────────────────────────────────────────────────────

def _versions(project_folder: str, reel: str) -> list[dict[str, Any]]:
    """The reel's recorded versions, oldest first."""
    from library.tools.versions import reel_versions

    try:
        rows = reel_versions.versions_of(project_folder, reel)
    except Exception as unreadable:
        raise HistoryError(
            f"the version ledger of {project_folder} could not be read "
            f"({unreadable}). A reel whose ledger cannot be read has no "
            "measured history: reporting an empty one would claim that "
            "nobody did anything, which is not what an unreadable "
            "record says.") from unreadable
    return [dict(row) for row in rows or ()]


def _acts(project_folder: str, reel: str) -> tuple[list[dict[str, Any]],
                                                  str]:
    """The recorded acts on the reel, and where they came from.

    The VERSION LEDGER is the primary record: it carries every build
    and every touch with its rows.  Where it holds none, the CREATIVE
    COMMIT LEDGER is the record of the same acts, and the source says
    so - a reader must be able to tell which ledger the number came
    from.
    """
    from library.tools.versions import reel_versions

    versions = _versions(project_folder, reel)
    acts = [row for row in versions
            if str(row.get("kind") or "") in reel_versions.ACTS]
    if acts:
        return acts, "the version ledger (reel_versions.json)"
    try:
        from library.tools import creative_revision

        revisions = list(creative_revision.list_revisions(project_folder))
    except Exception as unreadable:
        raise HistoryError(
            f"the creative commit ledger of {project_folder} could not be "
            f"read ({unreadable}).") from unreadable
    named = [row for row in revisions
             if _is_this_reel(row.get("affected_entities"), reel)]
    return named, "the creative commit ledger (creative_revisions.json)"


def _is_this_reel(entities: Any, reel: str) -> bool:
    """Whether a recorded act belongs to this reel.

    A rebuild stages its work on a timeline of the same name with
    ``(rebuild staging)`` appended, and those acts are THIS reel's
    acts: a build that was staged and promoted is one build, not two.
    """
    for entity in entities or ():
        name = str(entity or "")
        if name == reel or name.startswith(f"{reel} ("):
            return True
    return False


def _corrections(project_folder: str, reel: str) -> list[dict[str, Any]]:
    """The captain's recorded hand corrections on the reel."""
    from library.tools import edit_outcome, reel_edit_history

    try:
        entries = reel_edit_history.history_for(
            edit_outcome.review_dir(project_folder), reel)
    except Exception as unreadable:
        raise HistoryError(
            f"the edit history of {project_folder} could not be read "
            f"({unreadable}).") from unreadable
    return [dict(entry) for entry in entries or ()
            if str(entry.get("act") or "") == reel_edit_history.ACT_MANUAL_EDIT]


def _acceptance(project_folder: str, reel: str) -> dict[str, Any] | None:
    """The recorded acceptance of this reel, or None."""
    from library.tools import reel_acceptance

    try:
        found = reel_acceptance.acceptance_for(project_folder, reel)
    except Exception as unreadable:
        raise HistoryError(
            f"the delivery acceptances of {project_folder} could not be "
            f"read ({unreadable}).") from unreadable
    return dict(found) if found else None


# ── the four numbers ─────────────────────────────────────────────────────

def _first_pass_acceptance(acceptance: Mapping[str, Any] | None,
                           corrections: Sequence[Mapping[str, Any]]
                           ) -> tuple[bool | None, str]:
    """Was the FIRST attempt accepted without a revision?

    Three answers, never two: `True`, `False` and `None`.  A reel
    nobody looked at is `None`, because `None` is what the benchmark
    counts as "not measured" and `False` would count as a refusal.
    """
    if corrections:
        return False, (
            "a hand correction is recorded on this reel, so the first "
            "attempt was not accepted as it stood"
            + (" - the reel was accepted and then changed, which is "
               "still not a first-pass acceptance"
               if acceptance else ""))
    if acceptance:
        return True, (
            f"the delivery was accepted by "
            f"{acceptance.get('by') or 'the captain'} and no correction "
            "is recorded after it")
    return None, (
        "no acceptance and no hand correction are recorded, so nothing "
        "decided whether the first attempt was accepted")


def _attempt_count(acts: Sequence[Mapping[str, Any]]) -> int | None:
    """How many recorded acts the reel went through."""
    return len(acts) if acts else None


def _correction_seconds(acts: Sequence[Mapping[str, Any]],
                         corrections: Sequence[Mapping[str, Any]]
                         ) -> tuple[float | None, str]:
    """The window between the last Ren act and the captain's correction.

    An UPPER BOUND on the time spent correcting: the window also holds
    the time the captain watched the reel.  The basis travels with the
    number, because a number without its basis is a claim.
    """
    if not corrections:
        return None, (
            "no hand correction is recorded, so no correction time was "
            "measured - not zero seconds: nobody corrected this reel, "
            "and an absent measurement is never a zero")
    first = corrections[0]
    at = first.get("at")
    earlier = [act for act in acts
               if _parse_time(_act_time(act)) is not None
               and _parse_time(_act_time(act)) <= _parse_time(at)]
    if not earlier:
        return None, (
            "the correction is recorded but no Ren act before it carries "
            "a timestamp, so no window was measured")
    last = earlier[-1]
    seconds = _elapsed_seconds(at, _act_time(last))
    if seconds is None:
        return None, (
            "the correction and the act before it carry no usable "
            "timestamps, so no window was measured")
    return seconds, (
        "the elapsed window between the last recorded Ren act on this "
        "reel and the captain's hand correction; an UPPER BOUND on the "
        "time spent correcting, because the window also holds the time "
        "the reel was watched")


def _rows_of(act: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = act.get("rows")
    return rows if isinstance(rows, Mapping) else {}


def _items_of(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = row.get("items")
    return [dict(item) for item in items or () if isinstance(item, Mapping)]


def _item_identity(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (str(item.get("name") or ""), item.get("start"), item.get("end"),
            item.get("duration"))


def _preserved_work(acts: Sequence[Mapping[str, Any]]
                    ) -> tuple[dict[str, Any] | None, str]:
    """What survived the last recorded revision, as named checks.

    The checks come from the version ledger's OWN rows: a row is
    preserved when the row after the revision holds the same items, and
    an item the build placed is preserved when it is still placed.
    """
    with_rows = [act for act in acts if _rows_of(act)]
    if len(with_rows) < 2:
        return None, (
            "no two recorded acts carry rows to compare, so what survived "
            "the last revision was not measured")
    before = _rows_of(with_rows[-2])
    after = _rows_of(with_rows[-1])
    checks: dict[str, bool] = {}
    dropped = 0
    for name, row in before.items():
        earlier_items = _items_of(row if isinstance(row, Mapping) else {})
        later_row = after.get(name)
        later_items = _items_of(later_row if isinstance(later_row, Mapping)
                                 else {})
        later_ids = {_item_identity(item) for item in later_items}
        gone = sum(1 for item in earlier_items
                   if _item_identity(item) not in later_ids)
        dropped += gone
        checks[f"row:{name}"] = (gone == 0 and earlier_items == later_items)
    checks["no_item_was_dropped"] = dropped == 0
    return {"checks": checks}, (
        "read off the version ledger's own rows: for each row, did the "
        "row survive the last recorded revision with the same items, and "
        "did any item the build placed go missing")


# ── the derived history ──────────────────────────────────────────────────

def derive_history(project_folder: str, reel: str) -> dict[str, Any]:
    """One reel's tracked numbers, derived from its project's records.

    The returned block is the shape `finished_video_benchmark` scores:
    the four numbers, each `None` when nothing measured it, plus the
    source and the basis of every number that WAS measured.
    """
    project_folder = os.path.abspath(str(project_folder))
    reel = str(reel)
    acts, acts_source = _acts(project_folder, reel)
    corrections = _corrections(project_folder, reel)
    acceptance = _acceptance(project_folder, reel)

    accepted, accepted_basis = _first_pass_acceptance(acceptance,
                                                       corrections)
    attempts = _attempt_count(acts)
    seconds, seconds_basis = _correction_seconds(acts, corrections)
    preserved, preserved_basis = _preserved_work(acts)

    unmeasured: list[str] = []
    if accepted is None:
        unmeasured.append("first_pass_acceptance")
    if attempts is None:
        unmeasured.append("attempts")
    if seconds is None:
        unmeasured.append("human_correction_seconds")
    if preserved is None:
        unmeasured.append("preserved_work")

    return {
        "first_attempt_accepted": accepted,
        "attempts": attempts,
        "human_correction_seconds": seconds,
        "preserved_work": preserved,
        "sources": {
            "attempts": acts_source,
            "first_attempt_accepted": (
                "the reel's delivery acceptance and its recorded hand "
                "corrections"),
            "human_correction_seconds": (
                "the timestamps of the reel's recorded acts and of the "
                "captain's hand correction"),
            "preserved_work": "the version ledger's recorded rows",
            "project": project_folder,
            "reel": reel,
        },
        "basis": {
            "first_attempt_accepted": accepted_basis,
            "human_correction_seconds": seconds_basis,
            "preserved_work": preserved_basis,
            "attempts": (
                "the number of recorded acts on this reel - a build or a "
                "touch - each one an attempt at the edit"),
        },
        "unmeasured": unmeasured,
        "derived_at": _now(),
    }


def history_for_corpus(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The history a corpus entry declares, derived or inline.

    An entry carries EITHER an inline `history` (what somebody recorded
    by hand, with its sources) OR a `history_from` (the project and the
    reel the video came from, from which the numbers are derived).  An
    entry that carries both is ambiguous and refuses: two accounts of
    the same run would be read as one.
    """
    inline = entry.get("history")
    declared = entry.get("history_from")
    if inline is not None and declared is not None:
        raise HistoryError(
            "the corpus entry carries both an inline history and a "
            "history_from: a run has one account of what happened to it")
    if declared is None:
        return dict(inline or {})
    if not isinstance(declared, Mapping):
        raise HistoryError(
            "the corpus entry's history_from is not an object naming the "
            "project and the reel")
    project = str(declared.get("project") or "").strip()
    reel = str(declared.get("reel") or "").strip()
    if not project or not reel:
        raise HistoryError(
            "the corpus entry's history_from names no project or no reel")
    return derive_history(project, reel)
