"""F03: repeated equivalent verification does not re-pay the VLM cost.

The defects these name:

* the same candidate asked twice paid twice: the verdict store had no
  key through the converged C05 vocabulary, so a repeat could not reuse
  across runs of the converged code;
* a verdict recorded before the convergence (legacy frame-times key,
  no family tag) was filed but invisible to the new lookup;
* an overlapping window - the same event re-queried with shifted
  bounds - missed the exact frame-times key and paid a full VLM call
  for frames already judged;
* a recorded "yes" was reused for a window that does not show its
  establishing frame, which would judge a window by a frame it never
  saw;
* two recorded verdicts disagreeing about the same frames were never
  noticed: the lookup missed both and paid without naming the conflict;
* a recipe change (prompt version) kept serving the old answer instead
  of re-verifying;
* two identical spans in one batch each paid a call.

No model loads here and no media is read: a scripted stub counts the
calls the real VLM would have taken, and frame files are faked.
"""

import contextlib
import json

import pytest

from library.tools import cache_keys, source_memory
from library.tools import span_verification as sv

STATEMENT = "covers his mouth with his hand"
DIGEST = "f03" + "0" * 61


def _model_id():
    from library.tools import vision_model

    return vision_model.MODEL_ID


class StubModel:
    """A VLM that answers from a script and counts what it was asked.

    The script is in call order: each entry is
    ``(answer, frame_1based, reason)``.  An empty script answers "no":
    a stub that runs dry must not invent a "yes".
    """

    def __init__(self, script=()):
        self._script = list(script)
        self.calls = []

    def analyze_images(self, images, prompt, max_tokens=120):
        self.calls.append({"n_images": len(images)})
        if self._script:
            answer, frame, reason = self._script.pop(0)
        else:
            answer, frame, reason = ("no", None, "the stub ran dry")
        return json.dumps({"answer": answer, "frame": frame, "reason": reason})


@pytest.fixture
def mem(monkeypatch, tmp_path):
    """A source memory root where every frame file exists, by construction."""

    def fake_frame_files(content_digest, root):
        times = [round(10.0 + i * 0.5, 3) for i in range(80)]
        times += [round(100.0 + i * 10.0, 3) for i in range(10)]
        return {t: f"/fake/{content_digest}/{t}.jpg" for t in times}

    monkeypatch.setattr(sv, "_frame_files", fake_frame_files)
    return {"digest": DIGEST, "root": tmp_path}


def _span(start, end, step=1.0):
    frames = [
        {"t": round(start + i * step, 3), "d": 0.0}
        for i in range(round((end - start) / step) + 1)
    ]
    return {"start": start, "end": end, "frames": frames}


def _answers(out):
    return [(v["answer"], v.get("frame_t")) for v in out]


def test_identical_spans_reuse_the_stored_verdict_and_file_the_c05_key(mem):
    """Before: a repeat asked the model again - the store had no C05 key.

    After: the cold run files the verdict under both the legacy key and
    the C05 verification key; the warm run serves it with zero calls,
    and the filed verdict carries the family and recipe version the
    reuse lookup reads.
    """
    model_id = _model_id()
    cold = StubModel([("yes", 2, "hand at mouth")])
    out = sv.verify_spans(
        mem["digest"], [_span(10, 13)], STATEMENT, root=mem["root"], model=cold
    )
    assert len(cold.calls) == 1
    assert _answers(out) == [("yes", 11.0)]
    assert out[0]["cached"] is False

    stored = sv.read_verdicts(mem["digest"], mem["root"])
    shown = [10.0, 11.0, 12.0, 13.0]
    legacy = sv.verdict_key(STATEMENT, shown, model_id)
    sig = sv.c05_signature(mem["digest"], sv.FAMILY_SPAN, STATEMENT, shown, model_id)
    assert legacy in stored, "the legacy key still files the verdict"
    assert sig in stored, "the cold run also files the C05 key"
    assert stored[sig].get("family") == sv.FAMILY_SPAN
    assert stored[sig].get("producer_version") == sv.PRODUCER_VERSION

    warm = StubModel()
    out2 = sv.verify_spans(
        mem["digest"], [_span(10, 13)], STATEMENT, root=mem["root"], model=warm
    )
    assert warm.calls == [], "the identical repeat pays no VLM call"
    assert _answers(out2) == _answers(out)
    assert out2[0]["cached"] is True


