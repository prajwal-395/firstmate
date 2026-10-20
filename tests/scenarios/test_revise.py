"""`ren revise`: one note in, one verified change (or one question) out.

The defect: every part of the note-to-change loop existed and nothing
called them in order. A person typed `ren spec prepare`, answered a
handshake file, ran `ren spec proposal` and `ren spec resolve`,
hand-wrote the ops and scope JSON the patch flow takes, and chose
between a patch and a rebuild themselves. These scenarios drive the
one verb end to end on test doubles: the translation (the host model's
answer to the handshake file), the live Resolve handles and the guarded
apply, and the engine runs a rebuild shells out to. The project, its
shadow store and its records live under `tmp_path`.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

REEL = "Reel 02 - pricing"
QUALIFICATION = "Ren Qualification"
FPS = 30.0
START = 108000


def _caption(uid: str, start: int, end: int) -> dict:
    return {"unique_id": uid, "name": f"{uid}.mov", "record_in": START + start,
            "record_out": START + end, "duration": end - start,
            "enabled": True, "transform": {"ZoomX": 1.0, "ZoomY": 1.0},
            "markers": []}


SNAPSHOT = {
    "timeline": REEL, "project": QUALIFICATION, "fps": FPS,
    "start_frame": START, "end_frame": START + 900,
    "tracks": [
        {"type": "video", "index": 1, "name": "A-roll", "clips": [{
            "unique_id": "aroll-1", "name": "host.mov", "record_in": START,
            "record_out": START + 900, "duration": 900, "enabled": True,
            "transform": {"ZoomX": 1.0, "ZoomY": 1.0}, "markers": []}]},
        {"type": "video", "index": 5, "name": "Subtitles", "clips": [
            _caption("cap-1", 30, 90), _caption("cap-2", 90, 150),
            _caption("cap-3", 600, 660)]},
    ],
    "markers": {"timeline": [], "notes": []},
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    from library.tools import assignment_spine as spine
    from library.tools import reel_proposal, timeline_shadow
    from library.tools.project_layout import ProjectLayout
    from library.tools.reel_proposal import PROPOSAL_FORMAT, ReelMoment

    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    root = tmp_path / "pricing-show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\n'
        f'project_format_version: 1\nresolve:\n  project_name: "{QUALIFICATION}"\n',
        encoding="utf-8")
    layout = ProjectLayout(str(root))
    layout.ensure()
    # The reel is proposed, approved and built: the assignment holds it
    # in review, the way `ren propose` / `ren build` leave it.
    moment = ReelMoment(number=2, slug="pricing", reason="the price lands",
                        timeline_start=10.0, timeline_end=40.0).as_dict()
    moment["approval"] = "approved"
    path = reel_proposal.proposal_path(str(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": PROPOSAL_FORMAT,
                                "moments": [moment]}), encoding="utf-8")
    spine.on_propose(root)
    spine.end_act(spine.begin_build(root), ok=True)
    Path(layout.pipeline_data_path).write_text(json.dumps({"step_outputs": {
        "select_reels": {"reel_selection": {"moments": [
            {"number": 2, "slug": "pricing"}]}}}}), encoding="utf-8")
    timeline_shadow.ShadowStore().record(
        project=QUALIFICATION, timeline_id="tl-02", timeline_name=REEL,
        snapshot=SNAPSHOT, source=timeline_shadow.OBSERVED, expected_head=0)
    return root


def _answer(project: Path, result: dict, response: dict) -> None:
    """The host model answers the translation handshake file."""
    from library.tools import llm_handshake

    Path(llm_handshake.response_path(str(project), result["spec_id"])) \
        .write_text(json.dumps({"format": "edit_spec/1", **response}),
                    encoding="utf-8")


def _assignment(project: Path) -> dict:
    from library.tools import assignment_spine as spine

    return spine.current(project, spine.KIND_REELS)


def _outcomes(project: Path) -> list:
    from library.tools import feedback_outcomes

    return feedback_outcomes.read_events(str(project))


def _ledger(project: Path) -> list:
    from library.tools import edit_ledger

    return edit_ledger.load_rows(str(project))


def _assert_program_refused_unqualified(result: dict, live) -> None:
    """The AXI policy now refuses semantic ops without production proof."""
    from library.tools import revise

    assert result["status"] == revise.STATUS_REFUSED
    assert result["classification"]["verdict"] == "refused"
    assert "no production-qualified executor" in (
        result["classification"]["reason"])
    assert live.applied == []


@pytest.fixture
def qualification_runtime(monkeypatch):
    """The live runtime the qualification inspection reads.

    The inspection checks the recorded qualification predicates against
    the LIVE runtime, so a test stands in for Resolve here rather than
    opening the live instance (tests/conftest.py). This fixture's
    timeline holds no item, which is not the single seeded clip the
    recorded evidence was earned on, so the promotion does not carry and
    a promoted program stays refused - the gate, unchanged.
    """
    from types import SimpleNamespace

    from library.tools import axi_qualification_state

    monkeypatch.setattr(
        axi_qualification_state, "_read_runtime",
        lambda project, timeline: {
            "resolve": SimpleNamespace(
                GetVersionString=lambda: "DaVinci Resolve 21.1.0.14"),
            "project": SimpleNamespace(GetName=lambda: QUALIFICATION),
            "timeline": SimpleNamespace(GetName=lambda: REEL),
            "rows": []})


@pytest.fixture
def live(monkeypatch, qualification_runtime):
    """Resolve, as the patch path meets it: handles, read-back, apply."""
    from types import SimpleNamespace

    from library.tools import timeline_shadow as shadow

    applied = []
    patched = json.loads(json.dumps(SNAPSHOT))

    def apply_patch(**kw):
        applied.append(kw)
        for op in kw["operations"]:
            for track in patched["tracks"]:
                for clip in track["clips"]:
                    if clip["unique_id"] == op["unique_id"]:
                        clip["transform"][op["key"]] = op["value"]
        return {"backup": {"generation": 1, "hash": "pre"},
                "patch": {"status": "committed", "generation": 2},
                "outside_scope": []}

    monkeypatch.setattr(shadow, "read_live", lambda *a, **k: patched)
    monkeypatch.setattr(shadow, "timeline_key",
                        lambda *a, **k: (QUALIFICATION, "tl-02", REEL))

    @contextlib.contextmanager
    def handles(project, timeline_name):
        assert timeline_name == REEL
        yield (SimpleNamespace(GetName=lambda: QUALIFICATION),
               SimpleNamespace(GetName=lambda: REEL))

    return SimpleNamespace(applied=applied, handles=handles,
                           options={"live": handles,
                                    "patch_overrides": {
                                        "apply_patch": apply_patch}})


def _scale(uid: str, key: str) -> dict:
    return {"op": "clip.set_property",
            "text": f"scale {uid} up; 1.25 is the model's size, not stated",
            "params": {"unique_id": uid, "key": key, "value": 1.25}}


def test_unqualified_caption_size_program_is_refused_before_any_write(
        project, live, monkeypatch):
    """Typed programs cannot bypass AXI's production qualification gate."""
    from library.tools import revise

    started = revise.start(project, "the captions are too small", reel=REEL)
    assert started["status"] == revise.STATUS_AWAITING
    context = json.loads(json.loads(Path(started["llm_request"]).read_text(
        encoding="utf-8"))["context"])
    # The translation is shown the reel's own items, so its program can
    # name them by id - never an invented one.
    assert context["reel_timeline"].get("available"), context["reel_timeline"]
    subtitles = next(row for row in context["reel_timeline"]["rows"]
                     if row["name"] == "Subtitles")
    assert [item["unique_id"] for item in subtitles["items"]] == [
        "cap-1", "cap-2", "cap-3"]
    assert context["reel_timeline"]["generation"] == "1"

    _answer(project, started, {
        "request": "the captions are too small",
        "program": {"kind": "edit_program", "reel": REEL,
                    "base_generation": "1",
                    "idempotency_key": "captions-bigger",
                    "anchor": {"kind": "reel"},
                    "ops": [_scale(uid, key) for uid in ("cap-1", "cap-2")
                            for key in ("ZoomX", "ZoomY")]}})
    result = revise.advance(project, started["revise_id"], **live.options)

    _assert_program_refused_unqualified(result, live)
    assert "route" not in result
    assert _ledger(project) == []


