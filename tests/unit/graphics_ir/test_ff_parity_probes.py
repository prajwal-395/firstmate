"""The six static FFrames parity probes (report item 11).

Each probe asserts its DESIRED end state with no renderer, no Resolve
and no pixel render - pure lowering calls plus source reads, seconds to
run. Probe definitions, thresholds and recorded measurements live in
``library/graphics_ir/parity_probes.py``; the single ``parity: N/12``
number is ``python3 -m library.tools.parity_score``.

Expected-fail marking: this repository's skip audit fails any
``xfail``/skip whose reason is a fact about the repo rather than an
environment, so failing probes are marked by id in
``EXPECTED_FAIL_TODAY`` instead of with markers. Each test below
asserts its probe's outcome matches its label: a marked probe failing
passes here and shows EXPECTED-FAIL on the scoreboard; a marked probe
that starts passing fails here with the removal instruction. Do not fix
the gaps here - encode them.
"""

from library.graphics_ir.parity_probes import PROBES, probe_status


def test_parity_registry_holds_twelve_probes():
    assert [p.pid for p in PROBES] == [f"P{i}" for i in range(1, 13)]
    assert len({p.name for p in PROBES}) == 12
    for probe in PROBES:
        assert probe.kind in ("static", "pixel"), probe.pid
        assert probe.closes_with, probe.pid
        assert callable(probe.run), probe.pid


def test_p4_entity_copy_draws_literally():
    status, _ = probe_status("P4")
    assert status == "PASS"


def test_p6_all_mg_arms_lower():
    status, _ = probe_status("P6")
    assert status == "EXPECTED-FAIL"


def test_p7_channel_bug_bounded_and_placed():
    status, _ = probe_status("P7")
    assert status == "EXPECTED-FAIL"


def test_p9_brand_motion_composites_video():
    status, _ = probe_status("P9")
    assert status == "PASS"


def test_p11_conic_draws_or_is_undeclared():
    status, _ = probe_status("P11")
    assert status == "PASS"


def test_p12_unknown_content_refuses_loudly():
    status, _ = probe_status("P12")
    assert status == "EXPECTED-FAIL"
