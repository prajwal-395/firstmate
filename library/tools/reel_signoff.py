"""A BUILT reel carries a durable sign-off, and promotion respects it.

The captain, 2026-09-12, answering
`data/vep-can-it-hold-up-in-a-real-editing-workflow` §7: *a built reel
carries a durable sign-off that promotion must respect.*

What was wrong
--------------
Approval existed, and it was on the wrong object. `reel_proposal.Approval`
rules on a PROPOSED MOMENT - a span of the master the captain agreed was
worth cutting - and it is ruled on BEFORE any timeline is built. Nothing
could be said about a reel that had been built and watched. So the newest
build under the final name was the answer by construction, and an
approved reel could be silently replaced by a rebuild that was never
looked at. `staging_holds.py:14` states the absence in this repository's
own words: *"No existing record marks a staging timeline as verified"*.

What this is
------------
The other half of `staging_holds`, and deliberately the same shape: a
small durable per-reel record under `pipeline_output/review/`, on the
version-control allow-list, read by the one place a captain-visible
timeline is replaced.

- a HOLD protects a staging that has not been promoted yet, from the
  sweep;
- a SIGN-OFF protects a reel that HAS been promoted, from the next
  promotion.

Both are released by an explicit act, neither expires. A sign-off that
timed out would be an approval the machine withdrew on the captain's
behalf.

What it records
---------------
The reel's BASE name (`feedback_ledger.base_reel_name`, so a sign-off
survives the reel being staged, backed up and promoted - those are three
names for one reel), the ROUND it was signed off in
(`versions.rounds` - the version object a sign-off attaches to), a digest
of the rows that were on the timeline at the time
(`versions.rounds.digest_rows`), when, and the captain's own words.

The digest is what lets `describe` answer a question a bare flag cannot:
whether the timeline in front of you is still the one that was approved,
or a later build that quietly took its name.

Declare, then proceed - not block
---------------------------------
The captain said promotion must RESPECT a sign-off. Between the two
readings - block outright, or force the promotion to declare what it is
overwriting - this takes the second, for the reason
`reel_replace_guard` already takes it: a refusal that cannot be
overridden is a refusal an operator routes around, and the one thing
that must never happen is a signed-off reel being replaced BY ACCIDENT.
So a promotion over a sign-off refuses by default, names the reel, the
round, the date and the captain's words, and prints the exact
declaration that proceeds deliberately:

    build-reels --supersede 'Reel 09 - your-website-is-only-20-percent'

A superseded sign-off is not deleted. It is moved to `superseded` with
the round that replaced it, so "this reel was approved once and then
rebuilt" stays answerable - which is the question that was unanswerable
before this existed.

Per reel, like the guard
------------------------
The check is asked per reel inside the promotion loop, so a refusal on
one reel never holds back a sibling that passed - the structure the
2026-09-11 round established when one refusal discarded three buildable
reels.

`tests/test_reel_signoff.py`.
"""

from __future__ import annotations

import json
import os
import tempfile

from library.tools.ren_refusal import RenRefusal
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

# Re-exported so callers can catch the completion gate without importing
# a second module: `sign_off` raises this while the reel owes an
# uncarried-note obligation (`library/tools/uncarried_notes.py`).
from library.tools.uncarried_notes import UncarriedNotesOpen

SIGNOFF_FILENAME = "reel_signoffs.json"
SIGNOFF_FORMAT = "reel_signoffs/1"


class SignOffsUnreadable(RenRefusal):
    """The sign-off file exists but cannot be parsed, and this says so."""

    def __init__(self, message: str) -> None:
        super().__init__(
            what=message,
            why=("a sign-off record that cannot be read must REFUSE, "
                 "never be replaced with an empty one: an unreadable "
                 "approval reads exactly like no approval"),
            fix=("recover pipeline_output/review/reel_signoffs.json from "
                 "backup or version history - never hand-write an empty "
                 "one, or promotion would overwrite the reel the captain "
                 "signed off"))


class SignOffNotDeclared(RenRefusal):
    """A promotion would replace a signed-off reel and did not say so."""

    def __init__(self, message: str) -> None:
        super().__init__(
            what=message,
            why="a promotion would replace a reel the captain signed off",
            fix=("replace it deliberately: re-run the same command with "
                 "the --supersede declaration the message above spells"))


