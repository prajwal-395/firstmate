"""The reader for the render's own QA findings.

Step 6.02 measures the finished video and writes `exports/qa_report.json`.
Every number the captain named as a shortfall was already in that file on
the run of record for project 001 - the 6.25 s caption gap, 13 of 45 cards
over 25 characters per second, 10 of 11 mix windows missing the target the
plan itself declared, and the -20.94 LUFS master - each filed as `info` or
`warning`, in a file nothing opened.  A measurement nobody reads is the
same defect as a gate that cannot fail (AGENTS.md section 10.4): it reads
as coverage and is not.

This module is the reader, and it is the WHOLE reader.  Two consumers use
it - the run summary at the end of `run_pipeline`, and step 3.03
`review_rough_cut`, which is handed the LAST render's findings because it
is the step with a review job.  Both go through `read_qa_report`, so
neither can develop a private opinion about which findings matter.

Three properties this module exists to hold:

**Severity is preserved and it means something.**  `passed` is the
check's own verdict; `severity` is how loud that verdict is.  A finding
whose check did not pass is FAILING at its declared severity.  A finding
that passed while carrying a non-`info` severity is ADVISORY if and only
if it is one of the declared `REPORT_ONLY_METRICS` - the two checks that
measure a number, miss a target and deliberately do not gate.  Everything
else is CLEAN and counted rather than printed.

**Nothing here blocks a run.**  Reading is not gating.  Step 6.02 already
decides what fails; promoting a report-only check is one boolean in
`render_qa` and stays there.  `tests/test_qa_findings_reach_a_reader.py`
pins the run summary's status against the findings it prints.

**A finding that reaches no reader is itself reported.**  `FINDING_READERS`
carries one row per metric either producer can emit, naming the step that
could act on it and what a reader does with it.  A metric with no row
comes back in `QAFindings.unrouted`, is printed first and loudest, and
fails a test that harvests the metric names out of `render_qa.py` and
`subtitle_qa.py`.  The failure mode this module removes cannot be
reintroduced by adding a check and forgetting the reader.

Owners are DAG node ids (`plan_subtitles`, not `step_4_01_plan_subtitles`)
- the vocabulary `pipeline_data.json` and both ledgers key everything by.
See `library/tools/project_layout.node_id_for` for the other half.


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

The occupancy gate needs to know what the picture was SUPPOSED to look like, so `compile_manifest._conform_fields` records the resolved `framing_intent` on every clip.
A declared letterbox is exempt from the fill floor and never from the consistency half.
`tests/test_baseline_craft_properties.py`.
**Every QA finding has a reader, and one that has none is reported.**
One enumeration, `library/tools/qa_findings.py`. [why](docs/RULE_EVIDENCE.md#the-qa-report-had-no-reader)
- **Two readers, one module.** The run summary prints them at the end of every run, and step 3.03 `review_rough_cut` is handed them as `render_qa_findings`. Both go through `read_qa_report`, so neither can develop a private opinion about which findings matter.
- **Reading is not gating.** The summary block runs AFTER `status` is decided and assigns nothing; promoting a report-only check is still one boolean in `render_qa`. The test pins that ordering off the runner's own source.
- **`passed` is the verdict; `severity` is how loud it is.** A check that did not pass is FAILING at its declared severity. One that passed while carrying a non-`info` severity is ADVISORY **if and only if** its metric is in `REPORT_ONLY_METRICS`, the two whose gate boolean is False. Advisory is read off that enumeration and never off severity alone, and a check's severity moves with its verdict.
- **A metric with no row in `FINDING_READERS` is named first and loudest** - in the summary and in what 3.03 receives - and fails the test, which harvests the metric names out of both producers and checks BOTH directions.
- **No DAG edge carries the findings to 3.03 and none can**: `validate` is the final node and 3.03 is in phase 3, so an edge would be a back edge. They travel by name in `gather_step_inputs`, only to a step whose manifest DECLARES them, and they describe the LAST render - `load_findings` asks state first and the file second and RECORDS which answered. They carry their own legend, because `handoff.md` is frozen (the `CUTS_LEGEND` route), and the legend says plainly that a finding is not grounds to reject a rough cut.
- `tests/test_qa_findings_reach_a_reader.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


# ── Verdicts ─────────────────────────────────────────────────────────

FAILING = "failing"
"""The check's own `passed` is False."""

