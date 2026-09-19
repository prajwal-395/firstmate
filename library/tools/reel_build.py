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

The candidates are not the decision.  `judge_take_cuts` sits above them
and withdraws what cannot be one telling removed and a later one kept:
a backwards or empty span (a word-stream window across two simultaneous
speakers runs backwards in time), overlapping tellings, two voices in
one telling, an excision from the middle of a flowing utterance
(lc-0004), and an edge through a timed word.  What it deliberately does
not judge is whether a whole-segment pair is a retake or a refrain tail
(lc-0005): word overlap cannot make that call, so `take_cut_context`
renders the pair's sentences, turn-crossing and novelty for the model
that can, and `possible_retellings` surfaces the cross-turn paraphrases
the cut lane refuses (lc-0006) where the boundary is still open.

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

import hashlib
import os

from library.tools.paths import REMOTION_DIR
from library.tools.frame_utils import span_frames
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN
from library.tools import resolve_bin_layout as bins
from library.tools.resolve_lock import (
    assert_current_timeline, resolve_lease, under_lease)
from library.tools.timeline_ingest import resolve_project_exactly
from library.tools.timeline_layout import (
    EXPLAINER,
    FRAME,
    MOTION_GRAPHICS,
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

    The project's `source.program_stream` declaration first - the
    project's current word, which a stale catalog cannot overrule -
    then the catalog's recorded selection, then the live master
    timeline (its rows already carry only program audio), an explicit
    per-angle map ahead of all of them - what a test passes. Every
    source on an angle must resolve to ONE channel; a source nothing
    recorded, and an angle whose sources disagree, REFUSE rather than
    default: the mix is declared or measured, never stream 0 dressed
    as the mix.
    """
    explicit = dict(explicit or {})
    try:
        from library.tools.footage_identity import (
            declared_program_stream)
        declared = declared_program_stream(project_folder)
    except Exception:
        declared = None
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
            channel = declared
            basis = "the source.program_stream declaration"
            if channel is None:
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
                    f"it off. Declare source.program_stream in the "
                    f"project's project.yaml and re-run catalog_footage, "
                    f"or build from a master whose rows already carry "
                    f"program audio.")
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
                        has_frame: bool = False,
                        explainer_spans=None,
                        semantic_spans=None,
                        lower_third_spans=None,
                        card_role=None,
                        card_spans=()) -> dict:
    """The material `timeline_layout.plan_layout` answers with a plan.

    Angles come from the master's own picture rows (`reel_angles`) and
    every count from what this reel will place - a row exists because
    something goes on it, which is what makes "two speakers collapsed
    onto one row" and "blank rows with nothing on them" structurally
    impossible rather than fixed once. `caption_spans` are (start, end)
    in FRAMES, the unit the layout packs in.

    `card_role` is the declared full-frame CARD row
    (`full_frame_element.CARD_ROW_ROLES`) and `card_spans` the (start,
    end) frame spans of this reel's head/tail cards, in the order they
    play. Both travel into the material so the row exists because a
    card goes on it - and so the placer replays the plan's own packing
    (`timeline_layout.card_spans_for_role` + `lane_of_span`) rather
    than a second reading of it. A `full_frame_span` never arrives
    here: a span IS the body's picture and stays on it.
    """
    if card_role is not None and card_role not in (SEMANTIC,
                                                   MOTION_GRAPHICS):
        raise ReelBuildError(
            f"card_role {card_role!r} names no card row; card roles are "
            f"{SEMANTIC!r} and {MOTION_GRAPHICS!r}. A track index is "
            f"refused here on purpose: indices differ per reel.")
    if card_role is None and card_spans:
        raise ReelBuildError(
            "card spans with no card role: a row exists because "
            "something goes on it, and a span without its role names "
            "no row. Declare effect.card_row_role.")
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
            "explainer_spans": [tuple(s) for s in (explainer_spans or [])],
            "semantic_spans": [tuple(s) for s in (semantic_spans or [])],
            "mg_spans": [tuple(sp) for sp in (lower_third_spans or [])],
            "has_generators": False,
            "timed_text_spans": [], "music_spans": [], "sfx_spans": [],
            "card_role": card_role,
            "card_spans": [tuple(s) for s in (card_spans or [])],
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
        # In FRAMES. A row per overlapping LAYER, not per segment: two
        # tight-box animations that play at once are two rows, which is
        # what the captain asked for on Reel 26 (2026-09-11).
        "explainer_spans": [tuple(s) for s in (explainer_spans or [])],
        "semantic_spans": [tuple(s) for s in (semantic_spans or [])],
        # The speaker lower thirds ride the MOTION_GRAPHICS role - the
        # reel's own row for a graphic that is neither the explainer nor
        # the model's semantic visuals. A row per overlapping LAYER, the
        # same packing the two above get.
        "mg_spans": [tuple(sp) for sp in (lower_third_spans or [])],
        "has_generators": False,
        "timed_text_spans": [], "music_spans": [], "sfx_spans": [],
        # The declared card row and this reel's head/tail card spans, in
        # play order. `plan_layout` packs them with the role's own
        # spans, so the row exists because a card goes on it - and the
        # placer below replays that same packing to find each card's
        # lane rather than indexing the first a-roll row.
        "card_role": card_role,
        "card_spans": [tuple(s) for s in (card_spans or [])],
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
    """{(audio row index): {item uids on it}} - one inventory snapshot.

    A row Resolve will not list reads as None - UNKNOWN - never as
    empty: an empty snapshot would make every pre-existing item on
    that row look ADDED, and the sweep below would DELETE speech it
    never placed. `sweep_placed_audio` skips enforcement on unknown
    rows and reports them unverified.
    """
    out = {}
    for row in plan.speech_rows():
        try:
            items = timeline.GetItemListInTrack("audio", row.index) or []
        except Exception:
            out[row.index] = None
            continue
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
    on the right row are different failures with the same fix. A row
    the AFTER inventory would not list reports spill-unchecked: no
    spill read is no spill found. A row the BEFORE snapshot never saw
    (`_speech_row_uids` None) skips enforcement entirely and reports
    unverified: nothing on it can be told added from pre-existing, so
    deleting there would delete speech this placement may never have
    added.
    """
    after_items: dict = {}
    after_failed = []
    for row in plan.speech_rows():
        try:
            items = timeline.GetItemListInTrack("audio", row.index) or []
        except Exception:
            after_failed.append(row.index)
            continue
        after_items[row.index] = list(items)
    kept, deleted, unverified = [], [], []
    for index in after_failed:
        unverified.append(
            f"{label} (A{index}): spill unchecked - the row would not "
            f"list, so no spill read is reported as none found")
    for index, items in after_items.items():
        seen_before = before.get(index, set())
        if seen_before is None:
            unverified.append(
                f"{label} (A{index}): before-inventory unreadable - "
                f"added cannot be told from pre-existing, so "
                f"enforcement is skipped and the row is kept")
            kept.extend(items)
            continue
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


def _is_held_frame(item) -> bool:
    """Whether this timeline item is a rendered HOLD, not footage.

    `reel_ending` owns both the artefact and the question - this asks
    it with the path Resolve gives, rather than restating the naming
    convention where a later rename would not reach it. An item Resolve
    will not answer for is NOT called a hold: the caller's default is
    to treat it as picture, which is the safe way to be wrong here.
    """
    from library.tools.reel_ending import is_freeze_path
    try:
        pool_item = item.GetMediaPoolItem()
    except Exception:  # noqa: BLE001 - an unreadable item is not a hold
        return False
    if not pool_item:
        return False
    try:
        return is_freeze_path(pool_item.GetClipProperty("File Path"))
    except Exception:  # noqa: BLE001 - same reading
        return False


def link_reel_groups(timeline, plan, offset_links=()) -> dict:
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

    `offset_links` are the `OffsetLink` groups an offset placement
    declared (`plan_j_cut`, `plan_cutaway`): picture and speech that
    deliberately start on different frames. Each is matched to
    timeline items by exact record span and unioned with whatever
    group those items already sit in, then linked in ONE call per
    union - so an offset build keeps the SOP guarantee (picture, its
    speech, and captions inside that speech stay linked) without a
    same-start match. An offset build is strict: anything still
    unlinked afterwards raises `OffsetRefused` naming it, rather than
    placing silently unlinked. Without `offset_links` the legacy
    behaviour stands - leftovers warn and conformance fails them.
    """
    record = {"link_groups": [], "caption_links": [], "warnings": []}
    verified_groups: list = []  # each verified call's member items
    offset_links = list(offset_links or [])

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
        pictures_here = picture_starts.get(start, [])
        if offset_links and not pictures_here:
            # An offset build's heads start where no picture does -
            # the J-cut's early audio, a cutaway's trimmed piece.
            # Linking those speeches among themselves here would
            # pre-merge what the offset unions keep as take-pure
            # groups (and claim captions the hints must place), so
            # the bucket is deferred: a hint links it, or the strict
            # pass refuses it. Without offsets this changes nothing.
            continue
        group = list(pictures_here)
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
            verified_groups.append(list(group))
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
    if offset_links:
        _link_offset_unions(timeline, plan, record, verified_groups,
                            speech_index, caption_items, claimed_captions,
                            offset_links)
    return record


def _link_offset_unions(timeline, plan, record, verified_groups,
                        speech_index, caption_items, claimed_captions,
                        offset_links) -> None:
    """Link what an offset placement declared, strictly.

    Each `OffsetLink` is matched to timeline items by exact record
    span, unioned with the verified groups its items already sit in
    (a cutaway picture joins a grouped speech by re-linking the whole
    union in ONE call - linking is exclusive, so the one call IS the
    group), and linked in one call per union. Claimed captions stay
    with their first host: a hint only ever claims the unclaimed, so
    two speech groups can never merge through one shared caption.

    Strict means strict: a hint matching nothing, a union that reads
    back unlinked, or any a-roll picture or speech item still linked
    to nothing afterwards raises `OffsetRefused` naming it. On success
    the legacy warnings stand resolved and are cleared - every one of
    them names an item this pass just linked.

    The one thing the picture census EXCLUDES is a rendered HOLD
    (`_is_held_frame`), because a held frame has no audio anywhere on
    the timeline and so cannot be linked to anything at all. That is
    the difference between "was not linked" and "could not be" - and a
    gate that cannot tell them apart refuses correct output (AGENTS.md
    10.4). Nothing else is excluded: a full-frame CARD on an a-roll row
    would hit this same census, and is NOT covered here because no
    build has been measured doing it - this project declares
    `effect.card_row_role`, so its cards ride the Motion Graphics row.
    """
    by_speech_span: Dict[tuple, list] = {}
    for start, end, item, _angle_key in speech_index:
        by_speech_span.setdefault((start, end), []).append(item)
    picture_spans: Dict[tuple, list] = {}
    for row in plan.aroll_rows():
        try:
            items = timeline.GetItemListInTrack("video", row.index) or []
        except Exception:
            items = []
        for item in items:
            span = _timeline_span(item)
            if span is None:
                continue
            picture_spans.setdefault((span[0], span[1]), []).append(item)

    parent: Dict[str, str] = {}

    def find(uid):
        parent.setdefault(uid, uid)
        while parent[uid] != uid:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    def union(uids):
        uids = [u for u in uids if u is not None]
        if not uids:
            return
        root = find(uids[0])
        for uid in uids[1:]:
            parent[find(uid)] = root

    members: Dict[str, object] = {}
    for group in verified_groups:
        union([_item_uid(m) for m in group])
        for member in group:
            members[_item_uid(member)] = member

    unions: Dict[str, list] = {}
    for link in offset_links:
        speech_span = (int(link.speech[0]), int(link.speech[1]))
        speeches = by_speech_span.get(speech_span, [])
        if not speeches:
            raise OffsetRefused(
                f"Offset link for speech span {speech_span[0]}-"
                f"{speech_span[1]} matches no speech item on the "
                f"timeline - the placement it was planned from did "
                f"not land, so the offset is refused rather than "
                f"half-linked.")
        # One link, one union: the speech, its pictures and its
        # captions join in a SINGLE union call below, because linking
        # is exclusive - unioning speeches with speeches and pictures
        # with pictures but never the two together would leave the
        # offset group unformed and rip the pictures out of the
        # verified groups they already sit in.
        link_uids = [_item_uid(s) for s in speeches]
        for member in speeches:
            members[_item_uid(member)] = member
        for picture_span in (link.pictures or ()):
            key = (int(picture_span[0]), int(picture_span[1]))
            pictures = picture_spans.get(key, [])
            if not pictures:
                raise OffsetRefused(
                    f"Offset link for picture span {key[0]}-{key[1]} "
                    f"matches no picture item on the timeline - the "
                    f"placement it was planned from did not land, so "
                    f"the offset is refused rather than half-linked.")
            link_uids.extend(_item_uid(p) for p in pictures)
            for member in pictures:
                members[_item_uid(member)] = member
        for speech in speeches:
            for caption in caption_items:
                if _item_uid(caption) in claimed_captions:
                    continue
                span = _timeline_span(caption)
                if span is None:
                    continue
                if speech_span[0] <= span[0] and span[1] <= speech_span[1]:
                    link_uids.append(_item_uid(caption))
                    members[_item_uid(caption)] = caption
        union(link_uids)

    for uid in list(parent):
        unions.setdefault(find(uid), []).append(uid)

    already = set()
    for group in verified_groups:
        already.add(frozenset(_item_uid(m) for m in group))
    for root, uids in unions.items():
        items = [members[u] for u in uids if u in members]
        if frozenset(uids) in already:
            continue
        if len(items) < 2:
            continue
        try:
            ok = timeline.SetClipsLinked(items, True)
        except Exception as exc:
            raise OffsetRefused(
                f"Offset link of {len(items)} items raised {exc!r} - "
                f"refused rather than placed half-linked.")
        if not ok:
            raise OffsetRefused(
                "Resolve declined the offset link group "
                f"({len(items)} items) - refused rather than placed "
                "half-linked.")
        for member in items:
            want = {_item_uid(other) for other in items
                    if other is not member}
            want.discard(None)
            if want and not (want <= _linked_ids(member)):
                raise OffsetRefused(
                    "Offset link group read back unlinked - refused "
                    "rather than placed half-linked.")
        start = min(_timeline_span(m)[0] for m in items
                    if _timeline_span(m) is not None)
        record["link_groups"].append(
            {"speech_start": start, "members": len(items),
             "offset": True})
        for caption in items:
            if caption in caption_items:
                if _item_uid(caption) in claimed_captions:
                    continue
                claimed_captions.add(_item_uid(caption))
                span = _timeline_span(caption)
                record["caption_links"].append(
                    {"caption_start": span[0] if span else start,
                     "caption_end": span[1] if span else start,
                     "speech_start": start,
                     "members": len(items)})

    unlinked = []
    for _start, _end, item, _key in speech_index:
        if not _linked_ids(item):
            span = _timeline_span(item)
            unlinked.append(f"speech at {span[0]}-{span[1]}")
    for row in plan.aroll_rows():
        try:
            items = timeline.GetItemListInTrack("video", row.index) or []
        except Exception:
            # A row the census cannot read is not a row with nothing
            # unlinked: it joins `unlinked` so the OffsetRefused below
            # fires, rather than placing silently over an unchecked row.
            unlinked.append(
                f"picture census on {row.name} unreadable - unverified")
            continue
        for item in items:
            if _is_held_frame(item):
                # A HELD FRAME is a copy of a frame the reel already
                # plays, laid on the ending shot's own row so it
                # inherits that shot's framing and grade
                # (`reel_ending`, AGENTS.md 10.4). It carries no audio
                # and answers no speech, so there is nothing on this
                # timeline for it to link TO - and an item that cannot
                # be linked is not an item that was left unlinked.
                # Measured 2026-09-12 rebuilding Reel 09 through the
                # variant path: every reel whose ending declares
                # `tail_hold: freeze` refused here with "picture at
                # 1650-1669 on Craig", after placing correctly. The
                # ordinary rebuild's own link pass records a warning
                # and carries on; only this offset census raises.
                continue
            if not _linked_ids(item):
                span = _timeline_span(item)
                unlinked.append(
                    f"picture at {span[0]}-{span[1]} on {row.name}"
                    if span else f"picture on {row.name}")
    if unlinked:
        shown = "; ".join(unlinked[:4])
        if len(unlinked) > 4:
            shown += f" (+{len(unlinked) - 4} more)"
        raise OffsetRefused(
            f"Offset build leaves {len(unlinked)} a-roll item(s) "
            f"unlinked: {shown} - refused rather than placed silently "
            f"unlinked.")
    record["warnings"] = []


def reel_resolution(project_folder) -> tuple:
    """The frame this project's reels are built at, DECLARED not assumed.

    Set EXPLICITLY on every reel timeline.  Measured in phase one: the
    PROJECT's own resolution is 3840x2160 and only the existing timelines
    override it, so a timeline created through the API inherits the
    horizontal UHD default.  That is a silent wrong answer rather than an
    error, and sixteen of them would be sixteen rebuilds.

    What it is NOT is a property of the reels product.  This was
    `REEL_RESOLUTION = (1080, 1920)` - a module constant, read by nothing
    but its own test, while sixteen sites below wrote the same two numbers
    by hand.  A reel is a short EXCERPT of a master; nothing about that
    says vertical, and a client asking for the same excerpts as 16:9
    long-form was answerable only by editing sixteen literals.  So the
    frame comes from `library/tools/delivery_format.py` like every other
    consumer of it: the brand template declares it, the project may
    override with `pipeline.delivery_format`, and the default is still
    vertical 1080x1920 - which is what `geo-podcast` resolves to, so every
    reel already built is built at the identical numbers.
    """
    from library.tools.delivery_format import resolve_delivery_format
    width, height = resolve_delivery_format(project_folder or None)
    return (int(width), int(height))


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


#: Why the judge withdrew a candidate cut. Enumerated rather than free
#: text, because the build PRINTS these and a reader has to tell "the
#: span ran backwards" from "the telling was mid-sentence" at a glance.
JUDGE_INVERTED_OR_EMPTY = "inverted_or_empty"
JUDGE_SPANS_OVERLAP = "spans_overlap"
JUDGE_CROSS_SPEAKER = "cross_speaker"
JUDGE_MID_UTTERANCE = "mid_utterance"
JUDGE_MID_WORD_EDGE = "mid_word_edge"

JUDGE_REASONS = (
    JUDGE_INVERTED_OR_EMPTY,
    JUDGE_SPANS_OVERLAP,
    JUDGE_CROSS_SPEAKER,
    JUDGE_MID_UTTERANCE,
    JUDGE_MID_WORD_EDGE,
)

#: A dropped edge within this of a bound segment edge counts as ON that
#: edge. Word timings land to ~10ms; a word-stream window ending a whole
#: telling reads within a few ms of the segment's own edge, while a
#: genuine interior excision sits hundreds of ms inside.
EDGE_TOLERANCE = 0.02


def _edges_share_one_segment(dropped_start: float, dropped_end: float,
                             start: float, end: float,
                             transcript: dict) -> bool:
    """Do both dropped edges sit strictly inside ONE bound segment?

    The mid-utterance test: an excision from a live sentence is interior
    at both ends to the utterance it cuts. A span covering whole
    segments - one or many - always has an edge on a segment boundary,
    so reel 03's finely segmented takes pass however short they are.
    """
    for segment in _segments_in(start, end, transcript):
        try:
            seg_start = float(segment["timeline_start"])
            seg_end = float(segment["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if seg_start < dropped_start and dropped_end < seg_end:
            return True
    return False


def _bound_edge_times(transcript: dict) -> List[float]:
    """Every bound segment edge in time order."""
    from library.tools.reel_proposal import bound_segments

    edges: List[float] = []
    for segment in bound_segments(transcript or {}):
        try:
            edges.append(float(segment["timeline_start"]))
            edges.append(float(segment["timeline_end"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(edges)


def _bound_word_speakers(transcript: dict) -> List[tuple]:
    """`(start, end, speaker)` for every timed word in a BOUND segment.

    Bound only: a straddling segment carries no single source, so its
    speaker claim cannot testify whose telling a word belongs to. Words
    falling in straddling time are ignored by the judge rather than
    judged by a claim nobody anchors.
    """
    from library.tools.reel_proposal import bound_segments

    out = []
    for segment in bound_segments(transcript or {}):
        speaker = segment.get("speaker") or ""
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start:
                out.append((word_start, word_end, speaker))
    return out


def _timed_word_edges(transcript: dict) -> List[tuple]:
    """`(start, end)` for every timed word, bound or straddling.

    The seam half reads every word: a cut edge through ANY timed word
    plays half a word and then jumps, whatever clip anchors it.
    """
    out = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start:
                out.append((word_start, word_end))
    return out


def judge_take_cuts(cuts: Sequence["Cut"], start: float, end: float,
                    transcript: dict) -> tuple:
    """The judge above the take-cut candidates: `(kept, withdrawn)`.

    `redundant_takes` GENERATES - pair-scan plus word-stream, by shared
    words alone. This JUDGES each candidate structurally, and withdraws
    only what cannot be "one telling removed, a later one kept":

    - `inverted_or_empty`: the dropped span runs backwards or covers
      nothing. Measured on Reel 15: a word-stream window straddling two
      simultaneous speakers' segments matched backwards in time, and
      `keep_ranges` laid the reel's ranges over each other - seconds
      played twice.
    - `spans_overlap`: the kept telling starts before the dropped one
      ends. A retake's first telling must END before the second BEGINS;
      overlap is simultaneous speech or bleed, not first-then-retake.
    - `cross_speaker`: the dropped span's own timed words are spoken by
      more than one speaker. One telling has one voice.
    - `mid_utterance`: both dropped edges sit strictly inside ONE bound
      segment, with flowing speech on both sides. That is an excision
      from the middle of a live sentence (lc-0004's "got to get into",
      cut out from between "we've" and "geo geo geo"), not a telling
      removed. A span covering whole segments - however many - passes:
      reel 03's finely segmented takes are whole tellings however short.
    - `mid_word_edge`: a dropped edge lands strictly inside a timed
      word. The build refuses that loudly; the judge says it first,
      with the cut attached, so the decision carries its seam.

    A pair-scan cut drops exactly one bound segment, so its edges sit on
    segment edges and it passes `mid_utterance` by construction. Whether
    a whole-segment drop is a retake or a refrain tail (lc-0005) is a
    semantic judgement word overlap cannot make - not even in this
    direction - so the judge does not try: `take_cut_context` renders
    that pair for the model instead, and the captain's insistence
    remains the backstop.

    Returns `(kept_cuts, withdrawn)` where each withdrawn entry is
    `{"cut", "reason", "why"}` - the cut, the reason code above, and
    one human sentence, because a withdrawal nobody can see is a
    content change nobody can see.
    """
    kept: List["Cut"] = []
    withdrawn: List[dict] = []
    edges = _bound_edge_times(transcript)
    speakers = _bound_word_speakers(transcript)
    word_edges = _timed_word_edges(transcript)

    def _refuse(cut: "Cut", reason: str, why: str) -> None:
        assert reason in JUDGE_REASONS, reason
        withdrawn.append({"cut": cut, "reason": reason, "why": why})

    for cut in cuts or ():
        dropped_start = float(cut.dropped_start)
        dropped_end = float(cut.dropped_end)
        kept_start = float(cut.kept_start)
        kept_end = float(cut.kept_end)
        if not dropped_end > dropped_start:
            _refuse(cut, JUDGE_INVERTED_OR_EMPTY,
                      f"drops {dropped_start:.2f}-{dropped_end:.2f}s, which "
                      f"is backwards or empty - not a telling removed")
            continue
        if kept_start < dropped_end and kept_end > dropped_start:
            _refuse(cut, JUDGE_SPANS_OVERLAP,
                      f"drops {dropped_start:.2f}-{dropped_end:.2f}s while "
                      f"keeping {kept_start:.2f}-{kept_end:.2f}s, which "
                      f"overlaps it - a retake's first telling ends "
                      f"before the second begins")
            continue
        voices = {speaker for word_start, word_end, speaker in speakers
                  if speaker and word_start < dropped_end
                  and word_end > dropped_start}
        if len(voices) > 1:
            _refuse(cut, JUDGE_CROSS_SPEAKER,
                      f"drops {dropped_start:.2f}-{dropped_end:.2f}s whose "
                      f"own words are spoken by {sorted(voices)} - one "
                      f"telling has one voice")
            continue
        if (edges
                and not any(abs(dropped_start - known) <= EDGE_TOLERANCE
                            for known in edges)
                and not any(abs(dropped_end - known) <= EDGE_TOLERANCE
                            for known in edges)
                and _edges_share_one_segment(dropped_start, dropped_end,
                                             start, end, transcript)):
            _refuse(cut, JUDGE_MID_UTTERANCE,
                      f"drops {dropped_start:.2f}-{dropped_end:.2f}s from "
                      f"the middle of one flowing utterance - an excision "
                      f"from a live sentence, not a telling removed")
            continue
        bad_edge = next(
            (edge for edge in (dropped_start, dropped_end)
             if any(word_start < edge < word_end
                    for word_start, word_end in word_edges)),
            None)
        if bad_edge is not None:
            _refuse(cut, JUDGE_MID_WORD_EDGE,
                      f"drops to {bad_edge:.2f}s inside a timed word - "
                      f"the reel would play half a word and then jump")
            continue
        kept.append(cut)
    return kept, withdrawn


#: What ends a sentence for the take context below. WhisperX segments
#: are short clause-level chunks, so sentence bounds are read off
#: terminal punctuation at segment ends and speaker changes - never
#: invented mid-segment.
SENTENCE_TERMINALS = (".", "!", "?", "\u2026")


def sentence_spans(transcript: dict) -> List[dict]:
    """The transcript's sentences as `{start, end, speaker, text}`.

    Built from BOUND segments in time order: a sentence runs across
    same-speaker segments until a segment text ends in terminal
    punctuation or the speaker changes. An unpunctuated run-on stays
    one sentence - splitting it would invent a boundary the
    transcription never measured.
    """
    from library.tools.reel_proposal import bound_segments

    ordered = sorted(
        bound_segments(transcript or {}),
        key=lambda s: (float(s.get("timeline_start", 0.0)),
                       float(s.get("timeline_end", 0.0))))
    spans: List[dict] = []
    current: Optional[dict] = None
    for segment in ordered:
        try:
            seg_start = float(segment["timeline_start"])
            seg_end = float(segment["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if seg_end <= seg_start:
            continue
        speaker = segment.get("speaker") or ""
        text = (segment.get("text") or "").strip()
        if current is None or current["speaker"] != speaker:
            if current is not None:
                spans.append(current)
            current = {"start": seg_start, "end": seg_end,
                       "speaker": speaker, "text": text}
        else:
            current["end"] = seg_end
            current["text"] = (current["text"] + " " + text).strip()
        if text.endswith(SENTENCE_TERMINALS):
            spans.append(current)
            current = None
    if current is not None:
        spans.append(current)
    return spans


def sentence_around(when: float, transcript: dict) -> dict:
    """The sentence sounding at `when`, or an empty text when none was
    measured there. Never invented: a time in straddling or silent
    seconds has no sentence anyone can snap to."""
    for sentence in sentence_spans(transcript):
        if sentence["start"] <= float(when) < sentence["end"]:
            return sentence
    return {"start": float(when), "end": float(when), "speaker": "",
            "text": ""}


def _span_position(span_start: float, span_end: float,
                   sentence: dict) -> str:
    """Where a cut span sits in its sentence: onset, tail, whole or
    middle. The lc-0005 shape is a tail (the sentence's object removed,
    its head orphaned); Reel 28's false start is an onset."""
    if not (sentence or {}).get("text"):
        return "unknown"
    at_start = abs(span_start - sentence["start"]) <= EDGE_TOLERANCE
    at_end = abs(span_end - sentence["end"]) <= EDGE_TOLERANCE
    if at_start and at_end:
        return "whole"
    if at_start:
        return "onset"
    if at_end:
        return "tail"
    return "middle"


def take_cut_context(cut: "Cut", transcript: dict) -> dict:
    """What the model needs to judge one candidate cut, beside its spans.

    The surrounding sentences (not just the two spans), whether the
    repeat crosses a speaker turn, and whether the second telling adds
    anything (`novel_words`, ordered kept content words the dropped
    telling never said). `seam_gap_seconds` is the silence between the
    dropped telling's end and the next timed word - zero means the cut
    edge has nowhere to land but on sound. REPORTS, never decides.
    """
    from library.tools.reel_proposal import (
        _content_words, _content_words_ordered, bound_segments)

    dropped_sentence = sentence_around(float(cut.dropped_start), transcript)
    kept_sentence = sentence_around(float(cut.kept_start), transcript)
    dropped_words = _content_words(cut.dropped_text or "")
    novel = [word for word in
             _content_words_ordered(cut.kept_text or "")
             if word not in dropped_words]
    between = [segment for segment in bound_segments(transcript)
               if float(segment.get("timeline_end", 0.0))
               > float(cut.dropped_end)
               and float(segment.get("timeline_start", 0.0))
               < float(cut.kept_start)]
    turn_speakers = sorted({segment.get("speaker") or ""
                            for segment in between
                            if (segment.get("speaker") or "")
                            != (cut.speaker or "")})
    later = [word_start
             for word_start, _ in _timed_word_edges(transcript)
             if word_start >= float(cut.dropped_end) - 1e-9]
    return {
        "dropped_start": round(float(cut.dropped_start), 2),
        "dropped_end": round(float(cut.dropped_end), 2),
        "dropped_text": (cut.dropped_text or "").strip(),
        "dropped_sentence": dropped_sentence.get("text", ""),
        "dropped_position": _span_position(float(cut.dropped_start),
                                           float(cut.dropped_end),
                                           dropped_sentence),
        "kept_start": round(float(cut.kept_start), 2),
        "kept_end": round(float(cut.kept_end), 2),
        "kept_text": (cut.kept_text or "").strip(),
        "kept_sentence": kept_sentence.get("text", ""),
        "kept_position": _span_position(float(cut.kept_start),
                                        float(cut.kept_end),
                                        kept_sentence),
        "speaker": cut.speaker,
        "containment": round(float(cut.containment), 3),
        "jaccard": round(float(cut.jaccard), 3),
        "crosses_turn": bool(turn_speakers),
        "turn_speakers": turn_speakers,
        "novel_words": novel,
        "gap_seconds": round(float(cut.kept_start)
                             - float(cut.dropped_end), 2),
        "seam_gap_seconds": (round(min(later) - float(cut.dropped_end), 3)
                             if later else None),
    }


#: A retelling suspect needs this many content words a side. Below it
#: are interjections ("Yeah.", "company.") - real echoes, but nothing
#: a span could be redrawn past.
RETELLING_MIN_WORDS = 3


def possible_retellings(start: float, end: float,
                        transcript: dict) -> List[dict]:
    """Paraphrase-class repeats the cut lane refuses, for the model.

    lc-0006's shape: the same speaker says the same thing twice across
    another speaker's turn, reworded past every bar the cut lane holds.
    No threshold can catch that without also catching deliberate
    restatement, so this REPORTS rather than cuts: suspects both
    tellings name with content (multi-word each side), that cross a
    speaker turn, and that touch no audio a confident cut already drops
    (that audio does not play, so reporting it would name a repetition
    the viewer never hears).

    Each entry carries what the model needs to judge it: both
    sentences, the turn crossed (`between_text`), and what the second
    telling adds. Measured on the field test this names exactly one
    pair episode-wide across the approved spans - the lc-0006 telling -
    so the recall it buys costs the model a glance, not a list.
    """
    from library.tools.reel_proposal import _content_words

    cuts = redundant_takes(start, end, transcript)
    dropped_audio = [(float(c.dropped_start), float(c.dropped_end))
                     for c in cuts]
    out = []
    for suspect in suspected_takes(start, end, transcript):
        if (len(_content_words(suspect.dropped_text or ""))
                < RETELLING_MIN_WORDS
                or len(_content_words(suspect.kept_text or ""))
                < RETELLING_MIN_WORDS):
            continue
        context = take_cut_context(suspect, transcript)
        if not context["crosses_turn"]:
            continue
        if any(not (drop_end <= float(suspect.dropped_start)
                    or drop_start >= float(suspect.dropped_end))
               for drop_start, drop_end in dropped_audio):
            continue
        between = " ".join(
            (segment.get("text") or "").strip()
            for segment in _segments_in(start, end, transcript)
            if float(segment.get("timeline_end", 0.0))
            > float(suspect.dropped_end)
            and float(segment.get("timeline_start", 0.0))
            < float(suspect.kept_start))
        entry = {"kind": "possible_retelling",
                 "between_text": between,
                 "why": ("the same speaker says this twice across "
                         "another speaker's turn, reworded past the cut "
                         "bars - the build will play both. Redraw the "
                         "span past one telling, or keep both on purpose.")}
        entry.update(context)
        out.append(entry)
    return sorted(out, key=lambda entry: entry["dropped_start"])


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

    A TIMED word whose span will not parse counts as spoken: it claims
    speech somewhere the reader cannot bound, so absorbing the remnant
    would delete words on the evidence of a parse failure. The caller
    turns any non-None answer into its REFUSING error.
    """
    for segment in transcript.get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                return str(word.get("word", ""))
            if word_end > word_start and word_start < end and word_end > start:
                return str(word.get("word", ""))
    return None


def absorb_wordless_remnants(
        ranges: Sequence[Tuple[float, float]],
        intervals: Sequence[tuple],
        transcript: dict,
        kind: str = "keep exclusion") -> tuple:
    """Fold a cut's own edge-dust back into the cut.

    Cutting at exact word boundaries can strand a sub-floor nub where
    the master clip starts just before the first word - Reel 09's
    LCATL0013 clip starts 0.28s before Craig's 'so', so striking from
    the word left a 6-frame picture+audio item the F7 floor refuses.
    A remnant under `ABSORB_REMNANT_SECONDS` that touches the cut that
    made it and carries NO timed words is absorbed: the cut extends
    over silence nobody can hear. One that carries speech REFUSES,
    naming the cut - extending over words would delete speech nobody
    cut, and shrinking past them invents the boundary instead. Returns
    `(ranges, intervals)` with the intervals extended to what was
    actually cut, so the mid-word check and the operator both read the
    true edges.

    `kind` names what cut the intervals: the default `"keep exclusion"`
    keeps the strike wording byte-identical; `"take cut"` reads the
    take-cut wording instead (redraw the span past the take, which a
    strike cannot do and a take cut can).
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
            elif kind == "take cut":
                raise ReelBuildError(
                    f"REFUSING to build: {ident} strands a "
                    f"{(b - a):.2f}s fragment ({a:.2f}-{b:.2f}s) "
                    f"carrying the word {spoken!r} - placing it fails "
                    f"the readability floor, and cutting it would "
                    f"delete speech the cutter kept. Redraw the span "
                    f"past the take instead.")
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


def absorb_wordless_take_gaps(
        ranges: Sequence[Tuple[float, float]],
        cuts: Sequence["Cut"],
        transcript: dict) -> List[Tuple[float, float]]:
    """Fold wordless dust stranded BETWEEN take cuts back into a cut.

    Two adjacent take cuts can leave a wordless island between them -
    Reel 15 keeps 1221.51-1221.73s (0.22s of room tone) between dropping
    "if you're a salon that specializes in 3D nail art" and "and your
    content is built around that niche, ...", and placing that island
    is a 5-frame picture+audio item the F7 floor refuses, exactly the
    death Reel 13 died with its strike's tail. The strike path absorbs
    its own edge-dust (`absorb_wordless_remnants`); the take path never
    did, so the island survived `reel_ranges` and waited for the gate.
    The island carries no timed words, so one cut extends over it; an
    island carrying speech REFUSES, naming the cut, because cutting it
    would delete speech the cutter kept.
    """
    intervals = [(
        float(cut.dropped_start), float(cut.dropped_end),
        "take cut %.2f-%.2f" % (cut.dropped_start, cut.dropped_end),
    ) for cut in (cuts or [])]
    out, _grown = absorb_wordless_remnants(
        ranges, intervals, transcript, kind="take cut")
    return out


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


def withdraw_insisted_cuts(cuts: Sequence["Cut"],
                           insisted: Sequence[tuple]) -> tuple:
    """Take cuts the captain's keep insistences withdraw.

    Returns `(kept_cuts, withdrawn)` where `withdrawn` is
    `[(cut, insistence_id), ...]` - so a run can SAY which recorded
    insistence removed which cut rather than silently building
    different seconds (`transcript_corrections.record_keep_insistence`).

    A cut is withdrawn when the seconds it DROPS overlap insisted
    seconds at all.  Partial is enough: a cut that removes half of
    what the captain said to keep still removes it.
    """
    kept, withdrawn = [], []
    for cut in cuts or ():
        hit = next(
            (ident for lo, hi, ident in (insisted or ())
             if _overlaps(cut.dropped_start, cut.dropped_end, lo, hi) > 0),
            None)
        if hit is None:
            kept.append(cut)
        else:
            withdrawn.append((cut, hit))
    return kept, withdrawn


def reel_ranges(moment, transcript: dict,
                extra_cuts: Sequence[tuple] = (),
                insisted_spans: Sequence[tuple] = ()) -> List[Tuple[float, float]]:
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
    is untouched. A sub-floor nub a cut strands off a clip's lead-in
    is absorbed where it carries no timed words and refused where it
    carries speech (`absorb_wordless_remnants` for strikes,
    `absorb_wordless_take_gaps` for the wordless islands take cuts
    strand between them). A strike covering the
    whole body raises `ExclusionWipesBody` - the loop drops that reel
    WITH the reason rather than building an empty timeline. A strike
    edge through a word refuses like a take edge, naming the exclusion
    to re-record. The closer is always placed whole: a strike
    overlapping it is not applied here, and the captain picks another
    closer.

    `insisted_spans` are the captain's recorded keep INSISTENCES
    overlapping this moment, as `(start, end, id)` triples
    (`transcript_corrections.insisted_spans_for_span`). They withdraw
    take cuts rather than making them - the opposite direction to
    `extra_cuts`, and the only vocabulary for "that repetition is not
    a repeated take, leave it alone". Empty (the default) builds
    exactly what this built before.

    Past both declarations, `judge_take_cuts` withdraws candidate cuts
    that are structurally indefensible as one telling removed and a
    later one kept (a backwards span, overlapping tellings, two voices
    in one telling, an excision from mid-utterance, an edge through a
    word). The captain's word runs first, so a recorded insistence is
    never hidden behind a structural reason.
    """
    cuts = redundant_takes(moment.timeline_start, moment.timeline_end,
                           transcript)
    # BEFORE the wholeness guard: a withdrawn cut is a cut that is not
    # being made, so judging it for wholeness would refuse a build over
    # an edit nobody is applying.
    cuts, _withdrawn = withdraw_insisted_cuts(cuts, insisted_spans)
    cuts = judge_take_cuts(cuts, moment.timeline_start,
                           moment.timeline_end, transcript)[0]
    # The cut list is checked against the transcript's own runs before it
    # becomes the reel's shape, so a producer that bypassed
    # `redundant_takes` cannot strand a fragment silently.  Take cuts
    # ONLY: a recorded strike is not a take, has no kept take, and must
    # never be judged by take-wholeness.
    assert_takes_are_whole(cuts, moment.timeline_start, moment.timeline_end,
                           transcript)
    ranges = keep_ranges(moment.timeline_start, moment.timeline_end, cuts)
    # Wordless dust two take cuts strand between them is folded back
    # into a cut here - Reel 15's 0.22s island - for the same reason a
    # strike's edge-dust is below: placing it fails the F7 floor, and
    # it carries no words anyone would miss.
    ranges = absorb_wordless_take_gaps(ranges, cuts, transcript)
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
                # The master-transcript range this placement plays, in
                # seconds. The captain's transform overrides anchor to
                # SPOKEN WORDS, and this is the join: words in this
                # range are the words this item speaks, across any
                # rebuild, re-cut or renumbering.
                "master": (overlap_start_f / fps, overlap_end_f / fps),
            })
        cursor_frames += range_frames
    return out


class OffsetRefused(ReelBuildError):
    """An offset placement was asked for and cannot be built safely."""


@dataclass
class OffsetLink:
    """One link group an offset placement declares, by record span.

    `speech` is the (start, end) record span in FRAMES of the speech
    item the group is built around; `pictures` are the record spans of
    the picture items that travel with it. `link_reel_groups` matches
    timeline items to these spans exactly - the transform that moved
    the spans names them, so no tie-break at link time can join a
    speech to its neighbour's picture.
    """

    speech: tuple
    pictures: tuple = ()


@dataclass
class OffsetPlan:
    """An adjusted placements list plus what it needs downstream.

    `placements` replaces the list the transform was given - the build
    loop places from it, so picture, punch-in and frame runs all read
    the moved spans. `links` are the `OffsetLink` groups for
    `link_reel_groups`. `report` is the operator-readable account of
    what moved and what was checked.
    """

    placements: list
    links: list
    report: dict


def _placement_span_frames(place: dict, fps: float) -> tuple:
    """The (start, end) record span of a placement, in whole frames."""
    start = int(place["snapped_record"])
    duration = int(round((place["source_out"] - place["source_in"]) * fps))
    return (start, start + duration)


def _is_audio_place(place: dict) -> bool:
    return getattr(place["clip"], "track_type", "video") != "video"


def _check_audio_row_disjoint(adjusted: list, fps: float,
                              what: str) -> None:
    """No two audio placements from one master row may overlap.

    An offset moves audio cuts; two items on one row overlapping is a
    placement Resolve would trim silently, so it refuses by name.
    Picture rows are not checked here: a cutaway deliberately stacks
    angles on different rows, and rows are disjoint by construction.
    """
    by_row: Dict[tuple, list] = {}
    for place in adjusted:
        if not _is_audio_place(place):
            continue
        clip = place["clip"]
        key = (getattr(clip, "track_index", None),
               getattr(clip, "source_file", ""))
        by_row.setdefault(key, []).append(place)
    for key, items in by_row.items():
        spans = sorted(_placement_span_frames(p, fps) for p in items)
        for (first_s, first_e), (second_s, second_e) in zip(spans,
                                                            spans[1:]):
            if second_s < first_e:
                raise OffsetRefused(
                    f"{what}: audio placements from master row "
                    f"{key[0]} ({(key[1] or '').rsplit('/', 1)[-1]}) "
                    f"overlap at [{second_s}, {first_e}) frames after "
                    f"the move - Resolve would trim one silently, so "
                    f"the offset is refused rather than placed.")


def plan_j_cut(placements_list: Sequence[dict], fps: float,
               join_record_frame: int, lead_frames: int,
               words: Sequence[tuple] = ()) -> OffsetPlan:
    """Move the AUDIO cut earlier than the picture cut at one join.

    Every audio placement starting on `join_record_frame` (the incoming
    take's head on every angle) starts `lead_frames` earlier instead,
    reaching back into its own source; every audio placement ending on
    the join (the outgoing take's tail) ends that much earlier; an
    audio item spanning the join is split at the moved cut. Picture is
    untouched, so the ear crosses the join before the eye does. The
    reel plays the same words in the same order - only which take's
    room the first `lead_frames` of the incoming take are heard under
    changes.

    `words` are (start, end, text) in REEL seconds for the played span.
    A word inside the trimmed tail `[join-lead, join)` refuses: the
    lead must sit inside the pause, not inside speech. The extended
    head reaches source the reel never played, whose words no played
    transcript covers - the report names each lead-in source span so
    the operator checks what it carries (on Reel 09 that span is
    source the rough cut dropped) instead of the gate guessing.

    Returns an `OffsetPlan`: the moved placements, one `OffsetLink`
    per moved head speech (head speech plus its own take's picture,
    matched by angle so a same-start neighbour can never claim it),
    and the report. A join with no audio on it, a lead the source
    cannot supply (`source_in` below the lead), a tail the lead would
    erase, or a post-move audio overlap all raise `OffsetRefused`.
    """
    if lead_frames <= 0:
        raise OffsetRefused(
            f"J-cut lead is {lead_frames} frames - a J-cut with no lead "
            f"is the straight cut it was asked to improve.")
    cut_frame = int(join_record_frame) - int(lead_frames)
    if cut_frame < 0:
        raise OffsetRefused(
            f"J-cut lead of {lead_frames} frames reaches before reel "
            f"frame 0 - the join sits too early for that lead.")

    heads = [p for p in placements_list
             if _is_audio_place(p)
             and int(p["snapped_record"]) == int(join_record_frame)]
    tails = [p for p in placements_list
             if _is_audio_place(p)
             and _placement_span_frames(p, fps)[1] == int(join_record_frame)]
    spanned = [p for p in placements_list
               if _is_audio_place(p)
               and int(p["snapped_record"]) < int(join_record_frame)
               < _placement_span_frames(p, fps)[1]]
    if not heads and not spanned:
        raise OffsetRefused(
            f"J-cut at reel frame {join_record_frame}: no audio starts "
            f"or spans there, so there is no incoming take to lead with.")

    lead_seconds = int(lead_frames) / fps
    for word in (words or ()):
        start_s, end_s = float(word[0]), float(word[1])
        if start_s * fps < int(join_record_frame) and \
                end_s * fps > cut_frame:
            raise OffsetRefused(
                f"J-cut at reel frame {join_record_frame}: the word "
                f"{word[2]!r} ({start_s:.2f}-{end_s:.2f}s) sounds "
                f"inside the {lead_seconds:.2f}s the tail would give "
                f"up - the lead must sit inside the pause, so pick "
                f"a shorter lead or another join.")

    adjusted: list = []
    head_reports = []
    split_tails: list = []
    for place in placements_list:
        if place in heads:
            if float(place["source_in"]) < lead_seconds - 1e-9:
                raise OffsetRefused(
                    f"J-cut at reel frame {join_record_frame}: "
                    f"{getattr(place['clip'], 'source_file', '?').rsplit('/', 1)[-1]} "
                    f"starts its source at {place['source_in']:.2f}s, "
                    f"so a {lead_seconds:.2f}s lead reaches before the "
                    f"file - nothing to hear under the outgoing picture.")
            moved = dict(place)
            moved["snapped_record"] = int(place["snapped_record"]) - int(
                lead_frames)
            moved["record"] = moved["snapped_record"] / fps
            moved["source_in"] = float(place["source_in"]) - lead_seconds
            adjusted.append(moved)
            head_reports.append({
                "file": getattr(place["clip"], "source_file", "?"),
                "record_frame": moved["snapped_record"],
                "lead_in_source": [round(moved["source_in"], 3),
                                   round(float(place["source_in"]), 3)],
            })
        elif place in tails:
            span = _placement_span_frames(place, fps)
            if span[1] - int(lead_frames) <= span[0]:
                raise OffsetRefused(
                    f"J-cut at reel frame {join_record_frame}: the "
                    f"outgoing tail [{span[0]}, {span[1]}) is no longer "
                    f"than the {lead_frames}-frame lead - trimming it "
                    f"erases the item.")
            moved = dict(place)
            moved["source_out"] = float(place["source_out"]) - lead_seconds
            adjusted.append(moved)
        elif place in spanned:
            span = _placement_span_frames(place, fps)
            ratio = (cut_frame - span[0]) / (span[1] - span[0])
            mid_source = (float(place["source_in"])
                          + ratio * (float(place["source_out"])
                                     - float(place["source_in"])))
            tail_piece = dict(place)
            tail_piece["source_out"] = mid_source
            head_piece = dict(place)
            head_piece["snapped_record"] = cut_frame
            head_piece["record"] = cut_frame / fps
            head_piece["source_in"] = mid_source
            adjusted.extend([tail_piece, head_piece])
            split_tails.append(tail_piece)
            head_reports.append({
                "file": getattr(place["clip"], "source_file", "?"),
                "record_frame": cut_frame,
                "lead_in_source": "split at the moved cut - no unplayed "
                                  "source reached",
            })
        else:
            adjusted.append(place)

    _check_audio_row_disjoint(adjusted, fps, "J-cut")

    # Links, named by exact span so link time matches rather than
    # tie-breaks. One per moved head: the head speech plus its own
    # take's picture - same angle first (a continuous angle's own
    # picture is the listening shot's home), then greatest overlap,
    # then the incoming speech on a tie - computed HERE, where the
    # angle is known. Split tails claim their own picture too: a tail
    # the same-start pass already grouped unions with exactly its own
    # group and no second call is made, while a tail it could not
    # group is linked instead of left to the strict pass.
    pictures = [p for p in adjusted if not _is_audio_place(p)]
    links = []

    def _best_picture(speech_span, speech_angle):
        best = None
        best_key = None
        for pic in pictures:
            pic_span = _placement_span_frames(pic, fps)
            overlap = min(speech_span[1], pic_span[1]) - max(
                speech_span[0], pic_span[0])
            if overlap <= 0:
                continue
            key = (0 if _angle_key(pic["clip"]) == speech_angle else 1,
                   -overlap, -pic_span[0])
            if best_key is None or key < best_key:
                best_key = key
                best = pic
        return best

    def _claim_head(head):
        head_span = _placement_span_frames(head, fps)
        best = _best_picture(head_span, _angle_key(head["clip"]))
        if best is None:
            raise OffsetRefused(
                f"J-cut at reel frame {join_record_frame}: the moved "
                f"speech [{head_span[0]}, {head_span[1]}) overlaps "
                f"no picture - it would place unlinked, so the cut is "
                f"refused rather than placed silently.")
        pic_span = _placement_span_frames(best, fps)
        for link in links:
            if link.speech == head_span:
                if pic_span not in link.pictures:
                    link.pictures = link.pictures + (pic_span,)
                return
        links.append(OffsetLink(speech=head_span, pictures=(pic_span,)))

    moved_heads = [p for p in adjusted
                   if _is_audio_place(p)
                   and int(p["snapped_record"]) == cut_frame
                   and _placement_span_frames(p, fps)[1]
                   > int(join_record_frame)]
    for head in moved_heads:
        _claim_head(head)
    for tail in split_tails:
        _claim_head(tail)

    return OffsetPlan(
        placements=adjusted,
        links=links,
        report={"kind": "j_cut",
                "join_record_frame": int(join_record_frame),
                "lead_frames": int(lead_frames),
                "heads_moved": len(head_reports),
                "tails_trimmed": len(tails),
                "spanned_split": len(spanned),
                "lead_ins": head_reports,
                "lead_in_note": (
                    "each lead-in reaches source the reel never played "
                    "- check what it carries before approving the build"),
                })


def plan_cutaway(placements_list: Sequence[dict], fps: float,
                 hide_angle_key: str, window_record_frames: tuple,
                 cover_words: Sequence[tuple] = ()) -> OffsetPlan:
    """Hide one angle's picture over a window, revealing the angle below.

    Every picture placement of `hide_angle_key` overlapping the window
    is trimmed to exclude it (splitting where the window falls
    mid-item); every other angle's picture is untouched, so the
    continuous angle beneath shows through and the audio is never
    moved. A reaction cutaway is this with the hidden angle the
    speaker and the revealed angle the listener.

    `cover_words` are (start, end, text) in REEL seconds spoken by the
    REVEALED angle. Any inside the window refuses: a cutaway to a
    speaker mid-sentence is a jump to someone talking, not a reaction.
    The report names the window and the covering angles, so the choice
    of listening span is said, not implied.

    Every frame of the window must be covered by at least one other
    angle's picture, or the cutaway opens a hole - refused by frame.
    Returns an `OffsetPlan` with one `OffsetLink` per covered speech
    the window picture joins (the speech with the greatest overlap,
    the incoming on a tie).
    """
    window_start, window_end = (int(window_record_frames[0]),
                                int(window_record_frames[1]))
    if window_end <= window_start:
        raise OffsetRefused(
            f"Cutaway window [{window_start}, {window_end}) runs "
            f"backwards - nothing to reveal.")
    for word in (cover_words or ()):
        start_f = int(round(float(word[0]) * fps))
        end_f = int(round(float(word[1]) * fps))
        if start_f < window_end and end_f > window_start:
            raise OffsetRefused(
                f"Cutaway over [{window_start}, {window_end}): the "
                f"revealed angle says {word[2]!r} "
                f"({word[0]:.2f}-{word[1]:.2f}s) inside the window - a "
                f"cutaway to someone mid-sentence is not a reaction. "
                f"Pick a span where they are listening.")

    hidden = [p for p in placements_list
              if not _is_audio_place(p)
              and _angle_key(p["clip"]) == str(hide_angle_key)]
    if not any(_placement_span_frames(p, fps)[1] > window_start
               and _placement_span_frames(p, fps)[0] < window_end
               for p in hidden):
        raise OffsetRefused(
            f"Cutaway over [{window_start}, {window_end}): angle "
            f"{hide_angle_key} has no picture there - nothing to hide.")

    cover = [p for p in placements_list
             if not _is_audio_place(p)
             and _angle_key(p["clip"]) != str(hide_angle_key)]
    uncovered = None
    for frame in range(window_start, window_end):
        if not any(_placement_span_frames(p, fps)[0] <= frame
                   < _placement_span_frames(p, fps)[1] for p in cover):
            uncovered = frame
            break
    if uncovered is not None:
        raise OffsetRefused(
            f"Cutaway over [{window_start}, {window_end}): reel frame "
            f"{uncovered} has no covering picture under angle "
            f"{hide_angle_key} - the cutaway would open a hole there.")
    cover_angles = sorted({_angle_key(p["clip"]) for p in cover
                           if _placement_span_frames(p, fps)[1]
                           > window_start
                           and _placement_span_frames(p, fps)[0]
                           < window_end})

    adjusted: list = []
    for place in placements_list:
        if place in hidden:
            span = _placement_span_frames(place, fps)
            pieces = []
            if span[0] < window_start:
                pieces.append((span[0], min(span[1], window_start)))
            if span[1] > window_end:
                pieces.append((max(span[0], window_end), span[1]))
            total = span[1] - span[0]
            for piece_start, piece_end in pieces:
                if piece_end - piece_start <= 0:
                    continue
                piece = dict(place)
                ratio_start = (piece_start - span[0]) / total
                ratio_end = (piece_end - span[0]) / total
                src_in = float(place["source_in"])
                src_out = float(place["source_out"])
                piece["snapped_record"] = piece_start
                piece["record"] = piece_start / fps
                piece["source_in"] = (src_in
                                      + ratio_start * (src_out - src_in))
                piece["source_out"] = (src_in
                                       + ratio_end * (src_out - src_in))
                adjusted.append(piece)
            # A hidden item fully inside the window leaves no piece:
            # that is the cutaway, not a loss - said in the report.
        else:
            adjusted.append(place)

    # Links: every picture this transform created or exposed must
    # travel with speech, named by exact span so link time matches
    # rather than tie-breaks.
    #
    # - Each surviving hidden piece joins its own take's speech
    #   (same angle first, so a same-length neighbour can never claim
    #   it) - unless a speech starts on the piece's first frame, in
    #   which case the same-start pass already grouped it and no
    #   offset link is made. Abutting the window is the common case
    #   for needing one: the trim moved the picture start off its
    #   speech's start, so nothing starts there anymore.
    # - Each covering item overlapping the window joins a speech only
    #   when no speech starts on its first frame - the continuous
    #   cover groups by start and needs no link, while a cover that
    #   starts mid-reel is what the same-start pass could not group.
    #   A cover that TALKS in the window was refused above via
    #   `cover_words` (continuous audio beds overlap every window, so
    #   an audio-overlap check here would refuse the link every
    #   cutaway needs). The join goes to the speech with the greatest
    #   overlap, the incoming on a tie, so the seam's listener
    #   travels with the seam's words.
    speeches = [p for p in adjusted if _is_audio_place(p)]
    speech_starts = {_placement_span_frames(s, fps)[0]
                     for s in speeches}
    links: list = []
    window_pics = [
        p for p in adjusted
        if not _is_audio_place(p)
        and _angle_key(p["clip"]) != str(hide_angle_key)
        and _placement_span_frames(p, fps)[1] > window_start
        and _placement_span_frames(p, fps)[0] < window_end]

    def _best_speech(pic_span, prefer_angle=None):
        best = None
        best_key = None
        for speech in speeches:
            speech_span = _placement_span_frames(speech, fps)
            overlap = (min(pic_span[1], speech_span[1])
                       - max(pic_span[0], speech_span[0]))
            if overlap <= 0:
                continue
            if prefer_angle is None:
                key = (-overlap, -speech_span[0])
            else:
                key = (0 if _angle_key(speech["clip"]) == prefer_angle
                       else 1, -overlap, -speech_span[0])
            if best_key is None or key < best_key:
                best_key = key
                best = speech
        return best

    def _claim(pic_span, speech_span):
        for link in links:
            if link.speech == speech_span:
                if pic_span not in link.pictures:
                    link.pictures = link.pictures + (pic_span,)
                return
        links.append(OffsetLink(speech=speech_span,
                                pictures=(pic_span,)))

    for place in adjusted:
        if _is_audio_place(place):
            continue
        if _angle_key(place["clip"]) != str(hide_angle_key):
            continue
        pic_span = _placement_span_frames(place, fps)
        if pic_span[0] in speech_starts:
            # A speech starts on this piece's first frame - the
            # same-start pass grouped it, so no offset link is made.
            continue
        best = _best_speech(pic_span,
                            prefer_angle=str(hide_angle_key))
        if best is None:
            raise OffsetRefused(
                f"Cutaway over [{window_start}, {window_end}): the "
                f"trimmed picture [{pic_span[0]}, {pic_span[1]}) "
                f"overlaps no speech - it would place unlinked, so "
                f"the cutaway is refused rather than placed silently.")
        _claim(pic_span, _placement_span_frames(best, fps))

    for pic in window_pics:
        pic_span = _placement_span_frames(pic, fps)
        if pic_span[0] in speech_starts:
            # The same-start pass grouped this cover with the speech
            # starting on its first frame - no offset link is made.
            continue
        best = _best_speech(pic_span)
        if best is None:
            raise OffsetRefused(
                f"Cutaway over [{window_start}, {window_end}): the "
                f"revealed picture [{pic_span[0]}, {pic_span[1]}) "
                f"overlaps no speech - it would place unlinked, so "
                f"the cutaway is refused rather than placed silently.")
        _claim(pic_span, _placement_span_frames(best, fps))

    return OffsetPlan(
        placements=adjusted,
        links=links,
        report={"kind": "cutaway",
                "window_record_frames": [window_start, window_end],
                "hidden_angle": str(hide_angle_key),
                "cover_angles": cover_angles,
                "cover_note": (
                    f"frames [{window_start}, {window_end}) show "
                    f"angle(s) {', '.join(cover_angles)} while the "
                    f"audio stays - chosen because the cover speaks "
                    f"no word inside the window"),
                })


def resolve_cutaway_window_frames(cutaway: dict, cover_clip,
                                  placements_list: Sequence[dict],
                                  fps: float) -> tuple:
    """The reel-frame window a cutaway spec means on THIS build.

    `cutaway["window_seconds"]` are REEL seconds - they move on every
    rebuild that moves the reel's timing (a head card's lead, a
    retimed span, a redrawn closer), while the cover's SOURCE span
    (`cover.source_in/source_out`, checked onto the master clock by
    `verify_cover_clip`) does not. Measured on Reel 09, 2026-09-13: a
    recorded window of frames 574..598 silently meant different
    content after a rebuild, because `plan_cutaway` compares the
    recorded seconds against reel frames and nothing re-derived them.

    So where a cover rides along, the window is re-derived from where
    the cover's placements land on this build, and the recorded window
    is only a cross-check: agreement within one frame returns the
    recorded frames, and anything else raises `OffsetRefused` naming
    the recorded window, where the cover now plays, and the
    re-recorded seconds. A stale window is never used silently.

    Shape (a), deliberately, not source-anchored storage: the cover's
    source span already IS the stable record of intent, so a second
    source-anchored window field would duplicate it with no rule for
    which wins when the two disagree. One anchor, one check.

    Without a cover there is no second anchor, so the recorded window
    is returned as-is - the behaviour this replaced. Master-anchoring
    that case is follow-up work, named by the caller, not done here.
    """
    import os

    window_seconds = cutaway["window_seconds"]
    recorded = (int(round(float(window_seconds[0]) * fps)),
                int(round(float(window_seconds[1]) * fps)))
    if cover_clip is None:
        return recorded
    source_file = str(getattr(cover_clip, "source_file", "?") or "?")
    held = os.path.basename(source_file)
    spans = [_placement_span_frames(place, fps)
             for place in placements_list
             if place.get("clip") is cover_clip]
    if not spans:
        raise OffsetRefused(
            f"Cutaway cover {held} "
            f"[{float(getattr(cover_clip, 'source_in', 0)):.3f}, "
            f"{float(getattr(cover_clip, 'source_out', 0)):.3f}) "
            f"plays nowhere on this reel - the ranges no longer cover "
            f"its master span - so the recorded window "
            f"[{recorded[0]}, {recorded[1]}) has nothing to reveal. "
            f"Re-record the cover span and the window, or drop the "
            f"cutaway. Refused rather than hiding an absence.")
    derived = (min(start for start, _ in spans),
               max(end for _, end in spans))
    if (abs(derived[0] - recorded[0]) <= 1
            and abs(derived[1] - recorded[1]) <= 1):
        return recorded
    raise OffsetRefused(
        f"Cutaway window [{recorded[0]}, {recorded[1]}) "
        f"({recorded[0] / fps:.2f}-{recorded[1] / fps:.2f}s) is stale: "
        f"the cover {held} "
        f"[{float(getattr(cover_clip, 'source_in', 0)):.3f}, "
        f"{float(getattr(cover_clip, 'source_out', 0)):.3f}) now plays "
        f"at reel frames [{derived[0]}, {derived[1]}) "
        f"({derived[0] / fps:.2f}-{derived[1] / fps:.2f}s). A recorded "
        f"reel-seconds window moves on every rebuild - re-record "
        f"window_seconds as [{derived[0] / fps:.4f}, "
        f"{derived[1] / fps:.4f}] and rebuild. The stale window is "
        f"refused rather than used.")


def shift_captions_for_audio_lead(segments: Sequence[dict],
                                  join_record_frame: int,
                                  lead_frames: int,
                                  fps: float) -> tuple:
    """Move caption cards with the speech an audio lead moved.

    A card belongs to the speech containing its END (cards close on
    speech): cards ending on or before the join stay; cards ending
    after it shift earlier by the lead, travelling with the take they
    caption. A card STRADDLING the join shifts whole - its head (the
    outgoing take's last words) displays up to the lead early, the
    standard J-cut compromise - and is REPORTED by segment id rather
    than adjusted silently, so the captain can judge that one card.

    Returns (adjusted_segments, notes); the input dicts are not
    mutated.
    """
    from library.tools.frame_utils import span_frames

    join_frame = int(join_record_frame)
    lead = int(lead_frames)
    adjusted = []
    notes = []
    for segment in (segments or []):
        start, end = span_frames(segment["timeline_start"],
                                 segment["timeline_end"], fps)
        if end <= join_frame:
            adjusted.append(segment)
            continue
        moved = dict(segment)
        moved["timeline_start"] = ((start - lead) / fps
                                   if start >= join_frame
                                   else segment["timeline_start"])
        moved["timeline_end"] = (end - lead) / fps
        if start < join_frame:
            notes.append({
                "segment_id": segment.get("segment_id", "?"),
                "compromise": (
                    f"card [{start}, {end}) straddles the join at "
                    f"{join_frame}: shifted whole with the incoming "
                    f"take, so its head shows up to "
                    f"{lead / fps:.2f}s early"),
            })
        adjusted.append(moved)
    return adjusted, notes


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
                           lead_seconds: float = 0.0,
                           draw_gain: float = FALLBACK_DRAW_GAIN) -> list:
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
        spine, brand_effect={}, brand_style={}, project_folder=project_folder,
        # This reel's declared caption row (`external/reel_caption_row.json`)
        # reaches the plan here, where the style is resolved - a reel that
        # declares none plans exactly as before, on the project value.
        reel_name=name)
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
    # project that declares nothing renders tight - the default since
    # 2026-09-10 (`library/tools/overlay_mode.py`).
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
                              project_folder=project_folder,
                              draw_gain=draw_gain)
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


def card_row_role_for_project(project_folder: str,
                              brand_effect=None) -> str | None:
    """The declared full-frame card row role, or None where undeclared.

    The PROJECT's `project.yaml` wins over the brand template's `effect`
    slot (`full_frame_element.resolve_card_row_role`). `brand_effect`
    None means "read the brand the way `declared_cards` does" - the
    verifier's re-derivation passes nothing and gets the same answer
    the build got. Returns None rather than refusing: the refusal
    belongs at PLACEMENT, where cards exist to need a row
    (`build_reel_timeline`), so planning, dry runs and card-less reels
    never trip on a declaration they do not need.
    """
    from library.tools.full_frame_element import resolve_card_row_role

    if brand_effect is None:
        brand_effect = _brand_effect(project_folder)
    return resolve_card_row_role(brand_effect or {}, project_folder)


def plan_cards(moment, transcript: dict, ranges, project_folder: str,
               fps: float, *, width: int, height: int,
               declarations=None, ending=None, look=None) -> list:
    """Resolve this project's card declarations against ONE reel.

    `width`/`height` are the DECLARED delivery frame, stated by the
    caller - a full-frame card IS the frame, so planning one against an
    assumed frame draws the wrong picture. See
    library/tools/delivery_format.py.

    Returns ``[]`` when nothing is declared.  The facts a bound run
    quotes are measured off `ranges` - the ranges the build is about to
    place - so a card quotes the reel that will exist rather than the
    span the plan asked for.

    `ending` is the reel's declared ending (`library/tools/reel_ending.py`)
    or None.  A freeze holds the last frame after the keep ranges end and
    plays the switch-off over it, so those frames are picture too and a
    TAIL element starts after them - `reel_ending.ending_tail_frames`
    owns that ordering and says why.  Without it a declared closing
    element would land on top of the switch-off, which is two animations
    on the same frames.
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
    if ending is not None:
        from library.tools import reel_ending as _reel_ending
        body_frames += _reel_ending.ending_tail_frames(ending, look)
    # The ranges and the transcript travel too, and only a span reads
    # them: its segments anchor to the keep ranges one by one, so the
    # boundaries sit on the reel's own edit points.  Cards never read
    # them - a card's duration is declared, not measured.
    return ffe.plan_reel_cards(declarations, facts, body_frames, fps,
                               width=width, height=height,
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


def moment_cuts_and_insistences(moment, transcript: dict,
                                keep_exclusions, keep_insistences):
    """This approved moment's own strikes and stay-ins, grown past room tone.

    The cut list the keep ranges are cut from (`reel_ranges`), and the
    insistence spans that withdraw take cuts rather than making them.
    Grown over wordless clip lead-in AND wordless tail, because the nub
    the readability floor refuses forms at either edge - and neither
    growth may cross a timed word.

    Said on the run that honours them: a cut the operator cannot see is
    a silent content change. ONE spelling, here, because the pass-1
    build and `reel.ask` cut from the same list, and two spellings of
    which seconds a reel plays would be two different reels.
    """
    from library.tools import transcript_corrections as _tc
    moment_cuts = _tc.grow_cuts_over_wordless_leadin(
        _tc.exclusion_cuts_for_span(
            moment.timeline_start, moment.timeline_end,
            keep_exclusions),
        transcript)
    moment_cuts, _tails_held = _tc.grow_cuts_over_wordless_tail(
        moment_cuts, transcript)
    for _held in _tails_held:
        print(f"  keep exclusion {_held['id']} ends at "
              f"{_held['end']:.3f}s and was NOT grown forward "
              f"({_held['reason']}) - if the master cuts inside "
              f"the seconds after it, the strike strands a nub "
              f"the readability floor refuses", flush=True)
    for cut_start, cut_end, cut_id in moment_cuts:
        print(f"  keep exclusion {cut_id} cuts "
              f"{cut_start:.2f}-{cut_end:.2f}s from this reel - "
              f"recorded by the captain, applied at build so an "
              f"approved range is honoured rather than re-decided",
              flush=True)
    moment_insisted = _tc.insisted_spans_for_span(
        moment.timeline_start, moment.timeline_end,
        keep_insistences)
    return moment_cuts, moment_insisted


def derive_reel_ranges_and_cards(moment, transcript: dict, master_clips,
                                 project_folder: str, fps: float, name: str,
                                 moment_cuts, moment_insisted, *,
                                 card_declarations, look_decl,
                                 reel_width: int, reel_height: int,
                                 collect_trims: dict | None = None):
    """One reel's keep ranges and planned cards, exactly as pass 1 derives.

    `moment_cuts`/`moment_insisted` are `moment_cuts_and_insistences`
    output. What follows is the loop's own order: `reel_ranges` (a
    strike covering the whole body raises `ExclusionWipesBody` and the
    caller drops that reel WITH the reason), the captain's recorded
    span trims (`captain_edits.retime_ranges`), the declared ending
    (`reel_ending.apply_ending`, whose tail-fit refusal stands here),
    and the full-frame card plan (`plan_cards` - PLANNED only; rendering
    is the build's, the asks need `duration_frames` which planning
    already sets).

    Returns `(ranges, cards, ending_decl)`. Raises where the build loop
    refuses, so a caller that cannot derive this reel writes no ask for
    it - an ask for seconds the reel will not play is a question about
    another reel.

    `collect_trims` (the build loop's) is filled with the trim records
    this call applied or held (`{"applied": [...], "held": [...]}`,
    empty lists where no `span_retime` edit exists) - the per-reel
    build summary's honest source for which captain trims landed,
    without a second computation. The ask path passes nothing and the
    return shape never changes for it.
    """
    ranges = reel_ranges(moment, transcript,
                         extra_cuts=moment_cuts,
                         insisted_spans=moment_insisted)
    # The captain's recorded trims (`span_retime`,
    # `library/tools/captain_edits.py`): applied to the RANGES, before
    # cards derive from them. Trimming placements after captions were
    # planned from untrimmed ranges plays trimmed picture under
    # untrimmed cards - the ranges are the one shape everything reads,
    # so the trim lands here and nowhere else.
    from library.tools import captain_edits as _edits
    try:
        _all_edits = _edits.load_edits(project_folder)
    except Exception as exc:
        raise ReelBuildError(
            f"  {name}: captain_edits cannot be read: {exc}. A "
            f"recorded trim the build cannot read must refuse, "
            f"never build silently past it.")
    if any(e.get("kind") == "span_retime" for e in _all_edits):
        ranges, _rt_applied, _rt_held, _rt_stale = (
            _edits.retime_ranges(
                ranges,
                placements(ranges, master_clips, fps),
                transcript, _all_edits, fps=fps))
        for record in _rt_applied:
            print(f"  Captain edit: span {record['span_index']}'s "
                  f"{record['edge']} trimmed "
                  f"{record['was'][0]:.3f}-{record['was'][1]:.3f}s "
                  f"to {record['now'][0]:.3f}-"
                  f"{record['now'][1]:.3f}s onto "
                  f"{record['anchor_phrase']!r} - "
                  f"{record['reason']}", flush=True)
        for record in _rt_held:
            print(f"  Captain edit: span {record['span_index']}'s "
                  f"{record['edge']} already sits on "
                  f"{record['anchor_phrase']!r} - pin held",
                  flush=True)
        _edits.report_stale(_rt_stale)
        if collect_trims is not None:
            collect_trims["applied"] = list(_rt_applied)
            collect_trims["held"] = list(_rt_held)
    elif collect_trims is not None:
        # No span_retime edit: the summary reads these below, and an
        # absent key there would fail the filing it must never fail.
        collect_trims["applied"] = []
        collect_trims["held"] = []

    # WHERE THIS REEL ENDS (`library/tools/reel_ending.py`), on the
    # same ranges seam and directly after the trims: an ending is a
    # decision about the last keep range, so it lands before cards
    # derive from it, exactly as the trims do.
    from library.tools import reel_ending as _reel_ending
    _ending_decl = _reel_ending.resolve_ending(
        project_folder, name, moment, transcript)
    if _ending_decl is not None:
        ranges, _end_record = _reel_ending.apply_ending(
            ranges,
            placements(ranges, master_clips, fps),
            transcript, _ending_decl, fps)
        _reel_ending.report(_end_record)
        # The declared tail element must have a shot long enough to
        # draw in. Checked HERE, where the answer is a refusal naming
        # both counts, rather than at comp time where
        # `treatment_verify` would undo it to stderr and the reel would
        # ship without it.
        fits = _reel_ending.assert_tail_fits(
            [p for p in placements(ranges, master_clips, fps)
             if getattr(p["clip"], "track_type", "video")
             == "video"],
            _ending_decl, fps, look=look_decl)
        print(f"  Ending: tail element {fits['element']} needs "
              f"{fits['tail_frames']}f, ending shot plays "
              f"{fits['shot_frames']}f", flush=True)

    # Full-frame elements FIRST, because a head card decides where
    # every other thing on this reel starts. PLANNED here; the build
    # renders what planning returns, and the asks read
    # `duration_frames` off the plan - so a declaration that cannot be
    # resolved stops this reel here rather than after a timeline
    # exists (`library/tools/full_frame_element.py`).
    cards = plan_cards(moment, transcript, ranges, project_folder,
                       fps=fps,
                       width=reel_width, height=reel_height,
                       declarations=card_declarations,
                       ending=_ending_decl, look=look_decl)
    if cards:
        print(f"  {len(cards)} full-frame element(s) declared",
              flush=True)
    return ranges, cards, _ending_decl


def write_visual_asks(moment, transcript: dict, ranges, master_clips,
                      project_folder: str, fps: float, name: str,
                      cards, look_decl) -> dict:
    """Write this reel's three visual asks, and return where they went.

    The semantic ask (`reel_semantic_visual.write_request`), the span
    ask (`reel_semantic_visual.write_span_request`) and - where the
    project declares the TV-frame look - the motion ask
    (`reel_look.write_motion_request` over
    `reel_look.motion_spine(placements(...))`). Every one of those
    writers is PURE (no Resolve); the `master_clips` this needs are read
    off the live master timeline, never written to.

    The pass-1 build calls this for every reel it touches, and
    `reel.ask` calls this for every approved reel - ONE spelling, so an
    ask written without a build is byte-identical (past the cosmetic
    `timestamp` re-stamp) to what a throwaway build would have written
    for the same reel. The answers stay with the model in both cases:
    this moves the QUESTION off the build, never the answer.

    Returns `{"reel_semantic": path, "reel_span": path,
    "reel_motion": path, "motion_spine": spine}`. A path is `""` where
    its ask was not written (no timed words, no declared look); the
    spine is None then, and is what the build resolves the motion
    answer against.
    """
    from library.tools import reel_look as _look
    from library.tools import reel_semantic_visual as sem_vis

    semantic_path = sem_vis.write_request(
        moment, transcript, list(ranges), project_folder, fps=fps)
    span_path = sem_vis.write_span_request(
        moment, transcript, list(ranges), project_folder, fps=fps)
    motion_path = ""
    spine = None
    if look_decl is not None:
        spine = _look.motion_spine(
            # The TRIMMED ranges, not a recompute from the moment: a
            # recompute un-trims the captain's span_retime pins and
            # plans motion for seconds the reel no longer plays.
            placements(list(ranges), master_clips, fps,
                       lead_frames=lead_frames(cards, fps)),
            fps)
        motion_path = _look.write_motion_request(
            moment.number, name, spine,
            transcript.get("segments") or [], project_folder)
    return {"reel_semantic": semantic_path, "reel_span": span_path,
            "reel_motion": motion_path, "motion_spine": spine}


def write_reel_asks_for_project(project_folder: str, transcript: dict,
                                only=None, name_suffix: str = "") -> dict:
    """Write every approved reel's three visual asks, without building.

    What `reel.ask` runs. Reads the approved plan and the master
    timeline's transcript, takes a READ-ONLY snapshot of the master
    timeline out of live Resolve (getters only - no timeline is
    created, no cursor lease is taken, no Fusion comp runs), derives
    each reel's keep ranges and planned cards through the SAME
    functions the pass-1 build calls, and writes the three asks through
    `write_visual_asks`. The model's answers are then written beside
    them by hand or by a lane, and the REAL build - pass 2 - places
    what was answered.

    The plan is NOT archived here: the build archives it because the
    next selector run may overwrite it, and an ask run overwrites
    nothing. Moment repair (`snap_moment_to_speech`) and closer
    redraws apply in memory, exactly as the build applies them, so the
    asks describe the seconds the build will play.

    Returns `{"reel_asks": [...], "skipped_by_exclusion": [...]}`. A
    reel whose strike covers its whole body is SKIPPED with the reason
    (`ExclusionWipesBody`), the way the build skips it; anything else
    the build would refuse on refuses here too, before any ask of any
    reel is answered - an ask for seconds the reel will not play is a
    question about another reel.
    """
    from library.tools import transcript_corrections as _tc
    from library.tools.reel_proposal import (
        proposal_path as _proposal_path,
        read_proposal,
        snap_moment_to_speech,
    )
    from library.tools.timeline_ingest import (
        resolve_binding,
        snapshot_timeline,
    )
    import sys as _sys

    fps = 24000 / 1001
    resolve_project_name, master_timeline_name = resolve_binding(
        project_folder)
    if not resolve_project_name or not master_timeline_name:
        raise ReelBuildError(
            f"{project_folder}/project.yaml declares no complete `resolve` "
            f"binding (project_name={resolve_project_name!r}, "
            f"timeline_name={master_timeline_name!r}). A reel is cut FROM a "
            f"master timeline, and a near match lands on another project "
            f"(AGENTS.md 5).")
    project = _connect_resolve_project(resolve_project_name)
    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == master_timeline_name:
            timeline = t
            break
    if not timeline:
        raise ValueError(
            f"Could not find master timeline {master_timeline_name}")
    master_clips = snapshot_timeline(
        timeline, project.GetName()).clips

    proposal_path = str(_proposal_path(project_folder))
    moments = read_proposal(proposal_path)
    from library.tools.reel_ledger import stored_windows as _ask_stored
    stored_siblings: dict = {}
    repair_moves_by_number: dict = {}
    for moment in moments:
        stored_siblings[int(moment.number)] = {
            "body": _ask_stored(moment)[0],
            "closer": _ask_stored(moment)[1],
        }
    repaired = []
    for moment in moments:
        fixed, moves = snap_moment_to_speech(moment, transcript)
        repair_moves_by_number[int(moment.number)] = list(moves)
        for move in moves:
            word = (f" through '{move['through']}'"
                    if move.get("through") else "")
            print(f"  Reel {moment.number:02d}: {move['boundary']} "
                  f"{move['was']:.3f}s -> {move['now']:.3f}s{word} "
                  f"(stored proposal predates the boundary snap)",
                  file=_sys.stderr)
        repaired.append(fixed)
    moments = repaired

    from library.tools import captain_edits as _edits
    try:
        _pin_edits = _edits.load_edits(project_folder)
    except _edits.CaptainEditError as exc:
        raise ReelBuildError(
            f"captain_edits cannot be read: {exc}. A recorded pin the "
            f"ask cannot read must refuse, never ask silently past "
            f"it.") from exc
    _pin_applied: list = []
    if any(e.get("kind") == "redraw_closer" for e in _pin_edits):
        moments, _pin_applied, _pin_held, _pin_stale = \
            _edits.apply_closer_redraws(moments, transcript, _pin_edits)
        for record in _pin_applied:
            print(f"  Reel {record['reel']:02d}: closer "
                  f"{record['was'][0]:.3f}s -> {record['now'][0]:.3f}s "
                  f"(now opens on {record['anchor_phrase']!r} - "
                  f"{record['reason']})", file=_sys.stderr)

    keep_exclusions = _tc.keep_exclusions(project_folder)
    keep_insistences = _tc.keep_insistences(project_folder)

    wanted = reel_numbers(only)
    building = [m for m in moments
                if str(getattr(m.approval, "value", m.approval)) == "approved"
                and (wanted is None or int(m.number) in wanted)]
    if wanted is not None:
        missing = wanted - {int(m.number) for m in building}
        if missing:
            raise ReelBuildError(
                f"asked for reel(s) {sorted(missing)}, and the plan "
                f"{proposal_path} has no APPROVED moment with those "
                f"numbers. Approved: "
                f"{sorted(int(m.number) for m in building)}. An ask that "
                f"quietly skipped them would report success having asked "
                f"nothing.")

    from library.tools import reel_look as _reel_look
    reel_width, reel_height = reel_resolution(project_folder)
    reel_look_decl = _reel_look.resolve_look(project_folder,
                                             reel_width, reel_height)
    card_declarations = declared_cards(project_folder)

    reel_asks = []
    skipped_by_exclusion = []
    skipped_out_of_window = []
    for moment in building:
        name = staging_name(built_name(moment, name_suffix or ""))
        moment_cuts, moment_insisted = moment_cuts_and_insistences(
            moment, transcript, keep_exclusions, keep_insistences)
        try:
            ranges, cards, _ending_decl = derive_reel_ranges_and_cards(
                moment, transcript, master_clips,
                project_folder, fps, name,
                moment_cuts, moment_insisted,
                card_declarations=card_declarations,
                look_decl=reel_look_decl,
                reel_width=reel_width, reel_height=reel_height)
        except ExclusionWipesBody as wiped:
            reason = str(wiped)
            print(f"  SKIPPING {name}: {reason}", flush=True)
            skipped_by_exclusion.append({"reel": name,
                                         "number": moment.number,
                                         "reason": reason})
            continue
        # The window audit, same seam as the build loop: an ask for
        # seconds no declaration covers is a question about another
        # reel, so it is skipped WITH the reason before anything is
        # answered. The ledger is filed either way.
        try:
            from library.tools import reel_ledger as _ask_ledger
            _ask_stored_windows = stored_siblings[int(moment.number)]
            _ask_ledger_entry = _ask_ledger.audit_ranges(
                number=int(moment.number), staging=name,
                final=destage(name),
                stored_body=_ask_stored_windows["body"],
                stored_closer=_ask_stored_windows["closer"],
                repaired_body=(float(moment.timeline_start),
                               float(moment.timeline_end)),
                repaired_closer=cta_range(moment),
                ranges=ranges,
                sibling_windows=stored_siblings,
                repair_moves=repair_moves_by_number.get(
                    int(moment.number), ()),
                pin_records=[
                    record for record in _pin_applied
                    if int(record.get("reel", -1)) == int(moment.number)],
                trim_records={},
                ending=_ending_decl)
        except _ask_ledger.OutOfWindowRange as out_of_window:
            reason = str(out_of_window)
            print(f"  SKIPPING {name}: {reason}", flush=True)
            try:
                _ask_ledger.file_reel_ledger(
                    project_folder, destage(name),
                    out_of_window.ledger or {})
            except Exception:
                pass
            skipped_out_of_window.append({"reel": name,
                                          "number": moment.number,
                                          "reason": reason})
            continue
        _ask_ledger.file_reel_ledger(
            project_folder, destage(name), _ask_ledger_entry)
        for _row in _ask_ledger_entry.get("ranges") or []:
            _over = (_row.get("overhang_seconds") or {})
            if not (_over.get("before") or _over.get("after")):
                continue
            _invaded = ", ".join(
                f"reel {hit['reel']}'s declared {hit['window']} "
                f"({hit['seconds'][0]:.2f}-{hit['seconds'][1]:.2f}s)"
                for hit in (_row.get("invades") or [])) or (
                "no other reel's declared pool")
            print(f"  {name}: {_row['origin']} range "
                  f"{_row['master'][0]:.2f}-{_row['master'][1]:.2f}s "
                  f"reaches "
                  f"{_over.get('before', 0):.2f}s before / "
                  f"{_over.get('after', 0):.2f}s past its declared "
                  f"{_row['origin']} window - overlapping "
                  f"{_invaded}", flush=True)
        paths = write_visual_asks(
            moment, transcript, ranges, master_clips,
            project_folder, fps, name, cards, reel_look_decl)
        reel_asks.append({"reel": name, "number": int(moment.number),
                          "reel_semantic": paths["reel_semantic"],
                          "reel_span": paths["reel_span"],
                          "reel_motion": paths["reel_motion"]})
    return {"reel_asks": reel_asks,
            "skipped_by_exclusion": skipped_by_exclusion,
            "skipped_out_of_window": skipped_out_of_window}



def reel_explainer_segments(moment, transcript: dict, ranges,
                            project_folder: str, fps: float, width: int,
                            height: int, judgement=None,
                            brand_effect=None, timeline_name: str = "",
                            draw_gain: float = FALLBACK_DRAW_GAIN):
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

    bands = _explainer_bands(project_folder, width, height,
                             draw_gain=draw_gain)
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
        safe_area=insets, project_folder=project_folder or "")

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
            project_folder=project_folder,
            draw_gain=draw_gain,
            # `reuse=True`: the cache-and-pair, the same opt-in the
            # reel caption path carries. The file is content-keyed
            # (`mg_<project>_<digest>`, step 4.06), so a rebuild - or
            # a sibling variant - rendering the same graphic pairs
            # back to the file already on disk instead of paying a
            # Chromium launch for identical pixels. The per-variant
            # `vox_<reel>_<index>` name travels as the entry's
            # `placement_label`, never as the file's identity.
            reuse=True)
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


def reel_lower_third_segments(moment, transcript: dict, ranges,
                              project_folder: str, fps: float, width: int,
                              height: int, brand_effect=None,
                              timeline_name: str = "",
                              subtitle_segments=None,
                              lead_seconds: float = 0.0,
                              extra_cuts: Sequence[tuple] = (),
                              draw_gain: float = FALLBACK_DRAW_GAIN):
    """One reel's SPEAKER lower thirds, THROUGH THE PIPELINE'S OWN STEPS.

    The same shape :func:`reel_explainer_segments` has, with the same
    discipline: no graphics logic of its own, named operations only, and
    every module it calls is one the master path already calls.

        reel_quality_bar.played_speech      the reel's lines, in reel time
        speaker_identity.plan_for_reel      who appears first, and where
        motion_graphics_plan.resolve_plan   step 4.06's OWN resolver
        motion_graphics_plan.plan_segments  step 4.06's OWN clusterer
        motion_graphics.render_segment      step 4.06's OWN renderer
        explainer_plan.measure_render       the ink, off the render

    Returns `(segments, plan)`. The plan is returned even when nothing
    is drawn, because a reel with no lower thirds must be able to say
    WHICH kind of nothing it has (`speaker_identity.BASES`).

    **Rendered FULL CANVAS as the probe, then bound tightly from
    its own pixels.** `mg_tight_box` PREDICTS a motion-graphics union
    from the composition's literals and the placement rides on that
    prediction, so these graphics never take the predicted path.
    Instead the full-canvas render is measured across every frame
    (`measure_mg_union`), the canvas is cut around that union, and the
    tight file is a crop of the probe - never a re-render, so the copy
    cannot re-wrap. `verify_measured_crop` proves the tight file IS
    its probe's region; a segment that does not bind stays full
    canvas and says why (`tight_fallback`).
    """
    import sys

    from library.tools import explainer_plan as ex
    from library.tools import motion_graphics_plan as mg
    from library.tools import operations
    from library.tools import speaker_identity as si
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_quality_bar import played_speech

    name = timeline_name or moment.timeline_name

    # The reel's own lines, in the order the reel plays them. `lead` is
    # what a HEAD full-frame card pushes the whole reel down by
    # (`reel_time`'s own parameter): `played_speech` measures from the
    # first kept frame, and a card in front of it moves every second
    # after it.
    lines = [{"speaker": line["speaker"],
              "reel_start": float(line["reel_start"]) + float(lead_seconds),
              "text": line["text"]}
             for line in played_speech(moment, transcript,
                                       extra_cuts=extra_cuts)]
    reel_seconds = (sum(max(0.0, end - start) for start, end in (ranges or []))
                    + float(lead_seconds))

    plan = si.plan_for_reel(
        reel_name=name, lines=lines, reel_seconds=reel_seconds,
        project_folder=project_folder, brand_effect=brand_effect,
        width=width, height=height,
        subtitle_segments=subtitle_segments or [])

    # Every refusal is SAID on the run that made it: a speaker declared
    # and then not named is a claim the viewer is shown half of.
    for refusal in plan.refused:
        print(f"  {name}: LOWER THIRD REFUSED ({refusal['reason']}) "
              f"{refusal['speaker']!r} - {refusal['detail']}",
              file=sys.stderr)
    if not plan.entries:
        if plan.declared:
            print(f"  {name}: NO SPEAKER LOWER THIRDS - {plan.basis}",
                  file=sys.stderr)
        return [], plan
    for introduction, entry in zip(plan.introductions, plan.entries):
        line = (f"  {name}: lower third for {introduction.speaker!r} at "
                f"{introduction.at_seconds}s - {introduction.name!r} / "
                f"{introduction.title!r}, colour {introduction.colour} from "
                f"{introduction.colour_basis}")
        truncated = (entry.get("data") or {}).get("truncated_for_next")
        if truncated:
            line += (f" - truncated to "
                     f"{truncated['duration_seconds']}s from "
                     f"{truncated['hold_seconds']}s, ends where the next "
                     f"card begins at {truncated['next_starts_at']}s")
        print(line, file=sys.stderr)
    print(f"  {name}: lower-third box {plan.box} - {plan.box['_basis']}",
          file=sys.stderr)

    resolved = mg.resolve_plan(
        plan.entries, timeline_duration=reel_seconds, fps=fps,
        palette_roles={}, asked=True)
    for dropped in resolved.dropped:
        print(f"  {name}: lower third dropped ({dropped.reason}) "
              f"{dropped.element}: {dropped.detail}", file=sys.stderr)
        plan.refused.append({"speaker": "", "reason": dropped.reason,
                             "detail": dropped.detail})
    # The overlap measurement, READ on this path rather than computed
    # and discarded. `basis_record` is where `overlapping_pairs` runs;
    # step 4.06's post-bridge is its only other reader, so before this
    # line no lower-third overlap could ever surface anywhere.
    plan.overlaps = resolved.basis_record()["drawn_through_each_other"]
    for pair in plan.overlaps:
        print(f"  {name}: lower thirds overlap: "
              f"{pair['elements']} at {pair['anchors']} share frames "
              f"{pair['frames']} - {pair['why']}", file=sys.stderr)
    if not resolved.moments:
        plan.basis = si.NO_DECLARED_SPEAKER_SPOKE
        return [], plan

    insets = {key: int(value) for key, value in plan.box.items()
              if key in ("top", "right", "bottom", "left")}
    segments_plan = mg.plan_segments(
        resolved.moments, fps=fps, width=width, height=height,
        safe_area=insets, project_folder=project_folder or "")

    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.MOTION_GRAPHICS_SEGMENTS, step="render_motion_graphics"))
    render = operations.get("motion_graphics.render_segment")
    segments = []
    for index, planned in enumerate(segments_plan):
        rendered = render.run(
            planned, out_dir,
            segment_name=f"lt_{_reel_slug(name)}_{index:02d}",
            progress=f"[{index + 1}/{len(segments_plan)}]",
            project_folder=project_folder,
            draw_gain=draw_gain,
            # FULL CANVAS, explicitly - see this function's docstring.
            # Explicit values win over the project's declaration in
            # `render_one_segment`, which is what makes this sayable at
            # all rather than a project-wide opt-out.
            overlay_geometry="full",
            reuse=True)
        if rendered is None:
            continue
        # LOOK AT WHAT WAS DRAWN. The placement claim is checked against
        # pixels, never asserted: an error REFUSES the segment rather
        # than placing a name on the caption row.
        #
        # MEASURED AT THE MIDPOINT, not the last frame. The explainer
        # reads its last frame because every stage of a build is up by
        # then; a lower third with a fade or a retract EXIT is nearly
        # gone on its last frame, and measuring there would read a
        # nearly-empty picture as the graphic's extent. The midpoint is
        # past the entrance and before the exit for every character in
        # the vocabulary.
        midpoint = max(0, int(planned["total_frames"]) // 2)
        try:
            measured = ex.measure_render(rendered["overlay_path"],
                                         frame=midpoint)
        except ex.ExplainerError as why:
            print(f"  {name}: lower-third render could not be measured - "
                  f"{why}", file=sys.stderr)
            measured = None
        if measured is not None:
            rendered["measured"] = measured
            rendered["measured_box"] = si.measured_box(measured)
            refused = False
            for finding in si.render_findings(measured, insets, width, height):
                print(f"  {name}: LOWER THIRD "
                      f"{finding['severity'].upper()} ({finding['code']}) "
                      f"{finding['message']}", file=sys.stderr)
                refused = refused or finding["severity"] == "error"
            if refused:
                plan.refused.append({
                    "speaker": "", "reason": si.INK_LEFT_THE_BOX,
                    "detail": f"segment {index} refused after measurement"})
                continue
            print(f"  {name}: lower-third ink measured at "
                  f"{rendered['measured_box']} on a {width}x{height} frame",
                  file=sys.stderr)
        _bind_lower_third_tight(
            name, planned, rendered, width, height,
            draw_gain=draw_gain)
        segments.append(rendered)
    # What was RENDERED, not what was intended: a reader grading the
    # timeline against this must not look for an item nothing placed.
    plan.segments = list(segments)
    if not segments:
        plan.basis = si.NO_DECLARED_SPEAKER_SPOKE
    return segments, plan


def _bind_lower_third_tight(name: str, planned: dict, rendered: dict,
                              width: int, height: int,
                              draw_gain: float = FALLBACK_DRAW_GAIN
                              ) -> None:
    """Bind one rendered lower third tightly, from its own pixels.

    One spelling, shared with step 4.06's own renderer: the render is
    the probe and the pixels decide
    (`library.tools.mg_tight_box.bind_probe_tight`). Mutates
    `rendered` in place, exactly as that function documents.
    """
    from library.tools import mg_tight_box as mgt

    mgt.bind_probe_tight(name, planned, rendered, width, height,
                         draw_gain=draw_gain, kind="lower third")


def _lower_third_rows(segments) -> list:
    """What a speaker lower third contributes to the rebuild digest.

    Its identity, its placement and the box it really drew in. The
    file is content-keyed (`mg_<project>_<digest>`), so two reels
    naming the same speaker for the same length share one file and one
    `segment_id` - which means the id alone cannot tell a graphic that
    MOVED from one that did not. `timeline_start` is what sees that.

    `measured_box` is in because a graphic whose ink landed somewhere
    else is a different placement even where every number the plan
    wrote agrees, and the rebuild decision is fail-closed
    (`library/tools/reel_rebuild_need.py`).
    """
    rows = []
    for segment in segments or ():
        if not isinstance(segment, dict):
            rows.append({"unreadable": repr(segment)[:120]})
            continue
        rows.append({
            "segment_id": segment.get("segment_id"),
            "timeline_start": segment.get("timeline_start"),
            "total_frames": segment.get("total_frames"),
            "measured_box": list(segment.get("measured_box") or ()),
        })
    return rows


def _reel_slug(timeline_name: str) -> str:
    """A timeline name as a filename-safe placement label."""
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", str(timeline_name or "reel")).strip(
        "_").lower()[:48] or "reel"


def _explainer_bands(project_folder: str, width: int, height: int,
                     draw_gain: float = FALLBACK_DRAW_GAIN):
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
                                dict(IDENTITY), draw_gain=draw_gain)
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


def _held_property(item, prop: str):
    """What Resolve actually holds for one transform property, or None.

    None means the read-back itself is unavailable - a proxy that does
    not serve `GetProperty`, an exception, a null read - and the caller
    falls back to judging the `SetProperty` return, the old behaviour,
    rather than refusing a placement it cannot see.  No `hasattr`:
    always True on Resolve's proxies, invented names included
    (AGENTS.md 5).  Same contract as
    `overlay_placement._read_back`, spelled here because that one is
    private to the caption path.
    """
    try:
        read = item.GetProperty(prop)
    except Exception:  # noqa: BLE001 - judged below, not raised
        return None
    if read is None:
        return None
    try:
        return float(read)
    except (TypeError, ValueError):
        return None


def assert_punch_took(name: str, item, source_file: str, properties: dict,
                      source_size, frame_width: int, frame_height: int,
                      screen_window,
                      draw_gain: float = FALLBACK_DRAW_GAIN) -> None:
    """Raise unless the transform Resolve HOLDS covers the screen window.

    PR 862's discipline, applied to the punch-in: `SetProperty` returns
    True past Resolve's silent Pan/Tilt clamp and reads back the clamp,
    so judging the return alone reports a clamped picture placed while
    it sits off the screen.  On a punch-in the same lie reads at the
    gate as black inside the television - Reel 09's 6x F12, 2026-09-09,
    where every item delivered the identical identity rect because the
    aim never took.  What matters is not what was asked for, it is what
    is held, so the held transform is graded against the window with
    the same predicate F12 grades with
    (`reel_look.uncovered_window_edges`).

    Read-back unavailable on every property falls back to the
    `SetProperty` return the caller already judged - refusing a
    placement that cannot be seen would trade an ungradeable item for
    a missing one, the same line `overlay_placement` draws.
    """
    from library.tools import reel_look as _look
    from library.tools.reel_framing import delivered_picture

    held = {key: _held_property(item, key) for key in properties}
    if all(value is None for value in held.values()):
        return
    effective = {key: (held[key] if held[key] is not None
                       else float(properties[key]))
                 for key in properties}
    delivered = delivered_picture(
        source_size[0], source_size[1],
        frame_width, frame_height, effective, draw_gain=draw_gain)
    bands = _look.uncovered_window_edges(delivered, screen_window)
    if bands:
        held_desc = ", ".join(
            f"{key}={held[key]:g}" if held[key] is not None
            else f"{key}=unreadable" for key in properties)
        raise _look.PunchInLeavesBlack(
            f"{name}: the punch-in did not take on "
            f"{os.path.basename(source_file)} - Resolve holds "
            f"{held_desc} and the picture is {delivered.rect} "
            f"against the screen window "
            f"({screen_window[0]:.0f}, {screen_window[1]:.0f}, "
            f"{screen_window[2]:.0f}, {screen_window[3]:.0f}): "
            f"{', '.join(bands)}. A picture that does not reach the "
            f"edges of the screen shows the set's own background "
            f"through it.")


def _recorded_first_measure(project_folder: str):
    """The default aim with a memory: records first, probes once.

    Returns a `measure(source_file, source_in, source_out)` callable
    with the same contract as
    `subject_framing.measure_subject_in_window` - a `SubjectPoint`, or
    None when the frames hold no aimable face, or
    `SubjectProbeUnavailable` when no detector exists AND no record
    does either.  After every call the callable's `last_provenance`
    names where the answer came from: `{"basis": "recorded", ...}` or
    `{"basis": "probed", ...}`, so the build can say it.
    """
    from library.tools.subject_framing import measure_subject_in_window

    def _measure(source_file, source_in, source_out):
        from library.tools.subject_framing import (
            read_recorded_subject, record_subject_measurement)

        subject, provenance = read_recorded_subject(
            project_folder, source_file, source_in, source_out)
        if provenance is not None:
            _measure.last_provenance = provenance
            return subject
        subject = measure_subject_in_window(
            source_file, source_in, source_out)
        _measure.last_provenance = record_subject_measurement(
            project_folder, source_file, source_in, source_out,
            subject) or {"basis": "probed"}
        return subject

    _measure.last_provenance = None
    return _measure


def aim_picture_row(name: str, look: dict, screen_window,
                    frame_width: int, frame_height: int,
                    row_items, row_places,
                    measure=None, size_of=None, project_folder=None,
                    draw_gain: float = FALLBACK_DRAW_GAIN) -> int:
    """Aim one picture row's punch-in, shot by shot, and prove it took.

    `row_items` are the row's timeline items in play order, `row_places`
    the placements that landed on it sorted by record frame - the two
    zip, so each punch-in is aimed at its own shot's speaker.  `measure`
    defaults to `subject_framing.measure_subject_in_window` and
    `size_of` to `_source_frame_size`; both are parameters so tests can
    drive this with fakes instead of Resolve and footage.

    `project_folder` turns the default measure into a recorded-first
    chain: a window measured on an earlier build is read from the
    project's sidecar (`subject_framing.read_recorded_subject`) and the
    detector never runs; a window with no record is probed once and
    filed (`record_subject_measurement`).  A rebuild with a warm cache
    therefore aims every shot without decoding a frame, and a build
    environment whose face detector is broken can no longer move the
    picture - the failure it used to cause (an incapacitated probe, or
    a re-probe that silently disagrees) cannot happen where no probe
    runs.  An explicitly passed `measure` (the tests) is used as-is.

    Returns how many shots were aimed.  A shot with no subject
    measurement plays uncropped rather than punched at a guess
    (captain, 2026-09-09: a centred 2.30 put Craig out of shot
    entirely) and is SAID.  Two failures raise instead of shipping a
    timeline the gate is guaranteed to delete:

    - the probe itself is incapacitated (`SubjectProbeUnavailable` -
      no face detector in this interpreter) becomes a `ReelLookRefused`
      naming the shot, because every shot would land uncropped and the
      6x identical-rectangle F12 that follows names only the symptom;
    - a transform Resolve did not hold (`assert_punch_took`) raises
      `PunchInLeavesBlack`, the same refusal the computed-properties
      post-condition raises, now grading what is HELD.
    """
    import sys

    from library.tools import reel_look as _look
    from library.tools.subject_framing import (
        SubjectProbeUnavailable, measure_subject_in_window,
        read_recorded_subject, record_subject_measurement)

    if measure is None:
        if project_folder is not None:
            measure = _recorded_first_measure(project_folder)
        else:
            measure = measure_subject_in_window
    if size_of is None:
        size_of = _source_frame_size
    aimed = 0
    for index, item in enumerate(row_items):
        if index >= len(row_places):
            break
        place = row_places[index]
        source_file = place["clip"].source_file
        try:
            subject = measure(
                source_file, place["source_in"], place["source_out"])
        except SubjectProbeUnavailable as exc:
            raise _look.ReelLookRefused(
                f"{name}: the punch-in cannot be aimed on "
                f"{os.path.basename(source_file)} "
                f"({place['source_in']:.2f}-{place['source_out']:.2f}s) - "
                f"{exc}") from exc
        source_size = size_of(item)
        if source_size is None:
            raise ReelBuildError(
                f"{name}: Resolve reports no resolution for "
                f"{os.path.basename(source_file)}, so the punch-in "
                f"cannot be aimed and must not be guessed at.")
        provenance = getattr(measure, "last_provenance", None)
        if isinstance(provenance, dict) and provenance.get("basis") == "recorded":
            basis = (f"recorded {provenance.get('measured_at') or 'date unknown'}"
                     + (f" ({(provenance.get('detector') or {}).get('cv2', '?')})"
                        if isinstance(provenance.get("detector"), dict) else ""))
        elif isinstance(provenance, dict):
            basis = "probed this build"
        else:
            basis = None
        properties = _look.punch_in_properties(
            look, subject, source_size[0], source_size[1],
            frame_width, frame_height, window=screen_window,
            draw_gain=draw_gain)
        if properties is None:
            print(f"  {name}: NO PUNCH-IN on "
                  f"{os.path.basename(source_file)} "
                  f"({place['source_in']:.2f}-{place['source_out']:.2f}s) "
                  f"- {_look.PUNCH_IN_REFUSED_NO_SUBJECT if subject is None else _look.PUNCH_IN_REFUSED_NOT_A_CLOSE_UP}"
                  f"{f' ({basis})' if basis else ''}"
                  f": an unaimed crop is a guess about where the "
                  f"speaker is. The shot plays uncropped.",
                  file=sys.stderr)
            continue
        for key, value in properties.items():
            # Judged by what it RETURNS (AGENTS.md 5) - and then READ
            # BACK (`assert_punch_took` below), because the return is a
            # lie past Resolve's silent clamp (PR 862).
            if not item.SetProperty(key, value):
                raise ReelBuildError(
                    f"{name}: Resolve refused {key}={value} on "
                    f"{item.GetName()!r}. The look declares a punch-in "
                    f"and a clip that did not take it plays at a "
                    f"different size to the ones beside it.")
        assert_punch_took(name, item, source_file, properties,
                          source_size, frame_width, frame_height,
                          screen_window, draw_gain=draw_gain)
        aimed += 1
        print(f"  {name}: punch-in {properties['ZoomX']:.4f} "
              f"(declared {look['punch_in']}, screen window needs "
              f"{_look.window_zoom_for(look, source_size, frame_width, frame_height):.4f}) "
              f"aimed at "
              f"subject x={subject.center_x} y={subject.center_y} "
              f"({subject.detected}/{subject.samples} frames)"
              f"{f' [{basis}]' if basis else ''} on "
              f"{os.path.basename(source_file)} -> Pan "
              f"{properties['Pan']}, Tilt {properties['Tilt']}",
              file=sys.stderr)
    return aimed


def _with_freeze(placements_list, freeze, fps: float) -> list:
    """The picture placements plus the declared hold, in play order.

    The Fusion manifest is built from a FRESH `placements()` call
    outside `build_reel_timeline`, so the hold the build rendered has
    to be joined back on here - otherwise `power_effects` arms the tail
    element on the live tail it was moved off, and the freeze plays
    with nothing drawn over it. `None` returns the list unchanged,
    which is every reel that declares no freeze.
    """
    if freeze is None:
        return list(placements_list)
    from library.tools import reel_ending as _ending_owner

    return list(placements_list) + [
        _ending_owner.freeze_placement(freeze, fps)]


def _inherit_freeze_treatment(name: str, timeline, track_plan,
                              video_row_by_angle: dict, freeze) -> dict:
    """Give the held frame the treatment of the shot it holds.

    A freeze is the ending shot's LAST FRAME, so it must draw like that
    frame. Two things decide how a picture item draws and neither is
    safe to recompute for a one-frame hold:

    * the punch-in TRANSFORM. The aim is a face probe on the clip's own
      pixels, and the captain's `transform_override` is anchored to
      SPOKEN WORDS - a held frame speaks none, so it would keep its
      computed aim while the shot beside it holds a hand value. Reel 13
      would have jumped from Pan -12 to Pan 46 on its final frames.
    * the GRADE. `CopyGrades` moves the Color-page nodes across whole,
      which is the only reading that cannot drift from the shot's.

    Both are judged by what Resolve RETURNS, and the transform is read
    back after it is set (AGENTS.md 5). Returns the record - what was
    inherited and what was read back - so the build can say it rather
    than assume it.

    THE HOLD IS NOT ALWAYS THE LAST ITEM ON ITS ROW.  A project's
    declared closing element (`effect.full_frame_elements`, placement
    `tail`) is placed on `track_plan.aroll_rows()[0]`, and a reel whose
    ending shot is that same first angle therefore carries the card
    AFTER the hold.  Reading `items[-1]` found the card and refused
    every such rebuild - measured 2026-09-12 bringing the captain's
    seven reels current: Reel 01 refused outright, and Reels 13, 23, 30
    and 31 would have.  Reel 26 passed only because its closer is the
    OTHER speaker, so the card and the hold landed on different rows -
    luck, not a rule.  The hold is found by the FILE this build
    rendered.
    """
    import sys

    row = video_row_by_angle.get(str(freeze.track_index))
    record = {"row": row, "held_from": freeze.held_from,
              "frames": freeze.duration_frames, "properties": {},
              "grades_copied": False}
    if row is None:
        raise ReelBuildError(
            f"{name}: the freeze tail names master row "
            f"{freeze.track_index} and that row is no reel angle, so "
            f"the hold has nowhere to sit.")
    items = timeline.GetItemListInTrack("video", row) or []
    if len(items) < 2:
        raise ReelBuildError(
            f"{name}: the freeze tail was placed on V{row} but that row "
            f"carries {len(items)} item(s) - there is no shot in front "
            f"of the hold to inherit from.")
    # The freeze is found by the FILE THIS BUILD RENDERED, never by
    # being last on the row. Measured 2026-09-12 rebuilding Reel 01:
    # the project's declared closing card (`effect.full_frame_elements`)
    # is placed TAIL on `aroll_rows()[0]`, so on every reel whose ending
    # shot is that same first angle the card sits after the hold and
    # `items[-1]` is the card. Reel 26 passed only because its closer is
    # the OTHER speaker, which is luck, not a rule. The shot to inherit
    # from is the item immediately BEFORE the hold in play order, which
    # is what this reads.
    held = shot = None
    for index, item in enumerate(items):
        source = item.GetMediaPoolItem()
        if source is None:
            continue
        if (source.GetClipProperty("File Path") or "") \
                != freeze.rendered_path:
            continue
        if index == 0:
            break
        held, shot = item, items[index - 1]
        break
    if held is None:
        raise ReelBuildError(
            f"{name}: no item on V{row} with a shot in front of it is "
            f"the freeze tail this build rendered "
            f"({freeze.rendered_path!r}), so nothing here knows which "
            f"item to give the shot's treatment to. The row carries "
            f"{len(items)} item(s). Refusing rather than grading the "
            f"wrong clip.")
    wanted = shot.GetProperty() or {}
    for key in ("ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle",
                "AnchorPointX", "AnchorPointY", "CropLeft", "CropRight",
                "CropTop", "CropBottom", "FlipX", "FlipY"):
        if key not in wanted:
            continue
        held.SetProperty(key, wanted[key])
    got = held.GetProperty() or {}
    for key in ("ZoomX", "Pan", "Tilt"):
        if key not in wanted:
            continue
        want, have = float(wanted[key]), float(got.get(key, "nan"))
        record["properties"][key] = {"wanted": want, "read_back": have}
        if abs(want - have) > 0.01:
            raise ReelBuildError(
                f"{name}: the freeze tail would not take the shot's "
                f"{key}: set {want}, reads back {have}. A hold that "
                f"draws differently from the frame it holds is a jump "
                f"cut on the last frames of the reel.")
    # `CopyGrades` returns False where Resolve declines; a hold with the
    # shot's framing and someone else's grade is the same jump in colour.
    record["grades_copied"] = bool(shot.CopyGrades([held]))
    if not record["grades_copied"]:
        print(f"  ! {name}: Resolve declined to copy the ending shot's "
              f"grade onto the freeze tail - the hold keeps the "
              f"timeline's own grade and may not match the frame it "
              f"holds", file=sys.stderr)
    print(f"  {name}: freeze tail inherits the ending shot on V{row} - "
          + ", ".join(f"{k} {v['read_back']}"
                      for k, v in record["properties"].items())
          + (", grade copied" if record["grades_copied"]
             else ", GRADE NOT COPIED"), file=sys.stderr)
    return record


def apply_transform_overrides(name: str, track_plan, video_row_by_angle: dict,
                              placements_list: list, timeline, transcript: dict,
                              project_folder: str, width: int, height: int,
                              look=None, screen_window=None,
                              draw_gain: float = FALLBACK_DRAW_GAIN) -> int:
    """Hold the captain's recorded transform overrides on the picture.

    The punch-in aims every shot at its measured subject; a hand move
    the captain made in the Inspector (Reel 09, 2026-09-10: Akshita's
    clip to Pan -35) is that aim being overruled, so overrides apply
    AFTER it - the held value is the captain's, never the aim's. Each
    one is judged the way the punch-in is: the `SetProperty` return
    AND the read-back (a silent clamp reads back the clamp, not the
    ask), and where the look declares a screen window the merged
    transform is re-proved against it (`assert_punch_took`) - an
    override that uncovered an edge raises rather than shipping
    black. Without a look there is no declared window, so the
    read-back equality is the whole proof. An override matching no
    placed span reports STALE, loudly, like every other captain's
    edit - and one whose words are spoken NOWHERE in the transcript
    reports again, on its own, because that one is the captain's value
    being overwritten for good rather than an override belonging to
    another reel (`captain_edits.lost_overrides`). Returns how many
    property holds were applied.
    """
    import sys

    from library.tools import captain_edits as _edits
    try:
        edits = _edits.load_edits(project_folder)
    except _edits.CaptainEditError as exc:
        raise ReelBuildError(
            f"captain_edits cannot be read: {exc}. A recorded override "
            f"the build cannot read must refuse, never build silently "
            f"past it.") from exc
    if not any(e.get("kind") == "transform_override" for e in edits):
        return 0
    video_places = [
        p for p in placements_list
        if getattr(p["clip"], "track_type", "video") == "video"]
    matched, stale = _edits.match_transform_overrides(
        video_places, transcript, edits, reel_name=name)
    _edits.report_stale(stale)
    # A stale override that belongs to ANOTHER reel is routine - every
    # recorded override is matched against every reel, so a build of
    # one reel prints every other reel's. A stale override whose words
    # are spoken NOWHERE is the opposite: the captain's value is gone
    # for every future build of every reel, and until now it printed
    # the same sentence as the eight routine ones. It is said again,
    # separately, so a rebuild overwriting a hand-set value cannot read
    # as housekeeping. Evidence: `docs/RULE_EVIDENCE.md`, section
    # `one-word-for-two-kinds-of-stale`.
    lost = _edits.lost_overrides(stale)
    for record in lost:
        print(f"  ! {name}: THE CAPTAIN'S "
              f"{record['property']}={record['value']:g} IS LOST - "
              f"{record['anchor_phrase']!r} is spoken nowhere in the "
              f"measured transcript, so this build and every future "
              f"one plays the engine's own aim instead. Re-capture it "
              f"against the words now spoken before promoting.",
              file=sys.stderr)
    if not matched:
        return 0
    position = {id(place): index
                for index, place in enumerate(video_places)}
    by_span: dict = {}
    for record in matched:
        by_span.setdefault(record["span_index"], []).append(record)
    applied = 0
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
            records = by_span.get(position.get(id(place), -1), [])
            if not records:
                continue
            source_file = place["clip"].source_file
            # ALL of this span's holds land before anything is proved:
            # a multi-property aim (zoom plus pan plus tilt for one
            # shot) arrives as one record per property, and proving
            # the window after the first one fails on the three not
            # yet set (measured on Reel 24, 2026-09-18: Pan held while
            # ZoomX was still 1, and the re-proof refused the build
            # before ZoomX was ever set). Each property is still
            # judged by its own read-back as it lands; the window is
            # proved once, on the merged hold.
            for record in records:
                prop, value = record["property"], record["value"]
                before = _held_property(item, prop)
                if not item.SetProperty(prop, value):
                    raise ReelBuildError(
                        f"{name}: Resolve refused the captain's "
                        f"{prop}={value:g} on {item.GetName()!r} "
                        f"(speaks {record['anchor_phrase']!r}). The "
                        f"decision is recorded and the clip did not "
                        f"take it - {record['reason']}")
                held = _held_property(item, prop)
                if held is not None and abs(held - value) > 1e-3:
                    raise ReelBuildError(
                        f"{name}: the captain's {prop}={value:g} did "
                        f"not take on {item.GetName()!r} - Resolve "
                        f"holds {held:g} (asked {value:g}). A silent "
                        f"clamp is a rebuild that reports the "
                        f"captain's value and plays another.")
                record["held"] = held
                record["before"] = before
            if screen_window is not None:
                current = {key: _held_property(item, key)
                           for key in ("ZoomX", "ZoomY", "Pan",
                                       "Tilt")}
                if any(v is not None for v in current.values()):
                    effective = {
                        key: (current[key]
                              if current[key] is not None
                              else (1.0 if key.startswith("Zoom")
                                    else 0.0))
                        for key in current}
                    try:
                        assert_punch_took(
                            name, item, source_file, effective,
                            _source_frame_size(item), width, height,
                            screen_window, draw_gain=draw_gain)
                    except Exception as exc:
                        first = records[0]
                        prop, value = (first["property"],
                                       first["value"])
                        raise ReelBuildError(
                            f"{name}: the captain's {prop}={value:g} "
                            f"on {item.GetName()!r} uncovers the "
                            f"screen window: {exc}") from exc
            for record in records:
                prop, value = record["property"], record["value"]
                held, before = record.get("held"), record.get("before")
                held_desc = (f"{held:g}" if held is not None
                             else "unreadable")
                before_desc = (f"{before:g}" if before is not None
                               else "unreadable")
                print(f"  {name}: captain's {prop} holds {held_desc} "
                      f"(was {before_desc}) on "
                      f"{os.path.basename(source_file)} - "
                      f"{record['reason']}", file=sys.stderr)
                applied += 1
    return applied


def pool_items_for(pool, filepath: str):
    """EVERY media pool item for *filepath*, in pool order.

    The plural half of `pool_item_for` below: a path normally holds
    one item, but a metadata refresh (`import_pool_item`) imports a
    second one beside a stale predecessor it must not delete (see
    that function), so a lookup that stops at the first hit would
    re-import on every rebuild - the unbounded duplication measured
    on 2026-09-09, back by another door.
    """
    found = []

    def _search(folder):
        for item in folder.GetClipList() or ():
            if item.GetClipProperty("File Path") == filepath:
                found.append(item)
        for sub in folder.GetSubFolderList() or ():
            _search(sub)

    _search(pool.GetRootFolder())
    return found


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
    held 1,210 items before the project was reset. The plural form
    is `pool_items_for` above, which the refresh path reads: a path
    under repair holds TWO items until history releases the stale one.
    """
    items = pool_items_for(pool, filepath)
    return items[0] if items else None


def _fresh_pooled_item(pool, filepath: str):
    """The pooled item for *filepath* whose metadata matches the file.

    A lookup hit is only reusable when Resolve's cached stream
    metadata still describes the bytes: artefacts are rewritten in
    place at a stable path (a re-render under an unchanged drawing
    digest, and `transcode_in_place`'s ProRes-to-qtrle carriage),
    while the pool caches dimensions and codec at import where the
    scripting API cannot refresh them. Measured 2026-09-13: 13 of 228
    overlay items held a predecessor's metadata, and every render of
    those frames failed decoding them.

    The comparison is `pool_stream_meta` - the one `deliver-reel`'s
    preflight refuses on, so the build heals what the deliver would
    refuse. One ffprobe per call, only when the path is already
    pooled; a fresh path imports with no probe at all.

    Returns `(item, None)` on a reusable hit and `(None, record)` when
    every pooled item disagrees, where `record` is the last
    disagreement - the caller imports fresh and says this. A path
    nothing comparable disagrees on reuses as before: an unreadable
    file or an unreadable property is skipped, never flagged.

    This NEVER deletes. The stale item stays pooled because the
    approved timeline still plays it: `MediaPool.DeleteClips` on a
    placed item takes media off that timeline, and promotion retires
    the old timeline to Archive rather than deleting it, so the stale
    item stays referenced by history either way
    (`orphan_removal.assert_removable` refuses placed items for
    exactly this reason). The staging timeline binds the fresh item;
    promotion carries it; the stale item outlives its usefulness
    beside the archive that still names it.
    """
    from library.tools import pool_stream_meta

    candidates = pool_items_for(pool, filepath)
    if not candidates:
        return None, None
    disk = pool_stream_meta.disk_stream(filepath)
    last_stale = None
    for candidate in candidates:
        pool_sig = pool_stream_meta.pool_stream(candidate)
        stale = pool_stream_meta.stream_disagreement(pool_sig, disk)
        if stale is None:
            return candidate, None
        last_stale = stale
    return None, last_stale


def _say_refreshed(filepath: str, record: dict) -> None:
    """The refresh, in the sentence an operator has to read."""
    import sys

    legs = []
    if "resolution" in record["mismatches"]:
        legs.append(f"pool says {record['pool_resolution']}, "
                    f"disk carries {record['disk_resolution']}")
    if "codec" in record["mismatches"]:
        legs.append(f"pool says {record['pool_codec']!r}, "
                    f"disk carries {record['disk_codec']!r}")
    print(f"  pool metadata for {os.path.basename(filepath)} went stale "
          f"({'; '.join(legs)}) - staging binds a fresh import; the "
          f"stale item is left for the archive that still plays it, "
          f"never deleted here", file=sys.stderr)


def import_dest_bin(filepath: str, project_folder: str = "") -> tuple[str, ...]:
    """The bin path a file imports INTO, decided before Resolve is asked.

    The same fact the organiser files by, read through the same
    function: a generated file's own directory names its category bin
    (`resolve_bin_layout.category_bin_for_file` - its render bin, or
    the shared assets bin for a production asset no render step
    wrote), so what an import lands in is what the next organise
    keeps it in - the destination is decided once, not once at import
    and again at filing. Anything not generated here (source footage,
    which this module never imports - it looks it up and skips when
    absent) belongs under the captain's source bin. A path with no
    project to read it against cannot be decided and answers the
    reels root, which is canonical (`is_canonical`) rather than
    wherever the current folder happens to be.
    """
    from library.tools import resolve_bin_layout as bins

    if project_folder:
        from library.tools.resolve_organization import is_generated

        if is_generated(filepath, project_folder):
            return bins.category_bin_for_file(filepath, project_folder)
        return (bins.SOURCE_BIN,)
    return (bins.REELS_BIN,)


def _ensure_bin_path(pool, path: tuple[str, ...]):
    """The folder at `path` under the root bin, made only if absent.

    Lookup-first, because `AddSubFolder` happily makes a SECOND folder
    of the same name (`execution/organise_media_pool.py`) - creating
    unconditionally forks the layout on every build. Every bin path
    here comes from `resolve_bin_layout`, never a literal: one module
    decides every bin path the way `timeline_layout` decides every
    track name.
    """
    folder = pool.GetRootFolder()
    for part in path:
        match = None
        for sub in (folder.GetSubFolderList() or []):
            if sub.GetName() == part:
                match = sub
                break
        if match is None:
            match = pool.AddSubFolder(folder, part)
            if not match:
                raise ReelBuildError(
                    f"Resolve would not make bin {part!r} under "
                    f"{folder.GetName()!r} - nothing was imported.")
        folder = match
    return folder


def import_pool_item(pool, filepath: str, project_folder: str = "",
                     dest: tuple[str, ...] | None = None):
    """The pool item for *filepath*, imported only if it is not there.

    A needed import lands in the bin `import_dest_bin` names -
    decided by the file, never inherited from whichever bin is
    CURRENT - and the current folder is restored afterwards, because
    `AddSubFolder` moves it to the bin it made and the next timeline
    creation would otherwise land somewhere nobody recorded. The
    lookup runs first, so re-running a build against a project that
    already holds the file adds no second entry: `ImportMedia` on a
    path already in the pool makes another item rather than returning
    the existing one (measured 2026-09-09).

    `dest` is a caller-computed bin path from the root - built with
    `overlay_import_bin` below, the render bin its kind belongs to
    under the timeline about to place it. Without it the import lands
    in the flat render bin and filing waits for a later organise pass
    that some build paths never run: measured 2026-09-10, a fresh
    overlay build left every subtitle and motion-graphics render in
    Source footage because CURRENT was there and no organise followed.
    With it the destination is binding at import time, the way
    `timeline_layout` owns track index and name. A tuple passed as
    `project_folder` reads as `dest`, so overlay callers keep spelling
    the destination positionally.

    Returns None when the import itself failed, so every caller keeps
    judging the call by what it RETURNS (AGENTS.md 5). Without a
    `project_folder` the destination cannot be decided and the import
    lands wherever is current - the old behaviour, kept for callers
    that have nothing to decide it from.

    EVERY item this returns has had its overlay clip attributes
    applied, the lookup hit included - see `_carry` below.

    A lookup hit is reused only when its cached stream metadata still
    matches the file (`_fresh_pooled_item`, the same `pool_stream_meta`
    comparison `deliver-reel` refuses on). A hit whose dimensions or
    codec disagree is NOT reused: the file was rewritten in place under
    it, and Resolve caches those where the scripting API cannot reach.
    The build imports fresh beside it - so the staging timeline binds
    the fresh item and promotion carries it - says the refresh on
    stderr, and never deletes the stale item: the approved timeline
    still plays it, and promotion retires that timeline to Archive
    rather than deleting it.
    """
    def _carry(item):
        """The clip attributes an alpha artefact needs, on EVERY return.

        Not on the import alone: a pool item first imported while its
        file was ProRes keeps its old attributes after that file is
        transcoded underneath it, so a build that only set them on a
        fresh import would place a stale reading of a current file.
        The lookup hit above is exactly that case, and it is the
        common one - a rebuild is the normal way to work on a reel.

        Decided by the FILE, so a-roll passing through here is left
        alone and an overlay is not (`overlay_carriage`); a refusal
        RAISES, because a `qtrle` clip whose Data Level did not take
        composites the whole frame 16/255 dark, including where the
        overlay draws nothing at all.
        """
        from library.tools.overlay_carriage import apply_clip_attributes

        if item is not None:
            apply_clip_attributes(item, filepath)
        return item

    fresh, stale_record = _fresh_pooled_item(pool, filepath)
    if fresh is not None:
        return _carry(fresh)
    if stale_record is not None:
        _say_refreshed(filepath, stale_record)
    if isinstance(project_folder, (tuple, list)) and dest is None:
        dest = tuple(project_folder)
        project_folder = ""
    if dest is not None:
        from library.tools.execution.organise_media_pool import (
            import_into_bin,
        )
        items = import_into_bin(pool, dest, [filepath])
        return _carry(items[0] if items else None)
    if not project_folder:
        items = pool.ImportMedia([filepath])
        return _carry(items[0] if items else None)
    dest = _ensure_bin_path(pool, import_dest_bin(filepath, project_folder))
    before = pool.GetCurrentFolder()
    try:
        pool.SetCurrentFolder(dest)
        items = pool.ImportMedia([filepath])
    finally:
        if before is not None:
            pool.SetCurrentFolder(before)
    return _carry(items[0] if items else None)


def _overlay_segment_id(segment: dict) -> str:
    """The intent key for one overlay segment.

    The declared `segment_id` first - caption segments carry one, and
    a rebuild that reuses the render keeps it. Otherwise the render
    filename stem, which is what a captain reading the timeline sees
    and what stays stable while the file is reused.
    """
    import os

    declared = (segment or {}).get("segment_id")
    if declared:
        return str(declared)
    path = (segment or {}).get("overlay_path") or ""
    return os.path.splitext(os.path.basename(path))[0]


def pool_sequence_for(pool, frame_dir: str):
    """The pooled image-sequence item for a rendered frame directory.

    A sequence reports ONE `File Path` with a bracket range
    (`dir/frame-[00-46].png`), so no single frame path ever matches it
    and a lookup by first frame always misses - which is how every
    rebuild re-imported every caption sequence beside the one already
    there. The directory is the identity: frame directories are per
    segment, so the mapping stays one to one.
    """
    if not frame_dir:
        return None
    wanted = os.path.normpath(frame_dir)
    found = None

    def _search(folder):
        nonlocal found
        for item in folder.GetClipList() or ():
            try:
                path = item.GetClipProperty("File Path") or ""
            except Exception:  # noqa: BLE001 - a stale handle, keep looking
                continue
            if not path:
                continue
            if "[" in path:
                if os.path.normpath(
                        os.path.dirname(path.split("[")[0])) == wanted:
                    found = item
                    return
            elif os.path.normpath(os.path.dirname(path)) == wanted:
                found = item
                return
        for sub in folder.GetSubFolderList() or ():
            if found is None:
                _search(sub)

    _search(pool.GetRootFolder())
    return found


def import_pool_sequence(pool, frame_paths: list, frame_dir: str,
                         project_folder: str = "",
                         dest: tuple[str, ...] | None = None):
    """The pool item for a frame directory, imported only if unpooled.

    The sequence-shaped half of `import_pool_item`: lookup by
    directory first (`pool_sequence_for`), else one `ImportMedia` of
    the whole frame list - which is what groups them into a single
    image-sequence item - into the decided bin with the current folder
    restored. `dest` is a caller-computed bin path (see
    `overlay_import_bin`); without it the flat render bin
    `import_dest_bin` names. Returns None when there is nothing to
    import or the import failed.
    """
    existing = pool_sequence_for(pool, frame_dir)
    if existing is not None:
        return existing
    if not frame_paths:
        return None
    if dest is None and not project_folder:
        items = pool.ImportMedia(list(frame_paths))
        return items[0] if items else None
    if dest is None:
        first = frame_paths[0] if frame_paths else frame_dir
        dest = _ensure_bin_path(pool, import_dest_bin(first, project_folder))
    else:
        from library.tools.execution.organise_media_pool import (
            ensure_folder,
        )
        folder = pool.GetRootFolder()
        for depth, part in enumerate(dest):
            folder = ensure_folder(pool, folder, part, None, dest[:depth])
        dest = folder
    before = pool.GetCurrentFolder()
    try:
        pool.SetCurrentFolder(dest)
        items = pool.ImportMedia(list(frame_paths))
    finally:
        if before is not None:
            pool.SetCurrentFolder(before)
    return items[0] if items else None


def create_reel_timeline(pool, name: str):
    """A reel timeline created where reels belong, nowhere else.

    `CreateEmptyTimeline` puts what it makes into whatever bin is
    CURRENT - wherever the operator last clicked - which is how reels
    landed in motion-graphics bins. The reels bin is the decision, made
    here rather than repaired afterwards by the organiser, and the
    current folder is restored so the next call inherits nothing from
    this one.

    A staging or scratch timeline is NEVER created in the reels bin:
    it goes to the dedicated scratch bin
    (`resolve_bin_layout.SCRATCH_BIN`), outside every bin the captain
    reviews - measured 2026-09-11, when three `(scratch fm-restore...)
    (rebuild staging)` timelines sat in `05 - Reels` beside the
    captain's own Reel 13 and took their feedback marker. This function
    is the choke point every build places through (`build_reel_timeline`
    below is the only caller), so one branch here covers every build
    path rather than a patch per call site.
    """
    from library.tools import resolve_bin_layout as bins

    if bins.is_scratch_timeline(name):
        dest = _ensure_bin_path(pool, (bins.SCRATCH_BIN,))
    else:
        dest = _ensure_bin_path(pool, (bins.REELS_BIN,))
    before = pool.GetCurrentFolder()
    try:
        pool.SetCurrentFolder(dest)
        timeline = pool.CreateEmptyTimeline(name)
    finally:
        if before is not None:
            pool.SetCurrentFolder(before)
    if not timeline:
        raise ValueError(f"Failed to create timeline {name}")
    return timeline


def overlay_import_bin(project_folder: str, timeline_name: str,
                       filepath: str) -> tuple[str, ...]:
    """Where an overlay import for this timeline lands, by construction.

    The render bin its KIND belongs to (a path fact off
    `project_layout.Area`, never a name parse) under the timeline that
    is about to place it - exactly the verdict `plan_organization`
    reaches for a sole-placed generated clip, computed BEFORE the
    import so the two cannot disagree. A production asset no render
    step wrote lands in the shared assets bin instead, which has no
    per-reel leaves. Spelled once, used by every overlay import below.
    """
    from library.tools import resolve_bin_layout as bins

    if bins.is_render_file(filepath, project_folder or ""):
        return (bins.render_bin_for_file(filepath, project_folder or ""),
                timeline_name)
    return (bins.ASSETS_BIN,)


def _overlay_spans(segments, fps: float):
    """(start, end) in FRAMES for each rendered overlay segment.

    What `timeline_layout` packs rows from. A segment whose span
    overlaps another's needs a row of its own, which is how two
    tight-box animations that play at once become two rows
    (`motion_graphics_plan.plan_segments` is what splits them).
    """
    return [(int(round(s["timeline_start"] * fps)),
             int(round(s["timeline_end"] * fps)))
            for s in (segments or [])
            if s.get("timeline_end", 0) > s.get("timeline_start", 0)]


def place_overlay_segments(pool, project, timeline, name: str, fps: float,
                           segments, track_rows, kind: str,
                           check: str, properties: dict = None,
                           project_folder: str = "",
                           overlay_intent: dict = None,
                           frame: tuple = None,
                           seen_ids: list = None,
                           do_not_draw: list = None,
                           intent_applied: list = None,
                           seen_labels: list = None,
                           sweep_out: list = None,
                           draw_gain: float = FALLBACK_DRAW_GAIN) -> list:
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

    Where the project declares intent (`overlay_intent` - loaded from
    `external/overlay_intent.json` by the caller), a pinned segment
    lands on the declared position instead of the computed one; the
    pin is selected by the segment's id, then its placing label, then
    `kind`. `seen_ids`, where given, collects every placed segment's
    intent id, and `seen_labels` every placing label it was placed
    under, so the caller can report declared pins that matched nothing
    (`overlay_intent.report_unmatched`) instead of dropping them
    silently. `intent_applied`, where given, collects every winning
    pin key, so the caller can report which pins DID apply.

    `track_rows` is the plan's rows for this kind, in row order, and a
    segment rides the one its own LANE names. Segments on one lane never
    overlap in time; two that do are two rows, which is the captain's
    Reel 26 request - "two different tighbox animations that are layered
    on seperate rows on the timeline". A lane with no row is a build
    REFUSED, never a graphic quietly stacked onto a row that is already
    showing something else. One int is accepted for the single-row case.

    Returns the segment ids a `do_not_draw` suppression held back, so
    the caller can keep them on the build record - `[]` where nothing
    was declared or nothing matched.

    `sweep_out`, where given, collects one post-build sweep record per
    tight segment placed (`overlay_verify.sweep_reel_overlays` reads
    them back off the timeline after the build): label, kind, segment
    id, track, record frame, canvas, the computed placement, and where
    the segment's file lives for the pixel half. Full-canvas segments
    carry no transform to judge and leave no record.
    """
    import sys

    from library.tools import do_not_draw as _dnd
    from library.tools.overlay_draw_intent import (
        draw_intent_for_segment,
        segment_canvas,
    )
    from library.tools.overlay_placement import apply_placement_transform
    from library.tools.reel_placed_assets import assert_placeable

    rows = ([int(track_rows)] if isinstance(track_rows, int)
            else [int(r) for r in (track_rows or [])])
    held_back: list = []
    for segment in segments or []:
        suppressed, why = _dnd.should_suppress(
            do_not_draw, name, segment or {})
        if suppressed:
            # The captain deleted this graphic and it stays deleted:
            # not imported, not placed, and SAID - on stderr and (by
            # the caller) on the build record. Suppressing the
            # placement rather than the plan keeps the record of what
            # was intended: the plan still lists it, the timeline
            # does not play it, and a rebuild that re-plans holds
            # the same deletion without being told again.
            print(f"  {name}: {why}", file=sys.stderr)
            held_back.append(_overlay_segment_id(segment or {}))
            continue
        if why:
            # The label re-pointed at a different graphic (the plan
            # shifted under it): NOT suppressed, and said loudly
            # rather than placed past. The graphic plays until the
            # declaration is re-transcribed.
            print(f"  {name}: {why}", file=sys.stderr)
        lane = int(segment.get("lane", 0) or 0)
        if lane >= len(rows):
            raise ReelBuildError(
                f"{name}: rendered {kind} segment "
                f"{segment.get('overlay_path')!r} plays on lane {lane} "
                f"and the track plan holds {len(rows)} row(s) for it. A "
                f"lane is a row - stacking it onto another lane's row "
                f"would hide one of two graphics the plan puts on screen "
                f"together.")
        track_index = rows[lane]
        if project_folder:
            assert_placeable(segment["overlay_path"], project_folder)
        item = import_pool_item(
            pool, segment["overlay_path"],
            overlay_import_bin(project_folder, name,
                               segment["overlay_path"]))
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
        # A declared pin for this segment wins over the computed
        # placement (Reel 09: the captain's hand corrections), so a
        # rebuild lands where they put things.
        tight = segment.get("tight_box") or {}
        placed_segment_id = _overlay_segment_id(segment)
        placed_label = (segment or {}).get("placement_label") or None
        if seen_ids is not None:
            seen_ids.append(placed_segment_id)
        if seen_labels is not None and placed_label:
            seen_labels.append(placed_label)
        canvas = ((tight.get("width"), tight.get("height"))
                  if tight.get("width") and tight.get("height")
                  else None)
        # `draw_intent` arms the pixel half: the held values are judged
        # against what the overlay is FOR - a declared pin where the
        # captain put one - so a stale-carriage value that reads back
        # cleanly is REPORTED rather than shipped. Unpinned graphics of
        # these kinds have no per-graphic declaration at placement time
        # and ride without it, exactly as before.
        note = apply_placement_transform(
            timeline, track_index, record_frame,
            tight.get("placement"),
            label=f"{kind} at {segment['timeline_start']:.2f}s",
            kind=kind,
            segment_id=placed_segment_id,
            intent=overlay_intent,
            # A pin names a PLACE; the canvas going down is what turns
            # it into a transform (`overlay_intent.transform_for`).
            canvas=canvas,
            frame=frame,
            # Which placing this file serves: a label pin survives the
            # re-render that kills the digest id, and a winning pin is
            # recorded so the build can say which pins applied. The
            # intent check below reads the SAME label, so the two
            # cannot disagree about which pin won.
            placement_label=placed_label,
            intent_matched=intent_applied,
            draw_gain=draw_gain,
            draw_intent=draw_intent_for_segment(
                segment, kind=kind, segment_id=placed_segment_id,
                placement_label=placed_label,
                intent=overlay_intent, frame_wh=frame,
                project_folder=project_folder, reel_name=name,
                draw_gain=draw_gain))
        if note:
            print(f"  {name}: {note}", file=sys.stderr)
        if sweep_out is not None and canvas is not None:
            sweep_out.append({
                "label": f"{kind} at {segment['timeline_start']:.2f}s",
                "kind": kind,
                "segment_id": placed_segment_id,
                "placement_label": placed_label,
                "track_index": track_index,
                "record_frame": record_frame,
                "canvas_wh": segment_canvas(segment),
                "placement": tight.get("placement"),
                "overlay_path": segment.get("overlay_path") or "",
                "frames_dir": "",
            })
    return held_back


def apply_offset_specs(placements_list: Sequence[dict], fps: float,
                       subtitle_segments, j_cut: dict = None,
                       cutaway: dict = None) -> tuple:
    """Apply the offset specs a build was asked for, or refuse.

    The one application `build_reel_timeline` and the Fusion-manifest
    side of a variant build both read: the join/window frame math, the
    caption shift travelling with moved speech, and the planner calls
    live here once, so the placements the timeline is laid from and
    the placements a manifest is drawn from cannot drift apart.

    Returns (placements, subtitle_segments, offset_links,
    offset_reports). `subtitle_segments` travels with moved speech
    here, BEFORE the material is read, so the caption row the plan
    creates answers the cards actually placed.
    """
    offset_links: list = []
    offset_reports: dict = {}
    caption_notes: list = []
    if j_cut is not None:
        join_frame = int(round(float(j_cut["join_seconds"]) * fps))
        lead_cut_frames = int(round(float(j_cut["lead_seconds"]) * fps))
        subtitle_segments, caption_notes = shift_captions_for_audio_lead(
            subtitle_segments, join_frame, lead_cut_frames, fps)
        j_plan = plan_j_cut(placements_list, fps, join_frame,
                            lead_cut_frames,
                            words=j_cut.get("words", ()))
        placements_list = j_plan.placements
        offset_links.extend(j_plan.links)
        offset_reports["j_cut"] = j_plan.report
    if cutaway is not None:
        window_seconds = cutaway["window_seconds"]
        window_frames = (int(round(float(window_seconds[0]) * fps)),
                         int(round(float(window_seconds[1]) * fps)))
        c_plan = plan_cutaway(placements_list, fps,
                              str(cutaway["hide_angle"]), window_frames,
                              cover_words=cutaway.get("cover_words", ()))
        placements_list = c_plan.placements
        offset_links.extend(c_plan.links)
        offset_reports["cutaway"] = c_plan.report
    if offset_reports:
        offset_reports["caption_notes"] = caption_notes
        offset_reports["room_tone_note"] = (
            "an offset moves the picture cut, never the room: the two "
            "takes sit seconds apart in source, so an audible room-tone "
            "step at the seam survives any picture treatment - listen "
            "at the seam, and if one is heard neither version wins on "
            "sound")
    return placements_list, subtitle_segments, offset_links, offset_reports


def _place_transition_element(pool, project, timeline, name: str,
                              placement, pool_item,
                              transitions_row: int, fps: float) -> None:
    """Place one project-declared transition element, and judge the write.

    The append is judged by what it RETURNS and by a RE-READ of the
    track, never by whether it was called (AGENTS.md 5): a declined
    append leaves no trace at placement time and surfaces a whole
    stage later as an F18 placed-against-planned mismatch, far from
    its cause. The re-read is the verdict, not the handle - a
    colliding append returns a truthy list of zombie handles and
    places nothing (`composed_edit` step 6), so the track wins over
    the return value.

    No `mediaType` is passed on purpose: transition elements are
    project-supplied artwork that may carry INTENTIONAL audio, and
    `mediaType: 1` would trade one silent drop for another. That
    choice belongs to the transition owner, not this judgement.

    Raises `ReelBuildError` naming the element where the append
    returns nothing or the track holds no item of the planned length
    at the planned frame.
    """
    # `startFrame`/`endFrame` are in the POOL ITEM's OWN frames, not
    # the timeline's - AGENTS.md 5. `record_frame` and
    # `duration_frames` stay TIMELINE frames, because that is what
    # the planner computed the cut's position in.
    element_fps = float(pool_item.GetClipProperty("FPS") or fps)
    source_frames = int(round(placement.element_seconds * element_fps))
    assert_current_timeline(project, timeline)
    placed = pool.AppendToTimeline([{
        "mediaPoolItem": pool_item,
        "startFrame": 0,
        "endFrame": source_frames,
        "trackIndex": transitions_row,
        "recordFrame": placement.record_frame,
    }])
    if not placed:
        raise ReelBuildError(
            f"{name}: Resolve would not place the transition element "
            f"{placement.element_path!r} on V{transitions_row} - "
            f"AppendToTimeline returned nothing, and an unplaced "
            f"element the record claims is a build F18 refuses")
    row_items = timeline.GetItemListInTrack(
        "video", transitions_row) or []
    landed = False
    for row_item in row_items:
        span = _timeline_span(row_item)
        if span is None:
            continue
        start, end = span
        if (start == placement.record_frame
                and end - start == placement.duration_frames):
            landed = True
            break
    if not landed:
        raise ReelBuildError(
            f"{name}: Resolve would not place the transition element "
            f"{placement.element_path!r} on V{transitions_row} at "
            f"frame {placement.record_frame} - the track holds no "
            f"{placement.duration_frames}-frame item there, and an "
            f"unplaced element the record claims is a build F18 "
            f"refuses")


def build_reel_timeline(project, moment, master_clips, subtitle_segments, fps, width, height, project_folder, transcript, timeline_name: str = "", cards=None, overlay_placements=None, explainer_segments=None, semantic_segments=None, look=None, motion=None, master_timeline=None, program_channels=None, extra_cuts: Sequence[tuple] = (), j_cut: dict = None, cutaway: dict = None, grade_cdl=None, power_grade=None, overlay_intent: dict = None, ranges=None, ending=None, lower_third_segments=None, card_row_role=None, do_not_draw: list = None,
                      draw_gain: float = FALLBACK_DRAW_GAIN):
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

    `j_cut` is an optional {"join_seconds", "lead_seconds", "words"}
    spec (`plan_j_cut`): the audio cut moves earlier than the picture
    cut at that join, so the ear crosses before the eye. `cutaway` is
    an optional {"hide_angle", "window_seconds", "cover_words"} spec
    (`plan_cutaway`): that angle's picture is hidden over the window,
    revealing the continuous angle beneath, and the audio never moves.
    Either spec makes the link pass strict - anything still unlinked
    afterwards refuses the build rather than placing silently - and
    shifts the caption cards that travel with moved speech. Both are
    None by default, which builds exactly what this built before.

    `grade_cdl` is the project's declared CDL half as
    `reel_look.resolve_grade_cdl` renders it (slope/offset/power/
    saturation in the key names step 6.01 formats), or None/{}. It is
    applied here, in process, onto every footage picture item - after
    the picture is placed and before this function returns, while the
    Fusion pass runs afterwards in its own process. That is the v04
    still's own order (`data/vep-grade-variants/report.md` section 2:
    CDL first as SetCDL, then the Fusion chain), held structurally
    rather than by convention. None means the project declares no
    look, and then this is the timeline it built before the CDL half
    existed.

    `power_grade` is the project's declared PowerGrade `.drx` as
    `reel_look.resolve_power_grade` reads it, or None. Where one is
    declared it is THE GRADE and `grade_cdl` does not go on separately:
    `ApplyGradeFromDRX` replaces the whole node graph, so the CDL rides
    INSIDE it, on the node the declaration names. Measured on Reel 09,
    2026-09-10: the CDL route on its own returned True and rendered a
    still byte-identical to no grade at all, while the DRX route read
    back 8 real Color page nodes and moved 28.9% of the frame.
    `reel_look.apply_grade` is the one place that choice is made.

    Returns the build record: the track plan as placed, what stream
    enforcement removed, what the link pass joined, which empty rows
    were deleted, and which master clips were skipped - so the
    conformance proof is gradeable without re-deriving any of it.
    """
    import sys, os

    name = timeline_name or moment.timeline_name

    # The graphics the captain deleted (`external/do_not_draw.json`,
    # loaded by the caller - [] where they declared none). Read once
    # here so every placer below - TV frame, captions, explainer,
    # semantic visuals, lower thirds - holds the same deletions, and
    # so the build record can say which rules fired and which matched
    # nothing.
    from library.tools import do_not_draw as _dnd
    suppressions = list(do_not_draw or [])
    suppressed_ids: list = []

    # The reel's shape is computed BEFORE anything is created, so a
    # refusal - a mid-word keep edge, an unresolvable program stream -
    # fires before a timeline exists rather than leaving half of one.
    # `extra_cuts` are the captain's recorded strikes for this moment:
    # the placer reads the SAME ranges the caption pass planned from,
    # so picture and captions cannot disagree about what plays.
    # `ranges` arrives precomputed where the caller already trimmed the
    # captain's span_retime pins out of it (the rebuild loop and the
    # variant path) - recomputing from the moment here would un-trim
    # them and place seconds nothing downstream planned for.
    if ranges is None:
        ranges = reel_ranges(moment, transcript, extra_cuts=extra_cuts)
    lead = lead_frames(cards, fps)
    # The declared card row is required BEFORE anything derives from
    # the cards: a reel whose head/tail cards name no row refuses here,
    # with the declaration named - never further down as a missing row,
    # and never by guessing the first a-roll row (the logo-on-V1
    # defect). Card-less reels never reach for it.
    if any(getattr(card, "placement", "") in ("head", "tail")
           for card in (cards or ())) and not card_row_role:
        from library.tools.full_frame_element import (
            CARD_ROW_ROLE_KEY, CARD_ROW_ROLES)
        raise ReelBuildError(
            f"{name}: this reel declares full-frame card(s) but no "
            f"effect.{CARD_ROW_ROLE_KEY}: declare one of "
            f"{', '.join(CARD_ROW_ROLES)} in the project's project.yaml "
            f"(or the brand template's `effect` slot). Which row the "
            f"closing card belongs on is the captain's call - V5 "
            f"\"Semantic\", where they hand-placed the logo on Reel 09, "
            f"or the \"Motion Graphics\" row. Until it is declared the "
            f"build stops rather than guessing V1.")
    placements_list = placements(ranges, master_clips, fps, lead_frames=lead)

    # ── Offset placements: the audio cut moves, the picture hides ──
    # `plan_j_cut` moves the audio cut earlier at one join (picture
    # untouched); `plan_cutaway` hides one angle's picture over a
    # window (audio untouched). Both return the moved placements plus
    # the link groups they declare, and both refuse what they cannot
    # build - so the loop below places from the moved list and the
    # link pass enforces what it declares.
    (placements_list, subtitle_segments, offset_links,
     offset_reports) = apply_offset_specs(
        placements_list, fps, subtitle_segments, j_cut, cutaway)

    # ── The declared FREEZE tail ──
    # `library/tools/reel_ending.py`. A declared `tail_hold: freeze`
    # holds the ending shot's LAST FRAME for exactly as long as the
    # tail element needs, so the animation begins after the last word
    # instead of over it (the captain's ruling of 2026-09-11, taken
    # over letting the switch-off run across his closing line).
    #
    # It joins `placements_list` HERE, before the track plan and
    # before anything places or measures the picture, so it is a
    # picture clip to every pass that follows: the placer puts it on
    # the same angle's row, `frame_runs` carries the TV frame over it,
    # and `reel_look.power_effects` arms the element on it because it
    # is the last picture clip of that row. Audio is NOT extended - a
    # freeze is picture only, and the voice plays to its natural end.
    freeze_tail = None
    if ending is not None:
        from library.tools import reel_ending as _ending_owner

        freeze_tail = _ending_owner.plan_freeze(
            [p for p in placements_list
             if getattr(p["clip"], "track_type", "video") == "video"],
            ending, fps, look=look)
    if freeze_tail is not None:
        from library.tools.project_layout import Area, ProjectLayout
        from library.tools.reel_placed_assets import (
            assert_placeable as _assert_placeable,
            promote_to_durable as _promote,
        )

        freeze_tail = _ending_owner.render_freeze(
            freeze_tail,
            os.path.join(str(ProjectLayout(project_folder).read_dir(
                Area.SCRATCH)), "reel_ending", "freeze"),
            fps)
        # Promoted out of scratch before anything points at it, the
        # rule every placed artefact follows: a timeline pointing under
        # scratch/ points at files a cleaner may throw away.
        import dataclasses as _dc

        freeze_tail = _dc.replace(
            freeze_tail,
            rendered_path=_promote(freeze_tail.rendered_path,
                                   project_folder, Area.REEL_CARDS))
        _assert_placeable(freeze_tail.rendered_path, project_folder)
        placements_list.append(
            _ending_owner.freeze_placement(freeze_tail, fps))
        print(f"  {timeline_name or moment.timeline_name}: freeze tail "
              f"holds frame {freeze_tail.held_source_seconds:.3f}s of "
              f"{os.path.basename(freeze_tail.held_from)} for "
              f"{freeze_tail.duration_frames}f at reel frame "
              f"{freeze_tail.reel_start_frame}, {freeze_tail.element} "
              f"draws over it", file=sys.stderr)

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
        explainer_spans=_overlay_spans(explainer_segments, fps),
        semantic_spans=_overlay_spans(semantic_segments, fps),
        lower_third_spans=_overlay_spans(lower_third_segments, fps),
        has_frame=look is not None,
        card_role=card_row_role,
        card_spans=[(int(card.reel_start_frame), int(card.reel_end_frame))
                    for card in (cards or ())
                    if getattr(card, "placement", "") in ("head", "tail")])
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
        "offsets": offset_reports,
        # The declared hold, so the Fusion pass places the tail element
        # on it without re-deriving what this build already rendered.
        "freeze": freeze_tail,
    }

    pool = project.GetMediaPool()

    # The freeze tail's artefact goes into the pool BEFORE the picture
    # loop asks for it: that loop finds media by path with
    # `pool_item_for` and SKIPS what it cannot find, with one line on
    # stderr - which is exactly the silent loss this owner exists to
    # end. An import Resolve declines refuses the build instead.
    if freeze_tail is not None:
        if import_pool_item(
                pool, freeze_tail.rendered_path,
                overlay_import_bin(project_folder, name,
                                   freeze_tail.rendered_path)) is None:
            raise ReelBuildError(
                f"{name}: Resolve would not import the freeze tail "
                f"{freeze_tail.rendered_path!r}. The reel's declared "
                f"ending holds a frame this build cannot place, so it "
                f"refuses rather than ending on the live tail and "
                f"calling that the declaration.")

    timeline = create_reel_timeline(pool, name)

    project.SetCurrentTimeline(timeline)

    # The frame the caller already resolved and every overlay above was
    # rendered at. Written from `width`/`height` rather than by literal:
    # a timeline sized differently from the overlays drawn for it is
    # exactly the 001 defect (a vertical overlay band down the middle of
    # a landscape master), and the two numbers cannot disagree if only
    # one of them exists.
    timeline.SetSetting("useCustomSettings", "1")
    timeline.SetSetting("timelineResolutionWidth", str(int(width)))
    timeline.SetSetting("timelineResolutionHeight", str(int(height)))

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

    # The cards, on the DECLARED card row - never the first a-roll row.
    # A head/tail card plays beside the footage (before the first frame
    # or after the last), so any row shows it and the row is
    # organisation: the project's `effect.card_row_role` names the
    # track-plan ROLE, and the row is resolved through
    # `rows_for_role` - the same route the explainer, semantic visuals
    # and lower thirds take - by the lane the plan's own packing gave
    # the card's span (`card_spans_for_role` + `lane_of_span`, the same
    # order the plan packed). No index is read here, so a reel with 5,
    # 6 or 7 video tracks lands the card on the named row regardless.
    # A `full_frame_span` is NOT a card in this sense: a span IS the
    # body's picture (the footage beneath it is suppressed), so it
    # stays on the first a-roll row and F23 keeps grading it there.
    from library.tools.timeline_layout import (
        card_spans_for_role, lane_of_span,
    )
    head_tail = [card for card in (cards or ())
                 if getattr(card, "placement", "") in ("head", "tail")]
    # `card_row_role` is required above, before anything derives from
    # the cards - so reaching here with cards and no role is a caller
    # that bypassed the declaration, and the plan's own packing guard
    # (`card_spans_for_role`) is what names it.
    card_role_rows = (track_plan.rows_for_role(card_row_role)
                      if card_row_role else [])
    if head_tail and not card_role_rows:
        raise ReelBuildError(
            f"{name}: card row role {card_row_role!r} minted no row. "
            f"A row exists because something goes on it, and cards "
            f"are on this reel - refusing rather than placing onto "
            f"an unplanned row.")
    role_spans = (card_spans_for_role(track_plan.material, card_row_role)
                  if head_tail else [])
    # One lane per head/tail card, in play order - the same order the
    # spans reached the material in, so position `base + i` is this
    # card's span. Keyed by ORDER, never by identity: `promote_cards`
    # below replaces a card object with a copy when it promotes one
    # out of scratch, so an id-keyed map would miss after promotion.
    card_lanes = [
        lane_of_span(role_spans, len(role_spans) - len(head_tail) + i)
        for i in range(len(head_tail))]
    for lane in card_lanes:
        if lane >= len(card_role_rows):
            raise ReelBuildError(
                f"{name}: a card packed onto lane {lane} of role "
                f"{card_row_role!r}, which holds "
                f"{len(card_role_rows)} row(s). The placer replays the "
                f"plan's packing and disagrees with it - refusing "
                f"rather than placing onto an unplanned row.")
    # The cards FIRST, so the timeline reads in play order.
    # Rendered into scratch as the renderer's own cache, so promoted
    # into the durable REEL_CARDS area before anything is imported: a
    # timeline that points under scratch/ points at files a cleaner may
    # throw away (library/tools/reel_placed_assets.py).
    from library.tools.reel_placed_assets import (
        assert_placeable, promote_cards,
    )
    cards = promote_cards(cards, project_folder)
    head_tail_at = 0
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
        card_item = import_pool_item(
            pool, path, overlay_import_bin(project_folder, name, path))
        if card_item is None:
            raise ReelBuildError(
                f"{name}: Resolve would not import the rendered card "
                f"{path!r}")
        assert_current_timeline(project, timeline)
        if getattr(card, "placement", "") in ("head", "tail"):
            dest_row = card_role_rows[card_lanes[head_tail_at]].index
            head_tail_at += 1
        else:
            # A span IS the body's picture, on the picture row.
            dest_row = track_plan.aroll_rows()[0].index
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
            # SOURCE frames, which is only the same number for a card
            # this engine rendered at the reel's own fps. A project's
            # own clip (`full_frame_clip`) was authored at whatever
            # rate it chose, and `source_frames` is what was measured
            # off the file.
            "endFrame": card.source_frames or card.duration_frames,
            "mediaType": 1,
            "trackIndex": dest_row,
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

    # ── The declared CDL: the look's hue half, on the footage ──
    # Step 6.01 applies this on the master through TimelineItem.SetCDL
    # and the reels path had no SetCDL call at all - so a reel carried
    # the Fusion four (texture and falloff) but not the slope/offset/
    # power/saturation that carries the colour split itself. Applied
    # here, in process, right after placement: the caller's Fusion pass
    # runs after this returns, which is the still recipe's CDL-first
    # order held structurally. Rendered cards sharing the picture rows
    # are matched out by source - a graphic is not footage.
    if grade_cdl or power_grade:
        from library.tools import reel_look as _grade
        footage_sources = {
            getattr(p["clip"], "source_file", "")
            for p in placements_list
            if getattr(p["clip"], "track_type", "video") == "video"}
        cdl_record = _grade.apply_grade(
            timeline, track_plan, grade_cdl, power_grade=power_grade,
            footage_sources={s for s in footage_sources if s})
        build_record["cdl"] = cdl_record
        route = cdl_record.get("route", "cdl")
        if route == "power_grade_drx":
            detail = f"PowerGrade {os.path.basename(cdl_record['path'])}"
            if cdl_record.get("cdl_node"):
                detail += f" + CDL on node {cdl_record['cdl_node']!r}"
        else:
            detail = f"CDL {grade_cdl.get('saturation', '?')} sat"
        verdict = "VERIFIED" if cdl_record.get("verified") else "UNVERIFIED"
        print(f"  {name}: {detail} on "
              f"{len(cdl_record['applied'])} picture item(s) [{verdict}]"
              + (f" - {len(cdl_record['warnings'])} warning(s)"
                 if cdl_record["warnings"] else ""), file=sys.stderr)
        for warning in cdl_record["warnings"]:
            print(f"  ⚠ {warning}", file=sys.stderr)
    else:
        build_record["cdl"] = {"applied": [], "skipped": [],
                               "warnings": [], "route": "none",
                               "verified": False,
                               "basis": "no look declared - nothing graded"}

    # ── The declared look: punch-in on the picture, the frame over it ──
    # The punch-in is the Edit-page transform, which is what the
    # captain's own reference capture measured. It is AIMED at the
    # speaker, per shot, and a shot with no subject measurement is left
    # unpunched rather than punched at a guess (captain, 2026-09-09:
    # a centred 2.30 put Craig out of shot entirely).
    # Every overlay id laid down on this reel, captions included
    # below, with every placing label it played under: the declared
    # pins that matched none of them are REPORTED after the last
    # placement, never silently dropped - and the pins that DID apply
    # ride the build record to the durable report.
    seen_intent_ids: list = []
    seen_intent_labels: list = []
    applied_intent_keys: list = []
    # One post-build sweep record per tight overlay placed below (the
    # TV frame, captions, explainer, semantic visuals, lower thirds):
    # `overlay_verify.sweep_reel_overlays` reads them back off the
    # timeline after the build and judges stored against intent.
    # REPORTED, never raised - collected here, run once, after the last
    # placement and before any row deletion moves the indices.
    _sweep_records: list = []
    screen_window = None
    if look is not None:
        from library.tools import reel_look as _look

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
            aimed += aim_picture_row(
                name, look, screen_window, width, height,
                row_items, row_places, project_folder=project_folder,
                draw_gain=draw_gain)

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
        suppressed_ids.extend(place_overlay_segments(
            pool, project, timeline, name, fps,
            promote_frame_overlays(
                _look.frame_overlay_segments(look, runs, fps, width, height,
                                             project_folder),
                project_folder),
            track_plan.row_for_role(FRAME).index,
            kind="TV frame", check="F4",
            properties=_look.frame_properties(look, width, height),
            project_folder=project_folder,
            overlay_intent=overlay_intent, frame=(width, height),
            seen_ids=seen_intent_ids,
            do_not_draw=suppressions,
            intent_applied=applied_intent_keys,
            seen_labels=seen_intent_labels,
            sweep_out=_sweep_records,
            draw_gain=draw_gain))
        print(f"  {name}: TV frame over {len(runs)} picture run(s) on "
              f"V{track_plan.row_for_role(FRAME).index} at cover zoom "
              f"{_look.frame_properties(look, width, height)['ZoomX']:.4f}, "
              f"punch-in aimed on {aimed}/{placed_shots} shot(s) "
              f"({look['origin']})", file=sys.stderr)

    # ── The captain's recorded transform overrides ──
    # A hand move in the Inspector lives only in the project file, so
    # a rebuild re-aims the punch-in over it. Overrides apply AFTER
    # the aim above (or with no look at all), hold the recorded value,
    # and re-prove coverage where the look declares a window. With no
    # look there is no window and the read-back is the whole proof.
    # A project that recorded none pays one file read and nothing
    # else - the store that was never written costs nothing here.
    held = apply_transform_overrides(
        name, track_plan, video_row_by_angle, placements_list,
        timeline, transcript, project_folder, width, height,
        look=look, screen_window=screen_window)
    if held:
        print(f"  {name}: {held} captain's transform hold(s) in force",
              file=sys.stderr)

    # ── The freeze inherits the shot it holds ──
    # A freeze IS the ending shot's last frame, so it must look exactly
    # like that frame: same punch-in transform, same grade. Its own aim
    # would be recomputed from a face probe and its own grade applied
    # from the same template, and either could land a pixel or a shade
    # off - which a viewer reads as a jump cut at the very last moment.
    # Inherited rather than recomputed, and READ BACK (AGENTS.md 5).
    # This is also why a word-anchored hold cannot reach it: the held
    # frame speaks nothing, so `freeze_placement` gives it an empty
    # master span and the shot's value arrives here instead.
    if freeze_tail is not None:
        inherited = _inherit_freeze_treatment(
            name, timeline, track_plan, video_row_by_angle, freeze_tail)
        build_record["freeze_tail"] = inherited

    # Captions are PLACED here and RENDERED by step 4.05, which is the
    # pipeline's renderer. This used to carry its own `npx remotion
    # render` loop - a third implementation of the same call - and it is
    # gone; `reel_subtitle_segments` above drives the step instead.
    from library.tools.overlay_draw_intent import (
        draw_intent_for_segment as _draw_intent_for_segment,
        segment_canvas as _segment_canvas,
    )
    from library.tools.overlay_placement import (
        place_overlay_segment,
        sequence_frame_paths,
    )
    for segment in (subtitle_segments or []):
        held_back, why = _dnd.should_suppress(
            suppressions, name, segment or {})
        if held_back:
            print(f"  {name}: {why}", file=sys.stderr)
            suppressed_ids.append(segment.get("segment_id"))
            continue
        if why:
            print(f"  {name}: {why}", file=sys.stderr)
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
            found = import_pool_sequence(
                pool, paths, frame_dir, project_folder,
                dest=overlay_import_bin(project_folder, name,
                                        paths[0] if paths else ""))
            items = [found] if found is not None else []
        else:
            found = import_pool_item(
                pool, segment["overlay_path"],
                overlay_import_bin(project_folder, name,
                                   segment["overlay_path"]))
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
        # The caption artefact rides the placement its tight box
        # computed - read off the entry step 4.05 recorded - and the
        # placer SETS it then READS BACK what Resolve holds. A
        # sequence shares the mov's frame numbering, so the handle
        # trim is the same arithmetic.
        seen_intent_ids.append(segment.get("segment_id"))
        # `draw_intent` arms the pixel half: a declared pin first (the
        # captain's place wins over the row), else the DECLARED caption
        # row for this reel - so a sidecar placement served under a
        # superseded row is REPORTED rather than shipped. Unverifiable
        # captions (legacy canvas, no render props) ride without it,
        # exactly as before.
        _caption_canvas = _segment_canvas(segment)
        placed, note = place_overlay_segment(
            pool, timeline, items[0],
            track_index=track_plan.caption_row().index,
            record_frame=record_start,
            source_in_frame=segment["source_in_frame"],
            source_out_frame=segment["source_in_frame"] + content_frames,
            placement=(segment.get("tight_box") or {}).get("placement"),
            label=segment.get("segment_id", "caption"),
            kind="caption",
            segment_id=segment.get("segment_id"),
            intent=overlay_intent,
            canvas=_caption_canvas,
            frame=(width, height),
            intent_matched=applied_intent_keys,
            draw_gain=draw_gain,
            draw_intent=_draw_intent_for_segment(
                segment, kind="caption",
                segment_id=segment.get("segment_id"),
                placement_label=None,
                intent=overlay_intent, frame_wh=(width, height),
                project_folder=project_folder, reel_name=name,
                draw_gain=draw_gain))
        if not placed:
            print(f"Failed to place {segment.get('segment_id')}: {note}",
                  file=sys.stderr)
        elif note:
            print(f"  caption {segment.get('segment_id')}: {note}",
                  file=sys.stderr)
        if placed and _caption_canvas is not None:
            # One post-build sweep record per tight caption: the values
            # half (`overlay_verify.sweep_reel_overlays`) reads it back
            # off the timeline after the build and judges it against
            # intent. Full-canvas captions need no transform and leave
            # no record.
            _sweep_records.append({
                "label": segment.get("segment_id", "caption"),
                "kind": "caption",
                "segment_id": segment.get("segment_id"),
                # Captions carry no placing label (the label tier is
                # for reel graphics); the key stays so every sweep
                # record has one schema.
                "placement_label": None,
                "track_index": track_plan.caption_row().index,
                "record_frame": record_start,
                "canvas_wh": _segment_canvas(segment),
                "placement": (segment.get("tight_box") or {}
                              ).get("placement"),
                "overlay_path": segment.get("overlay_path") or "",
                "frames_dir": frame_dir,
            })

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
        element_item = import_pool_item(
            pool, placement.element_path,
            overlay_import_bin(project_folder, name,
                               placement.element_path))
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
        _place_transition_element(
            pool, project, timeline, name, placement, items[0],
            transitions_row, fps)


    # The explainer. ADDITIVE, exactly as the captions above are: it is
    # laid over picture that keeps playing and moves no frame of it, so
    # no keep range, no caption timing and no footage binding changes
    # because a reel carries one. Its whole span is placed - unlike a
    # caption, a graphic renders no handles either side, so
    # `total_frames` IS the content.
    if explainer_segments:
        suppressed_ids.extend(place_overlay_segments(
            pool, project, timeline, name, fps, explainer_segments,
            [row.index for row in track_plan.rows_for_role(EXPLAINER)],
            kind="explainer", check="F21",
            project_folder=project_folder,
            overlay_intent=overlay_intent, frame=(width, height),
            seen_ids=seen_intent_ids,
            do_not_draw=suppressions,
            intent_applied=applied_intent_keys,
            seen_labels=seen_intent_labels,
            sweep_out=_sweep_records,
            draw_gain=draw_gain))



    # The semantic visuals. ADDITIVE, exactly as the explainer above
    # is: laid over picture that keeps playing, moving no frame of it.
    # Each segment renders with no handles either side, so its whole
    # span is placed - `total_frames` IS the content.
    if semantic_segments:
        suppressed_ids.extend(place_overlay_segments(
            pool, project, timeline, name, fps, semantic_segments,
            [row.index for row in track_plan.rows_for_role(SEMANTIC)],
            kind="semantic visual", check="F22",
            project_folder=project_folder,
            overlay_intent=overlay_intent, frame=(width, height),
            seen_ids=seen_intent_ids,
            do_not_draw=suppressions,
            intent_applied=applied_intent_keys,
            seen_labels=seen_intent_labels,
            sweep_out=_sweep_records,
            draw_gain=draw_gain))

    # The speaker lower thirds. ADDITIVE, exactly as the two above are:
    # laid over picture that keeps playing, moving no frame of it, so a
    # reel carrying one is cut identically to a reel carrying none.
    # Each segment rides its own `tight_box.placement` where the
    # measured bind succeeded (`reel_lower_third_segments`), and needs
    # no transform where it stayed full canvas - `overlay_intent` is
    # passed anyway because the placer reads it per segment and a
    # full-canvas segment declares no canvas.
    if lower_third_segments:
        suppressed_ids.extend(place_overlay_segments(
            pool, project, timeline, name, fps, lower_third_segments,
            [row.index for row in track_plan.rows_for_role(MOTION_GRAPHICS)],
            kind="speaker lower third", check="F21",
            project_folder=project_folder,
            overlay_intent=overlay_intent, frame=(width, height),
            seen_ids=seen_intent_ids,
            do_not_draw=suppressions,
            intent_applied=applied_intent_keys,
            seen_labels=seen_intent_labels,
            sweep_out=_sweep_records,
            draw_gain=draw_gain))

    if overlay_intent:
        # Pins that matched nothing on this reel, said aloud and kept
        # on the record: a pin for a segment another reel carries is
        # ordinary, but ordinary said plainly - the rebuild that
        # silently drops the captain's corrections is the defect this
        # answers. REPORTED, never raised: the reel IS built. The pins
        # that DID apply ride beside them, so the durable report can
        # say "N of M applied" instead of only what was lost.
        from library.tools.overlay_intent import (
            report_unmatched as report_unmatched_intent)
        build_record["unmatched_overlay_intent"] = report_unmatched_intent(
            overlay_intent, seen_intent_ids,
            source=f"overlay_intent.json ({name})",
            seen_labels=seen_intent_labels)
        build_record["applied_overlay_intent"] = sorted(
            set(applied_intent_keys))
    else:
        build_record["applied_overlay_intent"] = []

    if suppressions:
        # Suppressions that matched nothing on this reel, said aloud
        # and kept on the record: the graphic left the plan, so the
        # rule holds nothing back until it is retired or
        # re-transcribed. REPORTED, never raised: the reel IS built.
        # `seen` is every segment this build considered - placed AND
        # held back - so a suppression doing its job never reports.
        build_record["suppressed_overlays"] = suppressed_ids
        build_record["unmatched_do_not_draw"] = _dnd.report_unmatched(
            suppressions, name,
            list(subtitle_segments or [])
            + list(explainer_segments or [])
            + list(semantic_segments or [])
            + list(lower_third_segments or []),
            source=f"do_not_draw.json ({name})")
    else:
        build_record["suppressed_overlays"] = []
        build_record["unmatched_do_not_draw"] = []

    # ── Overlay sweep: stored transforms against intent, after the build ──
    # `overlay_verify` held both halves of "does it land where intended"
    # with no production caller: the values half (stored Pan/Tilt against
    # the intent-resolved expectation - declared wins over computed) and
    # the pixels half (stored-render ink against intent-render ink, where
    # a decodable still reaches). A stored -7680 where the intent needs
    # past it fails here with both numbers named, where the read-back
    # gate reported "held exactly". REPORTED, never raised: the reel IS
    # built, and a sweep that fails a correct build is worse than the
    # defect it catches. Runs here - after the last placement, before
    # the occupancy pass deletes empty rows and moves the indices.
    if _sweep_records:
        from library.tools.overlay_verify import (
            sweep_reel_overlays as _sweep_reel_overlays,
        )
        print(f"── Overlay sweep ({len(_sweep_records)} tight overlay(s)) ──",
              file=sys.stderr)
        build_record["overlay_sweep"] = _sweep_reel_overlays(
            timeline, _sweep_records, intent=overlay_intent,
            full_wh=(width, height), draw_gain=draw_gain)
    else:
        build_record["overlay_sweep"] = {
            "passed": True, "checked": 0,
            "detail": "no tight overlays placed on this reel",
        }

    # ── Link pass: picture to speech, captions into the group ──
    # Span-based, in ONE call per speech item (see `link_reel_groups`
    # for why a second call breaks the first). Every call is read
    # back; what did not join is said rather than trusted.
    print(f"── Link Pass ──", file=sys.stderr)
    link_record = link_reel_groups(timeline, track_plan,
                                   offset_links=offset_links)
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
        # Sorted keys (library/tools/stable_json.py): this record is keyed
        # by reel name in build order, so without sorting two identical
        # builds are different bytes and every variant merge conflicts.
        json.dump(stored, f, indent=2, sort_keys=True)
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
        # Sorted keys (library/tools/stable_json.py): this record is keyed
        # by reel name in build order, so without sorting two identical
        # builds are different bytes and every variant merge conflicts.
        json.dump(stored, f, indent=2, sort_keys=True)


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
        # Sorted keys (library/tools/stable_json.py): this record is keyed
        # by reel name in build order, so without sorting two identical
        # builds are different bytes and every variant merge conflicts.
        json.dump(stored, f, indent=2, sort_keys=True)


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


STAGING_SUFFIX = bins.STAGING_TIMELINE_SUFFIX
"""What a rebuild is placed INTO before the gate passes.

An alias, not a declaration: `resolve_bin_layout` owns the spelling
so the build and the bin layout cannot drift apart - the layout uses
it to recognise a staging timeline and route it to the scratch bin.
See `STAGING_TIMELINE_SUFFIX` for the contract.

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


@under_lease("promote staged reels")
def promote_staged_reels(project_folder: str, resolve_project_name: str,
                         master_timeline_name: str,
                         staged_to_final: dict,
                         organise: bool = True,
                         allow_drops=None,
                         supersede=None,
                         retain=None) -> dict:
    """Move passing stagings onto their final timeline names.

    The ONLY place an approved timeline is deleted. Reachable only
    after the conformance gate passed on the staging containers, so a
    gate-failing build can never arrive here - structure, not
    vigilance. Per reel, in phases:

    0. the staged rebuild is DIFFED against the approved original it
       would replace, live timeline against live timeline
       (`library/tools/reel_replace_guard.py`, issue #925). A row
       that loses items, or a row the rebuild does not have at all,
       REFUSES - with the exact declaration that would proceed
       deliberately - unless the caller named that row in
       `allow_drops`. A retiring timeline that cannot be read refuses
       rather than passing. The diff is PER REEL: a reel that passes
       promotes while a refused sibling stays staged, so one refusal
       can never discard work that passed (the 2026-09-11 round lost
       three buildable reels this way). NOTHING for a passing reel is
       held back by a failing one, and nothing for a refused reel is
       renamed - its staging, baselines and hold remain, and the
       refusal names only itself;
    1. the approved original, where one exists, is renamed to its
       backup name - nothing is deleted and nothing is lost;
    2. the staging is renamed to the final name - each `SetName` is
       judged by what it returns, and a refusal names the backups that
       still hold the approved content;
    3. the sidecar baselines (provenance, transition overlays,
       explainer plans) are renamed staging -> final, so the next
       verifier grades the promoted timelines against the baseline the
       gate just passed rather than refusing on absence;
    4. only then is the backup DELETED - unless the caller named the
       reel in `retain`, when it is retired instead: renamed to
       `... (archived round NNN)` and filed in `05 - Reels/Archive`
       (`library/tools/reel_retirement.py`). A generation the
       retention bound releases is collected in the same call, guarded
       by `assert_deletion_scope` against the archived names alone -
       and on the default path the reel's earlier archived generations
       go too (retention 0), so the archive ends empty for it. A reel
       carrying a durable sign-off always retires: the captain approved
       that cut, and deleting its only copy is what "unless i
       explicitly ask for otherwise" does not cover;
    5. the round is stamped (`library/tools/round_version.py`): the
       rows the guard read in phase 0 are stored against the round
       this batch of the captain's feedback opened, which is what
       makes `round-diff` answer off disk afterwards.

    A reel whose approved timeline carries a durable SIGN-OFF
    (`library/tools/reel_signoff.py`) refuses in phase 0 unless the
    caller named it in `supersede` - the same declare-then-proceed
    shape `allow_drops` takes, per reel, so one signed-off reel never
    holds back a sibling. A declared supersession moves the sign-off
    to `superseded` after the rename lands; it is never deleted.

    A fresh build - no timeline under the final name yet - skips phases
    0 and 1 for that reel; everything else is identical, so there is one
    swap path rather than a replacing path and a fresh path.

    Stale debris REFUSES: leftover staging or backup containers from
    an interrupted run are named and the operator clears them in
    Resolve before re-running. Reusing a debris container as this
    run's staging would grade one run's content as another's.

    `allow_drops` declares intended reductions by ROW, never by
    blanket: `{final timeline name: [row keys]}` where a row key is
    `"video:Semantic"` (or the bare `"Semantic"`), or a flat list of
    `"ROW"` / `"FINAL::ROW"` specs applied to what this call promotes.
    There is no "allow everything" value - a blanket override is the
    same as no guard.

    `retain` names the reels whose superseded generation this promotion
    may RETIRE into the archive rather than delete, by base reel name
    in any container spelling (`reel_retirement.parse_retain`). The
    explicit opt-in for a future "keep the old one so I can compare":
    absent - the default - means one timeline per reel and an empty
    archive. A reel carrying a durable sign-off retires whatever this
    says. There is no "retain everything" value.

    Every marker on a retiring timeline is READ before phase 1, and
    the ones whose picture still plays in the replacement are placed
    onto it after phase 2. One that cannot be placed is named, with
    the captain's own words, on stderr. Whether markers SHOULD always
    be carried stays an open product question; that a promotion must
    not discard them in silence does not.

    Returns `{"promoted": [final names...], "organised": ...,
    "replace_reports": {final: guard report...},
    "refused": {final: refusal text...},
    "markers": {final: {"carried": [...], "uncarried": [...]}}}`.

    A partial refusal still RAISES - automation must not read it as
    clean - after the passing reels have fully promoted (renames,
    sidecars, holds, filing). The raise names only the refused
    reel(s) with their guard text, lists what already promoted, and
    states that the refused stagings remain in the project with
    their baselines and holds intact for a deliberate re-run.
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

    from library.tools import reel_replace_guard as _guard
    try:
        declared = _guard.parse_specs(allow_drops, finals)
    except ValueError as bad_declaration:
        raise ReelBuildError(
            f"REFUSING to promote: {bad_declaration}") from bad_declaration
    from library.tools import reel_signoff as _signoff
    try:
        declared_supersessions = _signoff.parse_supersede(supersede)
    except ValueError as bad_declaration:
        raise ReelBuildError(
            f"REFUSING to promote: {bad_declaration}") from bad_declaration
    from library.tools import reel_retirement as _retire_decl
    try:
        declared_retain = _retire_decl.parse_retain(retain)
    except ValueError as bad_declaration:
        raise ReelBuildError(
            f"REFUSING to promote: {bad_declaration}") from bad_declaration
    replace_reports = {}
    refused = {}
    superseded_signoffs = {}
    # The captain's typed notes, read off each RETIRING timeline before
    # anything is renamed (`library/tools/marker_carry.py`). Promotion
    # replaces the timeline object, so its markers go with it - which
    # is how three notes on Reel 09 became "0 note(s)" with nothing
    # saying so. Read here, reported below by name, and carried where
    # the picture under them still plays in the replacement.
    from library.tools import marker_carry as _markers
    carried_markers = {}
    # The rows the guard reads off each incoming staging, kept for the
    # round stamp below. A fresh reel has no original to diff against
    # but is still part of the round, so its rows are taken too -
    # best effort, never refusing - rather than the version record
    # having a hole where every first build should be.
    incoming_by_final = {}
    for final in finals:
        if final in originals:
            continue
        # A FRESH reel: nothing is being replaced, so the guard has no
        # question to ask and cannot refuse here. Its rows are still
        # taken for the round, best effort - a version record that
        # could not read a first build says nothing about it, and must
        # never refuse a promotion the guard itself would have waved
        # through.
        try:
            incoming_by_final[final] = _guard.snapshot_timeline(
                staged_found[staged_to_final[final]],
                staged_to_final[final], side="staged")
        except Exception:                                   # noqa: BLE001
            pass
    for final in finals:
        if final not in originals:
            continue
        staging = staged_to_final[final]
        try:
            _signoff.assert_declared(
                project_folder, final, declared_supersessions)
            incoming_rows = _guard.snapshot_timeline(
                staged_found[staging], staging, side="staged")
            incoming_by_final[final] = incoming_rows
            retired_rows = _guard.snapshot_timeline(
                originals[final], final, side="retiring")
            replace_reports[final] = _guard.check_replacement(
                final, staging, retired_rows, incoming_rows,
                allowed=declared.get(final, ()))
            notes = _markers.read_markers(originals[final], final)
            if notes:
                keep, lost = _markers.plan_carry(
                    notes, staged_found[staging])
                # SAID before the rename, so a promotion about to
                # discard the captain's words has already said which
                # even if the rename below refuses.
                _markers.report(final, keep, lost)
                carried_markers[final] = {"carried": keep,
                                          "uncarried": lost}
        except _guard.ReplaceGuardUnreadable as unreadable:
            refused[final] = (
                f"REFUSING to promote {final!r}: {unreadable} Nothing "
                f"for this reel was renamed; its approved timeline is "
                f"still in the project.")
        except _markers.MarkerCarryUnreadable as unreadable:
            refused[final] = (
                f"REFUSING to promote {final!r}: {unreadable} Nothing "
                f"for this reel was renamed; its approved timeline is "
                f"still in the project.")
        except _guard.ReplaceGuardRefused as guard_refused:
            refused[final] = str(guard_refused)
        except _signoff.SignOffNotDeclared as not_declared:
            refused[final] = str(not_declared)
        except _signoff.SignOffsUnreadable as unreadable:
            refused[final] = (
                f"REFUSING to promote {final!r}: {unreadable} Nothing "
                f"for this reel was renamed; its approved timeline is "
                f"still in the project.")
    ok_finals = [final for final in finals if final not in refused]

    for final in ok_finals:
        staging = staged_to_final[final]
        if final in originals:
            if not originals[final].SetName(backups[final]):
                raise ReelBuildError(
                    f"REFUSING to promote: Resolve would not rename "
                    f"{final!r} aside to {backups[final]!r}. Nothing "
                    f"was deleted and the staging {staging!r} is "
                    f"untouched - re-run once Resolve allows renames.")
            print(f"Retired {final} to {backups[final]}", flush=True)
    for final in ok_finals:
        staging = staged_to_final[final]
        if not staged_found[staging].SetName(final):
            raise ReelBuildError(
                f"REFUSING to promote: Resolve would not rename staging "
                f"{staging!r} to {final!r}. The approved content is "
                f"safe under "
                f"{[backups[f] for f in ok_finals if f in originals]} - "
                f"rename it back in Resolve and re-run.")
        print(f"Promoted {staging} to {final}", flush=True)
        notes = carried_markers.get(final)
        if notes and notes["carried"]:
            declined = _markers.place(staged_found[staging],
                                      notes["carried"])
            notes["declined"] = declined

    import os
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    # The baselines were filed under the staging containers the gate
    # graded; the claim is renamed to the final names, which carry the
    # same cards over the same footage. `{old: new}` throughout.
    # Scoped to the reels that actually promoted: a refused reel's
    # staging, baselines and hold stay exactly as they were, so the
    # refusal can be re-driven deliberately without rebuilding what
    # already landed.
    promoted_claimed = {staged_to_final[final]: final
                        for final in ok_finals}
    if not ok_finals:
        lines = [
            f"REFUSING to promote {len(refused)} reel(s): "
            f"{sorted(refused)}. Nothing was renamed; the approved "
            f"timelines are still in the project."]
        for final in finals:
            if final in refused:
                lines.append(refused[final])
        raise ReelBuildError("\n".join(lines))
    from library.tools.plan_provenance import rename_reel_entries
    rename_reel_entries(review_dir, promoted_claimed)
    _rename_overlay_records(review_dir, promoted_claimed)
    from library.tools.explainer_plan import rename_plan_reels
    rename_plan_reels(project_folder, promoted_claimed)
    from library.tools.reel_semantic_visual import rename_record_reels
    rename_record_reels(project_folder, promoted_claimed)
    from library.tools.reel_semantic_visual import rename_span_record_reels
    rename_span_record_reels(project_folder, promoted_claimed)
    # The lower-third record, for the same reason and on the same
    # mapping: a plan left under the staging name is a plan F24 cannot
    # find, and F24 then reports the graphics it placed as items
    # nothing accounts for.
    from library.tools.speaker_identity import (
        rename_plan_reels as _rename_lower_third_plans)
    _rename_lower_third_plans(project_folder, promoted_claimed)
    # The render ledger binds each caption to the timeline it was
    # rendered for, and it is read as a reference ROOT. Left naming the
    # staging container this promotion just renamed away, every entry
    # would pin its mov LIVE for ever against a timeline that does not
    # exist - which is why no sweep could reclaim the 584 MB the
    # 2026-09-10 measurement found. Never fatal: a ledger that cannot be
    # re-pointed leaves MORE files live, which is the safe direction.
    try:
        from library.tools.caption_asset_gc import rename_ledger_timelines
        from library.tools.project_layout import Area, ProjectLayout
        renamed = rename_ledger_timelines(
            str(ProjectLayout(project_folder).read_dir(
                Area.SUBTITLE_SEGMENTS)),
            promoted_claimed)
        if renamed["renamed"]:
            print(f"Re-pointed {renamed['renamed']} render-ledger "
                  f"binding(s) at the promoted names", flush=True)
    except Exception as ledger_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  render ledger not re-pointed ({ledger_failed}) - its "
              f"entries still name the staging timeline, so their files "
              f"stay LIVE and nothing is swept", file=_sys.stderr)

    # ── DELETE by default, RETIRE only when asked (the captain, 2026-09-18)
    # Before this every backup was renamed into `05 - Reels/Archive`
    # with the round it was current for, which is how "(archived round
    # 001)" came to sit on reel titles the captain reviews. Now the
    # backup is DELETED unless the caller named the reel in `retain` -
    # the explicit per-reel opt-in for "keep the old one so I can
    # compare" - and the archive stays empty otherwise. A signed-off
    # reel always retires: the captain approved that cut. Only a
    # generation the retention bound releases - never one just retired,
    # and never one carrying a sign-off - is collected. Bounded by the
    # number of REELS rather than the number of rounds, and by exactly
    # the reels this call promoted.
    #
    # Never fatal. The reels are promoted; a retirement that cannot
    # rename leaves the approved content under its backup name, which
    # the next build refuses on loudly rather than losing.
    from library.tools import reel_retirement as _retire
    from library.tools import round_version as _rounds
    retirement = {"archived": {}, "unfiled": [], "collected": [],
                  "kept": [], "deleted": []}
    rounds_by_final: dict = {}
    try:
        recorded = _rounds.discover(project_folder)
        current_round = recorded[-1]["round"] if recorded else 1
        signed = set(_signoff.signed_off(project_folder))
        # A sign-off is itself an explicit keep: the cut the captain
        # approved retires even when nobody declared `retain`.
        retain_finals = sorted(
            final for final in ok_finals if final in originals
            and (_signoff.base_name(final) in declared_retain
                 or _signoff.base_name(final) in signed))
        delete_finals = sorted(
            final for final in ok_finals if final in originals
            and final not in retain_finals)
        rounds_by_final.update({
            final: _retire.retiring_round(recorded, final, current_round)
            for final in retain_finals})
        backup_objects = {}
        for timeline in timelines_to_replace(
                project, {backups[final] for final in ok_finals}):
            for final in ok_finals:
                if timeline.GetName() == backups[final]:
                    backup_objects[final] = timeline
        if retain_finals:
            retirement.update(_retire.retire_timelines(
                project, pool,
                {final: backup_objects[final] for final in retain_finals
                 if final in backup_objects},
                rounds_by_final))
        if delete_finals:
            discarded = _retire.delete_backups(
                project, pool,
                {backups[final]: backup_objects[final]
                 for final in delete_finals
                 if final in backup_objects})
            retirement["deleted"] = discarded["deleted"]
        live_names = set()
        for index in range(1, project.GetTimelineCount() + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline:
                live_names.add(timeline.GetName())
        if retirement["archived"]:
            retirement.update(_retire.collect_superseded(
                project, pool, live_names, list(retirement["archived"]),
                signed))
        if delete_finals:
            # Nothing was retired for these reels, so the bound above
            # has nothing to bound: their earlier archived generations
            # go too (retention 0, signed-off ones excepted), and the
            # archive ends empty for them.
            legacy = _retire.collect_superseded(
                project, pool, live_names, delete_finals, signed,
                retained=0)
            retirement["collected"].extend(legacy["collected"])
            retirement["kept"].extend(legacy["kept"])
        print(_retire.render(retirement), flush=True)
    except Exception as retirement_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  retirement refused ({retirement_failed}) - the reels "
              f"are promoted; the replaced timelines are still in the "
              f"project under their backup names and nothing was "
              f"deleted", file=_sys.stderr)
        retirement["refused"] = f"{retirement_failed}"

    # ── COMPARISONS, bounded the way the archive is ──────────────
    # Suffix verification builds (`name_suffix`, e.g. `... (baseline
    # scratch)`) promote into suffixed finals that sit pending a human
    # decision, and nothing ever retired or collected them - the same
    # accumulation the archive above was built to stop, arriving by
    # the door it does not watch
    # (`library/tools/comparison_retirement.py`). On the next
    # promotion touching a base reel, its superseded live comparisons
    # retire to the same archive and its archived ones beyond one per
    # reel are collected under the same guard, never touching a
    # sign-off, a hold, a variant or a plan final.
    #
    # Never fatal, for the block above's reason: the reels are
    # promoted, and a comparison lifecycle that breaks a build is
    # worse than one that skips a round loudly.
    from library.tools import comparison_retirement as _comp
    comparison_retirement: dict = {"bases": [], "retired": {},
                                   "unfiled": [], "collect": [],
                                   "collected": [], "kept": [],
                                   "planned_retire": [],
                                   "planned_collect": [],
                                   "refused": "", "notes": []}
    try:
        comparison_retirement = _comp.collect_for_bases(
            project, pool, project_folder, ok_finals,
            master_timeline_name)
        print(_comp.render(comparison_retirement), flush=True)
    except Exception as comparison_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  comparison retirement refused ({comparison_failed}) - "
              f"the reels are promoted; superseded comparisons are "
              f"still in the project and nothing was deleted",
              file=_sys.stderr)
        comparison_retirement["refused"] = f"{comparison_failed}"

    # A declared supersession ends the sign-off it was declared
    # against - AFTER the rename landed, so a promotion that refused
    # later never retires an approval it did not replace. Recorded as
    # superseded, never deleted: "this reel was approved once and then
    # rebuilt" is exactly the question that had no answer before.
    for final in ok_finals:
        if _signoff.base_name(final) not in declared_supersessions:
            continue
        try:
            ended = _signoff.supersede(
                project_folder, final,
                round_number=rounds_by_final.get(final))
        except Exception:  # noqa: BLE001
            ended = None
        if ended:
            superseded_signoffs[final] = ended
            print(f"Sign-off on {final!r} superseded by this build "
                  f"(recorded, not deleted)", flush=True)

    # The promotion happened - the staging containers are now the
    # approved timelines under their final names - so their pending
    # holds go (issue #971). Released AFTER the renames, so a failed
    # promotion keeps every hold; and BEFORE the organise/sweep
    # below, which is tidying, not promotion. A refused reel's hold
    # stays: its staging is still pending, and the sweep must keep
    # refusing it loudly rather than sweeping it. A suffix
    # verification build keeps its suffixed-final hold: that container
    # still awaits a human promotion decision, and only an explicit
    # release (or a later promotion naming it as staging) ends it.
    from library.tools import staging_holds as _holds
    _holds.release_holds(
        project_folder,
        [staged_to_final[final] for final in ok_finals])

    # ── STAMP THE ROUND ──────────────────────────────────────────
    # The version object (`library/tools/round_version.py`). The rows
    # stored here are the ones the replace guard already read off the
    # incoming timeline in phase 0, so this costs no Resolve call - and
    # storing them is what lets `round-diff` answer long after the
    # timeline they describe has been retired and collected. Never
    # fatal: a version record that fails a build is worse than none.
    stamped_round = None
    try:
        from library.tools.plan_provenance import read_provenance
        stamped_round = _rounds.stamp_promotion(
            project_folder,
            {final: incoming_by_final[final] for final in ok_finals
             if incoming_by_final.get(final)},
            read_provenance(review_dir))
        print(f"Round {stamped_round['round']}: stamped "
              f"{len(stamped_round.get('reels') or {})} reel(s). "
              f"`manage_project.py round-diff <project>` shows what "
              f"changed since the round before.", flush=True)
    except Exception as stamp_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  round not stamped ({stamp_failed}) - the reels are "
              f"promoted and unaffected, but this promotion is not in "
              f"the version record", file=_sys.stderr)

    # ── CLOSE EACH PROMOTED REEL'S BUILD SIGNATURE ──────────────
    # The carried half (`library/tools/reel_rebuild_need.py`): what
    # this reel's approved timeline holds, read back NOW. This is the
    # only moment it means "what is approved" - the build placed a
    # staging container, and until the renames above the final name
    # still held the reel being replaced. One `snapshot_timeline` per
    # promoted reel, MEASURED at 0.043-0.153 s each.
    #
    # Never fatal: a signature that fails to close costs the NEXT
    # build a placement, which is the fail-closed direction. A
    # promotion that failed over a bookkeeping write would be worse.
    try:
        from library.tools import reel_rebuild_need as _need_record
        from library.tools.plan_provenance import (
            record_carried_digests as _record_carried)
        _live = {}
        for _index in range(1, (project.GetTimelineCount() or 0) + 1):
            _timeline = project.GetTimelineByIndex(_index)
            if _timeline is not None and _timeline.GetName() in ok_finals:
                _live[_timeline.GetName()] = _timeline
        _carried = {}
        for _final in ok_finals:
            _timeline = _live.get(_final)
            if _timeline is None:
                continue
            # SELF-read: the reel made current before it is read. The
            # digest depends on which timeline is current, so the two
            # ends must read the same way or they disagree by
            # construction (`reel_rebuild_need.carried_digest_live`).
            _digest = _need_record.carried_digest_live(project, _timeline)
            if _digest:
                _carried[_final] = _digest
        _record_carried(review_dir, _carried)
        if _carried:
            print(f"  build signature closed for {len(_carried)} "
                  f"promoted reel(s) - the next build can tell whether "
                  f"they still need a Resolve pass", flush=True)
    except Exception as _signature_failed:                # noqa: BLE001
        import sys as _sys
        print(f"  build signature not closed ({_signature_failed}) - "
              f"the reels are promoted and unaffected; the next build "
              f"will place them again rather than assume",
              file=_sys.stderr)

    # ── FILE THE MEDIA POOL ──────────────────────────────────
    # The pass is idempotent and files by reference: each generated
    # clip goes under the reel timeline that places it
    # (`library/tools/resolve_organization.py`, executed by
    # `library/tools/execution/organise_media_pool.py`), so running it
    # twice moves nothing the second time - which is what makes it
    # something a build can call without anyone remembering to.
    #
    # Never fatal, for the same reason as every other post-promotion
    # step above: the reels are already promoted, and a filing pass
    # that errors taking a good build down with it would be a bad
    # trade. A refusal is said on stderr and carried on the record as
    # `{"refused": ...}`, so a later reader can see the filing was
    # attempted and declined - `resolve-organize` is the retry.
    #
    # Quiet when there is nothing to do: an idle build prints one line
    # instead of the full trio. The unplaced and scratch reports keep
    # their every-time semantics on the `resolve-organize` CLI; the
    # build hook says them when it moved something.
    organised = None
    if organise:
        from library.tools.execution.organise_media_pool import (
            organise_project, render_unplaced)
        from library.tools.resolve_organization import (
            render_scratch_report)
        try:
            organised = organise_project(
                project, project_folder, master_timeline_name, apply=True)
        except Exception as organise_failed:  # noqa: BLE001
            import sys as _sys
            print(f"  media-pool filing refused ({organise_failed}) - "
                  f"the reels are promoted and unaffected; file them with "
                  f"resolve-organize", file=_sys.stderr)
            organised = {"refused": f"{organise_failed}"}
        else:
            _moves = ((organised.get("journal") or {}).get("moves")) or []
            _retired = ((organised.get("retirement") or {}).get("retired")) or []
            if not _moves and not _retired:
                print("  Media pool already organised - nothing to file.",
                      flush=True)
            else:
                print(f"Filed {len(_moves)} media-pool "
                      f"item(s); undo with "
                      f"resolve-organize --revert "
                      f"{organised['journal']['journal_path']}", flush=True)
                print(render_unplaced(organised["unplaced"]), flush=True)
                print(render_scratch_report(organised["scratch"]), flush=True)

    # The sweep runs HERE, on every build, because this is the moment
    # the project is settled: the reels carry their final names, the
    # pool has just been filed, and anything still unplaced and named by
    # no current record is a generation a previous iteration left
    # behind. A refusal is reported and does NOT fail the build - the
    # reels are already promoted, and declining to remove something is
    # a good outcome (AGENTS.md 5).
    swept = None
    if organise:
        from library.tools.build_sweep import render_sweep, sweep_build
        try:
            swept = sweep_build(project, project_folder, apply=True)
            print(render_sweep(swept), flush=True)
        except Exception as sweep_failed:  # noqa: BLE001
            import sys as _sys
            print(f"  build sweep refused ({sweep_failed}) - nothing "
                  f"further was removed; the reels are unaffected",
                  file=_sys.stderr)
    if refused:
        _raise_partial_promotion(finals, ok_finals, refused)
    return {"promoted": ok_finals, "organised": organised, "swept": swept,
            "replace_reports": replace_reports,
            "refused": dict(refused),
            "markers": carried_markers,
            "retirement": retirement,
            "comparison_retirement": comparison_retirement,
            "round": stamped_round,
            "superseded_signoffs": superseded_signoffs}


def _raise_partial_promotion(finals, ok_finals, refused) -> None:
    """The refusal half of a partially promoted batch, naming only itself.

    The passing reels already fully promoted above (renames, sidecars,
    holds, filing) before this raises, so nothing that passed is
    discarded because a sibling failed. Each refused reel's staging
    container, baselines and hold remain in place for a deliberate
    re-run.
    """
    lines = [
        f"Promoted {len(ok_finals)} reel(s): {sorted(ok_finals)}. "
        f"REFUSING to promote {len(refused)} reel(s): "
        f"{sorted(refused)}. The refused staging containers remain in "
        f"the project with their baselines and holds; their approved "
        f"timelines are untouched."]
    for final in finals:
        if final in refused:
            lines.append(refused[final])
    raise ReelBuildError("\n".join(lines))


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
    filed stay where they are - the plan is derived from the live
    proposals file, which the discard did not touch.

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
    # The gate refused, so nothing here is pending promotion any more:
    # release the named holds whether or not the timelines were still
    # in the project (issue #971). A hold for a name this call never
    # staged is a no-op release, so variant-final names reaching this
    # path cannot unprotect anything.
    from library.tools import staging_holds as _holds
    _holds.release_holds(project_folder, staging)
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    from library.tools.plan_provenance import drop_reel_entries
    drop_reel_entries(review_dir, staging)
    _drop_overlay_records(review_dir, staging)
    from library.tools.explainer_plan import drop_plan_reels
    drop_plan_reels(project_folder, staging)
    from library.tools.reel_semantic_visual import drop_record_reels
    drop_record_reels(project_folder, staging)
    from library.tools.reel_semantic_visual import drop_span_record_reels
    drop_span_record_reels(project_folder, staging)
    from library.tools.speaker_identity import (
        drop_plan_reels as _drop_lower_third_plans)
    _drop_lower_third_plans(project_folder, staging)
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

    Deleting is a write, so under an EXCLUSIVE hold - the same hold
    the in-process discard paths take, now that no whole-build hold
    covers this call.
    """
    project = _connect_resolve_project(resolve_project_name)
    with resolve_lease("discard refused staging", exclusive=True):
        discard_staged_reels(project, project_folder, staging_names,
                             master_timeline_name)


def _verify_payload(row: dict | None, *, passed: bool,
                    refusal: str = "") -> dict:
    """The summary's verify slice: gate verdict plus conformance counts.

    `row` is one `conformance_rows` entry (None where the report could
    not be read - the verdict stays, the counts go absent). The full
    finding rows live in the report the slice names, never here.
    """
    from library.tools import reel_phase_log as _payload_log
    row = row if isinstance(row, dict) else {}
    return {
        "passed": passed,
        "errors": row.get("errors"),
        "warnings": row.get("warnings"),
        "finding_classes": row.get("finding_classes"),
        "captions_expected": row.get("captions_expected"),
        "captions_actual": row.get("captions_actual"),
        "uncaptioned_seconds": row.get("uncaptioned_seconds"),
        "plan_seconds": row.get("plan_seconds"),
        "actual_frames": row.get("actual_frames"),
        "report": _payload_log.CONFORMANCE_REPORT_REL,
        "refusal": refusal,
    }


def _owed_layers(awaiting_report: dict | None, *names: str) -> list:
    """Model-answer layers one reel still owes, best-effort [].

    The owing record keys reels by whatever name was current when the
    answer went missing (staging for reels this build touched, final
    for ones it did not), so every spelling the summary knows is
    tried in order.
    """
    wanted = {str(name) for name in names if name}
    for row in ((awaiting_report or {}).get("reels") or ()):
        if isinstance(row, dict) and str(row.get("reel")) in wanted:
            return list(row.get("layers") or [])
    return []


def _file_reel_summary(project_folder: str, *, number: int, name: str,
                       facts: dict, outcome: str,
                       decision_reason: str = "",
                       verify: dict | None = None,
                       retired_to: str | None = None,
                       markers: dict | None = None,
                       version_control: dict | None = None,
                       drift_end: dict | None = None,
                       answers_owed: list | None = None,
                       gain_record: dict | None = None) -> None:
    """Assemble one reel's build summary from the facts stash and file it.

    Never raises: the phase log's own contract says a filing failure is
    said on stderr, and this wrapper adds the same for an assembly
    failure, because an instrument must never fail the build it
    instruments (AGENTS.md 10.4). `facts` is the loop's per-reel stash
    (derivation facts always, placement facts where placed);
    everything else arrives only on paths that computed it.
    """
    try:
        from library.tools import reel_phase_log as _summary_log
        decision = (facts.get("decision") or {})
        payload = _summary_log.assemble_summary(
            outcome=outcome,
            staging=facts.get("staging"), final=facts.get("final"),
            decision=(decision_reason or decision.get("reason")),
            answers=facts.get("answers"),
            semantic_record=facts.get("semantic_record"),
            span_record=facts.get("span_record"),
            motion_record=facts.get("motion_record"),
            captain_trims=facts.get("trims"),
            keep_exclusions=facts.get("keep_exclusions"),
            draw_gain_record=gain_record,
            captions=facts.get("captions"),
            cards=facts.get("cards"),
            suppressed_overlays=facts.get("suppressed_overlays"),
            overlay_sweep=facts.get("overlay_sweep"),
            transition_placements=facts.get("transition_placements"),
            has_freeze_tail=facts.get("has_freeze_tail"),
            verify=verify,
            retired_to=retired_to,
            markers=markers,
            version_control=version_control,
            drift_end=drift_end,
            answers_owed=answers_owed)
        try:
            reel_number = int(number)
        except Exception:
            reel_number = 0
        _summary_log.file_build_summary(
            project_folder, reel_number, name, payload)
    except Exception:
        pass


def rebuild_reels_in_project(project_slug: str, skip_captions: bool = False,
                             verify: bool = True, only=None,
                             name_suffix: str = "",
                             organise: bool = True,
                             intent_file: str = "",
                             allow_drops=None,
                             supersede=None,
                             retain=None,
                             reuse_unchanged: bool = True) -> dict:
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
       backups by default - retiring them only for reels named in
       `retain` or carrying a sign-off (`promote_staged_reels`).
       Filing the media pool (`organise`) happens here, because filing is about reels that
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
    TIDIES UP rather than accumulating: Current plan means the PLAN -
    the live proposals file's approved moments
    (`plan_provenance.current_plan_names`), never the reels this call
    placed - so building one reel leaves every other planned reel
    exactly where it was, and only a reel the live plan no longer names
    moves to `Reels/Earlier plans` - moved and relabelled, never
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

    `intent_file` names an overlay-intent file outright
    (`library/tools/overlay_intent.py`); empty reads the project's own
    `external/overlay_intent.json` when the captain declared one, and
    builds purely computed placements otherwise. A declared position
    wins over the computed one, so a rebuild lands where the captain
    put things - Reel 09's hand corrections survive the next build
    instead of being recomputed past.

    `allow_drops` declares intended reductions by ROW for the replace
    guard (`library/tools/reel_replace_guard.py`, issue #925): a list
    of `"ROW"` (every reel this call promotes) or `"FINAL::ROW"`
    specs, or an already per-final `{final: [rows]}` mapping. It is
    normalised here against the finals this call stages and carried on
    the record as `allow_drops`, so the `verify_reels` node promotes
    with the same declaration the build was given rather than
    re-deriving one.

    `reuse_unchanged` is the per-reel decision NOT to pay a Resolve
    pass for a reel nothing changed about
    (`library/tools/reel_rebuild_need.py`). Measured in the tree, a
    reel's Resolve pass is 19.4-67.1 s of which the Fusion comp pass
    is 17.0-63.7 s of FIXED overhead (`docs/RULE_EVIDENCE.md`, "what
    it costs"), so placing an unchanged reel again buys nothing and
    costs that. The decision is made from two digests - the
    derivation this build computed, and the live timeline read back -
    and it is FAIL-CLOSED: an absent record, an unreadable reel or
    either digest disagreeing all mean REBUILD. The reason per reel is
    printed and carried on the record as `rebuild_need`.

    `False` places every reel this call names, whatever the state
    says. That is what a caller asks for when it wants the placement
    itself - a re-place onto drift-free state, or a measurement of
    what the pass costs - and it is how the before/after of the skip
    is measured at all.

    The one caller that passes False is the `build_reels` node of
    `library/processes/reels`, whose process has `verify_reels` as its
    own node. A build that was placed and a build that conformed are
    two facts that fail for different reasons, and a ledger keyed by
    node id can only tell them apart if two nodes recorded them.
    Running the verifier in both places would report one set of
    findings twice under two step ids.

    THE LEASE this call holds is the placement it exists to protect,
    never the whole build. The instance cursor (AGENTS.md 5,
    `library/tools/resolve_lock.py`) is established and written
    through in narrow sections, and each holds the instance only
    for itself:

    - connect (`"build reels connect"`, EXCLUSIVE, seconds): the
      scripting handshake blocks unboundedly while another lane
      places, so it is ordered behind any in-progress placement
      rather than contended inside it.
    - the draw-gain probe (`"draw-gain probe"`, EXCLUSIVE): it
      creates a fixed-name scratch timeline, appends, stills twice
      and deletes - a second concurrent probe would delete the
      first's scratch as "stale".
    - one hold per placed reel (`f"place {name}"` with the staging
      container, EXCLUSIVE):
      the carried self-read, the rebuild-need decision, the
      placement and the Fusion comp pass, which acts on the current
      timeline's items in a subprocess that inherits this hold. The
      per-reel derivation before it - Remotion caption and card
      renders, model-answer reads, digest computation - touches no
      Resolve state and holds nothing, so lanes overlap there and
      serialise only here, for the measured 19.4-67.1 s a reel's
      Resolve pass costs.
    - the gate and the sweep (`"verify built reels"`,
      `"sweep all reels"`, SHARED): reads that must grade a stable
      staging, several of which run together while no writer runs.
      The advisory surveys (prebuild census, divergence) are read
      through their own per-timeline shared leases instead - a
      minutes-long outer read hold would block every other lane's
      placement, which is the serialisation this shape exists to
      end. Promotion holds its own exclusive lease
      (`promote_staged_reels`); both discard paths take one.

    Two lanes building the SAME project at once are still a
    supervisor error, and no lease shape can fix them: staging
    containers are deterministic per reel name, so a second build
    would place beside the first under the same names, and the
    per-reel sidecar merges are read-modify-write. The stale-debris
    refusal above is what says so loudly instead of grading one
    run's content as another's. Cross-project overlap is the
    parallelism this shape provides.

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

    if os.path.isabs(project_slug) and os.path.isdir(project_slug):
        project_folder = project_slug
    else:
        proj = get_project(project_slug)
        if not proj:
            raise ValueError(f"Unknown project {project_slug}")
        project_folder = str(proj.project_root)

    # The captain's pinned overlay positions, if they declared any.
    # Missing is the normal case (purely computed placements); a
    # malformed file REFUSES rather than building past it, because a
    # silently ignored pin rebuilds the wrong positions while
    # reading as honoured.
    from library.tools.overlay_intent import (
        OverlayIntentError, load_intent)
    try:
        overlay_intent = load_intent(
            project_folder,
            intent_file=intent_file or None)
    except OverlayIntentError as exc:
        raise ReelBuildError(
            f"overlay intent cannot be honoured: {exc}") from exc
    # The graphics the captain deleted, if they declared any. Missing
    # is the normal case (everything planned is drawn); a malformed
    # file REFUSES rather than building past it, because a silently
    # ignored deletion rebuilds the graphic back onto the timeline -
    # which is the defect this answers.
    from library.tools.do_not_draw import DoNotDrawError, load_rules
    try:
        suppression_rules = load_rules(project_folder)
    except DoNotDrawError as exc:
        raise ReelBuildError(
            f"do_not_draw cannot be honoured: {exc}") from exc

    with open(os.path.join(project_folder, "project.yaml")) as f:
        config = yaml.safe_load(f)
        
    resolve_config = config.get("resolve", {})
    resolve_name = resolve_config.get("project_name", os.path.basename(project_slug))
    master_timeline_name = resolve_config.get("timeline_name")
    if not master_timeline_name:
        raise ValueError("Missing 'timeline_name' under 'resolve' in project.yaml")

    # The handshake first, under its own short EXCLUSIVE hold: while
    # another lane places, a connect blocks inside `scriptapp` with no
    # bound, no diagnostic and no holder to name (measured 2026-09-12)
    # - so it is ordered behind any in-progress placement rather than
    # contended inside it. Handles stay valid after the hold releases;
    # every cursor write below takes its own hold.
    with resolve_lease("build reels connect", exclusive=True):
        resolve = scriptapp_preserving_locale(dvr, "Resolve")
        pm = resolve.GetProjectManager()
        project = resolve_project_exactly(pm, resolve_name)

    # The frame this project's reels are DELIVERED in, resolved ONCE and
    # threaded from here: the timeline size, every overlay render, the
    # TV-frame check and the entry-unit refusal all read this one value,
    # so none of them can disagree with another. `geo-podcast` declares
    # nothing and gets the vertical default, which is the frame every
    # reel it has was built at.
    reel_width, reel_height = reel_resolution(project_folder)

    # Pan/Tilt sets are interpreted in the ENTRY timeline's units and
    # silently converted to the target's (measured 2026-09-10 - see
    # `overlay_placement.entry_unit_mismatch`), while same-process
    # read-back echoes the set value. Reels build at the delivery frame,
    # so a session entered on the 3840x2160 master would store every
    # overlay scaled while every check reads back clean. Refuse
    # before anything lands rather than placing a wrong-but-stored
    # timeline.
    from library.tools.overlay_placement import entry_unit_mismatch
    unit_refusal = entry_unit_mismatch(project, (reel_width, reel_height))
    if unit_refusal:
        raise ReelBuildError(unit_refusal)

    # ── TRANSFORM DRIFT BASELINE (start of build) ──
    # Every reel's own build snapshot compared against a self-read of
    # its live timeline, printing the per-reel factor
    # (`library/tools/drift_check.py`). The same check runs again at
    # the end of this call, so whatever halves Pan and Tilt the way
    # 2026-09-16 did is bracketed to this build instead of to 21
    # hours. Reports, never refuses: a detector that fails the build
    # is a gate, and this one is an instrument. Its cursor excursions
    # run under a SHARED hold: they exclude another lane's placement
    # while running beside its reads, and whatever cursor position
    # they leave behind is re-established by every placement's own
    # per-write check.
    try:
        from library.tools import drift_check as _drift_start
        with resolve_lease("build reels drift baseline",
                           exclusive=False):
            _drift_start.check_project(project_folder, when="build start",
                                       resolve=resolve, project=project)
    except Exception as exc:  # noqa: BLE001
        print(f"  drift baseline unavailable ({exc!r}) - the build "
              f"continues without a start bracket", flush=True)

    from library.tools.reel_proposal import proposal_path as _proposal_path
    proposal_path = str(_proposal_path(project_folder))
    moments = read_proposal(proposal_path)

    # Archive the plan so it survives being overwritten by the next
    # selector run.  The archive sits alongside the live file, named
    # with a timestamp so it sorts chronologically and never collides.
    from library.tools.plan_provenance import archive_plan
    archive_plan(proposal_path)
    
    # Read as BYTES, once: the same read supplies the transcript every
    # decision below is made from AND the digest the rebuild-need
    # signature carries (`library/tools/reel_rebuild_need.py`).
    # Digesting the file a second time would be a second spelling of
    # this path (`tests/test_operations.py`) and a second chance for
    # the two reads to disagree.
    with open(os.path.join(project_folder, "pipeline_output/scratch/timeline_transcript/transcript.json"), "rb") as f:
        transcript_bytes = f.read()
    transcript = json.loads(transcript_bytes.decode("utf-8"))
    transcript_digest = hashlib.sha256(transcript_bytes).hexdigest()

    # Stored proposals predate the boundary drawer: a boundary drawn
    # before it can sit inside a word, and the build reads the file
    # AS-IS.  Repair each moment on the way through with the SAME snap
    # generation runs, so the rebuild plays word-edge boundaries without
    # re-deciding WHICH moments the captain approved
    # (`reel_proposal.snap_moment_to_speech`).  In memory only - the file
    # keeps exactly what they ruled on.
    from library.tools.reel_proposal import (
        decision_lines,
        snap_moment_to_speech,
    )
    from library.tools.reel_ledger import stored_windows as _stored_windows
    repaired = []
    # The DECLARED windows, captured BEFORE the repair below moves
    # anything: the snap widens boundaries outward, so after it the
    # moment no longer says what was declared - and the window audit
    # (`reel_ledger.audit_ranges`) checks final ranges against the
    # declaration, never the repair. Keyed by reel number, with the
    # repair moves beside them for the ledger.
    stored_siblings: dict = {}
    repair_moves_by_number: dict = {}
    for moment in moments:
        stored_siblings[int(moment.number)] = {
            "body": _stored_windows(moment)[0],
            "closer": _stored_windows(moment)[1],
        }
    for moment in moments:
        fixed, moves = snap_moment_to_speech(moment, transcript)
        repair_moves_by_number[int(moment.number)] = list(moves)
        for move in moves:
            word = (f" through '{move['through']}'"
                    if move.get("through") else "")
            print(f"  Reel {moment.number:02d}: {move['boundary']} "
                  f"{move['was']:.3f}s -> {move['now']:.3f}s{word} "
                  f"(stored proposal predates the boundary snap)",
                  file=sys.stderr)
            # A cascade is loud on the run that would place it; a run
            # with nothing over the decision threshold prints exactly
            # what it printed before.  The repair itself is untouched.
            for line in decision_lines(moment.number, move, transcript,
                                       project_folder):
                print(line, file=sys.stderr)
        repaired.append(fixed)
    moments = repaired

    # The captain's recorded closer pins, applied to APPROVED moments in
    # memory - the file keeps exactly what they ruled on, like the
    # repair above. Selection draws pinned starts on new proposals; a
    # stored proposal predates the pin, so the build redraws it on the
    # way through rather than rebuilding the old start the captain
    # struck. Applying a recorded pin is obedience, not re-decision
    # (the standing of keep exclusions since PR 865), and the redrawn
    # span is checked like a new one before it moves. A malformed pin
    # file REFUSES rather than building silently past it; a pin no
    # approved closer answers is reported, never silent.
    from library.tools import captain_edits as _edits
    try:
        _pin_edits = _edits.load_edits(project_folder)
    except _edits.CaptainEditError as exc:
        raise ReelBuildError(
            f"captain_edits cannot be read: {exc}. A recorded pin the "
            f"build cannot read must refuse, never build silently past "
            f"it.") from exc
    _pin_applied: list = []
    if any(e.get("kind") == "redraw_closer" for e in _pin_edits):
        moments, _pin_applied, _pin_held, _pin_stale = \
            _edits.apply_closer_redraws(moments, transcript, _pin_edits)
        for record in _pin_applied:
            print(f"  Reel {record['reel']:02d}: closer "
                  f"{record['was'][0]:.3f}s -> {record['now'][0]:.3f}s "
                  f"(now opens on {record['anchor_phrase']!r} - "
                  f"{record['reason']})", file=sys.stderr)
        for record in _pin_held:
            print(f"  Reel {record['reel']:02d}: closer already opens on "
                  f"{record['anchor_phrase']!r} - pin held",
                  file=sys.stderr)
        _edits.report_stale(_pin_stale)

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
    # And the INVERSE: seconds the captain says stay IN, which withdraw
    # take cuts rather than making them. Read beside the strikes, for
    # the same reason - a declaration the store cannot supply must
    # refuse the batch, not the twelfth reel.
    keep_insistences = _tc.keep_insistences(project_folder)

    # The master read-back this build places from, under a SHARED
    # hold: a named-handle read that runs beside other readers while
    # no writer moves underneath it. `snapshot_timeline` takes the
    # same shared hold itself; the re-entry is a no-op.
    with resolve_lease("build reels survey", exclusive=False):
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

    # ── DRAW-GAIN PROBE (start of build) ──
    # The Pan/Tilt draw gain is renderer STATE, not geometry: it read
    # 1.0 on 2026-09-11 and 2.0 on 2026-09-17 with no restart and no
    # deploy in between. So this build calibrates the renderer it is
    # about to place onto - one probe element, one still, once per
    # build, never per element - and every placement below is computed
    # with what the probe measured. A probe that cannot run falls back
    # to the documented fallback LOUDLY (never silently), and a
    # measured gain that disagrees with the fallback lands on the
    # record as a finding: that disagreement is the first
    # machine-readable handle on the renderer state moving again.
    # Reports, never refuses: like the drift baseline, an instrument.
    # EXCLUSIVE, and the one whole-build hold that stays coarse: the
    # probe creates a fixed-name scratch timeline, appends, stills
    # twice and deletes, so a second concurrent probe would remove
    # the first's scratch as "stale" and still the wrong timeline.
    from library.tools import draw_gain_probe as _gain_probe
    from library.tools.resolve_transform import (
        FALLBACK_DRAW_GAIN as _FALLBACK_GAIN)
    try:
        with resolve_lease("draw-gain probe", exclusive=True):
            gain_record = _gain_probe.calibrate(
                resolve, project, (reel_width, reel_height))
    except Exception as exc:  # noqa: BLE001 - probe never raises, belt
        # and braces: a probe-shaped surprise must not fail a build.
        gain_record = {
            "gain": _FALLBACK_GAIN, "source": "fallback",
            "disagrees_with_fallback": False,
            "warnings": [f"probe raised {exc!r} outside itself"],
            "probe": {},
        }
    _gain_probe.log_record(gain_record)
    run_gain = float(gain_record.get("gain") or _FALLBACK_GAIN)

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
    # The debris refusal reads live state, so it reads under a SHARED
    # hold: no placement renames underneath the enumeration. Fast -
    # two enumerations, never the minutes-long census below, which
    # reads through its own per-timeline holds instead for exactly
    # this reason.
    with resolve_lease("build reels debris check", exclusive=False):
        stale_staging = timelines_to_replace(project, staged_names)
        stale_backups = timelines_to_replace(
            project, {backup_name(final) for final in target_names})
    if stale_staging:
        raise ReelBuildError(
            f"REFUSING to build: {len(stale_staging)} staging "
            f"timeline(s) from an interrupted run are still in the "
            f"project - "
            f"{sorted(t.GetName() for t in stale_staging)}. Delete "
            f"them in Resolve and re-run; reusing a debris container "
            f"would grade one run's content as another's.")
    if stale_backups:
        raise ReelBuildError(
            f"REFUSING to build: {len(stale_backups)} backup "
            f"timeline(s) from an interrupted promotion are still in "
            f"the project - "
            f"{sorted(t.GetName() for t in stale_backups)}. They hold "
            f"approved content a previous run moved aside. Restore or "
            f"delete them in Resolve and re-run.")

    # Measure before building: the cross-reel census runs FIRST, on
    # every multi-reel build, whether or not anyone remembers. It reads
    # the existing final timelines this call is about to replace -
    # read-only, minutes - and PRINTS where the same kind of element
    # sits in materially different places, which is the signal a
    # decision is owed before the build, not after (2026-09-11: the
    # caption row census existed only after a build, a refusal and a
    # discard). Advisory only, never a refusal: a legitimate difference
    # is allowed and a build must not be hostage to a warning.
    try:
        from library.tools import reel_prebuild_census as _census
        prebuild_census = _census.report_prebuild(
            project, list(staged_to_final))
    except Exception as census_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  pre-build census unavailable ({census_failed}) - "
              f"building without it", file=_sys.stderr)
        prebuild_census = {"unavailable": str(census_failed)}

    # DECLARED, and CARRIED: which reels have what the project declares.
    # Over EVERY approved reel, not the ones this call places - the
    # reels a build leaves behind are exactly the ones that go stale,
    # and 2026-09-11 shipped a logo card that reached one reel of eight
    # while the engine's claim was "every reel inherits it". Read-only,
    # printed, never a refusal: whether to rebuild a diverged reel is
    # the captain's decision (`library/tools/reel_divergence.py`).
    # EVERY APPROVED REEL, read back once. Two readers want exactly
    # these reads - the divergence survey below and the rebuild-need
    # decision in the loop (`reel_rebuild_need`) - so the reads happen
    # HERE, once, and both are handed the result. Measured on the
    # captain's eight reels: 0.043-0.153 s each, 0.445 s for all
    # eight, which is what makes a read-back affordable per build at
    # all. Taken outside the survey's try so a survey that fails does
    # not silently cost every reel a placement.
    from library.tools import reel_divergence as _divergence
    _approved = [built_name(m, name_suffix) for m in moments
                 if str(getattr(m.approval, "value",
                                m.approval)) == "approved"]
    try:
        live_snapshots, live_unread = _divergence.snapshots_for(
            project, _approved, snapshot_fn=snapshot_timeline)
    except Exception as _reads_failed:                    # noqa: BLE001
        print(f"  live reel read-back unavailable ({_reads_failed}) - "
              f"every reel will be placed", file=sys.stderr)
        live_snapshots, live_unread = {}, {
            name: f"read-back unavailable ({_reads_failed})"
            for name in _approved}

    try:
        from library.tools.reel_ending import resolve_ending as _resolve_end
        _by_final = {built_name(m, name_suffix): m for m in moments}
        _snapshots, _unread = dict(live_snapshots), dict(live_unread)
        _endings = {}
        for _final in _approved:
            try:
                _ending = _resolve_end(project_folder, _final,
                                       _by_final.get(_final), transcript)
            except Exception:  # noqa: BLE001
                continue
            if _ending is not None:
                _endings[_final] = _ending
        from library.tools.plan_provenance import (
            read_provenance as _read_provenance)
        _recorded_assets = (_read_provenance(os.path.join(
            project_folder, "pipeline_output", "review")) or {}).get(
                "asset_hashes") or {}
        divergence_report = _divergence.report_divergence(
            project_folder, _snapshots, brand_effect=_brand_effect(
                project_folder),
            endings=_endings, notes=_unread,
            recorded_assets=_recorded_assets)
    except Exception as divergence_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  divergence survey unavailable ({divergence_failed}) - "
              f"building without it", file=_sys.stderr)
        divergence_report = {"unavailable": str(divergence_failed)}

    # The forced consultation (`library/tools/edit_depth.py`): each
    # layer against the one it derives from, every build, read-only.
    # One import, one call - the module runs only on `edit_video`
    # otherwise, while the reels it never consults are the ones the
    # captain reviews. Printed, never a refusal: frozen step-output
    # history fails a wording scan no rebuild can fix, and a witness
    # that refused on history would hold every build hostage to it.
    try:
        coherence_report = report_layer_coherence(project_folder)
    except Exception as coherence_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  layer-coherence witness unavailable "
              f"({coherence_failed}) - building without it",
              file=_sys.stderr)
        coherence_report = {"unavailable": str(coherence_failed)}

    # Proof in proportion to the edit: what verification THIS build
    # owes, computed from real state and SAID before anything is
    # placed (`library/tools/proof_scope.py`). A new plan, a run with
    # shared-state declarations, or a census that disagreed earns the
    # full burden; a re-run of the recorded plan earns stills for the
    # reels without prior proof and no post-build census past the
    # pre-build report above. Guidance, never a gate: it prints and
    # records, it cannot fail. The kind never scales - stills are real
    # pixels, never read-backs, at every level.
    try:
        from library.tools import proof_scope as _proof_scope
        _shared_declarations: list = []
        if allow_drops:
            _shared_declarations.append("allow-drops declared")
        if keep_insistences:
            _shared_declarations.append(
                f"{len(keep_insistences)} keep insistence(s) recorded")
        if keep_exclusions:
            _shared_declarations.append(
                f"{len(keep_exclusions)} keep exclusion(s) recorded")
        if _pin_applied:
            _shared_declarations.append(
                f"{len(_pin_applied)} closer redraw(s) applied")
        if overlay_intent:
            _shared_declarations.append("overlay intent pins recorded")
        _proof_inputs = _proof_scope.build_inputs(
            os.path.join(project_folder, "pipeline_output", "review"),
            proposal_path, list(staged_to_final))
        proof_scope_record = _proof_scope.report_scope(
            _proof_scope.scope_for_build(
                reels=list(staged_to_final),
                plan_is_new=bool(_proof_inputs["plan_is_new"]),
                shared_declarations=_shared_declarations,
                disagreement_reported=bool(
                    (prebuild_census or {}).get("disagreements")),
                reels_without_prior_proof=list(
                    _proof_inputs["reels_without_prior_proof"])))
    except Exception as scope_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  proof scope unavailable ({scope_failed}) - prove fully",
              file=_sys.stderr)
        proof_scope_record = {"level": "FULL",
                              "stills": list(staged_to_final),
                              "census": "full",
                              "reasons": ["scope could not be computed"]}

    # Pending-promotion holds (issue #971): every staging container
    # this call is about to place is held against the cleanup sweep
    # until promotion or discard releases it - a verified staging
    # that is never promoted must survive tidying, and a timeline
    # with a scratch-shaped name that no plan claims is exactly what
    # the sweep would otherwise take. On a suffix build the suffixed
    # final is held too: the build promotes INTO it, and it then sits
    # pending a human promotion decision no automatic step ends.
    # Approved finals of ordinary builds are never held - no sweep
    # may take them anyway. Taken up front, so even a crash between
    # staging and promotion leaves the protection, not the hole; a
    # rebuild re-takes the same names and the window restarts.
    from library.tools import staging_holds as _holds
    for _final, _staging in staged_to_final.items():
        _holds.take_hold(
            project_folder, _staging, awaiting=_final,
            reason="staged rebuild awaiting promotion",
            taken_by="rebuild_reels_in_project")
    if name_suffix:
        for _target in sorted(target_names):
            _holds.take_hold(
                project_folder, _target, awaiting=None,
                reason=("suffix verification build - awaiting an "
                        "explicit promotion decision"),
                taken_by="rebuild_reels_in_project")

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

    # ── DOES THIS REEL NEED A RESOLVE PASS AT ALL ────────────────
    # The wholesale halves of every reel's signature, computed ONCE:
    # the engine source (0.059 s measured), the project's own
    # declarations (0.003 s), the plan, the transcript and the live
    # master read-back. Each is deliberately over-covering - a digest
    # that misses an input skips a reel that needed rebuilding, and a
    # digest that covers too much costs a placement
    # (`library/tools/reel_rebuild_need.py`).
    from library.tools import reel_rebuild_need as _need
    from library.tools.plan_provenance import (
        plan_content_hash as _plan_hash,
        read_provenance as _read_prov,
    )
    _engine_code = _need.engine_code_digest()
    # The PROJECT-WIDE half: `project.yaml`, the brand template's
    # content, the project's artwork trees, every `external/`
    # declaration that is not a per-reel pin store, and the digests of
    # the assets a declaration names by absolute path. Changing any of
    # them can change every reel, so any of them changing costs a full
    # round. The per-reel pin stores are deliberately NOT here - their
    # effect on one reel is carried by that reel's own derivation, and
    # folding them in would make a pin on Reel 13 rebuild Reel 23
    # (`library/tools/reel_rebuild_need.py`).
    try:
        from library.tools.reel_divergence import (
            asset_digests as _signature_assets)
        _signature_asset_hashes = _signature_assets(
            project_folder, brand_effect=_brand_effect(project_folder))
    except Exception:                                     # noqa: BLE001
        _signature_asset_hashes = None
    _project_wide = _need.project_wide_digest(
        project_folder,
        brand_template=_brand_effect(project_folder),
        asset_hashes=_signature_asset_hashes)
    _plan_digest = _plan_hash(proposal_path)
    # SELF-read, for the same reason as the carried half below:
    # read off a timeline that is not current, the master's
    # transforms come back different, and this digest is part of
    # EVERY reel's derivation - so a run that entered on another
    # timeline would rebuild the whole project. A cursor excursion,
    # so under a SHARED hold like the baseline above.
    with resolve_lease("build reels master digest", exclusive=False):
        _master_digest = _need.carried_digest_live(project, timeline)

    # The carried half is read with THE REEL ITSELF CURRENT, because a
    # transform does not read back the same way twice: what Resolve
    # returns depends on which timeline is current at the moment of
    # the read (`reel_rebuild_need.carried_digest_live`, with the
    # measurement). The survey's snapshots above cannot serve here -
    # they were all taken under one current timeline, whichever the
    # run happened to enter on, so they disagree with the records
    # promotion closed. Each read restores the current timeline, so
    # the entry units the placements are computed in are unchanged.
    _carried_self_reads: dict = {}

    def _carried_self_read(final_name: str):
        if final_name in _carried_self_reads:
            return _carried_self_reads[final_name]
        _timeline = None
        try:
            for _i in range(1, (project.GetTimelineCount() or 0) + 1):
                _t = project.GetTimelineByIndex(_i)
                if _t is not None and _t.GetName() == final_name:
                    _timeline = _t
                    break
        except Exception:                                 # noqa: BLE001
            _timeline = None
        digest = (_need.carried_digest_live(project, _timeline)
                  if _timeline is not None else None)
        _carried_self_reads[final_name] = digest
        return digest
    _recorded_signatures = ((_read_prov(os.path.join(
        project_folder, "pipeline_output", "review")) or {}).get(
            "build_signatures") or {})
    build_signatures: dict = {}
    rebuild_decisions: list = []
    left_alone: list = []

    built_reel_names = []
    caption_hashes = {}
    footage_binding_hashes = {}
    overlay_records = {}
    track_plans = {}
    skipped_by_exclusion: list = []
    # Reels this call deliberately did NOT build because a final range
    # touches no declared window of its own (`reel_ledger.audit_ranges`
    # raising `OutOfWindowRange`): the per-reel shape of a refusal,
    # beside `skipped_by_exclusion` above. Whatever the captain
    # approved stays exactly as it is, WITH the reason on the record.
    skipped_out_of_window: list = []
    # Per-reel facts for the build summary (`reel_phase_log`), keyed
    # by STAGING name and grown through the loop: derivation facts
    # after the decision, placement facts after the build returns,
    # verify/promote facts after the gate. Filed once the reel's story
    # for this build is complete - never re-derived, never estimated.
    summary_facts: dict = {}
    # Which declared overlay pins each placed reel honoured, and which
    # it placed nothing for: per-reel lists off each `build_record`,
    # aggregated after the loop into the durable `overlay_intent_report`
    # the mainline return carries. A pin is counted once however many
    # segments it fanned out over.
    intent_applied_by_reel: dict = {}
    intent_unmatched_by_reel: dict = {}
    # Read ONCE, before the loop: a malformed declaration must stop the
    # whole build, not the twelfth reel of nineteen.
    card_declarations = declared_cards(project_folder)
    explainer_plans = []
    lower_third_plans = []
    semantic_records = []
    span_records = []
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
    reel_look_decl = _reel_look.resolve_look(project_folder,
                                             reel_width, reel_height)
    motion_records = []
    if reel_look_decl is not None:
        print(f"TV-frame look declared by {reel_look_decl['origin']}: "
              f"punch-in {reel_look_decl['punch_in']}, frame "
              f"{os.path.basename(reel_look_decl['asset'])}", file=sys.stderr)
    # The project's designed film look, read ONCE beside the TV-frame
    # declaration for the same reason: a malformed series_look must stop
    # the whole build, and the Fusion pass merges it onto every
    # picture clip of every reel below. {} is a project that declares
    # no look, and then the reels carry no grade - the captain agreed
    # the Fusion route, and agreement covers what was declared.
    reel_grade_look = _reel_look.resolve_grade_look(project_folder)
    if reel_grade_look:
        print(f"  grade look rides the Fusion pass: "
              f"{sorted(reel_grade_look)}", file=sys.stderr)
    # The look's CDL half, read ONCE beside the Fusion half for the
    # same reason: the project's own `style.series_look` winning
    # whole-slot over its brand template's (`effective_series_look`).
    # {} is a project that declares no look, and then the reels carry
    # no CDL - the timeline each reel got before this half existed.
    reel_grade_cdl = _reel_look.resolve_grade_cdl(project_folder)
    # The project's declared PowerGrade, read ONCE for the batch beside
    # the two look halves and for the same reason: a refused
    # declaration - no provenance, no file - must stop the whole build
    # rather than the fourteenth reel. None is a project that declares
    # none, and then the CDL half is the route exactly as before.
    reel_power_grade = _reel_look.resolve_power_grade(project_folder)
    # Where each reel ENDS and what draws over its tail
    # (`library/tools/reel_ending.py`), and the captain's caption-only
    # timing (`library/tools/caption_timing.py`). Both read ONCE here
    # for the reason every declaration above is: a malformed one must
    # stop the whole build before a timeline exists, not the twelfth
    # reel of nineteen. `[]`/None is a project that declares neither,
    # and then every reel below is built exactly as it was before
    # either owner existed.
    from library.tools import caption_timing as _caption_timing
    from library.tools import reel_ending as _reel_ending
    _endings = _reel_ending.load_endings(project_folder)
    _caption_pins = _caption_timing.load_pins(project_folder)
    if _endings:
        print(f"  {len(_endings)} declared reel ending(s): "
              + ", ".join(f"{e['reel']} -> {e.get('tail_element', 'none')}"
                          for e in _endings), file=sys.stderr)
    if _caption_pins:
        print(f"  {len(_caption_pins)} declared caption-timing pin(s)",
              file=sys.stderr)
    if reel_power_grade:
        print(f"  grade rides the COLOR PAGE: "
              f"{os.path.basename(reel_power_grade['path'])}"
              + (f", per-clip CDL on node "
                 f"{reel_power_grade['cdl_node']!r}"
                 if reel_power_grade.get("cdl_node")
                 else ", DRX alone - no per-clip CDL declared"),
              file=sys.stderr)
    if reel_grade_cdl:
        print(f"  grade CDL rides SetCDL on every picture item: "
              f"slope "
              f"{reel_grade_cdl['slope_r']:.4f}/"
              f"{reel_grade_cdl['slope_g']:.4f}/"
              f"{reel_grade_cdl['slope_b']:.4f}, "
              f"sat {reel_grade_cdl['saturation']:.4f}", file=sys.stderr)
    # What the model read of each reel, and what the project declares.
    # Both are read ONCE for the batch: the judgement is one file and the
    # declaration is one project, and re-reading either per reel would be
    # nineteen answers to one question.
    judgement = _read_judgement(project_folder)
    brand_effect = _brand_effect(project_folder)
    # The declared card row, read ONCE for the batch beside the
    # judgement and the declarations above: one project, one answer.
    card_row_role = card_row_role_for_project(project_folder, brand_effect)
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
            try:
                _moment_number = int(moment.number)
            except Exception:
                _moment_number = 0
            # The summary's slot, opened before anything can skip: a
            # reel skipped below still files what decided it.
            summary_facts[name] = {
                "staging": name, "final": final,
                "number": _moment_number,
            }
            print(f"Building {name}", flush=True)
            # Where this reel's records START, so a reel the decision
            # below LEAVES ALONE can be rolled back out of them. The
            # per-reel record writers all merge by reel NAME, so an
            # entry filed under a staging container nothing staged is
            # a phantom baseline the next verifier would grade
            # against. Bookmarked rather than conditional: the
            # derivation has to run to be compared, so the records it
            # produces exist before the decision does.
            _mark = (len(explainer_plans), len(semantic_records),
                     len(span_records), len(motion_records),
                     len(lower_third_plans))
            # This approved moment's own strikes, cut from its ranges
            # below. Said on the run that honours them: a cut the
            # operator cannot see is a silent content change. Grown
            # over wordless clip lead-in first (`grow_cuts...`), so a
            # strike at a word's start does not strand 6 frames of
            # room tone the readability floor then refuses.
            # Grown over wordless clip lead-in at the head
            # (`grow_cuts_over_wordless_leadin`) AND over a wordless
            # tail at the end (`grow_cuts_over_wordless_tail`), because
            # the nub the readability floor refuses forms at either
            # edge: measured 2026-09-12, lc-0006 ends on Craig's word
            # edge at 899.400 and the master's angle switch is at
            # 899.482, so resuming there admitted 2 frames of the wrong
            # camera. Neither growth may cross a timed word.
            #
            # ONE spelling (`moment_cuts_and_insistences`): the pass-1
            # build and `reel.ask` cut from the same list, and two
            # spellings of which seconds a reel plays would be two
            # different reels.
            moment_cuts, moment_insisted = moment_cuts_and_insistences(
                moment, transcript, keep_exclusions, keep_insistences)
            for _cut, _ident in withdraw_insisted_cuts(
                    redundant_takes(moment.timeline_start,
                                    moment.timeline_end, transcript),
                    moment_insisted)[1]:
                print(f"  keep insistence {_ident} WITHDRAWS the take cut "
                      f"at {_cut.dropped_start:.2f}-{_cut.dropped_end:.2f}s "
                      f"({_cut.speaker}): those words stay in - "
                      f"{_cut.dropped_text[:80]!r}", flush=True)
            # And what the take judge withdrew: a candidate cut that is
            # not one telling removed and a later one kept. SAID with
            # the reason, like an insistence - a withdrawal nobody can
            # see is a content change nobody can see. Judged past the
            # insistences, the same order `reel_ranges` applies, so a
            # recorded insistence is never hidden behind one of these.
            _insisted_kept, _ = withdraw_insisted_cuts(
                redundant_takes(moment.timeline_start,
                                moment.timeline_end, transcript),
                moment_insisted)
            for _refused in judge_take_cuts(
                    _insisted_kept, moment.timeline_start,
                    moment.timeline_end, transcript)[1]:
                _cut = _refused["cut"]
                print(f"  take judge WITHDRAWS the take cut at "
                      f"{_cut.dropped_start:.2f}-{_cut.dropped_end:.2f}s "
                      f"({_cut.speaker}): {_refused['why']} "
                      f"[{_refused['reason']}]", flush=True)
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
            # The keep ranges and the planned cards, by the ONE spelling
            # (`derive_reel_ranges_and_cards`) the ask path shares: a
            # strike covering the whole body raises `ExclusionWipesBody`
            # and this reel is dropped WITH the reason, never split and
            # never emptied - the approved timeline already in Resolve
            # is left exactly as it is, like a reel this call did not
            # name.
            # The trim records the shared derivation applied, for the
            # per-reel summary: the derivation owns the computation
            # (one spelling for the ask path and the build), and this
            # collector carries the records back out without a second
            # computation - re-running the trims here would re-trim
            # already-trimmed ranges and file fiction.
            _trim_records: dict = {}
            try:
                ranges, cards, _ending_decl = (
                    derive_reel_ranges_and_cards(
                        moment, transcript, master_clips,
                        project_folder, 24000 / 1001, name,
                        moment_cuts, moment_insisted,
                        card_declarations=card_declarations,
                        look_decl=reel_look_decl,
                        reel_width=reel_width,
                        reel_height=reel_height,
                        collect_trims=_trim_records))
            except ExclusionWipesBody as wiped:
                reason = str(wiped)
                print(f"  SKIPPING {name}: {reason}", flush=True)
                skipped_by_exclusion.append({"reel": name,
                                             "number": moment.number,
                                             "reason": reason})
                try:
                    from library.tools import reel_phase_log as _skip_log
                    _skip_log.log_wait(
                        project_folder, moment.number, name,
                        f"skipped by exclusion: {reason}")
                except Exception:
                    pass
                _file_reel_summary(
                    project_folder,
                    number=summary_facts[name]["number"], name=name,
                    facts=summary_facts[name],
                    outcome="skipped_by_exclusion",
                    decision_reason=f"skipped by exclusion: {reason}")
                current_staging = None
                continue

            # ── The window audit ──
            # `library/tools/reel_ledger.py`: every final range must
            # touch the reel's DECLARED body or its declared closer -
            # what the stored file says, before the repair above moves
            # anything. A range touching neither is refused for THIS
            # reel only (the `ExclusionWipesBody` shape above): whatever
            # the captain approved stays exactly as it is, WITH the
            # reason, and the other reels build untouched. A range that
            # touches but reaches PAST its window is kept and said
            # loudly with the invaded reel named: refusing those would
            # break legitimate word-edge cover (Reel 02's closer end
            # moved 0.22s to cover its closing word), so the audit
            # names them instead of stopping them. Either way the
            # ledger is filed under the FINAL name, so the WHY is on
            # disk whether this reel places or not.
            try:
                from library.tools import reel_ledger as _reel_ledger
                # Directly indexed: captured above from the same moment
                # list this loop walks, so a missing key is a programming
                # error and reads as one rather than as a window verdict.
                _stored = stored_siblings[int(moment.number)]
                _window_ledger = _reel_ledger.audit_ranges(
                    number=int(moment.number), staging=name,
                    final=destage(name),
                    stored_body=_stored["body"],
                    stored_closer=_stored["closer"],
                    repaired_body=(float(moment.timeline_start),
                                   float(moment.timeline_end)),
                    repaired_closer=cta_range(moment),
                    ranges=ranges,
                    sibling_windows=stored_siblings,
                    repair_moves=repair_moves_by_number.get(
                        int(moment.number), ()),
                    pin_records=[
                        record for record in _pin_applied
                        if int(record.get("reel", -1))
                        == int(moment.number)],
                    trim_records=_trim_records,
                    ending=_ending_decl)
            except _reel_ledger.OutOfWindowRange as out_of_window:
                reason = str(out_of_window)
                print(f"  SKIPPING {name}: {reason}", flush=True)
                try:
                    _reel_ledger.file_reel_ledger(
                        project_folder, destage(name),
                        out_of_window.ledger or {})
                except Exception:
                    pass
                skipped_out_of_window.append({"reel": name,
                                              "number": moment.number,
                                              "reason": reason})
                try:
                    from library.tools import reel_phase_log as _skip_log
                    _skip_log.log_wait(
                        project_folder, moment.number, name,
                        f"skipped out of window: {reason}")
                except Exception:
                    pass
                _file_reel_summary(
                    project_folder,
                    number=summary_facts[name]["number"], name=name,
                    facts=summary_facts[name],
                    outcome="skipped_out_of_window",
                    decision_reason=f"skipped out of window: {reason}")
                current_staging = None
                continue
            _reel_ledger.file_reel_ledger(
                project_folder, destage(name), _window_ledger)
            for _row in _window_ledger.get("ranges") or []:
                _over = (_row.get("overhang_seconds") or {})
                if not (_over.get("before") or _over.get("after")):
                    continue
                _invaded = ", ".join(
                    f"reel {hit['reel']}'s declared {hit['window']} "
                    f"({hit['seconds'][0]:.2f}-{hit['seconds'][1]:.2f}s)"
                    for hit in (_row.get("invades") or [])) or (
                    "no other reel's declared pool")
                print(f"  {name}: {_row['origin']} range "
                      f"{_row['master'][0]:.2f}-{_row['master'][1]:.2f}s "
                      f"reaches "
                      f"{_over.get('before', 0):.2f}s before / "
                      f"{_over.get('after', 0):.2f}s past its declared "
                      f"{_row['origin']} window - overlapping "
                      f"{_invaded}", flush=True)


            # Full-frame elements FIRST, because a head card decides where
            # every other thing on this reel starts. Planned above and
            # rendered here before anything is placed, so a declaration
            # that cannot be resolved - a binding with nothing behind
            # it, a typeface that will not draw - stops this reel here
            # rather than after a timeline exists
            # (`library/tools/full_frame_element.py`).
            if cards:
                from library.tools.full_frame_element import render_reel_cards
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
                fps=24000 / 1001, width=reel_width, height=reel_height,
                timeline_name=name, lead_seconds=lead,
                draw_gain=run_gain)

            # The captain's caption-only timing
            # (`library/tools/caption_timing.py`), applied to the
            # RENDERED segments and before anything places them. Not a
            # `span_retime`: those hold picture spans, and a card moved
            # off the picture under it is a class that vocabulary
            # cannot say. Reel 13's closer cards died twice for it.
            if subtitle_segments is not None and _caption_pins:
                (subtitle_segments, _cap_applied, _cap_short,
                 _cap_stale) = _caption_timing.apply_pins(
                    subtitle_segments, _caption_pins, 24000 / 1001)
                _caption_timing.report(_cap_applied, _cap_short,
                                       _cap_stale)

            # The animated explainer. A project that declares none gets
            # `([], plan)` with the plan saying `not_declared`, and the
            # timeline it gets is the one it got before this existed.
            explainer_segments, explainer_plan = reel_explainer_segments(
                moment, transcript, ranges, project_folder,
                fps=24000 / 1001, width=reel_width, height=reel_height,
                judgement=judgement, brand_effect=brand_effect,
                timeline_name=name, draw_gain=run_gain)
            explainer_plans.append(explainer_plan)

            # WHO IS SPEAKING, named on their first appearance in THIS
            # reel (`library/tools/speaker_identity.py`). A project that
            # declares no speakers gets `([], plan)` with the plan
            # saying `not_declared`, and the timeline it gets is the one
            # it got before this existed. The rendered captions travel
            # because the box this graphic sits in is the safe area with
            # its bottom raised off their MEASURED height - a lower
            # third ON the caption row is the one thing the roster's own
            # `never` for this element forbids.
            lower_third_segments, lower_third_plan = (
                reel_lower_third_segments(
                    moment, transcript, ranges, project_folder,
                    fps=24000 / 1001, width=reel_width, height=reel_height,
                    brand_effect=brand_effect, timeline_name=name,
                    subtitle_segments=subtitle_segments,
                    lead_seconds=lead, extra_cuts=moment_cuts,
                    draw_gain=run_gain))
            lower_third_plans.append(lower_third_plan)

            # The three visual asks - semantic, span, motion - by the ONE
            # spelling (`write_visual_asks`) the ask path shares, so an
            # ask written without a build is what a build would have
            # written. The answers are read off the response files when
            # a model has written them, and the reel builds without
            # them - SAID as `awaiting_model_answer` - when none have.
            # A headless build never blocks on a model.
            from library.tools import reel_semantic_visual as sem_vis
            ask_paths = write_visual_asks(
                moment, transcript, ranges, master_clips,
                project_folder, 24000 / 1001, name, cards,
                reel_look_decl)
            semantic_segments, semantic_record = sem_vis.build_for_reel(
                moment, transcript, ranges, project_folder,
                fps=24000 / 1001, width=reel_width, height=reel_height,
                timeline_name=name)
            semantic_records.append(semantic_record)

            # The span picture plan: what the MODEL says each beat of
            # this reel's speech SHOWS. Resolved and RECORDED here, in
            # the V6 record's own convention
            # (`library/tools/reel_semantic_visual.py`), for the
            # conformance gate (F23) to grade against. Nothing places
            # the moments yet - the placer needs its own change - so an
            # all-refused plan must read as a refusal, never as a
            # decision for no pictures.
            span_record = sem_vis.resolve_span_record(
                moment, transcript, ranges, project_folder,
                fps=24000 / 1001, timeline_name=name,
                asked=bool(ask_paths["reel_span"]))
            span_records.append(span_record)

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
                spine = getattr(subtitle_segments, "spine", None)
                if entries and _caption_pins:
                    # The hash answers "what was said and WHEN in the
                    # reel", so it must describe the PINNED timings -
                    # otherwise the verifier, re-deriving them through
                    # the same owner, reads a pin as a changed card
                    # grouping and refuses to grade caption durations.
                    # `timeline` is this build's own container, so a pin
                    # scoped to another reel's placement does not move
                    # this reel's hash (2026-09-12: Reel 13's closer pins
                    # reached Reel 23's shared-CTA cards).
                    entries = _caption_timing.retime_entries(
                        entries, spine, _caption_pins, 24000 / 1001,
                        timeline=name)[0]
                if entries:
                    from library.tools.plan_provenance import caption_content_hash
                    caption_hashes[name] = caption_content_hash(entries)
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
                # The spine the ask above was written against - the
                # TRIMMED ranges, never a recompute from the moment.
                spine = ask_paths["motion_spine"]
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

            # ── PHASE LOG: the answers arrived ─────────────────────
            # Logged HERE, at the moment the answers are read, with each
            # channel's basis - never inferred from file times later.
            # The semantic and span waits (unanswered asks) were logged
            # by the waiter inside `build_for_reel`/`resolve_span_record`;
            # the motion one is logged here, where its record is in hand.
            from library.tools import reel_phase_log as _phase_log
            try:
                if reel_look_decl is not None and motion_records:
                    _motion_basis = motion_records[-1].get("basis")
                    if _motion_basis == _look.MOTION_AWAITING_ANSWER:
                        _phase_log.log_wait(
                            project_folder, moment.number, name,
                            f"no model answer on file "
                            f"({_look.motion_request_stem(moment.number)}"
                            f".json) - every shot plays still")
                else:
                    _motion_basis = "not_declared"
                _answers_event = _phase_log.log_event(
                    project_folder, moment.number, name,
                    _phase_log.ANSWERS_ARRIVED,
                    detail=(f"semantic={semantic_record.get('basis')} "
                            f"span={span_record.get('basis')} "
                            f"motion={_motion_basis}"))
            except Exception:
                _answers_event = {}
                _motion_basis = "not_declared"

            # ── THE DECISION, taken with everything derived and
            # nothing placed ─────────────────────────────────────────
            # This is the last moment before Resolve is touched for
            # this reel, and the first at which the derivation is
            # complete - so it is where the two digests are compared.
            # The reason is printed either way: a skip whose grounds
            # are invisible is indistinguishable from a reel silently
            # dropped, and a REBUILD that says which half disagreed is
            # how an operator sees what their edit reached.
            _derivation = _need.derivation_digest(
                reel_number=moment.number,
                engine_code=_engine_code,
                project_wide=_project_wide,
                plan_content_hash=_plan_digest,
                transcript_hash=transcript_digest,
                master_digest=_master_digest,
                ranges=ranges,
                placements_list=placements(
                    ranges, master_clips, 24000 / 1001,
                    lead_frames=lead_frames(cards, 24000 / 1001)),
                cards=cards,
                caption_segments=subtitle_segments,
                explainer_segments=explainer_segments,
                semantic_segments=semantic_segments,
                overlay_placements=(overlay_plan.placements
                                    if overlay_plan else None),
                motion_record=(motion_records[-1]
                               if reel_look_decl is not None
                               and motion_records else None),
                ending=_ending_decl,
                look=reel_look_decl,
                grade_cdl=reel_grade_cdl,
                grade_look=reel_grade_look,
                power_grade=reel_power_grade,
                # The card row travels only where cards do
                # (`derivation_digest` keeps card-less digests
                # byte-identical): a reel whose closing card moves rows
                # reads as changed, which is what a stale row IS.
                card_row_role=card_row_role,
                # The speaker lower thirds travel in `extra` rather
                # than under `explainer_segments`, and ONLY when there
                # are some.
                #
                # Not folded in with the explainer, because
                # `_segment_rows` names what it digests ("captions,
                # explainer, semantic") and a second kind arriving
                # under one of those names makes the record say
                # something untrue. Not a new named parameter either:
                # that would add a key to EVERY project's digest body
                # and re-place every reel in the fleet once, for
                # projects that declare no speakers at all. Absent
                # means none, so a project without them digests
                # byte-identically to before this existed.
                #
                # `timeline_start` is IN, unlike the explainer's rows.
                # The file is content-keyed, so `segment_id` alone
                # cannot see a graphic that moved without changing -
                # which is exactly what a first appearance shifting
                # does.
                extra={"extra_cuts": [list(c) for c in moment_cuts],
                       "insisted": [list(sp) for sp in moment_insisted],
                       "overlay_intent": bool(overlay_intent),
                       "skip_captions": bool(skip_captions),
                       **({"lower_thirds": _lower_third_rows(
                           lower_third_segments)}
                          if lower_third_segments else {})},
            )
            build_signatures[name] = _need.signature_for_record(
                _derivation)
            # ── THE HOLD, per reel ──
            # The carried self-read, the decision, the placement and
            # the Fusion comp pass hold the instance EXCLUSIVELY, and
            # nothing else in this loop does. Everything above -
            # ranges, cards, caption renders, model answers, digests -
            # derived with no hold, so lanes overlap there and meet
            # only here, for the measured seconds-to-a-minute a reel's
            # Resolve pass costs. The Fusion subprocess inherits this
            # hold, because comps act on the current timeline's items
            # and must not run while another lane moves the cursor.
            # A `continue` below exits the hold; the loop's failure
            # path discards under its own hold.
            with resolve_lease(f"place {name}", exclusive=True):
                _decision = _need.decide(
                    final, _derivation,
                    _carried_self_read(final),
                    _recorded_signatures.get(final))
                rebuild_decisions.append(_decision.as_dict())
                # Derivation facts for the summary, stashed while every
                # record is in hand: answers per channel with their drops,
                # the captain's trims and keep-exclusions, the decision.
                # Placement facts join below; verify/promote facts at
                # filing. A reel the decision leaves alone files from
                # this stash alone - nothing placed means nothing more
                # to say.
                summary_facts[name].update({
                    "decision": _decision.as_dict(),
                    "answers": {
                        "semantic": (semantic_record.get("basis")
                                     if isinstance(semantic_record, dict)
                                     else None),
                        "span": (span_record.get("basis")
                                 if isinstance(span_record, dict) else None),
                        "motion": _motion_basis,
                    },
                    "semantic_record": semantic_record,
                    "span_record": span_record,
                    "motion_record": (
                        motion_records[-1]
                        if reel_look_decl is not None and motion_records
                        else None),
                    "trims": {"applied": list(
                                    _trim_records.get("applied") or []),
                                "held": list(
                                    _trim_records.get("held") or [])},
                    "keep_exclusions": [
                        {"id": str(cut_id), "start": cut_start,
                         "end": cut_end}
                        for cut_start, cut_end, cut_id in moment_cuts],
                })
                if reuse_unchanged and _decision.leave_alone:
                    # NOT placed, and nothing about it touched: no
                    # staging exists, so this hold covered only the
                    # decision read above. Its signature record is
                    # left exactly as it was, and every per-reel
                    # sidecar keeps its entry because every writer
                    # here merges per reel. The approved timeline is
                    # the one that was already there.
                    print(f"  LEAVING {final} ALONE: {_decision.reason}",
                          flush=True)
                    try:
                        from library.tools import reel_phase_log as _alone_log
                        _alone_log.log_wait(
                            project_folder, moment.number, name,
                            f"leaving alone: {_decision.reason}")
                    except Exception:
                        pass
                    _file_reel_summary(
                        project_folder,
                        number=summary_facts[name]["number"], name=name,
                        facts=summary_facts[name],
                        outcome="left_alone")
                    _holds.release_holds(project_folder, [name])
                    build_signatures.pop(name, None)
                    caption_hashes.pop(name, None)
                    footage_binding_hashes.pop(name, None)
                    overlay_records.pop(name, None)
                    del explainer_plans[_mark[0]:]
                    del semantic_records[_mark[1]:]
                    del span_records[_mark[2]:]
                    del motion_records[_mark[3]:]
                    del lower_third_plans[_mark[4]:]
                    left_alone.append(final)
                    current_staging = None
                    continue
                print(f"  placing {name}: {_decision.reason}", flush=True)
                # The build starts HERE: everything between the answers
                # and this line was derivation plus the seconds-long
                # decision read above, so the seconds since
                # `answers_arrived` name exactly what an M05-class stall
                # costs - and "no engine wait recorded between" says the
                # stall sat upstream of the engine (worker loop, model
                # turns, another lane's Resolve lease), not inside it.
                try:
                    _waited = _phase_log.seconds_since(_answers_event)
                    _phase_log.log_event(
                        project_folder, moment.number, name,
                        _phase_log.BUILD_STARTED,
                        detail=(f"placing {name}: {_decision.reason}"
                                + (f"; {_waited}s since answers arrived "
                                   f"(derivation only, no engine wait "
                                   f"recorded between)"
                                   if _waited is not None else "")))
                except Exception:
                    pass
                build_result = build_reel_timeline(
                    project=project,
                    moment=moment,
                    master_clips=master_clips,
                    subtitle_segments=subtitle_segments,
                    fps=24000/1001,
                    width=reel_width,
                    height=reel_height,
                    project_folder=project_folder,
                    transcript=transcript,
                    timeline_name=name,
                    cards=cards,
                    overlay_placements=(overlay_plan.placements
                                        if overlay_plan else None),
                    explainer_segments=explainer_segments,
                    semantic_segments=semantic_segments,
                    lower_third_segments=lower_third_segments,
                    look=reel_look_decl,
                    motion=reel_motion,
                    # The live master is how the program stream resolves
                    # on projects whose catalog predates stream recording.
                    master_timeline=timeline,
                    extra_cuts=moment_cuts,
                    # The look's CDL half, applied inside the build right
                    # after placement - the Fusion pass below runs after
                    # the build returns, which is the still recipe's
                    # CDL-first order held structurally.
                    grade_cdl=reel_grade_cdl,
                    # The captain's pinned overlay positions ({} when they
                    # declared none): declared wins over computed, so a
                    # rebuild keeps their corrections.
                    overlay_intent=overlay_intent,
                    power_grade=reel_power_grade,
                    draw_gain=run_gain,
                    # The ranges the captions, overlays and explainers above
                    # were planned from - already trimmed of the captain's
                    # span_retime pins. Recomputing from the moment would
                    # un-trim them.
                    ranges=ranges,
                    # WHERE THIS REEL ENDS, and what draws over its tail -
                    # including a declared freeze, which the build renders
                    # and places as the ending shot's held last frame.
                    ending=_ending_decl,
                    # The declared card row: which NAMED row the closing
                    # card lands on. None where nothing declares one; the
                    # build refuses a card-carrying reel then rather than
                    # guessing V1.
                    card_row_role=card_row_role,
                    # The graphics the captain deleted ([] when they
                    # declared none): a rebuild holds the deletion without
                    # being told again.
                    do_not_draw=suppression_rules,
                )
                suppressed_here = list(
                    build_result.get("suppressed_overlays") or [])
                if suppressed_here:
                    # What THIS build held back, recorded onto the plan the
                    # gate grades: `do_not_draw` suppresses the PLACEMENT,
                    # never the plan, so without this F22 reads a recorded
                    # segment with no placed item as a defect. The exemption
                    # fires only on these ids - a rule that matched nothing
                    # stays `unmatched_do_not_draw`, reported below.
                    semantic_record["suppressed"] = [
                        {"segment_id": sid} for sid in suppressed_here]
                    print(f"  {name}: held back {len(suppressed_here)} "
                          f"segment(s) on the captain's deletion: "
                          f"{', '.join(suppressed_here)}", file=sys.stderr)
                # The plan each staging was placed from, keyed by staging
                # name - so the conformance proof grades what was built,
                # never a re-derivation, and promotion renames it with the
                # timeline it describes.
                track_plans[name] = build_result["track_plan"]
                if overlay_intent:
                    # Per-reel intent application, keyed by STAGING name
                    # here and remapped to finals beside `caption_hashes`
                    # after promotion: the durable report below must speak
                    # the timeline names Resolve holds, not the staging the
                    # gate graded.
                    intent_applied_by_reel[name] = list(
                        build_result.get("applied_overlay_intent") or [])
                    intent_unmatched_by_reel[name] = list(
                        build_result.get("unmatched_overlay_intent") or [])
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
                # planned. The subprocess inherits THIS hold, so no other
                # lane moves the cursor between the placement and the
                # comp pass that reads it back.
                if reel_look_decl is not None:
                    from library.tools import reel_look as _look
                    # The plan this reel was placed from, so the manifest's
                    # clips ride the same per-angle rows the picture sits on:
                    # a drift planned for a V2 shot must travel on V2, and
                    # the rows come from the layout owner rather than a
                    # hardcoded V1 beside it.
                    manifest = _look.fusion_manifest(
                        # The TRIMMED ranges, for the reason the motion spine
                        # above states: a recompute from the moment un-trims
                        # the captain's pins. Plus the declared FREEZE, which
                        # is a picture clip on the ending shot's row and the
                        # last one there - so the tail element is armed on
                        # the held frames rather than on the live tail.
                        _with_freeze(
                            placements(
                                ranges, master_clips,
                                24000/1001,
                                lead_frames=lead_frames(cards, 24000/1001)),
                            build_result.get("freeze"), 24000/1001),
                        reel_look_decl, reel_motion, 24000/1001,
                        track_plan=build_result["track_plan"],
                        angle_key=_angle_key,
                        grade_look=reel_grade_look,
                        # The reel's ending owns the tail element, declared
                        # or inherited from its call to action. Resolved
                        # with the SAME moment and transcript the ranges
                        # seam used: two answers to "where does this reel
                        # end" would arm the element on a clip the build
                        # did not freeze.
                        ending=_reel_ending.resolve_ending(
                            project_folder, name, moment, transcript))
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
                try:
                    _phase_log.log_event(
                        project_folder, moment.number, name,
                        _phase_log.BUILD_FINISHED,
                        detail=f"placed {name} (picture, captions, comps)")
                except Exception:
                    pass
                # Placement facts for the summary, while the build record
                # is in hand: caption segments planned against caption
                # items actually linked, cards placed, overlays held back
                # or swept, the freeze tail, transition elements.
                try:
                    _planned_entries = entries
                except NameError:
                    _planned_entries = None
                _caption_links = build_result.get("caption_links")
                _link_warnings = build_result.get("link_warnings")
                _sweep = build_result.get("overlay_sweep")
                _transitions = build_result.get("transition_placements")
                summary_facts[name].update({
                    "captions": {
                        "planned": (len(_planned_entries)
                                    if isinstance(_planned_entries,
                                                  (list, tuple)) else None),
                        "linked": (len(_caption_links)
                                   if isinstance(_caption_links,
                                                 (list, tuple)) else None),
                        "link_warnings": (len(_link_warnings)
                                          if isinstance(_link_warnings,
                                                        (list, tuple))
                                          else None),
                    },
                    "cards": list(cards or ()),
                    "suppressed_overlays": list(suppressed_here),
                    "overlay_sweep": (_sweep if isinstance(_sweep, dict)
                                      else None),
                    "transition_placements": (
                        len(_transitions)
                        if isinstance(_transitions, (list, tuple)) else None),
                    "has_freeze_tail": (
                        build_result.get("freeze_tail") is not None),
                })
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
    except Exception as exc:
        placed = list(dict.fromkeys(
            built_reel_names + ([current_staging] if current_staging else [])))
        try:
            from library.tools import reel_phase_log as _fail_log
            if current_staging:
                try:
                    _failed_number = int(moment.number)
                except Exception:
                    _failed_number = 0
                _fail_log.log_wait(
                    project_folder, _failed_number, current_staging,
                    f"build failed: {exc}")
        except Exception:
            pass
        # Discarding deletes staging containers, so under an EXCLUSIVE
        # hold like every other write path - the loop above released
        # its per-reel holds as it went.
        with resolve_lease("discard failed staging", exclusive=True):
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

    # What each reel's speaker lower thirds really were, INCLUDING the
    # reels with none, and merged per reel for the reason above.
    from library.tools.speaker_identity import write_plans as _write_lower
    _write_lower(project_folder, lower_third_plans)

    # What each reel's semantic visuals really were, INCLUDING the reels
    # with none. MERGED per reel, for the same reason `write_provenance`
    # merges: a partial (`only`) build must not delete the record of the
    # reels it did not touch, or F22 would grade those timelines against
    # an absence. See `library/tools/reel_semantic_visual.py`.
    from library.tools.reel_semantic_visual import (
        write_records as _write_semantic_records)
    _write_semantic_records(project_folder, semantic_records)

    # What each reel's span picture plan resolved to, INCLUDING the
    # reels with none. MERGED per reel, for the same reason as the V6
    # records above: a partial (`only`) build must not delete the
    # record of the reels it did not touch, or F23 would grade those
    # timelines against an absence.
    from library.tools.reel_semantic_visual import (
        write_span_records as _write_span_records)
    _write_span_records(project_folder, span_records)

    # The model answers this build still owes, counted across reels.
    # The per-reel stderr lines above are SAID, not REPORTED - nothing
    # answered "this build owes N model answers", and a build that
    # owes one must not read as finished without naming it
    # (`library/tools/awaiting_model_answers.py`, AGENTS.md 10.4).
    # Read from the records just written (plus this build's motion
    # list, which lives on the record below rather than a file), so a
    # partial build still counts the reels it did not touch. Printed
    # and carried on the record; never a gate - a build the captain
    # wants to look at is still worth building.
    from library.tools import awaiting_model_answers as _awaiting
    awaiting_report = _awaiting.collect(
        project_folder, motion_records=motion_records)
    for _line in _awaiting.summary_lines(awaiting_report):
        print(f"  {_line}", file=sys.stderr)

    # Record which plan we built from, so the verifier can detect
    # if the plan changes before verification runs.
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    from library.tools.plan_provenance import write_provenance
    # The bytes behind every declared asset, hashed NOW: a file
    # replaced on disk afterwards changes every reel playing it with
    # no build, and only a build-time digest makes the next survey
    # read it as changed rather than current
    # (`library/tools/reel_divergence.py`). Read-only and never a
    # refusal - a missing file is recorded as missing (None) and the
    # plan-time readers stay the ones that refuse.
    try:
        from library.tools.reel_divergence import asset_digests as _digests
        asset_hashes = _digests(
            project_folder, brand_effect=_brand_effect(project_folder))
    except Exception as _assets_failed:  # noqa: BLE001
        print(f"  declared-asset digests unavailable "
              f"({_assets_failed}) - recording without them",
              file=sys.stderr)
        asset_hashes = None
    # MERGES into any existing record: a partial rebuild must not
    # delete the provenance of the reels it did not touch.
    write_provenance(review_dir, proposal_path, built_reel_names,
                     caption_hashes=caption_hashes,
                     footage_binding_hashes=footage_binding_hashes,
                     asset_hashes=asset_hashes,
                     build_signatures=build_signatures)

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

    # A reel the decision LEFT ALONE was never staged, so it is not in
    # the mapping the gate grades or the promotion moves. Dropped here
    # rather than never entered, because the derivation that decides it
    # runs inside the loop and the mapping is what the loop iterates.
    for _final in left_alone:
        staged_to_final.pop(_final, None)
    if left_alone:
        print(f"  {len(left_alone)} reel(s) needed no Resolve pass: "
              + ", ".join(left_alone), flush=True)

    organised = None
    staged_out = dict(staged_to_final)
    # The replace guard's declaration, normalised against the finals
    # THIS call stages - so the `verify_reels` node promotes with the
    # same declaration the build was given rather than re-deriving one,
    # and so the record says which rows the operator knowingly let go.
    from library.tools import reel_replace_guard as _decl_guard
    try:
        declared_drops = _decl_guard.parse_specs(
            allow_drops, list(staged_to_final))
    except ValueError as bad_declaration:
        raise ReelBuildError(
            f"REFUSING to build: {bad_declaration}") from bad_declaration
    # The sign-off declaration travels the same way and for the same
    # reason: the `verify_reels` node promotes with the declaration the
    # build was given, never one it re-derives.
    from library.tools import reel_signoff as _decl_signoff
    try:
        declared_supersede = sorted(_decl_signoff.parse_supersede(supersede))
    except ValueError as bad_declaration:
        raise ReelBuildError(
            f"REFUSING to build: {bad_declaration}") from bad_declaration
    # The retain declaration travels the same way: the reels whose
    # superseded generation the promotion may retire rather than
    # delete (`reel_retirement.parse_retain`). Absent means the
    # default - one timeline per reel, an empty archive.
    from library.tools import reel_retirement as _decl_retire
    try:
        declared_retain = sorted(_decl_retire.parse_retain(retain))
    except ValueError as bad_declaration:
        raise ReelBuildError(
            f"REFUSING to build: {bad_declaration}") from bad_declaration
    # Staging container -> reel number, so the phase log can name which
    # reel each staging line belongs to without parsing timeline names.
    _staged_numbers: dict = {}
    for _m in building:
        try:
            _staged_numbers[
                staging_name(built_name(_m, name_suffix))] = int(_m.number)
        except Exception:
            pass
    # The promotion record for the end-of-build summaries (None where
    # this build promoted nothing: unverified, refused, or left alone).
    _promoted_record: dict | None = None
    if verify and not built_reel_names and left_alone:
        # Nothing was placed, and that IS the answer: every reel this
        # call named already carries what this build would have given
        # it. There is no staging to grade and nothing to promote, and
        # `verify_built_reels` REFUSES an empty scope on purpose - a
        # gate that passes having graded nothing reads as coverage
        # (AGENTS.md 10.4). So the gate is not called with nothing;
        # the run says what it did instead.
        print(f"  nothing staged: all "
              f"{len(left_alone)} reel(s) needed no Resolve pass, so "
              f"there is no staging to grade and nothing to promote. "
              f"The approved timelines are untouched.", flush=True)
    elif verify:
        # Scoped to what THIS call placed - the staging containers, not
        # the approved timelines: verifying the whole project here is
        # what made one reel cost 49 gradings, and grading eighteen
        # untouched timelines against the current plan is how a clean
        # single-reel build failed on findings it never touched
        # (data/vep-rebuild-verify/report.md in the firstmate home, 3.5). `None` is unreachable
        # here - `built_reel_names` is a list, possibly empty - and an
        # empty one is refused inside `verify_built_reels` rather than
        # passing on nothing.
        # The gate grades under a SHARED hold: its reads must see a
        # stable staging, and several such reads run together while
        # no writer runs. Promotion below takes its own exclusive
        # hold once the gate has passed.
        try:
            with resolve_lease("verify built reels", exclusive=False):
                verify_built_reels(
                    project_folder=project_folder,
                    resolve_project_name=resolve_name,
                    master_timeline_name=master_timeline_name,
                    plan_path=proposal_path,
                    transcript_path=os.path.join(project_folder, "pipeline_output/scratch/timeline_transcript/transcript.json"),
                    only_reels=list(built_reel_names),
                )
        except Exception as gate_refused:
            # The gate refused: the staging containers and their
            # baselines go, the approved timelines were never named.
            # Reel 5's F17+F8 is exactly this path - and the reel the
            # captain approved is still in the project afterwards.
            # The pool is filed too, so the refused staging's caption
            # imports do not stay loose where ImportMedia left them.
            try:
                from library.tools import reel_phase_log as _gate_log
                for _staged in built_reel_names:
                    _gate_log.log_wait(
                        project_folder,
                        _staged_numbers.get(_staged, 0), _staged,
                        f"verification refused: {gate_refused}")
            except Exception:
                pass
            # The refused reels' summaries: the gate wrote its report
            # before refusing, so the finding classes land here even
            # though nothing promoted. A refusal whose codes need a
            # log grep is a refusal investigated twice.
            try:
                from library.tools import reel_phase_log as _refused_log
                _refused_rows = _refused_log.conformance_rows(
                    project_folder)
            except Exception:
                _refused_rows = {}
            for _staged in built_reel_names:
                _facts = summary_facts.get(_staged, {})
                try:
                    _refusal = str(gate_refused)
                except Exception:
                    _refusal = "(unrenderable refusal)"
                if len(_refusal) > 2000:
                    _refusal = _refusal[:2000] + "…(truncated)"
                _file_reel_summary(
                    project_folder,
                    number=_facts.get(
                        "number", _staged_numbers.get(_staged, 0)),
                    name=_staged, facts=_facts,
                    outcome="verify_refused",
                    verify=_verify_payload(
                        _refused_rows.get(_staged), passed=False,
                        refusal=_refusal),
                    answers_owed=_owed_layers(
                        awaiting_report, _staged,
                        _facts.get("final")),
                    gain_record=gain_record)
            # The refused staging goes under an EXCLUSIVE hold - a
            # delete is a write even where the cursor never moves.
            with resolve_lease("discard refused staging", exclusive=True):
                discard_staged_reels(project, project_folder, built_reel_names,
                                     master_timeline_name)
            raise
        try:
            from library.tools import reel_phase_log as _verified_log
            for _staged in built_reel_names:
                _verified_log.log_event(
                    project_folder, _staged_numbers.get(_staged, 0),
                    _staged, _verified_log.VERIFIED,
                    detail="conformance gate passed over the staging")
        except Exception:
            pass
        promoted = promote_staged_reels(
            project_folder, resolve_name, master_timeline_name,
            dict(staged_to_final), organise=organise,
            allow_drops=declared_drops,
            supersede=declared_supersede,
            retain=declared_retain)
        organised = promoted["organised"]
        # The end-of-build summaries file from this record (retirement,
        # markers, version control below join it there).
        _promoted_record = promoted
        # From here the record speaks final names: what is in Resolve
        # now is the promoted timelines, and the sidecar files were
        # renamed to match by the promotion.
        final_names = promoted["promoted"]
        try:
            from library.tools import reel_phase_log as _promo_log
            for _final in final_names:
                _promo_log.log_event(
                    project_folder,
                    _staged_numbers.get(staged_to_final.get(_final, ""), 0),
                    _final, _promo_log.CONSOLIDATED,
                    detail=f"promoted {staged_to_final.get(_final, '')} "
                           f"onto {_final}")
        except Exception:
            pass
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
        intent_applied_by_reel = {
            final: intent_applied_by_reel[staged_to_final[final]]
            for final in final_names
            if staged_to_final[final] in intent_applied_by_reel}
        intent_unmatched_by_reel = {
            final: intent_unmatched_by_reel[staged_to_final[final]]
            for final in final_names
            if staged_to_final[final] in intent_unmatched_by_reel}
        built_reel_names = list(final_names)
        staged_out = {}
        # ══════════════════════════════════════════════════════
        # VERSION-CONTROL RECORD (per-project git repo)
        # ══════════════════════════════════════════════════════
        # The 6.01 hook never fired for reels, so no reel build ever
        # committed its baseline (measured 2026-09-11). Snapshot each
        # promoted timeline beside the declaration it was built from
        # and commit, on both promotion paths. Never fails the build.
        _vc = None
        try:
            from library.tools import build_version_control as _bvc
            _vc = _bvc.record_reel_promotion(
                project_folder, resolve_name, list(final_names))
            if _vc.get("committed"):
                print(f"── Version control: committed {_vc['commit']} "
                      f"({len(_vc.get('files', []))} file(s)) ──",
                      flush=True)
            else:
                print(f"  version-control record not committed: "
                      f"{_vc.get('reason', 'unknown')}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  version-control record failed: {exc!r} - "
                  f"the reels are promoted and unaffected", flush=True)
        # The detection half of the conformance sweep, over the
        # PROMOTED project - every reel timeline, not just the ones
        # this call placed. The refusing gate above stays scoped (see
        # its comment); this reports whether the round disturbed a
        # reel it did not touch, and never refuses - a PLAN-MISMATCH
        # on an untouched reel means an older plan, not a defective
        # build (`sweep_all_reels_informational`).
        # The sweep reads every reel timeline, so like the gate it
        # reads under a SHARED hold rather than between placements.
        with resolve_lease("sweep all reels", exclusive=False):
            sweep_all_reels_informational(
                project_folder=project_folder,
                resolve_project_name=resolve_name,
                master_timeline_name=master_timeline_name,
                plan_path=proposal_path,
                transcript_path=os.path.join(
                    project_folder,
                    "pipeline_output/scratch/timeline_transcript/"
                    "transcript.json"))

    # File the layer-vs-source findings OUTSIDE the scan, and keep
    # only counts in the record below. The full rows quote the heard
    # form, so storing them in `pipeline_data.json` (which the check
    # scans) made every build re-find the previous build's rows and
    # doubled the state file per build
    # (`library/tools/layer_coherence.py`). The sidecar lives at the
    # project root, outside every scanned root; the summary carries
    # no quoted text and cannot self-match. Filing never fails the
    # build - the witness is advisory, and a missing sidecar just
    # means the counts below are all there is.
    try:
        from library.tools import layer_coherence as _coherence
        _coherence_path = _coherence.write_coherence_report(
            project_folder, coherence_report)
        coherence_summary = _coherence.summarize_coherence(
            coherence_report)
        # The filename only, never the absolute path: the summary
        # lives in `pipeline_data.json` (which the scan reads), and a
        # project folder named for the heard form must not become a
        # match. The file sits beside the state file that names it.
        coherence_summary["report_path"] = _coherence.\
            COHERENCE_REPORT_FILENAME
    except Exception as coherence_file_failed:  # noqa: BLE001
        import sys as _sys
        print(f"  layer-coherence filing unavailable "
              f"({coherence_file_failed}) - counts only",
              file=_sys.stderr)
        coherence_summary = {"status": "unfiled", "owned_total": 0,
                             "wording": 0, "pins": 0, "assets": 0,
                             "informational": 0}

    # ── TRANSFORM DRIFT CHECK (end of build) ──
    # The closing half of the baseline above: start and end together
    # bracket whatever moves a built timeline's transforms to this
    # build. Same discipline - reports, never refuses, never fails
    # the build it instruments - and the same SHARED hold, for the
    # same cursor-excursion reason.
    _drift_end_report = None
    try:
        from library.tools import drift_check as _drift_end
        with resolve_lease("build reels drift end", exclusive=False):
            _drift_end_report = _drift_end.check_project(
                project_folder, when="build end",
                resolve=resolve, project=project)
    except Exception as exc:  # noqa: BLE001
        print(f"  drift end-check unavailable ({exc!r}) - the build "
              f"record stands without an end bracket", flush=True)

    # ── PER-REEL BUILD SUMMARIES ──
    # One structured line per reel this build placed, filed here -
    # after the drift end-bracket - so the summary carries the whole
    # story: derivation, placement, gate, promotion, version control
    # and drift. Left-alone and skipped reels filed in the loop (their
    # story was complete there); a refused gate filed in its except.
    # Each filing is individually never-fail: a summary that cannot
    # land is a gap in the log, never a failed build.
    try:
        from library.tools import reel_phase_log as _summary_log
        _summary_rows = _summary_log.conformance_rows(project_folder)
    except Exception:
        _summary_rows = {}
    _drift_reels = ((_drift_end_report or {}).get("reels")
                    if isinstance(_drift_end_report, dict) else None)
    if _promoted_record is not None:
        _retired = (_promoted_record.get("retirement") or {})
        _archived = (_retired.get("archived") or {})
        _carried = (_promoted_record.get("markers") or {})
        for _final in _promoted_record.get("promoted") or ():
            _staging = staged_to_final.get(_final, "")
            _facts = summary_facts.get(_staging, {})
            _file_reel_summary(
                project_folder,
                number=_facts.get(
                    "number",
                    _staged_numbers.get(_staging, 0)),
                name=_final, facts={**_facts, "final": _final},
                outcome="promoted",
                verify=_verify_payload(
                    _summary_rows.get(_staging), passed=True),
                retired_to=_archived.get(_final),
                markers=(_carried.get(_final)
                         if isinstance(_carried.get(_final), dict)
                         else None),
                version_control=_vc,
                drift_end=(_drift_reels.get(_final)
                           if isinstance(_drift_reels, dict) else None),
                answers_owed=_owed_layers(
                    awaiting_report, _final, _staging),
                gain_record=gain_record)
    elif not verify and built_reel_names:
        # Placed but neither graded nor promoted: the staging
        # containers are what Resolve holds. No conformance slice -
        # the report on disk belongs to an earlier gate, and reading
        # it here would misattribute another run's verdict.
        for _staged in built_reel_names:
            _facts = summary_facts.get(_staged, {})
            _file_reel_summary(
                project_folder,
                number=_facts.get(
                    "number", _staged_numbers.get(_staged, 0)),
                name=_staged, facts=_facts,
                outcome="placed_unverified",
                answers_owed=_owed_layers(awaiting_report, _staged),
                gain_record=gain_record)

    # ── OVERLAY INTENT: which declared pins this build honoured ──
    # Each reel computed its own `unmatched_overlay_intent`, and the
    # mainline return carried NONE of it - a pin killed by a re-render
    # was visible only on stderr of a run nobody re-reads, which is
    # how this project's pins died unnoticed. The aggregate below
    # lands on the returned record (which the `build_reels` node
    # writes to its step output), beside the per-reel breakdown, with
    # the count of pins that DID apply: "N of M applied" is the
    # sentence that would have made the defect visible months ago.
    # REPORTED, never a gate: a build must not start failing because
    # a pin went stale. `unmatched` here means "matched on no reel
    # THIS build placed" - pins for reels left alone report here
    # until those reels rebuild, which is ordinary, not stale.
    if overlay_intent:
        from library.tools.overlay_intent import CAPTION_KIND as _CAPTION_K
        _declared_pins = sorted(
            key for key in overlay_intent if key != _CAPTION_K)
        _applied_union = sorted({
            key for keys in intent_applied_by_reel.values()
            for key in keys if key in overlay_intent})
        _unmatched_union = sorted(
            key for key in _declared_pins if key not in _applied_union)
        _per_reel_intent = {
            final: {"applied": sorted(intent_applied_by_reel.get(final, [])),
                    "unmatched": sorted(
                        intent_unmatched_by_reel.get(final, []))}
            for final in built_reel_names}
        overlay_intent_report = {
            "placed_reels": list(built_reel_names),
            "left_alone": list(left_alone),
            "declared": _declared_pins,
            "applied": _applied_union,
            "unmatched": _unmatched_union,
            "per_reel": _per_reel_intent,
            "kind_defaults": sorted(
                key for key in overlay_intent if key == _CAPTION_K),
        }
        print(f"  overlay intent: {len(_applied_union)} of "
              f"{len(_declared_pins)} pin(s) applied on "
              f"{len(built_reel_names)} placed reel(s)"
              + (f" - unmatched on placed reels: "
                 f"{', '.join(_unmatched_union)}"
                 if _unmatched_union else "")
              + (f" ({len(left_alone)} reel(s) left alone: pins for "
                 f"those reels report unmatched until they rebuild)"
                 if left_alone else ""),
              file=sys.stderr, flush=True)
    else:
        overlay_intent_report = {
            "placed_reels": list(built_reel_names),
            "left_alone": list(left_alone),
            "declared": [],
            "applied": [],
            "unmatched": [],
            "per_reel": {},
            "kind_defaults": [],
        }

    return {
        "timelines_built": built_reel_names,
        # Reels this call deliberately did NOT build: a recorded strike
        # covers the whole body, so the reel is dropped WITH the reason
        # (the build-time shape of the no-split rule) and whatever the
        # captain approved stays exactly as it is. A reader that wants
        # to know what was held back reads this, never silence.
        "skipped_by_exclusion": skipped_by_exclusion,
        # Reels this call deliberately did NOT build because a final
        # range touches no declared window of its own
        # (`reel_ledger.OutOfWindowRange`): the per-reel shape of a
        # refusal, beside `skipped_by_exclusion` above. Whatever the
        # captain approved stays exactly as it is, WITH the reason.
        "skipped_out_of_window": skipped_out_of_window,
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
        # What the renderer drew per Pan/Tilt unit when this build
        # placed: the build-time probe's record (`draw_gain_probe`),
        # source "measured" or "fallback", with the probe detail.
        # `disagrees_with_fallback` is the loud disagreement the
        # calibration exists to surface: the renderer state moved
        # again, and every placement below was computed with the
        # measured value, not the fallback.
        "draw_gain_calibration": gain_record,
        # The replace guard's declaration, normalised to per-final row
        # keys (issue #925). `{}` when the caller declared nothing -
        # which is the common case, and is not the same as allowing.
        # The `verify_reels` node promotes off this rather than
        # re-deriving a declaration, so whatever the operator allowed
        # is what the promotion honours.
        "allow_drops": {final: sorted(rows)
                        for final, rows in declared_drops.items()
                        if rows},
        # The reels whose captain sign-off this build was told it may
        # replace (`reel_signoff`). `[]` when none was declared, which
        # is the common case and is NOT the same as allowing: an
        # undeclared sign-off refuses the promotion by name.
        "supersede": declared_supersede,
        # The reels whose superseded generation the promotion may
        # retire rather than delete (`reel_retirement`). `[]` is the
        # default - one timeline per reel, an empty archive - and is
        # NOT the same as retaining: only a named reel keeps its
        # previous generation, plus any reel carrying a sign-off.
        "retain": declared_retain,
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
        # The model answers this build still owes, counted across
        # reels from the records above (`awaiting_model_answers`).
        # Carried so the run summary and the dashboard can name the
        # incomplete reels without re-reading the record files.
        "awaiting_model_answers": awaiting_report,
        # What verification this build owes, computed pre-build from
        # real state (`library/tools/proof_scope.py`) - FULL for a new
        # mechanism, stills-for-changed-regions for a re-run. Guidance
        # the build printed, never a gate.
        "proof_scope": proof_scope_record,
        # Which APPROVED reels carry what the project declares - every
        # one of them, not just the reels this call placed. The reels
        # left out of a build are the ones that diverge, and a build
        # that says nothing about them is how "every reel inherits it"
        # came to be said of an engine and believed of a project
        # (`library/tools/reel_divergence.py`). Reported, never a gate.
        "divergence": divergence_report,
        # What the layer-vs-source check said before anything was
        # placed (`library/tools/layer_coherence.py`): counts only,
        # never the full rows. The full rows quote the heard form, so
        # they live in the sidecar outside the scan
        # (`layer_coherence.coherence_report_path`) and only this
        # summary reaches `pipeline_data.json` - storing rows where
        # the check scans doubled the state file every build.
        # Reported, never a gate, for the same reason as the
        # divergence above.
        "coherence_summary": coherence_summary,
        # Per reel: whether it needed a Resolve pass, and WHY - for
        # every reel this call considered, including the ones it
        # placed (`library/tools/reel_rebuild_need.py`). A reader that
        # wants to know why a reel was not rebuilt reads this rather
        # than inferring it from an absence.
        "rebuild_need": rebuild_decisions,
        # The reels this call deliberately did not place because
        # nothing about them changed. `[]` is a build that placed
        # everything it named, which is also what `reuse_unchanged=
        # False` always produces. NOT the same list as
        # `skipped_by_exclusion`: that one is content the captain
        # struck, this one is work that was already done.
        "reels_left_alone": left_alone,
        # Which declared overlay pins this build honoured, and which
        # matched on no reel it placed - the aggregate above, carried
        # so the step output (not just stderr) says whether a pin
        # applied. Reported, never a gate.
        "overlay_intent_report": overlay_intent_report,
    }



def verify_cover_clip(source_file: str, source_in: float, source_out: float,
                      master_clips: Sequence, transcript: dict,
                      fps: float, require_face: bool = True):
    """Check an unplaced source span as a cutaway cover, and place it.

    A reaction cutaway needs picture the master never carried - the
    listening angle the edit cut away from before the seam. The reel
    builder places only from master clips, so that span arrives here
    as an external input (AGENTS.md 3): CHECKED, never asserted. The
    sync is derived, not taken on trust - slope-1 continuation from
    the nearest placed clip of the same file on a picture row, and a
    cover with no same-file placed neighbour has no sync basis and is
    refused. Every other claim is checked too: the file exists, the
    span is inside it and disjoint from what is already placed (no
    double-placed frames), the transcript has the cover angle silent
    across the derived master span (a cutaway to someone mid-sentence
    is not a reaction), and the span itself is a locked static shot
    (a camera that moved there is not the listening framing the reel
    cut away from).

    Returns a `TimelineClip` on the neighbour's row, carrying the
    neighbour's framing - the same camera keeps the same crop - with
    the derived master span. Anything unchecked raises
    `OffsetRefused` naming it.
    """
    import os
    import subprocess

    from library.tools.timeline_ingest import TimelineClip

    if not os.path.isabs(source_file):
        source_file = os.path.abspath(source_file)
    if not os.path.isfile(source_file):
        raise OffsetRefused(
            f"Cutaway cover {source_file}: no such file - a cover "
            f"that is not on disk cannot be checked, so it is refused "
            f"rather than placed.")
    if not (float(source_out) - float(source_in) >= 1.0 / fps):
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)} "
            f"[{source_in}, {source_out}): shorter than one frame - "
            f"nothing to reveal.")
    if float(source_in) < 0:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)} starts "
            f"before the file - nothing to hear or see there.")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=width,height",
         "-of", "default=noprint_wrappers=1", source_file],
        capture_output=True, encoding="utf-8", check=False)
    duration = None
    width = height = None
    for line in (probe.stdout or "").splitlines():
        if line.startswith("duration="):
            try:
                duration = float(line.split("=", 1)[1])
            except ValueError:
                duration = None
        elif line.startswith("width="):
            try:
                width = int(line.split("=", 1)[1])
            except ValueError:
                width = None
        elif line.startswith("height="):
            try:
                height = int(line.split("=", 1)[1])
            except ValueError:
                height = None
    if duration is None:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)}: ffprobe "
            f"reports no duration - bounds cannot be checked, so the "
            f"cover is refused rather than placed unchecked.")
    if float(source_out) > duration:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)} "
            f"[{source_in}, {source_out}) overruns the file "
            f"({duration:.2f}s) - refused rather than placed past "
            f"the end.")
    same_file = [
        c for c in master_clips
        if getattr(c, "track_type", "") == "video"
        and os.path.abspath(getattr(c, "source_file", "")) == source_file]
    if not same_file:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)}: no "
            f"placed clip plays that file on any picture row, so "
            f"there is no sync basis - the cover is refused rather "
            f"than placed on asserted timing.")
    for placed in same_file:
        if (float(source_in) < float(placed.source_out)
                and float(source_out) > float(placed.source_in)):
            raise OffsetRefused(
                f"Cutaway cover {os.path.basename(source_file)} "
                f"[{source_in}, {source_out}) overlaps the placed "
                f"span [{placed.source_in}, {placed.source_out}) - "
                f"those frames already play, so the cover is refused "
                f"rather than double-placed.")
    neighbour = min(
        same_file,
        key=lambda c: min(abs(float(source_in) - float(c.source_out)),
                          abs(float(c.source_in) - float(source_out))))
    span = float(source_out) - float(source_in)
    if float(neighbour.source_out) <= float(source_in):
        master_start = (float(neighbour.timeline_end)
                        + (float(source_in) - float(neighbour.source_out)))
    else:
        master_start = (float(neighbour.timeline_start)
                        - (float(neighbour.source_in) - float(source_out)))
    master_end = master_start + span
    speaker = getattr(neighbour, "speaker", "?") or "?"
    for segment in (transcript.get("segments") or []):
        if str(segment.get("speaker", "")) != str(speaker):
            continue
        for word in (segment.get("words") or []):
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                # A timed word with no readable span cannot be shown to
                # sit outside the cover: placing it claims "no speech
                # here" on the evidence of a parse failure. Refused like
                # the unreadable picture size below it.
                raise OffsetRefused(
                    f"Cutaway cover {os.path.basename(source_file)} "
                    f"over master {master_start:.2f}-{master_end:.2f}s: "
                    f"{speaker} has a timed word "
                    f"{(word.get('word', '?') if isinstance(word, dict) else '?')!r} "
                    f"with no readable span - "
                    f"speech inside the cover cannot be ruled out, so "
                    f"the cover is refused rather than placed over "
                    f"possible speech.")
            if start < master_end and end > master_start:
                raise OffsetRefused(
                    f"Cutaway cover {os.path.basename(source_file)} "
                    f"over master {master_start:.2f}-{master_end:.2f}s: "
                    f"{speaker} says {word.get('word', '?')!r} "
                    f"({start:.2f}-{end:.2f}s) inside it - a cutaway "
                    f"to someone mid-sentence is not a reaction. "
                    f"Pick a span where they are listening.")
    if width is None or height is None:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)}: ffprobe "
            f"reports no picture size - the static check cannot run, "
            f"so the cover is refused rather than placed unchecked.")
    frames = _cover_frames_static(
        source_file, float(source_in), float(source_out),
        neighbour=float(neighbour.source_out),
        check_face=require_face)
    if frames["mean_luma"] < 5.0:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)} "
            f"[{source_in}, {source_out}): mean luma "
            f"{frames['mean_luma']:.1f} - effectively black, not a "
            f"listening shot.")
    if frames["rim_diff"] > 3.0:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)} "
            f"[{source_in}, {source_out}): frame-rim difference "
            f"{frames['rim_diff']:.1f}/255 - the camera moved there, "
            f"so this is not the locked framing the reel cut away "
            f"from. (Subject motion {frames['center_diff']:.1f} is "
            f"reported, never refused: a listener moves.)")
    if require_face and frames["face_shift"] is None:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)} "
            f"[{source_in}, {source_out}): {frames['face_note']} - "
            f"the cover must carry the same face in the same "
            f"framing, checked rather than assumed.")
    if require_face and frames["face_shift"] > 0.20:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)} "
            f"[{source_in}, {source_out}): the face sits "
            f"{frames['face_shift'] * 100:.0f}% of frame width from "
            f"where the neighbouring placed shot holds it - a "
            f"reframed or different shot, not the listening "
            f"framing the reel cut away from.")
    neighbour_frames = getattr(neighbour, "source_frames", None)
    return TimelineClip(
        resolve_item_id=(
            f"external-cover:{os.path.basename(source_file)}:"
            f"{float(source_in):.3f}-{float(source_out):.3f}"),
        track_type="video",
        track_index=int(getattr(neighbour, "track_index", 1)),
        track_name=str(getattr(neighbour, "track_name", speaker)),
        speaker=str(speaker),
        source_file=source_file,
        source_in=float(source_in),
        source_out=float(source_out),
        source_in_frame=int(round(float(source_in) * fps)),
        source_out_frame=int(round(float(source_out) * fps)),
        source_frames=(int(neighbour_frames)
                       if neighbour_frames is not None
                       else int(round(duration * fps))),
        timeline_start=master_start,
        timeline_end=master_end,
        name=os.path.basename(source_file),
        transform=dict(getattr(neighbour, "transform", None) or {}),
    )


def _cover_frames_static(source_file: str, source_in: float,
                         source_out: float, neighbour: float,
                         check_face: bool = True) -> dict:
    """The cover span is the neighbour shot, still listening.

    Three frames (head, middle, tail) at a fixed 960x540, so the
    thresholds below are resolution-independent:

    - `rim_diff`: mean inter-frame difference over the outer 10% rim.
      The rim is background for a centered subject, so it answers
      whether the CAMERA moved. Measured floor on locked podcast
      cameras is ~1.3/255 (compression noise); the refusal line is
      3.0.
    - `center_diff`: the same over the inner frame. A listener moves
      - head, hands - so this is REPORTED, never refused.
    - `face_shift`: the largest Haar face box (frontal, else profile)
      in the middle cover frame versus one in the neighbour's placed
      span, as a fraction of frame width. Same face, same framing -
      the cover continues the shot the reel cut away from. None with
      a note when no face reads on either side; the caller refuses,
      because an unchecked cover is not a check. Skipped (None, no
      note) when `check_face` is false - the test seam, so synthetic
      fixtures without faces can still prove bounds, sync and
      silence; production always checks.

    Needs the Haar cascades (`opencv-python>=4.8,<5`), the same
    interpreter the TV-frame look's punch-in already requires - and
    says so rather than passing blind without them.
    """
    import os
    import subprocess

    import numpy as np

    width, height = 960, 540

    def grab(stamp):
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(stamp, 0.0):.3f}",
             "-i", source_file, "-frames:v", "1", "-f", "rawvideo",
             "-pix_fmt", "gray", "-s", f"{width}x{height}", "-"],
            capture_output=True, check=False)
        if proc.returncode != 0 or len(proc.stdout) < width * height:
            raise OffsetRefused(
                f"Cutaway cover {os.path.basename(source_file)}: "
                f"ffmpeg could not read a frame at {stamp:.2f}s - "
                f"the lock check cannot run, so the cover is refused "
                f"rather than placed unchecked.")
        return np.frombuffer(proc.stdout[:width * height],
                             dtype=np.uint8).astype(float).reshape(
                                 height, width)

    span = float(source_out) - float(source_in)
    frames = [grab(float(source_in)),
              grab(float(source_in) + span / 2.0),
              grab(float(source_out) - span / 4.0)]
    rim = np.zeros((height, width), dtype=bool)
    rim[int(height * 0.1):-int(height * 0.1),
        int(width * 0.1):-int(width * 0.1)] = True
    rim = ~rim
    rim_diffs = [float(np.abs(b[rim] - a[rim]).mean())
                 for a, b in zip(frames, frames[1:])]
    center_diffs = [float(np.abs(b[~rim] - a[~rim]).mean())
                    for a, b in zip(frames, frames[1:])]
    result = {"mean_luma": float(frames[0].mean()),
              "rim_diff": max(rim_diffs) if rim_diffs else 0.0,
              "center_diff": max(center_diffs) if center_diffs else 0.0,
              "face_shift": None,
              "face_note": "face check disabled"}
    if not check_face:
        return result

    try:
        import cv2
    except ImportError:
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)}: no cv2 "
            f"in this interpreter, so no face check - run the build "
            f"under the project's .venv, as the punch-in already "
            f"requires.")
    cascade_dir = getattr(cv2, "data", None) and cv2.data.haarcascades
    front_path = (os.path.join(cascade_dir,
                               "haarcascade_frontalface_alt2.xml")
                  if cascade_dir else "")
    prof_path = (os.path.join(cascade_dir, "haarcascade_profileface.xml")
                 if cascade_dir else "")
    if not (front_path and os.path.isfile(front_path)
            and prof_path and os.path.isfile(prof_path)):
        raise OffsetRefused(
            f"Cutaway cover {os.path.basename(source_file)}: the "
            f"Haar cascades ship absent from this cv2 "
            f"({getattr(cv2, '__version__', '?')}) - no face check, "
            f"so the cover is refused rather than placed unchecked.")

    def face(gray):
        import numpy as _np
        gray_u8 = _np.clip(gray, 0, 255).astype(_np.uint8)
        front = cv2.CascadeClassifier(front_path)
        boxes = front.detectMultiScale(gray_u8, 1.1, 4)
        if len(boxes) == 0:
            prof = cv2.CascadeClassifier(prof_path)
            boxes = prof.detectMultiScale(gray_u8, 1.1, 4)
        if len(boxes) == 0:
            return None
        biggest = max(boxes, key=lambda b: b[2] * b[3])
        return (float(biggest[0] + biggest[2] / 2) / width,
                float(biggest[1] + biggest[3] / 2) / height)

    ref = grab(float(neighbour) - 0.25)
    cover_face, ref_face = face(frames[1]), face(ref)
    if cover_face is None or ref_face is None:
        result["face_note"] = (
            "no face reads on the cover"
            if cover_face is None else
            "no face reads on the neighbouring placed shot")
        return result
    result["face_shift"] = abs(cover_face[0] - ref_face[0])
    result["face_note"] = ""
    return result


@under_lease("build reel variants")
def build_reel_variants(project_slug: str, reel_number: int,
                        variants: Sequence[dict]) -> dict:
    """Build comparison variants of one approved reel beside it.

    One reel, one seam, two treatments: the caller names a suffix and
    an offset spec per variant (`j_cut` / `cutaway` as
    `build_reel_timeline` takes them, plus an optional `cover`
    {"source_file", "source_in", "source_out"} span
    `verify_cover_clip` checks onto the master clock), and each is
    built with the SAME derivation the rebuild runs today - ranges,
    cards, captions (namespaced per variant, so no variant renders
    into the overlays another timeline points at), explainer,
    semantic visuals, transition elements, look and the CURRENT
    motion answer, reused and never re-authored. A variant that
    differs from the approved timeline anywhere but its seam is a
    build defect, not a comparison.

    Deliberate deviations from `rebuild_reels_in_project`, each for
    the reason the rebuild's own discipline gives:

    - Built straight to the FINAL variant name, with a pre-assert
      that no timeline holds it: there is no approved original to
      protect, so there is nothing staging would stage FOR - but
      anything half-placed on failure is deleted, and an occupied
      name refuses rather than overwrites.
    - The structural gate (`timeline_conformance.verify_timeline`,
      the same read-back the offset tests grade) instead of the
      plan-re-deriving verifier: the offsets are INTENTIONAL
      deviations from the plan, so the plan gate would fail them by
      design. A structural failure deletes the variant and raises.
    - No plan provenance merge and no pool organise: variants are
      not plan reels, so the plan's record must not claim them, and
      filing would sort comparison timelines away from what they
      compare against.
    - Fusion comps go in exactly as the rebuild puts them (same
      manifest call, same applier): under the look the frame row
      spans the picture the variant really placed, so the manifest
      is drawn from the OFFSET placements via `apply_offset_specs`,
      never from the straight ones.

    `variants` entries carry an optional `watch` string - what the
    captain should look and listen for - recorded per variant.
    Returns {"reel", "resolve_project_name", "variants": {final:
    {"build_record", "conformance", "watch"}}}.

    The batch is atomic: a variant that fails removes every variant
    this call placed, because a comparison with one version in it is
    not a comparison - same bargain the rebuild strikes with its
    staging.
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
    from library.tools.timeline_conformance import verify_timeline
    from library.tools.timeline_layout import TrackPlan, TrackSpec

    if not variants:
        raise ReelBuildError(
            "build_reel_variants was asked to build zero variants - "
            "a comparison with nothing in it. Name at least one.")
    resolve = scriptapp_preserving_locale(dvr, "Resolve")
    pm = resolve.GetProjectManager()

    if os.path.isabs(project_slug) and os.path.isdir(project_slug):
        project_folder = project_slug
    else:
        proj = get_project(project_slug)
        if not proj:
            raise ValueError(f"Unknown project {project_slug}")
        # NOTE: `project_root`, not `.root` - `ProjectConfig`
        # carries no `.root` (issue #895 fixed the sibling rebuild
        # path that read one), so a slug call of either path fails
        # loudly on anything but this attribute.
        project_folder = str(proj.project_root)

    with open(os.path.join(project_folder, "project.yaml")) as f:
        config = yaml.safe_load(f)
    resolve_config = config.get("resolve", {})
    resolve_name = resolve_config.get("project_name",
                                     os.path.basename(project_slug))
    master_timeline_name = resolve_config.get("timeline_name")
    if not master_timeline_name:
        raise ValueError(
            "Missing 'timeline_name' under 'resolve' in project.yaml")

    project = resolve_project_exactly(pm, resolve_name)

    from library.tools.reel_proposal import proposal_path as _proposal_path
    moments = read_proposal(str(_proposal_path(project_folder)))
    moment = next((m for m in moments if int(m.number) == int(reel_number)),
                  None)
    if moment is None:
        raise ReelBuildError(
            f"reel {reel_number}: the plan names no such moment.")
    if str(getattr(moment.approval, "value", moment.approval)) != "approved":
        raise ReelBuildError(
            f"reel {reel_number}: not approved - variants compare "
            f"treatments of an approved reel, not alternative selections.")

    from library.tools.timeline_transcript import transcript_path
    with open(transcript_path(project_folder)) as f:
        transcript = json.load(f)
    from library.tools.reel_proposal import snap_moment_to_speech
    moment, _moves = snap_moment_to_speech(moment, transcript)

    # The captain's recorded closer pin, on the SAME terms and in the
    # same order as `rebuild_reels_in_project` - snap first, then
    # redraw. A variant exists to be compared against the approved
    # reel, so it must carry every recorded obedience that reel
    # carries: a variant whose closer opens on different words is a
    # comparison of two things at once, and the seam it was built to
    # show is not the difference the captain would see.
    #
    # Measured 2026-09-11 on Reel 09: without this, the reaction-cutaway
    # variant opened its closer 54 frames later than the approved reel
    # (source 483.563s against 481.311s), which is exactly the captain's
    # report - "another timeline which has the 8 frame cutaway to
    # akshita, but does not have the updated cta".
    from library.tools import captain_edits as _edits
    try:
        _pin_edits = _edits.load_edits(project_folder)
    except _edits.CaptainEditError as exc:
        raise ReelBuildError(
            f"captain_edits cannot be read: {exc}. A recorded pin the "
            f"build cannot read must refuse, never build silently past "
            f"it.") from exc
    if any(e.get("kind") == "redraw_closer" for e in _pin_edits):
        _redrawn, _pin_applied, _pin_held, _pin_stale = \
            _edits.apply_closer_redraws([moment], transcript, _pin_edits)
        moment = _redrawn[0]
        for record in _pin_applied:
            print(f"  Reel {record['reel']:02d}: closer "
                  f"{record['was'][0]:.3f}s -> {record['now'][0]:.3f}s "
                  f"(now opens on {record['anchor_phrase']!r} - "
                  f"{record['reason']})", file=sys.stderr)
        for record in _pin_held:
            print(f"  Reel {record['reel']:02d}: closer already opens on "
                  f"{record['anchor_phrase']!r} - pin held",
                  file=sys.stderr)
        _edits.report_stale(_pin_stale)

    from library.tools import transcript_corrections as _tc
    keep_exclusions = _tc.keep_exclusions(project_folder)
    keep_insistences = _tc.keep_insistences(project_folder)

    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == master_timeline_name:
            timeline = t
            break
    if not timeline:
        raise ValueError(
            f"Could not find master timeline {master_timeline_name}")
    master_clips = list(snapshot_timeline(timeline,
                                          project.GetName()).clips)

    existing = set()
    for i in range(1, project.GetTimelineCount() + 1):
        existing.add(project.GetTimelineByIndex(i).GetName())
    finals = [built_name(moment, str(spec.get("suffix", "")))
              for spec in variants]
    for final in finals:
        if final in existing:
            raise ReelBuildError(
                f"variant {final!r} already exists in the project - "
                f"delete it in Resolve and re-run; a variant build "
                f"never overwrites.")
    if moment.timeline_name in finals:
        raise ReelBuildError(
            "a variant suffix that reproduces the approved name "
            "builds OVER the captain's timeline - refused.")

    # A variant that DECLARES is built from its own branch, because a
    # declaration lives in `external/<store>.json` and nowhere else
    # (`timeline_variants.branch_requirement`). Asked before anything
    # is placed: built from the wrong branch it would carry the other
    # version's declaration and differ nowhere, reported as a
    # comparison.
    from library.tools import timeline_variants as _variants
    for spec in variants:
        blocked = _variants.branch_requirement(
            project_folder, int(reel_number), spec)
        if blocked:
            raise ReelBuildError(blocked)

    fps = 24000 / 1001
    moment_cuts = _tc.grow_cuts_over_wordless_leadin(
        _tc.exclusion_cuts_for_span(moment.timeline_start,
                                    moment.timeline_end,
                                    keep_exclusions),
        transcript)
    moment_cuts, _ = _tc.grow_cuts_over_wordless_tail(
        moment_cuts, transcript)
    ranges = reel_ranges(
        moment, transcript, extra_cuts=moment_cuts,
        insisted_spans=_tc.insisted_spans_for_span(
            moment.timeline_start, moment.timeline_end, keep_insistences))

    # The captain's recorded trims, same seam as the rebuild loop:
    # variants compare seams, so every variant is cut from the same
    # trimmed ranges the approved reel was built from. `_pin_edits`
    # is the load above: one read, both pin kinds.
    if any(e.get("kind") == "span_retime" for e in _pin_edits):
        ranges, _vrt_applied, _vrt_held, _vrt_stale = (
            _edits.retime_ranges(
                ranges, placements(ranges, master_clips, fps),
                transcript, _pin_edits, fps=fps))
        for record in _vrt_applied:
            print(f"  Captain edit: span {record['span_index']}'s "
                  f"{record['edge']} trimmed onto "
                  f"{record['anchor_phrase']!r} - "
                  f"{record['reason']}", flush=True)
        for record in _vrt_held:
            print(f"  Captain edit: span {record['span_index']}'s "
                  f"{record['edge']} already sits on "
                  f"{record['anchor_phrase']!r} - pin held",
                  flush=True)
        _edits.report_stale(_vrt_stale)

    from library.tools import transition_overlay as overlay_mod
    overlay_effect = overlay_mod.resolve_declaration({}, project_folder)
    overlay_declared = overlay_mod.declared_overlay(overlay_effect) is not None
    card_declarations = declared_cards(project_folder)
    judgement = _read_judgement(project_folder)
    brand_effect = _brand_effect(project_folder)
    # The declared card row, read ONCE beside the declarations: which
    # named row the closing card lands on is one project-wide answer.
    # None where nothing declares one; the build refuses a
    # card-carrying reel then (`build_reel_timeline`) rather than
    # guessing V1.
    card_row_role = card_row_role_for_project(project_folder, brand_effect)
    # The delivery frame, resolved ONCE for the variant build exactly as
    # `rebuild_reels_in_project` resolves it: a variant differs in a SEAM
    # or a per-project declaration and in nothing else, so it must be
    # built at the frame the reel it varies was built at.
    reel_width, reel_height = reel_resolution(project_folder)
    from library.tools import reel_look as _reel_look
    reel_look_decl = _reel_look.resolve_look(project_folder,
                                             reel_width, reel_height)

    # ── DRAW-GAIN PROBE (start of variant build) ──
    # Same instrument as `rebuild_reels_in_project` above: one
    # calibration per build, placements computed with what it
    # measured, fallback loud. See that block for why the gain is
    # measured rather than declared.
    from library.tools import draw_gain_probe as _variant_gain_probe
    try:
        _variant_gain_record = _variant_gain_probe.calibrate(
            resolve, project, (reel_width, reel_height))
    except Exception as exc:  # noqa: BLE001 - probe never raises
        _variant_gain_record = {
            "gain": FALLBACK_DRAW_GAIN, "source": "fallback",
            "disagrees_with_fallback": False,
            "warnings": [f"probe raised {exc!r} outside itself"],
            "probe": {},
        }
    _variant_gain_probe.log_record(_variant_gain_record)
    run_gain = float(_variant_gain_record.get("gain")
                     or FALLBACK_DRAW_GAIN)

    # The three per-reel DECLARATIONS the rebuild reads, read here on
    # the same terms (AGENTS.md 3, `tests/
    # test_reel_variants_carry_recorded_obedience.py`). Until the
    # variant spec widened past the seam these were simply absent from
    # this path, so every variant was built with no declared ending,
    # no pinned overlay positions and no caption-timing pins - three
    # differences from the approved reel on top of the one it was
    # built to show. They are also the stores a variant may now DIFFER
    # in (`timeline_variants.declarable`): the declaration lives in
    # `external/<store>.json` on the variant's own branch, so reading
    # it here IS how a declaring variant differs, with no second
    # builder anywhere.
    from library.tools.overlay_intent import OverlayIntentError, load_intent
    try:
        variant_overlay_intent = load_intent(project_folder)
    except OverlayIntentError as exc:
        raise ReelBuildError(
            f"overlay intent cannot be honoured: {exc}") from exc
    from library.tools.do_not_draw import (
        DoNotDrawError as _DndError, load_rules as _load_rules)
    try:
        variant_suppressions = _load_rules(project_folder)
    except _DndError as exc:
        raise ReelBuildError(
            f"do_not_draw cannot be honoured: {exc}") from exc
    from library.tools import caption_timing as _caption_timing
    from library.tools import reel_ending as _reel_ending
    _caption_pins = _caption_timing.load_pins(project_folder)
    # The look's CDL half rides along exactly as the rebuild carries
    # it: no live timeline predates it either way (it is newer than
    # Reel 09), so variants and any rebuild from today match.
    reel_grade_cdl = _reel_look.resolve_grade_cdl(project_folder)
    # And the PowerGrade half on the same terms. A variant exists to be
    # compared against the approved reel, so it must carry the SAME
    # grade route: a variant graded by CDL while the reel it is
    # compared against is graded on the Color page is not a comparison
    # of seams, it is a comparison of grades. `resolve_power_grade`
    # raises on a refused declaration here exactly as it does in the
    # rebuild - one build, one bar.
    reel_power_grade = _reel_look.resolve_power_grade(project_folder)

    # WHERE THIS REEL ENDS. Resolved against the REEL's own name, not
    # a variant's: an ending is a property of the reel, and every
    # variant in one call reads one branch, so it is applied ONCE -
    # before the cards, which are planned from the ended ranges the
    # way the rebuild plans them.
    _ending_decl = _reel_ending.resolve_ending(
        project_folder, moment.timeline_name, moment, transcript)
    if _ending_decl is not None:
        ranges, _end_record = _reel_ending.apply_ending(
            ranges, placements(ranges, master_clips, fps),
            transcript, _ending_decl, fps)
        _reel_ending.report(_end_record)

    # Cards are identical for every variant (same moment, same
    # ranges): planned and rendered once, shared by all variants.
    cards = plan_cards(moment, transcript, ranges, project_folder,
                       fps=fps, width=reel_width, height=reel_height,
                       declarations=card_declarations,
                       ending=_ending_decl, look=reel_look_decl)
    if cards:
        from library.tools.full_frame_element import render_reel_cards
        cards = render_reel_cards(cards, str(REMOTION_DIR),
                                 card_render_dir(project_folder))
    lead = lead_frames(cards, fps) / fps
    base_placements = placements(ranges, master_clips, fps,
                                 lead_frames=lead_frames(cards, fps))

    # Phase one is PURE: covers checked, offset plans dry-run, for
    # every variant before anything renders or any timeline exists.
    # A variant that cannot be built refuses here, in seconds and
    # with nothing placed - never after its sibling already built.
    # (Render and Resolve writes are phase two; a failure there
    # still removes the whole batch, per the atomicity above.)
    checked = []
    for spec, final in zip(variants, finals):
        clips = list(master_clips)
        cover_clip = None
        if spec.get("cover") is not None:
            cover = spec["cover"]
            cover_clip = verify_cover_clip(
                cover["source_file"], float(cover["source_in"]),
                float(cover["source_out"]), master_clips,
                transcript, fps)
            clips.append(cover_clip)
        variant_placements = placements(
            ranges, clips, fps, lead_frames=lead_frames(cards, fps))
        if spec.get("j_cut") is not None:
            plan_j_cut(
                variant_placements, fps,
                int(round(float(spec["j_cut"]["join_seconds"]) * fps)),
                int(round(float(spec["j_cut"]["lead_seconds"]) * fps)),
                words=spec["j_cut"].get("words", ()))
        effective = spec
        if spec.get("cutaway") is not None:
            # The window is re-derived from the cover on THIS build
            # (`resolve_cutaway_window_frames`) - a stale recording
            # refuses here, in seconds and with nothing placed, never
            # after its sibling already built. What the rest of the
            # build receives is the re-derived window, so the dry-run
            # above and the timeline below cannot disagree about what
            # the spec means.
            window = resolve_cutaway_window_frames(
                spec["cutaway"], cover_clip, variant_placements, fps)
            if cover_clip is None:
                print(f"  {final}: cutaway window "
                      f"[{window[0]}, {window[1]}) is reel seconds with "
                      f"no cover to check it against - re-verify it "
                      f"after any rebuild that moves the reel's timing "
                      f"(master-anchoring it is follow-up work).",
                      file=sys.stderr)
            plan_cutaway(
                variant_placements, fps,
                str(spec["cutaway"]["hide_angle"]),
                window,
                cover_words=spec["cutaway"].get("cover_words", ()))
            effective = dict(
                spec,
                cutaway=dict(spec["cutaway"],
                             window_seconds=[window[0] / fps,
                                             window[1] / fps]))
        checked.append((effective, final, clips))

    built = {}
    try:
        for spec, final, clips in checked:
            print(f"Building variant {final}", flush=True)

            subtitle_segments = reel_subtitle_segments(
                moment, transcript, ranges, project_folder,
                fps=fps, width=reel_width, height=reel_height,
                timeline_name=final, lead_seconds=lead,
                draw_gain=run_gain)
            # The captain's caption-only timing, applied to the
            # RENDERED segments and before anything places them -
            # the same seam the rebuild applies it at.
            if subtitle_segments is not None and _caption_pins:
                (subtitle_segments, _cap_applied, _cap_short,
                 _cap_stale) = _caption_timing.apply_pins(
                    subtitle_segments, _caption_pins, fps)
                _caption_timing.report(_cap_applied, _cap_short,
                                       _cap_stale)
            explainer_segments, _explainer_plan = reel_explainer_segments(
                moment, transcript, ranges, project_folder,
                fps=fps, width=reel_width, height=reel_height,
                judgement=judgement, brand_effect=brand_effect,
                timeline_name=final, draw_gain=run_gain)
            # The speaker lower thirds, on the variant's own timeline
            # and from the variant's own ranges - a variant that cuts a
            # speaker's first line out introduces whoever now speaks
            # first, which is what makes this a plan and not a copy.
            lower_third_segments, _lower_third_plan = (
                reel_lower_third_segments(
                    moment, transcript, ranges, project_folder,
                    fps=fps, width=reel_width, height=reel_height,
                    brand_effect=brand_effect, timeline_name=final,
                    subtitle_segments=subtitle_segments,
                    lead_seconds=lead, extra_cuts=moment_cuts,
                    draw_gain=run_gain))
            from library.tools import reel_semantic_visual as sem_vis
            sem_vis.write_request(moment, transcript, ranges,
                                  project_folder, fps=fps)
            semantic_segments, _semantic_record = sem_vis.build_for_reel(
                moment, transcript, ranges, project_folder,
                fps=fps, width=reel_width, height=reel_height,
                timeline_name=final)
            overlay_plan = None
            if overlay_declared:
                overlay_plan = overlay_mod.plan_reel_overlays(
                    overlay_effect, ranges, fps, project_folder,
                    closer_seam_frame=_closer_seam_frame(
                        moment, ranges, fps))
            reel_motion = []
            if reel_look_decl is not None:
                spine = _reel_look.motion_spine(base_placements, fps)
                _reel_look.write_motion_request(
                    moment.number, final, spine,
                    transcript.get("segments") or [], project_folder)
                reel_motion, motion_record = _reel_look.resolve_motion(
                    _reel_look.read_motion_answer(project_folder,
                                                 moment.number),
                    spine, fps)
                motion_record["reel"] = final
                if motion_record["basis"] == _reel_look.MOTION_AWAITING_ANSWER:
                    print(f"  {final}: NO PICTURE MOTION - no model "
                          f"answer on file, every shot plays still",
                          file=sys.stderr)
            # Phase log: the variant's answers, at the moment they are
            # read - the same `answers_arrived` the rebuild logs, keyed
            # to the variant's own timeline name.
            from library.tools import reel_phase_log as _vphase_log
            try:
                _v_motion_basis = (
                    motion_record.get("basis")
                    if reel_look_decl is not None else "not_declared")
                if (reel_look_decl is not None
                        and _v_motion_basis
                        == _reel_look.MOTION_AWAITING_ANSWER):
                    _vphase_log.log_wait(
                        project_folder, int(reel_number), final,
                        f"no model answer on file "
                        f"({_reel_look.motion_request_stem(moment.number)}"
                        f".json) - every shot plays still")
                _v_answers = _vphase_log.log_event(
                    project_folder, int(reel_number), final,
                    _vphase_log.ANSWERS_ARRIVED,
                    detail=(f"semantic={_semantic_record.get('basis')} "
                            f"motion={_v_motion_basis}"))
                _v_waited = _vphase_log.seconds_since(_v_answers)
                _vphase_log.log_event(
                    project_folder, int(reel_number), final,
                    _vphase_log.BUILD_STARTED,
                    detail=(f"building variant {final}"
                            + (f"; {_v_waited}s since answers arrived"
                               if _v_waited is not None else "")))
            except Exception:
                pass

            build_result = build_reel_timeline(
                project=project,
                moment=moment,
                master_clips=clips,
                subtitle_segments=subtitle_segments,
                fps=fps,
                width=reel_width,
                height=reel_height,
                project_folder=project_folder,
                transcript=transcript,
                timeline_name=final,
                cards=cards,
                overlay_placements=(overlay_plan.placements
                                    if overlay_plan else None),
                explainer_segments=explainer_segments,
                semantic_segments=semantic_segments,
                lower_third_segments=lower_third_segments,
                look=reel_look_decl,
                motion=reel_motion,
                master_timeline=timeline,
                extra_cuts=moment_cuts,
                j_cut=spec.get("j_cut"),
                cutaway=spec.get("cutaway"),
                grade_cdl=reel_grade_cdl,
                power_grade=reel_power_grade,
                # The captain's pinned overlay positions ({} when they
                # declared none): declared wins over computed, so a
                # variant keeps their corrections exactly as a rebuild
                # does.
                overlay_intent=variant_overlay_intent,
                draw_gain=run_gain,
                # WHERE THIS REEL ENDS, and what draws over its tail.
                ending=_ending_decl,
                # The trimmed ranges every variant plan above was drawn
                # from - recomputing from the moment would un-trim the
                # captain's pins.
                ranges=ranges,
                # The declared card row, shared by all variants the
                # way the cards themselves are.
                card_row_role=card_row_role,
                # The graphics the captain deleted, read on the
                # variant's own branch exactly as the rebuild reads
                # them - a declaring variant differs here, with no
                # second builder anywhere.
                do_not_draw=variant_suppressions,
            )
            if reel_look_decl is not None:
                manifest = _reel_look.fusion_manifest(
                    apply_offset_specs(
                        placements(ranges, clips, fps,
                                   lead_frames=lead_frames(cards, fps)),
                        fps, None, spec.get("j_cut"),
                        spec.get("cutaway"))[0],
                    reel_look_decl, reel_motion, fps,
                    track_plan=build_result["track_plan"],
                    angle_key=_angle_key)
                if not _reel_look.apply_comps(manifest, project_folder,
                                             resolve_name, final):
                    raise ReelBuildError(
                        f"{final}: the Fusion pass refused or failed - "
                        f"a variant that lost its comps is not the "
                        f"approved picture with a different seam.")

            placed = None
            for i in range(1, project.GetTimelineCount() + 1):
                t = project.GetTimelineByIndex(i)
                if t.GetName() == final:
                    placed = t
                    break
            if placed is None:
                raise ReelBuildError(
                    f"{final}: built without error but no timeline "
                    f"under that name reads back - refused rather "
                    f"than reported as placed.")
            raw = build_result["track_plan"]
            plan = TrackPlan(
                video_tracks=[TrackSpec(**t) for t in raw["video_tracks"]],
                audio_tracks=[TrackSpec(**t) for t in raw["audio_tracks"]],
                material=raw.get("material", {}))
            report = verify_timeline(placed, plan=plan)
            if not report.get("passed"):
                raise ReelBuildError(
                    f"{final}: structural conformance failed: "
                    f"{report.get('violations')} - the variant is "
                    f"removed rather than left beside the reel.")
            if report.get("checks_skipped"):
                raise ReelBuildError(
                    f"{final}: conformance skipped "
                    f"{report.get('checks_skipped')} - a gate that "
                    f"did not run is not a pass.")
            print(f"  {final}: conformance-clean "
                  f"({len(report.get('checks_run', []))} checks)",
                  flush=True)
            try:
                _vphase_log.log_event(
                    project_folder, int(reel_number), final,
                    _vphase_log.BUILD_FINISHED,
                    detail=f"variant {final} placed and conformance-clean")
                _vphase_log.log_event(
                    project_folder, int(reel_number), final,
                    _vphase_log.VERIFIED,
                    detail=(f"structural conformance passed "
                            f"({len(report.get('checks_run', []))} checks)"))
            except Exception:
                pass
            # The ROWS of what was just placed, stored off Resolve.
            # `round_diff.diff_reel` over two of these is what turns
            # "watch both and decide" into a readable list of what
            # actually differs - the same measurement the promotion's
            # replace guard already reads, so the comparison and the
            # guard cannot disagree about what a row holds.
            from library.tools import reel_replace_guard as _vguard
            rows = _vguard.snapshot_timeline(placed, final,
                                             side="variant")
            built[final] = {"build_record": build_result,
                            "conformance": report,
                            "suffix": str(spec.get("suffix", "")),
                            "rows": rows,
                            "watch": spec.get("watch", "")}
            from library.tools import variant_choice as _choice
            _choice.record_build(
                project_folder, int(reel_number), moment.timeline_name,
                str(spec.get("suffix", "")), rows,
                watch=str(spec.get("watch", "")),
                declares=list(spec.get("declares") or ()))
    except Exception:
        # Atomic batch (see docstring): every variant this call placed
        # goes, and names it never reached are tolerated by the
        # discard. `None` for the master keeps the discard to the
        # timelines - a variant refusal must not file the shared pool
        # as a side effect; stray caption imports stay loose and the
        # error says so.
        discard_staged_reels(project, project_folder, list(built)
                             + [f for f in finals if f not in built],
                             None)
        raise
    return {"reel": moment.timeline_name,
            "resolve_project_name": resolve_name,
            "variants": built}


def report_layer_coherence(project_folder: str) -> dict:
    """Run the layer-vs-source check over a reels project.

    PRINT, return, never raise. The forced consultation
    (`library/tools/edit_depth.py`): every layer that disagrees with
    its source is named with both values. Advisory only - a wording
    staleness in frozen step-output history must not hold a build
    hostage - which is why this prints beside the prebuild census and
    the divergence survey rather than gating inside `verify_built_reels`.
    """
    import sys as _sys

    try:
        from library.tools import layer_coherence as _coherence
        report = _coherence.check_project(str(project_folder))
    except Exception as failed:  # noqa: BLE001
        print(f"  layer-coherence witness unavailable ({failed}) - "
              f"building without it", file=_sys.stderr)
        return {"unavailable": str(failed)}
    owned = sum(len(report.get(key, []) or ())
                for key in ("wording", "pins", "assets"))
    if owned:
        print(f"LAYER COHERENCE: {owned} owned-layer divergence(s) "
              f"stand - run `python3 -m library.tools.layer_coherence "
              f"<project>` for both values of each.", file=_sys.stderr)
    return report


def sweep_all_reels_informational(project_folder: str,
                                  resolve_project_name: str,
                                  master_timeline_name: str,
                                  plan_path: str,
                                  transcript_path: str) -> dict:
    """Grade EVERY reel timeline and PRINT the findings. Never raises.

    The detection half of the conformance sweep, restored beside the
    scoped refusing gate rather than in place of it. The gate
    (`verify_built_reels`, `only_reels=<what this build placed>`) stays
    scoped on purpose: whole-project grading as a REFUSAL failed clean
    single-reel builds on findings from timelines they never touched -
    94 of 121 errors in `data/vep-rebuild-verify/report.md`, 3.5 - and
    cost a re-grade per reel per build (PR #658,
    `tests/test_verify_scopes_to_built_reels.py`). Reverting that
    would reintroduce both. So the gate refuses on what was placed,
    and this sweep REPORTS on everything else.

    Read-only (the verifier's getters only) into its own
    `conformance_sweep_report.json` - never the gate's
    `conformance_report.json`, which a sweep must not overwrite.

    How to read it: a finding on a reel this build placed is news. A
    PLAN-MISMATCH on a reel it did not touch means that reel was built
    from an older plan, not that it is defective - per-reel plan
    awareness is version-object work (ranked items 6-10) and this
    sweep does not have it, so it says so instead of refusing.
    """
    import json as _json
    import os as _os
    import sys as _sys

    review_dir = _os.path.join(str(project_folder), "pipeline_output",
                               "review")
    _os.makedirs(review_dir, exist_ok=True)
    sweep_path = _os.path.join(review_dir,
                               "conformance_sweep_report.json")
    try:
        from library.tools.reel_conformance_verifier import run_verification
    except ImportError as exc:
        print(f"  whole-project conformance sweep unavailable "
              f"({exc}) - building without it", file=_sys.stderr)
        return {"unavailable": str(exc)}
    try:
        with open(transcript_path, encoding="utf-8") as handle:
            transcript = _json.load(handle)
        exit_code = run_verification(
            project_name=resolve_project_name,
            master_name=master_timeline_name,
            plan_path=plan_path,
            transcript=transcript,
            json_path=sweep_path,
            review_dir=review_dir,
            project_folder=str(project_folder),
            only_reels=None,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  whole-project conformance sweep unavailable "
              f"({exc}) - building without it", file=_sys.stderr)
        return {"unavailable": str(exc)}
    if exit_code == 0:
        print(f"  whole-project sweep: every reel timeline conforms "
              f"({sweep_path})", flush=True)
    else:
        print(f"  whole-project sweep: findings stand (exit "
              f"{exit_code}) - {sweep_path}. A PLAN-MISMATCH on a reel "
              f"this build did not touch means an older plan, not a "
              f"defective reel.", flush=True)
    return {"exit_code": exit_code, "report": sweep_path}


def verify_built_reels(project_folder: str, resolve_project_name: str, master_timeline_name: str, plan_path: str, transcript_path: str, only_reels=None, draw_gain: float = None) -> None:
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
            project_folder=project_folder,
            only_reels=None if only_reels is None else list(only_reels),
            draw_gain=draw_gain,
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
