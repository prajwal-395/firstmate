"""Named views a manifest may put in the prompt instead of a raw dot path.

`context_fields` is an allow-list of dot paths into a step's routed
inputs, so what the model reads is always the SHAPE the pipeline stores.
That is right for most things and wrong for one: WhisperX's per-word
timings.

`temporal_index.*.speech_regions` carries, per clip, a list of regions
each holding its text AND every word in it with a start and an end.  On
project 001 that is 110 regions, 1,439 word records and 93,246 bytes -
82.5% of `creative_direction`'s entire prompt - to say 7,184 bytes worth
of English.  No creative model is asked anything a word boundary
answers; the code that cuts on them (`speech_sequence`'s post-bridge,
`spine_contract`, `plan_subtitles`, `bookends`) reads them from the
per-clip index files and from the unprojected inputs, never from the
prompt.

A view is the enumeration that lets a manifest ask for the READING of a
routed input rather than its storage shape.  One table, one builder per
name, and an unknown name raises - the same shape `beat_grid`,
`energy_reading`, `music_behavior`, `safe_area` and `series_look` use.

Declare one by putting `view:<name>` in `context_fields`.  A view whose
source input is not routed to the step contributes nothing, exactly as a
dot path that resolves to nothing does; the contract that a step
declaring a view also declares its source input is asserted on the
manifests, in `tests/test_transcript_view.py`.


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**Every LLM step declares `context_fields`, and the deterministic half loses nothing by it.**
Projection happens inside `present_llm_step`, so a hybrid's post-bridge and a `deterministic_with_llm` step's `step.py` keep receiving the unprojected inputs - only the prompt narrows. A step declaring none is handed every byte it was routed. [why](docs/RULE_EVIDENCE.md#two-steps-had-no-projection)
- A path prefixed with `-` DROPS what the paths above it selected. Prefer it to enumerating what to keep. `"timed_spine"` then `"-timed_spine.structure.*.word_timestamps"`.
- `render` (6.01) and `validate` (6.02) are the only unprojected LLM steps (captain decision). `tests/test_llm_context_routing.py` holds that exemption list.
- **A pre-bridge's own table is never projected away, and you do not have to list it.** `project_step_context` restores any `bridge_supplied` key the allow-list dropped. Listing it is the only way to NARROW it. [why](docs/RULE_EVIDENCE.md#the-bridge-table-that-was-projected-away)

**A table the prompt names, arriving with zero rows, is reported on the run that sends it.**
`library/tools/empty_table_guard.py`, called from `present_llm_step`, names every TOP-LEVEL key whose value is `[0]{...}` or `[]`.
It never fails a run and catches zero-row tables only. [why](docs/RULE_EVIDENCE.md#a-prompt-that-described-an-empty-table)
- **Build a pre-bridge table on a key the DAG really routes, and key its rows on the identifier the answer has to name.** A-roll entries are keyed `spine_block_position`, not `segment_id`.

**Word timings do not reach a prompt, and what a step cannot select by NAME it selects with a named VIEW.**
`library/tools/context_views.py` is the enumeration: a manifest may put `view:<name>` in `context_fields` and get a READING of a routed input rather than a path into it. An unknown name raises, and a view's NAME is the key it writes - which is what makes a second projection a no-op, and an `llm_only` step is projected twice on every run. [why](docs/RULE_EVIDENCE.md#the-transcript-arrived-with-every-word)
- `view:transcript` is what step 2.01 reads. **Step 2.02 does NOT declare it** - its pre-bridge builds `transcripts_toon` instead. [why](docs/RULE_EVIDENCE.md#the-transcript-shipped-twice) `view:transcript` is what step 2.01 reads instead of `temporal_index.*.speech_regions`: what was said, in which clip, between which two seconds.
- `view:spoken_lines` is what step 3.04 reads instead of the `timeline_transcript` document: every line of the cut's speech, with the speaker and the two seconds it sits between, and NOTHING else. It is the SUB-TURN boundary - the pre-bridge's `turns` table collapses 929 bound segments into 136 turns, and the step's own post-bridge snaps every chosen boundary out to a bound-segment edge, so without it the model is asked to draw a line at a granularity it cannot see. **The `turns` table stops carrying the text this view carries**: a turn's text is its segments' texts joined by a space, to the byte, so publishing both is the summary and its own source. A segment that straddles a cut is not a row and is REPORTED in one line, because a reel boundary is never placed on one. **A row also carries the TRANSCRIBER's own confidence in its words** - `avg_logprob`, verbatim, and the column appears only when the transcript records one; a transcript that records none says so in one line rather than carrying a derived stand-in. `script_mismatch` names any line whose letters fall outside the transcript's own dominant script, which is a transcription failure signature and not a speech one. No threshold fires on either: `library/tools/transcript_confidence.py`.
- `view:picture` is what a step reads to see a clip past its opening, and **every step that decides from what a shot looks like declares it** - 2.01, 2.02, 3.02, 4.02, 4.03 and 4.04. [why](docs/RULE_EVIDENCE.md#the-director-saw-the-first-nineteen-seconds)
  - **It is NOT a substitute for `scene[]` and must not be swapped in for it.** The view goes BESIDE `analysis.scene` (different axes).
  - **Its rows are keyed by the CATALOG clip id wherever a routed input makes that join possible.** It joins against `clip_catalog`, `a_roll_assignments` and `b_roll_assignments`; unjoinable documents are reported in `not_in_the_clip_list`.
  - **Raw `blocks` in a `context_fields` allow-list is the wrong route**, several times the view's size once `json.dumps`'d into a cell. `tests/test_picture_view.py` fails if one comes back.
- `view:stability` is the two camera-steadiness signals SIDE BY SIDE - declared by `select_broll`, `plan_transitions` and `plan_vfx`. **It resolves nothing**: whatever picked a winner would become the measurement (section 10.5). The VLM's per-window `camera[].stability` and the deterministic per-clip `assessment.camera_stability` measure different things and on 001 they disagreed on 9 of 17 clips while three tables in one run carried two different answers about the same clip.
- `view:alignment` is what step 2.02's `alignment_report` measured about each passage's INSIDES - declared by `review_rough_cut`. It ORDERS and REPORTS; no threshold fires on any of it. The run summary is its second reader (`library/tools/alignment_findings.py`).
- `view:prosody` is what steps 2.01 and 2.02 read instead of `prosody_analysis.profiles`. An allow-list selects by NAME and cannot tell a measurement from a record of its absence, so this selects by `library/tools/prosody_profile.profile_defect` - the same predicate step 1.05 refuses to write a hollow profile with. Real profiles pass through (minus the contour and per-word lists, which never reach a prompt); the rest become ONE line saying how many measured nothing and why. **State the absence, never hide it.** [why](docs/RULE_EVIDENCE.md#seventeen-copies-of-an-error-are-not-a-measurement)
- `view:emphasis` is what the anchor-consuming planners (4.02, 4.03, 4.04) read instead of the per-word prosody table: three scored words per spine block, joinable by `block_position` and addressable through `anchor: {word}` with `occurrence`. The model still decides; the measurement is context.
- `view:motion` is what the cut and effect planners (4.02, 4.03) read instead of the per-sample flow series: per block, the clip's dominant direction and motion kind plus the action onsets and apexes inside the block's own range, joinable by `block_position` and addressable through `anchor: {motion_peak}` / `{action_onset}` with `occurrence`. The model still decides; the measurement is context.
- `view:beatgrid` is what a step reads to address a beat by NUMBER instead of snapping to one in code: one row per bar (bar, downbeat seconds, beats in it) plus the grid's provenance. Declared by `mesh_spine`, `plan_transitions`, `plan_vfx` and `plan_sfx`; the per-beat series stays withheld and the post-bridge resolves every anchor to an exact frame (`library/tools/sub_block_anchor.py`).
- `view:sectiongrid` is what a step reads to address a musical section by LABEL instead of by seconds: one row per measured section (label, span, first-downbeat seconds) plus the grid's provenance. Declared by `mesh_spine`, `plan_transitions` and `plan_vfx`; the boundary series stays withheld and the post-bridge resolves every `anchor: {section}` to an exact frame (`library/tools/sub_block_anchor.py`). Labels arrive verbatim from the model - a section it cannot give stays absent, never guessed.
- `view:soundevents` is what a step reads to address a non-speech sound by LABEL instead of by seconds: one row per block with a source clip, carrying the measured event spans inside the block's own source range (label, timeline span, confidence) plus the measurement's provenance. Declared by `plan_transitions`, `plan_vfx` and `plan_sfx`; the frame series stays withheld and the post-bridge resolves every `anchor: {event}` to an exact frame (`library/tools/sub_block_anchor.py`). Labels arrive verbatim from the model's own AudioSet vocabulary - voiced speech withheld, a label no clip measured stays absent, never guessed.
- **A view is not routing.** The step still has to declare the input the view reads.
- **The code that cuts on the timings still gets every word**, because none of it reads the prompt: every post-bridge and `step.py` receives the UNPROJECTED inputs.
- **A declaration of NOTHING BUT `-` paths means "everything, minus these".**
- `tests/test_transcript_view.py`, `tests/test_prosody_view.py`, `tests/test_spoken_lines_view.py`.
"""

