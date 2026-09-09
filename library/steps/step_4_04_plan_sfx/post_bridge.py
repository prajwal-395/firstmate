#!/usr/bin/env python3
"""
Step 4.4 Bridge: Resolve SFX Creative Plan to Execution Data

Takes the LLM's creative SFX selections and resolves them to precise
timeline positions using signal data from the temporal index.

**Each entry names an `sfx_id` - a real file out of the library
catalogue the pre-bridge published - and it is resolved HERE, at plan
time.** An id the catalogue does not contain fails the step, naming the
id and the count of playable sounds. That is the whole reason
`library/tools/sfx_library.py` exists, and it is the one guarantee this
file must not lose: a plan that reaches `compile_manifest` names sounds
that are on disk.

The entry carries its resolved `source_file` forward, so `compile_manifest`
places the sound the model chose rather than re-deriving one from a type
name three steps later.

Placement strategy by the sound's own MEASURED ENVELOPE - the library's
`technical.energy_profile.envelope_shape`, not a word the model typed:

  punchy               → snap to nearest onset (transient) within ±200ms
  swelling             → end at the nearest energy peak
  fading / sustained   → align to scene boundary or block edge
  unmeasured           → snap to nearest onset within ±100ms

Every tolerance above is the one the strategy already used; nothing here
is a new number. What changed is the KEY. It used to be `sfx_type`, one
of eight abstract names, and two of the five strategies were keyed on
distinctions the library never made: `click` and `tick` resolved to the
same file, `swell` and `riser` resolved to the same file, and
`reverse_cymbal` resolved to no file at all. A strategy selected by a
distinction that does not exist in the library is not a strategy, so the
word-end-snap and last-word-end branches are withdrawn rather than
re-keyed. Speech-gap avoidance below still keeps a sound off a word.

A sound's DURATION is bounded by its own measured length and chosen by
the PLAN inside it. `DURATION_DEFAULTS` said a `bass_impact` runs 0.5s
while the file the matcher handed it was a 5.3-second riser, so the
table went; but the schema then had nowhere to put a length at all, and
the same `whoosh_impact.mp3` the run of record played for 0.25s played
for its full 8.04s. Both are a number nobody chose. `duration_seconds`
is optional - declaring none plays the whole sound - and a request past
what the file measures is REFUSED BY NAME, never clamped. A truncated
sound also carries a one-frame de-click ramp, because cutting a
waveform mid-cycle clicks. See `library/tools/sfx_duration.py`.

All placements are cross-referenced against:
  1. Onset times (transient anchors, ~23ms precision)
  2. Energy peaks (30Hz, ~33ms precision)
  3. Scene boundaries (~33ms precision)
  4. Word end times (speech boundaries, ~10ms precision)
  5. Beat grid (when music BPM is available)

Speech collision avoidance: SFX are shifted to the nearest gap if they
would overlap with active speech.

Classification: Deterministic / Data Transformation
Idempotent: Yes
"""
import json
import sys

from library.tools.fairlight_presets import select_preset_for_content
from library.tools.pipeline_validation import require_keys, require_type
from library.tools.sfx_duration import (
    SfxDurationRefused,
    declick_fade_seconds,
    full_playable_seconds,
    resolve_played_seconds,
)
from library.tools.sfx_envelope import placement_of
from library.tools.sfx_level import read_volume_db
from library.tools.sfx_library import load_sfx_catalog, resolve_sfx_id
from library.tools.spine_contract import (
    block_word_end_times_timeline,
    is_speech_block,
    source_to_timeline,
)


# There is no VOLUME_MAP. How loud a sound plays is the plan's own
# number in dB, and the four-word ladder that used to resolve
# subtle|low|medium|prominent into -18|-14|-10|-6 is withdrawn - the same
# ruling that removed step 4.03's INTENSITY_MAP. The record of what it
# held is library/tools/sfx_level.WITHDRAWN_VOLUME_LADDER.

# There is no DURATION_DEFAULTS table. How long a sound runs is a
# property of the sound, and the library measures it. The table used to
# say `bass_impact: 0.5` while the file behind that name was a 5.317s
# riser, so the manifest asserted a length the audio did not have.

