"""The mix declared the bed, received it, and never opened it.

Step 5.02 declared `music_selection` and `creative_direction` REQUIRED and
`enhancement_spec` optional, and read none of the three - the reader
discipline guard named all three the moment its deterministic-step blind
spot was fixed (`python3 -m library.tools.input_contract --bad`).

That is not a paperwork defect.  A `music_behavior` word is a RELATIVE dB
applied to whatever level the music file already carries, so whether the
planned offset lands is decided by the bed's own loudness - and on 001
the bed is mastered 8.6 dB hotter than the speech, so -18 dB of clip gain
buys about 12.5 dB of separation and 1 of 11 speech-bearing windows met
the margin the check judged it against.  The step could not see which
track was chosen, let alone how loud it is.

These tests follow the measurements end to end: 2.04 measures every
candidate, the CHOSEN one's scalars travel on `music_selection`, and 5.02
reads them.  They also pin the two honesty properties the wiring is worth
nothing without - an unmeasured bed is an admitted absence and never a
level of 0, and the render-side check says when the margin it judged
against was a clip gain rather than a separation somebody declared.

Nothing here asserts a level, and since 2026-09-16 there is no level to
assert: the captain removed the five clip gains, step 5.02 asks a mix
engineer for the SEPARATION over these same measurements, and the gain is
solved from it (`library/tools/decided_value.py`).  What this file pins is
unchanged by that - the measurements reach the step, an unmeasured bed is
an admitted absence, and the render-side check says which kind of number
it judged against.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import music_measurement as mm  # noqa: E402
from library.tools import music_behavior as mb  # noqa: E402
from library.tools import render_qa  # noqa: E402

STEP_DIR = REPO / "library" / "steps" / "step_5_02_audio_mix"
SELECTION_STEP_DIR = REPO / "library" / "steps" / "step_2_04_music_selection"


# What a mix engineer decided on one run. The test supplies it because no
# module holds one any more.
DECIDED = {"prominent": -7.5, "background": -19.5}


def _mix_spec(spine, selection, decided=None):
    """`audio_mix_spec` for one spine and one bed, at decided levels."""
    from library.steps.step_5_02_audio_mix import mix as mix_module

    pre = mix_module.measure(spine, selection)
    by_scope = {scope: {"value": value, "answer": None}
                for scope, value in (DECIDED if decided is None
                                     else decided).items()}
    automation, undetermined = mix_module.solve_automation(pre, by_scope)
    return mix_module.assemble(pre, [], automation,
                               undetermined)["audio_mix_spec"]


def _load_post_bridge():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "step_2_04_post_bridge_under_test", SELECTION_STEP_DIR / "post_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(audio_path: str) -> dict:
    """One measured candidate, in the shape `measure_candidates` emits."""
    return {
        "title": "rise",
        "source": "library",
        "audio_path": audio_path,
        "duration_ok": True,
        "measured": True,
        "measurement_note": "",
        "integrated_lufs": -13.9,
        "loudness_range_lu": 6.0,
        "true_peak_dbtp": -0.4,
        "rms_spread_db": 5.7,
        "window_seconds": 60.0,
        "window_spread_db": 3.1,
        "speech_band_ratio_db": -2.8,
        # The two that must NOT travel onward.
        "window_envelope_dbfs": [-16.3] * mm.ENVELOPE_BUCKETS,
        "track_sections": [
            {"start_seconds": 0.0, "end_seconds": 60.0,
             "mean_dbfs": -16.3, "spread_db": 3.1},
        ],
        "track_sections_note": "",
    }


def _spine(*behaviours) -> dict:
    return {"structure": [
        {"position": index, "block_type": "speech", "content": "words",
         "timeline_start": float(index * 2), "timeline_end": float(index * 2 + 2),
         "music_behavior": behaviour}
        for index, behaviour in enumerate(behaviours)
    ]}


# ── The measurements travel with the track that was chosen ──────────

def test_the_chosen_track_carries_its_own_scalars():
    chosen = _candidate("/library/rise.mp3")
    other = dict(_candidate("/library/other.mp3"), integrated_lufs=-22.0)

    measurements = mm.selection_measurements(
        {"audio_path": "/library/rise.mp3"}, [other, chosen])

    assert measurements["measured"] is True
    assert measurements["integrated_lufs"] == -13.9, \
        "the scalars must come from the CHOSEN candidate, not the first one"
    assert measurements["speech_band_ratio_db"] == -2.8


def test_no_raw_value_list_travels_onward():
    """AGENTS.md 10.1: `music_selection` is declared whole by two prompts."""
    measurements = mm.selection_measurements(
        {"audio_path": "/library/rise.mp3"}, [_candidate("/library/rise.mp3")])

    for withheld in mm.WITHHELD_FROM_THE_SELECTION:
        assert withheld not in measurements, (
            f"{withheld} reached `music_selection`, which `plan_transitions` "
            f"and `mesh_spine` declare whole - it would land in both prompts")


def test_every_measurement_is_either_carried_or_recorded_as_withheld():
    accounted = (set(mm.SELECTION_MEASUREMENT_KEYS)
                 | set(mm.WITHHELD_FROM_THE_SELECTION))
    assert set(mm.MEASURED_KEYS) <= accounted


def test_a_track_that_was_not_measured_says_so():
    measurements = mm.selection_measurements(
        {"audio_path": "/downloads/fetched.mp3"},
        [_candidate("/library/rise.mp3")])

    assert measurements["measured"] is False
    assert "not one of the 1 measured candidates" in \
        measurements["measurement_note"]
    assert "integrated_lufs" not in measurements, \
        "an unmeasured bed has no level, not a level of 0"


def test_the_post_bridge_folds_the_measurements_onto_the_selection(tmp_path):
    """End to end through 2.04's real post-bridge, no model in the loop."""
    audio = tmp_path / "rise.mp3"
    audio.write_bytes(b"not really audio, but it is on disk")

    post_bridge = _load_post_bridge()
    resolved = post_bridge.resolve_selection(
        selection={
            "title": "rise",
            "source": "library",
            "audio_path": str(audio),
            "duration_seconds": 180.0,
            "direction_justification": {
                "direction_mood": "hopeful",
                "why_it_fits": "it lifts",
                "forbidden_registers": ["sombre"],
                "why_not_forbidden": {"sombre": "it never sits in a minor key"},
            },
        },
        candidates=[_candidate(str(audio))],
        target_duration=60.0,
        project_folder=str(tmp_path),
    )

    assert resolved["measurements"]["integrated_lufs"] == -13.9
    assert "track_sections" not in resolved["measurements"]


