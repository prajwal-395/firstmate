"""The brief travels as a reference the model can follow, not as a copy.

The tests FOLLOW the reference (path and line range parsed out of it, the
printed command run) - reachability is the property. History:
docs/evidence/brief_reference.md.
"""
import json
import subprocess
import sys
from pathlib import Path
import pytest
import os
import hashlib
from library.tools import brief_snapshot
import re
from library.tools.brief_reference import REFERENCED_INPUTS, reference_path
from library.tools.context_projector import project_fields
from library.tools.reel_diagnostics_reference import FIELDS
import stat
from library.tools import decided_value as dv
from library.tools import taste_profile as tp


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.tools import model_task  # noqa: E402
from library.tools.brief_reference import (  # noqa: E402
    INLINE_WHEN_UNDER_BYTES,
    build_reference,
    is_inline,
    parse_sections,
    project_pinned_sections,
    project_series_identity,
    restore_for_harness,
)

# A document shaped like the captain's: a preamble, a section far too big
# to inline, and a section small enough that quoting it costs nothing.
BIG_SECTION_BODY = "Music is company, never encouragement. " * 60
DOCUMENT = (
    "# Channel Spec\n\n"
    "Master reference for the channel.\n\n"
    "## Music & Sound Philosophy\n\n"
    f"{BIG_SECTION_BODY}\n\n"
    "### Sound Design Direction\n\n"
    "UNIQUE_DEEP_SENTENCE_7c21 silence is a tool.\n\n"
    "## Volume Targets\n\n"
    "Thirty videos a month.\n"
)


def _reference(tmp_path, **kw):
    brief = tmp_path / "brief.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    return brief, build_reference(str(brief), DOCUMENT, **kw)


# ── The rule ────────────────────────────────────────────────────────


def test_the_preamble_and_a_section_under_the_threshold_are_inline(tmp_path):
    """Clause 1: the document's own statement of what it is; clause 2: a
    section quoting which costs about what describing it costs."""
    _, sections = parse_sections(DOCUMENT)
    small = [s for s in sections if s.title == "Volume Targets"][0]
    assert small.body_bytes < INLINE_WHEN_UNDER_BYTES
    inline, reason = is_inline(small, set())
    assert inline and str(INLINE_WHEN_UNDER_BYTES) in reason

    _, ref = _reference(tmp_path)
    assert "Master reference for the channel." in ref
    assert "Thirty videos a month." in ref


def test_a_section_over_the_threshold_is_a_map_entry_and_not_a_copy(tmp_path):
    """Clause 4: heading, size, line range and lede - not the body."""
    _, ref = _reference(tmp_path)
    assert "## Music & Sound Philosophy" in ref
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in ref, (
        "the deep section's body reached the prompt; the map is a map")
    assert "- Sound Design Direction" in ref, (
        "the map has to name the sub-headings or the model cannot tell "
        "whether the section holds what it needs")


def test_the_project_pins_sections_and_nothing_is_pinned_by_default(tmp_path):
    """Clause 3: the judgement belongs to whoever owns the video, so there
    is no default list - that would be the engine deciding which parts of
    the captain's document are about the captain's video."""
    _, ref = _reference(tmp_path, pinned=["music & sound philosophy"])
    assert "UNIQUE_DEEP_SENTENCE_7c21" in ref
    assert "pinned by the project" in ref

    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text('name: "T"\nslug: "t"\n',
                                          encoding="utf-8")
    assert project_pinned_sections(str(project)) == []


# ── Reachability: the property the whole change rests on ────────────

def test_following_the_reference_reaches_a_section_it_was_not_handed(tmp_path):
    """The proof.

    Everything used here is parsed out of the reference string itself -
    the path off the `FILE:` line, the line range off the heading - and
    the command is the one the reference tells the model to run.  Nothing
    is smuggled in from the test.
    """
    brief, ref = _reference(tmp_path)

    path = reference_path(ref)
    assert Path(path).is_absolute() or Path(path).exists()

    import re
    m = re.search(r"## Music & Sound Philosophy\s+\[[\d,]+ B, "
                  r"lines (\d+)-(\d+)\]", ref)
    assert m, "the map must carry a line range or the path is not followable"
    start, end = m.groups()

    body = subprocess.run(["sed", "-n", f"{start},{end}p", path],
                          capture_output=True, encoding="utf-8").stdout

    assert "UNIQUE_DEEP_SENTENCE_7c21" in body, (
        "the line range in the map does not hold the section it names")
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in ref, (
        "this only proves anything if the sentence was NOT handed over")