# What an entry IS in the mix: `literal` is a sound tied to a visible
# event, placed at it; `layer` is the atmospheric layer (captain's
# ruling 2026-09-08) - a riser, drone or crackle that plays UNDER the
# picture as a bed rather than marking a moment.  A layer is NOT shifted
# off speech the way a literal sound is: `_avoid_speech_collision`
# moves a literal sound into a word gap, and a bed moved into a gap
# stops being a bed.  The word is the plan's to declare and there is no
# default: an entry naming no role is literal, and a role outside this
# pair is dropped rather than read as one of them.
SFX_ROLES = ("literal", "layer")


def _stated_reason(sfx: dict) -> bool:
    """Whether the entry states why this sound earns its place.

    A missing key, a non-string, and a blank string are all no reason.
    A layer is not tied to a visible event, so its rationale is the
    only thing holding it to the moment.
    """
    rationale = sfx.get("rationale")
    return isinstance(rationale, str) and bool(rationale.strip())


def _previous_cut(block: dict, spine_blocks: list) -> float:
    """The cut before this block's own timeline start.

    The greatest `timeline_start` strictly earlier than the block's, or
    0.0 when there is none - the first block has no previous cut, so
    the top of the reel is its only boundary.  This is what a J-cut
    lead is measured against: the lead start must not be earlier.
    """
    try:
        start = float(block.get("timeline_start", 0.0))
    except (TypeError, ValueError):
        return 0.0
    earlier = []
    for other in spine_blocks:
        try:
            moment = float(other.get("timeline_start", 0.0))
        except (TypeError, ValueError):
            continue
        if moment < start - 1e-9:
            earlier.append(moment)
    return max(earlier) if earlier else 0.0


def _find_nearest(target: float, candidates: list, max_dist: float = None) -> float:
    """Find the nearest value in candidates to target.

    Returns the nearest candidate, or target if no candidates are
    within max_dist (or if candidates is empty).
    """
    if not candidates:
        return target
    nearest = min(candidates, key=lambda c: abs(c - target))
    if max_dist is not None and abs(nearest - target) > max_dist:
        return target
    return nearest


def _locate_sfx(sfx: dict, spine_blocks: list, block_by_position: dict):
    """Resolve an SFX creative entry to (spine_block, timeline_start).

    Returns (None, None) when the entry names no position at all, so the
    caller can drop it loudly instead of silently placing it at 0.0.
    """
    pos = sfx.get("spine_block_position", sfx.get("target_block_position"))
    if pos is not None and str(pos) in block_by_position:
        block = block_by_position[str(pos)]
        # An explicit timeline position inside the block wins; otherwise
        # anchor to the block's own start.
        for key in ("timeline_start", "timeline_in"):
            if sfx.get(key) is not None:
                return block, float(sfx[key])
        return block, block["timeline_start"]

    for key in ("timeline_start", "timeline_in"):
        if sfx.get(key) is not None:
            tl = float(sfx[key])
            return _find_block_for_time(tl, spine_blocks), tl

    return None, None


def _find_block_for_time(timeline_time: float, spine_blocks: list) -> dict:
    """Find the spine block that contains a given timeline position."""
    for block in spine_blocks:
        tl_start = block.get("timeline_start", 0)
        tl_end = block.get("timeline_end", 0)
        if tl_start - 0.1 <= timeline_time <= tl_end + 0.1:
            return block
    # Fallback: find the nearest block
    if spine_blocks:
        return min(spine_blocks,
                   key=lambda b: abs(b.get("timeline_start", 0) - timeline_time))
    return {}


def _source_to_timeline(source_time: float, block: dict) -> float:
    """Convert a source-domain time to timeline-domain for a given block."""
    return source_to_timeline(source_time, block)


def _get_word_times_in_block(block: dict) -> list:
    """Word end times for a block, in the timeline domain.

    Read straight off the spine, which carries the block's own word
    timings (see library/tools/spine_contract.py).
    """
    return block_word_end_times_timeline(block)


