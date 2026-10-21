"""A DELIVERED reel carries a durable acceptance, and promotion respects it.

The captain's ruling, 2026-10-19, answering the Reel 26 grade and
version-control audit (`data/vep-reel26-grades-vc/report.md` §2.1):
*"promotion replaced the accepted look without the captain's approval"*.

What was wrong
--------------
A sign-off protects a reel that HAS one. A reel that was DELIVERED -
rendered, verified and handed over - and never signed off had nothing
protecting it, so the next rebuild promoted over it silently and the
captain's accepted look was replaced by a build nobody looked at. The
approval of a MOMENT (which words are worth cutting) is not the
approval of a LOOK (what the frames look like), and only the second
one is what a delivery says.

This is
-------
The same shape as `library/tools/reel_signoff.py`, one level down: a
sign-off says the captain approved a reel as it stands; an ACCEPTANCE
says the captain TOOK this reel - it was delivered, verified and is
the generation the project is now judged by. Both are durable, both
are released by an explicit act, and a promotion over either one must
declare that it is superseding it.

Why it is a separate record
---------------------------
A sign-off can be withdrawn (`reel_signoff.withdraw`), and a
withdrawal is the captain changing his mind about a reel he has not
yet taken. An acceptance is a fact about what was handed over: it
names the timeline, the Resolve identity of the generation that was
delivered, when, and the file that was delivered. It is never deleted;
a supersession moves it to `superseded`, the way a superseded sign-off
is kept.

Durability
----------
Written through `library/tools/versions/store.py`, so the record is
versioned with the project: an acceptance that lives only in a loose
file is an acceptance that can be lost by a cleanup, and a lost
acceptance reads exactly like no acceptance - which is the defect this
record exists to close.

`tests/unit/reels/test_reel_acceptance.py`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone

from library.tools.ren_refusal import RenRefusal
from library.tools.versions import store

ACCEPTANCE_FILENAME = "reel_acceptances.json"
ACCEPTANCE_FORMAT = "reel_acceptances/1"


class AcceptanceNotDeclared(RenRefusal):
    """A promotion would replace an accepted (delivered) reel silently."""

    USER_MESSAGE = (
        "This promotion would replace a reel the captain already took. "
        "Nothing was renamed - say which reel you are superseding and I "
        "will do it deliberately.")

    def __init__(self, message: str, *, reel: str = "",
                 command: str = "build-reels") -> None:
        super().__init__(
            what=message,
            why=("a reel that was delivered is the generation the captain "
                 "accepted; replacing it without a declaration is how the "
                 "accepted look was replaced by a build nobody watched"),
            fix=(f"declare it: `{command} --supersede {reel!r}`. The "
                 "acceptance is then recorded as superseded, never deleted, "
                 "and the timeline it covered is retired to the archive bin "
                 "rather than removed."))
        self.reel = reel
        self.command = command


def path_for(project_folder) -> str:
    """Where the acceptance record lives for one project."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        ACCEPTANCE_FILENAME)


