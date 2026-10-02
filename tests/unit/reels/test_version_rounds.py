"""The version object: one version per BATCH of the captain's feedback.

The captain's answer of 2026-09-12 to the versioning question
(`data/vep-can-it-hold-up-in-a-real-editing-workflow` §7) is *per
round*, and a round is DISCOVERED from what already exists rather than
declared: the interleaving of the asks in the feedback ledger with the
promotions that answered them.

Each test here fails if its mechanism is removed:

- the boundary rule itself - remove it (one round for everything, or a
  round per ask) and the batching assertions fail;
- replies of ours never opening a round;
- the stamp carrying the rows, which is what makes `versions.rounds` free;
- backfill marking every entry RECONSTRUCTED and carrying no
  `built_with` - a stamp invented after the fact is not a measurement;
- a stamped entry outranking a reconstructed one.
"""
import json
import subprocess
import pytest
from library.tools.versions import rounds as rv
from library.tools.versions import rounds
from pathlib import Path
from library.tools import provenance
from library.tools.project_layout import Area, ProjectLayout
from library.tools.versions.runs import archive_previous_run
import sys
from library.tools.versions import store as bvc


def _ask(identity, reel, when):
    return {"identity": identity, "reel": reel, "first_asked": when}


# ── The boundary rule ────────────────────────────────────────────

def test_a_round_is_a_batch_of_asks_bounded_by_builds():
    """Six markers at once, one pass, one result to look at.

    Nothing was promoted between them, so they are one batch - which is
    the captain's own definition of a round and is what a per-ask
    version would get wrong.
    """
    rounds = rv.round_boundaries(
        [_ask(f"n{n}", f"Reel 0{n}", f"2026-09-11T03:08:4{n}Z")
         for n in range(1, 6)],
        promotions=["2026-09-10T22:00:00Z"])
    assert [entry["round"] for entry in rounds] == [1, 2]
    assert len(rounds[1]["opened_by"]) == 5
    assert rounds[1]["opened_at"] == "2026-09-11T03:08:41Z"

    # The other half: an ask arriving once a build answered the previous
    # batch opens a round of its own; remove the promotion between them
    # and the asks collapse - the boundary is the build, not a clock.
    asks = [_ask("a", "Reel 01", "2026-09-11T03:08:43Z"),
            _ask("b", "Reel 09", "2026-09-11T22:51:54Z")]
    with_build = rv.round_boundaries(
        asks, promotions=["2026-09-10T20:00:00Z",
                          "2026-09-11T17:05:52Z"])
    assert [entry["round"] for entry in with_build] == [1, 2, 3]
    assert [len(entry["opened_by"]) for entry in with_build] == [0, 1, 1]

    without_build = rv.round_boundaries(
        asks, promotions=["2026-09-10T20:00:00Z"])
    assert [entry["round"] for entry in without_build] == [1, 2]
    assert len(without_build[1]["opened_by"]) == 2


def test_a_reply_of_ours_opens_no_round(tmp_path, monkeypatch):
    """`asks_of` reads ASKS only. A green reply of ours typed onto the
    timeline between two rounds is not the captain asking for anything,
    and a version stamped on one would be a version of our own
    answer."""
    from library.tools import feedback_ledger

    class Entry:
        def __init__(self, identity, kind):
            self.identity = identity
            self.reel = "Reel 09"
            self.first_asked = "2026-09-11T04:47:30Z"
            self.kind = kind

    monkeypatch.setattr(
        feedback_ledger, "collect",
        lambda folder: {"a": Entry("a", feedback_ledger.KIND_ASK),
                        "b": Entry("b", feedback_ledger.KIND_REPLY)})
    asks = rv.asks_of(str(tmp_path))
    assert [ask["identity"] for ask in asks] == ["a"]


# ── Stamping ─────────────────────────────────────────────────────

def _rows(count, frames):
    return {"video:Akshita": {
        "media_type": "video", "index": 1, "name": "Akshita",
        "items": [{"name": f"clip {n}", "start": n * 10,
                   "end": n * 10 + 10, "duration": 10}
                  for n in range(count)],
        "count": count, "frames": frames}}


def test_a_promotion_stamps_the_round_with_the_rows_it_promoted(tmp_path):
    """The rows are the payload that makes a round diff free: stored
    here, `versions.rounds` answers off disk with no Resolve and long after
    the timeline itself has been retired and collected."""
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    stamped = rv.stamp_promotion(
        str(project), {"Reel 09 - x": _rows(3, 30)},
        provenance={"built_at_reels": {"Reel 09 - x": "2026-09-12T01:00Z"},
                    "built_with": {"Reel 09 - x": "abc123"}})
    assert stamped["round"] == 1
    entry = stamped["reels"]["Reel 09 - x"]
    assert entry["source"] == rv.SOURCE_STAMPED
    assert entry["built_with"] == "abc123"
    assert entry["rows"]["video:Akshita"]["count"] == 3

    document = rv.read_rounds(str(project))
    assert document["rounds"][-1]["reels"]["Reel 09 - x"]["rows"] \
        ["video:Akshita"]["frames"] == 30


def test_an_unreadable_rounds_file_refuses_rather_than_starting_empty(
        tmp_path):
    """The record of what was built when is the one thing that cannot be
    recomputed. Replacing an unreadable one with an empty document
    would lose every round silently."""
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / rv.ROUNDS_FILENAME).write_text("{not json",
                                             encoding="utf-8")
    with pytest.raises(rv.RoundsUnreadable):
        rv.read_rounds(str(project))


# ── Backfill ─────────────────────────────────────────────────────

def _git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args],
                          capture_output=True, encoding="utf-8",
                          check=True)


