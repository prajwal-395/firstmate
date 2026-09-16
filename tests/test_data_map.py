"""The map, and the gate on it, held to failing in BOTH directions.

AGENTS.md 10.4: a gate that cannot fail is worse than no gate, and one
that fails correct output is the same defect from the other side.  This
module proves both for all three checks `data_map.disagreements` makes,
and separately proves the map can still find reads that are KNOWN to
exist - because a map that cannot find a known read has not looked.

    python3 -m pytest tests/test_data_map.py -q
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import data_map, field_flow  # noqa: E402


@pytest.fixture(scope="module")
def rows():
    return data_map.survey()


# ── The gate does not fail correct output ────────────────────────────

def test_the_gate_is_clean_on_this_tree(rows):
    problems = data_map.disagreements(rows)
    assert problems == [], "\n".join(problems)


# ── The gate CAN fail, one direction at a time ───────────────────────

def _row(tag, **kwargs):
    origin, path = field_flow.split(tag)
    return data_map.FieldRow(origin=origin, path=path, **kwargs)


def test_the_gate_fails_when_an_anchor_loses_its_reader(rows):
    """The instrument going blind must be a build failure, not a
    quieter report."""
    anchor = "OUT@catalog#clip_catalog[].width"
    assert anchor in data_map.ANCHORS
    blinded = [row for row in rows if row.tag != anchor]
    problems = data_map.disagreements(blinded)
    assert any(anchor in problem and "ANCHOR" in problem
               for problem in problems), problems


def test_the_gate_fails_when_a_field_recorded_as_unread_gains_a_reader(rows):
    """`catalog.source_resolution` must stay unread - giving it the
    reader its old comment claimed would put back the grain error that
    shipped 001 as a 1920x1080 master."""
    tag = "OUT@catalog#source_resolution"
    assert tag in data_map.NOT_READ_ANCHORS
    observation = next(row.observation for row in rows if row.tag == tag)
    invented = [row for row in rows if row.tag != tag]
    invented.append(_row(tag, observation=observation, readers=(
        field_flow.Access(root="OUT@catalog", path="source_resolution",
                          module="library/tools/invented.py", line=1,
                          func="f", shape=field_flow.GET),)))
    problems = data_map.disagreements(invented)
    assert any(tag in problem for problem in problems), problems


def test_the_gate_fails_when_an_origin_carries_more_than_it_is_budgeted(rows):
    origin = "OUT@catalog"
    observation = data_map.Observation(
        origin=origin, path="invented", types=("string",), occurrences=1,
        bytes=64)
    problems = data_map.disagreements(
        list(rows) + [_row(f"{origin}#invented", observation=observation)])
    assert any(problem.startswith(f"BUDGET {origin}")
               and "budget" in problem for problem in problems), problems


def test_the_gate_fails_when_a_budget_is_larger_than_what_is_owed(rows):
    """A ratchet that may only move down has to REFUSE a stale number.
    A line no longer needed is a lie about what is still owed."""
    origin = "OUT@catalog"
    kept = [row for row in rows
            if not (row.origin == origin and row.unread and row.observed)]
    dropped = len(rows) - len(kept)
    assert dropped > 0, f"{origin} has no unread field to remove"
    problems = data_map.disagreements(kept)
    assert any(problem.startswith(f"BUDGET {origin}") and "Lower it"
               in problem for problem in problems), problems


def test_the_gate_fails_when_an_origin_has_no_budget_row(rows, monkeypatch):
    """A section with no bound is the trap - the same reasoning
    `check_agents_md_size.py` uses for a `##` heading with no budget."""
    trimmed = dict(data_map.UNREAD_BUDGET)
    trimmed.pop("OUT@catalog")
    monkeypatch.setattr(data_map, "UNREAD_BUDGET", trimmed)
    problems = data_map.disagreements(rows)
    assert any("no budget row" in problem for problem in problems), problems


def test_the_gate_fails_when_a_budgeted_origin_disappears(rows, monkeypatch):
    widened = dict(data_map.UNREAD_BUDGET)
    widened["OUT@a_step_that_does_not_exist"] = 3
    monkeypatch.setattr(data_map, "UNREAD_BUDGET", widened)
    problems = data_map.disagreements(rows)
    assert any("a_step_that_does_not_exist" in problem
               and "Delete the row" in problem
               for problem in problems), problems


def test_the_gate_fails_when_a_document_is_declared_and_never_observed(
        rows, monkeypatch):
    invented = data_map.Document(
        "an_invented_document", ("nothing_writes_this.json",),
        "A document added without a run that produces one.",
        "nobody", "state", "nowhere")
    monkeypatch.setattr(data_map, "DOCUMENTS",
                        data_map.DOCUMENTS + (invented,))
    problems = data_map.disagreements(rows)
    assert any("an_invented_document" in problem
               for problem in problems), problems


def test_the_gate_fails_when_a_recorded_absence_becomes_stale(
        rows, monkeypatch):
    """`NOT_OBSERVED` records a document no run has produced. One that
    IS produced now must delete its entry, or the record reads as
    coverage."""
    stale = dict(data_map.NOT_OBSERVED)
    stale["reel_proposals_v2"] = "invented reason"
    monkeypatch.setattr(data_map, "NOT_OBSERVED", stale)
    problems = data_map.disagreements(rows)
    assert any("reel_proposals_v2" in problem and "Delete the entry"
               in problem for problem in problems), problems


def test_the_gate_fails_when_a_recorded_hole_in_the_map_has_closed(
        rows, monkeypatch):
    """`KNOWN_MISSED_READS` is where a hand check found a reader this
    map cannot see. One that the map CAN see now is stale, and a
    recorded hole that has closed reads as one that is still open."""
    stale = dict(data_map.KNOWN_MISSED_READS)
    stale["OUT@catalog#clip_catalog[].width"] = "invented"
    monkeypatch.setattr(data_map, "KNOWN_MISSED_READS", stale)
    problems = data_map.disagreements(rows)
    assert any("MISSED" in problem and "clip_catalog[].width" in problem
               for problem in problems), problems



# ── The map still finds reads that are known to exist ────────────────

def test_every_anchor_has_a_route_and_says_where(rows):
    """The validation the brief asked for, run every time: pick fields
    you KNOW are read and confirm the map says so."""
    by_tag = {row.tag: row for row in rows}
    for tag, why in sorted(data_map.ANCHORS.items()):
        row = by_tag.get(tag)
        assert row is not None, f"{tag} is not in the map at all. {why}"
        assert row.routes, f"{tag} reports no reader. {why}"
        assert row.evidence(), f"{tag} is read and names no evidence. {why}"


def test_a_known_unread_field_is_still_unread(rows):
    by_tag = {row.tag: row for row in rows}
    for tag, why in sorted(data_map.NOT_READ_ANCHORS.items()):
        row = by_tag.get(tag)
        assert row is not None, f"{tag} is not in the map at all. {why}"
        assert not row.routes, (
            f"{tag} now reports {'/'.join(row.routes)}. {why}")


def test_the_two_contracts_agree_about_what_is_read():
    """A cross-check against a DIFFERENT instrument.

    `output_contract` works at OUTPUT level and this map at FIELD level,
    so where the older one says an output is carried by an edge, this
    one must carry at least one field under that output.  Two
    independently written surveys disagreeing is worth more than either
    of them agreeing with itself.
    """
    from library.tools import output_contract

    observed = data_map.load_observed()
    rows = data_map.survey()
    with_route = {row.tag for row in rows if row.routes}

    checked = 0
    for output in output_contract.survey():
        if output_contract.ROUTE_EDGE not in output.routes:
            continue
        origin = f"{field_flow.ROOT_OUTPUT}{output.node}"
        paths = observed.get(origin, {})
        if output.name not in paths:
            continue  # the snapshot's projects never ran this step
        checked += 1
        under = {f"{origin}{field_flow.TAG_SEPARATOR}{path}"
                 for path in paths
                 if path == output.name or path.startswith(output.name + ".")
                 or path.startswith(output.name + "[")}
        assert under & with_route, (
            f"{output.node}.{output.name} is carried by a DAG edge, and "
            f"no field under it has a reader in the field-level map. One "
            f"of the two instruments is wrong.")
    assert checked > 10, (
        f"only {checked} outputs were cross-checked; the snapshot has "
        f"stopped covering the DAG")


# ── The snapshot is a SHAPE, and carries no project content ──────────

def test_the_snapshot_carries_no_values_and_no_project_content():
    """It is committed, so it has to be safe to commit.

    Every path segment is a field name, an element marker or a
    collapsed lookup - never a clip id, a speaker's name or a file.
    """
    import re

    raw = json.loads(data_map.OBSERVED_FILE.read_text(encoding="utf-8"))
    allowed = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*|\[\]|\{\})$")
    for origin, paths in raw["shapes"].items():
        for path, row in paths.items():
            for segment in path.replace("[]", ".[]").replace(
                    "{}", ".{}").split("."):
                if not segment:
                    continue
                assert allowed.match(segment), (
                    f"{origin}#{path}: `{segment}` is not a field name")
            assert isinstance(row, list) and len(row) >= 2, (
                f"{origin}#{path}: a row is [occurrences, bytes, types...]")
            assert all(isinstance(item, str) for item in row[2:])


def test_every_document_is_declared_once_and_completely():
    seen = set()
    for document in data_map.DOCUMENTS:
        assert document.id not in seen, f"{document.id} declared twice"
        seen.add(document.id)
        assert document.literals, f"{document.id} names no filename"
        assert len(document.what) > 30, f"{document.id} says too little"
        assert document.writer, f"{document.id} names no writer"


def test_a_step_input_resolves_back_to_the_step_that_produced_it():
    """The join that makes the map a map rather than a pile of dicts."""
    assert data_map.resolve(
        "IN@step_3_01_assign_aroll#clip_catalog[].width") == \
        "OUT@catalog#clip_catalog[].width"
    assert data_map.resolve(
        "DOC@pipeline_data#step_outputs.catalog.clip_catalog") == \
        "OUT@catalog#clip_catalog"
    assert data_map.resolve("OUT@catalog#clip_catalog") == \
        "OUT@catalog#clip_catalog", "an origin already resolved is a no-op"


def test_what_breaks_is_derived_from_the_shape_not_guessed(rows):
    by_tag = {row.tag: row for row in rows}
    raises = by_tag["OUT@mesh_spine#audio_spine.structure[].block_type"]
    assert "raises" in raises.breaks
    nothing = by_tag["OUT@catalog#source_resolution"]
    assert nothing.breaks.startswith("NOTHING")
    assert nothing.bytes > 0, (
        "a field that costs nothing to keep would not be worth ranking")


def test_a_collapsed_lookup_key_still_joins_to_the_code_that_reads_it():
    """`observe` folds a dict keyed by data into `{}`; the code reads
    `tracks["V1"]["clips"]` by name. If these do not join, the whole
    manifest's track content reads as unread - which would be the
    opposite of true."""
    observed = data_map.load_observed()
    assert data_map.observed_key(
        "OUT@compile_manifest#assembly_manifest.tracks.V1.clips",
        observed) == "assembly_manifest.tracks{}.clips"
    assert data_map.observed_key(
        "OUT@compile_manifest#assembly_manifest.tracks[].clips",
        observed) == "assembly_manifest.tracks{}.clips", (
        "a DYNAMIC key must join to the same collapsed path")
    assert data_map.observed_key(
        "OUT@compile_manifest#assembly_manifest.tracks.V1.invented",
        observed) == "", "the match may not invent a path"


def test_an_unread_verdict_is_corroborated_by_a_second_instrument(rows):
    """`output_contract._read_literals` knows nothing about documents or
    dataflow and answers the same question from the only other angle.
    Where it also finds nothing, two instruments agree."""
    unread = [row for row in rows if row.observed and row.unread]
    assert unread, "a survey with nothing unread would not need a ranking"
    corroborated = [row for row in unread if not row.name_read_somewhere]
    assert corroborated, (
        "not one unread field's name is absent from every read position "
        "in the tree - the corroborating instrument has stopped working")
    # The corroborating instrument sees a key that is a STRING LITERAL
    # at the read site - a subscript, a `.get`, an `in`, a key inside a
    # list handed to a call.  It cannot see a key that arrives as a
    # variable, and `field_flow` can, so the two disagree in exactly one
    # direction and on exactly these five fields.  Recorded rather than
    # counted, so a NEW one is a finding and a stale entry fails too.
    # A sixth entry lived here -
    # `OUT@music_selection#music_selection.measurements.true_peak_dbtp`,
    # read by `music_measurement.bed_reading` through a loop variable.
    # PR 1153 refactored `MEASUREMENT_LEGEND` (a dict, whose keys the
    # literal scan does not count) into `MEASURED_KEYS = frozenset({...})`,
    # and a string inside a set handed to a call IS a read position to
    # that scan - so the name now resolves as a literal at
    # music_measurement.py:211. The loop-variable read itself is
    # byte-identical before and after that commit; only the record went
    # stale, so the entry was deleted rather than the code changed.
    seen_only_by_the_dataflow = {
        "DOC@pipeline_data#run_restarts":
            "`run_restart.append_to_state` reads it through a module "
            "constant, not a literal.",
        "OUT@creative_direction#creative_direction.audience_emotion":
            "`creative_direction.direction_value(cd, key)` - the key is "
            "a PARAMETER, and every caller passes a different one.",
        "OUT@creative_direction#creative_direction.emotional_landscape":
            "the same call.",
        "OUT@creative_direction#creative_direction.key_moments":
            "the same call.",
        "OUT@creative_direction#creative_direction.target_energy":
            "the same call - and `target_energy` has ONE reading "
            "(AGENTS.md 10.1), so losing this read would lose the field "
            "that rule is about.",
    }
    dict_read = [row for row in rows
                 if any(access.shape != field_flow.ATTR
                        for access in row.readers)]
    assert dict_read, "no field is read by a subscript or a `.get`"
    disagreeing = {row.tag for row in dict_read
                   if not row.name_read_somewhere}
    assert disagreeing == set(seen_only_by_the_dataflow), (
        f"new: {sorted(disagreeing - set(seen_only_by_the_dataflow))}\n"
        f"stale: {sorted(set(seen_only_by_the_dataflow) - disagreeing)}")


def test_the_blind_spots_all_have_a_measured_size():
    sizes = data_map.blind_spot_sizes()
    assert data_map.KNOWN_BLIND_SPOTS, "a blind spot list may not be empty"
    for name in ("dynamic_reads", "reads_the_artefacts_say_cannot_exist",
                 "observed_fields", "documents_never_observed"):
        assert name in sizes
    assert sizes["observed_fields"] > 1000
    assert sizes["dynamic_reads"] > 0, (
        "a dynamic-key read count of zero would mean the analyzer had "
        "stopped noticing them, not that the tree had none")
