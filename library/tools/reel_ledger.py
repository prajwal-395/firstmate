"""Why each placed master range is on a reel, written down at build time.

Reel 13 of the field test played 7.6 seconds drawn from another
moment's pool, positioned after its own closer - and nothing on disk
could say how it got there. Measured against the real project
records: the stored closer ended at 342.03s (the captain's own pin,
silence after "The link's in our bio."), Reel 05's declared body
opens at 342.038s, and the build-time boundary repair
(`reel_proposal.snap_moment_to_speech`) widened the closer end
outward to the bound segment edge at 349.54s - through Craig's
opening "So", the first word of Reel 05. `reel_ranges` then placed
the widened closer whole, because a closer is placed whole whatever
it covers. Two failures at once: a range reached a timeline that no
declared window covers, and no record named the path that put it
there. This module is both halves of the fix.

THE AUDIT (`audit_ranges`) runs on the final ranges - after take
cuts, strikes, trims and the ending - against the DECLARED windows:
what the stored proposal file says, before any repair widens it.
A repair is not a declaration, so a second the repair admits is a
second that must be justified, not assumed:

* a range touching NEITHER declared window is foreign to this reel
  under every reading - no word-edge repair can explain it - and
  raises `OutOfWindowRange`. The build loop skips that reel WITH
  the reason (the `ExclusionWipesBody` shape), leaving the approved
  timeline exactly as it is. A refusal that broke twenty-nine
  correct reels to catch one would be the wrong trade, and placing
  the range would be the defect this exists to end.
* a range that touches a declared window but REACHES PAST it is
  kept and REPORTED, with the overhang in seconds and which other
  reel's declared pool those seconds fall in. Refusing those would
  break legitimate word-edge cover - measured on Reel 02, whose
  closer end moved 328.231s to 328.45s to cover the closing word
  "bio." - so the audit names them instead of stopping them.

There is deliberately no threshold: any overhang is said, down to
the millisecond. What counts as "too far" is a judgement for the
operator reading the report, not a number in this module (AGENTS.md
10.5). `EPSILON` below is float hygiene only - the snap writes
edges like 349.53999999999996 - never a tolerance for content.

THE LEDGER (`file_reel_ledger`) is the WHY on disk: per placed
range its origin (body or closer), the declared and repaired
windows it was checked against, the repair moves, pin records,
trims and ending that shaped it, and any overhang with the invaded
reel named. Filed per reel under its FINAL name into
`pipeline_output/review/reel_decisions.json`, merged the way
provenance merges - so promotion needs no rename wiring and a
later question ("why is this clip here") is answered from disk,
never re-derived. Filing never fails a build: like the phase log,
a write that cannot land is said on stderr and the build
continues (AGENTS.md 10.4).

`tests/unit/reels/test_reel_out_of_window.py`.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

FORMAT = "reel_decisions/1"
FILENAME = "reel_decisions.json"

EPSILON = 1e-6
"""Float hygiene for window comparisons, nothing more. The snap writes
edges like 349.53999999999996 for 349.54, so an intersection test
without slack can miss contact by a rounding dust. This never admits
content: an overhang is still measured exactly and reported."""


class OutOfWindowRange(RuntimeError):
    """A final range touches no declared window of its own reel.

    Raised by `audit_ranges`, caught per reel by the build loop and
    the ask path, which skip that reel WITH the reason rather than
    placing seconds no declaration covers - or killing the twentynine
    reels that derived cleanly. Carries the `ledger` that was being
    audited, so the skip path files the WHY even for the reel it
    refuses to place.
    """

    ledger: Optional[dict] = None


def stored_windows(moment) -> tuple:
    """The DECLARED windows off a stored moment: `(body, closer|None)`.

    Read before any repair runs: the snap widens boundaries outward
    and the redraws move them, so after either the moment no longer
    says what was declared. Floats, because everything downstream
    compares in seconds.
    """
    body = (float(moment.timeline_start), float(moment.timeline_end))
    closer = None
    cta = getattr(moment, "call_to_action", None)
    if cta is not None:
        start = getattr(cta, "timeline_start", None)
        end = getattr(cta, "timeline_end", None)
        if (isinstance(start, (int, float)) and not isinstance(start, bool)
                and isinstance(end, (int, float))
                and not isinstance(end, bool)):
            closer = (float(start), float(end))
    return body, closer


def _touches(range_start: float, range_end: float,
             window: Optional[tuple]) -> bool:
    """Whether a range shares at least a dust with a window."""
    if window is None:
        return False
    return (range_start < window[1] + EPSILON
            and range_end > window[0] - EPSILON)


def _overhang(range_start: float, range_end: float,
              window: tuple) -> tuple:
    """Seconds of the range outside its window, `(before, after)`."""
    before = max(0.0, window[0] - range_start)
    after = max(0.0, range_end - window[1])
    return round(before, 3), round(after, 3)


def _invaded_reels(outside: Sequence[tuple],
                   sibling_windows: dict, own_number) -> list:
    """Declared pools the outside seconds fall in, naming the reel.

    `sibling_windows` maps reel number to
    `{"body": (start, end), "closer": (start, end)|None}` - the same
    stored declaration this reel is checked against, so "Reel 13's
    overhang is Reel 05's opener" is a comparison of two declarations,
    never of placed content. The reel's own windows never testify:
    an overhang inside them is not an invasion.
    """
    found = []
    for start, end in outside:
        if end - start <= 0:
            continue
        for number, windows in (sibling_windows or {}).items():
            try:
                if int(number) == int(own_number):
                    continue
            except (TypeError, ValueError):
                continue
            for kind in ("body", "closer"):
                window = (windows or {}).get(kind)
                if window is None:
                    continue
                overlap = (min(end, float(window[1]))
                           - max(start, float(window[0])))
                if overlap > EPSILON:
                    found.append({
                        "reel": int(number),
                        "window": kind,
                        "seconds": [round(max(start, float(window[0])), 3),
                                    round(min(end, float(window[1])), 3)],
                    })
    return found


def audit_ranges(*, number: int, staging: str = "", final: str = "",
                 stored_body: tuple, stored_closer: Optional[tuple],
                 repaired_body: Optional[tuple] = None,
                 repaired_closer: Optional[tuple] = None,
                 ranges: Sequence[tuple] = (),
                 sibling_windows: Optional[dict] = None,
                 repair_moves: Sequence[dict] = (),
                 pin_records: Sequence[dict] = (),
                 trim_records: Optional[dict] = None,
                 ending: Optional[dict] = None) -> dict:
    """Check final ranges against declared windows; ledger either way.

    Pure: reads nothing, writes nothing, raises `OutOfWindowRange`
    naming the first range that touches neither declared window.
    Returns the ledger dict - filed by the caller even on the raise
    path, because a skipped reel is exactly when the WHY is needed.
    Call `file_reel_ledger` with the return before acting on it.

    Origin assignment follows how `reel_ranges` builds the list: the
    body first, the closer appended last, trims and endings only ever
    shrinking. So the last range intersecting the declared closer is
    the closer; every other range is body. A reel with no declared
    closer has body ranges only.
    """
    checked = [(float(a), float(b)) for a, b in (ranges or [])]
    rows = []
    disjoint = []
    for index, (start, end) in enumerate(checked):
        in_body = _touches(start, end, stored_body)
        in_closer = _touches(start, end, stored_closer)
        is_last = index == len(checked) - 1
        if is_last and stored_closer is not None and in_closer:
            origin = "closer"
            window = stored_closer
        else:
            origin = "body"
            window = stored_body
        before, after = _overhang(start, end, window)
        outside: list = []
        if before > 0:
            outside.append((start, min(end, window[0])))
        if after > 0:
            outside.append((max(start, window[1]), end))
        rows.append({
            "index": index,
            "master": [round(start, 3), round(end, 3)],
            "origin": origin,
            "declared_window": [round(window[0], 3), round(window[1], 3)],
            "overhang_seconds": {"before": before, "after": after},
            "invades": _invaded_reels(outside, sibling_windows, number),
        })
        if not in_body and not in_closer:
            disjoint.append(index)
    ledger = {
        "format": FORMAT,
        "reel_number": int(number),
        "staging": staging or "",
        "final": final or staging or "",
        "filed_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "declared": {
            "body": [round(stored_body[0], 3), round(stored_body[1], 3)],
            "closer": (None if stored_closer is None else
                       [round(stored_closer[0], 3),
                        round(stored_closer[1], 3)]),
        },
        "repaired": {
            "body": (None if repaired_body is None else
                     [round(float(repaired_body[0]), 3),
                      round(float(repaired_body[1]), 3)]),
            "closer": (None if repaired_closer is None else
                       [round(float(repaired_closer[0]), 3),
                        round(float(repaired_closer[1]), 3)]),
            "moves": [dict(m) for m in (repair_moves or [])],
        },
        "ranges": rows,
        "disjoint": list(disjoint),
        "pins": [dict(p) for p in (pin_records or [])],
        "trims": ({"applied": list((trim_records or {}).get("applied") or []),
                   "held": list((trim_records or {}).get("held") or []),
                   "drifted": list((trim_records or {}).get("drifted") or []),
                   "stale": list((trim_records or {}).get("stale") or [])}),
        "ending": dict(ending) if ending is not None else None,
    }
    if disjoint:
        first = rows[disjoint[0]]
        error = OutOfWindowRange(
            f"reel {number}: range {first['master'][0]:.2f}-"
            f"{first['master'][1]:.2f}s touches neither its declared "
            f"body ({ledger['declared']['body'][0]:.2f}-"
            f"{ledger['declared']['body'][1]:.2f}s) nor its declared "
            f"closer "
            f"({ledger['declared']['closer'] if ledger['declared']['closer'] is not None else 'none'}) "
            f"- no declaration covers these seconds, so this reel is "
            f"skipped with the reason rather than placed. The ledger "
            f"names every placed range's origin in "
            f"{FILENAME}.")
        error.ledger = ledger
        raise error
    return ledger


def ledger_path(project_folder) -> Path:
    """Where the merged ledger lives: beside provenance and the gate."""
    return (Path(project_folder) / "pipeline_output" / "review"
            / FILENAME)


def _ledger_same(previous, current) -> bool:
    """Whether a filing would change anything but the stamp.

    `filed_at` names the run that filed, not the decision - a rebuild
    that derives the same windows, ranges, moves, pins, trims and
    ending decided nothing new, and the ledger must say so by staying
    byte-identical (`test_a_left_alone_reel_keeps_every_sidecar_entry_it_had`).
    Anything else differing is a new decision and is filed with a new
    stamp.
    """
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return False
    previous = {k: v for k, v in previous.items() if k != "filed_at"}
    current = {k: v for k, v in current.items() if k != "filed_at"}
    return previous == current


def file_reel_ledger(project_folder, final_name: str,
                     ledger: dict) -> Optional[Path]:
    """Merge one reel's ledger under its FINAL name. Never raises.

    Keyed by final name so promotion needs no rename wiring: the
    build loop knows both names and files the one Resolve will hold.
    Byte-idempotent: a filing that changes nothing but `filed_at`
    leaves the file untouched, so a build that placed nothing leaves
    a left-alone reel's sidecar exactly as it was. A write that
    cannot land is said on stderr and the build continues - an
    instrument must never fail the build it instruments (AGENTS.md
    10.4) - and the missing entry reads as a gap, never as a clean
    reel.
    """
    try:
        path = ledger_path(project_folder)
        from library.tools.project_file_lock import lock_project_file
        with lock_project_file(path):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            reels = existing.get("reels")
            if not isinstance(reels, dict):
                reels = {}
                existing["reels"] = reels
            if _ledger_same(reels.get(str(final_name)), ledger):
                return path
            existing["format"] = FORMAT
            reels[str(final_name)] = ledger
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            return path
    except Exception as exc:  # noqa: BLE001 - the contract is never-fail
        print(f"  reel ledger unfiled for {final_name}: {exc!r} - "
              f"the build continues without it", file=sys.stderr)
        return None


def read_reel_ledger(project_folder) -> dict:
    """The merged ledger, or `{}`. Never raises."""
    try:
        data = json.loads(
            ledger_path(project_folder).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
