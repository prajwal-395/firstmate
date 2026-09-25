"""One declared edit ledger the build replays (E2, rung 6 foundation; K3).

The captain's question, 2026-09-24: can Ren take a natural-language
request and execute it faithfully. The execution-frontier scout's
answer: not yet - and cluster K3 names the architecture that cannot
carry it: *"Hands exist but live outside the plan - resolve-axi can
isolate voice, set a LUT, switch multicam, trim - and the next build
paints every one of those over. No declared store carries them."*
(`data/vep-ren-execution-frontier/report.md`). On the board the
captain chose E2: **one declared edit ledger the build replays**.
This module is that store.

What it generalises, and what it does not
-----------------------------------------
`captain_edits` (`library/tools/captain_edits.py`) already holds
word-anchored deltas - `drop_fragment` and the rest - that survive a
rebuild at the plan level. The `external/*.json` pins
(`mix_intent`, `overlay_intent`, `reel_ending`, `caption_timing`,
`placed_assets`, `do_not_draw`, `reel_cta`,
`tail_extend_authorizations`) hold standing decisions each owned by
the module that reads them. This ledger generalises the FIRST across
the hands: a direct edit made with Ren's hands (resolve-axi) or
recorded from the captain is a row here, and the build replays it
after its own passes - so the next build carries it instead of
painting over it. The owned pin stores keep their owners and their
appliers; a row here never duplicates one. The five `captain_edits`
kinds may ALSO be recorded here (same validation, imported not
restated) - and `captain_edits.load_edits` reads them back as one
merged view, so every existing applier replays ledger rows of those
kinds with no second implementation. New direct-edit records land
here; the legacy file stays readable.

Where it lives, and on which side of the split
----------------------------------------------
`<project>/external/edit_ledger.json`, a DECLARATION - `version`
plus the owner's own field - on the declarations side of the
`external/` split (`library/tools/external_inputs.py`): checked by
its owner's reader, never asserted, never a step output. Per reel:
a row may carry `reel` (the timeline-name prefix convention from
`reel_ending`), so one reel's edits revert alone. Declarations are
SOURCE anchored on words and outlive builds; regenerated state is
build OUTPUT (0 of 47 clip identities survive a rebuild) - the two
were ruled apart and this store sits on the declarations side.

A row
-----
`op` (what), `anchor` (what it holds onto), `params` (how much, in
the requester's units), `reel` (which reel, optional), `stated_by`
(who stated it) and `reason` (their words):

    {"op": "voice_isolation", "anchor": {"kind": "reel"},
     "reel": "Reel 09 - ...", "params": {"track": 1, "amount": 60},
     "stated_by": "requester",
     "reason": "remove the background noise from the host's mic"}

The anchor is words or the whole reel - never frames. A frame number
is not stable across a rebuild (re-transcription moves every
boundary), which is the same reason `captain_edits` refuses one at
write time; a row carrying one is REFUSED here with that reason,
not lost at rebuild. Numbers the requester states - frames of lead,
dB, percent, words per card - travel in `params`, in the requester's
units (E3); a feel word otherwise.

`stated_by` is the taste seam, and deliberately a tier, not a
scope: `captain` (a hand move in Resolve), `requester` (the words
of the natural-language request) or `model` (reasoned over measured
signal this run). `decided_value`'s stated-preference tier is where
a taste profile plugs in later - not scoped further here.

The ops
-------
Hands ops the build replays today: `voice_isolation` (track-level
Resolve Voice Isolation, the Hands-probe MX2.1 row) and `clip_lut`
(a node LUT on the clips speaking the anchor, the C1.3-class row).
Plan-level kinds, replayed through the existing appliers via the
merged view: `transform_override`, `span_retime`, `drop_fragment`,
`caption_fix`, `redraw_closer`. Carriers for later rungs, validated
and stored but NOT replayed yet: `retime` (rung 8 hands), `grade`
(rung 8 hands) and `angle_plan` (the E5 declared per-reel angle
plan: camera, minimum shot, lead). The schema carries them now so
a later rung wires the replayer, not the store.

Replay discipline
-----------------
The build replays hands rows AFTER its own passes (beside
`apply_transform_overrides` in `reel_build.build_reel_timeline`),
each judged by Resolve's own read-back - the same discipline as the
resolve-axi verb that recorded it. A row the build cannot replay
(no such track, no node graph, anchor spoken nowhere, carrier with
no replayer yet) is REPORTED BY NAME - op, reel, anchor - on
stderr and in the build record, never dropped silently. A ledger
the build cannot read at all REFUSES the build: a recorded decision
the build cannot see is a build that paints over it by construction.

`tests/test_edit_ledger.py`.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

#: Schema version this reader honours.
EDIT_LEDGER_VERSION = 1

#: The file basename, under the project's external-inputs area.
EDIT_LEDGER_FILENAME = "edit_ledger.json"

#: Ops the build replays onto the live timeline after its own passes.
REPLAYED_OPS = ("voice_isolation", "clip_lut")

#: Plan-level kinds, replayed through the existing `captain_edits`
#: appliers via the merged view (`project_onto_captain_edits`), never
#: here - replaying them twice would double-apply every hold.
PROJECTED_OPS = ("transform_override", "span_retime", "drop_fragment",
                 "caption_fix", "redraw_closer")

#: Carriers for later rungs: validated and stored now, reported by
#: name (never silently dropped) until their rung wires a replayer.
#: `retime`/`grade` are the rung-8 hands; `angle_plan` is the E5
#: declared per-reel angle plan (camera, minimum shot, lead).
CARRIER_OPS = ("retime", "grade", "angle_plan")

#: The complete vocabulary.
OPS = REPLAYED_OPS + PROJECTED_OPS + CARRIER_OPS

#: Anchor kinds. Words survive a rebuild; the whole reel is the
#: track-level scope. Frames do not survive one and are refused.
ANCHOR_KINDS = ("words", "reel")

#: Who stated the value - the taste seam as a tier, not a scope.
#: `decided_value`'s stated-preference tier is where a taste profile
#: plugs in later.
STATED_BY = ("captain", "requester", "model")


class EditLedgerError(ValueError):
    """A ledger row that cannot be what it claims to be, refused by name."""


# ── Validation: a row is small, readable, and anchored to words ──────

def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def validate_rows(value) -> list:
    """Structural check. Raises `EditLedgerError` naming what is wrong."""
    from library.tools import captain_edits as _edits

    if not isinstance(value, list) or not value:
        raise EditLedgerError(
            "edit_ledger rows must be a non-empty list of rows. An empty "
            "one is the absence of edits, not edits declared from "
            "outside - leave the file out instead.")
    for index, row in enumerate(value):
        label = f"edit_ledger.rows[{index}]"
        if not isinstance(row, dict):
            raise EditLedgerError(f"{label} is not an object")
        op = row.get("op")
        if op not in OPS:
            raise EditLedgerError(
                f"{label}.op is {op!r}. Known ops: "
                f"{', '.join(OPS)}.")
        anchor = row.get("anchor")
        if not isinstance(anchor, dict):
            raise EditLedgerError(
                f"{label} carries no anchor object - a row holds onto "
                f"spoken words (kind 'words') or the whole reel (kind "
                f"'reel'), which is what survives a rebuild.")
        kind = anchor.get("kind")
        if kind not in ANCHOR_KINDS:
            if kind in ("timeline_frames", "frames", "frame",
                        "source_frames"):
                raise EditLedgerError(
                    f"{label} anchors to {kind!r}: a frame number is not "
                    f"stable across a rebuild - re-transcription moves "
                    f"every boundary, and 0 of 47 clip identities survive "
                    f"one. Anchor to the spoken words instead.")
            raise EditLedgerError(
                f"{label}.anchor.kind is {kind!r}: one of "
                f"{', '.join(ANCHOR_KINDS)}.")
        phrase = anchor.get("phrase", "")
        if kind == "words":
            if (not isinstance(phrase, str)
                    or not _edits.normalize(phrase)):
                raise EditLedgerError(
                    f"{label}.anchor.phrase names no spoken words - a "
                    f"word-anchored row holds onto what was SAID, which "
                    f"is what survives a rebuild.")
        elif phrase:
            raise EditLedgerError(
                f"{label} is reel-anchored and carries a phrase: the "
                f"whole reel needs no words - leave 'phrase' out.")
        for field in ("anchor_frame", "anchor_frames", "frame",
                      "timeline_start", "timeline_end", "start", "end"):
            if field in row or (isinstance(anchor, dict)
                                and field in anchor):
                raise EditLedgerError(
                    f"{label} carries {field!r}: a frame or timecode pin "
                    f"breaks the moment anything upstream re-times. "
                    f"Anchor to spoken words instead.")
        reel = row.get("reel")
        if reel is not None and (
                not isinstance(reel, str) or not reel.strip()):
            raise EditLedgerError(
                f"{label} carries reel={reel!r}: a per-reel scope names "
                f"the reel's timeline name (matched by prefix, the "
                f"`reel_ending` convention), or is left out - a row with "
                f"no `reel` holds on every reel in scope.")
        params = row.get("params")
        if not isinstance(params, dict):
            raise EditLedgerError(
                f"{label} carries no 'params' object - the amount in "
                f"the requester's units (dB, frames, percent, LUT path) "
                f"is what the build replays.")
        _validate_params(label, row, anchor)
        stated_by = row.get("stated_by")
        if stated_by not in STATED_BY:
            raise EditLedgerError(
                f"{label}.stated_by is {stated_by!r}: one of "
                f"{', '.join(STATED_BY)} - captain (a hand move), "
                f"requester (the request's words) or model (reasoned "
                f"over signal). The taste seam is a tier, not scoped "
                f"further.")
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise EditLedgerError(
                f"{label} carries no 'reason' - the requester's or "
                f"captain's own words, which is what any listing reads "
                f"back. A row nobody can read back is not reviewable.")
    return value


def _validate_params(label: str, row: dict, anchor: dict) -> None:
    """Per-op amounts, in the requester's units. Raises `EditLedgerError`."""
    from library.tools import captain_edits as _edits
    from library.tools import dialogue_cleanup as _dclean

    op = row.get("op")
    params = row.get("params") or {}
    if op == "voice_isolation":
        if anchor.get("kind") != "reel":
            raise EditLedgerError(
                f"{label} is voice_isolation with a word anchor: "
                f"isolation is per TRACK, whole-timeline - anchor kind "
                f"'reel'.")
        track = params.get("track")
        if (isinstance(track, bool) or not isinstance(track, int)
                or track < 1):
            raise EditLedgerError(
                f"{label} names track {track!r}: the audio track number "
                f"from 1, Resolve's own numbering.")
        amount = params.get("amount")
        if not _is_number(amount) or not (
                _dclean.VOICE_ISOLATION_MIN <= amount
                <= _dclean.VOICE_ISOLATION_MAX):
            raise EditLedgerError(
                f"{label} wants amount {amount!r}: Resolve's own 0..100 "
                f"scale, passed through - the strength the floor made "
                f"right.")
    elif op == "clip_lut":
        if anchor.get("kind") != "words":
            raise EditLedgerError(
                f"{label} is clip_lut with no word anchor: a LUT holds "
                f"on the clips speaking the anchor's words, so the "
                f"words name which clips.")
        lut = params.get("lut")
        if not isinstance(lut, str) or not lut.strip():
            raise EditLedgerError(
                f"{label} names no LUT: the Resolve LUT path (as "
                f"`color lut --lut` takes it) is what the build sets.")
        node = params.get("node", 1)
        if isinstance(node, bool) or not isinstance(node, int) \
                or node < 1:
            raise EditLedgerError(
                f"{label} names node {node!r}: the node index from 1 - "
                f"the Graph API has no AddNode, so only an existing "
                f"node can hold it.")
    elif op == "transform_override":
        prop = params.get("property")
        if prop not in _edits.TRANSFORM_PROPERTIES:
            raise EditLedgerError(
                f"{label} names property {prop!r}: one Edit-page "
                f"transform the build itself sets - "
                f"{', '.join(_edits.TRANSFORM_PROPERTIES)}.")
        number = params.get("value")
        if not _is_number(number):
            raise EditLedgerError(
                f"{label} carries value {number!r}: the number "
                f"{prop} must hold.")
        if prop in ("ZoomX", "ZoomY") and not number > 0:
            raise EditLedgerError(
                f"{label} wants {prop} {number!r}: a zoom of zero or "
                f"less draws nothing.")
        if prop in ("Pan", "Tilt") and abs(number) > \
                _edits.PAN_TILT_RAIL_1080X1920:
            raise EditLedgerError(
                f"{label} wants {prop} {number:g}: Resolve holds only "
                f"+- {_edits.PAN_TILT_RAIL_1080X1920:.0f} and clamps "
                f"past it silently.")
    elif op == "span_retime":
        edge = params.get("edge")
        if edge not in ("head", "tail"):
            raise EditLedgerError(
                f"{label} names edge {edge!r}: head (its opening) or "
                f"tail (its close) onto the anchor's own word edge.")
    elif op == "caption_fix":
        replacement = params.get("replacement")
        if (not isinstance(replacement, str)
                or not replacement.strip()):
            raise EditLedgerError(
                f"{label} is a caption_fix with no 'replacement' - "
                f"name what the caption should read.")
    elif op == "redraw_closer":
        opening = params.get("from_phrase")
        if (not isinstance(opening, str)
                or not _edits.normalize(opening)):
            raise EditLedgerError(
                f"{label} is a redraw_closer with no 'from_phrase' - "
                f"a pin must name WHICH closer it moves, in the words "
                f"that closer opens on now.")
    elif op == "drop_fragment":
        pass
    elif op == "retime":
        percent = params.get("percent")
        segments = params.get("segments")
        if percent is None and segments is None:
            raise EditLedgerError(
                f"{label} is a retime with neither 'percent' nor "
                f"'segments' - name the speed the words must play at.")
        if percent is not None and (
                not _is_number(percent) or percent <= 0):
            raise EditLedgerError(
                f"{label} wants percent {percent!r}: a positive speed, "
                f"100 for sync.")
    elif op == "grade":
        if not any(key in params for key in ("lut", "cdl", "drx")):
            raise EditLedgerError(
                f"{label} is a grade naming no route - one of 'lut' "
                f"(a Resolve LUT path), 'cdl' or 'drx'.")
    elif op == "angle_plan":
        camera = params.get("camera")
        if camera is not None and not isinstance(camera, str):
            raise EditLedgerError(
                f"{label} names camera {camera!r}: the angle to hold, "
                f"in the project's own camera words.")
        for key in ("min_shot_seconds", "lead_frames"):
            number = params.get(key)
            if number is not None and (
                    not _is_number(number) or number < 0):
                raise EditLedgerError(
                    f"{label} carries {key} {number!r}: a non-negative "
                    f"number in the requester's units.")


