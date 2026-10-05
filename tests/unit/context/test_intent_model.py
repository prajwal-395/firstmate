"""The structured intent model, proved against named defects.

The gap: intent is prose, so a planner cannot ask "does this edit serve
the narrative theme?" and a key moment cannot be checked against the
timeline - there is no entity to check.  Each test names one defect the
model closes: a beat with no purpose, a setup/payoff link to a beat that
does not exist, a key moment grounded in no footage, a pacing entry
naming an unknown beat, and a planner's output that serves beats without
naming them.  None of them asserts that the module contains a particular
entry or count - the model is built, mutated and queried, so a test
passes only when the model actually does what the defect requires.
"""
from __future__ import annotations

from library.tools import intent_model


def _beat(beat_id="beat:1", purpose="hook the viewer", setup_for=None,
          payoff_of=None, clip_id="clip_001", start=0.0, end=5.0):
    return {
        "beat_id": beat_id,
        "purpose": purpose,
        "setup_for": setup_for,
        "payoff_of": payoff_of,
        "span": {"clip_id": clip_id, "source_start": start,
                 "source_end": end},
    }


def _claim(claim_id="claim:1", statement="the launch failed",
           supports=None):
    return {"claim_id": claim_id, "statement": statement,
            "supports": supports if supports is not None else []}


def _moment(moment_id="moment:1", description="the reveal",
            clip_id="clip_001", start=20.0, end=24.0):
    return {"moment_id": moment_id, "description": description,
            "span": {"clip_id": clip_id, "source_start": start,
                     "source_end": end}}


def _pacing(beat_id="beat:1", energy=0.5, target_asl=3.0):
    return {"beat_id": beat_id, "energy": energy, "target_asl": target_asl}


def _model():
    """A valid model: two beats in a setup/payoff pair, a claim, a key
    moment grounded in the first beat's span, and a pacing curve."""
    return {
        "beats": [
            _beat("beat:1", purpose="set up the problem",
                  setup_for="beat:2", start=0.0, end=5.0),
            _beat("beat:2", purpose="land the payoff",
                  payoff_of="beat:1", start=10.0, end=15.0),
        ],
        "claims": [_claim("claim:1", "the launch failed", []),
                   _claim("claim:2", "the fix worked", ["claim:1"])],
        "key_moments": [_moment("moment:1", "the reveal", start=1.0,
                                end=3.0)],
        "pacing_curve": [_pacing("beat:1", 0.3, 4.0),
                         _pacing("beat:2", 0.9, 2.0)],
    }


CATALOG = {"clip_001", "clip_002"}


# ── A valid model is accepted ──────────────────────────────────────

def test_a_valid_model_passes_validation():
    assert intent_model.validate_intent_model(_model(), CATALOG) == []


def test_a_model_with_no_beats_has_nothing_to_name():
    """An empty model is valid - it is the model's own statement that the
    piece has no narrative structure, not a defect to hide."""
    assert intent_model.validate_intent_model({}) == []


# ── The model's own contract ──────────────────────────────────────

def test_a_beat_with_no_purpose_is_refused():
    model = _model()
    model["beats"][0]["purpose"] = ""
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("no purpose" in p for p in problems)


def test_a_duplicate_beat_id_is_refused():
    model = _model()
    model["beats"][1]["beat_id"] = "beat:1"
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("duplicate beat_id" in p for p in problems)


def test_a_setup_link_to_a_beat_that_does_not_exist_is_refused():
    model = _model()
    model["beats"][0]["setup_for"] = "beat:9"
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("setup" in p and "beat:9" in p for p in problems)


def test_a_payoff_link_to_a_beat_that_does_not_exist_is_refused():
    model = _model()
    model["beats"][1]["payoff_of"] = "beat:7"
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("payoff" in p and "beat:7" in p for p in problems)


def test_a_beat_that_links_to_itself_is_refused():
    model = _model()
    model["beats"][0]["setup_for"] = "beat:1"
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("links to itself" in p for p in problems)