def read(project_folder) -> dict:
    """The acceptance document, or an empty one when none was written."""
    path = path_for(project_folder)
    if not os.path.exists(path):
        return {"format": ACCEPTANCE_FORMAT, "acceptances": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise RenRefusal(
            what=f"{path} exists but could not be read ({unreadable}).",
            why=("an unreadable acceptance record must REFUSE, never be "
                 "replaced with an empty one: an unreadable acceptance "
                 "reads exactly like no acceptance"),
            fix=("recover pipeline_output/review/reel_acceptances.json from "
                 "the project's version history - never hand-write an "
                 "empty one"))
    if not isinstance(document, dict) or not isinstance(
            document.get("acceptances"), dict):
        raise RenRefusal(
            what=f"{path} is not a {ACCEPTANCE_FORMAT} document.",
            why="an acceptance record that cannot be read is no record",
            fix="recover it from the project's version history")
    return document


def _write(project_folder, document: Mapping) -> str:
    return store.write_record(path_for(project_folder), document,
                              prefix=".reel-acceptances-")


def base_name(timeline: str) -> str:
    """The reel's base name, so an acceptance survives staging and backup.

    One spelling, borrowed from `library/tools/reel_signoff.py`: a reel
    has three names in a build (final, staging, backup) and one
    identity.
    """
    from library.tools.reel_signoff import base_name as _base_name

    return _base_name(timeline)


def record(project_folder, timeline: str, *, note: str = "",
           by: str = "captain", source: str = "delivery",
           timeline_id: str = "", delivered_file: str = "",
           rows: Mapping | None = None, at: str | None = None) -> dict:
    """Record that this generation of the reel was delivered and taken.

    `rows` is the reel's `reel_read.rows_of` snapshot when it is known.
    It is digested, never stored whole, for the reason every other
    digest in this codebase is a digest: two copies of one measurement
    are the disagreement this codebase keeps catching.
    """
    from library.tools.versions import rounds

    document = read(project_folder)
    reel = base_name(timeline)
    entry = {
        "reel": reel,
        "timeline": str(timeline or ""),
        "timeline_id": str(timeline_id or ""),
        "accepted_at": str(at or datetime.now(timezone.utc).isoformat()),
        "by": str(by or "captain"),
        "source": str(source or "delivery"),
        "note": str(note or ""),
        "delivered_file": str(delivered_file or ""),
        "rows_digest": (rounds.digest_rows(rows)
                        if rows is not None else ""),
    }
    document["acceptances"][reel] = entry
    document["format"] = ACCEPTANCE_FORMAT
    _write(project_folder, document)
    return entry


def acceptances(project_folder) -> dict:
    """`{base reel name: entry}` for every live acceptance."""
    return dict(read(project_folder).get("acceptances") or {})


def acceptance_for(project_folder, timeline: str) -> dict | None:
    """The live acceptance covering this timeline name, or None."""
    return acceptances(project_folder).get(base_name(timeline))


def supersede(project_folder, timeline: str, *,
              by: str = "superseded") -> dict | None:
    """Move this reel's acceptance to `superseded`. Never deletes it.

    Called by the promotion that was declared, AFTER the rename landed:
    a promotion that refuses later must not have already retired the
    acceptance it did not replace.
    """
    document = read(project_folder)
    reel = base_name(timeline)
    entry = document["acceptances"].pop(reel, None)
    if entry is None:
        return None
    document.setdefault("superseded", []).append({
        **entry,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "ended_by": str(by or "superseded"),
    })
    _write(project_folder, document)
    return entry


def refusal_message(final: str, entry: Mapping,
                    command: str = "build-reels") -> str:
    """Why this promotion refuses, and the declaration that proceeds."""
    reel = entry.get("reel") or base_name(final)
    when = str(entry.get("accepted_at") or "")[:19].replace("T", " ")
    delivered = str(entry.get("delivered_file") or "")
    lines = [
        f"REFUSING to promote {final!r}: the reel already in the "
        f"project was DELIVERED and accepted"
        + (f" on {when}" if when else "")
        + ". Nothing was renamed; the accepted timeline is still in the "
        "project."]
    if delivered:
        lines.append(f"  The delivery: {delivered}")
    if entry.get("note"):
        lines.append(f'  Note: "{entry["note"]}"')
    lines.append(
        f"  To replace it deliberately, declare it: "
        f"`{command} --supersede {reel!r}`. The acceptance is then "
        "recorded as superseded, never deleted - and the timeline it "
        "covered is retired to the archive bin rather than removed.")
    return "\n".join(lines)


def assert_declared(project_folder, final: str, declared, *,
                    command: str = "build-reels") -> dict | None:
    """Raise unless an acceptance on this reel was declared superseded.

    Returns the acceptance entry when one exists and WAS declared (so
    the caller can record the supersession), None when there is none.
    """
    entry = acceptance_for(project_folder, final)
    if entry is None:
        return None
    from library.tools.reel_signoff import parse_supersede

    names = declared if isinstance(declared, set) else parse_supersede(
        declared)
    if base_name(final) in names:
        return entry
    raise AcceptanceNotDeclared(
        refusal_message(final, entry, command),
        reel=str(entry.get("reel") or base_name(final)),
        command=command)


def describe(project_folder, timeline: str) -> str:
    """One line about this reel's acceptance, for a reader."""
    entry = acceptance_for(project_folder, timeline)
    if entry is None:
        return f"{base_name(timeline)}: not delivered and accepted."
    when = str(entry.get("accepted_at") or "")[:19].replace("T", " ")
    return (f"{entry.get('reel')}: ACCEPTED (delivered) by "
            f"{entry.get('by', 'captain')} on {when}"
            + (f", file {entry['delivered_file']}"
               if entry.get("delivered_file") else ""))


__all__ = [
    "ACCEPTANCE_FILENAME",
    "ACCEPTANCE_FORMAT",
    "AcceptanceNotDeclared",
    "acceptance_for",
    "acceptances",
    "assert_declared",
    "describe",
    "path_for",
    "read",
    "record",
    "supersede",
]
