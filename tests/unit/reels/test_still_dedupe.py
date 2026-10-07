"""Duplicate stills collapse onto one pool item; nothing else moves.

Tonight's `still-overlays` pass left the TV frame as many separate
stills. These tests prove the pure half offline: pixel hashing merges
what file bytes would not, the plan points every duplicate at one
canonical item, and the judge refuses anything the re-points do not
own. Each fails on main, where `still_content` and `still_dedupe`
do not exist.
"""

import struct
import zlib

import pytest

from library.tools import still_content
from library.tools import still_dedupe


def _png(path, rgb, text=None):
    """A minimal truecolour PNG: `rgb` per pixel, optional tEXt chunk."""
    def chunk(kind, data):
        blob = kind + data
        return (struct.pack(">I", len(data)) + blob
                + struct.pack(">I", zlib.crc32(blob) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgb) * 2 for _ in range(2))
    out = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)))
    if text is not None:
        out += chunk(b"tEXt", b"comment\x00" + text.encode("latin-1"))
    out += chunk(b"IEND", b"")
    path.write_bytes(out)
    return str(path)


def test_same_pixels_different_metadata_share_a_digest(tmp_path):
    first = _png(tmp_path / "a.png", (32, 80, 192), text="reel-07")
    second = _png(tmp_path / "b.png", (32, 80, 192), text="reel-13")
    assert first != second
    assert (tmp_path / "a.png").read_bytes() != (
        tmp_path / "b.png").read_bytes()
    assert still_content.hash_pixels(first) == still_content.hash_pixels(
        second)


def test_different_pixels_hash_differently(tmp_path):
    first = _png(tmp_path / "a.png", (32, 80, 192))
    second = _png(tmp_path / "b.png", (33, 80, 192))
    assert still_content.hash_pixels(first) != still_content.hash_pixels(
        second)


def test_non_png_falls_back_to_file_bytes(tmp_path):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"\x00" * 64)
    second.write_bytes(b"\x00" * 64)
    assert still_content.hash_pixels(str(first)) == still_content.hash_pixels(
        str(second))
    second.write_bytes(b"\x01" * 64)
    assert still_content.hash_pixels(str(first)) != still_content.hash_pixels(
        str(second))


def test_unreadable_still_refuses(tmp_path):
    with pytest.raises(still_content.DedupeRefused):
        still_content.hash_pixels(str(tmp_path / "missing.png"))


def test_content_name_is_stable():
    assert (still_content.content_still_filename("ab" * 32)
            == still_content.content_still_filename("ab" * 32))
    assert still_content.content_still_filename("ab" * 32).endswith(".png")


def _item(timeline, record, pool_uid, path, digest, duration=None):
    return {"timeline": timeline, "record": list(record),
            "pool_uid": pool_uid, "file_path": path, "digest": digest,
            "duration": (duration if duration is not None
                         else record[1] - record[0]),
            "props": {}, "markers": {}}


def test_identical_stills_plan_one_canonical_repoint():
    digest = "d" * 64
    plan = still_content.plan_dedupe([
        _item("Reel 13", [0, 100], "uid-b", "/proj/b_still.png", digest),
        _item("Reel 07", [0, 100], "uid-a", "/proj/a_still.png", digest),
    ])
    assert len(plan["groups"]) == 1
    assert plan["groups"][0]["members"] == 2
    assert len(plan["repoints"]) == 1
    repoint = plan["repoints"][0]
    assert repoint["canonical_file"] == "/proj/a_still.png"
    assert repoint["file_path"] == "/proj/b_still.png"
    assert plan["skipped"] == []


def test_same_file_twice_is_nothing_to_collapse():
    digest = "d" * 64
    plan = still_content.plan_dedupe([
        _item("Reel 07", [0, 100], "uid-a", "/proj/a_still.png", digest),
        _item("Reel 07", [553, 653], "uid-a", "/proj/a_still.png", digest),
    ])
    assert plan["groups"] == [] and plan["repoints"] == []


def test_unhashable_item_is_skipped_never_merged():
    plan = still_content.plan_dedupe([
        _item("Reel 07", [0, 100], "uid-a", "/proj/a_still.png", ""),
        _item("Reel 13", [0, 100], "uid-b", "/proj/b_still.png", ""),
    ])
    assert plan["groups"] == [] and plan["repoints"] == []
    assert len(plan["skipped"]) == 2


def test_different_spans_each_keep_their_span():
    """A re-point preserves its own record span, so the same still
    held longer on another reel re-points alike - spans are never
    compared."""
    digest = "d" * 64
    plan = still_content.plan_dedupe([
        _item("Reel 07", [0, 100], "uid-a", "/proj/a_still.png",
              digest),
        _item("Reel 13", [0, 200], "uid-b", "/proj/b_still.png",
              digest),
    ])
    assert len(plan["repoints"]) == 1
    assert plan["repoints"][0]["record"] == [0, 200]