def _avoid_speech_collision(
    sfx_time: float,
    sfx_duration: float,
    word_times_tl: list,
    block_start: float,
    block_end: float,
) -> float:
    """Shift SFX placement to avoid overlapping with speech.

    Checks if the SFX would overlap with any word boundary.
    If so, finds the nearest gap between words.
    """
    if not word_times_tl:
        return sfx_time

    sfx_end = sfx_time + sfx_duration

    # Check for collision with any word boundary
    collision = False
    for wt in word_times_tl:
        if sfx_time <= wt <= sfx_end:
            collision = True
            break

    if not collision:
        return sfx_time

    # Find the nearest gap between word boundaries
    # Gaps are the spaces between consecutive word end times
    sorted_times = sorted(word_times_tl)
    best_gap_start = sfx_time
    best_gap_dist = float("inf")

    for i in range(len(sorted_times) - 1):
        gap_start = sorted_times[i]
        # Estimate next word starts ~0.2s before its end time
        gap_end = sorted_times[i + 1] - 0.2
        gap_size = gap_end - gap_start

        # Gap must be large enough for the SFX
        if gap_size >= sfx_duration * 0.8:
            dist = abs(gap_start - sfx_time)
            if dist < best_gap_dist:
                best_gap_dist = dist
                best_gap_start = gap_start

    # Also check before the first word and after the last word
    if sorted_times[0] - block_start >= sfx_duration:
        dist = abs(block_start - sfx_time)
        if dist < best_gap_dist:
            best_gap_start = block_start

    if block_end - sorted_times[-1] >= sfx_duration:
        dist = abs(sorted_times[-1] - sfx_time)
        if dist < best_gap_dist:
            best_gap_start = sorted_times[-1]

    return max(block_start, min(best_gap_start, block_end - sfx_duration))


def find_sfx_placement(
    envelope: str,
    timeline_start: float,
    sfx_duration: float,
    block: dict,
    temporal_index: dict,
    beat_grid: list = None,
) -> float:
    """Signal-driven SFX placement, keyed on the sound's measured envelope.

    `envelope` is the library's own `technical.energy_profile.envelope_shape`
    for the file the model chose - `punchy`, `swelling`, `fading`,
    `sustained`, or "" where the profiler measured none. It is NOT a word
    the model typed: the model names a sound, and the sound's measured
    shape decides how it is snapped to the picture.

    1. Select candidate times from the primary signal
    2. Snap to nearest onset for tighter sync
    3. Beat-quantize if music is present
    4. Avoid speech collision

    Returns:
        Refined timeline position for the SFX
    """
    clip_id = block["clip_id"]
    src_start = block["source_start"]
    src_end = block["source_end"]
    tl_start = block["timeline_start"]
    tl_end = block["timeline_end"]

    # If no clip_id (transition_slot, outro), keep original position
    if not clip_id:
        return timeline_start

    # Convert temporal index data to timeline domain
    onset_times_src = temporal_index.get("onset_times", [])
    energy_peaks_src = temporal_index.get("energy_curve", {}).get("peak_times", [])
    scene_boundaries_src = [s["time"] for s in temporal_index.get("scene_boundaries", [])]

    # Filter to this block's source range and convert to timeline
    def in_block_src(t):
        return src_start - 0.1 <= t <= src_end + 0.1

    onsets_tl = [_source_to_timeline(t, block) for t in onset_times_src if in_block_src(t)]
    peaks_tl = [_source_to_timeline(t, block) for t in energy_peaks_src if in_block_src(t)]
    scenes_tl = [_source_to_timeline(t, block) for t in scene_boundaries_src if in_block_src(t)]

    beat_grid = beat_grid or []

    # ── Envelope-specific placement strategies ──

    if envelope == "punchy":
        # A punchy sound is a single sharp attack, and it feels best when
        # that attack lands exactly on a natural audio transient.
        # Search within ±200ms of the intended position.
        placed = _find_nearest(timeline_start, onsets_tl, max_dist=0.2)

        # If no onset nearby, try energy peak
        if placed == timeline_start and peaks_tl:
            placed = _find_nearest(timeline_start, peaks_tl, max_dist=0.5)

        # Beat-snap if available
        if beat_grid:
            placed = _find_nearest(placed, beat_grid, max_dist=0.05)

        return placed

    if envelope == "swelling":
        # A swelling sound builds - it ENDS at an energy peak. Place the
        # start so that start + duration = peak.
        if peaks_tl:
            target_end = _find_nearest(timeline_start + sfx_duration, peaks_tl, max_dist=2.0)
            return max(tl_start, target_end - sfx_duration)
        return timeline_start

    if envelope in ("fading", "sustained"):
        # A sound with no sharp attack of its own guides attention across
        # a cut. Align to scene boundary or block edge - whichever is
        # nearest - then tighten onto an onset.
        candidates = scenes_tl + [tl_start, tl_end]
        placed = _find_nearest(timeline_start, candidates, max_dist=0.5)
        return _find_nearest(placed, onsets_tl, max_dist=0.1)

    # Nothing measured the envelope - keep the planned position, snapping
    # onto an onset only if one is already close.
    return _find_nearest(timeline_start, onsets_tl, max_dist=0.1)


