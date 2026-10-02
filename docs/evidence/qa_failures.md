# QA check failures: loud, distinct, not yet fatal - history

Moved verbatim from the module docstring of the deleted
`tests/test_qa_failure_visibility.py` (test-suite halving, 2026-10-02). Its
four tests scanned `resolve_build_timeline.py` / `step_6_01_render/step.py`
source text; the behaviour is pinned by
`tests/unit/resolve/test_resolve_build_timeline.py::test_loud_banner_prints_on_qa_failure_but_not_fatal`
(`qa_failures` carries the failure, the banner prints, `success` is unchanged).

```text
A failed QA station must be visible, and must not read as a warning.

Error-severity QA check failures used to be appended to
`results["warnings"]`, where they sat among "Fairlight preset not found"
and friends - and the build still printed "Build succeeded" with an empty
error list. A check that runs, can fail, and whose failure nobody sees is
barely better than one that cannot fail, which is the defect Phase 0
existed to remove.

Step one of the captain's two-step ruling (2026-08-16): make it LOUD and
distinct. Not fatal - `success` is deliberately unchanged, because nobody
has yet measured how often these fire on real footage, and making them
fatal on no evidence would be the mirror image of the mistake. Step two is
that measurement, and `qa_failures` is the channel that supplies it.
```
