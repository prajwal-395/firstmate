"""The media-pool file-path index: one pool walk per placement pass.

`build_reel_timeline` looks every placed file up in the pool - footage,
cards, captions, overlays - and each lookup walked every folder calling
`GetClipProperty` per item. A pool holding thousands of renders turns
~100 lookups into hundreds of thousands of scripting calls, which is
what the Reel 26 rebuilds paid per placement. `PoolIndex` walks once
and answers from memory, in the same order the walk would have
returned; a path the snapshot never saw falls back to a live walk.

These tests count scripting calls on a fake pool, never wall-clock:
a reintroduction of per-item walks fails the counts, not a timer.
"""

from __future__ import annotations


class _Clip:
    def __init__(self, path, calls):
        self._path = path
        self._calls = calls

    def GetClipProperty(self, key):
        self._calls["prop"] += 1
        if key == "File Path":
            return self._path
        return ""


class _Folder:
    def __init__(self, name, calls):
        self._name = name
        self._calls = calls
        self._subs = []
        self._clips = []

    def GetName(self):
        return self._name

    def GetClipList(self):
        self._calls["clip_list"] += 1
        return list(self._clips)

    def GetSubFolderList(self):
        self._calls["sub_list"] += 1
        return list(self._subs)


class _Pool:
    """A pool of `n_items` across `n_bins`, plus one nested bin."""

    def __init__(self, n_items=200, n_bins=10):
        self.calls = {"clip_list": 0, "sub_list": 0, "prop": 0}
        self.root = _Folder("Root", self.calls)
        bins = []
        for b in range(n_bins):
            folder = _Folder(f"bin-{b:02d}", self.calls)
            self.root._subs.append(folder)
            bins.append(folder)
        nested = _Folder("nested", self.calls)
        bins[0]._subs.append(nested)
        for i in range(n_items):
            bins[i % n_bins]._clips.append(
                _Clip(f"/renders/r_{i:05d}.mov", self.calls))
        nested._clips.append(_Clip("/renders/nested.mov", self.calls))
        # A duplicated path: the refresh case keeps both items.
        bins[1]._clips.append(_Clip("/renders/dup.mov", self.calls))
        bins[2]._clips.append(_Clip("/renders/dup.mov", self.calls))
        # An image-sequence bracket path, filed under its frame dir.
        bins[3]._clips.append(
            _Clip("/renders/frames/frame-[00-10].png", self.calls))

    def GetRootFolder(self):
        return self.root


def _reset(pool):
    for key in pool.calls:
        pool.calls[key] = 0
    return pool.calls


def test_index_returns_pool_order_hits_duplicates_and_misses():
    from library.tools.reel_build import PoolIndex, pool_items_for

    pool = _Pool()
    index = PoolIndex(pool)
    live = pool_items_for(pool, "/renders/r_00007.mov")
    assert [c._path for c in index.items_for("/renders/r_00007.mov")] == [
        c._path for c in live]
    assert index.items_for("/renders/r_00007.mov")[0] is live[0]
    dups_live = pool_items_for(pool, "/renders/dup.mov")
    dups_index = index.items_for("/renders/dup.mov")
    assert len(dups_live) == len(dups_index) == 2
    assert [c._path for c in dups_index] == [c._path for c in dups_live]
    assert dups_index[0] is dups_live[0] and dups_index[1] is dups_live[1]
    assert index.items_for("/renders/no-such-file.mov") == []
    assert pool_items_for(pool, "/renders/no-such-file.mov") == []


def test_index_costs_one_walk_not_one_per_lookup():
    from library.tools.reel_build import PoolIndex, pool_item_for

    pool = _Pool(n_items=200, n_bins=10)
    _reset(pool)
    index = PoolIndex(pool)
    build_calls = dict(pool.calls)

    paths = [f"/renders/r_{i:05d}.mov" for i in range(0, 200, 2)]
    paths += ["/renders/no-such-file.mov"]
    _reset(pool)
    for path in paths:
        pool_item_for(pool, path, pool_index=index)
    indexed_calls = dict(pool.calls)

    # The misses fall back to one live walk each; every hit answers
    # from memory with no scripting call at all.
    assert indexed_calls["prop"] <= build_calls["prop"] + 200 + 1
    assert indexed_calls["clip_list"] <= build_calls["clip_list"] + 12

    _reset(pool)
    for path in paths:
        pool_item_for(pool, path)
    live_calls = dict(pool.calls)
    assert live_calls["prop"] > 10 * max(indexed_calls["prop"], 1), (
        "per-lookup walks must cost far more than the indexed path, "
        "or the regression check cannot see the slow algorithm return")


def test_index_miss_falls_back_to_a_live_walk():
    from library.tools.reel_build import PoolIndex, pool_item_for

    pool = _Pool()
    index = PoolIndex(pool)
    late = _Clip("/renders/late.mov", pool.calls)
    pool.root._subs[4]._clips.append(late)
    # Another lane pooled this after the snapshot was taken: the lookup
    # finds it live instead of reading the absence as final.
    assert pool_item_for(pool, "/renders/late.mov", pool_index=index) is late


def test_sequence_index_matches_the_live_directory_walk():
    from library.tools.reel_build import PoolIndex, pool_sequence_for

    pool = _Pool()
    index = PoolIndex(pool)
    live = pool_sequence_for(pool, "/renders/frames")
    assert live is not None
    assert index.sequence_for("/renders/frames") is live
    assert index.sequence_for("/renders") is pool_sequence_for(
        pool, "/renders")
    assert index.sequence_for("") is None
    assert pool_sequence_for(pool, "/renders/frames",
                             pool_index=None) is live


def test_pool_item_for_without_index_is_unchanged():
    from library.tools.reel_build import pool_item_for

    pool = _Pool()
    assert pool_item_for(
        pool, "/renders/r_00007.mov")._path == "/renders/r_00007.mov"
    assert pool_item_for(pool, "/renders/no-such-file.mov") is None
    assert pool_item_for(
        pool, "/renders/dup.mov")._path == "/renders/dup.mov"