def _snapshot(count):
    return {"metadata": {"name": "Reel 09 - x"},
            "tracks": [{"type": "video", "index": 1, "name": "Akshita",
                        "clips": [{"name": f"clip {n}",
                                   "record_in": n * 10,
                                   "record_out": n * 10 + 10,
                                   "duration": 10}
                                  for n in range(count)]}]}


@pytest.fixture
def repo_project(tmp_path):
    """A project with two committed timeline snapshots - the shape
    `versions.store.record_reel_promotion` writes on every
    promotion."""
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "t")
    path = review / "Reel_09_-_x.timeline.json"
    for count in (2, 3):
        path.write_text(json.dumps(_snapshot(count)), encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m", f"reels build: {count}")
    return project


def test_backfill_reconstructs_from_the_committed_snapshots(repo_project):
    """The rounds that predate the stamp are recoverable, because every
    promotion since 2026-09-11 committed the timeline it promoted."""
    report = rv.backfill(str(repo_project))
    assert report["commits"] == 2
    assert report["reels"] >= 1
    rounds = rv.read_rounds(str(repo_project))["rounds"]
    promoted = [entry for entry in rounds if entry["reels"]]
    assert promoted, "backfill recorded no reel"
    latest = promoted[-1]["reels"]["Reel 09 - x"]
    assert latest["rows"]["video:Akshita"]["count"] == 3
    # Which engine revision built a reel cannot be recovered once the
    # build is over (AGENTS.md 10.1), so a reconstruction says
    # RECONSTRUCTED and leaves the stamp empty rather than inventing one.
    entries = [reel for entry in rv.read_rounds(str(repo_project))["rounds"]
               for reel in (entry["reels"] or {}).values()]
    assert entries
    for reel in entries:
        assert reel["source"] == rv.SOURCE_RECONSTRUCTED
        assert reel["built_with"] == ""
        assert reel["commit"]


def test_a_measurement_outranks_a_reconstruction(repo_project):
    """A stamp taken at promotion time is never overwritten by a later
    backfill: the reconstruction knows strictly less."""
    rv.backfill(str(repo_project))
    document = rv.read_rounds(str(repo_project))
    target = [entry for entry in document["rounds"] if entry["reels"]][-1]
    target["reels"]["Reel 09 - x"]["source"] = rv.SOURCE_STAMPED
    target["reels"]["Reel 09 - x"]["built_with"] = "deadbeef"
    rv.write_rounds(str(repo_project), document)

    rv.backfill(str(repo_project))
    kept = [entry for entry in rv.read_rounds(str(repo_project))["rounds"]
            if entry["reels"]][-1]["reels"]["Reel 09 - x"]
    assert kept["source"] == rv.SOURCE_STAMPED
    assert kept["built_with"] == "deadbeef"


# --------------------------------------------------------------------------
# From test_version_round_diff.py
#
# What changed between round 3 and round 4, in the captain's terms.
#
# The payoff of the version object, and the reason the versioning
# question mattered: without it a round is SUPERSEDED - the new reel is
# there and the old one is gone - and with it a round is REVIEWABLE.
#
# Each test fails if its mechanism is removed:
#
# - the diff itself, over two STORED row snapshots, off disk;
# - a reel not rebuilt in a round reported as such rather than as a loss;
# - the re-render classification, without which every round diff is
#   mostly noise (every overlay filename carries a content digest, so a
#   rebuild that changed nothing lands 34 new names at 34 identical
#   spans) - and noise is how a real change goes unread;
# - the guard's own row vocabulary, so a row named in a round diff and a
#   row named in a promotion refusal are spelled the same way;
# - a missing round raising rather than diffing against nothing.

def _row(name, items):
    return {"media_type": "video", "index": 1, "name": name,
            "items": list(items),
            "count": len(items),
            "frames": sum(item["duration"] for item in items)}


def _item(name, start, duration):
    return {"name": name, "start": start, "end": start + duration,
            "duration": duration}


def _rounds(tmp_path, earlier_reels, later_reels):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    rounds.write_rounds(str(project), {
        "format": rounds.ROUNDS_FORMAT,
        "rounds": [
            {"round": 2, "opened_at": "2026-09-11T03:08Z",
             "opened_by": ["a"], "reels_asked": ["Reel 01"],
             "why": "opened by the captain's feedback",
             "reels": earlier_reels},
            {"round": 3, "opened_at": "2026-09-11T22:51Z",
             "opened_by": ["b"], "reels_asked": ["Reel 09"],
             "why": "opened by the captain's feedback",
             "reels": later_reels},
        ]})
    return project


def _reel(rows, when="2026-09-12T01:28Z"):
    return {"promoted_at": when, "built_at": when, "built_with": "abc",
            "rows": rows, "source": rounds.SOURCE_STAMPED}


def test_a_row_gained_or_lost_is_named_in_the_guards_vocabulary(tmp_path):
    """The real shape of the 2026-09-12 round on the captain's project:
    a tail logo card arrived on the Akshita row of six reels."""
    earlier = {"Reel 01": _reel({"video:Akshita": _row(
        "Akshita", [_item("LC4930.MXF", 0, 684)])})}
    later = {"Reel 01": _reel({"video:Akshita": _row(
        "Akshita", [_item("LC4930.MXF", 0, 684),
                    _item("logo_reveal.mov", 1274, 71)])})}
    project = _rounds(tmp_path, earlier, later)

    diff = rounds.diff_rounds(str(project), 2, 3)
    rendered = rounds.render_diff(diff)
    assert "logo_reveal.mov" in rendered
    assert "3 -> 4 item(s)" not in rendered      # 1 -> 2 here
    assert "1 -> 2 item(s)" in rendered
    changed = diff["reels"]["Reel 01"]["changed"]
    assert [row["key"] for row in changed] == ["video:Akshita"]
    assert changed[0]["gained"][0]["name"] == "logo_reveal.mov"

    # A whole row gone is named as gone, in the promote guard's own
    # vocabulary (the V5 'Semantic' row shape).
    earlier = {"Reel 01": _reel({
        "video:Semantic": _row("Semantic", [_item("s.mov", 0, 40)])})}
    project = _rounds(tmp_path / "gone", earlier, {"Reel 01": _reel({})})
    diff = rounds.diff_rounds(str(project), 2, 3)
    entry = diff["reels"]["Reel 01"]
    assert entry["changed"][0]["state"] == rounds.LOST_ROW
    assert "the row is gone" in rounds.render_diff(diff)


def test_overlays_re_rendered_at_identical_spans_are_not_a_change(
        tmp_path):
    """Every caption `.mov` carries a content digest in its name, so a
    rebuild that changed nothing lands 34 new filenames at 34 identical
    spans. Remove this and the change that matters is buried under
    them - which is the failure mode this classification exists for."""
    earlier = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item(f"sub_{n}_aaaa.mov", n * 100, 50)
                      for n in range(15)])})}
    later = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item(f"sub_{n}_bbbb.mov", n * 100, 50)
                      for n in range(15)])})}
    project = _rounds(tmp_path, earlier, later)

    diff = rounds.diff_rounds(str(project), 2, 3)
    entry = diff["reels"]["Reel 01"]
    assert entry["changed"] == []
    assert [row["key"] for row in entry["rerendered"]] == [
        "video:Subtitles"]
    rendered = rounds.render_diff(diff)
    assert "rebuilt, and nothing moved" in rendered
    assert "re-rendered at identical spans" in rendered
    # And it is a MEASUREMENT of the spans, not a reading of the
    # filenames: nothing here parses a naming scheme.
    assert "sub_0_bbbb.mov" not in rendered

    # The bound on the classification: one span differs and the row is
    # a change again.
    earlier = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item("a.mov", 0, 50), _item("b.mov", 50, 50)])})}
    later = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item("a.mov", 0, 50), _item("b.mov", 60, 50)])})}
    project = _rounds(tmp_path / "moved", earlier, later)

    entry = rounds.diff_rounds(str(project), 2, 3)["reels"]["Reel 01"]
    assert [row["key"] for row in entry["changed"]] == ["video:Subtitles"]
    assert entry["rerendered"] == []


