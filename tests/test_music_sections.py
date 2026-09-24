"""The section grid: measured labels reach the planners, and only those.

Fidelity rung 5c: plans cut on bars but no step could see the sections -
`mesh_spine` paced gaps from prose, the cut planners counted bars from
the track head, and the Infected drop landed only as a novelty section.
Step 2.06 now emits `section_grid` (allin1, bar-aligned); these tests
drive the readers on fixture grids shaped like the measured one
(intro/intro/solo/outro on the Infected instrumental, verse/chorus on
the lyrics cut) and assert the mapping, the addressing and the refusal
half: a label the model did not give refuses with what the grid
carries, never coined.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.context_views import build_view
from library.tools.frame_utils import seconds_to_frame
from library.tools.music_sections import (
    NON_ADDRESSABLE,
    addressable_labels,
    available,
    find_sections,
    sections_timeline,
)
from library.tools.sub_block_anchor import AnchorRefused, resolve_anchor
from library.tools.analysis.music_pipeline import (
    _snap_to_downbeats,
    _validate_section_grid,
)

FPS = 30.0


def _sections():
    # Shaped like the measured Infected instrumental grid: two intro
    # spans split at the 35.15 drop, then solo, then outro.
    return [
        {"label": "start", "start": 0.0, "end": 0.16,
         "first_downbeat": 0.16, "mean_label_activation": None},
        {"label": "intro", "start": 0.16, "end": 35.15,
         "first_downbeat": 0.16, "mean_label_activation": 0.81},
        {"label": "intro", "start": 35.15, "end": 67.43,
         "first_downbeat": 35.15, "mean_label_activation": 0.77},
        {"label": "solo", "start": 99.71, "end": 136.7,
         "first_downbeat": 99.71, "mean_label_activation": 0.69},
        {"label": "outro", "start": 185.83, "end": 198.6,
         "first_downbeat": 185.83, "mean_label_activation": 0.9},
        {"label": "end", "start": 198.6, "end": 198.61,
         "first_downbeat": 198.6, "mean_label_activation": None},
    ]


def _analysis(sections=None):
    return {"tempo": {"bpm": 90.0, "beats": [], "downbeats": [],
                      "downbeat_source": "detected"},
            "section_grid": {
                "available": True,
                "method": "allin1-harmonix-all",
                "validation": "first downbeat 0.16s, 70 bars over 198.6s",
                "labels_absent_note": "no drop or phrase labels",
                "sections": _sections() if sections is None else sections,
            }}


def _selection(source_in=0.0):
    if source_in == 0.0:
        return {}
    return {"section": {"source_in": source_in, "why": "test"}}


def _block(start=30.0, stop=45.0):
    return {"position": 1, "block_type": "speech", "clip_id": "clip_001",
            "source_start": 0.0, "source_end": 15.0,
            "timeline_start": start, "timeline_end": stop,
            "word_timestamps": [], "alignment_method": "mfa"}


# ── The reader maps file time through the chosen section ──────────────

def test_sections_map_through_the_chosen_section():
    """Bed from 35.15 s: the drop span opens the timeline at 0.0 s."""
    rows = sections_timeline(_analysis(), _selection(35.15))
    by_label = [(r["label"], r["start_seconds"],
                 r["first_downbeat_seconds"]) for r in rows]
    assert ("intro", 0.0, 0.0) in by_label
    assert ("solo", pytest.approx(64.56, abs=1e-3),
            pytest.approx(64.56, abs=1e-3)) in by_label
    # The pre-drop spans are not in the edit.
    assert all(r["start_seconds"] >= 0 for r in rows)


def test_unavailable_grid_reads_empty():
    assert sections_timeline({"section_grid": {"available": False}},
                             {}) == []
    assert sections_timeline({}, {}) == []
    assert not available({})
    assert not available({"section_grid": {"available": True,
                                           "sections": []}})


def test_sentinels_are_not_addressable():
    labels = addressable_labels(_analysis())
    assert "start" not in labels and "end" not in labels
    assert set(NON_ADDRESSABLE) == {"start", "end"}
    assert labels == ["intro", "intro", "solo", "outro"]


def test_find_sections_pairs_matches_with_what_is_present():
    matches, present = find_sections(_analysis(), {}, "intro")
    assert len(matches) == 2
    assert present == ["intro", "intro", "solo", "outro"]
    matches, _ = find_sections(_analysis(), {}, "chorus")
    assert matches == []


# ── The anchor resolves a section to its first downbeat ───────────────

def test_section_anchor_resolves_to_the_first_downbeat():
    """The drop span's first downbeat, file 35.15 s, timeline 35.15 s."""
    hit = resolve_anchor({"section": "intro", "occurrence": 2},
                         block=_block(), music_analysis=_analysis(),
                         music_selection={}, frame_rate=FPS,
                         step="plan_vfx", plan="vfx_creative", index=0)
    assert hit["timeline_seconds"] == pytest.approx(35.15, abs=1e-9)
    assert hit["frame"] == seconds_to_frame(35.15, FPS)
    assert "first downbeat" in hit["method"]


