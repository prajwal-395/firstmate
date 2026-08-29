"""Which seconds of a chosen cutaway clip are on screen.

A cutaway is placed ``video_only: True`` - `compile_manifest` writes it on
every B-roll placement - so **the clip's own audio is never heard**.  The
window it plays must therefore be chosen from what is IN THE PICTURE.

`step_3_02_select_broll`'s post-bridge used to choose it from
``temporal_index.energy_curve``, which is per-second RMS of that muted
audio.  On project 001's run of record that decided **7 of 7 windows**,
31.0% of the finished picture: five of the seven were centred within
0.000 s of the nearest audio peak.  The clip the captain complained about
by name - "this clip is honestly like broll of nothing" - is one of them,
though its window is the one this module changes least: 22.87 s of one
driving plate holds no better 2.5 s, and which CLIP is chosen is a
different decision.

This module is the whole of the replacement, and it does two jobs:

- it **enumerates the candidate windows of a clip** and describes each one
  with the measurements that are actually present in the picture, in a
  shape a prompt could carry (`CANDIDATE_LEGEND`);
- it **chooses one**, from the model's own ``preferred_moment`` matched
  against the time-bounded visual descriptions the vision pass wrote.

The choosing half is deliberately thin, because widening it is where taste
gets invented (AGENTS.md §10.5).  The engine resolves the model's words to
seconds; it does not decide that a moving shot beats a still one.  See
`DECLINED_TO_RANK`.
"""

from dataclasses import dataclass, field


# ── What may never choose a window ────────────────────────────────────
#
# A cutaway plays muted.  These are the keys of a `temporal_index` record
# that measure its SOUND, and none of them may reach a window decision.
# `tests/test_cutaway_window.py` reads this module's own source and fails
# if one appears in it.
AUDIO_SIGNALS = {
    "energy_curve": "per-second RMS of the clip's audio, which is muted",
    "audio_events": "onsets and transients in that same muted audio",
    "onset_times": "derived from the muted audio",
    "word_end_times": "derived from the muted audio",
    "speech_regions": "where the muted audio has speech in it",
    "speech_activity": "voice activity in the muted audio",
}


# ── What a candidate window is described by ───────────────────────────
#
# Column definitions as DATA, so route 2 - handing these rows to the model
# and letting it choose the window - needs no edit to the step's frozen
# `handoff.md`.  Same route `music_measurement.MEASUREMENT_LEGEND` and
# `transition_carriers.CUTS_LEGEND` take.
#
# A legend says what a key IS.  It never says what to conclude from it.
CANDIDATE_LEGEND = {
    "span_start": "seconds into the clip where this candidate span begins",
    "span_end": "seconds into the clip where it ends",
    "video_in": "the window this candidate would play, in seconds",
    "video_out": "the end of that window, in seconds",
    "describes": "what the vision pass observed on screen during the span",
    "moment_match": (
        "share of the words in the selection's own preferred_moment that "
        "appear in `describes`, 0.0-1.0"
    ),
    "motion_mean": (
        "mean absolute frame-to-frame difference over the window, 0-1 grey "
        "scale (motion_energy normalised value x peak_mean_abs_diff)"
    ),
    "motion_peak": "the largest such difference in the window",
    "camera_motion": (
        "mean optical-flow magnitude over the window; large and consistent "
        "is a pan or tilt, large and erratic is handheld shake"
    ),
    "brightness_mean": "mean frame brightness over the window, 0-255",
    "saturation_mean": "mean frame saturation over the window, 0-255",
    "face_fraction": (
        "share of face-presence samples in the window that found a face"
    ),
    "usable_overlap": (
        "share of the window inside a MEASURED usable range; None when "
        "nothing measured the clip's usable ranges"
    ),
}


