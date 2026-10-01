"""Apply the plan's native Resolve operations, judged by read-back.

This is the build-side half of fidelity rung 3b. The plan side
(`plan_vfx` admitting `speed_ramp`/`freeze_frame`,
`plan_transitions` admitting the granted native transitions,
`compile_manifest` carrying them as `native_speed_ops` and
`native_transitions`) ends here: each op is applied through the
resolve-axi 0.6.0 verb's native call and judged by the verb's
read-back, never claimed off the write's True alone.

- Speed: `TimelineItem.SetSpeed` with a constant Percentage, judged by
  `GetSpeed` re-reading equal within 1e-6. `speed_ramp` is a SEQUENCE
  of constant steps (Resolve 21.1 carries no speed-curve API), so each
  segment is matched to the ONE timeline item spanning exactly that
  segment and given its own write plus its own read-back. The linked
  dialogue audio rides the same write (finding 17): every dialogue
  item sharing the video item's record span is retimed to the same
  Percentage and judged by its own re-read, and an audio refusal
  fails the op (restoring the video item) rather than shipping the
  pair split across two speeds.
- Freeze: `SetSpeed` 0.0, judged by `GetSpeed` re-reading 0.0. A freeze
  after a retime refuses exactly as the verb does (measured 2026-09-24:
  the write answers True while `GetSpeed` re-reads 100.0).
- Transitions: `TimelineItem.AddTransition` at the cut on the track
  of the clip the transition was planned into (finding 16), judged by
  the returned transition item whose span reads. An empty answer
  refuses naming the type and category tried, with the handles hint.

Two limits are refusals, not fallbacks:

- A multi-segment ramp whose steps do not each span exactly one
  timeline item refuses: blading one item into steps is an unmeasured
  verb (no split call in the 21.1 stub this rung measured), so the
  build will not invent the cuts. Place the steps as separate items
  (blade by placement) or plan one step per item.
- Plan speed ops never ripple (`RippleTimeline` False): a ripple moves
  every later cut, and the Fusion pass plus the caption offsets below
  are keyed to the placed positions. A ripple is the verb's explicit
  flag (`resolve_axi edit speed --ripple`), never a plan default.

Every refusal and every failed read-back is returned in the report -
the caller (the timeline build) records them as ERRORS, never
warnings: an op the plan placed that the timeline does not carry is a
picture the build disobeyed, and it must fail loudly.
"""

from __future__ import annotations


SPEED_TOLERANCE = 1e-6
"""`GetSpeed` must re-read within this of the written Percentage."""


def _item_span_seconds(item, fps: float) -> tuple:
    """The item's (start, end) in timeline seconds.

    Read off `GetStart` plus `GetDuration`, never `GetEnd` alone:
    measured on Resolve 21.1, an item placed with startFrame 3000 and
    endFrame 3074 reads GetStart=0, GetEnd=74, GetDuration=74 - the end
    is exclusive, and a 29.97fps source on a 30fps timeline truncates
    the ratio on top. Start-plus-duration is the span Resolve plays.
    """
    return (float(item.GetStart()) / fps,
            (float(item.GetStart()) + float(item.GetDuration())) / fps)


def _spans_match(span_a: tuple, span_b: tuple, fps: float) -> bool:
    """Whether two timeline spans are the same cut, within 1.5 frames.

    The op span is compile-time seconds; the item span is placed frames,
    and the two round independently (source-to-timeline fps ratios,
    inclusive/exclusive frame conventions - measured a full frame apart
    on 21.1). Half a frame would refuse rounding noise as mismatch.
    The exactly-one-match requirement below is what keeps this honest:
    two items never share both endpoints within 1.5 frames unless they
    are stacked duplicates, which refuses as ambiguous.
    """
    eps = 1.5 / fps + 1e-6
    return (abs(span_a[0] - span_b[0]) <= eps
            and abs(span_a[1] - span_b[1]) <= eps)


def _read_speed_percent(item):
    """The item's current speed Percentage, or (None, reason)."""
    try:
        opts = item.GetSpeed()
    except AttributeError:
        return None, "this build has no GetSpeed to re-read"
    except Exception as exc:
        return None, f"the speed re-read raised ({exc})"
    try:
        return float((opts or {}).get("Percentage")), ""
    except (TypeError, ValueError):
        return None, f"GetSpeed re-reads {opts!r}"