def test_a_shot_order_note_becomes_a_scoped_rebuild(project, monkeypatch):
    """Catches: an order change patched onto the timeline (only a fresh
    plan re-chooses order), or rebuilt wholesale instead of from the step
    that owns the order and for the one reel the note is about."""
    from library.tools import revise

    started = revise.start(
        project, "put the pricing answer before the intro story", reel=REEL)
    _answer(project, started, {
        "request": "put the pricing answer before the intro story",
        "clauses": [{
            "id": "order", "status": "resolved",
            "text": "play the pricing answer before the intro story",
            "op": "speech_selection", "op_source": "model",
            "anchor": {"kind": "words", "phrase": "we doubled the price"},
            "anchor_source": "requester",
            "reel": REEL, "reel_source": "requester",
            "values": {"order": {
                "value": "the pricing answer plays before the intro story",
                "unit": "passage order", "stated_by": "requester"}}}]})
    ran = []
    result = revise.advance(project, started["revise_id"],
                            run=lambda argv: ran.append(argv) or 0)

    assert result["status"] == revise.STATUS_UNVERIFIED
    assert result["route"] == revise.ROUTE_REBUILD
    assert result["classification"]["verdict"] == "rebuild"
    assert ran == [["run", str(project), "--rerun", "speech_sequence"],
                   ["build-reels", str(project), "--only-reel", "2"]]
    assert result["commands"] == [
        f"ren edit {project} --rerun speech_sequence",
        f"ren build {project} --only-reel 2"]
    assert result["verification"]["state"] == "not_proven"
    assert [row["op"] for row in _ledger(project)] == ["plan_change"]
    # One outcome: planned before the run, then answered after it - and
    # answered honestly: no check proves a re-planned order, so it is
    # recorded unverifiable rather than verified.
    [outcome] = _outcomes(project)
    assert outcome["status"] == "unverifiable"
    assert outcome["action"]["summary"] == result["change"]["summary"]