class UnplayableSfxPlan(ValueError):
    """A plan naming a sound the library does not have.

    Raised at PLAN time, from step 4.04, with every offending id named.
    It is not a warning and it is not a per-entry drop: an unplayable
    entry is a plan the model wrote against something it thinks exists,
    and silently removing it ships an edit missing a sound nobody decided
    to cut.
    """


def assert_plan_is_playable(creative_plan: list, catalog: list) -> dict:
    """Resolve every `sfx_id` in the plan, or refuse the whole plan.

    Returns {index -> catalogue entry}. Raises `UnplayableSfxPlan` naming
    each entry that cannot be resolved, and why - a missing id, or an id
    the library has no file for.
    """
    resolved, problems = {}, []
    for i, sfx in enumerate(creative_plan):
        sfx_id = sfx.get("sfx_id")
        where = sfx.get("spine_block_position", sfx.get("timeline_start", "?"))
        if not sfx_id:
            problems.append(
                f"entry {i} (block {where}) names no sfx_id - which sound "
                f"plays is this step's decision and nothing substitutes one"
            )
            continue
        entry = resolve_sfx_id(sfx_id, catalog)
        if entry is None:
            problems.append(
                f"entry {i} (block {where}) names sfx_id {sfx_id!r}, which "
                f"is not in the SFX library catalogue"
            )
            continue
        resolved[i] = entry
    if problems:
        raise UnplayableSfxPlan(
            f"{len(problems)} of {len(creative_plan)} planned sound(s) "
            f"cannot be played from the {len(catalog)}-entry SFX library:"
            "\n  - " + "\n  - ".join(problems)
            + "\nChoose an sfx_id out of the catalogue at the path in "
              "sfx_catalog_reference."
        )
    return resolved


def _entry_source_in(entry: dict) -> float:
    """Where playback starts inside the chosen file.

    `transient_offset_sec` is the library's measurement of where the
    file's own loudest moment falls, and trimming to it makes that moment
    land on `timeline_in`. That is right for a sound whose whole content
    IS its attack, and wrong for one that builds: `find_sfx_placement`
    already END-aligns a `swelling` sound so its climax arrives at an
    energy peak, so trimming its head off would throw away the build the
    placement is pointing at.

    So the trim applies to `punchy` alone, and every other shape plays
    from its own beginning. One measurement, read the same way twice.
    """
    if entry.get("envelope") != "punchy":
        return 0.0
    transient = entry.get("transient_offset_sec")
    if not isinstance(transient, (int, float)) or transient <= 0:
        return 0.0
    return float(transient)


def _entry_duration(entry: dict, source_in: float = 0.0,
                    requested=None, fps: float = 30.0) -> tuple:
    """How much of the chosen sound plays, and its de-click ramp.

    Returns `(played_seconds, fade_out_seconds)`. The plan's own
    `duration_seconds` decides, bounded by what the library measured;
    an entry declaring none plays the whole remainder, which is the
    ABSENCE of a decision. Everything about the bound, the refusal and
    the ramp is in `library/tools/sfx_duration.py`.
    """
    playable = full_playable_seconds(entry, source_in)
    played = resolve_played_seconds(entry, source_in, requested, fps)
    return played, declick_fade_seconds(played, playable, fps)