VIEW_PREFIX = "view:"


def _transcript(data: dict) -> dict:
    """What was said, in which clip, between which two seconds.

    Built from the `temporal_index` input, which the DAG maps from step
    1.04's `temporal_event_indices` - a LIST of per-clip dicts (AGENTS.md 10.3).
    The per-word `words` array under each region is what this view exists
    to leave behind.
    """
    clips = data.get("temporal_index")
    if not isinstance(clips, list):
        return {}

    lines = []
    for entry in clips:
        if not isinstance(entry, dict):
            continue
        clip_id = entry.get("clip_id")
        for region in entry.get("speech_regions") or []:
            text = (region.get("text") or "").strip()
            if not text:
                continue
            lines.append({
                "clip_id": clip_id,
                "start": round(float(region.get("start", 0.0)), 3),
                "end": round(float(region.get("end", 0.0)), 3),
                "text": text,
            })
    if not lines:
        return {}
    return {"transcript": lines}


def _prosody(data: dict) -> dict:
    """The prosody that was MEASURED, and one line for what was not.

    `prosody_analysis.profiles` is a per-clip mapping and the failure
    path used to fill it with records that say only why nothing was
    measured.  Project 001 carried seventeen identical "parselmouth not
    installed" profiles into the creative-direction prompt, and one arm
    of the A/B said, unasked, that it had to ignore the whole section.

    A path allow-list cannot tell a measurement from a record of its
    absence - it selects by NAME - so this selects by `profile_defect`,
    the same predicate step 1.05 rejects a hollow profile with.  The
    absence is REPORTED rather than hidden: a model told plainly that
    prosody was not measured knows not to reason about it, which is what
    seventeen copies of an error message failed to say.
    """
    from library.tools.prosody_profile import profile_defect

    analysis = data.get("prosody_analysis")
    if not isinstance(analysis, dict):
        return {}
    profiles = analysis.get("profiles")
    # Step 1.05 writes a clip_id -> profile mapping; a LIST of profiles
    # each carrying its own clip_id is the other shape this key has been
    # written in, and reading only one of them would send nothing at all.
    if isinstance(profiles, list):
        profiles = {str(p.get("clip_id", i)): p
                    for i, p in enumerate(profiles) if isinstance(p, dict)}
    if not isinstance(profiles, dict):
        return {}

    measured, unmeasured = {}, {}
    for clip_id, profile in profiles.items():
        defect = profile_defect(profile)
        if defect:
            unmeasured[clip_id] = defect
        else:
            # The contours are inside the "prosody" block, not at the top level.
            # AGENTS.md 10.1: No raw value list reaches a prompt.
            cleaned = {}
            for k, v in profile.items():
                if k == "prosody" and isinstance(v, dict):
                    cleaned[k] = {pk: pv for pk, pv in v.items()
                                  if pk not in ("pitch_contour_10ms",
                                                "intensity_contour_50ms",
                                                # The per-word table is
                                                # 219 rows on 001's
                                                # longest clip; steps that
                                                # plan from it read
                                                # view:emphasis instead.
                                                "word_prosody")}
                else:
                    cleaned[k] = v
            measured[clip_id] = cleaned

    view = {}
    if measured:
        view["measured"] = measured
        view["clips_measured"] = len(measured)
    if unmeasured:
        reasons = sorted(set(unmeasured.values()))
        view["not_measured"] = (
            f"{len(unmeasured)} of {len(profiles)} clip(s) have no prosody "
            f"measurement: " + "; ".join(reasons[:3])
            + ("; ..." if len(reasons) > 3 else "")
        )
    if not view:
        return {}
    return {"prosody": view}


def _catalog_clip_ids(data: dict) -> dict:
    """The document id -> catalog clip id map, from whatever names both.

    The documents are keyed by FILE STEM (`IMG_1816_v3`) and every table a
    planning step reasons over is keyed by the catalog's synthetic id
    (`clip_011`) - AGENTS.md 10.1.  A table the step cannot join to its own
    spine is the `topics_toon` defect: step 2.02 reported it as "the two
    tables cannot be joined without a mapping the context does not
    contain", and answered from a mapping it had derived at an earlier
    step instead.

    Returns {the document's own clip_id: the catalog clip_id}.  The join
    needs a list carrying both a clip_id and a path.  The catalog
    is the one that carries every clip; the A-roll and B-roll assignments
    carry the placed ones and are what a planning step is routed when it
    is not routed the catalog.  A document that joins to none of them
    keeps the id it came with.
    """
    from library.tools.semantic_index import build_semantic_lookup

    entries = []
    for key in ("clip_catalog", "a_roll_assignments", "b_roll_assignments"):
        value = data.get(key)
        if isinstance(value, dict):
            value = list(value.values())
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            entries.append(item)
            # An A-roll assignment names its clips one level down.
            for seg in item.get("video_segments") or []:
                if isinstance(seg, dict):
                    entries.append(seg)
    if not entries:
        return {}

    docs = data.get("semantic_analysis_documents")
    if docs is None:
        docs = data.get("semantic_analysis")
    lookup = build_semantic_lookup(docs, entries)
    return {
        str(doc.get("clip_id")): cid
        for cid, doc in lookup.items()
        if isinstance(doc, dict) and doc.get("clip_id")
    }