def test_legacy_verdicts_are_served_and_migrated_to_c05(mem):
    """Before: a verdict filed before the convergence stayed invisible.

    After: the legacy exact key still serves it, and the hit is migrated
    to the C05 key once - with the family tag the overlap scan reads -
    so the next run finds it through the converged vocabulary.
    """
    model_id = _model_id()
    shown = [10.0, 11.0, 12.0, 13.0]
    legacy = sv.verdict_key(STATEMENT, shown, model_id)
    seed = {
        "answer": "no",
        "frame": None,
        "frame_t": None,
        "reason": "seeded",
        "frames": shown,
        "statement": STATEMENT,
        "model": model_id,
        "prompt_version": sv.PROMPT_VERSION,
        "seconds": 4.0,
        "reply": "{}",
    }
    source_memory.write_json(
        source_memory.source_dir(mem["digest"], mem["root"])
        / source_memory.SLOT_VERDICTS,
        {"content_digest": mem["digest"], "verdicts": {legacy: seed}},
    )

    stub = StubModel()
    out = sv.verify_spans(
        mem["digest"], [_span(10, 13)], STATEMENT, root=mem["root"], model=stub
    )
    assert stub.calls == [], "the legacy verdict answers, no call is paid"
    assert out[0]["cached"] is True
    assert out[0]["answer"] == "no"

    stored = sv.read_verdicts(mem["digest"], mem["root"])
    sig = sv.c05_signature(mem["digest"], sv.FAMILY_SPAN, STATEMENT, shown, model_id)
    assert sig in stored, "the legacy hit migrated to the C05 key"
    assert stored[sig].get("family") == sv.FAMILY_SPAN


def test_overlapping_window_reuses_a_no_without_a_new_call(mem):
    """Before: a shifted window over judged frames missed the exact key.

    After: a recorded "no" over a superset of the requested frames
    decides the narrower window - every requested frame was judged
    false - so no call is paid.
    """
    cold = StubModel([("no", None, "nothing there")])
    sv.verify_spans(
        mem["digest"], [_span(10, 13)], STATEMENT, root=mem["root"], model=cold
    )
    assert len(cold.calls) == 1

    warm = StubModel()
    out = sv.verify_spans(
        mem["digest"], [_span(11, 12)], STATEMENT, root=mem["root"], model=warm
    )
    assert warm.calls == [], "the judged frames answer the narrower window"
    assert out[0]["answer"] == "no"
    assert out[0]["cached"] is True
    assert out[0]["reuse"] == "overlap"


def test_yes_reuses_only_when_its_establishing_frame_is_shown(mem):
    """A "yes" is evidence about one frame, not about the whole window.

    Reusing it for a window that does not show that frame would judge
    footage by a frame it never saw - so the narrower window that shows
    it reuses, and the shifted one that does not pays one fresh call.
    """
    cold = StubModel([("yes", 2, "hand at mouth")])
    sv.verify_spans(
        mem["digest"], [_span(20, 23)], STATEMENT, root=mem["root"], model=cold
    )
    assert len(cold.calls) == 1  # frame_t == 21.0

    showing = StubModel()
    out = sv.verify_spans(
        mem["digest"], [_span(21, 24)], STATEMENT, root=mem["root"], model=showing
    )
    assert showing.calls == []
    assert _answers(out) == [("yes", 21.0)]
    assert out[0]["reuse"] == "overlap"

    shifted = StubModel([("no", None, "fresh look, nothing there")])
    out2 = sv.verify_spans(
        mem["digest"], [_span(22, 25)], STATEMENT, root=mem["root"], model=shifted
    )
    assert len(shifted.calls) == 1, (
        "frame 21 is not shown, so the recorded yes decides nothing"
    )
    assert out2[0]["cached"] is False
    assert out2[0].get("reuse") is None


def test_conflicting_recorded_verdicts_reopen_with_one_fresh_call(mem):
    """Before: two disagreeing verdicts were invisible; the miss re-paid
    without saying why.

    After: a recorded "yes" and a recorded "no" that both decide the
    frames are contradictory evidence, so the question reopens with one
    fresh call marked reopened - serving either side silently would be a
    guess.
    """
    model_id = _model_id()
    verdict_no = {
        "answer": "no",
        "frame": None,
        "frame_t": None,
        "reason": "seeded no",
        "frames": [10.0, 11.0, 12.0, 13.0],
        "statement": STATEMENT,
        "model": model_id,
        "prompt_version": sv.PROMPT_VERSION,
        "family": sv.FAMILY_SPAN,
        "seconds": 4.0,
        "reply": "{}",
    }
    verdict_yes = {
        "answer": "yes",
        "frame": 1,
        "frame_t": 12.0,
        "reason": "seeded yes",
        "frames": [11.0, 12.0, 13.0, 14.0],
        "statement": STATEMENT,
        "model": model_id,
        "prompt_version": sv.PROMPT_VERSION,
        "family": sv.FAMILY_SPAN,
        "seconds": 4.0,
        "reply": "{}",
    }
    source_memory.write_json(
        source_memory.source_dir(mem["digest"], mem["root"])
        / source_memory.SLOT_VERDICTS,
        {
            "content_digest": mem["digest"],
            "verdicts": {"seed-no": verdict_no, "seed-yes": verdict_yes},
        },
    )

    stub = StubModel([("no", None, "fresh look settles it")])
    out = sv.verify_spans(
        mem["digest"], [_span(12, 13)], STATEMENT, root=mem["root"], model=stub
    )
    assert len(stub.calls) == 1, "the conflict reopens with exactly one call"
    assert out[0]["reopened"] == "conflicting_verdicts"
    assert out[0]["answer"] == "no"
    assert out[0]["cached"] is False


