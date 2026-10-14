"""Estimated source signals with explicit lineage to their input audio.

HTDemucs is a music stem separator, not a dialogue diarizer. Its vocals
and accompaniment outputs are useful estimated signals; ambience and
per-speaker stems remain unavailable unless a producer can identify them.
The source recording is never modified or replaced.
"""
from __future__ import annotations

import hashlib
import resource
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np

MODEL = "htdemucs"
MODEL_SIGNATURE = "955717e8"
MODEL_CHECKPOINT = "955717e8-8726e21a.th"
METHOD = "demucs-infer/htdemucs"
ESTIMATED = "estimated"


def _project_artifact_ref(value: str) -> str:
    reference = PurePosixPath(value)
    if not value or reference.is_absolute() or ".." in reference.parts:
        raise ValueError("audio artifact reference must be project-relative")
    return reference.as_posix()


@dataclass(frozen=True)
class SeparatedSignal:
    """A derived audio signal whose source and estimated status are mandatory."""

    id: str
    kind: str
    source_signal_id: str
    status: str
    method: str
    model: str
    sample_rate: int
    samples: np.ndarray

    def record(self, *, samples_ref: str) -> dict:
        if not self.id or not self.source_signal_id or not samples_ref:
            raise ValueError("signal id, source signal id and sample reference are required")
        if self.status != ESTIMATED:
            raise ValueError("model-separated signals must be explicitly estimated")
        samples_ref = _project_artifact_ref(samples_ref)
        return {
            "id": self.id,
            "kind": self.kind,
            "source_signal_id": self.source_signal_id,
            "status": self.status,
            "method": self.method,
            "model": self.model,
            "sample_rate": self.sample_rate,
            "samples_ref": samples_ref,
        }


def cached_model_path(cache_dir: Path | None = None) -> Path:
    """Return the already-installed checkpoint or refuse without downloading."""
    if cache_dir is None:
        import torch
        cache_dir = Path(torch.hub.get_dir()) / "checkpoints"
    path = cache_dir / MODEL_CHECKPOINT
    if not path.is_file():
        raise FileNotFoundError(f"cached {MODEL} checkpoint missing: {path}; downloads are disabled")
    return path


def separate_waveform(waveform: np.ndarray, sample_rate: int, *,
                      source_signal_id: str, separator=None) -> dict[str, SeparatedSignal]:
    """Separate vocals and accompaniment using only the cached HTDemucs model.

    `ambience` and individual `speaker` signals are deliberately omitted:
    HTDemucs does not estimate either class. Callers must not treat the
    original mixture or a coarse stem as evidence for those signals.
    """
    if not source_signal_id:
        raise ValueError("source_signal_id is required for lineage")
    audio = np.asarray(waveform, dtype=np.float32)
    if audio.ndim != 2 or audio.shape[0] not in (1, 2) or audio.shape[1] == 0:
        raise ValueError("waveform must be non-empty channel-first mono or stereo audio")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    import torch

    if separator is None:
        from library.tools.heavy_work_lock import owns
        if not owns("music.analyse:model_memory"):
            raise RuntimeError(
                "HTDemucs inference requires heavy_work_lock profile "
                "music.analyse:model_memory (8 GB RAM ceiling)"
            )
        from demucs_infer.api import Separator

        checkpoint = cached_model_path()
        separator = Separator(model=MODEL_SIGNATURE,
                              repo=checkpoint.parent,
                              device="cpu", shifts=0, jobs=0, progress=False)
    _original, stems = separator.separate_tensor(
        torch.from_numpy(audio.copy()), sr=sample_rate)
    rate = int(separator.samplerate)
    # `vocals` is only a coarse music-trained candidate, not verified dialogue.
    vocals = stems["vocals"].detach().cpu().numpy().copy()
    accompaniment = np.stack([
        stems[name].detach().cpu().numpy()
        for name in stems if name != "vocals"
    ]).sum(axis=0)
    arrays = {"dialogue_candidate": vocals, "music_candidate": accompaniment}
    return {
        kind: SeparatedSignal(
            id=f"{source_signal_id}:{kind}", kind=kind,
            source_signal_id=source_signal_id, status=ESTIMATED,
            method=METHOD, model=MODEL, sample_rate=rate, samples=samples,
        )
        for kind, samples in arrays.items()
    }


def si_sdr(reference: np.ndarray, estimate: np.ndarray) -> float:
    """Scale-invariant SDR in dB, with strict shape and finite-value checks."""
    ref = np.asarray(reference, dtype=np.float64).reshape(-1)
    est = np.asarray(estimate, dtype=np.float64).reshape(-1)
    if ref.shape != est.shape or not ref.size or not np.isfinite(ref).all() or not np.isfinite(est).all():
        raise ValueError("SI-SDR inputs must be same-sized, non-empty and finite")
    ref = ref - ref.mean()
    est = est - est.mean()
    energy = float(ref @ ref)
    if energy <= np.finfo(np.float64).tiny:
        raise ValueError("SI-SDR reference must have non-zero energy")
    target = (float(est @ ref) / energy) * ref
    noise = est - target
    return float(10 * np.log10((float(target @ target) + 1e-12) /
                               (float(noise @ noise) + 1e-12)))