def _picture(data: dict) -> dict:
    """What the footage SHOWS, across the WHOLE clip, one row per record.

    The creative director chose the story from `analysis.scene`, which is
    `scene[]` rendered as prose - and `scene[]` is one segment per clip on
    001, so `IMG_1816_v3` (188.6s, and the source of seven of the ten
    spoken lines in the cut) was described as "[0.0-18.9s] Outdoor urban
    area with a parking lot and construction site...".  That is 10% of the
    clip.  The step that decides what the video is about was deciding it
    from the first nineteen seconds.

    The material was already there and going to other steps: the vision
    pass writes one action window per ~10 seconds - 19 of them for that
    clip, 86 across 001's seventeen, covering 95% of the footage and
    reaching the last second of every one of the seventeen - and
    `vision_schema_adapter` renders them as `blocks`.

    **This is not a substitute for `scene[]`, and must not be read as
    one.**  `scene[]` says WHERE the clip is - location, type, lighting,
    notable features - and on 001 it says it for 46.4% of the footage.
    The action windows say WHAT HAPPENS, for all of it.  A step that needs
    the place still declares `analysis.scene`; this is the other axis, and
    it is the one that covers the whole clip.

    One row per record, not the raw records: `body_language` restates the
    same moment as posture and expression and costs 2.4x the bytes of
    `visual`, and `label` is `scene[]`'s location, which is the field that
    is degenerate in the first place.  `visual` is the reading that
    answers "what happens in this clip, and when".

    Rows are keyed by the CATALOG clip id wherever the routed inputs make
    that join possible (`_catalog_clip_ids`), because that is the id every
    other table in a planning step's context uses.

    A clip the vision pass described no action for is NAMED rather than
    silently absent - the same rule `_prosody` follows.
    """
    docs = data.get("semantic_analysis_documents")
    if docs is None:
        # `plan_transitions` is routed the whole step 1.03 output under
        # `semantic_analysis`; the documents are the same list.
        docs = data.get("semantic_analysis")
    if isinstance(docs, dict):
        if "semantic_analysis_documents" in docs:
            docs = docs["semantic_analysis_documents"]
        else:
            docs = list(docs.values())
    if not isinstance(docs, list):
        return {}

    joined = _catalog_clip_ids(data)

    rows, undescribed, unjoined, unparsed = [], [], [], []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        own_id = doc.get("clip_id")
        clip_id = joined.get(str(own_id), own_id)
        if joined and own_id and str(own_id) not in joined:
            unjoined.append(str(own_id))
        described = False
        for block in doc.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            visual = (block.get("visual") or "").strip()
            if not visual:
                continue
            described = True
            row = {
                "clip_id": clip_id,
                "start": _seconds(block.get("start")),
                "end": _seconds(block.get("end")),
                "visual": visual,
            }
            rows.append(row)
            if block.get("parse_error"):
                unparsed.append(
                    f"{clip_id} [{_seconds(block.get('start'))}-"
                    f"{_seconds(block.get('end'))}s]")
        if not described and clip_id:
            undescribed.append(str(clip_id))

    if not rows:
        return {}

    view = {"observed": rows}
    if undescribed:
        view["not_described"] = (
            f"{len(undescribed)} clip(s) have no observed action to show: "
            + ", ".join(sorted(undescribed))
        )
    if unparsed:
        view["unparsed_windows"] = (
            f"{len(unparsed)} window(s) could not be parsed and are shown "
            f"as unmeasured rather than omitted: "
            + ", ".join(unparsed)
        )
    if unjoined:
        # A MIXED table is the dangerous one: some rows key to the clip
        # ids the rest of the context uses and some do not, and nothing
        # on the row says which.  Say it rather than let it be inferred.
        view["not_in_the_clip_list"] = (
            f"{len(unjoined)} clip(s) are named by the vision pass's own id "
            f"because no routed clip list names them: "
            + ", ".join(sorted(unjoined))
        )
    return {"picture": view}


MAX_UNBINDABLE_LISTED = 20
"""How many unbindable stretches `_spoken_lines` names one by one.

Past this the line says how many it did not name.  An unbounded list of
them would be the raw-value-list defect (AGENTS.md 10.1) wearing the
clothes of a report.
"""


