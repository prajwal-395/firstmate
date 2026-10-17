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
        f'project_format_version: 1\nresolve:\n  project: "{QUALIFICATION}"\n',
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


@pytest.fixture
def live(monkeypatch):
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


def test_a_caption_size_note_is_patched_in_place_and_says_where(
        project, live, monkeypatch):
    """Catches: a caption-size note going to a rebuild (or nowhere) instead
    of a patch on the captions already placed, an outcome that is not
    recorded, and a reply that does not say where the change landed."""
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

    assert result["status"] == revise.STATUS_PATCHED
    assert result["route"] == revise.ROUTE_PATCH
    assert result["classification"]["verdict"] == "patch"
    # The ops and the scope JSON the flow used to take by hand.
    [applied] = live.applied
    assert applied["scope"] == {"clips": ["cap-1", "cap-2"],
                                "marker_frames": [], "clip_markers": []}
    assert [op["op"] for op in applied["operations"]] == [
        "clip.set_property"] * 4
    # The summary names the region: the reel, the row, the seconds.
    change = result["change"]
    assert change["region"] == {"reel": REEL, "rows": ["V5 Subtitles"],
                                "start_seconds": 1.0, "end_seconds": 5.0}
    assert "captions" in change["changed_categories"]
    assert result["reply"] == change["summary"]
    assert "V5 Subtitles" in result["reply"] and REEL in result["reply"]
    assert "0:01.0" in result["reply"] and "0:05.0" in result["reply"]
    # Verified by the read-back, and recorded with that summary.
    assert result["verification"]["record"]["status"] == "resolved_verified"
    [outcome] = _outcomes(project)
    assert outcome["status"] == "resolved_verified"
    assert outcome["action"]["kind"] == "patch"
    assert outcome["action"]["summary"] == change["summary"]
    assert outcome["verification"]["state"] == "timeline_readback"
    # The program is in the ledger, and the assignment says a revision
    # happened to the reel it owns - still in review, the reel still built.
    assert [row["op"] for row in _ledger(project)] == ["edit_program"]
    record = _assignment(project)
    assert record["history"][-1]["event"] == "revise"
    assert record["state"] == "review"
    assert next(iter(record["reels"].values()))["status"] == "built"


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

    assert result["status"] == revise.STATUS_REBUILT
    assert result["route"] == revise.ROUTE_REBUILD
    assert result["classification"]["verdict"] == "rebuild"
    assert ran == [["run", str(project), "--rerun", "speech_sequence"],
                   ["build-reels", str(project), "--only-reel", "2"]]
    assert result["commands"] == [
        f"ren edit {project} --rerun speech_sequence",
        f"ren build {project} --only-reel 2"]
    assert REEL in result["reply"] and "speech_sequence" in result["reply"]
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

    assert result["status"] == revise.STATUS_REBUILT
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


def test_revising_signed_off_work_asks_first(project, live):
    """Catches: a patch quietly changing a reel the creator already
    approved - D2 asks, exactly as a rebuild of approved work does."""
    from library.tools import assignment_spine as spine
    from library.tools import revise

    spine.on_sign_off(project, REEL)
    started = revise.start(project, "the captions are too small", reel=REEL)
    _answer(project, started, {
        "request": "the captions are too small",
        "program": {"kind": "edit_program", "reel": REEL,
                    "base_generation": "1", "idempotency_key": "k",
                    "ops": [_scale("cap-1", "ZoomX")]}})
    with pytest.raises(spine.AssignmentDecisionRequired):
        revise.advance(project, started["revise_id"], **live.options)
    assert live.applied == [] and _ledger(project) == []


def test_a_program_against_a_moved_reel_is_refused_before_any_write(
        project, live):
    """Catches: ops naming items as they were applied to what the reel
    holds now."""
    from library.tools import revise

    started = revise.start(project, "the captions are too small", reel=REEL)
    _answer(project, started, {
        "request": "the captions are too small",
        "program": {"kind": "edit_program", "reel": REEL,
                    "base_generation": "0", "idempotency_key": "k",
                    "ops": [_scale("cap-1", "ZoomX")]}})
    with pytest.raises(revise.ReviseRefused, match="changed since"):
        revise.advance(project, started["revise_id"], **live.options)
    assert live.applied == [] and _ledger(project) == []


def test_off_qualification_the_same_patch_is_a_touch_up(project, monkeypatch):
    """Catches: the guarded patch path (live only on Ren Qualification)
    being routed around on a real project - the same ops go through
    `ren touch`, stated by row and position."""
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

    assert result["status"] == revise.STATUS_TOUCHED
    [argv] = ran
    assert argv[:3] == ["touch-reel", str(project), "2"]
    assert json.loads(argv[4]) == [{
        "op": "set_properties", "row": "V5", "item": 2,
        "properties": {"ZoomX": 1.25, "ZoomY": 1.25}}]
    assert result["change"]["region"]["start_seconds"] == 20.0


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


def _record_landed_generation(reel: str, head: int) -> None:
    """Record what Resolve now holds, the way the real guarded apply does.

    The `live` double applies without recording a shadow generation; the
    test records the double's own read-back so the next revision meets a
    moved reel exactly as it would live.
    """
    from library.tools import timeline_shadow as shadow

    shadow.ShadowStore().record(
        project=QUALIFICATION, timeline_id="tl-02", timeline_name=reel,
        snapshot=json.loads(json.dumps(
            shadow.read_live(QUALIFICATION, reel))),
        source=shadow.PATCH, expected_head=head)


def _zoom(read_back: dict) -> dict:
    return {clip["unique_id"]: (clip.get("transform") or {}).get("ZoomX")
            for track in read_back["tracks"] for clip in track["clips"]
            if clip.get("unique_id")}