def test_preview_flag_reaches_the_scoped_build(project, monkeypatch):
    """A one-build request survives revise's scoped rebuild routing."""
    from library.tools import revise

    started = revise.start(
        project, "put the pricing answer before the intro story", reel=REEL,
        preview=True)
    _answer(project, started, {
        "request": "put the pricing answer before the intro story",
        "clauses": [{
            "id": "order", "status": "resolved",
            "text": "play the pricing answer before the intro story",
            "op": "speech_selection", "op_source": "model",
            "anchor": {"kind": "words", "phrase": "we doubled the price"},
            "anchor_source": "requester", "reel": REEL,
            "reel_source": "requester",
            "values": {"order": {
                "value": "the pricing answer plays before the intro story",
                "unit": "passage order", "stated_by": "requester"}}}]})
    ran = []
    result = revise.advance(
        project, started["revise_id"], run=lambda argv: ran.append(argv) or 0)

    assert result["status"] == revise.STATUS_UNVERIFIED
    assert ran[-1] == ["build-reels", str(project), "--only-reel", "2",
                       "--preview"]


def test_an_ambiguous_note_asks_exactly_one_question_and_changes_nothing(
        project, live):
    """Catches: a note whose referent is unclear being guessed into an edit,
    or bounced back as a list of questions instead of one."""
    from library.tools import revise, timeline_shadow

    before = json.dumps(_assignment(project), sort_keys=True)
    started = revise.start(project, "make that bit pop more")
    question = {"status": "needs_clarification", "op_source": "model"}
    _answer(project, started, {
        "request": "make that bit pop more",
        "clauses": [
            {**question, "id": "which", "text": "make that bit pop more",
             "op": "visual_effect",
             "missing_referent": "which moment 'that bit' is",
             "question": "Which moment do you mean - the price reveal at "
                         "0:12 or the closing line?"},
            {**question, "id": "how", "text": "pop more", "op": "look",
             "missing_referent": "what 'pop' should change",
             "question": "Should it pop through colour or through motion?"},
        ]})
    result = revise.advance(project, started["revise_id"], **live.options)

    assert result["status"] == revise.STATUS_QUESTION
    assert result["reply"] == ("Which moment do you mean - the price reveal "
                               "at 0:12 or the closing line?")
    assert isinstance(result["question"], dict)
    assert "--answer" in result["answer_with"]
    # Nothing changed: no ledger row, no outcome, no timeline write, no
    # move of the assignment, the reel's recorded generation untouched.
    assert _ledger(project) == []
    assert _outcomes(project) == []
    assert live.applied == []
    assert json.dumps(_assignment(project), sort_keys=True) == before
    store = timeline_shadow.ShadowStore()
    assert store.head(QUALIFICATION, "tl-02").generation == 1

    # The answer is translated again under the same note.
    answered = revise.advance(project, started["revise_id"],
                              answer="the price reveal")
    assert answered["status"] == revise.STATUS_AWAITING
    assert answered["note_id"] == started["note_id"]
    from library.tools import llm_handshake
    request = llm_handshake.read_request(answered["llm_request"])
    assert "Answered: the price reveal" in request["prompt"]


