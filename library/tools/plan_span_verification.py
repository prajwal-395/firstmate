"""VI-014: verify a reel plan's source-span claims ON DEMAND.

The punch list asks for *"Resolve references such as 'the second time
she opens the box.'"*  The ordinal half of that is DONE and lives in
``library/tools/temporal_query.py`` (``resolve_ordinal_reference``) and
``library/tools/occurrence_identity.py``.  This module is the OTHER half:
the span an answer names is a CLAIM, and a claim that was never checked
against the source file itself is a claim the pipeline can be wrong
about.

**The claim.**  A reel moment carries ``source_spans`` - the raw footage
it plays, as ``{source_file, source_start, source_end}``.  Those spans
were derived by REBASING the master timeline's transcript rows onto the
source files.  A rebase is an inference: it says "these source seconds
carry these words" because the master timeline says those words fall
inside a clip whose source in-point is that offset.  Nothing in the
pipeline has ever checked the rebase against the SOURCE FILE's own
transcript.

**The check.**  ``on_demand_verification`` answers the claim from the
source's OWN measured words - the independent measurement - and says
``supported``, ``contradicted`` or ``unresolved``.  This module runs
that check over a plan's spans and reports the result.

**What this is NOT.**  It is not a gate, and it does not refuse a
build.  The captain's ruling stands: a report-only check that has a
reader is a check; a report-only check that is promoted to a gate
without the captain's ruling is not this module's business.  The
findings are written where a reader can find them, and the caller
decides what to do with them.

**The budget.**  A verification costs a read of the source's own
transcript.  It is charged against the caller's budget, and a plan
larger than that budget is verified as far as the budget goes, with
the rest REPORTED as unverified - never as verified, and never as
wrong.

**Where the readings live.**  Each verdict is filed in the source's own
memory under its own slot (``on_demand.json``), beside the measurements
it was made from.  A second plan over the same source and the same span
costs nothing: the recorded verdict is the answer.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from library.tools import on_demand_verification as od

FORMAT = "plan_span_verification/1"
"""The format of the report this module writes, so a reader can refuse a
file it does not know."""

FINDINGS_FILENAME = "plan_span_verification.json"
"""The report's name, inside the project's REVIEW area.

The review area is where the plan's own decisions live (``plan_provenance``,
``reel_decisions``, ``moment_boundary_repairs``), and a finding about the
plan belongs beside them rather than in a step's output directory.
"""


@dataclass
class SpanFinding:
    """One plan span, and what the source's own words say about it."""

    reel_number: int
    source_file: str
    source_start: float
    source_end: float
    verdict: od.Verdict
    detail: str = ""
    seconds: float = 0.0
    reused: bool = False
    words_claimed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"reel_number": self.reel_number,
                "source_file": self.source_file,
                "source_start": round(self.source_start, 3),
                "source_end": round(self.source_end, 3),
                "verdict": self.verdict.value,
                "detail": self.detail,
                "seconds": round(self.seconds, 3),
                "reused": self.reused,
                "words_claimed": self.words_claimed}

    @classmethod
    def from_dict(cls, data: Any) -> "SpanFinding":
        if not isinstance(data, dict):
            raise ValueError("a span finding is a mapping")
        return cls(reel_number=int(data.get("reel_number") or 0),
                   source_file=str(data.get("source_file") or ""),
                   source_start=float(data.get("source_start") or 0.0),
                   source_end=float(data.get("source_end") or 0.0),
                   verdict=od.Verdict(data.get("verdict")),
                   detail=str(data.get("detail") or ""),
                   seconds=float(data.get("seconds") or 0.0),
                   reused=bool(data.get("reused")),
                   words_claimed=int(data.get("words_claimed") or 0))


@dataclass
class Report:
    """What the verification found over a plan."""

    findings: list[SpanFinding] = field(default_factory=list)
    seconds: float = 0.0
    spans_total: int = 0
    spans_checked: int = 0
    spans_unverified: int = 0
    budget_seconds: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT,
                "summary": {
                    "spans_total": self.spans_total,
                    "spans_checked": self.spans_checked,
                    "spans_unverified": self.spans_unverified,
                    "seconds": round(self.seconds, 3),
                    "budget_seconds": round(self.budget_seconds, 3),
                    "supported": sum(1 for f in self.findings
                                     if f.verdict is od.Verdict.SUPPORTED),
                    "contradicted": sum(1 for f in self.findings
                                         if f.verdict is od.Verdict.CONTRADICTED),
                    "unresolved": sum(1 for f in self.findings
                                       if f.verdict is od.Verdict.UNRESOLVED),
                    "reused": sum(1 for f in self.findings if f.reused),
                },
                "findings": [f.to_dict() for f in self.findings],
                "reasons": list(self.reasons)}

    def by_verdict(self, verdict: od.Verdict) -> list[SpanFinding]:
        return [f for f in self.findings if f.verdict is verdict]


