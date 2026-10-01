"""Step 3.04's repeated-take diagnostics travel as a REFERENCE, not a copy.

Measured 2026-10-01 on snapshot `reel-endtoend-before` (the geo podcast,
37 candidates), `o200k_base`: `select_reels` sent 49,777 tokens, and
19,850 of them were three per-candidate fields - `retake_candidates`
(11,649), `repetition_inside` (4,306) and `possible_retellings`
(3,895). They are the evidence a reel's `takes_dropped` verdict is drawn
from, and a verdict is only drawn for a stretch the model CHOOSES: the
run of record chose 19 of 37 and struck takes in 9. The other stretches'
evidence was carried in full on every call, just in case.

So the map travels inline - one heading per candidate that carries any,
titled with the candidate's own `start-end`, its lede counting what is
in it and whether the build removes it - and the evidence is one
`sed -n` away. The mechanism is `library/tools/brief_reference.py`,
unchanged; this module is the document's SHAPE, the same split
`footage_reference.footage_document` and `sfx_library.catalog_document`
make.

**Nothing is rewritten.** Each item is the `json.dumps` of the dict the
bridge built, one per line, so what the model reads at the path is the
same bytes it read in the table cell. The candidate rows themselves keep
every measurement column; only the three fields named in `FIELDS` leave
the prompt, through `-` paths in the step's `context_fields`. Every
post-bridge and `step.py` still receives the unprojected candidates.
"""

from __future__ import annotations

import json

FIELDS = ("repetition_inside", "retake_candidates", "possible_retellings")
"""The per-candidate fields that move into the document, in reading order."""

DOCUMENT_NAME = "reel_candidate_diagnostics.md"

REFERENCE_DOCUMENT_NAME = (
    "The repeated-take evidence for each candidate (`repetition_inside`, "
    "`retake_candidates`, `possible_retellings`)")
REFERENCE_WHY = (
    "a `takes_dropped` verdict is drawn only for a stretch you choose, so "
    "read the section of every candidate you choose or consider before you "
    "write its verdict. A candidate with no heading here carries none of "
    "the three")


def _lede(candidate: dict) -> str:
    """What the section holds, in words.

    In words rather than field names because the map's lede is passed
    through `brief_reference._lede`, which strips `_` as markup.
    """
    parts = []
    runs = candidate.get("repetition_inside") or []
    if runs:
        removed = sum(1 for run in runs if run.get("build_removes_it"))
        parts.append(f"{len(runs)} repeated run(s) inside, the build "
                     f"removes {removed}")
    retakes = candidate.get("retake_candidates") or []
    if retakes:
        parts.append(f"{len(retakes)} retake candidate(s)")
    retold = candidate.get("possible_retellings") or []
    if retold:
        parts.append(f"{len(retold)} possible retelling(s)")
    return "; ".join(parts)


def carries_diagnostics(candidates) -> bool:
    """True when any candidate carries a field this document holds."""
    return any(candidate.get(field)
               for candidate in candidates or () for field in FIELDS)


def diagnostics_document(candidates) -> str:
    """Every candidate's repeated-take evidence, as one markdown document.

    One `##` section per candidate that carries any, titled `start-end`
    exactly as the candidate row prints them, so the row and its section
    are joined by the two numbers the model already reads.
    """
    out = [
        "# Repeated-take evidence per reel candidate",
        "",
        ("One section per candidate that carries any, titled with the "
         "candidate's own `start-end` in seconds of the cut. Each item is "
         "one JSON object per line, exactly as the bridge measured it."),
        "",
    ]
    for candidate in candidates or ():
        if not any(candidate.get(field) for field in FIELDS):
            continue
        out.append(f"## {candidate['start']}-{candidate['end']}")
        out.append(_lede(candidate))
        out.append("")
        for field in FIELDS:
            items = candidate.get(field) or []
            if not items:
                continue
            out.append(f"{field}:")
            out.extend(json.dumps(item, ensure_ascii=False) for item in items)
            out.append("")
    return "\n".join(out).rstrip() + "\n"
