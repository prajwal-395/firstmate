"""From a user note to the edit decisions it touches.

The captain's spec (2026-10-06, s10): "Client says the intro feels
slow." Ren interprets that comment, identifies the edit decisions
implicated by it, proposes changes, creates V7, and preserves V6.

Three steps, three owners
-------------------------
1. The GRAPH supplies candidates: the decided reaction cutaways
   anchored to the note's clips and times, read through the landed
   editorial-findings query. A finding recorded before decisions
   existed carries none and supplies no candidate, and a note with no
   anchor supplies none either - never the whole graph.
2. The MODEL judges the match: a caller-injected `judge` reads the
   note's words against each candidate's reason and evidence and names
   the implicated ones with why. No judge means every candidate
   returns UNEVALUATED rather than guessed - the `transcript_hygiene`
   rule: direct provider calls were removed, so a host harness judges
   through its own channel and tests inject a fake.
3. The PROPOSAL names the change: the implicated decisions, the note's
   own words as the direction (quoted, never paraphrased into a fix -
   the concrete edit is taste and belongs to the model), the preserved
   parent version read off the versions ledger, and the proposed new
   version number. The new version is PROPOSED, never written: the
   ledger is history, and a proposal that wrote history would be the
   change wearing its proposal's clothes.

Why a new module, and what reads it: no existing module answers note
-> decisions. `marker_routing` answers note -> steps, `feedback_ledger`
answers a note's durable identity, `project_graph` answers queries.
This composes the three. The co-editor agent reads it through the CLI,
which judges nothing and writes nothing:

    python3 -m library.tools.decision_implication propose \
        --project <dir> --reel <reel> --text "make this more serious" \
        [--clip <clip_id>]... [--t <seconds>]...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools.ren_refusal import RenRefusal  # noqa: E402


class ImplicationRefused(RenRefusal):
    """A judge's verdict that cannot be honoured as written.

    A verdict naming a decision the candidates never supplied, an
    implication with no reason, or an answer that is not JSON is
    refused whole - never partially applied. Half a judgement applied
    to the wrong decision is a guess with a signature.
    """


def _anchor_spans(times) -> list:
    """Each stated time as a (start, end) span. A point is a zero-length
    span; anything else is skipped, never honoured as zero."""
    spans = []
    for moment in times or ():
        if isinstance(moment, bool):
            continue
        if isinstance(moment, (int, float)):
            spans.append((moment, moment))
        elif (isinstance(moment, (list, tuple)) and len(moment) == 2
                and all(isinstance(v, (int, float))
                        and not isinstance(v, bool) for v in moment)):
            spans.append((min(moment), max(moment)))
    return spans


def _anchored(record: dict, clip_ids: set, spans: list) -> bool:
    """Whether a finding record touches the note's clips or times.

    Clips match the speech clip or the reaction clip the record names.
    Times overlap the speech span the decision reacts to: a span the
    record cannot place in time matches nothing. Either anchor
    suffices - a clip note and a moment note arrive by different
    routes and each must work alone.
    """
    for key in ("speech_clip_id", "reaction_clip_id"):
        if record.get(key) and record[key] in clip_ids:
            return True
    start = record.get("speech_timeline_start")
    end = record.get("speech_timeline_end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        for span_start, span_end in spans:
            if start <= span_end and span_start <= end:
                return True
    return False


def candidates_for_note(graph: dict, clip_ids=(), times=()) -> list:
    """The decision rows anchored to the note's clips and times.

    Reads the decided reaction cutaways through the landed
    editorial-findings query and keeps the ones the anchors touch,
    deduplicated by node id. A finding recorded before decisions
    existed carries none and is skipped; an anchor that is empty is
    skipped, never honoured as zero. Never raises on the anchors'
    content - no anchor means no candidates.
    """
    from library.tools import project_graph

    if not isinstance(graph, dict):
        raise TypeError("candidates_for_note needs the project graph")
    clips = {c for c in clip_ids or ()
             if c and isinstance(c, str)}
    spans = _anchor_spans(times)
    if not clips and not spans:
        return []
    rows = []
    for finding in project_graph.find_editorial_findings(
            graph, project_graph.FINDING_REACTION_CUTAWAY):
        record = finding.get("record") or {}
        decision = record.get("decision")
        if not isinstance(decision, dict):
            continue
        if not _anchored(record, clips, spans):
            continue
        rows.append({
            "node_id": finding["node_id"],
            "speech_block_position": record.get("speech_block_position"),
            "speech_clip_id": record.get("speech_clip_id"),
            "reaction_clip_id": record.get("reaction_clip_id"),
            "speech_timeline_start": record.get("speech_timeline_start"),
            "speech_timeline_end": record.get("speech_timeline_end"),
            "reaction_land": (record.get("timing") or {}).get(
                "reaction_land"),
            "decision": decision,
        })
    return rows


def judge_candidates(note_text: str, candidates: list, judge=None) -> dict:
    """Sort the candidates into implicated / unaffected / unevaluated.

    `judge(note_text, candidates)` is the injected model: it answers
    with a JSON object carrying an `implicated` list of
    `{"node_id", "why"}`. The `why` is the judge's reasoning, kept
    verbatim. With no judge every candidate returns UNEVALUATED - the
    match is unjudged, never guessed.
    """
    node_ids = [row["node_id"] for row in candidates
                if isinstance(row, dict) and row.get("node_id")]
    if judge is None:
        return {"implicated": [], "unaffected": [],
                "unevaluated": list(node_ids)}
    verdict = _parse_verdict(judge(note_text, candidates), set(node_ids))
    implicated_ids = [entry["node_id"] for entry in verdict]
    return {
        "implicated": verdict,
        "unaffected": [node_id for node_id in node_ids
                       if node_id not in implicated_ids],
        "unevaluated": [],
    }


def _parse_verdict(raw, candidate_ids: set) -> list:
    """The judge's implicated list, or a refusal. Pure."""
    text = raw if isinstance(raw, str) else json.dumps(raw or {})
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        verdict = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ImplicationRefused(
            f"the verdict is not JSON ({exc}): {str(text)[:160]!r}.",
            why=("a verdict that cannot be honoured is refused, never "
                 "partially applied - half a judgement is a guess"),
            fix=("answer with one JSON object carrying an `implicated` "
                 "list of {node_id, why}.")) from exc
    if not isinstance(verdict, dict) or not isinstance(
            verdict.get("implicated"), list):
        raise ImplicationRefused(
            "the verdict carries no `implicated` list.",
            why=("a verdict that cannot be honoured is refused, never "
                 "partially applied - half a judgement is a guess"),
            fix=("answer with one JSON object carrying an `implicated` "
                 "list of {node_id, why}."))
    honoured = []
    for entry in verdict["implicated"]:
        if not isinstance(entry, dict):
            raise ImplicationRefused(
                f"an implicated entry is not an object: {entry!r}.",
                why=("a judgement that cannot name its decision is not "
                     "a judgement"),
                fix="name each implicated decision {node_id, why}.")
        node_id = entry.get("node_id")
        why = entry.get("why")
        if node_id not in candidate_ids:
            raise ImplicationRefused(
                f"the verdict implicates {node_id!r}, which the graph "
                f"never supplied as a candidate.",
                why=("a decision the candidates never named cannot be "
                     "implicated by this note"),
                fix="implicate only the supplied candidate node ids.")
        if not why or not isinstance(why, str):
            raise ImplicationRefused(
                f"the verdict implicates {node_id!r} with no reason.",
                why=("an implication with no reason is not a judgement "
                     "the proposal can carry"),
                fix="carry each implication with its `why`.")
        honoured.append({"node_id": node_id, "why": why})
    return honoured


