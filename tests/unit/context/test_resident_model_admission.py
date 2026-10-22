"""VI-015: multimodal model admission - what a resident model needs, decided
BEFORE it loads, and what happens when the machine cannot host it.

Measured 2026-10-22 and 2026-10-23 on the captain's 24 GiB box: the
Gemma 4 12B 4-bit weights need 14.43 GB of physical footprint at their
peak, SAM 2 needs 10.14 GB, and the HTDemucs stem separation needs
1.83 GB. The machine has already panicked from memory pressure once (a
12-17 GB python beside Resolve), so admission has to account for
RESIDENT memory, not only for exclusive build directories.

The basis of every number here:
`docs/benchmarks/2026-10-22-prod006-resource-constrained.md` and
`docs/benchmarks/2026-10-23-vi015-model-admission.md`.
"""
import pytest

from library.tools import resident_model_admission, resource_scheduler

# ── The estimate is computed before the load ────────────────────────

def test_the_demand_is_computed_from_the_weights_that_are_about_to_load(
        monkeypatch):
    """The reservation is not a number typed beside the load: it is
    computed from the artifact that is about to be read, so a bigger
    checkpoint asks for more memory instead of overrunning the
    reservation it was given."""
    model = resident_model_admission.resident("gemma-4-12b")
    weights = resident_model_admission.weights_gb(model.model_id)
    if weights is not None:
        assert weights > 0, "the weights on disk are a real reading"
    monkeypatch.setattr(resident_model_admission, "weights_gb",
                        lambda _id: 20.0)
    assert resident_model_admission.demand_gb(model) == pytest.approx(
        20.0 * resident_model_admission.OVERHEAD_FACTOR)


def test_the_demand_is_never_below_the_measured_reservation():
    """The measured PEAK is the floor: an estimate that disagreed with
    it would be a smaller reservation than the measurement, and the
    tail is what swaps the machine to death."""
    for key, model in resident_model_admission.RESIDENT_MODELS.items():
        declared = resource_scheduler.demand_for(model.profile).get("ram_gb", 0)
        assert declared >= 1, f"{key} carries no memory demand at all"
        assert resident_model_admission.demand_gb(model) >= declared, key
        assert resident_model_admission.demand_gb(model) >= model.resident_gb, key


def test_every_resident_model_is_admitted_against_memory():
    """A resident model with no RAM demand is admitted on a machine
    that cannot host it - and then it loads beside the 15 GB VLM
    session that was refused. That is the panic this machine has
    already had, so every resident model carries a demand."""
    for key, model in resident_model_admission.RESIDENT_MODELS.items():
        demand = resource_scheduler.demand_for(model.profile)
        assert demand.get("ram_gb", 0) >= 1, key


# ── Two resident models cannot share this machine ───────────────────

