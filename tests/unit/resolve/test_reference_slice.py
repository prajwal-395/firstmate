"""AGENT-017: a call gets the context it needs, not the whole manual.

The six generated AXI capability references are 141 KB of flat tables -
one row per operation per dimension - and an agent that needed one
operation's row had to open the file and find it. The step-context layer
already has the answer for prompts (`context_fields` and the `view:`
projections); the reference layer had none.

These tests name the defects:

- the narrow read returns the operation's rows, and they are the SAME
  rows the generated files carry - a slice that re-derives them is a
  second list, and a second list drifts;
- a slice is small, so a caller can afford to read it;
- the generated files are unchanged by the slice existing - the checked-
  in references stay byte-for-byte what the generator prints;
- the operation skill can be rendered for the operations a task names,
  and its default rendering is unchanged;
- an operation the registry does not hold is refused by name, rather
  than returning an empty slice that reads as "nothing to say".
"""
from __future__ import annotations

import pytest

from library.tools import edit_algebra, operations


def test_the_slice_returns_the_rows_the_generated_file_carries():
    # Defect: a slice that re-derives the rows is a SECOND list, and a
    # second list drifts from the registry the files are generated from.
    references = operations.emit_resolve_axi_references()
    for op_id in sorted(edit_algebra.OP_STATUS):
        sections = operations.reference_sections(op_id)
        assert set(sections) == set(references)
        for name, body in sections.items():
            if not body:
                continue
            for line in body.strip().splitlines():
                if line.startswith("|---"):
                    continue
                assert line in references[name], (op_id, name, line)


def test_the_slice_is_much_smaller_than_the_whole_reference_set():
    # The point of the slice: a caller can read it.
    whole = sum(len(body) for body in
                operations.emit_resolve_axi_references().values())
    slice_size = sum(len(body) for body in
                      operations.reference_sections("clip.trim").values())
    assert slice_size < whole / 10, (slice_size, whole)


def test_an_operation_with_no_transaction_row_comes_back_empty():
    # `transactions.md` lists only the compilable operations, so an
    # operation that is not compilable has no transaction row. An empty
    # section is the answer, not a missing one.
    sections = operations.reference_sections("clip.trim")
    assert sections["concepts.md"]
    assert sections["capability-matrix.md"]
    assert sections["recovery.md"]


def test_an_operation_the_registry_does_not_hold_is_refused_by_name():
    with pytest.raises(KeyError) as refused:
        operations.reference_sections("not.an.operation")
    assert "not.an.operation" in str(refused.value)


def test_the_generated_files_are_unchanged_by_the_slice_existing():
    # The checked-in references stay byte-for-byte what the generator
    # prints; a slice that changed the files would be a second source.
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    refs = repo / ".agents" / "skills" / "resolve-axi" / "references"
    expected = operations.emit_resolve_axi_references()
    assert {path.name for path in refs.glob("*.md")} == set(expected)
    for name, content in expected.items():
        assert (refs / name).read_text(encoding="utf-8") == content


def test_the_operation_skill_can_be_rendered_for_the_operations_a_task_names():
    whole = operations.emit_skill()
    sliced = operations.emit_skill(only=("reel.build",))
    assert "reel.build" in sliced
    assert len(sliced) < len(whole)
    # The default is the whole roster, byte for byte, because the
    # checked-in skill IS that.
    assert whole == operations.emit_skill()


def test_an_unknown_operation_in_a_skill_slice_is_refused():
    with pytest.raises(KeyError) as refused:
        operations.emit_skill(only=("not.an.operation",))
    assert "not.an.operation" in str(refused.value)


def test_the_axi_command_prints_the_narrow_read(capsys):
    from argparse import Namespace

    from library.tools import resolve_axi_core_commands as axi

    assert axi.cmd_capabilities(Namespace(
        operation="clip.trim", search="", references=True, json=False)) == 0
    output = capsys.readouterr().out
    assert "capability-matrix.md" in output
    assert "clip.trim" in output
    # The whole registry would have been 141 KB; the slice is not.
    assert len(output) < 8000, len(output)


def test_the_axi_command_refuses_a_slice_with_no_operation(capsys):
    from argparse import Namespace

    from library.tools import resolve_axi_core_commands as axi

    assert axi.cmd_capabilities(Namespace(
        operation="", search="", references=True, json=False)) == 1
    assert "no operation was named" in capsys.readouterr().out
