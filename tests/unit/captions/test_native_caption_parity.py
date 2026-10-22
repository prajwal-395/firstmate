"""The recorded caption parity measurement, and what it claims.

The measurement is in `tests/fixtures/native_caption_parity/cards.json`:
the geo-podcast project's own 24 shipped caption cards (of 1278), the ink
IoU of each card as the NATIVE renderer draws it against the artefact
the project shipped, and the same measure for the reference engine
(Remotion's `SubtitleOverlay`, as the repository draws it today).

This test is the reading of that record. It cannot re-measure: the cards
live in the captain's project, which a test may not reach
(`tests/tooling/test_tests_never_reach_real_projects.py`), so the live
half of the measurement is the bench that produced the fixture -
`docs/benchmarks/2026-10-22-infra006-caption-parity.py`.

What the record says, and what these tests hold it to:

  * the native renderer reproduces 24 of 24 sampled shipped captions
    within the ink IoU floor of 0.90, so the caption layer can be given
    to it - which is the lane's claim;
  * the reference engine does NOT: 23 of 24. The one card below the
    floor is a card the reference engine draws differently from the
    artefact that shipped, while the native renderer reproduces it.
    The browser's word-gap rule changed after those cards shipped
    (`remotion-subtitles/src/compositions/SubtitleOverlay/AnimatedWord.tsx`,
    2026-10-17), so today the browser - not the native renderer - is the
    engine that would change a delivered picture;
  * the native renderer is faster per card on the same machine and the
    same cards.

The denominators are separate and are never combined: the population is
1278 cards, the sample is 24.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = (Path(__file__).resolve().parents[2] / "fixtures"
           / "native_caption_parity")
RECORD = FIXTURE / "cards.json"
FLOOR = 0.90


def _record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def test_the_record_is_a_population_a_sample_and_a_floor():
    """The denominators are named, and the sample is smaller."""
    record = _record()
    assert record["population"] == 1278
    assert record["sample"] == 24
    assert record["floor"] == FLOOR
    assert len(record["cards"]) == record["sample"]


def test_the_native_renderer_reproduces_every_sampled_shipped_caption():
    """The lane's claim, on the record: 24 of 24 within the floor."""
    record = _record()
    within = [card for card in record["cards"]
              if card["native_vs_shipped_mean_iou"] >= FLOOR]
    assert len(within) == record["sample"], (
        f"the native renderer reproduces {len(within)} of "
        f"{record['sample']} sampled shipped captions within the floor; "
        f"the lane's claim is {record['sample']} of {record['sample']}")


def test_the_card_below_the_floor_is_the_reference_engine_s_drift():
    """The one card the reference engine cannot reproduce is not the
    native renderer's.

    A card below the floor is either a card the ENGINE draws differently
    (the native renderer would change the delivered picture - the defect
    the gate closes) or a card the ARTEFACT drifted from (both engines
    are equally far from it - nobody changed the delivered picture).
    The record must say which, per card.
    """
    record = _record()
    for card in record["cards"]:
        native = card["native_vs_shipped_mean_iou"]
        reference = card.get("reference_vs_shipped_mean_iou")
        if reference is None:
            continue
        if reference < FLOOR:
            assert native >= FLOOR, (
                f"{card['segment_id']}: the reference engine cannot "
                f"reproduce the shipped artefact ({reference}) and the "
                f"native renderer cannot either ({native}); the record "
                f"must say which engine drifted")
        assert native >= reference, (
            f"{card['segment_id']}: the native renderer reproduces the "
            f"shipped artefact less well than the reference engine does "
            f"({native} against {reference})")


def test_the_native_renderer_is_the_faster_of_the_two():
    """Per card, on the same machine and the same cards."""
    record = _record()
    native = record["seconds"]["native_mean"]
    reference = record["seconds"]["reference_mean"]
    assert native < reference, (
        f"the native renderer drew a card in {native}s against the "
        f"reference engine's {reference}s")
    assert reference / native >= 1.33, (
        f"the native renderer is {reference / native:.2f}x faster per "
        f"card; the lane's claim is at least 1.33x")