# ── Reading: the file the captain writes ─────────────────────────────

def ledger_path(project_folder) -> Path:
    from library.tools.project_layout import Area, ProjectLayout

    return Path(ProjectLayout(str(project_folder)).read_dir(
        Area.EXTERNAL_STATE)) / EDIT_LEDGER_FILENAME


def load_rows(project_folder=None) -> list:
    """The verified rows in force, or [] where none were ever written.

    The owner's reader: `external_inputs` checks the declaration with
    this and leaves it out of the supplied state. Raises
    `EditLedgerError` for a malformed file, unchanged - the owner
    wrote the clearest message about its own file.
    """
    if not project_folder:
        return []
    try:
        path = ledger_path(project_folder)
    except (KeyError, ValueError):
        return []
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EditLedgerError(
            f"{path} cannot be read: {exc}") from exc
    if not isinstance(document, dict) or "rows" not in document:
        raise EditLedgerError(
            f"{path.name} must be an object with 'version' and 'rows' "
            f"(a declaration, not supplied state).")
    version = document.get("version")
    if version != EDIT_LEDGER_VERSION:
        raise EditLedgerError(
            f"{path.name} carries version {version!r}: this reader "
            f"honours {EDIT_LEDGER_VERSION}.")
    return validate_rows(document["rows"])


