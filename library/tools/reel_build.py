"""Cutting an approved reel onto its own timeline, bad takes removed.

The captain, approving the second batch: *"the only thing i would say is
to remove the bad takes out so that the timelines of the reels are the
finished cut"*.

What is CUT and what is only FLAGGED
------------------------------------
A retake is easy to see and hard to prove.  Measured over the sixteen
approved reels, every loose rule removed real content:

- **containment alone** drops "make sure you're writing about that"
  against "Make sure you're writing why you're better than a competitor"
  at 1.00, because a short phrase is wholly inside a longer sentence,
- **without a same-speaker test** it drops Craig's actual question
  against Akshita's answer echoing it - and worse, mic bleed puts her
  words on his track, so the two sides can BOTH read as Craig,
- **without a duration test** it drops a 4.3s line to keep a 0.5s
  fragment of the same sentence.

So the cut rule is deliberately narrow, and everything it is not sure
about is REPORTED rather than removed:

    same speaker, durations within DURATION_RATIO of each other,
    containment >= CUT_CONTAINMENT and Jaccard >= CUT_JACCARD,
    the second beginning within CUT_WINDOW_SECONDS of the first ending.

`redundant_takes` returns those.  `suspected_takes` returns the near
misses, which become MARKERS on the built timeline rather than edits -
the captain reviews in Resolve through
`library/tools/marker_feedback.py`, so a suspect belongs where they are
already looking.  A wrong cut is content they have to notice is missing;
a marker is one keystroke to act on.

DISTANT repeats are suspects, never cuts.  A pair that meets the CUT
text bars with agreeing durations but sits further apart than
CUT_WINDOW_SECONDS survives for exactly one reason - distance - and
distance alone cannot tell a callback from a retake, so widening the
window would cut deliberate restatement.  The suspect lane therefore
runs the same bars with no window and reports what only distance kept:
same speaker, over both bars, durations within DURATION_RATIO, gap past
CUT_WINDOW_SECONDS.  No new number: the window is the existing one,
read as the classifier rather than moved as the gate.
`tests/test_reel_distant_repeats.py`.

A TAKE IS REMOVED WHOLE OR NOT AT ALL
-------------------------------------
The unit of a cut is a RUN of repeated lines, not a pair.  A run with a
line the pairing test could not accept is left entirely alone and
REPORTED (`redundant_runs`, `refused_take_groups`), because removing
part of a take strands the rest where its own opening used to be - and
at the head of a span that fragment becomes the reel's first line.  The
rule has no number in it, and `assert_takes_are_whole` refuses a cut
list that breaks it whatever produced that list.
`docs/RULE_EVIDENCE.md#the-take-that-was-two-thirds-cut` has the
measurement, and what it does and does not change across the nineteen.

**The LATER take is kept.** A retake exists because the first attempt was
flubbed - reel 02's first is "stuffed all their keywords with H1 tags",
which is backwards, and its retake says it correctly. That is a
JUDGEMENT, so it is recorded per cut and reversible rather than silent.

Sync
----
A reel is built from KEEP RANGES over the master's own timebase, not by
copying clips and closing gaps per track.  Both picture tracks are cut
against the same ranges and laid down at the same running offset, so
removing a take cannot slide one speaker against the other.

The closing CTA comes from anywhere in the episode
--------------------------------------------------
A reel's body is ONE contiguous master window with its bad takes taken
out of it.  Its closer need not be next to it: `reel_ranges` appends the
moment's `call_to_action` range LAST, and `placements` lays the ranges
end to end at a running offset without caring how far apart they were on
the master.  That is not a new capability in the placement math - it has
always taken a list of ranges and never objected to a distant one - it is
only the first thing to hand it one.

Why this had to exist: the captain's format closes every reel on a
genuinely spoken call to action, and this episode says about six of them
in nineteen minutes.  While a reel was one window, "every reel ends on a
spoken CTA" and "about sixteen reels" could not both be true, and the
batch came out at three.  **The same CTA range may close any number of
reels** - nothing is copied or synthesised to do it, the same real clip is
placed again, which is an ordinary editing move.

Two things this deliberately is not:

- **Not a way to assemble a body.**  The captain rejected a batch that
  read as "two halves of different scripts combined".  `reel_ranges`
  takes ONE body window and at most ONE closer, so a collage is not
  expressible here rather than merely discouraged.
- **Not a chooser.**  Which passage is a good CTA, and which reel it
  suits, is the model's judgement and the captain's approval.  Nothing in
  this module scores, ranks or matches one.

The CTA range is placed WHOLE - `redundant_takes` is not run over it.  It
is a passage the plan named to the second and the captain approved; a
retake scan silently shortening the closer would be a worse failure than
leaving a repetition in a clip somebody chose deliberately.  Placed
whole is not unscanned, though: `closer_repeats` measures the closer
against itself and against the body ranges the reel actually plays, and
reports both - carried per moment by `reel_proposal.enrich` so the
model sees the echo while it can still pick another closer, and printed
by `rebuild_reels_in_project` so an operator sees it at build time.
A closer echoing the body is the reel playing those words twice; which
of the two readings stays is taste, so the report never shortens one.

`tests/test_reel_build.py`.
"""
from __future__ import annotations

import os

from library.tools.paths import REMOTION_DIR
from library.tools.frame_utils import span_frames
from library.tools.resolve_lock import assert_current_timeline
from library.tools.timeline_ingest import resolve_project_exactly
from library.tools.timeline_layout import (
    EXPLAINER,
    FRAME,
    SEMANTIC,
    TRANSITIONS,
    plan_layout,
)

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

CUT_CONTAINMENT = 0.75
CUT_JACCARD = 0.55
CUT_WINDOW_SECONDS = 30.0
DURATION_RATIO = 2.0
"""A cut needs all of these. Each one is here because dropping it removed
real content from the sixteen approved reels - see the module docstring.

`MIN_TAKE_SECONDS = 1.5` was a sixth condition and is GONE, 2026-09-05.
It skipped any pair where EITHER side was shorter than a second and a
half, and the protection the docstring claims for a duration test -
"without a duration test it drops a 4.3s line to keep a 0.5s fragment of
the same sentence" - is `DURATION_RATIO`'s, not its: a 4.3s line against
a 0.5s fragment is a ratio of 8.6 and was already refused.  What the
floor uniquely blocked was a pair where BOTH sides are short and their
durations agree, which is a retake WhisperX happened to segment finely
rather than anything a viewer would call short.

Measured on reel 03 of the captain's approved nineteen: three takes of
"search didn't change, the question changed, whoever AI understands best
gets the answer" inside forty seconds, its retakes segmented at 0.20s to
1.66s, the identical pair at 309.92/310.12 scoring containment 1.000 and
Jaccard 1.000 with a duration ratio of 1.30 - and `redundant_takes`
returned NOTHING, because every side was under the floor.  The proposal's
own word-stream detector reported the repeat at similarity 1.0 and the
build left it in.  A number with no reason of its own, standing between a
confidently detected repeat and the cut it asked for, is the hardcoded
threshold this pipeline does not have (AGENTS.md 10.5)."""

SUSPECT_CONTAINMENT = 0.60
"""Below the cut bar and above this, a marker is written instead."""

MIN_RANGE_SECONDS = 0.04
"""The shortest range `keep_ranges` will emit, and the same bound
`_is_removed` reads.

MECHANICAL, not taste: a frame at this project's 24000/1001 fps is
0.0417s, so a range under this places no picture at all. It was already
here as the bare literal `b - a > 0.04` in `keep_ranges`; naming it is
the whole change, so that "this segment leaves nothing placeable" and
"this range places nothing" ask one question rather than two."""

def reel_angles(master_clips) -> List[dict]:
    """One angle per master PICTURE row, in first-seen order.

    A reel inherits its angles from the master it is cut from: each
    master video row that carries picture (not a layer - rows carrying
    a singleton role's name like B-Roll or Subtitles are decoration,
    not cameras) becomes one a-roll row and one speech row on the reel.

    `key` is the master track index as a string, so a master audio
    clip joins its picture's angle by its own track index - on the
    captain's master V1/A1 are Akshita and V2/A2 are Craig. `label`
    is the master video row's own name, falling back to the clip
    speaker and then to V{index}: the reel's rows are named from the
    material, never invented.
    """
    order: List[int] = []
    names: Dict[int, str] = {}
    speakers: Dict[int, str] = {}
    for clip in master_clips or ():
        if getattr(clip, "track_type", "video") != "video":
            continue
        try:
            index = int(getattr(clip, "track_index", 0))
        except (TypeError, ValueError):
            continue
        if index <= 0 or _is_layer_row(getattr(clip, "track_name", "") or ""):
            continue
        if index not in order:
            order.append(index)
        track_name = (getattr(clip, "track_name", "") or "").strip()
        if track_name and index not in names:
            names[index] = track_name
        speaker = getattr(clip, "speaker", "") or ""
        if speaker and index not in speakers:
            speakers[index] = speaker
    out = []
    for index in order:
        label = names.get(index) or speakers.get(index) or f"V{index}"
        out.append({"key": str(index), "label": label,
                    "track_index": index})
    return out


def _angle_key(clip) -> str:
    """The reel-angle key of a master clip: its track index as a string.

    One spelling for the placement loop and the punch-in pass - two
    different readings of one track index are two chances to aim a
    punch-in at another speaker's shot."""
    try:
        return str(int(getattr(clip, "track_index", "")))
    except (TypeError, ValueError):
        return ""


def _is_layer_row(track_name: str) -> bool:
    """Whether a master row name is a LAYER, not a camera angle.

    Rows carrying the layout owner's singleton role names (B-Roll,
    Subtitles, Transitions, Explainer, Semantic, Frame, Motion
    Graphics, Generator Effects, Timed Text, Music, SFX, numbered or
    not) hold decoration, not picture-with-speech, so their clips
    must not become reel angles. Anything else - "Akshita", "Craig",
    even an unnamed "Video 1" - is a camera whose picture the reel
    carries.
    """
    stem = (track_name or "").strip()
    if not stem:
        return False
    from library.tools.timeline_layout import SINGLETON_NAMES
    if stem in SINGLETON_NAMES:
        return True
    for base in SINGLETON_NAMES:
        if stem.startswith(base + " "):
            rest = stem[len(base) + 1:]
            if rest.isdigit():
                return True
    return False


def reel_speech_name(master_clips, index: int, label: str,
                     channel: int) -> str:
    """What the reel calls one angle's speech row.

    The master audio row's own name where it has one ("Akshita CH1"),
    else the angle label plus the recorded program stream - the same
    "angle plus stream" shape the SOP's own example carries.
    """
    for clip in master_clips or ():
        if (getattr(clip, "track_type", "") == "audio"
                and getattr(clip, "track_index", None) == index):
            track_name = (getattr(clip, "track_name", "") or "").strip()
            if track_name and not _is_default_track_name(
                    track_name, "audio", index):
                return track_name
            break
    return f"{label} CH{channel}"


def _is_default_track_name(name: str, media_type: str, index: int) -> bool:
    """Resolve's own untouched row name - nobody organised that row."""
    stem = (name or "").strip()
    return (not stem or stem in ("Video", "Audio", "Subtitle")
            or stem == f"{media_type.capitalize()} {index}")


def catalog_program_channels(project_folder: str,
                             ) -> Tuple[Dict[str, int], Dict[str, str]]:
    """The RECORDED program stream per source file, from the catalog.

    Returns ({key: channel}, {key: refusal}) keyed by source path AND
    basename. The catalog is read through `pipeline_data.json`, the
    same route `reel_conformance_verifier._catalog_source_sizes`
    takes: the state file is where a step's output is guaranteed to
    have landed. A source whose catalog entry recorded a refusal is
    refused here too, with the catalog's own words. A project with no
    catalog (timeline-ingested) comes back empty and the master
    timeline below answers instead.
    """
    import json as _json

    channels: Dict[str, int] = {}
    refusals: Dict[str, str] = {}
    if not project_folder:
        return channels, refusals
    try:
        with open(os.path.join(project_folder, "pipeline_data.json"),
                  encoding="utf-8") as handle:
            state = _json.load(handle)
    except (OSError, ValueError):
        return channels, refusals
    entries = (((state.get("step_outputs") or {}).get("catalog") or {})
               .get("clip_catalog") or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source_file") or entry.get("path") or ""
        if not source:
            continue
        keys = {source, source.rsplit("/", 1)[-1]}
        selection = entry.get("program_stream") or {}
        try:
            channel = int(selection.get("channel"))
        except (TypeError, ValueError):
            channel = None
        if channel is not None:
            for key in keys:
                channels.setdefault(key, channel)
            continue
        streams = entry.get("audio_streams") or []
        if len(streams) == 1:
            # One stream is the program - the "single" basis, the same
            # reading `select_program_stream` gives it. Mechanical, not
            # taste: there is nothing else it could be.
            try:
                only = int(streams[0].get("channel", 1))
            except (TypeError, ValueError):
                only = 1
            for key in keys:
                channels.setdefault(key, only)
            continue
        refusal = entry.get("program_stream_refusal")
        if refusal:
            for key in keys:
                refusals.setdefault(key, str(refusal))
    return channels, refusals


def master_program_channels(master_timeline) -> Dict[str, int]:
    """The program stream per source file, read off the MASTER timeline.

    The master's own speech rows already carry only program audio -
    its builder deletes anything else on the spot - so the channel
    mapping on what is really there IS the recorded selection, for
    projects whose catalog predates recording one (or that have no
    catalog at all). A source whose items disagree, or whose mapping
    is unreadable, is left out rather than guessed at: the caller
    refuses it by name.
    """
    import json as _json

    if master_timeline is None:
        return {}
    try:
        audio_rows = master_timeline.GetTrackCount("audio") or 0
    except Exception:
        return {}
    per_source: Dict[str, set] = {}
    for index in range(1, audio_rows + 1):
        try:
            items = master_timeline.GetItemListInTrack("audio", index) or []
        except Exception:
            continue
        for item in items:
            try:
                mapping = _json.loads(
                    item.GetSourceAudioChannelMapping())
                channels = tuple((mapping.get("track_mapping", {})
                                  .get("1", {}).get("channel_idx", [])) or ())
            except Exception:
                continue
            if len(channels) != 1:
                continue
            try:
                pool_item = item.GetMediaPoolItem()
                path = (pool_item.GetClipProperty("File Path")
                        if pool_item is not None else "")
            except Exception:
                continue
            if path:
                per_source.setdefault(path, set()).add(channels[0])
                per_source.setdefault(path.rsplit("/", 1)[-1], set()).add(
                    channels[0])
    return {source: next(iter(seen)) for source, seen in per_source.items()
            if len(seen) == 1}


def resolve_reel_program_channels(angles: Sequence[dict],
                                  master_clips,
                                  project_folder: str = "",
                                  master_timeline=None,
                                  explicit: Optional[Dict[str, int]] = None,
                                  ) -> Dict[str, int]:
    """The program stream per reel angle, or a refusal naming the source.

    The catalog's recorded selection first, the live master timeline
    second (its rows already carry only program audio), an explicit
    per-angle map ahead of both - what a test passes, and the only
    override. Every source on an angle must resolve to ONE channel;
    a source nothing recorded, and an angle whose sources disagree,
    REFUSE rather than default: the mix is declared or measured,
    never stream 0 dressed as the mix.
    """
    explicit = dict(explicit or {})
    catalog, refused = catalog_program_channels(project_folder)
    live = master_program_channels(master_timeline)

    by_angle_files: Dict[str, set] = {}
    for clip in master_clips or ():
        if getattr(clip, "track_type", "") != "audio":
            continue
        try:
            key = str(int(getattr(clip, "track_index", 0)))
        except (TypeError, ValueError):
            continue
        source = getattr(clip, "source_file", "") or ""
        if source:
            by_angle_files.setdefault(key, set()).add(source)

    out: Dict[str, int] = {}
    for angle in angles:
        key = angle["key"]
        if key in explicit:
            out[key] = int(explicit[key])
            continue
        files = sorted(by_angle_files.get(key, set()))
        if not files:
            # No audio of this angle on the reel: nothing will be
            # checked against the channel, so no record is needed.
            # 1 is the placeholder the layout default would carry.
            out[key] = 1
            continue
        channels: Dict[str, int] = {}
        for source in files:
            short = source.rsplit("/", 1)[-1]
            channel = catalog.get(source, catalog.get(short))
            basis = "the catalog's recorded program stream"
            if channel is None:
                channel = live.get(source, live.get(short))
                basis = ("the master timeline's own speech rows, which "
                         "already carry only program audio")
            if channel is None:
                raise ReelBuildError(
                    f"REFUSING to build: no recorded program stream for "
                    f"{short} (angle {label_of(angles, key)!r}). The "
                    f"catalog records none"
                    + (f" - {refused.get(source, refused.get(short))}"
                       if refused.get(source, refused.get(short)) else
                       " and it predates stream recording")
                    + f", and the master timeline carries nothing to read "
                    f"it off. Declare audio.program_stream and re-run "
                    f"catalog_footage, or build from a master whose rows "
                    f"already carry program audio.")
            channels[source] = int(channel)
        distinct = set(channels.values())
        if len(distinct) != 1:
            raise ReelBuildError(
                f"REFUSING to build: angle {label_of(angles, key)!r} mixes "
                f"sources with different program streams - "
                + ", ".join(f"{s.rsplit('/', 1)[-1]} CH{c}"
                            for s, c in sorted(channels.items()))
                + ". One speech row carries one program stream.")
        out[key] = distinct.pop()
    return out


def label_of(angles: Sequence[dict], key: str) -> str:
    """An angle's label, for refusals. The key when nothing carries it."""
    for angle in angles:
        if angle.get("key") == key:
            return angle.get("label") or key
    return key


def reel_track_material(master_clips,
                        program_channels: Dict[str, int],
                        caption_spans=(),
                        has_transitions: bool = False,
                        has_explainer: bool = False,
                        has_semantic: bool = False,
                        has_frame: bool = False) -> dict:
    """The material `timeline_layout.plan_layout` answers with a plan.

    Angles come from the master's own picture rows (`reel_angles`) and
    every count from what this reel will place - a row exists because
    something goes on it, which is what makes "two speakers collapsed
    onto one row" and "blank rows with nothing on them" structurally
    impossible rather than fixed once. `caption_spans` are (start, end)
    in FRAMES, the unit the layout packs in.
    """
    angles = reel_angles(master_clips)
    if not angles:
        # No picture rows to inherit: the layout falls back to its
        # legacy single-camera pair, exactly as a manifest declaring
        # no angles does.
        return {
            "angles": [], "has_broll": False,
            "has_frame": False, "caption_spans": [tuple(s) for s in
                                                  (caption_spans or [])],
            "has_transitions": bool(has_transitions),
            "has_explainer": bool(has_explainer),
            "has_semantic": bool(has_semantic),
            "mg_spans": [], "has_generators": False,
            "timed_text_spans": [], "music_spans": [], "sfx_spans": [],
        }
    described = []
    for angle in angles:
        channel = int(program_channels.get(angle["key"], 1))
        described.append({
            "key": angle["key"],
            "label": angle["label"],
            "speech_name": reel_speech_name(
                master_clips, angle["track_index"], angle["label"],
                channel),
            "program_channel": channel,
        })
    return {
        "angles": described,
        "has_broll": False,
        "has_frame": bool(has_frame),
        "caption_spans": [tuple(s) for s in (caption_spans or [])],
        "has_transitions": bool(has_transitions),
        "has_explainer": bool(has_explainer),
        "has_semantic": bool(has_semantic),
        "mg_spans": [], "has_generators": False,
        "timed_text_spans": [], "music_spans": [], "sfx_spans": [],
    }


def _placed_channel(item) -> Optional[int]:
    """The single source channel a placed audio item carries, or None."""
    import json as _json

    try:
        mapping = _json.loads(item.GetSourceAudioChannelMapping())
        channels = list((mapping.get("track_mapping", {})
                         .get("1", {}).get("channel_idx", [])) or [])
    except Exception:
        return None
    return channels[0] if len(channels) == 1 else None


def _speech_row_uids(timeline, plan) -> dict:
    """{(audio row index): {item uids on it}} - one inventory snapshot."""
    out = {}
    for row in plan.speech_rows():
        try:
            items = timeline.GetItemListInTrack("audio", row.index) or []
        except Exception:
            items = []
        out[row.index] = {_item_uid(item) for item in items}
    return out


def sweep_placed_audio(timeline, plan, before: dict, target_row: int,
                       expected_channel: int, label: str):
    """Delete what one placement invented outside its row and channel.

    Measured on the live project: an explicit `mediaType: 2` append of
    an MXF's audio RETURNS one item and PLACES two - the program
    stream on the named row plus a non-program spill on the next audio
    row. Judging the call by its return keeps the spill; reading the
    rows back finds it. So everything this placement ADDED (row
    inventory after minus before) is checked, not just what the call
    returned: what sits on the target row carrying the angle's
    recorded program channel stays, everything else added is deleted
    on the spot and recorded. An item whose mapping cannot be read is
    KEPT and reported - an unreadable check must not delete speech,
    and it must not read as a passing one either.

    Returns (kept, deleted, unverified). `deleted` names the row each
    stray sat on, because a spill on the next row and a wrong stream
    on the right row are different failures with the same fix.
    """
    after_items: dict = {}
    for row in plan.speech_rows():
        try:
            items = timeline.GetItemListInTrack("audio", row.index) or []
        except Exception:
            items = []
        after_items[row.index] = list(items)
    kept, deleted, unverified = [], [], []
    for index, items in after_items.items():
        seen_before = before.get(index, set())
        for item in items:
            if _item_uid(item) in seen_before:
                continue
            channel = _placed_channel(item)
            if channel is None:
                unverified.append(f"{label} (A{index})")
                kept.append(item)
                continue
            if index == target_row and channel == expected_channel:
                kept.append(item)
                continue
            try:
                timeline.DeleteClips([item], False)
            except Exception as exc:
                deleted.append({"label": label, "row": index,
                                "expected_channel": expected_channel,
                                "placed_channels": [channel],
                                "removal": f"REFUSED ({exc})"})
                continue
            deleted.append({"label": label, "row": index,
                            "expected_channel": expected_channel,
                            "placed_channels": [channel]})
    return kept, deleted, unverified


def _timeline_span(item):
    try:
        return (item.GetStart(), item.GetEnd())
    except Exception:
        return None


def _linked_ids(item):
    try:
        return {i.GetUniqueId() for i in (item.GetLinkedItems() or [])}
    except Exception:
        return set()


def _item_uid(item):
    try:
        return item.GetUniqueId()
    except Exception:
        return None


def link_reel_groups(timeline, plan) -> dict:
    """Picture to speech, captions into the group - in ONE call per start.

    Every a-roll picture and every speech item starting on one frame,
    plus every caption falling inside any of those speeches' spans,
    link in a single `SetClipsLinked` call - because linking is
    exclusive, not additive: a second call sharing an item with the
    first BREAKS the first group rather than joining it (measured
    2026-09-09, and once more live: two same-start speeches sharing
    one collapsed picture row, where the second pair-call stole the
    picture out of the first group). Grouping by start frame makes a
    shared item structurally impossible: starts are distinct, so no
    two calls ever share one.

    A caption that falls inside speeches starting on different frames
    joins the first host in plan row order - the same order the
    verifier's own host lookup reads, so "the first host" is the same
    speech item on both sides of the gate. Every call is read back,
    not trusted.
    """
    record = {"link_groups": [], "caption_links": [], "warnings": []}

    speech_index = []  # (start, end, item, angle_key), plan row order
    for row in plan.speech_rows():
        try:
            items = timeline.GetItemListInTrack("audio", row.index) or []
        except Exception:
            items = []
        for item in items:
            span = _timeline_span(item)
            if span is None:
                continue
            speech_index.append((span[0], span[1], item, row.occupant))

    picture_starts: Dict[int, list] = {}
    for row in plan.aroll_rows():
        try:
            items = timeline.GetItemListInTrack("video", row.index) or []
        except Exception:
            items = []
        for item in items:
            span = _timeline_span(item)
            if span is None:
                continue
            picture_starts.setdefault(span[0], []).append(item)

    caption_row = plan.caption_row()
    caption_items = []
    if caption_row is not None:
        try:
            caption_items = (timeline.GetItemListInTrack(
                "video", caption_row.index) or [])
        except Exception:
            caption_items = []
    claimed_captions = set()

    starts: Dict[int, dict] = {}
    for start, end, speech, _angle_key in speech_index:
        bucket = starts.setdefault(start, {"speeches": [], "keys": []})
        bucket["speeches"].append((start, end, speech))
        bucket["keys"].append(_angle_key)
    # Starts in first-encounter order down the plan's rows, so the
    # caption claim below meets hosts in the verifier's own order.
    order: List[int] = []
    for start, _end, _speech, _angle_key in speech_index:
        if start not in starts or start in order:
            continue
        order.append(start)

    for start in order:
        bucket = starts[start]
        group = list(picture_starts.get(start, []))
        group.extend(speech for _, _, speech in bucket["speeches"])
        spans = [(s, e) for s, e, _ in bucket["speeches"]]
        for caption in caption_items:
            if _item_uid(caption) in claimed_captions:
                # Linking is EXCLUSIVE: a caption that joined one
                # group and is linked again afterwards breaks the
                # first group rather than joining the second. Both
                # mics are hot, so spans overlap - the first host
                # wins and the caption is never offered twice.
                continue
            span = _timeline_span(caption)
            if span is None:
                continue
            if any(s <= span[0] and span[1] <= e for s, e in spans):
                group.append(caption)
        if len(group) < 2:
            only = bucket["speeches"][0]
            key = bucket["keys"][0]
            speech_row = plan.speech_row_for_angle(key)
            row_name = speech_row.name if speech_row else key
            record["warnings"].append(
                f"Speech at {only[0]}-{only[1]} on {row_name} has no "
                f"picture starting on its frame; left unlinked")
            continue
        try:
            ok = timeline.SetClipsLinked(group, True)
        except Exception as exc:
            record["warnings"].append(
                f"Link at frame {start} raised {exc!r}")
            continue
        if not ok:
            record["warnings"].append(
                f"Link at frame {start} declined")
            continue
        # Read back EVERY member, not just the speech: a group that
        # formed half-way is a breakage wearing a pass.
        verified = True
        for member in group:
            want = {_item_uid(other) for other in group
                    if other is not member}
            want.discard(None)
            if want and not (want <= _linked_ids(member)):
                verified = False
                break
        if verified:
            record["link_groups"].append(
                {"speech_start": start, "members": len(group)})
            for caption in group:
                if caption in caption_items:
                    claimed_captions.add(_item_uid(caption))
                    span = _timeline_span(caption)
                    record["caption_links"].append(
                        {"caption_start": span[0] if span else start,
                         "caption_end": span[1] if span else start,
                         "speech_start": start,
                         "members": len(group)})
        else:
            record["warnings"].append(
                f"Link at frame {start} read back unlinked")
    return record


REEL_RESOLUTION = (1080, 1920)
"""Set EXPLICITLY on every reel timeline.

Measured in phase one: the PROJECT's own resolution is 3840x2160 and only
the existing timelines override it, so a timeline created through the API
inherits the horizontal UHD default. That is a silent wrong answer rather
than an error, and sixteen of them would be sixteen rebuilds."""


class ReelBuildError(RuntimeError):
    """A reel could not be built safely."""


@dataclass(frozen=True)
class Cut:
    """One take removed, and the one kept in its place."""

    dropped_start: float
    dropped_end: float
    dropped_text: str
    kept_start: float
    kept_end: float
    kept_text: str
    speaker: Optional[str]
    containment: float
    jaccard: float

    def as_dict(self) -> dict:
        return {
            "dropped_start": round(self.dropped_start, 2),
            "dropped_end": round(self.dropped_end, 2),
            "dropped_text": self.dropped_text,
            "kept_start": round(self.kept_start, 2),
            "kept_end": round(self.kept_end, 2),
            "kept_text": self.kept_text,
            "speaker": self.speaker,
            "containment": round(self.containment, 3),
            "jaccard": round(self.jaccard, 3),
            "kept": "the later take - a retake exists because the first "
                    "was flubbed",
        }


@dataclass(frozen=True)
class Blocked:
    """A repeated take the scan could not pair SAFELY.

    Same speaker, inside the same window, over the containment and
    Jaccard bars - and refused because the two readings differ in
    length by more than `DURATION_RATIO`.  That refusal is right on its
    own terms: it is what stops a 4.3s line being dropped to keep a
    0.5s fragment of it.

    It is recorded rather than discarded because the refusal is only
    safe in ISOLATION.  A blocked segment sitting in the middle of a run
    of repeated lines is not a pair the cutter declined to judge - it is
    a piece of a take the cutter is otherwise removing, and leaving it
    behind strands it (see `redundant_runs`)."""

    start: float
    end: float
    text: str
    matched_start: float
    matched_end: float
    matched_text: str
    speaker: Optional[str]
    containment: float
    jaccard: float
    duration_ratio: float

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            "matched_start": round(self.matched_start, 2),
            "matched_end": round(self.matched_end, 2),
            "matched_text": self.matched_text,
            "speaker": self.speaker,
            "containment": round(self.containment, 3),
            "jaccard": round(self.jaccard, 3),
            "duration_ratio": round(self.duration_ratio, 2),
            "refused_by": (f"the two readings differ in length by "
                           f"{self.duration_ratio:.2f}x, over "
                           f"DURATION_RATIO={DURATION_RATIO}"),
        }


