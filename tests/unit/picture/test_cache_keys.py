"""C05: cache reuse is predictable and never silently incompatible.

The defects these name:

* Four caches built keys four different ways, in four modules, and two
  of them served semantically incompatible results under one key: the
  derived-feature store keyed path-plus-mtime across a content change,
  and the vision frame cache keyed one directory whatever the sampling
  interval was.
* A stale entry was a missing entry: no key named what moved, so a
  reader could not tell a stale answer from an absent one.
* Lifecycle ``producer_key`` strings named neither model nor version,
  so two producers could file under one name.

Each test names one defect; the "before" column is what the key did
before the converged construction in :mod:`library.tools.cache_keys`.
No model loads here, no media is read: keys, digests and manifests only.
"""

import json

import pytest

from library.tools import analysis_cache, cache_keys
from library.tools.analysis import measurement_layers
from library.tools.shared_decode import (
    DECODE_PRODUCER_VERSION,
    Recipe,
    _read_manifest,
    recipe_configuration,
)
from library.tools.shared_decode import (
    _key as shared_decode_key,
)
from library.tools.video_features import cache as feature_cache


def test_every_builder_keys_the_same_four_components():
    """Before: four modules, four key shapes, no shared vocabulary.

    After: the analysis key IS the canonical key, and every other
    builder's output equals the canonical module's output for the same
    components - one construction, four call sites.
    """
    key = analysis_cache.AnalysisKey(
        source_identity="digest-1", model="owlv2_named",
        configuration="cfg-1", analysis_version="v1")
    assert isinstance(key, cache_keys.CacheKey)
    assert key.signature() == cache_keys.CacheKey(
        source_identity="digest-1", model="owlv2_named",
        configuration="cfg-1", analysis_version="v1").signature()

    payload = {"format": measurement_layers.STORE_FORMAT, "layer": "objects",
               "source": "digest-1", "method": "m1",
               "params": {"model_id": "m"}}
    assert (measurement_layers.layer_key("objects", "digest-1", "m1",
                                          {"model_id": "m"})
            == cache_keys.signature_of(payload))

    recipe = Recipe(rate_hz=5.0, width=180, height=320)
    assert shared_decode_key("digest-1", recipe) == (
        cache_keys.observation_key(
            source_identity="digest-1", model="ffmpeg-decode",
            configuration=recipe_configuration(recipe),
            analysis_version=DECODE_PRODUCER_VERSION).signature())

    digest_key = feature_cache.cache_key(
        source_path="/v/a.mp4", byte_size=10, mtime_ns=5,
        start=0.0, end=1.0, model_id="m", precision="fp32",
        source_digest="digest-1")
    assert digest_key == cache_keys.observation_key(
        source_identity="digest-1", model="m",
        configuration={"adapter": feature_cache.ADAPTER_VERSION,
                       "interval": {"start": 0.0, "end": 1.0},
                       "precision": "fp32"},
        analysis_version=feature_cache.ADAPTER_VERSION).signature()


def test_two_recipes_that_decode_different_pixels_have_different_keys():
    """Before: the shared-decode key named the recipe but not the
    producer version, so a command change served another command's
    frames.  After: every pixel-changing field moves the key."""
    base = Recipe(rate_hz=5.0, width=180, height=320)
    variants = [
        Recipe(rate_hz=2.0, width=180, height=320),
        Recipe(rate_hz=5.0, width=360, height=640),
        Recipe(rate_hz=5.0, width=180, height=320,
               pixel_format="rgb24"),
        Recipe(rate_hz=5.0, width=180, height=320,
               start_s=10.0, end_s=20.0),
    ]
    keys = {shared_decode_key("digest-1", base)}
    for variant in variants:
        key = shared_decode_key("digest-1", variant)
        assert key not in keys, f"{variant} shares the base recipe's key"
        keys.add(key)
    assert shared_decode_key("digest-2", base) not in keys


