"""Defect guard: cache eviction must refuse incomplete value evidence."""

import pytest

from library.tools.cache_lifecycle import (
    CacheBudget,
    CachePolicyRefused,
    _retention_value,
)


def test_value_policy_refuses_unknown_usage_measurements():
    with pytest.raises(CachePolicyRefused, match="measurements are incomplete"):
        _retention_value({"identity": {"size_bytes": 1024}, "now_epoch": 10})


def test_budget_requires_separated_watermarks():
    with pytest.raises(ValueError, match="watermarks"):
        CacheBudget(total_bytes=1024, low_watermark=0.9,
                    high_watermark=0.8)