def _pair_scores(a: dict, b: dict) -> Tuple[float, float]:
    from library.tools.reel_proposal import _content_words

    wa, wb = _content_words(a.get("text", "")), _content_words(b.get("text", ""))
    if not wa or not wb:
        return 0.0, 0.0
    shared = len(wa & wb)
    return shared / min(len(wa), len(wb)), shared / len(wa | wb)


def _segments_in(start: float, end: float, transcript: dict) -> List[dict]:
    from library.tools.reel_proposal import bound_segments

    return sorted(
        (s for s in bound_segments(transcript)
         if s["timeline_end"] > start and s["timeline_start"] < end),
        key=lambda s: s["timeline_start"])


def _scan(start: float, end: float, transcript: dict,
           containment_floor: float, jaccard_floor: float,
           enforce_shape: bool,
           window_seconds: Optional[float] = CUT_WINDOW_SECONDS,
           ) -> Tuple[List[Cut], List[Blocked]]:
    """Every pair the text bars accept, split by what the SHAPE test did.

    The two returns are the same scan seen twice: `cuts` are the pairs
    that passed every condition, `blocked` are the ones that read as the
    same sentence by the same speaker inside the same window and were
    refused ONLY because their durations disagree by more than
    `DURATION_RATIO`.

    A blocked pair used to be a `continue` and nothing else, which is
    how a run of three repeated lines came to have two of them cut and
    the third left standing (see `redundant_runs`).  Scoring now happens
    BEFORE the shape test so that a refusal can say what it refused;
    which pairs are CUT is unchanged, because a cut still needs both.

    `window_seconds=None` runs the same bars with no window at all.  Only
    the suspect lane asks that way: the cut lane always passes the
    window, so no distant pair can become a cut whatever else it meets.
    """
    inside = _segments_in(start, end, transcript)
    used, found, blocked = set(), [], []
    for index, first in enumerate(inside):
        if id(first) in used:
            continue
        held: Optional[Blocked] = None
        for second in inside[index + 1:]:
            if id(second) in used or first.get("speaker") != second.get("speaker"):
                continue
            if (window_seconds is not None
                    and second["timeline_start"] - first["timeline_end"]
                    > window_seconds):
                break
            da = first["timeline_end"] - first["timeline_start"]
            db = second["timeline_end"] - second["timeline_start"]
            if da <= 0 or db <= 0:
                continue
            containment, jaccard = _pair_scores(first, second)
            if containment < containment_floor or jaccard < jaccard_floor:
                continue
            ratio = max(da, db) / min(da, db)
            if enforce_shape and ratio > DURATION_RATIO:
                if held is None:
                    held = Blocked(
                        start=float(first["timeline_start"]),
                        end=float(first["timeline_end"]),
                        text=(first.get("text") or "").strip(),
                        matched_start=float(second["timeline_start"]),
                        matched_end=float(second["timeline_end"]),
                        matched_text=(second.get("text") or "").strip(),
                        speaker=first.get("speaker"),
                        containment=containment, jaccard=jaccard,
                        duration_ratio=ratio)
                continue
            used.add(id(first))
            used.add(id(second))
            found.append(Cut(
                dropped_start=float(first["timeline_start"]),
                dropped_end=float(first["timeline_end"]),
                dropped_text=(first.get("text") or "").strip(),
                kept_start=float(second["timeline_start"]),
                kept_end=float(second["timeline_end"]),
                kept_text=(second.get("text") or "").strip(),
                speaker=first.get("speaker"),
                containment=containment, jaccard=jaccard))
            held = None
            break
        if held is not None:
            blocked.append(held)
    return found, blocked


@dataclass(frozen=True)
class RedundantRun:
    """One uninterrupted run of repeated speech, and whether it can go.

    A run is a maximal chain of segments that are CONSECUTIVE in the
    span's own segment list, carry ONE speaker, and are every one of them
    a repeated take - one the scan either cut or blocked.  The chain
    breaks at the first segment that is neither, and at a change of
    speaker.  There is no time threshold in that definition and there
    must not be one: "nothing else was said in between" is a fact about
    the transcript, not a number somebody chose.

    `whole` is False when any member is `Blocked`, which is the only way
    a run can be partly removable.
    """

    segments: Tuple[dict, ...]
    cuts: Tuple[Cut, ...]
    blocked: Tuple[Blocked, ...]

    @property
    def whole(self) -> bool:
        return not self.blocked

    @property
    def withdrawn(self) -> bool:
        """Did holding this run whole actually take a cut away?

        A run with a blocked member and NO cut in it is the duration
        guard doing its ordinary job on a lone pair - nothing was going
        to be removed there and nothing is being withheld.  Only a run
        that has both is a cut this rule withdrew, and only those are
        reported, so the report accounts for exactly the difference the
        rule makes."""
        return bool(self.cuts) and bool(self.blocked)

    @property
    def start(self) -> float:
        return float(self.segments[0]["timeline_start"])

    @property
    def end(self) -> float:
        return float(max(s["timeline_end"] for s in self.segments))

    @property
    def speaker(self) -> Optional[str]:
        return self.segments[0].get("speaker")

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "speaker": self.speaker,
            "lines": [(segment.get("text") or "").strip()
                      for segment in self.segments],
            "would_have_cut": [cut.as_dict() for cut in self.cuts],
            "could_not_cut": [block.as_dict() for block in self.blocked],
            "why_nothing_was_cut": (
                f"{len(self.cuts)} of {len(self.segments)} lines in this "
                f"repeated run could be paired safely and "
                f"{len(self.blocked)} could not, so cutting it would have "
                f"removed part of a take and left the rest standing where "
                f"its own opening used to be. A take is removed WHOLE or "
                f"not at all, so this repetition is still in the reel."),
            "the_fix": (
                "decide which take to keep and redraw the span past the "
                "other, or leave the repetition in - the choice of take "
                "is a judgement and this is not the place that makes it"),
        }


def redundant_runs(start: float, end: float,
                   transcript: dict) -> List[RedundantRun]:
    """Every run of repeated speech inside a span, WHOLE or not.

    Why a run rather than a pair
    ---------------------------
    Measured on reel 03 of the captain's approved nineteen, span
    301.24-341.27s: Akshita says one sentence three times, and the
    transcript segments the first take as three consecutive lines -

        301.24-302.57  "Yeah, so search didn't change."
        302.63-303.45  "The question changed."
        303.55-306.40  "And whoever AI best understands, gets the answer."

    The first two paired with the second take at containment 1.000 and
    Jaccard 1.000 and were CUT.  The third paired with the second take's
    own third line at containment 1.000 and Jaccard 1.000 too, and was
    refused because 2.851s against 0.600s is a ratio of 4.75, over
    `DURATION_RATIO`.  So two thirds of a take were removed and its tail
    was left - and because the take was at the head of the span, that
    orphaned tail became the reel's FIRST LINE.  The reel opened on
    "And whoever AI best understands, gets the answer", the answer
    before the question, and the model's own written hook did not arrive
    until 3.41 seconds in.

    The guard was not wrong.  It answers a MECHANICAL question - are
    these two utterances the same sentence, safely enough to drop one -
    and 2.851s against 0.600s is exactly the shape it exists to refuse
    (`test_a_fragment_is_never_kept_over_a_full_line`).  What it must not
    do is decide, on its own, that two thirds of a take may go: whether
    what is left reads is a judgement, and this module does not hold it.

    So the unit of a cut is the RUN, and the rule has no number in it:

        **A repeated run is removed WHOLE or not at all.**

    A run with a blocked member is removed not at all, and is REPORTED
    instead - `refused_take_groups`, carried per moment by
    `reel_proposal.enrich` so the model that chose the span sees it while
    it can still redraw the span, and printed by
    `rebuild_reels_in_project` so an operator sees it at build time.
    """
    inside = _segments_in(start, end, transcript)
    cuts, blocked = _scan(start, end, transcript,
                          CUT_CONTAINMENT, CUT_JACCARD, True)
    cut_at = {round(c.dropped_start, 4): c for c in cuts}
    blocked_at = {round(b.start, 4): b for b in blocked}

    runs: List[RedundantRun] = []
    chain: List[dict] = []

    def close() -> None:
        if not chain:
            return
        runs.append(RedundantRun(
            segments=tuple(chain),
            cuts=tuple(cut_at[key] for key in
                       (round(float(s["timeline_start"]), 4) for s in chain)
                       if key in cut_at),
            blocked=tuple(blocked_at[key] for key in
                          (round(float(s["timeline_start"]), 4) for s in chain)
                          if key in blocked_at)))
        chain.clear()

    for segment in inside:
        key = round(float(segment["timeline_start"]), 4)
        if key not in cut_at and key not in blocked_at:
            close()
            continue
        if chain and chain[-1].get("speaker") != segment.get("speaker"):
            close()
        chain.append(segment)
    close()
    return runs


def refused_take_groups(start: float, end: float,
                        transcript: dict) -> List[dict]:
    """The repeated runs this span will NOT cut, and why.

    An empty list is a complete answer: every repetition the scan was
    going to cut, it could cut whole.  A non-empty one names a
    repetition that is STILL IN THE REEL, what part of it could have
    gone, what stopped the rest, and that redrawing the span is the way
    out.  Nothing here scores or rejects the moment - it reports
    (AGENTS.md 10.5).

    Only runs where a cut was actually WITHDRAWN appear.  A lone pair
    the duration guard refused is not a withheld cut and is not listed
    here; it is a near miss, and `suspected_takes` is where those live.
    """
    return [run.as_dict() for run in redundant_runs(start, end, transcript)
            if run.withdrawn]


