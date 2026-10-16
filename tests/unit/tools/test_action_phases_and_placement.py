from library.tools import action_phases as ap
from library.tools import cut_placement as cp


def _motion(rise=(2.0, 3.0)):
    samples = []
    for tenth in range(0, 60):
        tick = tenth / 10.0
        if rise[0] <= tick <= rise[1]:
            value = 0.9
        elif rise[0] - 0.5 <= tick < rise[0]:
            value = 0.4
        else:
            value = 0.1
        samples.append({"tick_seconds": tick, "value": value})
    return samples


def test_phases_split_preparation_execution_completion():
    phased = ap.phase_action(0.0, 6.0, _motion(), evidence_id="m1")
    assert phased["status"] == "phased"
    names = [p["phase"] for p in phased["phases"]]
    assert names == ["preparation", "execution", "completion"]
    execution = phased["phases"][1]
    assert execution["start_seconds"] == 2.0
    assert execution["end_seconds"] == 3.0


def test_no_motion_samples_is_unphaseable_not_thirds():
    phased = ap.phase_action(0.0, 6.0, [])
    assert phased["status"] == "unphaseable"
    assert phased["phases"] == []
    assert ap.action_boundary_cuts(phased) == []


def test_boundary_cuts_sit_at_phase_edges():
    phased = ap.phase_action(0.0, 6.0, _motion(), evidence_id="m1")
    cuts = ap.action_boundary_cuts(phased)
    assert [c["position_seconds"] for c in cuts] == [2.0, 3.0]
    assert all(c["evidence_id"] == "m1" for c in cuts)


def test_crosses_execution_detects_mid_action():
    phased = ap.phase_action(0.0, 6.0, _motion())
    assert ap.crosses_execution(2.5, phased)
    assert not ap.crosses_execution(2.0, phased)
    assert not ap.crosses_execution(4.0, phased)


def _words_with_pause():
    return [
        {"word": "cut", "source_start": 0.0, "source_end": 0.4},
        {"word": "here", "source_start": 1.0, "source_end": 1.4},
    ]


def test_refine_moves_mid_word_cut_to_pause_center():
    verdict = cp.refine_cut(0.2, _words_with_pause())
    assert verdict["status"] == "moved"
    assert verdict["position_seconds"] == 0.7


def test_refine_vetoes_mid_word_with_no_pause_nearby():
    words = [
        {"word": "a", "source_start": 0.0, "source_end": 5.0},
        {"word": "b", "source_start": 5.0, "source_end": 9.0},
    ]
    verdict = cp.refine_cut(2.0, words)
    assert verdict["status"] == "vetoed"
    assert verdict["vetoes"]


def test_refine_vetoes_action_execution_crossing():
    phased = ap.phase_action(0.0, 6.0, _motion())
    words = [
        {"word": "lift", "source_start": 0.0, "source_end": 0.4},
        {"word": "it", "source_start": 4.0, "source_end": 4.4},
    ]
    verdict = cp.refine_cut(2.5, words, phased_actions=[phased])
    assert verdict["status"] == "vetoed"
    assert any("execution" in veto for veto in verdict["vetoes"])


def test_refine_vetoes_crosstalk_span():
    verdict = cp.refine_cut(
        0.7, _words_with_pause(),
        crosstalk_spans=[{"start_seconds": 0.5, "end_seconds": 0.9}])
    assert verdict["status"] == "vetoed"


def test_refine_reports_prosody_peak_without_veto():
    verdict = cp.refine_cut(0.7, _words_with_pause(),
                            prosody_peaks=[0.75])
    assert verdict["status"] == "placed"
    assert verdict["position_seconds"] == 0.7
    assert any("prosody peak" in item
               for item in verdict["adjustments"])
