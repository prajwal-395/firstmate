"""A piece of captain feedback, with an identity that survives a rebuild.

The defect this closes
----------------------
The captain, 2026-09-11: *"it looks like we are just constantly asking
for the same things over and over"*.  Measured across four rounds on
`lucie/geo-podcast`: the leftover timelines were asked for four times
before it stuck, the caption position twice, the ending across three
rounds, and the logo card was asked for, reported done, and measured
absent.

The cause is an identity problem, and it is stated in this repository's
own words.  `marker_routing._note_id` keys a note on
``timeline:source:frame``, and `marker_resolution` records the
consequence in its docstring: *"stable across re-routings of the same
pull but NOT across a rebuild that moves the frame"*.  Every fix in this
pipeline rebuilds the timeline.  So a note's identity is destroyed by
the exact act that is supposed to answer it: the blue marker is read,
the work is done, the timeline is rebuilt, the frame moves, the green
reply is written, and afterwards nothing can be asked whether that
question is still true.  The next round it gets typed again.

What this module adds, and what it deliberately does not
--------------------------------------------------------
**It is NOT a second source of truth.**  The captain works in markers
and will not move, and they are right not to: the markers ARE the
question.  This is a derived LOG of what the pull files already say,
and when the two disagree the markers win - an entry the latest pull no
longer shows is recorded as gone from the timeline, never re-asserted
onto it.  Nothing here writes to Resolve.

What a log can hold that a marker cannot is the part that was missing: a
removed marker leaves no record that the question was ever asked, so
"was this asked before?" and "is it still true a rebuild later?" had no
place to be answered from.

The identity
------------
:func:`durable_identity` is the durable half.  It is a digest of two things
and nothing else:

* the reel's BASE name, with container suffixes stripped - a note
  survives its timeline being staged, backed up, promoted and retired
  into the archive, because those are names for one reel;
* the captain's own WORDS, normalised for whitespace and case only.

No frame, no timecode, no pull file.  Those are the things a rebuild
changes, which is precisely why they cannot be in an identity meant to
outlive one.  `marker_resolution`'s docstring already reaches for this -
*"the record carries the captain's text verbatim precisely so a moved
note is still findable by its words"* - and this is that lookup, built.

The states
----------
:data:`STATES` reuses `marker_resolution`'s vocabulary unchanged, plus
:data:`STATE_OPEN` for a note nobody has answered yet.  A second
vocabulary for the same thing is how two records come to disagree, so
there is not one.

Two readings this makes possible
--------------------------------
* **RE-ASKED** - a note carrying a ``resolved_verified`` record that is
  seen again on a pull taken AFTER that record.  In plain terms: we said
  done, and the captain's marker is still there.  That is the expensive
  failure the round report named, and it now has a name and a count.
* **ECHOED** - one identity's words appearing on more than one reel.
  That is the "apply this to all of the reels" shape, and a count of the
  reels it was typed on is the evidence for treating it as one
  mechanism rather than N edits.

Neither refuses anything.  Both are reported, because what to do about a
re-ask is a judgement, and a ledger that blocked a build would be a
record holding work hostage.

``tests/unit/context/test_ledgers.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools.project_layout import Area, ProjectLayout  # noqa: E402

LEDGER_FILENAME = "feedback_ledger.json"
LEDGER_FORMAT = "feedback_ledger/1"


# ── States: reused, never re-invented ─────────────────────────────

STATE_OPEN = "open"
"""Asked, and nothing has answered it yet."""


def _resolution_states() -> tuple:
    from library.tools import marker_resolution as mr

    return (mr.STATUS_RESOLVED_VERIFIED, mr.STATUS_ADDRESSED_UNVERIFIED,
            mr.STATUS_DECLINED, mr.STATUS_UNVERIFIABLE)


STATES = (STATE_OPEN,) + _resolution_states()
"""Every state an entry may hold. `marker_resolution` owns four of the
five and this module does not restate them - a second spelling of
`resolved_verified` is how two records come to disagree about one
note."""


# ── The durable identity ──────────────────────────────────────────

#: Suffixes a build puts on a reel's timeline while it is being
#: replaced. They are three names for one reel, so an identity must
#: not change when the container does. Owned by `reel_build` and
#: `resolve_bin_layout`; read from them so a rename is one edit.
def container_suffixes() -> tuple:
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX

    return (STAGING_TIMELINE_SUFFIX, " (pre-rebuild backup)")


def base_reel_name(timeline: str) -> str:
    """The reel a timeline name is a container for.

    Strips the build's own container suffixes, repeatedly, because a
    staged backup carries both - and a retired generation carries the
    archive suffix instead. Anything else in the name is left
    exactly as it is: a hand-made container the captain named is a
    different reel to them, and collapsing it into the final would file
    a note about a scratch against the reel they review.

    Which suffixes strip is deliberate, not exhaustive. The three that
    do are ENGINE-OWNED and machine-shaped - `resolve_bin_layout`
    owns the staging spelling, `reel_build` the backup spelling, and
    `reel_retirement.archived_name` the `(archived round NNN[.M])`
    spelling, read back through its own `parse_archived` rather than
    re-spelled here. Hand-made copies the project has actually carried
    - `(batch-1050)`, `(final)`, `(MFA timings)`, `(all three fixes)`,
    `(baseline scratch)` - do NOT strip: they carry captain or
    firstmate intent no pattern can recover, and a regex that stripped
    any parenthesis would collide a reel legitimately named with one
    against a different reel.
    """
    from library.tools.reel_retirement import parse_archived

    name = (timeline or "").strip()
    changed = True
    while changed:
        changed = False
        for suffix in container_suffixes():
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
                changed = True
        reel, _number = parse_archived(name)
        if reel is not None:
            name = reel.strip()
            changed = True
    return name


def normalise_text(text: str) -> str:
    """The captain's words, compared for whitespace and case only.

    Deliberately shallow. Aggressive normalisation - stripping
    punctuation, stemming, dropping short words - merges two different
    notes into one identity, and a ledger that silently answers note B
    with note A's resolution is worse than one that files the same words
    twice.
    """
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def note_text(note: Mapping) -> str:
    """A note's words: its `text`, else name and note joined.

    The same field precedence `marker_resolution._note_text_fields`
    uses, so one note read through either module yields one string.
    """
    note = note or {}
    explicit = note.get("text")
    if explicit:
        return str(explicit)
    return "\n\n".join(
        part for part in (str(note.get("name") or ""),
                          str(note.get("note") or "")) if part)


def durable_identity(timeline: str, text: str) -> str:
    """The id that survives a rebuild: reel + words, and nothing else.

    Not to be confused with `marker_feedback.note_identity`, which is
    the WITHIN-A-PULL dedup identity ("have I already collected this
    reading?") and includes the frame on purpose. This is the other
    kind: the one a rebuild must not change. Two names, because they
    answer two questions.

    Readable prefix, then a digest, the same shape
    `plan_provenance.plan_content_hash` takes: a human can see which
    reel it belongs to in a directory listing, and the digest is what
    makes it exact.
    """
    reel = base_reel_name(timeline)
    payload = f"{reel}\n{normalise_text(text)}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in reel)
    return f"{stem or 'timeline'}:{digest}"


def identity_of(note: Mapping, timeline: str = "") -> str:
    """One collected note's durable identity."""
    return durable_identity(timeline or (note or {}).get("timeline") or "",
                         note_text(note))


#: What a `durable_identity` looks like: a readable reel stem, one
#: colon, sixteen hex digest characters. Owned here because this module
#: owns `durable_identity`; `marker_feedback.reply_record` (the single
#: writer of a reply's `answers` key) and `marker_carry` (which re-pairs
#: replies by it) both validate against this rather than re-spelling it.
IDENTITY_PATTERN = re.compile(r"[^:]+:[0-9a-f]{16}")


def is_identity(value) -> bool:
    """Whether `value` has the shape `durable_identity` produces.

    The grammar half of the `answers` single-writer rule: a reply's
    `answers` is either empty (nothing claimed) or one of these, never
    prose ("R04 blue feedback") and never a frame ("... @162"). Both of
    those were found on live reels, and both decay: prose never joined
    to anything, and a frame is invalidated by the next rebuild.
    """
    return (isinstance(value, str)
            and IDENTITY_PATTERN.fullmatch(value) is not None)


# ── The entries ───────────────────────────────────────────────────

KIND_ASK = "ask"
"""A note nothing identifies as ours, which is the captain's question.

The DEFAULT, and deliberately so. `marker_feedback` has no vocabulary of
marker colours - *"the typed text is the signal"* - so a note is only
ours when it carries our own machine record saying so. Getting this
backwards in either direction has very different costs: treating one of
our replies as an open question is noise a reader clears in a second,
and treating the captain's question as our reply LOSES IT. So anything
unmarked is theirs.
"""

KIND_REPLY = "reply"
"""A note we wrote back - carrying a `marker_feedback` reply record,
or the `reply:` name shape those records were built to replace.

The shape half exists because the replies on the captain's own
timeline predate the record: every marker on `lucie/geo-podcast`
carries an EMPTY `customData`, our two green replies included
(`marker_feedback.REPLY_RECORD_KIND`), so a reader that only trusts
the machine record files our answers as the captain's open questions.
The convention is the writer's: the captain types `feedback`, we write
`reply:`. A captain note that merely mentions the word reply stays an
ask - only a FIRST line starting with `reply:` reads as ours, which is
the one prefix our writer puts in the Name field and the captain's
`feedback` shape never carries.
"""

#: The first line a reply of ours starts with, case-insensitively.
#: The colon matters: "reply to all reels" is a captain sentence, and
#: only `reply:` is the writer's stamp.
REPLY_NAME_PREFIX = "reply:"


def _carries_reply_shape(raw: Mapping) -> bool:
    """Does this note wear our `reply:` name shape, record or none?

    Read off the note's own first line only - a mention of the word
    deeper in the body is the captain quoting us back, not us writing.
    """
    first = re.split(r"\r?\n", note_text(raw), maxsplit=1)[0]
    return first.strip().lower().startswith(REPLY_NAME_PREFIX)


@dataclass
class FeedbackEntry:
    """One thing the captain asked for, and everything since."""

    identity: str
    reel: str
    text: str
    kind: str = KIND_ASK
    answers: str = ""
    """For a reply: the durable identity of the note it answers, read
    off its own `customData`. This is the link that survives the blue
    marker being deleted."""

    answered_by: List[str] = field(default_factory=list)
    """For an ask: every reply of ours that names this identity."""

    state: str = STATE_OPEN

    first_asked: str = ""
    """The earliest pull that carried these words."""

    last_seen: str = ""
    """The latest pull that carried them. A note still on the timeline
    has a `last_seen` from the most recent pull; one the captain removed
    stops advancing, which is what `on_timeline` reads."""

    pulls: int = 0
    seen_on: List[str] = field(default_factory=list)
    """Every timeline name these words were typed on, container names
    included - the evidence behind ECHOED."""

    frames: List[int] = field(default_factory=list)
    """Every frame it has been read at. Recorded, never part of the
    identity: this is the list that shows a note moving under a rebuild
    while remaining one note."""

    resolution: Dict[str, Any] = field(default_factory=dict)
    """The `marker_resolution` record that set this state, if any."""

    reasked: bool = False
    """Seen on a pull taken after a `resolved_verified` record."""

    def as_dict(self) -> dict:
        return asdict(self)


def _iso(value) -> str:
    return str(value or "")


def _newer(a: str, b: str) -> bool:
    """Is `a` a later timestamp than `b`? Missing sorts earliest."""
    return _iso(a) > _iso(b)


# ── Building the ledger from what the pulls already say ───────────

def _authorship(raw: Mapping) -> tuple:
    """`(kind, answers)` for one collected note, from its OWN record.

    The machine record in `customData` decides first - it is the one
    statement nobody can type by accident. Without one, the `reply:`
    name shape decides: our writer stamps it and the captain's
    `feedback` shape never carries it. Colour decides nothing, and a
    note carrying neither is the captain's - filing an unmarked note
    as ours would LOSE their question, which costs more than counting
    one of our answers as open.
    """
    from library.tools import marker_feedback

    records = []
    for source in ((raw or {}).get("custom_data"),
                   (raw or {}).get("custom_data_raw")):
        if not source:
            continue
        try:
            records = marker_feedback.reply_records_in(source)
        except Exception:                         # noqa: BLE001
            records = []
        if records:
            break
    if records:
        answers = ""
        for record in records:
            if record.get("answers"):
                answers = str(record["answers"])
                break
        return KIND_REPLY, answers
    if _carries_reply_shape(raw):
        # Our words, but written before the reply record existed, so
        # there is no identity to hang onto the ask it answered - the
        # body quotes a frame (`You asked (marker @1902)`), not the
        # durable identity. Counted as ours and reported unlinked,
        # never silently counted open.
        return KIND_REPLY, ""
    return KIND_ASK, ""


def collect(project_folder, pulls=None) -> Dict[str, FeedbackEntry]:
    """Every note this project has pulled, folded onto its identity.

    `pulls` is `[(path, payload)]` as `marker_feedback.pulled_files`
    returns; omitted, it reads the project's own. Notes are folded
    oldest pull first, so `first_asked` is genuinely the first.
    """
    if pulls is None:
        from library.tools import marker_feedback
        pulls = marker_feedback.pulled_files(project_folder)
    entries: Dict[str, FeedbackEntry] = {}
    for _path, payload in pulls:
        payload = payload or {}
        timeline = str(payload.get("timeline") or "")
        pulled_at = _iso(payload.get("pulled_at"))
        for raw in payload.get("notes") or ():
            text = note_text(raw)
            if not normalise_text(text):
                continue          # a marker with no words asks nothing
            identity = durable_identity(timeline, text)
            entry = entries.get(identity)
            if entry is None:
                kind, answers = _authorship(raw)
                entry = FeedbackEntry(
                    identity=identity, reel=base_reel_name(timeline),
                    text=text, kind=kind, answers=answers,
                    first_asked=pulled_at, last_seen=pulled_at)
                entries[identity] = entry
            if not entry.first_asked or _newer(entry.first_asked, pulled_at):
                entry.first_asked = pulled_at
            if _newer(pulled_at, entry.last_seen):
                entry.last_seen = pulled_at
            entry.pulls += 1
            if timeline and timeline not in entry.seen_on:
                entry.seen_on.append(timeline)
            frame = raw.get("frame")
            if isinstance(frame, int) and frame not in entry.frames:
                entry.frames.append(frame)
    return entries


def apply_resolutions(entries: Mapping[str, FeedbackEntry],
                      resolutions) -> Dict[str, FeedbackEntry]:
    """Fold `marker_resolution` records onto the entries they answer.

    A resolution is matched by recomputing the DURABLE identity from the
    record's own `timeline` and `text` - the record already carries both
    verbatim - rather than by its stored `note_id`, which is the
    frame-keyed one a rebuild invalidates. That is the whole join, and
    it is why no change to `marker_resolution`'s storage was needed.

    A record for words this project never pulled is skipped rather than
    creating an entry: the pulls are what the captain asked, and a
    resolution is an answer to one of them.
    """
    from library.tools import marker_resolution as mr

    out = dict(entries)
    for record in resolutions or ():
        record = record or {}
        identity = durable_identity(str(record.get("timeline") or ""),
                                 note_text(record))
        entry = out.get(identity)
        if entry is None:
            continue
        resolved_at = _iso(record.get("resolved_at"))
        held = _iso((entry.resolution or {}).get("resolved_at"))
        if entry.resolution and not _newer(resolved_at, held):
            continue
        status = str(record.get("status") or "")
        if status not in STATES:
            continue
        entry.state = status
        entry.resolution = {
            "status": status,
            "resolved_at": resolved_at,
            "check": record.get("check") or "",
            "verifier": record.get("verifier") or "",
            "marker_removed": bool(record.get("marker_removed")),
            "note_id": record.get("note_id") or "",
        }
        if (status == mr.STATUS_RESOLVED_VERIFIED
                and _newer(entry.last_seen, resolved_at)):
            entry.reasked = True
    return out


def link_replies(entries: Mapping[str, FeedbackEntry],
                 ) -> Dict[str, FeedbackEntry]:
    """Hang each reply of ours off the ask it names. Mutates and returns.

    A reply naming an identity nothing pulled is left linked to nothing
    rather than dropped: it is still a note on the captain's timeline
    and hiding it would be this module deciding a record is wrong.
    """
    out = dict(entries)
    for entry in out.values():
        if entry.kind != KIND_REPLY or not entry.answers:
            continue
        answered = out.get(entry.answers)
        if answered is not None and entry.identity not in answered.answered_by:
            answered.answered_by.append(entry.identity)
    return out


def echoes(entries: Mapping[str, FeedbackEntry]) -> Dict[str, List[str]]:
    """Normalised words -> every reel they were typed on, where >1.

    One instruction typed onto four reels is one mechanism, not four
    edits, and this is the count that says so before the work is
    planned rather than after the fourth rebuild.

    Asks only. Our own replies repeat by design - the same sentence goes
    onto every reel it was applied to - and counting those as echoes
    would report our own tidiness as the captain repeating themselves.
    """
    by_words: Dict[str, List[str]] = {}
    for entry in entries.values():
        if entry.kind != KIND_ASK:
            continue
        by_words.setdefault(normalise_text(entry.text), []).append(entry.reel)
    return {words: sorted(set(reels))
            for words, reels in by_words.items() if len(set(reels)) > 1}


def build(project_folder, pulls=None, resolutions=None) -> dict:
    """The whole ledger document: entries, re-asks, echoes.

    Reads nothing but files this project already writes - the pull files
    `marker_feedback` records and the resolution records
    `marker_resolution` records - so running it costs no Resolve session
    and changes no state.
    """
    if resolutions is None:
        from library.tools import marker_resolution as mr
        resolutions = mr.all_resolutions(project_folder)
    entries = link_replies(
        apply_resolutions(collect(project_folder, pulls), resolutions))
    ordered = sorted(entries.values(),
                     key=lambda e: (e.first_asked, e.identity))
    asks = [e for e in ordered if e.kind == KIND_ASK]
    return {
        "format": LEDGER_FORMAT,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "entries": [e.as_dict() for e in ordered],
        # Counts are over ASKS. A reply of ours is on the timeline and
        # in `entries`, and it is not something the captain is waiting
        # on - counting it open would inflate the one number a reader
        # acts on.
        "open": [e.identity for e in asks if e.state == STATE_OPEN],
        "reasked": [e.identity for e in asks if e.reasked],
        "echoes": echoes(entries),
    }


# ── Where it lives ────────────────────────────────────────────────

def ledger_path(project_folder) -> Path:
    """Beside the pulls, in the CAPTURED area a rebuild cannot reach."""
    return ProjectLayout(project_folder).write_path(
        Area.MARKER_FEEDBACK, LEDGER_FILENAME)


def read_ledger(project_folder) -> Optional[dict]:
    path = ProjectLayout(project_folder).read_path(
        Area.MARKER_FEEDBACK, LEDGER_FILENAME)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                             # noqa: BLE001
        return None


def write_ledger(project_folder, document: Mapping) -> Path:
    """Write the ledger. It is DERIVED, so it is rewritten whole.

    Nothing is merged from the previous file: everything here is
    recomputed from the pulls and the resolution records, which are the
    durable halves. A ledger that accumulated state of its own would
    become the second source of truth this module exists not to be.
    """
    path = ledger_path(project_folder)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def refresh(project_folder) -> dict:
    """Rebuild the ledger from the project's own records and write it."""
    document = build(project_folder)
    write_ledger(project_folder, document)
    return document


# ── Reporting ─────────────────────────────────────────────────────

def _readable(text: str, limit: int = 96) -> str:
    """A note on one line, for a table.

    The first line of a marker's joined text is its NAME field - often
    just "feedback" - and the words the captain actually typed are in
    the second. So this joins rather than taking the first line, which
    printed nothing but the word "feedback" for every note on
    `lucie/geo-podcast`.
    """
    joined = re.sub(r"\s+", " ", text or "").strip()
    return joined[:limit] + ("..." if len(joined) > limit else "")


def render(document: Mapping) -> str:
    """The printable ledger: what is open, what came back, what echoes."""
    entries = list((document or {}).get("entries") or ())
    asks = [e for e in entries if (e.get("kind") or KIND_ASK) == KIND_ASK]
    replies = [e for e in entries if e not in asks]
    lines = [f"── Captain feedback ledger ({len(asks)} ask(s), "
             f"{len(replies)} reply of ours) ──"]
    if not entries:
        lines.append("  no note has been pulled from this project.")
        return "\n".join(lines)
    by_state: Dict[str, List[dict]] = {}
    for entry in asks:
        by_state.setdefault(entry.get("state") or STATE_OPEN,
                            []).append(entry)
    for state in STATES:
        held = by_state.get(state) or []
        if not held:
            continue
        lines.append(f"  {state}: {len(held)}")
        for entry in held:
            words = _readable(entry.get("text") or "")
            mark = "  RE-ASKED" if entry.get("reasked") else ""
            lines.append(f"      {entry['identity']}{mark}")
            lines.append(f"        {entry.get('reel', '')}: \"{words}\"")
            answered = entry.get("answered_by") or []
            if answered:
                lines.append(f"        answered by {len(answered)} reply "
                             f"marker(s) of ours")
            frames = entry.get("frames") or []
            if len(frames) > 1:
                lines.append(f"        read at frames "
                             f"{', '.join(str(f) for f in frames)} - one "
                             f"note, moved by a rebuild")
    unlinked = [e for e in replies if not e.get("answers")]
    if unlinked:
        lines.append(f"  {len(unlinked)} reply marker(s) of ours name no "
                     f"question - written before a reply recorded what it "
                     f"answered, so the ask they close cannot be told from "
                     f"the timeline.")
    reasked = list((document or {}).get("reasked") or ())
    if reasked:
        lines.append(f"  -> {len(reasked)} note(s) RE-ASKED after being "
                     f"recorded resolved_verified. A fix that was verified "
                     f"and asked for again did not hold.")
    for words, reels in sorted(((document or {}).get("echoes") or {}).items()):
        lines.append(f"  -> echoed on {len(reels)} reels: \"{words[:60]}\"")
        lines.append(f"        {', '.join(reels)} - one instruction, not "
                     f"{len(reels)} edits")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.feedback_ledger",
        description="What the captain asked for, and what happened to it.")
    parser.add_argument("project", help="path to the project folder")
    parser.add_argument("--write", action="store_true",
                        help="write the ledger into the project")
    parser.add_argument("--json", action="store_true",
                        help="print the ledger as JSON")
    args = parser.parse_args(argv)

    document = refresh(args.project) if args.write else build(args.project)
    print(json.dumps(document, indent=2) if args.json else render(document))
    if args.write:
        print(f"\nwritten: {ledger_path(args.project)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
