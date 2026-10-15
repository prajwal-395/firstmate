"""Evidence-explained B-roll selection and repeated-shot refusal (F05).

B-roll must be better than generic similarity search: every cutaway
records the relation it claims, the evidence behind it and the time
coverage it carries - and the same source seconds may not cover two
timeline spans as two cutaways. These tests hold both halves: the
planner (`select_broll.resolve_broll`) refuses a replayed window and
records a `selection_evidence` receipt, and the manifest gate
(`manifest_validator`) refuses a replayed V2 window on a manifest that
never went through the planner.
"""

import os

import pytest

from library.steps.step_3_02_select_broll.post_bridge import resolve_broll
from library.tools.manifest_validator import validate_manifest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# The vision pass observed two different things in this clip; the moment
# names which half plays.
BLOCKS = [
    {"label": "parking lot", "visual": "wide view of parked cars",
     "start": 0.0, "end": 10.0},
    {"label": "brick wall", "visual": "close detail of a brick wall",
     "start": 10.0, "end": 20.0},
]

DOCS = [{"clip_id": "clip_b", "blocks": list(BLOCKS)}]

SPINE = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_a",
     "timeline_start": 0.0, "timeline_end": 4.0},
    {"position": 2, "block_type": "speech", "clip_id": "clip_a",
     "timeline_start": 4.0, "timeline_end": 8.0},
]}

CATALOG = [
    {"clip_id": "clip_a", "path": "/footage/a.MOV",
     "duration_seconds": 45.0, "width": 1920, "height": 1080,
     "rotation": 0},
    {"clip_id": "clip_b", "path": "/footage/b.MOV",
     "duration_seconds": 20.0, "width": 1920, "height": 1080,
     "rotation": 0},
]


def _resolve(creative, interjections=(), spine=None, catalog=None,
             docs=None):
    return resolve_broll(
        list(creative), list(interjections),
        catalog if catalog is not None else CATALOG,
        docs if docs is not None else DOCS, [],
        spine if spine is not None else SPINE,
        target_resolution=(1080, 1920))


def _cut(position, moment, rationale="shows what the speech names"):
    return {"clip_id": "clip_b", "spine_block_position": position,
            "preferred_moment": moment,
            "selection_rationale": rationale}


# ── The planner refuses a replayed source window ──────────────────────

def test_the_same_window_on_two_blocks_is_a_repeated_shot():
    """Same clip, no described difference: both blocks resolve to the
    same source seconds, and the second placement refuses - naming both
    blocks and the window - instead of replaying the shot."""
    with pytest.raises(ValueError, match="block 2.*block 1|repeated shot"):
        _resolve([_cut(1, "wide view of parked cars"),
                  _cut(2, "wide view of parked cars")])


def test_two_different_moments_of_one_clip_both_play():
    """Same clip is fine when the moments resolve to different seconds:
    source diversity is about the shot, not the file."""
    out = _resolve([_cut(1, "wide view of parked cars"),
                    _cut(2, "close detail of a brick wall")])
    first, second = out["b_roll_assignments"]
    assert (first["video_in"], first["video_out"]) != (
        second["video_in"], second["video_out"])


def test_an_interjection_replaying_an_assignment_refuses():
    """The redundancy check spans assignments and interjections alike:
    a free stretch of V2 carrying the assignment's own window is still
    the same shot replayed."""
    bare_catalog = [
        dict(CATALOG[0]),
        dict(CATALOG[1], duration_seconds=45.0),
    ]
    with pytest.raises(ValueError, match="repeated shot"):
        _resolve(
            [_cut(1, "harbour", "harbour cutaway")],
            [{"clip_id": "clip_b", "over_spine_block_position": 2,
              "timeline_start": 4.0, "timeline_end": 8.0}],
            catalog=bare_catalog, docs=[])


# ── A valid selection records its evidence and time coverage ──────────