def _slim(name, record, path, uid, props=None, markers=None):
    return {"name": name, "record": list(record), "mpi_path": path,
            "pool_uid": uid, "props": props or {},
            "markers": markers or {}}


def test_clean_repoint_judges_clean():
    before = {"V7:Frame": [
        _slim("b_still.png", [553, 653], "/proj/b_still.png", "uid-b")]}
    after = {"V7:Frame": [
        _slim("b_still.png", [553, 653], "/proj/a_still.png", "uid-b")]}
    repoints = [{"row": "V7:Frame", "record": [553, 653],
                 "canonical_file": "/proj/a_still.png"}]
    assert still_dedupe.judge_dedupe(before, after, repoints) == []


def test_moved_non_target_item_refuses():
    before = {"V7:Frame": [
        _slim("b_still.png", [553, 653], "/proj/b_still.png", "uid-b")],
        "V1:Speech": [
        _slim("shot.mov", [0, 100], "/media/shot.mov", "uid-s")]}
    after = {"V7:Frame": [
        _slim("b_still.png", [553, 653], "/proj/a_still.png", "uid-b")],
        "V1:Speech": [
        _slim("shot.mov", [0, 99], "/media/shot.mov", "uid-s")]}
    repoints = [{"row": "V7:Frame", "record": [553, 653],
                 "canonical_file": "/proj/a_still.png"}]
    problems = still_dedupe.judge_dedupe(before, after, repoints)
    assert len(problems) == 1 and "V1:Speech" in problems[0]


class _FakeClip:
    def __init__(self, kind, path, uid):
        self._kind, self._path, self._uid = kind, path, uid

    def GetClipProperty(self, key):
        return {"Type": self._kind, "File Path": self._path}[key]

    def GetUniqueId(self):
        return self._uid


class _FakeFolder:
    def __init__(self, clips=(), subs=()):
        self._clips, self._subs = clips, subs

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _FakePool:
    def __init__(self, root):
        self._root = root

    def GetRootFolder(self):
        return self._root


def test_digest_index_merges_identical_stills(tmp_path):
    from library.tools import still_overlays

    first = _png(tmp_path / "tv_a_still.png", (10, 10, 10))
    second = _png(tmp_path / "tv_b_still.png", (10, 10, 10))
    pool = _FakePool(_FakeFolder(clips=(
        _FakeClip("Still", first, "uid-a"),
        _FakeClip("Still", second, "uid-b"),
        _FakeClip("Video", str(tmp_path / "card.mov"), "uid-v"),
    )))
    index = still_overlays.still_digest_index(pool, str(tmp_path))
    assert len(index) == 1


def test_digest_index_leaves_captain_imported_stills_out(tmp_path):
    from library.tools import still_overlays

    outside = tmp_path / "outside"
    outside.mkdir()
    imported = _png(outside / "bug.png", (10, 10, 10))
    pool = _FakePool(_FakeFolder(clips=(
        _FakeClip("Still", imported, "uid-captain"),)))
    assert still_overlays.still_digest_index(
        pool, str(tmp_path / "project")) == {}


def test_the_dedupe_stills_verb_is_registered_in_both_front_doors():
    from pathlib import Path

    import ren.commands as ren_commands

    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    assert by_name["dedupe-stills"].subcommand == "dedupe-stills"
    native = (Path(__file__).resolve().parents[3] / "native" / "ren-cli"
              / "src" / "commands.rs").read_text(encoding="utf-8")
    assert 'name: "dedupe-stills"' in native


def test_provenance_key_names_the_tv_frame_family():
    assert still_content.provenance_key("tv_frame_17bebae123.mov") is None
    assert (still_content.provenance_key(
        "/proj/tv_frame_17bebae123_still.png") == "tv_frame")
    assert still_content.provenance_key("tv_frame_d90b567817.png") == (
        "tv_frame")
    assert still_content.provenance_key("tv_still.png") == "tv_frame"
    assert still_content.provenance_key(
        "post_header_fe28c5bde3_tight_still.png") is None
    assert still_content.provenance_key("ph_still.png") is None


def test_png_info_reads_the_header_without_decoding(tmp_path):
    path = _png(tmp_path / "a.png", (32, 80, 192))
    assert still_content.png_info(path) == {
        "width": 2, "height": 2, "depth": 8, "color_type": 2}
    assert still_content.png_info(str(tmp_path / "missing.png")) is None


def test_decode_round_trips_filtered_pngs(tmp_path):
    import struct
    import zlib

    def filtered(path, filtered_rows):
        def chunk(kind, data):
            blob = kind + data
            return (struct.pack(">I", len(data)) + blob
                    + struct.pack(">I", zlib.crc32(blob) & 0xFFFFFFFF))

        out = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR",
                       struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"".join(filtered_rows)))
               + chunk(b"IEND", b""))
        path.write_bytes(out)
        return str(path)

    # Sub-filtered rows of solid red then solid blue.
    path = filtered(tmp_path / "f.png",
                    [b"\x01" + bytes((10, 0, 0)) * 2,
                     b"\x01" + bytes((0, 0, 200)) * 2])
    width, height, gray = still_content.decode_png_gray(path)
    assert (width, height) == (2, 2)
    assert gray[0][0] == (10 * 299) // 1000
    assert gray[1][0] == (200 * 114) // 1000