def rows_for_reel(rows: list, reel_name: str = "") -> list:
    """Rows holding on this reel's build: unscoped plus prefix-scoped.

    The `reel_ending` prefix convention, shared with
    `captain_edits._reel_in_scope`: a staging suffix does not make it
    another reel. A row scoped elsewhere is not returned - and never
    silently either (see `report_unreplayable` at replay time).
    """
    from library.tools import captain_edits as _edits

    kept = []
    for row in rows or []:
        scope = row.get("reel")
        if scope is None:
            kept.append(row)
        elif _edits._reel_in_scope(scope, reel_name or ""):
            kept.append(row)
    return kept


def _row_identity(row: dict) -> tuple:
    """What makes two rows the SAME decision: op, anchor, scope, and
    the field that distinguishes it - the re-ruling rule from
    `captain_edits._edit_identity`, extended over the hands ops. A
    re-stating of the same decision SUPERSEDES it; a different scope
    or amount is a different row: Reel 02's isolation at 60 must not
    supersede Reel 01's, and a re-leveled 80 supersedes the 60."""
    anchor = row.get("anchor") or {}
    base = (row.get("op"), anchor.get("kind"),
            _anchor_phrase(row), row.get("reel") or "")
    params = row.get("params") or {}
    op = row.get("op")
    if op == "voice_isolation":
        return base + (params.get("track"),)
    if op == "clip_lut":
        # The LUT is the value being re-ruled, like voice isolation's
        # amount. A new look on the same reel, words and node replaces
        # the old choice; keeping both would replay two SetLUT calls and
        # leave a stale decision behind the later one.
        return base + (params.get("node", 1),)
    if op == "transform_override":
        return base + (params.get("property"),)
    if op == "span_retime":
        return base + (params.get("edge"),)
    if op == "redraw_closer":
        from library.tools import captain_edits as _edits
        return base + (_edits.normalize(params.get("from_phrase", "")),)
    if op == "caption_fix":
        return base
    return base