def test_a_reel_not_rebuilt_in_the_round_says_so(tmp_path):
    """A real answer - "round 3 did not touch Reel 13" - not a gap, and
    certainly not a loss."""
    earlier = {"Reel 13": _reel({"video:Akshita": _row(
        "Akshita", [_item("a.mov", 0, 10)])})}
    project = _rounds(tmp_path, earlier, {})
    diff = rounds.diff_rounds(str(project), 2, 3)
    assert diff["reels"]["Reel 13"]["state"] == "not rebuilt in this round"
    assert "not rebuilt in round 3" in rounds.render_diff(diff)


def test_a_missing_round_raises_rather_than_diffing_against_nothing(
        tmp_path):
    """An empty side reads exactly like a round that removed
    everything."""
    project = _rounds(tmp_path, {}, {})
    with pytest.raises(ValueError) as refused:
        rounds.diff_rounds(str(project), 2, 9)
    assert "not recorded" in str(refused.value)


# --------------------------------------------------------------------------
# From test_version_runs.py
#
# Tests that reasoning traces and LLM archives survive across runs.
#
# The core assertion: a second run cannot silently overwrite the first
# run's reasoning traces, and a retry with the same run id is idempotent.

@pytest.fixture
def scratch_project(tmp_path):
    """A minimal project directory with pipeline_data.json and
    reasoning / llm_request / llm_response files that simulate a
    completed run."""
    project = tmp_path / "scratch_project"
    project.mkdir()

    # pipeline_data.json - minimal
    state = {
        "project_folder": str(project),
        "edit_completed": ["creative_direction"],
        "step_outputs": {},
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )
    (project / "project.yaml").write_text("slug: scratch\n", encoding="utf-8")

    # Create pipeline_output dirs
    layout = ProjectLayout(project)
    reasoning_dir = layout.write_dir(Area.REASONING)
    requests_dir = layout.write_dir(Area.LLM_REQUESTS)
    responses_dir = layout.write_dir(Area.LLM_RESPONSES)

    # Simulate run-1 output
    (reasoning_dir / "creative_direction.md").write_text(
        "# Reasoning: creative_direction\n\n"
        "I read the footage catalog and chose narrative thread A.\n"
        "Confidence: 0.85\n",
        encoding="utf-8",
    )
    (reasoning_dir / "speech_sequence.md").write_text(
        "# Reasoning: speech_sequence\n\n"
        "Selected 46s of speech from 13 minutes.\n",
        encoding="utf-8",
    )
    (requests_dir / "creative_direction.json").write_text(
        json.dumps({"step_id": "creative_direction", "prompt": "decide"}),
        encoding="utf-8",
    )
    (responses_dir / "creative_direction.json").write_text(
        json.dumps({"narrative_thread": "A"}),
        encoding="utf-8",
    )

    return project