def test_executable_operation_can_ask_for_a_missing_timeline_target(
        project, live):
    """Catches: a missing target ID making an executable op fail its schema.

    The editor gets the target question, and the unresolved program never
    reaches classification or the guarded timeline apply.
    """
    from library.tools import edit_spec, llm_handshake, revise

    started = revise.start(project, "lower the music on the missing clip")
    timeline = edit_spec._reel_timeline(str(project), REEL)
    listed_ids = {
        item["unique_id"]
        for row in timeline["rows"]
        for item in row["items"]
    }
    missing_id = "audio-clip-not-in-timeline"
    assert missing_id not in listed_ids
    request = llm_handshake.read_request(started["llm_request"])
    context = json.loads(request["context"])
    supplied_ids = {
        item["unique_id"]
        for row in context["reel_timeline"]["rows"]
        for item in row["items"]
    }
    assert missing_id not in supplied_ids
    _answer(project, started, {
        "request": "lower the music on the missing clip",
        "program": {
            "kind": "edit_program", "reel": REEL,
            "base_generation": "1", "idempotency_key": "missing-audio",
            "ops": [{
                "op": "audio.gain",
                "text": "Lower the music on the missing clip",
                "params": {"unique_id": missing_id, "db": -6},
            }],
        },
    })

    result = revise.advance(project, started["revise_id"], **live.options)

    assert result["status"] == revise.STATUS_QUESTION
    assert result["question"]["missing_referent"] == (
        f"timeline item {missing_id}")
    assert result["reply"] == (
        f"Which item on {REEL} did you mean by {missing_id!r}?")
    assert live.applied == []
    assert _ledger(project) == []


def test_unqualified_program_does_not_touch_signed_off_work(project, live):
    """A refused program leaves approved work and its assignment untouched."""
    from library.tools import assignment_spine as spine
    from library.tools import revise

    spine.on_sign_off(project, REEL)
    started = revise.start(project, "the captions are too small", reel=REEL)
    _answer(project, started, {
        "request": "the captions are too small",
        "program": {"kind": "edit_program", "reel": REEL,
                    "base_generation": "1", "idempotency_key": "k",
                    "ops": [_scale("cap-1", "ZoomX")]}})
    result = revise.advance(project, started["revise_id"], **live.options)
    _assert_program_refused_unqualified(result, live)
    assert spine.current(project, spine.KIND_REELS)["state"] == "approved"
    assert live.applied == [] and _ledger(project) == []


