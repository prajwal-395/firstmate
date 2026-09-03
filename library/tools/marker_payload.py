"""The one shape written into a Resolve marker's `customData`.

Resolve gives every marker - media pool, timeline and timeline item - a
`customData` STRING that the UI does not expose.  The captain cannot type
into it; only a script can write it.  That is what makes it the place to
attach machine-written context to a note the captain typed by hand,
without touching the two fields they DID type into (§15: `name` and
`note` are kept verbatim, always).

This module owns the shape of that string, and nothing else does.  It has
no Resolve dependency and no I/O: it is the envelope, its readers, and
one merge.  `marker_capture.py` is one writer, `marker_feedback.py` is
the reader, and `timeline_decisions.py` - which stamps each clip with the
step whose decision produced it - is the second writer, a caller of
`merge_record` and nothing more, exactly as this module expected.

── The envelope ────────────────────────────────────────────────────────

    {
      "schema":     "vep.marker/1",     # REQUIRED. how to PARSE this.
      "id":         "mk_20260828T2312Z_4f1a2b",   # REQUIRED. stable.
      "created_at": "2026-08-28T23:12:04+00:00",
      "updated_at": "2026-08-28T23:14:41+00:00",
      "records":    [ RECORD, ... ],    # REQUIRED. append-only.
      "foreign":    <anything>          # OPTIONAL, see below.
    }

A RECORD is one writer's statement about this marker:

    {
      "kind":           "still",           # REQUIRED. what it is.
      "writer":         "capture_frame",   # REQUIRED. who wrote it.
      "writer_version": 1,                 # REQUIRED. that writer's own.
      "id":             "still_...",       # REQUIRED. stable; a rewrite
                                           #   of the same record reuses it.
      "at":             "2026-08-28T...",  # REQUIRED.
      "path":           "marker_feedback/stills/x.png",   # OPTIONAL
      ... whatever that kind means
    }

── Why it is shaped like this ──────────────────────────────────────────

TWO VERSION FIELDS, NOT ONE.  `schema` governs how to parse the envelope -
that there is a `records` list, and that each entry is self-describing.
`writer_version` governs one writer's own keys.  They move
independently on purpose: the second writer can change what a `decision`
record carries without forcing every reader of every OTHER kind to relearn
anything, and the envelope version stays where a reader can branch on it
with one comparison.  A single flat blob with one version would have made
"the still writer added a field" and "the envelope changed shape" the same
event.

ONE HETEROGENEOUS LIST, NOT A KEY PER WRITER.  A marker accumulates: the
captain clicks the button twice at the same frame, and a pipeline writer
stamps the same marker later.  A list of self-describing records appends
without either writer knowing the other's key names, and a reader that has
never heard of a `kind` still carries it through untouched.  Reserving a
top-level `provenance: []` for the second task would have been this
module guessing that writer's shape before it exists.

AN ATTACHMENT IS A RECORD WITH A `path`, not a record of a particular
kind.  `attachments_of` selects on the key, so the decision writer gets
"a reader can open this" for free the day it wants to point at a step's
output.json - it does not have to be a `still` to be openable.

`path` IS PROJECT-RELATIVE when it can be.  The customData lives in
Resolve's project database, which is not in the pipeline project folder
and outlives any one machine's paths.  A relative path survives the
project folder moving; an absolute one does not.  `path_absolute` may sit
beside it as a convenience and is never the authority.

NOTHING IS EVER DESTROYED.  `customData` that is not one of these
envelopes - hand-written JSON, another tool's string, an older shape - is
carried into `foreign` verbatim rather than overwritten.  The captain
cannot see this field to notice it went missing, which is exactly why it
must not.

The envelope is the whole of what may go in `customData`.  Resolve
round-tripped 4 KB of UTF-8 including apostrophes and en-dashes byte for
byte (measured, see `marker_capture`), so there is no escaping layer here
and none is wanted: it is `json.dumps` and `json.loads`.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

One enumeration, `library/tools/marker_payload.py`: a versioned ENVELOPE carrying a list of
self-describing records, with the reasoning for that shape in the module docstring.
- `schema` versions the ENVELOPE, `writer_version` versions one writer's own keys, and they move
  independently so a second writer can grow without every reader relearning the envelope.
- **An attachment is a record with a `path`, not a record of a particular kind**, so a writer
  pointing at a file gets "a reader can open this" for free.
- **`customData` this module did not write is kept under `foreign`, never overwritten.** The UI
  does not show the field, so nobody would notice it going missing.
- `pull` and `show` surface attachments, and a note with one prints differently from one without.
  **A path the captain TYPED into a note is surfaced too**, told apart by `origin`, matched
  conservatively (absolute POSIX path or `file://`) and never rewritten out of the text.
- `tests/test_marker_payload.py`, `tests/test_marker_capture_against_resolve.py`.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

SCHEMA = "vep.marker/1"
"""The envelope version.  Bump only when a reader must parse differently."""

SUPPORTED_SCHEMAS = (SCHEMA,)

KIND_STILL = "still"
"""A frame exported off the Resolve timeline by the capture button."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_id(prefix: str) -> str:
    """A stable id.  Time first so a sort is chronological, then random."""
    return f"{prefix}_{stamp()}_{uuid.uuid4().hex[:6]}"


