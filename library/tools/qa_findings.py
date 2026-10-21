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
if it is one of the declared `REPORT_ONLY_METRICS` - the checks that
measure a number, miss a target and deliberately do not gate.  Everything
else is CLEAN and counted rather than printed.

**Nothing here blocks a run.**  Reading is not gating.  Step 6.02 already
decides what fails; promoting a report-only check is one boolean in
`render_qa` and stays there.  `tests/contracts/test_qa_findings_reach_a_reader.py`
pins the run summary's status against the findings it prints.

**A finding that reaches no reader is itself reported.**  `FINDING_READERS`
carries one row per metric either producer can emit, naming the step that
could act on it and what a reader does with it.  A metric with no row
comes back in `QAFindings.unrouted`, is printed first and loudest, and
fails a test that harvests the metric names out of `render_qa.py`,
`subtitle_qa.py` and `reel_hearing.py`.  The failure mode this module
removes cannot be reintroduced by adding a check and forgetting the
reader.

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
`tests/unit/picture/test_picture_qa.py`.
**Every QA finding has a reader, and one that has none is reported.**
One enumeration, `library/tools/qa_findings.py`. [why](docs/RULE_EVIDENCE.md#the-qa-report-had-no-reader)
- **Two readers, one module.** The run summary prints them at the end of every run, and step 3.03 `review_rough_cut` is handed them as `render_qa_findings`. Both go through `read_qa_report`, so neither can develop a private opinion about which findings matter.
- **Reading is not gating.** The summary block runs AFTER `status` is decided and assigns nothing; promoting a report-only check is still one boolean in `render_qa`. The test pins that ordering off the runner's own source.
- **`passed` is the verdict; `severity` is how loud it is.** A check that did not pass is FAILING at its declared severity. One that passed while carrying a non-`info` severity is ADVISORY **if and only if** its metric is in `REPORT_ONLY_METRICS`, the two whose gate boolean is False. Advisory is read off that enumeration and never off severity alone, and a check's severity moves with its verdict.
- **A metric with no row in `FINDING_READERS` is named first and loudest** - in the summary and in what 3.03 receives - and fails the test, which harvests the metric names out of every producer and checks BOTH directions. The harvest reads a metric named POSITIONALLY as well as one named by keyword, because four of `render_qa`'s are named positionally and two of those had no reader at all. [why](docs/RULE_EVIDENCE.md#the-harvest-that-could-not-see-half-the-metrics)
- **A finding is MEASURABLE or SUBJECTIVE, and the report says which.** A measurement is a number against a stated bound, and a gate may act on it. A judgement about taste is neither, and a gate may not: nobody can reproduce it. `SUBJECTIVE_METRICS` is the small set that says so, and everything else with a reader is a measurement by construction. [why](docs/RULE_EVIDENCE.md#the-qa-report-had-no-reader)
- **No DAG edge carries the findings to 3.03 and none can**: `validate` is the final node and 3.03 is in phase 3, so an edge would be a back edge. They travel by name in `gather_step_inputs`, only to a step whose manifest DECLARES them, and they describe the LAST render - `load_findings` asks state first and the file second and RECORDS which answered. **Step 3.03's own `handoff.md` defines every field and states plainly that a finding is not grounds to reject a rough cut** - the prose carries it since the freeze lifted 2026-09-09, so nothing ships a legend beside the table.
- `tests/contracts/test_qa_findings_reach_a_reader.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from library.tools import reel_hearing


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

DETERMINISTIC = "deterministic"
"""A MEASURABLE technical failure: a number against a stated bound.

The same word `library/tools/creative_qa.py` uses for the same
distinction, on the delivery path.  One vocabulary for both surfaces:
a finding that is a measurement is a measurement, whichever report it
is read in.
"""

SUBJECTIVE = "subjective"
"""An AESTHETIC concern: a judgement about taste, not a measurement.

It is not a smaller kind of failure.  It is a different KIND of
finding: nobody can reproduce it, so no gate may act on it, and the
reader of a subjective finding asks a human rather than a threshold.
"""

UNCLASSIFIED = "unclassified"
"""A metric no reader claims.  It has no kind because nothing owns it."""

SUBJECTIVE_METRICS = frozenset({"broll_correspondence"})
"""The metrics whose finding is a JUDGEMENT rather than a measurement.

`broll_correspondence` asks whether a placed cutaway illustrates the
speech it covers, and the answer comes from a model that looked at the
picture.  That is a judgement, and it is reported to the same reader as
every other finding while being marked so a reader knows a human is
being asked.

Everything else in `FINDING_READERS` is a MEASUREMENT: a number, read
off the delivered file or off the plan, against a bound somebody
declared.  A metric that is not in this set and has a reader is
DETERMINISTIC by construction - which is why the set is the small one
and not the large one, and why adding a measurement does not mean
changing a list.
"""

REPORT_ONLY_METRICS = frozenset({"chroma_presence", "speech_above_bed",
                                 "music_levels", "punch_in_face",
                                 "caption_legibility", "caption_obscuring",
                                 "transition_render", "vfx_render",
                                 "graphics_render",
                                 # The perceptual readings of the delivered
                                 # file (`library/tools/render_perception.py`).
                                 # They measure a number against a bound
                                 # nobody has measured on real footage yet,
                                 # so they report their miss in their
                                 # severity and pass.  Each one names the
                                 # boolean that would promote it.
                                 "cut_motivation", "dialogue_integrity",
                                 "visual_continuity", "flash_frames",
                                 "section_levels", "caption_consistency",
                                 "narrative_coverage", "brief_pacing"})
"""The checks that measure a number, miss a target and do not gate.

`render_qa.CHROMA_PRESENCE_GATES` and `render_qa.CAPTION_QUALITY_GATES`
are the booleans, and both are False: these report `passed=True` whatever
they measured, and say what they measured in `severity` instead.
Promoting one is that boolean and nothing here.

`SPEECH_ABOVE_BED_GATES` is True (captain, 2026-10-05) but the gate
activates only once its separation targets are populated - a per-run
decision step 5.02 records on `music_automation[].separation_target_db`,
never a constant.  Until at least one window declares one, the check
reports `passed=True` at `warning` and is read here as ADVISORY, exactly
as the other report-only checks; once a target is declared, a miss is a
FAILING `error` like any gate.  The set is what makes the report-only
half advisory - it says nothing about the gated half.

It is an enumeration rather than a rule about severity because a report
that is already on disk cannot be re-severitied.  `subtitle_qa` used to
stamp its failing severity on a PASSING result, so 001's own report
carries `subtitle_overlap` at `error` next to the words "No overlapping
subtitles".  Reading advisory off severity alone would turn every one of
those into a finding, which is the loud-and-wrong half of the defect this
module exists to avoid.  `tests/contracts/test_qa_findings_reach_a_reader.py` holds
this set against render_qa's booleans.
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
        "encoding", "render",
        "The delivered file's codec and stream structure. The run summary "
        "and rough-cut review surface the measured format so the export "
        "can be checked against its delivery declaration."),
    FindingReader(
        "channel_configuration", "render",
        "The delivered audio channel layout. The run summary and rough-cut "
        "review expose the measured layout for comparison with the "
        "project's delivery requirements."),
    FindingReader(
        "channels", "render",
        "Every channel the delivered file declares carries signal. A "
        "channel that carries none is a hole where a voice was, and the "
        "loudness measurement cannot see it: a channel at digital zero "
        "contributes nothing to the integrated value either. The run "
        "summary and rough-cut review surface the silent channels, and "
        "the delivery QC gate refuses the file on this check "
        "(deliverable_qc.verify_audio_spec), so the channel layout and "
        "the mix are checked before delivery."),
    FindingReader(
        "overlay_geometry", "render",
        "Whether each overlay the plan placed is IN the delivered file, at "
        "the size it was asked for. An overlay drawn at twice its size and "
        "a graphic that is not in the file at all are invisible to a "
        "timeline read-back - Resolve stores the transform it was given and "
        "reads it back cleanly - so the delivered file's own pixels are "
        "what measure it (render_qa.measure_delivered_overlay_geometry). "
        "The build that placed the overlay owns the correction."),
    FindingReader(
        "clipping", "render",
        "Clipped samples in the delivered audio. The run summary and "
        "rough-cut review surface the affected runs so the mix/export can "
        "be corrected before delivery."),
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
        "punch_in_face", "build_reels",
        "A face cut by the frame edge at a declared punch-in window. "
        "The punch-in's aim is decided by the subject measurement "
        "(subject_framing.measure_subject_in_window) and placed by "
        "reel_build; a cropped face means the aim or the zoom missed. "
        "Reports and does not gate: whether a cropped face at a "
        "punch-in blocks delivery is a pending captain call "
        "(render_qa.PUNCH_IN_FACE_GATES)."),
    FindingReader(
        "chroma_presence", "color_grade",
        "Reports its number and does not gate: no chroma floor is "
        "declared, and the value is an open captain decision."),
    FindingReader(
        "speech_above_bed", "audio_mix",
        "Where someone speaks, the speech is above the bed by the "
        "separation the plan declared. The target is a SEPARATION and "
        "clip gain cannot buy one (AGENTS.md 10.4). Gates once a "
        "separation target is populated (render_qa.SPEECH_ABOVE_BED_GATES, "
        "captain 2026-10-05); reports until one is."),
    FindingReader(
        "music_levels", "audio_mix",
        "The bed's rendered per-block level against the planned curve "
        "(audio_mix.music_automation[].target_level_db). The mix sets the "
        "curve on the timeline and otio_mix.verify reads it back there; "
        "this is the render's OWN per-block level, which the OTIO import's "
        "timeline rebuild puts out of reach of the timeline read-back. "
        "Reports and does not gate: whether a block whose rendered level "
        "misses its planned plateau blocks delivery is a pending captain "
        "call (render_qa.MUSIC_CURVE_GATES)."),
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
    FindingReader(
        "caption_legibility", "plan_subtitles",
        "A caption whose smallest card inks under the legibility floor "
        "at the delivered resolution. The type size is the plan's "
        "declaration (`subtitle_style`), so an illegible caption is the "
        "size the plan chose - or a render that drew it smaller than "
        "planned. REPORTED, not gated: promoting it is "
        "render_qa.CAPTION_QUALITY_GATES."),
    FindingReader(
        "caption_obscuring", "plan_subtitles",
        "A caption whose delivered box overlaps a face the render "
        "detects over its span. The caption row is the plan's "
        "declaration and the subject's position is the footage's, so an "
        "obscuring caption is a row that sits on the speaker - or a "
        "punch-in that put the face under the caption row. REPORTED, "
        "not gated: promoting it is render_qa.CAPTION_QUALITY_GATES."),
    # ── reel_hearing.py ──
    #
    # The first producer that measures the render against the PLAN
    # rather than against itself.  Nothing gates on these
    # (`reel_hearing.GATES` is False and no build reads the record);
    # they are read here so they are classified, severitied and owned
    # by the same table as every other finding rather than by a second
    # opinion.
    FindingReader(
        reel_hearing.SCRIPT_METRIC, "build_reels",
        "Words the render says that the plan does not, or does not say "
        "that the plan does. The reel's own clip ranges decide which "
        "words it carries, so a divergence is either the cut landing "
        "somewhere else or the transcript the cut was made from being "
        "wrong about what is there."),
    FindingReader(
        reel_hearing.DRIFT_METRIC, "build_reels",
        "Where the render says a word against where the plan puts it. "
        "Isolated words are two transcribers disagreeing; a RUN of "
        "consecutive words drifting the same way is a passage sitting "
        "somewhere the plan does not think it is, and every word-timed "
        "decision over it - captions, karaoke, take boundaries - is "
        "placed from that wrong position."),
    FindingReader(
        reel_hearing.COVERAGE_METRIC, "plan_subtitles",
        "Speech in the delivered file with no caption card on screen "
        "while it is spoken. `subtitle_gaps` asks the same question of "
        "the PLAN; this asks it of what was heard, so a card the plan "
        "never generated - a transcript row with text and no word "
        "timings generates none - is visible here and nowhere else."),
    FindingReader(
        reel_hearing.FIT_METRIC, "temporal_index",
        "A transcript row carrying text with no word timing under it - "
        "the CAUSE of the three above rather than a fourth symptom, "
        "which is why its owner is the step that wrote the transcript "
        "and not the one that placed the cards. Forced alignment "
        "already discovers these and nothing read them; a row that lost "
        "its words generates no caption card, no karaoke highlight and "
        "no take boundary, silently. `library/tools/transcript_fit.py` "
        "measures the whole document without a render."),
    FindingReader(
        reel_hearing.PAIRING_METRIC, "plan_subtitles",
        "A caption card paired with speech its own filename does not "
        "declare - the wrong file in a slot, one speaker's caption over "
        "another speaker's audio, or the right file at the wrong time. "
        "The caption plan binds each card to its source span, so it is "
        "the step that can re-plan the binding or re-place the card; "
        "needs no render, no audio and no model, only the filename "
        "against the placement."),
    # ── broll_correspondence.py ──
    FindingReader(
        "broll_correspondence", "select_broll",
        "A placed cutaway whose visual content does not illustrate the "
        "speech it covers. The B-roll selection step (3.02) chose the "
        "cutaway to illustrate the spoken words; a non-illustrating "
        "cutaway means the choice was wrong - either the wrong clip or "
        "the wrong window. REPORTED, not gated: promoting it is "
        "broll_correspondence.BROLL_CORRESPONDENCE_GATES."),
    FindingReader(
        "transition_render", "plan_transitions",
        "A drawn transition whose exported pixels show a freeze or a "
        "hard cut instead of an intermediate blend. The transition "
        "type and its placement are the plan's decision "
        "(plan_transitions); a freeze means the transition drew nothing "
        "and a hard cut means the blend never reached the picture. "
        "REPORTED, not gated: promoting it is "
        "render_qa.TRANSITION_RENDER_GATES."),
    FindingReader(
        "vfx_render", "plan_vfx",
        "A VFX whose window matches the untreated source in the export "
        "- the comp was written but never reached the picture. The "
        "effect and its window are the plan's decision (plan_vfx), so "
        "an effect that drew nothing means the plan named an effect "
        "the build failed to composite. REPORTED, not gated: promoting "
        "it is render_qa.VFX_RENDER_GATES."),
    FindingReader(
        "graphics_render", "render_motion_graphics",
        "An overlay whose window matches the picture underneath in the "
        "render - the asset was placed on the timeline but never "
        "composited. The overlay plan (4.06) decides what is drawn "
        "where, so an overlay absent from the render means the "
        "placement or the render missed it. REPORTED, not gated: "
        "promoting it is render_qa.GRAPHICS_RENDER_GATES."),
    # ── render_perception.py ──
    #
    # The PERCEPTUAL readings of the delivered file: what a viewer sees,
    # measured on that file and compared against what the plan
    # declared.  They are all REPORTED and none of them gates - see
    # `library/tools/render_perception.py`, whose `*_GATES` booleans
    # are what promoting one would cost.
    FindingReader(
        "grade_delivery", "color_grade",
        "The DIRECTION half of the grade: whether a graded span's "
        "exported pixels still say what its CDL says. The grade is the "
        "colorist's decision (`color_grade`), and a span whose pixels "
        "contradict it means the write did not reach the picture. "
        "REPORTED, not gated: a direction failure is always a magnitude "
        "failure too, and `grade_landing` is the half that decides."),
    FindingReader(
        "grade_landing", "color_grade",
        "The MAGNITUDE half of the grade: whether a graded span's "
        "exported pixels land as far from the source as its CDL demands. "
        "The direction half cannot ask how far the grade landed, "
        "because a shortfall there confounds the grade with the "
        "pipeline and the encode; this asks it against the CDL's own "
        "demanded output, which the confound cannot touch. GATES "
        "(render_qa.GRADE_LANDING_GATES, captain 2026-10-05)."),
    FindingReader(
        "cut_motivation", "compile_manifest",
        "A cut in the delivered picture that no declared seam "
        "explains. The manifest carries every seam the plan wrote, so a "
        "change of picture in the render with no seam behind it is a "
        "change the conform made and the plan never asked for. The "
        "plan's own picture order is the conform's business, and this "
        "is the render-side backstop."),
    FindingReader(
        "dialogue_integrity", "compile_manifest",
        "A seam that lands inside a spoken word. The word is cut off "
        "mid-word, and every decision placed on that word's timing - "
        "its caption, its karaoke highlight, its take boundary - is "
        "placed against a word the listener never heard whole. The "
        "word timings are the spine's (`speech_sequence`); the seam "
        "that damaged one is the conform's."),
    FindingReader(
        "visual_continuity", "compile_manifest",
        "A join that was meant to be invisible and is not, or a flash "
        "frame nobody planned. A declared split is one shot cut in "
        "two, and the frames either side of it should be consecutive "
        "frames of the same footage; a visible join means the conform "
        "put the wrong source range back together. The UNEXPECTED "
        "FRAMING CHANGE is not this row's business: "
        "`render_qa.measure_frame_occupancy` already holds one "
        "geometry per declared framing."),
    FindingReader(
        "flash_frames", "render",
        "A single frame far brighter or darker than both of its "
        "neighbours. A flash is one frame, not a shot, and it is the one "
        "visual defect a viewer cannot mistake for a cut. No plan asks "
        "for one, so it needs no owner beyond the render that carried "
        "it. REPORTED, not gated: promoting it is "
        "render_perception.FLASH_FRAMES_GATES."),
    FindingReader(
        "section_levels", "audio_mix",
        "Two adjacent sections delivered at very different levels. The "
        "master as a whole is `lufs`'s business and the bed's own curve "
        "is `music_levels`'s; this asks whether the mix STEPS between "
        "one section and the next, which is what the ear hears as a "
        "jump. A section the plan marked `silent` is exempt, because "
        "that step is the plan's."),
    FindingReader(
        "caption_consistency", "plan_subtitles",
        "Caption cards drawn at different sizes from one another. The "
        "other three caption questions have their own rows - "
        "`caption_legibility` for whether a card can be read, "
        "`subtitle_qa` for its timing, `qa/subtitle_qa` for whether it "
        "is clipped - and this one is whether the reel's captions look "
        "like they came from the same reel. The card's type size is "
        "the plan's declaration (`subtitle_style`)."),
    FindingReader(
        "narrative_coverage", "compile_manifest",
        "A piece of the plan that is not in the deliverable: a declared "
        "cut the render does not show, or a deliverable that ends "
        "before the plan's last section. The CONTENT half - whether "
        "the deliverable SAYS what the plan said - is "
        "`reel_hearing`'s, and it is a different row because it is "
        "measured by transcribing the master."),
    FindingReader(
        "brief_pacing", "mesh_spine",
        "The delivered pace is not the pace the plan wrote: a declared "
        "seam the render puts somewhere else. The brief is prose and "
        "the spine is what the pipeline made of it, so the spine's own "
        "seams are the pace the brief asked for. The spine sets every "
        "block's place on the timeline, and a seam that drifts means "
        "the reel runs at a pace nobody chose."),
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
    evidence_ids: List[str] = field(default_factory=list)
    declared_kind: str = ""
    """The kind the report itself carried, when it carried one.

    Empty means the report said nothing about it, and the kind is then
    READ OFF THE METRIC - a metric with a reader is a measurement
    unless it is one of the `SUBJECTIVE_METRICS`.  A report that says
    otherwise is believed: the three readings, and the report is the
    one that was written last.
    """

    @property
    def kind(self) -> str:
        """DETERMINISTIC, SUBJECTIVE or UNCLASSIFIED.

        The question this answers is QA-016's: is this finding a
        MEASURABLE technical failure or a subjective aesthetic
        concern?  A gate may act on the first.  The second is asked of
        a human, because nobody can reproduce a judgement about taste.
        """
        if self.declared_kind in (DETERMINISTIC, SUBJECTIVE):
            return self.declared_kind
        if self.reader is None:
            return UNCLASSIFIED
        if self.metric in SUBJECTIVE_METRICS:
            return SUBJECTIVE
        return DETERMINISTIC

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
            "kind": self.kind,
            "severity": self.severity,
            "verdict": self.verdict,
            "owned_by": reader.owner if reader else "",
            "detail": self.detail,
            "evidence_ids": list(self.evidence_ids),
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
    edit_verifications: List[Dict[str, Any]] = field(default_factory=list)

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

    @property
    def deterministic(self) -> List[Finding]:
        """The MEASURABLE findings - a number against a stated bound.

        These are the findings a gate may act on, because a gate can
        reproduce them.  QA-016's question, asked of the whole report.
        """
        return [f for f in self.findings if f.kind == DETERMINISTIC]

    @property
    def subjective(self) -> List[Finding]:
        """The AESTHETIC findings - a judgement, not a measurement.

        These are asked of a human.  A gate may not act on one: nobody
        can reproduce a judgement about taste, so a threshold that
        "reproduced" it would be reproducing itself.
        """
        return [f for f in self.findings if f.kind == SUBJECTIVE]

    def __bool__(self) -> bool:
        return bool(self.findings or self.edit_verifications)

    def counts(self) -> Dict[str, int]:
        """The VERDICT counts - how many passed, failed, advised.

        The kind counts are a separate question and a separate method:
        `counts()` is the verdict of a check and `kind_counts()` is what
        KIND of finding it is, and the two are not the same axis.
        """
        return {
            "total": len(self.findings),
            FAILING: len(self.failing),
            ADVISORY: len(self.advisory),
            CLEAN: len(self.clean),
            "unrouted": len(self.unrouted),
        }

    def kind_counts(self) -> Dict[str, int]:
        """The KIND counts - measurable against a judgement."""
        return {
            DETERMINISTIC: len(self.deterministic),
            SUBJECTIVE: len(self.subjective),
            UNCLASSIFIED: len(self.findings) - len(self.deterministic)
                         - len(self.subjective),
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
    verifications: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        if row.get("record_type") == "edit_verification":
            verifications.append({
                "category": str(row.get("category", "")),
                "status": (row.get("status") if row.get("status") in
                           {"pass", "fail", "unmeasurable"} else "unmeasurable"),
                "evidence_ids": list(row.get("evidence_ids") or []),
                "reason": str(row.get("reason", "")),
            })
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
            evidence_ids=list(row.get("evidence_ids") or []),
            declared_kind=str(row.get("kind", "") or ""),
        ))
    if not findings and not verifications:
        return QAFindings()
    return QAFindings(findings=findings, source=source,
                      source_detail=source_detail or source,
                      edit_verifications=verifications)


EDIT_VERIFICATION_EVIDENCE = {
    "speech": ("speech_above_bed",),
    "coverage": ("black_frames", "frame_occupancy"),
    "sync": (),
    "visual_conflict": ("caption_obscuring",),
    "continuity": (),
    "graphics": ("graphics_render",),
}


def edit_verification_rows(findings: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Build explicit, evidence-linked edit checks; missing measures stay unknown."""
    by_metric = {str(row["metric"]): row for row in findings
                 if isinstance(row, Mapping) and row.get("metric")}
    records = []
    for category, metrics in EDIT_VERIFICATION_EVIDENCE.items():
        present = [by_metric[metric] for metric in metrics if metric in by_metric]
        missing = [metric for metric in metrics if metric not in by_metric]
        evidence_ids = [evidence_id for row in present
                        for evidence_id in row.get("evidence_ids", [])]
        if not present or missing or not evidence_ids:
            status = "unmeasurable"
            reason = ("No direct render measurement is available" if not present
                      else "Required measurements or evidence IDs are missing")
        else:
            # Report-only render checks keep `passed=True` by contract;
            # their non-info severity is the recorded measured miss.
            status = ("fail" if any(row.get("passed") is not True
                                    or row.get("severity", "info") != "info"
                                    for row in present) else "pass")
            reason = "Measured by: " + ", ".join(row["metric"] for row in present)
        records.append({"record_type": "edit_verification",
                        # Keep the historical qa_report row contract: downstream
                        # readers index metric directly even for extension rows.
                        "metric": "edit_verification", "category": category,
                        "status": status, "evidence_ids": evidence_ids,
                        "reason": reason})
    return records


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
    from library.tools import capability_outputs
    rows = capability_outputs.value(state or {}, "validation.resolve",
                                    "qa_report")
    if isinstance(rows, list) and rows:
        # A hearing happens after a reel has been delivered, outside the
        # validation step that produced `rows`. Its rows are appended to
        # the shared report on disk; include those even when this run has
        # a validation payload in state, without duplicating an already
        # merged hearing row.
        path = _report_path(project_folder)
        hearing_rows = []
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    disk_rows = json.load(handle)
                if isinstance(disk_rows, list):
                    hearing_rows = [row for row in disk_rows
                                    if isinstance(row, Mapping)
                                    and row.get("_hearing_id")]
            except (OSError, ValueError):
                hearing_rows = []
        existing = {(row.get("_hearing_id"), row.get("metric"))
                    for row in rows if isinstance(row, Mapping)
                    and row.get("_hearing_id")}
        additions = [row for row in hearing_rows
                     if (row.get("_hearing_id"), row.get("metric"))
                     not in existing]
        detail = "this run's validate step (6.02)"
        if additions:
            detail += ", plus heard reel findings from exports/qa_report.json"
        return read_qa_report(list(rows) + additions, SOURCE_STATE, detail)

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
    kinds = qa.kind_counts()
    lines = [f"  QA findings: {counts['total']} checks, "
             f"{counts[FAILING]} failing, {counts[ADVISORY]} advisory, "
             f"{counts[CLEAN]} clean"]
    lines.append(f"      source: {qa.source_detail}")
    # The KIND of finding is a different axis from its verdict, and
    # QA-016 asks for it explicitly: a measurable technical failure is
    # something a gate can act on, and a subjective aesthetic concern
    # is something a human is asked about.
    lines.append(f"      kind: {kinds[DETERMINISTIC]} measurable, "
                 f"{kinds[SUBJECTIVE]} subjective")
    for verification in qa.edit_verifications:
        refs = ", ".join(verification["evidence_ids"]) or "none"
        lines.append(f"      edit {verification['category']}: "
                     f"{verification['status']} (evidence: {refs}; "
                     f"{verification['reason']})")

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
# What each field IS - and the rule that none of it is grounds to reject
# a rough cut - is stated in step 3.03's own `handoff.md`, under
# "`render_qa_findings` - measurements of the LAST render".  It travelled
# beside the table as a `QA_FINDINGS_LEGEND` dict only while that file
# was under the captain's freeze; the freeze was lifted 2026-09-09, and a
# definition living in two places is worse than either.


def findings_for_review(qa: QAFindings) -> Dict[str, Any]:
    """The whole report, for a step that reviews.

    Every row travels, clean ones included.  Filtering would put this
    module in the position of deciding which measurements a reviewer is
    allowed to see, which is the shape of the defect it was written to
    remove.  Seventeen rows is a couple of kilobytes.
    """
    ordered = sorted(qa.findings, key=_sort_key)
    return {
        "source": qa.source,
        "source_detail": qa.source_detail,
        "counts": qa.counts(),
        "kind_counts": qa.kind_counts(),
        "unrouted_metrics": [f.metric for f in qa.unrouted],
        "findings": [f.as_dict() for f in ordered],
        "edit_verifications": qa.edit_verifications,
        # Keyed rather than a sixth column: the reading is a constant per
        # metric, so a column repeats it on every clean row for nothing,
        # and a blank cell there would read as an absent measurement
        # rather than as "no reader had to act".
        "readings": {f.metric: f.reading()
                     for f in ordered if f.verdict != CLEAN},
    }