def _anchor_phrase(row: dict) -> str:
    from library.tools import captain_edits as _edits

    anchor = row.get("anchor") or {}
    if anchor.get("kind") == "words":
        return _edits.normalize(anchor.get("phrase", ""))
    return ""


def _ledger_key(row: dict) -> str:
    """The key-scheme entry key, derived from the owner's own
    identity - one answer to what makes two rows the same, never two
    (`declaration_keys`)."""
    return json.dumps(list(_row_identity(row)), ensure_ascii=False)


def record_row(project_folder, row: dict) -> tuple:
    """Append one direct-edit decision to the ledger, or supersede it.

    Validates structurally BEFORE touching disk, checks a word anchor
    against the measured transcript where one is on file (a typo fails
    here, not on the next build), then merges through the key scheme:
    an exact duplicate is REFUSED as already in force, a re-stating
    of the same decision REPLACES it, anything else appends. Returns
    `(row, action)` with action `"recorded"` or `"superseded"`. The
    write is atomic and merged, so two agents recording different
    rows do not lose one (`declaration_keys`).
    """
    from library.tools import captain_edits as _edits

    validate_rows([row])
    row = json.loads(json.dumps(row, ensure_ascii=False))
    anchor = row.get("anchor") or {}
    if anchor.get("kind") == "words":
        transcript = _edits.load_transcript(project_folder)
        _edits.check_anchor_spoken(
            {"kind": "drop_fragment",
             "anchor_phrase": anchor.get("phrase", "")},
            transcript)
        if row.get("op") == "span_retime" and transcript is not None:
            resolved = _edits._resolve_single_edge(
                transcript, anchor.get("phrase", ""),
                (row.get("params") or {}).get("edge"))
            if resolved is not None:
                params = dict(row.get("params") or {},
                              recorded_edge=resolved)
                row = dict(row, params=params)
    from library.tools.declaration_keys import (
        read_entries, write_entries)
    store_key = "edit_ledger"
    base, _envelope = read_entries(project_folder, store_key)
    mine = dict(base)
    key = _ledger_key(row)
    action = "superseded" if key in base else "recorded"
    if row in [entry for entry in base.values()]:
        raise EditLedgerError(
            f"already in force: {describe_rows([row])[0]} - recording "
            f"it again would list the same decision twice.")
    mine[key] = row
    write_entries(project_folder, store_key, mine, base,
                  envelope={"version": EDIT_LEDGER_VERSION})
    return row, action