def test_a_valid_selection_carries_its_evidence_and_coverage():
    """One receipt joins the stated relation, what vouched for the clip
    and chose its seconds, and the block's span beside the placed span."""
    out = _resolve([_cut(1, "close detail of a brick wall")])
    (entry,) = out["b_roll_assignments"]
    evidence = entry["selection_evidence"]
    assert evidence["relation"] == "reasoned"
    assert evidence["selection_rationale"] == "shows what the speech names"
    assert evidence["preferred_moment"] == "close detail of a brick wall"
    assert evidence["search_status"]
    assert evidence["window_basis"]
    assert evidence["window_basis_detail"]
    assert (evidence["block_timeline_start"],
            evidence["block_timeline_end"]) == (0.0, 4.0)
    assert (evidence["placed_timeline_start"],
            evidence["placed_timeline_end"]) == (
        entry["timeline_start"], entry["timeline_end"])
    assert evidence["coverage_shortfall_seconds"] == \
        entry["coverage_shortfall_seconds"] == 0.0


def test_a_short_cutaway_records_block_span_beside_placed_span():
    """Temporal coverage validation, recorded: a 3s clip over a 4s
    speech block plays 3s, and the receipt keeps the block's 4s beside
    the placed 3s with the shortfall - the hole nothing downstream
    must discover first."""
    catalog = [dict(CATALOG[0]),
               dict(CATALOG[1], duration_seconds=3.0)]
    out = _resolve([_cut(1, "harbour", "harbour cutaway")],
                   catalog=catalog, docs=[])
    (entry,) = out["b_roll_assignments"]
    evidence = entry["selection_evidence"]
    assert entry["coverage_shortfall_seconds"] == pytest.approx(1.0)
    assert evidence["block_timeline_end"] == 4.0
    assert evidence["placed_timeline_end"] == pytest.approx(3.0)
    assert evidence["coverage_shortfall_seconds"] == pytest.approx(1.0)


def test_an_unstated_relation_is_said_aloud_not_silently_carried():
    """An entry stating neither rationale nor moment still resolves -
    the catalog path stands - but its receipt reads `unreasoned`
    instead of passing as an explained match."""
    out = _resolve([{"clip_id": "clip_b", "spine_block_position": 1}])
    (entry,) = out["b_roll_assignments"]
    assert entry["selection_evidence"]["relation"] == "unreasoned"


# ── The manifest gate refuses a replayed V2 window ────────────────────

V1_SOURCE = os.path.abspath(__file__)
V2_SOURCE = os.path.join(REPO_ROOT, "library", "tools",
                         "manifest_validator.py")


def _manifest(first_window, second_window):
    def _clip(label, window, start):
        return {"source_file": V2_SOURCE,
                "source_in": window[0], "source_out": window[1],
                "timeline_in": start, "timeline_out": start + 4.0,
                "label": label}
    return {
        "project": {"name": "Test", "resolution": [1080, 1920],
                    "frame_rate": 30.0, "duration_seconds": 10.0},
        "tracks": {
            "V1": {"clips": [{
                "source_file": V1_SOURCE,
                "source_in": 17.666, "source_out": 22.348,
                "timeline_in": 0.0, "timeline_out": 8.0,
                "label": "a"}]},
            "V2": {"clips": [
                _clip("broll_1", first_window, 0.0),
                _clip("broll_2", second_window, 4.0)]},
        },
        "subtitles": [],
    }


def test_the_validator_names_a_replayed_v2_window():
    """A hand-edited manifest the planner never saw: two V2 clips over
    different timelines playing the same source seconds refuse, naming
    both clips."""
    errors = validate_manifest(_manifest((1.5, 5.5), (1.5, 5.5)))
    assert any("broll_2" in e and "broll_1" in e and "repeated shot" in e
               for e in errors), errors


def test_two_different_v2_windows_validate():
    """Same file, different seconds: a different shot, and the gate
    stays quiet."""
    assert validate_manifest(_manifest((1.5, 5.5), (5.5, 9.5))) == []


def test_v2_clips_without_a_source_range_are_not_windows():
    """Clips naming no source range carry nothing to compare: the
    duplicate-window check stays quiet and leaves them to the
    structural checks (which require the range)."""
    manifest = _manifest((1.5, 5.5), (5.5, 9.5))
    for clip in manifest["tracks"]["V2"]["clips"]:
        del clip["source_in"]
        del clip["source_out"]
    errors = validate_manifest(manifest)
    assert not any("repeated shot" in e for e in errors), errors
    assert any("source_in" in e for e in errors), errors
