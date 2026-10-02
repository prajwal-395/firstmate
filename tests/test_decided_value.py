"""How a creative value gets decided, and what the decision has to say.

The captain removed nine constants that decide creative outcomes with one
ruling rather than nine answers (2026-09-16): a stated preference wins,
then the project's creative direction, then the model reasoning over
measured signal, and only then a documented fallback that is visible AS
one.  `library/tools/decided_value.py` is the only implementation of that
precedence, and these tests are what stop a tenth constant being written
instead of a tenth slot.

What is pinned here:

* every rung, in order, including the ones that CANNOT answer - a rung
  that was skipped says why, so a run short of a preference reads
  differently from one short of a measurement;
* the solver - the model answers in units an ear can judge and the engine
  computes what the renderer reads, from measurements, and produces
  NOTHING rather than a stand-in when a term was never measured;
* the fallback - reached last, recorded as itself, owned by somebody who
  is not the engine;
* the trace - merged never replaced, and a value with no record behind it
  is refused.

The route to the model (schema appender, split-out answer, the
post-bridge hand-off) is exercised by the scenarios below; creative
quantity ownership is declared separately on the capability registry.
"""

import pytest

from library.tools import decided_value as dv

SLOT = "mix.speech_above_bed_db"

# Project 001's own numbers: a bed mastered about 8.5 dB hotter than the
# iPhone speech, which is the material that proved a single constant
# could not generalise.
MEASURED = {"bed_integrated_lufs": -13.9, "speech_lufs": -22.4}


@pytest.fixture(autouse=True)
def _isolate_user_taste_profile(tmp_path, monkeypatch):
    """A local user's profile must not change the ladder's unit tests."""
    config = tmp_path / "user-config" / "ren" / "config.env"
    monkeypatch.setenv("REN_CONFIG", str(config))


def _decide(**kwargs):
    kwargs.setdefault("scope", "background")
    kwargs.setdefault("measurements", MEASURED)
    return dv.decide(SLOT, **kwargs)


# ─── The registry is a contract ───────────────────────────────────────

class TestTheRegistry:

    def test_an_unregistered_slot_raises_rather_than_resolving(self):
        with pytest.raises(dv.UnknownSlot):
            dv.slot("mix.something_nobody_registered")


# ─── The ladder, rung by rung ─────────────────────────────────────────

class TestTheLadder:

    def test_a_stated_preference_wins(self, tmp_path):
        (tmp_path / "project.yaml").write_text(
            "pipeline:\n"
            "  creative_preferences:\n"
            "    mix:\n"
            "      speech_above_bed_db:\n"
            "        background: 14\n", encoding="utf-8")
        decision = _decide(
            project_folder=str(tmp_path),
            model_answer={"value": 6.0, "why": "I would rather it were 6"})
        assert decision.basis == dv.STATED
        assert decision.answer == 14
        assert "creative_preferences" in decision.source

    def test_the_model_decides_when_the_project_states_nothing(self):
        decision = _decide(
            model_answer={"value": 9.0, "why": "the bed is hot and busy"})
        assert decision.basis == dv.REASONED
        assert decision.why == "the bed is hot and busy"
        assert decision.answer == 9.0

    def test_an_answer_with_no_why_is_not_a_decision(self):
        """5.01's rule: a value nobody can review is what the constant
        this replaces was."""
        decision = _decide(model_answer={"value": 9.0, "why": "   "})
        assert decision.basis == dv.FALLBACK

    def test_the_fallback_is_reached_last_and_says_it_is_one(self):
        decision = _decide()
        assert decision.basis == dv.FALLBACK
        assert decision.value == -18
        assert "Lucie" in decision.source
        assert "Superseded by" in decision.source

    def test_a_slot_with_no_fallback_yields_no_value_at_all(self):
        """Never 0, never a stand-in: the consumer drops or refuses."""
        import dataclasses

        row = dataclasses.replace(dv.slot(SLOT), fallback=None,
                                  undetermined_means="the window gets no gain")
        original = dict(dv.SLOTS)
        try:
            dv.SLOTS[SLOT] = row
            decision = _decide()
        finally:
            dv.SLOTS.clear()
            dv.SLOTS.update(original)
        assert decision.basis == dv.UNDETERMINED
        assert decision.value is None
        assert decision.why == "the window gets no gain"
        assert decision.decided is False

    def test_every_rung_that_could_not_answer_says_why(self):
        """A run short of a preference must read differently from one
        short of a measurement, or the record measures nothing."""
        decision = _decide()
        skipped = {r["basis"]: r["why"] for r in decision.rungs_skipped}
        assert set(skipped) == {dv.STATED, dv.DIRECTED, dv.REASONED}
        assert all(why.strip() for why in skipped.values())
        assert "creative_preferences" in skipped[dv.STATED]
        assert "DIRECTION_KEYS" in skipped[dv.DIRECTED]