def _digest_for(project_folder: str, source_file: str,
                recorded: Optional[dict] = None) -> Optional[str]:
    """The content digest of a source file, from the runner's own record.

    The digest is what decides whether a recorded verdict about the file
    still applies to it, so the lookup is by CONTENT and never by path.

    The record is read ONCE per sweep: a plan names the same few
    sources over and over, and re-reading the state file per span made
    a sweep of 590 spans cost more in state-file reads than in
    measurements.
    """
    from library.tools import source_memory
    if recorded is None:
        recorded = source_memory.load_recorded_fingerprints(project_folder)
    for entry in recorded.values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("path") or "") == str(source_file):
            digest = entry.get("content_digest")
            if digest:
                return str(digest)
    return None


def _claimed_words(transcript: dict, source_file: str,
                   start: float, end: float) -> list[str]:
    """The words the MASTER timeline places inside a source span.

    This is the claim being checked: the master transcript's rows are
    rebased onto the source file's timebase, and the rebased words are
    what the plan says those source seconds carry.
    """
    base = os.path.basename(str(source_file))
    words: list[str] = []
    for segment in (transcript or {}).get("segments") or []:
        if os.path.basename(str(segment.get("source_file") or "")) != base:
            continue
        seg_source_start = segment.get("source_start")
        seg_timeline_start = segment.get("timeline_start")
        if seg_source_start is None or seg_timeline_start is None:
            continue
        for word in segment.get("words") or []:
            if word.get("word") is None or word.get("start") is None:
                continue
            rebased = (float(seg_source_start)
                       + (float(word["start"]) - float(seg_timeline_start)))
            if start - 1e-6 <= rebased <= end + 1e-6:
                words.append(str(word["word"]))
    return words


def verify_plan_spans(moments: Sequence[Any],
                       transcript: dict,
                       project_folder: str,
                       *,
                       budget_seconds: float = 120.0,
                       memory_root: Optional[str] = None,
                       ) -> Report:
    """Verify every source span a plan names, on demand, inside a budget.

    `moments` are the plan's moments (anything with ``number`` and
    ``source_spans``).  Each span is checked against the SOURCE FILE's own
    measured words, which is the independent measurement of the same
    speech.

    The budget is spent in the order the plan lists the spans.  When it
    runs out, the spans not reached are counted as UNVERIFIED - never as
    verified and never as wrong.  A plan whose spans name a file the
    runner never fingerprinted is reported as unverified with the reason,
    because an unfingerprinted file has no identity to check a verdict
    against.
    """
    root = memory_root or None
    report = Report(budget_seconds=float(budget_seconds))
    started = time.perf_counter()
    stores: dict[str, od.EvidenceStore] = {}
    from library.tools import source_memory as _sm
    recorded = _sm.load_recorded_fingerprints(project_folder)

    for moment in moments:
        spans = list(getattr(moment, "source_spans", ()) or ())
        report.spans_total += len(spans)
        number = int(getattr(moment, "number", 0) or 0)
        for span in spans:
            source_file = str(span.get("source_file") or "")
            try:
                start = float(span.get("source_start"))
                end = float(span.get("source_end"))
            except (TypeError, ValueError):
                report.spans_unverified += 1
                report.findings.append(SpanFinding(
                    reel_number=number, source_file=source_file,
                    source_start=0.0, source_end=0.0,
                    verdict=od.Verdict.UNRESOLVED,
                    detail="the span names no measurable range"))
                continue
            if end <= start:
                report.spans_unverified += 1
                report.findings.append(SpanFinding(
                    reel_number=number, source_file=source_file,
                    source_start=start, source_end=end,
                    verdict=od.Verdict.UNRESOLVED,
                    detail="the span is not a range"))
                continue

            digest = _digest_for(project_folder, source_file, recorded)
            if not digest:
                report.spans_unverified += 1
                report.findings.append(SpanFinding(
                    reel_number=number, source_file=source_file,
                    source_start=start, source_end=end,
                    verdict=od.Verdict.UNRESOLVED,
                    detail=("the runner recorded no content digest for this "
                            "file, so no verdict about it can be filed")))
                continue

            remaining = float(budget_seconds) - (time.perf_counter() - started)
            if remaining <= 0.0:
                report.spans_unverified += 1
                report.findings.append(SpanFinding(
                    reel_number=number, source_file=source_file,
                    source_start=start, source_end=end,
                    verdict=od.Verdict.UNRESOLVED,
                    detail=("the verification budget ran out before this "
                            "span was checked")))
                continue
            claim = " ".join(_claimed_words(transcript, source_file, start, end))
            if not claim.strip():
                report.spans_unverified += 1
                report.findings.append(SpanFinding(
                    reel_number=number, source_file=source_file,
                    source_start=start, source_end=end,
                    verdict=od.Verdict.UNRESOLVED,
                    detail=("the master transcript places no word in this "
                            "span, so there is no claim to check")))
                continue

            request = od.Request(
                span=od.Span(source_file=source_file, start_s=start,
                             end_s=end, content_digest=digest),
                claim=claim,
                kind=od.RequestKind.SPEECH_CONTEXT,
                decision="the reel plays these source seconds",
                budget_seconds=remaining)
            store = stores.get(digest)
            if store is None:
                store = od.EvidenceStore.for_source(
                    digest, Path(root).expanduser() if root else None)
                stores[digest] = store
            outcome = od.verify(request, store)
            report.findings.append(SpanFinding(
                reel_number=number, source_file=source_file,
                source_start=start, source_end=end,
                verdict=outcome.verdict, detail=outcome.detail,
                seconds=outcome.seconds, reused=outcome.reused,
                words_claimed=len(od.claim_words(claim))))
            if outcome.verdict is not od.Verdict.UNRESOLVED:
                report.spans_checked += 1
            else:
                report.spans_unverified += 1

    # The write is charged: the sweep's cost includes what it cost to
    # record what it found, and a budget that ignored the write would
    # report a cost the caller never spent.
    od.flush(list(stores.values()))
    report.seconds = round(time.perf_counter() - started, 3)
    return report