def test_recipe_change_reopens_verification(mem, monkeypatch):
    """A prompt-version bump is a new question, not a cache hit.

    Serving the old wording's answer under the new wording would be a
    stale verdict read as current - so the bump misses every key and
    pays one fresh call.
    """
    cold = StubModel([("yes", 1, "under the old wording")])
    sv.verify_spans(
        mem["digest"], [_span(10, 13)], STATEMENT, root=mem["root"], model=cold
    )
    assert len(cold.calls) == 1

    monkeypatch.setattr(sv, "PROMPT_VERSION", sv.PROMPT_VERSION + 1)
    fresh = StubModel([("no", None, "under the new wording")])
    out = sv.verify_spans(
        mem["digest"], [_span(10, 13)], STATEMENT, root=mem["root"], model=fresh
    )
    assert len(fresh.calls) == 1, "the new recipe re-verifies"
    assert out[0]["answer"] == "no", "the old answer is not served"
    assert out[0]["cached"] is False


def test_duplicate_spans_in_one_batch_cost_one_call(mem):
    """Two identical candidates in one request are one question.

    The second span's verdict is filed by the first span's call inside
    the same batch, so the batch pays once and both answers agree.
    """
    stub = StubModel([("yes", 3, "hand at mouth")])
    out = sv.verify_spans(
        mem["digest"],
        [_span(10, 13), _span(10, 13)],
        STATEMENT,
        root=mem["root"],
        model=stub,
    )
    assert len(stub.calls) == 1
    assert _answers(out) == [("yes", 12.0), ("yes", 12.0)]
    assert [v["cached"] for v in out] == [False, True]


def test_interval_overlap_reuses_within_the_same_spacing(mem):
    """The interval family reuses the same way, but the spacing is part
    of the question: the same frames at a different declared gap were
    asked under different prompt words, so they re-verify.
    """
    cold = StubModel([("no", None, "nothing there")])
    sv.verify_intervals(
        mem["digest"],
        [{"frames": [{"t": 100.0}, {"t": 110.0}, {"t": 120.0}]}],
        STATEMENT,
        gap_seconds=10.0,
        root=mem["root"],
        model=cold,
    )
    assert len(cold.calls) == 1

    same_gap = StubModel()
    out = sv.verify_intervals(
        mem["digest"],
        [{"frames": [{"t": 110.0}, {"t": 120.0}]}],
        STATEMENT,
        gap_seconds=10.0,
        root=mem["root"],
        model=same_gap,
    )
    assert same_gap.calls == []
    assert out[0]["answer"] == "no"
    assert out[0]["reuse"] == "overlap"

    other_gap = StubModel([("no", None, "fresh look")])
    out2 = sv.verify_intervals(
        mem["digest"],
        [{"frames": [{"t": 110.0}, {"t": 120.0}]}],
        STATEMENT,
        gap_seconds=5.0,
        root=mem["root"],
        model=other_gap,
    )
    assert len(other_gap.calls) == 1, (
        "a different declared spacing is a different question"
    )
    assert out2[0]["answer"] == "no"
    assert out2[0]["cached"] is False


def test_c05_key_names_source_model_prompt_and_window():
    """The converged key moves when - and only when - the question moves."""
    model_id = _model_id()
    base = {
        "source_identity": DIGEST,
        "model": model_id,
        "family": sv.FAMILY_SPAN,
        "prompt_version": sv.PROMPT_VERSION,
        "prompt_template": sv.PROMPT,
        "statement": STATEMENT,
        "frame_times": [10.0, 11.0, 12.0],
        "producer_version": sv.PRODUCER_VERSION,
        "preprocessing": sv.SPAN_PREPROCESSING,
        "sampling": sv.SPAN_SAMPLING,
    }
    first = cache_keys.verification_key(**base).signature()

    def moved(**overrides):
        params = dict(base)
        params.update(overrides)
        return cache_keys.verification_key(**params).signature()

    assert moved() == first
    assert moved(statement=STATEMENT + " twice") != first
    assert moved(frame_times=[10.0, 11.0, 13.0]) != first
    assert moved(model="another-model") != first
    assert moved(prompt_version=sv.PROMPT_VERSION + 1) != first
    assert moved(source_identity="0" * 64) != first
    # Case and surrounding whitespace are not a new question.
    assert moved(statement="  COVERS his Mouth With His HAND  ") == first

    other_model = cache_keys.verification_key(**{**base, "model": "another-model"})
    assert cache_keys.differing_components(
        {
            "source_identity": DIGEST,
            "model": model_id,
            "configuration": "x",
            "analysis_version": sv.PRODUCER_VERSION,
        },
        other_model,
    ) == ["model", "configuration"]