ADVISORY = "advisory"
"""It passed the gate and still missed its own target.  Report-only."""

CLEAN = "clean"
"""Nothing to say."""

SEVERITIES = ("error", "warning", "info")
"""Loudest first.  Anything else is reported under its own name."""

_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}

BLOCKING_SEVERITY = "error"
"""The one severity a gate is entitled to act on.  Reading never gates."""

REPORT_ONLY_METRICS = frozenset({"chroma_presence", "speech_above_bed"})
"""The checks that measure a number, miss a target and do not gate.

`render_qa.CHROMA_PRESENCE_GATES` and `render_qa.SPEECH_ABOVE_BED_GATES`
are the two booleans, and both are False: these two report `passed=True`
whatever they measured, and say what they measured in `severity` instead.
Promoting one is that boolean and nothing here.

It is an enumeration rather than a rule about severity because a report
that is already on disk cannot be re-severitied.  `subtitle_qa` used to
stamp its failing severity on a PASSING result, so 001's own report
carries `subtitle_overlap` at `error` next to the words "No overlapping
subtitles".  Reading advisory off severity alone would turn every one of
those into a finding, which is the loud-and-wrong half of the defect this
module exists to avoid.  `tests/test_qa_findings_reach_a_reader.py` holds
this set against render_qa's two booleans.
"""


@dataclass(frozen=True)
class FindingReader:
    """Who could act on one metric, and what acting on it means."""

    metric: str
    owner: str
    """The DAG node id that owns the decision behind it."""
    reading: str
    """One sentence: what a reader does with this finding."""


def _rows(*specs: FindingReader) -> Dict[str, FindingReader]:
    return {spec.metric: spec for spec in specs}


FINDING_READERS: Dict[str, FindingReader] = _rows(
    # ── render_qa.py ──
    FindingReader(
        "lufs", "audio_mix",
        "Integrated loudness and true peak of the delivered master. The "
        "mix sets the balance, not the level of the sum: the Sep-03 master "
        "needs +5.68 dB of makeup gain and its true peak is already -2.60 "
        "dBTP, so gain without a limiter clips - no clip-gain decision "
        "closes this. The priced fix is a post-export mastering pass "
        "(library/tools/master_loudness.py, proven on that master: -19.68 "
        "to -14.14 LUFS, gate passes), wired in 6.01 post-export once a "
        "Resolve end-to-end witnesses it."),
    FindingReader(
        "black_frames", "compile_manifest",
        "Black nobody declared. The plan's own coverage assertion "
        "(_assert_timeline_fully_covered) owns the upstream verdict."),
    FindingReader(
        "freeze_frames", "render",
        "A frozen picture in the delivered file, which the timeline "
        "build is what produced."),
    FindingReader(
        "color_histogram", "color_grade",
        "Crushed or blown levels in the delivered file."),
    FindingReader(
        "frame_occupancy", "compile_manifest",
        "What the conform did to the picture. _conform_fields decides "
        "the geometry and records framing_delivered beside it."),
    FindingReader(
        "face_intact", "compile_manifest",
        "A face cut by the frame edge. manifest_validator's P8 carries "
        "the plan-side verdict; this is the render-side backstop."),
    FindingReader(
        "chroma_presence", "color_grade",
        "Reports its number and does not gate: no chroma floor is "
        "declared, and the value is an open captain decision."),
    FindingReader(
        "speech_above_bed", "audio_mix",
        "Reports its number and does not gate: the target is a "
        "SEPARATION and clip gain cannot buy one (AGENTS.md 10.4)."),
    FindingReader(
        "silence_under_picture", "mesh_spine",
        "Picture on screen with nothing at all on any track. The spine "
        "sets every gap and every music_behavior, and silencing the bed "
        "is not silencing the film - `audio_mix` writes the levels and "
        "`compile_manifest` sees both tracks. The near-silence ladder "
        "beside it reports and judges nothing."),
    FindingReader(
        "resolution", "render",
        "The delivery format the render was actually made at."),
    FindingReader(
        "framerate", "render",
        "The frame rate the render was actually made at."),
    FindingReader(
        "duration", "compile_manifest",
        "The delivered length against the manifest's own expected "
        "duration."),
    FindingReader(
        "audio_streams", "render",
        "A silent master. SetRenderSettings must ask for audio "
        "explicitly (AGENTS.md section 5)."),
    # ── subtitle_qa.py ──
    FindingReader(
        "subtitle_overflow", "plan_subtitles",
        "A caption card outlasting the timeline it was grouped for."),
    FindingReader(
        "subtitle_overlap", "plan_subtitles",
        "Two caption cards on screen at once."),
    FindingReader(
        "subtitle_too_short", "plan_subtitles",
        "A card under the readable floor. manifest_validator's P6 owns "
        "the plan-side verdict and exempts the one card no grouping can "
        "lengthen."),
    FindingReader(
        "subtitle_too_long", "plan_subtitles",
        "A card held long enough to go stale."),
    FindingReader(
        "subtitle_gaps", "speech_sequence",
        "Uncaptioned seconds INSIDE a speech block. A planned B-roll "
        "breath is not one of these - the check reads the spine. What is "
        "left is either a real pause in the speech or a passage anchored "
        "to the wrong words; the alignment report says which."),
    FindingReader(
        "subtitle_read_speed", "plan_subtitles",
        "Characters per second. The grouper fits cards to the caption "
        "box at the resolved type size, so this moves with that size."),
)