def test_a_decode_entry_from_another_command_is_refused(tmp_path):
    """Before: the manifest named the recipe but not the command that
    decoded it, so a command change kept serving old bytes.  After: a
    manifest from another producer version is a miss, not a hit."""
    from library.tools.shared_decode import SCHEMA

    recipe = Recipe(rate_hz=5.0, width=180, height=320)
    frames_path = tmp_path / "frames.raw"
    frames_path.write_bytes(b"\x00" * 180 * 320)
    manifest_path = tmp_path / "frames.json"
    manifest_path.write_text(json.dumps({
        "schema": SCHEMA, "content_digest": "digest-1",
        "key": "stale", "producer_version": "ren-shared-decode-producer/0",
        "signature": recipe.signature, "bytes": frames_path.stat().st_size,
        "frame_count": 1, "frame_bytes": recipe.frame_bytes,
    }))
    assert _read_manifest(manifest_path, frames_path, recipe,
                          "digest-1") is None


def test_a_decode_entry_filed_under_another_source_is_refused(tmp_path):
    """Before: nothing in the manifest bound the entry to its source
    beyond the directory name.  After: a digest mismatch is a miss."""
    from library.tools.shared_decode import SCHEMA

    recipe = Recipe(rate_hz=5.0, width=180, height=320)
    frames_path = tmp_path / "frames.raw"
    frames_path.write_bytes(b"\x00" * 180 * 320)
    manifest_path = tmp_path / "frames.json"
    manifest_path.write_text(json.dumps({
        "schema": SCHEMA, "content_digest": "digest-OTHER",
        "key": shared_decode_key("digest-OTHER", recipe),
        "producer_version": DECODE_PRODUCER_VERSION,
        "signature": recipe.signature, "bytes": frames_path.stat().st_size,
        "frame_count": 1, "frame_bytes": recipe.frame_bytes,
    }))
    assert _read_manifest(manifest_path, frames_path, recipe,
                          "digest-1") is None


def test_same_path_new_bytes_files_under_a_new_key():
    """Before: the feature store keyed path-plus-mtime, so a file
    replaced under an unchanged mtime served the old file's tensors.
    After: the content-addressed key moves with the bytes, and the
    same bytes file under one key whatever the path."""
    kwargs = {"start": 0.0, "end": 1.0, "model_id": "m", "precision": "fp32"}
    first = feature_cache.cache_key(
        source_path="/v/a.mp4", byte_size=10, mtime_ns=5,
        source_digest="digest-1", **kwargs)
    replaced = feature_cache.cache_key(
        source_path="/v/a.mp4", byte_size=10, mtime_ns=5,
        source_digest="digest-2", **kwargs)
    assert first != replaced
    moved = feature_cache.cache_key(
        source_path="/elsewhere/b.mp4", byte_size=99, mtime_ns=7,
        source_digest="digest-1", **kwargs)
    assert moved == first


def test_feature_keys_name_model_precision_and_interval():
    """A feature tensor is an observation of one model at one precision
    over one interval: any of those moving must move the key."""
    kwargs = {"source_path": "/v/a.mp4", "byte_size": 10, "mtime_ns": 5,
                "source_digest": "digest-1"}
    base = feature_cache.cache_key(start=0.0, end=1.0, model_id="m",
                                   precision="fp32", **kwargs)
    assert feature_cache.cache_key(start=0.0, end=2.0, model_id="m",
                                   precision="fp32", **kwargs) != base
    assert feature_cache.cache_key(start=0.0, end=1.0, model_id="m2",
                                   precision="fp32", **kwargs) != base
    assert feature_cache.cache_key(start=0.0, end=1.0, model_id="m",
                                   precision="fp16", **kwargs) != base


def test_frame_cache_intervals_do_not_share_a_directory():
    """Before: the fixed-interval frame cache wrote every interval into
    one directory, so two intervals' frames overwrote each other under
    the same names.  After: the interval is in the namespace."""
    from library.tools.analysis import vision_pipeline_v3 as v3

    five = v3.frame_cache_subdir(interval_s=5.0)
    ten = v3.frame_cache_subdir(interval_s=10.0)
    assert five != ten
    assert v3.frame_cache_subdir(interval_s=5.0) == five
    adaptive = v3.frame_cache_subdir(policy_version="p1",
                                     sample_times=[1.0, 2.0])
    assert adaptive != five
    assert (v3.frame_cache_subdir(policy_version="p1",
                                  sample_times=[1.0, 3.0]) != adaptive)
    with pytest.raises(ValueError):
        v3.frame_cache_subdir(sample_times=[1.0])


