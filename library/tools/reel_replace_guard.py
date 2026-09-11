"""A replace is a diff, and the diff refuses (issue #925).

A build that REPLACES a timeline is judged only against its own PLAN, so
no gate can fail on a feature the plan does not name. Two rebuilds
proved it in one day: a `--only-reel` rebuild dropped the 24-frame
Akshita reaction cutaway (V1 3 clips -> 2, hole closed, conformance
clean), and a build from a tree with no Remotion `node_modules` dropped
the whole V5 'Semantic' row (4 items -> absent, conformance clean).

So `promote_staged_reels` - the ONE place a captain-visible timeline is
replaced - diffs the incoming timeline against the one it retires, LIVE
AGAINST LIVE, before the first rename. The plan is the thing that is
wrong, so it is neither side of the comparison.

What refuses
------------
Per final timeline, per row (keyed `"<media>:<track name>"`, e.g.
`"video:Semantic"` - indices shift when rows come and go, names do
not):

- a row of the retiring timeline that the incoming one does not have
  at all (a whole feature class gone);
- a row the incoming one holds FEWER items on, unless it reads as a
  JOIN (below).

Frame totals are REPORTED per row, and join the trigger as the join
half: a shortened cut holds the same items over fewer frames, and
that must pass. Grain removed and a j-cut deleted likewise move no
item count, which is why a count trigger is not a nuisance for the
legitimate reductions the captain actually makes.

A join is not a loss
--------------------
Keep insistence `lc-0004` withdrew a take cut, so two adjacent Craig
clips placed as two items became one item placed over MORE frames -
and the count trigger read the merge as content lost. A count drop
with the frames kept or grown is a merge, not a deletion, PROVIDED
every retired item's name still plays on the incoming row: names are
the source-coverage proxy this snapshot carries (name plus span is
already the diff's whole identity - `_item_identity` - and reel rows
carry source names, so two halves of one source share one name while
a dropped cover like `LC4932 cover` has one nothing else carries).

So a row with fewer incoming items is a JOIN, and passes undeclared,
exactly when the incoming row holds at least as many frames AND
every distinct retired name still occurs among the incoming items.
Both halves are load-bearing, and the round's own cutaway is the
counter-example that proves frames alone are not enough: that loss
went 3 items to 2 over EQUAL frames (hole closed), so a frames-only
rule would have waved a real deletion through. Names refuse it.

What this still cannot see, stated plainly: a substitution that keeps
every name and grows the frames - one same-named item's seconds
swapped for another's - passes. Timeline spans cannot tell those
seconds apart; the guard never saw source ranges, before or now, so
this is the standing limit of a span-based diff, not a new hole.
A same-name substitution that SHRINKS the frames still refuses on
the count drop without join cover.

What a declared reduction looks like
------------------------------------
A reduction the build intends is declared by ROW, never by blanket: the
caller names the specific row(s) that may shrink. `allow_drops` is a
mapping of final timeline name to the row keys allowed to shrink on
it, e.g. `{"Reel 09 - ... (final)": ["video:Semantic"]}`. A bare track
name (`"Semantic"`) matches that name on any media type; the canonical
`"video:Semantic"` form is what the refusal message prints, so it can
be copied back verbatim.

Raw caller specs (`parse_specs`) add one convenience shape,
`"FINAL::video:Semantic"` for one reel and `"video:Semantic"` for every
reel the invocation promotes, which is what `build-reels --allow-drop`
collects. There is deliberately no "allow everything" value: a blanket
override is the same as no guard.

Fail closed
-----------
A retiring timeline that cannot be read REFUSES rather than passes - a
guard that fails open reads as protection. So does an incoming staging
that cannot be read: promoting what cannot be judged is the defect.
Fresh builds (no timeline under the final name yet) skip the diff for
that reel - there is nothing being replaced.
"""

from __future__ import annotations


MEDIA_TYPES = ("video", "audio")

#: How many missing items a refusal names inline per row. The report the
#: promote result carries names every one; the message stays readable.
MISSING_SHOWN = 5


class ReplaceGuardUnreadable(RuntimeError):
    """A timeline in the comparison could not be read, and this says which."""


class ReplaceGuardRefused(RuntimeError):
    """The incoming timeline carries less than the one it would replace."""


def row_key(media_type: str, track_name: str) -> str:
    """The canonical identity of a row: `"video:Semantic"`."""
    return f"{media_type}:{track_name}"


