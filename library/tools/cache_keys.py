"""C05: the ONE place a cache key is constructed.

Every cache in the pipeline answers the same question - "may a
measurement that was already made be used again" - and every one of
them used to answer it differently.  The analysis cache
(:mod:`library.tools.analysis_cache`) keyed by source, model,
configuration and analysis version; the layer cache
(:mod:`library.tools.analysis.measurement_layers`) by source, method
digest and parameters; the shared decode
(:mod:`library.tools.shared_decode`) by source and recipe signature;
the derived-feature store (:mod:`library.tools.video_features.cache`)
by file path and mtime; the vision frame cache by policy version and
sample times.  Two of those share no component vocabulary at all, and
two of them serve semantically incompatible results under one key
(the feature store's path+mtime key across a content change; the
frame cache's interval-blind directory).

So all key construction lives here, under ONE component vocabulary:

* ``source_identity`` - the content digest of the media, never a path
  and never a stat.  A re-recorded file never serves another file's
  measurements.
* ``model`` - the producer or model that answered, with its revision
  where the producer versions one.
* ``configuration`` - the digest of HOW the measurement was asked:
  the sampling recipe, the preprocessing, the prompt, the score
  threshold, the phrases.  Two requests that differ in any of them
  ask different questions.
* ``analysis_version`` - the version of the analysis itself: the
  producer's own version string, which changes when the method does.

And ONE distinction the punchlist's architectural rules require: a
reusable source OBSERVATION (boxes on a frame, decoded pixels, a
feature tensor - true of the source whatever project asked) versus a
context-dependent INTERPRETATION (a semantic window whose prompt
carries this clip's transcript slice and temporal boundaries).  An
observation key must not smuggle context in, and an interpretation
key must name the context it depends on, or a window measured under
one transcript is served under another.

``CacheKey`` is byte-identical to the analysis cache's
``AnalysisKey`` for the same inputs: converging on this module must
not invalidate the measurements already filed.  Keys that were
deficient (the feature store's, the frame cache's, the shared
decode's missing producer version) change bytes exactly once, to a
key that says what they measure - and a changed key is a MISS, never
a false hit, so the migration direction is safe.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

FORMAT = "ren-cache-keys/1"
"""The key vocabulary's own version, so a reader can refuse a key it does not know."""

KEY_COMPONENTS = ("source_identity", "model", "configuration",
                  "analysis_version")
"""The four things a cached result is reusable under, in the punch list's order."""

OBSERVATION = "observation"
"""A measurement true of the source whatever project asked: reusable across contexts."""

INTERPRETATION = "interpretation"
"""A measurement true only under its context (prompt, transcript slice, window): never reused without it."""

#: Configuration payload keys that name asking-context rather than the
#: source.  An observation carrying any of them is a misfiled
#: interpretation, and is refused rather than keyed.
_CONTEXT_KEYS = frozenset({"context", "transcript", "transcript_slice",
                            "prompt", "prompt_version", "window"})


def canonical_json(parts: Any, *, ensure_ascii: bool = True) -> str:
    """The canonical encoding every key digests: sorted keys, compact."""
    return json.dumps(parts, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=ensure_ascii)


def configuration_digest(parts: dict[str, Any]) -> str:
    """A digest of a configuration, by VALUE.

    Byte-identical to the analysis cache's digest for the same payload:
    the same question asked twice is the same digest, whatever module
    asks it.
    """
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str,
                   ensure_ascii=False).encode("utf-8")).hexdigest()


