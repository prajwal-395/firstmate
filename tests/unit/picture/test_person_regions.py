"""M3d person regions (`library/tools/person_regions.py`): the codec, the
geometry a reader answers from without decoding, the face-containment
link that names a region, the IoU linking of per-frame instances into
tracks, and the lane's freshness."""
import gzip
import json

import numpy as np
import pytest

from library.tools import footage_analysis, source_memory
from library.tools import person_regions as pr


def _mask(grid=(12, 8), *boxes):
    """A bool mask on a `(w, h)` grid, on inside the given cell boxes
    `(x0, y0, x1, y1)` (exclusive ends)."""
    width, height = grid
    mask = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in boxes:
        mask[y0:y1, x0:x1] = True
    return mask


class TestCodec:
    def test_round_trip_when_the_first_cell_is_in_the_mask(self):
        """Would catch: dropping the leading zero-length background run,
        which inverts every mask whose top-left cell is foreground."""
        mask = _mask((5, 3), (0, 0, 2, 1), (3, 2, 5, 3))
        runs = pr.encode_runs(mask)
        assert runs[0] == 0
        assert np.array_equal(pr.decode_runs(runs, (5, 3)), mask)

    def test_round_trip_background_first(self):
        mask = _mask((6, 4), (2, 1, 4, 3))
        assert np.array_equal(
            pr.decode_runs(pr.encode_runs(mask), (6, 4)), mask)

    def test_truncated_runs_are_refused(self):
        """Would catch: a short sidecar entry decoding to a mask padded
        with background - a person silently cropped."""
        with pytest.raises(ValueError):
            pr.decode_runs([3, 2], (6, 4))


class TestGeometry:
    def test_interior_point_is_inside_an_l_shape_where_bbox_centre_is_not(self):
        """Would catch: summarising a region by its bbox centre or
        centroid, which on an L-shaped body (an arm out, a person beside
        a table edge) lands on background."""
        mask = _mask((20, 20), (0, 0, 6, 20), (0, 14, 20, 20))
        summary = pr.summarize(mask)
        x1, y1, x2, y2 = summary["bbox"]
        assert not mask[int((y1 + y2) / 2 * 20), int((x1 + x2) / 2 * 20)]
        cx, cy = summary["centroid"]
        assert not mask[int(cy * 20), int(cx * 20)]
        ix, iy = summary["interior_point"]
        assert mask[int(iy * 20), int(ix * 20)]

    def test_summary_is_normalised_to_the_grid(self):
        mask = _mask((10, 5), (2, 1, 6, 4))
        summary = pr.summarize(mask)
        assert summary["bbox"] == [0.2, 0.2, 0.6, 0.8]
        assert summary["area"] == pytest.approx(12 / 50)


class TestFaceContainment:
    GRID = (12, 8)

    def test_face_inside_one_instance_names_it(self):
        person = _mask(self.GRID, (2, 1, 6, 8))
        hand = _mask(self.GRID, (9, 5, 12, 8))
        face = {"box": [2 / 12, 1 / 8, 5 / 12, 3 / 8]}
        out, counts = pr.assign_sample([face], ["face_001"], [person, hand])
        assert out[0]["face_track"] == "face_001"
        assert out[0]["containment"] == 1.0
        assert out[1] == {"unassigned": "no_face_inside"}
        assert counts == [1]

    def test_a_faceless_instance_is_never_given_the_nearest_face(self):
        """Would catch: owning a region by proximity. The off-frame hand
        (LCATL0013 t~60-90) is a person instance with no face; it must
        stay unowned, not become the seated speaker's."""
        hand = _mask(self.GRID, (7, 5, 12, 8))
        face = {"box": [2 / 12, 1 / 8, 5 / 12, 3 / 8]}
        out, counts = pr.assign_sample([face], ["face_001"], [hand])
        assert out == [{"unassigned": "no_face_inside"}]
        assert counts == [0]

    def test_a_face_mostly_outside_is_not_inside(self):
        """Majority of the face box, not any overlap."""
        person = _mask(self.GRID, (0, 0, 3, 8))
        face = {"box": [2 / 12, 1 / 8, 6 / 12, 3 / 8]}
        out, counts = pr.assign_sample([face], ["face_001"], [person])
        assert out[0]["unassigned"] == "no_face_inside"
        assert counts == [0]

    def test_two_faces_in_one_mask_stay_unassigned(self):
        both = _mask(self.GRID, (0, 0, 12, 8))
        faces = [{"box": [0.0, 0.0, 0.2, 0.3]}, {"box": [0.6, 0.0, 0.8, 0.3]}]
        out, _ = pr.assign_sample(faces, ["face_001", "face_002"], [both])
        assert out[0]["unassigned"] == "several_faces_inside"

    def test_a_face_m3b_never_vouched_for_names_nothing(self):
        person = _mask(self.GRID, (2, 1, 6, 8))
        face = {"box": [2 / 12, 1 / 8, 5 / 12, 3 / 8]}
        out, _ = pr.assign_sample([face], [None], [person])
        assert out[0]["unassigned"] == "face_has_no_track"
        assert out[0]["face_track"] is None


