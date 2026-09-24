#!/usr/bin/env python3
"""Step 3.4 post-bridge: the model's chosen reels, CHECKED against the cut.

What is checked here is that every claim the answer makes is about the
material: the span is inside the timeline, it is not empty, real speech
was measured inside it, and it does not cut a transcript segment in half
so the short would open or close mid-sentence.

What is NOT checked is whether the choice is any good. Which stretches
are worth a short is the model's judgement and this file has no opinion
about it - no count is required, no length is enforced, and a moment
carrying a call to action is not treated as a defect.

A moment may also name a `cta` - the spoken closer it ends on, taken from
ANYWHERE in the episode, which the reel plays after its body. It gets the
same treatment the body span gets and no more: snapped out to whole
segments, then checked to be real seconds somebody really speaks in.
WHICH passage is a good closer, and which reel it suits, is the model's
call; nothing here scores one, ranks them, or prefers the nearest.
Several moments naming the SAME cta is expected rather than a defect -
this episode says about six calls to action and the format asks every
reel to close on one.

Every moment comes out PROPOSED. The captain approves before anything is
built, and `reel_proposal.assert_approved` is the gate that holds it.
"""

from __future__ import annotations

import sys
from typing import List

from library.tools.plan_keys import refuse_unknown_keys


# The moment-entry keys this step reads. Anything else on a moment is
# REFUSED by `refuse_unknown_keys` in `resolve`, never dropped: an
# unread key is how a probe's SFX `at_word` landed 3.06 s early on the
# block start. `title` is the legacy spelling of `slug`;
# `call_to_action` the legacy spelling of `cta`. (`value`, `hook` and
# `close` used to be advertised beside them and nothing read any of
# the three - the handoff no longer asks for them; what they carried
# is inside `reason`, which must argue the whole reel.)
MOMENT_ENTRY_KEYS = frozenset({
    "start",
    "end",
    "slug",
    "title",
    "reason",
    "cta",
    "call_to_action",
    "takes_dropped",
})


def _call_to_action(entry: dict, body_start: float, body_end: float,
                    transcript: dict):
    """The closer this moment ends on, or (None, None) if it names none.

    Returns `(cta, drop_reason)`. A moment that names no `cta` gets None
    and no reason - a reel with no declared closer ends where its body
    ends, and nothing here supplies one. Never author, template, pad or
    synthesise a call to action: every second of a closer is speech the
    episode really contains, and `validate_proposal` refuses a range
    where the transcript measured nothing said.

    A `cta` sitting INSIDE this moment's own body is a bad PICK rather
    than an invented timecode - the model told the truth about the
    seconds, it just named a closer the reel already plays - so the
    moment is dropped with a reason instead of raising and taking the
    whole batch with it.
    """
    from library.tools.reel_proposal import CallToAction, snap_to_speech

    raw = entry.get("cta") or entry.get("call_to_action")
    if not raw:
        return None, None
    try:
        cta_start = float(raw["start"])
        cta_end = float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None, ("names a cta with no usable start/end. A closer is "
                      "a real span of the episode or it is nothing.")

    cta_start, cta_end = snap_to_speech(cta_start, cta_end, transcript)
    if min(cta_end, body_end) > max(cta_start, body_start):
        return None, (
            f"its cta ({cta_start:.1f}-{cta_end:.1f}s) sits inside its own "
            f"body ({body_start:.1f}-{body_end:.1f}s), so the reel would "
            f"play those seconds twice. Name a closer from elsewhere in "
            f"the episode, or none.")
    return CallToAction(
        timeline_start=cta_start,
        timeline_end=cta_end,
        note=str(raw.get("note") or raw.get("reason") or "").strip(),
    ), None