def _overlaps(a_start: float, a_end: float,
              b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _is_removed(segment: dict, cuts: Sequence[Cut]) -> bool:
    """Does `cuts` leave nothing placeable of this segment?

    "Placeable" is `MIN_RANGE_SECONDS`, the bound `keep_ranges` already
    applies to every range it emits, so this asks the same question the
    range arithmetic answers rather than a second one of its own.
    """
    left = (float(segment["timeline_end"]) - float(segment["timeline_start"])
            - sum(_overlaps(float(segment["timeline_start"]),
                            float(segment["timeline_end"]),
                            cut.dropped_start, cut.dropped_end)
                  for cut in cuts))
    return left < MIN_RANGE_SECONDS


def assert_takes_are_whole(cuts: Sequence[Cut], start: float, end: float,
                           transcript: dict) -> None:
    """REFUSE a cut list that would remove part of a repeated run.

    Asked of the LIST ABOUT TO BE APPLIED, whatever produced it - the
    same reason `assert_deletion_scope` is not folded into
    `timelines_to_replace`.  A guard that is the selection restated
    cannot catch the selection being wrong, and `redundant_takes` has
    TWO producers: the pair scan, and the word-stream detector whose
    findings are appended after it.  This one re-derives the runs from
    the transcript and checks the cut list against them.

    **Why this rule cannot strand a fragment at the head of a reel.**  A
    reel opens on the first second its keep ranges retain.  A cut only
    ever removes segments, and after this guard every run a cut touches
    is removed entirely - so the segment that leads the reel is either
    the span's own first segment, untouched, or the first segment of
    whatever follows a wholly removed run: the start of another
    speaker's turn or of speech that is not a repetition at all.  A
    partly removed run is what leaves a tail with its own opening gone,
    and a partly removed run is not expressible once this passes.
    """
    for run in redundant_runs(start, end, transcript):
        touched = [segment for segment in run.segments
                   if any(_overlaps(float(segment["timeline_start"]),
                                    float(segment["timeline_end"]),
                                    cut.dropped_start, cut.dropped_end) > 0
                          for cut in cuts)]
        if not touched:
            continue
        left = [segment for segment in run.segments
                if not _is_removed(segment, cuts)]
        if left:
            raise ReelBuildError(
                f"REFUSING to cut: {len(touched)} line(s) of a "
                f"{len(run.segments)}-line repeated run "
                f"({run.start:.2f}-{run.end:.2f}s, {run.speaker}) would be "
                f"removed and {len(left)} would be left standing - "
                f"{[(segment.get('text') or '').strip()[:50] for segment in left]}. "
                f"A take is removed WHOLE or not at all: removing part of "
                f"one strands what is left where its own opening used to "
                f"be, which is how reel 03 came to open on the answer "
                f"before the question was asked. Either the whole run "
                f"goes, or none of it does and "
                f"`refused_take_groups` reports it.")


def redundant_takes(start: float, end: float, transcript: dict) -> List[Cut]:
    """Takes confident enough to REMOVE, and WHOLE enough to be coherent.

    Two producers, one rule.  The pair scan finds retakes the transcript
    segmented into separate lines; `reel_proposal.duplicate_takes` finds
    the ones it did not, off the word stream.  Both are then held to
    `redundant_runs`: a cut inside a run that cannot be removed whole is
    withdrawn, and `refused_take_groups` says so.
    """
    from library.tools.reel_proposal import duplicate_takes
    cuts, _blocked = _scan(start, end, transcript,
                           CUT_CONTAINMENT, CUT_JACCARD, True)

    for dt in duplicate_takes(start, end, transcript):
        seg1 = _segments_in(dt["first_start"], dt["first_end"], transcript)
        seg2 = _segments_in(dt["second_start"], dt["second_end"], transcript)
        if seg1 and seg2 and id(seg1[0]) == id(seg2[0]):
            # Evidence from reel 03 (301.2-341.3) and reel 05 (287, 792) confirms the earlier takes were aborted flubs and the later take is the completed thought.
            cuts.append(Cut(
                dropped_start=dt["first_start"],
                dropped_end=dt["first_end"],
                dropped_text=dt.get("first_text", "").strip(),
                kept_start=dt["second_start"],
                kept_end=dt["second_end"],
                kept_text=dt.get("second_text", "").strip(),
                speaker=seg1[0].get("speaker"),
                containment=dt.get("similarity", 0.0),
                jaccard=dt.get("similarity", 0.0)
            ))

    refused = [run for run in redundant_runs(start, end, transcript)
               if not run.whole]
    kept = [cut for cut in cuts
            if not any(_overlaps(cut.dropped_start, cut.dropped_end,
                                 run.start, run.end) > 0 for run in refused)]
    return sorted(kept, key=lambda c: c.dropped_start)


def suspected_takes(start: float, end: float, transcript: dict) -> List[Cut]:
    """Near misses, plus distant near-identicals. These become MARKERS, never edits.

    Two lanes, one return.  The loose lane is the old one: pairs inside
    the window under the cut bars, shape unenforced.  The distant lane is
    new: pairs past the window at the CUT bars with the shape enforced -
    the only reason those survive is distance, which is judgement, so
    they are surfaced rather than removed.  A distant pair touching audio
    a confident cut already drops is left out: that audio does not play,
    so reporting it would mark a repetition the viewer never hears."""
    cuts = redundant_takes(start, end, transcript)
    confident = {(c.dropped_start, c.kept_start) for c in cuts}
    dropped = {c.dropped_start for c in cuts}
    loose, _blocked = _scan(start, end, transcript,
                            SUSPECT_CONTAINMENT, 0.0, False,
                            CUT_WINDOW_SECONDS)
    out = [c for c in loose
           if (c.dropped_start, c.kept_start) not in confident]
    seen = {(c.dropped_start, c.kept_start) for c in out}
    distant, _blocked = _scan(start, end, transcript,
                              CUT_CONTAINMENT, CUT_JACCARD, True, None)
    for candidate in distant:
        key = (candidate.dropped_start, candidate.kept_start)
        if key in confident or key in seen:
            continue
        if candidate.dropped_start in dropped:
            continue
        if candidate.kept_start - candidate.dropped_end <= CUT_WINDOW_SECONDS:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def keep_ranges(start: float, end: float,
                cuts: Sequence[Cut]) -> List[Tuple[float, float]]:
    """The reel's span with each dropped take taken out of it.

    Ranges are over the MASTER's timebase and are applied to both picture
    tracks identically, which is what keeps the two speakers in sync.
    """
    ranges = [(start, end)]
    for cut in sorted(cuts, key=lambda c: c.dropped_start):
        out: List[Tuple[float, float]] = []
        for a, b in ranges:
            if cut.dropped_end <= a or cut.dropped_start >= b:
                out.append((a, b))
                continue
            if a < cut.dropped_start:
                out.append((a, cut.dropped_start))
            if cut.dropped_end < b:
                out.append((cut.dropped_end, b))
        ranges = out
    return [(a, b) for a, b in ranges if b - a > MIN_RANGE_SECONDS]


def subtract_interval_cuts(
        ranges: Sequence[Tuple[float, float]],
        intervals: Sequence[tuple]) -> List[Tuple[float, float]]:
    """Tuple-interval subtraction with `keep_ranges`' own sliver rule.

    The captain's keep exclusions arrive as plain `(start, end[, id])`
    intervals - deliberately NOT `Cut`s, because a `Cut` claims a kept
    take in place of the dropped one and a strike keeps no take. The
    arithmetic is the same shape, so it reads the same way, including
    the sliver rule: a remnant under `MIN_RANGE_SECONDS` is dropped
    rather than placed as a one-frame chirp, which is the defect class
    the captain's "random tiny audio bite" belongs to.
    """
    out = [(float(a), float(b)) for a, b in ranges]
    for interval in intervals or []:
        s, e = float(interval[0]), float(interval[1])
        if not e > s:
            continue
        nxt: List[Tuple[float, float]] = []
        for a, b in out:
            if e <= a or s >= b:
                nxt.append((a, b))
                continue
            if a < s:
                nxt.append((a, s))
            if e < b:
                nxt.append((e, b))
        out = nxt
    return [(a, b) for a, b in out if b - a > MIN_RANGE_SECONDS]


#: A kept remnant under this long, left behind by a strike's own edge,
#: cannot be placed: the F7 readability floor refuses picture and audio
#: items under 0.5s (12 frames), so building it is building a refusal.
ABSORB_REMNANT_SECONDS = 0.5


def _remnant_has_timed_words(start: float, end: float,
                             transcript: dict) -> object:
    """The first timed word sounding inside `[start, end)`, or None.

    Any timed word - bound or straddling: where something was really
    said the remnant carries speech, whatever the segment's source
    status. Silence, breath and room tone time nothing.
    """
    for segment in transcript.get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start and word_start < end and word_end > start:
                return str(word.get("word", ""))
    return None


def absorb_wordless_remnants(
        ranges: Sequence[Tuple[float, float]],
        intervals: Sequence[tuple],
        transcript: dict) -> tuple:
    """Fold a strike's own edge-dust back into the strike.

    Cutting at exact word boundaries can strand a sub-floor nub where
    the master clip starts just before the first word - Reel 09's
    LCATL0013 clip starts 0.28s before Craig's 'so', so striking from
    the word left a 6-frame picture+audio item the F7 floor refuses.
    A remnant under `ABSORB_REMNANT_SECONDS` that touches the cut that
    made it and carries NO timed words is absorbed: the cut extends
    over silence nobody can hear. One that carries speech REFUSES,
    naming the exclusion - extending over words would delete speech
    the captain never struck, and shrinking past them invents the
    boundary instead. Returns `(ranges, intervals)` with the intervals
    extended to what was actually cut, so the mid-word check and the
    operator both read the true edges.
    """
    out = [(float(a), float(b)) for a, b in ranges]
    grown = [(float(c[0]), float(c[1]),
              (str(c[2]) if len(c) > 2 else "")) for c in (intervals or [])]
    for index, (s, e, ident) in enumerate(grown):
        # Remnants this cut left BEHIND it (kept range ending where the
        # cut starts) and AHEAD of it (kept range starting where the
        # cut ends) - identified by the exact edge float the
        # subtraction wrote, so a short range from any other cause is
        # never swept up here.
        for side in ("behind", "ahead"):
            edge = s if side == "behind" else e
            span = None
            for a, b in out:
                if side == "behind" and b == edge and a < edge:
                    span = (a, b)
                elif side == "ahead" and a == edge and b > edge:
                    span = (a, b)
            if span is None:
                continue
            a, b = span
            if b - a >= ABSORB_REMNANT_SECONDS:
                continue
            spoken = _remnant_has_timed_words(a, b, transcript)
            if spoken is None:
                out = [(x, y) for x, y in out if (x, y) != span]
                s, e = (a, e) if side == "behind" else (s, b)
                grown[index] = (s, e, ident)
            else:
                raise ReelBuildError(
                    f"REFUSING to build: keep exclusion {ident!r} "
                    f"strands a {(b - a):.2f}s fragment "
                    f"({a:.2f}-{b:.2f}s) carrying the word "
                    f"{spoken!r} - placing it fails the readability "
                    f"floor, and cutting it would delete speech the "
                    f"captain never struck. Re-record the strike to "
                    f"include the fragment or start past it.")
    out = [(a, b) for a, b in out if b - a > MIN_RANGE_SECONDS]
    return out, grown


class ExclusionWipesBody(ReelBuildError):
    """A recorded strike covers this reel's whole body.

    Raised rather than building a closer-only timeline: a reel whose
    body the captain struck entirely is dropped WITH this reason, and
    the loop turns it into a skip rather than a batch-killing refusal.
    That is the build-time shape of the no-split rule - one reel in,
    zero or one out, never two, never an empty container.
    """


def exclusion_midword_edges(start: float, end: float,
                             intervals: Sequence[tuple],
                             transcript: dict) -> List[dict]:
    """Recorded-strike edges landing inside a timed word.

    The same question `midword_keep_edges` asks of take cuts, with one
    precedence the take lane never needs. An edge exactly ON a BOUND
    word's edge is clean even where a STRADDLING row spans it: a bound
    row anchors a real source clip, so its edge is ground truth about
    audible sound, while a straddling row has no source at all and is
    usually WhisperX bridging silence (`reel_proposal.bound_segments`
    - on this episode a single 'well' claims 34 seconds). Letting the
    phantom testify would refuse a cut at a real word boundary, which
    is the REAL defect wearing the check's clothes. Anything else
    strictly inside any timed word is refused like a take edge: each
    entry names the exclusion id, so the fix is re-recording that
    strike at word boundaries rather than redrawing any span.
    """
    words: List[Tuple[float, float, str]] = []
    bound_edges: List[float] = []
    for segment in transcript.get("segments") or ():
        bound = bool(segment.get("resolve_item_id"))
        for word in segment.get("words") or ():
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start:
                words.append((word_start, word_end, str(word.get("word", ""))))
                if bound:
                    bound_edges.extend((word_start, word_end))
    found = []
    for interval in intervals or []:
        s, e = float(interval[0]), float(interval[1])
        ident = str(interval[2]) if len(interval) > 2 else ""
        for edge in (s, e):
            if edge <= start or edge >= end:
                continue
            if any(abs(edge - known) <= 0.005 for known in bound_edges):
                continue
            for word_start, word_end, text in words:
                if word_start < edge < word_end:
                    found.append({
                        "edge": round(edge, 3),
                        "exclusion": ident,
                        "word": text,
                        "word_start": round(word_start, 2),
                        "word_end": round(word_end, 2),
                    })
                    break
    return found


def midword_keep_edges(start: float, end: float, transcript: dict,
                       cuts: Optional[Sequence[Cut]] = None) -> List[dict]:
    """Interior keep-range edges landing strictly inside a timed word.

    The cutter draws interior edges at dropped-take boundaries, which are
    transcript SEGMENT edges - and a segment edge can sit inside a word
    (R07's cut lands 0.02s into "Your"; R02's 3-frame sliver is shorter
    than the words it plays). The outer span edges are owned by
    `reel_proposal.snap_to_speech` and never appear here; every edge
    reported is one a cut drew, which the snap never touches.

    The predicate is the snap's own - strictly inside, bound or
    straddling words alike, untimed words unable to testify - so "this
    edge cuts a word" and "this boundary cuts a word" ask one question
    rather than two. An edge exactly ON a word edge is clean and stays.
    A transcript with no word timings yields no findings, never a pass
    it did not earn: there is nothing to measure against.

    REPORTS, never repairs. Widening an interior edge reinstates part of
    a take the cutter deliberately dropped; narrowing it drops more
    speech the cutter deliberately kept. Both change content, so the
    span is redrawn by whoever chose it - the model at selection time,
    the captain at review - and this names the edge and the word.
    """
    if cuts is None:
        cuts = redundant_takes(start, end, transcript)
    ranges = keep_ranges(start, end, cuts)
    if len(ranges) < 2:
        return []
    words: List[Tuple[float, float, str]] = []
    for segment in transcript.get("segments") or ():
        for word in segment.get("words") or ():
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start:
                words.append((word_start, word_end, str(word.get("word", ""))))
    edges: Dict[float, str] = {}
    for range_start, range_end in ranges:
        for when, side in ((range_start, "keep_start"),
                           (range_end, "keep_end")):
            if when == start or when == end:
                continue
            key = round(when, 6)
            if key not in edges:
                edges[key] = side
    found = []
    for edge in sorted(edges):
        for word_start, word_end, text in words:
            if word_start < edge < word_end:
                found.append({
                    "edge": edge,
                    "side": edges[edge],
                    "word": text,
                    "word_start": round(word_start, 2),
                    "word_end": round(word_end, 2),
                })
                break
    return found


def cta_range(moment) -> Optional[Tuple[float, float]]:
    """The moment's closing CTA range on the MASTER, or None.

    One place reads `call_to_action` off a moment, so a moment that does
    not carry the attribute at all - an older plan, a stand-in - reads as
    "no closer" rather than raising.

    **A closer must present two real NUMBERS, or it is not a closer.**
    That is not defensiveness about types: a bare `unittest.mock.MagicMock`
    answers every attribute with a truthy mock, and `float()` of one is
    1.0, so a moment stand-in that never mentioned a CTA otherwise reads
    as closing on the single second 1.00-1.00. `ReelMoment.from_dict`
    already coerces the real thing through `float()`, so anything
    reaching here that is not a number is a stand-in rather than a plan.
    """
    cta = getattr(moment, "call_to_action", None)
    if cta is None:
        return None
    start = getattr(cta, "timeline_start", None)
    end = getattr(cta, "timeline_end", None)
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        return None
    if not isinstance(end, (int, float)) or isinstance(end, bool):
        return None
    return (float(start), float(end))


def closer_repeats(moment, transcript: dict) -> List[dict]:
    """What the closer repeats - of itself, and of the body it closes.

    Two shapes, both REPORTED, neither applied.  The closer is placed
    WHOLE whatever this finds: a passage the plan named and the captain
    approved is never silently shortened, and a closer echoing the body
    may be a deliberate callback rather than a leftover take.  Which of
    the two readings stays is taste, so this names both ranges and leaves
    the choice to whoever chose them - the model at selection time (via
    `reel_proposal.enrich`), the captain at review, the operator at build.

    No number of its own: the text bars and `DURATION_RATIO` are the
    cut's, read here as "same enough to play twice".  The body side is
    only what the reel PLAYS - segments the cut lane drops are skipped,
    because a closer echoing audio that never plays is not a double
    play.  An empty list is a complete answer: the closer says nothing
    the reel already said.
    """
    closer = cta_range(moment)
    if closer is None:
        return []
    out: List[dict] = []
    for cut in _scan(closer[0], closer[1], transcript,
                     CUT_CONTAINMENT, CUT_JACCARD, True,
                     CUT_WINDOW_SECONDS)[0]:
        record = cut.as_dict()
        record["kind"] = "closer_repeats_itself"
        record["why_kept"] = (
            "the closer stutters, and the closer is placed whole - pick "
            "a tighter closer or redraw its range rather than letting "
            "this shorten it silently")
        out.append(record)
    cuts = redundant_takes(moment.timeline_start, moment.timeline_end,
                           transcript)
    surviving = [segment
                 for segment in _segments_in(moment.timeline_start,
                                             moment.timeline_end, transcript)
                 if not _is_removed(segment, cuts)]
    for body in surviving:
        for said in _segments_in(closer[0], closer[1], transcript):
            if body.get("speaker") != said.get("speaker"):
                continue
            containment, jaccard = _pair_scores(body, said)
            if containment < CUT_CONTAINMENT or jaccard < CUT_JACCARD:
                continue
            body_dur = (float(body["timeline_end"])
                        - float(body["timeline_start"]))
            said_dur = (float(said["timeline_end"])
                        - float(said["timeline_start"]))
            if body_dur <= 0 or said_dur <= 0:
                continue
            if max(body_dur, said_dur) / min(body_dur, said_dur) > DURATION_RATIO:
                continue
            out.append({
                "kind": "closer_echoes_body",
                "body_start": round(float(body["timeline_start"]), 2),
                "body_end": round(float(body["timeline_end"]), 2),
                "body_text": (body.get("text") or "").strip(),
                "closer_start": round(float(said["timeline_start"]), 2),
                "closer_end": round(float(said["timeline_end"]), 2),
                "closer_text": (said.get("text") or "").strip(),
                "speaker": body.get("speaker"),
                "containment": round(containment, 3),
                "jaccard": round(jaccard, 3),
                "why_kept": (
                    "the reel plays these words twice - once in the body, "
                    "once in the closer - and the closer is placed whole. "
                    "Keep the callback deliberately, or pick a closer that "
                    "says something the body has not said."),
            })
    return out


def reel_ranges(moment, transcript: dict,
                extra_cuts: Sequence[tuple] = ()) -> List[Tuple[float, float]]:
    """Every master range this reel plays, IN THE ORDER IT PLAYS THEM.

    The body first, with its bad takes cut out of it, and then the
    closing CTA - which may come from anywhere in the episode and is
    under no obligation to be adjacent to, or after, the body.

    This is the ONE place that order is spelled.  `build_reel_timeline`,
    the caption pass and the conformance verifier all call it, so a reel
    cannot be built to one order and checked against another.

    `extra_cuts` are the captain's recorded keep exclusions overlapping
    this moment's body, as `(start, end[, id])` intervals
    (`transcript_corrections.exclusion_cuts_for_span` - which is where
    the reason approval does not block them is written down). They cut
    the body exactly like take cuts do: one reel in, one reel out,
    fewer seconds, never two reels. Empty (the default) builds exactly
    what this built before, so every caller without a project in hand
    is untouched. A sub-floor nub the cut strands off a clip's lead-in
    is absorbed where it carries no timed words and refused where it
    carries speech (`absorb_wordless_remnants`). A strike covering the
    whole body raises `ExclusionWipesBody` - the loop drops that reel
    WITH the reason rather than building an empty timeline. A strike
    edge through a word refuses like a take edge, naming the exclusion
    to re-record. The closer is always placed whole: a strike
    overlapping it is not applied here, and the captain picks another
    closer.
    """
    cuts = redundant_takes(moment.timeline_start, moment.timeline_end,
                           transcript)
    # The cut list is checked against the transcript's own runs before it
    # becomes the reel's shape, so a producer that bypassed
    # `redundant_takes` cannot strand a fragment silently.  Take cuts
    # ONLY: a recorded strike is not a take, has no kept take, and must
    # never be judged by take-wholeness.
    assert_takes_are_whole(cuts, moment.timeline_start, moment.timeline_end,
                           transcript)
    ranges = keep_ranges(moment.timeline_start, moment.timeline_end, cuts)
    intervals = [(float(c[0]), float(c[1]),
                  (str(c[2]) if len(c) > 2 else ""))
                 for c in (extra_cuts or [])]
    if intervals:
        ranges = subtract_interval_cuts(
            ranges, [(s, e) for s, e, _ in intervals])
        ranges, intervals = absorb_wordless_remnants(
            ranges, intervals, transcript)
        if not ranges:
            ident = next((i for _, _, i in intervals if i), "")
            raise ExclusionWipesBody(
                f"keep exclusion {ident} covers this reel's whole body "
                f"({moment.timeline_start:.2f}-"
                f"{moment.timeline_end:.2f}s): nothing would play, so "
                f"this reel is dropped with the reason rather than "
                f"built as an empty timeline.")
        bad_strike = exclusion_midword_edges(
            moment.timeline_start, moment.timeline_end,
            [(s, e, i) for s, e, i in intervals], transcript)
        if bad_strike:
            first = bad_strike[0]
            raise ReelBuildError(
                f"REFUSING to build: keep exclusion "
                f"{first['exclusion']!r} ends at {first['edge']:.2f}s "
                f"inside the word {first['word']!r} "
                f"({first['word_start']:.2f}-{first['word_end']:.2f}s), so "
                f"the reel would play that word cut in half and then "
                f"jump. Re-record the strike at word boundaries - the "
                f"span itself is approved and stays as the captain drew "
                f"it.")
    # A cut edge that lands inside a word plays half a word and then
    # jumps - R07's "Your we-", R02's 125ms chirp. The snap owns the
    # OUTER span edges only, so an interior edge through a word reaches
    # here unrepaired, and placing it would author the defect the plan
    # is refused for upstream. Refused, naming the edge and the word,
    # so the span is redrawn past the take instead.
    bad = midword_keep_edges(moment.timeline_start, moment.timeline_end,
                             transcript, cuts)
    if bad:
        first = bad[0]
        raise ReelBuildError(
            f"REFUSING to build: a keep edge at {first['edge']:.2f}s "
            f"lands inside the word {first['word']!r} "
            f"({first['word_start']:.2f}-{first['word_end']:.2f}s), so "
            f"the reel would play that word cut in half and then jump. "
            f"A repair in either direction changes content - widening "
            f"reinstates part of a take the cutter dropped, narrowing "
            f"drops more speech it kept - so redraw the span past the "
            f"take instead.")
    closer = cta_range(moment)
    if closer is None:
        return ranges
    if closer[1] - closer[0] <= 0.04:
        raise ReelBuildError(
            f"the closer runs {closer[0]:.2f}-{closer[1]:.2f}s, under a "
            f"frame. Nothing can be placed from it.")
    if min(closer[1], moment.timeline_end) > max(closer[0],
                                                 moment.timeline_start):
        raise ReelBuildError(
            f"the closer ({closer[0]:.2f}-{closer[1]:.2f}s) overlaps its "
            f"own body ({moment.timeline_start:.2f}-"
            f"{moment.timeline_end:.2f}s), so the reel would play those "
            f"seconds twice and `reel_time` would map them to the first "
            f"copy only - leaving the second silently uncaptioned. "
            f"`validate_proposal` refuses this when the proposal is "
            f"WRITTEN; this is the same refusal at BUILD time, because a "
            f"plan is a file the captain edits and `read_proposal` does "
            f"not re-run validation.")
    return ranges + [closer]


def closer_seam(moment, ranges: Sequence[Tuple[float, float]]
                ) -> Optional[float]:
    """The REEL second at which the closer starts, or None if there is
    no closer.

    The one seam a caption card may not span. The seams a bad-take cut
    leaves are NOT this: those join speech the editor deliberately made
    contiguous, and a card reading across one is a sentence as spoken.
    A closer is a different passage of the episode, and a card joining
    the body's last words to its first would be a sentence nobody said.
    """
    if cta_range(moment) is None:
        return None
    return sum(range_end - range_start
               for range_start, range_end in ranges[:-1])


def _closer_seam_frame(moment, ranges: Sequence[Tuple[float, float]],
                       fps: float) -> Optional[int]:
    """`closer_seam` in FRAMES, on the same arithmetic the picture used.

    `closer_seam` sums range LENGTHS in seconds, which is the right unit
    for a caption card. A transition element is placed on a frame, and
    `placements` accumulates `round(end*fps) - round(start*fps)` per
    range - so the frame is computed that way here rather than by
    rounding the seconds, which drifts by a frame on a reel with enough
    ranges.
    """
    if closer_seam(moment, ranges) is None:
        return None
    total = 0
    for range_start, range_end in ranges[:-1]:
        total += int(round(range_end * fps)) - int(round(range_start * fps))
    return total


def reel_time(master_time: float,
              ranges: Sequence[Tuple[float, float]],
              at_end: bool = False,
              lead_seconds: float = 0.0) -> Optional[float]:
    """Where a MASTER second lands on the reel, or None if it was cut.

    A reel is its keep ranges laid end to end, so a caption timed against
    the master has to come through the same arithmetic the picture did -
    otherwise removing a bad take slides every caption after it out of
    sync with the speech it belongs to.

    The ranges are walked IN LIST ORDER and the first one containing the
    second wins, so a closing CTA range that sits earlier on the master
    than the body still maps to the END of the reel.  That holds because
    the ranges are DISJOINT: a CTA overlapping its own body is refused by
    `reel_proposal.validate_proposal` when the proposal is written and by
    `reel_ranges` when the reel is built, which is what stops one master
    second having two answers here.

    A range is half-open, `[start, end)`, which is the right reading for
    a word's START and the wrong one for its END. `snap_to_speech` lands
    a range end EXACTLY on a segment's `timeline_end`, which is exactly
    the last word's end - so the closing word of every range read the
    exclusive way falls outside every range and returns None. On a reel
    that closes on a CTA those are the words the whole feature exists to
    deliver. Pass `at_end=True` to read `(start, end]` instead.

    `lead_seconds` is what a HEAD full-frame card pushes the whole reel
    down by (`library/tools/full_frame_element.py`). It is added to the
    ANSWER and never to the membership test, because "is this master
    second played at all" is a question about the ranges and nothing
    else. A caller measuring inside the BODY - `reel_opening`, which asks
    which words fall in the reel's first three seconds of SPEECH - leaves
    it at zero on purpose: a card in front does not change which words
    open the talking.
    """
    cursor = 0.0
    for range_start, range_end in ranges:
        inside = (range_start < master_time <= range_end if at_end
                  else range_start <= master_time < range_end)
        if inside:
            return lead_seconds + cursor + (master_time - range_start)
        cursor += range_end - range_start
    return None


def placements(ranges: Sequence[Tuple[float, float]],
               clips: Sequence, fps: float,
               lead_frames: int = 0) -> List[dict]:
    """Where each master clip lands on the reel, in exact frames and seconds.

    One entry per (keep range, overlapping clip). `record` is the running
    offset on the REEL, so the ranges close up and both tracks move
    together. Math is done in frames to prevent rounding holes at cuts.

    `lead_frames` is what a HEAD full-frame card occupies before any
    footage plays. It is added to the record cursor and to nothing else -
    a card in front changes WHERE a clip lands, never which frames of it
    play - and it is a whole number of FRAMES rather than seconds so the
    card and the first clip abut exactly. A one-frame rounding gap here
    is an F1 black hole (`reel_conformance_verifier`), which is why the
    lead enters the picture arithmetic here and nowhere else.
    """
    out: List[dict] = []
    cursor_frames = int(lead_frames)
    for range_start, range_end in ranges:
        range_start_f = int(round(range_start * fps))
        range_end_f = int(round(range_end * fps))
        range_frames = range_end_f - range_start_f
        
        for clip in clips:
            clip_start_f = int(round(clip.timeline_start * fps))
            clip_end_f = int(round(clip.timeline_end * fps))
            
            overlap_start_f = max(clip_start_f, range_start_f)
            overlap_end_f = min(clip_end_f, range_end_f)
            if overlap_end_f - overlap_start_f <= 0:
                continue
                
            into_clip_f = overlap_start_f - clip_start_f
            clip_source_in_f = int(round(clip.source_in * fps))
            
            # Keep seconds for backward compatibility, but provide exact snapped frames
            source_in_f = clip_source_in_f + into_clip_f
            source_out_f = source_in_f + (overlap_end_f - overlap_start_f)
            record_f = cursor_frames + (overlap_start_f - range_start_f)
            
            out.append({
                "clip": clip,
                "source_in": source_in_f / fps,
                "source_out": source_out_f / fps,
                "record": record_f / fps,
                "snapped_record": record_f,
                "track_index": clip.track_index,
                "speaker": clip.speaker,
            })
        cursor_frames += range_frames
    return out


class _SegmentsWithEntries(list):
    """A list of rendered segments that also carries the plan's entries.

    ``reel_subtitle_segments`` returns rendered segments (overlay paths)
    for timeline building, but the provenance hash must digest the plan's
    CAPTION ENTRIES (text, start, length) and the FOOTAGE BINDING (which
    clips they were computed against).  This subclass is a plain list
    everywhere an iterable or list is expected, so
    ``build_reel_timeline`` and test mocks are unaffected, while the
    build loop can read ``.caption_entries`` for the caption hash and
    ``.spine`` for the footage binding hash.
    """

    def __init__(self):
        super().__init__()
        self.caption_entries = []
        self.spine = None


def reel_subtitle_segments(moment, transcript: dict, ranges, project_folder: str,
                           fps: float, width: int, height: int,
                           timeline_name: str = "",
                           lead_seconds: float = 0.0) -> list:
    """Caption one reel THROUGH THE PIPELINE'S OWN STEPS.

    The captain's ruling of 2026-09-04: `reel_subtitles.py` should never
    have existed, and "whatever funcitonality was put into that python
    file should have been augmented into the pipeline". This is that
    augmentation, and it adds no caption logic of its own:

        reel_spine.spine_for_reel   the reel's own audio spine, in reel time
        subtitles.plan              step 4.01 groups and styles the cards
        subtitles.render_segment    step 4.05 renders each one

    Both steps are reached through the operation registry, so this is a
    named operation being driven rather than a script reimplementing a
    step. Neither step knows a reel from a master - the spine is the only
    thing that differs, which is the whole point of the producer.

    Per-speaker styling is 4.01's, resolved from the spine's own speakers.
    This function names no speaker: the list that used to live here was
    hardcoded to one series' two hosts.
    """
    import sys

    from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
        generate_subtitle_props_per_block,
    )
    from library.tools import operations
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_spine import spine_for_reel

    # What Resolve will CALL this reel.  It is the segment name's
    # `timeline` component (library/tools/subtitle_segment_id.py), and
    # that component is what stops one reel's overlay overwriting
    # another's - so a build into a different container must caption
    # into different files, or it silently rewrites the overlays the
    # timeline it is NOT touching is still pointing at.
    name = timeline_name or moment.timeline_name

    # A reel that cannot be spined is REPORTED and built without
    # captions, not allowed to abort the other eighteen. The reason is
    # printed rather than swallowed: a reel silently shipping with no
    # subtitles is the defect this whole change exists to fix.
    from library.tools.reel_spine import ReelSpineError
    try:
        spine = spine_for_reel(moment, transcript, ranges,
                               lead_seconds=lead_seconds)
    except ReelSpineError as why:
        print(f"  {name}: NO CAPTIONS - {why}",
              file=sys.stderr)
        return []
    if spine.get("bleed_blocks_dropped"):
        print(f"  {name}: dropped "
              f"{spine['bleed_blocks_dropped']} mic-bleed block(s)",
              file=sys.stderr)
    # A row the transcriber split mid-sentence is too short to carry a
    # legible card and is given back to its sentence upstream of the
    # grouping.  Both halves are SAID: how many were rejoined, and the
    # text of every one that could not be - the latter is a card that
    # will flash, and it is named rather than left to a warning three
    # steps later.
    if spine.get("fragment_blocks_merged"):
        print(f"  {name}: rejoined "
              f"{spine['fragment_blocks_merged']} mid-sentence row(s) "
              f"to their own sentence", file=sys.stderr)
    for text in spine.get("fragment_blocks_unmerged") or []:
        print(f"  {name}: SHORT BLOCK {text!r} - under the caption floor "
              f"and nothing contiguous on the side its sentence runs, so "
              f"its card will be short", file=sys.stderr)
    # Speech this reel PLAYS that no block carries, in reel seconds. The
    # honest outcome for a row nothing can bind is that the build SAYS
    # which seconds go uncaptioned, rather than something guessing a
    # clip for it.
    for span in spine.get("unbindable_spans") or []:
        print(f"  {name}: NO CAPTION over reel "
              f"{span['reel_start']:.2f}-{span['reel_end']:.2f}s "
              f"({span['seconds']:.2f}s of {span['speaker']}'s words, "
              f"master {span['master_start']:.1f}-{span['master_end']:.1f}) "
              f"- no clip carries them", file=sys.stderr)

    plan = operations.get("subtitles.plan").run(
        spine, brand_effect={}, brand_style={}, project_folder=project_folder)
    plan_entries = (plan.get("subtitle_plan") or {}).get(
        "subtitle_entries") or []
    # The render fps IS the timeline fps, exactly - never int(round()).
    # Rounding 24000/1001 to 24 renders 24fps media that Resolve time-maps
    # onto the 23.976 timeline, and the fractional frame is lost: every
    # caption segment landed one frame short (15 x F2 delta -1 on the
    # 2026-09-08 rebuild) while the verifier correctly expects
    # round(span * 24000/1001). Same-fps media maps 1:1 under any snap,
    # so the placed length equals the expected one exactly. Remotion
    # renders fractional fps faithfully (measured: 24000/1001 in, true
    # 24000/1001 ProRes out), and full-frame cards already pass it through.
    props_list = generate_subtitle_props_per_block(
        plan["subtitle_plan"], fps=fps, width=width, height=height,
        audio_spine=spine)
    if not props_list:
        result = _SegmentsWithEntries()
        result.caption_entries = plan_entries
        result.spine = spine
        return result

    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.SUBTITLE_SEGMENTS, step="render_subtitles"))
    render = operations.get("subtitles.render_segment")
    # The project's declared carrying, resolved once for the reel. A
    # project that declares nothing renders full-canvas video - today's
    # path, byte for byte.
    from library.tools.overlay_mode import (
        resolve_overlay_container,
        resolve_overlay_geometry,
    )
    overlay_geometry = resolve_overlay_geometry(project_folder or None)
    overlay_container = resolve_overlay_container(project_folder or None)
    segments = _SegmentsWithEntries()
    segments.caption_entries = plan_entries
    segments.spine = spine
    for index, props in enumerate(props_list, 1):
        # `reuse=True`: the cache-and-pair. A rebuild re-derives the same
        # props for an unchanged caption, and the segment name (speaker +
        # timeline + source span, `subtitle_segment_id`) plus the recorded
        # reuse key (props digest + renderer fingerprint, step 4.05) are
        # the per-card identity `caption_content_hash` cannot supply - it
        # digests the whole card list into one per-reel hash, so it can
        # never key a per-card cache. A caption whose text, timing, style
        # or renderer changed re-renders; one that did not is paired back
        # to the file already on disk instead of orphaning it with a fresh
        # render. The plain-run default stays False (opt-in, never
        # flipped) - this call site is the explicit opt-in.
        rendered = render.run(props, out_dir, name,
                              progress=f"[{index}/{len(props_list)}]",
                              reuse=True,
                              overlay_geometry=overlay_geometry,
                              overlay_container=overlay_container,
                              project_folder=project_folder)
        if rendered is None:
            continue
        # A FAILED render is a record of what went wrong, not a segment:
        # it carries no `source_in_frame`, so appending it reached the
        # placer and died there with a KeyError three functions away
        # from the cause. REFUSED by name and with the renderer's own
        # reason, at the point of knowledge - a reel missing a caption
        # card is a defective reel, and F2/NO-REFERENCE would only say
        # so later and with less pointing at it.
        if rendered.get("provenance") == "failed":
            raise ReelBuildError(
                f"{name}: caption segment "
                f"{rendered.get('segment_id', '?')!r} did not render: "
                f"{rendered.get('failure') or 'no reason recorded'}")
        segments.append(rendered)
    return segments


