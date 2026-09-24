#!/usr/bin/env python3
"""Step 3.4 pre-bridge: the tables the handoff tells the model to read.

Two tables, because they describe different things. `turns` is the
STRUCTURE of the conversation - who spoke and between which two seconds -
and is what a reader needs to judge whether a stretch is a two-hander.
`reel_candidates` is one row per contiguous stretch the measurements
found, and is what tells them the SHAPE of it.

A third table is beside them and is not built here: `spoken_lines`, the
conversation itself, one row per line of speech with the seconds it may
be cut at. It is a declared VIEW of `timeline_transcript` rather than a
pre-bridge table (`library/tools/context_views._spoken_lines`), because
what it exists to leave behind - 8,509 per-word timings, 940 absolute
source paths, 940 Resolve item ids - is left behind by the PROJECTION,
which is the mechanism a step declares what may reach its prompt with.
`turns` no longer carries the words, because that view does.

Nothing here is ranked, scored or filtered, and that is the point of the
file rather than a nicety about it. Reel selection existed for two
rejected batches as a crewmate's own judgement wrapped in a validator -
see section 15 of docs/FIELD_TEST_PODCAST_FINDINGS.md - and the specific
mechanism was a `pitch_share >= 0.15` exclusion that removed ten
candidates for containing a call to action. The captain's format ENDS on
one. So pitch is published as a measurement beside `closes_on_pitch`, and
every stretch found reaches the table including the ones the
measurements are unenthusiastic about.

`within_length_guidance` is published rather than enforced for the same
reason: the 45-90s brief is guidance the model weighs, and a hard ceiling
silently withheld every story that needed longer to finish.
"""

from __future__ import annotations

from typing import Dict, List

LEAD = "lead_speaker"
ANSWERER = "answering_speaker"


def _declared_roster(data: dict):
    """The project's declared speaker roster, or None.

    `source.speakers` in project.yaml via `footage_identity` - None
    means undeclared (the historical two-speaker reading), `[]` a
    declared-zero project, else `[{name, role?}]`. Never fails the
    step: an unreadable declaration reads as undeclared, the same
    way video preferences do below.
    """
    project_folder = (data or {}).get("project_folder") or ""
    if not project_folder:
        return None
    try:
        from library.tools.footage_identity import declared_speakers
        return declared_speakers(project_folder)
    except Exception:  # noqa: BLE001 - roster never fails a build
        return None


def _roster_notes(declared, counts: Dict[str, int]) -> list:
    """What the transcript says about the declared roster, as notes.

    A transcript voice the roster does not name, and a declared name
    that never speaks, are both REPORTED - the model weighs them, and
    neither changes the count the thresholds read.
    """
    if not declared:
        return []
    notes = []
    names = [entry["name"] for entry in declared]
    for speaker in sorted(counts):
        if speaker not in names:
            notes.append(
                f"{speaker} speaks in this cut but the project's "
                f"declared roster names only {names} - weigh their "
                f"lines as heard, not as a second voice the project "
                f"asked for.")
    silent = [name for name in names if name not in counts]
    if silent:
        notes.append(
            f"declared speaker(s) {silent} never speak in this cut - "
            f"no candidate can carry them.")
    return notes