def resolve(llm_output: dict, data: dict) -> dict:
    from library.tools.reel_proposal import (
        Approval, ReelMoment, enrich, is_conversation,
        overlaps_picture_hole, slugify, snap_to_speech,
        validate_proposal)

    transcript = data.get("timeline_transcript") or {}
    duration = float((transcript.get("derived_from") or {})
                     .get("duration_seconds") or 0.0)
    # Keep exclusions the captain recorded
    # (`library/tools/transcript_corrections.py`) are enforced HERE, on
    # every regenerated proposal, so a struck fragment stays out of the
    # reel no matter how often selection re-runs. An exclusion at a
    # moment's edge trims it; one in its middle drops the moment with
    # the reason - splitting one reel into two would be a new editorial
    # decision, and this step never makes one.
    project_folder = data.get("project_folder") or ""
    keep_exclusions: list = []
    if project_folder:
        try:
            from library.tools import transcript_corrections as _tc
            keep_exclusions = _tc.keep_exclusions(project_folder)
        except Exception:  # noqa: BLE001 - exclusions never break selection
            keep_exclusions = []
    # How many voices a moment must hold: the project's declared
    # speaker count, else the historical two. A monologue project (1)
    # keeps any moment somebody speaks in; a declared-zero project (0)
    # keeps everything with speech - `validate_proposal` still refuses
    # an invented timecode below.
    min_speakers = 2
    if project_folder:
        try:
            from library.tools.footage_identity import (
                expected_speaker_count)
            declared_count = expected_speaker_count(project_folder)
            if declared_count is not None:
                min_speakers = declared_count
        except Exception:  # noqa: BLE001 - the roster never breaks selection
            pass
    chosen = (
        llm_output.get("moments")
        or llm_output.get("reels")
        or (llm_output.get("reel_selection") or {}).get("moments")
        or []
    )

    # A moment key nothing here reads is refused before anything
    # resolves - the refusal travels the post-bridge retry path so the
    # model re-plans instead of the reel shipping without what the key
    # asked for.
    refuse_unknown_keys(chosen, MOMENT_ENTRY_KEYS,
                         step="select_reels", plan="moments")

    moments: List[ReelMoment] = []
    dropped: List[dict] = []
    survivors: list = []
    for index, entry in enumerate(chosen, 1):
        try:
            start = float(entry["start"])
            end = float(entry["end"])
        except (KeyError, TypeError, ValueError):
            dropped.append({"entry": entry,
                            "reason": "no usable start/end"})
            continue
        # Out to whole segments, then out of any word interior: a short
        # must not open or close mid-sentence, and 63 of this episode's
        # 906 segments straddle a cut and are excluded from the segment
        # arithmetic entirely - while their WORDS still constrain the
        # boundary (`reel_proposal.snap_to_speech`).
        start, end = snap_to_speech(start, end, transcript)
        if keep_exclusions:
            from library.tools import transcript_corrections as _tc
            from library.tools.reel_proposal import bound_segments
            grid = [(float(s.get("timeline_start") or 0.0),
                     float(s.get("timeline_end") or 0.0))
                    for s in bound_segments(transcript)]
            kept, struck = _tc.apply_keep_exclusions(
                [{"start": start, "end": end, "entry": entry}],
                keep_exclusions, segments=grid)
            if struck:
                dropped.append({"entry": entry,
                                "reason": struck[0]["reason"]})
                continue
            start, end = kept[0]["start"], kept[0]["end"]
            entry = dict(entry, start=start, end=end,
                         trimmed_by=kept[0].get("trimmed_by", [])) \
                if kept[0].get("trimmed_by") else entry
        cta, cta_dropped = _call_to_action(entry, start, end, transcript)
        if cta_dropped:
            dropped.append({"entry": entry, "reason": cta_dropped})
            continue
        enriched = enrich(ReelMoment(
            number=index,
            slug=slugify(entry.get("slug") or entry.get("title") or ""),
            reason=str(entry.get("reason") or "").strip(),
            timeline_start=start,
            timeline_end=end,
            call_to_action=cta,
            approval=Approval.PROPOSED,
        ), transcript)
        # Drop bad PICKS, not integrity failures. The model told the
        # truth about the timecode; it just made a pick it could not
        # know was bad. Raising would discard the whole batch.
        hole_reason = overlaps_picture_hole(enriched, transcript)
        if hole_reason:
            dropped.append({"entry": entry, "reason": hole_reason})
            continue
        not_convo = is_conversation(enriched, transcript,
                                      min_speakers=min_speakers)
        if not_convo:
            dropped.append({"entry": entry, "reason": not_convo})
            continue

        for i, existing in enumerate(moments):
            overlap_start = max(enriched.timeline_start, existing.timeline_start)
            overlap_end = min(enriched.timeline_end, existing.timeline_end)
            overlap_dur = overlap_end - overlap_start
            if overlap_dur >= 1.0:
                from dataclasses import replace
                warning1 = f" [OVERLAP: shares {overlap_dur:.1f}s ({overlap_start:.1f}-{overlap_end:.1f}s) with {existing.slug}]"
                enriched = replace(enriched, reason=enriched.reason + warning1)
                warning2 = f" [OVERLAP: shares {overlap_dur:.1f}s ({overlap_start:.1f}-{overlap_end:.1f}s) with {enriched.slug}]"
                moments[i] = replace(existing, reason=existing.reason + warning2)

        moments.append(enriched)
        survivors.append((entry, start, end, enriched.slug))

    from dataclasses import replace
    moments = [replace(m, number=idx) for idx, m in enumerate(moments, 1)]

    # The captain's recorded closer pins
    # (`library/tools/captain_edits.py`, `redraw_closer`): a shared
    # closer the captain ruled must open on earlier words is redrawn on
    # every regenerated proposal, so selection cannot re-emit the old
    # start. Applied after enrich (identification reads the closer's
    # measured opening words) and before validation (the redrawn span
    # is checked like a new span). An unappliable pin is reported,
    # never silent - and never fatal to the batch: a reel the pin
    # cannot reach keeps its span with the reason.
    closer_redraws = {"applied": [], "held": [], "stale": []}
    if project_folder:
        try:
            from library.tools import captain_edits as _edits
            _all = _edits.load_edits(project_folder)
            if any(e.get("kind") == "redraw_closer" for e in _all):
                moments, _applied, _held, _stale = \
                    _edits.apply_closer_redraws(moments, transcript, _all)
                closer_redraws = {"applied": _applied, "held": _held,
                                  "stale": _stale}
                for record in _applied:
                    print(
                        f"  Captain edit: reel {record['reel']}'s closer "
                        f"{record['was'][0]:.3f}-{record['was'][1]:.3f}s "
                        f"now opens on {record['anchor_phrase']!r} "
                        f"({record['now'][0]:.3f}s) - "
                        f"{record['reason']}",
                        file=sys.stderr)
                for record in _held:
                    print(
                        f"  Captain edit: reel {record['reel']}'s closer "
                        f"already opens on "
                        f"{record['anchor_phrase']!r} - pin held",
                        file=sys.stderr)
                if _stale:
                    _edits.report_stale(_stale)
        except Exception as exc:  # noqa: BLE001 - pins never break selection
            print(f"WARNING: closer pins could not apply ({exc}); "
                  f"continuing without them.", file=sys.stderr)

    if moments:
        # The fallback duration is the furthest second any moment plays,
        # which is not always a body end: a closer may come from later in
        # the episode than every body, and a timeline shorter than the
        # material would refuse it as outside the timeline.
        furthest = max(
            max(m.timeline_end,
                m.call_to_action.timeline_end if m.call_to_action else 0.0)
            for m in moments)
        # Reels a recorded pin just redrew carry the captain's own
        # word-edge guarantee instead of the whole-segments one: the
        # pin may open mid-row where the row is chunking, and
        # refusing it here would make a recorded decision break every
        # future regeneration (`reel_proposal.validate_proposal`).
        pinned = frozenset(int(r["reel"]) for r in closer_redraws["applied"])
        validate_proposal(moments, transcript, duration or furthest,
                          pinned_cta_reels=pinned)

    # The model's take verdicts (`takes_dropped` on the surviving
    # moments) are RECORDED as keep exclusions, with the model's reason
    # and the reel that verdicted them - so a struck take stays out of
    # every regeneration, not just this one. Recorded past validation:
    # a verdict for a moment that failed validation would strike
    # seconds for a reel that does not exist. Refusals print loudly
    # and never break the batch.
    horizon = float(duration or 0.0)
    if moments and not horizon:
        horizon = max(
            max(m.timeline_end,
                m.call_to_action.timeline_end if m.call_to_action else 0.0)
            for m in moments)
    take_recorded, take_refused = record_take_verdicts(
        survivors, transcript, project_folder, horizon)

    considered = (
        llm_output.get("considered")
        or (llm_output.get("reel_selection") or {}).get("considered")
        or []
    )
    undetermined = (
        llm_output.get("undetermined")
        or (llm_output.get("reel_selection") or {}).get("undetermined")
        or []
    )

    # WAS one project's absolute path, hardcoded: every project this
    # step ever ran for was told its transcript lived in
    # `lucie/geo-podcast`. The engine serves a daily channel and client
    # work (AGENTS.md 14), so a project's own path is not the engine's to
    # state - and the module that WRITES the file already knows where it
    # goes.
    from library.tools.timeline_transcript import transcript_path

    project_folder = data.get("project_folder") or ""
    where = (f" The FULL transcript these were chosen from is at: "
             f"{transcript_path(project_folder)}." if project_folder else "")

    return {
        "reel_selection": _respelt_selection({
            "moments": [m.as_dict() for m in moments],
            "considered": considered,
            "undetermined": undetermined,
            "dropped": dropped,
            "captain_closer_redraws": closer_redraws,
            "approval": (
                "every moment is PROPOSED. Nothing is built until the "
                "captain approves it - see reel_proposal.assert_approved."
                + where
            ),
        }, project_folder),
    }