def test_the_example_command_the_reference_prints_actually_runs(tmp_path):
    """A path with an apostrophe in it is the captain's real case:
    `series portfolio '26 planning`.  A command the model cannot paste is
    not an affordance."""
    planning = tmp_path / "series portfolio '26 planning"
    planning.mkdir()
    brief = planning / "brand.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    ref = build_reference(str(brief), DOCUMENT)

    command = [l.strip() for l in ref.split("\n") if "sed -n" in l][0]
    out = subprocess.run(["bash", "-c", command],
                         capture_output=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "the example command returned nothing"


# ── #258: which series this video belongs to ─────────────────────────
#
# The captain's channel brief names eight series, and the blind A/B on
# 001 showed the model reaching for one of them by name - "Through the
# 4th Wall", precisely the series 001 is not.  Nothing in the run said
# which series the video is in, so the roster read as a choice.  The
# reference header now states the project's membership - or, when the
# project names none, says that absence out loud.


def test_documents_that_are_not_the_brief_carry_no_membership_line(tmp_path):
    """The anchor is the brief's, not the mechanism's.  The SFX
    catalogue and the footage analysis name no series, so a reference
    built without an identity carries no line either way."""
    _, ref = _reference(tmp_path)
    assert "belongs to the series" not in ref
    assert "names no series" not in ref


def _project_with(tmp_path, declaration: str):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "project.yaml").write_text(declaration, encoding="utf-8")
    return project


def test_a_malformed_series_declaration_raises(tmp_path):
    project = _project_with(
        tmp_path,
        'name: "T"\nslug: "t"\npipeline:\n  series:\n    - "a"\n')
    with pytest.raises(ValueError, match="series"):
        project_series_identity(str(project))


def test_the_agent_restore_states_the_series_ahead_of_the_document(tmp_path):
    """The agent harness follows a path, so the restore in
    `present_llm_step` files the reference - never a copy of the whole
    document - with the membership line stated in its header."""
    from unittest.mock import patch

    from library.processes.edit_video import run_pipeline

    brief = tmp_path / "brief.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    ref = build_reference(str(brief), DOCUMENT,
                          series_identity="Through the 4th Wall")

    project = _project_with(
        tmp_path, 'name: "T"\nslug: "t"\nseries: "Through the 4th Wall"\n')
    prompt = tmp_path / "handoff.md"
    prompt.write_text("Do the work.\n", encoding="utf-8")

    with patch.object(model_task, "_agent_sleep"), patch.object(
            model_task, "_agent_clock",
            side_effect=[0, 0, 10, 10, 10, 10]):
        with pytest.raises(run_pipeline.LLMError, match="Timeout"):
            run_pipeline.present_llm_step(
                str(prompt),
                {"creative_brief": ref, "project_folder": str(project)},
                "plan_vfx",
                manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                        "outputs": [{"name": "vfx_plan"}]}},
                full_auto="agent", llm_timeout=1)

    req = json.loads((project / "pipeline_output" / "llm_requests"
                      / "plan_vfx.json").read_text(encoding="utf-8"))
    assert ("This video belongs to the series 'Through the 4th Wall'."
            in req["context"]), (
        "the membership line travels in the reference header, ahead of "
        "the roster the model reads from the file")
    assert "NOT copied into this prompt" in req["context"]
    assert str(brief) in req["context"]
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in req["context"], (
        "the whole document must not travel when the harness can read "
        "the file - that copy was the 37%-84% bloat this module removed")


def test_the_api_restore_states_the_absence_for_a_project_naming_none(tmp_path):
    """The 001 case under `api`: no series declared, full roster
    travelling, and the absence said out loud ahead of it."""
    brief = tmp_path / "brief.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    ref = build_reference(str(brief), DOCUMENT, series_identity="")

    project = _project_with(tmp_path, 'name: "T"\nslug: "t"\n')
    inputs, restored = restore_for_harness(
        {"creative_brief": ref, "project_folder": str(project)}, "api")

    assert restored == ["creative_brief"]
    assert inputs["creative_brief"].startswith(
        "The project names no series for this video.")
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in ref
    assert "UNIQUE_DEEP_SENTENCE_7c21" in inputs["creative_brief"]


# ── The declaration round-trips through the project config ────────────
#
# `manage_project.py new` writes `project.yaml` off
# `project_config_to_dict`, so a series the schema cannot carry is a
# series the next rewrite silently drops.


def test_a_top_level_series_is_kept_not_dropped(tmp_path):
    """The runtime reads `series` at the top level or under `pipeline:`,
    so the schema must honour a top-level declaration rather than lose
    it on the next rewrite."""
    from library.schemas.project_config import (
        load_project_config, project_config_to_dict,
    )
    project = _project_with(
        tmp_path, 'name: "T"\nslug: "t"\nseries: "Night Owls"\n')

    config = load_project_config(project / "project.yaml")
    assert config.pipeline.series == "Night Owls"
    assert (project_config_to_dict(config)["pipeline"]["series"]
            == "Night Owls")


# --------------------------------------------------------------------------
# From test_brief_attachment.py
#
# Attaching a creative brief is a choice, and every reading of it is stated.
#
# The captain, 2026-09-02: *"this should be like an optional attachment we
# can add as context if we want, not something that automatically goes in"*
# and *"it should be optionally configurable by the user whether we are
# even adding a creative brief or not"*.
#
# The two failure modes this sits between, and both are silences:
#
# * **Attaching automatically.** A declared path went into eight prompts
#   on every run, with no way to say "not this time".
# * **Reading an absent key as a decline.** Every project that already
#   declares a brief would lose it on its next run, silently - the same
#   defect arriving from the other direction.
#
# So the key is a THREE-state declaration and the absent state is "the
# PATH is the declaration", said out loud in the run header.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import brief_attachment as ba