# ── What is measured and deliberately not ranked on ───────────────────
#
# Every column above is recorded on every candidate.  Only `moment_match`
# and `usable_overlap` reach the choice.  The rest are here because a
# reviewer and a future model should see them - not because this module
# knows what they mean for a cutaway.
DECLINED_TO_RANK = {
    "motion_mean": (
        "that a busier window is a better cutaway is a taste claim, and "
        "the evidence does not support it: on 001 a motion-peak rule "
        "selects a different window from the shipped audio-peak rule on "
        "7 of 7, and nothing establishes either as right"
    ),
    "motion_peak": "same claim as motion_mean",
    "camera_motion": (
        "whether a pan reads better than a locked-off frame is the "
        "captain's decision, not an engine constant"
    ),
    "brightness_mean": (
        "a night shot is not a defective one; a brightness floor would be "
        "a look decision arriving one level up (AGENTS.md §12)"
    ),
    "saturation_mean": "same class as brightness_mean",
    "face_fraction": (
        "a cutaway may be wanted precisely because nobody is in it, or "
        "precisely because somebody is; neither is an engine default"
    ),
}


@dataclass
class WindowChoice:
    """The window, and what chose it."""

    video_in: float
    video_out: float
    basis: str
    basis_detail: str = ""
    candidates: list = field(default_factory=list)

    def as_tuple(self) -> tuple:
        return self.video_in, self.video_out


# The four bases a window can be chosen on. `undiscriminated` is the
# absence of a decision, not a decision - the same reading
# `music_section` gives a track with no declared section and
# `transition_vocabulary.CUT_TYPES` gives an undecorated cut.
BASES = {
    "moment_match": (
        "the selection's own preferred_moment matched what the vision "
        "pass observed during this span better than any other span"
    ),
    "moment_match_tie": (
        "two or more spans matched the moment equally well; the earliest "
        "won, and the tie is recorded rather than broken on a preference"
    ),
    "legacy_block_index": (
        "the document carries blocks with no time bounds, so the matched "
        "block's position in the list is all its position can be read from"
    ),
    "single_span": (
        "the clip yields ONE candidate span, so nothing chose the window - "
        "the clip did.  Recorded distinctly from `moment_match`, which "
        "would otherwise read as a decision that was never available"
    ),
    "undiscriminated": (
        "nothing in the picture distinguished one span from another, so "
        "the window starts where the clip's first candidate span does"
    ),
    "no_room": "the clip is no longer than the slot; the whole clip plays",
}


# ── Reading the signals ───────────────────────────────────────────────

def _curve_mean(values, sample_rate_hz, start, end, scale=1.0):
    """Mean of a sampled curve over [start, end), or None where empty."""
    if not values or not sample_rate_hz or sample_rate_hz <= 0:
        return None
    i_start = max(0, int(start * sample_rate_hz))
    i_end = min(len(values), int(end * sample_rate_hz) + 1)
    segment = [v for v in values[i_start:i_end] if isinstance(v, (int, float))]
    if not segment:
        return None
    return round(sum(segment) / len(segment) * scale, 4)


def _curve_max(values, sample_rate_hz, start, end, scale=1.0):
    if not values or not sample_rate_hz or sample_rate_hz <= 0:
        return None
    i_start = max(0, int(start * sample_rate_hz))
    i_end = min(len(values), int(end * sample_rate_hz) + 1)
    segment = [v for v in values[i_start:i_end] if isinstance(v, (int, float))]
    if not segment:
        return None
    return round(max(segment) * scale, 4)


