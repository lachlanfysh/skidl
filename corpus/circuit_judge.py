"""Deterministic circuit quality scorer.

Wraps reference_oracle.py to provide structural analysis beyond BOM/netlist
similarity: power connectivity, decoupling coverage, floating input detection.

CLI self-test:
    python3 -m corpus.circuit_judge
"""

from __future__ import annotations

import copy
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from corpus.reference_oracle import (
    bom_match_score,
    component_key,
    netlist_match_score,
    spec_netlist,
)

POWER_PIN_RE = re.compile(
    r"^(V(CC|DD|DDIO|DDA|SSA|SS|IN|OUT|BAT|REF|BUS)|"
    r"A?V(CC|DD)|D?V(CC|DD)|IOV(DD)|"
    r"GND|A?GND|D?GND|VSS|AVSS|DVSS)$",
    re.IGNORECASE,
)

POWER_NET_RE = re.compile(
    r"^(V(CC|DD|DDA|DDIO|SS|IN|OUT|BAT|REF|BUS)|"
    r"A?V(CC|DD)|D?V(CC|DD)|IOV(DD)|"
    r"GND|A?GND|D?GND|VSS|AVSS|DVSS|"
    r"\+\d+(\.\d+)?V(\d+)?|"
    r"\+3\.?3V?|"
    r"\+5V?)$",
    re.IGNORECASE,
)

GROUND_NET_RE = re.compile(
    r"^(GND|A?GND|D?GND|VSS|AVSS|DVSS|GNDA|GNDD)$",
    re.IGNORECASE,
)

DECAP_VALUE_RE = re.compile(r"^(100n|0\.1u)", re.IGNORECASE)

_PASSIVE_CONNECTOR_PREFIXES = {"R", "C", "L", "D", "J", "SW", "F", "FB"}


@dataclass
class DeterministicScore:
    bom_score: float
    netlist_score: float
    part_count_gen: int
    part_count_ref: int
    missing_parts: list[str]
    extra_parts: list[str]
    power_pins_connected: bool
    floating_inputs: list[str]
    has_decoupling: bool
    decoupling_coverage: dict[str, bool]
    structural_warnings: list[str]
    structural_score: float


def _prepare_spec(spec_dict: dict) -> dict:
    data = copy.deepcopy(spec_dict)
    for net in data.get("nets", []):
        nodes = []
        for pin_ref in net.get("pins", []):
            if "." not in pin_ref:
                continue
            ref, pin = pin_ref.split(".", 1)
            nodes.append({"ref": ref, "pin": pin})
        net["nodes"] = nodes
    return data


def _is_passive_or_connector(ref: str) -> bool:
    prefix = re.match(r"[A-Za-z]+", ref)
    if not prefix:
        return False
    return prefix.group(0).upper() in _PASSIVE_CONNECTOR_PREFIXES


def _find_missing_parts(gen_nl, ref_nl) -> tuple[list[str], list[str]]:
    gen_keys = Counter(str(component_key(c)) for c in gen_nl.components)
    ref_keys = Counter(str(component_key(c)) for c in ref_nl.components)
    missing = []
    for key, count in ref_keys.items():
        deficit = count - gen_keys.get(key, 0)
        for _ in range(deficit):
            missing.append(key)
    extra = []
    for key, count in gen_keys.items():
        surplus = count - ref_keys.get(key, 0)
        for _ in range(surplus):
            extra.append(key)
    return missing, extra


def _check_power_connectivity(spec: dict) -> tuple[bool, list[str]]:
    ic_refs = set()
    for part in spec.get("parts", []):
        ref = str(part.get("ref", ""))
        if not _is_passive_or_connector(ref):
            ic_refs.add(ref)

    # Check each IC: does it have at least one pin on a power net and one on a ground net?
    # Two ways a pin counts: pin name matches power pattern, OR the net itself is a power/ground net.
    unconnected_power = []
    for part in spec.get("parts", []):
        ref = str(part.get("ref", ""))
        if ref not in ic_refs:
            continue
        has_power = False
        has_ground = False
        for net in spec.get("nets", []):
            net_name = net.get("name", "")
            is_power_net = net.get("power", False) or bool(POWER_NET_RE.match(net_name))
            is_ground_net = bool(GROUND_NET_RE.match(net_name))
            for node in net.get("nodes", []):
                node_ref = node["ref"] if isinstance(node, dict) else str(node[0])
                node_pin = node["pin"] if isinstance(node, dict) else str(node[1])
                if node_ref != ref:
                    continue
                if is_ground_net or GROUND_NET_RE.match(node_pin):
                    has_ground = True
                elif is_power_net or POWER_PIN_RE.match(node_pin):
                    has_power = True
        if not has_power:
            unconnected_power.append(f"{ref}:VCC")
        if not has_ground:
            unconnected_power.append(f"{ref}:GND")

    all_connected = len(unconnected_power) == 0
    return all_connected, unconnected_power