def test_an_observation_refuses_asking_context():
    """Before: nothing stopped a transcript-carrying measurement from
    being keyed as a source observation.  After: context in an
    observation payload is refused, and a context-free interpretation
    is refused too."""
    with pytest.raises(ValueError):
        cache_keys.observation_key(
            source_identity="d", model="m", analysis_version="v1",
            configuration={"transcript_slice": {"text": "hi"}})
    with pytest.raises(ValueError):
        cache_keys.interpretation_key(
            source_identity="d", model="m", analysis_version="v1",
            configuration={"rate_hz": 5.0}, context={})
    interpretation = cache_keys.interpretation_key(
        source_identity="d", model="m", analysis_version="v1",
        configuration={"rate_hz": 5.0},
        context={"prompt_version": "p1",
                 "transcript_slice": {"text": "hi"}})
    other = cache_keys.interpretation_key(
        source_identity="d", model="m", analysis_version="v1",
        configuration={"rate_hz": 5.0},
        context={"prompt_version": "p1",
                 "transcript_slice": {"text": "bye"}})
    assert interpretation.signature() != other.signature()


def test_windows_are_interpretations_objects_are_observations():
    """The windows layer carries its prompt inputs in its params; the
    detectors and the picture pass measure the source.  The kind says
    which reuse each entry allows."""
    assert measurement_layers.layer_kind("windows") == "interpretation"
    assert measurement_layers.layer_kind("window") == "interpretation"
    assert measurement_layers.layer_kind("objects") == "observation"
    assert measurement_layers.layer_kind("picture") == "observation"


def test_stale_entries_are_named_never_served():
    """Before: a stale entry read as a miss with no reason.  After:
    selective invalidation splits the store into the current entry
    and the stale ones, each stale one naming what moved."""
    wanted = cache_keys.CacheKey("d1", "m1", "cfg-1", "v1")
    entries = {
        wanted.signature(): {"key": wanted.to_dict(), "frames": {}},
        "other": {"key": {**wanted.to_dict(), "model": "m2"}},
        "keyless": {"frames": {}},
    }
    current, stale = cache_keys.selective_invalidate(entries, wanted)
    assert set(current) == {wanted.signature()}
    by_signature = {row["signature"]: row["differing"] for row in stale}
    assert by_signature["other"] == ["model"]
    assert set(by_signature["keyless"]) == set(cache_keys.KEY_COMPONENTS)


def test_detector_keys_move_with_phrases_rate_and_producer():
    """The detector cache is the analysis key: a pass that looked for
    other phrases, at another rate, or under another producer version
    is a different measurement."""
    from library.tools.analysis import object_grounding as og

    producer = og.PRODUCERS["owlv2_named"]
    base = og.analysis_key(source_digest="d1", producer=producer,
                           phrases=["cell phone"], sample_fps=1.0)
    assert (og.analysis_key(source_digest="d1", producer=producer,
                            phrases=["cup"], sample_fps=1.0)
            != base)
    assert (og.analysis_key(source_digest="d1", producer=producer,
                            phrases=["cell phone"], sample_fps=2.0)
            != base)
    block = og.analysis_key_block(base)
    assert block["signature"] == base.signature()
    assert (analysis_cache.AnalysisKey.from_dict(block["key"]) == base)


def test_producer_keys_name_model_and_version():
    """Before: ``f\"{digest}:{layer}:{name}\"`` let two producers file
    under one name.  After: the kind, source, model and version are all
    in the name, and a nameless one is refused."""
    first = cache_keys.producer_key("decoded_frame", "d1", "ffmpeg", "v1",
                                    "frames.raw")
    assert first != cache_keys.producer_key("decoded_frame", "d1",
                                            "ffmpeg", "v2", "frames.raw")
    assert first != cache_keys.producer_key("decoded_frame", "d1",
                                            "gemma", "v1", "frames.raw")
    with pytest.raises(ValueError):
        cache_keys.producer_key("decoded_frame", "d1", "", "v1")
