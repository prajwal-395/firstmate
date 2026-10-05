"""A reel variant and an edit variant agree: the two systems are one.

The defect this closes
---------------------
A variant of a reel cannot be expressed as a variant of the
corresponding edit region - the two systems do not meet. The reel
variant mechanism (`variants.py`) is keyed by `reel_number`, branches
`variant/r<NN>-<slug>`, and knows nothing about the edit graph. The
edit alternatives mechanism (`edit_variants.py`) forks the graph at a
named region and knows nothing about reels. A captain who asks for a
different cutaway on reel 9 and a different cutaway on the opening of
the main edit is asking for the same thing twice, and the engine cannot
tell.

The fix makes `edit_variants.py` the general mechanism: a reel variant
is an edit variant of the region the reel was cut from, plus a reel
build. `region_for_moment` maps a reel's moment to its edit region,
`produce_reel_variant` produces the fork through the general path, and
`reel_variant_agrees_with_edit_variant` proves a reel variant and an
edit variant describe the same difference.

Each test names a defect the unification closes: a reel variant that
cannot be expressed as an edit variant (the two systems do not meet), a
reel variant and an edit variant that disagree (the comparison is
dishonest), and a reel variant that differs outside its declared
dimension (which the general validation must refuse, as it refuses any
other fork).
"""
from __future__ import annotations

from pathlib import Path

from library.tools import edit_graph
from library.tools.versions import edit_variants
from library.tools.versions.edit_variants import (
    AlternativeSpec,
    ReelVariantSpec,
)

REPO = Path(__file__).resolve().parents[2]


def _block(position, block_type="speech", clip_id="clip_001",
           source=(0.0, 5.0), timeline=(0.0, 5.0)):
    return {
        "position": position,
        "block_type": block_type,
        "clip_id": clip_id,
        "source_start": source[0],
        "source_end": source[1],
        "timeline_start": timeline[0],
        "timeline_end": timeline[1],
        "word_timestamps": [{"word": "a", "source_start": source[0],
                             "source_end": source[1]}],
        "alignment_method": "search",
        "music_behavior": "background",
    }


def _small_edit_state() -> dict:
    """A small edit's recorded state: three blocks, three passages, and
    the decisions that hang off them.

    Blocks 1-3 are speech cut from clip_001; a B-roll cover sits on
    block 3. The three passages are the "opening" a reel is cut from.
    """
    return {
        "capability_outputs": {
            "spine.mesh": {"audio_spine": {"structure": [
                _block(1, source=(5.0, 10.0), timeline=(0.0, 5.0)),
                _block(2, source=(15.0, 20.0), timeline=(5.0, 10.0)),
                _block(3, source=(25.0, 30.0), timeline=(10.0, 15.0)),
            ]}},
            "speech.enrich": {"speech_sequence": {"body_sequence": [
                {"position": 1, "clip_id": "clip_001",
                 "source_start": 5.0, "source_end": 10.0,
                 "start_time": 0.0, "end_time": 5.0, "text": "hello"},
                {"position": 2, "clip_id": "clip_001",
                 "source_start": 15.0, "source_end": 20.0,
                 "start_time": 5.0, "end_time": 10.0, "text": "world"},
                {"position": 3, "clip_id": "clip_001",
                 "source_start": 25.0, "source_end": 30.0,
                 "start_time": 10.0, "end_time": 15.0, "text": "again"},
            ]}},
            "aroll.assign": {"a_roll_assignments": {"a_roll_assignments": [
                {"spine_block_position": 1, "clip_id": "clip_001",
                 "source_start": 5.0, "source_end": 10.0,
                 "timeline_start": 0.0, "timeline_end": 5.0},
                {"spine_block_position": 2, "clip_id": "clip_001",
                 "source_start": 15.0, "source_end": 20.0,
                 "timeline_start": 5.0, "timeline_end": 10.0},
                {"spine_block_position": 3, "clip_id": "clip_001",
                 "source_start": 25.0, "source_end": 30.0,
                 "timeline_start": 10.0, "timeline_end": 15.0},
            ]}},
            "broll.resolve": {"b_roll_assignments": {"b_roll_assignments": [
                {"spine_block_position": 3, "clip_id": "clip_002",
                 "video_in": 0.0, "video_out": 2.0,
                 "timeline_start": 10.0, "timeline_end": 12.0},
            ]}},
        }
    }


def _intent_model() -> dict:
    """A small intent model: three beats, one claim, one key moment."""
    return {
        "beats": [
            {"beat_id": "beat:1", "purpose": "setup", "span": {
                "clip_id": "clip_001", "source_start": 5.0,
                "source_end": 10.0}},
            {"beat_id": "beat:2", "purpose": "development", "span": {
                "clip_id": "clip_001", "source_start": 15.0,
                "source_end": 20.0}},
            {"beat_id": "beat:3", "purpose": "payoff", "span": {
                "clip_id": "clip_001", "source_start": 25.0,
                "source_end": 30.0}},
        ],
        "claims": [
            {"claim_id": "claim:1", "statement": "the opening works",
             "supports": []},
        ],
        "key_moments": [],
        "pacing_curve": [],
    }