def test_section_anchor_edge_end_resolves_to_the_span_end():
    hit = resolve_anchor({"section": "solo", "edge": "end"},
                         block=_block(90.0, 150.0),
                         music_analysis=_analysis(), music_selection={},
                         frame_rate=FPS, step="plan_vfx",
                         plan="vfx_creative", index=0)
    assert hit["timeline_seconds"] == pytest.approx(136.7, abs=1e-9)


def test_section_anchor_outside_the_block_refuses():
    with pytest.raises(AnchorRefused, match="outside block"):
        resolve_anchor({"section": "solo"}, block=_block(),
                       music_analysis=_analysis(), music_selection={},
                       frame_rate=FPS, step="plan_vfx",
                       plan="vfx_creative", index=0)


UNRESOLVABLE_SECTIONS = [
    pytest.param({"section": "chorus"}, "no-drop fixture",
                 "section 'chorus' is not in the measured grid",
                 id="label-absent"),
    pytest.param({"section": "drop"}, "no-drop fixture",
                 "section 'drop' is not in the measured grid",
                 id="drop-never-coined"),
    pytest.param({"section": "start"}, "sentinel",
                 "is grid bookkeeping, not music",
                 id="sentinel"),
    pytest.param({"section": "intro", "occurrence": 3}, "past count",
                 "occurs 2 time",
                 id="occurrence-past"),
    pytest.param({"section": "  "}, "blank",
                 "names no section",
                 id="blank"),
]


@pytest.mark.parametrize("anchor, _why, match", UNRESOLVABLE_SECTIONS)
def test_unresolvable_section_anchors_refuse(anchor, _why, match):
    with pytest.raises(AnchorRefused, match=match):
        resolve_anchor(anchor, block=_block(0.0, 200.0),
                       music_analysis=_analysis(), music_selection={},
                       frame_rate=FPS, step="plan_transitions",
                       plan="transitions", index=0)


def test_section_anchor_without_a_grid_refuses():
    with pytest.raises(AnchorRefused, match="no usable section grid"):
        resolve_anchor({"section": "chorus"}, block=_block(0.0, 200.0),
                       music_analysis={}, music_selection={},
                       frame_rate=FPS, step="plan_transitions",
                       plan="transitions", index=0)


# ── The producer validates before it adopts ───────────────────────────

def test_validation_catches_a_dropped_opening():
    """The measured 1-in-3 failure: first downbeat six bars in."""
    downbeats = [round(16.31 + i * 2.69, 3) for i in range(64)]
    problems = _validate_section_grid(downbeats, 198.6)
    assert any("first downbeat" in p for p in problems)


def test_validation_passes_the_measured_grid():
    downbeats = [round(0.16 + i * 2.69, 3) for i in range(73)]
    assert _validate_section_grid(downbeats, 198.6) == []
    assert _validate_section_grid([1.0], 198.6) != []


def test_boundaries_snap_to_their_own_runs_downbeats():
    snapped, delta = _snap_to_downbeats(35.15, [32.46, 35.15, 37.84])
    assert (snapped, delta) == (35.15, 0.0)
    snapped, delta = _snap_to_downbeats(35.0, [32.46, 35.15, 37.84])
    assert snapped == 35.15 and abs(delta - 0.15) < 1e-9


def test_no_noncommercial_weights_in_the_pipeline():
    """madmom's RNN processors load CC-BY-NC-SA weights - the tempo
    chain must never instantiate or import them. (The module docstring
    names them to record WHY they are banned; that discussion is not a
    load path. beat_this's DBN post-processor is weight-free HMM code
    and lives outside this file.)"""
    path = os.path.join(PROJECT_ROOT, "library", "tools", "analysis",
                        "music_pipeline.py")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    for banned in ("RNNBeatProcessor(", "RNNDownBeatProcessor(",
                   "madmom.features", "import madmom\n",
                   "import madmom "):
        assert banned not in source, f"non-commercial path: {banned!r}"


# ── The view shows labels, never the boundary series ──────────────────

def test_sectiongrid_view_lists_sections_with_provenance():
    data = {"music_analysis": _analysis(), "music_selection": {}}
    view = build_view("sectiongrid", data)["sectiongrid"]
    assert view["method"] == "allin1-harmonix-all"
    assert view["section_count"] == 6
    first = view["sections"][1]
    assert first["label"] == "intro"
    assert first["first_downbeat_seconds"] == pytest.approx(0.16)
    assert "legend" in view and "labels_absent_note" in view


def test_sectiongrid_view_absent_when_no_grid():
    data = {"music_analysis": {}, "music_selection": {}}
    assert build_view("sectiongrid", data) == {}
