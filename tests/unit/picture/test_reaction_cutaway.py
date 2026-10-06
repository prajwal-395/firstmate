"""Tests for reaction cutaway selection.

These tests pin behaviour: a reaction shot is found when another person
is visible during speech, a shot already in the spine is not a candidate,
a block with no speech gets nothing, and the reaction lands at the end of
the speech moment.
"""
from library.tools.reaction_cutaway import select_reaction_cutaways


def _block(position, clip_id, src_start, src_end, tl_start, tl_end,
           words=None):
    return {
        "position": position,
        "clip_id": clip_id,
        "source_start": src_start,
        "source_end": src_end,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "word_timestamps": words or [],
    }


def _word(src_start, src_end):
    return {"source_start": src_start, "source_end": src_end}


def _graph(speaking_edges, reaction_edges):
    """Build a minimal project graph with speaks_in and appears_in edges."""
    edges = []
    for person_id, clip_id, start, end in speaking_edges:
        edges.append({
            "from": person_id,
            "to": f"shot:{clip_id}",
            "type": "speaks_in",
            "payload": {"start": start, "end": end},
        })
    for person_id, clip_id, start, end in reaction_edges:
        edges.append({
            "from": person_id,
            "to": f"shot:{clip_id}",
            "type": "appears_in",
            "payload": {"start": start, "end": end},
        })
    return {"nodes": [], "edges": edges}


class TestSelectReactionCutaways:
    def test_reaction_shot_is_found(self):
        """Another person visible during speech is a reaction candidate."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
                   [_word(1.0, 3.0), _word(5.0, 8.0)]),
        ]
        graph = _graph(
            speaking_edges=[("p1", "c1", 1.0, 8.0)],
            reaction_edges=[("p2", "c2", 2.0, 6.0)],
        )
        result = select_reaction_cutaways(blocks, graph)
        assert len(result) == 1
        entry = result[0]
        assert entry["speech_clip_id"] == "c1"
        assert entry["speaking_person_id"] == "p1"
        assert entry["reaction_clip_id"] == "c2"
        assert entry["reacting_person_id"] == "p2"
        assert entry["reaction_source_start"] == 2.0
        assert entry["reaction_source_end"] == 6.0

    def test_shot_already_in_spine_is_not_a_candidate(self):
        """A reaction shot already placed in the spine is not a cutaway."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
                   [_word(1.0, 3.0)]),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0,
                   [_word(1.0, 3.0)]),
        ]
        graph = _graph(
            speaking_edges=[("p1", "c1", 1.0, 3.0)],
            reaction_edges=[("p2", "c2", 1.0, 3.0)],
        )
        assert select_reaction_cutaways(blocks, graph) == []

    def test_block_with_no_speech_gets_nothing(self):
        """A block with no word timestamps has no speech moment."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
        ]
        graph = _graph(
            speaking_edges=[("p1", "c1", 0.0, 10.0)],
            reaction_edges=[("p2", "c2", 2.0, 6.0)],
        )
        assert select_reaction_cutaways(blocks, graph) == []

    def test_block_with_no_speaking_person_gets_nothing(self):
        """A clip with no speaks_in edge has no measured speaker."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
                   [_word(1.0, 3.0)]),
        ]
        graph = _graph(
            speaking_edges=[],
            reaction_edges=[("p2", "c2", 2.0, 6.0)],
        )
        assert select_reaction_cutaways(blocks, graph) == []

    def test_reaction_lands_at_end_of_speech_moment(self):
        """The reaction lands at the end of the last word in the block."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
                   [_word(1.0, 3.0), _word(5.0, 8.0)]),
        ]
        graph = _graph(
            speaking_edges=[("p1", "c1", 1.0, 8.0)],
            reaction_edges=[("p2", "c2", 2.0, 6.0)],
        )
        result = select_reaction_cutaways(blocks, graph)
        assert len(result) == 1
        # Last word ends at source 8.0, block starts at source 0.0,
        # timeline starts at 0.0 - so speech moment is at timeline 8.0
        assert result[0]["timing"]["speech_moment"] == 8.0
        assert result[0]["timing"]["reaction_land"] == 8.0

    def test_reacting_person_is_not_the_speaker(self):
        """A shot where the speaker appears is not a reaction to themselves."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
                   [_word(1.0, 3.0)]),
        ]
        graph = _graph(
            speaking_edges=[("p1", "c1", 1.0, 3.0)],
            reaction_edges=[("p1", "c2", 1.0, 3.0)],
        )
        assert select_reaction_cutaways(blocks, graph) == []

    def test_multiple_speaking_persons_each_get_reactions(self):
        """Each speaking person in a block gets their own reaction search."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
                   [_word(1.0, 3.0)]),
        ]
        graph = _graph(
            speaking_edges=[("p1", "c1", 1.0, 3.0),
                            ("p2", "c1", 1.0, 3.0)],
            reaction_edges=[("p3", "c2", 1.0, 3.0)],
        )
        result = select_reaction_cutaways(blocks, graph)
        assert len(result) == 2
        persons = {r["speaking_person_id"] for r in result}
        assert persons == {"p1", "p2"}

    def test_custom_offset_is_applied(self):
        """A caller-stated offset moves the reaction land."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
                   [_word(1.0, 3.0)]),
        ]
        graph = _graph(
            speaking_edges=[("p1", "c1", 1.0, 3.0)],
            reaction_edges=[("p2", "c2", 1.0, 3.0)],
        )
        result = select_reaction_cutaways(blocks, graph, reaction_offset=0.5)
        assert len(result) == 1
        assert result[0]["timing"]["reaction_land"] == 3.5
        assert result[0]["timing"]["offset_seconds"] == 0.5