def bleed_db(estimate: np.ndarray, desired: np.ndarray,
             undesired: Mapping[str, np.ndarray]) -> float:
    """Interference energy relative to desired-source energy in a known fixture."""
    components = [np.asarray(desired, dtype=np.float64).reshape(-1)]
    labels = list(undesired)
    components.extend(np.asarray(undesired[k], dtype=np.float64).reshape(-1) for k in labels)
    output = np.asarray(estimate, dtype=np.float64).reshape(-1)
    if any(row.shape != output.shape for row in components):
        raise ValueError("bleed fixture stems and estimate must have identical shapes")
    matrix = np.column_stack(components)
    coefficients, *_ = np.linalg.lstsq(matrix, output, rcond=None)
    energies = np.square(coefficients) * np.sum(np.square(matrix), axis=0)
    return float(10 * np.log10((float(np.sum(energies[1:])) + 1e-12) /
                               (float(energies[0]) + 1e-12)))


def synthetic_c6_fixture(sample_rate: int = 44100, duration_s: float = 6.0,
                         seed: int = 6) -> dict[str, np.ndarray | int]:
    """Deterministic, labeled three-stem synthetic proxy; not real-footage gold."""
    if sample_rate <= 0 or duration_s <= 0:
        raise ValueError("sample_rate and duration_s must be positive")
    rng = np.random.default_rng(seed)
    t = np.arange(round(sample_rate * duration_s), dtype=np.float64) / sample_rate
    # A modulated harmonic voice proxy, a chord-like music bed, and quiet
    # broadband environmental noise. Labels describe construction, not truth
    # about any recorded footage.
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 2.1 * t) ** 2
    dialogue = envelope * (0.22 * np.sin(2 * np.pi * 180 * t)
                           + 0.10 * np.sin(2 * np.pi * 360 * t)
                           + 0.04 * np.sin(2 * np.pi * 540 * t))
    music = (0.14 * np.sin(2 * np.pi * 261.63 * t)
             + 0.11 * np.sin(2 * np.pi * 329.63 * t)
             + 0.09 * np.sin(2 * np.pi * 392.00 * t))
    ambience = 0.018 * rng.standard_normal(t.shape)
    stems = {"dialogue": dialogue, "music": music, "ambience": ambience}
    return {"sample_rate": sample_rate, **stems,
            "mixture": sum(stems.values()).astype(np.float32)}


def records_for_stems(stems: Mapping[str, SeparatedSignal], *,
                      artifact_refs: Mapping[str, str]) -> list[dict]:
    """Create sidecar-ready record metadata and refuse missing artifact refs."""
    if set(stems) != set(artifact_refs):
        raise ValueError("each separated signal needs exactly one durable artifact reference")
    return [stems[k].record(samples_ref=artifact_refs[k]) for k in sorted(stems)]


def write_separated_outputs(stems: Mapping[str, SeparatedSignal],
                            output_dir: str | Path, *,
                            artifact_prefix: str | Path = "") -> dict[str, str]:
    """Persist new WAVs and return project-relative refs without replacing input."""
    import soundfile as sf

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for kind, signal in stems.items():
        if signal.kind != kind or signal.status != ESTIMATED:
            raise ValueError("artifact key, signal kind and estimated status must agree")
    existing = [directory / f"{kind}.wav" for kind in stems
                if (directory / f"{kind}.wav").exists()]
    if existing:
        raise FileExistsError(f"refusing to replace derived audio artifact: {existing[0]}")
    prefix = Path(artifact_prefix)
    if prefix.is_absolute() or ".." in prefix.parts:
        raise ValueError("artifact_prefix must be a safe project-relative path")
    refs: dict[str, str] = {}
    for kind, signal in sorted(stems.items()):
        path = directory / f"{kind}.wav"
        audio = np.asarray(signal.samples).T
        with sf.SoundFile(path, mode="x", samplerate=signal.sample_rate,
                          channels=audio.shape[1], format="WAV",
                          subtype="FLOAT") as output:
            output.write(audio)
        refs[kind] = (prefix / path.name).as_posix()
    return refs


