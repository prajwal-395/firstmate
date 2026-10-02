"""Step 4.04's post-bridge places what the plan asked for, and refuses what it cannot read.

Layering is legal, a sound's measured envelope keys its placement, a
`role: "layer"` plays under speech, and a plan key or role nothing reads
is refused or dropped by name. History: docs/evidence/sfx.md.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
import re
from library.tools.music_behavior import MusicBehaviorError
from library.tools.music_measurement import (
    bed_level_after_gain,
    bed_reading,
    bed_under_block,
)
from library.steps.step_5_04_compile_manifest.step import main as compile_manifest_main
from library.steps.step_4_04_plan_sfx.bridge import (
    build_sfx_candidates,
)


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.steps.step_4_04_plan_sfx.post_bridge import (  # noqa: E402
    UnplayableSfxPlan,
    resolve_sfx,
)
from library.tools import sfx_envelope as se  # noqa: E402
from library.tools.plan_keys import UnreadPlanKey  # noqa: E402

SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"

RISER = "test_riser.wav"
IMPACT = "test_impact.wav"
DRONE = "test_drone.wav"


@pytest.fixture
def sfx_library(tmp_path):
    """A riser, an impact and a drone - never the captain's."""
    lib = tmp_path / "sfx_library"
    lib.mkdir()
    entries = []
    for name, duration, shape in ((RISER, 4.0, "swelling"),
                                  (IMPACT, 0.3, "punchy"),
                                  (DRONE, 8.0, "sustained")):
        audio = lib / name
        audio.write_bytes(b"RIFF....WAVEfmt ")
        entries.append({
            "file": name,
            "path": str(audio),
            "folder_category": "Accents",
            "description": f"a {shape} test sound",
            "technical": {
                "basic": {"duration": duration},
                "energy_profile": {"envelope_shape": shape},
            },
            "transient_offset_sec": 0.0,
        })
    (lib / "sfx_index.json").write_text(json.dumps(entries))
    return {"PIPELINE_SFX_LIBRARY": str(lib)}


def _block(position, tl_start, tl_end, words=()):
    """One speech block with word ends at the given TIMELINE seconds."""
    return {
        "position": position, "block_type": "speech",
        "clip_id": "clip_001",
        "source_start": tl_start, "source_end": tl_end,
        "timeline_start": float(tl_start), "timeline_end": float(tl_end),
        "word_timestamps": [{"source_end": float(w)} for w in words],
        "alignment_method": "whisperx",
    }


def _payload(plan, blocks=None, peaks=(1.5,), onsets=(0.2,)):
    return {
        "sfx_creative": plan,
        "timed_spine": {"structure": blocks or [_block(1, 0, 6),
                                                _block(2, 6, 12)]},
        "temporal_event_indices": [{
            "clip_id": "clip_001",
            "onset_times": list(onsets),
            "energy_curve": {"peak_times": list(peaks)},
            "scene_boundaries": [],
        }],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }


def _run(payload, sfx_library):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(sfx_library)
    proc = subprocess.run(
        [sys.executable, str(SFX / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env, check=False)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)["sfx_spec"]["sfx_list"], proc.stderr


# ── Layering and the envelope ─────────────────────────────────────────

def test_two_sounds_on_one_block_survive_as_two_placements(sfx_library):
    """Layering is legal, and the collapse check does not catch it: a
    collapse is ONE distinct position across the whole plan."""
    placed, _ = _run(_payload([
        {"spine_block_position": 1, "sfx_id": RISER, "volume_db": -14,
         "rationale": "the build under the line"},
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "the weight it arrives on"},
        {"spine_block_position": 2, "sfx_id": IMPACT, "volume_db": -10,
         "rationale": "the answering hit"},
    ]), sfx_library)

    assert len(placed) == 3
    on_block_one = [s for s in placed if s["spine_block_position"] == 1]
    assert len(on_block_one) == 2
    assert {s["sfx_id"] for s in on_block_one} == {RISER, IMPACT}
    # Each layer carries its own level - not one bus level for the pair.
    assert sorted(s["volume_db"] for s in on_block_one) == [-14.0, -8.0]


def test_moments_planned_on_different_blocks_cannot_collapse_to_one_position():
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _assert_sfx_distributed,
    )

    with pytest.raises(ValueError, match="collapse"):
        _assert_sfx_distributed([
            {"label": "sfx_001", "timeline_in": 6.0,
             "spine_block_position": 1},
            {"label": "sfx_002", "timeline_in": 6.0,
             "spine_block_position": 4},
        ])


def test_the_envelope_decides_which_end_is_anchored(sfx_library):
    """A swelling sound ENDS on the peak - that is what a build is; a
    punchy one STARTS on the transient (the 0.2 s onset)."""
    for sfx_id, peak, envelope, timeline_in, timeline_out in (
            (RISER, 5.0, "swelling", 1.0, 5.0),
            (IMPACT, 1.5, "punchy", 0.2, None)):
        placed, _ = _run(_payload([
            {"spine_block_position": 1, "sfx_id": sfx_id, "volume_db": -14,
             "rationale": "on the moment"},
        ], peaks=(peak,)), sfx_library)
        entry = placed[0]
        assert entry["sfx_envelope"] == envelope
        assert entry["timeline_in"] == pytest.approx(timeline_in, abs=0.05)
        if timeline_out is not None:
            assert entry["timeline_out"] == pytest.approx(
                timeline_out, abs=0.05)


def test_the_placement_code_and_the_prompt_read_the_same_table():
    """The sentence the manifest records and the one the planner reads
    are rendered from one table, so they cannot drift."""
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _describe_placement,
    )
    for envelope in se.ENVELOPES:
        assert _describe_placement(envelope.shape) == envelope.engine_does
        assert envelope.engine_does in se.envelope_legend()[envelope.shape]
    # An unmeasured shape is an absence, not a fifth row.
    assert _describe_placement("") == se.UNMEASURED_PLACEMENT
    assert se.UNMEASURED not in se.ENVELOPES_BY_SHAPE


# ── Atmospheric layers (captain's ruling 2026-09-08) ──────────────────

def test_layer_plays_under_speech_instead_of_being_shifted_off_it(
        sfx_library):
    """A literal sound is moved into the word gap at 2.0; a reasoned
    layer stays at the envelope-placed position, under the words."""
    words = [round(0.1 * i, 3) for i in range(1, 21)]
    placed, _ = _run(_payload([
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "a hit on the opening"},
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "role": "layer",
         "rationale": "a textural tick under the opening words"},
    ], [_block(1, 0, 12, words)], peaks=(), onsets=()), sfx_library)
    assert len(placed) == 2
    literal = [s for s in placed if s.get("role") != "layer"]
    layer = [s for s in placed if s.get("role") == "layer"]
    assert len(literal) == 1 and len(layer) == 1
    assert literal[0]["timeline_in"] == 2.0
    assert layer[0]["timeline_in"] == 0.0
    assert layer[0]["duration_seconds"] == 0.3


