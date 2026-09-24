"""direction_contradiction.py - a step says its measurements disagree with
the creative direction it was handed, and complies anyway.

On the 29 Aug 2026 run of project 001 the words "emotion" and "energy"
appear ZERO times in the semantic documents and FOUR times each in the
`creative_direction` block carried into station 2 and station 3.  Step
2.01 reads the footage once, writes down how it feels, and every creative
step after it inherits that reading whole.  None of them had any way to
say "what I was routed disagrees with what I was told".  One early
judgement governed everything after it, unchallengeably.

That only became a real defect when prosody landed (#417, 2026-09-01):
prosody is the first DETERMINISTIC measurement that CAN disagree with an
inherited affect reading - pitch, speaking rate, voice quality and
intensity, measured by signal processing rather than judged.  That is
exactly why the captain wanted it.

**The captain's ruling of 2026-09-01 is the shape of this module.**  A
step MAY FLAG a contradiction and MAY NOT ACT ON ONE.  Flagging is the
whole design; deviating is what the ruling was against.  Nothing here
reads the content of a flag, nothing gates on one, and the field is taken
out of the answer before anything validates it - so a step that flags
produces exactly the output it would have produced silently.  Whoever
decides what a contradiction means becomes the director, and that is the
captain (AGENTS.md 10.4, 10.5).

**It is the wave-3 shape carrying different cargo.**
`library/tools/undetermined.py` is the sibling: a structured field, split
out of the answer before validation, reporting and never gating, with an
empty answer and an absent one read differently.  This is deliberately
its twin rather than a second invention - same `take`/`record`/
`summary_lines` surface, same collector, same route into the prompt as
DATA beside the context.  That route is REUSE: the same words go to
every flagging step, and N copies in N `handoff.md` files is N things
to keep equal.  It is not the captain's handoff freeze, lifted
2026-09-09.

**Where it differs, and why.**  The sibling has THREE readings; this has
FOUR.  A gap is named by naming it, so `what` alone is a complete entry
there.  A CONTRADICTION is not: it is a claim ABOUT a measurement, and a
claim with no measurement behind it is a model politely disagreeing with
its brief.  The failure mode the sibling was designed against - a field
models fill with noise on every call - has a sharper form here, because
prose disagreement is the cheapest thing a model can produce.  So an
entry that names no measurement, or names one the step was not routed, or
names a direction field step 2.01 is not asked for, is UNEVIDENCED: kept
verbatim, reported as itself, and never counted as a contradiction.

    CONTRADICTED           at least one entry names a direction field, a
                           measurement, and the routed input it came
                           from.  Evidence travelling with the claim.
    NOTHING_CONTRADICTED   the model answered `[]`.  A real answer, and
                           the expected one on most calls.
    UNEVIDENCED            the model wrote entries and not one of them
                           carried a measurement.  Neither a
                           contradiction nor a silence.
    NOT_DECLARED           the key is absent.  The model was asked and
                           did not reply, recorded as a non-answer and
                           never as "nothing contradicted".

Same line the repository draws everywhere between an admitted absence and
a measured emptiness: `usable_ranges` `[]`/`unmeasured` against
`[]`/`deterministic_v1` (AGENTS.md 10.3), `primary_subject_visible` None
against `[]`, `speech_present` True-or-None-never-False.

**Which steps can flag, argued rather than assumed.**  A contradiction
needs THREE things in one step: a prompt to say it in, an inherited
direction claim, and a measurement of the material to hold against it.
So a step flags when it reaches a model, declares `creative_direction`
as an input, and is routed at least one measurement.  Step 2.01 is
excluded because it AUTHORS the direction - it has nothing inherited to
contradict.  The model-reaching half is
`undetermined.DECLARING_STEPS`, borrowed rather than restated so the two
cannot drift; the routed half is derived from `dag.json` rather than
listed here, so inserting an edge cannot leave this stale.

That comes to NINE steps - every step that reaches a model except the
one that writes the direction.  `creative_cohesion` (5.03) declares
`creative_direction`, is deterministic, and is excluded with a reason
rather than by omission: a step with no prompt cannot be asked, and
inventing a comparison for it in code would be this module deciding that
a measurement disagrees, which is the captain's call.

`color_grade` (5.01) was in that excluded list until 2026-09-03 and
joined by DERIVATION alone when it stopped being deterministic - it
declares `creative_direction`, and the vision documents its bridge joins
scene descriptions from are a routed measurement.  It is the clearest
case the channel has: a direction that says the piece is vibrant, held
against nine clips one of which measures 53 luma.


**The inventory this marking comes from, and what it judged.**
Prose-as-evidence flows found 2026-09-07 by joining every step manifest's
`context_fields` against the producing step's output schema (calibrated:
the sweep was checked against 4.02's `narrative_verdict` prose and
`direction_justification.why_not_forbidden`, both known present, before
any absence was believed).  The line drawn: whole-object prose keyed by
its authoring step (`creative_direction`, `rough_cut_review`,
`speech_sequence`) is SELF-MARKING - the key names the source.  Prose
subfields inside an object this file classifies as a MEASUREMENT are
not, and those are what `VISION_PROSE_PATHS` marks:

* MARKED HERE - vision prose inside `semantic_analysis_documents` /
  `semantic_analysis`, cited as measurement by 9 flagging steps
  (2.02, 2.05, 3.02, 4.02, 4.03, 4.04 and the rest of
  `EVIDENCE_SOURCES`).  A downstream DECISION (contradicted vs not)
  rested on unattributed text: prose-vs-prose read as CONTRADICTED.
* ALREADY MARKED - `cut_decisions` verdict+note reaches 4.02 as
  `narrative_verdict` / `verdict_note`, and step 4.02's `handoff.md`
  states in its own prose that a verdict is the review's JUDGEMENT
  rather than a measurement; the B-roll `description` column is
  vision prose travelling beside measured framing/stability/tags, and
  4.02's `outgoing/incoming_footage` deliberately carries measurements
  only.  Pinned by test, not rebuilt.
* SELF-MARKING, RECORDED - `creative_direction` (all eight fields are
  model prose by construction; `DIRECTION_KEYS` + `direction_value`
  raising on anything else is the source record), `rough_cut_review`
  whole-object to 4.02/4.03/4.04 (the key is the source), 2.02's
  `body_sequence` ordering (a judgement by construction; the spine
  contract already marks its time fields as lookup hints, AGENTS.md 6).
* CORRECTLY EXCLUDED - `music_selection.direction_justification`:
  2.05 explicitly DROPs `why_not_forbidden` with a `-` path. The drop
  IS the marking. Pinned by test. (3.03's old
  `-audio_spine.music_selection.*` drops went with the nested copy
  itself when the audit trail moved to its own file - captain's
  ruling, 2026-09-16; `library/tools/music_audit_trail.py`.)
* GENUINELY JUST PROSE - `topics_toon` / `transcripts_toon`
  (2.02's own prompt tables, consumed only by its own prompt),
  the derived-column definitions each planning handoff now carries in
  its own prose, run-summary lines.  No decision rests on them as
  evidence.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/direction_contradiction.py`. On 001's 29 Aug run "emotion" and "energy" appear ZERO times in the semantic documents and FOUR times each in the `creative_direction` block at stations 2 and 3: step 2.01 reads the footage once and every creative step after it inherits that reading whole, with no way to say what it measured disagrees. Prosody (#417) is the first deterministic measurement that CAN disagree with an inherited affect reading.
- **Captain's ruling, 2026-09-01: a step MAY FLAG and MAY NOT ACT.** Nothing reads a flag's content, nothing gates on one, and the field is SPLIT OUT of the answer before validation - so the output a flagging step produces is byte-for-byte the one it would have produced silently. Escalation to the captain happens OUTSIDE the pipeline.
- **It is `undetermined.py`'s twin carrying different cargo** - same `take`/`record`/`summary_lines` surface, same collector, same route into the prompt as DATA beside the context, and for the same reason: one instruction asked of many steps is single-sourced here rather than copied into each prompt. Do not build a second mechanism.
- **FOUR readings, not the sibling's three.** A gap is named by naming it; a CONTRADICTION is a claim ABOUT a measurement, and a claim with no measurement is a model politely disagreeing with its brief. `contradicted` needs an entry naming a `direction_field` in `DIRECTION_KEYS`, a `measurement`, and a `measured_in` the step was really routed. Everything else is `unevidenced` - kept verbatim, reported as itself, and NEVER counted as a contradiction. `nothing_contradicted` (`[]`) and `not_declared` (key absent) are the other two, and they are not each other.
- **Every step that reaches a model except the one that authors the direction - nine of them.** `color_grade` joined on 2026-09-03 by DERIVATION alone when 5.01 stopped being deterministic. A step needs a prompt to say it in, an inherited direction claim and a routed measurement. The model-reaching half is borrowed from `undetermined.DECLARING_STEPS`; the routed half is DERIVED from `dag.json`, so a new edge cannot leave it stale, and `render_motion_graphics` joined by that derivation alone when 4.06 stopped being deterministic. `creative_cohesion` declares `creative_direction` and is deterministic, so it has nothing to say it in. `validate` reaches a model and holds measurements but is handed no inherited direction, so it is out on the direction half - recorded in `CONSIDERED_AND_EXCLUDED` rather than left as a derivation side-effect.
- **`MEASURED_OUTPUTS` and `DECLINED_OUTPUTS` must together account for every output of every deterministic step**, and an unaccounted one raises at import - a new deterministic output says which side it is on before it can go quiet.
- **Its collector is the sibling's, and so are the sibling's two rules**: one flag per model ATTEMPT, numbered, with `final_by_step` the per-STEP reading the summary prints; and `state["direction_contradictions"]` MERGED rather than replaced, carried rows marked `from_a_previous_run`. The two channels print into the same run summary, so they must count on the same basis.
- **`Flag` and `Declaration` are constructed POSITIONALLY, so a new field goes LAST.** Added above `entries`, it takes the entries and the real entries land in the field after it - no error, just wrong rows, until something compares them.
- `tests/test_direction_contradiction.py`, `tests/test_undetermined_declaration.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from library.tools.creative_direction import DIRECTION_KEYS
from library.tools.undetermined import DECLARING_STEPS as _REACHES_A_MODEL

# The key the model writes.  One spelling, here, because a key name
# spelled twice is this repository's dominant bug class.
FIELD = "contradicts_direction"

# The four readings.  Spelled differently on purpose.
CONTRADICTED = "contradicted"
NOTHING_CONTRADICTED = "nothing_contradicted"
UNEVIDENCED = "unevidenced"
NOT_DECLARED = "not_declared"

READINGS = (CONTRADICTED, NOTHING_CONTRADICTED, UNEVIDENCED, NOT_DECLARED)

# What one entry may say.  The first three are what makes it a
# contradiction rather than an opinion; the rest are optional colour.
ENTRY_KEYS = (
    "direction_field",
    "direction_said",
    "measurement",
    "measured_in",
    "why_they_disagree",
    "complied_by",
)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DAG = os.path.join(_REPO, "library", "processes", "edit_video", "dag.json")
_STEPS = os.path.join(_REPO, "library", "steps")


# ── What counts as a measurement ────────────────────────────────────
#
# A measurement is a value a DETERMINISTIC step produced by measuring
# the material - the footage, the speech, the chosen track.  It is not
# every deterministic output: `subtitle_plan` and `assembly_manifest`
# are consolidations of decisions taken upstream, and holding a decision
# against the direction that produced it would prove nothing.
#
# The two tables below must TOGETHER account for every output of every
# deterministic step, and `_assert_deterministic_outputs_accounted`
# raises at import when one is unaccounted for.  Same shape as
# `creative_direction.MECHANICALLY_READ_KEYS` and `PROMPT_ONLY_KEYS`:
# a new deterministic output has to say which side it is on before it
# can go quiet.

MEASURED_OUTPUTS = {
    "clip_catalog": "1.02 - measured duration, frame rate and stored resolution per clip",
    "project_fps": "1.02 - the footage's measured frame rate",
    "source_resolution": "1.02 - the footage's measured stored resolution",
    # A MIXED container: per-window camera segments, usable ranges with a
    # deterministic method, and subject-visibility ranges are measurements;
    # the scene/action/object prose and the assessment judgements inside
    # the SAME document are VLM readings. `VISION_PROSE_PATHS` names the
    # prose half, and a step holding the document is told both halves by
    # name in `prompt_block` - otherwise a summary is cited as a
    # measurement and prose-vs-prose reads as CONTRADICTED.
    "semantic_analysis_documents": "1.03 - the vision pass's per-window scene, camera, action and assessment measurements (MIXED with VLM prose - see VISION_PROSE_PATHS)",
    "temporal_event_indices": "1.04 - the same index, per clip, as the event view",
    "audio_catalog": "1.02 - measured duration and audio streams per audio-only file (voiceover / music)",
    "audio_indices": "1.04 - the same sound index, per audio file, as the speech/energy view",
    "prosody_analysis": "1.05 - parselmouth pitch contour, speaking rate, voice quality and intensity",
    "object_segmentation": "1.06 - SAM 2 subject masks (unwired; nothing consumes them)",
    "ocr_extraction": "1.07 - on-screen text read off the frames",
    "music_analysis": "2.06 - librosa tempo, beat grid and energy dynamics of the chosen track",
}


# ── The prose half of the vision document ─────────────────────────────
#
# One step's prose becomes another step's evidence with no record of its
# source (2026-09-07 inventory, full table in the module docstring's
# PROSE_AS_EVIDENCE section).  The sharpest case is this file's own:
# `semantic_analysis_documents` is listed above as a measurement, and the
# document DOES contain measurements - camera segments, usable ranges
# with `usable_ranges_method`, subject-visibility ranges.  But the same
# document carries VLM prose judgements under the paths below, and every
# flagging step routed the document was told the whole of it is something
# it MEASURED.  A contradiction entry citing scene prose against the
# direction's prose is then prose-vs-prose recorded as CONTRADICTED -
# exactly the shape of the dashboard defect where unmeasured assessment
# defaults were rendered as findings.
#
# Marking, not removal: the paths still travel (B-roll, VFX and SFX
# planning genuinely need to know what the footage shows).  What changes
# is that `prompt_block` names them as NOT measurements for every step
# routed the document, so a downstream decision can tell a measurement
# from a reading.  One instruction, many steps, so this takes the
# MEASUREMENT_LEGEND / CUTS_LEGEND route: the words travel as data
# beside the context.
#
# Keyed as the model addresses them: `analysis.*` / `assessment.*` /
# `objects[]` / `blocks[]` inside one semantic document, matching the
# `context_fields` dotted paths (`semantic_analysis_documents.*.`...)
# and the `clip_observations` accessor in
# `library/tools/semantic_index.py`.

VISION_PROSE_PATHS = {
    "analysis.scene": (
        "VLM scene prose (`vision_schema_adapter.scene_prose` rendering of "
        "`scene[]` - location, type, lighting, notable features). A "
        "description of what the footage shows, not a measurement of it."
    ),
    "analysis.motion": (
        "VLM camera prose (`camera_prose` rendering of `camera[]`). "
        "Framing/movement/stability words are a reading of the picture, "
        "not signal processing - v3 measures no mood and no energy at "
        "all (AGENTS.md 10.1)."
    ),
    "analysis.mood": (
        "Retired-schema VLM affect. v3 does not measure mood; a legacy "
        "document's mood prose is a previous model's feeling about a "
        "still, not evidence."
    ),
    "analysis.energy": (
        "Same as mood: retired-schema VLM affect, unmeasured by v3."
    ),
    "analysis.emotion": (
        "Same as mood: retired-schema VLM affect, unmeasured by v3."
    ),
    "analysis.audio_prediction": (
        "The VLM guessing what the clip sounds like from the picture. "
        "Step 4.04 reads it for SFX planning; it is a guess about audio "
        "made without listening, never a measurement of any."
    ),
    "assessment.keywords": (
        "Tags derived from scene types, object roles and framings "
        "(`derived_keywords`) plus VLM-supplied keywords. Useful for "
        "retrieval, not evidence of anything."
    ),
    "assessment.moment_type": (
        "Retired-schema VLM verdict on what kind of moment this is. "
        "Deliberately NOT re-derived by the adapter (its docstring says "
        "why inventing it would mislead); a document carrying one was "
        "judged by a model."
    ),
    "assessment.content_type": (
        "VLM classification of what the clip is (talking-head vs "
        "coverage). Coverage decisions rest on it in 3.02, so its source "
        "matters: it is a classification, and `unknown` means unclassified, "
        "never b-roll."
    ),
    "assessment.usable_portions": (
        "A prose RENDERING of the ranges, not the ranges. "
        "`usable_ranges` with `usable_ranges_method` decides; a legacy "
        "document can carry `usable_portions: 0.0-188.5s` beside method "
        "`unmeasured` (001: 17 of 17 clips), and reading the rendering "
        "made the stale assertion look measured."
    ),
    "objects[].label": (
        "VLM prose sentences about what is in shot "
        "(`subject_summary` reads the primary subject first). "
        "Deliberately excluded from `derived_keywords`: labels are "
        "sentences, not tags."
    ),
    "objects[].readable_text": (
        "On-screen text the VLM claims to see. The local model reads "
        "sparsely, not never - a recorded claim that it provably cannot "
        "was wrong - so a reading is a lead, not a transcript. Step 1.07 "
        "(`ocr_extraction`) is the measurement where text matters."
    ),
    "blocks[].visual": (
        "One observed action rendered as prose (`_blocks_from_actions`), "
        "or `UNPARSED_WINDOW_VISUAL` where the VLM response could not be "
        "parsed at all. The sentinel says unmeasured; citing it as what "
        "the clip shows inverts its meaning."
    ),
    "blocks[].body_language": (
        "VLM prose about bodies, per action window. Same status as "
        "`blocks[].visual`."
    ),
    "blocks[].speech_cue": (
        "VLM prose about what the speech seems to accompany. A cue, not "
        "a transcript - the transcript is `temporal_index`."
    ),
}

# Routed input names that carry a vision document.  Most steps address
# it as `semantic_analysis_documents`; 4.02's input is named
# `semantic_analysis`.  The name differs, the document does not, and a
# caveat keyed on one spelling would leave the other unmarked.
VISION_INPUT_NAMES = frozenset({"semantic_analysis_documents", "semantic_analysis"})

DECLINED_OUTPUTS = {
    "sfx_library_status": "0.01 validates a SHARED library, not this project's material",
    "project_config": "1.01 - the project's own declarations, not a measurement of anything",
    "raw_footage_files": "1.01 - a listing of what is on disk",
    "raw_audio_files": "1.01 - a listing of audio-only files on disk",
    "skipped_files": "1.01/1.02 - a listing of what was not read",
    "total_files": "1.01 - a count of a listing",
    "total_clips": "1.02 - a count of a listing",
    "total_clips_analyzed": "1.03 - a count of a listing",
    "index_dir": "1.04 - a path",
    "source": "1.04 - which route produced the index",
    "total_failed": "1.04 - a count",
    "total_indexed": "1.04 - a count",
    "total_reused": "1.04 - a count",
    "a_roll_assignments": "3.01 places what 2.02 already chose; a placement is not a measurement",
    "hook_assignment": "3.01 - the same placement, for the hook",
    "subtitle_plan": "4.01 renders decisions already taken into caption cards",
    "subtitle_overlay": "4.05 - a rendered artifact",
    # 4.06 became hybrid on 2026-09-02 and its outputs are therefore no
    # longer required to be accounted for here.  The rows stay because
    # the claim they make is unchanged - both are rendered artifacts,
    # not measurements - and deleting a true row to satisfy a coverage
    # check is how a table starts disagreeing with the system.
    "motion_graphics_overlay": "4.06 - a rendered artifact",
    "timed_text_overlay": "4.06 - a rendered artifact",
    # 5.01 became hybrid on 2026-09-03 and its output is therefore no
    # longer required to be accounted for here.  The row stays because
    # the claim it makes is unchanged and deleting a true row to satisfy
    # a coverage check is how a table starts disagreeing with the system.
    "color_grade_spec": "5.01 carries a declared look plus a colourist's judgement; neither is a measurement of the material",
    "audio_mix_spec": "5.02 - a mix plan. It embeds measured levels, but it is routed to no step holding the direction, so nothing here could read them",
    "cohesion_review": "5.03 observes decisions, not material",
    "assembly_manifest": "5.04 consolidates every decision taken; holding it against the direction proves nothing",
}


def _load_dag() -> dict:
    with open(_DAG, encoding="utf-8") as fh:
        return json.load(fh)


def _manifest_for(step_ref: str) -> dict:
    dirname = step_ref.rsplit("/", 1)[-1]
    with open(os.path.join(_STEPS, dirname, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _build() -> tuple:
    dag = _load_dag()
    manifests, deterministic, outputs = {}, set(), {}
    for node in dag["nodes"]:
        node_id = node["id"]
        manifest = _manifest_for(node["step_ref"])
        manifests[node_id] = manifest
        if manifest.get("classification", {}).get("determinism") == "deterministic":
            deterministic.add(node_id)
        outputs[node_id] = [
            o.get("name") for o in manifest.get("interface", {}).get("outputs", [])
        ]

    # A model-reaching step need not be in the DAG. `select_reels` runs
    # on a FINISHED cut rather than inside the pipeline that produces
    # one, and this loop only ever walked DAG nodes - so an unwired model
    # step was invisible here, and the coverage assertion in
    # tests/test_direction_contradiction.py could never be satisfied for
    # one. Read those from the layout, which is where an unwired step
    # declares itself and its reason.
    from library.tools.project_layout import STEPS as _STEP_DIRS

    for step_dir in _STEP_DIRS:
        if step_dir.wired or step_dir.node_id in manifests:
            continue
        if step_dir.node_id not in _REACHES_A_MODEL:
            continue
        manifests[step_dir.node_id] = _manifest_for(
            f"step_{step_dir.dirname}")

    unaccounted = sorted({
        name
        for node_id in deterministic
        for name in outputs[node_id]
        if name not in MEASURED_OUTPUTS and name not in DECLINED_OUTPUTS
    })
    if unaccounted:
        raise RuntimeError(
            "library/tools/direction_contradiction.py does not account for "
            f"the deterministic output(s) {', '.join(unaccounted)}. Add each "
            "to MEASURED_OUTPUTS (a measurement of the material a step could "
            "hold against the direction) or to DECLINED_OUTPUTS with the "
            "reason it is not one."
        )

    routed: Dict[str, Dict[str, str]] = {}
    for edge in dag.get("edges", []):
        producer, consumer = edge.get("from"), edge.get("to")
        if producer not in deterministic:
            continue
        for produced, received in (edge.get("data_mapping") or {}).items():
            if produced in MEASURED_OUTPUTS:
                routed.setdefault(consumer, {})[received] = (
                    f"{MEASURED_OUTPUTS[produced]} (routed as `{received}`)"
                )
    return manifests, routed


_MANIFESTS, _ROUTED = _build()


# Measurements a step holds that no DAG edge carries.  Both are real
# routes the repository already documents, and neither is inferable from
# `dag.json`, so each is listed with the reason it cannot be.
OFF_DAG_MEASUREMENTS = {
    "audio_mix": {
        "bed_measurements": (
            "5.02's own pre-bridge reading of the chosen bed - its "
            "integrated loudness, loudness range, and how much of its "
            "energy sits in the band a voice lives in. It is the step's "
            "own output, so no edge carries it in, and it is where a "
            "direction can be contradicted: a brief calling the piece "
            "intimate against a bed that measures loud and busy in the "
            "speech band."
        ),
        "mix_windows": (
            "5.02's own per-window table - what the spine planned the bed "
            "to do under each block and the measured loudness of the "
            "speech under it (one ffmpeg loudnorm pass per block). Same "
            "route and same reason as bed_measurements: the step "
            "measures it itself."
        ),
    },
    "select_reels": {
        "reel_candidates": (
            "3.04's own pre-bridge collapses the cut into turns and "
            "measures every candidate stretch - turn count, how often it "
            "changes hands, each speaker's share, where the hosts pitch, "
            "length against the brief, and takes recorded more than once "
            "(library/tools/reel_exchange.py). It is the step's own "
            "output, so no edge carries it in - and it is exactly where "
            "the direction can be contradicted: a stretch the brief "
            "calls a highlight that the turns show is one person talking."
        ),
    },
    "music_selection": {
        "music_candidates": (
            "2.04's own pre-bridge measures every candidate track - "
            "integrated loudness, loudness range, RMS spread, the played "
            "window's envelope and the share of energy in the speech band "
            "(library/tools/music_measurement.py). It is the step's own "
            "output, so no edge carries it in."
        ),
    },
    "review_rough_cut": {
        "render_qa_findings": (
            "6.02's measurements of the LAST render. `validate` is the "
            "final node and 3.03 is in phase 3, so an edge would be a back "
            "edge; they travel by name in `gather_step_inputs` "
            "(AGENTS.md 10.4)."
        ),
    },
}

# Measurements a step holds that travel on a DAG edge the derivation
# cannot see.  `_build` only routes outputs of DETERMINISTIC steps, so a
# deterministic measurement a HYBRID step's post-bridge computes - 2.02's
# aligner writing `alignment_report` onto its own output - never appears
# in `_ROUTED`, and 3.03's richest routed measurement was not citable:
# any disagreement grounded in it read as UNEVIDENCED.
#
# Unlike OFF_DAG_MEASUREMENTS these DO arrive on an edge, so each entry
# is admitted only where the step declares the input AND shows it to
# the model.  `mesh_spine` is routed `speech_sequence` too but drops the
# report by name (`-speech_sequence.alignment_report`) with no view
# replacing it, so its model never sees the numbers and must not be
# invited to cite them.  3.03 drops the raw report the same way but
# declares `view:alignment` - the same numbers ordered for a reader
# (`library/tools/alignment_findings.py`) - which is the half its model
# sees, and the entry says so.
HYBRID_MEASUREMENTS = {
    "review_rough_cut": {
        "speech_sequence": (
            "2.02's aligner measurements of each passage's INSIDES "
            "(alignment_report: leading_gap_seconds, "
            "largest_gap_seconds, voiced_fraction, anchors_considered, "
            "per passage), routed as `speech_sequence` and shown to you "
            "as `view:alignment`. Cite only those numbers. The "
            "`body_sequence` ordering beside them is 2.02's own judgement, "
            "not a measurement of anything."
        ),
    },
}

# The step that AUTHORS the direction cannot inherit one.
AUTHORS_THE_DIRECTION = "creative_direction"


# Considered and deliberately left out.  `validate` (6.02) reaches a
# model and holds measurements - its own bridge runs render_qa over the
# finished render - so whether it belongs in this channel is a real
# question (task vep-validate-in-flagging-steps, 2026-09-02).  The
# answer is no, on the direction half of the membership rule: its
# manifest declares no `creative_direction` input and no DAG edge routes
# one to it, so it holds nothing inherited to hold those measurements
# against.  Its only deterministic-routed input is `assembly_manifest`,
# which DECLINED_OUTPUTS records as a consolidation of decisions, not a
# measurement.  Recorded here rather than left to the derivation alone
# so the next edit to this file meets the question deliberately instead
# of re-answering it by accident.  If a future edge routes the
# direction to `validate`, delete this row and let the derivation pick
# it up - and list its bridge's render_qa measurements in
# OFF_DAG_MEASUREMENTS, the route `music_selection` and `select_reels`
# take for measurements no edge carries.
CONSIDERED_AND_EXCLUDED = {
    "validate": (
        "declares no `creative_direction` input and no DAG edge routes "
        "one to it; nothing inherited to hold its render_qa measurements "
        "against"
    ),
}


def _flagging_steps() -> Dict[str, Dict[str, str]]:
    steps: Dict[str, Dict[str, str]] = {}
    for node_id, manifest in _MANIFESTS.items():
        if node_id == AUTHORS_THE_DIRECTION:
            continue
        if node_id not in _REACHES_A_MODEL:
            # No prompt, nothing to say it in.
            continue
        inputs = {
            i.get("name")
            for i in manifest.get("interface", {}).get("inputs", [])
        }
        if "creative_direction" not in inputs:
            continue
        evidence = {
            name: why
            for name, why in _ROUTED.get(node_id, {}).items()
            if name in inputs
        }
        evidence.update(OFF_DAG_MEASUREMENTS.get(node_id, {}))
        evidence.update({
            name: why
            for name, why in HYBRID_MEASUREMENTS.get(node_id, {}).items()
            if name in inputs
        })
        if evidence:
            steps[node_id] = evidence
    return steps


# node id -> {input name: what it measures}.  The steps that hold BOTH an
# inherited direction and something measured to hold against it.
EVIDENCE_SOURCES = _flagging_steps()
FLAGGING_STEPS = frozenset(EVIDENCE_SOURCES)


def flags(step_id: str) -> bool:
    return step_id in EVIDENCE_SOURCES


def evidence_sources(step_id: str) -> Dict[str, str]:
    return dict(EVIDENCE_SOURCES.get(step_id, {}))


# ── One flag ────────────────────────────────────────────────────────

@dataclass
class Flag:
    """One step's answer to the question, on one attempt.

    `attempt` is the sibling's field, for the sibling's reason
    (`undetermined.Declaration`): a step whose answer fails QA is asked
    again, so one step can produce three flags in a run, and unnumbered
    they are indistinguishable rows that make a reader counting steps
    count model calls. Every attempt is kept - a step flagging the SAME
    contradiction three times running is evidence, not noise - and
    `final_by_step` is what a reader wanting one row per step calls.
    It is declared LAST, below.
    """

    step_id: str
    reading: str
    entries: List[Dict[str, str]] = field(default_factory=list)
    """The entries that carried a measurement. Only these are
    contradictions."""

    unevidenced: List[Dict[str, str]] = field(default_factory=list)
    """Entries the model wrote that named no measurement, or named one
    the step was not routed, or named a direction field 2.01 is not asked
    for. Kept verbatim and reported as themselves: dropping them would
    hide that the field is being filled with prose, which is the failure
    mode this design is against."""

    malformed: str = ""
    """Set when the key was present but not a shape this could read. A
    model that answered in the wrong shape did answer, and that is a
    different thing to fix from a model that stayed silent."""

    attempt: int = 1
    """Which model call this was, stamped by `record`. LAST in the field
    order deliberately: `Flag` is constructed positionally, so inserting
    a field above `entries` silently rebinds every such call."""

    @property
    def contradicted(self) -> bool:
        return self.reading == CONTRADICTED


def schema_entry() -> dict:
    """The field, in the shape `generate_output_schema_text` renders."""
    return {
        "name": FIELD,
        "type": "array",
        "required": False,
        "description": (
            "Measurements you were routed that contradict the creative "
            "direction you were handed. Empty list [] if nothing you "
            "measured disagreed - that is a complete answer and the "
            "expected one. Flagging does not change your answer: comply "
            "with the direction regardless."
        ),
    }


def prompt_block_for(evidence: Dict[str, str]) -> str:
    """The instruction for a holder of `evidence`, whatever declared it.

    Steps hold DAG-routed measurements (`prompt_block` below, per step
    because the vocabularies it names are per step). A project-declared
    creative task holds the measurements its own declaration names -
    the OFF_DAG_MEASUREMENTS shape, which `select_reels` and
    `music_selection` already take for measurements no edge carries - so
    it is rendered through this same builder rather than a second one.
    One shape for "flag what you measured against what you were handed".
    """
    sources = dict(evidence)
    lines = [
        "\n\n## Where your measurements disagree with the direction\n\n",
        f"Alongside your answer, return `{FIELD}`: the places where "
        "something you were MEASURED disagrees with the creative "
        "direction you were handed.\n\n",
        "**Flagging does not change what you do.** The creative "
        "direction governs your answer whether you flag or not. Produce "
        "the answer the direction asks for, and record the disagreement "
        "beside it. Do not deviate, hedge or split the difference: the "
        "flag is read by a human, and it is the only thing here that "
        "decides anything.\n\n",
        "Each entry is an object:\n\n",
        "```json\n"
        "{\n"
        '  "direction_field": "which creative_direction field it '
        'contradicts",\n'
        '  "direction_said": "what that field said, quoted",\n'
        '  "measurement": "the measured value, with its number or its '
        'measured words",\n'
        '  "measured_in": "which of your routed inputs you read it in",\n'
        '  "why_they_disagree": "one sentence",\n'
        '  "complied_by": "what you did anyway"\n'
        "}\n"
        "```\n\n",
        "`direction_field` must be one of: " + ", ".join(DIRECTION_KEYS) + ".\n\n",
        "`measured_in` must be one of the measurements you were routed:\n\n",
    ]
    for name, why in sorted(sources.items()):
        lines.append(f"- `{name}` - {why}\n")
    if VISION_INPUT_NAMES & set(sources):
        lines.append(
            "\nOne of those inputs is MIXED: the vision document carries "
            "measurements AND another model's prose in the same object, "
            "and only the first half can contradict anything. These "
            "paths are VLM readings, not measurements - do not cite them "
            "as `measurement`, and an entry whose only evidence is one "
            "of them is prose disagreeing with prose:\n\n"
        )
        for path in sorted(VISION_PROSE_PATHS):
            lines.append(f"- `{path}` - {VISION_PROSE_PATHS[path]}\n")
        lines.append(
            "\nWhat DOES count in the vision document: camera segments "
            "with time bounds, `usable_ranges` read with "
            "`usable_ranges_method`, and subject-visibility ranges. "
            "Cite those with their numbers and bounds.\n"
        )
    lines.append(
        "\nAn entry with no `measurement`, or naming an input outside "
        "that list, is NOT a contradiction and is recorded separately as "
        "an unevidenced disagreement. The evidence has to travel with "
        "the claim: an opinion that the direction is wrong is not what "
        "this field collects, and prose disagreement here is worse than "
        "silence.\n\n"
        "Return `[]` when nothing you measured disagreed. An empty list "
        "is a complete answer and it is the expected one on most calls - "
        "it is read as \"nothing contradicted\", not as a failure to "
        "reply. Omitting the field entirely is read as a non-answer and "
        "recorded as one, so return `[]` rather than leaving it out.\n"
    )
    return "".join(lines)


def prompt_block(step_id: str) -> str:
    """The instruction, delivered as DATA beside the context.

    One instruction asked of many steps, so this takes the route
    `music_measurement.MEASUREMENT_LEGEND` and `CUTS_LEGEND` already
    take: the words travel with the call rather than being edited into
    the prompt file.

    The block is per step because the two closed vocabularies it names -
    the direction's own fields, and the measurements THIS step was
    routed - are what make an entry checkable.
    """
    return prompt_block_for(evidence_sources(step_id))


def _normalise_entry(raw: Any) -> Optional[Dict[str, str]]:
    if isinstance(raw, str):
        text = raw.strip()
        # A bare string can carry no measurement, so it can only ever be
        # unevidenced. It is kept rather than dropped.
        return {"direction_said": text} if text else None
    if not isinstance(raw, dict):
        return None
    entry = {}
    for key in ENTRY_KEYS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str) and value.strip():
            entry[key] = value.strip()
    return entry or None


def is_evidenced(step_id: str, entry: Dict[str, str],
                 evidence: Optional[Dict[str, str]] = None) -> bool:
    """Whether an entry carries the evidence that makes it a contradiction.

    Three conditions, and all three are closed vocabularies rather than
    judgements: the direction field is one step 2.01 is asked for, the
    measurement is present, and the input it was read in is one this step
    was actually routed. Nothing here judges whether the disagreement is
    real - that is the captain's, and a rule deciding it would become the
    director (AGENTS.md 10.5).

    `evidence` overrides the DAG-derived table: a project-declared
    creative task holds the measurements its own declaration names
    rather than routed ones, and `take` below passes them through. None
    means "what the derivation says", which is every step.
    """
    sources = EVIDENCE_SOURCES.get(step_id, {}) if evidence is None else evidence
    return (
        entry.get("direction_field") in DIRECTION_KEYS
        and bool(entry.get("measurement"))
        and entry.get("measured_in") in sources
    )


def take(step_id: str, answer: Any,
         evidence: Optional[Dict[str, str]] = None) -> tuple:
    """Split the flag out of a model answer.

    Returns `(answer_without_the_field, Flag)`.

    The field is REMOVED from the answer rather than carried with it, for
    the reason its sibling gives: a step's outputs are what its manifest
    declares, `validate_step_output` refuses an unexpected extra key, and
    a post-bridge is handed the model's answer as its own input. It is
    also what makes compliance structural rather than promised - the
    output that leaves this function is the output the step would have
    produced without the field.

    `evidence` is the project-declared task half: a task's measurements
    come from its own declaration rather than the derivation, so its
    caller passes them and `is_evidenced` reads them instead. None means
    "the derivation", which is every step - and a caller passing an
    evidence map for a step id would be overruling the derivation, so
    only None is valid there.
    """
    if (evidence is None and not flags(step_id)) or not isinstance(answer, dict):
        return answer, Flag(step_id=step_id, reading=NOT_DECLARED)

    if FIELD not in answer:
        return answer, Flag(step_id=step_id, reading=NOT_DECLARED)

    remainder = {k: v for k, v in answer.items() if k != FIELD}
    raw = answer[FIELD]

    if raw is None:
        return remainder, Flag(
            step_id=step_id, reading=NOT_DECLARED,
            malformed="the field was present and null")

    malformed = ""
    if isinstance(raw, list):
        rows = raw
    else:
        rows = [raw]
        malformed = "answered with a single value, not a list"

    if isinstance(raw, list) and not raw:
        return remainder, Flag(step_id=step_id, reading=NOTHING_CONTRADICTED)

    entries = [e for e in (_normalise_entry(r) for r in rows) if e]
    if not entries:
        return remainder, Flag(
            step_id=step_id, reading=NOT_DECLARED,
            malformed=f"{len(rows)} entries, none of them readable")

    evidenced = [e for e in entries if is_evidenced(step_id, e, evidence)]
    unevidenced = [e for e in entries if not is_evidenced(step_id, e, evidence)]
    reading = CONTRADICTED if evidenced else UNEVIDENCED
    return remainder, Flag(step_id=step_id, reading=reading, entries=evidenced,
                           unevidenced=unevidenced, malformed=malformed)


# ── The collector ───────────────────────────────────────────────────
#
# `present_llm_step` and the run summary are the same process, the same
# way `get_logger()` and `undetermined` already are. The record also goes
# onto the pipeline state, so an audit reading `pipeline_data.json`
# months later sees it without the process that collected it.

_collected: List[Flag] = []


def record(flag: Flag) -> None:
    """Collect one attempt's flag, NUMBERING it as it lands.

    Counted off what has actually arrived rather than passed in by the
    retry loop, for the reason `undetermined.record` states: a call that
    raises before an answer is parsed records nothing, so a loop index
    and the stored rows can drift apart.
    """
    flag.attempt = 1 + sum(1 for f in _collected if f.step_id == flag.step_id)
    _collected.append(flag)


def collected() -> List[Flag]:
    return list(_collected)


def reset() -> None:
    _collected.clear()


def as_records(flags_=None) -> List[dict]:
    """The flags in the shape that goes onto the state file."""
    rows = []
    for f in (collected() if flags_ is None else flags_):
        row = {"step_id": f.step_id, "reading": f.reading,
               "entries": list(f.entries), "attempt": f.attempt}
        if f.unevidenced:
            row["unevidenced"] = list(f.unevidenced)
        if f.malformed:
            row["malformed"] = f.malformed
        rows.append(row)
    return rows


def final_by_step(flags_=None) -> List[Flag]:
    """One flag per step - the LAST attempt, in first-seen order.

    The last attempt is the one whose answer the step returned. The
    earlier ones are still in `collected()` with their own numbers.
    """
    rows = collected() if flags_ is None else flags_
    latest: Dict[str, Flag] = {}
    for f in rows:
        latest[f.step_id] = f
    return list(latest.values())


def merge_records(previous, current) -> List[dict]:
    """Fold this run's flag rows onto what a previous run recorded.

    `state["direction_contradictions"]` was REPLACED, so a `--rerun` of
    one step erased every other step's flags - the narrowest possible run
    destroying the record. Same shape and same reasoning as
    `undetermined.merge_records`: a step this run answered replaces its
    own rows at every attempt, and a step it did not reach keeps them,
    MARKED, because a carried flag is a claim about measurements that may
    since have moved.
    """
    fresh = {row.get("step_id") for row in current}
    merged = []
    for row in previous or []:
        if not isinstance(row, dict) or row.get("step_id") in fresh:
            continue
        carried = dict(row)
        carried["from_a_previous_run"] = True
        merged.append(carried)
    merged.extend(current)
    return merged


def summary_lines(flags_=None) -> List[str]:
    """What the run summary prints, after `status` is decided.

    This is the READER, and it is the only one. It prints; it assigns
    nothing and it fails nothing. Escalating a contradiction to the
    captain happens outside the pipeline.
    """
    every_attempt = collected() if flags_ is None else flags_
    if not every_attempt:
        return []
    # One row per STEP - the last attempt.  Printing every attempt made a
    # reader counting names count model calls.
    rows = final_by_step(every_attempt)
    lines = ["Where a step's measurements contradicted the direction:"]
    retried = sorted(f"{f.step_id} ({f.attempt} attempts)"
                     for f in rows if f.attempt > 1)
    contradicted = [f for f in rows if f.reading == CONTRADICTED]
    empty = [f for f in rows if f.reading == NOTHING_CONTRADICTED]
    unevidenced = [f for f in rows if f.reading == UNEVIDENCED]
    silent = [f for f in rows if f.reading == NOT_DECLARED]
    for f in contradicted:
        for entry in f.entries:
            lines.append(
                f"  {f.step_id}: `{entry.get('direction_field')}` said "
                f"\"{entry.get('direction_said', '')}\" - measured "
                f"\"{entry.get('measurement')}\" in "
                f"`{entry.get('measured_in')}`"
            )
            why = entry.get("why_they_disagree")
            if why:
                lines.append(f"      {why}")
            complied = entry.get("complied_by")
            if complied:
                lines.append(f"      complied by: {complied}")
    if contradicted:
        lines.append("  The step FLAGGED and COMPLIED. Nothing in the "
                     "pipeline acts on this; it is for the captain.")
    for f in unevidenced:
        lines.append(
            f"  {f.step_id}: {len(f.unevidenced)} disagreement(s) carrying "
            "no routed measurement - recorded as unevidenced, NOT as a "
            "contradiction")
    if empty:
        lines.append("  measured nothing that contradicted: "
                     + ", ".join(sorted(f.step_id for f in empty)))
    if silent:
        lines.append("  did not answer the question (recorded as a "
                     "non-answer, not as \"nothing contradicted\"): "
                     + ", ".join(sorted(f.step_id for f in silent)))
    if retried:
        # Said rather than hidden: the reading above is the LAST attempt's.
        lines.append("  answered more than once (the reading above is the "
                     "last attempt): " + ", ".join(retried))
    return lines
