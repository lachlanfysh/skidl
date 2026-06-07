"""First-order PDN (Power Delivery Network) impedance estimate.

Computes per-rail target impedance, capacitor impedance curves (C+ESR+ESL),
combined decoupling network impedance, and flags frequency bands where the
network exceeds the target. Layout distance adds ESL penalty when placement
data is available.

Limitation: parallel impedance is computed from magnitudes only (not complex
phasors). This means anti-resonance peaks between caps of different values
are not modelled — the real impedance at those frequencies can be higher
than reported. Treat results as a first-order inventory check, not a
full frequency-domain PDN simulation.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .report import FindingSeverity, SimulationFinding

# ---------------------------------------------------------------------------
# ESR/ESL lookup table — typical values for MLCC ceramics by package
# ---------------------------------------------------------------------------
# Sources: Murata/TDK/Samsung datasheets for common X5R/X7R MLCCs.
# Format: (value_f_min, value_f_max, footprint_pattern) → (esr_ohm, esl_nh)
# Fallback: generic MLCC defaults when footprint is unknown.

_PACKAGE_RE = {
    "0201": re.compile(r"0201|0603Metric", re.IGNORECASE),
    "0402": re.compile(r"0402|1005Metric", re.IGNORECASE),
    "0603": re.compile(r"0603|1608Metric", re.IGNORECASE),
    "0805": re.compile(r"0805|2012Metric", re.IGNORECASE),
    "1206": re.compile(r"1206|3216Metric", re.IGNORECASE),
    "1210": re.compile(r"1210|3225Metric", re.IGNORECASE),
}

# (esr_ohm, esl_nh) by package size — conservative typical values
_PACKAGE_PARASITICS: dict[str, tuple[float, float]] = {
    "0201": (0.050, 0.3),
    "0402": (0.030, 0.4),
    "0603": (0.015, 0.5),
    "0805": (0.010, 0.7),
    "1206": (0.008, 1.0),
    "1210": (0.005, 1.2),
}

_DEFAULT_ESR = 0.015  # 15mΩ — conservative MLCC default
_DEFAULT_ESL = 0.5    # 0.5nH — conservative MLCC default

# Per-trace-mm ESL penalty (roughly 1nH per mm of trace at typical geometries)
_TRACE_ESL_PER_MM = 1.0  # nH/mm


@dataclass
class CapModel:
    ref: str
    capacitance: float  # farads
    esr: float          # ohms
    esl: float          # henries (converted from nH internally)
    footprint: str = ""
    package: str = ""
    distance_mm: float | None = None
    distance_esl_penalty: float = 0.0  # henries added for trace
    provenance: str = "estimated"

    @property
    def effective_esl(self) -> float:
        return self.esl + self.distance_esl_penalty

    def impedance_at(self, freq_hz: float) -> float:
        """Magnitude of series RLC impedance at given frequency."""
        if freq_hz <= 0:
            return float("inf")
        omega = 2 * math.pi * freq_hz
        z_c = 1 / (omega * self.capacitance) if self.capacitance > 0 else float("inf")
        z_l = omega * self.effective_esl
        return math.sqrt(self.esr ** 2 + (z_l - z_c) ** 2)

    def resonant_freq(self) -> float:
        """Series resonant frequency in Hz."""
        if self.capacitance <= 0 or self.effective_esl <= 0:
            return 0.0
        return 1 / (2 * math.pi * math.sqrt(self.effective_esl * self.capacitance))


# ---------------------------------------------------------------------------
# Target impedance
# ---------------------------------------------------------------------------
# Tier-based defaults: (rail_voltage_min, rail_voltage_max) → Z_target
_Z_TARGET_TIERS: list[tuple[float, float, float]] = [
    (0.0, 1.2, 0.015),    # sub-1V cores: 15mΩ
    (1.2, 2.0, 0.025),    # 1.8V: 25mΩ
    (2.0, 4.0, 0.050),    # 3.3V: 50mΩ
    (4.0, 6.0, 0.100),    # 5V: 100mΩ
    (6.0, 15.0, 0.200),   # 12V: 200mΩ
    (15.0, 100.0, 0.500), # high voltage: 500mΩ
]


def _tier_z_target(voltage: float) -> float:
    for v_min, v_max, z in _Z_TARGET_TIERS:
        if v_min <= abs(voltage) < v_max:
            return z
    return 0.500


@dataclass
class PDNConstraints:
    freq_min: float = 1e3      # Hz (default 1kHz)
    freq_max: float = 100e6    # Hz (default 100MHz)
    freq_points: int = 200     # log-spaced sweep points
    ripple_fraction: float | None = None  # e.g. 0.05 for 5%
    transient_current: float | None = None  # amps


@dataclass
class RailPDNResult:
    net_name: str
    voltage: float
    z_target: float
    z_target_source: str  # "user" or "tier_default"
    caps: list[CapModel] = field(default_factory=list)
    frequencies: list[float] = field(default_factory=list)
    combined_impedance: list[float] = field(default_factory=list)
    violations: list[tuple[float, float]] = field(default_factory=list)  # (freq, Z)
    worst_z: float | None = None
    worst_freq: float | None = None
    findings: list[SimulationFinding] = field(default_factory=list)


@dataclass
class PDNReport:
    rails: list[RailPDNResult] = field(default_factory=list)
    findings: list[SimulationFinding] = field(default_factory=list)

    @property
    def rails_passing(self) -> int:
        return sum(1 for r in self.rails if not r.violations)

    @property
    def rails_failing(self) -> int:
        return sum(1 for r in self.rails if r.violations)

    def summary(self) -> str:
        lines = ["PDN impedance report (first-order estimate, magnitude-only):"]
        lines.append(
            f"  Rails analyzed: {len(self.rails)} "
            f"({self.rails_passing} pass, {self.rails_failing} fail)"
        )
        for r in self.rails:
            status = "PASS" if not r.violations else "FAIL"
            lines.append(
                f"  [{status}] {r.net_name}: {r.voltage:.3g}V, "
                f"Z_target={r.z_target * 1000:.1f}mΩ ({r.z_target_source}), "
                f"{len(r.caps)} caps"
            )
            if r.worst_z is not None:
                lines.append(
                    f"    Worst: {r.worst_z * 1000:.1f}mΩ "
                    f"@ {_freq_str(r.worst_freq)}"
                )
            if r.violations:
                lines.append(
                    f"    {len(r.violations)} frequency points exceed target"
                )
            for cap in r.caps:
                dist = (f", {cap.distance_mm:.1f}mm away"
                        if cap.distance_mm is not None else "")
                lines.append(
                    f"    {cap.ref}: {_value_str(cap.capacitance)}F, "
                    f"ESR={cap.esr * 1000:.1f}mΩ, "
                    f"ESL={cap.esl * 1e9:.2f}nH{dist} "
                    f"[{cap.provenance}]"
                )
        for f in self.findings:
            lines.append(f"  {f.summary()}")
        return "\n".join(lines)


def _freq_str(freq: float | None) -> str:
    if freq is None:
        return "?"
    if freq >= 1e6:
        return f"{freq / 1e6:.1f}MHz"
    if freq >= 1e3:
        return f"{freq / 1e3:.1f}kHz"
    return f"{freq:.0f}Hz"


def _value_str(val: float) -> str:
    if val >= 1:
        return f"{val:.3g}"
    if val >= 1e-3:
        return f"{val * 1e3:.3g}m"
    if val >= 1e-6:
        return f"{val * 1e6:.3g}µ"
    if val >= 1e-9:
        return f"{val * 1e9:.3g}n"
    return f"{val * 1e12:.3g}p"


# ---------------------------------------------------------------------------
# Package detection
# ---------------------------------------------------------------------------
def _detect_package(footprint: str) -> str:
    for pkg, pattern in _PACKAGE_RE.items():
        if pattern.search(footprint):
            return pkg
    return ""


def _lookup_parasitics(package: str) -> tuple[float, float]:
    if package in _PACKAGE_PARASITICS:
        esr, esl_nh = _PACKAGE_PARASITICS[package]
        return esr, esl_nh * 1e-9
    return _DEFAULT_ESR, _DEFAULT_ESL * 1e-9


# ---------------------------------------------------------------------------
# Combined impedance of parallel caps
# ---------------------------------------------------------------------------
def _parallel_impedance(caps: list[CapModel], freq: float) -> float:
    """Compute combined impedance of caps in parallel at given frequency."""
    if not caps:
        return float("inf")
    admittance_sum = 0.0
    for cap in caps:
        z = cap.impedance_at(freq)
        if z > 0:
            admittance_sum += 1.0 / z
    return 1.0 / admittance_sum if admittance_sum > 0 else float("inf")


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def analyze_pdn(
    circuit=None,
    constraints: PDNConstraints | None = None,
    placed: list | None = None,
    fp_bboxes: dict | None = None,
) -> PDNReport:
    """Analyze power delivery network impedance per rail.

    Uses decoupling report (Step 3) for cap-to-rail association and
    rail sanity (Step 6) for known rail voltages. Optionally uses
    placement data for distance-based ESL penalty.

    Args:
        circuit: Target circuit. Defaults to builtins.default_circuit.
        constraints: Frequency range and optional user-specified target Z.
        placed: List of PlacedPart from layout engine (optional).
        fp_bboxes: Footprint bounding boxes (optional).
    """
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    if constraints is None:
        constraints = PDNConstraints()

    report = PDNReport()

    # Build placement lookup: ref → (x, y)
    # Accepts either dict[str, (x,y)] or list of PlacedPart objects
    placement: dict[str, tuple[float, float]] | None = None
    if placed is not None:
        if isinstance(placed, dict):
            placement = placed
        else:
            placement = {}
            for pp in placed:
                placement[pp.ref] = (pp.x_mm, pp.y_mm)

    # Get rail voltages from harness
    from .rail_sanity import _build_node_voltages
    node_voltages = _build_node_voltages(circuit)

    # Build per-rail total load current from harness declarations
    # Multiple loads on the same rail sum; resistance loads derive I = V/R
    harness = getattr(circuit, "sim_harness", None)
    rail_load_current: dict[str, float] = {}
    if harness:
        for load in harness.loads:
            current = getattr(load, "current", None)
            resistance = getattr(load, "resistance", None)
            if current is not None and current > 0:
                rail_load_current[load.net_name] = (
                    rail_load_current.get(load.net_name, 0.0) + current
                )
            elif resistance is not None and resistance > 0:
                v_info = node_voltages.get(load.net_name)
                if v_info is not None and v_info[0] != 0:
                    i_derived = abs(v_info[0]) / resistance
                    rail_load_current[load.net_name] = (
                        rail_load_current.get(load.net_name, 0.0) + i_derived
                    )

    # Get decoupling data
    from .decoupling import analyze_decoupling
    decoupling = analyze_decoupling(circuit, placement, fp_bboxes)

    # Build cap lookup from decoupling report
    cap_by_ref: dict[str, object] = {}
    for cap_info in decoupling.caps:
        cap_by_ref[cap_info.ref] = cap_info

    # Build part lookup for footprints
    part_by_ref: dict[str, object] = {}
    for part in circuit.parts:
        part_by_ref[part.ref] = part

    rails_with_caps: dict[str, list[CapModel]] = {}

    # Collect minimum distance per (rail, cap_ref) across all associations
    best_dist: dict[tuple[str, str], float | None] = {}
    for assoc in decoupling.associations:
        rail = assoc.rail
        if not rail:
            continue
        key = (rail, assoc.cap_ref)
        d = assoc.distance_mm
        if key not in best_dist:
            best_dist[key] = d
        elif d is not None and (best_dist[key] is None or d < best_dist[key]):
            best_dist[key] = d

    for (rail, cap_ref), dist_mm in best_dist.items():
        cap_info = cap_by_ref.get(cap_ref)
        if cap_info is None:
            continue
        cap_val = cap_info.value_f
        if cap_val is None or cap_val <= 0:
            continue

        part = part_by_ref.get(cap_ref)
        footprint = str(getattr(part, "footprint", "") or "") if part else ""
        package = _detect_package(footprint)
        esr, esl = _lookup_parasitics(package)
        prov = f"{'lookup:' + package if package else 'default'}"

        dist_penalty = 0.0
        if dist_mm is not None and dist_mm > 0:
            dist_penalty = dist_mm * _TRACE_ESL_PER_MM * 1e-9

        cap_model = CapModel(
            ref=cap_ref,
            capacitance=cap_val,
            esr=esr,
            esl=esl,
            footprint=footprint,
            package=package,
            distance_mm=dist_mm,
            distance_esl_penalty=dist_penalty,
            provenance=prov,
        )

        rails_with_caps.setdefault(rail, []).append(cap_model)

    # Also include rails from harness that have no caps
    for src_name, (voltage, _) in node_voltages.items():
        if src_name not in rails_with_caps and voltage != 0.0:
            rails_with_caps.setdefault(src_name, [])

    # Frequency sweep
    freqs = _log_space(constraints.freq_min, constraints.freq_max,
                       constraints.freq_points)

    # Analyze each rail
    for rail_name in sorted(rails_with_caps.keys()):
        v_info = node_voltages.get(rail_name)
        if v_info is None:
            report.findings.append(SimulationFinding(
                severity=FindingSeverity.WARNING,
                message=f"Rail {rail_name} has caps but no declared voltage — "
                        f"skipping PDN analysis",
                nets=[rail_name],
                category="pdn_skipped",
            ))
            continue

        voltage = v_info[0]
        if voltage == 0.0:
            continue  # skip GND

        # Compute target impedance
        if (constraints.ripple_fraction is not None
                and constraints.transient_current is not None
                and constraints.transient_current > 0):
            z_target = (voltage * constraints.ripple_fraction
                        / constraints.transient_current)
            z_source = "user"
        elif rail_name in rail_load_current:
            # Use harness-declared load current with default 5% ripple
            ripple = constraints.ripple_fraction or 0.05
            z_target = voltage * ripple / rail_load_current[rail_name]
            z_source = "harness_load"
        else:
            z_target = _tier_z_target(voltage)
            z_source = "tier_default"

        caps = rails_with_caps.get(rail_name, [])

        rail_result = RailPDNResult(
            net_name=rail_name,
            voltage=voltage,
            z_target=z_target,
            z_target_source=z_source,
            caps=caps,
            frequencies=list(freqs),
        )

        if not caps:
            rail_result.findings.append(SimulationFinding(
                severity=FindingSeverity.WARNING,
                message=f"No decoupling caps found on {rail_name}",
                nets=[rail_name],
                category="no_decoupling",
            ))
            rail_result.combined_impedance = [float("inf")] * len(freqs)
            rail_result.violations = [(f, float("inf")) for f in freqs]
            if freqs:
                rail_result.worst_z = float("inf")
                rail_result.worst_freq = freqs[0]
        else:
            combined = []
            violations = []
            worst_z = 0.0
            worst_freq = freqs[0] if freqs else None
            for f in freqs:
                z = _parallel_impedance(caps, f)
                combined.append(z)
                if z > z_target:
                    violations.append((f, z))
                if z > worst_z:
                    worst_z = z
                    worst_freq = f
            rail_result.combined_impedance = combined
            rail_result.violations = violations
            rail_result.worst_z = worst_z
            rail_result.worst_freq = worst_freq

            if violations:
                rail_result.findings.append(SimulationFinding(
                    severity=FindingSeverity.WARNING,
                    message=(
                        f"{rail_name}: impedance exceeds target at "
                        f"{len(violations)}/{len(freqs)} frequency points, "
                        f"worst {worst_z * 1000:.1f}mΩ @ {_freq_str(worst_freq)}"
                    ),
                    nets=[rail_name],
                    category="pdn_violation",
                ))

        report.rails.append(rail_result)
        report.findings.extend(rail_result.findings)

    return report


def _log_space(start: float, stop: float, num: int) -> list[float]:
    if num <= 0 or start <= 0 or stop <= 0:
        return []
    if num == 1:
        return [start]
    log_start = math.log10(start)
    log_stop = math.log10(stop)
    step = (log_stop - log_start) / (num - 1)
    return [10 ** (log_start + i * step) for i in range(num)]