def _graph() -> dict:
    return edit_graph.build_graph(_small_edit_state())


# ── A reel variant is an edit variant of its region ──────────────────


def test_a_reel_variant_is_produced_through_the_general_mechanism():
    """The defect: a variant of a reel cannot be expressed as a variant
    of the corresponding edit region - the two systems do not meet. The
    fix makes `produce_reel_variant` produce the reel variant's fork
    through the same path `produce_alternatives` uses, so a reel variant
    IS an edit variant of its region.
    """
    graph = _graph()
    region = edit_variants.region_for_moment(graph, 0.0, 15.0)
    assert "broll:3" in region

    spec = ReelVariantSpec(
        reel_number=9,
        base_timeline_name="Reel 09 - The Opening",
        suffix=" (reaction-cutaway)",
        region=region,
        dimension=edit_variants.BROLL_COVER,
        value="clip_003",
        trade_off="a tighter cover",
    )
    reel_variant = edit_variants.produce_reel_variant(
        graph, _intent_model(), spec)

    # The reel variant is an edit variant: the same shape, the same
    # fork, the same validation path.
    assert reel_variant["spec"]["dimension"] == edit_variants.BROLL_COVER
    assert reel_variant["spec"]["region"] == list(region)
    assert reel_variant["spec"]["value"] == "clip_003"

    # The fork itself is the edit variant's fork: the B-roll cover on
    # block 3 now carries the variant's clip.
    forked = reel_variant["graph"]
    for node in forked["nodes"]:
        if node["node_id"] == "broll:3":
            assert node["payload"]["clip_id"] == "clip_003"


def test_a_reel_variant_and_an_edit_variant_agree():
    """The defect: a reel variant and an edit variant of the same
    region are unrelated systems, so nothing proves they describe the
    same difference. The fix makes `reel_variant_agrees_with_edit_variant`
    check that a reel variant and an edit variant declare the same
    region, dimension and value - the same fork.
    """
    graph = _graph()
    region = edit_variants.region_for_moment(graph, 0.0, 15.0)

    reel_spec = ReelVariantSpec(
        reel_number=9,
        base_timeline_name="Reel 09 - The Opening",
        suffix=" (reaction-cutaway)",
        region=region,
        dimension=edit_variants.BROLL_COVER,
        value="clip_003",
        trade_off="a tighter cover",
    )
    reel_variant = edit_variants.produce_reel_variant(
        graph, _intent_model(), reel_spec)

    edit_alternative = edit_variants.produce_alternatives(
        graph, _intent_model(), [
            AlternativeSpec(
                region=region,
                dimension=edit_variants.BROLL_COVER,
                value="clip_003",
                trade_off="a tighter cover",
            ),
        ])[0]

    # They agree: the same region, the same dimension, the same value.
    problems = edit_variants.reel_variant_agrees_with_edit_variant(
        reel_variant, edit_alternative)
    assert problems == []

    # And they produce the same fork: the same nodes, the same payloads.
    reel_nodes = {n["node_id"]: n for n in reel_variant["graph"]["nodes"]}
    edit_nodes = {n["node_id"]: n for n in edit_alternative["graph"]["nodes"]}
    assert reel_nodes.keys() == edit_nodes.keys()
    for node_id in reel_nodes:
        assert reel_nodes[node_id]["payload"] == edit_nodes[node_id]["payload"]


def test_a_reel_variant_and_an_edit_variant_that_disagree_are_refused():
    """The defect: a reel variant and an edit variant that declare
    different regions, dimensions or values are different forks, and
    reporting them as agreeing would make the comparison dishonest. The
    fix makes `reel_variant_agrees_with_edit_variant` refuse the
    disagreement, by name.
    """
    graph = _graph()
    region = edit_variants.region_for_moment(graph, 0.0, 15.0)

    reel_spec = ReelVariantSpec(
        reel_number=9,
        base_timeline_name="Reel 09 - The Opening",
        suffix=" (reaction-cutaway)",
        region=region,
        dimension=edit_variants.BROLL_COVER,
        value="clip_003",
        trade_off="a tighter cover",
    )
    reel_variant = edit_variants.produce_reel_variant(
        graph, _intent_model(), reel_spec)

    # An edit variant of a DIFFERENT region.
    other_region = edit_variants.region_for_moment(graph, 0.0, 5.0)
    edit_alternative = edit_variants.produce_alternatives(
        graph, _intent_model(), [
            AlternativeSpec(
                region=other_region,
                dimension=edit_variants.BROLL_COVER,
                value="clip_003",
                trade_off="a tighter cover",
            ),
        ])[0]
    problems = edit_variants.reel_variant_agrees_with_edit_variant(
        reel_variant, edit_alternative)
    assert any("regions differ" in p for p in problems)

    # An edit variant of a DIFFERENT dimension.
    edit_alternative = edit_variants.produce_alternatives(
        graph, _intent_model(), [
            AlternativeSpec(
                region=region,
                dimension=edit_variants.PASSAGE_ORDER,
                value=(2, 1, 3),
                trade_off="reordered",
            ),
        ])[0]
    problems = edit_variants.reel_variant_agrees_with_edit_variant(
        reel_variant, edit_alternative)
    assert any("dimensions differ" in p for p in problems)

    # An edit variant of a DIFFERENT value.
    edit_alternative = edit_variants.produce_alternatives(
        graph, _intent_model(), [
            AlternativeSpec(
                region=region,
                dimension=edit_variants.BROLL_COVER,
                value="clip_004",
                trade_off="a looser cover",
            ),
        ])[0]
    problems = edit_variants.reel_variant_agrees_with_edit_variant(
        reel_variant, edit_alternative)
    assert any("values differ" in p for p in problems)