class TestLinking:
    def test_tracks_follow_masks_not_vision_instance_indices(self):
        """Would catch: linking by Vision's per-frame instance index,
        which swaps between adjacent samples (seen on geo-podcast)."""
        left = _mask((12, 8), (0, 0, 4, 8))
        right = _mask((12, 8), (8, 0, 12, 8))
        rows = pr.link_tracks([[left, right], [right, left], [left, right]])
        assert rows[0] == [0, 1]
        assert rows[1] == [1, 0]
        assert rows[2] == [0, 1]

    def test_an_undecoded_sample_breaks_the_track(self):
        mask = _mask((12, 8), (0, 0, 4, 8))
        rows = pr.link_tracks([[mask], None, [mask]])
        assert rows[1] is None
        assert rows[0] != rows[2]

    def test_a_low_overlap_pair_starts_a_new_track(self):
        a = _mask((12, 8), (0, 0, 4, 8))
        b = _mask((12, 8), (3, 0, 7, 8))
        assert pr.link_tracks([[a], [b]]) == [[0], [1]]

    def test_track_owner_needs_a_majority_of_samples(self):
        entries = ([{"face_track": "face_001"}] * 2
                   + [{"unassigned": "no_face_inside"}] * 3)
        owner = pr.track_owner(entries)
        assert owner["face_track"] is None
        assert owner["unassigned"] == "no_majority_face_track"
        assert owner["face_track_fraction"] == 0.4
        assert pr.track_owner(entries[:4])["face_track"] is None
        owner = pr.track_owner([{"face_track": "face_001"}] * 3
                               + [{"unassigned": "no_face_inside"}])
        assert owner == {"face_track": "face_001",
                         "face_track_fraction": 0.75}


class TestAssemble:
    def test_counts_faces_inside_and_names_tracks(self):
        grid = (12, 8)
        person = _mask(grid, (2, 1, 6, 8))
        hand = _mask(grid, (9, 5, 12, 8))
        docs = [{"instances": [{"index": 1, "runs": pr.encode_runs(person)},
                               {"index": 2, "runs": pr.encode_runs(hand)}]},
                {"instances": [{"index": 1, "runs": pr.encode_runs(hand)},
                               {"index": 2, "runs": pr.encode_runs(person)}]},
                None]
        face = {"box": [2 / 12, 1 / 8, 5 / 12, 3 / 8]}
        m3 = [{"faces": [face]}, {"faces": [face]}, {"faces": [face]}]
        built = pr.assemble([0.0, 0.5, 1.0], docs, m3,
                            [["face_001"], ["face_001"], ["face_001"]], grid)
        assert built["faces"]["with_face_track"] == 2
        assert built["faces"]["fraction_inside_exactly_one"] == 1.0
        assert built["samples"][2] == {"t": 1.0, "instances": None}
        by_id = {t["track_id"]: t for t in built["tracks"]}
        assert len(by_id) == 2
        owners = sorted(str(t.get("face_track")) for t in by_id.values())
        assert owners == ["None", "face_001"]
        first, second = built["samples"][0], built["samples"][1]
        assert first["instances"][0]["track"] == second["instances"][1]["track"]
        mask_ix = second["instances"][1]["mask"]
        assert np.array_equal(
            pr.decode_runs(built["masks"][mask_ix], grid), person)


