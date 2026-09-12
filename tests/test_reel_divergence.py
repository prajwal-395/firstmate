"""A landed mechanism that did not reach the reels is DETECTED.

The worked example these tests are built from, measured 2026-09-11 on
`lucie/geo-podcast` off the project's own serialized timelines with no
Resolve running: the logo card was declared under
`effect.full_frame_elements`, Reel 26 carried `logo_reveal.mov`, and
Reels 01/13/23/28/30/31 did not.  "Every reel inherits it" was true of
the engine and false of the project.

Each test below names which half of the mechanism it would catch the
removal of.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import reel_divergence as rd  # noqa: E402


# ── Fakes: the two snapshot shapes the detectors accept ───────────

class FakeClip:
    def __init__(self, name, track_type="video", source_file=""):
        self.name = name
        self.track_type = track_type
        self.track_name = "V1"
        self.source_file = source_file


class FakeSnapshot:
    def __init__(self, *clips):
        self.clips = list(clips)


def serialized(*names, track_type="video"):
    """A `timeline_serializer`-shaped document carrying `names`."""
    return {"metadata": {"name": "Reel 99 - fake"},
            "tracks": [{"type": track_type, "index": 1, "name": "V1",
                        "clips": [{"name": n, "file_path": f"/x/{n}"}
                                  for n in names]}]}


def project_declaring(tmp_path, yaml_text):
    (tmp_path / "project.yaml").write_text(yaml_text, encoding="utf-8")
    return str(tmp_path)


LOGO_YAML = """\
effect:
  full_frame_elements:
    - element: full_frame_clip
      placement: tail
      asset: /shared/brand/logo_reveal.mov
      reason: captain 2026-09-11
