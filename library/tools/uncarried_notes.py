"""A promotion that drops the captain's note owes him an answer, on disk.

The defect this closes
----------------------
The promotion path reported a note it was about to lose, twice, and both
copies were discarded before anything could act.
`marker_carry.report` writes each uncarried note to stderr by name with
the captain's words, deliberately. `step_7_02_verify_reels` then read
only `promoted["organised"]` and `promoted["promoted"]` off the promotion
record and never touched `promoted["markers"]`, which carries the same
losses as structured data. Measured 2026-09-20: a reel's blue was
correctly identified as unresolvable and named on stderr with the
captain's words. No durable record survived, nobody acted, and the note
was gone for roughly forty minutes until an external count found it.

What this is, and the two decisions it implements
------------------------------------------------
`step_7_02` feeds `promoted["markers"]["uncarried"]` to `record` here,
and this files one OBLIGATION per dropped note: the reel it belongs to,
the captain's words verbatim, why the carry refused, and where the
seam re-placement put the Blue back (or that re-placement was declined,
so these words survive only here).

RECORD-AND-CONTINUE, never fail. An uncarried note is often the CORRECT
outcome: a note pinned to a take the captain asked to have removed
cannot carry anywhere, and refusing to guess a position is right - a
marker silently re-anchored to the wrong item is worse than one honestly
reported missing (`marker_carry` keeps its refusal; nothing here guesses
either). Failing every legitimate uncarry would train people to bypass
the gate, which is how a safety feature becomes a formality.

THE RECORD IS AN OBLIGATION, NOT AN ENTRY. A JSON file nobody reads and
a stderr line nobody reads differ only in how long the evidence
survives. So an open obligation blocks the reel's completion:
`reel_signoff.sign_off` refuses while one is open, and only an explicit
`discharge` - naming the obligation and saying what happened to the
note - releases it. A discharge with no stated reason is refused: that
is the bypass wearing the uniform.

What is filed, and what is not
------------------------------
Genuine asks only. `plan_carry` emits two kinds of uncarried entry:
asks (no `pairing` key - the captain's words) and replies of OURS
(`pairing` of `stranded` or `independent-*` - our green answering a note
that is itself uncarried, or binding to nothing). A stranded reply
re-filed here would file our answer text as a Blue note of his with a
fresh Green beside it - the same reason the seam re-placement already
skips them. They stay reported on stderr and on the retired backup, and
the ask they follow carries the obligation. Anything unmarked is filed
as his: the default runs the same way `feedback_ledger` runs it, because
treating one of our replies as an open question is noise and treating
the captain's question as our reply LOSES IT.

The clip plane is never read here because `marker_carry` never reads
it. That is a separate row.

Where it lives
--------------
`pipeline_output/review/uncarried_notes.json`, beside the sign-offs and
the holds, on the version-control allow-list's `review/**` line - so no
allow-list change was needed. `{"open": {identity: entry},
"discharged": [entries]}`: a discharge moves the entry, never deletes
it, and a note dropped AGAIN after discharge reopens with its prior
discharge kept in `history` - a discharge said that instance was
handled, and a new drop is a new fact.

`tests/test_uncarried_notes.py`.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Mapping

NOTES_FILENAME = "uncarried_notes.json"
NOTES_FORMAT = "uncarried_notes/1"

#: Pairings `plan_carry` puts on uncarried entries that are OUR reply
#: markers, never the captain's words. Skipped by `record` - they stay
#: reported on stderr (`marker_carry.report`), never filed as his.
#: Anything else unmarked files as his (see the module docstring).
_REPLY_PAIRINGS = frozenset({
    "stranded",
    "independent-legacy",
    "independent-unpaired",
    "independent-unclaimed",
})


class UncarriedNotesUnreadable(RuntimeError):
    """The obligation file exists but cannot be parsed, and this says so."""


class UncarriedNotesOpen(RuntimeError):
    """This reel has open uncarried-note obligations, so it is not done.

    Carries `.reel` and `.open` (the obligation entries). A sign-off
    says the reel is done, and a reel with the captain's words
    unaccounted for is not done - discharge each one first.
    """

    def __init__(self, message: str, reel: str = "",
                 open: list | None = None) -> None:
        super().__init__(message)
        self.reel = reel
        self.open = list(open or ())


class UncarriedNoteUnknown(RuntimeError):
    """No open obligation matches this discharge, and this says which do."""


class DischargeRefused(RuntimeError):
    """A discharge needs a stated reason, and none was given."""


def notes_path_for(project_folder) -> str:
    """`pipeline_output/review/uncarried_notes.json` - beside the holds."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        NOTES_FILENAME)