def _spoken_lines(data: dict) -> dict:
    """Every LINE of the cut's speech, and the seconds it may be cut at.

    Built from the `timeline_transcript` input.  One row per bound
    segment: who says it, the second it starts, the second it ends, and
    what it says.  Nothing else - no per-word timings, no absolute
    source path, no Resolve item id.  Nothing asks a model a question a
    word boundary answers.

    **This is the SUB-TURN boundary, and it is a capability rather than
    a convenience.**  Step 3.04's pre-bridge collapses these segments
    into speaker `turns` (`reel_exchange.turns_from_transcript`), and a
    turn is the coarser unit: on the field-test episode 929 bound
    segments collapse into 136 turns, so 793 of the places a reel may
    start or stop disappear in the collapse.  The step's own post-bridge
    then snaps every chosen boundary OUT to a bound-segment edge
    (`reel_proposal.snap_to_speech`), which means the two halves of one
    step disagreed about what a boundary is: the model was asked to draw
    a line at a granularity it could not see.

    It showed.  The run of 2026-09-06 that still carried the raw
    document timed a reel's closer to `Akshita 337.59-341.27`, a
    boundary that exists only in `segments` - her turn is
    328.61-341.27.  The run after the document was projected away said,
    unprompted, that *"every boundary I can name is a TURN boundary,
    because `turns` is the only speech table I was given"*, and opened
    its reel on "well this has been fun recently" because the line that
    should have opened it sits inside a turn.

    The document was right to go: 817,316 characters of it, 8,509
    per-word timing records, 940 absolute source paths and 940 Resolve
    item ids, to say 47,182 characters of English.  This carries the
    English and the two numbers that make it cuttable.

    **The turn table stops carrying the text this one carries.**  A
    turn's text is exactly its segments' texts joined by a space - the
    same 47,975 bytes, to the character - so publishing both is the
    summary and its own source (AGENTS.md 10.1).  The pre-bridge's
    `turns` keeps the grouping and the spans, which are a different
    axis and are what `reel_candidates` counts.

    **A segment that straddles a cut is not a row.**  `bound_segments`
    states the rule this obeys - "a reel boundary is never placed using
    one" - and `snap_to_speech` and `enrich` read the same list, so a
    row here that nothing downstream will snap to would be an offer the
    step cannot keep.  They are REPORTED in one line rather than hidden:
    on the field test 11 of them span 254.06 seconds, one is a 12.07s
    Akshita row lying across the Craig turn a reel borrowed as its
    closer, and the step that drew that boundary could not see it while
    the step that graded it could.

    **A row says how sure the TRANSCRIBER was of its own words, and the
    view says when the transcript records nothing.**  `avg_logprob` is
    the ASR's own per-line number, verbatim, and the column appears only
    when the transcript carries it; a transcript that does not gets one
    line saying so instead of a fabricated stand-in.  **A transcript
    heard by the HYBRID carries none by construction** - that
    transcriber emits no such number - and it says THAT rather than the
    sentence about an old transcript, because a re-run would not produce
    one.  What it publishes instead is `alignment_score`, the forced
    ALIGNER's own mean fit for the row's words, under its own name and
    with its own legend saying plainly it is not a confidence: measured,
    a misspelled brand name scores HIGHER than the right spelling.  It
    is the only per-row number that arm has, so it is published rather
    than measured and dropped, and nothing fires on it.  Adopting MFA
    costs even that number - it emits no per-word score - so an
    MFA-timed transcript carries no score column and the legend says
    the absence is by construction rather than leaving it to be
    discovered.  Beside it,
    `script_mismatch` names any line whose letters fall outside the
    script the rest of the transcript is written in - exact, no
    threshold, no model call, and on the field test 1 line of 940, which
    is the one the model asked about.  Both come from
    `library/tools/transcript_confidence.py`, which holds the account of
    the 15 seconds of reel the absence cost.
    """
    from library.tools.reel_proposal import bound_segments, straddling_segments
    from library.tools.transcript_confidence import (
        ALIGNMENT_SCORE_ABSENT_MFA,
        ALIGNMENT_SCORE_LEGEND,
        any_line_carries_confidence,
        confidence_notice,
        line_confidence,
        line_alignment_score,
        mismatch_report,
        timed_by_mfa,
    )

    document = data.get("timeline_transcript")
    if not isinstance(document, dict):
        return {}

    rows = sorted(bound_segments(document),
                  key=lambda s: float(s.get("timeline_start") or 0.0))
    # The column is added for the whole table or for none of it. A
    # per-row key that appears on some rows and not others makes the
    # TOON table sparse, which costs an empty cell on every row that
    # does not have one and tells a reader nothing about why.
    carries_confidence = any_line_carries_confidence(rows)
    # The aligner's score is published only where the transcriber's own
    # confidence is missing. Both at once would put two numbers that
    # answer different questions side by side under one heading, which
    # is how one gets read as the other.
    carries_alignment_score = (
        not carries_confidence
        and any(line_alignment_score(row) is not None for row in rows))

    lines = []
    for segment in rows:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        row = {
            "speaker": segment.get("speaker"),
            "start": round(float(segment.get("timeline_start") or 0.0), 2),
            "end": round(float(segment.get("timeline_end") or 0.0), 2),
            "text": text,
        }
        if carries_confidence:
            confidence = line_confidence(segment)
            row["avg_logprob"] = (None if confidence is None
                                  else round(confidence, 3))
        elif carries_alignment_score:
            fit = line_alignment_score(segment)
            row["alignment_score"] = None if fit is None else round(fit, 3)
        lines.append(row)
    if not lines:
        return {}

    view = {"lines": lines}

    # ── What the TRANSCRIBER said about its own reading ──────────────
    #
    # The model that chose reel 22 named this as the one thing it needed
    # and did not have, and put the cost in its own `could_not_determine`:
    # it ended a reel 15 seconds early because it could not tell a
    # garbled READING from garbled AUDIO.  Both halves of the answer are
    # here and they are different questions - see
    # `library/tools/transcript_confidence.py`.
    #
    # No threshold fires on either.  The number is published and the
    # model judges it, which is the captain's standing ruling
    # (AGENTS.md 10.5).
    # Which sentence, and there are THREE of them: the rows carry the
    # number, or the hybrid transcriber wrote them and emits none at
    # all, or this transcript predates the number being kept. The
    # middle one is new and is not the first one's "re-run and it will
    # be there" - a hybrid re-run produces the same absence, and a
    # reader told otherwise is being misled about what can be checked.
    view["transcription_confidence"] = confidence_notice(document, rows)
    if carries_alignment_score:
        view["alignment_score_legend"] = ALIGNMENT_SCORE_LEGEND
    elif timed_by_mfa(document):
        # Adopting MFA costs the per-word aligner score, and a column
        # that simply is not there reads as broken alignment. So the
        # legend key is present with the absence stated, rather than
        # missing - the same shape as the confidence absence above.
        view["alignment_score_legend"] = ALIGNMENT_SCORE_ABSENT_MFA
    mismatches = mismatch_report(document)
    if mismatches:
        view["script_mismatch"] = mismatches

    unbindable = sorted(straddling_segments(document),
                        key=lambda s: float(s.get("timeline_start") or 0.0))
    if unbindable:
        spans = []
        for segment in unbindable[:MAX_UNBINDABLE_LISTED]:
            spans.append(
                f"{float(segment.get('timeline_start') or 0.0):.2f}-"
                f"{float(segment.get('timeline_end') or 0.0):.2f} "
                f"{segment.get('speaker') or 'unattributed'}: "
                f"{(segment.get('text') or '').strip()}")
        held_back = len(unbindable) - len(spans)
        total = sum(float(s.get("timeline_end") or 0.0)
                    - float(s.get("timeline_start") or 0.0)
                    for s in unbindable)
        view["not_a_boundary"] = (
            f"{len(unbindable)} stretch(es) of speech, spanning "
            f"{total:.0f}s of the cut, could not be bound to one clip and "
            f"are NOT rows above, because a reel boundary is never placed "
            f"on one. They are usually the transcriber bridging a silent "
            f"gap, so the words are not necessarily spoken across the "
            f"whole span - but a reel whose edge falls inside one cuts "
            f"through it: "
            + "; ".join(spans)
            + (f"; and {held_back} more" if held_back else ""))

    return {"spoken_lines": view}


def _alignment(data: dict) -> dict:
    """What the aligner measured about each passage's INSIDES.

    `speech_sequence.alignment_report` is a real per-passage measurement
    that two steps are routed and both drop by name, and that no gate or
    report has ever opened.  On 001 it recorded a 1.169s silence inside a
    2.982s block - 39% of it - and nothing said so.

    It ORDERS and it REPORTS.  No threshold fires on any of these
    numbers, and AGENTS.md section 6's rule that there is no gap
    threshold and no voiced-fraction band is unchanged: whether a long
    internal pause is a dramatic beat or dead air is the model's to
    decide, which is why it has to be able to see it.
    See library/tools/alignment_findings.py.
    """
    from library.tools.alignment_findings import (
        ALIGNMENT_LEGEND, passage_rows,
    )

    sequence = data.get("speech_sequence")
    if isinstance(sequence, dict):
        report = sequence.get("alignment_report")
    else:
        report = None
    rows = passage_rows(report)
    if not rows:
        return {}
    return {"alignment": {"legend": ALIGNMENT_LEGEND, "passages": rows}}


