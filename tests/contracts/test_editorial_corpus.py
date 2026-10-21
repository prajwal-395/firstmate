"""BASE-003 - the representative editorial validation corpus holds together.

Each test here names a defect that would make the corpus lie:

- the engine could reach the sealed answer key, and a run could be
  handed the answers;
- the public half could drift from the sealed half, and a run could be
  handed a brief that no longer matches its key;
- a brief could rest on a premise the footage does not carry, and the
  corpus could score a run for failing to find what was never there;
- a brief could pair with footage of the wrong shape;
- the corpus could claim a category it has no brief for;
- a new brief could be a copy of one the engine has already been
  measured against;
- the shipped eight-brief set could be quietly rescored, which the
  captain ruled against.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_corpus_tool():
    """The corpus tool, which lives outside the engine on purpose."""
    path = REPO_ROOT / "evaluation" / "tools" / "corpus.py"
    spec = importlib.util.spec_from_file_location("editorial_corpus_tool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load_corpus_tool()

from library.tools.editorial_acceptance import briefs, footage


@pytest.fixture(scope="module")
def sealed():
    return corpus.load_sealed_briefs()


@pytest.fixture(scope="module")
def catalogue():
    return corpus.load_sealed_substrates()


@pytest.fixture(scope="module")
def shipped():
    return briefs.load_brief_set()


# ── the sealed half must stay out of the engine's reach ──────────────

ENGINE_TREES = ("library", "ren", "native", "bin", "scripts")
SEALED_MARKERS = ("evaluation/corpus", "corpus/sealed", "sealed/answer")
ENGINE_SUFFIXES = (".py", ".sh", ".rs", ".toml", ".json", ".md", ".js", ".ts")
SKIP_DIRS = {"target", "__pycache__", "node_modules", ".git"}


def test_the_engine_cannot_reach_the_sealed_answer_key():
    """No module the engine ships may name the sealed corpus path.

    The defect this names: a module under library/ (or ren/, or the
    launchers) reading the answer key, which would hand every run the
    answers it is supposed to work out.
    """
    offenders: list[str] = []
    for tree in ENGINE_TREES:
        root = REPO_ROOT / tree
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in ENGINE_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for marker in SEALED_MARKERS:
                if marker in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    assert not offenders, (
        "the engine must not read the sealed answer key: " + "; ".join(offenders))


def test_the_sealed_key_lives_outside_every_engine_tree():
    """The sealed half is not inside a tree the engine ships."""
    for tree in ENGINE_TREES:
        sealed_dir = REPO_ROOT / tree / "evaluation"
        assert not sealed_dir.exists(), (
            f"the sealed corpus must not live under {tree}/")


# ── the public half must carry no answer ─────────────────────────────

def test_the_public_half_carries_no_answer_key(sealed):
    """A brief that leaks its key fails here, not in a flattering score."""
    problems: list[str] = []
    for brief in sealed["briefs"]:
        key = briefs.oracle(brief)
        view = briefs.public_view(brief)
        problems.extend(f"{brief['id']}: {problem}" for problem
                        in briefs.leak_problems(view, key))
    assert not problems, "the public half leaks the key: " + "; ".join(problems)


def test_the_public_half_is_the_projection_of_the_sealed_half(sealed):
    """The committed public half is DERIVED, so it cannot drift."""
    committed = json.loads(
        corpus.PUBLIC_BRIEFS.read_text(encoding="utf-8"))
    assert committed == corpus.public_projection(sealed), (
        "the public half is stale; run "
        "`python3 evaluation/tools/corpus.py --write-public`")


def test_the_public_half_holds_no_footage_measurement(sealed):
    """The public half must not carry the substrate measurement either.

    A run that can read which features the footage carries can work out
    a brief's answer from the measurement instead of from the footage.
    """
    text = corpus.PUBLIC_BRIEFS.read_text(encoding="utf-8")
    for feature in footage.FEATURES:
        assert feature not in text, (
            f"the public half names the measured feature {feature!r}")


# ── every brief rests on a premise the footage settles ───────────────

def test_every_brief_pairs_with_footage_of_its_own_shape(sealed, catalogue):
    """A brief written against one shape is not answered by another."""
    audit = corpus.premise_audit(sealed, catalogue)
    for row in audit["rows"]:
        assert row["substrate"], (
            f"{row['brief_id']} pairs with no substrate")
        assert row["profile_matches"], (
            f"{row['brief_id']} pairs with footage of the wrong shape: "
            f"{row['substrate']}")


def test_every_brief_rests_on_a_premise_the_footage_settles(sealed,
                                                             catalogue):
    """A premise that is unknown is not good enough for a scored brief."""
    audit = corpus.premise_audit(sealed, catalogue)
    assert audit["unsettled"] == [], (
        "these briefs rest on a premise the footage does not settle: "
        + ", ".join(audit["unsettled"]))


def test_every_edit_brief_rests_on_a_premise_the_footage_carries(
        sealed, catalogue):
    """The corpus measures edits, so an edit's premise must be present."""
    audit = corpus.premise_audit(sealed, catalogue)
    for row in audit["rows"]:
        if row["outcome"] != "edit":
            continue
        carried = [requirement for requirement in row["requirements"]
                   if requirement["presence"] == "present"]
        assert carried, (
            f"{row['brief_id']} asks for an edit on a premise nothing "
            "carries")
        for requirement in carried:
            assert requirement["state"] == footage.SATISFIED, (
                f"{row['brief_id']} rests on {requirement['feature']!r}, "
                "which the footage does not carry")