def test_archive_preserves_first_run(scratch_project):
    """A second run archives the first run's traces and cannot overwrite them."""
    project = scratch_project
    layout = ProjectLayout(project)

    # Verify the reasoning files exist
    reasoning_dir = layout.read_dir(Area.REASONING)
    assert (reasoning_dir / "creative_direction.md").is_file()
    assert (reasoning_dir / "speech_sequence.md").is_file()
    original_content = (reasoning_dir / "creative_direction.md").read_text(
        encoding="utf-8"
    )

    # --- "Run 2" starts: archive the previous run's artifacts ---
    run_id_2 = provenance.new_run_id()
    archived = archive_previous_run(str(project), run_id_2)

    # Archive was created
    assert archived is not None
    assert archived.is_dir()
    assert (archived / "reasoning" / "creative_direction.md").is_file()
    assert (archived / "reasoning" / "speech_sequence.md").is_file()
    assert (archived / "llm_requests" / "creative_direction.json").is_file()
    assert (archived / "llm_responses" / "creative_direction.json").is_file()

    # Archived content matches original
    archived_content = (archived / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    )
    assert archived_content == original_content

    # Now simulate run 2 overwriting the reasoning files
    (reasoning_dir / "creative_direction.md").write_text(
        "# Reasoning: creative_direction (run 2)\n\n"
        "Different reasoning for run 2.\n",
        encoding="utf-8",
    )

    # The archive still has the ORIGINAL content
    archived_after = (archived / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    )
    assert archived_after == original_content
    assert archived_after != (reasoning_dir / "creative_direction.md").read_text(
        encoding="utf-8"
    )

    # A retry with the same run_id returns the existing archive and does
    # not overwrite it with the changed traces.
    assert archive_previous_run(str(project), run_id_2) == archived
    assert archived_after == (archived / "reasoning" / "creative_direction.md"
                              ).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# From test_version_store.py
#
# Pin the per-project version-control contract (AGENTS.md 3).
#
# The allow-list test names a binary directory the module never names:
# if a future binary area is added to the engine, this is the test that
# catches it being swallowed into the repo.