def _stability(data: dict) -> dict:
    """The two stability signals SIDE BY SIDE, saying where they differ.

    `camera[].stability` is the vision model's per-window verdict and
    `assessment.camera_stability` is a deterministic per-clip one; they
    measure different things and they disagree.  On project 001 they
    disagreed on 9 of 17 clips, and three tables in a single run carried
    two different answers about clip_017 - the clip carrying 20.2s of a
    56.6s edit.  `plan_vfx` read the VLM's `stable` and put both of the
    video's effects there; `select_broll` and `plan_transitions` read the
    deterministic `unstable` about the same clip in the same run.  No
    step was told the two answers exist.

    **Nothing here resolves the disagreement.**  Whatever picked a winner
    would become the measurement (AGENTS.md 10.5).  It is DATA with a
    legend beside it.  **Not a freeze workaround any more** - the
    captain's freeze on `handoff.md` was lifted 2026-09-09 - and this
    view was not folded into any one prompt's prose because a view is
    routed to whichever steps declare it, so its columns have no single
    prompt to live in.  On the record as foldable per consumer.

    A clip neither signal measured is left out; a clip only one of them
    measured is `one_sided` and says so rather than reading as agreement.
    """
    from library.tools.camera_stability import (
        STABILITY_LEGEND, compare_stability_signals,
    )

    docs = data.get("semantic_analysis_documents")
    if docs is None:
        docs = data.get("semantic_analysis")
    if isinstance(docs, dict):
        if "semantic_analysis_documents" in docs:
            docs = docs["semantic_analysis_documents"]
        else:
            docs = list(docs.values())
    if not isinstance(docs, list):
        return {}

    joined = _catalog_clip_ids(data)

    rows, disagreeing = [], []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        assessment = doc.get("assessment") or {}
        deterministic = assessment.get("camera_stability")
        # A document written before the method field existed produced a
        # label without recording which signal produced it. Say that; do
        # not report it as "unmeasured", which is a different claim.
        method = assessment.get("camera_stability_method") or "unrecorded"
        words = []
        for window in doc.get("camera") or []:
            if isinstance(window, dict):
                word = window.get("stability")
                if isinstance(word, str) and word.strip() and word not in words:
                    words.append(word.strip())
        if deterministic in (None, "", "unknown") and not words:
            continue
        own_id = doc.get("clip_id")
        clip_id = joined.get(str(own_id), own_id)
        verdict = compare_stability_signals(deterministic, words)
        if verdict == "disagree":
            disagreeing.append(str(clip_id))
        rows.append({
            "clip_id": clip_id,
            "deterministic_stability": deterministic or "unknown",
            "deterministic_method": method,
            "vlm_stability": "/".join(words) if words else "unmeasured",
            "signals_agree": verdict,
        })

    if not rows:
        return {}

    view = {"legend": STABILITY_LEGEND, "clips": rows}
    view["disagreements"] = (
        f"{len(disagreeing)} of {len(rows)} clips carry two different "
        f"answers: " + ", ".join(sorted(disagreeing))
    ) if disagreeing else (
        f"0 of {len(rows)} clips carry two different answers."
    )
    return {"stability": view}


def _seconds(value):
    """A time in seconds, or None when the record carries no time."""
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


BEATGRID_LEGEND = {
    "what_this_is": (
        "One row per bar of the chosen music: the bar number, the "
        "timeline second its downbeat lands on, and how many grid beats "
        "the bar carries."
    ),
    "basis": (
        "Downbeat seconds are timeline seconds assuming the bed opens "
        "the reel at the chosen section (file time minus source_in). "
        "They address the music; the post-bridge resolves them."
    ),
    "how_to_address_a_beat": (
        "By NUMBER, never by seconds: a VFX/SFX/transition plan entry "
        "carries anchor {bar, beat}, {downbeat} or {beat} "
        "(library/tools/sub_block_anchor.py). Seconds are shown so a "
        "duration can be sanity-checked, not so an entry can name one."
    ),
    "estimated_vs_detected": (
        "downbeat_source says which grid answered: 'detected' where a "
        "tracker heard the bar starts, 'estimated' where they are every "
        "4th detected beat (which can sit a beat off). An anchor may "
        "demand the detected grid with grid: detected."
    ),
    "withheld": (
        "The per-beat series stays out of the prompt deliberately "
        "(AGENTS.md 10.1): proximity is decided in code, addressing by "
        "number is what this table is for."
    ),
}


def _beatgrid(data: dict) -> dict:
    """Bars and downbeats of the measured grid, for sub-block anchors.

    Fidelity rung 2: plans address whole blocks because no step could
    see the grid - `mesh_spine` is told not to derive one and the raw
    series are withheld from every planning prompt by name. This view
    is the addressed middle: bar numbers and downbeat seconds a plan
    entry names through `anchor`, resolved to exact frames at
    post-bridge time by `library/tools/sub_block_anchor.py`.

    Empty (no view) when no usable downbeat grid is routed - an
    estimated grid still answers, so only absence or sparseness says
    nothing. A view is not routing: the step still declares
    `music_analysis` and `music_selection`.
    """
    from library.tools.beat_grid import (
        beat_positions,
        bpm,
        downbeat_positions,
    )

    analysis = data.get("music_analysis")
    if not isinstance(analysis, dict):
        return {}
    selection = data.get("music_selection")
    downbeats = downbeat_positions(analysis, selection)
    if not downbeats:
        return {}
    beats = beat_positions(analysis, selection)
    tempo = analysis.get("tempo") or {}

    bars = []
    for number, start in enumerate(downbeats, start=1):
        stop = downbeats[number] if number < len(downbeats) else None
        count = sum(
            1 for b in beats
            if b >= start - 1e-9 and (stop is None or b < stop - 1e-9))
        bars.append({
            "bar": number,
            "downbeat_seconds": round(float(start), 2),
            "beats_in_bar": count,
        })
    return {"beatgrid": {
        "legend": BEATGRID_LEGEND,
        "bpm": bpm(analysis),
        "downbeat_source": tempo.get("downbeat_source") or "unknown",
        "downbeat_method": tempo.get("method") or "unknown",
        "bar_count": len(bars),
        "bars": bars,
    }}


SECTIONGRID_LEGEND = {
    "what_this_is": (
        "One row per measured section of the chosen music: the model's "
        "own functional label, the timeline seconds the span covers, "
        "and the timeline second of its first downbeat - the "
        "bar-aligned moment a cut or effect lands on."
    ),
    "basis": (
        "Seconds are timeline seconds assuming the bed opens the reel "
        "at the chosen section (file time minus source_in). "
        "Boundaries are snapped to the nearest downbeat of the same "
        "analysis run that labelled them. They address the music; the "
        "post-bridge resolves them."
    ),
    "how_to_address_a_section": (
        "By LABEL, never by seconds: a VFX/transition plan entry "
        "carries anchor {section: <label>} with optional {occurrence} "
        "(the nth span of that label, 1-based) and {edge: end} for "
        "the span's end. Without edge it resolves to the span's FIRST "
        "DOWNBEAT. (library/tools/sub_block_anchor.py). Seconds are "
        "shown so a duration can be sanity-checked, not so an entry "
        "can name one."
    ),
    "labels_are_measured": (
        "Labels arrive verbatim from the section model (intro, verse, "
        "chorus, bridge, solo, outro and what else it gives). A "
        "section the model cannot give - a drop, a phrase, a chorus "
        "on a track whose grid has none - stays absent and says so: "
        "an anchor naming it refuses with what the grid carries. "
        "`mean_label_activation` is the model's own mean softmax for "
        "the winning label over the span, not a calibrated "
        "confidence; null where the run carried no activations."
    ),
    "withheld": (
        "The boundary series stays out of the prompt deliberately "
        "(AGENTS.md 10.1): addressing by label is what this table is "
        "for."
    ),
}