def signoffs_path_for(project_folder) -> str:
    """`pipeline_output/review/reel_signoffs.json` - beside the holds."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        SIGNOFF_FILENAME)


def base_name(timeline: str) -> str:
    """The reel a timeline name is a container for.

    `feedback_ledger.base_reel_name` is the one owner - a sign-off and a
    note must agree about which reel they are on, or a note filed
    against `Reel 09` and a sign-off on `Reel 09 (pre-rebuild backup)`
    read as two reels.
    """
    from library.tools.feedback_ledger import base_reel_name

    return base_reel_name(timeline)


def read_signoffs(project_folder) -> dict:
    """The sign-off document, or an empty one."""
    path = signoffs_path_for(project_folder)
    if not os.path.exists(path):
        return {"format": SIGNOFF_FORMAT, "signoffs": {},
                "superseded": []}
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise SignOffsUnreadable(
            f"{path} exists but could not be read ({unreadable}). A "
            f"sign-off record that cannot be read must REFUSE, never "
            f"be replaced with an empty one: an unreadable approval "
            f"reads exactly like no approval, and promotion would "
            f"then overwrite the reel the captain signed off.") \
            from unreadable
    if not isinstance(document, dict) or not isinstance(
            document.get("signoffs"), dict):
        raise SignOffsUnreadable(
            f"{path} is not a sign-off document ({SIGNOFF_FORMAT}).")
    document.setdefault("superseded", [])
    return document


def _write(project_folder, document: Mapping) -> str:
    path = signoffs_path_for(project_folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps(dict(document), indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    handle, staged = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=".signoffs-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(payload)
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise
    return path


def sign_off(project_folder, timeline: str, *, note: str = "",
             by: str = "captain", rows: Mapping | None = None,
             round_number: int | None = None) -> dict:
    """Record that this BUILT reel is approved as it stands.

    `rows` is the reel's `reel_read.rows_of` snapshot when it is known -
    from the round record, or read live. It is digested, never stored
    whole: `versions.rounds` already holds the rows for the round, and two
    copies of one measurement is the disagreement this codebase keeps
    catching (AGENTS.md 10.1).

    `round_number` defaults to the round this project is currently in,
    so the sign-off says WHICH version was approved rather than only
    that one was.

    Refuses while this reel owes an uncarried-note obligation
    (`library/tools/uncarried_notes.py`): a sign-off is the act that
    says a built reel is done, and a reel with the captain's words
    still unaccounted for is not done. Only an explicit discharge -
    naming the obligation and saying what happened to the note -
    releases it. Reels that never dropped a note sign off exactly as
    before.
    """
    from library.tools.versions import rounds
    from library.tools import uncarried_notes as _owed

    _owed.assert_none_open(project_folder, timeline)

    document = read_signoffs(project_folder)
    reel = base_name(timeline)
    if round_number is None:
        try:
            round_number = rounds.open_round(project_folder)["round"]
        except Exception:                                   # noqa: BLE001
            round_number = None
    entry = {
        "reel": reel,
        "signed_off_at": datetime.now(timezone.utc).isoformat(),
        "by": str(by or "captain"),
        "note": str(note or ""),
        "round": round_number,
        "rows_digest": (rounds.digest_rows(rows)
                        if rows is not None else ""),
        "timeline": str(timeline or ""),
    }
    document["signoffs"][reel] = entry
    document["format"] = SIGNOFF_FORMAT
    _write(project_folder, document)
    return entry


def withdraw(project_folder, timeline: str, why: str = "") -> bool:
    """Withdraw a sign-off. Returns whether one was there.

    The withdrawal is recorded in `superseded` with its reason, for the
    same reason a superseded one is: an approval that was given and
    taken back is a fact about the reel.
    """
    document = read_signoffs(project_folder)
    reel = base_name(timeline)
    entry = document["signoffs"].pop(reel, None)
    if entry is None:
        return False
    document.setdefault("superseded", []).append({
        **entry,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "ended_by": "withdrawn",
        "why": str(why or ""),
    })
    _write(project_folder, document)
    return True


def signed_off(project_folder) -> dict:
    """`{base reel name: entry}` for every live sign-off."""
    return dict(read_signoffs(project_folder).get("signoffs") or {})


def signoff_for(project_folder, timeline: str) -> dict | None:
    """The live sign-off covering this timeline name, or None."""
    return signed_off(project_folder).get(base_name(timeline))


def parse_supersede(raw) -> set:
    """Raw `--supersede` specs to the set of base reel names they name.

    Accepts a reel name in any container spelling, so the exact string
    the refusal prints can be pasted back. Anything that is not a
    non-empty string raises rather than reading as an empty
    declaration - the same rule `reel_replace_guard.parse_specs` holds
    to, and for the same reason: a silently empty declaration is a
    guard that passed without being asked.
    """
    if raw is None:
        return set()
    if isinstance(raw, str):
        raw = [raw]
    try:
        specs = list(raw)
    except TypeError:
        raise ValueError(
            f"supersede must be a list of reel names, got {raw!r}.")
    out = set()
    for spec in specs:
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError(
                f"supersede specs must be non-empty reel names, got "
                f"{spec!r}.")
        out.add(base_name(spec.strip()))
    return out


def refusal_message(final: str, entry: Mapping,
                    command: str = "build-reels") -> str:
    """Why this promotion refuses, and the declaration that proceeds.

    `command` is the command the operator was actually running, because
    the declaration a refusal prints has to be the one they can paste.
    A `variant choose` told to run `build-reels --supersede` sends them
    to a different act entirely - and a refusal whose instruction does
    not fit the situation is a refusal that gets routed around, which
    is the one thing the declare-then-proceed shape exists to prevent.
    """
    reel = entry.get("reel") or base_name(final)
    when = str(entry.get("signed_off_at") or "")[:19].replace("T", " ")
    round_number = entry.get("round")
    words = str(entry.get("note") or "").strip()
    lines = [
        f"REFUSING to promote {final!r}: the reel already in the "
        f"project is SIGNED OFF"
        + (f" (round {round_number})" if round_number else "")
        + f", approved by {entry.get('by', 'captain')} on {when}. "
          f"Nothing was renamed; the signed-off timeline is still in "
          f"the project."]
    if words:
        lines.append(f'  Their words: "{words}"')
    lines.append(
        f"  To replace it deliberately, declare it: "
        f"`{command} --supersede {reel!r}`. The sign-off is then "
        f"recorded as superseded, never deleted - and the timeline it "
        f"covered is retired to the archive bin rather than removed.")
    return "\n".join(lines)


def assert_declared(project_folder, final: str,
                    declared: Sequence[str] | set | None,
                    command: str = "build-reels") -> dict | None:
    """Raise unless a sign-off on this reel was declared superseded.

    Returns the sign-off entry when one exists and WAS declared (so the
    caller can record the supersession), None when there is none.
    `command` is passed through to the refusal so it prints the
    declaration for the act the operator is performing.
    """
    entry = signoff_for(project_folder, final)
    if entry is None:
        return None
    names = declared if isinstance(declared, set) else parse_supersede(
        declared)
    if base_name(final) in names:
        return entry
    raise SignOffNotDeclared(refusal_message(final, entry, command))


def supersede(project_folder, final: str, round_number=None) -> dict | None:
    """Move this reel's sign-off to `superseded`. Never deletes it.

    Called by the promotion that was declared, AFTER the rename landed:
    a promotion that refuses later must not have already retired the
    approval it did not replace.
    """
    document = read_signoffs(project_folder)
    reel = base_name(final)
    entry = document["signoffs"].pop(reel, None)
    if entry is None:
        return None
    document.setdefault("superseded", []).append({
        **entry,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "ended_by": "superseded",
        "superseded_in_round": round_number,
    })
    _write(project_folder, document)
    return entry


def describe(project_folder, timeline: str,
             rows: Mapping | None = None) -> str:
    """One line about this reel's sign-off, for a reader.

    When `rows` is given, says whether the timeline still carries the
    picture that was signed off - the question a bare "approved" flag
    cannot answer once a rebuild has taken the name.
    """
    from library.tools.versions import rounds

    entry = signoff_for(project_folder, timeline)
    if entry is None:
        return f"{base_name(timeline)}: not signed off."
    when = str(entry.get("signed_off_at") or "")[:19].replace("T", " ")
    line = (f"{entry['reel']}: SIGNED OFF by {entry.get('by', 'captain')} "
            f"on {when}"
            + (f", round {entry['round']}" if entry.get("round") else ""))
    if rows is not None and entry.get("rows_digest"):
        same = rounds.digest_rows(rows) == entry["rows_digest"]
        line += (" - the timeline still carries the rows that were "
                 "approved." if same else
                 " - WARNING: the timeline's rows differ from what was "
                 "approved, so a later build has taken this name.")
    return line