# ── One finding ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One row of `qa_report.json`, read."""

    metric: str
    passed: bool
    severity: str
    detail: str
    value: Any = None
    threshold: Any = None

    @property
    def verdict(self) -> str:
        if not self.passed:
            return FAILING
        if self.metric in REPORT_ONLY_METRICS and self.severity != "info":
            return ADVISORY
        return CLEAN

    @property
    def reader(self) -> Optional[FindingReader]:
        return FINDING_READERS.get(self.metric)

    @property
    def owner(self) -> str:
        reader = self.reader
        return reader.owner if reader else ""

    @property
    def blocks(self) -> bool:
        """Whether a GATE would be entitled to act on it.

        Reading it here never does; this is what the summary prints so a
        reader can tell an advisory note from a real failure without
        having to know which checks gate.
        """
        return self.verdict == FAILING and self.severity == BLOCKING_SEVERITY

    def as_dict(self) -> Dict[str, Any]:
        reader = self.reader
        return {
            "metric": self.metric,
            "severity": self.severity,
            "verdict": self.verdict,
            "owned_by": reader.owner if reader else "",
            "detail": self.detail,
        }

    def reading(self) -> str:
        reader = self.reader
        return (reader.reading if reader else
                "NO READER IS DECLARED FOR THIS METRIC. It was measured "
                "and nothing in the pipeline claims it.")


def _sort_key(finding: Finding):
    verdict_rank = {FAILING: 0, ADVISORY: 1, CLEAN: 2}[finding.verdict]
    return (verdict_rank,
            _SEVERITY_RANK.get(finding.severity, len(SEVERITIES)),
            finding.metric)


# ── The whole report, read ───────────────────────────────────────────

SOURCE_NONE = "none"
SOURCE_STATE = "state"
SOURCE_FILE = "file"


