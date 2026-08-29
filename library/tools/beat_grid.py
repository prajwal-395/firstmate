"""The real beat grid, read from what `music_analysis` actually emits.

P4.1. Two steps want to snap to the beat and neither was reaching one.

**`plan_transitions` synthesised its own grid.** `bridge.py` and
`post_bridge.py` built `[i * 60/bpm for i in ...]` from
`music_selection`'s BPM, starting at t=0. No track's first beat lands at
0.000s, so every "beat-snapped" cut was snapped to a grid offset from the
music by the track's lead-in - close enough to look deliberate in code and
wrong on every cut.

**`plan_sfx` read a key that does not exist.** It asked for
`music_analysis["beat_grid"]["bars"]`, and the producer emits no
`beat_grid` at all. `music_pipeline.analyze_music` returns
`{"tempo": {"bpm", "beats", "downbeats"}, "key", "structure", ...}` and
step 2.06 passes it through unchanged. So `bars` was always `[]` and the
SFX beat-snapping never ran either. `docs/PIPELINE_PLAN.md` credited
`plan_sfx` as the one honest consumer of the real grid; it was not.

That is the dominant bug class in this repository - the producer and the
consumer disagree about a name, the reader `.get()`s a default, and the
run reports success over empty data. This module is the single place that
knows the producer's shape, so there is one name to change if it ever
moves, and `tests/test_beat_grid.py` asserts the two ends still agree.

**Time domain.** Beat times come from the music file. The bed is placed at
`timeline_in: 0.0` and at whatever `source_in` the model's chosen section
names (`library/tools/music_section.py`), so timeline time is
`file time - source_in` and a grid used unmapped is wrong by the offset on
every cut. `beat_positions` and `downbeat_positions` therefore take the
selection and RETURN TIMELINE TIME; the offset is read in one place, by
`music_section.section_offset_seconds`.

The selection argument is required rather than defaulted. A default of
"no offset" is the value that is silently wrong, and this module exists
because a producer and a consumer disagreed quietly once already.
`assert_music_offset_is_the_chosen_section` is the other end: it checks
the manifest placed the bed where the grid was mapped for.
"""

from typing import Any, Dict, List, Optional

# Below this many beats a "grid" is noise rather than a rhythm, and
# snapping to it would move cuts for no musical reason.
MIN_USABLE_BEATS = 8


def beat_positions(music_analysis: Optional[Dict[str, Any]],
                   music_selection: Optional[Dict[str, Any]]) -> List[float]:
    """Every detected beat, in TIMELINE seconds, ascending.

    Empty when the analysis is absent, unavailable or too sparse to be a
    rhythm. Empty means "do not snap", which is what every caller already
    does with an empty grid.

    `music_selection` is required: it carries the chosen section, and a
    grid read without it is off by that section's offset on every beat.
    """
    return _times(music_analysis, "beats", music_selection)


def downbeat_positions(music_analysis: Optional[Dict[str, Any]],
                       music_selection: Optional[Dict[str, Any]]) -> List[float]:
    """Bar starts, in TIMELINE seconds, ascending.

    The stronger grid: a cut on a downbeat reads as intentional where a
    cut on any beat can read as busy. `plan_sfx` wants these - it was
    asking for `beat_grid.bars`, which is what a bar start is.
    """
    return _times(music_analysis, "downbeats", music_selection)


def _times(music_analysis: Optional[Dict[str, Any]], key: str,
           music_selection: Optional[Dict[str, Any]]) -> List[float]:
    from library.tools.music_section import section_offset_seconds

    if not music_analysis or not isinstance(music_analysis, dict):
        return []
    # A failed or skipped analysis says so; do not snap to nothing.
    if music_analysis.get("available") is False:
        return []

    offset = section_offset_seconds(music_selection)
    tempo = music_analysis.get("tempo")
    if not isinstance(tempo, dict):
        return []

    raw = tempo.get(key)
    if not isinstance(raw, list):
        return []

    out = []
    for value in raw:
        try:
            t = float(value) - offset
        except (TypeError, ValueError):
            continue
        # A beat before the chosen section starts is not in the edit.
        if t >= 0:
            out.append(round(t, 4))

    out.sort()
    if len(out) < MIN_USABLE_BEATS:
        return []
    return out


def bpm(music_analysis: Optional[Dict[str, Any]]) -> Optional[float]:
    """Detected BPM, from the same place the beats come from.

    `step_2_06`'s own log line reads `analysis.get('bpm')`, which is not
    where it lives, so it has printed `BPM=?` on every run.
    """
    if not music_analysis or not isinstance(music_analysis, dict):
        return None
    tempo = music_analysis.get("tempo")
    if not isinstance(tempo, dict):
        return None
    try:
        value = float(tempo.get("bpm"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def assert_music_offset_is_the_chosen_section(
        manifest: Dict[str, Any],
        music_selection: Optional[Dict[str, Any]]) -> None:
    """The bed was placed at the offset the grid was mapped for.

    `beat_positions` subtracts the chosen section's `source_in` from every
    beat, so the two ends have to agree. Raises rather than warning: a
    snapped cut silently off by the music's offset looks exactly like a
    snapped cut that is correct.

    It also holds the other half of the invariant - the bed starts at
    timeline 0 - because that is what makes `file time - source_in` a
    timeline time at all.
    """
    from library.tools.music_section import section_offset_seconds

    expected = section_offset_seconds(music_selection)
    tracks = manifest.get("tracks") or {}
    clips = (tracks.get("A2") or {}).get("clips") or []
    for clip in clips:
        source_in = float(clip.get("source_in", 0.0) or 0.0)
        timeline_in = float(clip.get("timeline_in", 0.0) or 0.0)
        if abs(timeline_in) > 1e-6:
            raise ValueError(
                f"Music is placed at timeline_in {timeline_in}, not 0. The "
                f"beat grid is mapped as file time minus the chosen "
                f"section, which is a timeline time only while the bed "
                f"starts the timeline."
            )
        if abs(source_in - expected) > 1e-6:
            raise ValueError(
                f"Music is placed with source_in {source_in} but the "
                f"selection's chosen section starts at {expected}. The beat "
                f"grid was mapped through {expected}, so every beat-snapped "
                f"cut and SFX hit would be off by the difference. Place the "
                f"bed at the section the selection declares."
            )