def describe_rows(rows: list) -> list:
    """One plain-language line per row, in the requester's own words."""
    lines = []
    for number, row in enumerate(rows or [], start=1):
        op = row.get("op")
        anchor = row.get("anchor") or {}
        phrase = anchor.get("phrase", "") if anchor.get("kind") \
            == "words" else "the whole reel"
        scope = row.get("reel")
        where = f" on reel {scope!r}" if scope else ""
        reason = (row.get("reason") or "").strip()
        params = row.get("params") or {}
        stated = row.get("stated_by", "")
        if op == "voice_isolation":
            lines.append(
                f"{number}. Isolate: voice isolation on audio"
                f"{params.get('track')} at {params.get('amount')}"
                f"{where} ({stated}) - {reason}")
        elif op == "clip_lut":
            lines.append(
                f"{number}. Grade: LUT {params.get('lut')!r} on node "
                f"{params.get('node', 1)} wherever the speech says "
                f"{phrase!r}{where} ({stated}) - {reason}")
        elif op == "transform_override":
            lines.append(
                f"{number}. Framing: wherever the speech says "
                f"{phrase!r}, {params.get('property')} holds "
                f"{params.get('value')}{where} ({stated}) - {reason}")
        elif op == "span_retime":
            lines.append(
                f"{number}. Trim: the {params.get('edge')} of the span "
                f"speaking {phrase!r} sits on those words' own edge"
                f"{where} ({stated}) - {reason}")
        elif op == "drop_fragment":
            lines.append(
                f"{number}. Removed: the passage saying {phrase!r} is "
                f"cut, and everything after it moves up"
                f"{where} ({stated}) - {reason}")
        elif op == "caption_fix":
            lines.append(
                f"{number}. Captions: wherever the speech says "
                f"{phrase!r}, the caption reads "
                f"{params.get('replacement', '')!r}{where} ({stated}) "
                f"- {reason}")
        elif op == "redraw_closer":
            lines.append(
                f"{number}. Redrawn: the closer opening on "
                f"{params.get('from_phrase', '')!r} now opens on "
                f"{phrase!r}, end fixed{where} ({stated}) - {reason}")
        else:
            lines.append(f"{number}. {op}: {phrase!r}{where} "
                         f"({stated}) - {reason}")
    for line in lines:
        print(line)
    return lines


