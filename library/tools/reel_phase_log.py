"""Per-reel phase log: when each reel was asked, answered, built, verified, consolidated.

2026-09-18 the captain asked why reel builds take as long as they do.
The investigation (`firstmate` lane `vep-where-the-reel-wall-clock-goes`)
apportioned batch 5 and hit a wall on the largest item: reel M05 waited
85 minutes between its plan answers arriving (10:06:58) and its build
landing (11:32:08), with a 64-minute window, 10:17 to 11:21, with zero
writes to the project from any lane. The cause could not be recovered,
because nothing written to disk distinguishes deep verification reading
(pure reads leave no trace), Resolve contention from the two other lanes
running that day, or a worker stalled on throttled model turns.

This module is the instrument that makes the next such question
answerable. The LANE writes one line per phase transition, per reel, at
the moment it happens:

- `plan_asked`: the engine wrote a plan request for the reel (one line
  per channel: semantic, span, motion).
- `answers_arrived`: the engine read the model's answers for the reel,
  naming each channel's basis (`planned`, `awaiting_model_answer`, ...).
- `build_started` / `build_finished`: the Resolve placement began and
  ended. `build_started` carries seconds since `answers_arrived`, so an
  M05-class stall names itself on the line.
- `verified`: the conformance gate graded the reel's staging and passed.
- `consolidated`: promotion moved the staging onto the final name.
- `wait`: a one-line reason for any wait, written by the thing doing the
  waiting - no model answer on file, a reel left alone, a gate refusal,
  a build failure. A reel with `plan_asked` but no `build_finished`
  must have a `wait` line saying why, or the silence is back.

THE TRAP THIS MUST NOT WALK BACK INTO: the request files under
`pipeline_output/llm_requests` are REWRITTEN on every build
(`reel_semantic_visual.write_request`), so their mtime is the last
rewrite and not the ask. Every event here carries its OWN timestamp
taken at the moment the phase happens (`_utcnow`), and nothing is ever
inferred from a file time later. A phase whose timing cannot be captured
honestly is absent rather than estimated.

CHEAP ON PURPOSE: append-only JSON lines, one helper, call sites at the
places that already know. The file is JSONL rather than JSON so
concurrent lanes append without a read-modify-write race. Filing never
fails a build: a write that cannot land is said on stderr and the build
continues, because an instrument that refuses correct output is worse
than a missing line (AGENTS.md 10.4) - and a missing line is visible as
a gap, while a fabricated timestamp would be trusted.

THE BUILD SUMMARY: one structured line per reel per build (`build_summary`
phase, filed by the build, read back through `summarize()`), carrying
what the build computed so no lane greps the build log again. Its
content is DERIVED from what lanes actually grepped for: batch 5 spent
36.7 minutes over 65 calls re-reading its own build logs (measured from
the lane's recorded tool calls in `opencode.db`, session
`ses_f4b92eb63ffeHewCrUHLvdWEuQ`, not from file spans), in categories:
answer/drop state per channel (`awaiting`, `NO SEMANTIC`, `NO SPAN`,
`NO PICTURE`), the place/leave-alone decision, captain trims applied,
the draw-gain probe's measured-vs-fallback value, caption counts,
cards placed, the conformance verdict and finding classes (`: ok (`,
`FINDINGS`, `F[0-9]+`), promotion/retirement, the version-control
commit, and the build-end drift bracket (`drift build end: Reel NN`).
A field the build cannot capture honestly is None rather than
estimated: standalone `drift` runs are a different producer and only
the build-end bracket for reels this build touched lands here, and
no waiver fact is filed: the engine grants no waivers - the gate's
F5/F25 baseline comparison reports pre-existing findings as warnings,
and nothing else softens an error. `assemble_summary` builds the payload from pieces the caller
already holds; `file_build_summary` files it under the same never-fail
contract as every other line.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional

FORMAT = "reel_phase_log/1"
FILENAME = "reel_phase_log.jsonl"

PLAN_ASKED = "plan_asked"
ANSWERS_ARRIVED = "answers_arrived"
BUILD_STARTED = "build_started"
BUILD_FINISHED = "build_finished"
VERIFIED = "verified"
CONSOLIDATED = "consolidated"
WAIT = "wait"
BUILD_SUMMARY = "build_summary"

PHASES = (PLAN_ASKED, ANSWERS_ARRIVED, BUILD_STARTED, BUILD_FINISHED,
          VERIFIED, CONSOLIDATED, WAIT, BUILD_SUMMARY)
"""Every phase a line may carry. Unknown phases are refused, because a
line whose phase nothing reads is another silence."""


CONFORMANCE_REPORT_FILENAME = "conformance_report.json"
CONFORMANCE_REPORT_REL = ("pipeline_output/review/"
                          + CONFORMANCE_REPORT_FILENAME)
"""Where the conformance gate files its per-reel verdicts. The summary
carries the small per-reel slice and names this path for the full
finding rows, so the lane reads JSON instead of grepping the log."""

MAX_DROPS_PER_CHANNEL = 20
"""Drops are usually few; the cap bounds one pathological line."""


def _utcnow() -> str:
    """This instant, UTC, ISO-8601. The single place timestamps are taken."""
    return datetime.datetime.now(
        datetime.timezone.utc).isoformat()


def _log_path(project_folder: str) -> str:
    from library.tools.project_layout import Area, ProjectLayout

    return os.path.join(
        str(ProjectLayout(project_folder).write_dir(Area.REVIEW)),
        FILENAME)


def _jsonable(value: Any) -> Any:
    """This value through a JSON round-trip, or its repr when exotic.

    The build passes plain JSON types; this is belt and braces so one
    unserializable field can never fail a filing (and with it a build).
    """
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return {"unserializable": repr(value)[:500]}


def log_event(project_folder: str, reel_number: int, reel_name: str,
              phase: str, detail: str = "",
              summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write one phase line for one reel, and return it.

    The timestamp is taken HERE, at the moment the caller transitions -
    never passed in, never read off a file. Callers wrap this in
    try/except-free code on purpose: a filing failure is said on stderr
    and the event (with `"unfiled": True`) is still returned, so the
    build never fails over its own instrument.

    `summary` carries the structured build-summary payload, on
    `build_summary` lines only - a phase line's `detail` stays the
    one-line human reason a lane skims, while the machine facts ride
    beside it.
    """
    if phase not in PHASES:
        raise ValueError(
            f"{phase!r} is not a reel phase this log may carry: "
            f"{list(PHASES)}. A line whose phase nothing reads is "
            f"another silence.")
    event: Dict[str, Any] = {
        "format": FORMAT,
        "reel_number": int(reel_number),
        "reel": str(reel_name or ""),
        "phase": phase,
        "at": _utcnow(),
        "detail": str(detail or ""),
    }
    if summary is not None:
        if not isinstance(summary, dict):
            raise TypeError(
                f"a build summary must be a dict, not "
                f"{type(summary).__name__} - a scalar summary is a "
                f"detail line wearing a payload's clothes.")
        event["summary"] = _jsonable(summary)
    if not project_folder:
        event["unfiled"] = True
        print("  reel phase log: no project folder, "
              f"line for reel {reel_number} phase {phase} not filed",
              file=sys.stderr)
        return event
    try:
        path = _log_path(project_folder)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
    except (OSError, ValueError) as exc:
        event["unfiled"] = True
        print(f"  reel phase log unavailable ({exc}) - reel {reel_number} "
              f"phase {phase} said here and not filed",
              file=sys.stderr)
    return event