def _striped(path, left, right, text=None):
    """An 8x8 PNG, left half one colour, right half another."""
    import struct
    import zlib

    def chunk(kind, data):
        blob = kind + data
        return (struct.pack(">I", len(data)) + blob
                + struct.pack(">I", zlib.crc32(blob) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(left) * 4 + bytes(right) * 4
                   for _ in range(8))
    out = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)))
    if text is not None:
        out += chunk(b"tEXt", b"comment\x00" + text.encode("latin-1"))
    out += chunk(b"IEND", b"")
    path.write_bytes(out)
    return str(path)


def test_dhash_matches_same_graphic_and_splits_copy(tmp_path):
    first = _striped(tmp_path / "a.png", (32, 80, 192), (200, 200, 200),
                     text="reel-07")
    second = _striped(tmp_path / "b.png", (32, 80, 192), (200, 200, 200),
                      text="reel-13")
    third = _striped(tmp_path / "c.png", (200, 200, 200), (32, 80, 192))
    hashes = [still_content.phash_still(path)
              for path in (first, second, third)]
    assert still_content.hamming(hashes[0], hashes[1]) == 0
    assert still_content.hamming(hashes[0], hashes[2]) > 4


def _graphic_item(path, phash, pool_uid, depth=8, size=100):
    return {"timeline": "Reel 07", "record": [0, 100],
            "pool_uid": pool_uid, "file_path": path, "phash": phash,
            "depth": depth, "size": size, "props": {}, "markers": {}}


def test_graphic_group_merges_on_tight_match_prefers_direct_render():
    members = [
        _graphic_item("/proj/tv_frame_17be_still.png", 0, "uid-extract",
                      depth=16, size=105121),
        _graphic_item("/proj/tv_frame_d90.png", 0, "uid-direct",
                      depth=8, size=41427),
    ]
    plan = still_content.plan_graphic_group(members)
    assert plan["groups"][0]["canonical"] == "/proj/tv_frame_d90.png"
    assert plan["groups"][0]["worst_distance"] == 0
    assert len(plan["repoints"]) == 1
    assert (plan["repoints"][0]["canonical_file"]
            == "/proj/tv_frame_d90.png")


def test_graphic_group_refuses_past_the_tight_match():
    members = [
        _graphic_item("/proj/tv_frame_17be_still.png", 0, "uid-a"),
        _graphic_item("/proj/tv_frame_d90.png",
                      (1 << 64) - 1, "uid-b"),
    ]
    with pytest.raises(still_content.DedupeRefused):
        still_content.plan_graphic_group(members)


def test_graphic_group_skips_unhashable_member():
    members = [_graphic_item("/proj/tv_frame_17be_still.png", 0, "uid-a"),
               _graphic_item("/proj/tv_frame_d90.png", None, "uid-b")]
    plan = still_content.plan_graphic_group(members)
    assert plan["repoints"] == [] and len(plan["skipped"]) == 1


def test_project_plan_partitions_exact_and_graphic(tmp_path):
    """Graphic-keyed items merge only through their graphic group;
    plain items only through exact groups - one item, one canonical."""
    first = _striped(tmp_path / "tv_frame_aaaaaaaaaa_still.png",
                     (32, 80, 192), (200, 200, 200))
    second = _striped(tmp_path / "tv_frame_bbbbbbbbbb.png",
                      (32, 80, 192), (200, 200, 200))
    plain = _striped(tmp_path / "card_a.png", (32, 80, 192),
                     (200, 200, 200))
    import hashlib

    def item(path, uid, digest=None):
        with open(path, "rb") as handle:
            blob = handle.read()
        return {"timeline": "Reel 07", "record": [0, 100],
                "pool_uid": uid, "file_path": path,
                "digest": digest or hashlib.sha256(blob).hexdigest(),
                "depth": 8, "size": len(blob), "props": {}, "markers": {}}

    candidates = [item(first, "uid-a"), item(second, "uid-b"),
                  item(plain, "uid-c", digest="shared"),
                  dict(item(plain, "uid-d", digest="shared"),
                       timeline="Reel 13", record=[0, 50])]
    # Plain items share one file: nothing to collapse there.
    plan = still_dedupe.plan_project_repoints(candidates)
    assert len(plan["graphic_groups"]) == 1
    assert plan["graphic_groups"][0]["worst_distance"] == 0
    assert len(plan["repoints"]) == 1
    assert plan["repoints"][0]["timeline"] in ("Reel 07",)
