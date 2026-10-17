"""MLX MiniLM encoder for sentence embeddings.

Uses mlx-lm weights (4-bit or bf16) with a custom BERT forward pass,
producing 384-dim mean-pooled embeddings compatible with the existing
PyTorch fp32 sentence-transformers embeddings.

Priority: MLX 4-bit -> MLX bf16 -> PyTorch fp32 (fallback).
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np

MLX_MINILM_4BIT = "mlx-community/all-MiniLM-L6-v2-4bit"
MLX_MINILM_BF16 = "mlx-community/all-MiniLM-L6-v2-bf16"
EMBED_DIM = 384
MAX_SEQ_LENGTH = 256


def _get_snapshot(model_id: str) -> Optional[str]:
    """Find the snapshot directory for a cached HF model."""
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    hub_dir = os.path.join(cache_dir, f"models--{model_id.replace('/', '--')}")
    if not os.path.exists(hub_dir):
        return None
    snapshots_dir = os.path.join(hub_dir, "snapshots")
    if not os.path.exists(snapshots_dir):
        return None
    for d in os.listdir(snapshots_dir):
        p = os.path.join(snapshots_dir, d)
        if os.path.isdir(p):
            return p
    return None


class MLXMiniLM:
    """MiniLM BERT encoder using MLX with mean pooling."""

    def __init__(self, model_id: str = MLX_MINILM_4BIT, quantized: bool = True):
        self.model_id = model_id
        self.quantized = quantized
        self.snapshot = _get_snapshot(model_id)
        if not self.snapshot:
            raise FileNotFoundError(
                f"Model {model_id} not found in HF cache. "
                f"Run: huggingface-cli download {model_id}"
            )

        import mlx.core as mx

        self.mx = mx
        self._load_weights()
        self._build_model()

    def _load_weights(self):
        from safetensors import safe_open

        path = os.path.join(self.snapshot, "model.safetensors")
        if self.quantized:
            with safe_open(path, framework="np") as f:
                self.raw_tensors = {k: f.get_tensor(k) for k in f.keys()}
        else:
            from safetensors.torch import load_file

            torch_tensors = load_file(path)
            self.raw_tensors = {}
            for k, t in torch_tensors.items():
                if "bfloat16" in str(t.dtype):
                    self.raw_tensors[k] = t.float().numpy()
                else:
                    self.raw_tensors[k] = t.numpy()

    def _dequantize(self, name: str):
        mx = self.mx
        w = mx.array(self.raw_tensors[f"{name}.weight"])
        scales = mx.array(self.raw_tensors[f"{name}.scales"])
        biases = mx.array(self.raw_tensors[f"{name}.biases"])
        return mx.dequantize(w, scales, biases, group_size=64, bits=4)

    def _get_weight(self, name: str):
        mx = self.mx
        if self.quantized:
            return self._dequantize(name)
        return mx.array(self.raw_tensors[f"{name}.weight"])

    def _get_bias(self, name: str):
        mx = self.mx
        key = f"{name}.bias"
        if key in self.raw_tensors:
            return mx.array(self.raw_tensors[key])
        return None

    def _build_model(self):
        mx = self.mx
        w = {}

        w["word_embeddings"] = self._get_weight("embeddings.word_embeddings")
        w["position_embeddings"] = self._get_weight("embeddings.position_embeddings")
        w["token_type_embeddings"] = self._get_weight("embeddings.token_type_embeddings")
        w["emb_ln_weight"] = mx.array(self.raw_tensors["embeddings.LayerNorm.weight"])
        w["emb_ln_bias"] = mx.array(self.raw_tensors["embeddings.LayerNorm.bias"])

        for i in range(6):
            p = f"encoder.layer.{i}"
            w[f"{p}.q_weight"] = self._get_weight(f"{p}.attention.self.query")
            w[f"{p}.q_bias"] = self._get_bias(f"{p}.attention.self.query")
            w[f"{p}.k_weight"] = self._get_weight(f"{p}.attention.self.key")
            w[f"{p}.k_bias"] = self._get_bias(f"{p}.attention.self.key")
            w[f"{p}.v_weight"] = self._get_weight(f"{p}.attention.self.value")
            w[f"{p}.v_bias"] = self._get_bias(f"{p}.attention.self.value")
            w[f"{p}.attn_out_weight"] = self._get_weight(f"{p}.attention.output.dense")
            w[f"{p}.attn_out_bias"] = self._get_bias(f"{p}.attention.output.dense")
            w[f"{p}.attn_ln_weight"] = mx.array(
                self.raw_tensors[f"{p}.attention.output.LayerNorm.weight"]
            )
            w[f"{p}.attn_ln_bias"] = mx.array(
                self.raw_tensors[f"{p}.attention.output.LayerNorm.bias"]
            )
            w[f"{p}.ffn_inter_weight"] = self._get_weight(f"{p}.intermediate.dense")
            w[f"{p}.ffn_inter_bias"] = self._get_bias(f"{p}.intermediate.dense")
            w[f"{p}.ffn_out_weight"] = self._get_weight(f"{p}.output.dense")
            w[f"{p}.ffn_out_bias"] = self._get_bias(f"{p}.output.dense")
            w[f"{p}.ffn_ln_weight"] = mx.array(
                self.raw_tensors[f"{p}.output.LayerNorm.weight"]
            )
            w[f"{p}.ffn_ln_bias"] = mx.array(
                self.raw_tensors[f"{p}.output.LayerNorm.bias"]
            )

        w["pooler_weight"] = self._get_weight("pooler.dense")
        w["pooler_bias"] = self._get_bias("pooler.dense")

        self.weights = w

    def _linear(self, x, weight, bias=None):
        mx = self.mx
        out = mx.matmul(x, mx.transpose(weight))
        if bias is not None:
            out = out + bias
        return out

    def _layer_norm(self, x, weight, bias, eps=1e-12):
        mx = self.mx
        mean = mx.mean(x, axis=-1, keepdims=True)
        var = mx.var(x, axis=-1, keepdims=True)
        return (x - mean) / mx.sqrt(var + eps) * weight + bias

    def _gelu(self, x):
        mx = self.mx
        return 0.5 * x * (
            1 + mx.tanh(mx.sqrt(2 / mx.pi) * (x + 0.044715 * x * x * x))
        )

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Encode texts into 384-dim L2-normalized embeddings."""
        mx = self.mx
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            encoded = tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=MAX_SEQ_LENGTH,
                return_tensors="np",
            )

            input_ids = mx.array(encoded["input_ids"].astype(np.int32))
            attention_mask = mx.array(encoded["attention_mask"].astype(np.int32))
            token_type_ids = mx.zeros_like(input_ids)

            emb = self._forward(input_ids, attention_mask, token_type_ids)
            all_embeddings.append(np.array(emb))

        if not all_embeddings:
            return np.zeros((0, EMBED_DIM), dtype="float32")
        return np.concatenate(all_embeddings, axis=0).astype("float32")

    def _forward(self, input_ids, attention_mask, token_type_ids):
        mx = self.mx
        w = self.weights

        seq_len = input_ids.shape[1]
        position_ids = mx.expand_dims(mx.arange(seq_len), 0)

        x = (
            w["word_embeddings"][input_ids]
            + w["position_embeddings"][position_ids]
            + w["token_type_embeddings"][token_type_ids]
        )
        x = self._layer_norm(x, w["emb_ln_weight"], w["emb_ln_bias"])

        mask = mx.expand_dims(mx.expand_dims(attention_mask, 1), 1)
        mask = (1 - mask) * -10000.0

        batch_size, seq_len, hidden = x.shape
        num_heads = 12
        head_dim = hidden // num_heads

        for i in range(6):
            p = f"encoder.layer.{i}"

            q = self._linear(x, w[f"{p}.q_weight"], w[f"{p}.q_bias"])
            k = self._linear(x, w[f"{p}.k_weight"], w[f"{p}.k_bias"])
            v = self._linear(x, w[f"{p}.v_weight"], w[f"{p}.v_bias"])

            q = mx.transpose(mx.reshape(q, (batch_size, seq_len, num_heads, head_dim)), (0, 2, 1, 3))
            k = mx.transpose(mx.reshape(k, (batch_size, seq_len, num_heads, head_dim)), (0, 2, 1, 3))
            v = mx.transpose(mx.reshape(v, (batch_size, seq_len, num_heads, head_dim)), (0, 2, 1, 3))

            scores = mx.matmul(q, mx.transpose(k, (0, 1, 3, 2))) / (head_dim**0.5)
            scores = scores + mask
            attn_weights = mx.softmax(scores, axis=-1)
            attn_output = mx.matmul(attn_weights, v)

            attn_output = mx.reshape(
                mx.transpose(attn_output, (0, 2, 1, 3)),
                (batch_size, seq_len, hidden),
            )

            attn_output = self._linear(
                attn_output, w[f"{p}.attn_out_weight"], w[f"{p}.attn_out_bias"]
            )
            x = x + attn_output
            x = self._layer_norm(x, w[f"{p}.attn_ln_weight"], w[f"{p}.attn_ln_bias"])

            ffn_hidden = self._linear(
                x, w[f"{p}.ffn_inter_weight"], w[f"{p}.ffn_inter_bias"]
            )
            ffn_hidden = self._gelu(ffn_hidden)
            ffn_output = self._linear(
                ffn_hidden, w[f"{p}.ffn_out_weight"], w[f"{p}.ffn_out_bias"]
            )
            x = x + ffn_output
            x = self._layer_norm(x, w[f"{p}.ffn_ln_weight"], w[f"{p}.ffn_ln_bias"])

        mask_expanded = mx.broadcast_to(
            mx.expand_dims(attention_mask, -1), x.shape
        )
        sum_embeddings = mx.sum(x * mask_expanded, axis=1)
        mask_sum = mx.sum(mask_expanded, axis=1)
        mean_embeddings = sum_embeddings / mask_sum

        norms = mx.sqrt(mx.sum(mean_embeddings**2, axis=-1, keepdims=True))
        mean_embeddings = mean_embeddings / norms

        mx.eval(mean_embeddings)
        return mean_embeddings


_mlx_encoder: Optional[MLXMiniLM] = None
_mlx_backend: Optional[str] = None


def get_mlx_encoder() -> Optional[MLXMiniLM]:
    """Get or create the global MLX MiniLM encoder, or None if unavailable."""
    global _mlx_encoder, _mlx_backend

    if _mlx_encoder is not None:
        return _mlx_encoder

    try:
        import mlx.core as mx  # noqa: F401
    except ImportError:
        return None

    for model_id, quantized, backend_name in [
        (MLX_MINILM_4BIT, True, "mlx-4bit"),
        (MLX_MINILM_BF16, False, "mlx-bf16"),
    ]:
        try:
            enc = MLXMiniLM(model_id, quantized=quantized)
            _mlx_encoder = enc
            _mlx_backend = backend_name
            return enc
        except Exception:
            continue

    return None


def mlx_encode(texts: list[str]) -> Optional[np.ndarray]:
    """Encode texts using the MLX MiniLM encoder, or None if unavailable."""
    enc = get_mlx_encoder()
    if enc is None:
        return None
    return enc.encode(texts)