def _speakers(transcript: dict, declared=None) -> tuple:
    from library.tools.reel_exchange import turns_from_transcript

    turns = turns_from_transcript(transcript)
    asks: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    seconds: Dict[str, float] = {}
    for turn in turns:
        if not turn.speaker:
            continue
        counts[turn.speaker] = counts.get(turn.speaker, 0) + 1
        seconds[turn.speaker] = seconds.get(turn.speaker, 0.0) + turn.duration
        if turn.asks:
            asks[turn.speaker] = asks.get(turn.speaker, 0) + 1

    if declared is not None and len(declared) == 0:
        # A declared-zero project: music, montage. There is no lead
        # to find and no second voice to miss - the caller offers no
        # candidates and says so.
        return None, None, turns, (
            "the project declares no speakers, so no exchange can be "
            "identified and none is looked for")

    if declared is not None and len(declared) == 1:
        # The monologue path: one declared voice, no answerer. The
        # lead is the declared name where it speaks, else whoever the
        # transcript holds - said, not smoothed over.
        name = declared[0]["name"]
        if not counts:
            return None, None, turns, (
                f"the project declares one speaker ({name}) but this "
                f"cut holds no speech at all - nothing to cut a "
                f"monologue from")
        if name in counts:
            lead = name
            why = (f"{name} is the project's declared speaker"
                   + (f" ({declared[0]['role']})"
                      if declared[0].get("role") else "")
                   + " - no exchange structure to infer.")
        elif len(counts) == 1:
            lead = next(iter(counts))
            why = (f"the project declares {name} but this cut holds "
                   f"{lead} - weighed as the monologue voice, and the "
                   f"mismatch is reported.")
        else:
            lead = max(counts, key=lambda s: seconds[s])
            why = (f"the project declares one speaker ({name}) but "
                   f"this cut holds {sorted(counts)} - {lead} speaks "
                   f"longest and is weighed as the monologue voice, "
                   f"and the mismatch is reported.")
        return lead, None, turns, why

    if len(counts) < 2:
        return None, None, turns, ""

    def ask_rate(speaker: str) -> float:
        return asks.get(speaker, 0) / counts[speaker]

    def mean_turn(speaker: str) -> float:
        return seconds[speaker] / counts[speaker]

    lead = max(counts, key=lambda s: (ask_rate(s), -mean_turn(s)))
    answerer = max((s for s in counts if s != lead),
                   key=lambda s: seconds[s])
    why = (f"{lead} asks on {ask_rate(lead):.0%} of their turns against "
           f"{ask_rate(answerer):.0%} for {answerer}, and speaks for "
           f"{mean_turn(lead):.0f}s a turn against {mean_turn(answerer):.0f}s. "
           f"Inferred, not declared - correct it if it is wrong.")
    if declared:
        roster = ", ".join(
            entry["name"]
            + (f" ({entry['role']})" if entry.get("role") else "")
            for entry in declared)
        why += f" Declared roster: {roster}."
    return lead, answerer, turns, why


def repetition_inside(start: float, end: float, transcript: dict) -> list[dict]:
    """The repeated runs INSIDE one candidate window, and whether a build
    will remove them.

    `retake_of` already tells this step when a whole stretch is another
    stretch recorded again.  It says nothing about a stretch that
    contains a repetition of its OWN, and that is the case that decides
    where a reel starts.

    Measured on reel 03 of the field test, 2026-09-06.  Akshita says one
    sentence twice inside 301.24-341.27, and the build cannot separate
    the takes: `redundant_runs` holds a run WHOLE when any member of it
    is refused by the duration guard, so the repetition stays in the
    reel and `refused_take_groups` reports it.  That report is produced
    by the BUILD, after the span is fixed, and reaches nothing that can
    move a boundary.  The step that CAN move one is this one, and its
    handoff already promised the facts - *"Both whole exchanges and
    single lines are repeated, and both are reported with the timecodes
    of each take"* - while the candidate table carried only the first
    half of that.  A handoff that names a table the context does not
    deliver is the contract defect AGENTS.md 10.1 is about.

    So the same measurement is offered where the boundary is still open.
    It is a MEASUREMENT and not a verdict: whether a repetition is worth
    redrawing a span for, and which take to keep, stays the model's call
    (AGENTS.md 10.5).  Nothing here filters a candidate out for having
    one.

    Each run's cuts arrive with what the model needs to judge them -
    `take_cut_context`: the dropped and kept tellings' own sentences,
    where each sits in its sentence (a tail-drop orphans the head it
    was cut from; a false start opens a new one), whether the repeat
    crosses a speaker turn, and what the second telling adds. A
    cross-turn paraphrase the cut lane refuses outright is not in any
    run at all, so candidates carry those separately (see below).
    """
    from library.tools.reel_build import (
        judge_take_cuts, redundant_runs, take_cut_context)

    out: list[dict] = []
    for run in redundant_runs(float(start), float(end), transcript):
        cuts = []
        _, withdrawn = judge_take_cuts(list(run.cuts), float(start),
                                       float(end), transcript)
        refused = {(round(w["cut"].dropped_start, 4),
                    round(w["cut"].kept_start, 4)): w
                   for w in withdrawn}
        for cut in run.cuts:
            context = take_cut_context(cut, transcript)
            key = (round(float(cut.dropped_start), 4),
                   round(float(cut.kept_start), 4))
            if key in refused:
                # The build will NOT remove this telling, and the
                # reason says why - a mid-word edge, bleed-free second
                # voice, an excision from a live sentence. The boundary
                # is the only thing that changes that, and this step
                # is the only place it can be moved: redraw the span
                # past the dropped telling, or keep both on purpose.
                context["judge"] = {
                    "build_removes": False,
                    "reason": refused[key]["reason"],
                    "why": refused[key]["why"],
                    "recommended_action": (
                        f"redraw the span past "
                        f"{float(cut.dropped_start):.2f}-"
                        f"{float(cut.dropped_end):.2f}s, or keep both "
                        f"tellings on purpose"),
                }
            else:
                context["judge"] = {"build_removes": True}
            cuts.append(context)
        out.append({
            "start": round(run.start, 2),
            "end": round(run.end, 2),
            "speaker": run.speaker,
            "lines": [(segment.get("text") or "").strip()
                      for segment in run.segments],
            "build_removes_it": run.whole,
            "cuts": cuts,
            "why": (
                "every line of this run pairs with a later one, so the "
                "build removes the run whole and the reel does not play it"
                if run.whole else
                f"{len(run.cuts)} of {len(run.segments)} lines pair safely "
                f"and {len(run.blocked)} do not, so the build leaves the "
                f"run ENTIRE - a take is removed whole or not at all. "
                f"These seconds WILL play twice unless the span is drawn "
                f"clear of one of the takes."),
        })
    return out