# ── The general validation applies to reel variants ─────────────────


def test_a_reel_variant_that_differs_outside_its_dimension_is_refused():
    """The defect: a reel variant fork that changed something it did
    not declare would be a different edit wearing the variant's name -
    and the reel-only mechanism had no check for it. The fix routes
    reel variant forks through `validate_alternatives`, which refuses
    any payload difference outside the declared dimension.
    """
    graph = _graph()
    region = edit_variants.region_for_moment(graph, 0.0, 15.0)

    spec = ReelVariantSpec(
        reel_number=9,
        base_timeline_name="Reel 09 - The Opening",
        suffix=" (reaction-cutaway)",
        region=region,
        dimension=edit_variants.BROLL_COVER,
        value="clip_003",
        trade_off="a tighter cover",
    )
    reel_variant = edit_variants.produce_reel_variant(
        graph, _intent_model(), spec)

    # A clean reel variant validates cleanly.
    problems = edit_variants.validate_alternatives(
        [reel_variant], graph, edit_variants.BROLL_COVER)
    assert problems == []

    # Simulate a reel variant fork that also moved a block's timeline.
    for node in reel_variant["graph"]["nodes"]:
        if node["node_id"] == "spine_block:1":
            node["payload"]["timeline_start"] = 99.0
    problems = edit_variants.validate_alternatives(
        [reel_variant], graph, edit_variants.BROLL_COVER)
    assert any("differs outside the declared dimension" in p
               for p in problems)


def test_a_reel_variant_that_changes_the_argument_is_refused():
    """The defect: a reel variant fork that re-pointed a passage at a
    different beat would edit the story, not the telling - and the
    reel-only mechanism had no check for it. The fix routes reel
    variant forks through `validate_alternatives`, which refuses any
    intent-ref difference.
    """
    graph = _graph()
    region = edit_variants.region_for_moment(graph, 0.0, 15.0)

    spec = ReelVariantSpec(
        reel_number=9,
        base_timeline_name="Reel 09 - The Opening",
        suffix=" (reaction-cutaway)",
        region=region,
        dimension=edit_variants.BROLL_COVER,
        value="clip_003",
        trade_off="a tighter cover",
    )
    reel_variant = edit_variants.produce_reel_variant(
        graph, _intent_model(), spec)

    # Simulate a reel variant fork that changed the argument.
    reel_variant["graph"]["nodes"][0]["intent_refs"] = ["beat:9"]
    problems = edit_variants.validate_alternatives(
        [reel_variant], graph, edit_variants.BROLL_COVER)
    assert any("intent refs differ" in p for p in problems)


# ── The region a reel variant forks ─────────────────────────────────


def test_region_for_moment_maps_a_reel_to_its_edit_region():
    """The defect: a reel is cut from a moment - a span in the main
    edit's timeline - but nothing mapped that span to the graph nodes
    the moment plays, so a reel variant had no region to fork. The fix
    makes `region_for_moment` compute the region: every node whose
    timeline span overlaps the moment's span.
    """
    graph = _graph()

    # A moment covering blocks 1-3 maps to every node on those blocks.
    region = edit_variants.region_for_moment(graph, 0.0, 15.0)
    assert "spine_block:1" in region
    assert "spine_block:2" in region
    assert "spine_block:3" in region
    assert "passage:1" in region
    assert "passage:2" in region
    assert "passage:3" in region
    assert "aroll:1" in region
    assert "aroll:2" in region
    assert "aroll:3" in region
    assert "broll:3" in region

    # A moment covering only block 2 maps to that block's nodes.
    region = edit_variants.region_for_moment(graph, 5.0, 10.0)
    assert "spine_block:2" in region
    assert "spine_block:1" not in region
    assert "spine_block:3" not in region

    # A moment outside every block maps to nothing.
    region = edit_variants.region_for_moment(graph, 100.0, 200.0)
    assert region == ()