# ─── The formula, and what it refuses to compute ──────────────────────

class TestTheSolver:

    def test_the_gain_is_arithmetic_over_the_judgement_and_two_measures(self):
        decision = _decide(model_answer={"value": 9.0, "why": "hot bed"})
        # (speech - separation) - bed
        assert decision.value == pytest.approx((-22.4 - 9.0) - -13.9)

    @pytest.mark.parametrize("missing", ["bed_integrated_lufs", "speech_lufs"])
    def test_an_unmeasured_term_produces_no_value_rather_than_a_stand_in(
            self, missing):
        measurements = dict(MEASURED)
        measurements[missing] = None
        decision = _decide(
            measurements=measurements,
            model_answer={"value": 9.0, "why": "hot bed"})
        assert decision.basis == dv.FALLBACK, (
            "reasoning over a measurement nobody took is the defect this "
            "module exists to remove, one level up")
        reasoned = next(r for r in decision.rungs_skipped
                        if r["basis"] == dv.REASONED)
        assert "not measured" in reasoned["why"]

    def test_nothing_is_clamped_in_either_direction(self):
        """The captain: "it could very well be possible that we need to
        use values outside of these bounds"."""
        for separation in (-40.0, 0.0, 120.0):
            decision = _decide(
                model_answer={"value": separation, "why": "this piece"})
            assert decision.value == pytest.approx(
                (-22.4 - separation) - -13.9)

    def test_an_answer_that_is_not_a_number_is_not_solved(self):
        decision = _decide(model_answer={"value": "quite a lot", "why": "x"})
        assert decision.basis == dv.FALLBACK


# ─── The route to the model ───────────────────────────────────────────

class TestTheAsk:

    def test_the_answer_is_split_out_of_the_step_s_own_output(self):
        answer = {"audio_mix_spec": {"x": 1},
                  dv.FIELD: [{"slot": SLOT, "scope": "background",
                              "value": 9.0, "why": "hot bed"}]}
        remaining, taken = dv.take("audio_mix", answer)
        assert dv.FIELD not in remaining
        assert remaining == {"audio_mix_spec": {"x": 1}}
        assert taken[(SLOT, "background")]["value"] == 9.0

    def test_an_entry_for_a_slot_this_step_does_not_decide_is_dropped(self):
        answer = {dv.FIELD: [{"slot": "mix.something_else", "value": 1,
                              "why": "because"}]}
        _, taken = dv.take("audio_mix", answer)
        assert taken == {}

    def test_an_entry_with_no_why_is_dropped_at_the_door(self):
        answer = {dv.FIELD: [{"slot": SLOT, "scope": "background",
                              "value": 9.0}]}
        _, taken = dv.take("audio_mix", answer)
        assert taken == {}

    def test_the_answer_reaches_a_post_bridge_through_one_key(self):
        """A post-bridge is a subprocess and cannot read the collector."""
        dv.reset()
        _, taken = dv.take("audio_mix", {dv.FIELD: [
            {"slot": SLOT, "scope": "background", "value": 9.0, "why": "w"}]})
        dv.stash("audio_mix", taken)
        merge_data = {dv.MERGE_KEY: dv.stashed("audio_mix")}
        assert dv.answers_from(merge_data, SLOT, "background")["value"] == 9.0
        assert dv.answers_from(merge_data, SLOT, "prominent") is None
        dv.reset()
        assert dv.stashed("audio_mix") == []


# ─── The trace ────────────────────────────────────────────────────────

class TestTheTrace:

    def test_a_value_with_no_record_behind_it_is_refused(self):
        with pytest.raises(dv.UndecidedValue):
            dv.assert_decided(SLOT, -18, [], scope="background")

    def test_a_value_with_a_record_passes(self):
        record = _decide().as_record()
        dv.assert_decided(SLOT, record["value"], [record], scope="background")

    def test_records_are_merged_not_replaced(self):
        """A `--rerun audio_mix` answers one step; replacing the key would
        erase every other step's decisions."""
        existing = [{"slot": "other.value", "scope": "", "step": "color_grade",
                     "basis": dv.REASONED, "value": 1},
                    {"slot": SLOT, "scope": "background", "step": "audio_mix",
                     "basis": dv.FALLBACK, "value": -18}]
        fresh = [{"slot": SLOT, "scope": "background", "step": "audio_mix",
                  "basis": dv.REASONED, "value": -19.5}]
        merged = dv.merge_records(existing, fresh)
        assert len(merged) == 2
        carried = next(r for r in merged if r["step"] == "color_grade")
        assert carried["from_a_previous_run"] is True
        answered = next(r for r in merged if r["step"] == "audio_mix")
        assert answered["value"] == -19.5
        assert "from_a_previous_run" not in answered