def _item_identity(item) -> dict:
    """What an item IS for the diff: its name and its record span.

    Unique ids are useless across timelines - the staging's items are
    different objects by construction - so identity is name plus span.
    """
    try:
        name = item.GetName()
        start = item.GetStart()
        end = item.GetEnd()
        duration = item.GetDuration()
    except Exception as unreadable:
        raise ReplaceGuardUnreadable(
            f"a timeline item could not be read ({unreadable}); refusing "
            f"rather than diffing half a row.") from unreadable
    return {"name": name, "start": start, "end": end,
            "duration": duration}


def snapshot_timeline(timeline, timeline_name: str,
                      side: str = "retiring") -> dict:
    """Every row of a live timeline: its items and their spans.

    Raises `ReplaceGuardUnreadable` on ANY read failure - a half-read
    timeline must refuse, never pass on the rows that happened to read.
    `side` names which half of the comparison this is (`"retiring"` or
    `"staged"`), so the refusal says what could not be seen.
    """
    rows = {}
    try:
        for media_type in MEDIA_TYPES:
            count = timeline.GetTrackCount(media_type) or 0
            for index in range(1, count + 1):
                name = timeline.GetTrackName(media_type, index) or ""
                if not name:
                    name = f"#{index}"
                key = row_key(media_type, name)
                items = timeline.GetItemListInTrack(media_type, index) or []
                identities = [_item_identity(item) for item in items]
                rows[key] = {
                    "media_type": media_type,
                    "index": index,
                    "name": name,
                    "items": identities,
                    "count": len(identities),
                    "frames": sum(
                        (entry["duration"] or 0) for entry in identities),
                }
    except ReplaceGuardUnreadable:
        raise
    except Exception as unreadable:
        raise ReplaceGuardUnreadable(
            f"the {side} timeline {timeline_name!r} could not be read "
            f"({unreadable}); the replace guard refuses rather than "
            f"promoting over what it cannot see.") from unreadable
    return rows


def _span(entry: dict) -> str:
    return f"@{entry['start']}..{entry['end']} ({entry['duration']}f)"


def _match_key(entry: dict) -> tuple:
    return (entry["name"], entry["start"], entry["end"])


def diff_rows(retired: dict, incoming: dict) -> list:
    """One verdict per retired row, in retired row order.

    Rows only the incoming timeline has are new features, not losses,
    so they appear nowhere here. Frame totals ride along for the
    report; only item counts and row absence refuse.
    """
    verdicts = []
    for key, old in retired.items():
        new = incoming.get(key)
        if new is None:
            verdicts.append({
                "key": key,
                "media_type": old["media_type"],
                "index": old["index"],
                "name": old["name"],
                "retired_count": old["count"],
                "incoming_count": None,
                "retired_frames": old["frames"],
                "incoming_frames": None,
                "missing": list(old["items"]),
                "lost_row": True,
            })
            continue
        new_keys = {_match_key(entry) for entry in new["items"]}
        verdicts.append({
            "key": key,
            "media_type": old["media_type"],
            "index": old["index"],
            "name": old["name"],
            "retired_count": old["count"],
            "incoming_count": new["count"],
            "retired_frames": old["frames"],
            "incoming_frames": new["frames"],
            "missing": [entry for entry in old["items"]
                        if _match_key(entry) not in new_keys],
            "lost_row": False,
        })
    return verdicts


def _declared(key: str, name: str, allowed: set) -> bool:
    """Did the caller declare this row's reduction, canonically or bare."""
    return key in allowed or name in allowed


def _is_join(old: dict, new: dict) -> bool:
    """Whether a row's item-count drop reads as a merge, not a deletion.

    `old` and `new` are one row's snapshot halves (`snapshot_timeline`
    rows: `items` of name/start/end/duration, plus `count` and
    `frames`). A withdrawn take cut merges two adjacent placements
    into one continuous one: fewer items, at least as many frames
    (the restored seconds are back in), every retired name still on
    the row. All three must hold - the cutaway loss this guard first
    caught proves frames alone are not sufficient (3 items to 2 over
    equal frames, with `LC4932 cover` gone), and names alone are not
    either (a shrunken same-named row keeps every name while losing
    seconds).
    """
    if new["count"] >= old["count"]:
        return False
    if (new["frames"] or 0) < (old["frames"] or 0):
        return False
    incoming_names = {entry["name"] for entry in new["items"]}
    return all(entry["name"] in incoming_names
               for entry in old["items"])