def read(**yaml_body):
    return ba.read_declaration_from(yaml_body, where="project.yaml")


# ── The three readings ───────────────────────────────────────────────

def test_a_declared_path_and_no_key_is_read_as_attaching_it():
    """The compatibility case, and it is a READING, not a default.

    Project 001 declares `creative_brief` at the top level and no
    attach key. Reading that as a decline would take the captain's own
    brief off the run that most depends on it.
    """
    got = read(creative_brief="brief.md")
    assert got.reading == ba.ATTACHED
    assert got.attached
    assert not got.interview
    assert "read as the choice to attach" in got.basis


def test_declining_is_one_line_and_wins_over_a_declared_path():
    got = read(creative_brief="brief.md", attach_creative_brief=False)
    assert got.reading == ba.DECLINED
    assert not got.attached
    assert got.interview
    assert got.path == "brief.md", (
        "the path is still recorded - the project has a brief and chose "
        "not to send it, which is a different fact from having none")


# ── The one refusal ──────────────────────────────────────────────────

def test_an_unsatisfiable_or_unreadable_declaration_is_refused():
    """Attaching a brief that does not exist is refused by name; an
    unreadable value is refused rather than guessed - guessing is the
    difference between the brief reaching every planning step and none."""
    with pytest.raises(ba.BriefAttachmentError) as excinfo:
        read(attach_creative_brief=True)
    assert "creative_brief" in str(excinfo.value)
    with pytest.raises(ba.BriefAttachmentError):
        read(attach_creative_brief="maybe")


# ── It says so, whichever way it read ────────────────────────────────

def test_every_reading_prints_a_line_naming_itself():
    """The shape `describe_brand_absence` established: an absence stated
    once per run is a decision a reader can see."""
    for attachment in (read(creative_brief="b.md"),
                       read(creative_brief="b.md", attach_creative_brief=False),
                       read()):
        line = ba.describe(attachment)
        assert line
        assert "Creative brief:" in line
    assert "ATTACHED" in ba.describe(read(creative_brief="b.md"))
    assert "DECLINED" in ba.describe(
        read(creative_brief="b.md", attach_creative_brief=False))
    assert "NONE DECLARED" in ba.describe(read())


# --------------------------------------------------------------------------
# From test_brief_snapshot.py
#
# The creative brief has a versioned home (AGENTS.md 3, 10.1).
#
# A brief declared outside the project is snapshotted verbatim under
# `pipeline_output/provenance/`, so "what brief did this build read" has a
# versioned answer (`read_snapshot`); a run that reads none clears it.

BRIEF_TEXT = """# Channel brief

## Voice

We whisper, never shout.

## Colour

Warm highlights, honest shadows.
"""


def _outside_brief(tmp_path, text=BRIEF_TEXT):
    """A brief that lives OUTSIDE the project, like the captain's own."""
    planning = tmp_path / "planning-tree"
    planning.mkdir()
    src = planning / "creative_brief.md"
    src.write_text(text, encoding="utf-8")
    return src