def project_model_signal(signal: SeparatedSignal, *, asset_id: str,
                         clock_id: str, start_tick: int, end_tick: int,
                         provenance_id: str, samples_ref: str) -> dict:
    """Adapt a derived signal to the canonical project model Signal record."""
    if start_tick >= end_tick:
        raise ValueError("signal interval must be non-empty")
    if not all((asset_id, clock_id, provenance_id, samples_ref)):
        raise ValueError("asset, clock, producer provenance and samples_ref are required")
    samples_ref = _project_artifact_ref(samples_ref)
    if (signal.status != ESTIMATED or not signal.id
            or not signal.source_signal_id):
        raise ValueError("project model estimated signals require ids and estimated status")
    return {
        "id": signal.id,
        "asset_id": asset_id,
        "interval": {"clock_id": clock_id, "start": start_tick, "end": end_tick},
        "measurement_type": f"audio.separated.{signal.kind}",
        "unit": "external_audio_samples",
        "samples": [],
        "sample_clock_id": clock_id,
        "provenance_id": provenance_id,
        "processing_status": "analyzed",
        "source_signal_id": signal.source_signal_id,
        "samples_ref": samples_ref,
        "estimate_status": signal.status,
    }


def benchmark_c6(*, separator=None) -> dict:
    """Measure HTDemucs on the deterministic synthetic C6 proxy fixture."""
    fixture = synthetic_c6_fixture()
    mixture = np.asarray(fixture["mixture"], dtype=np.float32)
    stereo = np.stack((mixture, mixture))
    separated = separate_waveform(stereo, int(fixture["sample_rate"]),
                                  source_signal_id="fixture-c6-mixture",
                                  separator=separator)
    truth = {name: np.asarray(fixture[name], dtype=np.float64)
             for name in ("dialogue", "music", "ambience")}
    estimates = {
        name: np.asarray(signal.samples, dtype=np.float64).mean(axis=0)
        for name, signal in separated.items()
    }
    mixture_estimate = mixture.astype(np.float64)
    before = {
        "dialogue_candidate": {
            "si_sdr_db": round(si_sdr(truth["dialogue"], mixture_estimate), 3),
            "bleed_db": round(bleed_db(
                mixture_estimate, truth["dialogue"],
                {"music": truth["music"], "ambience": truth["ambience"]}), 3),
        },
        "music_candidate": {
            "si_sdr_db": round(si_sdr(truth["music"], mixture_estimate), 3),
            "bleed_db": round(bleed_db(
                mixture_estimate, truth["music"],
                {"dialogue": truth["dialogue"], "ambience": truth["ambience"]}), 3),
        },
    }
    after = {
        "dialogue_candidate": {
            "interpreted_as": "estimated vocals stem",
            "si_sdr_db": round(si_sdr(truth["dialogue"], estimates["dialogue_candidate"]), 3),
            "bleed_db": round(bleed_db(
                estimates["dialogue_candidate"], truth["dialogue"],
                {"music": truth["music"], "ambience": truth["ambience"]}), 3),
            "status": ESTIMATED,
        },
        "music_candidate": {
            "interpreted_as": "estimated sum of non-vocal stems",
            "si_sdr_db": round(si_sdr(truth["music"], estimates["music_candidate"]), 3),
            "bleed_db": round(bleed_db(
                estimates["music_candidate"], truth["music"],
                {"dialogue": truth["dialogue"], "ambience": truth["ambience"]}), 3),
            "status": ESTIMATED,
        },
    }
    proxy_pass = all(
        after[name]["si_sdr_db"] > before[name]["si_sdr_db"]
        and after[name]["bleed_db"] < before[name]["bleed_db"]
        for name in before
    )
    return {
        "fixture": "C6 synthetic three-stem proxy",
        "fixture_labels": ["dialogue", "music", "ambience"],
        "sample_rate": int(fixture["sample_rate"]),
        "duration_seconds": round(len(mixture) / int(fixture["sample_rate"]), 3),
        "model": MODEL,
        "method": METHOD,
        "execution": {
            "device": "cpu",
            "heavy_work_profile": "music.analyse:model_memory",
            "ram_ceiling_gb": 8,
        },
        "checkpoint_size_bytes": cached_model_path().stat().st_size,
        "checkpoint_sha256": _checkpoint_sha256(),
        "peak_rss_bytes": _process_peak_rss_bytes(),
        "before_separation": {"signal": "original mixture", **before},
        "outputs": after,
        "proxy_pass": proxy_pass,
        "proxy_failure_reason": (
            None if proxy_pass else
            "each separated candidate must improve SI-SDR and reduce bleed vs the original mixture"
        ),
        "unsupported_outputs": {
            "ambience": "HTDemucs accompaniment cannot identify ambience separately",
            "speaker": "HTDemucs vocals does not identify individual speakers",
        },
        "real_footage_accuracy_measured": False,
        "limitations": [
            "Synthetic proxy is not a licensed annotated multitrack corpus.",
            "Dialogue is a music-trained vocals estimate, not speech-verified output.",
            "Original mixture is retained; no separated estimate replaces it.",
        ],
    }


def _checkpoint_sha256() -> str:
    with cached_model_path().open("rb") as checkpoint:
        return hashlib.file_digest(checkpoint, "sha256").hexdigest()


def _process_peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        help="write the JSON measurement report to this path")
    args = parser.parse_args(argv)
    report = benchmark_c6()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