def test_unqualified_program_is_refused_before_any_write(
        project, live):
    """A stale semantic program is stopped by qualification before write."""
    from library.tools import revise

    started = revise.start(project, "the captions are too small", reel=REEL)
    _answer(project, started, {
        "request": "the captions are too small",
        "program": {"kind": "edit_program", "reel": REEL,
                    "base_generation": "0", "idempotency_key": "k",
                    "ops": [_scale("cap-1", "ZoomX")]}})
    result = revise.advance(project, started["revise_id"], **live.options)
    _assert_program_refused_unqualified(result, live)
    assert live.applied == [] and _ledger(project) == []


def test_off_qualification_unqualified_patch_is_refused(
        project, monkeypatch, qualification_runtime):
    """No project scope bypasses the production executor qualification."""
    from library.tools import edit_spec, revise

    monkeypatch.setattr(edit_spec, "_resolve_project_name",
                        lambda folder: QUALIFICATION)
    from library.tools.resolved import jobs

    monkeypatch.setattr(jobs, "QUALIFICATION_PROJECT", "Some Other Project")
    started = revise.start(project, "the captions are too small", reel=REEL)
    _answer(project, started, {
        "request": "the captions are too small",
        "program": {"kind": "edit_program", "reel": REEL,
                    "base_generation": "1", "idempotency_key": "k",
                    "ops": [_scale("cap-3", "ZoomX"),
                            _scale("cap-3", "ZoomY")]}})
    ran = []
    result = revise.advance(project, started["revise_id"],
                            run=lambda argv: ran.append(argv) or 0)

    assert result["status"] == revise.STATUS_REFUSED
    assert result["classification"]["verdict"] == "refused"
    assert "no production-qualified executor" in (
        result["classification"]["reason"])
    assert ran == []
    assert "route" not in result


def _prop(uid: str, key: str, value: float) -> dict:
    return {"op": "clip.set_property",
            "text": f"set {key} on {uid} to {value}",
            "params": {"unique_id": uid, "key": key, "value": value}}


def _program(reel: str, base: str, ops: list, key: str) -> dict:
    return {
        "request": "test revision",
        "program": {"kind": "edit_program", "reel": reel,
                    "base_generation": base, "idempotency_key": key,
                    "anchor": {"kind": "reel"}, "ops": ops}}


def test_a_second_property_program_is_still_refused(
        project, live):
    """A second property program cannot bypass fail-closed AXI routing."""
    from library.tools import revise

    first = revise.start(project, "the first caption is small", reel=REEL)
    _answer(project, first, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.25)], "first"))
    landed_a = revise.advance(project, first["revise_id"], **live.options)
    _assert_program_refused_unqualified(landed_a, live)

    second = revise.start(project, "the second caption is small", reel=REEL)
    _answer(project, second, _program(
        REEL, "1", [_prop("cap-2", "ZoomX", 1.5)], "second"))
    landed_b = revise.advance(project, second["revise_id"], **live.options)
    _assert_program_refused_unqualified(landed_b, live)
    assert _ledger(project) == []


def test_a_rival_property_program_is_refused_before_any_write(
        project, live):
    """Conflicting property programs are both refused before any write."""
    from library.tools import revise

    first = revise.start(project, "the caption is small", reel=REEL)
    _answer(project, first, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.25)], "first"))
    landed_a = revise.advance(project, first["revise_id"], **live.options)
    _assert_program_refused_unqualified(landed_a, live)

    second = revise.start(project, "the caption is tiny", reel=REEL)
    _answer(project, second, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.5)], "second"))
    landed_b = revise.advance(project, second["revise_id"], **live.options)
    _assert_program_refused_unqualified(landed_b, live)
    assert _ledger(project) == []