def declared_cards(project_folder: str) -> list:
    """The full-frame element declarations this project carries.

    ``[]`` when it declares none, which is every project unless someone
    opted in - the same shape ``content.bookends`` and
    ``effect.timed_text_overlay`` take.  Malformed RAISES, here, before a
    single timeline is created: a declaration that renders nothing is
    indistinguishable from no declaration at all
    (``library/tools/full_frame_element.py``).

    Both sources are asked and the PROJECT wins, because a card is copy
    the viewer reads and copy the viewer reads is artwork (AGENTS.md 14).
    """
    from library.tools.brand_registry import (
        query_slots, resolve_project_template)
    from library.tools.full_frame_element import (
        declared_elements, resolve_declaration)

    brand_effect = {}
    project_yaml = os.path.join(project_folder, "project.yaml")
    if os.path.exists(project_yaml):
        import yaml
        with open(project_yaml, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        named = ((config.get("pipeline") or {}).get("brand_template") or "")
        if named:
            brand_effect = query_slots(
                resolve_project_template(named), "effect")
    return declared_elements(resolve_declaration(brand_effect, project_folder))


def plan_cards(moment, transcript: dict, ranges, project_folder: str,
               fps: float, declarations=None) -> list:
    """Resolve this project's card declarations against ONE reel.

    Returns ``[]`` when nothing is declared.  The facts a bound run
    quotes are measured off `ranges` - the ranges the build is about to
    place - so a card quotes the reel that will exist rather than the
    span the plan asked for.
    """
    from library.tools import full_frame_element as ffe

    declarations = (declared_cards(project_folder) if declarations is None
                    else declarations)
    if not declarations:
        return []
    facts = ffe.ReelFacts.from_moment(
        moment, ranges, transcript,
        opening_seconds=ffe.required_opening_window(declarations))
    # In FRAMES, and by the SAME arithmetic `placements` uses per range
    # edge, so a tail card starts on the frame after the last clip ends
    # rather than a rounding away from it.
    body_frames = sum(int(round(end * fps)) - int(round(start * fps))
                      for start, end in ranges)
    # The ranges and the transcript travel too, and only a span reads
    # them: its segments anchor to the keep ranges one by one, so the
    # boundaries sit on the reel's own edit points.  Cards never read
    # them - a card's duration is declared, not measured.
    return ffe.plan_reel_cards(declarations, facts, body_frames, fps,
                               project_folder=project_folder,
                               ranges=list(ranges), transcript=transcript)


def lead_frames(cards, fps: float) -> int:
    """How many frames the HEAD cards occupy before any footage plays.

    In FRAMES, and summed from each card's own already-rounded frame
    count rather than from the seconds - a lead computed in seconds and
    rounded once leaves a one-frame gap between the last card and the
    first clip on some durations, and a one-frame gap is an F1 black
    hole.

    A span contributes nothing here: it does not precede the footage, it
    replaces it for the whole body (`build_reel_timeline` suppresses the
    footage video where a span plays), so there is no lead to shift
    anything by.
    """
    return sum(card.duration_frames for card in (cards or ())
               if card.placement == "head")


def card_render_dir(project_folder: str) -> str:
    """Where this project's rendered cards live."""
    from library.tools.full_frame_element import FULL_FRAME_RENDER_DIRNAME
    return os.path.join(project_folder, "pipeline_output", "scratch",
                        FULL_FRAME_RENDER_DIRNAME)



def reel_explainer_segments(moment, transcript: dict, ranges,
                            project_folder: str, fps: float, width: int,
                            height: int, judgement=None,
                            brand_effect=None, timeline_name: str = ""):
    """One reel's animated explainer, THROUGH THE PIPELINE'S OWN STEPS.

    Exactly the shape :func:`reel_subtitle_segments` has, for the same
    reason and with the same discipline: it adds no graphics logic of
    its own, it drives named operations, and the modules it calls are
    the ones the master path already calls.

        reel_quality_bar.played_speech      the reel's lines, in reel time
        explainer_plan.author_explainer     parts -> stages -> plan entries
        motion_graphics_plan.resolve_plan   step 4.06's OWN resolver
        motion_graphics_plan.plan_segments  step 4.06's OWN clusterer
        motion_graphics.render_segment      step 4.06's OWN renderer

    `played_speech` is the SAME function step 3.05's pre-bridge builds
    its `lines` table with, called here rather than respelled: the words
    a stage is anchored against must be the words the judge read, or one
    field of one reading would ground for one reader and not the other.

    Returns `(segments, plan)`.  `plan` is returned even when nothing is
    drawn, because a reel with no explainer must be able to say WHICH
    kind of nothing it has - `explainer_plan.BASES` is the enumeration
    and `pipeline_output/review/explainer_plans.json` is where the build
    records it for the verifier to grade against.
    """
    import sys

    from library.tools import explainer_plan as ex
    from library.tools import motion_graphics_plan as mg
    from library.tools import operations
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_quality_bar import played_speech
    from library.tools.safe_area import resolve_safe_area

    name = timeline_name or moment.timeline_name

    declaration = ex.resolve_declaration(brand_effect or {}, project_folder)
    if declaration is None:
        return [], ex.ExplainerPlan(reel_name=name, declared=False,
                                    basis=ex.NOT_DECLARED)

    # The reel's own lines and its own length, from its own ranges.
    # WITH WORDS: a stage is anchored to the word its quote begins on.
    # Without them six sources enumerated in one breath sit in two
    # transcript segments, so all six land on two instants and the
    # build stops being a build. The words never reach a prompt - this
    # is the build reading them, not the judge (AGENTS.md 10.1).
    lines = [{"at": line["reel_start"], "speaker": line["speaker"],
              "says": line["text"], "words": line.get("words") or []}
             for line in played_speech(moment, transcript, with_words=True)]
    reel_seconds = sum(max(0.0, end - start) for start, end in (ranges or []))

    bands = _explainer_bands(project_folder, width, height)
    plan = ex.author_explainer(
        reel_name=name, reel_number=int(moment.number),
        reel_seconds=reel_seconds, judgement=judgement,
        declaration=declaration, lines=lines, bands=bands)

    # Every refusal is SAID, on the run that made it. A stage silently
    # dropped is a claim the viewer is shown half of.
    for refusal in ((plan.anchored.refused if plan.anchored else []) or []):
        print(f"  {name}: EXPLAINER STAGE REFUSED "
              f"({refusal['reason']}) {refusal['stage']!r} - "
              f"{refusal['detail']}", file=sys.stderr)
    if plan.band and not plan.band["available"]:
        print(f"  {name}: explainer band {plan.band['band']!r} has zero "
              f"height on this frame - the picture leaves nothing there",
              file=sys.stderr)
    if plan.band and plan.band["covers_picture"]:
        print(f"  {name}: explainer sits OVER the picture "
              f"({plan.band['picture_covered_fraction']:.1%} of the frame)",
              file=sys.stderr)
    if not plan.entries:
        print(f"  {name}: NO EXPLAINER - {plan.basis}", file=sys.stderr)
        return [], plan

    resolved = mg.resolve_plan(
        plan.entries, timeline_duration=reel_seconds, fps=fps,
        palette_roles={}, asked=True)
    for dropped in resolved.dropped:
        print(f"  {name}: explainer entry dropped ({dropped.reason}) "
              f"{dropped.element}: {dropped.detail}", file=sys.stderr)
    if not resolved.moments:
        plan.basis = ex.NOTHING_TO_DRAW
        return [], plan

    # The box the graphic is POSITIONED in is the declared band, not the
    # whole safe area. `explainer_plan.band_insets` says why that needs
    # no new drawing code. Where the bands could not be measured at all
    # the platform's own safe area is used and the run SAYS so, rather
    # than a rectangle being guessed.
    if bands is not None:
        insets = ex.band_insets(bands, plan.band["band"])
    else:
        print(f"  {name}: explainer positioned in the whole safe area - "
              f"the source size is not in this project's catalog, so the "
              f"picture bands could not be measured", file=sys.stderr)
        insets = resolve_safe_area(project_folder=project_folder,
                                   width=width, height=height).as_props()
    segments_plan = mg.plan_segments(
        resolved.moments, fps=fps, width=width, height=height,
        safe_area=insets)

    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.MOTION_GRAPHICS_SEGMENTS, step="render_motion_graphics"))
    render = operations.get("motion_graphics.render_segment")
    segments = []
    for index, planned in enumerate(segments_plan):
        # The project travels so the renderer reads the project's OWN
        # `motion_graphics_overlay_geometry` declaration
        # (`library/tools/overlay_mode.py`) - a project declaring tight
        # gets tight boxes here exactly as the master pass and the
        # semantic-visual half do, and a project declaring nothing
        # renders full canvas as before. The geometry itself stays
        # unresolved (None) so an explicit value still wins and the
        # declaration is read live, per render.
        rendered = render.run(
            planned, out_dir,
            segment_name=ex.segment_name(name, index),
            progress=f"[{index + 1}/{len(segments_plan)}]",
            project_folder=project_folder)
        if rendered is None:
            continue
        # LOOK AT WHAT WAS DRAWN. A graphic too big for its band is not
        # drawn outside the frame, it is drawn CUT OFF, and the file is
        # a valid picture of the right size that nothing downstream can
        # tell apart. An error here REFUSES the segment rather than
        # placing it; a warning is said and placed.
        try:
            measured = ex.measure_render(rendered["overlay_path"])
        except ex.ExplainerError as why:
            print(f"  {name}: explainer render could not be measured - "
                  f"{why}", file=sys.stderr)
            measured = None
        if measured is not None:
            rendered["measured"] = measured
            refused = False
            for finding in ex.render_findings(
                    measured, bands, {"band": plan.band["band"]}
                    if plan.band else {"band": "over"}):
                print(f"  {name}: EXPLAINER {finding['severity'].upper()} "
                      f"({finding['code']}) {finding['message']}",
                      file=sys.stderr)
                refused = refused or finding["severity"] == "error"
            if refused:
                continue
        segments.append(rendered)
    # What was RENDERED, not what was intended: F21 grades the timeline
    # against this, so a segment the renderer refused must not appear
    # here or the check would look for an item nothing placed.
    plan.segments = list(segments)
    if not segments:
        plan.basis = ex.NOTHING_TO_DRAW
    return segments, plan


def _explainer_bands(project_folder: str, width: int, height: int):
    """The picture-area enumeration for a reel of this project.

    Built from the two measurements that already exist - the rectangle a
    reel really delivers (`reel_framing`) and the platform's keep-clear
    insets (`safe_area`). Returns None where the source size is unknown,
    which is REPORTED by the caller rather than guessed at: a band
    computed from an assumed source size would be a confident wrong
    rectangle.
    """
    from library.tools import explainer_plan as ex
    from library.tools.reel_framing import IDENTITY, delivered_picture
    from library.tools.safe_area import resolve_safe_area

    source = _reel_source_size(project_folder)
    if source is None:
        return None
    picture = delivered_picture(source[0], source[1], width, height,
                                dict(IDENTITY))
    insets = resolve_safe_area(project_folder=project_folder,
                               width=width, height=height)
    return ex.picture_bands(picture.rect, width, height, insets)