# ── The merged view: one store, every existing applier ───────────────

def project_onto_captain_edits(rows: list) -> list:
    """Ledger rows of the five plan-level kinds, as `captain_edits`
    edits - so `captain_edits.load_edits` reads one merged view and
    every existing applier (mesh_spine drops, caption fixes, closer
    redraws, transform holds, span trims) replays them with no second
    implementation. Hands ops and carriers project to nothing: they
    replay (or wait) here, never there."""
    edits = []
    for row in rows or []:
        op = row.get("op")
        if op not in PROJECTED_OPS:
            continue
        anchor = row.get("anchor") or {}
        edit = {"kind": op,
                "anchor_phrase": anchor.get("phrase", ""),
                "reason": (
                    f"[{row.get('stated_by', '')}] "
                    f"{row.get('reason', '')}".strip())}
        params = row.get("params") or {}
        if op == "transform_override":
            edit["property"] = params.get("property")
            edit["value"] = params.get("value")
            if row.get("reel") is not None:
                edit["reel"] = row.get("reel")
        elif op == "span_retime":
            edit["edge"] = params.get("edge")
            if params.get("recorded_edge") is not None:
                edit["recorded_edge"] = params.get("recorded_edge")
        elif op == "caption_fix":
            edit["replacement"] = params.get("replacement")
        elif op == "redraw_closer":
            edit["from_phrase"] = params.get("from_phrase")
        edits.append(edit)
    return edits


# ── Replay: hands rows onto the live timeline, after the build ──────

def _row_name(row: dict) -> str:
    """A row reported BY NAME: op, reel, anchor - never a silent drop."""
    anchor = row.get("anchor") or {}
    where = (anchor.get("phrase", "") if anchor.get("kind") == "words"
             else "whole reel")
    reel = row.get("reel") or "every reel"
    return f"{row.get('op')} on {reel} at {where!r}"


def report_unreplayable(records: list) -> list:
    """A row the build could not replay is LOUD: stderr, every record,
    every rebuild. A silently dropped row is the K3 defect back again -
    the hands edit painted over with nothing said."""
    lines = []
    for record in records or []:
        line = (f"UNREPLAYABLE LEDGER ROW: "
                f"{record.get('name', '')} - "
                f"{record.get('reason', '')}".strip())
        print(line, file=sys.stderr)
        lines.append(line)
    return lines


