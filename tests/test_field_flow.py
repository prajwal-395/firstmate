"""The analyzer, held to the distinction it exists to make.

`library/tools/field_flow.py` answers "which FIELD of which document does
this line read".  Everything downstream of it - `data_map`, the ranking
of what the pipeline carries and does not use, the gate - is only worth
what this is worth, so it is tested against modules written HERE rather
than against the repository's own moving contents.

Two properties matter more than any other, and they are the two the
repository has already been burned by:

* a WRITE is not a read (#601 credited six outputs to lines writing
  their own key);
* a name match is not a read (`output_contract.KNOWN_NAME_COLLISIONS`).

Every test builds its module under `tmp_path`.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import field_flow  # noqa: E402


def _analyse(tmp_path, source, documents=None, **kwargs):
    module = tmp_path / "subject.py"
    module.write_text(source, encoding="utf-8")
    seeds = field_flow.Seeds(
        documents=documents if documents is not None else {
            "widget": ["widget.json"]},
        **kwargs)
    return field_flow.analyse(seeds, files=[module])


def _tags(world):
    return {access.tag for access in world.accesses}


def _by_tag(world):
    found = {}
    for access in world.accesses:
        found.setdefault(access.tag, []).append(access)
    return found


# ── The two distinctions the instrument exists for ───────────────────

def test_a_dict_literal_key_is_a_write_and_not_a_read(tmp_path):
    """#601's defect, made unrepeatable at field level.

    `{"size": total}` names `size` and reads nothing.  The survey that
    preceded this credited `scan.total_files` to exactly that shape in
    an unrelated module.
    """
    world = _analyse(tmp_path, '''
import json
from pathlib import Path

def use(folder):
    doc = json.loads((Path(folder) / "widget.json").read_text())
    return {"size": doc["height"], "height": 0, "name": "x"}
''')
    tags = _tags(world)
    assert "DOC@widget#height" in tags, (
        "the subscript on the document is a read and must be recorded")
    assert "DOC@widget#size" not in tags, (
        "`size` appears only as a dict-literal KEY, which is a write")
    assert "DOC@widget#name" not in tags


def test_a_name_match_off_an_unrelated_dict_is_not_a_read(tmp_path):
    """The half `output_contract` could not close.

    Two dicts, one field name.  A survey built on names would credit the
    document; this one follows the value and credits nothing.
    """
    world = _analyse(tmp_path, '''
import json
from pathlib import Path

def use(folder, unrelated):
    doc = json.loads((Path(folder) / "widget.json").read_text())
    stranger = dict(unrelated)
    a = stranger["source"]
    b = stranger.get("start")
    return doc["height"], a, b
''')
    tags = _tags(world)
    assert "DOC@widget#height" in tags
    assert "DOC@widget#source" not in tags
    assert "DOC@widget#start" not in tags


# ── What the analyzer has to follow to see this repository at all ────

def test_a_lookup_built_by_comprehension_carries_the_document(tmp_path):
    """`{c["clip_id"]: c for c in clip_catalog}`.

    The single most common idiom in this tree, and the reason
    `field_flow.CONTAINER` exists: step 3.01 builds one and then reads
    `width`, `height`, `rotation` and `frame_rate` off it.
    """
    world = _analyse(tmp_path, '''
import json
from pathlib import Path

def use(folder, wanted):
    doc = json.loads((Path(folder) / "widget.json").read_text())
    lookup = {item["id"]: item for item in doc["items"]}
    chosen = lookup.get(wanted)
    return chosen.get("width", 0)
''')
    tags = _tags(world)
    assert "DOC@widget#items[].width" in tags, (
        "the read through the lookup must reach the document")
    assert "DOC@widget#items[].id" in tags
    for tag in tags:
        assert not tag.endswith("#wanted"), (
            "the KEY a lookup is indexed by is data, never a field name")


def test_a_key_held_in_a_parameter_is_resolved_from_its_callers(tmp_path):
    """`beat_grid._times(analysis, key)` with `"beats"` at the call site.

    Without this the beat grid - the whole reason `music_analysis.tempo`
    is measured - reads as an unread field.
    """
    world = _analyse(tmp_path, '''
import json
from pathlib import Path

def _times(doc, key):
    return doc["tempo"].get(key) or []

def beats(folder):
    doc = json.loads((Path(folder) / "widget.json").read_text())
    return _times(doc, "beats"), _times(doc, "downbeats")
''')
    tags = _tags(world)
    assert "DOC@widget#tempo.beats" in tags
    assert "DOC@widget#tempo.downbeats" in tags


# ── The SHAPE, which is the answer to "what breaks" ──────────────────

def test_every_shape_is_recorded_with_its_own_consequence(tmp_path):
    world = _analyse(tmp_path, '''
import json
from pathlib import Path

def require_keys(data, keys):
    for key in keys:
        if key not in data:
            raise KeyError(key)

def use(folder):
    doc = json.loads((Path(folder) / "widget.json").read_text())
    require_keys(doc, ["must_exist"])
    hard = doc["raises"]
    soft = doc.get("silent")
    defaulted = doc.get("substituted", 30.0)
    if "branch" in doc:
        pass
    return hard, soft, defaulted
''')
    by_tag = _by_tag(world)
    shapes = {tag: {a.shape for a in accesses}
              for tag, accesses in by_tag.items()}
    assert shapes["DOC@widget#raises"] == {field_flow.SUBSCRIPT}
    assert shapes["DOC@widget#silent"] == {field_flow.GET}
    assert shapes["DOC@widget#substituted"] == {field_flow.GET_DEFAULT}
    assert shapes["DOC@widget#branch"] == {field_flow.CONTAINS}
    assert shapes["DOC@widget#must_exist"] == {field_flow.REQUIRE}

    substituted = by_tag["DOC@widget#substituted"][0]
    assert substituted.default == "30.0", (
        "the substituted value is what makes a creative fallback "
        "visible (AGENTS.md 10.5)")
    assert field_flow.BREAKS[field_flow.SUBSCRIPT] != \
        field_flow.BREAKS[field_flow.GET_DEFAULT], (
        "the shapes must not all mean the same thing")


def test_the_step_inputs_seed_only_fires_inside_a_step_directory(tmp_path):
    """`json.loads(sys.stdin.read())` is the runner handing a step its
    inputs.  Outside `library/steps/` it is somebody else's stdin."""
    module = tmp_path / "subject.py"
    module.write_text('''
import json, sys

def main():
    data = json.loads(sys.stdin.read())
    return data["clip_catalog"]
''', encoding="utf-8")
    world = field_flow.analyse(
        field_flow.Seeds(documents={}, step_dirs=["step_9_99_fake"]),
        files=[module])
    assert not _tags(world), (
        "a module outside a step directory must not seed step inputs")


# ── The bounds, which must be visible rather than silent ─────────────

def test_the_analyzer_bounds_are_counted_rather_than_silent(tmp_path):
    """A path deeper than anything real is dropped, and it SAYS so."""
    world = _analyse(tmp_path, '''
import json
from pathlib import Path

def use(folder):
    doc = json.loads((Path(folder) / "widget.json").read_text())
    return doc["a"]["b"]["c"]["d"]["e"]["f"]["g"]["h"]["i"]["j"]["k"]["l"]["m"]
''')
    assert world.too_deep > 0, (
        "a 13-segment path is past MAX_PATH_SEGMENTS and must be counted")
    assert field_flow.depth("DOC@x#a.b[].c") == 4