def _sectiongrid(data: dict) -> dict:
    """Measured sections and their first downbeats, for section anchors.

    Fidelity rung 5c: plans cut on bars but no step could see the
    sections - `mesh_spine` paced gaps from prose and the cut planners
    counted bars from the track head. This view is the addressed
    middle: labels with spans and first-downbeat seconds a plan entry
    names through `anchor: {section}`, resolved to exact frames at
    post-bridge time by `library/tools/sub_block_anchor.py`.

    Empty (no view) when no usable section grid is routed. A view is
    not routing: the step still declares `music_analysis` and
    `music_selection`.
    """
    from library.tools.music_sections import sections_timeline

    analysis = data.get("music_analysis")
    if not isinstance(analysis, dict):
        return {}
    selection = data.get("music_selection")
    rows = sections_timeline(analysis, selection)
    if not rows:
        return {}
    grid = analysis.get("section_grid") or {}
    return {"sectiongrid": {
        "legend": SECTIONGRID_LEGEND,
        "method": grid.get("method") or "unknown",
        "validation": grid.get("validation") or "unrecorded",
        "labels_absent_note": grid.get("labels_absent_note") or "",
        "section_count": len(rows),
        "sections": [
            {
                "label": r["label"],
                "start_seconds": r["start_seconds"],
                "end_seconds": r["end_seconds"],
                "first_downbeat_seconds": r["first_downbeat_seconds"],
                "mean_label_activation": r["mean_label_activation"],
            }
            for r in rows
        ],
    }}


EMPHASIS_LEGEND = {
    "what_this_is": (
        "Per spine block, the three most emphasized spoken words, "
        "measured from pitch, loudness and duration - not guessed from "
        "punctuation. `most_emphasized_word` is a hint, not an order: "
        "a brief that names a word wins over it."
    ),
    "formula": (
        "emphasis = mean(f0_rel_semitones/3.0, loud_rel_db/4.0, "
        "log2(dur_ratio)) over the terms that measured: word-median F0 "
        "in semitones above the speaker's own clip median, word-median "
        "dB above the phrase's own word-span median, actual duration "
        "over expected from the speaker's own seconds-per-letter. "
        "`terms` says how many of the three answered; duration always "
        "does. Components ride along so a high score can be judged, "
        "not just taken."
    ),
    "how_to_address_a_word": (
        "By NAME through a sub-block anchor, never by seconds: a "
        "VFX/SFX/transition plan entry carries anchor {word: <word>} "
        "with optional {occurrence} (the nth saying in the block, "
        "1-based) and {edge: end}. The post-bridge resolves it to the "
        "exact frame (library/tools/sub_block_anchor.py). The anchor "
        "names the spelling in the block's line; `occurrence` here "
        "counts that same spelling."
    ),
    "withheld": (
        "The per-word table for the whole clip stays out of the prompt "
        "deliberately (AGENTS.md 10.1): three words per block is what a "
        "plan entry names, and the code that cuts on timings reads the "
        "full table from the unprojected inputs, never from the prompt."
    ),
}

_EMPHASIS_STRIP = " \t\n\r…—–!?,.;:'\"()[]{}"


def _emphasis_norm(text) -> str:
    return str(text or "").strip(_EMPHASIS_STRIP).lower()


def _emphasis(data: dict) -> dict:
    """The most emphasized words per spine block, for anchored plans.

    Fidelity rung 5b: step 1.05 measures one emphasis score per timed
    word, but the steps that plan punches and sounds (4.02, 4.03, 4.04)
    are routed `timed_spine` with the word timings projected OUT - by
    design, they are too big for a prompt. This view is the addressed
    middle: three scored words per block, joinable to the block by
    `block_position` and to the anchor by `(word, occurrence)`.
    The model still decides; the measurement is context.

    Matching is by spelling, paired in spoken order within each
    spelling: the profile and the block share the source clock, but
    step 2.02 may re-anchor a passage past its hint, so a time window
    would drop shifted words while order survives. A block whose clip
    measured nothing is NAMED in one line, not silently absent.
    """
    analysis = data.get("prosody_analysis")
    if not isinstance(analysis, dict):
        return {}
    profiles = analysis.get("profiles")
    if isinstance(profiles, list):
        profiles = {str(p.get("clip_id", i)): p
                    for i, p in enumerate(profiles)
                    if isinstance(p, dict)}
    if not isinstance(profiles, dict) or not profiles:
        return {}

    spine = data.get("timed_spine")
    if isinstance(spine, dict):
        spine = spine.get("structure")
    if not isinstance(spine, list):
        return {}

    rows, unmeasured = [], []
    for block in spine:
        if not isinstance(block, dict):
            continue
        position = block.get("position")
        words = block.get("word_timestamps")
        if isinstance(block.get("content"), dict) and not words:
            words = block["content"].get("word_timestamps")
        if not words:
            continue
        profile = profiles.get(str(block.get("clip_id")))
        prosody = (profile or {}).get("prosody") or {}
        scored = prosody.get("word_prosody") or []
        if not scored:
            unmeasured.append(position)
            continue

        by_spelling: dict = {}
        for entry in scored:
            if not isinstance(entry, dict):
                continue
            by_spelling.setdefault(
                _emphasis_norm(entry.get("word")), []).append(entry)
        for entries in by_spelling.values():
            entries.sort(key=lambda e: float(e.get("start", 0)))

        block_words = [w for w in words if isinstance(w, dict)]
        seen: dict = {}
        candidates = []
        for entry in block_words:
            spelling = _emphasis_norm(entry.get("word"))
            pool = by_spelling.get(spelling) or []
            index = seen.get(spelling, 0)
            seen[spelling] = index + 1
            if index >= len(pool):
                continue
            hit = pool[index]
            if hit.get("emphasis") is None:
                continue
            candidates.append({
                "word": spelling,
                "occurrence": index + 1,
                "emphasis": hit["emphasis"],
                "f0_st": hit.get("f0_rel_semitones"),
                "loud_db": hit.get("loud_rel_db"),
                "dur_ratio": hit.get("dur_ratio"),
            })
        if not candidates:
            unmeasured.append(position)
            continue
        candidates.sort(key=lambda c: -c["emphasis"])
        top = candidates[:3]
        rows.append({
            "block_position": position,
            "top_words": top,
            "most_emphasized_word": top[0]["word"],
            "most_emphasized_occurrence": top[0]["occurrence"],
        })

    view: dict = {"legend": EMPHASIS_LEGEND}
    if rows:
        view["blocks"] = rows
        view["blocks_measured"] = len(rows)
    if unmeasured:
        view["not_measured"] = (
            f"{len(unmeasured)} block(s) have no word-emphasis "
            f"measurement: "
            + ", ".join(sorted({str(p) for p in unmeasured}))
        )
    if not rows and not unmeasured:
        return {}
    return {"emphasis": view}