class TestSidecar:
    def test_same_masks_same_name_and_tampered_content_refused(
            self, tmp_path, monkeypatch):
        """Content addressing must hold: a rebuild of the same masks names
        the same file - even when zlib compresses them to other bytes, as
        two Python builds did on LCATL0013 - and content that no longer
        hashes to the record's sha256 is refused rather than decoded as
        someone's mask."""
        masks = [pr.encode_runs(_mask((6, 4), (1, 1, 3, 3)))]
        digest = "d" * 8
        sdir = source_memory.source_dir(digest, tmp_path)
        ref = pr.write_sidecar(sdir, masks, (6, 4))
        again = pr.write_sidecar(source_memory.source_dir("e" * 8, tmp_path),
                                 masks, (6, 4))
        assert again["file"] == ref["file"]
        real = gzip.compress
        monkeypatch.setattr(pr.gzip, "compress",
                            lambda data, mtime=0: real(data, 1, mtime=mtime))
        other_zlib = pr.write_sidecar(
            source_memory.source_dir("f" * 8, tmp_path), masks, (6, 4))
        monkeypatch.setattr(pr.gzip, "compress", real)
        assert other_zlib["file"] == ref["file"]
        doc = {"grid": [6, 4], "sidecar": ref}
        assert pr.read_mask(digest, doc, 0, tmp_path).sum() == 4
        path = sdir / ref["file"]
        path.write_bytes(gzip.compress(json.dumps(
            {"codec": pr.CODEC, "grid": [6, 4], "masks": [[24]]}).encode()))
        with pytest.raises(ValueError):
            pr.read_mask(digest, doc, 0, tmp_path)


class TestLane:
    LANE = next(item for item in footage_analysis.LANES
                if item.name == "regions")

    def _slots(self, tmp_path, digest, method, sidecar=True):
        sdir = source_memory.source_dir(digest, tmp_path)
        sdir.mkdir(parents=True)
        for slot in (source_memory.SLOT_FRAMES_INDEX,
                     source_memory.SLOT_PERSONS, source_memory.SLOT_IDENTITY):
            source_memory.write_json(sdir / slot, {"content_digest": digest})
        ref = (pr.write_sidecar(sdir, [], (2, 2)) if sidecar
               else {"file": "regions.masks.missing.json.gz"})
        source_memory.write_json(sdir / source_memory.SLOT_REGIONS, {
            "content_digest": digest, "sidecar": ref,
            "instrument": {"method": method}})

    def test_lane_version_is_the_producers(self):
        """Would catch: bumping the producer's slot version without the
        lane, so stale records are served as fresh forever."""
        assert self.LANE.method == pr.M3D_METHOD

    def test_reads_faces_and_their_tracks(self):
        assert source_memory.SLOT_PERSONS in self.LANE.reads
        assert source_memory.SLOT_IDENTITY in self.LANE.reads

    def test_current_record_is_fresh(self, tmp_path):
        self._slots(tmp_path, "fresh", pr.M3D_METHOD)
        assert footage_analysis.slot_is_fresh(self.LANE, "fresh", tmp_path)

    def test_older_slot_version_is_stale(self, tmp_path):
        self._slots(tmp_path, "old", "person-instance-mask-v0")
        assert not footage_analysis.slot_is_fresh(self.LANE, "old", tmp_path)

    def test_record_without_its_sidecar_is_stale(self, tmp_path):
        """Would catch: a record whose masks were cleaned away reading as
        fresh, so every mask read fails until someone rebuilds by hand."""
        self._slots(tmp_path, "nosidecar", pr.M3D_METHOD, sidecar=False)
        assert not footage_analysis.slot_is_fresh(
            self.LANE, "nosidecar", tmp_path)


