"""D11: nothing checked `grade_assessment` against `color_correction`.

On the 2026-09-03 run of project 001 the colourist's assessment claimed a
saturation of 1.10 on *every* placed clip while two entries carried no
`saturation` term and shipped at neutral. Both halves were stored, both
were read by a human, and nothing compared them.

The check lives in `library/tools/color_correction.py` - the one module
that owns both spellings (`FIELD` and `ASSESSMENT_FIELD`) - and its
finding is recorded on `correction_basis.assessment_mismatches`, beside
the claim, so the run summary route (`step_exporter`) reads it where the
human reads the assessment. It REPORTS, never refuses: a prose claim is
a model judgement, and a gate that fails correct output is no coverage
(AGENTS.md 10.4).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import color_correction as cc

# The D11 shape: nine placed clips, seven corrected with a saturation
# term, two left without one. Expected values below are derived from the
# expressions the code uses - the Correction objects and the term table -
# never copied literals.
CUT = [f"clip_{n:03d}" for n in (11, 13, 17, 2, 8, 5, 1, 14, 6)]
SATURATED = CUT[:7]
UNSATURATED = CUT[7:]
CLAIMED_SATURATION = 1.10


def _corrections():
    entries = [
        {"clip_id": clip_id, "saturation": CLAIMED_SATURATION,
         "why": "a gentle lift across the cut"}
        for clip_id in SATURATED
    ]
    # The two D11 clips: corrected on another axis, carrying no
    # saturation term, so they ship at the term's neutral.
    entries.append({"clip_id": UNSATURATED[0], "exposure_stops": 0.3,
                    "why": "same lot, same light, a third of a stop adrift"})
    entries.append({"clip_id": UNSATURATED[1], "exposure_stops": 0.2,
                    "why": "sits under the other daylight exteriors"})
    corrections, dropped = cc.read_corrections(entries, CUT)
    assert not dropped
    return corrections


def test_a_universal_claim_the_cdl_does_not_carry_is_named():
    corrections = _corrections()
    assessment = (
        f"I applied one global saturation of {CLAIMED_SATURATION} "
        f"to every placed clip"
    )
    mismatches = cc.check_assessment(assessment, corrections, CUT)
    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch["term"] == "saturation"
    assert mismatch["claimed"] == corrections[0].saturation
    # Derived from the code's own structures, not literals: every cut
    # clip whose correction declares no saturation term ships neutral.
    by_clip = {c.clip_id: c for c in corrections}
    expected = [c for c in CUT
                if by_clip.get(c) is None
                or "saturation" not in by_clip[c].declared]
    assert sorted(mismatch["clips"]) == sorted(expected)
    assert sorted(expected) == sorted(UNSATURATED)
    neutral = cc.TERMS_BY_KEY["saturation"].neutral
    for clip_id in UNSATURATED:
        assert f"{clip_id}" in mismatch["detail"]
    assert f"{neutral}" in mismatch["detail"]


def test_a_universal_claim_the_cdl_carries_is_quiet():
    entries = [
        {"clip_id": clip_id, "saturation": CLAIMED_SATURATION,
         "why": "a gentle lift across the cut"}
        for clip_id in CUT
    ]
    corrections, dropped = cc.read_corrections(entries, CUT)
    assert not dropped
    assert cc.check_assessment(
        f"saturation {CLAIMED_SATURATION} across every clip in the cut",
        corrections, CUT) == []


def test_prose_without_a_checkable_claim_is_not_read():
    corrections = _corrections()
    # The three assessments the existing fixtures already carry: no
    # universal scope plus term plus number together, so no claim.
    assert cc.check_assessment(
        "one stop and a third across the last cut", corrections, CUT) == []
    assert cc.check_assessment(
        "these three sit together already", corrections, CUT) == []
    assert cc.check_assessment("one clip under", corrections, CUT) == []
    assert cc.check_assessment("", corrections, CUT) == []
    # Complex prose - two terms or two numbers - is declined: pairing
    # them would invent a reading.
    assert cc.check_assessment(
        f"saturation {CLAIMED_SATURATION} on every clip and exposure "
        f"+0.5 on the interior",
        corrections, CUT) == []
    assert cc.check_assessment(
        "every clip got saturation 1.10 except the two at 1.0",
        corrections, CUT) == []


def test_define_color_grade_threads_the_cut_clips_into_the_record():
    from library.steps.step_5_01_color_grade.grade import define_color_grade

    corrections = _corrections()
    assessment = (
        f"I applied one global saturation of {CLAIMED_SATURATION} "
        f"to every placed clip"
    )
    rows = [{"clip_id": clip_id, "luma": 120.0,
             "luma_method": "test", "luma_samples": 10} for clip_id in CUT]
    spec = define_color_grade(
        {"entries": [{"track": "V1", "clip_id": clip_id,
                      "entry_id": clip_id, "source_file": ""}
                     for clip_id in CUT]},
        measured_clips=rows, corrections=corrections, decided=True,
        assessment=assessment)["color_grade_spec"]
    assert sorted(
        spec["correction_basis"]["assessment_mismatches"][0]["clips"]
    ) == sorted(UNSATURATED)