def block_time_bounds(block: dict) -> tuple:
    """A block's measured (start, end), or (None, None) when it has none.

    Blocks derived from the v3 analyser carry the time window the action
    was observed in; blocks from the retired schema carry only their
    position in the list.
    """
    start, end = block.get("start"), block.get("end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return float(start), float(end)
    return None, None


def measured_usable_ranges(clip_analysis: dict):
    """The clip's usable ranges, or None when nothing measured them.

    **The method decides, not the ranges** - AGENTS.md §10.3.  A document
    written before #248 carries ``usable_ranges: [[0, duration]]`` beside
    ``usable_ranges_method: "unmeasured"``, and reading the range would
    make that stale assertion govern which seconds play.  Project 001
    carries exactly that on 17 of 17 clips.
    """
    assessment = clip_analysis.get("assessment")
    if not isinstance(assessment, dict):
        return None
    if assessment.get("usable_ranges_method") != "deterministic_v1":
        return None
    ranges = assessment.get("usable_ranges")
    if not isinstance(ranges, list):
        return None
    out = []
    for entry in ranges:
        if (isinstance(entry, (list, tuple)) and len(entry) == 2
                and all(isinstance(v, (int, float)) for v in entry)):
            out.append((float(entry[0]), float(entry[1])))
    return out


def describe_span(blocks: list, start: float, end: float) -> str:
    """What the vision pass observed while this span was on screen.

    Picked by time overlap, never by index proportion: a span is a stretch
    of seconds and a timed block is another, so the join is the one they
    share.
    """
    best, best_overlap = None, 0.0
    for block in blocks:
        b_start, b_end = block_time_bounds(block)
        if b_start is None:
            continue
        overlap = min(end, b_end) - max(start, b_start)
        if overlap > best_overlap:
            best, best_overlap = block, overlap
    if best is None:
        return ""
    return " ".join(
        str(best.get(key, "")) for key in ("label", "visual", "broll_context")
    ).strip()


def moment_match(preferred_moment: str, description: str) -> float:
    """Share of the moment's words that appear in the description."""
    if not preferred_moment or not description:
        return 0.0
    wanted = {w for w in preferred_moment.lower().split() if w}
    if not wanted:
        return 0.0
    seen = set(description.lower().split())
    return round(len(wanted & seen) / len(wanted), 4)


def index_resolution_seconds(temporal_index: dict):
    """How finely this index can tell two moments apart, in seconds.

    Two span points closer together than one sample of the FASTEST curve
    the index carries are the same point as far as anything downstream can
    measure.  Project 001's documents put a block boundary at the rounded
    clip duration and the catalog puts the real one 3 ms later, which
    yielded a 0.003 s "candidate span" describing nothing.

    This is a resolution read off the measurements, not a bound somebody
    picked; an index carrying no sampled curve gets None and nothing is
    merged.
    """
    rates = []
    for key in ("motion_energy", "optical_flow_direction", "face_presence",
                "color_curves"):
        curve = temporal_index.get(key)
        if isinstance(curve, dict):
            rate = curve.get("sample_rate_hz")
            if isinstance(rate, (int, float)) and rate > 0:
                rates.append(float(rate))
    if not rates:
        return None
    return 1.0 / max(rates)


def span_points(
    clip_analysis: dict, temporal_index: dict, clip_duration: float
) -> list:
    """Where the PICTURE changes: scene boundaries and block bounds.

    Strategy 1 of the old chooser needed >= 2 scene boundaries and got
    them on 2 of project 001's 17 clips, which is why the audio strategy
    ran on almost everything.  Taking the union with the vision pass's own
    time-bounded action windows raises that to **13 of 17** on the same
    footage - the four that stay at one span are 3.6-9.1 s clips, where a
    single window is most of the clip anyway.
    """
    points = {0.0}
    if clip_duration > 0:
        points.add(round(float(clip_duration), 3))
    for boundary in temporal_index.get("scene_boundaries") or []:
        time_point = boundary.get("time") if isinstance(boundary, dict) else None
        if isinstance(time_point, (int, float)):
            points.add(round(float(time_point), 3))
    for block in clip_analysis.get("blocks") or []:
        start, end = block_time_bounds(block)
        if start is None:
            continue
        points.add(round(start, 3))
        points.add(round(end, 3))
    inside = sorted(p for p in points if 0.0 <= p <= clip_duration + 1e-6)

    resolution = index_resolution_seconds(temporal_index)
    if resolution:
        merged = [inside[0]]
        for point in inside[1:]:
            if point - merged[-1] < resolution:
                merged[-1] = point      # keep the later one: the clip's end
            else:
                merged.append(point)
        inside = merged

    return inside if len(inside) >= 2 else [0.0, max(clip_duration, 0.0)]


def fit_to_clip(
    video_in: float, video_out: float,
    target_duration: float, clip_duration: float,
) -> tuple:
    """Clamp a window into the clip while keeping it target_duration long.

    A B-roll clip must be able to fill the slot it covers.  Returning a
    window shorter than the slot leaves a gap on V2 where the A-roll shows
    through mid-cutaway.
    """
    if clip_duration <= 0:
        return 0.0, round(target_duration, 3)

    span = min(target_duration, clip_duration)
    video_in = max(0.0, video_in)
    video_out = video_in + span
    if video_out > clip_duration:
        video_out = clip_duration
        video_in = max(0.0, video_out - span)
    return round(video_in, 3), round(video_out, 3)


def _overlap_fraction(start: float, end: float, ranges) -> float:
    length = end - start
    if length <= 0 or not ranges:
        return 0.0
    covered = 0.0
    for r_start, r_end in ranges:
        covered += max(0.0, min(end, r_end) - max(start, r_start))
    return round(min(1.0, covered / length), 4)


def candidate_windows(
    preferred_moment: str,
    clip_analysis: dict,
    temporal_index: dict,
    clip_duration: float,
    target_duration: float,
) -> list:
    """One row per candidate window, described by `CANDIDATE_LEGEND`.

    This is the form route 2 needs: a model handed these rows plus the
    legend can name a window itself.  Nothing here is filtered or
    re-ranked - every span of the clip is offered.
    """
    blocks = clip_analysis.get("blocks") or []
    usable = measured_usable_ranges(clip_analysis)

    motion = temporal_index.get("motion_energy") or {}
    motion_scale = motion.get("peak_mean_abs_diff") or 1.0
    flow = temporal_index.get("optical_flow_direction") or {}
    flow_values = [
        v.get("magnitude") for v in (flow.get("values") or [])
        if isinstance(v, dict)
    ]
    colour = temporal_index.get("color_curves") or {}
    faces = temporal_index.get("face_presence") or {}

    points = span_points(clip_analysis, temporal_index, clip_duration)
    rows = []
    for i in range(len(points) - 1):
        span_start, span_end = points[i], points[i + 1]
        if span_end - span_start <= 0:
            continue
        video_in, video_out = fit_to_clip(
            span_start, span_start + target_duration,
            target_duration, clip_duration,
        )
        description = describe_span(blocks, span_start, span_end)
        rows.append({
            "span_start": round(span_start, 3),
            "span_end": round(span_end, 3),
            "video_in": video_in,
            "video_out": video_out,
            "describes": description,
            "moment_match": moment_match(preferred_moment, description),
            "motion_mean": _curve_mean(
                motion.get("values"), motion.get("sample_rate_hz"),
                video_in, video_out, motion_scale),
            "motion_peak": _curve_max(
                motion.get("values"), motion.get("sample_rate_hz"),
                video_in, video_out, motion_scale),
            "camera_motion": _curve_mean(
                flow_values, flow.get("sample_rate_hz"),
                video_in, video_out),
            "brightness_mean": _curve_mean(
                colour.get("brightness_values"),
                colour.get("sample_rate_hz"), video_in, video_out),
            "saturation_mean": _curve_mean(
                colour.get("saturation_values"),
                colour.get("sample_rate_hz"), video_in, video_out),
            "face_fraction": _curve_mean(
                faces.get("values"), faces.get("sample_rate_hz"),
                video_in, video_out),
            "usable_overlap": (
                None if usable is None
                else _overlap_fraction(video_in, video_out, usable)
            ),
        })
    return rows


def _legacy_block_window(
    preferred_moment: str, blocks: list,
    clip_duration: float, target_duration: float,
):
    """Where a retired-schema document says the matched block sits.

    Blocks with no time bounds carry nothing but their position in the
    list, so that is all their position can be read from.  A v3 document
    never reaches this.
    """
    untimed = [b for b in blocks if block_time_bounds(b)[0] is None]
    if not untimed or not preferred_moment:
        return None
    wanted = {w for w in preferred_moment.lower().split() if w}
    best_idx, best_overlap = 0, -1
    for i, block in enumerate(untimed):
        text = " ".join([
            str(block.get("label", "")), str(block.get("visual", "")),
        ]).lower()
        overlap = len(wanted & set(text.split()))
        if overlap > best_overlap:
            best_idx, best_overlap = i, overlap
    start = (best_idx / len(untimed)) * clip_duration
    return fit_to_clip(
        start, start + target_duration, target_duration, clip_duration)


def choose_window(
    preferred_moment: str,
    clip_analysis: dict,
    temporal_index: dict,
    clip_duration: float,
    target_duration: float,
) -> WindowChoice:
    """The seconds of this clip that play, and what chose them.

    Order of decision:

    1. A window must sit inside a MEASURED usable range where the clip has
       one.  That is a measurement of the picture, not a preference; a
       clip nothing measured is unfiltered.
    2. The span whose observed description best matches the selection's
       own ``preferred_moment`` wins.  **The model's words choose**; the
       engine only resolves them to seconds, the same shape
       `speech_sequence` uses to turn a passage into a source range.
    3. Ties, and a clip where nothing matched, take the earliest surviving
       span and RECORD that nothing discriminated it.  Inventing a
       preference here - busiest, brightest, most motion - is the taste
       fabrication AGENTS.md §10.5 forbids.

    A document from the retired schema carries blocks with no time bounds,
    so no span can be described and step 2 has nothing to read.  It takes
    the matched block's position in the list, which is all such a document
    supports.
    """
    candidates = candidate_windows(
        preferred_moment, clip_analysis, temporal_index,
        clip_duration, target_duration,
    )
    if not candidates:
        video_in, video_out = fit_to_clip(
            0.0, target_duration, target_duration, clip_duration)
        return WindowChoice(video_in, video_out, "undiscriminated",
                            "the clip yielded no candidate span", [])

    if clip_duration <= target_duration:
        video_in, video_out = fit_to_clip(
            0.0, target_duration, target_duration, clip_duration)
        return WindowChoice(
            video_in, video_out, "no_room",
            f"clip is {clip_duration:.3f}s against a "
            f"{target_duration:.3f}s slot",
            candidates,
        )

    viable = [c for c in candidates if c["usable_overlap"] is None
              or c["usable_overlap"] > 0.0]
    excluded = len(candidates) - len(viable)
    if not viable:
        viable, excluded = candidates, 0

    # A retired-schema document has no timed block, so no candidate span
    # carries a description and matching cannot discriminate. The block's
    # position in the list is all its position can be read from.
    blocks = clip_analysis.get("blocks") or []
    if blocks and not any(block_time_bounds(b)[0] is not None for b in blocks):
        legacy = _legacy_block_window(
            preferred_moment, blocks, clip_duration, target_duration)
        if legacy is not None:
            return WindowChoice(
                legacy[0], legacy[1], "legacy_block_index",
                "blocks carry no time bounds; used the matched block's "
                "position in the list",
                candidates,
            )

    best = max(viable, key=lambda c: (c["moment_match"], -c["span_start"]))

    if len(viable) == 1:
        # One span is not a choice. Saying `moment_match` here would
        # report a decision the clip never offered.
        detail = "the clip yields one candidate span; the window is its head"
        if excluded:
            detail += f"; {excluded} span(s) outside the measured usable ranges"
        return WindowChoice(best["video_in"], best["video_out"],
                            "single_span", detail, candidates)

    if best["moment_match"] > 0.0:
        tied = [c for c in viable if c["moment_match"] == best["moment_match"]]
        basis = "moment_match" if len(tied) == 1 else "moment_match_tie"
        detail = (
            f"matched {best['moment_match']:.2f} of the moment's words "
            f"against {len(viable)} spans"
        )
        if len(tied) > 1:
            detail += (
                f"; {len(tied)} spans tied at that score, earliest taken"
            )
        if excluded:
            detail += f"; {excluded} span(s) outside the measured usable ranges"
        return WindowChoice(best["video_in"], best["video_out"],
                            basis, detail, candidates)

    first = min(viable, key=lambda c: c["span_start"])
    detail = f"{len(candidates)} span(s), none matching the preferred moment"
    if excluded:
        detail += f"; {excluded} outside the measured usable ranges"
    return WindowChoice(first["video_in"], first["video_out"],
                        "undiscriminated", detail, candidates)
