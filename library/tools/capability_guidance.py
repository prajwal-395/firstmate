"""Structured guidance for an operation Ren cannot serve.

The captain's principle: *"Don't make the model responsible for
understanding the internal machinery of editing. Make the machinery
expose clear, deterministic, composable capabilities that the model can
reason about."*  A refusal that says only "unknown capability 'x'"
makes the model reason about the machinery: it guesses at ids, tries
them, and reads prose to find out what exists.  Guidance is the
structured alternative - the same shape on every interface, whether the
question was asked of `ren` or of `resolve-axi`.

One vocabulary, one shape
-------------------------

Every guidance record carries the same six fields, so a host reads one
answer whichever interface produced it:

    status        what kind of gap this is (see `STATUSES`)
    asked         the id the caller asked for
    what          the consequence, in plain words
    why           why it refused
    fix           the exact next step
    next_action   the command to run for that next step
    alternatives  the ids that ARE available, nearest first
    supported     the ids that are available here, in full

`status` is the machine-readable field; the prose fields are what a
host relays to the editor.  `alternatives` is what keeps the model from
guessing: an unknown id comes back with the nearest real ids, never
with a bare "no".

`closest_ids` is the matcher behind `alternatives`: a typo, a
remembered name from another vocabulary, and a near-miss domain word all
land on the right capability instead of on nothing.
"""

from __future__ import annotations

import difflib

#: The id is not a capability Ren holds.
UNKNOWN = "unknown"

#: The capability exists, and this machine cannot perform it.
UNAVAILABLE = "unavailable"

#: The capability exists and performs, but degraded - an optional need
#: it can run without is missing.
DEGRADED = "degraded"

#: The operation is implemented but carries no live qualification
#: evidence, so it may not be used in production.
UNQUALIFIED = "unqualified"

#: The operation is qualified but not enabled for production routing.
NOT_PRODUCTION_ENABLED = "not_production_enabled"

#: The need behind the operation could not be measured at all - an
#: undeterminable answer is not a measured absence.
UNDETERMINED = "undetermined"

STATUSES = (UNKNOWN, UNAVAILABLE, DEGRADED, UNQUALIFIED,
            NOT_PRODUCTION_ENABLED, UNDETERMINED)

#: The statuses that mean "this cannot be done as asked".
REFUSING = (UNKNOWN, UNAVAILABLE, UNQUALIFIED, NOT_PRODUCTION_ENABLED)


def guidance(status: str, asked: str, what: str, why: str, fix: str,
             *, next_action: str = "", alternatives=(),
             supported=()) -> dict:
    """One guidance record, in the shape every interface answers with.

    `alternatives` are `{id, summary, verdict}` rows - the ids that ARE
    available, nearest first, so a caller is never left guessing what
    to ask for instead.  `supported` is the whole available set, for a
    caller that wants to enumerate.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown guidance status {status!r}; "
                         f"expected one of {list(STATUSES)}")
    return {
        "status": status,
        "asked": asked,
        "what": what,
        "why": why,
        "fix": fix,
        "next_action": next_action,
        "alternatives": [dict(row) for row in alternatives],
        "supported": list(supported),
    }


def closest_ids(asked: str, known, limit: int = 5) -> list[str]:
    """The known ids nearest `asked`, nearest first.

    A capability id is dotted (`reel.build`), so a caller that types
    `reel build`, `reel_build` or `reelbuild` means the same thing.
    Each spelling is compared, and the best match wins.
    """
    known = list(known)
    if not known:
        return []
    spellings = {asked, asked.replace(".", ""), asked.replace(".", " "),
                 asked.replace("_", "."), asked.replace(" ", ".")}
    scored: list[tuple[float, str]] = []
    for candidate in known:
        forms = {candidate, candidate.replace(".", ""),
                 candidate.replace(".", " "), candidate.replace(" ", ".")}
        best = max(difflib.SequenceMatcher(None, form, spelling).ratio()
                    for form in spellings for spelling in forms)
        scored.append((best, candidate))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [name for score, name in scored[:limit] if score > 0.34]


def render(guidance_record: dict) -> str:
    """The prose a host relays: the consequence first, then the detail.

    The first line is the plain consequence, the same shape
    `RenRefusal.render` uses, so a host relays one line and keeps the
    detail for the editor who asks.
    """
    lines = [f"ren: refused - {guidance_record['what']}",
             f"  why: {guidance_record['why']}",
             f"  fix: {guidance_record['fix']}"]
    alternatives = guidance_record.get("alternatives") or []
    if alternatives:
        names = ", ".join(str(row.get("id")) for row in alternatives)
        lines.append(f"  available instead: {names}")
    if guidance_record.get("next_action"):
        lines.append(f"  next: {guidance_record['next_action']}")
    return "\n".join(lines)
