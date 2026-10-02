"""Two candidates that are the same recording, established from measurements.

On project 001 the catalogue held seven files.  Four survived the duration
check, and **two of those four were the same recording** - "Sickick -
Infected (lyrics)" as an ``.mp3`` in the shared library and
``Sickick - Infected _lyrics_.wav`` in the project's own ``music/``.  The
model was told it had four things to choose between and it had three.  A
third of the choice set was a copy.

The filenames do not say so.  ``Sickick - Infected (lyrics)`` and
``Sickick - Infected _lyrics_`` differ by two characters that a string
comparison has no reason to forgive, and the same two characters separate
them from ``Sickick- _Infected_ _Instrumental_``, which is a genuinely
different recording that must NOT be folded in.  So the test is the
measurements, which #296 already computes and pays for.

What separates a re-encode from a different recording
-----------------------------------------------------
Measured on this repository's own files, 2026-08-28.  Two 001 tracks were
re-encoded from their WAV masters to mp3 128k, mp3 320k, opus 96k and aac
128k, and every encode was measured against its master:

=====================  =======================  =====================
                       worst delta vs master    what it means
=====================  =======================  =====================
integrated_lufs        0.45 LU  (mp3 128k)      lossy coding moves it
loudness_range_lu      0.10 LU
rms_spread_db          0.35 dB
window envelope        0.50 dB, worst bucket
speech_band_ratio_db   0.05 dB
true_peak_dbtp         0.44 dB                  see below
=====================  =======================  =====================

001's real pair - two independent encodes nobody in this repository made -
agrees: identical integrated loudness, identical LRA, identical envelope
to 0.1 dB, and 0.44 dB apart on ``rms_spread_db``.

:data:`DB_TOLERANCE` is 1.0 dB, twice the worst of those.  The nearest
non-duplicate pair in 001 is the lyrics track against the instrumental of
the SAME SONG, and they are 19.6 dB apart in the first envelope bucket and
1.39 LU apart in integrated loudness - so the margin is more than an order
of magnitude, on the hardest real pair available.

``true_peak_dbtp`` is deliberately NOT compared.  It is the one figure
lossy coding reliably moves - a codec's reconstruction overshoots - and at
0.44 dB it is the noisiest column measured while carrying the least
information about whether two files are the same performance.

Nothing is deleted
------------------
A duplicate is MARKED, not dropped.  The defect was that the model could
not tell two rows were one recording, and saying so fixes it; removing a
row would have the pipeline deciding which encode the captain gets to
choose from, on a measurement inference, with no way to see what went.
The representative is the first in catalogue order - a position, not a
verdict about quality.

This module compares.  It does not rank, and it does not prefer lossless
to lossy: which encode to use is the captain's, and both are on the table.

``tests/unit/audio/test_music_duplicates.py``.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**Two candidates that are the same recording are established from the MEASUREMENTS, never the filename.**
`library/tools/music_duplicates.py`. [why](docs/RULE_EVIDENCE.md#a-third-of-the-choice-set-was-a-copy)
- The tolerance is measured, not picked: 1.0 dB.
- `true_peak_dbtp` is NOT compared, and `DECLINED_SIGNALS` says why: lossy coding moves it most and it says least.
- **A duplicate is MARKED, not dropped.**
- `tests/unit/audio/test_music_duplicates.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# Every measured column compared, in dB or LU.  One tolerance covers them
# all because they are all levels: see the table above.
COMPARED_DB_FIELDS = (
    "integrated_lufs",
    "loudness_range_lu",
    "rms_spread_db",
    "window_spread_db",
    "speech_band_ratio_db",
)

# The per-bucket envelope over the played window.  Compared as the worst
# single bucket, because a duplicate has to match everywhere and a
# different recording usually diverges in one place first - on 001 the
# instrumental's silent intro.
ENVELOPE_FIELD = "window_envelope_dbfs"

# Twice the worst re-encode difference measured (0.50 dB).
DB_TOLERANCE = 1.0

# Two encodes of one recording differ in length only by container
# padding.  Same figure the selection contract uses for ffprobe rounding.
DURATION_TOLERANCE_SECONDS = 0.5

# Measured and left out, with the reason, so the next person weighing one
# is arguing with a decision rather than rediscovering it.
DECLINED_SIGNALS = {
    "true_peak_dbtp": (
        "the noisiest column across re-encodes (0.44 dB) and the least "
        "informative about whether two files are the same performance - a "
        "lossy codec's reconstruction overshoots the master's peak."
    ),
    "the filename": (
        "'Sickick - Infected (lyrics)' and 'Sickick - Infected _lyrics_' "
        "are the pair this module exists for, and they are no closer to "
        "each other by string distance than either is to "
        "'Sickick- _Infected_ _Instrumental_', which is a different "
        "recording. A name is a claim; a measurement is not."
    ),
    "an audio fingerprint (chromaprint/AcoustID)": (
        "it would answer this question better and it is another binary "
        "dependency, another network service for the lookup, and a second "
        "way of knowing where one already exists. Revisit it if two "
        "different recordings are ever found inside DB_TOLERANCE."
    ),
    "file size or byte digest": (
        "identical only for identical files. The pair on 001 is 5.5 MB of "
        "mp3 against 38.8 MB of wav."
    ),
}


def _both_measured(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return bool(a.get("measured")) and bool(b.get("measured"))


def _delta(a: Dict[str, Any], b: Dict[str, Any],
           field: str) -> Optional[float]:
    left, right = a.get(field), b.get(field)
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return abs(float(left) - float(right))


def _envelope_delta(a: Dict[str, Any],
                    b: Dict[str, Any]) -> Optional[float]:
    left, right = a.get(ENVELOPE_FIELD), b.get(ENVELOPE_FIELD)
    if not isinstance(left, list) or not isinstance(right, list):
        return None
    if len(left) != len(right) or not left:
        return None
    worst = 0.0
    for one, two in zip(left, right):
        if not isinstance(one, (int, float)) or not isinstance(two, (int, float)):
            return None
        worst = max(worst, abs(float(one) - float(two)))
    return worst


def same_recording(a: Dict[str, Any],
                   b: Dict[str, Any]) -> Tuple[bool, Dict[str, float]]:
    """Whether two measured candidates are the same recording, and by how much.

    Returns ``(verdict, deltas)``.  The verdict is False whenever a
    comparison could not be made - an unmeasured candidate, a missing
    column, envelopes of different lengths.  An absent measurement is not
    evidence of sameness.
    """
    deltas: Dict[str, float] = {}
    if not _both_measured(a, b):
        return False, deltas

    duration = _delta(a, b, "duration_seconds")
    if duration is None or duration > DURATION_TOLERANCE_SECONDS:
        if duration is not None:
            deltas["duration_seconds"] = round(duration, 3)
        return False, deltas
    deltas["duration_seconds"] = round(duration, 3)

    for field in COMPARED_DB_FIELDS:
        delta = _delta(a, b, field)
        if delta is None:
            return False, deltas
        deltas[field] = round(delta, 3)
        if delta > DB_TOLERANCE:
            return False, deltas

    envelope = _envelope_delta(a, b)
    if envelope is None:
        return False, deltas
    deltas[ENVELOPE_FIELD] = round(envelope, 3)
    if envelope > DB_TOLERANCE:
        return False, deltas

    return True, deltas


def mark_duplicates(candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every candidate, with the duplicates among them named.

    Returns new dicts; the input is not mutated.  A candidate that is a
    re-encode of an earlier one in the list gains ``duplicate_of`` (the
    representative's ``audio_path``), ``duplicate_of_title`` and
    ``duplicate_deltas``.  The representative gains ``duplicate_count``.
    Nothing is removed.
    """
    marked = [dict(candidate) for candidate in candidates]
    representatives: List[int] = []

    for index, candidate in enumerate(marked):
        match = None
        for rep_index in representatives:
            verdict, deltas = same_recording(marked[rep_index], candidate)
            if verdict:
                match = (rep_index, deltas)
                break
        if match is None:
            representatives.append(index)
            continue
        rep_index, deltas = match
        representative = marked[rep_index]
        candidate["duplicate_of"] = representative.get("audio_path", "")
        candidate["duplicate_of_title"] = representative.get("title", "")
        candidate["duplicate_deltas"] = deltas
        representative["duplicate_count"] = (
            int(representative.get("duplicate_count") or 0) + 1
        )

    return marked