def test_layer_may_span_past_its_own_block(sfx_library):
    """An 8s drone placed on a 4s block plays all 8s."""
    placed, _ = _run(_payload([
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
         "role": "layer",
         "rationale": "a bed that carries across the cut"},
    ], [_block(1, 0, 4), _block(2, 4, 8)], peaks=(), onsets=()),
        sfx_library)
    assert len(placed) == 1
    assert placed[0]["timeline_in"] == 0.0
    assert placed[0]["timeline_out"] == 8.0


def test_an_unreasoned_or_unread_role_is_dropped_by_name(sfx_library):
    """A layer is tied to no visible event, so without a reason it goes;
    a role nothing reads would ship a placement nobody asked for."""
    for entry, said in (
            ({"role": "layer"}, "states no reason"),
            ({"role": "sting", "rationale": "a sting on the cut"}, "sting")):
        placed, stderr = _run(_payload([
            {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
             **entry},
        ], [_block(1, 0, 12)], peaks=(), onsets=()), sfx_library)
        assert placed == []
        assert said in stderr


# ── Unread plan keys ──────────────────────────────────────────────────

def test_an_unread_plan_key_refuses_loudly():
    """Measured defect: `at_word` was read by nothing and the whoosh
    landed 3.06 s early on the block start."""
    plan = [{
        "sfx_id": "whoosh_impact",
        "spine_block_position": 3,
        "volume_db": -10.0,
        "rationale": "marks the cut",
        "at_word": "watch",
    }]
    with pytest.raises(UnreadPlanKey) as excinfo:
        resolve_sfx(plan, {}, [], {}, {}, 30.0, {}, {}, catalog=[])
    message = str(excinfo.value)
    assert "at_word" in message
    assert "sfx_creative" in message
    for key in ("sfx_id", "spine_block_position", "volume_db"):
        assert key in message


def test_legacy_position_spellings_pass_the_key_gate():
    plan = [{
        "sfx_id": "no-such-sound",
        "target_block_position": 2,
        "timeline_start": 1.5,
        "volume_db": -10.0,
        "rationale": "marks the cut",
    }]
    # Past the key gate: it fails later, on the unplayable id.
    with pytest.raises(UnplayableSfxPlan):
        resolve_sfx(plan, {"structure": []}, [], {}, {}, 30.0, {},
                    {}, catalog=[])


# --------------------------------------------------------------------------
# From test_sfx_catalogue_by_reference.py
#
# The SFX catalogue reaches the prompt as a REFERENCE, not as a copy.
#
# The bridge writes the catalogue into its own step directory and puts
# `brief_reference`'s map in the prompt; every sound stays reachable by its
# exact `sfx_id`, and a harness that cannot follow a path gets the document
# whole. This FOLLOWS the reference rather than asserting its shape.
# Measured sizes: docs/RULE_EVIDENCE.md#the-catalogue-was-copied-into-the-prompt.

SFX_STEP = REPO / "library" / "steps" / "step_4_04_plan_sfx"

from library.tools.brief_reference import (  # noqa: E402
    reference_path,
    restore_for_harness,
)
from library.tools.sfx_library import (  # noqa: E402
    CATALOG_DOCUMENT_NAME,
    catalog_document,
    load_sfx_catalog,
)

# Enough sounds that the difference between a map and a copy is real,
# and each carrying the prose that made #298 worth doing.
SOUNDS = [
    ("whoosh_impact.mp3", "Whoosh", 8.04, "punchy", "cold tense"),
    ("riser_2.mp3", "Risers", 5.317, "swelling", "cold tense"),
    ("camera soft click.wav", "Camera Shutter", 0.46, "fading",
     "neutral calm"),
    ("Alien_racecar.wav", "Risers", 5.69, "fading", "cold tense"),
    ("Paper_crinkle_04.wav", "not really sure how to use these", 2.62,
     "swelling", "neutral calm"),
]


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "sfx"
    lib.mkdir()
    index, semantic = [], []
    for name, category, duration, envelope, temperature in SOUNDS:
        audio = lib / name
        audio.write_bytes(b"RIFF....WAVEfmt ")
        index.append({
            "file": name,
            "path": str(audio),
            "folder_category": category,
            "technical": {
                "basic": {"duration": duration},
                "energy_profile": {"envelope_shape": envelope},
            },
            "transient_offset_sec": 0.1,
        })
        semantic.append({
            "file": name,
            "description": f"A {envelope} {category} sound, and it doesn't "
                           f"ring on. " + "Body prose. " * 12,
            "source_object": "a thing being moved",
            "evokes": ["speed", "weight"],
            "emotional_temperature": temperature,
            "works_when": "Use it on a hard cut. " + "Because. " * 10,
            "avoid_when": "Avoid it under a quiet line. " + "Because. " * 10,
        })
    (lib / "sfx_index.json").write_text(json.dumps(index), encoding="utf-8")
    (lib / "library_semantic.json").write_text(json.dumps(semantic),
                                               encoding="utf-8")
    return lib


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def _run_bridge(library, project):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PIPELINE_SFX_LIBRARY"] = str(library)
    proc = subprocess.run(
        [sys.executable, str(SFX_STEP / "bridge.py")],
        input=json.dumps({
            "project_folder": str(project),
            "timed_spine": {"structure": []},
            "temporal_event_indices": [],
            "transition_spec": [],
            "project_fps": 30.0,
        }),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env,
    )
    return proc


def _document_path(project):
    return (project / "pipeline_output" / "steps" / "4_04_plan_sfx"
            / CATALOG_DOCUMENT_NAME)


# ── The bridge writes the document and points at it ───────────────────

def test_the_bridge_writes_the_catalogue_into_its_own_step_directory(
        library, project):
    proc = _run_bridge(library, project)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    path = _document_path(project)
    assert path.exists(), sorted(project.rglob("*"))

    out = json.loads(proc.stdout)
    assert reference_path(out["sfx_catalog_reference"]) == str(path.resolve())
    # It never hands the step its own empty output back as input (an
    # empty `sfx_spec` read as a plan that had already placed nothing).
    assert "sfx_spec" not in out


