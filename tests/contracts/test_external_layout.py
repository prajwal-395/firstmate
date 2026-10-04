"""External supplied state and stable declarations have separate paths."""

from library.tools import external_inputs
from library.tools.project_layout import AREAS, Area, Kind, ProjectLayout
from library.tools.versions import store, variants


def test_external_state_and_declarations_are_distinct_protected_areas(tmp_path):
    state = AREAS[Area.EXTERNAL_STATE]
    declarations = AREAS[Area.EXTERNAL_DECLARATIONS]

    assert state.relpath == "external/state"
    assert declarations.relpath == "external/declarations"
    assert state.kind is declarations.kind is Kind.INPUT

    layout = ProjectLayout(tmp_path).ensure()
    assert not layout.read_dir(Area.EXTERNAL_STATE).exists()
    assert not layout.read_dir(Area.EXTERNAL_DECLARATIONS).exists()


def test_only_declarations_are_versioned_and_state_is_rebuilt(tmp_path):
    declaration_names = external_inputs.declaration_stems()
    state_names = set(external_inputs.CHECKS) - declaration_names

    assert "captain_edits" in declaration_names
    assert "captain_edits" in external_inputs.CHECKS
    assert state_names
    assert "/external/declarations/**" in store.ALLOW_LIST
    assert "/external/**" not in store.ALLOW_LIST

    state_key = sorted(state_names)[0]
    assert variants._is_generated(f"external/state/{state_key}.json")
    assert variants._is_generated(f"external/{state_key}.json")
    assert not variants._is_generated(
        "external/declarations/captain_edits.json")

    body = store.gitignore_body()
    assert "!/external/declarations/**" in body
    assert "!/external/state/**" not in body
