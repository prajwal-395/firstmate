"""The six pixel-side FFrames parity probes (report item 11).

Each probe's pass threshold lives here beside ``test_parity_gate.py``;
the evaluation compares the threshold against the last recorded
lock-held measurement (census, attributed per probe in
``library/graphics_ir/parity_probes.py``) with no render. Failing probes
are marked by id in ``EXPECTED_FAIL_TODAY`` - see
``test_ff_parity_probes.py`` for why markers cannot carry this - and
each test asserts its probe's outcome matches its label.

To re-measure after a C-item lands: render a few small frames only -
never the gate's 90-frame declaration - through FFrames and either
browser engine on the probe's own props, compare with the threshold in
``parity_probes.py``, and update the recorded value and its source
there. The score moves when the recorded value does.
"""

from library.graphics_ir.parity_probes import probe_status


def test_p1_subtitle_settled_within_census_floors():
    """Settled-frame parity: ink IoU >= 0.85, SSIM >= 0.97 (census 2.1)."""
    status, _ = probe_status("P1")
    assert status == "PASS"


def test_p2_mg_text_settled_same_words():
    """Same words, same reading order, settled IoU >= 0.5 (triangle)."""
    status, _ = probe_status("P2")
    assert status == "PASS"


def test_p3_mg_tracking_corrected():
    status, _ = probe_status("P3")
    assert status == "PASS"


def test_p5_fade_ramps_agree():
    status, _ = probe_status("P5")
    assert status == "PASS"


def test_p10_brand_sound_reaches_artefact():
    status, _ = probe_status("P10")
    assert status == "EXPECTED-FAIL"
