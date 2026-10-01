"""Step 3.04's repeated-take evidence reaches the prompt as a REFERENCE.

The step's `context_fields` drop `repetition_inside`, `retake_candidates`
and `possible_retellings` from every candidate row - 19,850 of 49,777
tokens on the geo podcast. A drop path is unconditional, so the defect
this guards is the silent one: the fields leave the prompt and nothing
puts them anywhere the model can reach.

The test FOLLOWS the reference the way `tests/test_brief_reference.py`
does: it projects the bridge's real output with the step's real
`context_fields`, parses the path and each line range out of the string
the model reads, and requires every dropped item to be at its own
candidate's section, verbatim.
"""
import json
import re
from pathlib import Path

from library.tools.brief_reference import REFERENCED_INPUTS, reference_path
from library.tools.context_projector import project_fields
from library.tools.reel_diagnostics_reference import FIELDS

REPO = Path(__file__).resolve().parents[1]
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