def resolve_sfx(
    creative_plan: list,
    timed_spine: dict,
    temporal_indices: list = None,
    music_analysis: dict = None,
    music_selection: dict = None,
    frame_rate: float = 30.0,
    creative_direction: dict = None,
    brand_audio: dict = None,
    catalog: list = None,
) -> dict:
    """Resolve creative SFX plan to execution specs.

    For each SFX in the creative plan:
    1. Resolve its `sfx_id` against the library catalogue, or REFUSE
    2. Find the spine block it belongs to
    3. Load the temporal index for that clip
    4. Apply envelope-driven signal placement, at the sound's own length
    5. Check for speech collision and shift if needed

    `catalog` is `sfx_library.load_sfx_catalog()`; it is a parameter so a
    test can drive this against a library it built itself.
    """
    catalog = load_sfx_catalog() if catalog is None else catalog
    entry_by_index = assert_plan_is_playable(creative_plan, catalog)
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    temporal_indices = temporal_indices or []

    # Build temporal index lookup by clip_id
    ti_lookup = {}
    for ti in temporal_indices:
        cid = ti.get("clip_id", "")
        if cid:
            ti_lookup[cid] = ti

    # Bar starts from the music analysis. This used to read
    # `music_analysis["beat_grid"]["bars"]`, and the producer emits no
    # `beat_grid` key at all - `analyze_music` returns
    # {"tempo": {"bpm", "beats", "downbeats"}, ...} - so `bars` was always
    # empty and SFX never snapped to anything. See
    # library/tools/beat_grid.py.
    # `music_selection` carries the chosen section, and the grid is in the
    # music file's clock: a bar start read without the offset is off by
    # wherever the bed starts. See library/tools/music_section.py.
    from library.tools.beat_grid import downbeat_positions
    beat_grid = downbeat_positions(music_analysis, music_selection)

    # There is no density scaling here, and there must not be one again.
    #
    # `scale_sfx_density` used to run over the plan at this point and
    # DELETE entries from it: half the impacts on a "moderate" energy,
    # everything but transitions and ambience on a "calm" one. The energy
    # it judged by was `creative_direction.get("energy_level",
    # "moderate")` - and `energy_level` is not a creative_direction key at
    # all (the real one is `target_energy`, see
    # library/tools/energy_reading.py), so the word was ALWAYS the
    # hardcoded "moderate". A constant in this file decided how many
    # sounds the piece kept.
    #
    # How many sound effects a piece gets is a creative decision -
    # captain's ruling 2026-08-20 - and that holds for cutting them as
    # much as for padding them. The function is deleted, not just
    # unwired. Guarded by tests/test_no_creative_floors.py.

    # There is no prosody alignment here, and there must not be one
    # again. `audio_reactive_sfx.align_sfx_to_prosody` ran at this point
    # behind `if prosody_analysis:` and NEVER ONCE FIRED, for five
    # independent reasons - no edge routes `prosody_analysis` here, the
    # shape it read is not 1.05's, the measurement it wanted has never
    # been produced, it branched on a `type` key the plan schema does not
    # have, and it wrote a `start_time` nothing reads. What it claimed to
    # do, `find_sfx_placement` and `_avoid_speech_collision` below
    # already do from stronger signals. See
    # library/tools/audio_reactive_sfx.py for the whole record.

    block_by_position = {str(b["position"]): b for b in spine_blocks}

    resolved = []
    for plan_index, sfx in enumerate(creative_plan):
        # Locate the SFX. An entry must say WHERE it goes: either a spine
        # block position or an explicit timeline position. Defaulting a
        # missing position to 0.0 is what stacked every planned SFX on top
        # of the first frame.
        block, tl_start = _locate_sfx(sfx, spine_blocks, block_by_position)
        if block is None:
            print(
                f"  Dropped SFX {sfx.get('sfx_id', '?')}: it names no "
                f"position (needs spine_block_position or timeline_start)",
                file=sys.stderr,
            )
            continue
        clip_id = block["clip_id"]

        # Get temporal index for this clip
        ti = ti_lookup.get(clip_id, {}) if clip_id else {}

        # Which sound plays, and how loud, is the decision this step
        # exists to make. An entry naming neither used to become a
        # `whoosh` at -14 dB, so a malformed plan entry put a sound on the
        # timeline that nobody chose.
        entry = entry_by_index[plan_index]
        volume_db, level_reason = read_volume_db(sfx)
        if volume_db is None:
            print(
                f"  Dropped SFX {entry['sfx_id']!r} on block "
                f"{block.get('position')!r}: {level_reason}.",
                file=sys.stderr,
            )
            continue
        # The atmospheric layer (captain's ruling 2026-09-08): a riser,
        # drone or crackle that plays UNDER the picture rather than
        # marking a visible event.  Whatever reasons a literal choice -
        # a real `sfx_id` (checked above) and the plan's own `volume_db`
        # (checked just above) - reasons a layer too, plus a stated
        # reason, because nothing else holds it to the moment.  A layer
        # with no rationale is sprinkling, and it goes.
        role = sfx.get("role") or "literal"
        if role not in SFX_ROLES:
            print(
                f"  Dropped SFX {entry['sfx_id']!r} on block "
                f"{block.get('position')!r}: unknown role {role!r} - the "
                f"roles are {', '.join(SFX_ROLES)}. A role nothing reads "
                f"would ship a literal placement for a behaviour the plan "
                f"asked for.",
                file=sys.stderr,
            )
            continue
        is_layer = (role == "layer")
        if is_layer and not _stated_reason(sfx):
            print(
                f"  Dropped SFX {entry['sfx_id']!r} on block "
                f"{block.get('position')!r}: role 'layer' states no "
                f"reason. A layer is not tied to a visible event, so it "
                f"needs a rationale naming what in the library entry made "
                f"it right for this moment.",
                file=sys.stderr,
            )
            continue
        # A J-cut lead (captain's ruling 2026-09-08): sound may arrive
        # BEFORE the cut it belongs to.  The whole sound starts this
        # many seconds earlier at the same duration.  This is purely a
        # placement offset - `compile_manifest` reads `timeline_in` /
        # `timeline_out` whatever produced them, so the mix needs to
        # know nothing.
        lead = sfx.get("lead_seconds")
        if lead is None:
            lead = 0.0
        if (isinstance(lead, bool) or not isinstance(lead, (int, float))
                or lead < 0):
            print(
                f"  Dropped SFX {entry['sfx_id']!r} on block "
                f"{block.get('position')!r}: lead_seconds {lead!r} is not "
                f"a positive number of seconds - a lead that states no "
                f"timing states nothing.",
                file=sys.stderr,
            )
            continue
        lead = float(lead)
        source_in = _entry_source_in(entry)
        # How long it plays is the PLAN's decision, bounded by the
        # sound's measured length. A request past that is refused by
        # name in `sfx_duration`, never clamped.
        duration, fade_out = _entry_duration(
            entry, source_in, sfx.get("duration_seconds"), frame_rate)

        # Signal-driven placement
        refined_start = find_sfx_placement(
            entry.get("envelope") or "", tl_start, duration, block, ti,
            beat_grid,
        )

        # Speech collision avoidance
        word_times_tl = _get_word_times_in_block(block)
        tl_end = block["timeline_end"]
        tl_block_start = block["timeline_start"]

        if not is_layer:
            refined_start = _avoid_speech_collision(
                refined_start, duration, word_times_tl,
                tl_block_start, tl_end,
            )
        # else: a layer plays UNDER speech by design. Shifting it into
        # a word gap would stop it being a layer.

        if lead > 0:
            # The lead wins over the avoidance above: the plan timed
            # this sound explicitly, and re-shifting it would eat the
            # J-cut. The boundary is what keeps it honest - the lead
            # start must neither run past the previous cut nor off the
            # top of the reel.
            lead_start = refined_start - lead
            prev_cut = _previous_cut(block, spine_blocks)
            if lead_start < prev_cut - 1e-9:
                if prev_cut <= 0.0:
                    where = (f"starts at {round(lead_start, 3)}s, off the "
                             f"top of the reel")
                else:
                    where = (f"starts at {round(lead_start, 3)}s, past the "
                             f"previous cut at {round(prev_cut, 3)}s")
                print(
                    f"  Dropped SFX {entry['sfx_id']!r} on block "
                    f"{block.get('position')!r}: lead_seconds {lead}s "
                    f"from {round(refined_start, 3)}s {where} - a J-cut "
                    f"lead must not run past the previous cut or off the "
                    f"top of the reel.",
                    file=sys.stderr,
                )
                continue
            refined_start = lead_start

        shift = abs(refined_start - tl_start)
        tl_in_sec = round(refined_start, 3)
        tl_out_sec = round(refined_start + duration, 3)

        # The resolved FILE travels with the entry. `compile_manifest`
        # used to re-derive it from the type name with a keyword match,
        # so the sound the model chose and the sound that played were two
        # separate answers to two different questions.
        tl_in_frame = int(round(tl_in_sec * frame_rate))
        tl_out_frame = int(round(tl_out_sec * frame_rate))

        placement_method = _describe_placement(entry.get("envelope") or "")
        if is_layer:
            placement_method += (
                "; placed as an atmospheric layer under the picture, not "
                "shifted into a word gap")
        if lead > 0:
            placement_method += (
                f"; arrives {lead}s before the cut (J-cut lead)")

        resolved.append({
            "label": f"sfx_{len(resolved)+1:03d}",
            "sfx_id": entry["sfx_id"],
            "source_file": entry["path"],
            "source_in": round(source_in, 3),
            "sfx_category": entry.get("category", ""),
            "sfx_envelope": entry.get("envelope") or "unmeasured",
            "role": role,
            "timeline_in": tl_in_sec,
            "timeline_out": tl_out_sec,
            "timeline_in_frame": tl_in_frame,
            "timeline_out_frame": tl_out_frame,
            "duration_seconds": duration,
            "played_whole_sound": fade_out == 0.0,
            # A truncated sound stops mid-waveform, and that step to
            # silence clicks. 0.0 where the sound ends by itself.
            "fade_out_seconds": round(fade_out, 4),
            "volume_db": volume_db,
            "rationale": sfx.get("rationale", ""),
            "placement_method": placement_method,
            "shift_from_original": round(shift, 3),
            "spine_block_position": block["position"],
        })
        if lead > 0:
            resolved[-1]["lead_seconds"] = lead

    _assert_sfx_distributed(resolved)

    # Determine Fairlight preset.
    #
    # This used to read `creative_direction["content_type"]`, and step
    # 2.01 is not asked for a content type - its schema is eight fields
    # of prose about the footage's story. So the value was "" on every
    # run and `select_preset_for_content`'s own undeclared-type answer
    # decided the preset, while the code read as though the creative
    # director had chosen it. See WITHDRAWN_DIRECTION_KEYS in
    # library/tools/creative_direction.py.
    #
    # The declared route into this choice is the brand template's audio
    # slot, which the selector already prefers as `preferred_preset`.
    # Nothing else is substituted here: a mix preset is a MECHANICAL
    # default (AGENTS.md section 10.5), so the selector's documented
    # answer for an undeclared type is a legitimate one - but it is now
    # reached by saying nothing was declared, not by reading a key that
    # cannot exist.
    preset_name = select_preset_for_content("", brand_audio)

    return {
        "sfx_list": resolved,
        "fairlight_preset": preset_name,
    }