def test_a_key_moment_grounded_in_no_clip_is_refused():
    model = _model()
    model["key_moments"][0]["span"] = {"clip_id": "", "source_start": 1.0,
                                       "source_end": 3.0}
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("names no clip_id" in p for p in problems)


def test_a_key_moment_grounded_in_a_clip_outside_the_catalog_is_refused():
    model = _model()
    model["key_moments"][0]["span"]["clip_id"] = "clip_999"
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("clip_999" in p and "not in the catalog" in p
               for p in problems)


def test_a_span_that_ends_before_it_starts_is_refused():
    model = _model()
    model["beats"][0]["span"] = {"clip_id": "clip_001", "source_start": 5.0,
                                 "source_end": 5.0}
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("ends before it starts" in p for p in problems)


def test_a_pacing_entry_naming_an_unknown_beat_is_refused():
    model = _model()
    model["pacing_curve"][0]["beat_id"] = "beat:42"
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("beat:42" in p and "not in the model" in p for p in problems)


def test_a_pacing_energy_outside_zero_to_one_is_refused():
    model = _model()
    model["pacing_curve"][0]["energy"] = 1.5
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("outside 0..1" in p for p in problems)


def test_a_claim_supporting_a_claim_that_does_not_exist_is_refused():
    model = _model()
    model["claims"][0]["supports"] = ["claim:99"]
    problems = intent_model.validate_intent_model(model, CATALOG)
    assert any("claim:99" in p and "not in the model" in p for p in problems)


# ── The planner-side contract ─────────────────────────────────────

def test_a_passage_naming_the_beats_it_serves_passes():
    model = _model()
    passages = [{"position": 1, "serves_beats": ["beat:1"]},
                {"position": 2, "serves_beats": ["beat:2"]}]
    assert intent_model.validate_serves_beats(passages, model) == []


def test_a_passage_naming_no_beat_is_refused():
    """A passage that serves the story but names no beat is untraceable -
    a reader cannot ask what it is for, and the graph cannot link it to
    an intent."""
    model = _model()
    passages = [{"position": 1, "serves_beats": []}]
    problems = intent_model.validate_serves_beats(passages, model)
    assert any("names no beat" in p for p in problems)


def test_a_passage_naming_a_beat_the_model_does_not_have_is_refused():
    """A passage naming a beat that does not exist is a claim with nothing
    under it - the beat it claims to serve is not in the model."""
    model = _model()
    passages = [{"position": 1, "serves_beats": ["beat:1", "beat:99"]}]
    problems = intent_model.validate_serves_beats(passages, model)
    assert any("beat:99" in p and "not in the intent model" in p
               for p in problems)


def test_a_passage_serving_no_beat_needs_no_beat_named():
    """When the model has no beats there is nothing to name, and the
    absence is the model's own statement - not a defect to invent a beat
    for."""
    passages = [{"position": 1, "serves_beats": []}]
    assert intent_model.validate_serves_beats(passages, {}) == []


# ── The queries a reader makes ────────────────────────────────────

def test_a_key_moment_inside_a_beats_span_is_found():
    model = _model()
    found = intent_model.moments_in_beat(model, "beat:1")
    assert [m["moment_id"] for m in found] == ["moment:1"]


def test_a_key_moment_outside_a_beats_span_is_not_found():
    model = _model()
    assert intent_model.moments_in_beat(model, "beat:2") == []


def test_setup_payoff_links_are_listed_in_both_directions():
    model = _model()
    links = intent_model.setup_payoff_links(model)
    assert ("beat:1", "setup", "beat:2") in links
    assert ("beat:2", "payoff", "beat:1") in links


def test_the_pacing_curve_names_the_energy_for_one_beat():
    model = _model()
    entry = intent_model.pacing_for_beat(model, "beat:2")
    assert entry["energy"] == 0.9
    assert intent_model.pacing_for_beat(model, "beat:42") == {}