def retake_candidates_inside(start: float, end: float,
                               transcript: dict) -> list:
    """Abandoned tellings the cut lane misses, for the model to verdict.

    `repetition_inside` reports what the pair scan paired - found or
    withdrawn with its reason. This reports what it never paired:
    false starts and paraphrases (`retake_scan`), each with both
    tellings' measured properties (`take_cut_context`: durations,
    completeness, disfluency, what the second telling adds) and a
    concrete recommended strike. A candidate overlapping a run cut's
    dropped span is the run's business and is left out - one
    repetition, one report, never two verdicts on it. Empty (the
    common case) is offered as nothing: a call with nothing to ask is
    not made, and a candidate with no retake carries no new key.

    The verdict travels as `takes_dropped` on the chosen moment, and
    the post-bridge records it as a keep exclusion - so a take the
    model strikes stays out of every regeneration, not just this one.
    """
    from library.tools import retake_scan
    from library.tools.reel_build import (
        Cut, redundant_runs, take_cut_context)

    run_drops = [(float(cut.dropped_start), float(cut.dropped_end))
                 for run in redundant_runs(float(start), float(end),
                                           transcript)
                 for cut in run.cuts]
    out = []
    for candidate in retake_scan.scan_span(
            float(start), float(end), transcript)["reported"]:
        dropped = (float(candidate["dropped_start"]),
                   float(candidate["dropped_end"]))
        if any(not (drop_end <= dropped[0] or drop_start >= dropped[1])
               for drop_start, drop_end in run_drops):
            continue
        similarity = float(candidate.get("similarity", 0.0) or 0.0)
        cut = Cut(
            dropped_start=dropped[0], dropped_end=dropped[1],
            dropped_text=candidate.get("dropped_text", ""),
            kept_start=float(candidate["kept_start"]),
            kept_end=float(candidate["kept_end"]),
            kept_text=candidate.get("kept_text", ""),
            speaker=candidate.get("speaker"),
            containment=similarity, jaccard=similarity,
            basis=candidate.get("basis", ""))
        context = take_cut_context(cut, transcript)
        context["kind"] = candidate.get("kind", "")
        context["shape"] = candidate.get("shape", "")
        context["basis"] = candidate.get("basis", "")
        context["recommended_action"] = candidate.get(
            "recommended_action", "")
        context["judge"] = {
            "build_removes": False,
            "reason": "below_the_cut_lane",
            "why": ("the pair scan never paired these tellings, so no "
                    "cut removes them - only a verdict does"),
            "recommended_action": candidate.get("recommended_action",
                                                ""),
        }
        out.append(context)
    return out