def _git_2(project, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _write(project, rel, content="x"):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


TEXT_FILES = [
    "project.yaml",
    "pipeline_data.json",
    "pipeline_run.json",
    "learned_context/learnings.json",
    "external/captain_edits.json",
    "context/look.md",
    "profiles/tight.json",
    "pipeline_output/steps/4_05_render_subtitles/output.json",
    "pipeline_output/steps/4_05_render_subtitles/summary.md",
    "pipeline_output/steps/5_04_compile_manifest/assembly_manifest.json",
    "pipeline_output/steps/6_01_render/fusion_comps/a.comp",
    "pipeline_output/steps/6_01_render/otio/cut.build.otio",
    "pipeline_output/steps/6_01_render/otio/cut.timeline.json",
    "pipeline_output/steps/6_01_render/otio/cut.BUILD-RECORD.md",
    "pipeline_output/llm_requests/r.json",
    "pipeline_output/llm_responses/r.json",
    "pipeline_output/gates/g.json",
    "pipeline_output/review/channel.json",
    "pipeline_output/review/reel_variants.json",
    "marker_feedback/pull.json",
    "timeline_captures/reel13-live-20260911/reel13_live_state.json",
    "pipeline_output/provenance/p.json",
    "pipeline_output/quarantine/mark_20240101.json",
    "pipeline_output/quarantine/mark_20240101.md",
    "pipeline_output/quarantine/sweep_20240101_manifest.md",
    "pipeline_output/RUN-TRACEBACK.md",
    "pipeline_output/ARTIFACTS.md",
]

# Binaries that must NEVER land in the repo - including
# 9_99_newthing/, a step directory this module never names, which is
# the regression shape for "the next new binary directory somebody
# adds".
BINARY_DECOYS = [
    "pipeline_output/steps/4_05_render_subtitles/seg_0001.mov",
    "pipeline_output/steps/9_99_newthing/big.bin",
    "pipeline_output/scratch/work.tmp",
    "pipeline_output/quarantine/moved_aside.mov",
    "pipeline_output/thumbnails/thumb.jpg",
    "pipeline_output/steps/7_01_build_reels/frame_overlays/f1.png",
    "exports/final.mp4",
    "audio/a.wav",
    "subtitle_overlays/o.mov",
]


# ── Declaration-store coverage ─────────────────────────────────────
#
# The class, enumerated 2026-09-12 when `learned_context/learnings.json`
# was found unversioned: every store the pipeline READS that controls
# the edit, checked against the GENERATED allow-list, with the verdict
# for each.
#
# Tracked (asserted below - each path is DERIVED from the reading
# module's own constants, never copied out of ALLOW_LIST, so deleting
# an allow-list entry breaks the assertion that names its reader):
#   project.yaml                       <- brand_registry, schemas
#   external/<key>.json                <- external_inputs, captain_edits
#   context/                           <- project_context
#   profiles/                          <- run_profile
#   learned_context/learnings.json     <- transcript_corrections,
#      project_context, reel_build, reel_conformance_verifier,
#      layer_coherence, captain_edits
#   marker_feedback/ (pulls, ledger, resolutions, stills)
#                                      <- marker_feedback, marker_routing,
#      feedback_ledger, marker_resolution, marker_capture
#   timeline_captures/                 <- hand-edit evidence (pinned above
#      in TEXT_FILES)
#   pipeline_output/provenance/creative_brief_snapshot.md (+ digest
#      sidecar)                       <- brief_snapshot, asserted in
#      tests/unit/context/test_brief.py rather than below
#
# Deliberately NOT tracked, and why:
#   creative_brief (the project.yaml-declared path; live: the
#      root-level creative_brief.md) - read by nine steps BY REFERENCE,
#      but the declaration is a free per-project path that may sit
#      outside the project folder entirely, so no static allow-list can
#      name it. The versioned project.yaml records WHERE it was - and
#      the run snapshots WHAT it said into
#      `pipeline_output/provenance/creative_brief_snapshot.md` plus its
#      digest sidecar (library/tools/brief_snapshot.py), which this
#      allow-list DOES version. The path stays free; the versioning
#      stopped being static. See tests/unit/context/test_brief.py.
#   brand_assets/, assets/, compositions/ - the captain's artwork and
#      source tree. Binary-capable (PNG, .drx, fonts, .mov), and the
#      allow-list's stated purpose is text-only; the versioned
#      project.yaml paths that REFERENCE them survive without the blobs.
#   subtitle_plans/, subtitle_overlays/ - the captain's standalone
#      scripts' area (props plus binary .mov renders); the pipeline
#      renders its own versioned copies under steps/4_05.
#   raw/, music/, audio/, transcripts/, fonts/ - source media and
#      derived caches: bulky, or reproducible by re-transcription.
#   pipeline_output/reasoning/, logs/, backups/, scratch/
#      - recomputable output, not declarations.
#
# A test that merely restated ALLOW_LIST would pass just as happily
# with learned_context/ still missing. This one cannot: its input
# comes from the modules that read the stores.


def _declaration_stores():
    """Store paths derived from the readers, not from the allow-list."""
    from library.tools import captain_edits
    from library.tools import feedback_ledger
    from library.tools import learned_context
    from library.tools import marker_resolution
    from library.tools.project_layout import (
        AREAS, Area, PROJECT_CONFIG_FILE)

    def _rel(area):
        return AREAS[Area(area)].relpath

    return [
        # project.yaml: read by brand_registry and the config schema.
        (PROJECT_CONFIG_FILE, "project.yaml readers"),
        # external/: read by external_inputs (CHECKS) and captain_edits.
        (f"{_rel(Area.EXTERNAL_STATE)}/{captain_edits.CAPTAIN_EDITS_KEY}.json",
         "external_inputs / captain_edits"),
        # context/: read by project_context on every planning step.
        (f"{_rel(Area.CONTEXT)}/look.md", "project_context"),
        # profiles/: read by run_profile.
        (f"{_rel(Area.RUN_PROFILES)}/tight.json", "run_profile"),
        # learned_context/: the single JSON record learned_context.py
        # writes; read by transcript_corrections, project_context,
        # reel_build, reel_conformance_verifier, layer_coherence and
        # captain_edits.
        (f"{_rel(Area.LEARNED_CONTEXT)}/{learned_context.LEARNINGS_FILE}",
         "transcript_corrections / learned_context readers"),
        # marker_feedback/: pulls, the feedback ledger and resolutions.
        (f"{_rel(Area.MARKER_FEEDBACK)}/{feedback_ledger.LEDGER_FILENAME}",
         "feedback_ledger"),
        (f"{_rel(Area.MARKER_FEEDBACK)}/"
         f"{marker_resolution.RESOLUTIONS_SUBDIR}/x.json",
         "marker_resolution"),
    ]


def test_learned_context_crash_tmp_stays_out(tmp_path):
    """The allow-list names the record, not the directory.

    `learned_context._save` writes through a `.learnings.*.tmp` file
    beside the record; a crash leaves one behind. Whole-directory
    re-inclusion would sweep it into the repo, so the entry is the
    file - and this pins that the leftover stays ignored.
    """
    from library.tools import learned_context
    from library.tools.project_layout import AREAS, Area

    rel = AREAS[Area(Area.LEARNED_CONTEXT)].relpath
    _write(tmp_path, f"{rel}/{learned_context.LEARNINGS_FILE}", "[]\n")
    _write(tmp_path, f"{rel}/.learnings.abc123.tmp", "{}\n")
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True
    assert bvc.commit_build(str(tmp_path), message="first\n")["committed"]
    tracked = set(_git_2(tmp_path, "ls-files").splitlines())
    assert f"{rel}/{learned_context.LEARNINGS_FILE}" in tracked
    assert f"{rel}/.learnings.abc123.tmp" not in tracked


# ── Text-only store (D7) ─────────────────────────────────────────
#
# The per-project store auto-committed with `git add -A` and tracked
# 716 MB on geo-podcast, including PNGs, a .drp, .drt and .wav
# files.  D7 (2026-09-23): text only; binaries recorded by hash and
# regenerated.  These pin the three halves: a versioned binary is
# never committed, a binary tracked before the rule leaves the repo
# with the working tree untouched, and the manifest can be checked.


def test_store_never_commits_a_binary(tmp_path):
    """A binary on a versioned path joins the manifest, not the repo.

    `marker_feedback/stills/` (Resolve still PNGs plus the `.drx`
    sidecar `ExportStills` writes unasked) and `timeline_captures/`
    are allow-listed wholesale, so the path allow-list alone cannot
    keep them out - this is the defect the text-type gate closes.
    """
    import hashlib

    _write(tmp_path, "pipeline_data.json", '{"a": 1}\n')
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    drx = b"\x00\x01binarygrade" + b"\x00" * 50
    _write(tmp_path, "marker_feedback/stills/cap1.png", png)
    _write(tmp_path, "marker_feedback/stills/cap1_1.1.1.drx", drx)
    mov = b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 100
    _write(tmp_path, "pipeline_output/review/clip.mov", mov)
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True

    result = bvc.commit_build(str(tmp_path), message="first\n")

    assert result["committed"] is True
    tracked = set(_git_2(tmp_path, "ls-files").splitlines())
    assert "pipeline_data.json" in tracked
    assert "marker_feedback/stills/cap1.png" not in tracked
    assert "marker_feedback/stills/cap1_1.1.1.drx" not in tracked
    assert "pipeline_output/review/clip.mov" not in tracked
    manifest_path = (tmp_path / "pipeline_output" / "provenance"
                     / "binary_manifest.json")
    assert "pipeline_output/provenance/binary_manifest.json" in tracked
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path["marker_feedback/stills/cap1.png"]["sha256"] == \
        hashlib.sha256(png).hexdigest()
    assert by_path["marker_feedback/stills/cap1.png"]["size"] == len(png)
    assert by_path["marker_feedback/stills/cap1_1.1.1.drx"]["sha256"] == \
        hashlib.sha256(drx).hexdigest()
    assert by_path["pipeline_output/review/clip.mov"]["sha256"] == \
        hashlib.sha256(mov).hexdigest()


def test_manifest_skips_ignored_renders_beside_step_records(tmp_path):
    """A render in a step directory is ignored, so it is no versioned binary.

    `pipeline_output/steps/*/` is globbed whole, so every render in it
    is a candidate until `git check-ignore` filters it. That call passed
    `--stdin` beside pathspecs, which git refuses, so nothing was ever
    filtered - and on geo-podcast the argv overflowed (E2BIG) and the
    commit raised.
    """
    _write(tmp_path, "pipeline_output/steps/4_05_render_subtitles/output.json",
           "{}\n")
    render = "pipeline_output/steps/4_05_render_subtitles/seg_0001.mov"
    _write(tmp_path, render, b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 100)
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True

    result = bvc.commit_build(str(tmp_path), message="first\n")

    assert result["committed"] is True
    assert render not in {e["path"] for e in result["binaries_recorded"]}


def test_legacy_tracked_binary_leaves_the_repo_but_stays_on_disk(tmp_path):
    """A binary tracked before the text-only rule is untracked, not kept.

    `git add -A` stages modifications to already-tracked files even
    when the ignore would refuse them untracked, so without the
    `rm --cached` half the 60 geo-podcast PNGs would keep
    recommitting their bytes on every build.  The working tree and
    the history are untouched - only the tracking ends.
    """
    import hashlib

    _write(tmp_path, "pipeline_data.json", '{"a": 1}\n')
    old = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    _write(tmp_path, "marker_feedback/stills/cap1.png", old)
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True
    # Simulate the pre-rule history: the binary got in before the
    # allow-list existed.
    _git_2(tmp_path, "add", "-f", "marker_feedback/stills/cap1.png")
    _git_2(tmp_path, "commit", "-m", "legacy binary")
    new = b"\x89PNG\r\n\x1a\n" + b"\x01" * 100
    _write(tmp_path, "marker_feedback/stills/cap1.png", new)

    result = bvc.commit_build(str(tmp_path), message="second\n")

    assert result["committed"] is True
    assert "marker_feedback/stills/cap1.png" not in \
        set(_git_2(tmp_path, "ls-files").splitlines())
    # The bytes on disk are the new ones - the store never rewrites
    # the working tree.
    assert (tmp_path / "marker_feedback" / "stills" / "cap1.png"
            ).read_bytes() == new
    manifest = json.loads(
        (tmp_path / "pipeline_output" / "provenance"
         / "binary_manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["sha256"] == hashlib.sha256(new).hexdigest()


def test_modified_jsonl_log_stays_tracked(tmp_path):
    """A JSON Lines log is text, so a modification is committed, not uncached.

    Uncaching it records a deletion on the branch, and merging that
    branch into one that still tracks the log deletes the live file -
    `pipeline_output/review/reel_phase_log.jsonl` on geo-podcast.
    """
    log = "pipeline_output/review/reel_phase_log.jsonl"
    _write(tmp_path, log, '{"phase": 1}\n')
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True
    assert bvc.commit_build(str(tmp_path), message="first\n")["committed"]
    _write(tmp_path, log, '{"phase": 1}\n{"phase": 2}\n')

    result = bvc.commit_build(str(tmp_path), message="second\n")

    assert result["committed"] is True
    assert log in set(_git_2(tmp_path, "ls-files").splitlines())
    assert _git_2(tmp_path, "show", f"HEAD:{log}").count("phase") == 2


def test_verify_binary_manifest_checks_hashes(tmp_path):
    """The manifest closes the D7 loop: regenerate, then check."""
    _write(tmp_path, "pipeline_data.json", '{"a": 1}\n')
    _write(tmp_path, "marker_feedback/stills/cap1.png",
           b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    _write(tmp_path, "marker_feedback/stills/cap2.png",
           b"\x89PNG\r\n\x1a\n" + b"\x01" * 10)
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True
    assert bvc.commit_build(str(tmp_path), message="first\n")["committed"]

    fresh = bvc.verify_binary_manifest(str(tmp_path))
    assert fresh["checked"] == 2
    assert fresh["mismatched"] == [] and fresh["missing"] == []

    _write(tmp_path, "marker_feedback/stills/cap1.png", b"changed")
    (tmp_path / "marker_feedback" / "stills" / "cap2.png").unlink()
    stale = bvc.verify_binary_manifest(str(tmp_path))
    assert stale["mismatched"] == ["marker_feedback/stills/cap1.png"]
    assert stale["missing"] == ["marker_feedback/stills/cap2.png"]


def test_build_record_names_the_blind_spot(tmp_path):
    written = bvc.write_build_record(
        str(tmp_path), "cut_01", "OTIO-body", {"schema_version": "1.0"})
    record = (tmp_path / f"pipeline_output/steps/6_01_render/otio/"
              "cut_01.BUILD-RECORD.md").read_text(encoding="utf-8")
    assert "Fusion comps empty" in record
    assert "CDL grades absent" in record
    assert "fusion_comps" in record
    assert written["otio"].endswith("cut_01.build.otio")
    assert written["timeline_json"].endswith("cut_01.timeline.json")


def test_record_without_repo_declines(tmp_path):
    class _Timeline:
        def Export(self, path, flag):
            raise AssertionError("must not reach Resolve without a repo")

    report = bvc.record_finished_timeline(
        object(), _Timeline(), str(tmp_path), "cut_01")
    assert report == {"committed": False, "reason": "no-repo", "files": []}


# ── The reels promotion record ─────────────────────────────────────
#
# The 6.01 hook never fired for reels, so no reel build committed its
# baseline (measured 2026-09-11). `record_reel_promotion` closes that:
# one snapshot per promoted timeline beside the declaration, then the
# commit. These pin the decline paths and the snapshot-then-commit
# shape; a Resolve that is absent or on another project records the
# reason instead of raising.


class _FakeTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class _FakeProject:
    def __init__(self, name, timelines):
        self._name = name
        self._timelines = list(timelines)
        self._current = self._timelines[0]

    def GetName(self):
        return self._name

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetCurrentTimeline(self):
        return self._current

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


class _FakeManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _FakeResolve:
    def __init__(self, project):
        self._project = project

    def GetProjectManager(self):
        return _FakeManager(self._project)


class _FakeDvr:
    def __init__(self, resolve):
        self._resolve = resolve

    def scriptapp(self, name):
        return self._resolve


def _promotion_project(name="Podcast (field test)"):
    project = _FakeProject(name, [
        _FakeTimeline("GEO Podcast - Synced"),
        _FakeTimeline("Reel 28 - the-nail-salon-query-google-cant-answer"),
    ])
    return project


def test_reel_promotion_without_repo_declines(tmp_path):
    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)", ["Reel 28"])
    assert report["committed"] is False
    assert report["reason"] == "no-repo"


def test_reel_promotion_commits_the_declarations_when_the_snapshot_fails(
        tmp_path, monkeypatch):
    """A snapshot the build could not take must NOT swallow the commit.

    Measured 2026-09-11 on the captain's project: the snapshot half
    returned early on any Resolve trouble - closed, busy, another
    project open - and the declarations, run state and step outputs
    already on disk went uncommitted with it. A declaration nothing in
    git remembers is one that goes missing the next time somebody edits
    the file, which is the failure this whole store exists to stop.
    """
    bvc.init_project_repo(str(tmp_path))
    (tmp_path / "external").mkdir()
    (tmp_path / "external" / "reel_ending.json").write_text(
        '{"version": 1, "endings": []}', encoding="utf-8")
    project = _promotion_project(name="Something else")
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        _FakeDvr(_FakeResolve(project)))

    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)", ["Reel 28"])

    assert report["committed"] is True
    assert report["snapshots"] == []
    # The failure is NAMED rather than dropped, in the report and in
    # the commit message - so a reader of the log can tell which
    # commits have no timeline snapshot behind them.
    assert "Something else" in report["snapshot_failed"]
    assert "snapshot failed" in report["reason"]
    log = subprocess.run(["git", "log", "-1", "--format=%B"],
                         cwd=str(tmp_path), capture_output=True,
                         text=True, encoding="utf-8", check=False)
    assert "NO TIMELINE SNAPSHOT" in log.stdout
    assert "external/reel_ending.json" in report["files"]


# --------------------------------------------------------------------------
# From test_reel_record_diff.py
#
# git diff for a reel: live projection vs last committed .timeline.json.
#
# The captain's version-control judgement (2026-09-21) makes the FILE the
# unit of revert and the COMMIT the unit of change - and what that needs
# is a printed diff of each reel's live projection against its last
# committed `.timeline.json` that NEVER refuses. Each test below names
# which half of that it would catch the removal of.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import reel_divergence as rd  # noqa: E402


# ── Fakes: serializer-shaped documents ──────────────────────────

def make_clip(name, record_in, record_out=None, **overrides):
    clip = {
        "name": name,
        "record_in": record_in,
        "record_out": (record_out if record_out is not None
                       else record_in + 100),
        "source_in": 0,
        "source_out": 100,
        "enabled": True,
        "file_path": f"/footage/{name}",
        "transform": {"Pan": 0.0, "Tilt": 0.25, "ZoomX": 2.307,
                      "ZoomY": 2.307},
        "crop": {"CropLeft": 0.0, "CropRight": 0.0, "CropTop": 0.0,
                 "CropBottom": 0.0, "CropSoftness": 0.0},
        "composite": {"Opacity": 100.0, "CompositeMode": "Normal"},
        "retime": {"process": "", "motion_estimation": "", "speed_ratio": 1.0},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(clip.get(key), dict):
            merged = dict(clip[key])
            merged.update(value)
            clip[key] = merged
        else:
            clip[key] = value
    return clip


def doc(name, *tracks):
    """A serializer-shaped document.

    Each track is `(type, index, track_name, [clips])`.
    """
    return {
        "metadata": {"name": name},
        "tracks": [{"type": kind, "index": index, "name": track_name,
                    "clips": list(clips)}
                   for kind, index, track_name, clips in tracks],
    }


def one_reel(name, *clips):
    return doc(name, ("video", 1, "V1", list(clips)))


# ── The pure diff: agreements ───────────────────────────────────

def test_identical_documents_and_read_noise_hold_what_the_commit_recorded():
    """Resolve read-back noise (transform_drift.UNMOVED = 1e-3) is not a
    hand edit. Remove the tolerance and every reel diffs itself."""
    record = one_reel("Reel 26", make_clip("a.MXF", 100),
                      make_clip("b.MXF", 200))
    live = one_reel("Reel 26", make_clip("a.MXF", 100),
                    make_clip("b.MXF", 200,
                              transform={"Pan": 0.0005, "Tilt": 0.2505}))
    per = rd.diff_record_vs_live(record, live, reel="Reel 26")
    assert per["compared"] is True
    assert per["counts"] == {"agreements": 2, "conflicts": 0,
                             "undetermined": 0}
    assert "hold exactly what the commit recorded" in per["line"]


# ── The pure diff: conflicts ────────────────────────────────────

def test_each_kind_of_change_is_one_conflict_that_names_itself():
    """A move is one conflict naming both spans (not removed-plus-added,
    which would double the triage); a reframe names the axis; an added
    clip is unclaimed; a whole new row is ONE finding, not one per clip."""
    base = make_clip("LC4930.MXF", 590, 1069)
    rows = [
        (one_reel("Reel 13", base),
         one_reel("Reel 13", make_clip("LC4930.MXF", 600, 1079)),
         0, ["moved", "@590..1069", "@600..1079"]),
        (one_reel("Reel 13", base),
         one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069,
                                       transform={"Pan": -24.0})),
         0, ["transform.Pan 0 -> -24"]),
        (one_reel("Reel 13", base),
         one_reel("Reel 13", base, make_clip("insert.MXF", 2000)),
         1, ["unclaimed by it", "insert.MXF"]),
        (one_reel("Reel 13", base),
         doc("Reel 13", ("video", 1, "V1", [base]),
             ("video", 4, "Captions", [make_clip("cap.mov", 100),
                                       make_clip("cap2.mov", 200)])),
         1, ["Captions", "2 clip(s)"]),
    ]
    for record, live, agreements, needles in rows:
        per = rd.diff_record_vs_live(record, live, reel="Reel 13")
        assert per["counts"] == {"agreements": agreements, "conflicts": 1,
                                 "undetermined": 0}, needles
        detail = per["conflicts"][0]["detail"]
        for needle in needles:
            assert needle in detail