def report_path(project_folder: str) -> str:
    """Where the report is written, inside the project's review area."""
    from library.tools.project_layout import Area, ProjectLayout
    layout = ProjectLayout(project_folder)
    return str(layout.write_path(Area.REVIEW, FINDINGS_FILENAME))


def write_report(project_folder: str, report: Report) -> str:
    """Write the report where a reader can find it, and return its path.

    The write holds the project's own file lock, the way the other
    review-area records do: a build and a verification can land at the
    same moment, and the file they share is written once.
    """
    path = report_path(project_folder)
    from library.tools.project_file_lock import lock_project_file
    with lock_project_file(path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=1)
    return path


def read_report(project_folder: str) -> Optional[dict]:
    """The report, or None when none has been written."""
    path = report_path(project_folder)
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.plan_span_verification",
        description=("Verify a reel plan's source-span claims against the "
                     "source files' own measured transcripts, on demand and "
                     "inside a resource budget."))
    parser.add_argument("--project", required=True,
                        help="The project whose plan to verify.")
    parser.add_argument("--plan", default="",
                        help="The plan file to read (default: the project's "
                             "approved proposal).")
    parser.add_argument("--budget-seconds", type=float, default=120.0,
                        help="The resource ceiling on the whole sweep.")
    parser.add_argument("--memory-root", default="",
                        help="The source-memory root to read and write.")
    parser.add_argument("--json", action="store_true",
                        help="Print the report as JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from library.tools.reel_proposal import proposal_path, read_proposal
    from library.tools.timeline_transcript import transcript_path

    project = os.path.abspath(args.project)
    path = args.plan or proposal_path(project)
    moments = read_proposal(path)
    try:
        with open(transcript_path(project), encoding="utf-8") as handle:
            transcript = json.load(handle)
    except (OSError, ValueError):
        transcript = {}
    report = verify_plan_spans(moments, transcript, project,
                               budget_seconds=args.budget_seconds,
                               memory_root=args.memory_root or None)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        summary = report.to_dict()["summary"]
        print(f"spans: {summary['spans_total']} total, "
              f"{summary['spans_checked']} decided, "
              f"{summary['spans_unverified']} unverified "
              f"({summary['supported']} supported, "
              f"{summary['contradicted']} contradicted, "
              f"{summary['unresolved']} unresolved)")
        print(f"cost: {summary['seconds']:.2f}s of a "
              f"{summary['budget_seconds']:.2f}s budget")
        for finding in report.by_verdict(od.Verdict.CONTRADICTED):
            print(f"  CONTRADICTED reel {finding.reel_number:2d} "
                  f"{os.path.basename(finding.source_file)} "
                  f"[{finding.source_start:.2f}-{finding.source_end:.2f}]: "
                  f"{finding.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