def test_the_bridge_refuses_when_there_is_nowhere_to_write_it(library):
    """No project folder means no path to point at, and no quiet copy."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PIPELINE_SFX_LIBRARY"] = str(library)
    proc = subprocess.run(
        [sys.executable, str(SFX_STEP / "bridge.py")],
        input=json.dumps({"timed_spine": {"structure": []}}),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env,
    )
    assert proc.returncode == 1
    assert "project_folder" in json.loads(proc.stdout)["error"]


def _ranges(reference: str) -> dict:
    return dict(re.findall(r"^## (.+?)  \[[\d,]+ B, lines (\d+-\d+)\]$",
                           reference, flags=re.M))


# ── Every sound is still reachable ────────────────────────────────────

def test_following_the_range_returns_prose_the_prompt_did_not_carry(
        library, project):
    """The load-bearing affordance: a heading, a size and a LINE RANGE."""
    proc = _run_bridge(library, project)
    reference = json.loads(proc.stdout)["sfx_catalog_reference"]
    path = Path(reference_path(reference))
    lines = path.read_text(encoding="utf-8").split("\n")

    # Every sound is named by the exact id an answer must use - a
    # reference that narrowed the menu would be the shortlist again.
    ranges = _ranges(reference)
    assert set(ranges) == {name for name, *_ in SOUNDS}
    assert {e["sfx_id"] for e in load_sfx_catalog(str(library))} == set(ranges)

    for name, span in ranges.items():
        first, last = (int(n) for n in span.split("-"))
        section = "\n".join(lines[first - 1:last])
        assert section.startswith(f"## {name}")
        avoid = next(l for l in section.splitlines()
                     if l.startswith("- avoid when:"))
        assert avoid not in reference, (
            f"{name}'s avoid_when is in the prompt as well as behind the "
            f"reference, so the indirection bought nothing")


# ── Clause 5: a harness that cannot follow a path ─────────────────────

def test_a_harness_that_cannot_read_a_file_gets_the_whole_catalogue(
        library, project):
    proc = _run_bridge(library, project)
    inputs = {"sfx_catalog_reference":
              json.loads(proc.stdout)["sfx_catalog_reference"]}

    restored, keys = restore_for_harness(inputs, "api")
    assert keys == ["sfx_catalog_reference"]
    assert restored["sfx_catalog_reference"] == catalog_document(
        load_sfx_catalog(str(library)))

    unchanged, keys = restore_for_harness(inputs, "agent")
    assert keys == []
    assert unchanged is inputs


# --------------------------------------------------------------------------
# From test_sfx_choice_from_the_catalogue.py
#
# The sound is chosen by the model, out of the library's own catalogue.
#
# The catalogue carries what the library records about each on-disk sound
# (all three index files); an id resolves exactly or not at all, and a plan
# naming a sound the library has not got fails at plan time, in step 4.04.

from library.tools.sfx_envelope import placement_of  # noqa: E402
from library.tools.sfx_library import (  # noqa: E402
    resolve_sfx_id,
)


# ── A library on tmp_path.  Never the captain's, never the shared one. ──

def _library(tmp_path, with_audio=True):
    lib = tmp_path / "sfx"
    lib.mkdir()
    (lib / "profiles").mkdir()

    present = lib / "present.wav"
    builder = lib / "builder.wav"
    if with_audio:
        present.write_bytes(b"RIFF....WAVEfmt ")
        builder.write_bytes(b"RIFF....WAVEfmt ")
    absent = lib / "gone.wav"

    (lib / "sfx_index.json").write_text(json.dumps([
        {
            "file": "present.wav",
            "path": str(present),
            "folder_category": "Accents",
            # EMPTY, the way 48 of the captain's 78 entries are. The
            # keyword matcher read this field and nothing else.
            "description": "",
            "technical": {
                "basic": {"duration": 0.4},
                "energy_profile": {"envelope_shape": "punchy"},
            },
            "transient_offset_sec": 0.05,
        },
        {
            "file": "builder.wav",
            "path": str(builder),
            "folder_category": "Risers",
            "description": "a build",
            "technical": {
                "basic": {"duration": 2.0},
                "energy_profile": {"envelope_shape": "swelling"},
            },
            # Its loudest moment is its climax, near the end.
            "transient_offset_sec": 1.7,
        },
        {
            "file": "gone.wav",
            "path": str(absent),
            "folder_category": "Accents",
            "description": "a sound whose file is not here",
            "technical": {"basic": {"duration": 1.0}},
        },
    ]), encoding="utf-8")

    (lib / "library_semantic.json").write_text(json.dumps([
        {
            "file": "present.wav",
            "description": "A dry, close snap, and it doesn't ring on.",
            "source_object": "a latch closing",
            "evokes": ["finality", "precision"],
            "emotional_temperature": "warm calm",
            "works_when": "Use this to punctuate a decision.",
            "avoid_when": "Avoid using this over speech.",
        },
    ]), encoding="utf-8")
    return lib


# ── What the model is offered ─────────────────────────────────────────

def test_the_catalogue_merges_all_three_index_files(tmp_path):
    """The semantic index describes what `sfx_index.json` left blank."""
    entry = load_sfx_catalog(str(_library(tmp_path)))[0]

    assert entry["sfx_id"] == "present.wav"
    assert entry["description"].startswith("A dry, close snap")
    assert entry["source_object"] == "a latch closing"
    assert entry["evokes"] == ["finality", "precision"]
    assert entry["works_when"] == "Use this to punctuate a decision."
    assert entry["avoid_when"] == "Avoid using this over speech."
    # and the measurements the index carries
    assert entry["duration_seconds"] == 0.4
    assert entry["envelope"] == "punchy"
    assert entry["transient_offset_sec"] == 0.05


def test_only_an_on_disk_sound_is_offered_and_an_id_resolves_exactly(
        tmp_path):
    """`gone.wav` is indexed but not on disk, so the model cannot name
    it; and a near miss never resolves - a nearest match is a chooser."""
    catalog = load_sfx_catalog(str(_library(tmp_path)))
    assert [e["sfx_id"] for e in catalog] == ["present.wav", "builder.wav"]
    assert resolve_sfx_id("present.wav", catalog)["path"].endswith(
        "present.wav")
    for near_miss in ("gone.wav", "present", "Present.wav", "present.mp3",
                      "", None):
        assert resolve_sfx_id(near_miss, catalog) is None, near_miss


def test_a_description_with_a_comma_and_an_apostrophe_needs_no_escaping(
        tmp_path):
    """Every field is its own line, so nothing is quoted at all.

    As a TOON table this had to be quoted with a backtick, because a
    tab-joined row would put "and it doesn't ring on" into the next
    column and the apostrophe would be doubled by any dialect quoting on
    `'` (AGENTS.md 10.1). A markdown line has no cell to break.
    """
    document = catalog_document(load_sfx_catalog(str(_library(tmp_path))))
    assert "doesn't" in document
    assert "doesn''t" not in document
    assert ("- description: A dry, close snap, and it doesn't ring on."
            in document)


# ── A plan naming an unplayable sound fails at PLAN time ──────────────

def _spine(n_blocks):
    return {"structure": [
        {"position": i + 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": i * 2.0, "source_end": i * 2.0 + 2.0,
         "timeline_start": i * 2.0, "timeline_end": i * 2.0 + 2.0,
         "word_timestamps": [], "alignment_method": "whisperx"}
        for i in range(n_blocks)]}


def _run_post_bridge(payload, library):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PIPELINE_SFX_LIBRARY"] = str(library)
    return subprocess.run(
        [sys.executable, str(SFX_STEP / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env)


def _payload_2(entries):
    return {
        "sfx_creative": entries,
        "timed_spine": _spine(2),
        "temporal_event_indices": [],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }


def test_a_plan_naming_a_sound_the_library_has_not_got_fails_here(tmp_path):
    library = _library(tmp_path)
    proc = _run_post_bridge(_payload_2([
        {"spine_block_position": 1, "sfx_id": "present.wav",
         "volume_db": -18, "rationale": "punctuates the decision"},
        {"spine_block_position": 2, "sfx_id": "reverse_cymbal",
         "volume_db": -14, "rationale": "marks the section"},
    ]), library)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    error = json.loads(proc.stdout)["error"]
    assert "reverse_cymbal" in error
    assert "1 of 2" in error
    # The good entry is not placed either: the plan is refused whole, so
    # nothing ships an edit that is quietly missing a planned sound.
    assert "sfx_spec" not in proc.stdout


def test_a_plan_naming_real_sounds_resolves_to_real_files(tmp_path):
    library = _library(tmp_path)
    proc = _run_post_bridge(_payload_2([
        {"spine_block_position": 1, "sfx_id": "present.wav",
         "volume_db": -18, "rationale": "punctuates the decision"},
    ]), library)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed, = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed["sfx_id"] == "present.wav"
    assert placed["source_file"] == str(library / "present.wav")
    assert os.path.exists(placed["source_file"])
    # Placement is keyed on the measured envelope. `punchy` means the
    # file's own transient lands on `timeline_in`, so playback starts
    # there and what plays is what is LEFT of the sound.
    assert placed["sfx_envelope"] == "punchy"
    assert placed["placement_method"] == placement_of("punchy")
    assert placed["source_in"] == 0.05
    assert placed["duration_seconds"] == pytest.approx(0.35)
    assert placed["timeline_out"] - placed["timeline_in"] == pytest.approx(0.35)


# --------------------------------------------------------------------------
# From test_sfx_duration.py
#
# The plan says how long a sound plays, inside what the sound measures, and how it fades.
#
# A requested length past what the file measures is REFUSED BY NAME and
# never clamped; no length plays the whole sound; a sound cut short or
# given a stated fade carries its ramp to the OTIO keyframes. The fixture
# carries `whoosh_impact.mp3`'s real measured numbers from the run of
# record. History: docs/evidence/sfx_duration.md.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.otio_mix import (  # noqa: E402
    MIN_VOLUME_DB,
    declick_curve,
    mix_targets,
)
from library.tools.sfx_duration import (  # noqa: E402
    DECLICK_FADE_FRAMES,
    SfxDurationRefused,
    declick_fade_seconds,
    resolve_played_seconds,
)

# The run of record, exactly.
WHOOSH = "whoosh_impact.mp3"
WHOOSH_SECONDS = 8.04
WHOOSH_TRANSIENT = 0.714
RUN_OF_RECORD_REQUEST = 0.25
FPS = 30.0


@pytest.fixture
def entry(tmp_path):
    """`whoosh_impact.mp3`'s measurements, on a library of our own."""
    lib = tmp_path / "sfx"
    lib.mkdir()
    audio = lib / WHOOSH
    audio.write_bytes(b"ID3\x03\x00\x00\x00")
    (lib / "sfx_index.json").write_text(json.dumps([{
        "file": WHOOSH,
        "path": str(audio),
        "folder_category": "Whoosh",
        "description": "A sharp air movement resolving into a low impact.",
        "technical": {
            "basic": {"duration": WHOOSH_SECONDS},
            "energy_profile": {"envelope_shape": "punchy"},
        },
        "transient_offset_sec": WHOOSH_TRANSIENT,
    }]), encoding="utf-8")
    catalog = load_sfx_catalog(str(lib))
    assert len(catalog) == 1
    return catalog[0]