def test_snapshot_records_what_the_build_read(tmp_path):
    """The fix: verbatim bytes plus the binding, at a fixed home."""
    src = _outside_brief(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    report = brief_snapshot.record_brief_for_run(str(project), str(src))

    assert report["snapshotted"] is True
    assert report["source"] == str(src)
    assert report["sha256"] == hashlib.sha256(
        BRIEF_TEXT.encode("utf-8")).hexdigest()

    md_rel, json_rel = brief_snapshot.snapshot_relpaths()
    assert (project / md_rel).read_text(encoding="utf-8") == BRIEF_TEXT
    record = json.loads((project / json_rel).read_text(encoding="utf-8"))
    assert record["source"] == str(src)
    assert record["sha256"] == report["sha256"]
    assert record["bytes"] == len(BRIEF_TEXT.encode("utf-8"))
    assert record["lines"] == BRIEF_TEXT.count("\n") + 1
    assert record["recorded_at"]

    # The lookup answers what the build read, byte for byte.
    found = brief_snapshot.read_snapshot(str(project))
    assert found is not None
    assert found["content"] == BRIEF_TEXT
    assert found["record"]["sha256"] == report["sha256"]


def test_relative_declaration_resolves_against_the_project(tmp_path):
    """A project-relative brief resolves the way the runner reads it."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "creative_brief.md").write_text(BRIEF_TEXT, encoding="utf-8")

    report = brief_snapshot.record_brief_for_run(
        str(project), "creative_brief.md")

    assert report["snapshotted"] is True
    found = brief_snapshot.read_snapshot(str(project))
    assert found is not None
    assert found["content"] == BRIEF_TEXT


def test_edited_brief_supersedes_and_stays_distinct(tmp_path):
    """A later edit to the outside file lands as a new snapshot version."""
    src = _outside_brief(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    first = brief_snapshot.record_brief_for_run(str(project), str(src))
    src.write_text(BRIEF_TEXT + "\n## New rule\n\nShout once.\n",
                   encoding="utf-8")
    second = brief_snapshot.record_brief_for_run(str(project), str(src))

    assert second["snapshotted"] is True
    assert second.get("unchanged") is not True
    assert second["sha256"] != first["sha256"]
    assert brief_snapshot.read_snapshot(str(project))["content"] == (
        src.read_text(encoding="utf-8"))


def test_no_brief_clears_a_stale_snapshot(tmp_path):
    """A run that reads no brief must not keep claiming yesterday's."""
    src = _outside_brief(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    assert brief_snapshot.record_brief_for_run(
        str(project), str(src))["snapshotted"] is True

    # The next run declines the brief: load_pipeline_state leaves
    # state["creative_brief"] unset, so the hook passes "".
    report = brief_snapshot.record_brief_for_run(str(project), "")

    assert report["snapshotted"] is False
    assert sorted(report["cleared"]) == sorted(
        [p.split("/")[-1] for p in brief_snapshot.snapshot_relpaths()])
    assert brief_snapshot.read_snapshot(str(project)) is None
    md_rel, json_rel = brief_snapshot.snapshot_relpaths()
    assert not (project / md_rel).exists()
    assert not (project / json_rel).exists()


# --------------------------------------------------------------------------
# From test_briefing_interview.py
#
# A step with no creative brief ASKS, rather than planning in silence.
#
# The captain, 2026-09-02: a declined brief should prompt the LLM to ask
# briefing questions. Membership is derived from the manifests
# (`briefing_interview._steps_declaring_the_brief`); the collector keeps one
# record per attempt and the final one per step is the reading.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import briefing_interview as bi  # noqa: E402


def test_select_reels_is_interviewed_without_a_brief():
    """The reel selector ASKS rather than selecting in silence.

    This landed once (89c61e6, the field-test lane) and was lost when
    that lane was never merged, so main shipped a reel selector that
    received no brief and was never asked what it would have wanted told.
    Nineteen reels were approved off a run that HAD the brief, from a
    worktree, which is why nothing downstream noticed.
    """
    assert bi.asks("select_reels", brief_attached=False) is True
    assert bi.asks("select_reels", brief_attached=True) is False


# ── The collector's two rules, shared with both siblings ─────────────

def test_one_record_per_attempt_numbered_and_final_by_step_is_the_reading():
    bi.reset()
    for i in range(3):
        bi.record(bi.Interview("mesh_spine", bi.ASKED,
                               [{"question": f"q{i}"}]))
    assert [i.attempt for i in bi.collected()] == [1, 2, 3]
    final = bi.final_by_step()
    assert len(final) == 1 and final[0].attempt == 3
    assert "3 attempts" in "\n".join(bi.summary_lines())


def test_a_step_this_run_did_not_reach_keeps_its_rows_and_is_marked():
    previous = [{"step_id": "plan_sfx", "reading": bi.ASKED, "entries": [],
                 "attempt": 1},
                {"step_id": "plan_vfx", "reading": bi.ASKED, "entries": [],
                 "attempt": 1}]
    current = [{"step_id": "plan_vfx", "reading": bi.NOTHING_TO_ASK,
                "entries": [], "attempt": 1}]
    merged = bi.merge_records(previous, current)
    by_step = {row["step_id"]: row for row in merged}
    assert by_step["plan_sfx"]["from_a_previous_run"] is True
    assert "from_a_previous_run" not in by_step["plan_vfx"]
    assert by_step["plan_vfx"]["reading"] == bi.NOTHING_TO_ASK


# ── The two modules agree about what "not attached" means ────────────

def test_every_not_attached_reading_leads_to_an_interview():
    for body in ({}, {"attach_creative_brief": False,
                      "creative_brief": "b.md"}):
        attachment = ba.read_declaration_from(body)
        assert attachment.interview
        assert bi.asks("plan_vfx", attachment.attached)
    attached = ba.read_declaration_from({"creative_brief": "b.md"})
    assert not attached.interview
    assert not bi.asks("plan_vfx", attached.attached)


# --------------------------------------------------------------------------
# From test_broll_context_share.py
#
# Step 3.02 must not carry three views of one vision analysis again.
#
# Measured on 001's frozen snapshot (`001-degradation-20260828`) with the
# step-replay bench, at 75d3e84, over a context of 88,475 B:
#
#     semantic_analysis_documents   35,813 B   40.5%
#     view:picture                  10,250 B   11.6%
#     broll_candidates_toon          7,613 B    8.6%
#     ---------------------------------------------
#     three views of one analysis   53,676 B   60.7%
#
# All three are readings of the SAME per-clip vision documents, and the
# share had grown rather than shrunk: #295 stopped copying the captain's
# brief, so the denominator fell faster than the duplication.  This is the
# step whose cutaway choices the captain complained about.
#
# After the change the raw structure travels as a REFERENCE
# (`library/tools/footage_reference.py`), through the mechanism #295 built
# for the brief and #299 applied to the SFX catalogue:
#
#     view:picture                  10,250 B   17.9%
#     broll_candidates_toon          7,613 B   13.3%
#     footage_analysis_reference     4,507 B    7.9%
#     ---------------------------------------------
#                                   22,370 B   39.1%   of 57,169 B
#
# With #340's frame strips also in the context: 94,994 B -> 63,688 B, and
# the three readings 56.5% -> 35.1%.  The fixture here draws no strips (its
# clip paths do not exist), so the ratio below is measured on the analysis
# alone, which is what it is about.
#
# ## What this file guards, and why in that shape
#
# A share measured against a synthetic fixture would only ever be as
# honest as the fixture, so the load-bearing assertion here is a RATIO
# between two things that scale together: what the prompt spends on
# readings of the analysis, against what the analysis itself would cost
# carried inline.  On 001 that ratio was **1.499** before and **0.625**
# after, and a re-added `semantic_analysis_documents.*.objects` alone -
# 62.8% of the structure's cells - puts it back over 1.
#
# The structural half is the thing that actually regrew: a positive
# `context_fields` path back into the raw documents.
#
# Nothing here reaches a real project (AGENTS.md 8): the fixture is built
# under `tmp_path`, shaped like 001 - seventeen clips, one scene segment
# and one camera segment each, and objects at 001's own density.

REPO = Path(__file__).resolve().parents[3]
BROLL_STEP = REPO / "library" / "steps" / "step_3_02_select_broll"

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    project_step_context,
)
from library.tools.footage_reference import DOCUMENT_NAME  # noqa: E402
from library.tools.toon_serializer import json_to_toon  # noqa: E402

# What the prompt may spend on READINGS of the vision analysis, as a
# multiple of what that analysis costs carried inline. 001 measured
# 1.499 before the change and 0.625 after; the ceiling leaves room for
# the map to grow with the catalogue and none for a fourth view.
READINGS_BUDGET = 0.80

# 001's own shape. Seventeen clips; one scene segment and one camera
# segment on all but one of them; 159 objects over the seventeen.
CLIPS = 17
OBJECTS_PER_CLIP = 9
ACTION_WINDOWS_PER_CLIP = 5


def _document(n: int) -> dict:
    """One v3 vision document, at 001's measured density."""
    stem = f"IMG_{1800 + n}"
    return {
        "clip_id": f"{stem}_v3",
        "file_path": f"/footage/{stem}.MOV",
        "duration_s": 45.0,
        "vision_schema_version": "3.0",
        "scene": [{
            "start": 0.0, "end": 18.9,
            "location": f"Outdoor urban area {n} with a parking lot",
            "type": "outdoor", "lighting": "Overcast daylight",
            "notable_features": ["Parked cars", "Concrete sidewalk",
                                 "Metal railing"],
        }],
        "camera": [{
            "start": 0, "end": 45, "mode": "handheld", "framing": "wide",
            "stability": "stable", "movement": "stationary",
        }],
        # As step 1.03 stores them: the v3 `actions[]` windows with
        # `vision_schema_adapter`'s `blocks` view already applied.
        "actions": [{
            "window": [w * 9.0, (w + 1) * 9.0],
            "actions": [{
                "start": w * 9.0, "end": (w + 1) * 9.0,
                "action": f"Clip {n} window {w}: the speaker walks past a "
                          f"row of parked cars and gestures at the building.",
                "speech_cue": None, "body_language": None,
            }],
        } for w in range(ACTION_WINDOWS_PER_CLIP)],
        "blocks": [{
            "timestamp_range": f"0:{w * 9:02d}-0:{(w + 1) * 9:02d}",
            "start": w * 9.0, "end": (w + 1) * 9.0,
            "label": f"Outdoor urban area {n}",
            "visual": f"Clip {n} window {w}: the speaker walks past a row "
                      f"of parked cars and gestures at the building.",
            "body_language": "Relaxed posture, open gestures throughout.",
            "speech_cue": None,
        } for w in range(ACTION_WINDOWS_PER_CLIP)],
        "objects": [{
            "label": f"object {o} in clip {n}, described at length",
            "appearances": [[0.0, 44.5]],
            "role": "background", "category": "structure",
            "readable_text": None,
        } for o in range(OBJECTS_PER_CLIP)],
        "assessment": {
            "content_type": "scenery" if n % 3 else "person_talking_to_camera",
            "clip_type": "b_roll",
            "keywords": ["outdoor", "scenery", "stationary", "wide"],
            "interest_score": 0.5,
            "camera_stability": "unknown",
            "usable_ranges": [[0, 45.0]],
            "usable_ranges_method": "unmeasured",
            "primary_subject_visible": [],
        },
    }


DOCUMENTS = [_document(n) for n in range(CLIPS)]
CATALOG = [{"clip_id": f"clip_{n:03d}", "filename": f"IMG_{1800 + n}.MOV",
            "path": f"/footage/IMG_{1800 + n}.MOV",
            "duration_seconds": 45.0, "width": 1920, "height": 1080,
            "rotation": 0, "frame_rate": 30.0}
           for n in range(CLIPS)]
A_ROLL = [{"segment_id": "block_1", "spine_block_position": 1,
           "video_segments": [{"clip_id": "clip_003"}]}]
SPINE = {"structure": [
    {"position": p, "block_type": "speech", "clip_id": "clip_003",
     "content": f"Spoken line {p} of the edit, as the spine records it.",
     "timeline_start": p * 5.0, "timeline_end": (p + 1) * 5.0,
     "visual_note": "the speaker, mid-sentence"}
    for p in range(11)]}


def _routed_inputs(project: Path) -> dict:
    """What the DAG routes 3.02, before the pre-bridge and the projection."""
    return {
        "project_folder": str(project),
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": DOCUMENTS,
        "temporal_event_indices": [{"clip_id": c["clip_id"],
                                    "scene_boundaries": [0.0, 22.5]}
                                   for c in CATALOG],
        "timed_spine": SPINE,
        "creative_direction": {"target_mood": "reflective",
                               "energy_arc": "building"},
    }


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def _bridge(project: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(BROLL_STEP / "bridge.py")],
        input=json.dumps(_routed_inputs(project)),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _manifest() -> dict:
    with open(BROLL_STEP / "manifest.json", encoding="utf-8") as fh:
        return json.load(fh)


def _sections(project: Path) -> dict:
    """The prompt's context, split by the top-level key each part came from.

    The runner's OWN assembly: the real pre-bridge, the real projection
    and the real serializer, so this measures the prompt rather than a
    model of it.
    """
    pre = _bridge(project)
    inputs = dict(_routed_inputs(project))
    inputs.update(pre)
    projected = project_step_context(inputs, _manifest(), set(pre))
    context = json_to_toon(projected)

    out, current, buf = {}, None, []
    for line in context.split("\n"):
        if line and not line[0].isspace() and (":" in line or "[" in line):
            key = line.split(":")[0].split("[")[0].strip()
            if key and key.replace("_", "").isalnum():
                if current is not None:
                    out[current] = "\n".join(buf)
                current, buf = key, [line]
                continue
        buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


# ── The structural half: no path back into the raw documents ──────────

def test_the_prompt_declares_no_path_into_the_raw_vision_documents():
    """The 40.5% section, and the thing that would regrow it.

    A `-` drop path is not a reading, so only POSITIVE paths count.
    """
    declared = [p for p in _manifest()["context_fields"]
                if not p.startswith("-")]
    offenders = [p for p in declared
                 if p.split(".")[0] in ("semantic_analysis_documents",
                                        "semantic_analysis")]
    assert not offenders, (
        f"step 3.02 reads the raw vision documents in its prompt again "
        f"({offenders}). It already ships two renderings of them - "
        f"`broll_candidates_toon` and `view:picture` - and the structure "
        f"itself is at the path in `footage_analysis_reference`.")


# ── The measured half: what the prompt spends on the analysis ─────────

# Every section of 3.02's prompt that is a reading of the ONE vision
# analysis. The raw keys are in here so that re-declaring one is counted
# against the budget rather than slipping past a list of the three that
# happen to be there today.
# The paths 3.02 used to declare into the raw documents. The denominator
# of the ratio below is what THEY cost, because that is what the prompt
# really carried - not what the whole stored document weighs.
WITHDRAWN_PATHS = [
    "semantic_analysis_documents.*.clip_id",
    "semantic_analysis_documents.*.duration_s",
    "semantic_analysis_documents.*.assessment.interest_score",
    "semantic_analysis_documents.*.assessment.keywords",
    "semantic_analysis_documents.*.assessment.clip_type",
    "semantic_analysis_documents.*.scene",
    "semantic_analysis_documents.*.camera",
    "semantic_analysis_documents.*.objects",
    "semantic_analysis_documents.*.assessment.content_type",
    "semantic_analysis_documents.*.assessment.camera_stability",
    "semantic_analysis_documents.*.assessment.usable_ranges",
    "semantic_analysis_documents.*.assessment.usable_ranges_method",
    "semantic_analysis_documents.*.assessment.primary_subject_visible",
]

# `broll_window_frames` (#340) is deliberately NOT here. A frame strip is
# a picture of the footage, not a reading of the analysis document, and
# it is the thing this collapse was making room for - counting it against
# the budget would charge the frames for the duplication they exposed.
READINGS_OF_THE_ANALYSIS = (
    "broll_candidates_toon", "picture", "footage_analysis_reference",
    "semantic_analysis_documents", "semantic_analysis",
)


# ── Nothing the choice needs became unreachable ───────────────────────

# What the model is told to decide on, and where each of them now is.
# `cutaway_window.choose_window` matches the answer's `preferred_moment`
# against `blocks[].visual`, so the observed-action rows are the one
# reading the ANSWER is resolved against and they stay in the prompt.
IN_THE_PROMPT = {
    "content_type": "broll_candidates_toon",
    "usable_range": "broll_candidates_toon",
    "framing": "broll_candidates_toon",
    "stability": "broll_candidates_toon",
    "camera_move": "broll_candidates_toon",
    "subjects": "broll_candidates_toon",
    "description": "broll_candidates_toon",
    "used_as_aroll": "broll_candidates_toon",
    "duration_s": "broll_candidates_toon",
}

# What only the raw structure ever carried. Each must come back from
# FOLLOWING the reference, and must not be in the prompt.
ONLY_AT_THE_PATH = (
    # `camera[]`'s per-segment time bounds - the table dedupes them away.
    "mode: handheld",
    # every object, not the four the `subjects` column keeps.
    f"object {OBJECTS_PER_CLIP - 1} in clip 0",
    # the assessment fields the table has no column for.
    "usable_ranges_method",
    "interest_score",
    # `scene[]` as fields rather than as one prose line.
    "notable_features",
)


def test_the_answer_is_still_resolved_against_something_it_can_see(project):
    """`choose_window` matches `preferred_moment` against the action rows."""
    picture = _sections(project)["picture"]
    assert "window 0" in picture and "window 4" in picture
    assert picture.count("clip_") >= CLIPS


def test_following_the_reference_returns_what_the_prompt_left_behind(project):
    """The discipline of `tests/unit/context/test_brief.py`: FOLLOW it.

    The path is parsed out of the same string the model reads, and what
    comes back must not have been in the prompt.
    """
    sections = _sections(project)
    reference = sections["footage_analysis_reference"]
    path = reference_path(reference)
    assert path, reference[:400]
    assert Path(path).name == DOCUMENT_NAME

    document = Path(path).read_text(encoding="utf-8")
    context = "\n".join(sections.values())
    for needle in ONLY_AT_THE_PATH:
        assert needle in document, f"the document lost {needle!r}"
        assert needle not in context, (
            f"{needle!r} is in the prompt AND at the path - that is the "
            f"duplication this change removed")


def test_the_map_names_every_clip_by_the_id_an_answer_must_use(project):
    """A section titled with an id the rest of the context does not speak
    is a section the model cannot look up."""
    reference = _sections(project)["footage_analysis_reference"]
    for entry in CATALOG:
        assert f"## {entry['clip_id']}  [" in reference, entry["clip_id"]


def test_the_bridge_refuses_when_there_is_nowhere_to_write_it():
    """No project folder means no path, and no quiet copy instead."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    payload = {k: v for k, v in _routed_inputs(Path("/nowhere")).items()
               if k != "project_folder"}
    proc = subprocess.run(
        [sys.executable, str(BROLL_STEP / "bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env,
    )
    assert proc.returncode == 1
    assert "project_folder" in json.loads(proc.stdout)["error"]


# --------------------------------------------------------------------------
# From test_reel_diagnostics_by_reference.py
#
# Step 3.04's repeated-take evidence reaches the prompt as a REFERENCE.
#
# The step's `context_fields` drop `repetition_inside`, `retake_candidates`
# and `possible_retellings` from every candidate row - 19,850 of 49,777
# tokens on the geo podcast. A drop path is unconditional, so the defect
# this guards is the silent one: the fields leave the prompt and nothing
# puts them anywhere the model can reach.
#
# The test FOLLOWS the reference the way `tests/unit/context/test_brief.py`
# does: it projects the bridge's real output with the step's real
# `context_fields`, parses the path and each line range out of the string
# the model reads, and requires every dropped item to be at its own
# candidate's section, verbatim.

STEP = REPO / "library" / "steps" / "step_3_04_select_reels"

CANDIDATES = [
    {"start": 0.14, "end": 47.48, "turns": 3},
    {"start": 53.85, "end": 111.88, "turns": 5,
     "repetition_inside": [{"start": 60.0, "end": 70.0, "speaker": "A",
                            "lines": ["say it once"], "build_removes_it": True,
                            "cuts": [{"dropped_text": "say it once"}],
                            "why": "every line pairs"}],
     "retake_candidates": [{"dropped_start": 96.3, "kept_start": 104.52,
                            "dropped_sentence": "For Geo, it's été.",
                            "recommended_action": "strike 96.30-101.24s"}]},
    {"start": 248.81, "end": 299.39, "turns": 4,
     "possible_retellings": [{"between_text": "That is crazy.",
                              "why": "same speaker twice"}]},
]


def test_every_dropped_item_is_at_its_candidates_section(tmp_path):
    from library.steps.step_3_04_select_reels import bridge

    out = {"reel_candidates": json.loads(json.dumps(CANDIDATES))}
    bridge._attach_diagnostics_reference(out, {"project_folder": str(tmp_path)})
    assert "reel_diagnostics_reference" in REFERENCED_INPUTS

    manifest = json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))
    projected = project_fields(out, manifest["context_fields"])
    for row in projected["reel_candidates"]:
        assert not set(FIELDS) & set(row), row

    reference = projected["reel_diagnostics_reference"]
    lines = Path(reference_path(reference)).read_text(
        encoding="utf-8").split("\n")
    ranges = {title: (int(a), int(b)) for title, a, b in re.findall(
        r"## (\S+)  \[[\d,]+ B, lines (\d+)-(\d+)\]", reference)}
    assert set(ranges) == {"53.85-111.88", "248.81-299.39"}

    for candidate in CANDIDATES:
        for field in FIELDS:
            for item in candidate.get(field) or []:
                a, b = ranges[f"{candidate['start']}-{candidate['end']}"]
                section = "\n".join(lines[a - 1:b])
                assert json.dumps(item, ensure_ascii=False) in section


def test_no_document_and_no_reference_when_nothing_carries_any(tmp_path):
    from library.steps.step_3_04_select_reels import bridge

    out = {"reel_candidates": [dict(CANDIDATES[0])]}
    bridge._attach_diagnostics_reference(out, {"project_folder": str(tmp_path)})
    assert "reel_diagnostics_reference" not in out
    assert not list(tmp_path.rglob("*.md"))


# --------------------------------------------------------------------------
# From test_taste_profile.py

SLOT = "mix.speech_above_bed_db"
MEASURED = {"bed_integrated_lufs": -13.9, "speech_lufs": -22.4}


def _user_profile_path(tmp_path, monkeypatch):
    config = tmp_path / "user-home" / ".config" / "ren" / "config.env"
    monkeypatch.setenv("REN_CONFIG", str(config))
    return config.parent / tp.PROFILE_FILENAME


def test_one_explicit_profile_preference_reaches_a_second_project(
        tmp_path, monkeypatch):
    profile = _user_profile_path(tmp_path, monkeypatch)
    first_project = tmp_path / "first-project"
    second_project = tmp_path / "second-project"
    first_project.mkdir()
    second_project.mkdir()

    written = tp.set_preference(
        SLOT, "background", 14, stated_by="Prajwal",
        reason="I want the voice clearly above the bed", path=profile)

    assert written == profile
    assert profile.is_file()
    assert first_project not in profile.parents
    assert second_project not in profile.parents
    data = json.loads(profile.read_text(encoding="utf-8"))
    assert list(data["preferences"]) == [SLOT]
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600

    decision = dv.decide(
        SLOT, scope="background", project_folder=str(second_project),
        measurements=MEASURED,
        model_answer={"value": 6, "why": "the bed sounds soft here"})

    assert decision.basis == dv.STATED
    assert decision.answer == 14
    assert decision.value == pytest.approx((-22.4 - 14) - -13.9)
    assert str(profile) in decision.source
    assert "stated by Prajwal" in decision.source
    assert "clearly above the bed" in decision.source


def test_project_preference_overrides_the_user_profile(tmp_path, monkeypatch):
    profile = _user_profile_path(tmp_path, monkeypatch)
    tp.set_preference(
        SLOT, "background", 14, stated_by="Prajwal", reason="Across projects",
        path=profile)
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "pipeline:\n"
        "  creative_preferences:\n"
        "    mix:\n"
        "      speech_above_bed_db:\n"
        "        background: 10\n", encoding="utf-8")

    decision = dv.decide(
        SLOT, scope="background", project_folder=str(project),
        measurements=MEASURED)

    assert decision.basis == dv.STATED
    assert decision.answer == 10
    assert decision.source == (
        "pipeline.creative_preferences.mix.speech_above_bed_db.background")


def test_an_absent_profile_is_not_created_or_filled_with_a_value(
        tmp_path, monkeypatch):
    profile = _user_profile_path(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()

    decision = dv.decide(
        SLOT, scope="background", project_folder=str(project),
        measurements=MEASURED,
        model_answer={"value": 9, "why": "the bed is mastered hot"})

    assert decision.basis == dv.REASONED
    assert not profile.exists()


def test_profile_cli_records_the_person_and_reason_in_user_config_dir(
        tmp_path, monkeypatch, capsys):
    profile = _user_profile_path(tmp_path, monkeypatch)

    assert tp.main([
        "set", SLOT, "--scope", "background", "--value", "12",
        "--stated-by", "Prajwal", "--reason", "A preference I stated",
    ]) == 0

    record = json.loads(profile.read_text(encoding="utf-8"))[
        "preferences"][SLOT]["background"]
    assert record["value"] == 12
    assert record["stated_by"] == "Prajwal"
    assert record["reason"] == "A preference I stated"
    assert "Recorded stated preference" in capsys.readouterr().out


def test_profile_refuses_a_record_without_provenance(tmp_path):
    profile = tmp_path / "taste_profile.json"
    profile.write_text(json.dumps({
        "version": tp.PROFILE_VERSION,
        "preferences": {
            SLOT: {"background": {"value": 14, "stated_by": "Prajwal"}},
        },
    }), encoding="utf-8")

    with pytest.raises(tp.TasteProfileError, match="missing 'reason'"):
        tp.stated_preference(SLOT, "background", path=profile)