def _empty() -> dict:
    return {"format": NOTES_FORMAT, "open": {}, "discharged": []}


def read_notes(project_folder) -> dict:
    """The obligation document, or an empty one.

    An unreadable file REFUSES, never reads as "nothing is owed": an
    obligation record that cannot be parsed reads exactly like no
    obligations, and the sign-off gate would then approve the reel the
    record was holding.
    """
    path = notes_path_for(project_folder)
    if not os.path.exists(path):
        return _empty()
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise UncarriedNotesUnreadable(
            f"{path} exists but could not be read ({unreadable}). An "
            f"obligation record that cannot be read must REFUSE, never "
            f"be replaced with an empty one.") from unreadable
    if (not isinstance(document, dict)
            or not isinstance(document.get("open"), dict)
            or not isinstance(document.get("discharged"), list)):
        raise UncarriedNotesUnreadable(
            f"{path} is not an uncarried-notes document ({NOTES_FORMAT}).")
    return document


def _write(project_folder, document: Mapping) -> str:
    path = notes_path_for(project_folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps(dict(document), indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    handle, staged = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=".uncarried-", suffix=".json")
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_genuine(entry: Mapping) -> bool:
    """Whether this uncarried entry is the captain's words, not ours."""
    pairing = (entry or {}).get("pairing")
    if pairing is None:
        return True
    if pairing in _REPLY_PAIRINGS:
        return False
    if (isinstance(pairing, str)
            and pairing.startswith("independent-")):
        return False
    return True


def _seam_outcome(entry: Mapping, replaced: Mapping,
                  declined: Mapping) -> dict | None:
    """Where this note's Blue went back, or that it went nowhere.

    Joined by frame off the promotion's own seam record - one marker per
    frame, so the frame is the key. None when the promotion carries no
    seam record for it: an honest absence, not a guess.
    """
    frame = (entry or {}).get("frame")
    if frame in (replaced or {}):
        plan = replaced[frame] or {}
        return {
            "replaced_at": plan.get("seam"),
            "ambiguous": bool(plan.get("ambiguous")),
            "explanation": str(plan.get("explanation") or ""),
            "reply_frame": plan.get("reply_frame"),
        }
    if frame in (declined or {}):
        plan = declined[frame] or {}
        return {
            "declined": True,
            "seam": plan.get("seam"),
            "why": str(plan.get("why") or ""),
        }
    return None


def record(project_folder, markers) -> dict:
    """File one obligation per dropped captain's note. Never raises.

    `markers` is `promoted["markers"]`: `{final timeline name:
    {"carried": [...], "uncarried": [...], "replaced": [...],
    "replace_declined": [...]}}` (`reel_build.promote_staged_reels`).
    Missing, empty, or all-carried files nothing and writes nothing -
    a promotion that carried everything leaves no obligation.

    Idempotent: reporting the same words again bumps `occurrences`,
    never duplicates. A note dropped again AFTER discharge reopens with
    its prior discharge kept in `history`.

    Returns `{"filed", "reopened", "open", "path"}` - JSON-plain, so a
    step may carry it on its own record. `path` is None when nothing
    was written.
    """
    from library.tools import feedback_ledger as _ledger

    current = read_notes(project_folder)
    filed, reopened = [], []
    touched = False
    for final, notes in (markers or {}).items():
        notes = notes or {}
        replaced = {m.get("frame"): m for m in (notes.get("replaced") or ())
                    if isinstance(m, Mapping)}
        declined = {m.get("frame"): m
                    for m in (notes.get("replace_declined") or ())
                    if isinstance(m, Mapping)}
        for entry in (notes.get("uncarried") or ()):
            if not isinstance(entry, Mapping) or not _is_genuine(entry):
                continue
            text = _ledger.note_text(entry)
            if not _ledger.normalise_text(text):
                continue  # a marker with no words asks nothing
            reel = _ledger.base_reel_name(str(final or ""))
            identity = _ledger.durable_identity(str(final or ""), text)
            if identity in current["open"]:
                held = current["open"][identity]
                held["occurrences"] = int(held.get("occurrences") or 1) + 1
                held["last_reported_at"] = _now()
                outcome = _seam_outcome(entry, replaced, declined)
                if outcome is not None:
                    held["seam"] = outcome
                touched = True
                continue
            previously = [e for e in current["discharged"]
                          if e.get("identity") == identity]
            if previously:
                entry_out = dict(previously[-1])
                entry_out["discharged"] = None
                entry_out["occurrences"] = int(
                    entry_out.get("occurrences") or 1) + 1
                entry_out["last_reported_at"] = _now()
                entry_out.setdefault("history", []).append({
                    "reopened_at": _now(),
                    "prior_discharge":
                        (previously[-1] or {}).get("discharged"),
                    "why": ("reported uncarried by a later promotion - "
                            "the discharge answered that instance, and "
                            "this drop is a new fact"),
                })
                outcome = _seam_outcome(entry, replaced, declined)
                if outcome is not None:
                    entry_out["seam"] = outcome
                current["open"][identity] = entry_out
                reopened.append(identity)
                continue
            current["open"][identity] = {
                "identity": identity,
                "reel": reel,
                "timeline": str(final or ""),
                "color": str(entry.get("color") or ""),
                "name": str(entry.get("name") or ""),
                "note": str(entry.get("note") or ""),
                "text": text,
                "frame": entry.get("frame"),
                "why": str(entry.get("why") or ""),
                "seam": _seam_outcome(entry, replaced, declined),
                "reported_at": _now(),
                "last_reported_at": _now(),
                "occurrences": 1,
                "history": [],
                "discharged": None,
            }
            filed.append(identity)
    path = None
    # A re-report that only bumps `occurrences` still writes: the bump
    # is a new fact (the note was dropped AGAIN), and a write skipped
    # here would silently discard it.
    if filed or reopened or touched:
        current["format"] = NOTES_FORMAT
        path = _write(project_folder, current)
    return {
        "filed": filed,
        "reopened": reopened,
        "open": {reel: sorted(
            identity for identity, entry in current["open"].items()
            if entry.get("reel") == reel)
            for reel in sorted(
                {entry.get("reel", "") for entry in
                 current["open"].values()})},
        "path": path,
    }


def open_for(project_folder, reel=None) -> list:
    """Every open obligation, oldest first; narrowed to one reel when named."""
    from library.tools import feedback_ledger as _ledger

    document = read_notes(project_folder)
    wanted = (_ledger.base_reel_name(str(reel))
              if reel is not None else None)
    return sorted(
        (entry for entry in document["open"].values()
         if wanted is None or entry.get("reel") == wanted),
        key=lambda entry: (entry.get("reported_at") or "",
                           entry.get("identity") or ""))


def discharge(project_folder, reel: str, identity: str, *,
              by: str = "captain", note: str = "") -> dict:
    """Discharge one open obligation, deliberately. Never deletes it.

    Names the obligation by identity and says what happened to the
    note - "answered at the re-placed Blue", "pinned to the removed
    take, correctly dropped" - because an obligation discharged with no
    stated reason is the bypass wearing the uniform, and that refusal
    is `DischargeRefused`. An identity nothing owes is
    `UncarriedNoteUnknown`, naming what IS open rather than clearing
    nothing silently.
    """
    from library.tools import feedback_ledger as _ledger

    if not str(note or "").strip():
        raise DischargeRefused(
            "discharging an uncarried note needs a stated reason - say "
            "what happened to the captain's words (answered, correctly "
            "dropped, re-typed). A discharge with no reason is a bypass.")
    document = read_notes(project_folder)
    base = _ledger.base_reel_name(str(reel or ""))
    entry = document["open"].get(str(identity or ""))
    if entry is None or entry.get("reel") != base:
        owed = open_for(project_folder, base)
        lines = [f"no open uncarried-note obligation {identity!r} on "
                 f"{base!r}."]
        for owed_entry in owed:
            lines.append(
                f"  open: {owed_entry['identity']} - "
                f"\"{_readable(owed_entry.get('text') or '')}\"")
        if not owed:
            lines.append(f"  {base!r} owes nothing - nothing discharged.")
        raise UncarriedNoteUnknown("\n".join(lines))
    entry["discharged"] = {
        "at": _now(),
        "by": str(by or "captain"),
        "note": str(note),
    }
    del document["open"][str(identity)]
    document["discharged"].append(entry)
    _write(project_folder, document)
    return entry


def _readable(text: str, limit: int = 96) -> str:
    import re as _re

    joined = _re.sub(r"\s+", " ", text or "").strip()
    return joined[:limit] + ("..." if len(joined) > limit else "")


def refusal_message(project_folder, reel: str, open: list) -> str:
    """Why the sign-off refuses, and the discharge that proceeds.

    Names the reel, quotes the captain's words verbatim, and prints the
    exact command - the same declare-then-proceed shape the sign-off's
    own promotion refusal takes.
    """
    lines = [
        f"REFUSING to sign off {reel!r}: {len(open)} uncarried note(s) "
        f"from its promotion are still open. A sign-off says the reel "
        f"is done, and a reel with the captain's words unaccounted for "
        f"is not done. Nothing was signed; the reel is unaffected."]
    for entry in open:
        words = _readable(entry.get("text") or "")
        seam = entry.get("seam") or {}
        if seam.get("replaced_at") is not None:
            where = (f"the Blue is back at frame {seam['replaced_at']}"
                     + (" (seam ambiguous: start of the replacing item)"
                        if seam.get("ambiguous") else ""))
        elif seam.get("declined"):
            where = ("re-placement was DECLINED "
                     f"({seam.get('why') or 'no reason recorded'}) - "
                     f"these words survive only here")
        else:
            where = "no seam record - see the promotion's stderr report"
        lines.append(f"  {entry.get('reel')}: \"{words}\"")
        lines.append(f"    {entry.get('why') or 'no reason recorded'}; "
                     f"{where}.")
        lines.append(
            f"    To discharge deliberately: "
            f"`manage_project.py discharge-uncarried {project_folder} "
            f"{entry.get('reel')!r} --identity {entry['identity']} "
            f"--note 'what happened to these words'`.")
    return "\n".join(lines)


def assert_none_open(project_folder, timeline: str) -> None:
    """Raise unless this reel owes nothing. The completion gate.

    Called by `reel_signoff.sign_off` before anything is written: a
    sign-off is the act that says a built reel is done, and an open
    uncarried-note obligation says one of the captain's notes is still
    unaccounted for. No file - or a file with nothing open on this reel -
    passes silently, so reels that never dropped a note sign off exactly
    as before.
    """
    from library.tools import feedback_ledger as _ledger

    reel = _ledger.base_reel_name(str(timeline or ""))
    owed = open_for(project_folder, reel)
    if owed:
        raise UncarriedNotesOpen(
            refusal_message(project_folder, reel, owed),
            reel=reel, open=owed)


def describe(project_folder, reel=None) -> str:
    """One printable account of what is owed and what was discharged."""
    owed = open_for(project_folder, reel)
    document = read_notes(project_folder)
    lines = [f"-- Uncarried notes ({len(owed)} open) --"]
    if not owed and not document["discharged"]:
        lines.append("  no promotion has dropped a captain's note.")
        return "\n".join(lines)
    for entry in owed:
        lines.append(f"  OPEN {entry.get('reel')}: \"{_readable(entry.get('text') or '')}\"")
        lines.append(f"    {entry['identity']} (reported "
                     f"{str(entry.get('reported_at') or '')[:19].replace('T', ' ')})")
    return "\n".join(lines)
