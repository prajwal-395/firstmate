#!/usr/bin/env python3
"""Step 5.02 post-bridge: the mix engineer's answer becomes the automation.

Reads the separation the engineer named for each behaviour, puts it
through the one ladder (`library/tools/decided_value.decide`), solves the
clip gain that delivers it from the bed's and the speech's own
measurements, and writes `audio_mix_spec`.

**Three things this deliberately does not do**, and they are 5.01's three.

It does not substitute a level for a behaviour the answer left out.  The
ladder falls to the REGISTERED fallback, which is recorded AS a fallback
naming whose mix it is, or to UNDETERMINED - which carries no gain at all
and says so.

It does not clamp.  How far above the bed a voice sits is the engineer's,
the same way `series_look` puts no bound on a declared slope.  A bed
mastered 8.6 dB hotter than the speech needs a number a bounded scale
would have refused, which is the captain's own "it could very well be
possible that we need to use values outside of these bounds".

And it does not read an empty answer as agreement.  A run where nobody
decided records `fallback` or `undetermined` per window, and those are
different facts from a decision that happened to land on the same number.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from library.steps.step_5_02_audio_mix.mix import (
    SLOT,
    apply_word_gap_ducking,
    assemble,
    plan_room_tone_fills,
    shortfall_lines,
    solve_automation,
)
from library.tools import decided_value
from library.tools import music_over_speech
from library.tools.audio_chain import AudioChainError, validate_intent
from library.tools.dialogue_cleanup import (
    CLEANUP_ENTRY_KEYS,
    plan_dialogue_eq,
    source_spectra,
    spans_by_source,
    validate_cleanup_request,
)
from library.tools.plan_keys import refuse_unknown_keys
from library.tools.ren_refusal import RenRefusal


# The decision-entry keys this step reads. Anything else is REFUSED,
# never dropped: an unread key is how a probe's SFX `at_word` landed
# 3.06 s early on the block start. (`decided_value.take` already
# strips the raw answer down to these keys before the bridge runs, so
# this gate covers the merge entries the bridge actually reads -
# `answers_from` matches on `slot`/`scope` and `decide` reads `value`
# and the required `why`.)
DECISION_ENTRY_KEYS = frozenset({
    "slot",
    "scope",
    "value",
    "why",
})

DUCKING_PLAN_KEYS = frozenset({
    "enabled", "duck_db", "release_ms", "why",
})
DELIVERY_PLAN_KEYS = frozenset({
    "dialogue_target_lufs", "true_peak_ceiling_dbtp", "why",
})


def _speech_reference(windows, scope):
    """The speech level a separation for `scope` is measured against.

    Returns `(lufs, basis, windows_measured)`.

    The loudest measured speech under that behaviour, because the window
    that has to stay intelligible is the one that decides whether the bed
    is too loud and a mean would hide it.

    `prominent` means music leads with no competing speech, so its
    windows usually carry none at all - and a scope with nothing to
    measure against would fall to the fallback on every run, which is the
    constant surviving in a new place. Its reference is therefore the
    PIECE's own speech: where the bed should sit when it leads, relative
    to the voice the rest of the video is carried by. That is still a
    measurement of this material, and the basis it was read on is
    recorded beside it rather than left for a reader to assume.
    """
    own = [w["speech_lufs"] for w in windows
           if w["music_behavior"] == scope
           and isinstance(w["speech_lufs"], (int, float))]
    if own:
        return (max(own),
                f"the loudest measured speech under a {scope!r} window",
                len(own))
    everywhere = [w["speech_lufs"] for w in windows
                  if isinstance(w["speech_lufs"], (int, float))]
    if everywhere:
        return (max(everywhere),
                (f"no {scope!r} window carries measured speech, so the "
                 f"reference is the loudest speech in the piece - where the "
                 f"bed sits when it leads, against the voice the rest of the "
                 f"video is carried by"),
                len(everywhere))
    return None, "no window in this edit carries measured speech", 0


def resolve_audio_mix(data: dict) -> dict:
    """Join the engineer's answer to the pre-bridge's measurements.

    `data` is the merged dict the runner hands a post-bridge: the step's
    inputs, the pre-bridge's output, and what the model answered.
    """
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")

    # A decision key nothing here reads is refused before anything
    # solves - the refusal travels the post-bridge retry path so the
    # model re-plans instead of the mix carrying a key nobody reads.
    refuse_unknown_keys(data.get(decided_value.MERGE_KEY) or [],
                         DECISION_ENTRY_KEYS, step="audio_mix",
                         plan="value_decisions")

    pre_output = {
        "bed_measurements": data.get("bed_measurements") or {},
        "mix_windows": data.get("mix_windows") or [],
        "mix_decision_legend": data.get("mix_decision_legend") or {},
    }
    bed = pre_output["bed_measurements"]

    # The measurements the judgement is made over, named exactly as the
    # slot names them so the record and the solver read the same keys.
    row = decided_value.slot(SLOT)
    decisions = []
    by_scope = {}
    for scope in row.scope_names():
        speech_lufs, basis, measured = _speech_reference(
            pre_output["mix_windows"], scope)
        measurements = {
            "bed_integrated_lufs": (bed.get("integrated_lufs")
                                    if bed.get("measured") else None),
            "speech_lufs": speech_lufs,
            "speech_reference_basis": basis,
            "windows_measured": measured,
        }
        decision = decided_value.decide(
            SLOT, scope=scope,
            project_folder=data.get("project_folder", ""),
            creative_direction=data.get("creative_direction"),
            model_answer=decided_value.answers_from(data, SLOT, scope),
            measurements=measurements,
            step="audio_mix")
        decisions.append(decision.as_record())
        by_scope[scope] = decision.as_record()

    automation, undetermined = solve_automation(pre_output, by_scope)
    ducking = resolve_music_ducking_plan(data)
    delivery = resolve_audio_delivery_plan(data)
    try:
        apply_word_gap_ducking(
            automation,
            data["audio_spine"] if ducking["enabled"] else {}, ducking)
    except ValueError as exc:
        raise RenRefusal(
            what="the word-gap music plan cannot be delivered",
            why=str(exc),
            fix=("re-plan the word-gap level so it stays within Resolve's "
                 "measurable clip-volume range"),
        ) from exc

    for line in decided_value.summary_lines(decisions):
        print(f"  {line}", file=sys.stderr)
    for window in undetermined:
        print(f"  Window {window['spine_block_position']} carries no bed "
              f"level: {window['why']}", file=sys.stderr)
    # Finding 25: a window that misses its decided separation is
    # reported here, on the run that planned it - met windows stay
    # silent.
    for line in shortfall_lines(automation):
        print(f"  {line}", file=sys.stderr)

    cleanup = resolve_cleanup(data)

    result = assemble(pre_output, decisions, automation, undetermined,
                      cleanup=cleanup)
    result["audio_mix_spec"]["music_ducking_plan"] = {
        **ducking,
        "blocks_with_word_intervals": sum(
            bool(row.get("word_intervals")) for row in automation),
        "blocks_with_delivered_levels": sum(
            bool(row.get("word_intervals") and
                 row.get("word_gap_level_db") is not None)
            for row in automation),
    }
    # The delivery spec the project's named platform selects. The LLM's
    # explicit audio_delivery_plan wins where it states one (a stated
    # preference); the platform spec is the fallback where it does not,
    # so a project that names a platform is mastered to THAT platform's
    # loudness target and true-peak ceiling rather than the default's -
    # and the QC gate reads the same spec, so a render for the wrong
    # spec is caught. See library/tools/delivery_spec.py.
    from library.tools.delivery_spec import resolve_delivery_spec_for_project
    delivery_spec = resolve_delivery_spec_for_project(
        data.get("project_folder", ""))
    result["audio_mix_spec"]["audio_delivery_plan"] = delivery
    result["audio_mix_spec"]["delivery_lufs_target"] = (
        delivery["dialogue_target_lufs"]
        if delivery["dialogue_target_lufs"] is not None
        else delivery_spec.loudness_target_lufs)
    result["audio_mix_spec"]["delivery_true_peak_ceiling_dbtp"] = (
        delivery["true_peak_ceiling_dbtp"]
        if delivery["true_peak_ceiling_dbtp"] is not None
        else delivery_spec.true_peak_ceiling_dbtp)
    result["audio_mix_spec"]["master_limiter"]["threshold_db"] = (
        result["audio_mix_spec"]["delivery_true_peak_ceiling_dbtp"])
    # The captain's named spans where the bed is deliberately over the
    # voice, parsed from the same timeline notes / edit requests every
    # other override uses (library/tools/music_over_speech.py).  They
    # travel inside `audio_mix_spec` - the audio step's own output - and
    # compile_manifest carries them onto the assembly manifest for the
    # speech-above-bed gate (render_qa.measure_speech_above_bed).
    result["audio_mix_spec"]["music_over_speech_overrides"] = \
        music_over_speech.overrides_from_timeline_notes(
            data.get("timeline_notes"))
    # Room-tone continuity: the dropouts the measure pass found, planned
    # as fills. compile_manifest stages and places them on the speech
    # row - the same route a J/L cut's fill travels. A source with
    # dropouts but no measured floor plans nothing (see
    # `plan_room_tone_fills`).
    result["audio_mix_spec"]["room_tone_continuity"] = {
        "sources": plan_room_tone_fills(data.get("cleanup_context") or {}),
    }
    result["audio_mix_spec"]["fairlight_chain"] = resolve_fairlight_chain(
        data,
        dialogue_lufs_target=result["audio_mix_spec"][
            "delivery_lufs_target"],
        true_peak_ceiling=result["audio_mix_spec"][
            "delivery_true_peak_ceiling_dbtp"])
    return result


def resolve_fairlight_chain(data: dict, dialogue_lufs_target: float,
                             true_peak_ceiling: float) -> dict:
    """The dialogue-bus EQ this run's own spectra ask for, as intent.

    Measures each played dialogue source's speech and floor spectra
    (`source_spectra` - one decode per source, the chain planner's own
    read; the cleanup_context record carries levels, not spectra, and
    spectra stay out of the prompt tables), derives the bus EQ
    (`plan_dialogue_eq` - duration-weighted across the sources that
    answered), and validates the whole intent through
    `audio_chain.validate_intent` - the validator's first production
    caller. The intent's targets are the delivery numbers this step
    already resolved (the program loudness target and the true-peak
    ceiling the QC gate reads), never constants restated here.

    The intent carries the dialogue EQ track only. The limiter
    PROCESSING entry and the dynamics wait for the executor slice: an
    entry nothing can apply yet is not shipped - only the ceiling
    travels, as the target the verifier will judge the render against.
    A source that cannot be measured (no floor, no played speech, an
    unreadable file) plans nothing and is named in `unplanned`; one
    bad source never sinks the bus chain. The plan applies nothing -
    compile_manifest carries `audio_mix_spec` (this key with it) onto
    the assembly manifest, where the Fairlight UI executor will read
    it.
    """
    sources = (data.get("cleanup_context") or {}).get("sources", []) or []
    structure = (data.get("audio_spine") or {}).get("structure", []) or []
    played, word_spans = spans_by_source(
        structure, data.get("a_roll_assignments") or [])

    measured: list = []
    unplanned: list = []
    for record in sources:
        if not isinstance(record, dict):
            continue
        source_file = record.get("source_file", "?")
        floor = record.get("floor") or {}
        segment = None
        if floor.get("level_dbfs") is not None:
            try:
                segment = (float(floor["segment_start"]),
                           float(floor["segment_end"]))
            except (KeyError, TypeError, ValueError):
                segment = None
        if segment is None:
            unplanned.append({
                "source_file": source_file,
                "reason": (record.get("floor_unmeasured_reason")
                           or "no measured floor segment on the record"),
            })
            continue
        spans = word_spans.get(source_file, [])
        played_ranges = record.get("played_ranges") or []
        try:
            spectra = source_spectra(source_file, played_ranges, spans,
                                     segment)
        except Exception as exc:
            unplanned.append({"source_file": source_file,
                              "reason": str(exc)[:300]})
            continue
        if spectra["absences"]:
            unplanned.append({"source_file": source_file,
                              "reason": "; ".join(spectra["absences"])})
            continue
        spectra["weight_seconds"] = spectra["speech_seconds"]
        measured.append(spectra)

    plan = plan_dialogue_eq(
        measured, measured[0]["spectrum_edges_hz"] if measured else [])
    tracks: list = []
    if plan["bands"]:
        tracks.append({
            "track": "dialogue",
            "role": "dialogue",
            "eq": plan["bands"],
        })
    intent = {
        "tracks": tracks,
        "targets": {
            "integrated_lufs": float(dialogue_lufs_target),
            "true_peak_ceiling_dbTP": float(true_peak_ceiling),
        },
    }
    try:
        validate_intent(intent)
    except AudioChainError as exc:
        print(f"  Fairlight chain refused by its own schema ({exc}) - "
              f"the mix ships without one", file=sys.stderr)
        return {"intent": {"tracks": [], "targets": intent["targets"]},
                "basis": dict(plan.get("basis", {})),
                "dropped": list(plan.get("dropped", [])),
                "unplanned": unplanned + [
                    {"source_file": "(intent)",
                     "reason": f"schema refusal: {exc}"}]}

    if tracks:
        print(f"  Fairlight chain: dialogue EQ "
              f"{', '.join(b['type'] + ' ' + str(b['frequency_hz']) + ' Hz'
                           for b in plan['bands'])} "
              f"(targets {dialogue_lufs_target:.1f} LUFS, "
              f"{true_peak_ceiling:.1f} dBTP)", file=sys.stderr)
    else:
        print(f"  Fairlight chain: no dialogue EQ earned - "
              f"{plan.get('why_empty', 'see basis')} "
              f"({len(unplanned)} source(s) unplanned)",
              file=sys.stderr)
    return {"intent": intent,
            "basis": dict(plan.get("basis", {})),
            "dropped": list(plan.get("dropped", [])),
            "unplanned": unplanned,
            "executor": ("unassigned - a later slice wires the Fairlight "
                         "UI executor; the build applies nothing from "
                         "this plan yet")}


def resolve_music_ducking_plan(data: dict) -> dict:
    """Validate the prompt's per-word music duck and gap recovery values."""
    plan = data.get("music_ducking_plan")
    if plan is None:
        return {"enabled": False, "duck_db": None, "release_ms": None,
                "why": "No word-gap ducking plan was declared."}
    if not isinstance(plan, dict):
        raise RenRefusal(
            what="the word-gap music plan is not an object",
            why="the renderer cannot read ducking fields from this answer.",
            fix=("return enabled, duck_db, release_ms, and why in one "
                 "music_ducking_plan object"),
        )
    refuse_unknown_keys([plan], DUCKING_PLAN_KEYS, step="audio_mix",
                        plan="music_ducking_plan")
    if set(plan) != DUCKING_PLAN_KEYS:
        missing = sorted(DUCKING_PLAN_KEYS - set(plan))
        raise RenRefusal(
            what="the word-gap music plan is incomplete",
            why=f"the answer omitted {', '.join(missing)}.",
            fix="return all four music_ducking_plan keys",
        )
    if not isinstance(plan["enabled"], bool):
        raise RenRefusal(
            what="the word-gap enabled value is not boolean",
            why="a string or number does not declare whether ducking applies.",
            fix="set enabled to true or false",
        )
    why = plan["why"]
    if not isinstance(why, str) or not why.strip():
        raise RenRefusal(
            what="the word-gap plan has no reason",
            why="the chosen depth and recovery timing have no stated basis.",
            fix="state the request or creative reading behind the values",
        )
    if not plan["enabled"]:
        if plan["duck_db"] is not None or plan["release_ms"] is not None:
            raise RenRefusal(
                what="the disabled word-gap plan carries active values",
                why="duck_db and release_ms would be unread settings.",
                fix="set both numeric keys to null when enabled is false",
            )
        return {**plan, "why": why.strip()}

    duck_db = _finite_plan_number(plan["duck_db"], "duck_db")
    release_ms = _finite_plan_number(plan["release_ms"], "release_ms")
    if duck_db < 0 or release_ms <= 0:
        raise RenRefusal(
            what="the word-gap music values cannot be delivered",
            why="duck_db must be non-negative and release_ms positive.",
            fix="re-plan the duck depth and recovery duration with those units",
        )
    return {**plan, "duck_db": duck_db, "release_ms": release_ms,
            "why": why.strip()}