def _reel_source_size(project_folder: str):
    """The display size of the footage a reel is cut from, or None.

    `reel_conformance_verifier._catalog_source_sizes` is the reader -
    the same one F12 already grades framing with, imported rather than
    respelled, so a reel's bands and its framing check can never
    disagree about what the source is.

    Reels are cut from ONE master shoot, so one size answers for the
    batch.  A catalog that disagrees with itself returns None rather
    than picking one: a band computed from an assumed source size is a
    confident wrong rectangle, and the caller REPORTS the absence.
    """
    from library.tools.reel_conformance_verifier import _catalog_source_sizes
    from library.tools.reel_framing import display_size

    sizes = set()
    for entry in (_catalog_source_sizes(project_folder) or {}).values():
        width, height = entry.get("width"), entry.get("height")
        if not width or not height:
            continue
        try:
            sizes.add(display_size(int(width), int(height),
                                   int(entry.get("rotation") or 0)))
        except Exception:
            continue
    if len(sizes) != 1:
        return None
    return sizes.pop()


def _source_frame_size(item):
    """(width, height) of a placed item's source, or None.

    Read off the media pool item's own `Resolution` property rather than
    the catalog: the reels path serves projects that were ingested from
    a Resolve timeline and have no catalog entry for their footage.
    None means Resolve could not say, and a caller that needs a size to
    aim a crop must refuse rather than assume one.
    """
    pool_item = item.GetMediaPoolItem() if hasattr(
        item, "GetMediaPoolItem") else None
    if pool_item is None:
        return None
    raw = pool_item.GetClipProperty("Resolution") or ""
    if "x" not in str(raw):
        return None
    try:
        parts = str(raw).lower().split("x")
        return (int(parts[0]), int(parts[1]))
    except (TypeError, ValueError):
        return None


def pool_item_for(pool, filepath: str):
    """The media pool item for *filepath*, or None if it is not there yet.

    Searched by FULL PATH through every folder, which is the identity
    `apply_fusion_comps._map_clips_to_items` matches on too - a basename
    match would pair a reel's overlay with another reel's file of the
    same name.

    Every import in this module goes through :func:`import_pool_item`
    below, which asks this FIRST.  It did not, and the cost was
    measured on 2026-09-09: the media pool's "not placed on any
    timeline" bin held 96 copies of 12 motion-graphic files - eight of
    each, one per build attempt - because every build re-imported files
    that were already there.  A rebuild is the normal way to work on a
    reel, so the pool grew by the whole overlay set every time; the bin
    held 1,210 items before the project was reset.
    """
    def _search(folder):
        for item in folder.GetClipList() or ():
            if item.GetClipProperty("File Path") == filepath:
                return item
        for sub in folder.GetSubFolderList() or ():
            found = _search(sub)
            if found:
                return found
        return None

    return _search(pool.GetRootFolder())


def import_pool_item(pool, filepath: str):
    """The pool item for *filepath*, imported only if it is not there.

    Returns None when the import itself failed, so every caller keeps
    judging the call by what it RETURNS (AGENTS.md 5).
    """
    existing = pool_item_for(pool, filepath)
    if existing is not None:
        return existing
    items = pool.ImportMedia([filepath])
    return items[0] if items else None


def place_overlay_segments(pool, project, timeline, name: str, fps: float,
                           segments, track_index: int, kind: str,
                           check: str, properties: dict = None,
                           project_folder: str = "") -> None:
    """Place rendered overlay segments onto one upper video track.

    One placer for the explainer track and the semantic-visual track:
    both append full-canvas graphics whose whole span is placed
    (`total_frames` IS the content, no handles either side), and two
    placers doing the same arithmetic are two chances to land one
    frame off.

    Video ONLY (`mediaType: 1`): the rendered overlay carries a silent
    audio stream, and without this Resolve silently drops the whole
    append - R09's first vox build placed nothing on V6 while every
    other video append in this module already passed it. Both the
    import and the append are judged by what they RETURN (AGENTS.md 5)
    and REFUSED as `ReelBuildError` rather than skipped: an unplaced
    segment the record claims is a build the conformance gate
    (`check`, F21/F22) refuses, so carrying on would only fail later
    with less pointing at the cause.

    A segment under scratch/ is refused before anything is imported
    (`reel_placed_assets.assert_placeable`): a file a timeline points
    at must live somewhere a cleaner may not throw away, and scratch/
    is exactly what a cleaner throws away. Pass `project_folder` and
    the check runs; without it there is nothing to check against.

    A tight segment carries its own `tight_box.placement` (Scaling=1,
    then Pan/Tilt - `library/tools/overlay_placement.py`), applied to
    the placed item through the same helper the caption loop uses; a
    full-canvas segment (`tight_box` None) needs no transform. A tight
    canvas without its transform is a small clip Resolve centres on
    the delivery frame, nowhere near the union it was computed from.
    """
    import sys

    from library.tools.overlay_placement import apply_placement_transform
    from library.tools.reel_placed_assets import assert_placeable

    for segment in segments or []:
        if project_folder:
            assert_placeable(segment["overlay_path"], project_folder)
        item = import_pool_item(pool, segment["overlay_path"])
        if item is None:
            raise ReelBuildError(
                f"{name}: Resolve would not import the rendered {kind} "
                f"{segment['overlay_path']!r}")
        assert_current_timeline(project, timeline)
        record_frame = int(round(segment["timeline_start"] * fps))
        placed = pool.AppendToTimeline([{
            "mediaPoolItem": item,
            "startFrame": 0,
            # EXCLUSIVE, the same reading every other placement here
            # uses. An inclusive endFrame leaves a one-frame gap, which
            # is a black hole F1 reports.
            "endFrame": segment["total_frames"],
            "mediaType": 1,
            "trackIndex": track_index,
            "recordFrame": record_frame,
        }])
        if not placed:
            raise ReelBuildError(
                f"{name}: Resolve would not place the rendered {kind} "
                f"{segment['overlay_path']!r} on V{track_index} - "
                f"AppendToTimeline returned nothing, and an unplaced "
                f"segment the record claims is a build {check} refuses")
        # A transform the caller declares for this whole track - today
        # only the TV frame's cover zoom. Judged by what SetProperty
        # RETURNS, because a frame that silently kept zoom 1.0 is the
        # letterboxed band all over again.
        for key, value in (properties or {}).items():
            for item in (placed if isinstance(placed, list) else []):
                if hasattr(item, "SetProperty") and not item.SetProperty(
                        key, value):
                    raise ReelBuildError(
                        f"{name}: Resolve refused {key}={value} on the "
                        f"{kind} at {segment['timeline_start']:.2f}s")
        # Where a tight clip lands. REPORTED, never raised - the clip
        # IS on the timeline, and failing the build over a movable
        # graphic would trade a misplaced one for a missing one (the
        # same discipline `overlay_placement` keeps for captions).
        note = apply_placement_transform(
            timeline, track_index, record_frame,
            (segment.get("tight_box") or {}).get("placement"),
            label=f"{kind} at {segment['timeline_start']:.2f}s")
        if note:
            print(f"  {name}: {note}", file=sys.stderr)


def build_reel_timeline(project, moment, master_clips, subtitle_segments, fps, width, height, project_folder, transcript, timeline_name: str = "", cards=None, overlay_placements=None, explainer_segments=None, semantic_segments=None, look=None, motion=None, master_timeline=None, program_channels=None, extra_cuts: Sequence[tuple] = ()):
    """Place one reel.  `timeline_name` is what Resolve will CALL it.

    Defaults to `moment.timeline_name`, which is the plan's own name and
    what every build did before `built_name` existed.  A caller that
    passes something else is building the same reel into a different
    container - see `built_name`.

    `cards` are the RENDERED full-frame elements this reel contains, each
    carrying the file it was rendered to
    (`library/tools/full_frame_element.py`).  They go on the first
    picture row, which is a picture track and not a layer above one: a
    full-frame element REPLACES picture for its stretch rather than
    overlaying it, so it is inside the same hole check, item count and
    framing check every other picture item is.  A HEAD card pushes all
    the footage down by `lead_frames`, which is the same number
    `reel_subtitle_segments` was given, so picture and captions move
    together or not at all.  A SPAN covers the whole body instead: the
    footage video is suppressed where it plays and the spine audio
    stays, so the reel is an animated cut over its own speech.

    `overlay_placements` are transition elements laid OVER the reel's own
    cuts, from `library/tools/transition_overlay.py`.  They are ADDITIVE:
    the picture and the captions below are placed identically whether
    there are none or ten, because an element hides a cut rather than
    consuming frames from either side of it - see
    `transition_overlay.TIMING_IS_ADDITIVE`.  None or an empty list
    places nothing AND adds no track, so a project that declares no
    element gets no transitions row - which is the same "declare
    nothing and get nothing" shape every other effect slot has.

    `look` is the project's resolved TV-frame declaration
    (`library/tools/reel_look.py`) or None.  Under it each angle's
    picture keeps its own row - one row per speaker, the way the speech
    rows already are (captain's ruling on Reel 09, 2026-09-09) - and
    plays at the declared punch-in, the frame asset spans each run of
    picture on the frame row above them, and the switch animation and
    any planned drift are applied afterwards as Fusion comps - by the
    caller, in its own process (AGENTS.md 5).  `motion` is the reel's
    RESOLVED drift plan, carried here only so the manifest that pass
    reads can be built from the placements this function really made.
    None for either is the timeline this function built before they
    existed.

    `master_timeline` is the live master this reel is cut from, and is
    how the builder reaches the recorded program stream on projects
    whose catalog predates stream recording (or that have no catalog):
    the master's own speech rows already carry only program audio.
    `program_channels` ({angle_key: channel}) overrides both routes -
    what a test passes.  Either way every angle's stream is resolved
    BEFORE the timeline is created, and an unresolvable one refuses
    the build rather than placing a default.

    Returns the build record: the track plan as placed, what stream
    enforcement removed, what the link pass joined, which empty rows
    were deleted, and which master clips were skipped - so the
    conformance proof is gradeable without re-deriving any of it.
    """
    import sys, os

    name = timeline_name or moment.timeline_name

    # The reel's shape is computed BEFORE anything is created, so a
    # refusal - a mid-word keep edge, an unresolvable program stream -
    # fires before a timeline exists rather than leaving half of one.
    # `extra_cuts` are the captain's recorded strikes for this moment:
    # the placer reads the SAME ranges the caption pass planned from,
    # so picture and captions cannot disagree about what plays.
    ranges = reel_ranges(moment, transcript, extra_cuts=extra_cuts)
    lead = lead_frames(cards, fps)
    placements_list = placements(ranges, master_clips, fps, lead_frames=lead)

    # ── The track plan: the material asks, timeline_layout answers ──
    # Every track index and name below comes from this plan. A-roll
    # gets one video row per master picture row and speech one audio
    # row per angle, named from the master's own rows - under the
    # TV-frame look too, where the frame row sits above the picture
    # rows it dresses (captain's ruling on Reel 09, 2026-09-09); the
    # reel's own additive rows (transitions, explainer, semantic, and
    # the frame under the look) arrive as roles, not hardcoded indices
    # - so there is exactly one thing that decides a track index.
    angles = reel_angles(master_clips)
    resolved_channels = resolve_reel_program_channels(
        angles, master_clips, project_folder,
        master_timeline=master_timeline, explicit=program_channels)
    caption_spans = [
        (int(round(s["timeline_start"] * fps)),
         int(round(s["timeline_end"] * fps)))
        for s in (subtitle_segments or [])
        if s.get("timeline_end", 0) > s.get("timeline_start", 0)]
    material = reel_track_material(
        master_clips, resolved_channels,
        caption_spans=caption_spans,
        has_transitions=bool(overlay_placements),
        has_explainer=bool(explainer_segments),
        has_semantic=bool(semantic_segments),
        has_frame=look is not None)
    track_plan = plan_layout(material)
    video_row_by_angle = {}
    for angle in angles:
        row = track_plan.video_row_for_angle(angle["key"])
        if row is not None:
            video_row_by_angle[angle["key"]] = row.index
    speech_row_by_angle = {
        angle["key"]: track_plan.speech_row_for_angle(angle["key"]).index
        for angle in angles
        if track_plan.speech_row_for_angle(angle["key"]) is not None}

    build_record: dict = {
        "timeline_name": name,
        "track_plan": track_plan.serializable(),
        "stream_enforcement": {"checked": 0, "deleted": [],
                               "unverified": []},
        "link_groups": [], "caption_links": [], "link_warnings": [],
        "deleted_empty_tracks": [], "skipped_clips": [],
    }

    pool = project.GetMediaPool()
    timeline = pool.CreateEmptyTimeline(name)
    if not timeline:
        raise ValueError(f"Failed to create timeline {name}")

    project.SetCurrentTimeline(timeline)

    timeline.SetSetting("useCustomSettings", "1")
    timeline.SetSetting("timelineResolutionWidth", "1080")
    timeline.SetSetting("timelineResolutionHeight", "1920")

    # The plan's rows, and only those. A row exists because the plan
    # put something on it; occupancy is enforced after placement, so
    # a row whose placements all fail is DELETED, never kept blank.
    while timeline.GetTrackCount("video") < len(track_plan.video_tracks):
        timeline.AddTrack("video")
    while timeline.GetTrackCount("audio") < len(track_plan.audio_tracks):
        timeline.AddTrack("audio")

    # Names come from the plan, which named them from the material, and
    # they go on BEFORE placement: a row the plan did not name is an
    # error, not a fallback, and there is no "Video 1" anywhere
    # downstream of the plan.
    for spec in track_plan.video_tracks + track_plan.audio_tracks:
        if not timeline.SetTrackName(spec.media_type, spec.index,
                                      spec.name):
            raise ReelBuildError(
                f"{name}: Resolve would not name {spec.media_type} row "
                f"{spec.index} {spec.name!r} - an unnamed row means "
                f"nothing organised it, so the build stops rather than "
                f"placing onto defaults.")

    # A span IS the picture for the whole body, so the footage video it
    # replaces is not placed: two pictures on V1 would be an overlap, not
    # a composite, and an overlay hiding the footage would leave its
    # sound playing underneath - the unimplementable shape
    # `full_frame_element` was written to refuse.  The spine's AUDIO
    # stays - a span covers the reel's seconds, it does not silence
    # them - which is what makes the reel an animated cut over its own
    # speech rather than a silent card.  Said where the operator is
    # looking, because a reel whose footage went nowhere should never
    # read as a reel that lost it.
    span_present = any(getattr(card, "placement", "") == "span"
                       for card in (cards or ()))
    if span_present:
        print(f"  {name}: full-frame span covers the body - footage video "
              f"suppressed, spine audio kept", file=sys.stderr)

    # The cards FIRST, so the timeline reads in play order, and on V1.
    # Rendered into scratch as the renderer's own cache, so promoted
    # into the durable REEL_CARDS area before anything is imported: a
    # timeline that points under scratch/ points at files a cleaner may
    # throw away (library/tools/reel_placed_assets.py).
    from library.tools.reel_placed_assets import (
        assert_placeable, promote_cards,
    )
    cards = promote_cards(cards, project_folder)
    for card in (cards or ()):
        path = getattr(card, "rendered_path", "") or ""
        if not path or not os.path.isfile(path):
            raise ReelBuildError(
                f"{name}: full-frame card {card.render_name!r} was planned "
                f"but its file is missing ({path!r}). A declared card that "
                f"does not reach the timeline leaves the reel starting on "
                f"speech, which is exactly what it looks like when nothing "
                f"was declared at all.")
        assert_placeable(path, project_folder)
        card_item = import_pool_item(pool, path)
        if card_item is None:
            raise ReelBuildError(
                f"{name}: Resolve would not import the rendered card "
                f"{path!r}")
        assert_current_timeline(project, timeline)
        pool.AppendToTimeline([{
            "mediaPoolItem": card_item,
            "startFrame": 0,
            # EXCLUSIVE, and this is MEASURED rather than assumed
            # (AGENTS.md 5: judge a Resolve call by what it RETURNS).
            # Written `duration_frames - 1` on the first build, the card
            # came back off the timeline as [0..52) - 52 frames for a
            # 53-frame plan - and the first clip started at 53, leaving a
            # one-frame black hole that F1 reported and F13 named. It is
            # the same exclusive reading the footage placement below uses
            # for `source_out`.
            "endFrame": card.duration_frames,
            "mediaType": 1,
            "trackIndex": track_plan.aroll_rows()[0].index,
            # The card's own integer frame, never `round(seconds * fps)`.
            "recordFrame": card.reel_start_frame,
        }])
    
    # The footage lookup is `pool_item_for` now, module level, because
    # every import in this module has to ask the same question and a
    # nested copy could only ever answer it for footage.



    # ── Picture and speech, one angle per row ──
    # Each clip rides the plan's row for its own angle - a video clip
    # the a-roll row, an audio clip the speech row - never the master's
    # raw track index. Audio is placed explicitly (`mediaType: 2` plus
    # the row) and read back: anything that is not the angle's
    # recorded program stream is deleted on the spot and recorded,
    # which is what stops the non-program bleed. A clip from a master
    # row that is no angle (a layer, a bed) is skipped and said, not
    # misplaced: a reel plays its angles' picture and speech.
    for p in placements_list:
        c = p["clip"]
        if span_present and c.track_type == "video":
            continue
        angle_key = _angle_key(c)
        if c.track_type == "video":
            dest_row = video_row_by_angle.get(angle_key)
            kind = "picture"
        else:
            dest_row = speech_row_by_angle.get(angle_key)
            kind = "speech"
        if dest_row is None:
            note = (f"{name}: skipping {kind} from master "
                    f"{c.track_type}{getattr(c, 'track_index', '?')} "
                    f"({getattr(c, 'source_file', '?').rsplit('/', 1)[-1]}) "
                    f"- that row is no reel angle, and a reel plays its "
                    f"angles' picture and speech")
            print(f"  {note}", file=sys.stderr)
            build_record["skipped_clips"].append(note)
            continue
        pool_item = pool_item_for(pool, c.source_file)
        if not pool_item:
            print(f"Source file {c.source_file} not in media pool", file=sys.stderr)
            continue

        pool_fps_str = pool_item.GetClipProperty("FPS") or str(fps)
        pool_fps = float(pool_fps_str)

        assert_current_timeline(project, timeline)

        # The row inventory BEFORE, so the sweep below can tell what
        # this append added: an explicit audio append returns one item
        # and can place two, the program stream plus a non-program
        # spill on the next row.
        before = _speech_row_uids(timeline, track_plan)
        pool.AppendToTimeline([{
            "mediaPoolItem": pool_item,
            "startFrame": int(round(p["source_in"] * pool_fps)),
            "endFrame": int(round(p["source_out"] * pool_fps)),
            "mediaType": 1 if c.track_type == "video" else 2,
            "trackIndex": dest_row,
            "recordFrame": p["snapped_record"]
        }])
        if c.track_type != "video":
            expected = resolved_channels.get(angle_key, 1)
            kept, deleted, unverified = sweep_placed_audio(
                timeline, track_plan, before, dest_row, expected,
                f"A{dest_row} {os.path.basename(c.source_file)} "
                f"@{p['snapped_record']}")
            build_record["stream_enforcement"]["checked"] += (
                len(kept) + len(deleted) + len(unverified))
            for record_ in deleted:
                build_record["stream_enforcement"]["deleted"].append(
                    record_)
                print(f"  ✗ {record_['label']}: stray on "
                      f"A{record_['row']} carrying "
                      f"CH{record_['placed_channels']}, program on that "
                      f"row is CH{record_['expected_channel']} - removed",
                      file=sys.stderr)
            for label in unverified:
                build_record["stream_enforcement"]["unverified"].append(
                    label)
                print(f"  ⚠ {label}: placed audio mapping unreadable - "
                      f"kept, UNVERIFIED", file=sys.stderr)
            if not kept:
                print(f"  ✗ A{dest_row} "
                      f"{os.path.basename(c.source_file)}: nothing of "
                      f"this angle's speech remains here", file=sys.stderr)

    # ── The declared look: punch-in on the picture, the frame over it ──
    # The punch-in is the Edit-page transform, which is what the
    # captain's own reference capture measured. It is AIMED at the
    # speaker, per shot, and a shot with no subject measurement is left
    # unpunched rather than punched at a guess (captain, 2026-09-09:
    # a centred 2.30 put Craig out of shot entirely).
    if look is not None:
        from library.tools import reel_look as _look
        from library.tools.subject_framing import measure_subject_in_window

        # The rectangle the picture has to cover: the frame's own
        # transparent window, in timeline pixels. NOT the delivery
        # frame - those are different rectangles and covering the wrong
        # one is what left black bands inside the screen.
        from library.tools.tv_frame import screen_window_rect
        screen_window = screen_window_rect(look, width, height)
        # The plan's picture rows - one per angle, never a hardcoded
        # V1 beside the plan. Each row's items zip with the placements
        # that landed on it (matched by the angle mapping above), so a
        # punch-in is aimed per speaker, per shot.
        aimed = 0
        placed_shots = sum(
            1 for p in placements_list
            if getattr(p["clip"], "track_type", "video") == "video")
        for aroll_row in track_plan.aroll_rows():
            row_items = (timeline.GetItemListInTrack(
                "video", aroll_row.index) or [])
            row_places = [
                p for p in placements_list
                if getattr(p["clip"], "track_type", "video") == "video"
                and video_row_by_angle.get(_angle_key(p["clip"]))
                == aroll_row.index]
            row_places.sort(key=lambda p: p["snapped_record"])
            for index, item in enumerate(row_items):
                if index >= len(row_places):
                    break
                place = row_places[index]
                source_file = place["clip"].source_file
                subject = measure_subject_in_window(
                    source_file, place["source_in"], place["source_out"])
                source_size = _source_frame_size(item)
                if source_size is None:
                    raise ReelBuildError(
                        f"{name}: Resolve reports no resolution for "
                        f"{os.path.basename(source_file)}, so the punch-in "
                        f"cannot be aimed and must not be guessed at.")
                properties = _look.punch_in_properties(
                    look, subject, source_size[0], source_size[1],
                    width, height, window=screen_window)
                if properties is None:
                    print(f"  {name}: NO PUNCH-IN on "
                          f"{os.path.basename(source_file)} "
                          f"({place['source_in']:.2f}-{place['source_out']:.2f}s) "
                          f"- {_look.PUNCH_IN_REFUSED_NO_SUBJECT if subject is None else _look.PUNCH_IN_REFUSED_NOT_A_CLOSE_UP}"
                          f": an unaimed crop is a guess about where the "
                          f"speaker is. The shot plays uncropped.",
                          file=sys.stderr)
                    continue
                for key, value in properties.items():
                    # Judged by what it RETURNS (AGENTS.md 5).
                    if not item.SetProperty(key, value):
                        raise ReelBuildError(
                            f"{name}: Resolve refused {key}={value} on "
                            f"{item.GetName()!r}. The look declares a punch-in "
                            f"and a clip that did not take it plays at a "
                            f"different size to the ones beside it.")
                aimed += 1
                print(f"  {name}: punch-in {properties['ZoomX']:.4f} "
                      f"(declared {look['punch_in']}, screen window needs "
                      f"{_look.window_zoom_for(look, source_size, width, height):.4f}) "
                      f"aimed at "
                      f"subject x={subject.center_x} y={subject.center_y} "
                      f"({subject.detected}/{subject.samples} frames) on "
                      f"{os.path.basename(source_file)} -> Pan "
                      f"{properties['Pan']}, Tilt {properties['Tilt']}",
                      file=sys.stderr)

        runs = _look.frame_runs(placements_list, fps)
        # The frame goes on as a RENDERED overlay, through the same
        # placer the explainer and the semantic visuals use: a still
        # cannot be placed for an arbitrary length through Resolve's
        # API, and one placed as a still came out at the project's
        # standard five seconds over a sixty-one second run.  The
        # render is ONE file at the longest run, shared however many
        # lengths use it - shorter runs trim it here via endFrame, so
        # one still is one artefact, not one per length.
        #
        # It carries the COVER zoom (`tv_frame.cover_zoom`), which is
        # what turns a landscape bezel conformed into a portrait frame
        # from a band across the middle into a frame around the picture.
        #
        # Promoted out of scratch BEFORE placement: the renderer drafts
        # these under scratch/reel_look/ and a timeline that points
        # there points at files a cleaner may throw away
        # (library/tools/reel_placed_assets.py). The placer re-proves
        # it through project_folder.
        from library.tools.reel_placed_assets import promote_frame_overlays
        place_overlay_segments(
            pool, project, timeline, name, fps,
            promote_frame_overlays(
                _look.frame_overlay_segments(look, runs, fps, width, height,
                                             project_folder),
                project_folder),
            track_plan.row_for_role(FRAME).index,
            kind="TV frame", check="F4",
            properties=_look.frame_properties(look, width, height),
            project_folder=project_folder)
        print(f"  {name}: TV frame over {len(runs)} picture run(s) on "
              f"V{track_plan.row_for_role(FRAME).index} at cover zoom "
              f"{_look.frame_properties(look, width, height)['ZoomX']:.4f}, "
              f"punch-in aimed on {aimed}/{placed_shots} shot(s) "
              f"({look['origin']})", file=sys.stderr)

    # Captions are PLACED here and RENDERED by step 4.05, which is the
    # pipeline's renderer. This used to carry its own `npx remotion
    # render` loop - a third implementation of the same call - and it is
    # gone; `reel_subtitle_segments` above drives the step instead.
    from library.tools.overlay_placement import (
        place_overlay_segment,
        sequence_frame_paths,
    )
    for segment in (subtitle_segments or []):
        frames_info = segment.get("frames") or {}
        frame_dir = frames_info.get("dir", "") if segment.get(
            "container") == "frames" else ""
        # Captions render durable already (Area.SUBTITLE_SEGMENTS); the
        # assert pins it, so a future caller that reaches into scratch
        # fails here rather than on the captain's timeline.
        assert_placeable(segment.get("overlay_path") or "", project_folder)
        if frame_dir:
            assert_placeable(frame_dir, project_folder)
        if frame_dir:
            paths = sequence_frame_paths(frame_dir)
            existing = pool_item_for(pool, paths[0]) if paths else None
            items = ([existing] if existing is not None
                     else pool.ImportMedia(paths))
        else:
            found = import_pool_item(pool, segment["overlay_path"])
            items = [found] if found is not None else []
        if not items:
            print(f"Failed to import {segment.get('overlay_path') or frame_dir}",
                  file=sys.stderr)
            continue

        assert_current_timeline(project, timeline)
        # The record span is rounded PER EDGE - [round(start), round(end))
        # - never round(start) + round(duration).  Abutting blocks share
        # one edge in seconds and must share it in frames, or the spans
        # overlap by a frame and Resolve trims one off the later item
        # (reel 07 block 23, 2026-09-08: planned 34, placed 33, the lone
        # F2 of the rebuild).  The source range is the same duration
        # counted from the content start, inside the render handles 4.05
        # leaves either side.  `span_frames` is the one arithmetic; F2
        # grades exactly this span, so placer and check agree by
        # construction and the gate stays exact.
        record_start, record_end = span_frames(
            segment["timeline_start"], segment["timeline_end"], fps)
        content_frames = max(record_end - record_start, 1)
        # A tight clip is placed small and moved into position;
        # full-canvas needs no transform. A sequence shares the mov's
        # frame numbering, so the handle trim is the same arithmetic.
        placement = (segment.get("tight_box") or {}).get("placement")
        placed, note = place_overlay_segment(
            pool, timeline, items[0],
            track_index=track_plan.caption_row().index,
            record_frame=record_start,
            source_in_frame=segment["source_in_frame"],
            source_out_frame=segment["source_in_frame"] + content_frames,
            placement=placement,
            label=segment.get("segment_id", "caption"))
        if not placed:
            print(f"Failed to place {segment.get('segment_id')}: {note}",
                  file=sys.stderr)
        elif note:
            print(f"  caption {segment.get('segment_id')}: {note}",
                  file=sys.stderr)

    # Transition elements last, on the plan's transitions row. Placed
    # from FRAMES the planner already computed against this reel's own
    # keep ranges - nothing is recomputed here, because a placer and a
    # planner that both do the arithmetic are two chances to land one
    # frame off the cut the element exists to hide. The planner stamps
    # a default slot; the plan's row wins, because exactly one thing
    # decides a track index - so the placements are re-stamped here
    # and the re-stamped rows are what the build record carries (what
    # the verifier grades against) rather than the planner's default.
    import dataclasses as _dataclasses

    transitions_row = track_plan.row_for_role(TRANSITIONS)
    transitions_row = transitions_row.index if transitions_row else None
    stamped_placements = [
        _dataclasses.replace(placement, track_index=transitions_row)
        if getattr(placement, "track_index", None) != transitions_row
        else placement
        for placement in (overlay_placements or [])]
    for placement in stamped_placements:
        if transitions_row is None:
            raise ReelBuildError(
                f"{name}: transition elements were planned with no "
                f"transitions row; refusing to place them on an "
                f"unplanned row.")
        element_item = import_pool_item(pool, placement.element_path)
        items = [element_item] if element_item is not None else []
        if not items:
            raise ValueError(
                f"transition element {placement.element_path} could not be "
                f"imported into the media pool, so the element the project "
                f"declared would be silently missing from {name}")

        # `startFrame`/`endFrame` are in the POOL ITEM's OWN frames, not
        # the timeline's - AGENTS.md 5, the same rule the picture loop
        # above obeys and the reason it reads `GetClipProperty("FPS")`.
        # The Lucie bumper is 30fps on a 23.976 timeline: 36 timeline
        # frames of it is 45 of its own, and passing 36 would have taken
        # 1.2s of a 1.5s element. `record_frame` and `duration_frames`
        # stay TIMELINE frames, because that is what the planner
        # computed the cut's position in.
        #
        # This is the same convention the picture and caption placements
        # use - `endFrame - startFrame` is the duration, not one less -
        # and it is not a fresh guess: F18 in the conformance verifier
        # compares the placed length against the planned one, so a wrong
        # reading of it fails the next verification rather than shipping.
        element_fps = float(items[0].GetClipProperty("FPS") or fps)
        source_frames = int(round(placement.element_seconds * element_fps))
        assert_current_timeline(project, timeline)
        pool.AppendToTimeline([{
            "mediaPoolItem": items[0],
            "startFrame": 0,
            "endFrame": source_frames,
            "trackIndex": transitions_row,
            "recordFrame": placement.record_frame,
        }])


    # The explainer. ADDITIVE, exactly as the captions above are: it is
    # laid over picture that keeps playing and moves no frame of it, so
    # no keep range, no caption timing and no footage binding changes
    # because a reel carries one. Its whole span is placed - unlike a
    # caption, a graphic renders no handles either side, so
    # `total_frames` IS the content.
    if explainer_segments:
        place_overlay_segments(
            pool, project, timeline, name, fps, explainer_segments,
            track_plan.row_for_role(EXPLAINER).index,
            kind="explainer", check="F21",
            project_folder=project_folder)



    # The semantic visuals. ADDITIVE, exactly as the explainer above
    # is: laid over picture that keeps playing, moving no frame of it.
    # Each segment renders with no handles either side, so its whole
    # span is placed - `total_frames` IS the content.
    if semantic_segments:
        place_overlay_segments(
            pool, project, timeline, name, fps, semantic_segments,
            track_plan.row_for_role(SEMANTIC).index,
            kind="semantic visual", check="F22",
            project_folder=project_folder)

    # ── Link pass: picture to speech, captions into the group ──
    # Span-based, in ONE call per speech item (see `link_reel_groups`
    # for why a second call breaks the first). Every call is read
    # back; what did not join is said rather than trusted.
    print(f"── Link Pass ──", file=sys.stderr)
    link_record = link_reel_groups(timeline, track_plan)
    build_record["link_groups"] = link_record["link_groups"]
    build_record["caption_links"] = link_record["caption_links"]
    build_record["link_warnings"] = link_record["warnings"]
    for warning in link_record["warnings"]:
        print(f"  ⚠ {warning}", file=sys.stderr)
    print(f"  ✓ {len(link_record['link_groups'])} link group(s), "
          f"{len(link_record['caption_links'])} caption(s) joined",
          file=sys.stderr)

    # ── Occupancy: a row with nothing on it leaves the timeline ──
    # The plan creates a row because something goes on it. When every
    # placement for a row failed, keeping the blank row is exactly the
    # defect being fixed - the row is deleted, and the deletion is on
    # the record. Delete from the top down so indices below hold
    # still while each deletion lands.
    plan_names = {(spec.media_type, spec.index): spec.name
                  for spec in (track_plan.video_tracks
                               + track_plan.audio_tracks)}
    for media_type in ("video", "audio"):
        try:
            count = timeline.GetTrackCount(media_type) or 0
        except Exception:
            continue
        for index in range(count, 0, -1):
            spec_name = plan_names.get((media_type, index))
            try:
                items = (timeline.GetItemListInTrack(media_type, index)
                         or [])
            except Exception:
                continue
            if items:
                if spec_name is None:
                    raise ReelBuildError(
                        f"{name}: unplanned {media_type} row {index} "
                        f"carries {len(items)} item(s); refusing to keep "
                        f"a row the plan never named.")
                continue
            try:
                gone = timeline.DeleteTrack(media_type, index)
            except Exception as exc:
                raise ReelBuildError(
                    f"{name}: empty {media_type.upper()}{index} "
                    f"({spec_name or 'unplanned'}) could not be removed: "
                    f"{exc}")
            if not gone:
                raise ReelBuildError(
                    f"{name}: empty {media_type.upper()}{index} "
                    f"({spec_name or 'unplanned'}) declined deletion - "
                    f"an empty row is never kept.")
            build_record["deleted_empty_tracks"].append(
                {"media_type": media_type, "index": index,
                 "name": spec_name or "unplanned"})
            print(f"  ✗ Empty row {media_type.upper()}{index} "
                  f"({spec_name or 'unplanned'}) removed", file=sys.stderr)

    build_record["transition_placements"] = [
        p.as_dict() if hasattr(p, "as_dict") else dict(p)
        for p in stamped_placements]

    return build_record



