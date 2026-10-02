"""ROUNDS: which promoted reel versions belong together, and what changed
between two of them. Part of the version model
(`library/tools/versions/__init__.py`).

A round is stamped once per batch of the captain's feedback, across every
reel that batch touched - a version is per ROUND (captain, 2026-09-12).

A round is DISCOVERED, never declared
-------------------------------------
Nobody types a round number. The interleaving of the captain's asks and
firstmate's promotions IS the round boundary (`round_boundaries`,
`discover`):

    a new round opens at the first ASK that arrives after at least one
    promotion has landed since the current round opened.

There is no clock threshold. A pull that re-reads notes already asked
opens nothing (the ledger folds them onto one identity, so `first_asked`
does not move), and a second marker typed before anything was rebuilt
joins the batch it belongs to. Replies of ours never open a round: only
`feedback_ledger` `KIND_ASK` entries are read (`asks_of`). Round 1 is the
first cut and says so (`FIRST_ROUND_WHY`) rather than claiming an opener.

What a round holds
------------------
Per reel it touched: when it was promoted, the `built_at`/`built_with`
stamps `plan_provenance` captures at build time, and the ROW SNAPSHOT
(`reel_read.rows_of`) of what was promoted. The rows are stored here so
the record of what a round contained outlives the timeline it describes
(`reel_retirement` may collect the timeline). A rounds file that exists
and cannot be parsed raises `RoundsUnreadable`.

Stamped, or reconstructed - and the record says which
-----------------------------------------------------
`stamp_promotion` records a round at the moment of promotion, the only
moment `built_with` can be known (AGENTS.md 10.1: merge time is not build
time). `backfill` reconstructs earlier rounds from the committed timeline
snapshots in the project's own git repo (written by
`store.record_reel_promotion`), and marks every entry it makes
`SOURCE_RECONSTRUCTED` with no `built_with` at all - a stamp invented
after the fact is not a measurement.

Diffing two rounds
------------------
`diff_rounds` answers what changed between two rounds, off disk, with no
Resolve and no git. Nothing new is computed: it is
`reel_replace_guard.diff_rows` - the promotion guard's own diff - over two
STORED `rows_of` snapshots, and the row key (`"video:Semantic"`) is the
vocabulary the guard's refusals print, so a row named in a round diff and
in a promotion refusal is the same row, spelled the same way.

Per reel, per row it reports items and frames on each side and the items
present in one and not the other (`UNCHANGED`, `CHANGED`, `ADDED_ROW`,
`LOST_ROW`). A row whose spans match one for one, in order, is
`RERENDERED` (`_is_rerender`): same placements, new digest-named files.
A reel promoted in one round and not the other is reported as such.

It does NOT report WHY anything changed: the feedback the round was
opened by is printed beside the rows, and joining the two is the reader's
judgement. The standing limit is the guard's own: a substitution that
keeps every item name and span is invisible to a span-based diff.

`tests/test_version_rounds.py`.

The ruling, the measured rounds of the first project and the scout report
on the timeline differ that could not do this:
docs/evidence/rounds.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[3]) not in sys.path:      # repo root, direct execution
    sys.path.insert(0, str(_HERE.parents[3]))

from library.tools.versions import store  # noqa: E402

ROUNDS_FILENAME = "rounds.json"
ROUNDS_FORMAT = "rounds/1"

SOURCE_STAMPED = "stamped"
"""Written at promotion time, by the promotion itself."""

SOURCE_RECONSTRUCTED = "reconstructed"
"""Rebuilt afterwards from a committed snapshot. Carries no
`built_with`: which engine revision built a reel cannot be recovered
once the build is over, and filling it in would be an invention."""

FIRST_ROUND_WHY = "the first cut - no feedback preceded it"


class RoundsUnreadable(RuntimeError):
    """The rounds file exists but cannot be parsed, and this says so."""


# ── Where the record lives ───────────────────────────────────────

def rounds_path_for(project_folder) -> str:
    """`pipeline_output/review/rounds.json`.

    Beside the holds, the provenance and the committed snapshots - on
    `store.ALLOW_LIST`, so every round is in the
    project's own git history rather than only on one disk.
    """
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        ROUNDS_FILENAME)


def read_rounds(project_folder) -> dict:
    """The rounds document, or an empty one. Never invents a round."""
    path = rounds_path_for(project_folder)
    if not os.path.exists(path):
        return {"format": ROUNDS_FORMAT, "rounds": []}
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise RoundsUnreadable(
            f"{path} exists but could not be read ({unreadable}). "
            f"A version record that cannot be read must not be "
            f"silently replaced with an empty one - the rounds it "
            f"holds are the only account of what was built when.") \
            from unreadable
    if not isinstance(document, dict) or not isinstance(
            document.get("rounds"), list):
        raise RoundsUnreadable(
            f"{path} is not a rounds document ({ROUNDS_FORMAT}).")
    return document


def write_rounds(project_folder, document: Mapping) -> str:
    """Write the rounds document atomically. Returns its path."""
    return store.write_record(rounds_path_for(project_folder), document,
                              prefix=".rounds-")


# ── The boundary rule, as a pure function ────────────────────────

def round_boundaries(asks: Sequence[Mapping],
                     promotions: Sequence[str]) -> list:
    """Group asks into rounds against the promotions that answered them.

    `asks` is `[{"identity", "reel", "first_asked"}]` - the shape
    `feedback_ledger` entries already carry - and `promotions` is a list
    of ISO timestamps, one per reel promoted. Neither needs to be
    sorted.

    Returns `[{"round": N, "opened_at": iso, "opened_by": [identity...],
    "reels_asked": [reel...], "why": text}]`, always with round 1 first
    even when no feedback exists yet: the first cut is a version.

    The rule, and the whole of it: an ask opens a NEW round exactly when
    a promotion has landed at or after the current round opened and at
    or before that ask. No window, no threshold - a batch is held
    together by nothing having been rebuilt in between, which is the
    captain's own definition of a round.
    """
    ordered = sorted(
        ({"identity": str(a.get("identity") or ""),
          "reel": str(a.get("reel") or ""),
          "first_asked": str(a.get("first_asked") or "")}
         for a in asks or ()),
        key=lambda a: (a["first_asked"], a["identity"]))
    promoted = sorted(str(p) for p in (promotions or ()) if p)
    rounds = [{"round": 1, "opened_at": "", "opened_by": [],
               "reels_asked": [], "why": FIRST_ROUND_WHY}]
    for ask in ordered:
        current = rounds[-1]
        since = current["opened_at"]
        built = any(since <= moment <= ask["first_asked"]
                    for moment in promoted)
        if built and (current["opened_by"] or current["round"] == 1):
            rounds.append({
                "round": current["round"] + 1,
                "opened_at": ask["first_asked"],
                "opened_by": [], "reels_asked": [],
                "why": "opened by the captain's feedback, after a "
                       "build answered the previous round"})
            current = rounds[-1]
        if not current["opened_at"]:
            current["opened_at"] = ask["first_asked"]
            if current["round"] == 1:
                current["why"] = ("opened by the captain's feedback, "
                                  "before anything was rebuilt")
        current["opened_by"].append(ask["identity"])
        if ask["reel"] and ask["reel"] not in current["reels_asked"]:
            current["reels_asked"].append(ask["reel"])
    return rounds


def asks_of(project_folder) -> list:
    """Every ASK in this project's feedback ledger, replies excluded.

    Read through `feedback_ledger.collect` rather than the pull files,
    so one note asked on three pulls is one ask with one `first_asked` -
    the durable identity is what makes a batch a batch.
    """
    from library.tools import feedback_ledger

    try:
        entries = feedback_ledger.collect(str(project_folder))
    except Exception:                                       # noqa: BLE001
        return []
    return [{"identity": entry.identity, "reel": entry.reel,
             "first_asked": entry.first_asked}
            for entry in entries.values()
            if entry.kind == feedback_ledger.KIND_ASK]


def promotions_of(document: Mapping) -> list:
    """Every promotion moment the rounds document already records."""
    moments = []
    for entry in (document or {}).get("rounds") or ():
        for reel in (entry.get("reels") or {}).values():
            if reel.get("promoted_at"):
                moments.append(str(reel["promoted_at"]))
    return sorted(moments)


def discover(project_folder, extra_promotions: Sequence[str] = ()) -> list:
    """The rounds of this project, boundaries recomputed, reels kept.

    The boundaries come from the ask ledger and the promotions already
    recorded; the per-reel payload of each surviving round is carried
    across by round number, so recomputing never loses a stamp. A round
    whose number no longer exists (feedback removed from every pull it
    was ever on) keeps its reels on the nearest lower round rather than
    dropping them - a promotion that happened is a fact, and the
    boundary is the thing that was re-derived.
    """
    document = read_rounds(project_folder)
    promoted = promotions_of(document) + [
        str(m) for m in (extra_promotions or ()) if m]
    boundaries = round_boundaries(asks_of(project_folder), promoted)
    existing = {entry.get("round"): entry
                for entry in (document.get("rounds") or ())}
    highest = max((b["round"] for b in boundaries), default=1)
    for entry in boundaries:
        prior = existing.get(entry["round"]) or {}
        entry["reels"] = dict(prior.get("reels") or {})
    for number, prior in sorted(existing.items(),
                                key=lambda pair: pair[0] or 0):
        if number in {b["round"] for b in boundaries}:
            continue
        target = max((b for b in boundaries
                      if (b["round"] or 0) <= (number or 0)),
                     key=lambda b: b["round"], default=boundaries[-1])
        for name, reel in (prior.get("reels") or {}).items():
            target["reels"].setdefault(name, reel)
    del highest
    return boundaries


def open_round(project_folder, extra_promotions: Sequence[str] = ()) -> dict:
    """The round a promotion happening NOW belongs to: the last one."""
    return discover(project_folder, extra_promotions)[-1]


# ── Stamping a promotion ─────────────────────────────────────────

def stamp_promotion(project_folder, rows_by_final: Mapping,
                    provenance: Mapping | None = None,
                    promoted_at: str | None = None,
                    choices: Mapping | None = None) -> dict:
    """Record this promotion against the round it belongs to.

    `rows_by_final` is `{final timeline name: rows}` in
    `reel_read.rows_of` shape - the promotion already has them, because
    the replace guard read the incoming timeline to diff it. Passing
    them in rather than re-reading is what keeps this off Resolve.

    `provenance` is the `plan_provenance` document, read for this reel's
    `built_at_reels` / `built_with` entries. Absent entries are recorded
    absent, never filled in.

    `choices` is `{final: {"chosen", "chosen_timeline", "over", "why"}}`
    when this promotion came from CHOOSING between two versions of a
    reel (`variants.choose`). Recorded against the reel
    it decided, because the choice and the version it produced are one
    fact: a round that says a reel changed and cannot say the change
    was a decision between two watched cuts has lost the only part a
    human remembers.

    Returns the round record that was written. Raises nothing a caller
    must catch on the normal path; the caller wraps it, because a
    version record that fails a build is worse than no version record.
    """
    from datetime import datetime, timezone

    moment = promoted_at or datetime.now(timezone.utc).isoformat()
    document = read_rounds(project_folder)
    boundaries = discover(project_folder, [moment])
    current = boundaries[-1]
    built_at = dict((provenance or {}).get("built_at_reels") or {})
    built_with = dict((provenance or {}).get("built_with") or {})
    for final, rows in (rows_by_final or {}).items():
        entry = {
            "promoted_at": moment,
            "built_at": built_at.get(final, ""),
            "built_with": built_with.get(final, ""),
            "rows": rows,
            "source": SOURCE_STAMPED,
        }
        choice = (choices or {}).get(final)
        if choice:
            entry["choice"] = dict(choice)
        current["reels"][final] = entry
    document["format"] = ROUNDS_FORMAT
    document["rounds"] = boundaries
    write_rounds(project_folder, document)
    return current


def digest_rows(rows: Mapping) -> str:
    """A digest of one reel's rows: what the picture carries, exactly.

    The same shape `plan_provenance.caption_content_hash` takes. Used by
    `reel_signoff` to record WHICH build was signed off, so a sign-off
    can say whether the timeline in front of the captain is still the
    one they approved.
    """
    import hashlib

    canonical = json.dumps(rows or {}, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ── Reconstructing the rounds that predate the stamp ─────────────

SNAPSHOT_SUFFIX = ".timeline.json"
SNAPSHOT_DIR = "pipeline_output/review"


def committed_snapshots(project_folder) -> list:
    """`[{"commit", "when", "paths"}]` for every commit that wrote a
    timeline snapshot, oldest first.

    `store.record_reel_promotion` writes one
    `<timeline>.timeline.json` per promoted reel and commits it, so a
    commit touching those files IS a promotion - which is what makes the
    rounds that predate this module recoverable at all. A project with
    no git repo has none, and says so by returning nothing.
    """
    result = store.git(project_folder, "log", "--reverse", "--name-only",
                  "--format=%x00%H%x1f%aI", "--", SNAPSHOT_DIR)
    if result.returncode != 0:
        return []
    commits = []
    for block in (result.stdout or "").split("\x00"):
        block = block.strip("\n")
        if not block:
            continue
        head, _, body = block.partition("\n")
        commit, _, when = head.partition("\x1f")
        paths = [line.strip() for line in body.splitlines()
                 if line.strip().endswith(SNAPSHOT_SUFFIX)]
        if paths:
            commits.append({"commit": commit, "when": when, "paths": paths})
    return commits


def _timeline_name_at(project_folder, commit: str, path: str) -> str:
    """The timeline's own name, read out of the committed snapshot.

    Never derived from the filename: `record_reel_promotion` replaces
    every character outside `[A-Za-z0-9-_.]` with an underscore, so the
    file stem cannot be turned back into `Reel 09 - ... (final)`. The
    document carries the real name under `metadata.name`.
    """
    blob = store.git(project_folder, "show", f"{commit}:{path}")
    if blob.returncode != 0:
        return ""
    try:
        document = json.loads(blob.stdout)
    except ValueError:
        return ""
    return str((document.get("metadata") or {}).get("name") or "")


def _rows_at(project_folder, commit: str, path: str):
    from library.tools import reel_read

    blob = store.git(project_folder, "show", f"{commit}:{path}")
    if blob.returncode != 0:
        return None, ""
    try:
        document = json.loads(blob.stdout)
    except ValueError:
        return None, ""
    name = str((document.get("metadata") or {}).get("name") or "")
    try:
        return reel_read.rows_of(document), name
    except Exception:                                       # noqa: BLE001
        return None, name


def backfill(project_folder) -> dict:
    """Reconstruct the rounds that predate stamping, from git.

    Boundaries come from the same rule `discover` uses, with the
    promotions taken from the snapshot commits. Per round, per reel, the
    rows are read from the LAST commit in that round that wrote that
    reel's snapshot - what the reel looked like when the round closed,
    which is what a round diff compares.

    Every entry is marked `SOURCE_RECONSTRUCTED` and carries no
    `built_with`. An entry already `SOURCE_STAMPED` is never overwritten:
    a measurement outranks a reconstruction.

    Returns `{"rounds": N, "reels": N, "commits": N}`; writes nothing
    when there is nothing to reconstruct.
    """
    commits = committed_snapshots(project_folder)
    if not commits:
        return {"rounds": 0, "reels": 0, "commits": 0,
                "why": "no committed timeline snapshot in this project"}
    document = read_rounds(project_folder)
    boundaries = round_boundaries(
        asks_of(project_folder),
        promotions_of(document) + [c["when"] for c in commits])
    existing = {entry.get("round"): entry
                for entry in (document.get("rounds") or ())}
    for entry in boundaries:
        entry["reels"] = dict((existing.get(entry["round"]) or {})
                              .get("reels") or {})

    def round_for(when: str) -> dict:
        chosen = boundaries[0]
        for entry in boundaries:
            if entry["opened_at"] and entry["opened_at"] <= when:
                chosen = entry
        return chosen

    reels = 0
    for commit in commits:
        entry = round_for(commit["when"])
        for path in commit["paths"]:
            rows, name = _rows_at(project_folder, commit["commit"], path)
            if rows is None or not name:
                continue
            recorded = entry["reels"].get(name)
            if recorded and recorded.get("source") == SOURCE_STAMPED:
                continue
            entry["reels"][name] = {
                "promoted_at": commit["when"],
                "built_at": "",
                "built_with": "",
                "rows": rows,
                "source": SOURCE_RECONSTRUCTED,
                "commit": commit["commit"],
                "snapshot": path,
            }
            reels += 1
    document["format"] = ROUNDS_FORMAT
    document["rounds"] = boundaries
    write_rounds(project_folder, document)
    return {"rounds": len(boundaries), "reels": reels,
            "commits": len(commits)}


# ── Reading it back ──────────────────────────────────────────────

def round_by_number(project_folder, number: int) -> dict | None:
    for entry in read_rounds(project_folder).get("rounds") or ():
        if entry.get("round") == number:
            return entry
    return None


def render_rounds(document: Mapping) -> str:
    """The rounds, in the sentences the captain has to read."""
    rounds = list((document or {}).get("rounds") or ())
    if not rounds:
        return ("No round recorded yet. A round is stamped when a build "
                "promotes; `round-diff --backfill` reconstructs the "
                "rounds already in this project's git history.")
    lines = [f"── {len(rounds)} round(s) ──"]
    for entry in rounds:
        opened = entry.get("opened_at") or "(the first build)"
        lines.append(
            f"  Round {entry.get('round')}: opened {opened} - "
            f"{entry.get('why', '')}")
        asked = entry.get("reels_asked") or []
        if asked:
            lines.append(
                f"    {len(entry.get('opened_by') or [])} ask(s) on "
                f"{len(asked)} reel(s): {', '.join(asked)}")
        reels = entry.get("reels") or {}
        if not reels:
            lines.append("    no reel was promoted in this round")
            continue
        stamped = sum(1 for r in reels.values()
                      if r.get("source") == SOURCE_STAMPED)
        lines.append(
            f"    {len(reels)} reel(s) promoted "
            f"({stamped} stamped, {len(reels) - stamped} reconstructed):")
        for name in sorted(reels):
            reel = reels[name]
            rows = reel.get("rows") or {}
            items = sum((row.get("count") or 0) for row in rows.values())
            lines.append(
                f"      {name} - {len(rows)} row(s), {items} item(s), "
                f"promoted {reel.get('promoted_at', '')}")
            choice = reel.get("choice") or {}
            if choice:
                over = ", ".join(choice.get("over") or ()) or "nothing"
                lines.append(
                    f"        CHOSE {choice.get('chosen', '')} over "
                    f"{over}: {choice.get('why', '')}")
    return "\n".join(lines)


# ── Diffing two reel versions ────────────────────────────────────

UNCHANGED = "unchanged"
CHANGED = "changed"
RERENDERED = "re-rendered"
ADDED_ROW = "row added"
LOST_ROW = "row gone"


def _spans(row) -> list:
    return [(item["start"], item["end"], item["duration"])
            for item in (row or {}).get("items") or ()]


def _is_rerender(earlier, later) -> bool:
    """Same pictures, same places, different files.

    Every overlay row in this pipeline is a rendered `.mov` whose
    filename carries a content digest (`subtitle_segment_id`,
    `motion_graphics_plan`), so a rebuild that changed nothing about
    the plan still lands 34 new filenames at 34 identical spans - and
    a plain name diff reports that as 34 items gone and 34 new. Every
    round diff would then be mostly noise, and noise is how a real
    change goes unread.

    A row is a RE-RENDER exactly when the spans match one for one, in
    order. That is a measurement of the two snapshots, not a reading of
    the filenames: no naming scheme is parsed, so a row of camera
    footage that genuinely did not move reports the same way, correctly.
    Whether the PIXELS differ is beyond what a span snapshot can see -
    the diff says the placement is identical and says nothing it cannot
    measure.
    """
    return bool(_spans(earlier)) and _spans(earlier) == _spans(later)


def diff_reel(earlier_rows: Mapping, later_rows: Mapping) -> dict:
    """One reel, two rounds: every row that differs, and how.

    `reel_replace_guard.diff_rows` answers for every row the EARLIER
    side has - rows only the later side has are new features, which the
    guard rightly ignores because it is asked about loss. A round diff
    is asked about CHANGE, so the rows only the later side carries are
    added back here rather than a second differ being written.
    """
    from library.tools import reel_replace_guard as guard

    verdicts = guard.diff_rows(dict(earlier_rows or {}),
                               dict(later_rows or {}))
    rows = []
    for verdict in verdicts:
        later = (later_rows or {}).get(verdict["key"]) or {}
        earlier = (earlier_rows or {}).get(verdict["key"]) or {}
        if verdict["lost_row"]:
            state = LOST_ROW
        elif (verdict["retired_count"] == verdict["incoming_count"]
              and verdict["retired_frames"] == verdict["incoming_frames"]
              and not verdict["missing"]):
            state = UNCHANGED
        elif _is_rerender(earlier, later):
            state = RERENDERED
        else:
            state = CHANGED
        gained = []
        if state != LOST_ROW:
            earlier_items = {
                (item["name"], item["start"], item["end"])
                for item in ((earlier_rows or {})
                             .get(verdict["key"], {}).get("items") or ())}
            gained = [item for item in (later.get("items") or ())
                      if (item["name"], item["start"], item["end"])
                      not in earlier_items]
        rows.append({
            "key": verdict["key"],
            "name": verdict["name"],
            "state": state,
            "earlier_count": verdict["retired_count"],
            "later_count": verdict["incoming_count"],
            "earlier_frames": verdict["retired_frames"],
            "later_frames": verdict["incoming_frames"],
            "gone": list(verdict["missing"]),
            "gained": gained,
        })
    for key, row in sorted((later_rows or {}).items()):
        if key in (earlier_rows or {}):
            continue
        rows.append({
            "key": key, "name": row.get("name", ""), "state": ADDED_ROW,
            "earlier_count": None, "later_count": row.get("count"),
            "earlier_frames": None, "later_frames": row.get("frames"),
            "gone": [], "gained": list(row.get("items") or ()),
        })
    return {"rows": rows,
            "changed": [row for row in rows
                        if row["state"] not in (UNCHANGED, RERENDERED)],
            "rerendered": [row for row in rows
                           if row["state"] == RERENDERED]}


def diff_rounds(project_folder, earlier: int, later: int) -> dict:
    """Everything that changed between two rounds of this project.

    Both rounds must exist; a missing one RAISES rather than diffing
    against nothing, because an empty side reads exactly like a round
    that removed everything.
    """
    document = read_rounds(project_folder)
    by_number = {entry.get("round"): entry
                 for entry in document.get("rounds") or ()}
    for number in (earlier, later):
        if number not in by_number:
            raise ValueError(
                f"round {number} is not recorded in this project "
                f"(recorded: {sorted(n for n in by_number if n)}). "
                f"`round-diff --backfill` reconstructs the rounds "
                f"already in the project's git history.")
    first, second = by_number[earlier], by_number[later]
    first_reels = first.get("reels") or {}
    second_reels = second.get("reels") or {}
    reels = {}
    for name in sorted(set(first_reels) | set(second_reels)):
        if name not in first_reels:
            reels[name] = {"state": "first built in this round",
                           "rows": [], "changed": []}
            continue
        if name not in second_reels:
            reels[name] = {"state": "not rebuilt in this round",
                           "rows": [], "changed": []}
            continue
        entry = diff_reel(first_reels[name].get("rows") or {},
                          second_reels[name].get("rows") or {})
        entry["state"] = (UNCHANGED if not entry["changed"]
                          else "rebuilt and changed")
        entry["built_with"] = second_reels[name].get("built_with", "")
        entry["promoted_at"] = second_reels[name].get("promoted_at", "")
        reels[name] = entry
    return {"earlier": first, "later": second, "reels": reels}


def row_line(row: Mapping) -> str:
    """One row's change, in one line.

    Public because `variants.render_comparison` renders the same
    measurement for two VERSIONS of a reel rather than two rounds of
    one, and two spellings of one row diff is how a reader comes to
    believe they are different measurements."""
    if row["state"] == LOST_ROW:
        return (f"      {row['key']}: {row['earlier_count']} item(s) "
                f"-> the row is gone "
                f"({row['earlier_frames']} frames)")
    if row["state"] == ADDED_ROW:
        return (f"      {row['key']}: a new row - {row['later_count']} "
                f"item(s), {row['later_frames']} frames")
    return (f"      {row['key']}: {row['earlier_count']} -> "
            f"{row['later_count']} item(s), "
            f"{row['earlier_frames']} -> {row['later_frames']} frames")


def render_diff(diff: Mapping, show_items: int = 3) -> str:
    """The round diff, in the sentences the captain has to read."""
    earlier, later = diff["earlier"], diff["later"]
    lines = [
        f"── Round {earlier.get('round')} -> round {later.get('round')} ──",
        (f"  Round {later.get('round')} opened "
         f"{later.get('opened_at') or '(at the first build)'} - "
         f"{later.get('why', '')}"),
    ]
    asked = later.get("reels_asked") or []
    if asked:
        lines.append(
            f"  {len(later.get('opened_by') or [])} ask(s) from the "
            f"captain, on: {', '.join(asked)}")
    touched = [name for name, entry in diff["reels"].items()
               if entry["state"] not in ("not rebuilt in this round",)]
    lines.append(
        f"  {len(touched)} reel(s) built in round {later.get('round')}, "
        f"of {len(diff['reels'])} the two rounds name between them.")
    for name in sorted(diff["reels"]):
        entry = diff["reels"][name]
        if entry["state"] == "not rebuilt in this round":
            lines.append(f"    {name}: not rebuilt in round "
                         f"{later.get('round')} - unchanged since round "
                         f"{earlier.get('round')}")
            continue
        if entry["state"] == "first built in this round":
            lines.append(f"    {name}: first built in round "
                         f"{later.get('round')}")
            continue
        rerendered = entry.get("rerendered") or []
        if entry["state"] == UNCHANGED:
            lines.append(
                f"    {name}: rebuilt, and nothing moved"
                + (f" ({len(rerendered)} row(s) re-rendered at "
                   f"identical spans)" if rerendered else ""))
            continue
        lines.append(f"    {name}: {len(entry['changed'])} row(s) "
                     f"changed")
        for row in rerendered:
            lines.append(
                f"      {row['key']}: {row['later_count']} item(s) "
                f"re-rendered at identical spans - nothing moved")
        for row in entry["changed"]:
            lines.append(row_line(row))
            for item in row["gone"][:show_items]:
                lines.append(
                    f"        gone:   {item['name']!r} "
                    f"@{item['start']}..{item['end']} "
                    f"({item['duration']}f)")
            if len(row["gone"]) > show_items:
                lines.append(f"        ... and {len(row['gone']) - show_items} "
                             f"more gone")
            for item in row["gained"][:show_items]:
                lines.append(
                    f"        new:    {item['name']!r} "
                    f"@{item['start']}..{item['end']} "
                    f"({item['duration']}f)")
            if len(row["gained"]) > show_items:
                lines.append(
                    f"        ... and {len(row['gained']) - show_items} "
                    f"more new")
    return "\n".join(lines)


def last_two(project_folder) -> tuple:
    """The last two rounds that actually PROMOTED something.

    A round the captain opened with feedback that has not been built
    yet holds no reels, so diffing against it would report every reel as
    missing. The two most recent rounds with reels in them is what "the
    last two rounds" means to a reader.
    """
    numbers = [entry.get("round")
               for entry in read_rounds(project_folder)
               .get("rounds") or ()
               if entry.get("reels")]
    if len(numbers) < 2:
        raise ValueError(
            f"this project has {len(numbers)} round(s) with a promoted "
            f"reel in them, so there is no pair to diff. "
            f"`round-diff --backfill` reconstructs the rounds already "
            f"in the project's git history.")
    return numbers[-2], numbers[-1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.versions.rounds",
        description="What changed between two rounds. Read-only, off "
                    "disk; never touches Resolve.")
    parser.add_argument("project", help="Path to the project folder")
    parser.add_argument("--from", dest="earlier", type=int, default=None)
    parser.add_argument("--to", dest="later", type=int, default=None)
    parser.add_argument("--backfill", action="store_true",
                        help="reconstruct rounds from the committed "
                             "timeline snapshots first")
    parser.add_argument("--list", action="store_true",
                        help="list the rounds instead of diffing")
    args = parser.parse_args(argv)
    if args.backfill:
        report = backfill(args.project)
        print(f"Reconstructed {report['reels']} reel promotion(s) across "
              f"{report['rounds']} round(s) from {report['commits']} "
              f"commit(s).")
    if args.list:
        print(render_rounds(read_rounds(args.project)))
        return 0
    if args.earlier is None or args.later is None:
        try:
            earlier, later = last_two(args.project)
        except ValueError as thin:
            print(str(thin))
            return 1
        args.earlier = args.earlier if args.earlier is not None else earlier
        args.later = args.later if args.later is not None else later
    try:
        diff = diff_rounds(args.project, args.earlier, args.later)
    except ValueError as missing:
        print(str(missing))
        return 1
    print(render_diff(diff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