def test_a_second_revision_lands_on_the_first_and_keeps_its_change(
        project, live):
    """Catches: a repeated revision losing the earlier revision's change
    (F08 repeated revisions). The first revision lands, the reel moves to
    generation 2, the second is refused naming the first, retranslates,
    lands - and the read-back holds both changes."""
    from library.tools import revise
    from library.tools import timeline_shadow as shadow

    first = revise.start(project, "the first caption is small", reel=REEL)
    _answer(project, first, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.25)], "first"))
    landed_a = revise.advance(project, first["revise_id"], **live.options)

    assert landed_a["status"] == revise.STATUS_PATCHED
    assert landed_a["landed_generation"] == 2
    assert landed_a["scope"] == {"clips": ["cap-1"], "marker_frames": [],
                                 "clip_markers": []}
    assert landed_a["supersedes"] == []
    _record_landed_generation(REEL, head=1)

    second = revise.start(project, "the second caption is small", reel=REEL)
    _answer(project, second, _program(
        REEL, "1", [_prop("cap-2", "ZoomX", 1.5)], "second"))
    with pytest.raises(revise.ReviseRefused) as refused:
        revise.advance(project, second["revise_id"], **live.options)
    message = str(refused.value)
    assert "changed since" in message
    # The refusal says what moved: the first revision, elsewhere on the
    # reel (disjoint scopes are stale, not conflicting).
    assert first["revise_id"] in message
    assert "elsewhere on the reel" in message
    assert "cap-2" not in message
    assert live.applied and len(live.applied) == 1
    assert [row["op"] for row in _ledger(project)] == ["edit_program"]

    retranslated = revise.advance(
        project, second["revise_id"], retranslate=True)
    assert retranslated["status"] == revise.STATUS_AWAITING
    assert revise.load(str(project), second["revise_id"])["prior_bases"] == [1]
    _answer(project, retranslated, _program(
        REEL, "2", [_prop("cap-2", "ZoomX", 1.5)], "second-again"))
    landed_b = revise.advance(project, second["revise_id"], **live.options)

    assert landed_b["status"] == revise.STATUS_PATCHED
    assert landed_b["landed_generation"] == 2
    assert landed_b["supersedes"] == []
    held = shadow.read_live(QUALIFICATION, REEL)
    assert _zoom(held)["cap-1"] == 1.25
    assert _zoom(held)["cap-2"] == 1.5


def test_a_revision_over_a_landed_rival_names_it_and_records_supersede(
        project, live):
    """Catches: two conflicting revisions collapsing into a silent
    last-writer-wins (F08 conflicting revisions). Both target the same
    clip property; the second is refused naming the first as conflicting,
    and after retranslation its landing records the supersede."""
    from library.tools import revise
    from library.tools import timeline_shadow as shadow

    first = revise.start(project, "the caption is small", reel=REEL)
    _answer(project, first, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.25)], "first"))
    landed_a = revise.advance(project, first["revise_id"], **live.options)
    assert landed_a["status"] == revise.STATUS_PATCHED
    _record_landed_generation(REEL, head=1)

    second = revise.start(project, "the caption is tiny", reel=REEL)
    _answer(project, second, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.5)], "second"))
    with pytest.raises(revise.ReviseRefused) as refused:
        revise.advance(project, second["revise_id"], **live.options)
    message = str(refused.value)
    assert first["revise_id"] in message
    assert "clip cap-1" in message
    assert "same targets" in message

    retranslated = revise.advance(
        project, second["revise_id"], retranslate=True)
    _answer(project, retranslated, _program(
        REEL, "2", [_prop("cap-1", "ZoomX", 1.5)], "second-again"))
    landed_b = revise.advance(project, second["revise_id"], **live.options)

    assert landed_b["status"] == revise.STATUS_PATCHED
    assert landed_b["supersedes"] == [first["revise_id"]]
    assert _zoom(shadow.read_live(QUALIFICATION, REEL))["cap-1"] == 1.5


def test_an_interrupted_rebuild_resumes_where_it_stopped(project):
    """Catches: an interrupted revision losing its plan (F08 interrupted
    revisions). The first run fails mid-rebuild and the record holds the
    failed command; continuing the same id reruns the same scoped
    commands and lands."""
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

    assert resumed["status"] == revise.STATUS_REBUILT
    assert resumed["route"] == revise.ROUTE_REBUILD
    assert ran == [["run", str(project), "--rerun", "speech_sequence"],
                   ["build-reels", str(project), "--only-reel", "2"]]
    assert [entry["status"] for entry in
            revise.load(str(project), started["revise_id"])["history"]] == [
        revise.STATUS_AWAITING, revise.STATUS_INCOMPLETE,
        revise.STATUS_REBUILT]


def test_a_failed_patch_leaves_the_revision_open_for_retry(project, live):
    """Catches: a patch that blows up mid-apply stranding the revision
    (F08 interrupted revisions). The failure raises, the record stays
    open, and continuing the same id lands the change exactly once."""
    from library.tools import revise

    started = revise.start(project, "the captions are too small", reel=REEL)
    _answer(project, started, _program(
        REEL, "1", [_prop("cap-1", "ZoomX", 1.25)], "once"))

    def explode(**_kwargs):
        raise RuntimeError("Resolve fell over mid-write")

    options = {**live.options,
               "patch_overrides": {"apply_patch": explode}}
    with pytest.raises(revise.ReviseRefused, match="did not land verified"):
        revise.advance(project, started["revise_id"], **options)
    assert (revise.load(str(project), started["revise_id"])["status"]
            not in revise.FINISHED)
    assert live.applied == []

    result = revise.advance(project, started["revise_id"], **live.options)

    assert result["status"] == revise.STATUS_PATCHED
    assert len(live.applied) == 1


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