"""


# ── The registry itself can fail ─────────────────────────────────

def test_a_detector_that_never_says_what_absent_looks_like_is_refused():
    """Removing `absent_looks_like` from a wired detector must FAIL.

    A check that cannot state its failing input cannot be shown to fail
    on one, and reads as coverage (AGENTS.md 10.4).
    """
    broken = dict(rd.DETECTORS)
    broken[rd.KEY_FULL_FRAME] = rd.Detector(
        key=rd.KEY_FULL_FRAME, what="a card",
        detect=rd._detect_full_frame_elements)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(rd, "DETECTORS", broken)
        with pytest.raises(rd.MalformedDetector) as refused:
            rd.assert_registry_is_well_formed()
    assert "ABSENT" in str(refused.value)


def test_an_undetectable_declaration_must_name_a_reason_and_an_owner():
    """A declaration silently missing from the table reads as agreeing."""
    for missing in ({"undetectable_reason": ""}, {"owner": ""}):
        broken = dict(rd.DETECTORS)
        entry = broken[rd.KEY_CAPTION_ROW]
        broken[rd.KEY_CAPTION_ROW] = rd.Detector(
            key=entry.key, what=entry.what,
            undetectable_reason=missing.get("undetectable_reason",
                                            entry.undetectable_reason),
            owner=missing.get("owner", entry.owner))
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(rd, "DETECTORS", broken)
            with pytest.raises(rd.MalformedDetector):
                rd.assert_registry_is_well_formed()


def test_the_shipped_registry_is_well_formed():
    rd.assert_registry_is_well_formed()


def test_the_caption_row_is_declared_undetectable_by_name():
    """It is REPORTED as having no detector, never left out.

    A row that vanished from the table would read as a declaration that
    agrees, which is the failure this whole module exists to stop.
    """
    entry = rd.DETECTORS[rd.KEY_CAPTION_ROW]
    assert entry.detect is None
    assert "999" in entry.undetectable_reason
    assert entry.owner


# ── The detector reads the artefact, both shapes ─────────────────

def test_a_reel_carrying_the_declared_card_reads_carried(tmp_path):
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {
        "Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))})
    assert report["reels"]["Reel 26"][rd.KEY_FULL_FRAME]["verdict"] \
        == rd.CARRIED


def test_a_reel_built_before_the_declaration_reads_absent(tmp_path):
    """The measured 2026-09-11 case: six reels of eight.

    Remove the detector and this reads `undetermined`, which is the
    silence the incident actually had.
    """
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {
        "Reel 01": FakeSnapshot(FakeClip("LCATL0013.MXF")),
        "Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))})
    assert report["reels"]["Reel 01"][rd.KEY_FULL_FRAME]["verdict"] \
        == rd.ABSENT
    assert report["divergent"][rd.KEY_FULL_FRAME] == ["Reel 01"]


def test_a_serialized_timeline_off_disk_reads_the_same(tmp_path):
    """The survey must run with Resolve CLOSED, or nobody runs it."""
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {
        "has": serialized("logo_reveal.mov"),
        "hasnt": serialized("LCATL0013.MXF")})
    assert report["reels"]["has"][rd.KEY_FULL_FRAME]["verdict"] == rd.CARRIED
    assert report["reels"]["hasnt"][rd.KEY_FULL_FRAME]["verdict"] == rd.ABSENT


def test_an_audio_clip_of_the_same_name_does_not_count(tmp_path):
    """A full-frame card REPLACES picture; it is a picture item."""
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {
        "Reel 01": FakeSnapshot(
            FakeClip("logo_reveal.mov", track_type="audio"))})
    assert report["reels"]["Reel 01"][rd.KEY_FULL_FRAME]["verdict"] \
        == rd.ABSENT


def test_a_project_declaring_no_card_has_no_card_row(tmp_path):
    """Nothing declared, nothing to diverge from - and no false ABSENT."""
    folder = project_declaring(tmp_path, "pipeline: {}\n")
    report = rd.survey(folder, {"Reel 01": FakeSnapshot(FakeClip("x.mov"))})
    assert rd.KEY_FULL_FRAME not in report["reels"]["Reel 01"]


# ── Undetermined is a first-class answer ─────────────────────────

def test_an_unread_reel_is_undetermined_not_absent(tmp_path):
    """An unread reel must never read as a reel without the card."""
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {},
                       notes={"Reel 09": "no timeline of that exact name"})
    cell = report["reels"]["Reel 09"][rd.KEY_FULL_FRAME]
    assert cell["verdict"] == rd.UNDETERMINED
    assert "no timeline of that exact name" in cell["detail"]
    assert rd.KEY_FULL_FRAME not in report["divergent"]


def test_a_detector_that_raises_is_undetermined_with_the_exception(tmp_path):
    folder = project_declaring(tmp_path, LOGO_YAML)

    def explode(snapshot, params):
        raise RuntimeError("resolve went away")

    broken = dict(rd.DETECTORS)
    broken[rd.KEY_FULL_FRAME] = rd.Detector(
        key=rd.KEY_FULL_FRAME, what="a card",
        absent_looks_like="no matching clip", detect=explode)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(rd, "DETECTORS", broken)
        report = rd.survey(folder, {"Reel 01": FakeSnapshot()})
    cell = report["reels"]["Reel 01"][rd.KEY_FULL_FRAME]
    assert cell["verdict"] == rd.UNDETERMINED
    assert "resolve went away" in cell["detail"]


# ── The freeze ending: applicability, not a false gate ───────────

def test_a_reel_closing_on_no_cta_is_not_reported_as_missing_a_freeze(tmp_path):
    """A gate that FAILS correct output is no coverage (AGENTS.md 10.4)."""
    folder = project_declaring(tmp_path, "pipeline: {}\n")
    report = rd.survey(folder, {"Reel 05": FakeSnapshot(FakeClip("a.mxf"))},
                       endings={})
    cell = report["reels"]["Reel 05"][rd.KEY_FREEZE_ENDING]
    assert cell["verdict"] == rd.UNDETERMINED


def test_a_reel_owed_a_freeze_and_without_one_reads_absent(tmp_path):
    from library.tools.reel_ending import FREEZE_PREFIX

    folder = project_declaring(tmp_path, "pipeline: {}\n")
    endings = {"Reel 05": {"tail_hold": "freeze"},
               "Reel 07": {"tail_hold": "freeze"}}
    report = rd.survey(folder, {
        "Reel 05": FakeSnapshot(FakeClip("a.mxf")),
        "Reel 07": FakeSnapshot(FakeClip(f"{FREEZE_PREFIX}abc.png"))},
        endings=endings)
    assert report["reels"]["Reel 05"][rd.KEY_FREEZE_ENDING]["verdict"] \
        == rd.ABSENT
    assert report["reels"]["Reel 07"][rd.KEY_FREEZE_ENDING]["verdict"] \
        == rd.CARRIED


def test_the_ending_row_survives_endings_being_omitted(tmp_path):
    """A row that vanished would read as agreement."""
    folder = project_declaring(tmp_path, "pipeline: {}\n")
    report = rd.survey(folder, {"Reel 05": FakeSnapshot()})
    assert rd.KEY_FREEZE_ENDING in report["reels"]["Reel 05"]


# ── The claim gate: the reporting failure, mechanised ────────────

def test_a_claim_the_artefacts_do_not_back_is_REFUSED(tmp_path):
    """The exact 2026-09-11 claim, refused by measurement.

    Remove `assert_reaches` and nothing anywhere distinguishes "the
    engine can do this" from "the reels have this".
    """
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {
        "Reel 01": FakeSnapshot(FakeClip("a.mxf")),
        "Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))})
    with pytest.raises(rd.ClaimNotBackedByArtefacts) as refused:
        rd.assert_reaches(report, rd.KEY_FULL_FRAME)
    assert "Reel 01" in str(refused.value)
    assert "Reel 26" not in str(refused.value)


def test_a_claim_backed_by_every_surveyed_reel_passes(tmp_path):
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {
        "Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))})
    assert rd.assert_reaches(report, rd.KEY_FULL_FRAME) == ["Reel 26"]


def test_an_undetermined_reel_refuses_the_claim_too(tmp_path):
    """"We did not look" is not evidence that something is there."""
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {
        "Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))},
        notes={"Reel 09": "not built"})
    with pytest.raises(rd.ClaimNotBackedByArtefacts) as refused:
        rd.assert_reaches(report, rd.KEY_FULL_FRAME)
    assert "Reel 09" in str(refused.value)
    assert rd.UNDETERMINED in str(refused.value)


def test_a_claim_over_zero_reels_is_refused(tmp_path):
    folder = project_declaring(tmp_path, LOGO_YAML)
    with pytest.raises(rd.ClaimNotBackedByArtefacts):
        rd.assert_reaches(rd.survey(folder, {}), rd.KEY_FULL_FRAME)


def test_a_claim_about_an_unknown_declaration_is_refused(tmp_path):
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.survey(folder, {"Reel 26": FakeSnapshot()})
    with pytest.raises(rd.ClaimNotBackedByArtefacts):
        rd.assert_reaches(report, "a_declaration_nobody_detects")


# ── The build-time report reports rather than refusing ───────────

def test_report_divergence_prints_and_never_raises(tmp_path, capsys):
    """Whether to rebuild a stale reel is the captain's decision."""
    folder = project_declaring(tmp_path, LOGO_YAML)
    report = rd.report_divergence(folder, {
        "Reel 01": FakeSnapshot(FakeClip("a.mxf"))})
    assert report["divergent"][rd.KEY_FULL_FRAME] == ["Reel 01"]
    printed = capsys.readouterr().out
    assert "ABSENT" in printed
    assert "DIVERGE" in printed