def retime_linked_audio(video_item, video_name: str, percent: float,
                         fps: float, audio_items: list,
                         retimed_ids: set) -> tuple:
    """SetSpeed the dialogue items sharing one video item's record span.

    Finding 17: `SetSpeed` on a talking shot left its audio at full
    speed - picture playing source 25-61 in 72 record frames while
    audio1 still played 25-97 - so a retimed talking shot lost sync
    while the build reported success. Every dialogue item playing the
    video item's own record span rides the same write, each judged by
    its own `GetSpeed` re-read.

    Args:
        video_item: the just-retimed video item (its CURRENT record
            span selects the audio - plan ops never ripple, so the
            span the op matched is the span still playing).
        video_name: the video item's name, for failure messages.
        percent: the Percentage just written on the video item.
        fps: the timeline rate.
        audio_items: the dialogue-row audio items to select from -
            the caller scopes these (the build passes its speech rows
            only, never the bed or SFX rows), so a span match here is
            linkage, not coincidence.
        retimed_ids: the build's already-retimed set, shared with the
            video path - a freeze on an already-retimed item refuses
            exactly as the video freeze does.

    Returns (applied, failed): `applied` is one row per judged audio
    write; `failed` is None, or the one failure naming the audio item
    that refused. On failure the caller restores the video item - a
    half-retimed (picture fast, sound slow) pair must never ship as
    an applied op.
    """
    try:
        span = _item_span_seconds(video_item, fps)
    except Exception as exc:
        return [], {"item": "?",
                    "what": f"the retimed video item {video_name!r} "
                            f"would not read its span ({exc}) - its "
                            f"linked audio cannot be found, refusing "
                            f"to claim the retime",
                    "fix": "verify the item by hand"}
    try:
        namespaced = [(a, _item_span_seconds(a, fps)) for a in audio_items]
    except Exception:
        namespaced = []
    cands = [a for a, s in namespaced if _spans_match(s, span, fps)]
    applied = []
    for audio in cands:
        try:
            name = audio.GetName()
        except Exception:
            name = "?"
        is_freeze = abs(percent) <= 1e-9
        if is_freeze:
            if id(audio) in retimed_ids:
                return applied, {
                    "item": name,
                    "what": f"freeze on linked audio {name!r} refuses: "
                            f"it was already retimed by this build",
                    "fix": "plan the freeze on an item this build has "
                           "not retimed"}
            current, reason = _read_speed_percent(audio)
            if current is None:
                return applied, {
                    "item": name,
                    "what": f"freeze on linked audio {name!r} refuses: "
                            f"{reason}",
                    "fix": "verify the item by hand"}
            if abs(current - 100.0) > SPEED_TOLERANCE:
                return applied, {
                    "item": name,
                    "what": f"freeze on linked audio {name!r} refuses: "
                            f"it already plays at {current:g}%",
                    "fix": "plan the freeze on an item at 100%"}
        try:
            wrote = bool(audio.SetSpeed({"Percentage": percent,
                                         "RippleTimeline": False}))
        except Exception as exc:
            return applied, {
                "item": name,
                "what": f"SetSpeed({percent:g}%) on linked audio "
                        f"{name!r} raised ({exc}) - the video item "
                        f"{video_name!r} is already retimed, verify "
                        f"by hand",
                "fix": "restore the video item to its pre-op speed "
                       "and verify the pair by hand"}
        if not wrote:
            return applied, {
                "item": name,
                "what": f"SetSpeed({percent:g}%) answered False on "
                        f"linked audio {name!r} - nothing was claimed "
                        f"for it",
                "fix": "verify the pair by hand"}
        back, reason = _read_speed_percent(audio)
        if back is None or abs(back - percent) > SPEED_TOLERANCE:
            detail = reason or f"re-reads {back:g}"
            return applied, {
                "item": name,
                "what": f"SetSpeed({percent:g}%) on linked audio "
                        f"{name!r} {detail} - refusing to claim it",
                "fix": "verify the pair by hand"}
        if not is_freeze:
            retimed_ids.add(id(audio))
        applied.append({"item": name, "percent": percent,
                        "verified": "GetSpeed re-reads equal"})
    return applied, None