MOTION_LEGEND = {
    "what_this_is": (
        "Per spine block, the measured motion of the picture it plays: "
        "the clip's dominant direction and motion kind, and the action "
        "onsets and apexes inside the block's own source range with "
        "their timeline seconds."
    ),
    "how_measured": (
        "Dense Farneback optical flow on a 160x90 proxy at 5 Hz "
        "(block-match fallback per pair where OpenCV could not answer, "
        "said per clip by motion_method). magnitude is mean field "
        "displacement in units of 8 proxy-px; direction is the field "
        "median's eight-way compass, 'static' below the stillness "
        "floor, 'mixed' where the field moves with no dominant "
        "translation. Onsets are rising-edge crossings of the recorded "
        "threshold, apexes scipy local maxima - at least 0.5 s apart."
    ),
    "camera_vs_subject": (
        "A separation HYPOTHESIS, not a tracking: the field median is "
        "read as the camera (one rigid move), the residual past it as "
        "subject and shake. A subject filling the frame reads as "
        "camera; violent motion with blur reads `mixed` with high "
        "residual, because smeared frames share no structure for a "
        "median to hold onto - the peak still marks WHEN, even where "
        "no direction survives. A zoom or dolly reads as expansion "
        "either way: the field cannot tell them apart, so "
        "dominant_motion says zoom_in/zoom_out for both."
    ),
    "clip_level_direction": (
        "clip_direction is the WHOLE CLIP's, not the block's range: a "
        "block inherits its clip's verdict. Two blocks cut from one "
        "clip share it; a block whose range sits still inside a moving "
        "clip is overstated. The per-sample series stays in the "
        "per-clip index file (index_path in the routed summaries), "
        "never in a prompt."
    ),
    "how_to_address_a_peak": (
        "By KIND and occurrence through a sub-block anchor, never by "
        "seconds: a VFX/transition plan entry carries anchor "
        "{motion_peak} (the nth apex in the block) or {action_onset} "
        "(the nth onset), with optional {occurrence} (1-based, the "
        "default is 1). The post-bridge resolves it to the exact "
        "frame (library/tools/sub_block_anchor.py). Seconds are shown "
        "so a duration can be sanity-checked, not so an entry can "
        "name one."
    ),
    "regional_framing": (
        "When a time-bounded Gemma label with a movement, gesture, or "
        "facial-change cue overlaps the block, regional_framing carries "
        "compact 10 Hz, 640x360 face and local-motion evidence, "
        "plus advisory crop and keep-clear suggestions. Motion regions "
        "are found from background-compensated Farneback flow and are "
        "not semantically classified as hands or bodies. No overlapping "
        "action span means no regional pass was run. These suggestions "
        "are evidence for the edit decision and never change what the "
        "renderer places on screen."
    ),
    "withheld": (
        "The per-sample direction/magnitude series stays out of the "
        "prompt deliberately (AGENTS.md 10.1): peaks per block is what "
        "a plan entry names, and the code that cuts on timings reads "
        "the full series from the unprojected inputs, never from the "
        "prompt."
    ),
}


def _motion(data: dict) -> dict:
    """Measured motion per spine block, for cuts and effects on action.

    Fidelity rung 4d: vision gives prose ("the camera pans"), and no
    planning step could see a measured direction, a magnitude, or the
    moment an action starts or peaks. This view is the addressed
    middle: one row per block with a source clip, carrying the clip's
    dominant direction and kind plus the onsets and apexes inside the
    block's own source range, each with timeline seconds. The model
    still decides; the measurement is context.

    Reads the routed temporal summaries (`temporal_event_indices`,
    or `temporal_index` where the edge lands under that name) - the
    same shape `view:transcript` reads, carrying `motion_peaks`,
    `dominant_motion`, `dominant_direction` and `motion_method` per
    clip. A block with no source clip, no source range, or no measured
    clip is NAMED in one line, not silently absent.
    """
    from library.tools.spine_contract import source_to_timeline

    summaries = data.get("temporal_event_indices")
    if not isinstance(summaries, list):
        summaries = data.get("temporal_index")
    if not isinstance(summaries, list):
        return {}
    by_clip = {}
    for entry in summaries:
        if isinstance(entry, dict) and entry.get("clip_id"):
            by_clip[str(entry["clip_id"])] = entry

    spine = data.get("timed_spine")
    if isinstance(spine, dict):
        spine = spine.get("structure")
        if spine is None:
            spine = []
    if not isinstance(spine, list):
        return {}

    rows, unmeasured = [], []
    for block in spine:
        if not isinstance(block, dict):
            continue
        position = block.get("position")
        clip_id = block.get("clip_id")
        src_start = block.get("source_start")
        src_end = block.get("source_end")
        tl_start = block.get("timeline_start")
        tl_end = block.get("timeline_end")
        summary = by_clip.get(str(clip_id)) if clip_id else None
        flow_ok = (
            summary is not None
            and isinstance(summary.get("motion_peaks"), list)
            and summary.get("motion_method", "unmeasured")
            != "unmeasured"
            and isinstance(src_start, (int, float))
            and isinstance(src_end, (int, float))
            and isinstance(tl_start, (int, float))
            and isinstance(tl_end, (int, float))
        )
        if not flow_ok:
            unmeasured.append(position)
            continue
        peaks = []
        for peak in summary.get("motion_peaks") or []:
            if not isinstance(peak, dict):
                continue
            try:
                moment = float(peak.get("time", float("nan")))
            except (TypeError, ValueError):
                continue
            if not (float(src_start) - 0.1 <= moment
                    <= float(src_end) + 0.1):
                continue
            peaks.append({
                "timeline_seconds": round(source_to_timeline(
                    moment, block), 3),
                "kind": peak.get("kind"),
                "magnitude": peak.get("magnitude"),
            })
        peaks.sort(key=lambda p: (p["timeline_seconds"],
                                  str(p["kind"])))
        rows.append({
            "block_position": position,
            "clip_id": clip_id,
            "clip_direction": summary.get(
                "dominant_direction", "unknown"),
            "clip_motion": summary.get("dominant_motion", "unknown"),
            "motion_method": summary.get("motion_method", "unmeasured"),
            "peaks": peaks,
            "regional_framing": _regional_framing(
                summary, float(src_start), float(src_end)),
        })

    view: dict = {"legend": MOTION_LEGEND}
    if rows:
        view["blocks"] = rows
        view["blocks_measured"] = len(rows)
    if unmeasured:
        view["not_measured"] = (
            f"{len(unmeasured)} block(s) have no motion measurement "
            f"(no source clip, no source range, or an unmeasured "
            f"clip): "
            + ", ".join(sorted({str(p) for p in unmeasured}))
        )
    if not rows and not unmeasured:
        return {}
    return {"motion": view}


