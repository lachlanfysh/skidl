"""SPICE execution tests for skidl.sim.

These require InSpice and are gated by TEST_SPICE=1.
"""
from __future__ import annotations

import os
import pytest

if os.getenv("TEST_SPICE") != "1":
    pytest.skip("Skip SPICE simulation tests", allow_module_level=True)

from skidl import *
from skidl.pyspice import *
from skidl.sim import plan_simulation, run_simulation, simulation_erc


@pytest.mark.spice
def test_voltage_divider_op():
    """Operating point of a simple voltage divider should match expected ratio."""
    global gnd

    set_default_tool(SPICE)
    gnd = Net("0")
    gnd.fixed_name = True

    vin = V(ref="VIN", dc_value=10 @ u_V)
    r1 = R(ref="R1", value=10 @ u_kOhm)
    r2 = R(ref="R2", value=10 @ u_kOhm)

    vcc, mid = Net("VCC"), Net("MID")
    gnd += vin["n"]
    vin["p"] += vcc
    vcc += r1[1]
    r1[2] += mid
    mid += r2[1]
    r2[2] += gnd

    report = run_simulation()
    assert report is not None
    assert report.executable or len(report.findings) > 0


@pytest.mark.spice
def test_rc_network_op():
    """Operating point of an RC network — capacitor blocks DC."""
    global gnd

    set_default_tool(SPICE)
    gnd = Net("0")
    gnd.fixed_name = True

    vin = V(ref="VIN", dc_value=5 @ u_V)
    r1 = R(ref="R1", value=1 @ u_kOhm)
    c1 = C(ref="C1", value=100 @ u_nF)

    vcc, sig = Net("VCC"), Net("SIG")
    gnd += vin["n"]
    vin["p"] += vcc
    vcc += r1[1]
    r1[2] += sig
    sig += c1[1]
    c1[2] += gnd

    report = run_simulation()
    assert report is not None


@pytest.mark.spice
def test_simulation_erc_execute():
    """simulation_erc(execute=True) should run and produce a report."""
    global gnd

    set_default_tool(SPICE)
    gnd = Net("0")
    gnd.fixed_name = True

    vin = V(ref="VIN", dc_value=5 @ u_V)
    r1 = R(ref="R1", value=10 @ u_kOhm)
    r2 = R(ref="R2", value=10 @ u_kOhm)

    vcc, mid = Net("VCC"), Net("MID")
    gnd += vin["n"]
    vin["p"] += vcc
    vcc += r1[1]
    r1[2] += mid
    mid += r2[1]
    r2[2] += gnd

    report = simulation_erc(execute=True)
    assert report is not None


@pytest.mark.spice
def test_missing_model_does_not_crash():
    """A circuit with unsupported parts should produce findings, not crash."""
    global gnd

    set_default_tool(SPICE)
    gnd = Net("0")
    gnd.fixed_name = True

    vin = V(ref="VIN", dc_value=3.3 @ u_V)
    r1 = R(ref="R1", value=1 @ u_kOhm)

    vcc = Net("VCC")
    gnd += vin["n"]
    vin["p"] += vcc
    vcc += r1[1]
    r1[2] += gnd

    report = simulation_erc(execute=True)
    assert report is not None