def parent_version(project_folder, reel: str) -> dict | None:
    """The reel's newest version on record, or None.

    Read off the versions ledger (`versions.reel_versions`), which is
    the only account of which version each reel has held. A reel the
    ledger never versioned reads as no parent - an absent version is
    absent, never version 0.
    """
    from library.tools.versions import reel_versions

    numbers = [entry.get("version")
               for entry in reel_versions.versions_of(
                   str(project_folder), str(reel))
               if isinstance(entry, dict)
               and isinstance(entry.get("version"), int)]
    if not numbers:
        return None
    return {"final": str(reel), "version": max(numbers)}


def propose(note_identity: str, note_text: str, verdict: dict,
            candidates: list, parent: dict | None) -> dict:
    """The change proposal for a judged note. Pure: writes nothing.

    Carries the implicated decisions whole (each with its recorded
    action, reason, evidence and confidence, so the change is made
    against the same measurement the decision rested on), the note's
    own words as the direction, the preserved parent version and the
    proposed new version number. With no parent on record there is
    nothing to preserve or extend, and that is said instead of a
    version number.
    """
    by_id = {row["node_id"]: row for row in candidates or ()
             if isinstance(row, dict) and row.get("node_id")}
    implicated = [by_id[entry["node_id"]]
                  for entry in verdict.get("implicated", [])
                  if entry["node_id"] in by_id]
    if parent is None:
        preservation = ("no version on record for this reel: nothing to "
                        "preserve or extend")
        proposed = None
    else:
        proposed = parent["version"] + 1
        preservation = (f"version {parent['version']} of {parent['final']} "
                        f"is preserved; the change belongs on proposed "
                        f"version {proposed}")
    return {
        "note_identity": note_identity,
        "direction": note_text,
        "implicated": implicated,
        "unaffected": list(verdict.get("unaffected", [])),
        "unevaluated": list(verdict.get("unevaluated", [])),
        "parent_version": parent,
        "proposed_version": proposed,
        "preservation": preservation,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.decision_implication",
        description="which edit decisions a user note touches, and the "
                    "versioned proposal answering it")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("propose", help="candidates + proposal for a note")
    p.add_argument("--project", required=True)
    p.add_argument("--reel", required=True,
                   help="the reel the note was typed on")
    p.add_argument("--text", required=True, help="the note's own words")
    p.add_argument("--clip", action="append", default=[],
                   help="anchoring clip id; repeatable")
    p.add_argument("--t", action="append", type=float, default=[],
                   help="anchoring timeline second; repeatable")
    args = parser.parse_args(argv)

    from library.tools import feedback_ledger, project_graph

    graph = project_graph.build_graph(args.project)
    candidates = candidates_for_note(graph, args.clip, args.t)
    verdict = judge_candidates(args.text, candidates)
    identity = feedback_ledger.durable_identity(args.reel, args.text)
    proposal = propose(identity, args.text, verdict, candidates,
                       parent_version(args.project, args.reel))
    print(json.dumps({"note_identity": identity,
                      "candidates": candidates,
                      "verdict": verdict,
                      "proposal": proposal}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
