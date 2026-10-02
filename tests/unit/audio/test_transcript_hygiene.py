"""The finding half proposes; a human (or keyed run) disposes.

`library/tools/transcript_hygiene.py` (Gap 2 of the 2026-09-19
field-test brief): deterministic shape-based nomination plus the
model's sentence-level judgement, recorded as `mistake_fix` with
evidence and provenance. These tests prove nomination nominates by
shape rather than text, the verdict pipeline refuses what it cannot
honour, recording attributes honestly, and a keyless environment
degrades to UNEVALUATED rather than guessing.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from library.tools import transcript_hygiene as hy


def _project(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "learned_context"), exist_ok=True)
    with open(os.path.join(root, "learned_context", "learnings.json"),
              "w", encoding="utf-8") as handle:
        json.dump([], handle)
    return root


def _doc():
    return {"transcription": {"arms": {"A": "hybrid"},
                              "aligners": {"A": "mfa"}},
            "segments": [
                {"speaker": "Akshita",
                 "text": "make niche qu niche questions",
                 "words": [
                     {"word": "make", "start": 1.0, "end": 1.2},
                     {"word": "niche", "start": 1.2, "end": 1.5},
                     {"word": "qu", "start": 1.5, "end": 1.7},
                     {"word": "niche", "start": 1.7, "end": 2.0},
                     {"word": "questions", "start": 2.0, "end": 2.5}]},
                {"speaker": "Craig",
                 "text": "better than X, Y, and Z",
                 "words": [
                     {"word": "better", "start": 3.0, "end": 3.3},
                     {"word": "than", "start": 3.3, "end": 3.5},
                     {"word": "X,", "start": 3.5, "end": 3.7},
                     {"word": "Y,", "start": 3.7, "end": 3.9},
                     {"word": "and", "start": 3.9, "end": 4.0},
                     {"word": "Z.", "start": 4.0, "end": 4.2}]},
                {"speaker": "Craig",
                 "text": "I I think so",
                 "words": [
                     {"word": "I", "start": 5.0, "end": 5.1},
                     {"word": "I", "start": 5.1, "end": 5.2},
                     {"word": "think", "start": 5.2, "end": 5.5},
                     {"word": "so", "start": 5.5, "end": 5.7}]},
            ]}


def test_nominate_measures_shapes_not_words():
    candidates = hy.nominate(_doc())
    # Placeholders nominate (single letters) and rely on the JUDGE to
    # keep them; the "I I" false start nominates as a duplicate; "qu"
    # nominates nothing - it is two chars, and no shape fires on two
    # chars without a list naming them, so the sentence-level judge
    # finds it in context instead.
    assert [(c["seg"], c["word"], c["shape"]) for c in candidates] == [
        (1, "X,", "single_char"), (1, "Y,", "single_char"),
        (1, "Z.", "single_char"), (2, "I", "duplicate")]
    # ...so add the single-char fragment shape check on its own doc.
    frag = {"segments": [
        {"speaker": "A", "text": "same f um same",
         "words": [{"word": "same", "start": 1.0, "end": 1.3},
                   {"word": "f", "start": 1.3, "end": 1.5},
                   {"word": "um", "start": 1.5, "end": 1.8},
                   {"word": "same", "start": 1.8, "end": 2.0}]}]}
    shapes = hy.nominate(frag)
    assert [(c["word"], c["shape"]) for c in shapes] == [("f", "single_char")]
    # And the dictionary singletons never nominate.
    arts = {"segments": [
        {"speaker": "A", "text": "a I see",
         "words": [{"word": "a", "start": 1.0, "end": 1.1},
                   {"word": "I", "start": 1.1, "end": 1.2},
                   {"word": "see", "start": 1.2, "end": 1.5}]}]}
    assert hy.nominate(arts) == []


def test_dispose_batch_refuses_what_it_cannot_honour():
    view = hy.segments_for_judgement(_doc())
    verdict = {
        "suppress": [
            {"seg": 0, "index": 2, "scope": "anchored",
             "why": "stray phoneme"},
            {"seg": 99, "index": 0, "scope": "anchored",
             "why": "no such segment"},
            {"seg": 0, "index": 0, "scope": "everywhere",
             "why": "wild scope"},
            {"seg": 0, "index": 1, "scope": "anchored", "why": ""},
        ],
        "respell": [
            {"heard": "aics", "correct": "AI sees",
             "why": "misheard product"},
            {"heard": "", "correct": "X", "why": "names nothing"},
        ],
    }
    (suppressions, respells), refused = hy._dispose_batch(view, verdict)
    assert [(s["seg"], s["index"], s["word"], s["scope"]) for s in suppressions] == [
        (0, 2, "qu", "anchored")]
    assert suppressions[0]["prev"] == "niche"
    assert suppressions[0]["next"] == "niche"
    assert respells == [{"heard": "aics", "correct": "AI sees",
                         "why": "misheard product"}]
    assert len(refused) == 4


def test_scan_records_with_evidence_and_provenance(tmp_path):
    """A preview (apply=False) records nothing; an applied scan records
    under the model's own name, and the next run enforces it."""
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)

    def stub_judge(view, candidates, terms, project_folder):
        assert len(view) == 3  # every sentence, not just candidates
        return {"suppress": [
                    {"seg": 0, "index": 2, "word": "qu",
                     "speaker": "Akshita", "prev": "niche",
                     "next": "niche", "scope": "anchored",
                     "why": "stray phoneme the reader does not need"}],
                "respell": [{"heard": "aics", "correct": "AI sees",
                             "why": "misheard product"}],
                "unevaluated": [], "refused": []}

    preview = hy.scan(project, _doc(), judge=stub_judge, apply=False)
    assert len(preview["suppress"]) == 1
    assert preview["recorded"] == []
    assert lc.active_for_step(project, "*") == []

    report = hy.scan(project, _doc(), judge=stub_judge, apply=True)
    assert report["recorded"] and len(report["recorded"]) == 2
    # Honest attribution: the model wears its own name.
    learnings = lc.active_for_step(project, "*")
    kinds = {l["id"]: (l["kind"], l["said_by"]) for l in learnings}
    assert all(kind == ("mistake_fix", "the pipeline")
               for kind in kinds.values())
    suppression = tc.suppressions(project)[0]
    assert suppression["scope"] == {"speaker": "Akshita",
                                    "surface": "qu",
                                    "prev": "niche", "next": "niche"}
    # ...and the next run enforces what was recorded.
    doc = _doc()
    tc.apply_to_document(doc, project)
    assert "qu" not in doc["segments"][0]["text"].split()