def _check_decoupling(spec: dict) -> dict[str, bool]:
    # Map each part ref to the power and ground nets it connects to
    ref_power_nets: dict[str, set[str]] = {}
    ref_ground_nets: dict[str, set[str]] = {}
    for net in spec.get("nets", []):
        net_name = net.get("name", "")
        is_power = net.get("power", False) or bool(POWER_NET_RE.match(net_name))
        is_ground = bool(GROUND_NET_RE.match(net_name))
        for node in net.get("nodes", []):
            ref = node["ref"] if isinstance(node, dict) else str(node[0])
            if is_ground:
                ref_ground_nets.setdefault(ref, set()).add(net_name)
            elif is_power:
                ref_power_nets.setdefault(ref, set()).add(net_name)

    # Identify capacitor refs with decoupling values
    cap_refs: set[str] = set()
    for part in spec.get("parts", []):
        ref = str(part.get("ref", ""))
        prefix = re.match(r"[A-Za-z]+", ref)
        if prefix and prefix.group(0).upper() == "C":
            value = str(part.get("value", "") or "")
            if DECAP_VALUE_RE.match(value):
                cap_refs.add(ref)

    coverage: dict[str, bool] = {}
    for part in spec.get("parts", []):
        ref = str(part.get("ref", ""))
        if _is_passive_or_connector(ref):
            continue
        ic_power = ref_power_nets.get(ref, set())
        ic_ground = ref_ground_nets.get(ref, set())
        if not ic_power and not ic_ground:
            coverage[ref] = False
            continue
        has_decap = False
        for cap_ref in cap_refs:
            cap_power = ref_power_nets.get(cap_ref, set())
            cap_ground = ref_ground_nets.get(cap_ref, set())
            shares_power = bool(ic_power & cap_power)
            shares_ground = bool(ic_ground & cap_ground)
            if shares_power and shares_ground:
                has_decap = True
                break
        coverage[ref] = has_decap

    return coverage


def score_deterministic(gen_spec: dict, ref_spec: dict) -> DeterministicScore:
    gen_prepared = _prepare_spec(gen_spec)
    ref_prepared = _prepare_spec(ref_spec)

    gen_nl = spec_netlist(gen_prepared)
    ref_nl = spec_netlist(ref_prepared)

    bom = bom_match_score(gen_nl, ref_nl)
    netlist = netlist_match_score(gen_nl, ref_nl)
    missing, extra = _find_missing_parts(gen_nl, ref_nl)
    power_connected, floating = _check_power_connectivity(gen_prepared)
    decoupling_cov = _check_decoupling(gen_prepared)
    has_decap = any(decoupling_cov.values()) if decoupling_cov else False

    warnings: list[str] = []
    if missing:
        warnings.append(f"{len(missing)} missing part(s) vs reference")
    if extra:
        warnings.append(f"{len(extra)} extra part(s) vs reference")
    if not power_connected:
        warnings.append(f"{len(floating)} IC power pin(s) unconnected")
    uncovered_ics = [ref for ref, covered in decoupling_cov.items() if not covered]
    if uncovered_ics:
        warnings.append(f"ICs without decoupling: {', '.join(sorted(uncovered_ics))}")

    power_score = 1.0 if power_connected else 0.0
    decap_score = 1.0 if has_decap else 0.0
    floating_score = max(0.0, 1.0 - len(floating) / 10.0)
    structural = (power_score + decap_score + floating_score) / 3.0

    return DeterministicScore(
        bom_score=bom,
        netlist_score=netlist,
        part_count_gen=len(gen_nl.components),
        part_count_ref=len(ref_nl.components),
        missing_parts=missing,
        extra_parts=extra,
        power_pins_connected=power_connected,
        floating_inputs=floating,
        has_decoupling=has_decap,
        decoupling_coverage=decoupling_cov,
        structural_warnings=warnings,
        structural_score=structural,
    )


if __name__ == "__main__":
    spec_path = Path(__file__).resolve().parent / "specs" / "bme280.json"
    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    with open(spec_path) as f:
        spec = json.load(f)

    print(f"loaded {spec_path.name}: {len(spec['parts'])} parts, {len(spec['nets'])} nets")

    # Identity test
    result = score_deterministic(spec, spec)
    print(f"\nidentity: bom={result.bom_score:.4f}  netlist={result.netlist_score:.4f}  "
          f"structural={result.structural_score:.4f}")
    assert result.bom_score == 1.0, f"expected bom=1.0, got {result.bom_score}"
    assert result.netlist_score == 1.0, f"expected netlist=1.0, got {result.netlist_score}"

    # Mutated test: remove 2 parts
    mutated = copy.deepcopy(spec)
    dropped = [p["ref"] for p in mutated["parts"][:2]]
    mutated["parts"] = mutated["parts"][2:]
    for net in mutated["nets"]:
        net["pins"] = [p for p in net["pins"] if p.split(".")[0] not in dropped]

    result_mut = score_deterministic(mutated, spec)
    print(f"mutated (dropped {dropped}): bom={result_mut.bom_score:.4f}  "
          f"netlist={result_mut.netlist_score:.4f}  structural={result_mut.structural_score:.4f}")
    print(f"  missing: {result_mut.missing_parts}")
    print(f"  extra:   {result_mut.extra_parts}")
    print(f"  warnings: {result_mut.structural_warnings}")
    assert result_mut.bom_score < 1.0, f"expected bom<1.0 after mutation, got {result_mut.bom_score}"

    print("\nself-test PASSED")
