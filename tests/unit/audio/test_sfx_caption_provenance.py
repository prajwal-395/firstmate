"""Generated SFX descriptions carry their source and uncertainty."""

from library.tools.analysis.sfx_pipeline import description_provenance


def test_audio_flamingo_caption_provenance_names_revision_license_and_limits():
    provenance = description_provenance("model.audio_flamingo_next")

    assert provenance["provider"] == \
        "nvidia/audio-flamingo-next-captioner-hf"
    assert provenance["revision"] == \
        "62aa63a66927f04f8308620f8e4b65696d3ea0a6"
    assert provenance["license"] == "NVIDIA OneWay Noncommercial"
    assert provenance["review_status"] == "unreviewed"
    assert "event timing" in provenance["uncertainty"]