def _write_overlay_records(review_dir: str, built_reel_names,
                           overlay_records: dict) -> str:
    """Merge this build's transition-element records into the stored file.

    Every reel THIS build placed is replaced by what it placed - or
    dropped, when it placed none - and every other reel's record is left
    exactly as it was. See the call site for why both halves matter.
    """
    import json
    import os

    path = os.path.join(review_dir, "transition_overlays.json")
    stored = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f) or {}
        except (OSError, ValueError):
            # A record that will not parse is not a reason to lose the
            # build. It is replaced by this build's own answer, and F18
            # then grades whatever is on V4 against that.
            stored = {}
    for name in built_reel_names:
        stored.pop(name, None)
    stored.update(overlay_records)
    if not stored:
        if os.path.exists(path):
            os.remove(path)
        return path
    os.makedirs(review_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)
    return path


def _rename_overlay_records(review_dir: str, mapping: dict) -> None:
    """Rename transition-element records, staging -> final.

    The staging half of promotion, for the same reason the provenance
    sidecar is renamed: the verifier graded the staging against this
    file, and after promotion the same placements live under the final
    name. Records for reels outside `mapping` are untouched. No file
    yet is a no-op - a project that declares no element records none.
    """
    import json
    import os

    if not mapping:
        return
    path = os.path.join(review_dir, "transition_overlays.json")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        stored = json.load(f) or {}
    for old, new in mapping.items():
        if old in stored:
            stored[new] = stored.pop(old)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)


def _drop_overlay_records(review_dir: str, names) -> None:
    """Remove transition-element records for the named reels.

    The gate-fail half of a refused staging: no record may survive for
    a container that is about to be deleted, or F18 would grade the
    surviving approved timeline against a refused build's placements.
    """
    import json
    import os

    drop = set(names or ())
    if not drop:
        return
    path = os.path.join(review_dir, "transition_overlays.json")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        stored = json.load(f) or {}
    for name in drop:
        stored.pop(name, None)
    if not stored:
        os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)


def timelines_to_replace(project, target_names) -> list:
    """The existing timelines THIS build will replace, by exact name.

    Exact, never a prefix.  The loop this replaces collected every
    timeline whose name began `"Reel "`, which on the field-test project
    is nineteen approved timelines - so building one reel deleted the
    other eighteen and deleted any reel the current plan no longer
    contains, with nothing backing them up and nothing saying so.

    A near match lands elsewhere is already the rule for addressing a
    Resolve PROJECT (AGENTS.md 5).  It is the same rule for a timeline,
    and this is the call site where getting it wrong destroys work
    rather than reading the wrong thing.
    """
    found = []
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() in target_names:
            found.append(timeline)
    return found


def assert_deletion_scope(timelines, target_names) -> None:
    """REFUSE to delete a timeline this build did not plan to place.

    The captain's ruling of 2026-09-06, on finding the unconditional
    delete on main: *"a refusal is cheap and a deleted timeline is
    not"*.

    This is deliberately NOT the same computation as
    `timelines_to_replace`, and it is deliberately not folded into it.
    A guard that is the selection restated cannot catch the selection
    being wrong; this one is asked of the LIST ABOUT TO BE DELETED,
    whatever produced it, immediately before `DeleteTimelines`.  So a
    later change to how the delete set is chosen fires this rather than
    quietly widening the blast radius, which is exactly how the loop it
    replaces came to take nineteen timelines to build one.

    `tests/test_reel_build_touches_only_its_own_timelines.py` calls this
    directly with both a permitted and a refused list, and drives a
    build with an over-collecting selection to prove it fires where it
    is actually wired.
    """
    unplanned = sorted(
        timeline.GetName() for timeline in timelines
        if timeline.GetName() not in target_names)
    if unplanned:
        raise ReelBuildError(
            f"REFUSING to build: it would delete {len(unplanned)} "
            f"timeline(s) this build never planned to place - "
            f"{unplanned}. This build places "
            f"{sorted(target_names)}. A timeline that is not being "
            f"rebuilt must be left alone: deleting it destroys approved "
            f"work that nothing here backs up, and a build that quietly "
            f"widened its own delete set is how nineteen approved reels "
            f"came to be deleted in order to write one.")


def reel_numbers(only) -> Optional[set]:
    """`only`, normalised to a set of reel numbers, or `None` for all.

    Here rather than in `step_7_01_build_reels`, because this is the
    module that READS the value: what a malformed one means is the
    reader's to say, and a guard in the step body would make that step
    refuse without an input its own manifest declares OPTIONAL - the
    disagreement `tests/test_input_declarations_are_true.py` exists to
    catch (AGENTS.md 3).

    A string is accepted because an operation's overrides can arrive
    from a shell or from JSON on stdin, where `--only-reel 3` and
    `"3 5"` are the same request.  Anything that is not a number
    REFUSES: a build cannot guess which moment a name refers to, and
    guessing wrong places the wrong reel onto a timeline.
    """
    if only is None:
        return None
    if isinstance(only, str):
        only = [part for part in only.replace(",", " ").split() if part]
    try:
        return {int(entry) for entry in only}
    except (TypeError, ValueError) as bad:
        raise ReelBuildError(
            f"only must be reel NUMBERS, got {only!r} ({bad}). A build "
            f"cannot guess which moment a name refers to, and guessing "
            f"wrong places the wrong reel onto a timeline.") from bad


def built_name(moment, name_suffix: str = "") -> str:
    """What Resolve will CALL this reel when THIS build places it.

    The plan owns the reel's name (`ReelMoment.timeline_name`); a build
    owns the container it puts it in.  They are the same string by
    default and must be allowed to differ, because a build that can only
    write the plan's name can only ever REPLACE what is already there.

    Spelled once, here, so the timeline Resolve creates, the name the
    build records, the key `caption_hashes` is filed under and the
    `timeline` component of every caption filename cannot drift apart -
    and a caption filename that drifted would overwrite the overlays a
    timeline this build is not touching still points at.
    """
    return f"{moment.timeline_name}{name_suffix}"


STAGING_SUFFIX = " (rebuild staging)"
"""What a rebuild is placed INTO before the gate passes.

The container a reel is graded in is never the approved timeline it
may replace: the build stages into `<final>{STAGING_SUFFIX}`, the
conformance verifier grades the staging, and only a pass promotes it
to the final name. A gate-failing build then deletes its own staging
and the approved reel is still there - which is the whole fix for the
2026-09-08 rebuild, where reel 5's F17+F8 failure landed on the live
timeline because the delete ran before the gate.

The suffix keeps the `Reel NN - ...` head intact, so the verifier's
reel-number parse and moment match work on a staging container
unchanged. `timelines_to_replace` matches it exactly like any other
name, so a stale staging container from a crashed run is found by the
same lookup - and REFUSED, never reused.
"""

BACKUP_SUFFIX = " (pre-rebuild backup)"
"""Where the approved timeline waits while a passing staging takes its name.

Promotion cannot rename the staging to the final name while the
original still holds it, and deleting the original first would reopen
the exact loss window this staging exists to close. So the original
moves to `<final>{BACKUP_SUFFIX}` first, the staging takes the final
name, and only then are the backups deleted. At every step every
second of approved content exists under SOME name; a crash leaves
named debris and the next build refuses until it is cleared, which is
recoverable where a deletion is not.
"""


def staging_name(final_name: str) -> str:
    """The staging container a rebuild of `final_name` is placed into."""
    return f"{final_name}{STAGING_SUFFIX}"


def destage(name: str) -> str:
    """The final name a staging container promotes to, or refuse."""
    if not name.endswith(STAGING_SUFFIX):
        raise ReelBuildError(
            f"{name!r} is not a staging container - it does not end in "
            f"{STAGING_SUFFIX!r}. Promotion renames only what the build "
            f"staged; anything else under that name is not this build's "
            f"to move.")
    return name[: -len(STAGING_SUFFIX)]


def backup_name(final_name: str) -> str:
    """Where the approved timeline waits out one promotion."""
    return f"{final_name}{BACKUP_SUFFIX}"


def _read_judgement(project_folder: str):
    """Step 3.05's reading of these reels, or None.

    `reel_quality_bar.read_judgement` is the one reader and it returns
    `(judgement, source)`; only the reading is wanted here. A project
    that has never been judged gets None, which every downstream reader
    treats as "no reel has parts" - which is true, and is not the same
    as "the claim has no parts".  `explainer_plan.BASES` keeps those
    apart in what is recorded.
    """
    from library.tools.reel_quality_bar import read_judgement
    try:
        judgement, _source = read_judgement(project_folder)
    except Exception:
        return None
    return judgement


def _brand_effect(project_folder: str) -> dict:
    """The `effect` slots of whatever brand template this project adopts.

    `{}` where it adopts none, which is most projects (AGENTS.md 10.1:
    a project that names no brand template gets NOTHING). The PROJECT's
    own declaration wins over this in every slot that reads it, so an
    empty template half is not a reduced capability.
    """
    from library.tools.brand_registry import (
        project_template_name, query_slots, resolve_project_template)
    try:
        name = project_template_name(project_folder)
        template = resolve_project_template(name, project_folder)
        return query_slots(template, "effect") or {}
    except Exception:
        return {}


