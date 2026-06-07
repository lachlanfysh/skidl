"""Regression tests: positional calling of sim_source/sim_load/etc.

Ensures that `circuit` stays in its original positional slot and
`confidence` is keyword-only. Old code like
`sim_source("VCC", 3.3, None, "user", ckt)` must still work.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


class MockNet:
    def __init__(self, name):
        self.name = name


class MockCircuit:
    def __init__(self):
        self.parts = []
        self.sim_harness = None

    def get_nets(self):
        return []


class TestSimSourcePositional:
    def test_five_positional_args(self):
        """sim_source("VCC", 3.3, None, "user", ckt) must attach to ckt."""
        from skidl.sim.declarations import sim_source

        ckt = MockCircuit()
        sim_source("VCC", 3.3, None, "user", ckt)

        assert ckt.sim_harness is not None
        assert len(ckt.sim_harness.sources) == 1
        src = ckt.sim_harness.sources[0]
        assert src.net_name == "VCC"
        assert src.voltage == 3.3
        assert src.provenance == "user"

    def test_confidence_keyword_only(self):
        """confidence must be keyword-only — cannot be passed positionally."""
        from skidl.sim.declarations import sim_source

        ckt = MockCircuit()
        sim_source("VCC", 3.3, circuit=ckt, confidence=0.8)

        src = ckt.sim_harness.sources[0]
        assert src.confidence == 0.8

    def test_positional_circuit_with_keyword_confidence(self):
        from skidl.sim.declarations import sim_source

        ckt = MockCircuit()
        sim_source("VCC", 3.3, None, "agent", ckt, confidence=0.7)

        src = ckt.sim_harness.sources[0]
        assert src.provenance == "agent"
        assert src.confidence == 0.7


class TestSimLoadPositional:
    def test_positional_circuit(self):
        """sim_load("VCC", current=0.1, circuit=ckt) works."""
        from skidl.sim.declarations import sim_load

        ckt = MockCircuit()
        sim_load("VCC", None, 0.1, None, "user", ckt)

        assert len(ckt.sim_harness.loads) == 1
        load = ckt.sim_harness.loads[0]
        assert load.net_name == "VCC"
        assert load.current == 0.1

    def test_confidence_keyword_only(self):
        from skidl.sim.declarations import sim_load

        ckt = MockCircuit()
        sim_load("VCC", current=0.1, circuit=ckt, confidence=0.5)

        load = ckt.sim_harness.loads[0]
        assert load.confidence == 0.5


class TestSimAssertRailPositional:
    def test_positional_circuit(self):
        """sim_assert_rail("VCC", 3.3, 0.05, "user", ckt) works."""
        from skidl.sim.declarations import sim_assert_rail

        ckt = MockCircuit()
        sim_assert_rail("VCC", 3.3, 0.05, "user", ckt)

        assert len(ckt.sim_harness.rail_assertions) == 1
        ra = ckt.sim_harness.rail_assertions[0]
        assert ra.net_name == "VCC"
        assert ra.nominal == 3.3

    def test_confidence_keyword_only(self):
        from skidl.sim.declarations import sim_assert_rail

        ckt = MockCircuit()
        sim_assert_rail("VCC", 3.3, circuit=ckt, confidence=0.9)

        ra = ckt.sim_harness.rail_assertions[0]
        assert ra.confidence == 0.9


class TestSimAssertNodeRatioPositional:
    def test_positional_circuit(self):
        from skidl.sim.declarations import sim_assert_node_ratio

        ckt = MockCircuit()
        sim_assert_node_ratio("OUT", "IN", 0.5, 0.05, "user", ckt)

        assert len(ckt.sim_harness.ratio_assertions) == 1
        ra = ckt.sim_harness.ratio_assertions[0]
        assert ra.output_net == "OUT"
        assert ra.ratio == 0.5