class TestBuildSource:
    def test_refuses_without_m3b(self, tmp_path, monkeypatch):
        """Faces are what name a region: no M3b, no M3d - never regions
        with every owner silently None."""
        digest = "nom3b"
        sdir = source_memory.source_dir(digest, tmp_path)
        sdir.mkdir(parents=True)
        media = tmp_path / "a.mov"
        media.write_bytes(b"x")
        source_memory.write_json(sdir / source_memory.SLOT_FRAMES_INDEX, {
            "content_digest": digest, "frames": []})
        source_memory.write_json(sdir / source_memory.SLOT_PERSONS, {
            "content_digest": digest, "frames": []})
        monkeypatch.setattr(source_memory, "is_fresh", lambda *a: True)
        with pytest.raises(RuntimeError, match="M3b"):
            pr.build_source_regions(str(media), digest, tmp_path)

    def test_writes_record_and_sidecar_from_the_m2_times(
            self, tmp_path, monkeypatch):
        """The whole build over a stub decode and stub Vision: samples
        land on M2's times, a frame the decode missed is `None`, and the
        record's sidecar decodes back to what the helper drew."""
        digest = "built"
        sdir = source_memory.source_dir(digest, tmp_path)
        sdir.mkdir(parents=True)
        media = tmp_path / "a.mov"
        media.write_bytes(b"x")
        times = [0.083, 0.584, 1.084]
        source_memory.write_json(sdir / source_memory.SLOT_FRAMES_INDEX, {
            "content_digest": digest,
            "frames": [{"file": f"frames/{i}.jpg", "t": t}
                       for i, t in enumerate(times)]})
        face = {"box": [2 / 12, 1 / 8, 5 / 12, 3 / 8]}
        source_memory.write_json(sdir / source_memory.SLOT_PERSONS, {
            "content_digest": digest,
            "instrument": {"frame_pixels": [12, 8]},
            "frames": [{"t": t, "faces": [face], "hands": []}
                       for t in times]})
        source_memory.write_json(sdir / source_memory.SLOT_IDENTITY, {
            "content_digest": digest})
        monkeypatch.setattr(source_memory, "is_fresh", lambda *a: True)
        from library.tools import event_spans
        monkeypatch.setattr(event_spans, "assign_face_tracks",
                            lambda m3, ident: [["face_001"]] * len(times))
        monkeypatch.setattr(pr, "_helper", lambda: "stub")

        def decode(source, out_dir, width):
            assert width == pr.DECODE_WIDTH
            return [(f"{out_dir}/a.jpg", 0.083), (f"{out_dir}/c.jpg", 1.084)
                    ], False

        monkeypatch.setattr(source_memory, "extract_iframes", decode)
        person = _mask((12, 8), (2, 1, 6, 8))
        monkeypatch.setattr(pr, "_measure", lambda paths, grid, helper: [
            {"pixels": [1152, 768],
             "instances": [{"index": 1, "runs": pr.encode_runs(person)}]}
            for _ in paths])

        pr.build_source_regions(str(media), digest, tmp_path)

        doc = pr.read_m3d(digest, tmp_path)
        assert [s["t"] for s in doc["samples"]] == times
        assert doc["samples"][1]["instances"] is None
        assert doc["samples_measured"] == 2
        assert doc["faces"]["fraction_inside_exactly_one"] == 1.0
        inst = doc["samples"][2]["instances"][0]
        assert np.array_equal(
            pr.read_mask(digest, doc, inst["mask"], tmp_path), person)
        assert doc["instrument"]["method"] == pr.M3D_METHOD
        lane = TestLane.LANE
        assert footage_analysis.slot_is_fresh(lane, digest, tmp_path)