def apply_native_speed_ops(timeline, ops: list, fps: float = 30.0,
                            dialogue_tracks: list = None,
                            video_tracks: tuple = (1, 2)) -> dict:
    """Apply each native speed op; judge each by `GetSpeed`.

    Args:
        timeline: the live Resolve timeline (items are read off V1 and
            V2, the picture rows native ops address).
        ops: `native_speed_ops` rows from the manifest - each carrying
            `op_id`, `effect_type` (`speed_ramp` with `segments`, or
            `freeze_frame`), and its timeline span.
        fps: the timeline rate, for frame/second conversion.
        dialogue_tracks: the Resolve audio track indices carrying
            dialogue (the build's speech rows) - linked audio is
            selected from these rows only, never the bed or SFX rows.
            None reads no audio: the video retime is still judged,
            but no linked-audio claim is made.
        video_tracks: the picture rows whose items an op may match -
            V1 and V2 for the manifest's native ops; a reel passes its
            own a-roll rows (a reel's angles ride per-angle rows).

    Returns a report with `applied` (one row per judged write) and
    `failed` (one row per refusal or failed read-back, each naming the
    op, what was tried, and the fix). No exception escapes for a
    per-op failure - the caller decides whether the build survives,
    and the timeline build does not.
    """
    items = []
    for track_index in video_tracks:
        try:
            items.extend(timeline.GetItemListInTrack("video", track_index)
                         or [])
        except Exception:
            continue
    dialogue_items = []
    for track_index in dialogue_tracks or []:
        try:
            dialogue_items.extend(
                timeline.GetItemListInTrack("audio", track_index) or [])
        except Exception:
            continue
    report: dict = {"applied": [], "failed": []}
    # Items already retimed by THIS report: a freeze on one refuses
    # (the measured freeze-after-ripple case), whatever the re-read
    # says - the pixels freeze while `GetSpeed` answers 100.0, so no
    # read-back could judge it.
    retimed_ids = set()

    def _fail(op_id, what, fix):
        report["failed"].append(
            {"op_id": op_id, "what": what, "fix": fix})

    for op in ops or []:
        op_id = op.get("op_id", "?")
        effect = op.get("effect_type", "")
        if effect == "freeze_frame":
            steps = [{"percent": 0.0,
                      "timeline_start": op.get("timeline_start"),
                      "timeline_end": op.get("timeline_end")}]
        elif effect == "speed_ramp":
            steps = list(op.get("segments") or [])
        else:
            _fail(op_id,
                  f"unknown native speed effect {effect!r} - nothing was "
                  f"written",
                  "name `speed_ramp` (with `segments`) or `freeze_frame`")
            continue
        for step in steps:
            try:
                span = (float(step["timeline_start"]),
                        float(step["timeline_end"]))
                percent = float(step["percent"])
            except (TypeError, ValueError, KeyError):
                _fail(op_id,
                      f"step {step!r} names no usable span and percent - "
                      f"nothing was written",
                      "re-plan the op with timeline_start, timeline_end "
                      "and a percent above 0 (a freeze is `freeze_frame`)")
                continue
            matches = [it for it in items
                       if _spans_match(_item_span_seconds(it, fps), span,
                                       fps)]
            if not matches:
                _fail(
                    op_id,
                    f"no timeline item spans "
                    f"{span[0]:.3f}-{span[1]:.3f}s for {percent:g}% - "
                    f"nothing was written",
                    "blade the step as its own item at placement time "
                    "(blading one item into steps is unmeasured - the "
                    "build will not invent the cuts), or re-plan the "
                    "step onto one item's span")
                continue
            if len(matches) > 1:
                _fail(op_id,
                      f"{len(matches)} items span "
                      f"{span[0]:.3f}-{span[1]:.3f}s - ambiguous, nothing "
                      f"was written",
                      "re-plan the step onto one item's span")
                continue
            item = matches[0]
            try:
                name = item.GetName()
            except Exception:
                name = "?"
            # The pre-op speed, read BEFORE the write: an audio
            # refusal below puts the video item back to this rather
            # than leave the pair split across two speeds (finding
            # 17). None where it will not read - then no restore is
            # attempted and the failure says the pair needs a hand.
            pre, _ = _read_speed_percent(item)
            is_freeze = abs(percent) <= 1e-9
            if is_freeze:
                if id(item) in retimed_ids:
                    _fail(
                        op_id,
                        f"freeze on {name!r} refuses: the item was already "
                        f"retimed by this build (freeze after a retime "
                        f"re-reads 100.0 while the pixels freeze - "
                        f"measured 2026-09-24 - so no read-back could "
                        f"judge it)",
                        "plan the freeze on an item this build has not "
                        "retimed")
                    continue
                current, reason = _read_speed_percent(item)
                if current is None:
                    _fail(op_id,
                          f"freeze on {name!r} refuses: {reason}",
                          "verify the freeze by hand")
                    continue
                if abs(current - 100.0) > SPEED_TOLERANCE:
                    _fail(
                        op_id,
                        f"freeze on {name!r} refuses: it already plays at "
                        f"{current:g}% (freeze after a retime re-reads "
                        f"100.0 - measured 2026-09-24)",
                        "plan the freeze on an item at 100%")
                    continue
            try:
                wrote = bool(item.SetSpeed({"Percentage": percent,
                                            "RippleTimeline": False}))
            except Exception as exc:
                _fail(op_id,
                      f"SetSpeed({percent:g}%) on {name!r} raised ({exc}) "
                      f"- verify by hand",
                      "verify the item by hand")
                continue
            if not wrote:
                _fail(op_id,
                      f"SetSpeed({percent:g}%) answered False on {name!r} "
                      f"- nothing was claimed",
                      "verify the item by hand")
                continue
            back, reason = _read_speed_percent(item)
            if back is None:
                _fail(op_id,
                      f"SetSpeed({percent:g}%) answered True on {name!r} "
                      f"but {reason} - refusing to claim it",
                      "verify the item by hand")
                continue
            if abs(back - percent) > SPEED_TOLERANCE:
                _fail(op_id,
                      f"SetSpeed({percent:g}%) reports True on {name!r} "
                      f"and re-reads {back:g} - refusing to claim it",
                      "verify the item by hand")
                continue
            if not is_freeze:
                retimed_ids.add(id(item))
            # The linked audio rides the same write (finding 17): a
            # talking shot retimed without its dialogue loses sync.
            audio_applied, audio_failed = retime_linked_audio(
                item, name, percent, fps, dialogue_items, retimed_ids)
            if audio_failed is not None:
                restored = ""
                if pre is not None:
                    try:
                        if bool(item.SetSpeed(
                                {"Percentage": pre,
                                 "RippleTimeline": False})):
                            back_pre, _ = _read_speed_percent(item)
                            if (back_pre is not None and abs(back_pre - pre)
                                    <= SPEED_TOLERANCE):
                                restored = (f" the video item was put "
                                            f"back to {pre:g}%")
                    except Exception:
                        pass
                _fail(op_id,
                      f"SetSpeed({percent:g}%) on {name!r} verified, but "
                      f"its linked audio refused - "
                      f"{audio_failed['what']}."
                      f"{restored or ' the video item could not be put back - verify the pair by hand'}",
                      audio_failed["fix"])
                continue
            report["applied"].append({
                "op_id": op_id,
                "item": name,
                "percent": percent,
                "span": [round(span[0], 3), round(span[1], 3)],
                "verified": "GetSpeed re-reads equal",
                "audio": (audio_applied if audio_applied
                          else "no linked dialogue audio on rows "
                               f"{list(dialogue_tracks or [])} - video only"),
            })
    return report


