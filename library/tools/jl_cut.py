"""J-cuts and L-cuts on the master path, with room-tone fill.

Fidelity probe P3 (doc J-cuts with room tone, geo-podcast): there is no
J/L vocabulary on the master path - `transition_vocabulary` withdraws
both as "audio thread out of scope", and the one J-cut that exists is a
reel variant for a single join with seconds typed by hand
(`reel_build.plan_j_cut`, `variant new --j-cut`). This module is the
master-path vocabulary the probe asked for.

What a J/L cut IS here
----------------------
The picture cut stays where step 4.02 resolves it. The AUDIO cut moves:

- `j_cut`: the ear crosses early. The outgoing speech item is trimmed
  to end at `audio_cut` (before the picture cut); the gap it opens -
  `[audio_cut, picture_cut)` - is filled with the INCOMING source's
  room tone, so the new room arrives before the new picture.
- `l_cut`: the ear lingers. The incoming speech item is trimmed to
  start at `audio_cut` (after the picture cut); the gap -
  `[picture_cut, audio_cut)` - is filled with the OUTGOING source's
  room tone, so the old room lingers under the new picture.

The trim construction (rather than the reel variant's reach-back one)
never touches source the run never played: every trimmed span is
inside played audio, and the fill is measured room, so there is no
lead-in span for an operator to check. The offset OPENS a gap, and the
fill CLOSES it - that is why a J/L join can never drop to digital
silence: coverage of the speech row is continuous by construction, and
`apply_jl_cuts` asserts it.

Addressing
----------
The join is the spine boundary the plan names (`cut_point_position`,
the incoming block - same addressing as every 4.02 entry). The audio
cut is EITHER `lead_seconds` / `lag_seconds` stated by the plan (the
model's number, not a hand-typed variant spec) OR a sub-block `anchor`
(`library/tools/sub_block_anchor.py`) resolving inside the outgoing
block (J) or the incoming block (L) - a word, a beat/downbeat/bar, or
a frame. Both stated and disagreeing past half a frame refuses: two
numbers for one cut is an ambiguous spec, not a choice to make
silently.

Refusals (`JLCutRefused`, the `RenRefusal` shape) cover: unknown kind,
unresolvable join, a non-positive or missing offset, an audio cut on
the wrong side of the picture cut, a lead/lag that escapes its block,
a trimmed span carrying speech (the offset must sit inside the pause),
either side without audio, and a fill source with no measurable room.
"""

from __future__ import annotations

import os

from library.tools.ren_refusal import RenRefusal


class JLCutRefused(RenRefusal):
    """A planned J/L cut names something that cannot be built."""


JL_KINDS = ("j_cut", "l_cut")

#: Plan-entry keys this vocabulary reads. Anything else on a J/L entry
#: is refused by the post-bridge's `refuse_unknown_keys` before this
#: module ever sees it - an unread key is how P5's `at_word` landed
#: 3.06 s early.
JL_ENTRY_KEYS = frozenset({
    "cut_point_position",
    "cut_point_original",
    "cut_point_timeline",
    "cut_time",
    "type",
    "transition_type",
    "lead_seconds",
    "lag_seconds",
    "anchor",
    "rationale",
})


def _refuse(what: str, why: str, fix: str) -> JLCutRefused:
    return JLCutRefused(what=what, why=why, fix=fix)


def normalise_kind(raw) -> str | None:
    """`j_cut` / `l_cut` for the plan's spelling, else None."""
    key = str(raw or "").strip().lower()
    return key if key in JL_KINDS else None