# ── The pure diff: undetermined is a first-class answer ─────────

def test_an_unreadable_record_is_undetermined_never_raised():
    per = rd.diff_record_vs_live({"metadata": {}}, one_reel("R",),
                                 reel="Reel 13")
    assert per["compared"] is False
    assert per["counts"]["undetermined"] == 1
    assert "committed record cannot be read" in per["line"]


# ── The git half: the record is HEAD, never the worktree ───────

def _git_3(project, *args):
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True, encoding="utf-8", check=False, timeout=120)
    assert result.returncode == 0, result.stderr
    return result


def _project_with_commits(tmp_path):
    """A project repo: Reel 26 committed, Reel 13 committed then
    hand-trimmed on disk, Reel 09 never committed."""
    root = tmp_path / "project"
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "Reel_26.timeline.json").write_text(
        json.dumps(one_reel("Reel 26", make_clip("a.MXF", 100))),
        encoding="utf-8")
    (review / "Reel_13.timeline.json").write_text(
        json.dumps(one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069))),
        encoding="utf-8")
    _git_3(root, "init")
    _git_3(root, "config", "user.name", "test")
    _git_3(root, "config", "user.email", "test@localhost")
    _git_3(root, "add", "-A")
    _git_3(root, "commit", "-m", "round: promote Reel 26, Reel 13")
    # A hand trim after the commit: live on disk, not in HEAD.
    (review / "Reel_13.timeline.json").write_text(
        json.dumps(one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1100))),
        encoding="utf-8")
    (review / "Reel_09.timeline.json").write_text(
        json.dumps(one_reel("Reel 09", make_clip("x.MXF", 10))),
        encoding="utf-8")
    return str(root)