def test_a_decided_batch_skips_heavy_work_admission(mem, monkeypatch):
    """Before: a legacy-only store with a nonzero interval gap took the
    heavy-work lock on every batch - the lookup rebuilt the legacy key
    without the gap, missed, and only the in-loop exact key saved the
    model call.

    After: the pre-lock check uses the same lookup as the loop (gap
    included), so a batch every recorded verdict decides loads no model
    at all.  There is no model here to load; taking the lock
    anyway would serialize behind the machine's real heavy work for
    nothing.
    """
    model_id = _model_id()
    shown = [100.0, 110.0, 120.0]
    legacy = sv.interval_verdict_key(STATEMENT, shown, model_id,
                                      gap_seconds=10.0)
    seed = {"answer": "no", "frame": None, "frame_t": None,
            "reason": "seeded legacy interval", "frames": shown,
            "statement": STATEMENT, "model": model_id,
            "prompt_version": sv.INTERVAL_PROMPT_VERSION,
            "seconds": 13.5, "reply": "{}"}
    source_memory.write_json(
        source_memory.source_dir(mem["digest"], mem["root"])
        / source_memory.SLOT_VERDICTS,
        {"content_digest": mem["digest"], "verdicts": {legacy: seed}})

    entered = []

    @contextlib.contextmanager
    def no_lock(owner):
        entered.append(owner)
        yield

    monkeypatch.setattr("library.tools.heavy_work_lock.heavy_work_lock",
                          no_lock)
    out = sv.verify_intervals(mem["digest"], [{"frames": [{"t": t}
                                                             for t in shown]}],
                              STATEMENT, gap_seconds=10.0,
                              root=mem["root"], model=StubModel())
    assert entered == [], "no model is loaded for a decided batch"
    assert out[0]["cached"] is True
    assert out[0]["answer"] == "no"


def test_recorded_request_replay_costs_no_repeat_verification(mem, monkeypatch):
    """The F03 measurement: a recorded equivalent request, before vs after.

    The seed store is what a previous run filed (three judged windows).
    The repeat asks one window identically, two windows overlapping the
    judged frames, one window twice, and one window genuinely new.
    BEFORE is the legacy exact-key lookup only (the new step neutralised,
    which is exactly the old code path); AFTER is the full reuse.
    The answers must be identical and the call count must fall.
    """
    seed_spans = [_span(10, 13), _span(20, 23), _span(30, 33)]
    seed_script = [
        ("no", None, "seeded no"),
        ("yes", 2, "seeded yes"),
        ("no", None, "seeded no"),
    ]
    sv.verify_spans(
        mem["digest"],
        seed_spans,
        STATEMENT,
        root=mem["root"],
        model=StubModel(seed_script),
    )

    snapshot = sv.read_verdicts(mem["digest"], mem["root"])

    def restore():
        source_memory.write_json(
            source_memory.source_dir(mem["digest"], mem["root"])
            / source_memory.SLOT_VERDICTS,
            {"content_digest": mem["digest"], "verdicts": dict(snapshot)},
        )

    request = [
        _span(10, 13),  # identical repeat -> exact hit
        _span(21, 24),  # shifted, shows yes-frame 21 -> overlap
        _span(21, 24),  # duplicate in-batch -> filed by the above
        _span(31, 32),  # narrower, inside judged no -> overlap
        _span(40, 43),
    ]  # genuinely new -> one fresh call

    after = StubModel([("no", None, "fresh look at the new window")])
    out_after = sv.verify_spans(
        mem["digest"], request, STATEMENT, root=mem["root"], model=after
    )
    after_calls = len(after.calls)

    restore()
    monkeypatch.setattr(
        sv, "find_deciding_verdict", lambda recorded, **kw: ("none", None)
    )
    before = StubModel(
        [("yes", 1, "repaid yes"), ("no", None, "repaid"), ("no", None, "repaid")]
    )
    out_before = sv.verify_spans(
        mem["digest"], request, STATEMENT, root=mem["root"], model=before
    )
    before_calls = len(before.calls)

    assert _answers(out_after) == _answers(out_before), (
        "reuse must not change a single answer"
    )
    assert (before_calls, after_calls) == (3, 1), (
        "the repeat pays the new window once instead of re-paying "
        "the shifted, narrowed and duplicated ones"
    )