def test_every_negative_brief_says_what_it_is_correct_to_report(
        sealed):
    """A refusal, a question and a report are three different answers."""
    outcomes = {str(brief.get("outcome")) for brief in sealed["briefs"]}
    assert {"refuse", "ask", "report"} <= outcomes, (
        "the corpus must carry all three kinds of correct report back")
    for brief in sealed["briefs"]:
        key = briefs.oracle(brief)
        if brief.get("outcome") == "edit":
            assert key["expected_refusal"] is False, brief["id"]
        else:
            assert key["expected_refusal"] is True, brief["id"]
            assert key["allowed_operations"] == [], (
                f"{brief['id']} expects no edit, so no operation may be "
                "allowed")


# ── the corpus says what it does not cover ───────────────────────────

def test_the_corpus_names_the_categories_it_does_not_cover(sealed):
    """A gap the corpus does not name is a gap it hides."""
    report = corpus.shape(sealed)
    assert report["categories_missing"], (
        "the corpus claims to cover every category; a category with no "
        "brief has to be named")
    for category in report["categories_missing"]:
        assert report["category_counts"][category] == 0


def test_the_published_manifest_matches_the_corpus(sealed):
    """The published shape is the corpus's own shape."""
    published = json.loads(
        corpus.PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    assert published["shape"] == corpus.shape(sealed), (
        "the published manifest is stale; run "
        "`python3 evaluation/tools/corpus.py --write-public`")


# ── the corpus is new material, and the old set is untouched ─────────

def test_no_brief_is_a_copy_of_a_brief_the_engine_has_already_seen(
        sealed, shipped):
    """A brief that copies a known request proves nothing new."""
    repeats = briefs.unfamiliar_against(
        sealed, briefs.corpus_instructions(shipped))
    assert repeats == [], (
        "these briefs copy a request in the shipped set: "
        + ", ".join(repeats))


def test_the_shipped_eight_brief_set_is_untouched():
    """The captain ruled that the shipped set stays scored as it is.

    Its digest is pinned here so that a lane cannot quietly rescore it,
    add to it, or drop a brief from it.
    """
    shipped = briefs.load_brief_set()
    assert len(shipped["briefs"]) == 8, (
        "the shipped acceptance set is eight briefs and stays eight")
    assert shipped["sha256"] == (
        "965880c76546d20010a625fccfda1614f13d6c338b9cc10e63355c5dac95cf21"), (
        "the shipped acceptance set changed; the captain ruled it stays "
        "scored as it was")


def test_the_corpus_holds_no_development_fixture(sealed, catalogue):
    """The sealed half is measured against real footage, not a fixture.

    The engine's own fixtures are development material: a brief scored
    against them measures the fixture, not the engine's editorial
    judgement.
    """
    for entry in footage.substrates(catalogue):
        blob = json.dumps(entry).lower()
        for marker in ("tests/fixtures", "fixture project", "sop_", "fake"):
            assert marker not in blob, (
                f"substrate {entry['id']!r} names development material "
                f"({marker})")