def _connect_resolve_project(resolve_project_name: str):
    """The named Resolve project, addressed exactly, never by prefix.

    `resolve_project_exactly` is referenced as this module's own global
    - not a function-local import - so a patch of
    `library.tools.reel_build.resolve_project_exactly` applies here
    rather than being shadowed by a fresh import of the real one.
    """
    import sys

    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        import os as _os
        _os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        _os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/libfusionscript.dylib"
        if "PYTHONPATH" not in _os.environ:
            _os.environ["PYTHONPATH"] = ""
        _os.environ["PYTHONPATH"] += ":" + _os.environ["RESOLVE_SCRIPT_API"] + "/Modules"
        sys.path.insert(0, _os.environ["RESOLVE_SCRIPT_API"] + "/Modules")
        import DaVinciResolveScript as dvr

    from library.tools import resolve_locale as _locale_mod
    resolve = _locale_mod.scriptapp_preserving_locale(dvr, "Resolve")
    return resolve_project_exactly(
        resolve.GetProjectManager(), resolve_project_name)


def promote_staged_reels(project_folder: str, resolve_project_name: str,
                         master_timeline_name: str,
                         staged_to_final: dict,
                         organise: bool = True) -> dict:
    """Move passing stagings onto their final timeline names.

    The ONLY place an approved timeline is deleted. Reachable only
    after the conformance gate passed on the staging containers, so a
    gate-failing build can never arrive here - structure, not
    vigilance. Per reel, in phases:

    1. the approved original, where one exists, is renamed to its
       backup name - nothing is deleted and nothing is lost;
    2. the staging is renamed to the final name - each `SetName` is
       judged by what it returns, and a refusal names the backups that
       still hold the approved content;
    3. the sidecar baselines (provenance, transition overlays,
       explainer plans) are renamed staging -> final, so the next
       verifier grades the promoted timelines against the baseline the
       gate just passed rather than refusing on absence;
    4. only then are the backups deleted, guarded by
       `assert_deletion_scope` against the backup set.

    A fresh build - no timeline under the final name yet - skips phase
    1 for that reel; everything else is identical, so there is one
    swap path rather than a replacing path and a fresh path.

    Stale debris REFUSES: leftover staging or backup containers from
    an interrupted run are named and the operator clears them in
    Resolve before re-running. Reusing a debris container as this
    run's staging would grade one run's content as another's.

    Returns `{"promoted": [final names...], "organised": ...}`.
    """
    if not staged_to_final:
        return {"promoted": [], "organised": None}
    for staging in staged_to_final.values():
        destage(staging)
    finals = list(staged_to_final.keys())
    backups = {final: backup_name(final) for final in finals}

    project = _connect_resolve_project(resolve_project_name)
    pool = project.GetMediaPool()

    stale_backups = timelines_to_replace(project, set(backups.values()))
    if stale_backups:
        raise ReelBuildError(
            f"REFUSING to promote: {len(stale_backups)} backup "
            f"timeline(s) from an interrupted run are still in the "
            f"project - "
            f"{sorted(t.GetName() for t in stale_backups)}. They hold "
            f"approved content a previous run moved aside. Restore or "
            f"delete them in Resolve and re-run; promoting over them "
            f"would orphan that content.")
    staged_found = {t.GetName(): t for t in
                    timelines_to_replace(project, set(staged_to_final.values()))}
    missing_staging = [s for s in staged_to_final.values()
                       if s not in staged_found]
    if missing_staging:
        raise ReelBuildError(
            f"REFUSING to promote: the build record names staged "
            f"timeline(s) {missing_staging} that are not in Resolve "
            f"project {resolve_project_name!r}. Deleting the approved "
            f"originals now would replace them with nothing.")
    originals = {t.GetName(): t for t in
                 timelines_to_replace(project, set(finals))}
    assert_deletion_scope(list(originals.values()), set(finals))

    for final in finals:
        staging = staged_to_final[final]
        if final in originals:
            if not originals[final].SetName(backups[final]):
                raise ReelBuildError(
                    f"REFUSING to promote: Resolve would not rename "
                    f"{final!r} aside to {backups[final]!r}. Nothing "
                    f"was deleted and the staging {staging!r} is "
                    f"untouched - re-run once Resolve allows renames.")
            print(f"Retired {final} to {backups[final]}", flush=True)
    for final in finals:
        staging = staged_to_final[final]
        if not staged_found[staging].SetName(final):
            raise ReelBuildError(
                f"REFUSING to promote: Resolve would not rename staging "
                f"{staging!r} to {final!r}. The approved content is "
                f"safe under "
                f"{[backups[f] for f in finals if f in originals]} - "
                f"rename it back in Resolve and re-run.")
        print(f"Promoted {staging} to {final}", flush=True)

    import os
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    # The baselines were filed under the staging containers the gate
    # graded; the claim is renamed to the final names, which carry the
    # same cards over the same footage. `{old: new}` throughout.
    claimed = {staging: final for final, staging in staged_to_final.items()}
    from library.tools.plan_provenance import rename_reel_entries
    rename_reel_entries(review_dir, claimed)
    _rename_overlay_records(review_dir, claimed)
    from library.tools.explainer_plan import rename_plan_reels
    rename_plan_reels(project_folder, claimed)
    from library.tools.reel_semantic_visual import rename_record_reels
    rename_record_reels(project_folder, claimed)

    backup_timelines = timelines_to_replace(project, set(backups.values()))
    assert_deletion_scope(backup_timelines, set(backups.values()))
    if backup_timelines:
        pool.DeleteTimelines(backup_timelines)

    organised = None
    if organise:
        from library.tools.execution.organise_media_pool import (
            organise_project, render_unplaced)
        organised = organise_project(
            project, project_folder, master_timeline_name, apply=True)
        print(f"Filed {len(organised['journal']['moves'])} media-pool "
              f"item(s); undo with "
              f"resolve-organize --revert "
              f"{organised['journal']['journal_path']}", flush=True)
        print(render_unplaced(organised["unplaced"]), flush=True)
    return {"promoted": finals, "organised": organised}


def _organise_after_refusal(project, project_folder: str,
                            master_timeline_name: str | None) -> None:
    """File what a refused build imported, instead of stranding it.

    `build_reel_timeline` puts every caption card, subtitle segment,
    transition element and explainer it places through
    `pool.ImportMedia`, which lands in whatever bin is CURRENT. On the
    pass path `promote_staged_reels(organise=True)` files those clips
    afterwards; on the refusal path nothing did, so a refused reel -
    reel 05 of the 2026-09-08 rebuild - left its 26 caption renders
    loose in `Reels/Current plan`. Running the organiser here files
    them the same way: generated and placed by nothing becomes
    `Reel subtitles/Not placed on any timeline`, and anything the
    surviving timelines still place files per reel. Timelines already
    filed stay where they are - the plan is derived from the same
    provenance the discard just updated.

    Never raises: a filing failure is said on stderr, and the gate's
    own refusal - the verdict that matters - still propagates.
    """
    if not master_timeline_name:
        return
    try:
        from library.tools.execution.organise_media_pool import (
            organise_project,
        )
        organise_project(project, project_folder,
                         master_timeline_name, apply=True)
    except Exception as organise_failed:
        import sys as _sys
        print(f"  organise after refusal failed ({organise_failed}) - "
              f"stray clips may remain; file them with "
              f"resolve-organize", file=_sys.stderr)


def discard_staged_reels(project, project_folder: str,
                         staging_names,
                         master_timeline_name: str | None = None) -> None:
    """Delete refused staging containers and forget their baselines.

    The gate-fail path, called before the refusal propagates: the
    staging timelines are removed - guarded by `assert_deletion_scope`
    against the staging set, so a wrong list cannot take an approved
    timeline with it - and the provenance, overlay and explainer
    records filed under the staging names are dropped, so no later
    verifier grades the surviving approved reels against a refused
    build's baseline. The approved timelines are never named here and
    cannot be reached through this function.

    When `master_timeline_name` is given, the media pool is then filed
    the way the pass path files it, so the caption clips the refused
    staging imported do not stay loose in whatever bin was current.
    `None` keeps the old behaviour (discard only) for callers that do
    not name the master.
    """
    import os

    staging = list(staging_names or ())
    if not staging:
        # Nothing staged, but a build that imported before failing may
        # still have left clips behind - file those too.
        _organise_after_refusal(project, project_folder,
                                master_timeline_name)
        return
    found = timelines_to_replace(project, set(staging))
    assert_deletion_scope(found, set(staging))
    if found:
        project.GetMediaPool().DeleteTimelines(found)
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    from library.tools.plan_provenance import drop_reel_entries
    drop_reel_entries(review_dir, staging)
    _drop_overlay_records(review_dir, staging)
    from library.tools.explainer_plan import drop_plan_reels
    drop_plan_reels(project_folder, staging)
    from library.tools.reel_semantic_visual import drop_record_reels
    drop_record_reels(project_folder, staging)
    _organise_after_refusal(project, project_folder, master_timeline_name)


def discard_staged_record(project_folder: str, resolve_project_name: str,
                          staging_names,
                          master_timeline_name: str | None = None) -> None:
    """Connect to Resolve and discard the named staging containers.

    The `verify_reels` node's fail path: the build node already
    returned, so the in-process discard in `rebuild_reels_in_project`
    cannot run. The refused staging is still removed and forgotten
    before the refusal propagates, and the approved timelines are
    still in the project. A connect failure is said, not swallowed -
    but it never stops the gate's own refusal from propagating.

    When `master_timeline_name` is given, the pool is filed afterwards
    for the same reason `discard_staged_reels` files it: a refusal
    that leaves its caption imports loose is the defect, and the gate
    refusing is correct.
    """
    project = _connect_resolve_project(resolve_project_name)
    discard_staged_reels(project, project_folder, staging_names,
                         master_timeline_name)


