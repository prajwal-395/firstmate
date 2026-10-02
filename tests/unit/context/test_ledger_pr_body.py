"""PR enumeration from the edit ledger: each test names its defect.

The verb (`library/tools/ledger_pr_body.py`) replaces the hand-written
134-card BEFORE/AFTER body of the 09-18 captions fix. A rendering that
drifts from that shape, drops rows under a filter, or loses a new op
to a raw dump sends the correction round trip back.
"""
import pytest

from library.tools import edit_ledger, ledger_pr_body


def _caption(phrase, replacement, reel=None):
    row = {"op": "caption_fix",
           "anchor": {"kind": "words", "phrase": phrase},
           "params": {"replacement": replacement},
           "stated_by": "model",
           "reason": "acronyms read uppercase"}
    if reel is not None:
        row["reel"] = reel
    return row


def test_caption_fix_renders_before_after_item_for_item():
    """A drifted rendering (wrong quotes, swapped sides, missing
    arrow) would not match the hand-written 1209 body it replaces,
    and the worker would hand-edit every line - the enumeration
    saving nothing."""
    row = _caption("seo two point oh", "SEO 2.0",
                   reel="Reel 01 - geo-is-comprehension-not-position")
    text = ledger_pr_body.render_enumeration([row])
    assert "### Reel 01 - geo-is-comprehension-not-position (1)" in text
    assert "- BEFORE 'seo two point oh'  ->  AFTER 'SEO 2.0'" in text
    apostrophe = _caption("the link's bio.", "the link's in our bio.")
    line = ledger_pr_body.render_row(apostrophe)
    assert line == ('- BEFORE "the link\'s bio."  ->  '
                    'AFTER "the link\'s in our bio."')


def test_reel_filter_keeps_unscoped_rows():
    """An unscoped row holds on EVERY reel, so a `--reel` filter that
    dropped it would silently lose a decision from the pasted body -
    the reader approving a change that is narrower than what ships."""
    scoped = _caption("ai sees you", "AI sees you", reel="Reel 02 - x")
    unscoped = _caption("the links bio", "the link's in our bio")
    text = ledger_pr_body.render_enumeration(
        [scoped, unscoped], reel_prefixes=("Reel 02",))
    assert "AI sees you" in text
    assert "in our bio" in text
    assert "### Every reel in scope (1)" in text


def test_every_known_op_has_a_dedicated_rendering():
    """A new op added to the ledger vocabulary with no branch here
    would reach the PR body only as a raw params dump - reviewable
    in name only. This fails the moment one does."""
    samples = {
        "voice_isolation": {
            "anchor": {"kind": "reel"}, "reel": "Reel 09 - hook",
            "params": {"track": 1, "amount": 60}},
        "clip_lut": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "reel": "Reel 09 - hook",
            "params": {"lut": "Film Looks/Kodak 2383", "node": 1}},
        "transform_override": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"property": "Pan", "value": 12}},
        "span_retime": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"edge": "head"}},
        "drop_fragment": {
            "anchor": {"kind": "words", "phrase": "the aside"}},
        "caption_fix": {
            "anchor": {"kind": "words", "phrase": "seo team"},
            "params": {"replacement": "SEO team"}},
        "redraw_closer": {
            "anchor": {"kind": "words", "phrase": "come back tomorrow"},
            "params": {"from_phrase": "see you soon"}},
        "retime": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"percent": 110}},
        "grade": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"lut": "Film Looks/Kodak 2383"}},
        "angle_plan": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"camera": "wide"}},
        "plan_change": {
            "anchor": {"kind": "reel"},
            "params": {"operation_type": "transition",
                       "owner": "plan_transitions",
                       "values": {"duration": {
                           "value": 12, "unit": "frames",
                           "stated_by": "requester"}}}},
    }
    assert set(samples) == set(edit_ledger.OPS)
    for op, partial in samples.items():
        row = {"op": op, "stated_by": "requester",
               "reason": "the requester's words", **partial}
        line = ledger_pr_body.render_row(row)
        assert not line.startswith(f"- {op}:"), \
            f"{op} fell through to the raw-dump fallback"


def test_malformed_ledger_refuses_instead_of_pasting_half(tmp_path):
    """A ledger the reader cannot parse must REFUSE (exit 1), never
    paste a half enumeration the worker files as complete - a
    recorded decision the body cannot see is approved blind."""
    from library.tools.project_layout import ProjectLayout

    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    ledger = edit_ledger.ledger_path(str(project))
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"version": 1, "rows": [{"op": "nope"}]}',
                      encoding="utf-8")
    assert ledger_pr_body.main([str(project)]) == 1
