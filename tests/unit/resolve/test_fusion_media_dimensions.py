"""Fusion execution uses the media dimensions Resolve actually reports."""

import pytest

from library.tools.edit_algebra import _fusion_media_dimensions


class _PoolItem:
    def __init__(self, properties):
        self.properties = properties

    def GetClipProperty(self, key):
        return self.properties.get(key)


class _TimelineItem:
    def __init__(self, pool_item):
        self.pool_item = pool_item

    def GetMediaPoolItem(self):
        return self.pool_item


def test_fusion_comp_uses_reported_portrait_media_dimensions():
    item = _TimelineItem(_PoolItem({"Resolution": "1080x1920"}))

    assert _fusion_media_dimensions(item) == (1080, 1920)


def test_fusion_comp_refuses_when_media_dimensions_are_unavailable():
    item = _TimelineItem(_PoolItem({}))

    with pytest.raises(RuntimeError, match="refusing to build a comp"):
        _fusion_media_dimensions(item)