def test_an_interrupted_rebuild_resumes_where_it_stopped(project):
    """Catches: an interrupted revision losing its plan (F08 interrupted
    revisions). The first run fails mid-rebuild and the record holds the
    failed command; continuing the same id reruns the same scoped
    commands and records the result as unverified."""
    from library.tools import revise

    started = revise.start(
        project, "put the pricing answer before the intro story", reel=REEL)
    _answer(project, started, {
        "request": "put the pricing answer before the intro story",
        "clauses": [{
            "id": "order", "status": "resolved",
            "text": "play the pricing answer before the intro story",
            "op": "speech_selection", "op_source": "model",
            "anchor": {"kind": "words", "phrase": "we doubled the price"},
            "anchor_source": "requester", "reel": REEL,
            "reel_source": "requester",
            "values": {"order": {
                "value": "the pricing answer plays before the intro story",
                "unit": "passage order", "stated_by": "requester"}}}]})
    stalled = revise.advance(project, started["revise_id"],
                             run=lambda argv: 1)

    assert stalled["status"] == revise.STATUS_INCOMPLETE
    assert stalled["failed_command"] == (
        f"ren edit {project} --rerun speech_sequence")
    assert "resume" in stalled
    record = revise.load(str(project), started["revise_id"])
    assert [entry["status"] for entry in record["history"]] == [
        revise.STATUS_AWAITING, revise.STATUS_INCOMPLETE]

    ran = []
    resumed = revise.advance(project, started["revise_id"],
                             run=lambda argv: ran.append(argv) or 0)

    assert resumed["status"] == revise.STATUS_UNVERIFIED
    assert resumed["route"] == revise.ROUTE_REBUILD
    assert ran == [["run", str(project), "--rerun", "speech_sequence"],
                   ["build-reels", str(project), "--only-reel", "2"]]
    assert [entry["status"] for entry in
            revise.load(str(project), started["revise_id"])["history"]] == [
        revise.STATUS_AWAITING, revise.STATUS_INCOMPLETE,
        revise.STATUS_UNVERIFIED]


def test_a_failed_patch_leaves_the_revision_open_for_retry(project, live):
    """An unqualified setter is refused before any live patch is attempted."""
    from library.tools import revise

    started = revise.start(project, "the captions are too small", reel=REEL)
    _answer(project, started, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.25)], "once"))

    result = revise.advance(project, started["revise_id"], **live.options)
    _assert_program_refused_unqualified(result, live)
    assert revise.load(str(project), started["revise_id"])["status"] == (
        revise.STATUS_REFUSED)
    assert _ledger(project) == []


def test_the_verb_is_on_both_front_doors():
    """Catches: a verb the Python registry knows and the native one does
    not (the native front door refuses it before exec'ing)."""
    from ren.commands import VERBS

    verb = next(v for v in VERBS if v.name == "revise")
    assert verb.module_argv == ("library.tools.revise",)
    native = (Path(__file__).resolve().parents[2] / "native" / "ren-cli" /
              "src" / "commands.rs").read_text(encoding="utf-8")
    assert 'name: "revise"' in native


