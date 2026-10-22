"""What a RESIDENT model needs, decided BEFORE it loads, and how it is admitted.

The captain's punchlist (VI-015) asks for four things this module owns:

* **Estimate memory requirements before loading heavyweight models.**  A
  resident model is admitted against a demand computed from the artifact
  that is about to be loaded: the weights on disk, plus the overhead this
  machine measured for a resident transformer, floored by the measured
  peak of that model's own pass.  The estimate is computed BEFORE the
  load, so a machine that cannot host the weights is told so instead of
  discovering it by swapping.
* **Avoid competing GPU-intensive passes that cannot coexist on the
  target machine.**  Every resident model carries a RAM demand, so the
  scheduler's running-demand sum refuses the second session instead of
  letting two model sessions and Resolve share a 24 GiB box.
* **Distinguish unavailable resources from model inference failures.**
  `failure_kind` names which of the two happened, so a reader never has
  to guess whether a stage degraded because the machine could not host
  the model or because the model failed on the footage.
* **Bounded recovery when model memory cannot be reserved.**  A refusal
  is retried a BOUNDED number of times, and a refusal that survives the
  retries ends the stage with the reason - never an unbounded wait and
  never a per-clip retry storm.

The scheduler itself is NOT new here: `library/tools/resource_scheduler.py`
still owns admission, and this module only decides WHAT is asked for and
HOW the answer is reported.  The profile's declared demand lives in the
scheduler's own table
(`resource_scheduler.MEASURED_MEMORY_RESERVATIONS_GB`); the estimate may
only RAISE it, never lower it below what was measured.

The registry is one enumeration: `RESIDENT_MODELS`.

The evidence for the rule is `docs/evidence/resident_model_admission.md`; the
measurement behind it is
`docs/benchmarks/2026-10-21-vi015-model-admission.md`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from library.tools import resource_scheduler

# ── the two ways a model stage can end ────────────────────────────────

MODEL_UNAVAILABLE = "model_unavailable"
"""The machine could not host the model.

The memory was not admitted: the demand did not fit the live headroom,
the machine was already paging, or the bounded wait for the reservation
ran out.  Nothing was measured, and the capability degrades - it does
not fail, and it does not pretend the model answered.
"""

INFERENCE_FAILED = "inference_failed"
"""The model was resident, and the inference itself failed.