def rebuild_reels_in_project(project_slug: str, skip_captions: bool = False,
                             verify: bool = True, only=None,
                             name_suffix: str = "",
                             organise: bool = True) -> dict:
    """Build every approved reel, and RETURN the record of what was placed.

    NOTHING APPROVED IS DELETED BEFORE THE GATE PASSES. This used to
    delete the timelines it was about to replace first and run the
    conformance verifier last, so a build the gate refused - reel 5 of
    the 2026-09-08 rebuild, F17 (mixed-speaker card) plus F8 (end cuts
    mid-word) - exited 1 AFTER replacing the live timeline, converting
    an approved reel into verify-failed content
    (`docs/REEL_REBUILD_RUN_20260908_R3.md`). The delete-then-verify
    order made that outcome expressible; the order below makes it not:

    1. STAGE: each reel is placed into `<final>{STAGING_SUFFIX}` and
       NOTHING existing is deleted or renamed. The gate grades the
       staging containers, and the sidecar baselines (provenance,
       transition overlays, explainer plans) are filed under the
       staging names so the grading has a recorded baseline.
    2. VERIFY (when `verify` is true): the gate runs over the staging.
       A refusal deletes the staging containers, drops their sidecar
       entries, and raises - the approved timelines were never named
       and are still there.
    3. PROMOTE: only a pass renames staging -> final, retiring each
       approved original to a backup name first and deleting the
       backups last (`promote_staged_reels`). Filing the media pool
       (`organise`) happens here, because filing is about reels that
       already exist under their real names.

    With `verify=False` the call stops after staging and returns the
    staging record; the `verify_reels` node grades it and promotes on
    a pass. Either way no path deletes an approved timeline before a
    passing gate.

    A BUILD MAY ONLY DELETE WHAT IT IS ABOUT TO PLACE - and now it
    deletes nothing at all until promotion. The names this call will
    place are still computed first and a build of one reel still
    touches one timeline. `write_provenance` has merged rather than
    replaced since #568 for the same reason - *"a partial rebuild must
    not delete the provenance of the reels it did not touch"*.

    `only` selects WHICH approved moments to build, by reel number.
    `None` is every approved moment, which is what every caller had.

    `name_suffix` is appended to the FINAL name each reel promotes to.
    `""` is the plan's own name, which is what every caller had. A
    non-empty suffix builds the same plan toward a different final
    container, which is the only way to compare a rebuild against an
    approved timeline instead of overwriting it. It reaches the
    captions too (`built_name`) - via the STAGING name, so a rebuild
    never renders into the overlay files the approved timeline still
    points at.

    `organise` files the media pool after promotion, so a rebuild
    TIDIES UP rather than accumulating: the reels this call placed
    land in `Reels/Current plan`, and a reel the live plan no longer
    names moves to `Reels/Earlier plans` - moved and relabelled, never
    deleted. A reel sitting in a bin outside that layout is where the
    captain put it and stays there (`resolve_organization.TIMELINE_BINS`). Without it, `CreateEmptyTimeline` and `ImportMedia` put
    what they make into whatever bin was CURRENT, which is wherever
    the operator last clicked; measured on the field test, that
    scattered 49 timelines and 2,573 renders across three bins with
    nothing recording why. It runs AFTER promotion, because filing is
    about reels that already exist and a failure to file must not read
    as a failure to build. See
    `library/tools/resolve_organization.py`. With `verify=False` the
    filing is deferred to whoever promotes.

    `verify` defaults to True, so nothing that called this before gets
    a weaker gate than it had: a direct caller still has the
    conformance verifier run and still gets a raise on a defective
    build - with the approved timelines intact.

    The one caller that passes False is the `build_reels` node of
    `library/processes/reels`, whose process has `verify_reels` as its
    own node. A build that was placed and a build that conformed are
    two facts that fail for different reasons, and a ledger keyed by
    node id can only tell them apart if two nodes recorded them.
    Running the verifier in both places would report one set of
    findings twice under two step ids.

    The RETURN VALUE is what the edge to `verify_reels` carries.
    `timelines_built` names what is IN RESOLVE right now - the staging
    containers while staged, the final names once promoted - so the
    record is always directly gradeable and never names a timeline
    that does not exist. `staged_timelines` maps final -> staging
    while anything is staged and is `{}` once promotion renamed them;
    the verifier promotes off that mapping. Every other field was
    already computed here and then dropped on the floor -
    `built_reel_names` and `caption_hashes` went into provenance and
    nowhere else - which is how a verifier could re-derive a different
    grouping a day later and grade against it.
    """
    import os
    import sys
    import json
    import yaml
    
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/libfusionscript.dylib"
        if "PYTHONPATH" not in os.environ:
            os.environ["PYTHONPATH"] = ""
        os.environ["PYTHONPATH"] += ":" + os.environ["RESOLVE_SCRIPT_API"] + "/Modules"
        sys.path.insert(0, os.environ["RESOLVE_SCRIPT_API"] + "/Modules")
        import DaVinciResolveScript as dvr

    from library.tools.resolve_locale import scriptapp_preserving_locale
    from library.tools.reel_proposal import read_proposal
    from library.tools.timeline_ingest import snapshot_timeline
    from library.tools.project_registry import get_project
    
    resolve = scriptapp_preserving_locale(dvr, "Resolve")
    pm = resolve.GetProjectManager()
    
    if os.path.isabs(project_slug) and os.path.isdir(project_slug):
        project_folder = project_slug
    else:
        proj = get_project(project_slug)
        if not proj:
            raise ValueError(f"Unknown project {project_slug}")
        project_folder = proj.root
    
    with open(os.path.join(project_folder, "project.yaml")) as f:
        config = yaml.safe_load(f)
        
    resolve_config = config.get("resolve", {})
    resolve_name = resolve_config.get("project_name", os.path.basename(project_slug))
    master_timeline_name = resolve_config.get("timeline_name")
    if not master_timeline_name:
        raise ValueError("Missing 'timeline_name' under 'resolve' in project.yaml")
    
    project = resolve_project_exactly(pm, resolve_name)
        
    from library.tools.reel_proposal import proposal_path as _proposal_path
    proposal_path = str(_proposal_path(project_folder))
    moments = read_proposal(proposal_path)

    # Archive the plan so it survives being overwritten by the next
    # selector run.  The archive sits alongside the live file, named
    # with a timestamp so it sorts chronologically and never collides.
    from library.tools.plan_provenance import archive_plan
    archive_plan(proposal_path)
    
    with open(os.path.join(project_folder, "pipeline_output/scratch/timeline_transcript/transcript.json")) as f:
        transcript = json.load(f)

    # Stored proposals predate the boundary drawer: a boundary drawn
    # before it can sit inside a word, and the build reads the file
    # AS-IS.  Repair each moment on the way through with the SAME snap
    # generation runs, so the rebuild plays word-edge boundaries without
    # re-deciding WHICH moments the captain approved
    # (`reel_proposal.snap_moment_to_speech`).  In memory only - the file
    # keeps exactly what they ruled on.
    from library.tools.reel_proposal import snap_moment_to_speech
    repaired = []
    for moment in moments:
        fixed, moves = snap_moment_to_speech(moment, transcript)
        for move in moves:
            word = (f" through '{move['through']}'"
                    if move.get("through") else "")
            print(f"  Reel {moment.number:02d}: {move['boundary']} "
                  f"{move['was']:.3f}s -> {move['now']:.3f}s{word} "
                  f"(stored proposal predates the boundary snap)",
                  file=sys.stderr)
        repaired.append(fixed)
    moments = repaired

    # The captain's recorded strikes, read ONCE for the batch: the same
    # store `select_reels` enforces on new proposals, applied here to
    # APPROVED moments as cuts inside their own ranges. Selection never
    # rewrites an approved range and the build never re-decides one -
    # applying a strike is obedience, not re-decision, and the full
    # reasoning lives in `transcript_corrections.exclusion_cuts_for_span`.
    # A strike the store cannot supply must REFUSE, never build silently
    # past it: quiet non-application is the defect this exists to end.
    from library.tools import transcript_corrections as _tc
    keep_exclusions = _tc.keep_exclusions(project_folder)
        
    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == master_timeline_name:
            timeline = t
            break
            
    if not timeline:
        raise ValueError(f"Could not find master timeline {master_timeline_name}")
        
    snapshot = snapshot_timeline(timeline, project.GetName())
    master_clips = snapshot.clips

    # WHICH moments this call builds, decided before anything is touched.
    # `only` is reel numbers; an approved moment not named by it is left
    # exactly as it is, timeline and all.
    wanted = reel_numbers(only)
    building = [m for m in moments
                if str(getattr(m.approval, "value", m.approval)) == "approved"
                and (wanted is None or int(m.number) in wanted)]
    if wanted is not None:
        missing = wanted - {int(m.number) for m in building}
        if missing:
            raise ReelBuildError(
                f"asked to build reel(s) {sorted(missing)}, and the plan "
                f"{proposal_path} has no APPROVED moment with those "
                f"numbers. Approved: "
                f"{sorted(int(m.number) for m in building)}. A build that "
                f"quietly skipped them would report success having placed "
                f"nothing.")

    # STAGE, not replace: the final names this call is FOR, and the
    # staging containers it actually places. Nothing existing is
    # deleted or renamed here - promotion does that, and only on a
    # passing gate. See `timelines_to_replace` and
    # `assert_deletion_scope`.
    target_names = {built_name(m, name_suffix) for m in building}
    # In BUILD order, not set order: everything downstream - the gate
    # scope, the promotion order, the record - reads this mapping's
    # order, and a run must report reels in the order it built them.
    staged_to_final = {built_name(m, name_suffix): staging_name(built_name(m, name_suffix))
                       for m in building}
    staged_names = set(staged_to_final.values())
    stale_staging = timelines_to_replace(project, staged_names)
    if stale_staging:
        raise ReelBuildError(
            f"REFUSING to build: {len(stale_staging)} staging "
            f"timeline(s) from an interrupted run are still in the "
            f"project - "
            f"{sorted(t.GetName() for t in stale_staging)}. Delete "
            f"them in Resolve and re-run; reusing a debris container "
            f"would grade one run's content as another's.")
    stale_backups = timelines_to_replace(
        project, {backup_name(final) for final in target_names})
    if stale_backups:
        raise ReelBuildError(
            f"REFUSING to build: {len(stale_backups)} backup "
            f"timeline(s) from an interrupted promotion are still in "
            f"the project - "
            f"{sorted(t.GetName() for t in stale_backups)}. They hold "
            f"approved content a previous run moved aside. Restore or "
            f"delete them in Resolve and re-run.")

    # The project's transition-element declaration, resolved ONCE - the
    # element is measured here rather than per reel, so a declaration
    # naming a file that does not exist or draws nothing fails before any
    # timeline is created rather than half way through nineteen.
    #
    # The brand half is `{}` and that is not a stub: `EffectSlots` carries
    # no `transition_overlay` field, so a template CANNOT declare one
    # today and reading one for the key would be a declaration that does
    # not bind. A transition element is artwork, and artwork belongs to
    # the project (AGENTS.md 14) - see `transition_overlay.resolve_declaration`.
    from library.tools import transition_overlay as overlay_mod
    overlay_effect = overlay_mod.resolve_declaration({}, project_folder)
    overlay_declared = overlay_mod.declared_overlay(overlay_effect) is not None

    built_reel_names = []
    caption_hashes = {}
    footage_binding_hashes = {}
    overlay_records = {}
    track_plans = {}
    skipped_by_exclusion: list = []
    # Read ONCE, before the loop: a malformed declaration must stop the
    # whole build, not the twelfth reel of nineteen.
    card_declarations = declared_cards(project_folder)
    explainer_plans = []
    semantic_records = []
    # The project's TV-frame declaration, read ONCE for the same reason
    # the cards are: a malformed declaration must stop the whole build
    # rather than the twelfth reel of nineteen. None is a project that
    # declares no look, and then every reel below is placed exactly as
    # it was before `library/tools/reel_look.py` existed.
    from library.tools import reel_look as _reel_look
    # CHECKED against the frame the reels are built at, before a
    # timeline exists: a frame asset whose aspect is not the delivery's
    # cannot surround the picture, and one smaller than the delivery
    # would be drawn upscaled. Both refuse here with both numbers named.
    reel_look_decl = _reel_look.resolve_look(project_folder, 1080, 1920)
    motion_records = []
    if reel_look_decl is not None:
        print(f"TV-frame look declared by {reel_look_decl['origin']}: "
              f"punch-in {reel_look_decl['punch_in']}, frame "
              f"{os.path.basename(reel_look_decl['asset'])}", file=sys.stderr)
    # What the model read of each reel, and what the project declares.
    # Both are read ONCE for the batch: the judgement is one file and the
    # declaration is one project, and re-reading either per reel would be
    # nineteen answers to one question.
    judgement = _read_judgement(project_folder)
    brand_effect = _brand_effect(project_folder)
    # A build that fails HALF WAY must not leave half a staging behind
    # silently either: whatever was placed is discarded on the way out,
    # so a re-run starts from no debris of this run. The approved
    # timelines were never touched and need no recovery.
    current_staging: str | None = None
    try:
        for moment in building:
            final = built_name(moment, name_suffix)
            name = staged_to_final[final]
            current_staging = name
            print(f"Building {name}", flush=True)
            # This approved moment's own strikes, cut from its ranges
            # below. Said on the run that honours them: a cut the
            # operator cannot see is a silent content change. Grown
            # over wordless clip lead-in first (`grow_cuts...`), so a
            # strike at a word's start does not strand 6 frames of
            # room tone the readability floor then refuses.
            moment_cuts = _tc.grow_cuts_over_wordless_leadin(
                _tc.exclusion_cuts_for_span(
                    moment.timeline_start, moment.timeline_end,
                    keep_exclusions),
                transcript)
            for cut_start, cut_end, cut_id in moment_cuts:
                print(f"  keep exclusion {cut_id} cuts "
                      f"{cut_start:.2f}-{cut_end:.2f}s from this reel - "
                      f"recorded by the captain, applied at build so an "
                      f"approved range is honoured rather than re-decided",
                      flush=True)
            # A repetition this build is LEAVING IN, and why, said where the
            # operator is already looking. Silence here is what let reel 03
            # be rebuilt worse at the open than the timeline it replaced.
            for group in refused_take_groups(moment.timeline_start,
                                              moment.timeline_end, transcript):
                print(f"  repetition kept at {group['start']:.2f}-"
                      f"{group['end']:.2f}s ({group['speaker']}): "
                      f"{group['why_nothing_was_cut']}", flush=True)
            # A repetition distance alone kept in, said where the operator
            # is already looking. Suspects never cut, so this changes
            # nothing placed - it only names what the reel plays twice.
            for suspect in suspected_takes(moment.timeline_start,
                                           moment.timeline_end, transcript):
                if (suspect.kept_start - suspect.dropped_end
                        <= CUT_WINDOW_SECONDS):
                    continue
                print(f"  distant repeat kept at "
                      f"{suspect.dropped_start:.2f}-"
                      f"{suspect.dropped_end:.2f}s echoed at "
                      f"{suspect.kept_start:.2f}-"
                      f"{suspect.kept_end:.2f}s ({suspect.speaker}): "
                      f"same words {suspect.kept_start - suspect.dropped_end:.1f}s "
                      f"apart, kept because distance alone cannot tell a "
                      f"callback from a retake", flush=True)
            for echo in closer_repeats(moment, transcript):
                if echo["kind"] == "closer_echoes_body":
                    print(f"  closer echoes the body: "
                          f"{echo['body_start']:.2f}-{echo['body_end']:.2f}s "
                          f"said again at {echo['closer_start']:.2f}-"
                          f"{echo['closer_end']:.2f}s "
                          f"({echo['speaker']}) - the reel plays those "
                          f"words twice", flush=True)
                else:
                    print(f"  closer repeats itself: "
                          f"{echo['dropped_start']:.2f}-"
                          f"{echo['dropped_end']:.2f}s said again at "
                          f"{echo['kept_start']:.2f}-"
                          f"{echo['kept_end']:.2f}s "
                          f"({echo['speaker']}) - the closer is placed "
                          f"whole", flush=True)
            try:
                ranges = reel_ranges(moment, transcript,
                                     extra_cuts=moment_cuts)
            except ExclusionWipesBody as wiped:
                # Dropped WITH the reason, never split and never emptied:
                # the strike covers the whole body, so there is no reel
                # left to build and no second reel to invent. The
                # approved timeline already in Resolve is left exactly
                # as it is, like a reel this call did not name.
                reason = str(wiped)
                print(f"  SKIPPING {name}: {reason}", flush=True)
                skipped_by_exclusion.append({"reel": name,
                                             "number": moment.number,
                                             "reason": reason})
                current_staging = None
                continue

            # Full-frame elements FIRST, because a head card decides where
            # every other thing on this reel starts. Planned and rendered
            # before anything is placed, so a declaration that cannot be
            # resolved - a binding with nothing behind it, a typeface that
            # will not draw - stops this reel here rather than after a
            # timeline exists (`library/tools/full_frame_element.py`).
            cards = plan_cards(moment, transcript, ranges, project_folder,
                               fps=24000 / 1001, declarations=card_declarations)
            if cards:
                from library.tools.full_frame_element import render_reel_cards
                print(f"  {len(cards)} full-frame element(s) declared",
                      flush=True)
                cards = render_reel_cards(
                    cards,
                    str(REMOTION_DIR),
                    card_render_dir(project_folder))
            lead = lead_frames(cards, 24000 / 1001) / (24000 / 1001)

            # Was: computed by the standalone captioner and then passed as
            # None, so every reel built since #524 carried no subtitles at
            # all while the work was done and discarded.
            subtitle_segments = None if skip_captions else reel_subtitle_segments(
                moment, transcript, ranges, project_folder,
                fps=24000 / 1001, width=1080, height=1920, timeline_name=name,
                lead_seconds=lead)

            # The animated explainer. A project that declares none gets
            # `([], plan)` with the plan saying `not_declared`, and the
            # timeline it gets is the one it got before this existed.
            explainer_segments, explainer_plan = reel_explainer_segments(
                moment, transcript, ranges, project_folder,
                fps=24000 / 1001, width=1080, height=1920,
                judgement=judgement, brand_effect=brand_effect,
                timeline_name=name)
            explainer_plans.append(explainer_plan)

            # The semantic visuals: what the MODEL says this reel's
            # speech wants drawn. The ask is written fresh on every
            # build from the moment, the transcript and these same
            # ranges; the answer is read off the response file when a
            # model has written one, and the reel builds without
            # visuals - SAID as `awaiting_model_answer` - when none
            # has. A headless build never blocks on a model.
            from library.tools import reel_semantic_visual as sem_vis
            sem_vis.write_request(
                moment, transcript, ranges, project_folder,
                fps=24000 / 1001)
            semantic_segments, semantic_record = sem_vis.build_for_reel(
                moment, transcript, ranges, project_folder,
                fps=24000 / 1001, width=1080, height=1920,
                timeline_name=name)
            semantic_records.append(semantic_record)

            # RECORD what was placed. Derived at build time and previously
            # written down nowhere, which is why the verifier could re-derive
            # a different grouping a day later and grade against it.
            #
            # Two hashes, answering different questions:
            # - caption_content_hash: WHAT was said and WHEN in the reel.
            # - footage_binding_hash: WHICH footage the captions were
            #   computed against. A caption that passes the content check
            #   but fails the binding check was placed against footage that
            #   moved - exactly the defect that was invisible before.
            if subtitle_segments:
                entries = getattr(subtitle_segments, "caption_entries", None)
                if entries:
                    from library.tools.plan_provenance import caption_content_hash
                    caption_hashes[name] = caption_content_hash(entries)
                spine = getattr(subtitle_segments, "spine", None)
                if spine:
                    from library.tools.plan_provenance import footage_binding_hash
                    try:
                        footage_binding_hashes[name] = footage_binding_hash(spine)
                    except ValueError:
                        pass  # No bindings in spine - skip silently

            # Transition elements over this reel's own cuts. The plan is
            # made from the SAME `ranges` the picture was placed from, so an
            # element cannot land on a cut that is not there. An empty plan
            # says why it is empty and that reason is printed, because a
            # declaration that quietly draws nothing is the failure this
            # whole area keeps producing (AGENTS.md 10.2).
            overlay_plan = None
            if overlay_declared:
                overlay_plan = overlay_mod.plan_reel_overlays(
                    overlay_effect, ranges, 24000 / 1001, project_folder,
                    closer_seam_frame=_closer_seam_frame(
                        moment, ranges, 24000 / 1001))
                overlay_records[name] = overlay_plan.as_dict()
                if overlay_plan.placements:
                    # The GESTURE is said on the run that places it. An
                    # element that stamps the cut rather than hiding it is a
                    # legitimate choice and an easy one to make by accident:
                    # both are asked for in the same words, and the
                    # difference is invisible until it is on the timeline.
                    print(f"  {len(overlay_plan.placements)} transition "
                          f"element(s) on V{overlay_mod.OVERLAY_TRACK}: "
                          f"seams {[p.seam_index for p in overlay_plan.placements]}"
                          f" - {overlay_plan.element.gesture}", flush=True)
                else:
                    print(f"  no transition element placed: "
                          f"{overlay_plan.reason_empty}", flush=True)

            # The declared look, and the drift a model planned under it.
            # Both are resolved BEFORE the timeline is created so a
            # malformed declaration refuses the build rather than
            # leaving half a reel behind; both are None where the
            # project declares nothing, and then this is the call it
            # was before `reel_look` existed.
            reel_motion = []
            if reel_look_decl is not None:
                from library.tools import reel_look as _look
                spine = _look.motion_spine(
                    placements(reel_ranges(moment, transcript,
                                           extra_cuts=moment_cuts),
                               master_clips,
                               24000/1001, lead_frames=lead_frames(cards, 24000/1001)),
                    24000/1001)
                _look.write_motion_request(
                    moment.number, name, spine,
                    transcript.get("segments") or [], project_folder)
                reel_motion, motion_record = _look.resolve_motion(
                    _look.read_motion_answer(project_folder, moment.number),
                    spine, 24000/1001)
                motion_record["reel"] = name
                motion_records.append(motion_record)
                if motion_record["basis"] == _look.MOTION_AWAITING_ANSWER:
                    print(f"  {name}: NO PICTURE MOTION - no model answer on "
                          f"file ({_look.motion_request_stem(moment.number)}"
                          f".json), every shot plays still", file=sys.stderr)
                for drop in motion_record["dropped"]:
                    print(f"  {name}: motion dropped on shot "
                          f"{drop['target_block_position']} "
                          f"({drop['effect_type']}): {drop['reason']}",
                          file=sys.stderr)

            build_result = build_reel_timeline(
                project=project,
                moment=moment,
                master_clips=master_clips,
                subtitle_segments=subtitle_segments,
                fps=24000/1001,
                width=1080,
                height=1920,
                project_folder=project_folder,
                transcript=transcript,
                timeline_name=name,
                cards=cards,
                overlay_placements=(overlay_plan.placements
                                    if overlay_plan else None),
                explainer_segments=explainer_segments,
                semantic_segments=semantic_segments,
                look=reel_look_decl,
                motion=reel_motion,
                # The live master is how the program stream resolves
                # on projects whose catalog predates stream recording.
                master_timeline=timeline,
                extra_cuts=moment_cuts,
            )
            # The plan each staging was placed from, keyed by staging
            # name - so the conformance proof grades what was built,
            # never a re-derivation, and promotion renames it with the
            # timeline it describes.
            track_plans[name] = build_result["track_plan"]
            if name in overlay_records and build_result.get(
                    "transition_placements") is not None:
                # The placer re-stamps transition elements onto the
                # plan's row; the record the verifier grades against
                # carries the stamped rows, not the planner's default.
                overlay_records[name]["placements"] = list(
                    build_result["transition_placements"])
            # The switch animation and the drift are Fusion comps, and a
            # comp cannot be imported by the process that created the
            # timeline (AGENTS.md 5).  So they go in here, in a
            # subprocess handed the destination it must find current -
            # after the picture is placed and before the gate reads it,
            # because a reel whose comps failed is not the reel that was
            # planned.
            if reel_look_decl is not None:
                from library.tools import reel_look as _look
                manifest = _look.fusion_manifest(
                    placements(
                        reel_ranges(moment, transcript,
                                    extra_cuts=moment_cuts), master_clips,
                        24000/1001,
                        lead_frames=lead_frames(cards, 24000/1001)),
                    reel_look_decl, reel_motion, 24000/1001)
                if not _look.apply_comps(manifest, project_folder,
                                         resolve_name, name):
                    raise ReelBuildError(
                        f"{name}: the Fusion pass refused or failed. The "
                        f"switch animation and every planned drift are "
                        f"comps, so a reel that lost them is a reel with a "
                        f"different picture from the one that was planned.")
            # Placed: only now is this staging a container the gate may
            # grade and promotion may move. An exception above leaves the
            # name off this list and the except below removes whatever
            # half-built container may exist under it.
            built_reel_names.append(name)
            for card in cards or ():
                # SAID on the run that placed it, rather than recorded in the
                # return value: the verifier re-derives the cards from the
                # project's own declaration with this module's code, the same
                # way it re-derives the placements, so a second copy in the
                # build record would be a field with no reader (AGENTS.md
                # 10.1).
                print(f"  placed {card.placement} card {card.render_name} at "
                      f"reel frame {card.reel_start_frame} "
                      f"({card.duration_frames}f)", flush=True)
    except Exception:
        placed = list(dict.fromkeys(
            built_reel_names + ([current_staging] if current_staging else [])))
        discard_staged_reels(project, project_folder, placed,
                             master_timeline_name)
        raise

    # What each reel's explainer really was, INCLUDING the empty ones.
    # Recorded rather than re-derived, for the reason
    # docs/CHROMA_KEY_TRANSITIONS_MEASURED.md gives: a re-derived plan is
    # only the build's plan while nothing changed in between, and that
    # assumption already produced 42 confident meaningless errors on this
    # path.
    from library.tools.explainer_plan import write_plans as _write_explainers
    _write_explainers(project_folder, explainer_plans)

    # What each reel's semantic visuals really were, INCLUDING the reels
    # with none. MERGED per reel, for the same reason `write_provenance`
    # merges: a partial (`only`) build must not delete the record of the
    # reels it did not touch, or F22 would grade those timelines against
    # an absence. See `library/tools/reel_semantic_visual.py`.
    from library.tools.reel_semantic_visual import (
        write_records as _write_semantic_records)
    _write_semantic_records(project_folder, semantic_records)

    # Record which plan we built from, so the verifier can detect
    # if the plan changes before verification runs.
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    from library.tools.plan_provenance import write_provenance
    # MERGES into any existing record: a partial rebuild must not
    # delete the provenance of the reels it did not touch.
    write_provenance(review_dir, proposal_path, built_reel_names,
                     caption_hashes=caption_hashes,
                     footage_binding_hashes=footage_binding_hashes)

    # The plan the verifier grades V4 against, written where the rest of
    # the build record lives. A placement nothing recorded is a placement
    # nothing can check.
    #
    # MERGED per reel, and the reels this build touched are REPLACED
    # whole - including being removed when the project no longer declares
    # an element. Overwriting the file would delete the record of the
    # eighteen reels a `--only 3` rebuild did not touch, which is the
    # defect #568 fixed for `write_provenance` and the same one seen from
    # here; leaving a stale entry would make F18 report an element as
    # missing from a reel that was correctly rebuilt without one.
    _write_overlay_records(review_dir, built_reel_names, overlay_records)

    organised = None
    staged_out = dict(staged_to_final)
    if verify:
        # Scoped to what THIS call placed - the staging containers, not
        # the approved timelines: verifying the whole project here is
        # what made one reel cost 49 gradings, and grading eighteen
        # untouched timelines against the current plan is how a clean
        # single-reel build failed on findings it never touched
        # (data/vep-rebuild-verify/report.md 3.5). `None` is unreachable
        # here - `built_reel_names` is a list, possibly empty - and an
        # empty one is refused inside `verify_built_reels` rather than
        # passing on nothing.
        try:
            verify_built_reels(
                project_folder=project_folder,
                resolve_project_name=resolve_name,
                master_timeline_name=master_timeline_name,
                plan_path=proposal_path,
                transcript_path=os.path.join(project_folder, "pipeline_output/scratch/timeline_transcript/transcript.json"),
                only_reels=list(built_reel_names),
            )
        except Exception:
            # The gate refused: the staging containers and their
            # baselines go, the approved timelines were never named.
            # Reel 5's F17+F8 is exactly this path - and the reel the
            # captain approved is still in the project afterwards.
            # The pool is filed too, so the refused staging's caption
            # imports do not stay loose where ImportMedia left them.
            discard_staged_reels(project, project_folder, built_reel_names,
                                 master_timeline_name)
            raise
        promoted = promote_staged_reels(
            project_folder, resolve_name, master_timeline_name,
            dict(staged_to_final), organise=organise)
        organised = promoted["organised"]
        # From here the record speaks final names: what is in Resolve
        # now is the promoted timelines, and the sidecar files were
        # renamed to match by the promotion.
        final_names = promoted["promoted"]
        caption_hashes = {
            final: caption_hashes[staged_to_final[final]]
            for final in final_names if staged_to_final[final] in caption_hashes}
        footage_binding_hashes = {
            final: footage_binding_hashes[staged_to_final[final]]
            for final in final_names
            if staged_to_final[final] in footage_binding_hashes}
        overlay_records = {
            final: overlay_records[staged_to_final[final]]
            for final in final_names
            if staged_to_final[final] in overlay_records}
        track_plans = {
            final: track_plans[staged_to_final[final]]
            for final in final_names
            if staged_to_final[final] in track_plans}
        built_reel_names = list(final_names)
        staged_out = {}

    return {
        "timelines_built": built_reel_names,
        # Reels this call deliberately did NOT build: a recorded strike
        # covers the whole body, so the reel is dropped WITH the reason
        # (the build-time shape of the no-split rule) and whatever the
        # captain approved stays exactly as it is. A reader that wants
        # to know what was held back reads this, never silence.
        "skipped_by_exclusion": skipped_by_exclusion,
        # Final -> staging while anything is staged, `{}` once
        # promotion renamed them. The `verify_reels` node grades
        # `timelines_built` and promotes off this mapping; absent on
        # records written before staging existed, which are already
        # final and promote to nothing.
        "staged_timelines": staged_out,
        "caption_hashes": caption_hashes,
        # The track plan each timeline was placed from, by final name
        # once promoted - so a conformance proof grades what was
        # built, never a re-derivation of it.
        "track_plans": track_plans,
        "plan_path": proposal_path,
        "resolve_project_name": resolve_name,
        "master_timeline_name": master_timeline_name,
        "captions_rendered": not skip_captions,
        "verified_in_place": bool(verify),
        # What this build was ASKED for, so the record can say that it
        # built one reel into a new container rather than reading like a
        # full rebuild that placed one timeline.
        "reels_requested": None if wanted is None else sorted(wanted),
        "name_suffix": name_suffix,
        # Where the media pool was filed, and the journal that undoes it.
        # None when the caller declined - and when the call stopped at
        # staging (`verify=False`), where filing waits for whoever
        # promotes. Never a silent empty record.
        "organised": organised,
        # The transition elements laid over each reel's cuts, per reel,
        # INCLUDING the reels where nothing was placed and why. An empty
        # record here means the project declares no element at all; a
        # reel present with no placements means it declares one and this
        # reel had no cut of the kind it asked for.
        "transition_overlays": overlay_records,
        # The declared look, and what each reel's picture motion came
        # to. `look` is None where the project declares none; `motion`
        # carries one record per reel INCLUDING the reels where every
        # entry was dropped and why, because a plan whose entries were
        # all refused must not read like a plan the model deliberately
        # left empty (`library/tools/reel_look.py`, MOTION_BASES).
        "look": reel_look_decl,
        "picture_motion": motion_records,
    }



def verify_built_reels(project_folder: str, resolve_project_name: str, master_timeline_name: str, plan_path: str, transcript_path: str, only_reels=None) -> None:
    """Run the reel conformance verifier as a quality gate after building reels.

    `only_reels` is the EXACT reel timeline names to grade - the build's
    own `timelines_built`. `None` grades every `Reel *` timeline (the
    deliberate sweep); a list grades only those, so a partial build
    pays for what it placed. An EMPTY list is REFUSED: a verification
    that grades nothing and passes is the gate that cannot fail
    (AGENTS.md 10.4).

    If the verifier finds ANY errors, this raises a RuntimeError with the findings,
    failing the build. The raw JSON and human-readable table are preserved in
    the project's pipeline_output/review directory.
    """
    if only_reels is not None and not list(only_reels):
        raise RuntimeError(
            "verify_built_reels was asked to grade zero reel timelines - "
            "the build placed nothing, so there is nothing to verify and "
            "a pass would mean nothing. Refusing instead of reporting "
            "success on an empty scope.")
    import os
    import json
    
    out_dir = os.path.join(project_folder, "pipeline_output", "review")
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "conformance_report.json")
    
    try:
        from library.tools.reel_conformance_verifier import run_verification
    except ImportError as e:
        raise RuntimeError(f"Reel conformance verifier is unavailable: {e}")
        
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            transcript = json.load(f)
            
        exit_code = run_verification(
            project_name=resolve_project_name,
            master_name=master_timeline_name,
            plan_path=plan_path,
            transcript=transcript,
            json_path=json_path,
            only_reels=None if only_reels is None else list(only_reels),
        )
    except Exception as e:
        raise RuntimeError(f"Reel conformance verifier failed to run: {e}")
        
    if exit_code == 1:
        findings_msg = "See conformance_report.json for details."
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    report = json.load(f)
                errors = [f for f in report.get("findings", []) if f.get("severity") == "error"]
                if errors:
                    findings_msg = json.dumps(errors, indent=2)
            except Exception:
                pass
        raise RuntimeError(f"Reel build produced a defective timeline. Verification failed with findings:\n{findings_msg}")
    elif exit_code == 2:
        raise RuntimeError("Reel conformance verifier encountered a fatal error (e.g. timeline changed during verification).")
