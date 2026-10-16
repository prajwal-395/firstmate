"""Deterministic benchmark metrics for reusable video features (TMP-033..041).

Stdlib only, so unit tests cover exactly what the benchmark reports:
rank AUC, average precision, precision@k, Spearman rank correlation,
cosine utilities and patch-token match scores. Held-out policy: these
helpers never see a split; the caller passes the labels.
"""

from __future__ import annotations

import math


def rank_auc(labels: list[int], scores: list[float]) -> float:
    """P(a positive scores above a negative); nan when single-class."""
    pos = [s for lab, s in zip(labels, scores) if lab == 1]
    neg = [s for lab, s in zip(labels, scores) if lab == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0
               for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def average_precision(labels: list[int], scores: list[float]) -> float:
    """Area under the precision-recall curve for this ranking."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    hits, total = 0, 0.0
    for rank, index in enumerate(order, start=1):
        if labels[index] == 1:
            hits += 1
            total += hits / rank
    return total / max(1, sum(labels))


def precision_at_k(labels: list[int], scores: list[float], k: int) -> float:
    """Fraction of the top-k that is positive."""
    if k <= 0:
        raise ValueError("k must be positive")
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return sum(labels[i] for i in order[:k]) / min(k, len(order))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    rank = 0
    while rank < len(order):
        tie = rank
        while (tie + 1 < len(order)
               and values[order[tie + 1]] == values[order[rank]]):
            tie += 1
        mean_rank = (rank + tie) / 2.0 + 1.0
        for pos in range(rank, tie + 1):
            out[order[pos]] = mean_rank
        rank = tie + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation; 0.0 when either side is constant."""
    if len(xs) != len(ys) or not xs:
        raise ValueError("spearman needs two non-empty equal-length series")
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    count = len(xs)
    mean_x, mean_y = sum(rx) / count, sum(ry) / count
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mean_x) ** 2 for a in rx)
                    * sum((b - mean_y) ** 2 for b in ry))
    return num / den if den else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two dense vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return dot / max(1e-12, norm)