def test_keyless_judge_returns_unevaluated(tmp_path, monkeypatch):
    project = _project(tmp_path)
    for var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    view = hy.segments_for_judgement(_doc())
    verdict = hy._llm_judge(view, hy.nominate(_doc()), [], project)
    assert verdict["suppress"] == [] and verdict["respell"] == []
    # Placeholders plus the duplicate: nominated, never guessed.
    assert len(verdict["unevaluated"]) == 4


def test_is_uncertain_reads_the_models_own_admissions():
    assert hy.is_uncertain("MODEL, NEEDS CAPTAIN CONFIRMATION: parallel "
                           "take settles it, retire if a name")
    assert hy.is_uncertain("low confidence: reads as a false start")
    assert hy.is_uncertain("BORDERLINE: keep if emphasis")
    assert hy.is_uncertain("unsure whether this is a name")
    assert not hy.is_uncertain("stray phoneme the reader does not need")
    assert not hy.is_uncertain("")
    assert not hy.is_uncertain(None)
    # The scanner's own composed template says "retire it if wrong" -
    # detection reads the row's `why`, never that template, so a
    # confident row wearing the template must not read as unsure.
    assert not hy.is_uncertain(
        "Model-proposed transcript spelling (not yet confirmed by the "
        "captain - retire it if wrong): reads as she even")


def test_scan_holds_uncertain_rows_pending_and_applies_confident(tmp_path):
    """The 2026-09-19 default, corrected end to end: the four rows the
    model flagged unsure record PENDING (held, never enforced) while
    the confident row beside them keeps auto-applying."""
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)

    def stub_judge(view, candidates, terms, project_folder):
        return {"suppress": [
                    {"seg": 0, "index": 2, "word": "qu",
                     "speaker": "Akshita", "prev": "niche",
                     "next": "niche", "scope": "anchored",
                     "why": "BORDERLINE: stray phoneme, keep if "
                            "emphasis"},
                    {"seg": 2, "index": 1, "word": "I",
                     "speaker": "Craig", "prev": "I", "next": "think",
                     "scope": "anchored",
                     "why": "false-start repeat the reader does not "
                            "need"}],
                "respell": [{"heard": "Sheehan", "correct": "she even",
                             "why": "NEEDS CAPTAIN CONFIRMATION: "
                                    "parallel take reads she even"}],
                "unevaluated": [], "refused": []}

    report = hy.scan(project, _doc(), judge=stub_judge, apply=True)
    assert len(report["recorded"]) == 3
    assert len(report["pending"]) == 2
    held = {l["id"]: l for l in lc.pending(project)}
    assert set(report["pending"]) == set(held)
    # Held rows enforce nothing: the words stand on the next pass.
    assert tc.spelling_corrections(project) == []
    assert [s["heard"] for s in tc.suppressions(project)] == ["I"]
    doc = _doc()
    tc.apply_to_document(doc, project)
    assert doc["segments"][0]["text"].split()[2] == "qu"
    # The confident row beside them applied as before.
    assert len(lc.active_for_step(project, "*")) == 1
    # Promotion enforces the held rows from the next run.
    for held_id in report["pending"]:
        lc.promote(project, held_id, reason="Captain: keep.")
    assert len(tc.spelling_corrections(project)) == 1
    assert sorted(s["heard"] for s in tc.suppressions(project)) == [
        "I", "qu"]
