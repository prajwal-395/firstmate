"""VC-13: producing-implementation versions and input records.

`code_identity` versions the implementation behind a registry
operation - resolved dynamically, never from a static table - and
proves a component's producing set complete. An unknown dependency
versions to None and the proof reads incomplete, which is what fails
the caller closed to a rebuild.
"""

from library.tools import code_identity as identity


def test_a_registry_operation_resolves_to_its_owning_step():
    ref = identity.operation_implementation_ref("reel.build")
    assert ref == "library/steps/step_7_01_build_reels/step.py"


def test_an_unknown_operation_resolves_to_nothing():
    assert identity.operation_implementation_ref(
        "no-such-operation") is None
    assert identity.operation_implementation_version(
        "no-such-operation") is None


def test_a_prompt_operation_versions_its_prompt_file():
    ref = identity.operation_implementation_ref("creative.direct")
    assert ref is not None and ref.endswith(".md")
    assert identity.operation_implementation_version(
        "creative.direct") is not None


def test_a_missing_file_has_no_fingerprint(tmp_path):
    assert identity.implementation_fingerprint(
        "library/tools/no_such_file.py") is None
    assert identity.implementation_fingerprint(
        str(tmp_path / "absent.py")) is None


def test_a_changed_file_moves_its_fingerprint(tmp_path):
    target = tmp_path / "producer.py"
    target.write_text("x = 1\n", encoding="utf-8")
    first = identity.implementation_fingerprint(
        str(target), repo_root=tmp_path)
    target.write_text("x = 2\n", encoding="utf-8")
    assert identity.implementation_fingerprint(
        str(target), repo_root=tmp_path) != first


def test_the_proof_is_complete_only_when_every_ref_versions():
    proof = identity.implementation_proof(["reel.build"])
    assert proof["complete"], proof["detail"]
    assert proof["unproven"] == []
    proof = identity.implementation_proof(
        ["reel.build", "no-such-operation"])
    assert not proof["complete"]
    assert proof["unproven"] == ["no-such-operation"]
    assert "rebuilds" in proof["detail"]


def test_input_records_name_the_unanswerable():
    versions = identity.implementation_versions_for(
        ["reel.build", "no-such-operation"])
    assert versions["reel.build"]
    assert versions["no-such-operation"] is None