def new_envelope() -> dict:
    now = utc_now()
    return {
        "schema": SCHEMA,
        "id": new_id("mk"),
        "created_at": now,
        "updated_at": now,
        "records": [],
    }


def is_envelope(value) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("schema"), str)
        and value["schema"].startswith("vep.marker/")
        and isinstance(value.get("records"), list)
    )


def parse(raw: str) -> dict:
    """Read a `customData` string into an envelope, losing nothing.

    Always returns an envelope.  A string that is not one - empty, plain
    text, someone else's JSON, a future schema - comes back as a fresh
    envelope carrying the original under `foreign`, so a writer that
    merges into it and writes it back destroys nothing.  `foreign_reason`
    says which of those it was.
    """
    envelope = new_envelope()
    if not raw:
        return envelope
    try:
        parsed = json.loads(raw)
    except Exception:
        envelope["foreign"] = raw
        envelope["foreign_reason"] = "customData was not JSON"
        return envelope
    if not is_envelope(parsed):
        envelope["foreign"] = parsed
        envelope["foreign_reason"] = (
            f"customData was JSON but not a {SCHEMA} envelope"
        )
        return envelope
    if parsed["schema"] not in SUPPORTED_SCHEMAS:
        envelope["foreign"] = parsed
        envelope["foreign_reason"] = (
            f"customData declares {parsed['schema']!r}, which this reader "
            f"does not understand; it is kept rather than rewritten"
        )
        return envelope
    # A real envelope: keep its identity and its whole history.
    parsed.setdefault("created_at", envelope["created_at"])
    parsed.setdefault("updated_at", parsed["created_at"])
    parsed.setdefault("id", envelope["id"])
    return parsed


def merge_record(envelope: dict, record: dict) -> dict:
    """Append `record`, or replace the one already carrying its `id`.

    Mutates and returns `envelope`.  Replacement by id is what lets a
    writer correct its own last statement without growing the string
    forever; a writer that wants a second, separate statement mints a
    second id.
    """
    for key in ("kind", "writer", "writer_version", "id", "at"):
        if not record.get(key) and record.get(key) != 0:
            raise ValueError(
                f"a marker record must declare {key!r}: {record!r}"
            )
    records = envelope.setdefault("records", [])
    for index, existing in enumerate(records):
        if isinstance(existing, dict) and existing.get("id") == record["id"]:
            records[index] = record
            break
    else:
        records.append(record)
    envelope["updated_at"] = utc_now()
    return envelope


def dumps(envelope: dict) -> str:
    """The exact string to hand Resolve.  Compact: it lives in a database."""
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def records_of(envelope: dict, kind: str = "") -> list:
    """Every record, or every record of one kind, in write order."""
    out = [r for r in envelope.get("records", []) if isinstance(r, dict)]
    return [r for r in out if r.get("kind") == kind] if kind else out


def attachments_of(envelope: dict) -> list:
    """Every record naming a file.  Selected on `path`, not on `kind`."""
    return [r for r in records_of(envelope) if r.get("path")]


# ── A path the captain typed by hand ────────────────────────────────
#
# The captain can already paste a path into a marker's Notes field and
# firstmate reads it out of the note text.  That keeps working, and is
# surfaced through the same channel as a written attachment so a reader
# has ONE list of things it can open rather than two.  The two are told
# apart by `origin`, never merged into one indistinguishable pile.
#
# Deliberately conservative.  This matches an absolute POSIX path or a
# file:// URL and nothing else: a bare `notes/foo.md` is indistinguishable
# from prose, and guessing would turn "the shot before" into a filename.
# A match is REPORTED, never rewritten, and never removed from the text.

_TYPED_PATH = re.compile(
    r"""(?:^|(?<=[\s"'(\[<]))(?:file://)?"""
    r"""(/(?:[^\s'"<>|:]+/)*[^\s'"<>|:]+\.[A-Za-z0-9]{1,8})"""
)

TYPED_PATH_RULE = (
    "an absolute POSIX path, or a file:// URL, ending in a short "
    "extension, and starting where a word starts - `notes/relative.md` "
    "is prose, not a path"
)


def typed_paths(text: str) -> list:
    """Absolute paths appearing in text the captain typed, in order.

    Duplicates are collapsed; order of first appearance is kept, because
    the first one they typed is the one they meant.
    """
    out: list = []
    for match in _TYPED_PATH.finditer(text or ""):
        path = match.group(1).rstrip(".,;:)]}")
        if path not in out:
            out.append(path)
    return out
