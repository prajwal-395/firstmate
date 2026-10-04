"""A reel nothing changed about is not placed again - and the decision
is fail-closed: a digest that misses an input silently ships last week's
reel, so these tests pin every REBUILD answer and that the engine trees
cover every module the reel build reaches. History:
docs/evidence/reel_rebuild_need.md ("The test suite's account").
"""

import ast
from pathlib import Path

from library.tools import reel_rebuild_need as need

REPO = Path(__file__).resolve().parents[3]


# ── The decision is fail-closed ──────────────────────────────────────

MATCH = {"derivation": "d" * 64, "carried": "c" * 64}


def test_both_halves_matching_is_the_only_way_to_be_left_alone():
    decision = need.decide("Reel 01", "d" * 64, "c" * 64, MATCH)
    assert decision.leave_alone
    assert decision.action == need.LEAVE_ALONE
    assert "match" in decision.reason


def test_every_other_answer_is_rebuild_and_says_why():
    rows = [
        (None, "c" * 64, MATCH, "this build could not digest its own"),
        ("d" * 64, "c" * 64, None, "no build signature on record"),
        ("d" * 64, "c" * 64, {"carried": "c" * 64}, "no derivation digest"),
        ("d" * 64, "c" * 64, {"derivation": "d" * 64}, "no carried"),
        ("d" * 64, None, MATCH, "could not be read"),
        ("x" * 64, "c" * 64, MATCH, "derivation changed"),
        ("d" * 64, "x" * 64, MATCH, "not the one that build placed"),
    ]
    for fresh, live, recorded, because in rows:
        decision = need.decide("Reel 01", fresh, live, recorded)
        assert decision.action == need.REBUILD, because
        assert because in decision.reason
        assert not decision.leave_alone


def test_a_signature_written_at_build_time_has_an_open_carried_half():
    """The build places a STAGING container, so the carried half cannot
    be honest until promotion renamed it - and an empty half can never
    match, which is the fail-closed direction."""
    record = need.signature_for_record("d" * 64)
    assert record["derivation"] == "d" * 64
    assert record["carried"] == ""
    assert need.decide("Reel 01", "d" * 64, "c" * 64,
                       record).action == need.REBUILD


# ── The engine half ──────────────────────────────────────────────────

def test_the_engine_digest_covers_every_module_the_reel_build_reaches():
    """The under-cover trap, checked mechanically.

    `plan_provenance.REEL_BUILD_CODE_FILES` names three paths and the
    reel build reaches far more than three modules; a digest built on
    that set leaves a change to `reel_ending.py` or `reel_look.py`
    invisible.  This asserts the trees this module digests contain
    every library module reachable from the build by import.
    """
    trees = [REPO / rel for rel in need.ENGINE_CODE_TREES]

    def imports_of(path):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return set()
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module \
                    and node.module.startswith("library"):
                found.add(node.module)
                found.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names
                             if a.name.startswith("library"))
        return found

    seen, queue, reached = set(), [
        REPO / "library/tools/reel_build.py",
        REPO / "library/steps/step_7_01_build_reels/step.py",
        REPO / "library/steps/step_7_02_verify_reels/step.py",
    ], set()
    while queue:
        path = queue.pop()
        if str(path) in seen:
            continue
        seen.add(str(path))
        reached.add(path)
        for module in imports_of(path):
            candidate = REPO / (module.replace(".", "/") + ".py")
            if candidate.is_file() and str(candidate) not in seen:
                queue.append(candidate)

    uncovered = sorted(
        str(path.relative_to(REPO)) for path in reached
        if not any(str(path).startswith(str(tree)) for tree in trees))
    assert not uncovered, (
        "these modules decide what a reel build places and are not "
        "under any tree the engine digest covers, so a change to one "
        "of them would leave an unchanged-looking reel:\n  "
        + "\n  ".join(uncovered))