def duplicate_groups(candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per group of two or more candidates that are one recording."""
    groups: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        parent = candidate.get("duplicate_of")
        if not parent:
            continue
        group = groups.setdefault(parent, {
            "representative": parent,
            "representative_title": candidate.get("duplicate_of_title", ""),
            "also": [],
            "worst_delta_db": 0.0,
        })
        group["also"].append(candidate.get("audio_path", ""))
        deltas = candidate.get("duplicate_deltas") or {}
        worst = max(
            (v for k, v in deltas.items()
             if k != "duration_seconds" and isinstance(v, (int, float))),
            default=0.0,
        )
        group["worst_delta_db"] = round(
            max(group["worst_delta_db"], float(worst)), 3)
    return list(groups.values())


def distinct_count(candidates: Sequence[Dict[str, Any]]) -> int:
    """How many genuinely different recordings the catalogue offers."""
    return sum(1 for c in candidates if not c.get("duplicate_of"))


def summarise(candidates: Sequence[Dict[str, Any]]) -> str:
    """One line the bridge prints, so a copy is visible in the run log."""
    groups = duplicate_groups(candidates)
    if not groups:
        return ""
    parts = []
    for group in groups:
        parts.append(
            f"{group['representative_title']!r} appears "
            f"{len(group['also']) + 1} times "
            f"(worst measured difference {group['worst_delta_db']} dB)"
        )
    return "; ".join(parts)