def resolve_audio_delivery_plan(data: dict) -> dict:
    """Validate explicit dialogue loudness and true-peak export targets."""
    plan = data.get("audio_delivery_plan")
    if plan is None:
        return {
            "dialogue_target_lufs": None,
            "true_peak_ceiling_dbtp": None,
            "why": "No numeric delivery target was requested; keep the "
                   "declared -14 LUFS and -1 dBTP delivery standard.",
        }
    if not isinstance(plan, dict):
        raise RenRefusal(
            what="the audio delivery plan is not an object",
            why="the export step cannot read targets from this answer.",
            fix=("return dialogue_target_lufs, "
                 "true_peak_ceiling_dbtp, and why in one object"),
        )
    refuse_unknown_keys([plan], DELIVERY_PLAN_KEYS, step="audio_mix",
                        plan="audio_delivery_plan")
    if set(plan) != DELIVERY_PLAN_KEYS:
        missing = sorted(DELIVERY_PLAN_KEYS - set(plan))
        raise RenRefusal(
            what="the audio delivery plan is incomplete",
            why=f"the answer omitted {', '.join(missing)}.",
            fix="return all three audio_delivery_plan keys",
        )
    why = plan["why"]
    if not isinstance(why, str) or not why.strip():
        raise RenRefusal(
            what="the audio delivery plan has no reason",
            why="the declared targets have no stated request or basis.",
            fix="state why the exact numeric targets or delivery defaults apply",
        )
    resolved = {"why": why.strip()}
    for key in ("dialogue_target_lufs", "true_peak_ceiling_dbtp"):
        value = plan[key]
        resolved[key] = (None if value is None else
                         _finite_plan_number(value, key))
    return resolved