def test_a_changed_source_file_changes_the_engine_digest(tmp_path):
    """Top-level and nested: `code_identity.step_code_hash` skips
    directories, so a digest built on it would miss `library/tools/fusion/`."""
    for rel in ("library/tools/thing.py",
                "library/tools/fusion/comp_builder.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
        first = need.engine_code_digest(tmp_path)
        path.write_text("x = 2\n", encoding="utf-8")
        assert need.engine_code_digest(tmp_path) != first, rel


# ── The declaration half ─────────────────────────────────────────────


def test_every_project_wide_input_changes_the_project_wide_digest(tmp_path):
    """`project.yaml` (caption_row is decided inside the build, where no
    derivation sees it), the brand template and named assets (outside
    the project folder), and an unclassified external declaration
    (fail-closed: over-covers from the day it lands)."""
    (tmp_path / "project.yaml").write_text("a: 1\n", encoding="utf-8")
    first = need.project_wide_digest(tmp_path)
    (tmp_path / "project.yaml").write_text("a: 2\n", encoding="utf-8")
    assert need.project_wide_digest(tmp_path) != first

    base = need.project_wide_digest(tmp_path, brand_template={"x": 1},
                                    asset_hashes={"/a.png": "aa"})
    assert base != need.project_wide_digest(
        tmp_path, brand_template={"x": 2}, asset_hashes={"/a.png": "aa"})
    assert base != need.project_wide_digest(
        tmp_path, brand_template={"x": 1}, asset_hashes={"/a.png": "bb"})

    external = tmp_path / "external"
    external.mkdir()
    first = need.project_wide_digest(tmp_path)
    (external / "something_new.json").write_text("[]", encoding="utf-8")
    second = need.project_wide_digest(tmp_path)
    assert second != first
    (external / "something_new.json").write_text(
        '[{"reel": 9}]', encoding="utf-8")
    assert need.project_wide_digest(tmp_path) != second


def test_a_per_reel_pin_store_is_not_in_the_project_wide_digest(tmp_path):
    """A pin on Reel 13 must not rebuild Reel 23: each store is keyed by
    reel and carried by that reel's own derivation."""
    (tmp_path / "project.yaml").write_text("a: 1\n", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    for stem in ("captain_edits", "reel_ending"):
        (external / f"{stem}.json").write_text("[]", encoding="utf-8")
        first = need.project_wide_digest(tmp_path)
        (external / f"{stem}.json").write_text(
            '[{"reel": 13}]', encoding="utf-8")
        assert need.project_wide_digest(tmp_path) == first, stem


def test_external_split_preserves_declaration_digest_and_ignores_state(
        tmp_path):
    legacy = tmp_path / "external"
    legacy.mkdir()
    old = legacy / "overlay_intent.json"
    old.write_text('{"version": 2, "targets": {}}\n', encoding="utf-8")
    before = need.project_wide_digest(tmp_path)

    declarations = legacy / "declarations"
    declarations.mkdir()
    old.rename(declarations / old.name)
    assert need.project_wide_digest(tmp_path) == before

    state = legacy / "state"
    state.mkdir()
    (state / "music_selection.json").write_text(
        '{"key": "music_selection", "value": {}}\n', encoding="utf-8")
    assert need.project_wide_digest(tmp_path) == before


# ── The carried half ─────────────────────────────────────────────────

class FakeClip:
    def __init__(self, **kwargs):
        defaults = dict(
            resolve_item_id="id-1", track_type="video", track_index=1,
            track_name="SpeakerTwo", speaker="SpeakerTwo",
            source_file="/f/a.mov", source_in_frame=10,
            source_out_frame=110, source_frames=1000,
            timeline_start=0.0, timeline_end=4.17, name="a.mov",
            transform={"Pan": 0.0, "ZoomX": 2.3070000001})
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


class FakeSnapshot:
    def __init__(self, clips, name="Reel 01", **kwargs):
        self.clips = tuple(clips)
        self.timeline_name = name
        self.project_name = "P"
        for key, value in dict(fps=24000 / 1001, width=1080, height=1920,
                               start_frame=0, end_frame=100).items():
            setattr(self, key, kwargs.get(key, value))


def test_the_carried_digest_ignores_the_timeline_name_and_the_item_id():
    """Promotion renames a staging container without touching a frame,
    and a re-placed item is a new object carrying the same picture."""
    base = need.carried_digest(FakeSnapshot([FakeClip()], name="A"))
    renamed = need.carried_digest(FakeSnapshot([FakeClip()], name="B"))
    reidentified = need.carried_digest(
        FakeSnapshot([FakeClip(resolve_item_id="id-99")], name="A"))
    assert base == renamed == reidentified


def test_a_changed_picture_changes_the_carried_digest():
    base = need.carried_digest(FakeSnapshot([FakeClip()]))
    for change in ({"transform": {"Pan": 1.0, "ZoomX": 2.307}},
                   {"source_in_frame": 11}, {"timeline_start": 0.5},
                   {"track_name": "Lucie"}):
        assert need.carried_digest(
            FakeSnapshot([FakeClip(**change)])) != base, change


def test_the_carried_digest_survives_float_noise_it_should_ignore():
    """Resolve reports transforms as floats; a repr difference below the
    rounding is not a content change, and a digest that read one as a
    change would rebuild every reel every build."""
    base = need.carried_digest(FakeSnapshot([FakeClip(
        transform={"Pan": 0.0, "ZoomX": 2.3070000001})]))
    same = need.carried_digest(FakeSnapshot([FakeClip(
        transform={"Pan": 0.0, "ZoomX": 2.30700000009})]))
    assert base == same


# ── The derivation half ──────────────────────────────────────────────

def _derivation(**overrides):
    body = dict(
        reel_number=1,
        engine_code="e" * 64,
        project_wide="p" * 64,
        plan_content_hash="l" * 64,
        transcript_hash="t" * 64,
        master_digest="m" * 64,
        ranges=[(1.0, 2.0)],
        placements_list=[],
        cards=[],
        caption_segments=[],
        explainer_segments=[],
        semantic_segments=[],
        overlay_placements=[],
        motion_record={},
        ending=None,
        look=None,
        grade_cdl=None,
        grade_look=None,
        power_grade=None,
    )
    body.update(overrides)
    return need.derivation_digest(**body)


def test_a_derivation_with_a_wholesale_half_missing_is_no_answer():
    """Not a weaker match - not a match. A digest computed without
    knowing the engine would leave a reel unplaced across a code
    change."""
    for half in ("engine_code", "project_wide"):
        assert _derivation(**{half: None}) is None
        assert _derivation(**{half: ""}) is None


def test_every_input_the_derivation_reads_changes_it():
    base = _derivation()
    for change in (
        {"reel_number": 2},
        {"engine_code": "z" * 64},
        {"plan_content_hash": "z" * 64},
        {"master_digest": "z" * 64},
        {"ranges": [(1.0, 3.0)]},
        {"ending": {"tail_element": "logo_reveal"}},
        {"look": {"punch_in": 1.1}},
        {"grade_cdl": {"slope_r": 1.0}},
        {"grade_look": {"glow": 1}},
        {"power_grade": {"path": "/g.drx"}},
        {"motion_record": {"basis": "answered"}},
        {"extra": {"skip_captions": True}},
    ):
        assert _derivation(**change) != base, change


def test_a_rendered_segment_that_changed_pixels_changes_the_derivation(
        tmp_path):
    artefact = tmp_path / "sub.mov"
    artefact.write_bytes(b"one")
    segment = {"segment_id": "s1", "source_in_frame": 0,
               "source_out_frame": 10, "reel_start_frame": 0,
               "frames": 10, "width": 840, "height": 480,
               "file": str(artefact)}
    first = _derivation(caption_segments=[segment])
    artefact.write_bytes(b"two")
    assert _derivation(caption_segments=[segment]) != first