@dataclass
class QAFindings:
    """Every row of one `qa_report.json`, classified and accounted for."""

    findings: List[Finding] = field(default_factory=list)
    source: str = SOURCE_NONE
    source_detail: str = "no render QA has been recorded for this project"

    @property
    def unrouted(self) -> List[Finding]:
        """Findings no row of FINDING_READERS claims.

        This is the failure this module exists to remove, turned inside
        out: a metric with no reader is not dropped, it is named.
        """
        return [f for f in self.findings if f.reader is None]

    @property
    def failing(self) -> List[Finding]:
        return [f for f in self.findings if f.verdict == FAILING]

    @property
    def advisory(self) -> List[Finding]:
        return [f for f in self.findings if f.verdict == ADVISORY]

    @property
    def clean(self) -> List[Finding]:
        return [f for f in self.findings if f.verdict == CLEAN]

    @property
    def reportable(self) -> List[Finding]:
        """Everything with something to say, loudest first."""
        return sorted((f for f in self.findings if f.verdict != CLEAN),
                      key=_sort_key)

    def __bool__(self) -> bool:
        return bool(self.findings)

    def counts(self) -> Dict[str, int]:
        return {
            "total": len(self.findings),
            FAILING: len(self.failing),
            ADVISORY: len(self.advisory),
            CLEAN: len(self.clean),
            "unrouted": len(self.unrouted),
        }


def read_qa_report(rows: Optional[Sequence[Mapping[str, Any]]],
                   source: str = SOURCE_NONE,
                   source_detail: str = "") -> QAFindings:
    """Read a `qa_report.json` payload.

    A row missing `passed` is read as NOT passed rather than as clean: an
    absent verdict is not a verdict of fine, and the same reasoning
    AGENTS.md section 10.3 applies to an absent measurement applies to an
    absent judgement of one.
    """
    findings: List[Finding] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        metric = str(row.get("metric", "")) or "(unnamed)"
        severity = str(row.get("severity", "")) or "info"
        findings.append(Finding(
            metric=metric,
            passed=bool(row.get("passed", False)),
            severity=severity,
            detail=str(row.get("detail", "")),
            value=row.get("value"),
            threshold=row.get("threshold"),
        ))
    if not findings:
        return QAFindings()
    return QAFindings(findings=findings, source=source,
                      source_detail=source_detail or source)


def load_findings(project_folder: str = "",
                  state: Optional[Mapping[str, Any]] = None) -> QAFindings:
    """The last render's findings, from state if this run has them.

    State first, because a `validate` that ran in THIS run is the report
    for the video on disk now.  The file second, because a project whose
    ledger predates the step - or whose state was reset by `--rerun edit`
    - still has the report the last render wrote, and a reader that
    could only see state would go quiet exactly when there is something
    to say.  Which of the two answered is RECORDED, never assumed: a
    finding read off disk is a finding about a PREVIOUS render and the
    summary says so.
    """
    outputs = ((state or {}).get("step_outputs") or {}).get("validate") or {}
    rows = outputs.get("qa_report")
    if isinstance(rows, list) and rows:
        return read_qa_report(rows, SOURCE_STATE,
                              "this run's validate step (6.02)")

    path = _report_path(project_folder)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                on_disk = json.load(handle)
        except (OSError, ValueError):
            return QAFindings(
                source=SOURCE_NONE,
                source_detail=f"{path} exists but could not be read")
        if isinstance(on_disk, list) and on_disk:
            return read_qa_report(
                on_disk, SOURCE_FILE,
                f"a previous render's report on disk ({path})")
    return QAFindings()


def _report_path(project_folder: str) -> str:
    if not project_folder:
        return ""
    try:
        from library.tools.project_layout import Area, ProjectLayout
        return str(ProjectLayout(project_folder).read_path(
            Area.EXPORTS, "qa_report.json"))
    except Exception:  # noqa: BLE001 - a report must never fail a run
        return os.path.join(project_folder, "exports", "qa_report.json")


# ── What the run summary prints ──────────────────────────────────────

_ICON = {FAILING: "✗", ADVISORY: "!"}

NOTHING_TO_READ = (
    "  QA findings: none recorded yet "
    "(step 6.02 writes exports/qa_report.json)")


