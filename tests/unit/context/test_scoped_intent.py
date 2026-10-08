from library.tools.scoped_intent import correct, declaration, from_sources, resolve


def d(source, value, scope="project-a", **kw):
    return declaration(
        "tone", value, source=source, scope=scope, provenance=f"{source}:{value}", **kw
    )


def test_creator_brand_project_precedence_and_provenance():
    view = resolve(
        [
            d("project", "warm"),
            d("brand", "bold"),
            d("creator", "quiet", explicit=True),
        ],
        project_id="project-a",
        now="2026-01-01T00:00:00+00:00",
    )
    assert view["values"] == {"tone": "quiet"}
    assert view["influenced_by"]["tone"]["provenance"] == "creator:quiet"
    assert view["declarations"][0]["resolution"] == "eligible"


def test_conflicting_equal_precedence_is_reported_without_arbitrary_winner():
    view = resolve(
        [d("creator", "quiet", explicit=True), d("creator", "bold", explicit=True)],
        project_id="project-a",
    )
    assert "tone" not in view["values"]
    assert len(view["conflicts"]["tone"]) == 2


def test_confidence_breaks_same_source_ties_before_conflict_reporting():
    view = resolve(
        [
            d("creator", "likely", explicit=True, confidence=0.6),
            d("creator", "confirmed", explicit=True, confidence=1.0),
        ],
        project_id="project-a",
    )
    assert view["values"]["tone"] == "confirmed"
    assert not view["conflicts"]


def test_expiry_scope_and_inference_do_not_cross_projects():
    rows = [
        d("creator", "global", "*", explicit=True),
        d("inference", "inferred", "project-a"),
        declaration(
            "expired",
            True,
            source="project",
            scope="project-b",
            provenance="old note",
            expires_at="2025-01-01T00:00:00+00:00",
        ),
    ]
    view = resolve(rows, project_id="project-b", now="2026-01-01T00:00:00+00:00")
    assert view["values"]["tone"] == "global"
    assert view["declarations"][1]["resolution"] == "out_of_scope"
    assert view["declarations"][2]["resolution"] == "expired"


def test_correction_and_projection_are_reproducible():
    old, replacement = correct(
        d("project", "old"), "new", provenance="captain correction"
    )
    old["superseded_by"] = replacement["provenance"]
    rows = [old, replacement]
    first = resolve(rows, project_id="project-a", now="2026-01-01T00:00:00+00:00")
    assert first == resolve(
        rows, project_id="project-a", now="2026-01-01T00:00:00+00:00"
    )
    assert first["values"]["tone"] == "new"
    assert first["declarations"][0]["resolution"] == "corrected"


def test_legacy_intent_and_brief_adapter_keeps_inference_separate():
    view = from_sources(
        intent_model={"declarations": [{"key": "tone", "value": "inferred"}]},
        creative_brief={"declarations": [{"key": "tone", "value": "creator"}]},
        project_id="project-a",
        now="2026-01-01T00:00:00+00:00",
    )
    assert view["values"]["tone"] == "creator"
    assert {row["source"] for row in view["declarations"]} == {"creator", "inference"}
    assert (
        view["digest"]
        == resolve(
            [
                declaration(
                    "tone",
                    "inferred",
                    source="inference",
                    scope="project-a",
                    provenance="intent_model",
                ),
                declaration(
                    "tone",
                    "creator",
                    source="creator",
                    scope="project-a",
                    provenance="creative_brief",
                    explicit=True,
                ),
            ],
            project_id="project-a",
            now="2026-01-01T00:00:00+00:00",
        )["digest"]
    )


def test_legacy_view_migration_preserves_declarations():
    from library.tools.scoped_intent import migrate

    row = {
        "key": "tone",
        "value": "warm",
        "source": "project",
        "scope": "project-a",
        "provenance": "project.yaml",
        "confidence": 1,
    }
    assert migrate([row]) == {"version": 1, "declarations": [row]}
    assert migrate({"declarations": [row]})["version"] == 1


def test_same_scoped_view_reaches_every_declared_planning_context(
    tmp_path, monkeypatch
):
    import json
    from pathlib import Path
    from library.tools import project_context, taste_profile

    monkeypatch.setattr(
        taste_profile, "_read", lambda path: {"version": 1, "preferences": {}}
    )
    project = tmp_path / "project"
    project.mkdir()
    state = {
        "project_folder": str(project),
        "intent_model": {"narrative_theme": "an inferred story"},
        "creative_brief": str(project / "brief.md"),
        "project_config": {"target_duration_seconds": 30},
    }
    manifests = sorted(Path("library/steps").glob("step_*/manifest.json"))
    planning_nodes = [
        path.parent.name.removeprefix("step_")
        for path in manifests
        if any(
            item.get("name") == "project_context"
            for item in json.loads(path.read_text(encoding="utf-8"))["interface"][
                "inputs"
            ]
        )
    ]
    assert planning_nodes
    digests = set()
    for node in planning_nodes:
        context = project_context.project_context_for_step(
            str(project), node, state=state
        )
        assert "scoped intent view (declarations and provenance)" in context
        view = json.loads(
            context.split(
                "--- scoped intent view (declarations and provenance) ---\n", 1
            )[1]
        )
        assert view["declarations"]
        assert view["influenced_by"]["attached_brief"]["source"] == "creator"
        digests.add(view["digest"])
    assert len(digests) == 1