# ── The bound ─────────────────────────────────────────────────────────

def test_a_stated_length_is_honoured_and_no_length_plays_the_whole_sound(
        entry):
    """0.25 s of an 8.04 s whoosh is what the plan asked for; declaring
    no length is the ABSENCE of a decision."""
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, RUN_OF_RECORD_REQUEST, FPS) == \
        RUN_OF_RECORD_REQUEST
    assert resolve_played_seconds(entry, 0.0, None, FPS) == WHOOSH_SECONDS
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, None, FPS) == pytest.approx(
            WHOOSH_SECONDS - WHOOSH_TRANSIENT)


def test_a_length_past_the_measured_one_is_refused_not_clamped(entry):
    with pytest.raises(SfxDurationRefused) as raised:
        resolve_played_seconds(entry, 0.0, WHOOSH_SECONDS + 0.5, FPS)
    message = str(raised.value)
    assert WHOOSH in message
    assert "8.04" in message
    assert "Nothing is clamped" in message


def test_the_transient_trim_is_inside_the_bound(entry):
    """`punchy` skips 0.714 s, so 8.04 s is no longer askable."""
    with pytest.raises(SfxDurationRefused) as raised:
        resolve_played_seconds(entry, WHOOSH_TRANSIENT, WHOOSH_SECONDS, FPS)
    assert "after the transient trim" in str(raised.value)
    remaining = WHOOSH_SECONDS - WHOOSH_TRANSIENT
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, remaining, FPS) == pytest.approx(remaining)


def test_the_catalogue_advertised_duration_is_accepted_by_the_contract():
    """Regression: a model copying the catalogue's advertised length was
    refused, because the catalogue rounded UP past the measured length
    (0.4599999 formatted as 0.46)."""
    catalog = [
        {"sfx_id": "camera soft click.wav",
         "duration_seconds": 0.4591609977324263, "category": "Foley"},
        {"sfx_id": "camera-shutter-6305.mp3",
         "duration_seconds": 0.3395918367346939, "category": "Foley"},
        {"sfx_id": "edge-case.wav",
         "duration_seconds": 0.4599999, "category": "Foley"},
    ]
    doc = catalog_document(catalog)
    for row in catalog:
        match = re.search(re.escape(row["sfx_id"])
                          + r".*?category Foley \| plays for ([\d\.]+) s",
                          doc, re.DOTALL)
        assert match, f"no identity line for {row['sfx_id']} in:\n{doc}"
        advertised = float(match.group(1))
        assert resolve_played_seconds(row, 0.0, advertised, 30.0) == \
            advertised