def match_clip_lut_rows(spans: list, transcript: dict, rows: list,
                        reel_name: str = "") -> tuple:
    """Which placed spans speak each ledger LUT row's anchor. Returns
    `(matched, unreplayable)`.

    `spans` are the placed picture spans in play order, each carrying
    its master-transcript range as `span["master"]` - the same
    containment `captain_edits.match_transform_overrides` uses, so a
    rebuild that re-cuts one shot into two keeps both under the grade
    rather than splitting it silently. A row scoped to another reel
    is routine (every ledger row is matched against every reel);
    an anchor spoken nowhere is LOST and says so by name.
    """
    from library.tools import captain_edits as _edits

    lut_rows = [row for row in (rows or [])
                if row.get("op") == "clip_lut"]
    matched, unreplayable = [], []
    stream = _edits._word_stream(transcript or {})
    for row in lut_rows:
        anchor = (row.get("anchor") or {}).get("phrase", "")
        scope = row.get("reel")
        params = row.get("params") or {}
        if scope is not None and not _edits._reel_in_scope(
                scope, reel_name or ""):
            unreplayable.append({
                "name": _row_name(row), "scope": "reel",
                "reason": (f"STALE - not on this reel: scoped to reel "
                           f"{scope!r} and this build is "
                           f"{reel_name or '(no reel named)'!r}. "
                           f"Expected on every build of a reel the "
                           f"decision is not about.")})
            continue
        anchor_tokens = _edits._tokens(anchor)
        hits = []
        for index, span in enumerate(spans or []):
            master = (span.get("master") if isinstance(span, dict)
                      else None)
            if not master:
                continue
            try:
                span_start, span_end = float(master[0]), float(master[1])
            except (TypeError, ValueError, IndexError):
                continue
            tokens = _edits._span_word_tokens(transcript, span_start,
                                              span_end)
            if _edits._contains_run(tokens, anchor_tokens):
                hits.append(index)
        if not hits:
            spoken = bool(_edits._run_starts(stream, anchor))
            if spoken:
                reason = (f"STALE - not on this reel: {anchor!r} is "
                          f"spoken in the transcript but in no span "
                          f"THIS reel placed, so it grades nothing "
                          f"here. Expected on every build of a reel "
                          f"the decision is not about.")
                scope_name = "reel"
            else:
                reason = (f"STALE - LOST: {anchor!r} is spoken NOWHERE "
                          f"in the measured transcript, so no rebuild "
                          f"of any reel can grade it again - the LUT "
                          f"is gone and the engine's own grade plays "
                          f"in its place. The passage was reworded or "
                          f"re-transcribed. Re-record it against the "
                          f"words now spoken.")
                scope_name = "transcript"
            unreplayable.append({"name": _row_name(row),
                                 "scope": scope_name, "reason": reason})
            continue
        for index in hits:
            matched.append({"span_index": index,
                            "lut": params.get("lut"),
                            "node": params.get("node", 1),
                            "anchor_phrase": anchor,
                            "reason": row.get("reason", "")})
    return matched, unreplayable


def apply_lut_to_item(item, lut: str, node: int = 1) -> dict:
    """Set one item's node LUT, judged by `GetLUT` on the same node -
    the same discipline as the `color lut` resolve-axi verb. Duck-typed
    so fakes judge the discipline in tests. Raises `EditLedgerError`
    naming what failed; a True that graded nothing is a refusal."""
    try:
        graph = item.GetNodeGraph()
    except AttributeError:
        raise EditLedgerError(
            "ledger LUT cannot replay: the item exposes no node graph "
            "on this build - nothing was written.")
    except Exception as exc:
        raise EditLedgerError(
            f"ledger LUT cannot replay: the node graph would not open "
            f"({exc}) - nothing was written.")
    if graph is None:
        raise EditLedgerError(
            "ledger LUT cannot replay: the item has no node graph - "
            "nothing was written.")
    try:
        count = graph.GetNumNodes()
    except Exception as exc:
        raise EditLedgerError(
            f"ledger LUT cannot replay: the node graph would not count "
            f"its nodes ({exc}) - nothing was written.")
    if node < 1 or (isinstance(count, int) and node > count):
        raise EditLedgerError(
            f"ledger LUT cannot replay: node {node} is outside "
            f"1..{count} - the Graph API has no AddNode, so only an "
            f"existing node can hold it. Nothing was written.")
    try:
        wrote = bool(graph.SetLUT(node, lut))
    except Exception as exc:
        raise EditLedgerError(
            f"ledger LUT cannot replay: SetLUT raised ({exc}) - "
            f"verify by hand.")
    if not wrote:
        raise EditLedgerError(
            f"ledger LUT cannot replay: SetLUT(node {node}, {lut!r}) "
            f"answered False - nothing was claimed.")
    try:
        back = graph.GetLUT(node)
    except Exception as exc:
        raise EditLedgerError(
            f"ledger LUT cannot replay: the LUT re-read raised ({exc}) "
            f"- verify by hand.")
    if not back or (back != lut
                    and not lut.endswith("/" + str(back))):
        raise EditLedgerError(
            f"ledger LUT cannot replay: SetLUT reports True and "
            f"re-reads {back!r} for {lut!r} - refusing to claim it.")
    return {"lut": lut, "node": node, "verified": "GetLUT re-read"}


