"""Reference oracle: compare generated circuit specs against real KiCad schematics.

Extracts a netlist from a reference .kicad_sch via kicad-cli, builds the same
structure from a CircuitSpec dict, and scores BOM / netlist similarity.

CLI self-test:
    python3 -m corpus.reference_oracle --self-test {project_dir_or_sch}

Extracts a reference netlist, verifies identity comparison scores 1.0/1.0,
then verifies a mutated copy (2 components dropped, one net renamed)
scores < 1.0.
"""

import argparse
import copy
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from simp_sexp import Sexp


class OracleError(Exception):
    """Raised when a reference netlist cannot be extracted or parsed."""


@dataclass
class RefNetlist:
    # components: [{ref, value, footprint, libsource}]
    components: list = field(default_factory=list)
    # nets: [{name, nodes: [(ref, pin, pinfunction)]}]
    nets: list = field(default_factory=list)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def _first(sexp, key, default=""):
    """Value of the first direct child sublist named `key`, as a string.

    Note: simp_sexp's max_depth counts the sublist itself as depth 2 when
    searching from its parent, so direct children need max_depth=2.
    """
    for hit in sexp.search(key, max_depth=2):
        if len(hit) > 1:
            return str(hit[1])
        return default
    return default


def extract_netlist(sch_path):
    """Run kicad-cli netlist export on a .kicad_sch and parse it."""
    sch_path = Path(sch_path)
    if not sch_path.exists():
        raise OracleError(f"schematic not found: {sch_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "netlist.net"
        try:
            proc = subprocess.run(
                ["kicad-cli", "sch", "export", "netlist",
                 "--format", "kicadsexpr", "-o", str(out), str(sch_path)],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise OracleError(f"kicad-cli failed to run: {exc}") from exc
        if proc.returncode != 0 or not out.exists():
            raise OracleError(
                f"kicad-cli netlist export failed (rc={proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:500]}"
            )
        text = out.read_text(errors="replace")

    try:
        sexp = Sexp(text)
    except Exception as exc:
        raise OracleError(f"failed to parse netlist s-expression: {exc}") from exc

    components = []
    for comp in sexp.search("components/comp"):
        libsource = ""
        for ls in comp.search("libsource", max_depth=2):
            lib, part = _first(ls, "lib"), _first(ls, "part")
            libsource = f"{lib}:{part}" if lib else part
        components.append({
            "ref": _first(comp, "ref"),
            "value": _first(comp, "value"),
            "footprint": _first(comp, "footprint"),
            "libsource": libsource,
        })

    nets = []
    for net in sexp.search("nets/net"):
        nodes = []
        for node in net.search("node", max_depth=2):
            ref = _first(node, "ref")
            pin = _first(node, "pin")
            pinfunction = _first(node, "pinfunction") or None
            nodes.append((ref, pin, pinfunction))
        nets.append({"name": _first(net, "name"), "nodes": nodes})

    if not components:
        raise OracleError(f"no components found in netlist for {sch_path}")
    return RefNetlist(components=components, nets=nets)


def spec_netlist(spec_dict):
    """Build a RefNetlist from a CircuitSpec dict (parts + nets)."""
    components = []
    for part in spec_dict.get("parts", []):
        lib, name = part.get("lib", ""), part.get("part", "")
        components.append({
            "ref": str(part.get("ref", "")),
            "value": str(part.get("value", "")),
            "footprint": str(part.get("footprint", "")),
            "libsource": f"{lib}:{name}" if lib else name,
        })

    nets = []
    for net in spec_dict.get("nets", []):
        nodes = []
        for node in net.get("nodes", []):
            if isinstance(node, dict):
                ref = str(node.get("ref", ""))
                pin = str(node.get("pin", ""))
                pinfunction = node.get("pinfunction") or None
            else:  # (ref, pin) or (ref, pin, pinfunction)
                ref = str(node[0])
                pin = str(node[1]) if len(node) > 1 else ""
                pinfunction = node[2] if len(node) > 2 and node[2] else None
            nodes.append((ref, pin, pinfunction))
        nets.append({"name": str(net.get("name", "")), "nodes": nodes})

    return RefNetlist(components=components, nets=nets)


# --------------------------------------------------------------------------
# Component keys + BOM score
# --------------------------------------------------------------------------

PASSIVE_PREFIXES = {"R", "C", "L", "D"}
FP_SIZE_RE = re.compile(r"(0402|0603|0805|1206)")


def _normalize_value(value):
    """Lowercase, strip spaces and ohm/F/H unit suffixes: '4.7kΩ'->'4.7k'."""
    val = re.sub(r"\s+", "", str(value).lower())
    val = val.replace("ω", "").replace("ohms", "").replace("ohm", "")
    # strip trailing farad/henry unit letter ("100nf" -> "100n", "10uh" -> "10u")
    if len(val) > 1 and val[-1] in ("f", "h"):
        val = val[:-1]
    return val


def component_key(comp):
    """Matching key for a component dict.

    Passives (R/C/L/D): (prefix, normalized_value, fp_size_token).
    Everything else: normalized part name (uppercase alnum only).
    """
    ref = str(comp.get("ref", ""))
    prefix = re.match(r"[A-Za-z]+", ref)
    prefix = prefix.group(0).upper() if prefix else ""
    if prefix in PASSIVE_PREFIXES:
        m = FP_SIZE_RE.search(str(comp.get("footprint", "")))
        return (prefix, _normalize_value(comp.get("value", "")), m.group(1) if m else "")
    libsource = str(comp.get("libsource", ""))
    name = libsource.split(":", 1)[-1] if libsource else str(comp.get("value", ""))
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def bom_match_score(gen, ref):
    """Multiset overlap of component keys: sum(min counts) / max(total counts)."""
    gen_keys = Counter(component_key(c) for c in gen.components)
    ref_keys = Counter(component_key(c) for c in ref.components)
    total_gen, total_ref = sum(gen_keys.values()), sum(ref_keys.values())
    if not total_gen and not total_ref:
        return 1.0
    if not total_gen or not total_ref:
        return 0.0
    overlap = sum(min(gen_keys[k], ref_keys[k]) for k in gen_keys.keys() & ref_keys.keys())
    return overlap / max(total_gen, total_ref)


# --------------------------------------------------------------------------
# Net signatures + netlist score
# --------------------------------------------------------------------------

def _pin_label(pin, pinfunction):
    if pinfunction:
        return str(pinfunction)
    return str(pin)


def net_signature(net, comp_key_by_ref):
    """Multiset (Counter) of (component_key, pin_label) over the net's nodes.

    Nodes whose ref has no known component key are skipped.
    """
    sig = Counter()
    for ref, pin, pinfunction in net["nodes"]:
        key = comp_key_by_ref.get(ref)
        if key is None:
            continue
        sig[(key, _pin_label(pin, pinfunction))] += 1
    return sig


def _multiset_jaccard(a, b):
    union = sum((a | b).values())
    if not union:
        return 0.0
    return sum((a & b).values()) / union


def _signatures(netlist):
    comp_key_by_ref = {c["ref"]: component_key(c) for c in netlist.components}
    sigs = []
    for net in netlist.nets:
        if len(net["nodes"]) < 2:  # exclude single-node nets
            continue
        sig = net_signature(net, comp_key_by_ref)
        if sig:
            sigs.append(sig)
    return sigs


def netlist_match_score(gen, ref):
    """Greedy best-Jaccard matching of net signatures.

    Score = sum of matched Jaccard similarities / number of reference nets.
    Single-node nets are excluded from both sides.
    """
    gen_sigs, ref_sigs = _signatures(gen), _signatures(ref)
    if not ref_sigs:
        return 1.0 if not gen_sigs else 0.0
    if not gen_sigs:
        return 0.0

    pairs = []
    for gi, gsig in enumerate(gen_sigs):
        for ri, rsig in enumerate(ref_sigs):
            j = _multiset_jaccard(gsig, rsig)
            if j > 0.0:
                pairs.append((j, gi, ri))
    pairs.sort(key=lambda t: t[0], reverse=True)

    used_gen, used_ref, total = set(), set(), 0.0
    for j, gi, ri in pairs:
        if gi in used_gen or ri in used_ref:
            continue
        used_gen.add(gi)
        used_ref.add(ri)
        total += j
    return total / len(ref_sigs)


# --------------------------------------------------------------------------
# Self-test CLI
# --------------------------------------------------------------------------

def _find_root_schematic(path):
    """Locate the root .kicad_sch for a project dir (or pass a file through)."""
    path = Path(path)
    if path.is_file():
        return path
    schs = sorted(path.glob("*.kicad_sch"))
    if not schs:
        raise OracleError(f"no .kicad_sch files in {path}")
    # Prefer the schematic whose stem matches a .kicad_pro (hierarchical root).
    for pro in sorted(path.glob("*.kicad_pro")):
        for sch in schs:
            if sch.stem == pro.stem:
                return sch
    # Fall back to a schematic that references child sheets, else the first.
    for sch in schs:
        if "(sheet" in sch.read_text(errors="replace"):
            return sch
    return schs[0]


def _mutate(netlist):
    """Drop 2 connected components and rename a net. Must lower both scores."""
    mutated = copy.deepcopy(netlist)

    connected_refs = []
    seen = set()
    for net in mutated.nets:
        for ref, _pin, _fn in net["nodes"]:
            if ref not in seen:
                seen.add(ref)
                connected_refs.append(ref)
    drop = set(connected_refs[:2])
    if len(drop) < 2:
        raise OracleError("not enough connected components to mutate")

    mutated.components = [c for c in mutated.components if c["ref"] not in drop]
    for net in mutated.nets:
        net["nodes"] = [n for n in net["nodes"] if n[0] not in drop]

    for net in mutated.nets:
        if len(net["nodes"]) >= 2:
            net["name"] = net["name"] + "_RENAMED"
            break
    return mutated, drop


def self_test(project_dir):
    sch = _find_root_schematic(project_dir)
    print(f"root schematic: {sch}")

    ref = extract_netlist(sch)
    multi = sum(1 for n in ref.nets if len(n["nodes"]) >= 2)
    print(f"extracted {len(ref.components)} components, {len(ref.nets)} nets "
          f"({multi} multi-node)")

    id_bom = bom_match_score(ref, ref)
    id_net = netlist_match_score(ref, ref)
    print(f"identity:  bom={id_bom:.4f}  netlist={id_net:.4f}")

    mutated, dropped = _mutate(ref)
    mut_bom = bom_match_score(mutated, ref)
    mut_net = netlist_match_score(mutated, ref)
    print(f"mutated (dropped {sorted(dropped)}, renamed one net): "
          f"bom={mut_bom:.4f}  netlist={mut_net:.4f}")

    ok = True
    if not (id_bom == 1.0 and id_net == 1.0):
        print("FAIL: identity comparison must score 1.0/1.0")
        ok = False
    if not (mut_bom < 1.0 and mut_net < 1.0):
        print("FAIL: mutated comparison must score below 1.0")
        ok = False
    print("self-test PASSED" if ok else "self-test FAILED")
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", metavar="PROJECT_DIR", default=None,
                        help="Run the oracle self-test against a KiCad project dir")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test(args.self_test)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