def _assert_sfx_distributed(resolved: list) -> None:
    """Fail when several planned moments collapse onto one timeline position.

    Five whooshes all at 0.000s is a collapse, not a sound design pass, and
    it used to survive all the way into the manifest.

    Layering - two or more sounds planned onto the SAME spine block
    (whoosh + bass hit, riser under a textural bed) - is legitimate sound
    design and is NOT caught here, even when the layered moment is the
    only sound in the plan. The plan tells them apart: every resolved
    entry carries the `spine_block_position` it was placed from, so
    entries naming several positions that landed on one timeline position
    are a placement collapse, and entries naming one are a layer.
    Entries with no provenance (legacy callers) keep the old strictness:
    several sounds sharing one position with nothing saying they were
    planned together is still refused.
    """
    if len(resolved) < 2:
        return
    positions = {round(s["timeline_in"], 3) for s in resolved}
    if len(positions) > 1:
        return
    pos = next(iter(positions))
    planned = {s.get("spine_block_position") for s in resolved
               if s.get("spine_block_position") is not None}
    if len(planned) == 1:
        return
    if len(planned) > 1:
        raise ValueError(
            f"{len(resolved)} SFX planned for {len(planned)} distinct spine "
            f"positions ({sorted(planned, key=repr)}) all resolved to the "
            f"same timeline position ({pos}s) - placement collapsed. A "
            f"layer shares one spine_block_position; these do not."
        )
    raise ValueError(
        f"{len(resolved)} SFX all resolved to the same timeline "
        f"position ({pos}s). A plan that places "
        f"every sound on one frame is a collapse, not layering. "
        f"Each SFX must name its own spine_block_position."
    )