def _regional_framing(summary: dict, source_start: float,
                      source_end: float) -> dict:
    """Project selected-span suggestions that overlap one timed block."""
    spans = summary.get("regional_motion_spans")
    status = summary.get("regional_motion_status", "unmeasured")
    reason = summary.get("regional_motion_reason")
    if not isinstance(spans, list):
        spans = []
    matched = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        bounds = span.get("source_range")
        if (not isinstance(bounds, list) or len(bounds) != 2
                or not all(isinstance(value, (int, float))
                           for value in bounds)):
            continue
        left = max(source_start, float(bounds[0]))
        right = min(source_end, float(bounds[1]))
        if right <= left:
            continue
        matched.append({
            **span,
            "block_overlap": [round(left, 3), round(right, 3)],
        })
    if matched:
        status = "measured"
        reason = None
    elif spans:
        status = "no_candidate_overlap"
        reason = "No selected regional-motion span overlaps this source range."
    return {
        "status": status,
        "reason": reason,
        "candidate_spans": matched,
    }


SOUNDEVENTS_LEGEND = {
    "what_this_is": (
        "Per spine block, the non-speech sounds the block's own clip "
        "measured: laughter, impacts, music entrances and what else "
        "the soundtrack carries, each with its timeline span and the "
        "model's own confidence."
    ),
    "how_measured": (
        "PANNs Cnn14 DecisionLevelMax frame-level sound-event "
        "detection over the AudioSet vocabulary at 100 frames/s "
        "(CC-BY-4.0 weights, scripts/install_panns.sh), spans merged "
        "across gaps under 0.25 s, said per clip by "
        "sound_event_method. confidence is the span's peak frame "
        "probability. Speech spans 97.5% of transcript word-time on "
        "the captain's podcast audio, which is what makes the frame "
        "timing trustworthy; a cough/sneeze burst both camera mics "
        "hear lands within ~1 s on an independent CED-tiny pass."
    ),
    "vocabulary": (
        "Labels are the model's own AudioSet words, verbatim - name "
        "one exactly in an event anchor. Voiced-speech classes "
        "(Speech, Male/Female/Child speech, Conversation, Narration, "
        "Babbling, Whispering) are WITHHELD: the transcript times "
        "speech to the word, so a cut never lands on 'speech' "
        "instead of on a word. A label no clip measured stays "
        "absent, never guessed."
    ),
    "how_to_address_an_event": (
        "By LABEL and occurrence through a sub-block anchor, never by "
        "seconds: a cut, effect or sound entry carries anchor "
        "{event: <label>} (the nth span of that label in the block, "
        "occurrence 1-based, default 1; edge end for the span's end, "
        "default start is the onset). The post-bridge resolves it to "
        "the exact frame (library/tools/sub_block_anchor.py). "
        "Seconds are shown so a duration can be sanity-checked, not "
        "so an entry can name one."
    ),
}


def _soundevents(data: dict) -> dict:
    """Measured non-speech sound events per spine block, for event anchors.

    Fidelity rung 5e: the soundtrack carried no timed events - plans
    could land a hit on a word, a beat or a frame, but never on the
    laugh or impact the footage actually holds. This view is the
    addressed middle: one row per block with a source clip, carrying
    the measured event spans inside the block's own source range, each
    with timeline seconds. The model still decides; the measurement is
    context.

    Reads the routed temporal summaries (`temporal_event_indices`,
    or `temporal_index` where the edge lands under that name) - the
    same shape `view:motion` reads, carrying `sound_events` and
    `sound_event_method` per clip. A block with no source clip, no
    source range, or no measured clip is NAMED in one line, not
    silently absent.
    """
    from library.tools.sound_events import events_in_block, measured
    from library.tools.spine_contract import source_to_timeline

    summaries = data.get("temporal_event_indices")
    if not isinstance(summaries, list):
        summaries = data.get("temporal_index")
    if not isinstance(summaries, list):
        return {}
    by_clip = {}
    for entry in summaries:
        if isinstance(entry, dict) and entry.get("clip_id"):
            by_clip[str(entry["clip_id"])] = entry

    spine = data.get("timed_spine")
    if isinstance(spine, dict):
        spine = spine.get("structure")
        if spine is None:
            spine = []
    if not isinstance(spine, list):
        return {}

    rows, unmeasured = [], []
    for block in spine:
        if not isinstance(block, dict):
            continue
        position = block.get("position")
        clip_id = block.get("clip_id")
        src_start = block.get("source_start")
        src_end = block.get("source_end")
        tl_start = block.get("timeline_start")
        tl_end = block.get("timeline_end")
        summary = by_clip.get(str(clip_id)) if clip_id else None
        sound_ok = (
            summary is not None
            and measured(summary)
            and isinstance(src_start, (int, float))
            and isinstance(src_end, (int, float))
            and isinstance(tl_start, (int, float))
            and isinstance(tl_end, (int, float))
        )
        if not sound_ok:
            unmeasured.append(position)
            continue
        spans = []
        for event in events_in_block(summary, float(src_start),
                                     float(src_end)):
            spans.append({
                "label": event["label"],
                "start_seconds": round(source_to_timeline(
                    event["start_seconds"], block), 3),
                "end_seconds": round(source_to_timeline(
                    event["end_seconds"], block), 3),
                "confidence": event["confidence"],
            })
        spans.sort(key=lambda s: (s["start_seconds"], s["end_seconds"]))
        rows.append({
            "block_position": position,
            "clip_id": clip_id,
            "sound_event_method": summary.get("sound_event_method",
                                              "unmeasured"),
            "events": spans,
        })

    view: dict = {"legend": SOUNDEVENTS_LEGEND}
    if rows:
        view["blocks"] = rows
        view["blocks_measured"] = len(rows)
    if unmeasured:
        view["not_measured"] = (
            f"{len(unmeasured)} block(s) have no sound-event measurement "
            f"(no source clip, no source range, or an unmeasured "
            f"clip): "
            + ", ".join(sorted({str(p) for p in unmeasured}))
        )
    if not rows and not unmeasured:
        return {}
    return {"soundevents": view}


# name -> builder(routed_inputs) -> a dict merged into the projection.
#
# A view's NAME is the key it writes.  That is what makes a second
# projection of an already-projected tree a no-op, which it has to be:
# an `llm_only` step is projected twice on every run - once by
# `gather_step_inputs` and again by `present_llm_step` - and the second
# pass sees a tree the first one already stripped the source out of.
CONTEXT_VIEWS = {
    "transcript": _transcript,
    "spoken_lines": _spoken_lines,
    "prosody": _prosody,
    "picture": _picture,
    "stability": _stability,
    "alignment": _alignment,
    "beatgrid": _beatgrid,
    "sectiongrid": _sectiongrid,
    "emphasis": _emphasis,
    "motion": _motion,
    "soundevents": _soundevents,
}


def is_view(path: str) -> bool:
    return path.startswith(VIEW_PREFIX)


def view_name(path: str) -> str:
    return path[len(VIEW_PREFIX):]


def build_view(name: str, data: dict) -> dict:
    """Build one named view. An unknown name raises rather than defaulting.

    Projecting an already-projected tree returns the view unchanged: the
    source input is gone by then, so the builder has nothing to read, and
    a second pass would otherwise DELETE the view the first one built.
    """
    if name not in CONTEXT_VIEWS:
        raise ValueError(
            f"Unknown context view {name!r}. "
            f"Known views: {sorted(CONTEXT_VIEWS)}"
        )
    built = CONTEXT_VIEWS[name](data)
    if not built and name in data:
        return {name: data[name]}
    return built