def _finite_plan_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenRefusal(
            what=f"{label} is not a number",
            why="the plan must supply a JSON number, not text or a boolean.",
            fix=f"re-plan {label} as a finite numeric value",
        )
    result = float(value)
    if not math.isfinite(result):
        raise RenRefusal(
            what=f"{label} is not finite",
            why=f"the answer supplied {value!r}.",
            fix=f"re-plan {label} as a finite numeric value",
        )
    return result


def resolve_cleanup(data: dict) -> dict:
    """The dialogue cleanup the plan asked for, validated entry by entry.

    Reads `cleanup_plan` (falling back to the raw response the way
    4.04 reads `sfx_creative`). A non-list answer is a warning and an
    empty plan - cleanup is opt-in, so nothing requested means nothing
    cleaned. Every entry is then validated strictly: an unknown key, an
    unknown tool, a missing or out-of-range amount, or a missing `why`
    refuses through the post-bridge retry path so the model re-plans
    instead of the mix carrying a request nothing applies.
    """
    plan = data.get("cleanup_plan")
    if not plan and "llm_raw_response" in data:
        try:
            parsed = json.loads(data["llm_raw_response"])
            plan = parsed if isinstance(parsed, list) else parsed.get(
                "cleanup_plan", [])
        except Exception:
            plan = data["llm_raw_response"]
    if plan is None:
        plan = []
    if not isinstance(plan, list):
        print(f"  Warning: LLM returned invalid response for cleanup_plan. "
              f"Defaulting to empty (no cleanup). "
              f"Response was: {str(plan)[:100]}", file=sys.stderr)
        plan = []
    plan = [v for v in plan if isinstance(v, dict)]
    refuse_unknown_keys(plan, CLEANUP_ENTRY_KEYS, step="audio_mix",
                        plan="cleanup_plan")
    requests = []
    for entry in plan:
        row = validate_cleanup_request(entry)
        requests.append(row)
        detail = (f"{row['tool']} {row['amount']}"
                  if row["tool"] == "voice_isolation" else row["tool"])
        span = (f" span {row['span_start']}-{row['span_end']}s"
                if row["span_start"] is not None else "")
        print(f"  Cleanup {row['source']}: {detail}{span} - {row['why']}",
              file=sys.stderr)
    if not requests:
        print("  Cleanup: no source requested it - nothing will be "
              "cleaned.", file=sys.stderr)
    context = data.get("cleanup_context") or {}
    return {
        "requests": requests,
        "tools": context.get("tools", {}),
        "sources": context.get("sources", []),
    }


def main():
    data = json.loads(sys.stdin.read())
    json.dump(resolve_audio_mix(data), sys.stdout, indent=2)


if __name__ == "__main__":
    main()