# ── The set: cross-reel summary, never refusing ────────────────

def test_the_set_reports_changed_unchanged_undetermined(tmp_path):
    """Reel 13 was hand-trimmed after the commit: it reads changed only
    because the record is HEAD, never the worktree file."""
    project = _project_with_commits(tmp_path)
    assert rd.committed_document(project, "Reel 13")["document"][
        "tracks"][0]["clips"][0]["record_out"] == 1069
    assert "no committed snapshot" in rd.committed_document(
        project, "Reel 09")["reason"]
    report = rd.diff_records(project, ["Reel 26", "Reel 13", "Reel 09"])
    assert report["changed"] == ["Reel 13"]
    assert report["unchanged"] == ["Reel 26"]
    assert report["undetermined_reels"] == ["Reel 09"]
    assert report["totals"]["conflicts"] == 1
    assert report["totals"]["agreements"] == 1


def test_a_live_doc_override_beats_the_worktree(tmp_path):
    """A fresher live read (fixture, recorded projection, later a
    Resolve read) replaces the worktree file as the live side."""
    project = _project_with_commits(tmp_path)
    live = one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069),
                    make_clip("insert.MXF", 1200))
    report = rd.diff_records(project, ["Reel 13"],
                             live_by_reel={"Reel 13": live})
    per = report["reels"]["Reel 13"]
    assert per["counts"]["conflicts"] == 1
    assert "insert.MXF" in per["conflicts"][0]["detail"]


