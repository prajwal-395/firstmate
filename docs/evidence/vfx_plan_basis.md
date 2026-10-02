# An empty VFX plan says WHY it is empty

Tests: `tests/test_vfx_plan_basis.py` (moved from its module docstring, 2026-10-02).

An empty VFX plan says WHY it is empty.

`{"visual_effects": []}` meant two opposite things and looked identical
in both.  On 001's run of record it was a decision - the planner wrote
`docs/run-001-reasoning/plan_vfx.md` before answering, applied the
handoff's own "long AND static" criterion to eleven blocks, and held the
empty answer across three `semantically empty` rejections.  It is the
same bytes when the planner names four effects and the post-bridge
discards every one of them, and the drop reasons went only to stderr.

These tests drive the REAL post-bridge as the runner drives it - one
subprocess, JSON on stdin, JSON on stdout - and assert the two cases come
out different.  They also assert the accepting half is untouched: an
empty plan is still accepted, still never padded, and still never
refused.
