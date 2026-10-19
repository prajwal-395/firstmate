import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from library.tools import project_model_store
from library.tools.project_model import validate_project_model
from library.tools.project_model_store import (
    append_model_patch,
    commit_evidence_import,
    commit_project_model,
    commit_project_model_records,
    load_project_model,
    merge_evidence_records,
    parity_report,
    write_parity_report,
)

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(encoding="utf-8")
)


def test_repeated_verified_head_is_validated_once_without_sharing_mutable_data(
        tmp_path, monkeypatch):
    project_model_store._validated_heads.clear()
    commit_project_model(tmp_path, FIXTURE, expected_revision=0)
    # Exercise the in-process verdict cache, not the persistent
    # validated-models file (covered by its own test below): an older
    # store has no such file.
    (tmp_path / "project_model" / "validated-models.json").unlink(
        missing_ok=True)
    calls = []
    reads = []
    original = project_model_store.validate_project_model
    original_read = project_model_store._read_events

    def validate(model):
        calls.append(model["id"])
        original(model)

    def read(path):
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(project_model_store, "validate_project_model", validate)
    monkeypatch.setattr(project_model_store, "_read_events", read)
    _, first = load_project_model(tmp_path)
    first["id"] = "caller-mutated"
    assert load_project_model(tmp_path) == (1, FIXTURE)
    assert len(calls) == 1
    # The snapshot fast path bypasses the full history scan when the tail
    # matches: full reads happen at most twice, never to re-validate.
    assert len(reads) <= 2
    assert project_model_store.project_model_revision(tmp_path) == 1
    assert len(reads) <= 2
    # A verified new head gets its own validation verdict.
    intents = [{**FIXTURE["intents"][0], "constraints": ["changed"]}]
    append_model_patch(tmp_path, {"intents": intents}, expected_revision=1)
    assert load_project_model(tmp_path)[1]["intents"] == intents
    assert len(calls) == 2
    # Even a cached head must pass the hash chain on every load.
    path = tmp_path / "project_model/events.jsonl"
    path.write_text(path.read_text().replace('"changed"', '"tampered"'),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_project_model(tmp_path)


def test_model_load_cache_detects_rewrite_even_when_mtime_is_restored(tmp_path):
    import os
    commit_project_model(tmp_path, FIXTURE, expected_revision=0)
    load_project_model(tmp_path)
    path = tmp_path / "project_model/events.jsonl"
    stat = path.stat()
    path.write_text(path.read_text().replace('"project-demo"', '"project-fake"'),
                    encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="hash mismatch"):
        project_model_store.project_model_revision(tmp_path)


def test_revision_log_replays_and_repairs_snapshot(tmp_path):
    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0, reason="fixture") == 1
    updated = dict(FIXTURE)
    updated["intents"] = [{**FIXTURE["intents"][0], "constraints": ["keep captions"]}]
    assert append_model_patch(project, {"intents": updated["intents"]}, expected_revision=1) == 2
    (project / "project_model" / "snapshot.json").unlink()

    revision, model = load_project_model(project)

    assert revision == 2
    assert model == updated
    snapshot = json.loads((project / "project_model" / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["revision"] == 2
    assert len((project / "project_model" / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.parametrize("damage", ["missing", "invalid_json", "stale_revision", "stale_hash"])
def test_snapshot_damage_is_rebuilt_from_the_saved_event_history(tmp_path, damage):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0, reason="saved baseline")
    updated = {**FIXTURE, "intents": [
        {**FIXTURE["intents"][0], "constraints": ["preserve evidence"]}
    ]}
    append_model_patch(project, {"intents": updated["intents"]}, expected_revision=1)
    snapshot_path = project / "project_model" / "snapshot.json"
    if damage == "missing":
        snapshot_path.unlink()
    elif damage == "invalid_json":
        snapshot_path.write_text("{truncated", encoding="utf-8")
    else:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if damage == "stale_revision":
            snapshot["revision"] = 1
        elif damage == "stale_hash":
            snapshot["head_hash"] = "0" * 64
        else:
            snapshot["model"] = FIXTURE
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    revision, model = load_project_model(project)

    assert (revision, model) == (2, updated)
    repaired = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert repaired["revision"] == 2
    assert repaired["head_hash"] == json.loads((project / "project_model" / "events.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])["event_hash"]
    assert repaired["model"] == updated
    import hashlib as _hashlib
    assert repaired["model_hash"] == _hashlib.sha256(json.dumps(
        updated, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8") + b"\n").hexdigest()


def test_commit_uses_tail_fast_path_without_full_history_scan(tmp_path, monkeypatch):
    """The second commit appends via tail + snapshot, not a full replay.

    Bypassing the GB-scale history scan is what takes model maintenance
    from minutes to seconds. The appended event and snapshot are
    byte-identical to the slow path: same revision, previous hash chain,
    and model hash.
    """
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0, reason="first")
    reads: list = []
    original_read = project_model_store._read_events
    monkeypatch.setattr(project_model_store, "_read_events",
                        lambda path: (_ for _ in ()).throw(
                            AssertionError("full history scan on fast path")))
    second = dict(FIXTURE)
    second["id"] = "project-demo-2"
    revision = commit_project_model(project, second, expected_revision=1,
                                    reason="second")
    assert revision == 2
    assert reads == []
    loaded_revision, loaded = load_project_model(project)
    assert (loaded_revision, loaded["id"]) == (2, "project-demo-2")
    events = (project / "project_model" / "events.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(events) == 2
    assert original_read(project / "project_model" / "events.jsonl")[1][
        "previous_hash"] == original_read(
            project / "project_model" / "events.jsonl")[0]["event_hash"]


def test_read_events_tail_handles_lines_longer_than_one_chunk(tmp_path):
    """A last line spanning several 1 MB chunks returns intact and in order.

    The first tail reader prepended to a bytes buffer per 64 KB chunk -
    O(n^2) on 340 MB replace lines, hanging the live build past
    10 minutes. Chunks join once now; a 3 MB line forces the
    multi-chunk path without a slow test.
    """
    from library.tools import project_model_store as store_mod
    path = tmp_path / "events.jsonl"
    big_payload = "y" * (3 * 1024 * 1024)
    first = {"revision": 1, "previous_hash": "0" * 64, "kind": "note",
             "payload": "x"}
    first["event_hash"] = "a" * 64
    second = {"revision": 2, "previous_hash": "a" * 64, "kind": "note",
              "payload": big_payload}
    second["event_hash"] = "b" * 64
    with path.open("w", encoding="utf-8") as stream:
        stream.write(store_mod._json_bytes(first).decode("utf-8"))
        stream.write(store_mod._json_bytes(second).decode("utf-8"))
    tail = store_mod._read_events_tail(path)
    assert tail is not None
    assert tail["revision"] == 2
    assert tail["event_hash"] == "b" * 64
    assert tail["payload"] == big_payload


def test_reel_projection_round_trips_and_falls_back_on_stale_head(tmp_path):
    """Routing reads frame maps without parsing the 350 MB snapshot.

    The sidecar carries only reel-scoped rows from the just-validated
    model; a revision+head match against the log tail answers, anything
    else falls back to the full verified load. A tampered head must not
    serve stale rows.
    """
    from library.tools import project_model_store as store_mod
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0, reason="first")
    revision, projection = store_mod.load_reel_projection(project)
    assert revision == 1
    assert projection is not None
    assert set(projection) == set(store_mod.REEL_SCOPED_COLLECTIONS)
    assert projection["clip_uses"] == FIXTURE["clip_uses"]
    # A head the log no longer carries falls back instead of serving
    # stale rows.
    sidecar_path = (project / "project_model"
                    / store_mod.REEL_PROJECTION_FILE)
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["head_hash"] = "0" * 64
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    assert store_mod.load_reel_projection(project) == (0, None)
    # A missing sidecar (older stores) also falls back.
    sidecar_path.unlink()
    assert store_mod.load_reel_projection(project) == (0, None)


def test_validated_model_hashes_persist_across_processes(tmp_path, monkeypatch):
    """A head validated at commit is not re-validated by later loads.

    The in-process cache dies with every build process; without the
    file, each of the 4-5 loads per end-to-end build re-validated the
    same 350 MB head (8.5 s a pass, 36 s in input routing alone). Hash
    equality is byte-identity with validated bytes; anything else falls
    back to the full verified read.
    """
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    validated_file = (project / "project_model" / "validated-models.json")
    hashes = json.loads(validated_file.read_text(encoding="utf-8"))
    assert len(hashes) == 1
    calls = []
    real_validate = project_model_store.validate_project_model
    monkeypatch.setattr(
        project_model_store, "validate_project_model",
        lambda model: (calls.append(1), real_validate(model))[1])
    project_model_store._validated_heads.clear()
    revision, model = load_project_model(project)
    assert revision == 1
    assert model["id"] == FIXTURE["id"]
    assert calls == []
    # A new head still earns its own validation at commit time.
    updated = dict(FIXTURE)
    updated["id"] = "project-demo-2"
    commit_project_model(project, updated, expected_revision=1)
    assert len(json.loads(validated_file.read_text(encoding="utf-8"))) == 2
    assert load_project_model(project)[0] == 2
    assert len(calls) == 1


def test_snapshot_with_matching_keys_but_stale_model_returns_events_replay(tmp_path):
    """A snapshot whose model was replaced is repaired from the replay.

    Freshness is revision + head + model hash (no 350 MB deep compare on
    every load). A snapshot with correct keys but a stale model fails the
    hash check, so load returns the verified events replay and rewrites
    the snapshot.
    """
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0, reason="saved baseline")
    updated = {**FIXTURE, "intents": [
        {**FIXTURE["intents"][0], "constraints": ["preserve evidence"]}
    ]}
    append_model_patch(project, {"intents": updated["intents"]}, expected_revision=1)
    snapshot_path = project / "project_model" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["model"] = FIXTURE
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    revision, model = load_project_model(project)

    assert (revision, model) == (2, updated)
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["model"] == updated


def test_snapshot_write_failure_after_durable_append_recovers_on_restart(tmp_path, monkeypatch):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    updated = {**FIXTURE, "intents": [
        {**FIXTURE["intents"][0], "constraints": ["recover committed revision"]}
    ]}
    real_write = project_model_store._write_snapshot

    def interrupted_write(*_args, **_kwargs):
        raise OSError("simulated interruption after event fsync")

    monkeypatch.setattr(project_model_store, "_write_snapshot", interrupted_write)
    with pytest.raises(OSError, match="simulated interruption"):
        append_model_patch(project, {"intents": updated["intents"]}, expected_revision=1)
    monkeypatch.setattr(project_model_store, "_write_snapshot", real_write)

    revision, replayed = load_project_model(project)
    assert revision == 2
    assert replayed == updated
    assert json.loads((project / "project_model" / "snapshot.json").read_text(
        encoding="utf-8"))["model"] == updated


def test_interrupted_event_append_refuses_without_discarding_valid_history(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    events_path = project / "project_model" / "events.jsonl"
    with events_path.open("ab") as stream:
        stream.write(b'{"revision":2,"kind":"merge_patch"')
    before = events_path.read_bytes()

    with pytest.raises(ValueError, match="invalid project model event at revision 2"):
        load_project_model(project)

    assert events_path.read_bytes() == before
    assert json.loads((project / "project_model" / "snapshot.json").read_text(
        encoding="utf-8"))["revision"] == 1


def test_concurrent_capability_evidence_updates_retry_without_losing_either(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    gate = Barrier(2)
    rows = [
        {**FIXTURE["observations"][0], "id": "obs-cap-a"},
        {**FIXTURE["observations"][0], "id": "obs-cap-b"},
    ]

    def write(row):
        gate.wait()
        try:
            return merge_evidence_records(
                project, observations=[row], expected_revision=1,
                author=f"capability:{row['id']}")
        except RuntimeError as exc:
            assert "stale project model revision 1" in str(exc)
            return merge_evidence_records(
                project, observations=[row], expected_revision=2,
                author=f"capability:{row['id']}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(write, rows))

    revision, model = load_project_model(project)
    assert revision == 3
    assert sorted(revisions) == [2, 3]
    assert {row["id"] for row in model["observations"]} >= {
        "obs-cap-a", "obs-cap-b", FIXTURE["observations"][0]["id"]
    }


def test_revision_conflict_and_corrupt_event_are_rejected(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    with pytest.raises(RuntimeError, match="stale"):
        commit_project_model(project, FIXTURE, expected_revision=0)
    events = project / "project_model" / "events.jsonl"
    events.write_text(events.read_text(encoding="utf-8").replace('"reason":""', '"reason":"tampered"'), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_project_model(project)


def test_capability_evidence_merge_appends_one_revision_and_stale_writer_refuses(tmp_path):
    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0) == 1
    added = {**FIXTURE["observations"][0], "id": "obs-from-capability"}
    assert merge_evidence_records(
        project, observations=[added], expected_revision=1,
        author="capability:semantics.analyse") == 2
    with pytest.raises(RuntimeError, match="stale project model revision 1"):
        merge_evidence_records(
            project, observations=[{**added, "id": "stale-observation"}],
            expected_revision=1, author="capability:semantics.analyse")
    revision, replayed = load_project_model(project)
    assert revision == 2
    assert replayed["observations"][-1] == added
    assert len((project / "project_model" / "events.jsonl").read_text(
        encoding="utf-8").splitlines()) == 2


def test_record_change_events_are_small_sorted_and_replay_with_legacy_history(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    changed = {**FIXTURE["observations"][0], "id": "obs-small-edit"}
    assert commit_project_model_records(project, [{
        "collection": "observations", "operation": "append",
        "id": changed["id"], "record": changed,
    }], expected_revision=1) == 2
    event = project_model_store._read_events(
        project / "project_model" / "events.jsonl")[-1]
    assert event["kind"] == "record_changes"
    assert event["payload"] == [{
        "collection": "observations", "operation": "append",
        "id": changed["id"], "record": changed,
    }]
    # Small against the full model a replace event would carry (the
    # envelope alone exceeds a one-row bare observations list, so that
    # narrower comparison cannot hold for tiny fixtures).
    assert len(project_model_store._json_bytes(event)) < len(
        project_model_store._json_bytes(FIXTURE))
    revision, model = load_project_model(project)
    assert revision == 2
    assert model["observations"] == sorted(
        [*FIXTURE["observations"], changed], key=lambda row: row["id"])


def test_record_change_update_and_delete_are_keyed_by_stable_id(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    # obs-1 stays: the fixture's edit-1 references it through
    # evidence_ids, so deleting it must refuse rather than dangle.
    # Exercise the stable-id update/delete cycle on an independent row.
    independent = {**FIXTURE["observations"][0], "id": "obs-stable-id"}
    commit_project_model_records(project, [{
        "collection": "observations", "operation": "append",
        "id": independent["id"], "record": independent,
    }], expected_revision=1)
    updated = {**independent, "confidence": 0.7}
    commit_project_model_records(project, [{
        "collection": "observations", "operation": "update",
        "id": independent["id"], "record": updated,
    }], expected_revision=2)
    commit_project_model_records(project, [{
        "collection": "observations", "operation": "delete",
        "id": independent["id"],
    }], expected_revision=3)
    observations = load_project_model(project)[1]["observations"]
    assert independent["id"] not in {row["id"] for row in observations}
    assert FIXTURE["observations"][0]["id"] in {row["id"] for row in observations}


def test_record_rebase_accepts_disjoint_and_refuses_overlapping_changes(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    obs = {**FIXTURE["observations"][0], "id": "obs-independent"}
    commit_project_model_records(project, [{
        "collection": "observations", "operation": "append",
        "id": obs["id"], "record": obs,
    }], expected_revision=1)
    intent = {**FIXTURE["intents"][0], "id": "intent-independent"}
    assert commit_project_model_records(project, [{
        "collection": "intents", "operation": "append",
        "id": intent["id"], "record": intent,
    }], expected_revision=1) == 3
    tail = project_model_store._read_events(
        project / "project_model" / "events.jsonl")[-1]
    assert tail["rebase_checked"] == [{
        "revision": 2,
        "event_hash": project_model_store._read_events(
            project / "project_model" / "events.jsonl")[1]["event_hash"],
    }]
    overlap = {**FIXTURE["observations"][0], "id": obs["id"]}
    # Base rev 1 predates the rev-2 append of this same (collection, id),
    # so the rebase must refuse as an overlapping record conflict. (A
    # base of rev 2 already contains the row, which refuses instead as
    # an invalid duplicate append.)
    with pytest.raises(RuntimeError, match="record conflict"):
        commit_project_model_records(project, [{
            "collection": "observations", "operation": "append",
            "id": overlap["id"], "record": overlap,
        }], expected_revision=1)


def test_editorial_patch_rebases_over_unrelated_evidence_record(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    observation = {**FIXTURE["observations"][0], "id": "obs-concurrent"}
    merge_evidence_records(project, observations=[observation],
                           expected_revision=1)
    intent = {**FIXTURE["intents"][0], "id": "intent-concurrent"}
    assert append_model_patch(
        project, {"intents": [*FIXTURE["intents"], intent]},
        expected_revision=1, reason="non-overlapping editorial update") == 3
    event = project_model_store._read_events(
        project / "project_model" / "events.jsonl")[-1]
    assert [entry["revision"] for entry in event["rebase_checked"]] == [2]


def test_checkpoint_bounds_replay_and_preserves_old_event_hashes(tmp_path, monkeypatch):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    for revision in range(1, project_model_store.CHECKPOINT_INTERVAL):
        append_model_patch(project, {"intents": FIXTURE["intents"]},
                           expected_revision=revision)
    events_path = project / "project_model" / "events.jsonl"
    hashes_before = [event["event_hash"] for event in
                     project_model_store._read_events(events_path)]
    checkpoint_path = project / "project_model" / project_model_store.CHECKPOINT_FILE
    assert checkpoint_path.is_file()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["revision"] == project_model_store.CHECKPOINT_INTERVAL
    assert project_model_store.migrate_project_model_checkpoint(project) == checkpoint[
        "revision"]
    assert [event["event_hash"] for event in
            project_model_store._read_events(events_path)] == hashes_before

    append_model_patch(project, {"intents": FIXTURE["intents"]},
                       expected_revision=checkpoint["revision"])
    (project / "project_model" / "snapshot.json").unlink()
    monkeypatch.setattr(project_model_store, "_read_events", lambda _path: pytest.fail(
        "checkpoint recovery must not replay history before its checkpoint"))
    revision, model = load_project_model(project)
    assert revision == checkpoint["revision"] + 1
    assert model == FIXTURE


def test_evidence_import_cas_preserves_timeline_state_and_edit_history(tmp_path):
    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0) == 1
    timeline = {"id": "timeline-state-1", "timeline_id": "timeline-1",
                "generation": 1, "clip_use_ids": ["use-1"],
                "edit_operation_ids": ["edit-1"]}
    assert append_model_patch(
        project, {"timeline_states": [timeline]}, expected_revision=1) == 2

    evidence = {**FIXTURE, "observations": [
        FIXTURE["observations"][0],
        {**FIXTURE["observations"][0], "id": "obs-imported"}]}
    assert commit_evidence_import(
        project, evidence, expected_revision=2,
        author="capability:project_model_evidence") == 3
    revision, current = load_project_model(project)
    assert revision == 3
    assert current["timeline_states"] == [timeline]
    assert current["edits"] == FIXTURE["edits"]
    assert {row["id"] for row in current["observations"]} == {"obs-1", "obs-imported"}
    assert project_model_store._read_events(
        project / "project_model" / "events.jsonl")[-1]["kind"] == "record_changes"
    with pytest.raises(RuntimeError, match="stale project model revision 2"):
        commit_evidence_import(
            project, evidence, expected_revision=2,
            author="capability:project_model_evidence")


def test_parity_report_is_read_only_and_names_unreadable_stores(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    (project / "pipeline_data.json").write_text("{", encoding="utf-8")
    before = (project / "pipeline_data.json").read_bytes()

    report = write_parity_report(project)

    assert report["adapters"]["pipeline_data"]["readable"] is False
    assert report["mismatches"] == [{"store": "pipeline_data", "kind": "unreadable", "path": "pipeline_data.json"}]
    assert (project / "pipeline_data.json").read_bytes() == before
    assert parity_report(project)["project_id"] == FIXTURE["id"]
    validate_project_model(FIXTURE)


def test_pipeline_data_projects_the_event_revision_and_parity_is_actionable(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    state = {"existing": True}
    from library.tools.project_model_store import project_pipeline_state

    assert project_pipeline_state(project, state) is True
    (project / "pipeline_data.json").write_text(json.dumps(state), encoding="utf-8")
    report = parity_report(project)
    assert report["revision"]["number"] == 1
    assert not any(row["kind"] in {
        "stale_revision", "project_model_projection_drift"
    } for row in report["mismatches"])

    state["project_model_projection"]["revision"] = 0
    (project / "pipeline_data.json").write_text(json.dumps(state), encoding="utf-8")
    drift = parity_report(project)["mismatches"]
    projection_drift = next(row for row in drift
                            if row["kind"] == "project_model_projection_drift")
    assert "save pipeline state" in projection_drift["action"]

    snapshot_path = project / "project_model" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["revision"] = 0
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    before = snapshot_path.read_bytes()
    report = parity_report(project)
    assert any(row["kind"] == "stale_revision" for row in report["mismatches"])
    assert snapshot_path.read_bytes() == before


def test_replay_validates_once_not_once_per_event(tmp_path, monkeypatch):
    """Replaying N events validates one model, not N models.

    Regression for the 2026-10-17 build stall: `_replay` validated the
    whole model after every event, so loading a 37-event log with a
    350 MB model ran 37 full jsonschema validations (7.6 s each -
    a 12-minute hang on the `sorted(validator.iter_errors(...))`
    frame). The hash chain `_read_events` verifies still guards every
    event; the schema check runs once on the replayed model.
    """
    from library.tools import project_model_store as store

    calls = []
    real_validate = store.validate_project_model

    def counting_validate(model):
        calls.append(id(model))
        return real_validate(model)

    monkeypatch.setattr(store, "validate_project_model", counting_validate)

    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0,
                                reason="baseline") == 1
    for revision in range(1, 6):
        updated = dict(FIXTURE)
        updated["intents"] = [{**FIXTURE["intents"][0],
                               "constraints": [f"keep {revision}"]}]
        append_model_patch(project, {"intents": updated["intents"]},
                           expected_revision=revision)
    calls.clear()
    store._validated_heads.clear()
    # Exercise the replay path, not the persistent validated-models
    # file (an older store has none): the file would let the fast path
    # answer without replaying at all.
    (project / "project_model" / "validated-models.json").unlink(
        missing_ok=True)

    revision, _model = load_project_model(project)

    assert revision == 6
    assert calls, "the replayed model must still be validated"
    assert len(calls) <= 2, (
        f"replaying 6 events validated {len(calls)} models - "
        "the per-event validation loop is back")


def test_replay_still_refuses_a_broken_event_chain(tmp_path):
    from library.tools.project_model_store import _read_events, _replay

    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0, reason="x")
    events_path = project / "project_model" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    lines.append(json.dumps({"revision": 2, "previous_hash": "0" * 64,
                             "kind": "bogus", "payload": {}}))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="broken project model event chain"):
        _read_events(events_path)
    with pytest.raises(ValueError, match="invalid event kind or order"):
        _replay([{"revision": 1, "kind": "bogus", "payload": {}}])


def test_long_history_replays_legacy_replace_and_both_incremental_formats(tmp_path):
    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0) == 1
    revision = append_model_patch(
        project, {"intents": FIXTURE["intents"]}, expected_revision=1)
    for index in range(2, project_model_store.CHECKPOINT_INTERVAL + 4):
        record = {**FIXTURE["intents"][0], "id": f"intent-{index}"}
        revision = commit_project_model_records(
            project, [{"collection": "intents", "operation": "append",
                       "id": record["id"], "record": record}],
            expected_revision=revision)

    events = project_model_store._read_events(
        project / "project_model" / "events.jsonl")
    assert [event["kind"] for event in events[:3]] == [
        "replace", "merge_patch", "record_changes"]
    (project / "project_model" / "snapshot.json").unlink()
    loaded_revision, model = load_project_model(project)
    assert loaded_revision == revision
    assert {row["id"] for row in model["intents"]} >= {
        f"intent-{index}"
        for index in range(2, project_model_store.CHECKPOINT_INTERVAL + 4)
    }


def test_restored_canonical_event_backup_rebuilds_derived_store_files(tmp_path):
    import shutil

    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0) == 1
    updated = {**FIXTURE, "intents": [
        {**FIXTURE["intents"][0], "constraints": ["saved history"]}
    ]}
    append_model_patch(project, {"intents": updated["intents"]},
                       expected_revision=1)
    event_path = project / "project_model" / "events.jsonl"
    backup = tmp_path / "events.jsonl.backup"
    shutil.copy2(event_path, backup)
    backup_bytes = backup.read_bytes()

    append_model_patch(project, {"intents": FIXTURE["intents"]},
                       expected_revision=2)
    shutil.copy2(backup, event_path)
    (project / "project_model" / "snapshot.json").unlink()

    revision, model = load_project_model(project)

    assert (revision, model) == (2, updated)
    assert event_path.read_bytes() == backup_bytes
    snapshot = json.loads((project / "project_model" / "snapshot.json")
                          .read_text(encoding="utf-8"))
    assert snapshot["revision"] == 2
    assert snapshot["model"] == updated


def test_speech_interpretation_upsert_appends_record_changes(tmp_path):
    from library.tools.project_model_narrative import (
        persist_speech_sequence,
    )

    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    interpretation = {
        "id": "interpretation-sequence-1",
        "kind": "narrative_relation",
        "author": "ignored-input-author",
        "certainty": "interpreted",
        "evidence_ids": ["event-pickup"],
        "scope": {"event_ids": ["event-pickup"]},
        "narrative_relation": {
            "event_ids": ["event-pickup"],
            "relation_type": "explanation",
            "speech_passage_ids": ["speech-1"],
            "status": "candidate",
        },
    }

    result = persist_speech_sequence(
        project, {"interpretations": [interpretation]})

    events = project_model_store._read_events(
        project / "project_model" / "events.jsonl")
    assert result["revision"] == 2
    assert events[-1]["kind"] == "record_changes"
    assert events[-1]["payload"][0]["collection"] == "interpretations"