def _describe_placement(envelope: str) -> str:
    """What the engine did with this sound, in one line.

    Read out of `library/tools/sfx_envelope.py`, which is the SAME table
    the pre-bridge renders into the prompt as `sfx_envelope_legend`. It
    used to be a private four-row dict here, so the sentence the manifest
    recorded and the sentence the planner would have needed could not
    have been checked against each other - and the planner was never told
    any of it.
    """
    return placement_of(envelope)


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    require_keys(data, ["music_analysis", "music_selection"],
                 "step_4_04_plan_sfx/post_bridge.py")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")

    creative = data.get("sfx_creative")
    if not creative and "llm_raw_response" in data:
        try:
            parsed = json.loads(data["llm_raw_response"])
            creative = parsed if isinstance(parsed, list) else parsed.get("sfx_creative", [])
        except Exception:
            creative = data["llm_raw_response"]
        
    if not isinstance(creative, list):
        print(f"  Warning: LLM returned invalid response for plan_sfx. Defaulting to empty list. Response was: {str(creative)[:100]}", file=sys.stderr)
        creative = []
        
    creative = [v for v in creative if isinstance(v, dict)]

    spine = data.get("timed_spine", {})
    temporal_raw = data.get("temporal_event_indices", [])
    temporal = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    music = data.get("music_analysis", {})
    music_selection = data.get("music_selection", {})
    fps = data.get("project_fps", data.get("frame_rate", 30.0))
    
    cd = data.get("creative_direction", {})
    brand_audio = data.get("brand_audio", {})
    
    # There is NO minimum SFX count. How many sound effects a piece gets is
    # a creative decision, not a quota. Captain's ruling 2026-08-20 - the
    # 5-10 requirement is removed outright, not reconciled and not
    # downgraded to a warning, with the accepted consequence that a thin
    # sound design is no longer caught mechanically. Do not reintroduce an
    # equivalent check. Guarded by tests/test_no_creative_floors.py.
    # `_assert_sfx_distributed` stays: it catches a COLLAPSE (several
    # planned moments on one frame), which is a broken plan, not a sparse
    # one. A single moment carrying several sounds is a layer and passes.

    # A plan naming a sound the library cannot play fails HERE, in step
    # 4.04, and not three steps later inside compile_manifest. The message
    # names every offending id, so the retry has something to act on.
    try:
        result = resolve_sfx(creative, spine, temporal, music,
                             music_selection, fps, cd, brand_audio)
    except (UnplayableSfxPlan, SfxDurationRefused) as unplayable:
        print(json.dumps({"error": str(unplayable), "step": "4.04_bridge"}))
        sys.exit(1)

    json.dump({"sfx_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