def log_wait(project_folder: str, reel_number: int, reel_name: str,
             reason: str) -> Dict[str, Any]:
    """One line saying why this reel is not moving, by the waiter.

    `reason` is one line naming what is waited on or what decided
    against moving: "no model answer on file (...), building without
    visuals", "LEAVING ALONE: ...", "verification refused: ...".
    """
    return log_event(project_folder, reel_number, reel_name, WAIT,
                     detail=reason)


LEASE_WAIT_KIND = "lease_wait"
LEASE_WAIT_PREFIX = "lease wait "
"""How a Resolve lease acquisition files itself: one `wait` line per
acquisition, ALWAYS including the uncontended ones. A zero-wait
acquisition files `0.0s ... (uncontended)` - an absent record and a
zero record must not look the same, or nobody can compute what
fraction of acquisitions contended. The human reason rides in
`detail`; the numbers ride beside it in `summary` for
`summarize_lease_waits`."""


def log_lease_wait(project_folder: str, reel_number: int, reel_name: str,
                   *, purpose: str, wait_seconds: float,
                   waited_on: str = "",
                   exclusive: bool = True) -> Dict[str, Any]:
    """File one `wait` line for a Resolve lease acquisition, never failing.

    `wait_seconds` is how long the caller waited before the instance
    was granted (`Lease.wait_seconds`); `waited_on` names who was
    observed holding it (`Lease.waited_on`), empty where nothing was.
    Filed under the same never-fail contract as every other line: a
    filing failure is said on stderr and the build continues.
    """
    try:
        waited = float(wait_seconds)
    except (TypeError, ValueError):
        waited = 0.0
    waited = max(0.0, waited)
    holder_text = str(waited_on or "").strip()
    if holder_text:
        detail = (f"{LEASE_WAIT_PREFIX}{waited:.1f}s for {purpose!r} "
                  f"behind {holder_text}")
    else:
        detail = (f"{LEASE_WAIT_PREFIX}{waited:.1f}s for {purpose!r} "
                  f"(uncontended)")
    return log_event(
        project_folder, reel_number, reel_name, WAIT,
        detail=detail,
        summary={"kind": LEASE_WAIT_KIND,
                 "purpose": str(purpose or ""),
                 "wait_seconds": round(waited, 3),
                 "waited_on": holder_text,
                 "exclusive": bool(exclusive),
                 # Contended means a holder was SEEN, not that the wait
                 # was long: an arrival that found the instance held is
                 # contention even where the holder released before the
                 # first poll. The queueing time is `wait_seconds`, and
                 # the reader reports both, so a near-zero finding reads
                 # as near-zero rather than as missing data.
                 "contended": bool(holder_text)})