def test_report_divergence_survives_a_total_failure(tmp_path, capsys):
    report = rd.report_divergence(tmp_path / "does-not-exist", None)
    assert "reels" in report or "unavailable" in report


def test_snapshots_for_records_an_absent_reel_rather_than_measuring_it():
    class FakeTimeline:
        def GetName(self):
            return "Reel 26 - x"

    class FakeProject:
        def GetTimelineCount(self):
            return 1

        def GetTimelineByIndex(self, index):
            return FakeTimeline()

        def GetName(self):
            return "Podcast"

    snapshots, notes = rd.snapshots_for(
        FakeProject(), ["Reel 26 - x", "Reel 09 - y"],
        snapshot_fn=lambda timeline, project: FakeSnapshot())
    assert set(snapshots) == {"Reel 26 - x"}
    assert "has not been built" in notes["Reel 09 - y"]


# ── The detector compares digests, not filenames ──────────────────

def _logo_project(tmp_path, payload=b"v1-bytes"):
    """A project declaring a REAL asset file, so digests are non-None."""
    asset = tmp_path / "logo_reveal.mov"
    asset.write_bytes(payload)
    (tmp_path / "project.yaml").write_text(
        "effect:\n  full_frame_elements:\n"
        "    - element: full_frame_clip\n      placement: tail\n"
        f"      asset: {asset}\n      reason: test\n", encoding="utf-8")
    return str(tmp_path), str(asset)