def _respelt_selection(selection: dict, project_folder: str) -> dict:
    """Recorded spelling corrections, enforced on the regenerated
    selection (the keep-exclusion precedent in this same file):
    measured previews and closer texts plus the model's own reasons
    carry the corrected spelling deterministically, even when the
    transcript copy this run read predates the correction. Identity
    keys (slugs) and decision anchors never move
    (`library/tools/display_respell.py`)."""
    from library.tools.display_respell import apply_post_pass
    apply_post_pass(selection, project_folder or "",
                    "select_reels post-bridge (reel_selection)")
    return selection


def _parse_take_verdict(item) -> tuple:
    """One `takes_dropped` entry as `(start, end, reason)`.

    The handoff asks for `{start, end, reason}` dicts, and the field's
    own history shows the model also writes them as `"300.0-312.04 -
    reason"` strings. Both parse; anything else is refused with the
    reason rather than guessed at. Returns `(start, end, reason)` with
    floats, or `(None, None, refusal)`.
    """
    import re

    if isinstance(item, dict):
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            return None, None, f"names a take with no usable start/end: {str(item)[:120]!r}"
        reason = str(item.get("reason") or item.get("why")
                     or item.get("note") or "").strip()
        return start, end, reason
    if isinstance(item, str):
        match = re.match(
            r"\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*"
            r"(?:[:–-]\s*(.*))?$", item.strip())
        if not match:
            return None, None, f"does not parse as 'start-end - reason': {item[:120]!r}"
        return float(match.group(1)), float(match.group(2)), (match.group(3) or "").strip()
    return None, None, f"is neither a span dict nor a 'start-end - reason' line: {str(item)[:120]!r}"


