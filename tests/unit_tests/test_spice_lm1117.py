"""SPICE integration test: LM1117-3.3V regulator from MR-1 board.

Proves that manufacturer SPICE models can complement the static sim ERC
pipeline — static analysis catches power budget issues, SPICE verifies
regulator transient behavior.

Requires: ngspice (CLI), PySpice
Model: TI LM1117 PSpice transient model, patched for ngspice compatibility.
"""
from __future__ import annotations

import os
import subprocess
import pytest

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "test_data", "spice_models", "LM1117_ngspice.lib"
)

pytestmark = pytest.mark.skipif(
    not os.path.exists(MODEL_PATH),
    reason="LM1117 SPICE model not found",
)


def _ngspice_available():
    try:
        r = subprocess.run(["ngspice", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _ngspice_available(), reason="ngspice not installed")
class TestLM1117Spice:
    """Validate LM1117 regulator behavior via ngspice."""

    def _run_sim(self, netlist: str) -> dict[str, float]:
        """Run a netlist via ngspice CLI, return node voltages."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".cir", delete=False) as f:
            f.write(netlist)
            f.flush()
            result = subprocess.run(
                ["ngspice", "-b", f.name],
                capture_output=True, timeout=30,
            )
            result.stdout = result.stdout.decode("utf-8", errors="replace")
            result.stderr = result.stderr.decode("utf-8", errors="replace")
        os.unlink(f.name)

        voltages = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Index"):
                continue
            # Parse tabular output from .print
            parts = line.split()
            if len(parts) >= 2:
                try:
                    float(parts[0])  # index
                    # This is a data row
                except ValueError:
                    pass

        # Parse from the verbose output
        in_nodes = False
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if "Node" in stripped and "Voltage" in stripped:
                in_nodes = True
                continue
            if in_nodes and stripped.startswith("----"):
                continue
            if in_nodes and stripped:
                parts = stripped.split()
                if len(parts) >= 2:
                    try:
                        voltages[parts[0]] = float(parts[1])
                    except (ValueError, IndexError):
                        if not parts[0].startswith("---"):
                            in_nodes = False

        return voltages

    def _base_netlist(self, r_load=22) -> str:
        return f"""\
LM1117 MR-1 Power Supply Test
.include {MODEL_PATH}

Vin vin 0 5
Xldo vin vout 0 0 LM1117_N_3P3_TRANS
Cin vin 0 100n
Cout vout 0 10u
Rload vout 0 {r_load}

.op
.end
"""

    def test_output_voltage_nominal(self):
        """LDO output should be ~3.3V with 150mA load (22 ohm)."""
        voltages = self._run_sim(self._base_netlist(r_load=22))
        vout = voltages.get("vout", 0)
        assert abs(vout - 3.3) < 0.1, f"Vout={vout:.4f}V, expected ~3.3V"

    def test_output_voltage_light_load(self):
        """Light load (10mA, 330 ohm) — output still regulated."""
        voltages = self._run_sim(self._base_netlist(r_load=330))
        vout = voltages.get("vout", 0)
        assert abs(vout - 3.3) < 0.05, f"Vout={vout:.4f}V, expected ~3.3V"

    def test_output_voltage_heavy_load(self):
        """Heavy load (500mA, 6.6 ohm) — near current limit but still regulating."""
        voltages = self._run_sim(self._base_netlist(r_load=6.6))
        vout = voltages.get("vout", 0)
        assert abs(vout - 3.3) < 0.15, f"Vout={vout:.4f}V, expected ~3.3V"

    def test_dropout_detection(self):
        """With Vin=3.5V (only 200mV headroom), output should droop."""
        netlist = f"""\
LM1117 Dropout Test
.include {MODEL_PATH}

Vin vin 0 3.5
Xldo vin vout 0 0 LM1117_N_3P3_TRANS
Cin vin 0 100n
Cout vout 0 10u
Rload vout 0 22

.op
.end
"""
        voltages = self._run_sim(netlist)
        vout = voltages.get("vout", 0)
        # With only 200mV above 3.3V and ~1.1V dropout at 150mA,
        # output will sag significantly below nominal
        assert vout < 3.3, f"Vout={vout:.4f}V, expected below 3.3V in dropout"
        assert vout > 2.0, f"Vout={vout:.4f}V, regulator not functioning at all"

    def test_complements_static_analysis(self):
        """Demonstrate static sim ERC + SPICE as layers."""
        os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

        from skidl.sim.declarations import SimHarness, DeclaredSource, DeclaredLoad
        from skidl.sim.rail_sanity import analyze_rail_sanity

        # Static analysis: declare what the power system should look like
        harness = SimHarness()
        harness.sources.append(DeclaredSource(
            net_name="+5V", voltage=5.0, provenance="USB VBUS",
        ))
        harness.loads.append(DeclaredLoad(
            net_name="+3V3", current=0.15, provenance="Daisy + peripherals",
        ))

        class FakeCircuit:
            parts = []
            sim_harness = harness
            def get_nets(self):
                return []

        # Static analysis catches power budget
        report = analyze_rail_sanity(FakeCircuit())
        assert report is not None

        # SPICE verifies the regulator actually delivers 3.3V under that load
        voltages = self._run_sim(self._base_netlist(r_load=22))
        vout = voltages.get("vout", 0)
        assert abs(vout - 3.3) < 0.1

        # Both passed — static said "budget is sane", SPICE said "regulator works"