# ── The render: summary first, capped detail ───────────────────

def _big_live(name, count):
    """The committed Reel 13 clip untouched, plus `count` added ones -
    so the diff is exactly `count` conflicts against one agreement."""
    clips = [make_clip(f"added-{index:02d}.MXF", 2000 + 100 * index)
             for index in range(count)]
    return one_reel(name, make_clip("LC4930.MXF", 590, 1069), *clips)


def test_the_render_leads_with_the_triage_and_caps_the_wall(tmp_path):
    """39 rows must not bury the summary: the first line says who
    changed, and the detail stops at the limit with an overflow line."""
    project = _project_with_commits(tmp_path)
    live = _big_live("Reel 13", 39)
    report = rd.diff_records(project, ["Reel 26", "Reel 13"],
                             live_by_reel={"Reel 13": live})
    text = rd.render_record_diff(report)
    first = text.splitlines()[0]
    assert "1 changed, 1 unchanged, 0 undetermined" in first
    assert "changed: Reel 13 (39)" in text
    assert "unchanged: Reel 26" in text
    assert text.count("CONFLICT") == rd.RECORD_DETAIL_LIMIT
    assert "... and 29 more" in text


# ── The CLI: a set in, a printed diff out ──────────────────────

def test_cli_record_diff_json_is_a_set_in_and_json_out(tmp_path, capsys):
    project = _project_with_commits(tmp_path)
    assert rd.main([project, "--record-diff", "--reel", "Reel 26",
                    "--reel", "Reel 13", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["changed"] == ["Reel 13"]
    assert report["unchanged"] == ["Reel 26"]