def retellings_inside(start: float, end: float, transcript: dict) -> list:
    """The cross-turn paraphrases inside one candidate window.

    The cut lane refuses these by design - reworded past its bars - so
    no run contains them and `repetition_inside` never names them. The
    model that can still redraw past one telling gets them here, with
    both sentences, the turn crossed, and what the second telling adds.
    Empty (the common case) is offered as nothing: a call with nothing
    to ask is not made, and a candidate with no retelling carries no
    new key.
    """
    from library.tools.reel_build import possible_retellings

    return possible_retellings(float(start), float(end), transcript)


def _publish_video_preferences(data: dict) -> dict:
    """The project's own soft length target and content rules, if declared.

    `target_length_seconds` is SOFT - guidance the model weighs, never a
    gate: a longer or shorter reel with defensible quality is allowed, so
    this publishes the seconds and refuses nothing on them. It sits
    BESIDE `length_guidance_seconds` (the 45-90 s series guidance) rather
    than replacing it, so a video that declares nothing reads exactly
    what it always read. `content_rules` (`speakers_must_interact`,
    `require_value_add`, `require_cta`) is the project's own statement
    of what a reel must do; absence keeps today's hardcoded format
    behaviour. Both come from the merged video preferences
    (``style.yaml`` locked value, then ``video.yaml``); a supplied
    `video_preferences` input wins over a disk read.
    """
    published: dict = {}
    project_folder = (data or {}).get("project_folder") or ""
    supplied = (data or {}).get("video_preferences")
    if not project_folder and supplied is None:
        return published
    try:
        from library.tools.video_prefs import (
            effective_content_rules, effective_target_length)
        soft = effective_target_length(
            project_folder, video_preferences=supplied)
        if soft is not None:
            published["target_length_seconds"] = soft
            published["target_length_note"] = (
                "SOFT target from the project's video preferences - "
                "guidance to weigh, never a gate or a refusal.")
        rules = effective_content_rules(
            project_folder, video_preferences=supplied)
        if rules is not None:
            published["content_rules"] = rules
    except Exception as exc:  # noqa: BLE001 - prefs never fail a build
        import sys
        print(f"  WARNING: could not read video preferences: {exc}",
              file=sys.stderr)
    return published