def summarize_lease_waits(
        events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Add up the lease-wait lines: does the exclusive hold serialise?

    Reads the `lease_wait` lines `log_lease_wait` filed and answers
    the question that decides whether a queue is worth building: how
    many acquisitions, what fraction found the instance held, and how
    much queueing time that cost in total. A near-zero result is a
    result - "build no queue" - not a failure, and this report
    supports it as readily as the opposite: every acquisition files,
    so `acquisitions` is the denominator, not a count of incidents.

    Lines are recognised by their `summary.kind`, falling back to the
    `detail` prefix for hand-written ones; anything else is not a
    lease line and is ignored. Never refuses: an empty log reports
    zeros.
    """
    rows: List[Dict[str, Any]] = []
    for event in events or ():
        if not isinstance(event, dict) or event.get("phase") != WAIT:
            continue
        payload = event.get("summary")
        if isinstance(payload, dict) and payload.get("kind") == (
                LEASE_WAIT_KIND):
            try:
                waited = float(payload.get("wait_seconds") or 0.0)
            except (TypeError, ValueError):
                waited = 0.0
            rows.append({
                "at": event.get("at"),
                "reel": event.get("reel") or "",
                "reel_number": event.get("reel_number"),
                "purpose": payload.get("purpose") or "",
                "wait_seconds": max(0.0, waited),
                "waited_on": str(payload.get("waited_on") or ""),
                "contended": bool(payload.get("waited_on")),
            })
        elif (isinstance(event.get("detail"), str)
                and event["detail"].startswith(LEASE_WAIT_PREFIX)):
            rows.append({
                "at": event.get("at"),
                "reel": event.get("reel") or "",
                "reel_number": event.get("reel_number"),
                "purpose": "",
                "wait_seconds": _lease_wait_seconds(event["detail"]),
                "waited_on": _lease_wait_holder(event["detail"]),
                "contended": "(uncontended)" not in event["detail"],
            })
    acquisitions = len(rows)
    contended_rows = [row for row in rows if row["contended"]]
    total_wait = round(sum(row["wait_seconds"] for row in rows), 3)
    max_row = max(rows, key=lambda row: row["wait_seconds"],
                  default=None)
    by_holder: Dict[str, Dict[str, Any]] = {}
    for row in contended_rows:
        slot = by_holder.setdefault(row["waited_on"] or "(unnamed)", {
            "acquisitions": 0, "total_wait_seconds": 0.0})
        slot["acquisitions"] += 1
        slot["total_wait_seconds"] = round(
            slot["total_wait_seconds"] + row["wait_seconds"], 3)
    return {
        "acquisitions": acquisitions,
        "contended": len(contended_rows),
        "fraction_contended": (round(len(contended_rows) / acquisitions, 3)
                               if acquisitions else 0.0),
        "total_wait_seconds": total_wait,
        "mean_wait_seconds": (round(total_wait / acquisitions, 3)
                              if acquisitions else 0.0),
        "max_wait_seconds": (max_row["wait_seconds"]
                             if max_row is not None else 0.0),
        "max_wait": max_row,
        "by_holder": by_holder,
        "rows": rows,
    }


def _lease_wait_seconds(detail: str) -> float:
    """The seconds off a hand-written lease-wait line, best-effort."""
    try:
        head = detail[len(LEASE_WAIT_PREFIX):].split("s ", 1)[0]
        return max(0.0, float(head))
    except (TypeError, ValueError, IndexError):
        return 0.0


def _lease_wait_holder(detail: str) -> str:
    """The holder off a hand-written lease-wait line, best-effort."""
    marker = " behind "
    if marker in detail:
        return detail.split(marker, 1)[1].strip()
    return ""


OUTCOME_PROMOTED = "promoted"
OUTCOME_LEFT_ALONE = "left_alone"
OUTCOME_VERIFY_REFUSED = "verify_refused"
OUTCOME_SKIPPED = "skipped_by_exclusion"
OUTCOME_OUT_OF_WINDOW = "skipped_out_of_window"
OUTCOME_PLACED = "placed_unverified"

OUTCOMES = (OUTCOME_PROMOTED, OUTCOME_LEFT_ALONE, OUTCOME_VERIFY_REFUSED,
            OUTCOME_SKIPPED, OUTCOME_OUT_OF_WINDOW, OUTCOME_PLACED)
"""How a reel's story ended on the build that files the summary. Closed
so a typo cannot file a new outcome nothing reads."""


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return None
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _drop_strings(dropped: Any) -> List[str]:
    """Dropped entries as short `element (reason)` lines, capped.

    Records disagree on shape - semantic drops are dicts, span drops
    are objects with the same field names - so both are read the same
    way. Nothing is skipped silently: an entry with neither half still
    files, because a drop that vanishes from the summary is the
    silence this log exists to close.
    """
    out: List[str] = []
    for item in (dropped or ()):
        if isinstance(item, dict):
            element = (item.get("element")
                       or item.get("target_block_position")
                       or item.get("segment_id")
                       or item.get("placement_label"))
            # Motion drops name the shot and the effect, not an
            # element - the same words the run says when it drops one
            # ("motion dropped on shot N (effect): reason").
            if (item.get("effect_type") and element
                    and not item.get("element")):
                element = f"{item['effect_type']} on shot {element}"
            reason = item.get("reason")
        else:
            element = getattr(item, "element", None)
            reason = getattr(item, "reason", None)
        element = str(element or "").strip()
        reason = str(reason or "").strip()
        if element and reason:
            out.append(f"{element} ({reason})")
        elif reason:
            out.append(reason)
        elif element:
            out.append(element)
        else:
            out.append("(a drop with no recorded element or reason)")
    if len(out) > MAX_DROPS_PER_CHANNEL:
        extra = len(out) - MAX_DROPS_PER_CHANNEL
        out = out[:MAX_DROPS_PER_CHANNEL] + [f"(+{extra} more)"]
    return out


def _drops_of(record: Any) -> List[str]:
    if isinstance(record, dict):
        return _drop_strings(record.get("dropped"))
    return _drop_strings(getattr(record, "dropped", None))


def _card_rows(cards: Any) -> List[Dict[str, Any]]:
    """Cards placed, in the four fields the run says when it places one.

    Card objects and plain dicts both arrive here (the loop holds
    objects); both read the same way. A card that yields none of the
    four is still listed by repr, never dropped.
    """
    rows: List[Dict[str, Any]] = []
    for card in (cards or ()):
        if isinstance(card, dict):
            get = card.get
        else:
            get = lambda key, _card=card: getattr(_card, key, None)  # noqa: E731
        rows.append({
            "placement": get("placement"),
            "render_name": get("render_name"),
            "reel_start_frame": _int_or_none(get("reel_start_frame")),
            "duration_frames": _int_or_none(get("duration_frames")),
        })
    return rows


def _marker_rows(markers: Any) -> List[Any]:
    """Uncarried markers as identity rows, without the note text.

    The run already SAYS each dropped note with the captain's words;
    the summary keeps which marker (color, name, frame) and why it
    could not be carried, so a lane checking "did my note survive"
    reads here.
    """
    rows: List[Any] = []
    for marker in (markers or ()):
        if isinstance(marker, dict):
            row = {key: marker.get(key) for key in
                   ("color", "name", "frame", "why")
                   if marker.get(key) is not None}
            rows.append(row or str(marker)[:200])
        else:
            rows.append(str(marker)[:200])
    return rows


def _exclusion_rows(exclusions: Any) -> List[Dict[str, Any]]:
    """Keep-exclusions this build cut from the reel, as id and seconds.

    Items arrive as `(start, end, id)` tuples from the loop or as
    dicts; both read the same way. The run SAYS each cut with its
    words; the summary keeps which exclusion id landed where, so a
    lane checking "did lc-0016 cut" reads here instead of grepping.
    """
    rows: List[Dict[str, Any]] = []
    for item in (exclusions or ()):
        if isinstance(item, dict):
            rows.append({
                "id": item.get("id"),
                "start": _float_or_none(item.get("start")),
                "end": _float_or_none(item.get("end")),
            })
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            start, end, ident = item
            rows.append({
                "id": str(ident),
                "start": _float_or_none(start),
                "end": _float_or_none(end),
            })
        else:
            rows.append({"id": str(item)[:200],
                         "start": None, "end": None})
    return rows


def _trim_rows(applied: Any, held: Any) -> Dict[str, List[Dict[str, Any]]]:
    """The captain's span trims this build honoured, without the prose.

    The run still SAYS each trim (span, edge, seconds, anchor, reason);
    the summary keeps the machine half - which span, which edge, which
    seconds - so a lane checking "did lc-0016 land" reads here.
    """
    def shape(record: Any, with_seconds: bool) -> Dict[str, Any]:
        get = (record.get if isinstance(record, dict)
               else lambda key: getattr(record, key, None))
        row: Dict[str, Any] = {
            "span_index": _int_or_none(get("span_index")),
            "edge": get("edge"),
        }
        if with_seconds:
            was = get("was")
            now = get("now")
            row["was"] = list(was) if isinstance(was, (list, tuple)) else was
            row["now"] = list(now) if isinstance(now, (list, tuple)) else now
        return row

    return {
        "applied": [shape(r, True) for r in (applied or ())],
        "held": [shape(r, False) for r in (held or ())],
    }


def assemble_summary(
        *, outcome: str,
        staging: Optional[str] = None,
        final: Optional[str] = None,
        decision: Optional[str] = None,
        answers: Optional[Dict[str, Any]] = None,
        semantic_record: Any = None,
        span_record: Any = None,
        motion_record: Any = None,
        captain_trims: Optional[Dict[str, Any]] = None,
        keep_exclusions: Any = None,
        draw_gain_record: Optional[Dict[str, Any]] = None,
        captions: Optional[Dict[str, Any]] = None,
        cards: Any = None,
        suppressed_overlays: Any = None,
        overlay_sweep: Optional[Dict[str, Any]] = None,
        transition_placements: Any = None,
        has_freeze_tail: Any = None,
        verify: Optional[Dict[str, Any]] = None,
        retired_to: Optional[str] = None,
        markers: Optional[Dict[str, Any]] = None,
        version_control: Optional[Dict[str, Any]] = None,
        drift_end: Optional[Dict[str, Any]] = None,
        answers_owed: Any = None) -> Dict[str, Any]:
    """The per-reel payload for one `build_summary` line, from in-hand pieces.

    Every argument is what the caller already holds - a channel record,
    the rebuild decision, the gain probe's record, the just-written
    conformance slice - and every field the caller cannot supply stays
    None rather than estimated. A fact this cannot capture honestly is
    absent by construction: there is no parameter for it.
    """
    if outcome not in OUTCOMES:
        raise ValueError(
            f"{outcome!r} is not a build outcome this summary may carry: "
            f"{list(OUTCOMES)}.")
    answers = answers or {}
    captions = captions or {}
    sweep = overlay_sweep or {}
    markers = markers or {}
    gain = draw_gain_record or {}
    trims = captain_trims or {}
    vc = version_control or {}
    drift = drift_end if isinstance(drift_end, dict) else None
    carried = markers.get("carried") or []
    uncarried = markers.get("uncarried") or []
    return {
        "outcome": outcome,
        "staging": staging,
        "final": final,
        "decision": decision,
        "answers": {
            "semantic": answers.get("semantic"),
            "span": answers.get("span"),
            "motion": answers.get("motion"),
        },
        "dropped": {
            "semantic": _drops_of(semantic_record),
            "span": _drops_of(span_record),
            "motion": _drops_of(motion_record),
        },
        "captain_trims": _trim_rows(trims.get("applied"), trims.get("held")),
        "keep_exclusions": _exclusion_rows(keep_exclusions),
        "draw_gain": {
            "gain": _float_or_none(gain.get("gain")),
            "source": gain.get("source"),
            "disagrees_with_fallback": gain.get("disagrees_with_fallback"),
        },
        "captions": {
            "planned": _int_or_none(captions.get("planned")),
            "linked": _int_or_none(captions.get("linked")),
            "link_warnings": _int_or_none(captions.get("link_warnings")),
        },
        "cards": _card_rows(cards),
        "suppressed_overlays": [str(s) for s in (suppressed_overlays or ())],
        "overlay_sweep": {
            "passed": sweep.get("passed"),
            "checked": _int_or_none(sweep.get("checked")),
            "note": (sweep.get("detail") or sweep.get("unavailable")),
        },
        "transition_placements": _int_or_none(transition_placements),
        "has_freeze_tail": (None if has_freeze_tail is None
                            else bool(has_freeze_tail)),
        "verify": verify,
        "retired_to": retired_to,
        "markers": {
            "carried": _int_or_none(len(carried)) if isinstance(
                carried, (list, tuple)) else None,
            "uncarried": _marker_rows(
                uncarried if isinstance(uncarried, (list, tuple)) else ()),
        },
        "version_control": {
            "committed": vc.get("committed"),
            "commit": vc.get("commit"),
            "files": _int_or_none(len(vc.get("files") or ()))
            if vc.get("files") is not None else None,
        },
        "drift_end": None if drift is None else {
            "compared": bool(drift.get("compared", True)),
            "moved": _int_or_none(drift.get("moved")),
            "missing": _int_or_none(drift.get("missing")),
            "total": _int_or_none(drift.get("total")),
            "factor": _float_or_none(drift.get("factor")),
        },
        "answers_owed": [str(layer) for layer in (answers_owed or ())],
    }


def file_build_summary(project_folder: str, reel_number: int,
                       reel_name: str,
                       payload: Dict[str, Any]) -> Dict[str, Any]:
    """File one `build_summary` line, never failing the build.

    The same contract as every other line: a filing failure is said on
    stderr and returned as `unfiled`, because an instrument that
    refuses correct output is worse than a missing line (AGENTS.md
    10.4). Callers still wrap the assemble-plus-file in try/except,
    the existing style at every phase-log call site.
    """
    try:
        outcome = (payload if isinstance(payload, dict) else {}).get(
            "outcome")
        return log_event(
            project_folder, reel_number, reel_name, BUILD_SUMMARY,
            detail=f"build summary: {outcome}",
            summary=payload)
    except Exception as exc:  # noqa: BLE001 - the contract is never-fail
        try:
            outcome = (payload if isinstance(payload, dict) else {}).get(
                "outcome")
        except Exception:
            outcome = None
        event = {
            "format": FORMAT,
            "reel_number": reel_number,
            "reel": str(reel_name or ""),
            "phase": BUILD_SUMMARY,
            "at": _utcnow(),
            "detail": f"build summary: {outcome}",
            "unfiled": True,
        }
        print(f"  reel build summary unavailable ({exc}) - reel "
              f"{reel_number} said here and not filed", file=sys.stderr)
        return event


def _parse_counts(text: Any) -> tuple:
    """An `"expected/actual"` tally into ints, best-effort Nones."""
    try:
        expected, actual = str(text).split("/")
        return _int_or_none(expected), _int_or_none(actual)
    except (TypeError, ValueError):
        return None, None


def conformance_rows(project_folder: str) -> Dict[str, Dict[str, Any]]:
    """The per-reel slice of the conformance report the gate just wrote.

    Best-effort, keyed by the timeline name the gate graded (the
    staging container on a build): a missing or unreadable report is
    `{}`, never a refusal, and the summary then carries the verdict
    with the counts absent rather than estimated. The full finding
    rows stay in `conformance_report.json`, named by
    `CONFORMANCE_REPORT_REL`.
    """
    from library.tools.project_layout import Area, ProjectLayout

    try:
        path = os.path.join(
            str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
            CONFORMANCE_REPORT_FILENAME)
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError):
        return {}
    rows: Dict[str, Dict[str, Any]] = {}
    reels = (report or {}).get("reels") if isinstance(report, dict) else None
    for row in (reels or ()):
        if not isinstance(row, dict):
            continue
        name = row.get("reel_name")
        if not name:
            continue
        expected, actual = _parse_counts(row.get("captions"))
        findings = row.get("findings") or ()
        rows[str(name)] = {
            "errors": _int_or_none(row.get("errors")),
            "warnings": _int_or_none(row.get("warnings")),
            "finding_classes": sorted({
                str(f.get("finding_class")) for f in findings
                if isinstance(f, dict) and f.get("finding_class")}),
            "captions_expected": expected,
            "captions_actual": actual,
            "uncaptioned_seconds": _float_or_none(
                row.get("uncaptioned_seconds")),
            "plan_seconds": _float_or_none(row.get("plan_seconds")),
            "actual_frames": _int_or_none(row.get("actual_frames")),
        }
    return rows


def read_events(project_folder: str) -> List[Dict[str, Any]]:
    """Every phase line filed for this project, in filed order.

    Malformed lines are skipped, never fatal: a half-written tail from
    a killed build must not take the whole instrument with it.
    """
    from library.tools.project_layout import Area, ProjectLayout

    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
        FILENAME)
    if not os.path.isfile(path):
        return []
    events: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("phase") in PHASES:
                events.append(row)
    return events


def _parse_at(value: Any) -> Optional[datetime.datetime]:
    try:
        at = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    return at


def seconds_since(event: Dict[str, Any]) -> Optional[float]:
    """Seconds from this event's own timestamp to now, or None.

    Both endpoints are recorded, never estimated: the event's `at`
    (taken when the phase happened) and this instant. What the
    `build_started` line uses to say how long the reel waited for its
    build since its answers arrived.
    """
    at = _parse_at((event or {}).get("at"))
    if at is None:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    return round((now - at).total_seconds(), 1)


def summarize(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-reel reading of the log: phase times, gaps, waits between.

    The next investigation's starting point. For each reel (keyed by
    name, falling back to number): the first timestamp of each phase,
    the seconds from `answers_arrived` to `build_started` (the M05
    gap lives here), the build duration, and every `wait` reason filed
    between the first ask and the build start - which is what names a
    silence as engine-caused or upstream of the engine. A reel whose
    answers arrived and whose build started much later with no `wait`
    in between stalled upstream of every wait the engine records:
    worker loop, model turns, or contention from another lane, each of
    which now has its own line to confirm or exclude.

    Each slot also carries `build_summary` - the latest `build_summary`
    line's payload for that reel, or None where no build has filed one.
    A reel is staged under one name and promoted under another, so a
    summary attaches by reel NUMBER to every slot sharing it: whichever
    name a lane looks up, the summary is there. Where several builds
    filed, the latest filed wins; a summary whose payload is not a
    dict is ignored, never fatal.
    """
    by_reel: Dict[str, Dict[str, Any]] = {}
    for event in events or ():
        key = str(event.get("reel") or event.get("reel_number"))
        slot = by_reel.setdefault(key, {
            "reel": event.get("reel") or "",
            "reel_number": event.get("reel_number"),
            "phases": {}, "waits": [],
            "build_summary": None, "build_summary_at": None,
        })
        phase = event.get("phase")
        at = _parse_at(event.get("at"))
        if phase == WAIT:
            slot["waits"].append({
                "at": event.get("at"),
                "detail": event.get("detail") or "",
            })
        elif phase in PHASES and phase not in slot["phases"] and at is not None:
            slot["phases"][phase] = event.get("at")

    for slot in by_reel.values():
        phases = slot["phases"]
        first_ask = _parse_at(phases.get(PLAN_ASKED))
        answers = _parse_at(phases.get(ANSWERS_ARRIVED))
        started = _parse_at(phases.get(BUILD_STARTED))
        finished = _parse_at(phases.get(BUILD_FINISHED))
        slot["seconds_ask_to_answers"] = (
            round((answers - first_ask).total_seconds(), 1)
            if first_ask is not None and answers is not None else None)
        slot["seconds_answers_to_build"] = (
            round((started - answers).total_seconds(), 1)
            if answers is not None and started is not None else None)
        slot["seconds_build"] = (
            round((finished - started).total_seconds(), 1)
            if started is not None and finished is not None else None)
        if first_ask is not None and started is not None:
            slot["waits_between_answers_and_build"] = [
                w for w in slot["waits"]
                if (answers is None or _parse_at(w.get("at")) is None
                    or _parse_at(w.get("at")) >= answers)
                and (_parse_at(w.get("at")) is None
                     or _parse_at(w.get("at")) <= started)]
        else:
            slot["waits_between_answers_and_build"] = list(slot["waits"])
    for key, event in _latest_summaries(events).items():
        payload = event.get("summary")
        kind, ident = key
        if kind == "number":
            targets = [slot for slot in by_reel.values()
                       if slot.get("reel_number") == ident]
        else:
            slot = by_reel.get(ident)
            targets = [slot] if slot is not None else []
        if not targets:
            name = str(event.get("reel") or event.get("reel_number"))
            fresh = {
                "reel": event.get("reel") or "",
                "reel_number": event.get("reel_number"),
                "phases": {}, "waits": [],
                "seconds_ask_to_answers": None,
                "seconds_answers_to_build": None,
                "seconds_build": None,
                "waits_between_answers_and_build": [],
                "build_summary": None, "build_summary_at": None,
            }
            by_reel.setdefault(name, fresh)
            targets = [by_reel[name]]
        for slot in targets:
            slot["build_summary"] = payload
            slot["build_summary_at"] = event.get("at")
    return by_reel


def _summary_key(event: Dict[str, Any]) -> tuple:
    """What a summary line attaches by: stable reel number, else name.

    A reel number of 0 (filed where the number could not be read)
    attaches by name only - 0 names no reel, and one reel's summary
    must never land on another's slot through a shared unknown.
    """
    number = event.get("reel_number")
    if isinstance(number, bool):
        number = int(number)
    if isinstance(number, (int, float)) and number:
        return ("number", number)
    return ("reel", str(event.get("reel") or ""))


def _latest_summaries(events: Any) -> Dict[tuple, Dict[str, Any]]:
    """The latest filed `build_summary` line per reel, in filed order.

    Non-dict payloads are ignored: a hand-edited line must not take
    the reading with it. Where timestamps tie or are absent, the later
    filed line wins - except a dateless line never displaces a dated
    one, because a line that cannot say when it was filed cannot claim
    to be newer than one that can.
    """
    latest: Dict[tuple, Dict[str, Any]] = {}
    for event in events or ():
        if not isinstance(event, dict):
            continue
        if event.get("phase") != BUILD_SUMMARY:
            continue
        if not isinstance(event.get("summary"), dict):
            continue
        key = _summary_key(event)
        at = _parse_at(event.get("at"))
        prev = latest.get(key)
        if prev is None:
            latest[key] = event
        elif at is not None and (
                _parse_at(prev.get("at")) is None or at >= _parse_at(
                    prev.get("at"))):
            latest[key] = event
    return latest