# ── Step 5.02 reads them ────────────────────────────────────────────

def test_the_mix_reads_the_bed_and_says_where_the_gain_puts_it():
    spec = _mix_spec(
        _spine("background", "prominent"),
        {"title": "rise", "audio_path": "/library/rise.mp3",
         "measurements": mm.selection_measurements(
             {"audio_path": "/library/rise.mp3"},
             [_candidate("/library/rise.mp3")])})

    assert spec["bed"]["measured"] is True
    assert spec["bed"]["integrated_lufs"] == -13.9
    assert spec["bed"]["speech_band_ratio_db"] == -2.8

    background, prominent = spec["music_automation"]
    # Where the gain PUTS the bed is arithmetic on the measurement, not a
    # chosen number - and the gain itself is now the decided one.
    assert background["target_level_db"] == DECIDED["background"]
    assert background["bed_level_after_gain_lufs"] == pytest.approx(
        -13.9 + DECIDED["background"])
    assert prominent["bed_level_after_gain_lufs"] == pytest.approx(
        -13.9 + DECIDED["prominent"])


def test_an_unmeasured_bed_is_an_admitted_absence_never_a_level():
    spec = _mix_spec(
        _spine("background"),
        {"title": "hand-picked", "audio_path": "/elsewhere/track.wav"})

    assert spec["bed"]["measured"] is False
    assert spec["bed"]["measurement_note"], \
        "an absent measurement must say why it is absent"
    assert "integrated_lufs" not in spec["bed"]
    assert spec["music_automation"][0]["bed_level_after_gain_lufs"] is None, \
        "None means unknown; 0 would read as a bed at full scale"
    # The plan is still complete - the mix is not gated on the measurement.
    assert spec["music_automation"][0]["target_level_db"] == \
        DECIDED["background"]