def _number(value, name: str, join_label: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _refuse(
            f"the {join_label} J/L cut states {name} {value!r}, which "
            f"is not a number",
            "an offset nobody can measure in seconds is not an offset - "
            "placing it at the picture cut instead would ship timing "
            "nobody decided on.",
            f"re-plan the cut with {name} as seconds (a positive "
            f"number), an `anchor`, or drop the cut.",
        )
    return float(value)


def words_in_span(block: dict, start_tl: float, end_tl: float) -> list:
    """Transcript words sounding inside [start_tl, end_tl), timeline domain.

    Reads the block's own `word_timestamps` through the spine contract
    (`source_to_timeline`) - the same interface `sub_block_anchor`
    resolves words through. A block with no word timings carries no
    words, so the span reads speech-free.
    """
    from library.tools.spine_contract import source_to_timeline

    hits = []
    for entry in block.get("word_timestamps") or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = source_to_timeline(float(entry["source_start"]), block)
            end = source_to_timeline(float(entry["source_end"]), block)
        except (KeyError, TypeError, ValueError):
            continue
        if start < end_tl and end > start_tl:
            hits.append(str(entry.get("word", "?")))
    return hits


def resolve_audio_cut(*, kind: str, entry: dict, outgoing: dict,
                      incoming: dict, boundary_frame: int,
                      frame_rate: float, music_analysis=None,
                      music_selection=None, index=0) -> dict:
    """Resolve the plan's offset to an exact audio cut, in frames.

    The reference is the V1 boundary frame - where the eye cuts a
    hard cut and where the incoming speech row starts - never the
    resolved picture-cut seconds: those can sit a frame off the
    boundary (frame quantization), and measuring the offset from
    them opens a real gap of digital silence between the fill's end
    and the incoming start. That gap is what `_assert_row_coverage`
    in `apply_jl_cuts` caught on the geo-podcast proof.

    Returns {"audio_cut_frame", "audio_cut_timeline", "lead_seconds",
    "method"}. `lead_seconds` is the frame-true shipped offset
    (frames / fps), which can differ from a stated seconds value by
    up to half a frame. Raises `JLCutRefused` where the plan names
    nothing buildable.
    """
    join_label = (f"into block {incoming.get('position', '?')!r} "
                  f"({kind})")
    anchor = entry.get("anchor")
    lead = None
    lag = None
    if entry.get("lead_seconds") is not None:
        lead = _number(entry["lead_seconds"], "lead_seconds", join_label)
    if entry.get("lag_seconds") is not None:
        lag = _number(entry["lag_seconds"], "lag_seconds", join_label)
    if kind == "j_cut" and lag is not None:
        raise _refuse(
            f"the {join_label} J-cut states `lag_seconds`",
            "a J-cut leads (audio early) - a lag is an L-cut's "
            "spelling, and reading it as a lead would ship the "
            "opposite offset.",
            "re-plan the cut with `lead_seconds`, or as `l_cut` with "
            "`lag_seconds`.",
        )
    if kind == "l_cut" and lead is not None:
        raise _refuse(
            f"the {join_label} L-cut states `lead_seconds`",
            "an L-cut lags (audio late) - a lead is a J-cut's "
            "spelling, and reading it as a lag would ship the "
            "opposite offset.",
            "re-plan the cut with `lag_seconds`, or as `j_cut` with "
            "`lead_seconds`.",
        )
    stated = lead if kind == "j_cut" else lag
    if stated is not None and stated <= 0:
        raise _refuse(
            f"the {join_label} cut states a non-positive offset "
            f"({stated}s)",
            "a J/L cut with no offset IS the straight cut it was asked "
            "to improve - placing it anyway would claim an offset "
            "nobody hears.",
            "re-plan the cut with a positive offset, or drop it and "
            "keep the straight cut.",
        )

    anchored_cut = None
    anchored_method = ""
    if anchor is not None:
        from library.tools.sub_block_anchor import resolve_anchor

        host = outgoing if kind == "j_cut" else incoming
        side = ("outgoing" if kind == "j_cut" else "incoming")
        hit = resolve_anchor(
            anchor, block=host, music_analysis=music_analysis,
            music_selection=music_selection, frame_rate=frame_rate,
            step="plan_transitions", plan="transition_creative",
            index=index)
        anchored_cut = hit["timeline_seconds"]
        anchored_method = f"anchor in {side} block: {hit['method']}"

    boundary_frame = int(boundary_frame)
    boundary_seconds = boundary_frame / float(frame_rate)
    half_frame = 0.5 / float(frame_rate)
    if anchored_cut is not None and stated is not None:
        implied = (boundary_seconds - anchored_cut if kind == "j_cut"
                   else anchored_cut - boundary_seconds)
        if abs(implied - stated) > half_frame + 1e-9:
            raise _refuse(
                f"the {join_label} cut states {stated:.3f}s and an "
                f"anchor resolving to a {implied:.3f}s offset",
                "two numbers for one cut is an ambiguous spec - picking "
                "one silently would ship timing the plan did not agree "
                "on.",
                "re-plan the cut with the offset and the anchor "
                "agreeing, or state only one of them.",
            )
        audio_exact = anchored_cut
        method = f"{anchored_method} (agrees with stated {stated:.3f}s)"
    elif anchored_cut is not None:
        audio_exact = anchored_cut
        method = anchored_method
    elif stated is not None:
        audio_exact = (boundary_seconds - stated if kind == "j_cut"
                       else boundary_seconds + stated)
        method = f"plan states {stated:.3f}s"
    else:
        raise _refuse(
            f"the {join_label} cut states no offset",
            "a J/L cut without a lead, a lag or an anchor is a straight "
            "cut wearing another name.",
            "re-plan the cut with `lead_seconds` (`j_cut`) or "
            "`lag_seconds` (`l_cut`), or with an `anchor`, or drop it.",
        )

    host = outgoing if kind == "j_cut" else incoming
    host_start = float(host["timeline_start"])
    host_end = float(host["timeline_end"])
    trim_start = min(audio_exact, boundary_seconds)
    trim_end = max(audio_exact, boundary_seconds)
    # The join-side edge may stray up to half a frame past the host
    # (the boundary frame itself quantizes the exact block seconds).
    # The sliver is always HEARD audio - the neighbor clip covers it -
    # never dropped speech, so no word check reaches it; past half a
    # frame it is a mistimed cut, not quantization, and refuses below.
    # The FAR edge is strict: that is where dropped speech lives.
    if kind == "j_cut":
        inside = (host_start - 1e-9 <= trim_start
                  and trim_end <= host_end + half_frame + 1e-9
                  and trim_end > host_start)
    else:
        inside = (host_start - half_frame - 1e-9 <= trim_start
                  and trim_end <= host_end + 1e-9
                  and trim_end > host_start)
    if not inside:
        raise _refuse(
            f"the {join_label} cut trims {trim_start:.3f}-"
            f"{trim_end:.3f}s, outside its "
            f"{'outgoing' if kind == 'j_cut' else 'incoming'} block "
            f"({host_start:.3f}-{host_end:.3f}s)",
            "an offset that escapes its block reaches into a moment the "
            "plan addressed nothing to.",
            "re-plan the cut with a shorter offset inside the block, "
            "or anchor it there.",
        )
    sounding = words_in_span(host, trim_start, trim_end)
    if sounding:
        quoted = ", ".join(repr(w) for w in sounding[:6])
        raise _refuse(
            f"the {join_label} cut trims {trim_start:.3f}-"
            f"{trim_end:.3f}s, where {quoted} sound"
            f"{'' if len(sounding) == 1 else 's'}",
            "the offset must sit inside the pause at the boundary - "
            "trimming speech drops words the spine promised. The audio "
            "is measured from the V1 boundary frame, so the pause has "
            "to cover the lead before it (J) or the lag after it (L).",
            "re-plan with a shorter offset inside the pause, or "
            "another join whose boundary breathes.",
        )
    # Placement is frame-granular: the audio cut lands on the nearest
    # frame to the exact offset. Speech was judged above on the exact
    # seconds - the plan's intent - so a word edge within half a frame
    # of the cut does not refuse; the at most half-frame nibble is the
    # timeline's own granularity, and the method says so. An exact
    # offset that quantizes onto the boundary (or past it) is a
    # straight cut (or the other kind) wearing this kind's name, and
    # refuses here, where the frames are known.
    audio_frame = int(round(audio_exact * frame_rate))
    audio_seconds = audio_frame / float(frame_rate)
    offset_frames = (boundary_frame - audio_frame if kind == "j_cut"
                     else audio_frame - boundary_frame)
    if offset_frames <= 0:
        raise _refuse(
            f"the {join_label} cut resolves its audio cut to frame "
            f"{audio_frame} ({audio_seconds:.3f}s), "
            f"{'on' if offset_frames == 0 else 'past'} "
            f"the boundary frame {boundary_frame} "
            f"({boundary_seconds:.3f}s)",
            "an audio cut on the wrong side of (or on) the boundary "
            f"is {'an L-cut' if kind == 'j_cut' else 'a J-cut'}, not "
            "the kind planned - and an audio cut ON the boundary is "
            "a straight cut.",
            f"re-plan the cut with the audio cut "
            f"{'before' if kind == 'j_cut' else 'after'} the boundary, "
            f"or as the other kind.",
        )
    else:
        method += (f"; placed on frame {audio_frame} "
                   f"({offset_frames} frame(s) off the boundary)")
    lead_seconds = offset_frames / float(frame_rate)
    return {
        "audio_cut_frame": audio_frame,
        "audio_cut_timeline": round(audio_seconds, 3),
        # The exact (unquantized) audio cut: the apply-time speech
        # recheck runs on these seconds - the plan's intent - so a
        # word edge within half a frame of the placed frame does not
        # refuse what plan time approved. Frames place; seconds judge.
        "audio_cut_exact": round(audio_exact, 3),
        "picture_cut_frame": boundary_frame,
        "picture_cut_timeline": round(boundary_seconds, 3),
        "lead_seconds": round(lead_seconds, 3),
        "method": method,
    }


def apply_jl_cuts(v1_clips: list, jl_entries: list, spine_blocks: list,
                  fps: float, measure_fn, stage_fill_fn,
                  stage_dir: str) -> dict:
    """Trim speech-row audio for each J/L join and fill the opened gap.

    `v1_clips` are the compiled V1 clip dicts (each carrying
    `source_file`, `source_in/out`, `timeline_in/out`,
    `timeline_in/out_frame`, and the block `position` in its label or
    `spine_position` key). `jl_entries` are resolved `audio_offset`
    dicts (each with `kind`, `audio_cut_frame`, `picture_cut_frame`
    - the V1 boundary frame - `outgoing_position`,
    `incoming_position`). `measure_fn(source_file, speech_spans)` and
    `stage_fill_fn(source_file, record, fill_seconds, out_path)` are
    the `room_tone` measurements (injected so tests use fakes).

    Every edge is computed in FRAMES: the timeline places and links
    by frame, and seconds carry up to half a frame of slop - which is
    how a fill once ended three frames from the incoming start,
    leaving real digital silence the seconds compared equal on. The
    trim lands the clip's audio edge exactly on the audio-cut frame
    and the fill spans exactly the frames between the audio cut and
    the boundary, so coverage is continuous by construction and
    `_assert_row_coverage` holds it in integers, not milliseconds.

    Returns {"v1_clips", "fills", "room_tone", "applied"}: the clips
    with `audio_src_in` / `audio_src_out` set on the trimmed side, one
    fill record per join (staged room tone abutting the trim exactly),
    the room-tone measurement per fill source, and the applied record
    per join. Raises `JLCutRefused` where a join cannot be built - an
    unknown join, a side without audio, a trimmed span carrying speech
    on the timeline as compiled (re-checked here, because state may
    have shifted since plan time), or a fill source with no measurable
    room.
    """
    clips = [dict(c) for c in v1_clips]
    by_position: dict = {}
    for clip in clips:
        pos = clip.get("spine_position")
        if pos is None:
            pos = _position_from_label(clip.get("label", ""))
        if pos is not None:
            by_position.setdefault(str(pos), []).append(clip)
    blocks = {str(b.get("position")): b for b in spine_blocks
              if isinstance(b, dict)}
    fills = []
    room_tone: dict = {}
    applied = []
    frame = 1.0 / float(fps)

    for entry in jl_entries or []:
        kind = normalise_kind(entry.get("kind"))
        if kind is None:
            raise _refuse(
                f"a J/L join names kind {entry.get('kind')!r}, which is "
                f"not a J/L kind",
                "only `j_cut` (ear early) and `l_cut` (ear lingers) "
                "reach the speech row.",
                "plan the join as `j_cut` or `l_cut`, or drop it.",
            )
        out_pos = str(entry.get("outgoing_position"))
        in_pos = str(entry.get("incoming_position"))
        try:
            audio_frame = int(entry["audio_cut_frame"])
        except (KeyError, TypeError, ValueError):
            audio_frame = int(round(float(entry["audio_cut_timeline"])
                                    * fps))
        try:
            boundary_frame = int(entry["picture_cut_frame"])
        except (KeyError, TypeError, ValueError):
            boundary_frame = int(round(float(entry["picture_cut_timeline"])
                                       * fps))
        lead = float(entry["lead_seconds"])
        out_clips = by_position.get(out_pos, [])
        in_clips = by_position.get(in_pos, [])
        if not out_clips or not in_clips:
            raise _refuse(
                f"the {kind} join {out_pos}->{in_pos} names a side "
                f"with no V1 clip",
                "both sides of a J/L join must reach V1 - there is no "
                "speech row to trim where no clip plays.",
                "re-plan the cut at a join between two V1 blocks, or "
                "drop it.",
            )
        # One clip per side: a join inside multi-segment blocks is a
        # join the picture cut itself cannot address, so it refuses
        # here rather than trimming one segment of several.
        if len(out_clips) != 1 or len(in_clips) != 1:
            raise _refuse(
                f"the {kind} join {out_pos}->{in_pos} lands inside "
                f"multi-segment V1 clips",
                "the picture cut addresses one boundary, and trimming "
                "one segment of several would move audio the cut does "
                "not own.",
                "re-plan the cut at a single-clip join, or drop it.",
            )
        outgoing, incoming = out_clips[0], in_clips[0]
        for side_clip, side_name in ((outgoing, "outgoing"),
                                     (incoming, "incoming")):
            if side_clip.get("video_only") or side_clip.get("bookend"):
                raise _refuse(
                    f"the {kind} join {out_pos}->{in_pos} has a "
                    f"{side_name} side ({side_clip.get('label', '?')}) "
                    f"with no speech audio",
                    "a silent card has no speech row to trim and no "
                    "words to keep.",
                    "re-plan the cut at a join between two speaking "
                    "blocks, or drop it.",
                )
            if not side_clip.get("source_file"):
                raise _refuse(
                    f"the {kind} join {out_pos}->{in_pos} has a "
                    f"{side_name} side naming no source file",
                    "without a source file there is no audio to trim "
                    "and no room to measure.",
                    "re-plan the cut at a join whose clips name their "
                    "sources, or drop it.",
                )
        out_block = blocks.get(out_pos, {})
        in_block = blocks.get(in_pos, {})

        if kind == "j_cut":
            trimmed, host_block = outgoing, out_block
            fill_source = incoming["source_file"]
        else:
            trimmed, host_block = incoming, in_block
            fill_source = outgoing["source_file"]
        audio_seconds = audio_frame / float(fps)
        boundary_seconds = boundary_frame / float(fps)
        # The speech recheck runs on the EXACT seconds - the plan's
        # intent, carried as `audio_cut_exact` - not on the quantized
        # placement frames: a word edge within half a frame of the
        # placed frame approved at plan time must not refuse here.
        # Entries without the exact key (hand-written, not resolved)
        # fall back to the placement seconds, strictly.
        try:
            exact_seconds = float(entry["audio_cut_exact"])
        except (KeyError, TypeError, ValueError):
            exact_seconds = audio_seconds
        gap = (min(exact_seconds, boundary_seconds),
               max(exact_seconds, boundary_seconds))
        sounding = words_in_span(host_block, gap[0] + 1e-9,
                                 gap[1] - 1e-9)
        if sounding:
            quoted = ", ".join(repr(w) for w in sounding[:6])
            raise _refuse(
                f"the {kind} join {out_pos}->{in_pos} trims "
                f"{gap[0]:.3f}-{gap[1]:.3f}s, where {quoted} sound"
                f"{'' if len(sounding) == 1 else 's'} on the compiled "
                f"timeline",
                "the compiled words moved under the plan - trimming "
                "them drops speech the spine promised.",
                "re-plan the cut from the current spine, or drop it.",
            )

        # Trim the audio range only: picture (`source_in/out`,
        # `timeline_in/out`, and both frame edges) is untouched, so
        # the eye still cuts at the boundary. The trim lands the
        # clip's audio edge EXACTLY on the audio-cut frame - seconds
        # are derived from frames, never the reverse, because the
        # half-frame slop in the seconds is what once left the fill
        # three frames from the incoming start. The builder already
        # places audio from `audio_src_in/out` when present
        # (defaulting to the picture range), so setting them here IS
        # the edit.
        audio_start_f, audio_end_f = _audio_edge_frames(trimmed, fps)[:2]
        if kind == "j_cut":
            if audio_frame <= audio_start_f:
                raise _refuse(
                    f"the {kind} join {out_pos}->{in_pos} moves the "
                    f"audio cut to frame {audio_frame}, at or before "
                    f"the tail starts (frame {audio_start_f}) - the "
                    f"offset erases or escapes its clip",
                    "the audio cut must sit inside the trimmed side's "
                    "own audio.",
                    "re-plan the cut with a shorter lead inside the "
                    "clip, or drop it.",
                )
            if audio_frame > audio_end_f:
                raise _refuse(
                    f"the {kind} join {out_pos}->{in_pos} moves the "
                    f"audio cut to frame {audio_frame}, past the tail "
                    f"end (frame {audio_end_f}) - the offset escapes "
                    f"its clip",
                    "the audio cut must sit inside the trimmed side's "
                    "own audio.",
                    "re-plan the cut with a shorter lead inside the "
                    "clip, or drop it.",
                )
            audio_end_src = float(trimmed.get("audio_src_out",
                                              trimmed["source_out"]))
            trimmed["audio_src_out"] = round(
                audio_end_src - (audio_end_f - audio_frame) / fps, 3)
        else:
            if audio_frame >= audio_end_f:
                raise _refuse(
                    f"the {kind} join {out_pos}->{in_pos} moves the "
                    f"audio cut to frame {audio_frame}, at or past "
                    f"the head end (frame {audio_end_f}) - the "
                    f"offset erases or escapes its clip",
                    "the audio cut must sit inside the trimmed side's "
                    "own audio.",
                    "re-plan the cut with a shorter lag inside the "
                    "clip, or drop it.",
                )
            if audio_frame < audio_start_f:
                raise _refuse(
                    f"the {kind} join {out_pos}->{in_pos} moves the "
                    f"audio cut to frame {audio_frame}, before the "
                    f"head starts (frame {audio_start_f}) - the "
                    f"offset escapes its clip",
                    "the audio cut must sit inside the trimmed side's "
                    "own audio.",
                    "re-plan the cut with a shorter lag inside the "
                    "clip, or drop it.",
                )
            audio_start_src = float(trimmed.get("audio_src_in",
                                                trimmed["source_in"]))
            trimmed["audio_src_in"] = round(
                audio_start_src + (audio_frame - audio_start_f) / fps, 3)

        # Fill the opened gap with the joining source's room tone. The
        # fill abuts the trim exactly (within a millisecond): coverage
        # of the speech row stays continuous, so no join drops to
        # digital silence.
        if fill_source not in room_tone:
            spans = _source_speech_spans(
                in_block if kind == "j_cut" else out_block)
            try:
                room_tone[fill_source] = measure_fn(fill_source, spans)
            except Exception as exc:
                if isinstance(exc, RenRefusal):
                    raise _refuse(
                        f"the {kind} join {out_pos}->{in_pos} needs "
                        f"room tone from "
                        f"{os.path.basename(fill_source)}: {exc.what}",
                        f"the fill IS the cut's second half - an "
                        f"unfillable gap is a gap left as digital "
                        f"silence. ({exc.why})",
                        exc.fix,
                    ) from exc
                raise
        record = room_tone[fill_source]
        fill_name = (f"roomtone_{out_pos}_{in_pos}_{kind}_"
                     f"{len(fills):02d}.wav")
        fill_path = os.path.join(stage_dir, "room_tone", fill_name)
        fill_start_frame = min(audio_frame, boundary_frame)
        fill_end_frame = max(audio_frame, boundary_frame)
        fill_seconds = (fill_end_frame - fill_start_frame) / fps
        staged = stage_fill_fn(fill_source, record, fill_seconds,
                               fill_path)
        fills.append({
            "source_file": staged["path"],
            "room_source_file": fill_source,
            "source_in": 0.0,
            "source_out": staged["fill_seconds"],
            "timeline_in": round(fill_start_frame / fps, 3),
            "timeline_out": round(fill_end_frame / fps, 3),
            "timeline_in_frame": fill_start_frame,
            "timeline_out_frame": fill_end_frame,
            "label": f"roomtone_fill_{out_pos}_{in_pos}_{kind}",
            "room_tone_fill": True,
            "fill_for": {"kind": kind, "outgoing_position": out_pos,
                         "incoming_position": in_pos},
            "level_dbfs": staged["level_dbfs"],
            "loops": staged["loops"],
        })
        applied.append({
            "kind": kind,
            "outgoing_position": out_pos,
            "incoming_position": in_pos,
            "picture_cut_timeline": round(boundary_frame / fps, 3),
            "picture_cut_frame": boundary_frame,
            "audio_cut_timeline": round(audio_frame / fps, 3),
            "audio_cut_frame": audio_frame,
            "lead_seconds": round(lead, 3),
            "method": entry.get("method", ""),
            "fill": fills[-1]["label"],
            "room_source": os.path.basename(fill_source),
            "room_level_dbfs": record["level_dbfs"],
        })

    _assert_row_coverage(clips, fills, fps)
    return {"v1_clips": clips, "fills": fills, "room_tone": room_tone,
            "applied": applied}


def _audio_edge_frames(clip: dict, fps: float) -> tuple:
    """The clip's AUDIO span in timeline frames, plus its source edges.

    Picture maps `source_in` to `timeline_in_frame` linearly, so the
    audio range maps by the same slope with its own endpoints (which
    equal the picture's until a J/L trim moves one). Returns
    (start_frame, end_frame, audio_src_in, audio_src_out).
    """
    src_in = float(clip["source_in"])
    src_out = float(clip["source_out"])
    aud_in_src = float(clip.get("audio_src_in", src_in))
    aud_out_src = float(clip.get("audio_src_out", src_out))
    start_f = (int(clip["timeline_in_frame"])
               + int(round((aud_in_src - src_in) * fps)))
    end_f = (int(clip["timeline_out_frame"])
             - int(round((src_out - aud_out_src) * fps)))
    return start_f, end_f, aud_in_src, aud_out_src


def _position_from_label(label: str):
    """Block position off a compiled clip label (`speech_3`, `hook_2`)."""
    text = str(label or "")
    tail = text.rsplit("_seg", 1)[0]
    parts = tail.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[1]
    return None


def _source_speech_spans(block: dict) -> list:
    """The block's word spans in SOURCE seconds, for room measurement."""
    spans = []
    for entry in block.get("word_timestamps") or []:
        if not isinstance(entry, dict):
            continue
        try:
            spans.append((float(entry["source_start"]),
                          float(entry["source_end"])))
        except (KeyError, TypeError, ValueError):
            continue
    return spans


def _assert_row_coverage(clips: list, fills: list, fps: float) -> None:
    """The speech row stays continuous across every J/L join, in frames.

    Each fill must abut a speech end at its head and a speech start
    at its tail EXACTLY - integer frame equality, no epsilon - overlap
    no speech span, and overlap no other fill. Frames, not seconds:
    the timeline places and links by frame, and seconds carry up to
    half a frame of slop - which is how a fill once ended three
    frames from the incoming start while the seconds compared equal,
    leaving real digital silence. This asserts the CONSTRUCTION, so
    a breach is a programming error (`ValueError`), never a plan
    refusal: the plan was already validated, and this proves the
    trim + fill kept coverage continuous - which is what stops any
    join dropping to digital silence.
    """
    spans = []
    for clip in clips:
        if clip.get("video_only"):
            continue
        start_f, end_f, _, _ = _audio_edge_frames(clip, fps)
        spans.append((start_f, end_f, str(clip.get("label", "?"))))
    for fill in fills:
        head = int(fill["timeline_in_frame"])
        tail = int(fill["timeline_out_frame"])
        if not any(stop == head for _, stop, _ in spans):
            raise ValueError(
                f"room-tone fill {fill['label']!r} starts at frame "
                f"{head}, abutting no speech end - the gap it "
                f"was staged for is not where the trim opened one.")
        if not any(start == tail for start, _, _ in spans):
            raise ValueError(
                f"room-tone fill {fill['label']!r} ends at frame "
                f"{tail}, abutting no speech start - the gap it "
                f"was staged for is not where the trim opened one.")
        for start, stop, label in spans:
            overlap = min(stop, tail) - max(start, head)
            if overlap > 0:
                raise ValueError(
                    f"room-tone fill {fill['label']!r} overlaps "
                    f"{overlap} frame(s) of speech {label!r} - the "
                    f"fill would double the room under words.")
    ordered = sorted((int(f["timeline_in_frame"]),
                      int(f["timeline_out_frame"]),
                      str(f["label"])) for f in fills)
    for (_, prev_out, prev_label), (next_in, _, _) in zip(ordered,
                                                          ordered[1:]):
        if prev_out > next_in:
            raise ValueError(
                f"room-tone fills overlap: {prev_label!r} runs past "
                f"the next fill's start.")