def test_a_declared_file_replaced_on_disk_reads_absent(tmp_path):
    """The worst story in the set: same name, different bytes.

    Remove the digest comparison and the reel reads CARRIED while its
    picture changed with no build, no commit and no record.
    """
    from library.tools.code_identity import hash_asset_file

    folder, asset = _logo_project(tmp_path)
    recorded = {asset: hash_asset_file(asset)}
    Path(asset).write_bytes(b"v2-regraded-by-hand")

    report = rd.survey(
        folder, {"Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))},
        recorded_assets=recorded)
    cell = report["reels"]["Reel 26"][rd.KEY_FULL_FRAME]
    assert cell["verdict"] == rd.ABSENT
    assert "replaced on disk" in cell["detail"]
    assert report["divergent"][rd.KEY_FULL_FRAME] == ["Reel 26"]
    with pytest.raises(rd.ClaimNotBackedByArtefacts):
        rd.assert_reaches(report, rd.KEY_FULL_FRAME)


def test_a_declared_file_deleted_after_the_build_reads_absent(tmp_path):
    """Gone from disk is not carried, whatever the timeline names."""
    from library.tools.code_identity import hash_asset_file

    folder, asset = _logo_project(tmp_path)
    recorded = {asset: hash_asset_file(asset)}
    Path(asset).unlink()

    report = rd.survey(
        folder, {"Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))},
        recorded_assets=recorded)
    cell = report["reels"]["Reel 26"][rd.KEY_FULL_FRAME]
    assert cell["verdict"] == rd.ABSENT
    assert "missing" in cell["detail"]


def test_matching_bytes_stay_carried_with_no_footnote(tmp_path):
    """The digest agreeing must not demote a carried reel."""
    from library.tools.code_identity import hash_asset_file

    folder, asset = _logo_project(tmp_path)
    report = rd.survey(
        folder, {"Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))},
        recorded_assets={asset: hash_asset_file(asset)})
    cell = report["reels"]["Reel 26"][rd.KEY_FULL_FRAME]
    assert cell["verdict"] == rd.CARRIED
    assert "predate" not in cell["detail"]


def test_a_pre_digest_build_reads_carried_by_name_and_says_so(tmp_path):
    """No recorded digest is not a divergence - it is an absence the
    survey states per reel rather than rounding either way."""
    folder, _asset = _logo_project(tmp_path)
    report = rd.survey(
        folder, {"Reel 26": FakeSnapshot(FakeClip("logo_reveal.mov"))})
    cell = report["reels"]["Reel 26"][rd.KEY_FULL_FRAME]
    assert cell["verdict"] == rd.CARRIED
    assert "name match only" in cell["detail"]


def test_hash_asset_file_hashes_bytes_and_records_absence(tmp_path):
    """The identity precedent, applied to media: content in, digest
    out, and a missing file is None rather than a hollow digest."""
    from library.tools.code_identity import hash_asset_file

    asset = tmp_path / "card.mov"
    asset.write_bytes(b"bytes-one")
    first = hash_asset_file(str(asset))
    assert first and len(first) == 64
    asset.write_bytes(b"bytes-two")
    assert hash_asset_file(str(asset)) != first
    assert hash_asset_file(str(tmp_path / "not-there.mov")) is None


def test_declared_asset_paths_covers_all_three_families(tmp_path):
    """`full_frame_elements`, `content.bookends` and every
    `external/` asset path - the collector that feeds both the
    build-time recording and the survey-time comparison."""
    folder, asset = _logo_project(tmp_path)

    card = tmp_path / "tailcard.mov"
    card.write_bytes(b"hand-placed")
    external = tmp_path / "external"
    external.mkdir()
    (external / "placed_assets.json").write_text(json.dumps({
        "version": 1, "assets": [{
            "slot": "tail", "asset": str(card),
            "duration_seconds": 5.0, "has_audio": False,
            "label": "tail card", "reason": "captain"}]}),
        encoding="utf-8")

    endcard = tmp_path / "endcard.mov"
    endcard.write_bytes(b"bookend")
    content = {"bookends": {"end_card": {
        "asset": str(endcard), "duration_seconds": 5.0}}}

    found = rd.declared_asset_paths(folder, brand_content=content)
    assert found[str(asset)] == "effect.full_frame_elements"
    assert found[str(card)] == "external/placed_assets.json"
    assert found[str(endcard)] == "content.bookends"


def test_declared_asset_paths_never_raises_on_a_broken_project(tmp_path):
    """A collector that raised would take the whole survey down with
    it - malformed declarations are the plan-time reader's to refuse."""
    assert rd.declared_asset_paths(tmp_path / "does-not-exist") == {}