def parse_specs(raw, finals) -> dict:
    """Raw caller specs to the per-final mapping the check reads.

    `raw` is a list of `"ROW"` (every reel this invocation promotes) or
    `"FINAL::ROW"` (that reel only) strings, or an already per-final
    mapping `{final: [rows]}`. Anything else is a caller bug and raises
    `ValueError` rather than reading as an empty declaration.
    """
    finals = list(finals or ())
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {final: {str(row) for row in (rows or ())}
                for final, rows in raw.items()}
    if isinstance(raw, str):
        raw = [raw]
    try:
        specs = list(raw)
    except TypeError:
        raise ValueError(
            f"allow_drops must be a list of 'ROW' / 'FINAL::ROW' specs "
            f"or a {{final: [rows]}} mapping, got {raw!r}.")
    global_rows, per_final = [], {}
    for spec in specs:
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError(
                f"allow_drops specs must be non-empty strings, got "
                f"{spec!r}. Declare the reduced row, e.g. "
                f"'video:Semantic'.")
        spec = spec.strip()
        if "::" in spec:
            final, _, row = spec.partition("::")
            final, row = final.strip(), row.strip()
            if not final or not row:
                raise ValueError(
                    f"allow_drops spec {spec!r} names no reel or no row. "
                    f"Write 'FINAL::ROW', e.g. "
                    f"'Reel 09 - moment (final)::video:Semantic'.")
            per_final.setdefault(final, set()).add(row)
        else:
            global_rows.append(spec)
    mapping = {final: set() for final in finals}
    for final in finals:
        mapping[final].update(global_rows)
        mapping[final].update(per_final.get(final, ()))
    for final, rows in per_final.items():
        mapping.setdefault(final, set()).update(rows)
    return mapping


def check_replacement(final: str, staging: str, retired: dict,
                      incoming: dict, allowed=None) -> dict:
    """Refuse by name when the incoming timeline holds less, or report.

    Returns the per-reel report: every retired row with its counts on
    both sides, what is missing where, and which declarations covered
    which reduction. Raises `ReplaceGuardRefused` naming the row, the
    counts and what is missing for the first undeclared loss - with the
    exact declaration that would proceed deliberately.
    """
    allowed = set(allowed or ())
    verdicts = diff_rows(retired, incoming)
    for verdict in verdicts:
        old, new = retired[verdict["key"]], incoming.get(verdict["key"])
        verdict["joined"] = bool(
            new is not None and not verdict["lost_row"] and _is_join(old, new))
    reduced = [verdict for verdict in verdicts
               if (verdict["lost_row"]
                   or verdict["incoming_count"] < verdict["retired_count"])
               and not verdict["joined"]]
    losses = [verdict for verdict in reduced
              if not _declared(verdict["key"], verdict["name"], allowed)]
    joined_keys = sorted({verdict["key"] for verdict in verdicts
                          if verdict["joined"]})
    covered = sorted({verdict["key"] for verdict in reduced
                      if _declared(verdict["key"], verdict["name"], allowed)})
    reduced_keys = ({verdict["key"] for verdict in reduced}
                    | {verdict["name"] for verdict in reduced})
    unused = sorted(key for key in allowed if key not in reduced_keys)
    report = {
        "final": final,
        "staging": staging,
        "rows": verdicts,
        "allowed": covered,
        "joined": joined_keys,
        "declared_unused": unused,
        "refused": bool(losses),
    }
    if not losses:
        return report
    lines = [(
        f"REFUSING to promote {final!r}: the staged rebuild carries less "
        f"than the approved timeline it would replace (built as "
        f"{staging!r}). Nothing was renamed; the approved timeline is "
        f"still in the project.")]
    for verdict in losses:
        label = (f"V{verdict['index']} {verdict['name']!r} "
                 f"({verdict['key']})")
        if verdict["lost_row"]:
            lines.append(
                f"  {label}: {verdict['retired_count']} item(s) -> row "
                f"absent (the whole feature class is gone; "
                f"{verdict['retired_frames']} frames).")
        else:
            lines.append(
                f"  {label}: {verdict['retired_count']} item(s) -> "
                f"{verdict['incoming_count']} "
                f"({verdict['retired_frames']}f -> "
                f"{verdict['incoming_frames']}f).")
        shown = verdict["missing"][:MISSING_SHOWN]
        for entry in shown:
            lines.append(
                f"    missing from the rebuild: "
                f"{entry['name']!r} {_span(entry)}.")
        hidden = len(verdict["missing"]) - len(shown)
        if hidden > 0:
            lines.append(f"    ... and {hidden} more (see replace_reports).")
    needed = sorted({verdict["key"] for verdict in losses})
    flags = " ".join(f"--allow-drop {key!r}" for key in needed)
    lines.append(
        f"To proceed deliberately, declare each reduced row: "
        f"`build-reels {flags}` "
        f"or `allow_drops={{{final!r}: {needed}}}`.")
    raise ReplaceGuardRefused("\n".join(lines))