# ── The click ─────────────────────────────────────────────────────────

def test_a_truncated_sound_gets_a_ramp_and_a_whole_one_does_not():
    playable = WHOOSH_SECONDS - WHOOSH_TRANSIENT
    cut_short = declick_fade_seconds(RUN_OF_RECORD_REQUEST, playable, FPS)
    assert cut_short == pytest.approx(DECLICK_FADE_FRAMES / FPS)
    assert declick_fade_seconds(playable, playable, FPS) == 0.0


def test_the_manifest_carries_the_ramp_to_the_mix_route():
    """Manifest -> `mix_targets` -> keyframes, which is the real seam
    (AGENTS.md 10.2): a dB's reader is `otio_mix`."""
    frames = int(round(RUN_OF_RECORD_REQUEST * FPS))
    manifest = {"tracks": {"A3": {"clips": [
        {"source_file": "/sfx/whoosh_impact.mp3", "label": "sfx_001",
         "timeline_in_frame": 1038, "timeline_out_frame": 1038 + frames,
         "volume_db": -18, "fade_out_seconds": DECLICK_FADE_FRAMES / FPS},
        {"source_file": "/sfx/riser_2.mp3", "label": "sfx_002",
         "timeline_in_frame": 1384, "timeline_out_frame": 1474,
         "volume_db": -14, "fade_out_seconds": 0.0},
    ]}}}
    cut_short, whole = mix_targets(manifest, fps=FPS)

    assert cut_short["role"] == "sfx"
    assert cut_short["keyframes"][0] == -18.0
    assert cut_short["keyframes"][frames - 1] == -100.0
    assert cut_short["keyframes"][frames - 1 - DECLICK_FADE_FRAMES] == -18.0
    # A sound that plays to its own end keeps the static level it had.
    assert whole["keyframes"] == {}
    assert whole["level_db"] == -14.0


# ── A stated fade (rung 7, E3: fade lengths in frames) ────────────────

def _fade_spine():
    return {"structure": [
        {"position": 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": 100.0, "source_end": 110.0,
         "timeline_start": 8.0, "timeline_end": 18.0,
         "word_timestamps": [
             {"word": "tell", "source_start": 100.0,
              "source_end": 100.4},
         ],
         "alignment_method": "mfa"},
    ]}


def _resolve_fade(**over):
    plan = {"sfx_id": "test_whoosh.wav", "spine_block_position": 1,
            "volume_db": -12.0, "anchor": {"word": "tell"},
            "rationale": "on the word", **over}
    catalog = [{
        "sfx_id": "test_whoosh.wav",
        "path": "/nonexistent/test_whoosh.wav",
        "category": "Accents",
        "duration_seconds": 1.2,
        "envelope": "punchy",
        "transient_offset_sec": 0.05,
    }]
    (placed,) = resolve_sfx([plan], _fade_spine(), [], {}, {}, FPS, {}, {},
                            catalog=catalog)["sfx_list"]
    return placed


def test_a_stated_fade_ships():
    for key, seconds, said in (
            ("fade_out_seconds", 0.5, "fades out over 0.500s (stated)"),
            ("fade_in_seconds", 0.25, "fades in over 0.250s (stated)")):
        placed = _resolve_fade(**{key: seconds})
        assert placed[key] == pytest.approx(seconds)
        assert said in placed["placement_method"]


def test_fade_frames_match_seconds():
    assert _resolve_fade(fade_out_frames=15)["fade_out_seconds"] == \
        pytest.approx(_resolve_fade(fade_out_seconds=0.5)["fade_out_seconds"])


def test_the_head_ramp_reaches_the_curve():
    """The stated fade-in renders as silence-to-level at the head."""
    keys = declick_curve(0.0, fps=FPS, clip_frame_count=36,
                         level_db=-12.0, fade_in_seconds=0.5)
    frames = sorted(keys)
    assert keys[frames[0]] == MIN_VOLUME_DB
    assert keys[15] == -12.0
    assert keys[frames[-1]] == -12.0


# --------------------------------------------------------------------------
# From test_sfx_hears_the_bed.py
#
# The bed's own level under each block reaches step 4.04 as DATA - a level, never a gain.
#
# The gain is decided at the mix, later. History: docs/evidence/sfx.md.

# 001's own chosen track.
MEASURED_BED = bed_reading({
    "title": 'Sickick - "Infected" (Instrumental)',
    "measurements": {"measured": True, "integrated_lufs": -15.17,
                     "speech_band_ratio_db": -8.6},
})


def test_the_bed_level_is_the_gain_applied_to_what_the_bed_measures():
    assert bed_level_after_gain(MEASURED_BED, -6) == -21.17
    # The number step 5.02 records for 001's fade_out block, exactly.
    assert bed_level_after_gain(MEASURED_BED, -12) == -27.17
    # The block the shipped sound sat on: its own level, never a gain.
    behaviour, cell = bed_under_block(
        {"block_type": "transition_slot", "position": 1,
         "music_behavior": "prominent"}, MEASURED_BED)
    assert behaviour == "prominent"
    assert "-15.17 LUFS" in cell
    assert "gain" not in cell


def test_an_unmeasured_bed_has_no_level_and_never_zero():
    empty = bed_reading({})
    assert bed_level_after_gain(empty, -18) is None
    _, cell = bed_under_block({"block_type": "speech", "position": 1}, empty)
    assert "unmeasured" in cell
    assert "0" not in cell.split("(")[0]


def test_a_behaviour_outside_the_vocabulary_raises():
    with pytest.raises(MusicBehaviorError):
        bed_under_block({"block_type": "speech", "position": 1,
                         "music_behavior": "ducked"}, MEASURED_BED)


# --------------------------------------------------------------------------
# From test_sfx_level.py
#
# The sound effect's level is the model's, and no number replaces it.
#
# An entry naming no `volume_db` is dropped with the reason; a level no clip
# can carry is refused, never clamped. History: docs/evidence/sfx.md.

REPO_2 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO_2)

from library.tools.sfx_level import (  # noqa: E402
    SfxLevelRefused,
    read_volume_db,
)

SFX_2 = os.path.join(REPO_2, "library", "steps", "step_4_04_plan_sfx")


def test_a_level_outside_what_a_clip_can_carry_is_refused_not_clamped():
    with pytest.raises(SfxLevelRefused, match="not clamped"):
        read_volume_db({"volume_db": -400})