def test_the_step_declares_only_what_it_reads():
    manifest = json.loads((STEP_DIR / "manifest.json").read_text("utf-8"))
    declared = {i["name"] for i in manifest["interface"]["inputs"]}
    assert declared == {"audio_spine", "music_selection", "a_roll_assignments",
                        "creative_direction", "project_folder"}, (
        "enhancement_spec described a feature nobody built; re-declaring "
        "it needs a reader in the same commit. `a_roll_assignments` "
        "arrived WITH its reader: it names the source ranges the speech is "
        "measured over (library/tools/speech_loudness.py). "
        "`creative_direction` and `project_folder` arrived with theirs on "
        "2026-09-16: the ladder in library/tools/decided_value.py tries a "
        "stated project preference and a declared direction value before "
        "it asks the model.")

    dag = json.loads(
        (REPO / "library" / "processes" / "edit_video" / "dag.json")
        .read_text("utf-8"))
    routed = set()
    for edge in dag["edges"]:
        if edge["to"] == "audio_mix":
            routed |= set(edge["data_mapping"])
    assert routed == declared - {"project_folder"}, (
        "a DAG edge routing a key no manifest declares is the same defect, "
        "and so is a manifest declaring an input no edge carries - which is "
        "what `creative_direction` was for the hour between this step "
        "declaring it and the edge landing. `project_folder` is the one "
        "exception and it is not an exception to the rule: the runner "
        "injects it into every step, so no edge carries it for any of them.")


def test_no_mix_level_is_chosen_here():
    """No module holds a level for a behaviour, and none may again."""
    for word, meaning in mb.MUSIC_BEHAVIORS.items():
        assert isinstance(meaning, str), (
            f"{word} carries a number again: {meaning!r}. The five clip "
            f"gains were removed on the captain's ruling of 2026-09-16")
    assert mb.SEPARATION_TARGETS_DB == {}, (
        "a separation target is decided per run over the measurements, on "
        "`audio_mix_spec.value_decisions`. A module-level dict of them is "
        "the constant coming back in a new place.")
    for behaviour in mb.MUSIC_BEHAVIORS:
        assert mb.separation_target_db(behaviour) is None


# ── The check says what it judged against ───────────────────────────

def _fake_master(monkeypatch, *, music_gain_db: float, speech_db: float):
    """A two-second master: a known bed under a known voice."""
    import numpy as np  # a hard dependency of render_qa itself

    rate = 48000
    n = rate * 2
    rng = np.random.default_rng(7)
    music = rng.standard_normal(n) * 0.1
    speech = rng.standard_normal(n) * (10 ** (speech_db / 20.0))
    mix = music * (10 ** (music_gain_db / 20.0)) + speech

    def decode(path, sample_rate=rate):
        return (music if path == "MUSIC" else mix).astype("float32")

    monkeypatch.setattr(render_qa, "_decode_mono", decode)


def _automation(separation_target_db):
    return [{
        "spine_block_position": 0,
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "music_behavior": "background",
        "target_level_db": DECIDED["background"],
        "separation_target_db": separation_target_db,
    }]


BLOCKS = [{"position": 0, "block_type": "speech"}]


def test_the_check_names_a_clip_gain_when_that_is_what_it_judged(monkeypatch):
    _fake_master(monkeypatch, music_gain_db=DECIDED["background"],
                 speech_db=-20.0)

    result = render_qa.measure_speech_above_bed(
        "MASTER", "MUSIC", _automation(None), 0.0, BLOCKS)

    window = result.value["windows"][0]
    assert window["required_margin_basis"] == "clip_gain_read_as_separation"
    assert window["required_margin_db"] == abs(DECIDED["background"])
    assert result.threshold["judged_on_clip_gain"] is True
    assert "CLIP GAIN" in result.detail, (
        "the report must say the margin was a clip gain, or a reader takes "
        "it for a separation somebody declared")


def test_a_declared_separation_target_is_what_the_check_uses(monkeypatch):
    """The decided separation is what now lands in that field: since
    2026-09-16 `music_automation[].separation_target_db` carries what the
    mix engineer asked for, so this reader stopped being vacuous."""
    _fake_master(monkeypatch, music_gain_db=DECIDED["background"],
                 speech_db=-20.0)

    result = render_qa.measure_speech_above_bed(
        "MASTER", "MUSIC", _automation(8.0), 0.0, BLOCKS)

    window = result.value["windows"][0]
    assert window["required_margin_basis"] == "declared_separation_target"
    assert window["required_margin_db"] == 8.0
    assert result.threshold["judged_on_clip_gain"] is False
    # The separation itself is measured either way - that half always worked.
    assert window["margin_db"] == pytest.approx(
        window["non_music_db"] - window["music_in_mix_db"], abs=0.01)


def test_the_gate_is_still_off():
    assert render_qa.SPEECH_ABOVE_BED_GATES is False, (
        "one of the two conditions beside this boolean changed on "
        "2026-09-16 - the plan now carries a real separation target rather "
        "than a clip gain read as one - and promoting a report to a gate "
        "is still the captain's call, not a consequence of that")