def apply_native_transitions(timeline, v1_items: list, ops: list,
                              fps: float = 30.0, v2_items: list = None
                              ) -> dict:
    """Place each native transition at its cut; judge each by return.

    Args:
        timeline: the live Resolve timeline (unused except for future
            span checks; the writes go through the items).
        v1_items: the V1 timeline items in timeline order - `after_clip`
            indexes into this list for `track: "v1"` ops.
        ops: `native_transitions` rows from the manifest - each carrying
            `transition_id`, `resolve_name`, `category`, `after_clip`
            (the OUTGOING clip's index on its track), `track`
            (`"v1"` or `"v2"` - the track of the clip the transition
            was planned into, finding 16) and `duration_frames`.
        fps: the timeline rate (recorded, not converted - durations ride
            in frames as Resolve takes them).
        v2_items: the V2 timeline items in timeline order, for
            `track: "v2"` ops. None where the timeline carries no
            b-roll row - a V2 op then refuses by name.

    Returns a report with `applied` and `failed`, shaped like
    `apply_native_speed_ops`'s. A transition whose span will not read,
    or an empty answer, is a failure naming the type and category
    tried - never a hard cut.

    An op carrying `at_end: true` is the rung-7 end slot: it places on
    the LAST item at its end (trailing onto nothing, which the hands
    probe measured the raw API granting) instead of on an incoming
    item's start.
    """
    report: dict = {"applied": [], "failed": []}

    def _fail(trans_id, what, fix):
        report["failed"].append(
            {"transition_id": trans_id, "what": what, "fix": fix})

    for op in ops or []:
        trans_id = op.get("transition_id", "?")
        want_type = op.get("resolve_name", "")
        category = op.get("category", "simple")
        after_clip = op.get("after_clip")
        at_end = bool(op.get("at_end"))
        duration = op.get("duration_frames")
        track = op.get("track", "v1")
        track_items = v2_items if track == "v2" else v1_items
        track_name = "V2" if track == "v2" else "V1"
        if track == "v2" and track_items is None:
            _fail(trans_id,
                  f"planned on the b-roll track but this timeline "
                  f"carries no V2 items - nothing was written",
                  "re-plan the transition onto a V1 cut, or place "
                  "b-roll first")
            continue
        if (not isinstance(after_clip, int) or isinstance(after_clip, bool)
                or after_clip < 0
                or after_clip >= len(track_items or [])
                or (not at_end and after_clip + 1 >= len(track_items or []))):
            _fail(trans_id,
                  f"after_clip {after_clip!r} is no {track_name} cut "
                  f"({len(track_items or [])} item(s)) - nothing was "
                  f"written",
                  "re-plan the transition onto a cut with an outgoing "
                  "and an incoming clip on the same track")
            continue
        if (not isinstance(duration, (int, float))
                or isinstance(duration, bool) or duration < 1):
            _fail(trans_id,
                  f"duration {duration!r} names no hold - nothing was "
                  f"written",
                  "re-plan the transition with a duration_feel so the "
                  "build knows how long to hold it")
            continue
        if at_end:
            target = track_items[after_clip]
            position = "end"
        else:
            target = track_items[after_clip + 1]
            position = "start"
        try:
            target_name = target.GetName()
        except Exception:
            target_name = "?"
        payload = {"type": want_type, "category": category,
                   "position": position, "alignment": "center",
                   "duration": int(duration)}
        try:
            placed = target.AddTransition(dict(payload))
        except Exception as exc:
            _fail(trans_id,
                  f"AddTransition({want_type!r}, {category!r}) raised "
                  f"({exc}) on {target_name!r} - verify by hand",
                  "verify the cut by hand")
            continue
        if placed is None or placed is False:
            _fail(
                trans_id,
                f"AddTransition({want_type!r}, {category!r}) answered "
                f"empty on {target_name!r} - the type or category took "
                f"nothing on this build (a clip starting at source 0 "
                f"has no head handles for a centered transition)",
                "re-plan with handles on both sides of the cut, or with "
                "a granted type and category")
            continue
        try:
            tr_span = {"name": placed.GetName(),
                       "record_in": placed.GetStart(),
                       "record_out": placed.GetEnd(),
                       "duration": placed.GetDuration()}
        except Exception as exc:
            _fail(trans_id,
                  f"AddTransition answered, but the transition item "
                  f"would not read its span ({exc}) - refusing to "
                  f"claim it",
                  "verify the cut by hand")
            continue
        report["applied"].append({
            "transition_id": trans_id,
            "type": want_type,
            "category": category,
            "position": position,
            "track": track_name,
            "transition": tr_span["name"],
            "duration": tr_span["duration"],
            "record_in": tr_span["record_in"],
            "record_out": tr_span["record_out"],
            "verified": "returned a transition item whose span reads",
        })
    return report