The weights loaded and the pass ran; the footage (or the model) broke
it.  This is a different fact from `MODEL_UNAVAILABLE`, and a reader
that cannot tell them apart cannot tell a busy machine from a broken
model.
"""

#: Weight file suffixes that count toward a model's resident size.
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".gguf", ".npz", ".pt", ".pth",
                    ".ckpt")

#: The overhead a resident transformer costs ON TOP of its weights on
#: this machine: the runtime, the allocator, the buffers and the
#: compressed-memory charges macOS does not count in RSS.
#:
#: Measured 2026-10-15 on this 24 GiB box: the Gemma 4 12B 4-bit
#: weights are 6.28 GB on disk and the granted process tree reached a
#: 14.43 GB peak physical footprint (owner `mem-measure-gemma`, one
#: sampled grant) - a factor of 2.30.  The factor is a property of the
#: runtime and the hardware, not of the model, so it is applied to
#: every resident model's weights; the MEASURED PEAK of each model is
#: the floor under it, because an estimate that under-reserves is what
#: pages the machine to death.
OVERHEAD_FACTOR = 2.30


@dataclass(frozen=True)
class ResidentModel:
    """One model the pipeline keeps resident, and what it needs to be so.

    `resident_gb` is the FLOOR: the measured peak physical footprint of
    this model's own pass on this machine (with its sample count), or a
    declared ceiling when the pass has not been measured.  `basis` says
    which, so a reader never has to guess whether a number was measured
    or asserted.
    """

    key: str
    profile: str
    model_id: str
    resident_gb: float
    basis: str
    samples: int
    nested_profiles: tuple[str, ...] = ()

    @property
    def measured(self) -> bool:
        """Whether the floor is a measurement rather than a declaration."""
        return self.samples > 0


RESIDENT_MODELS: dict[str, ResidentModel] = {
    "gemma-4-12b": ResidentModel(
        key="gemma-4-12b",
        profile="semantics.analyse:model_memory",
        model_id="mlx-community/gemma-4-12b-it-4bit",
        resident_gb=15.0,
        basis=("measured peak physical footprint 14.43 GB over 1 sampled "
               "grant (2026-10-15), plus a margin; the weights are 6.28 GB "
               "on disk"),
        samples=1,
        nested_profiles=("semantics.analyse:inference",),
    ),
    "sam2.1-hiera-small": ResidentModel(
        key="sam2.1-hiera-small",
        profile="objects.segment:model_memory",
        model_id="facebook/sam2.1-hiera-small",
        resident_gb=11.0,
        basis=("measured peak physical footprint 10.14 GB over 8 sampled "
               "grants (2026-10-22), plus a margin; the peak tracks the "
               "clip's frame count, so the margin covers the tail"),
        samples=8,
        nested_profiles=("objects.segment:inference",),
    ),
    "htdemucs": ResidentModel(
        key="htdemucs",
        profile="music.analyse:model_memory",
        model_id="955717e8-8726e21a.th",
        resident_gb=2.0,
        basis=("measured peak physical footprint 1.83 GB over 7 sampled "
               "grants (2026-10-21, 30 s and 60 s of source audio), plus a "
               "margin; the checkpoint is 0.08 GB on disk and the rest is "
               "the separation's own buffers"),
        samples=7,
        nested_profiles=(),
    ),
}

#: How many times a refused reservation is tried before the stage gives
#: up.  A refusal is usually permanent (the machine cannot host the
#: model), so the bound is small: recovery exists for the transient
#: case, where the session that held the memory has just ended.
DEFAULT_ATTEMPTS = 2

#: The pause between those attempts, in seconds.
DEFAULT_BACKOFF_S = 5.0


def resident(key: str) -> ResidentModel:
    """The resident model under `key`; an unknown key raises."""
    try:
        return RESIDENT_MODELS[key]
    except KeyError:
        raise KeyError(
            f"model admission: unknown resident model {key!r}; known: "
            f"{', '.join(sorted(RESIDENT_MODELS))}") from None


def _hub_cache_dir() -> Path:
    """Where the local Hugging Face cache lives, if it does."""
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _torch_checkpoint_dir() -> Path:
    """Where torch keeps its downloaded checkpoints."""
    try:
        import torch

        return Path(torch.hub.get_dir()) / "checkpoints"
    except Exception:  # noqa: BLE001 - torch may be absent or broken
        return Path.home() / ".cache" / "torch" / "hub" / "checkpoints"


def weights_gb(model_id: str) -> float | None:
    """The size of a model's WEIGHTS on disk, in GB, or None.

    Measured before the load, from the artifact that is about to be
    loaded: a Hugging Face repository in the local cache, or a single
    checkpoint file.  None means the weights are not on this machine -
    an absent reading is never zero.
    """
    if not model_id:
        return None
    candidate = Path(model_id)
    if candidate.is_file():
        return candidate.stat().st_size / 2 ** 30
    repo = _hub_cache_dir() / ("models--" + model_id.replace("/", "--"))
    if not repo.is_dir():
        checkpoint = _torch_checkpoint_dir() / model_id
        if checkpoint.is_file():
            return checkpoint.stat().st_size / 2 ** 30
        return None
    total = 0
    snapshots = repo / "snapshots"
    if not snapshots.is_dir():
        return None
    for snapshot in snapshots.iterdir():
        if not snapshot.is_dir():
            continue
        for path in snapshot.rglob("*"):
            if path.is_file() and path.suffix in WEIGHT_SUFFIXES:
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
    if not total:
        return None
    return total / 2 ** 30


def estimate_gb(model: ResidentModel) -> float:
    """The resident memory to reserve for `model`, in GB.

    Computed BEFORE the load: the weights on disk times the overhead
    this machine measured, floored by the model's own measured peak.
    The estimate may only RAISE the demand above that floor; it never
    lowers it, because the floor is the peak that swaps the machine to
    death when it is missed.
    """
    weights = weights_gb(model.model_id)
    if weights is None:
        return model.resident_gb
    return max(model.resident_gb, weights * OVERHEAD_FACTOR)


def demand_gb(model: ResidentModel) -> float:
    """The RAM demand this model asks the scheduler for, in GB.

    Never below the profile's declared demand: the scheduler's table
    records what was MEASURED for that profile, and an estimate that
    disagreed with it would be a smaller reservation than the
    measurement.
    """
    declared = resource_scheduler.demand_for(model.profile).get("ram_gb", 0)
    return max(float(declared), estimate_gb(model))


def failure_kind(exc: BaseException) -> str:
    """Whether `exc` is an unavailable resource or a failed inference."""
    if isinstance(exc, (resource_scheduler.MemoryUnavailable,
                        resource_scheduler.Cancelled)):
        return MODEL_UNAVAILABLE
    return INFERENCE_FAILED


def admission(key: str, profile: str, *,
              timeout: float | None = None,
              attempts: int = DEFAULT_ATTEMPTS,
              backoff_s: float = DEFAULT_BACKOFF_S) -> dict:
    """The heavy-work reservation's arguments for `key` under `profile`.

    The caller passes them to `heavy_work_lock.heavy_work_reservation`,
    so the LOCK SITE stays visible to the contract audit that checks
    every heavy-work lock is declared by a capability's execution phase
    - and that audit reads the profile the site names, so the site has
    to name it. `profile` is therefore checked against the enumeration
    here: a site that names a profile the model does not belong to
    raises rather than reserving the wrong thing.

    `ram_gb` is the ESTIMATE of the model about to load, computed
    before the load; `attempts` bounds the recovery when the machine
    refuses the model.
    """
    model = resident(key)
    if model.profile != profile:
        raise ValueError(
            f"model admission: {key!r} is admitted under "
            f"{model.profile!r}, but the lock site named {profile!r}")
    return {
        "nested_profiles": model.nested_profiles,
        "timeout": timeout,
        "ram_gb": demand_gb(model),
        "attempts": attempts,
        "backoff_s": backoff_s,
    }
