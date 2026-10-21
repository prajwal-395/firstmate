"""BASE-003 - the representative editorial validation corpus, and its halves.

The corpus is DATA: briefs a person wrote in their own words, the
footage they were written against, and the answer key the evaluator
holds. Three things live here, and the split between them is the point:

- ``sealed/`` carries the answer key. Nothing the engine ships reads
  this directory, and a test says so.
- ``public/`` carries what a run is allowed to see: the instruction, the
  project state, the acceptance criteria, and nothing else. It is
  DERIVED from the sealed half by :func:`public_projection`, so it cannot
  drift from the key it hides and cannot carry it by accident.
- the shape of the corpus - how many briefs there are, what they ask
  for, how many of them rest on a premise the footage carries - is
  published, because a corpus that says what it does not cover is worth
  more than one that looks complete.

The measurement the corpus is built for: Ren is evaluated on its ability
to COMPLETE MEANINGFUL EDITS, rather than on whether it rejects
requests it cannot obey. So every brief that asks for an edit rests on a
premise the footage actually carries, and every brief whose correct
answer is to report back names WHICH kind of report is correct - refuse,
ask, or report what could not be determined.

Nothing here touches Resolve, a model or a network: it is data in, data
out.

    python3 evaluation/tools/corpus.py --shape
        the corpus, its counts per category, and its premise coverage

    python3 evaluation/tools/corpus.py --write-public
        regenerate public/ from sealed/ and check it against what is
        committed

    python3 evaluation/tools/corpus.py --audit
        every brief, the substrate it pairs with, and the state of each
        premise it rests on
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.editorial_acceptance import briefs, footage

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
SEALED_DIR = CORPUS_ROOT / "sealed"
PUBLIC_DIR = CORPUS_ROOT / "public"

SEALED_BRIEFS = SEALED_DIR / "brief_set.json"
SEALED_SUBSTRATES = SEALED_DIR / "substrates.json"
SEALED_EVIDENCE = SEALED_DIR / "evidence.json"
PUBLIC_BRIEFS = PUBLIC_DIR / "brief_set.json"
PUBLIC_MANIFEST = PUBLIC_DIR / "manifest.json"

PUBLIC_SCHEMA = "ren-editorial-corpus-public/1"

#: The categories the corpus has to cover. A category nobody wrote a
#: brief for is a gap the corpus says out loud, not one it hides.
CATEGORIES: tuple[str, ...] = (
    "podcast_edit",
    "interview_edit",
    "single_camera_edit",
    "broll_heavy_edit",
    "caption_heavy_edit",
    "heavily_modified_timeline",
    "external_modification",
    "negative_refuse",
    "negative_decision",
    "negative_insufficient_evidence",
)

#: The outcomes a brief can have. The three negative ones are separate
#: outcomes, not one: a refusal, a question and a report of what could
#: not be determined are three different correct answers.
OUTCOMES: tuple[str, ...] = ("edit", "refuse", "ask", "report")


class CorpusError(ValueError):
    """The corpus violates its own contract."""


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")


def load_sealed_briefs(path: Path | None = None) -> dict[str, Any]:
    """The sealed brief set, validated by the ENGINE's own contract.

    The corpus is not a second harness: its briefs are the harness's
    briefs, so the harness's validator is the gate that a brief has to
    pass to be added.
    """
    target = path or SEALED_BRIEFS
    raw = _read(target)
    problems = briefs.validate_brief_set(raw)
    if problems:
        raise CorpusError(
            "the sealed brief set violates the harness's contract:\n  "
            + "\n  ".join(problems))
    raw["path"] = str(target)
    return raw


def load_sealed_substrates(path: Path | None = None) -> dict[str, Any]:
    """The sealed substrate catalogue, validated the same way."""
    target = path or SEALED_SUBSTRATES
    raw = _read(target)
    problems = footage.validate_catalogue(raw)
    if problems:
        raise CorpusError(
            "the sealed substrate catalogue violates its contract:\n  "
            + "\n  ".join(problems))
    raw["path"] = str(target)
    return raw


def public_projection(brief_set: Mapping[str, Any]) -> dict[str, Any]:
    """What a run may read of the corpus: the public half of every brief.

    Built from the harness's own ``public_view``, so the public half is
    the harness's idea of it and not a second opinion about it. The
    answer key cannot survive this step: ``public_view`` is built key by
    key, and the key is not one of the keys it copies.
    """
    return {
        "schema": PUBLIC_SCHEMA,
        "corpus_id": str(brief_set.get("corpus_id") or ""),
        "description": (
            "The public half of the representative editorial validation "
            "corpus: what a run is handed. The answer key is sealed "
            "beside it and is never shown to a run."),
        "briefs": [briefs.public_view(brief) for brief in brief_set["briefs"]],
    }


def shape(brief_set: Mapping[str, Any]) -> dict[str, Any]:
    """What the corpus is made of, counted.

    The counts are the corpus's own report, and they are published:
    a corpus that says which categories it does not cover is worth more
    than one that looks complete.
    """
    briefs_ = list(brief_set["briefs"])
    categories: dict[str, int] = {category: 0 for category in CATEGORIES}
    outcomes: dict[str, int] = {outcome: 0 for outcome in OUTCOMES}
    shapes: dict[str, int] = {}
    edit_briefs: list[str] = []
    negative_briefs: list[str] = []
    for brief in briefs_:
        category = str(brief.get("category") or "")
        outcome = str(brief.get("outcome") or "")
        if category in categories:
            categories[category] += 1
        if outcome in outcomes:
            outcomes[outcome] += 1
        shape_name = str(brief.get("footage_shape") or "")
        shapes[shape_name] = shapes.get(shape_name, 0) + 1
        (edit_briefs if outcome == "edit" else negative_briefs).append(
            str(brief["id"]))
    return {
        "corpus_id": str(brief_set.get("corpus_id") or ""),
        "brief_count": len(briefs_),
        "category_counts": categories,
        "categories_missing": [category for category in CATEGORIES
                               if not categories[category]],
        "outcome_counts": outcomes,
        "footage_shape_counts": shapes,
        "edit_briefs": edit_briefs,
        "negative_briefs": negative_briefs,
        "edit_share": (round(len(edit_briefs) / len(briefs_), 4)
                       if briefs_ else 0.0),
    }


def premise_audit(brief_set: Mapping[str, Any],
                   catalogue: Mapping[str, Any]) -> dict[str, Any]:
    """Every brief, the substrate it pairs with, and its premises.

    A brief that asks for an edit has to rest on a premise the footage
    CARRIES; a brief whose correct answer is a report has to rest on a
    premise the footage does NOT carry, or the report would be wrong.
    Either way the premise has to be settled - neither carried nor
    missing, but unknown, is not good enough for a brief that is going
    to be scored.
    """
    rows: list[dict[str, Any]] = []
    for brief in brief_set["briefs"]:
        key = briefs.oracle(brief)
        selection = footage.select_substrate(brief, catalogue)
        chosen = selection.get("selected")
        substrate = None
        if chosen:
            substrate = next((entry for entry in footage.substrates(catalogue)
                              if str(entry.get("id")) == chosen), None)
        states = []
        for requirement in key.get("footage_requirements") or ():
            states.append({
                "feature": requirement.get("feature"),
                "presence": requirement.get("presence"),
                "state": (footage.requirement_state(requirement, substrate)
                          if substrate else footage.REQUIREMENT_UNKNOWN),
            })
        settled = all(row["state"] == footage.SATISFIED for row in states)
        rows.append({
            "brief_id": str(brief["id"]),
            "category": str(brief.get("category") or ""),
            "outcome": str(brief.get("outcome") or ""),
            "expects_refusal": bool(key.get("expected_refusal")),
            "substrate": chosen,
            "substrate_profile": (str(substrate.get("profile") or "")
                                  if substrate else None),
            "profile_matches": bool(selection.get("profile_matches")),
            "requirements": states,
            "premise_settled": settled,
        })
    return {
        "brief_count": len(rows),
        "settled": sum(1 for row in rows if row["premise_settled"]),
        "unsettled": [row["brief_id"] for row in rows
                      if not row["premise_settled"]],
        "rows": rows,
    }


def write_public(brief_set: Mapping[str, Any]) -> dict[str, Any]:
    """Regenerate the public half and say whether it drifted.

    The public half is derived from the sealed one, so a change to a
    brief that does not reach the public half (a key, a category) leaves
    it alone, and a change to the instruction does not.
    """
    projection = public_projection(brief_set)
    previous = _read(PUBLIC_BRIEFS) if PUBLIC_BRIEFS.exists() else None
    _write(PUBLIC_BRIEFS, projection)
    manifest = {
        "schema": "ren-editorial-corpus-manifest/1",
        "corpus_id": str(brief_set.get("corpus_id") or ""),
        "shape": shape(brief_set),
        "public_briefs_sha256": hashlib.sha256(
            PUBLIC_BRIEFS.read_bytes()).hexdigest(),
    }
    _write(PUBLIC_MANIFEST, manifest)
    return {
        "public_briefs": str(PUBLIC_BRIEFS),
        "manifest": str(PUBLIC_MANIFEST),
        "drifted": previous is not None and previous != projection,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus.py",
        description="The representative editorial validation corpus.")
    parser.add_argument("--shape", action="store_true",
                        help="print the corpus shape and its counts")
    parser.add_argument("--audit", action="store_true",
                        help="print every brief's substrate and premises")
    parser.add_argument("--write-public", action="store_true",
                        help="regenerate the public half from the sealed one")
    args = parser.parse_args(argv)

    brief_set = load_sealed_briefs()
    catalogue = load_sealed_substrates()
    if args.write_public:
        written = write_public(brief_set)
        print(json.dumps(written, indent=2, sort_keys=True))
        return 0
    if args.audit:
        print(json.dumps(premise_audit(brief_set, catalogue), indent=2,
                         sort_keys=True))
        return 0
    print(json.dumps(shape(brief_set), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
