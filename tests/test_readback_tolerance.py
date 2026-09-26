"""A property Resolve hands back one ulp off was still written.

2026-09-25: two post-header swaps were refused because a Pan of
-8.610478359908884 read back as -8.610478359908885 - the item was placed
exactly where declared, and exact float equality called it a framing
nobody chose.
"""

from library.tools.composed_edit import set_properties


class _Item:
    def __init__(self, drift):
        self.props, self.drift = {}, drift

    def SetProperty(self, key, value):
        self.props[key] = value + self.drift if isinstance(
            value, float) else value
        return True

    def GetProperty(self):
        return dict(self.props)


def test_a_one_ulp_readback_is_the_value_written():
    assert set_properties(_Item(1e-15), {"Pan": -8.610478359908884,
                                         "Scaling": 1}) == {}


def test_a_value_that_did_not_take_is_still_refused():
    assert "Tilt" in set_properties(_Item(0.5), {"Tilt": 1844.0})
