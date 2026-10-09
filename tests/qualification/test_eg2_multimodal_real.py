"""The installed checkpoint must encode both modalities in one real space."""
import numpy as np
import pytest

from library.tools.analysis.footage_query import (
    EG2_TEXT_DIM,
    load_eg2_multimodal_embedder,
)

pytestmark = pytest.mark.real_model


def test_embeddinggemma2_real_embeddings_share_one_multimodal_space():
    encode, backend = load_eg2_multimodal_embedder()
    assert encode is not None, backend
    examples = {
        "text": encode(["a red bicycle beside a brick building"],
                       modality="text", is_query=True),
        "image": encode([np.zeros((224, 224, 3), dtype="uint8")],
                        modality="image"),
        "video": encode([np.zeros((8, 224, 224, 3), dtype="uint8")],
                        modality="video"),
        "audio": encode([{"array": np.zeros(16000, dtype="float32"),
                          "sampling_rate": 16000}], modality="audio"),
    }
    for modality, vector in examples.items():
        assert vector.shape == (1, EG2_TEXT_DIM), modality
        assert np.isfinite(vector).all(), modality
        assert np.allclose(np.linalg.norm(vector, axis=1), 1.0, atol=1e-4), modality
    assert "image+audio+video" in backend