def replay_on_timeline(name: str, rows: list, spans: list,
                       transcript: dict, timeline,
                       item_for_span=None,
                       reel_name: str = "") -> dict:
    """Replay this reel's hands rows onto its freshly built timeline.

    Runs AFTER the build's own passes - the held value is the
    requester's, never the plan's. `spans` are the placed picture
    spans in play order (with `master` ranges); `item_for_span` maps
    a span index to its live timeline item (or None). Voice-isolation
    rows ride `dialogue_cleanup.apply_voice_isolation` with its own
    read-back; LUT rows ride `apply_lut_to_item`. Returns
    `{"applied": [...], "unreplayable": [...]}` - and every
    unreplayable row is REPORTED BY NAME, never dropped silently.
    Carrier rows (`retime`, `grade`, `angle_plan`) have no replayer
    yet: they are reported by name as awaiting their rung, which is
    what keeps a stored decision from reading as a held one.
    """
    from library.tools import dialogue_cleanup as _dclean

    scoped = rows_for_reel(rows, reel_name or name)
    applied, unreplayable = [], []
    for row in scoped:
        op = row.get("op")
        if op == "voice_isolation":
            params = row.get("params") or {}
            try:
                _dclean.apply_voice_isolation(
                    timeline, int(params.get("track", 1)),
                    int(params.get("amount", 0)))
            except Exception as exc:
                unreplayable.append({
                    "name": _row_name(row), "scope": "timeline",
                    "reason": f"could not replay: {exc}"})
                continue
            applied.append({"name": _row_name(row), "op": op,
                            "track": params.get("track"),
                            "amount": params.get("amount")})
        elif op == "clip_lut":
            continue  # matched below, per span
        elif op in PROJECTED_OPS:
            continue  # the existing appliers hold these, never here
        elif op in CARRIER_OPS:
            unreplayable.append({
                "name": _row_name(row), "scope": "rung",
                "reason": (f"no replayer yet: {op} is carried for a "
                           f"later rung and this build holds nothing "
                           f"for it. Stored, reported, not applied.")})
        else:  # pragma: no cover - validation refuses unknown ops
            unreplayable.append({
                "name": _row_name(row), "scope": "op",
                "reason": f"unknown op {op!r} - nothing holds it."})
    matched, stale = match_clip_lut_rows(spans, transcript, scoped,
                                         reel_name or name)
    # Rows scoped elsewhere already reported inside the matcher as
    # routine reel-scope; only the transcript-lost kind is new here -
    # both arrive named, so report them all the same way.
    unreplayable.extend(stale)
    resolve_item = item_for_span or (lambda _index: None)
    for record in matched:
        item = resolve_item(record["span_index"])
        if item is None:
            unreplayable.append({
                "name": (f"clip_lut at "
                         f"{record['anchor_phrase']!r}"),
                "scope": "timeline",
                "reason": ("could not replay: no timeline item for "
                           "the matched span - the placement moved "
                           "under the grade.")})
            continue
        try:
            apply_lut_to_item(item, record["lut"],
                              record["node"])
        except EditLedgerError as exc:
            unreplayable.append({
                "name": (f"clip_lut at "
                         f"{record['anchor_phrase']!r}"),
                "scope": "timeline",
                "reason": f"could not replay: {exc}"})
            continue
        applied.append({"name": (f"clip_lut at "
                                 f"{record['anchor_phrase']!r}"),
                        "op": "clip_lut",
                        "lut": record["lut"], "node": record["node"]})
    report_unreplayable(unreplayable)
    return {"applied": applied, "unreplayable": unreplayable}


def main(argv=None) -> int:
    import sys as _sys

    args = list(_sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python3 -m library.tools.edit_ledger "
              "<project_folder> [list]", file=_sys.stderr)
        return 2
    project_folder = args[0]
    try:
        rows = load_rows(project_folder)
    except EditLedgerError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    if not rows:
        print(f"No edit-ledger rows in {project_folder}/external/.")
        return 0
    describe_rows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