def build_context(data: dict) -> dict:
    from library.tools.reel_exchange import (
        LENGTH_GUIDANCE, collapse_overlapping, collapse_retakes,
        exchange_windows, monologue_windows)

    transcript = data.get("timeline_transcript") or {}
    declared = _declared_roster(data)
    lead, answerer, turns, why = _speakers(transcript, declared)
    counts: Dict[str, int] = {}
    for turn in turns:
        if turn.speaker:
            counts[turn.speaker] = counts.get(turn.speaker, 0) + 1
    notes = _roster_notes(declared, counts)
    turn_rows = [{
        "speaker": t.speaker,
        "start": round(t.start, 2),
        "end": round(t.end, 2),
    } for t in turns]

    if declared is not None and len(declared) == 0:
        # A declared-zero project: music, montage. No candidates are
        # offered and none are looked for - an empty table with the
        # reason, not a refusal.
        out = _publish_video_preferences(data)
        undetermined = [why]
        undetermined.extend(notes)
        out.update({
            "turns": turn_rows,
            "reel_candidates": [],
            "declared_speakers": [],
            "undetermined": undetermined,
        })
        return out

    if not lead:
        out = _publish_video_preferences(data)
        undetermined = ([why] if why else [
            "the transcript names fewer than two speakers, so no "
            "exchange can be identified - a reel is a conversation "
            "and this step cannot invent a second voice"
        ])
        undetermined.extend(notes)
        out.update({
            "turns": [],
            "reel_candidates": [],
            "undetermined": undetermined,
        })
        if declared is not None:
            out["declared_speakers"] = declared
        return out

    if answerer is None:
        # The monologue path: one declared voice. Candidates are runs
        # of that voice grown to length, measured with the same table
        # minus the exchange structure. No ANSWERER key: there is no
        # second voice, and a null one would read as a missing one.
        windows = monologue_windows(turns, lead)
        stretches = collapse_overlapping(windows)
        grouped = collapse_retakes([group[0] for group in stretches])

        candidates: List[dict] = []
        for group in grouped:
            for exchange in group:
                candidates.append(exchange.measurements(lead=lead))
        candidates.sort(key=lambda c: c["start"])
        for candidate in candidates:
            inside = repetition_inside(candidate["start"],
                                       candidate["end"], transcript)
            if inside:
                candidate["repetition_inside"] = inside
            retakes = retake_candidates_inside(
                candidate["start"], candidate["end"], transcript)
            if retakes:
                candidate["retake_candidates"] = retakes
            retold = retellings_inside(candidate["start"],
                                       candidate["end"], transcript)
            if retold:
                candidate["possible_retellings"] = retold

        out = _publish_video_preferences(data)
        out.update({
            "turns": turn_rows,
            "reel_candidates": candidates,
            "length_guidance_seconds": list(LENGTH_GUIDANCE),
            "picture_holes": (transcript.get("derived_from") or {}).get(
                "picture_holes") or [],
            LEAD: lead,
            "who_leads_was_inferred": why,
            "declared_speakers": declared,
        })
        if notes:
            out["undetermined"] = notes
        from library.tools.display_respell import apply_post_pass
        apply_post_pass(out, (data or {}).get("project_folder") or "",
                        "select_reels bridge (reel_candidates)")
        return out

    windows = exchange_windows(turns, lead, answerer)
    stretches = collapse_overlapping(windows)
    grouped = collapse_retakes([group[0] for group in stretches])

    candidates: List[dict] = []
    for group in grouped:
        for exchange in group:
            candidates.append(exchange.measurements(lead=lead))
    candidates.sort(key=lambda c: c["start"])
    for candidate in candidates:
        inside = repetition_inside(candidate["start"], candidate["end"],
                                     transcript)
        if inside:
            candidate["repetition_inside"] = inside
        retakes = retake_candidates_inside(candidate["start"],
                                           candidate["end"], transcript)
        if retakes:
            candidate["retake_candidates"] = retakes
        retold = retellings_inside(candidate["start"], candidate["end"],
                                    transcript)
        if retold:
            candidate["possible_retellings"] = retold

    out = _publish_video_preferences(data)
    out.update({
        # The turn STRUCTURE, and no longer the words.  A turn's text is
        # exactly its segments' texts joined by a space - on the field
        # test, 47,975 characters against the segments' 47,182 plus the
        # 793 joining spaces, to the byte - so publishing it beside
        # `view:spoken_lines` would be the summary and the structure it
        # was rendered from, which AGENTS.md 10.1 forbids.  What is left
        # is the other axis: how the conversation divides into turns,
        # which is what `reel_candidates`' own `turns` and `alternations`
        # columns count.  See library/tools/context_views._spoken_lines.
        "turns": [{
            "speaker": t.speaker,
            "start": round(t.start, 2),
            "end": round(t.end, 2),
        } for t in turns],
        "reel_candidates": candidates,
        "length_guidance_seconds": list(LENGTH_GUIDANCE),
        "picture_holes": (transcript.get("derived_from") or {}).get(
            "picture_holes") or [],
        LEAD: lead,
        ANSWERER: answerer,
        "who_leads_was_inferred": why,
    })
    if declared is not None:
        out["declared_speakers"] = declared
    if notes:
        out["undetermined"] = notes
    # Recorded spelling corrections, enforced on the regenerated
    # measurements (the 3.04 keep-exclusion precedent): a candidate
    # quoting speech the correction respelt carries the corrected
    # spelling, deterministically, even when the transcript copy this
    # run read predates it. Identity keys (slugs, paths) never move.
    from library.tools.display_respell import apply_post_pass
    apply_post_pass(out, (data or {}).get("project_folder") or "",
                    "select_reels bridge (reel_candidates)")
    return out


def main():
    import sys
    import json
    import traceback
    try:
        data = json.loads(sys.stdin.read())
        print(json.dumps(build_context(data)))
    except Exception as e:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