@pytest.mark.real_scheduler_admission
def test_a_second_resident_model_is_not_admitted_beside_the_first(
        tmp_path, monkeypatch):
    """The running demands are summed, so the VLM's 15 GB and SAM 2's
    11 GB cannot both be resident on a 24 GiB box: the second one waits
    for memory the first is holding, and the bound ends the wait."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (20.0, 24.0))
    monkeypatch.setattr(resource_scheduler, "_swap_state_gb",
                        lambda: (1.0, 16.0))
    monkeypatch.setattr(resource_scheduler, "POLL_SECONDS", 0.02)
    scheduler = resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")
    token = scheduler.acquire("gemma", {"ram_gb": 15},
                              announce=lambda line: None)
    try:
        with pytest.raises(resource_scheduler.Cancelled):
            scheduler.acquire("sam 2", {"ram_gb": 11},
                              announce=lambda line: None, timeout=0.3)
        assert scheduler.jobs()[0]["owner"] == "gemma"
    finally:
        scheduler.release(token)


# ── The stem separation is admitted against a MEASUREMENT ──────────

def test_the_stem_separation_carries_its_measured_footprint():
    """The separator's own code asserted an "8 GB RAM ceiling" that was
    never measured, and the profile carried NO demand at all: the
    model loaded on a machine that could not host it. Measured 2026-10-23
    (1.83 GB peak physical footprint over 7 sampled grants), the demand
    is the measurement."""
    model = resident_model_admission.resident("htdemucs")
    assert model.measured, "the stem separation's footprint is measured"
    assert "measured peak physical footprint" in model.basis
    assert resource_scheduler.demand_for(model.profile) == {"ram_gb": 2}


# ── Bounded recovery when the memory cannot be reserved ─────────────

def test_a_refused_reservation_is_retried_a_bounded_number_of_times(
        monkeypatch):
    """The recovery is bounded: a machine that cannot host the model
    is told so after a fixed number of attempts, instead of waiting on
    memory it will never have (the stall PR #3027 paid for once)."""
    from library.tools import heavy_work_lock

    attempts = []

    def _refuse(owner, profile, nested_profiles, cancelled=None,
                timeout=None, ram_gb=None):
        attempts.append((profile, ram_gb))
        raise resource_scheduler.MemoryUnavailable(
            "resource scheduler: this machine is already paging")

    monkeypatch.setattr(heavy_work_lock, "take_heavy_memory_reservation",
                        _refuse)
    with pytest.raises(resource_scheduler.MemoryUnavailable), \
            heavy_work_lock.heavy_work_reservation(
                "test owner", "music.analyse:model_memory",
                nested_profiles=(), attempts=resident_model_admission.DEFAULT_ATTEMPTS,
                backoff_s=0.0, ram_gb=2.0):
        raise AssertionError("the body must never run")
    assert len(attempts) == resident_model_admission.DEFAULT_ATTEMPTS
    assert attempts[0] == ("music.analyse:model_memory", 2.0)


def test_the_admission_refuses_a_profile_the_model_does_not_belong_to():
    """The lock site has to name its profile literally (the contract
    audit reads it there), so the enumeration checks the name it is
    given: a site that names the wrong profile reserves the wrong
    thing."""
    with pytest.raises(ValueError):
        resident_model_admission.admission("htdemucs", "semantics.analyse:model_memory")


def test_a_refusal_is_reported_as_an_unavailable_resource():
    """A machine that cannot host the model and a model that failed on
    the footage are different facts, and the record says which."""
    assert resident_model_admission.failure_kind(
        resource_scheduler.MemoryUnavailable("no")) == \
        resident_model_admission.MODEL_UNAVAILABLE
    assert resident_model_admission.failure_kind(
        resource_scheduler.Cancelled("timed out")) == \
        resident_model_admission.MODEL_UNAVAILABLE
    assert resident_model_admission.failure_kind(RuntimeError("boom")) == \
        resident_model_admission.INFERENCE_FAILED


# ── The segmentation record names the kind ──────────────────────────

def test_the_segmentation_record_separates_an_unavailable_model_from_a_failed_one(
        tmp_path, monkeypatch):
    """A clip whose INFERENCE failed while the model was resident is a
    different fact from a machine that could not host the model, and
    the step's own output says which."""
    from library.capabilities.objects import segment as seg

    video = tmp_path / "clip.mov"
    video.write_bytes(b"not really a video")

    class _Segmenter:
        run_active = False

        def begin_run(self, **_kwargs):
            self.run_active = True

        def end_run(self):
            self.run_active = False

        def segment_clip(self, *_args, **_kwargs):
            raise RuntimeError("the tracker failed on this clip")

    monkeypatch.setattr(seg, "get_segmenter", lambda: _Segmenter())
    monkeypatch.setattr(seg, "heavy_work_reservation",
                        lambda *a, **k: _null_scope())

    result = seg.run_step(
        raw_footage_files=[],
        clip_catalog=[{"clip_id": "clip-1", "path": str(video)}],
        output_dir=str(tmp_path / "out"),
        color_grade_spec={"subject_grades": [{"clip_id": "clip-1"}]},
        face_boxes_by_clip={"clip-1": [0.1, 0.1, 0.5, 0.5]})

    failures = result["matte_trigger"]["failures"]
    assert failures == [{"clip_id": "clip-1",
                         "kind": resident_model_admission.INFERENCE_FAILED,
                         "reason": "the tracker failed on this clip"}]
    assert "memory_refusal" not in result["matte_trigger"]


def _null_scope():
    from contextlib import nullcontext

    return nullcontext()


# ── The semantic record names the kind too ──────────────────────────

def test_the_semantic_record_says_the_model_was_unavailable(tmp_path):
    """The vision child refused the WHOLE batch because the machine
    could not host the model (PR #3027's bounded wait). Every clip is
    then unmeasured for that one reason, and the record says the reason
    is an unavailable resource - not a failed inference."""
    import subprocess

    from library.capabilities.semantics import analyse
    from library.tools import vision_progress

    progress = vision_progress.VisionProgress(
        tmp_path / "vision_progress.json")
    progress.refuse("the vision model could not be reserved: this machine "
                    "did not free the memory the weights need",
                    scope="batch")

    def _run(_cmd, progress=None, progress_file=None):
        raise subprocess.CalledProcessError(1, "vision")

    seen = []
    analyse._analyse_missing(
        [str(tmp_path / "clip.mov")], ["python", "vision"],
        str(tmp_path), run=_run,
        on_clip_failure=lambda path, reason, kind: seen.append(
            (path, reason, kind)),
        progress_file=str(progress.path))

    assert len(seen) == 1
    _path, reason, kind = seen[0]
    assert "could not be reserved" in reason
    assert kind == resident_model_admission.MODEL_UNAVAILABLE


def test_the_semantic_stream_record_carries_the_kind(tmp_path):
    """The durable per-clip record says WHY the clip has no profile, so
    a reader never has to guess between a busy machine and a broken
    model."""
    from library.tools import semantic_profile_stream

    written = semantic_profile_stream.publish_missing_record(
        str(tmp_path), clip_id="clip-001",
        source_path=str(tmp_path / "clip.mov"), source_stem="clip",
        run_id="run-1", producer_code_hash="hash",
        reason="the vision model could not be reserved",
        kind=resident_model_admission.MODEL_UNAVAILABLE)
    record = semantic_profile_stream.read_record(
        str(tmp_path), clip_id="clip-001",
        source_path=str(tmp_path / "clip.mov"))
    assert record["kind"] == resident_model_admission.MODEL_UNAVAILABLE
    assert written.is_file()