def summary_lines(qa: QAFindings) -> List[str]:
    """The run summary's QA block, as lines.

    Written as lines rather than printed so the same text is testable
    without capturing a whole pipeline run.
    """
    if not qa:
        return [NOTHING_TO_READ]

    counts = qa.counts()
    lines = [f"  QA findings: {counts['total']} checks, "
             f"{counts[FAILING]} failing, {counts[ADVISORY]} advisory, "
             f"{counts[CLEAN]} clean"]
    lines.append(f"      source: {qa.source_detail}")

    unrouted = qa.unrouted
    if unrouted:
        # First and loudest.  A finding nobody reads is the defect this
        # block exists to remove, so it is never a footnote.
        lines.append(f"      ⚠ {len(unrouted)} finding(s) reach NO READER - "
                     f"add a row to qa_findings.FINDING_READERS:")
        for finding in sorted(unrouted, key=_sort_key):
            lines.append(f"        ? {finding.metric}: {finding.detail}")

    for finding in qa.reportable:
        icon = _ICON.get(finding.verdict, "·")
        owner = f" [{finding.owner}]" if finding.owner else ""
        lines.append(f"      {icon} {finding.severity:<7} "
                     f"{finding.metric}{owner}: {finding.detail}")

    if qa.advisory:
        lines.append("      advisory = measured, missed its own target, and "
                     "deliberately does not gate.")
    lines.append("      Reading these does not change the run status; "
                 "step 6.02 decides what fails.")
    return lines


# ── What review_rough_cut is handed ──────────────────────────────────
#
# Step 3.03's `handoff.md` is under the captain's freeze, so the table
# carries its own legend - the route `music_measurement.MEASUREMENT_LEGEND`
# and `transition_carriers.CUTS_LEGEND` already take (AGENTS.md sections 5
# and 10.5).  The legend says what a column IS.  It never says what to
# conclude, and it states plainly that none of this is grounds to reject
# the rough cut, because an advisory note turned into a hard rejection is
# a silent problem converted into a loud wrong one.

QA_FINDINGS_LEGEND = {
    "what_this_is": (
        "Measurements of a PREVIOUS render of this project, taken by step "
        "6.02 on the finished file. They are not checks on the rough cut "
        "in front of you, and they are not part of the mechanical gate."),
    "why_it_is_here": (
        "Several of them are consequences of decisions made at or before "
        "this step - which passage was anchored where, and how long each "
        "block holds. Nothing else in the pipeline reads them."),
    "not_grounds_for_rejection": (
        "Do NOT reject the rough cut because of a finding here. Reject "
        "only on the mechanical checks and the narrative criteria in this "
        "handoff. A finding is context; say what it implies for this cut "
        "if it implies anything, and otherwise leave it."),
    "metric": "the check's name in exports/qa_report.json",
    "severity": "error | warning | info - how loud the check itself is",
    "verdict": (
        f"{FAILING} = the check did not pass; "
        f"{ADVISORY} = it passed the gate and still missed its own target, "
        f"deliberately not gating; {CLEAN} = nothing to say"),
    "owned_by": (
        "the pipeline step whose decision the finding is about, as a DAG "
        "node id. An empty owner means no reader is declared for it."),
    "detail": "the check's own sentence, verbatim",
    "readings": (
        "one sentence per metric that has something to say, saying what a "
        "reader does with it. A clean check has no reading because there "
        "is nothing to read; its row is still in the table."),
}


def findings_for_review(qa: QAFindings) -> Dict[str, Any]:
    """The whole report, for a step that reviews.

    Every row travels, clean ones included.  Filtering would put this
    module in the position of deciding which measurements a reviewer is
    allowed to see, which is the shape of the defect it was written to
    remove.  Seventeen rows is a couple of kilobytes.
    """
    ordered = sorted(qa.findings, key=_sort_key)
    return {
        "legend": QA_FINDINGS_LEGEND,
        "source": qa.source,
        "source_detail": qa.source_detail,
        "counts": qa.counts(),
        "unrouted_metrics": [f.metric for f in qa.unrouted],
        "findings": [f.as_dict() for f in ordered],
        # Keyed rather than a sixth column: the reading is a constant per
        # metric, so a column repeats it on every clean row for nothing,
        # and a blank cell there would read as an absent measurement
        # rather than as "no reader had to act".
        "readings": {f.metric: f.reading()
                     for f in ordered if f.verdict != CLEAN},
    }