def test_a_marker_note_over_a_recorded_decision_is_judged_in_the_same_translation(
        project, monkeypatch):
    """Catches: a note that is about a recorded edit decision (here a
    reaction cutaway under the marker) being translated with no idea the
    decision exists - `decision_implication` had no caller at all."""
    from library.tools import marker_feedback, marker_routing, project_graph, revise
    from library.tools.project_layout import Area, ProjectLayout

    finding = {"node_id": "finding:cutaway-7", "record": {
        "decision": {"action": "cut to the guest laughing",
                     "reason": "the price lands"},
        "speech_timeline_start": 10.0, "speech_timeline_end": 15.0,
        "speech_clip_id": "clip_011", "reaction_clip_id": "clip_014"}}
    monkeypatch.setattr(project_graph, "build_graph", lambda folder: {})
    monkeypatch.setattr(project_graph, "find_editorial_findings",
                        lambda graph, kind: [finding])
    raw = {"source": "timeline_marker", "name": "note",
           "note": "this reaction comes in too early",
           "text": "note\n\nthis reaction comes in too early",
           "frame": START + 360, "frame_in_timeline_space": 360,
           "duration_frames": 1, "custom_data": {}, "attachments": []}
    pull = ProjectLayout(str(project)).write_path(
        Area.MARKER_FEEDBACK, f"Reel02.20261012{marker_feedback.PULL_FILE_SUFFIX}")
    pull.write_text(json.dumps({
        "format": marker_feedback.PULL_FORMAT, "timeline": REEL,
        "timeline_fps": FPS, "timeline_start_frame": START,
        "notes": [raw]}), encoding="utf-8")
    note_id = marker_routing.edit_link_id(raw, REEL, str(pull))

    started = revise.start(project, note_id=note_id)
    context = json.loads(json.loads(Path(started["llm_request"]).read_text(
        encoding="utf-8"))["context"])
    assert [c["node_id"] for c in context["decision_candidates"]] == [
        "finding:cutaway-7"]
    assert started["note_id"] == note_id and started["reel"] == REEL

    _answer(project, started, {
        "request": raw["text"],
        "implicated": [{"node_id": "finding:cutaway-7",
                        "why": "the note is about this cutaway's timing"}],
        "clauses": [{
            "id": "late", "status": "resolved", "op": "shot_selection",
            "op_source": "model", "text": "bring the reaction in later",
            "anchor": {"kind": "words", "phrase": "we doubled the price"},
            "anchor_source": "requester", "reel": REEL,
            "reel_source": "requester",
            "values": {"timing": {"value": "after the price lands",
                                  "unit": "cut point",
                                  "stated_by": "requester"}}}]})
    result = revise.advance(project, started["revise_id"], plan_only=True)

    assert result["status"] == revise.STATUS_PROPOSED
    [implicated] = result["decisions"]["implicated"]
    assert implicated["node_id"] == "finding:cutaway-7"
    assert result["decisions"]["direction"] == raw["text"]
    assert result["commands"] == [
        f"ren edit {project} --rerun select_broll",
        f"ren build {project} --only-reel 2"]
    assert _ledger(project) == [] and _outcomes(project) == []


def test_a_subjective_request_offers_candidates_and_asks_which_one(
        project, live):
    """Catches: a subjective request carried out as one unexamined plan, or
    candidates offered without the editor being asked to choose."""
    from library.tools import revise

    started = revise.start(project, "the opening feels slow", reel=REEL)
    assert started["status"] == revise.STATUS_AWAITING

    def candidate(name, rationale, key):
        return {"name": name, "rationale": rationale,
                "program": {"kind": "edit_program", "reel": REEL,
                            "base_generation": "1",
                            "idempotency_key": key,
                            "anchor": {"kind": "reel"},
                            "ops": [_scale("cap-1", "ZoomX")]},
                "criteria": [{"criterion": "the claim lands first",
                              "kind": "brief", "verdict": "pass",
                              "reason": "the claim is spoken first",
                              "evidence_refs": ["transcript:1"]}]}

    _answer(project, started, {
        "request": "the opening feels slow",
        "candidates": [candidate("Claim first", "The claim lands the point.",
                                 "claim-first"),
                       candidate("Question first", "Opens on a question.",
                                 "question-first")]})
    result = revise.advance(project, started["revise_id"], **live.options)

    assert result["status"] == revise.STATUS_QUESTION
    question = result["question"]
    assert question["missing_referent"] == "which candidate treatment to execute"
    assert "Claim first" in question["question"]
    assert "Question first" in question["question"]
    # Nothing was executed while the editor was being asked.
    assert live.applied == []
    assert _ledger(project) == []


def test_a_no_change_conclusion_settles_the_note_and_changes_nothing(
        project, live):
    """Catches: 'the material is already adequate' left as an open
    question, so the note held the project and nothing recorded that
    anyone had looked."""
    from library.tools import candidate_treatments, revise

    started = revise.start(project, "the opening feels slow", reel=REEL)
    _answer(project, started, {
        "request": "the opening feels slow",
        "no_change": {"reason": "the claim already lands first",
                      "evidence_refs": ["transcript:1"]}})
    result = revise.advance(project, started["revise_id"], **live.options)

    assert result["status"] == revise.STATUS_NO_CHANGE
    assert "Nothing needed changing" in result["reply"]
    assert live.applied == []
    assert _ledger(project) == []
    records = candidate_treatments.read_records(str(project))
    assert len(records) == 1
    assert records[0]["candidate_decision"]["decision"] == "no_change"
    assert records[0]["no_change"]["reason"] == "the claim already lands first"
