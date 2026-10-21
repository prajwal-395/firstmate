"""A large context table reaches a step as a REFERENCE, not as a copy.

The tests here FOLLOW the reference rather than asserting its shape:
they take the path off the `FILE:` line the model reads, open it, and
require that what comes back is what the prompt no longer carries.

Measured on project 001 at `c2419c4cb` (scout `scout-opt-planning-cost`,
C1): 138,380 bytes of the planning band's context were byte-identical
repeats across steps - about 48,700 tokens, 21% of the band.
"""

import re

import pytest

from library.tools import context_reference
from library.tools.context_reference import (
    apply_references,
    reference_path,
)
from library.tools.toon_serializer import json_to_toon


def _rows(count: int, key: str = "clip_id") -> list:
    return [{
        key: f"clip_{index:03d}",
        "start": float(index),
        "end": float(index) + 1.0,
        "visual": f"a person is doing something in clip {index}, at length "
                  f"{'x' * 40}",
    } for index in range(count)]


def _context(rows: int = 60) -> dict:
    return {
        "picture": {"observed": _rows(rows)},
        "creative_direction": {
            "narrative_theme": "a long confession about quitting",
            "target_mood": "honest",
        },
    }


def _apply(context: dict, tmp_path, harness: str = "agent"):
    return apply_references(context, str(tmp_path), harness,
                            directory=tmp_path / "refs")


# ── The reference ───────────────────────────────────────────────────


def test_a_large_table_is_not_in_the_prompt_and_is_at_the_path(tmp_path):
    """The defect this exists for: the same bytes, sent again and again.

    The table is what the prompt no longer carries; the file is where it
    went.  Nothing is withheld - the rows are all there.
    """
    context = _context()
    out, referenced = _apply(context, tmp_path)

    assert "picture" in referenced
    rendered = json_to_toon(out)
    assert "a person is doing something in clip 0" not in rendered, (
        "the table's rows reached the prompt anyway")
    assert "FILE:" in rendered

    path = reference_path(out["picture"])
    assert path, "the reference must name the file the model reads"
    body = open(path, encoding="utf-8").read()
    assert "a person is doing something in clip 0" in body
    assert len(_rows(60)) == 60


def test_the_same_table_in_two_steps_is_one_file(tmp_path):
    """The dedupe: five steps that read the same table share ONE file."""
    first, _ = _apply(_context(), tmp_path)
    second, _ = _apply(_context(), tmp_path)

    assert reference_path(first["picture"]) == reference_path(second["picture"])


def test_a_table_that_differs_gets_its_own_file(tmp_path):
    """Content-addressed: different bytes are a different file."""
    first, _ = _apply(_context(60), tmp_path)
    second, _ = _apply(_context(61), tmp_path)

    assert reference_path(first["picture"]) != reference_path(second["picture"])


def test_a_harness_that_cannot_read_a_file_keeps_the_bytes_inline(tmp_path):
    """A route the model cannot follow is a loss, not a saving."""
    out, referenced = _apply(_context(), tmp_path, harness="api")

    assert referenced == []
    assert "a person is doing something in clip 0" in json_to_toon(out)


def test_a_section_that_is_already_a_reference_is_not_wrapped_again(tmp_path):
    """The brief arrives as a reference; a second one is a second read."""
    context = {
        "creative_brief": "The brief is NOT copied into this prompt.\n"
                          "  FILE: /tmp/brief.md\n",
    }
    out, referenced = _apply(context, tmp_path)

    assert referenced == []
    assert out["creative_brief"] == context["creative_brief"]


def test_prose_stays_inline_however_large_it_is(tmp_path):
    """A direction is READ; a table is INDEXED.

    A map of a paragraph is not a map - the step cannot decide from an
    index of prose, so prose never travels by reference.
    """
    context = {"creative_direction": {"note": "y" * 6000}}
    out, referenced = _apply(context, tmp_path)

    assert referenced == []
    assert "y" * 100 in json_to_toon(out)


def test_a_table_whose_map_would_cost_more_than_the_table_stays_inline(
        tmp_path):
    """A map that costs as much as the thing it maps is a worse prompt."""
    rows = [{"id": f"an extremely long identifier number {index}",
             "value": "x" * 30} for index in range(40)]
    context = {"table": rows}
    out, referenced = _apply(context, tmp_path)

    assert referenced == []
    assert "an extremely long identifier number 0" in json_to_toon(out)


def test_the_map_names_the_line_range_that_holds_each_group(tmp_path):
    """The line range is the load-bearing affordance: `sed -n 'a,bp'`."""
    out, referenced = _apply(_context(), tmp_path)
    assert "picture" in referenced

    path = reference_path(out["picture"])
    lines = open(path, encoding="utf-8").read().split("\n")
    block = out["picture"]

    seen = 0
    for line in block.split("\n"):
        match = re.match(r"^\s*(?P<label>.+?): \d+ row\(s\), lines (?P<ranges>.+)$",
                         line)
        if not match:
            continue
        label = match.group("label").strip()
        for span in match.group("ranges").split(","):
            span = span.strip()
            if "-" in span:
                first, last = span.split("-")
            else:
                first = last = span
            held = "\n".join(lines[int(first) - 1:int(last)])
            assert label in held, (
                f"the map says {label} is at lines {span} and it is not")
            seen += 1
    assert seen == 60


def test_a_second_pass_does_not_wrap_a_reference_in_a_reference(tmp_path):
    """Idempotent: applying it twice leaves one reference, not two."""
    once, _ = _apply(_context(), tmp_path)
    twice, referenced = _apply(once, tmp_path)

    assert referenced == []
    assert twice["picture"] == once["picture"]


def test_an_unknown_harness_keeps_the_bytes_inline(tmp_path):
    """An unestablished harness is not assumed to reach anything."""
    out, referenced = _apply(_context(), tmp_path, harness="carrier-pigeon")

    assert referenced == []
    assert "a person is doing something in clip 0" in json_to_toon(out)


def test_a_section_the_engine_cannot_render_stays_inline(tmp_path):
    """A failure to render is not a reason to lose the bytes."""
    class Unrenderable:
        pass

    context = {"weird": Unrenderable()}
    out, referenced = _apply(context, tmp_path)

    assert referenced == []
    assert "weird" in out


def test_the_run_archive_carries_the_referenced_table(tmp_path):
    """An archived request that names a file the archive does not carry
    is an archived request nobody can rebuild."""
    from library.tools.versions import runs

    project = tmp_path / "proj"
    (project / "pipeline_output").mkdir(parents=True)
    out, referenced = apply_references(_context(), str(project), "agent")
    assert referenced

    archive = runs.archive_previous_run(str(project), "run-1")
    carried = archive / "llm_requests" / "refs"
    assert carried.is_dir(), "the archive dropped the referenced tables"
    assert any(path.is_file() for path in carried.iterdir())
