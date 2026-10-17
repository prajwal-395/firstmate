"""cheetah research weights never enter production (lane vep-mu-reid).

AUD-043's InsightFace 2.1 evaluation is permitted research: the cheetah
packs' MODEL.LICENSE grants non-commercial use only. The benchmark seam
is `library/tools/cheetah_reid.py`; this test fails any production
import of it or of InsightFace's `PersonAnalysis`, so a research-only
checkpoint cannot reach a commercial workload through a new caller.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ALLOWLIST = {
    Path("library/tools/cheetah_reid.py"),
    # This study runner only imports cheetah lazily after explicit
    # research acknowledgement; it is not reachable from production.
    Path("library/tools/reid_study.py"),
    Path("tests/unit/picture/test_cheetah_reid.py"),
    Path("tests/contracts/test_no_cheetah_in_production.py"),
}

MARKERS = ("cheetah_reid", "PersonAnalysis", "cheetah_s", "cheetah_l", "reid_0265")

SEARCH_ROOTS = ("library", "ren", "native/ren-cli/src", "remotion-subtitles")


def _hits():
    hits = []
    for root in SEARCH_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".rs",
                ".ts",
                ".tsx",
                ".js",
            }:
                continue
            rel = path.relative_to(REPO_ROOT)
            if rel in ALLOWLIST:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
            for marker in MARKERS:
                if marker in text:
                    hits.append(f"{rel}: {marker}")
    return hits


def test_no_production_cheetah_reference():
    assert _hits() == []


def test_person_entity_never_imports_cheetah():
    text = (REPO_ROOT / "library/tools/person_entity.py").read_text(encoding="utf-8")
    for marker in MARKERS:
        assert marker not in text