def _speech_in(start: float, end: float, transcript: dict) -> bool:
    """Do bound segments carry speech inside this span?"""
    from library.tools.reel_proposal import _speech_within, bound_segments
    return bool(_speech_within(bound_segments(transcript or {}), start, end))


def _edge_through_word(edge: float, transcript: dict) -> bool:
    """Does this second land strictly inside a timed word?"""
    from library.tools.reel_build import _timed_word_edges
    return any(word_start < edge < word_end
               for word_start, word_end in _timed_word_edges(transcript))


def record_take_verdicts(surviving: list, transcript: dict,
                         project_folder: str,
                         timeline_duration: float) -> tuple:
    """Record the model's take verdicts as keep exclusions.

    `surviving` is `[(entry, start, end, slug), ...]` for moments that
    passed every check - a dropped moment's verdicts die with it, so
    only survivors verdict. Each `takes_dropped` entry is validated
    structurally, never editorially: parseable seconds, inside the
    timeline, real speech inside, edges on timed-word boundaries, a
    reason given, and never the whole moment (rejecting the moment is
    `considered`'s job, not a take drop's). An exact-span duplicate of
    a recorded exclusion is reported, not re-recorded. Failures refuse
    LOUDLY on stderr and never break the batch: a verdict the store
    cannot take is a verdict the next run cannot see, and that must
    read as refused rather than as absent.

    Returns `(recorded, refused)` - recorded `{"id", "start", "end",
    "slug"}` and refused `{"verdict", "slug", "reason"}` - for the
    run to say what it did.
    """
    import sys

    from library.tools import transcript_corrections as _tc

    recorded, refused = [], []
    if not project_folder:
        for entry, start, end, slug in surviving:
            for item in entry.get("takes_dropped") or []:
                refused.append({"verdict": item, "slug": slug,
                                "reason": "no project_folder on this run: "
                                          "the verdict was held, not recorded"})
                print(f"  WARNING: reel {slug}'s take verdict {str(item)[:80]!r} "
                      f"held (no project store) - not recorded.",
                      file=sys.stderr)
        return recorded, refused

    try:
        existing = _tc.keep_exclusions(project_folder)
    except Exception as exc:  # noqa: BLE001 - verdicts never break selection
        existing = []
        print(f"  WARNING: keep exclusions unreadable ({exc}); take "
              f"verdicts will be checked against an empty store.",
              file=sys.stderr)

    for entry, start, end, slug in surviving:
        raw = entry.get("takes_dropped") or []
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            v_start, v_end, reason = _parse_take_verdict(item)
            if v_start is None:
                refused.append({"verdict": item, "slug": slug,
                                "reason": reason})
                print(f"  take verdict REFUSED on reel {slug}: {reason}",
                      file=sys.stderr)
                continue
            if not v_end > v_start:
                refused.append({"verdict": item, "slug": slug,
                                "reason": f"drops {v_start:.2f}-{v_end:.2f}s, "
                                          f"which is backwards or empty"})
                print(f"  take verdict REFUSED on reel {slug}: not a range.",
                      file=sys.stderr)
                continue
            if v_start < 0 or v_end > timeline_duration + 0.001:
                refused.append({"verdict": item, "slug": slug,
                                "reason": f"drops {v_start:.2f}-{v_end:.2f}s, "
                                          f"outside the 0-{timeline_duration:.2f}s timeline"})
                print(f"  take verdict REFUSED on reel {slug}: outside the timeline.",
                      file=sys.stderr)
                continue
            if not _speech_in(v_start, v_end, transcript):
                refused.append({"verdict": item, "slug": slug,
                                "reason": f"drops {v_start:.2f}-{v_end:.2f}s "
                                          f"where the transcript measured no speech"})
                print(f"  take verdict REFUSED on reel {slug}: no speech measured inside.",
                      file=sys.stderr)
                continue
            bad_edge = next((edge for edge in (v_start, v_end)
                             if _edge_through_word(edge, transcript)), None)
            if bad_edge is not None:
                refused.append({"verdict": item, "slug": slug,
                                "reason": f"drops to {bad_edge:.2f}s inside "
                                          f"a timed word - re-record on word edges"})
                print(f"  take verdict REFUSED on reel {slug}: edge through a word.",
                      file=sys.stderr)
                continue
            if not (reason or "").strip():
                refused.append({"verdict": item, "slug": slug,
                                "reason": f"drops {v_start:.2f}-{v_end:.2f}s "
                                          f"with no reason: say which telling "
                                          f"plays instead and why"})
                print(f"  take verdict REFUSED on reel {slug}: no reason given.",
                      file=sys.stderr)
                continue
            if v_start <= start and v_end >= end:
                refused.append({"verdict": item, "slug": slug,
                                "reason": f"drops {v_start:.2f}-{v_end:.2f}s, "
                                          f"the whole moment - reject the "
                                          f"moment in `considered` instead"})
                print(f"  take verdict REFUSED on reel {slug}: covers the whole moment.",
                      file=sys.stderr)
                continue
            if any(round(e["start"], 2) == round(v_start, 2)
                   and round(e["end"], 2) == round(v_end, 2)
                   for e in existing):
                print(f"  take verdict on reel {slug} already recorded at "
                      f"{v_start:.2f}-{v_end:.2f}s - not duplicated.",
                      file=sys.stderr)
                continue
            try:
                learning = _tc.record_keep_exclusion(
                    project_folder, v_start, v_end,
                    f"model take verdict (select_reels, reel '{slug}'): "
                    f"{reason.strip()} [drops {v_start:.2f}-{v_end:.2f}s "
                    f"of the chosen {start:.2f}-{end:.2f}s]",
                    author="model")
            except Exception as exc:  # noqa: BLE001 - verdicts never break selection
                refused.append({"verdict": item, "slug": slug,
                                "reason": f"the store refused it: {exc}"})
                print(f"  take verdict REFUSED on reel {slug}: {exc}",
                      file=sys.stderr)
                continue
            recorded.append({"id": learning.get("id", ""),
                             "start": v_start, "end": v_end, "slug": slug})
            existing.append({"start": v_start, "end": v_end})
            print(f"  take verdict RECORDED on reel {slug}: "
                  f"{learning.get('id', '')} strikes {v_start:.2f}-"
                  f"{v_end:.2f}s - {reason.strip()[:100]}",
                  file=sys.stderr)
    return recorded, refused


def main():
    import sys
    import json
    import traceback
    try:
        data = json.loads(sys.stdin.read())
        print(json.dumps(resolve(data, data)))
    except Exception as e:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