def test_the_plan_step_really_places_the_level_the_model_wrote(tmp_path):
    """End to end through step 4.04's own post-bridge: the written level
    is placed verbatim, and the entry naming none is dropped with the
    reason rather than given one."""
    library = tmp_path / "sfx"
    library.mkdir()
    sound = library / "camera soft click.wav"
    sound.write_bytes(b"RIFF....WAVEfmt ")
    (library / "sfx_index.json").write_text(json.dumps([{
        "file": "camera soft click.wav",
        "path": str(sound),
        "folder_category": "Accents",
        "description": "a soft camera shutter",
        "technical": {
            "basic": {"duration": 0.459},
            "energy_profile": {"envelope_shape": "fading"},
        },
        "transient_offset_sec": 0.0,
    }]), encoding="utf-8")

    payload = {
        "timed_spine": {"frame_rate": 30.0, "structure": [
            {"position": 1, "block_type": "speech", "clip_id": "clip_1",
             "timeline_start": 0.0, "timeline_end": 10.0,
             "source_start": 0.117, "source_end": 10.117,
             "music_behavior": "background", "word_timestamps": [],
             "alignment_method": "whisperx",
             "content": {"clip_id": "clip_1"}}]},
        "temporal_event_indices": [], "music_analysis": {},
        "music_selection": {},
        "sfx_creative": [
            {"spine_block_position": 1, "sfx_id": "camera soft click.wav",
             "volume_db": -4, "rationale": "the shutter under the line"},
            {"spine_block_position": 1, "sfx_id": "camera soft click.wav",
             "rationale": "named no level"},
        ],
    }
    env = dict(os.environ, PIPELINE_SFX_LIBRARY=str(library))
    env["PYTHONPATH"] = REPO_2 + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, os.path.join(SFX_2, "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", env=env, cwd=REPO_2, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert [p["volume_db"] for p in placed] == [-4.0]
    assert "names no volume_db" in proc.stderr


# --------------------------------------------------------------------------
# From test_sfx_transient.py
#
# Step 5.04 PLACES the sound step 4.04 chose; it does not choose one.
#
# `source_file` and `source_in` are resolved at plan time, against the
# library catalogue, and carried. This step used to keyword-match the
# library all over again from an `sfx_type`, so the sound the model chose
# and the sound that reached A3 were two separate answers.

def _mock_catalog():
    return [
        {"sfx_id": "delayed_hit.mp3", "path": "/mock/delayed_hit.mp3",
         "category": "Impacts", "duration_seconds": 2.0,
         "envelope": "punchy", "transient_offset_sec": 0.53,
         "description": "impact boom"},
        {"sfx_id": "broken.mp3", "path": "/mock/broken.mp3",
         "category": "Impacts", "duration_seconds": 2.0,
         "envelope": "fading", "transient_offset_sec": None,
         "description": "impact broken"},
    ]


def test_sfx_transient_placement(monkeypatch, tmp_path):
    mock_sfx_index = [
        {
            "file": "delayed_hit.mp3",
            "path": "/mock/delayed_hit.mp3",
            "folder_category": "Impacts",
            "description": "impact boom",
            "technical": {
                "basic": {"duration": 2.0}
            },
            "transient_offset_sec": 0.53
        },
        {
            "file": "broken.mp3",
            "path": "/mock/broken.mp3",
            "folder_category": "Impacts",
            "description": "impact broken",
            "technical": {
                "basic": {"duration": 2.0}
            },
            "transient_offset_sec": "unknown"
        }
    ]
    
    original_exists = os.path.exists
    def mock_exists(path):
        if path in ["/mock/delayed_hit.mp3", "/mock/broken.mp3"]:
            return True
        return original_exists(path)
    monkeypatch.setattr(os.path, "exists", mock_exists)
    
    import library.steps.step_5_04_compile_manifest.step as step504
    monkeypatch.setattr(step504, "load_sfx_catalog", _mock_catalog)

    input_state = {
        "a_roll_assignments": [],
        "b_roll_assignments": [],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [],
        "enhancement_spec": {"type": "none"},
        "color_grade_spec": {"lut": "none"},
        "audio_mix_spec": {"music_level": -20},
        "music_selection": {"path": "/mock/music.mp3"},
        "audio_spine": {"structure": [], "frame_rate": 30.0},
        "clip_catalog": [{"clip_id": "c1", "path": "test.mov", "width": 1080, "height": 1920}],
        "project_fps": 30.0,
        "semantic_analysis": {"semantic_analysis_documents": []},
        "sfx_spec": [
            {
                "label": "sfx_001",
                "sfx_id": "delayed_hit.mp3",
                "source_file": "/mock/delayed_hit.mp3",
                "source_in": 0.53,
                "timeline_in": 2.40,
                "timeline_out": 2.70,
                "volume_db": -12
            },
            {
                # No `source_file`: an sfx_id alone still resolves,
                # against the catalogue, by exact id.
                "label": "sfx_002",
                "sfx_id": "broken.mp3",
                "timeline_in": 5.0,
                "timeline_out": 6.0,
                "volume_db": -12
            }
        ]
    }
    
    def mock_load(out_dir, filename):
        key = filename.split(".")[0]
        # Map step names to keys in input_state
        mapping = {
            "mesh_spine": "audio_spine",
            "assign_aroll": "a_roll_assignments",
            "select_broll": "b_roll_assignments",
            "plan_subtitles": "subtitle_plan",
            "plan_transitions": "transition_spec",
            "plan_sfx": "sfx_spec",
            "music_selection": "music_selection",
            "plan_vfx": "enhancement_spec",
            "color_grade": "color_grade_spec",
            "audio_mix": "audio_mix_spec",
            "semantic_analysis": "semantic_analysis",
            "catalog": "clip_catalog"
        }
        mapped_key = mapping.get(key)
        if mapped_key and mapped_key in input_state:
            if mapped_key == "clip_catalog":
                return {"clip_catalog": input_state["clip_catalog"]}
            if mapped_key == "audio_spine":
                return {"audio_spine": input_state["audio_spine"]}
            return input_state[mapped_key]
        return {}
        
    monkeypatch.setattr(step504, "load", mock_load)
    monkeypatch.setattr(step504, "resolve_delivery_format", lambda *args: [1080, 1920])
    
    monkeypatch.setattr(step504, "_assert_timeline_fully_covered", lambda x: None)
    manifest = step504.compile_manifest("dummy_project")
    
    assert "tracks" in manifest
    assert "A3" in manifest["tracks"]
    
    a3_clips = manifest["tracks"]["A3"]["clips"]
    assert len(a3_clips) == 2
    
    sfx_clip = a3_clips[0]
    assert sfx_clip["source_file"] == "/mock/delayed_hit.mp3"
    assert sfx_clip["source_in"] == 0.53
    assert sfx_clip["timeline_in"] == 2.40
    assert sfx_clip["timeline_out"] == 2.70

    sfx2_clip = a3_clips[1]
    assert sfx2_clip["source_file"] == "/mock/broken.mp3"
    assert sfx2_clip["source_in"] == 0.0
    assert sfx2_clip["timeline_in"] == 5.0
    assert sfx2_clip["timeline_out"] == 6.0


def test_a_plan_that_names_only_a_type_is_refused_with_the_rerun(
        monkeypatch, tmp_path):
    """A plan written before the catalogue existed carries `sfx_type`.

    Nothing here turns that back into a file - that was the keyword
    matcher. The refusal names the one command that fixes it.
    """
    import library.steps.step_5_04_compile_manifest.step as step504
    monkeypatch.setattr(step504, "load_sfx_catalog", _mock_catalog)
    monkeypatch.setattr(step504, "resolve_delivery_format",
                        lambda *args: [1080, 1920])
    monkeypatch.setattr(step504, "_assert_timeline_fully_covered",
                        lambda x: None)

    state = {
        "audio_spine": {"structure": [], "frame_rate": 30.0},
        "clip_catalog": [{"clip_id": "c1", "path": "test.mov",
                          "width": 1080, "height": 1920}],
        "sfx_spec": [{"label": "sfx_001", "sfx_type": "bass_impact",
                      "timeline_in": 2.4, "timeline_out": 2.7}],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [],
        "music_selection": {"path": "/mock/music.mp3"},
    }

    def mock_load(out_dir, filename):
        key = filename.split(".")[0]
        mapping = {
            "mesh_spine": "audio_spine",
            "plan_subtitles": "subtitle_plan",
            "plan_transitions": "transition_spec",
            "plan_sfx": "sfx_spec",
            "music_selection": "music_selection",
            "catalog": "clip_catalog",
        }
        mapped = mapping.get(key)
        if mapped == "clip_catalog":
            return {"clip_catalog": state["clip_catalog"]}
        if mapped == "audio_spine":
            return {"audio_spine": state["audio_spine"]}
        return state.get(mapped, {})

    monkeypatch.setattr(step504, "load", mock_load)

    with pytest.raises(ValueError) as raised:
        step504.compile_manifest("dummy_project")
    message = str(raised.value)
    assert "sfx_id" in message
    assert "--rerun plan_sfx" in message


# --------------------------------------------------------------------------
# From test_plan_sfx_candidate_table.py
#
# `sfx_candidates_toon` carries rows, and they come from the spine.
#
# Regression #223: the table arrived with zero rows, built from an input no
# DAG edge carries and keyed by a name the answer cannot use (AGENTS.md
# 10.1). History: docs/evidence/sfx.md.

def _spine_2():
    """Three blocks in project 001's own shape.

    A speech block whose source range covers two measured energy peaks,
    a second covering none, and a transition slot with no source clip -
    the three answers the transient column has to be able to give.
    """
    return {
        "structure": [
            {
                "position": "hook",
                "block_type": "hook",
                "clip_id": "clip_011",
                "source_start": 0.836,
                "source_end": 3.234,
                "timeline_start": 0.0,
                "timeline_end": 2.4,
                "visual_note": "Open cold on his face.",
                "content": {
                    "text": "i can feel the silent judgment.",
                    "clip_id": "clip_011",
                },
            },
            {
                "position": 1,
                "block_type": "transition_slot",
                "clip_id": None,
                "source_start": None,
                "source_end": None,
                "timeline_start": 2.4,
                "timeline_end": 5.4,
                "visual_note": "The breath after the hook.",
                "content": None,
            },
            {
                "position": 2,
                "block_type": "speech",
                "clip_id": "clip_011",
                "source_start": 9.699,
                "source_end": 12.681,
                "timeline_start": 5.4,
                "timeline_end": 8.38,
                "visual_note": "Back on him.",
                "content": {
                    "text": "today is march 25th, 2026.",
                    "clip_id": "clip_011",
                },
            },
        ]
    }


def _temporal():
    return [
        {
            "clip_id": "clip_011",
            "energy_curve": {
                "sample_rate_hz": 30,
                "peak_times": [7.833, 9.867, 10.8, 12.6, 14.2],
            },
            "onset_times": [0.836, 1.045],
        }
    ]


def _inputs(**overrides):
    payload = {
        "timed_spine": _spine_2(),
        "temporal_event_indices": _temporal(),
    }
    payload.update(overrides)
    return payload


# ── The rows ──────────────────────────────────────────────────────────


def test_segment_id_is_the_position_the_answer_has_to_name():
    """The step's `llm_outputs` schema asks for `spine_block_position`.

    A table keyed by anything else names identifiers the model's answer
    cannot use, which is what `segment_id` off an A-roll slot would have
    been even had the input been routed.
    """
    payload = _inputs()
    assert "a_roll_assignments" not in payload  # what the runner hands it
    rows = build_sfx_candidates(payload)
    assert [r["segment_id"] for r in rows] == ["hook", 1, 2]


def test_a_speech_block_carries_its_line_and_a_slot_carries_its_note():
    rows = build_sfx_candidates(_inputs())
    assert rows[0]["text"] == "i can feel the silent judgment."
    assert rows[1]["text"] == "The breath after the hook."
    assert all(r["text"] for r in rows), (
        f"a row came out with no summary text: {rows}"
    )


# ── The transient column ──────────────────────────────────────────────


def test_the_transient_column_is_measured_not_a_constant(monkeypatch):
    # The bridge reads per-clip index FILES; hand it the fixture's indices.
    monkeypatch.setattr(
        'library.steps.step_4_04_plan_sfx.bridge._temporal_lookup',
        lambda data: {t['clip_id']: t
                      for t in data.get('temporal_event_indices', [])})
    """It was the literal string "No" on every row it built.

    Now it counts the energy peaks step 1.04 measured inside the
    block's own source range: none over the hook's 0.836-3.234, because
    the peaks on this clip start at 7.833, and three over 9.699-12.681.
    """
    rows = build_sfx_candidates(_inputs())
    assert rows[0]["action_sfx_suggested"] == "0 audio transients"
    assert rows[2]["action_sfx_suggested"] == "3 audio transients"


def test_a_block_with_no_source_clip_says_it_was_not_measured():
    """State the absence; never report it as a measured zero.

    A non-speech block names no `clip_id` on the SPINE, and this used to
    stop there - `not measured (no source clip)` on 5 of 001's 13 rows,
    the rows a whoosh would go on, while `b_roll_assignments` named the
    covering cutaway in the same prompt. With no cutaway over it there
    is genuinely nothing, and the cell says both halves.
    """
    rows = build_sfx_candidates(_inputs())
    assert rows[1]["action_sfx_suggested"] == (
        "not measured (no source clip and no cutaway over it)")


def test_a_covered_block_names_the_cutaway_and_says_it_plays_silent():
    """The cutaway is placed `video_only`; its own audio is never heard.

    So the honest cell is not "N transients" either - counting them
    would describe a sound nobody hears. See
    library/tools/broll_coverage.py.
    """
    rows = build_sfx_candidates(_inputs(b_roll_assignments=[{
        "spine_block_position": 1, "clip_id": "clip_004",
        "source_file": "/raw/IMG_1809.MOV",
        "video_in": 0.0, "video_out": 3.0,
    }]))
    assert rows[1]["action_sfx_suggested"] == (
        "covered by clip_004, video only - the cutaway's own audio is "
        "never heard")


# --------------------------------------------------------------------------
# From test_pacing_and_sfx_are_not_remembered.py
#
# Two steps decided from memory, and now they decide from context.
#
# `mesh_spine` (2.05) sees the creative direction's energy arc through its
# own projection, and `plan_sfx` (4.04) sees which transitions draw. The
# legacy DAG edges that route them are not pinned here (AGENTS.md 3).
# History (F7, F13): docs/evidence/sfx.md.

from library.steps.step_4_04_plan_sfx.bridge import (  # noqa: E402
    build_transition_rows,
)
from library.tools.context_projector import project_fields  # noqa: E402


def _manifest(step_dir: str) -> dict:
    return json.loads(
        (REPO / "library" / "steps" / step_dir / "manifest.json")
        .read_text(encoding="utf-8")
    )


# ── The direction reaches the step that sets the pace ────────────────


def test_the_energy_arc_survives_the_spines_projection():
    """The allow-list is what decides whether the prompt sees it.

    Routing without selecting would delete the direction again inside
    `project_step_context`, and the step would read exactly as it did
    on the run of record.
    """
    fields = _manifest("step_2_05_mesh_spine")["context_fields"]
    inputs = {"creative_direction": {
        "narrative_theme": "theme",
        "target_mood": "self-deprecating and quietly resolved",
        "target_energy": "building",
        "energy_arc": (
            "No triumphant lift at the end; the piece resolves by "
            "getting quieter and more certain, not louder."
        ),
        "emotional_landscape": "music should feel like company",
        "audience_emotion": "recognition, then permission",
        "key_moments": [{"moment": "the admission", "why": "..."}],
        "rationale": "why 2.01 chose this thread over three others",
    }}
    kept = project_fields(inputs, fields)["creative_direction"]

    assert "not louder" in kept["energy_arc"], (
        "the sentence 2.05 had to quote from memory is projected away "
        "again"
    )
    for name in ("target_mood", "target_energy", "emotional_landscape",
                 "narrative_theme", "audience_emotion"):
        assert name in kept, f"{name} no longer reaches the spine prompt"
    # The boundary: 2.01's own reasoning and a second copy of the
    # passages 2.02 already chose do not travel.
    assert "key_moments" not in kept and "rationale" not in kept


# ── The sound step can see the transitions ────────────────────────────


def _spine_3():
    """Four blocks, in project 001's own shape."""
    return {"structure": [
        {"position": "hook", "block_type": "hook",
         "timeline_start": 0.0, "timeline_end": 2.398},
        {"position": 1, "block_type": "transition_slot",
         "timeline_start": 2.398, "timeline_end": 6.06},
        {"position": 2, "block_type": "speech",
         "timeline_start": 6.06, "timeline_end": 8.742},
        {"position": 3, "block_type": "speech",
         "timeline_start": 8.742, "timeline_end": 11.444},
    ]}


def _spec():
    """One drawn transition, one hard cut, one jump cut.

    Shaped as step 4.02's post-bridge writes it: `cut_point_original` is
    the incoming block's own `timeline_start`, and the per-cut
    `rationale` is the prose that must not travel.
    """
    return [
        {"transition_id": "trans_001", "cut_point_original": 2.398,
         "cut_point_timeline": 2.398, "transition_type": "hard_cut",
         "duration_frames": 0,
         "rationale": "Nothing to draw. " + "x" * 400},
        {"transition_id": "trans_002", "cut_point_original": 6.06,
         "cut_point_timeline": 6.06, "transition_type": "defocus",
         "duration_frames": 15,
         "rationale": "A blur into the breath. " + "y" * 400},
        {"transition_id": "trans_003", "cut_point_original": 8.742,
         "cut_point_timeline": 8.742, "transition_type": "jump_cut",
         "duration_frames": 0,
         "rationale": "Labelled, not decorated. " + "z" * 400},
    ]


def _inputs_2(**overrides):
    payload = {"timed_spine": _spine_3(), "transition_spec": _spec(),
               "project_fps": 30.0}
    payload.update(overrides)
    return payload


def test_a_drawn_transition_is_told_apart_from_one_that_draws_nothing():
    """The handoff's second criterion is about a CREATIVE transition.

    A hard cut and a jump cut are real editorial labels that put
    nothing on screen (`transition_vocabulary.CUT_TYPES`, AGENTS.md
    10.5), so a column that only named the type would leave the model
    unable to apply the criterion at all.
    """
    rows = build_transition_rows(_inputs_2())
    by_position = {r["spine_block_position"]: r for r in rows}
    assert by_position[1]["draws_on_screen"] == "no"
    assert by_position[2]["draws_on_screen"] == "yes"
    assert by_position[2]["duration_frames"] == 15
    assert by_position[3]["draws_on_screen"] == "no", (
        "a jump_cut draws nothing - it is a label on a cut, not an "
        "effect on the picture"
    )


def test_a_cut_that_names_no_boundary_is_unresolved_not_the_nearest_block():
    """Never attach a cut to the block that happens to be closest.

    A wrong position is worse than an absent one here: it invites a
    sound onto a boundary the plan never decorated.
    """
    spec = _spec() + [{"transition_id": "trans_004",
                       "cut_point_original": 41.183,
                       "transition_type": "defocus",
                       "duration_frames": 15, "rationale": ""}]
    rows = build_transition_rows(_inputs_2(transition_spec=spec))
    assert rows[-1]["spine_block_position"] == "unresolved"
    assert rows[-1]["cut_point_seconds"] == 41.183, (
        "an unresolved cut still says where it is, or it is invisible"
    )


def test_the_table_is_empty_rather_than_wrong_without_the_plan():
    """An absent plan is zero rows, never a table of invented cuts.

    `empty_table_guard` reports a zero-row table on the run that sends
    it (AGENTS.md 10.1), which is the honest signal that the edge is
    gone.
    """
    payload = _inputs_2()
    del payload["transition_spec"]
    assert build_transition_rows(payload) == []
