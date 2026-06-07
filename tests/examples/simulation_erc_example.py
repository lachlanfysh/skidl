"""Example: Opt-in Simulation ERC Gate

This example shows how to use SKiDL's simulation ERC to catch schematic
issues before PCB layout.  Simulation ERC is opt-in — it does not change
default ERC behavior unless explicitly enabled.

Run with InSpice installed:
    TEST_SPICE=1 python simulation_erc_example.py

Without InSpice, the readiness report still prints useful findings.
"""

from skidl import *
from skidl.sim import (
    plan_simulation,
    run_simulation,
    simulation_erc,
    enable_simulation_erc,
)

# --- Option A: one-shot simulation ERC ---

# Build a voltage divider.
set_default_tool(SKIDL)

res = Part(
    tool=SKIDL,
    name="res",
    ref_prefix="R",
    dest=TEMPLATE,
    pins=[Pin(num=1, func=Pin.types.PASSIVE), Pin(num=2, func=Pin.types.PASSIVE)],
)
r1 = res(value="20K")
r2 = res(value="10K")

vcc = Net("VCC")
gnd = Net("GND")
mid = Net("MID")

vcc.drive = POWER
gnd.drive = POWER

vcc += r1[1]
r1[2] += mid
mid += r2[1]
r2[2] += gnd

# Run simulation ERC without executing SPICE (readiness only).
report = simulation_erc(execute=False)
print(report.summary())
print()

# --- Option B: plan then inspect ---

plan = plan_simulation()
print(plan.summary())
print()

# Show which parts have exact models.
for entry in plan.eligible_parts:
    print(f"  {entry.ref}: {entry.spice_element} ({entry.description})")

# Show which parts are skipped.
if plan.skipped_parts:
    print(f"  Skipped: {', '.join(plan.skipped_parts)}")

print()

# --- Option C: enable as an ERC callback ---

# This attaches the simulation ERC to the circuit's erc_list.
# When ERC() runs, simulation checks run automatically.
enable_simulation_erc(execute=False, severity="WARNING")

# Now ERC() includes simulation findings.
ERC()

# The latest report is stored on the circuit.
import builtins
ckt = builtins.default_circuit
if ckt.last_simulation_report:
    print()
    print("Stored simulation report:")
    print(ckt.last_simulation_report.summary())
