"""Which part of the track plays, and who decides it.

Captain's ruling of 2026-08-28, verbatim, flagged as unsettled: *"use the
sections from the music you find is best (this is another creative
decision thing we need to settle)"*.

A track is two to five minutes and the video is sixty seconds.  Most of
the track is never heard, and **which sixty seconds a viewer gets is a
creative decision** - a track whose first minute is a bare, near-silent
intro and whose second minute is the settled body is two completely
different beds depending on where it starts.  On 001's Sickick
instrumental those two minutes measure 31.1 dB of spread against 11.2 dB.

What was wrong
--------------
Nothing decided it, and nothing could.  ``compile_manifest`` wrote
``"source_in": 0.0`` as a literal, so every track played from its head
whatever the model said.  Step 2.04's frozen handoff has asked for
``splices`` - "a 3-minute track is never used in full; pick the sections
that fit particular moments" - since it was written, and the model has
been answering; ``splices`` reached ``pipeline_data.json`` and then
nothing.  That is AGENTS.md 10.2 exactly: a capability is only real where
the renderer reads it, and this one was not read anywhere.

So the decision now travels.  The model names a ``section`` on its
selection, ``compile_manifest`` places the bed at that offset, and the
beat grid is mapped through it.

There is no heuristic here, and there must not be one
-----------------------------------------------------
This module resolves and validates a section.  It does not choose one,
score one, or prefer one.  A "best section" rule - pick the flattest
window, pick the loudest, pick the one whose spread is closest to the
speech's - would be a hardcoded creative value wearing a new hat, which is
the defect class this week has been clearing (AGENTS.md 10.5).  What the
model gets instead is the measurement: ``music_measurement.track_sections``
describes every playable span of the track by level and spread, and the
model reads it and says where to start.

**A selection that declares no section plays from the head of the file.**
That is the absence of a decision, not a decision - the same reading
``transition_vocabulary.CUT_TYPES`` and ``house_look.NEUTRAL_CDL`` get:
undecorated, not chosen.  :func:`resolve_section` says which of the two
happened in :attr:`MusicSection.declared`, so a run can tell them apart.

The clock
---------
Beat times from ``music_analysis`` are measured in the MUSIC FILE's clock.
Placing the bed at a non-zero ``source_in`` puts timeline time
``file time - source_in``, so every beat-snapped cut moves by the offset
unless the grid is mapped.  ``library/tools/beat_grid.py`` takes the
selection for exactly this reason, and it is why the offset lives in one
function here rather than being recomputed at each of its three readers.

What is NOT supported, and would need more than the measurements carry
----------------------------------------------------------------------
See :data:`UNSUPPORTED_BY_THE_MEASUREMENTS`.  The honest limit is that a
non-zero section is described by its LEVEL and its SPREAD and not by its
SHAPE: ``window_envelope_dbfs`` is the twelve-bucket curve of the section
starting at 0 and there is no such curve for the others.  Say so rather
than inventing a rule.

``tests/test_music_section.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# The key the model writes its decision under.  Asked for through step
# 2.04's manifest `interface.llm_outputs`, which is what builds the
# injected output schema - handoff.md is under a captain freeze and names
# no such key.
SECTION_KEY = "section"

# Where the bed starts when nothing chose.  The head of the file: the
# absence of a decision, not one.
UNDECLARED_SOURCE_IN = 0.0

# Slack for float and ffprobe rounding when asking whether a section fits.
FIT_SLACK_SECONDS = 0.5

UNSUPPORTED_BY_THE_MEASUREMENTS = {
    "the shape of a non-zero section": (
        "music_measurement emits one twelve-bucket envelope, over the "
        "section starting at 0. track_sections gives every other section "
        "its mean level and its spread but not its curve, so a model can "
        "see that section 60-120 is 20 dB steadier and cannot see whether "
        "it rises or falls across the minute. Twelve buckets per section "
        "would be ~120 numbers per candidate, which AGENTS.md 10.1 rules "
        "out. Nothing here fills the gap with a rule."
    ),
    "where the musical phrases are": (
        "a section that starts mid-bar reads as a mistake, and nothing in "
        "the selection step knows the bar lines - step 2.06 measures tempo "
        "AFTER the choice. The grid is available downstream and no step "
        "currently snaps the bed's start to it."
    ),
    "whether a section has vocals": (
        "speech_band_ratio_db is measured over the whole track, not per "
        "section, so a track with a vocal middle-eight looks the same as "
        "one without."
    ),
}


class MusicSectionError(ValueError):
    """A declared section cannot be played."""


@dataclass(frozen=True)
class MusicSection:
    """Where the bed starts, and whether anybody decided it."""

    source_in: float
    declared: bool
    reason: str = ""
    why: str = ""

    def source_out(self, timeline_duration: float) -> float:
        return round(self.source_in + float(timeline_duration), 3)


def undeclared_section(reason: str) -> MusicSection:
    return MusicSection(
        source_in=UNDECLARED_SOURCE_IN, declared=False, reason=reason)


def declared_section(source_in: float, why: str = "") -> MusicSection:
    return MusicSection(source_in=round(float(source_in), 3),
                        declared=True, why=why)


def read_section(music_selection: Optional[Dict[str, Any]]) -> MusicSection:
    """The section on a selection, or the statement that none was declared.

    Malformed RAISES.  A section is a number the placement depends on; a
    string where a number belongs is a mistake to report, not one to
    quietly read as zero.
    """
    if not isinstance(music_selection, dict):
        return undeclared_section(
            "there is no music selection to read a section from")

    declared = music_selection.get(SECTION_KEY)
    if declared is None:
        return undeclared_section(
            "the selection declares no section, so the bed plays from the "
            "head of the file - the absence of a decision, not one")

    if not isinstance(declared, dict):
        raise MusicSectionError(
            f"music_selection.{SECTION_KEY} must be an object carrying "
            f"source_in (seconds into the track where the bed starts) and "
            f"why; got {type(declared).__name__}"
        )

    raw = declared.get("source_in")
    if raw is None:
        raise MusicSectionError(
            f"music_selection.{SECTION_KEY} declares no source_in. Name the "
            f"second of the track the bed starts at, or omit the section "
            f"entirely to play from the head."
        )
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise MusicSectionError(
            f"music_selection.{SECTION_KEY}.source_in must be a number of "
            f"seconds; got {raw!r}"
        )
    if raw < 0:
        raise MusicSectionError(
            f"music_selection.{SECTION_KEY}.source_in is {raw}; a section "
            f"cannot start before the file does."
        )

    return declared_section(raw, str(declared.get("why") or "").strip())


def section_offset_seconds(music_selection: Optional[Dict[str, Any]]) -> float:
    """The one reading of "how far into the file the bed starts".

    Every consumer that has to move between file time and timeline time
    calls this - the beat grid and the manifest compiler - so there is one
    place the answer comes from.  A malformed section raises here too.
    """
    return read_section(music_selection).source_in


def fits(section: MusicSection, track_duration: float,
         play_duration: float) -> bool:
    """Whether ``play_duration`` seconds are left in the file from here."""
    if not track_duration or track_duration <= 0:
        return True
    return (section.source_in + float(play_duration)
            <= float(track_duration) + FIT_SLACK_SECONDS)


def validate_section(music_selection: Optional[Dict[str, Any]],
                     track_duration: float,
                     play_duration: float) -> List[str]:
    """Everything wrong with a declared section, as sentences.

    Empty means playable - including the case where none was declared.
    """
    try:
        section = read_section(music_selection)
    except MusicSectionError as exc:
        return [str(exc)]

    if not section.declared:
        return []

    errors: List[str] = []
    if track_duration and section.source_in >= float(track_duration):
        errors.append(
            f"music_selection.{SECTION_KEY}.source_in is "
            f"{section.source_in:.1f}s but the track is only "
            f"{float(track_duration):.1f}s long - the bed would start after "
            f"the file ends."
        )
    elif not fits(section, track_duration, play_duration):
        errors.append(
            f"music_selection.{SECTION_KEY}.source_in is "
            f"{section.source_in:.1f}s, which leaves "
            f"{float(track_duration) - section.source_in:.1f}s of the track "
            f"against a {float(play_duration):.0f}s edit. The bed is placed "
            f"once and run to the end of the timeline, so the last "
            f"{float(play_duration) - (float(track_duration) - section.source_in):.1f}s "
            f"would be silent. Choose a section that starts no later than "
            f"{float(track_duration) - float(play_duration):.1f}s."
        )
    return errors


def resolve_section(music_selection: Optional[Dict[str, Any]],
                    track_duration: float,
                    timeline_duration: float) -> MusicSection:
    """The section to place, or a refusal saying why it cannot be played.

    Called by ``compile_manifest`` against the timeline's REAL length,
    which is the first moment that length is known.  It raises rather than
    sliding the section back to fit: moving the start is deciding which
    part plays, and that decision is not this module's.
    """
    errors = validate_section(music_selection, track_duration,
                              timeline_duration)
    if errors:
        raise MusicSectionError(
            "The chosen music section cannot be placed:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\n  Re-run step 2.04 (music_selection) to choose a section "
              "that covers the edit, or drop the section to play from the "
              "head of the file."
        )
    return read_section(music_selection)


def describe(section: MusicSection) -> str:
    """One line for the run log, so the placement is visible."""
    if not section.declared:
        return f"music section: from 0.0s - {section.reason}"
    return (
        f"music section: from {section.source_in:.1f}s"
        + (f" - {section.why}" if section.why else "")
    )