def signature_of(document: Any) -> str:
    """The digest of one key payload, canonically encoded.

    The single hash every converged builder files its entry under.
    The encoding matches the layer cache's historical one (compact
    separators, ``ensure_ascii=True``) so its stored keys do not move.
    """
    return hashlib.sha256(
        canonical_json(document, ensure_ascii=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheKey:
    """The four things a cached measurement is reusable under.

    ``source_identity`` is the content digest of the media (the
    source's own bytes).  ``model`` names the producer or model that
    answered.  ``configuration`` is the digest of how it was asked.
    ``analysis_version`` is the version of the analysis itself, which
    changes when the method changes.
    """

    source_identity: str
    model: str
    configuration: str
    analysis_version: str

    def signature(self) -> str:
        """The key's own digest: the name the entry is filed under."""
        joined = (f"{self.source_identity}|{self.model}|"
                  f"{self.configuration}|{self.analysis_version}")
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in KEY_COMPONENTS}

    @classmethod
    def from_dict(cls, document: Any) -> CacheKey | None:
        """The key a stored document carries, or None when it carries none.

        A document with a missing or empty component is not a key: it
        is a file that cannot be trusted to say what it measured, and
        treating it as one would serve it as current.
        """
        if not isinstance(document, dict):
            return None
        values = {}
        for name in KEY_COMPONENTS:
            value = document.get(name)
            if not isinstance(value, str) or not value.strip():
                return None
            values[name] = value
        return cls(**values)


def differing_components(cached: Any, wanted: CacheKey) -> list[str]:
    """Which of the four components of the key differ.

    An empty list means the entry is CURRENT for the wanted key.  A
    non-empty one names what moved, so a reader is told WHY the entry
    was not used instead of only that it was not - which is the whole
    of "selective invalidation".
    """
    key = cached if isinstance(cached, CacheKey) else CacheKey.from_dict(cached)
    if key is None:
        return list(KEY_COMPONENTS)
    return [name for name in KEY_COMPONENTS
            if getattr(key, name) != getattr(wanted, name)]


def selective_invalidate(entries: dict[str, dict], wanted: CacheKey
                         ) -> tuple[dict[str, dict], list[dict[str, Any]]]:
    """Split stored entries into the current one and the stale ones.

    Returns ``(current, stale)`` where ``current`` holds the single
    entry filed under ``wanted.signature()`` (or nothing) and
    ``stale`` names every other entry with the components that moved.
    Stale entries are REPORTED, never served: the caller measures
    again and may then evict what it names.
    """
    current: dict[str, dict] = {}
    stale: list[dict[str, Any]] = []
    hit = entries.get(wanted.signature())
    if isinstance(hit, dict) and CacheKey.from_dict(hit.get("key")) == wanted:
        current[wanted.signature()] = hit
    for signature, entry in entries.items():
        if signature == wanted.signature() and signature in current:
            continue
        if not isinstance(entry, dict):
            continue
        stale.append({"signature": signature,
                      "differing": differing_components(entry.get("key"),
                                                        wanted)})
    return current, stale


# ── the component vocabulary ──────────────────────────────────────────
#
# The five things the acceptance names - model, prompt, preprocessing,
# sampling recipe, producer semantics - as normalized mappings a
# caller merges into its configuration payload.  Each names its own
# version so a change to it moves the digest.


def model_component(model_id: str, revision: str | None = None,
                    **extra: Any) -> dict[str, Any]:
    """The producer that answered, and the weights it answered with."""
    payload: dict[str, Any] = {"model_id": str(model_id)}
    if revision is not None:
        payload["revision"] = str(revision)
    payload.update(extra)
    return {"model": payload}


def prompt_component(prompt_version: str, template_digest: str | None = None,
                     **extra: Any) -> dict[str, Any]:
    """The prompt an interpretation was asked under.

    An observation carrying this belongs under
    :func:`interpretation_key`, never under :func:`observation_key`.
    """
    payload: dict[str, Any] = {"prompt_version": str(prompt_version)}
    if template_digest is not None:
        payload["template_digest"] = str(template_digest)
    payload.update(extra)
    return {"prompt": payload}


def preprocessing_component(**fields: Any) -> dict[str, Any]:
    """How the source was prepared before it was measured.

    The decode geometry, the pixel format, the JPEG quality, the frame
    size - everything between the stored bytes and the measured pixels.
    Two lanes that preprocess differently have not measured the same
    thing.
    """
    return {"preprocessing": dict(fields)}


def sampling_component(rate_hz: float | None = None,
                       start_s: float | None = None,
                       end_s: float | None = None,
                       sample_times: list[float] | None = None,
                       **extra: Any) -> dict[str, Any]:
    """Which seconds of the source a measurement stands for."""
    payload: dict[str, Any] = dict(extra)
    if rate_hz is not None:
        payload["rate_hz"] = float(rate_hz)
    if start_s is not None:
        payload["start_s"] = float(start_s)
    if end_s is not None:
        payload["end_s"] = float(end_s)
    if sample_times is not None:
        payload["sample_times"] = [float(t) for t in sample_times]
    return {"sampling": payload}


def producer_component(producer_id: str, producer_version: str,
                       code_digest: str | None = None,
                       **extra: Any) -> dict[str, Any]:
    """The pipeline producer's own semantics: who measured, which method."""
    payload: dict[str, Any] = {"producer_id": str(producer_id),
                               "producer_version": str(producer_version)}
    if code_digest is not None:
        payload["code_digest"] = str(code_digest)
    payload.update(extra)
    return {"producer": payload}


def observation_key(*, source_identity: str, model: str,
                    configuration: str | dict[str, Any],
                    analysis_version: str) -> CacheKey:
    """A reusable source observation: true of the source, whoever asked.

    ``configuration`` is the digest, or the payload it is digested
    from.  A payload carrying asking-context (transcript, prompt,
    window) is REFUSED: that is an interpretation, and keying it here
    would serve one project's reading as another's measurement.
    """
    if isinstance(configuration, dict):
        offender = sorted(_CONTEXT_KEYS.intersection(configuration))
        if offender:
            raise ValueError(
                f"observation key carries interpretation context "
                f"{offender}: use interpretation_key")
        configuration = configuration_digest(configuration)
    return CacheKey(source_identity=str(source_identity), model=str(model),
                    configuration=str(configuration),
                    analysis_version=str(analysis_version))


def interpretation_key(*, source_identity: str, model: str,
                       configuration: str | dict[str, Any],
                       analysis_version: str,
                       context: dict[str, Any]) -> CacheKey:
    """A context-dependent interpretation: reusable only under its context.

    ``context`` is the asking-context the answer depends on - the
    prompt version, the transcript slice, the window bounds - and it is
    folded into the configuration digest, so two windows that differ
    only in their prompt inputs never share a key.  An empty context
    is refused: a context-dependent answer with no context named is
    an observation wearing the wrong kind.
    """
    if not isinstance(context, dict) or not context:
        raise ValueError(
            "interpretation key needs a non-empty context mapping")
    if isinstance(configuration, dict):
        configuration = dict(configuration)
        configuration["context"] = context
        configuration = configuration_digest(configuration)
    else:
        configuration = configuration_digest(
            {"configuration": configuration, "context": context})
    return CacheKey(source_identity=str(source_identity), model=str(model),
                    configuration=str(configuration),
                    analysis_version=str(analysis_version))


def verification_context(*, family: str, prompt_version: Any,
                           prompt_template: str, statement: str,
                           frame_times: Sequence[float] | None,
                           extra: dict[str, Any] | None = None
                           ) -> dict[str, Any]:
    """The asking-context a VLM verdict depends on.

    A verification verdict is an INTERPRETATION: the same frames under a
    different prompt version, a different statement or a different shown
    frame set are different questions, and the key must say so.  The
    statement is normalized (case and whitespace), because two callers
    that differ only in spacing asked the same question.  The frame
    times are what the model was SHOWN - the window the verdict is
    about - rounded the way the verifier files them.  ``family`` keeps
    the span and interval prompt families apart: they judge different
    claims under different prompts, so a verdict of one is never an
    answer of the other even when the frame times match.
    """
    if not str(family or "").strip():
        raise ValueError("a verification context needs its prompt family")
    template_digest = hashlib.sha256(
        str(prompt_template).encode("utf-8")).hexdigest()[:16]
    context: dict[str, Any] = {
        "family": str(family).strip(),
        "prompt_version": prompt_version,
        "template_digest": template_digest,
        "statement": " ".join(str(statement or "").strip().lower().split()),
        "frame_times": ([round(float(t), 3) for t in frame_times]
                          if frame_times is not None else []),
    }
    if extra:
        context.update(dict(extra))
    return context


def verification_key(*, source_identity: str, model: str, family: str,
                     prompt_version: Any, prompt_template: str,
                     statement: str, frame_times: Sequence[float] | None,
                     producer_version: str,
                     preprocessing: dict[str, Any] | None = None,
                     sampling: dict[str, Any] | None = None,
                     extra_context: dict[str, Any] | None = None
                     ) -> CacheKey:
    """A reusable VLM verification verdict, through the ONE vocabulary.

    The key names the source CONTENT (``source_identity`` is the content
    digest, never a path), the model that answered, HOW it was asked
    (prompt version and template, the statement, the shown frames, the
    preprocessing and the frame-selection recipe) and the producer
    version that files it.  A recipe change - a new prompt version, a
    retrained model, different shown frames, a different selection rule -
    moves the key, so a stale verdict is a MISS, never a false hit.
    """
    configuration: dict[str, Any] = {
        "producer": {"producer_id": "span-verification",
                       "producer_version": str(producer_version)},
    }
    if preprocessing is not None:
        configuration["preprocessing"] = dict(preprocessing)
    if sampling is not None:
        configuration["sampling"] = dict(sampling)
    return interpretation_key(
        source_identity=source_identity, model=model,
        configuration=configuration,
        analysis_version=str(producer_version),
        context=verification_context(
            family=family, prompt_version=prompt_version,
            prompt_template=prompt_template, statement=statement,
            frame_times=frame_times, extra=extra_context))


def producer_key(kind: str, source_identity: str, model: str,
                 version: str, extra: str = "") -> str:
    """The canonical lifecycle ``producer_key`` for one cache entry.

    The ad-hoc ``f"{digest}:{layer}:{name}"`` strings named neither
    the model nor the version, so two producers could file under one
    name.  The kind, the source, the model and the version are all in
    the name; a version change files elsewhere rather than overwriting.
    """
    if not all(isinstance(part, str) and part.strip()
               for part in (kind, source_identity, model, version)):
        raise ValueError(
            "producer key needs a kind, a source, a model and a version")
    tail = f":{extra.strip()}" if extra.strip() else ""
    return f"{kind.strip()}:{source_identity.strip()}:{model.strip()}:{version.strip()}{tail}"
