#!/usr/bin/env python3
"""
net_lint.py — orphan / cross-sheet-disconnect net lint for KiCad/SKiDL designs.

THE BUG CLASS
=============
When a SKiDL design creates ``Net("NAME")`` inline in more than one subcircuit
(instead of threading a single shared net object through), SKiDL emits them as
SEPARATE electrical nets and disambiguates by appending a numeric suffix:
a bare ``NAME`` net plus ``NAME1``, ``NAME2``, ... that are NOT connected to
each other. The author almost certainly meant ONE net. KiCad ERC misses this:
the halves become dangling global labels, or get padded by test points, so no
"unconnected pin" is reported.

This tool parses a ``.kicad_netlist`` s-expression, reconstructs net -> nodes,
and flags those families.

DISTINGUISHING A BUG FROM A LEGITIMATE BUS
==========================================
A real bus (``SD_D0, SD_D1, SD_D2, SD_D3`` or ``IO0, IO1``) is intentionally a
set of distinct nets and must NOT be flagged. The discriminating signal:

    The SKiDL collision pattern always has a BARE base name (``ENC_A``) existing
    as its own electrical net ALONGSIDE suffixed variants (``ENC_A1``, ``ENC_A2``).
    A bus has NO bare-base net (there is no plain ``SD_D``).

So the rule (see ``find_disconnects``) is:

    Flag a base name B as a probable disconnect when:
      1. a net named exactly B exists, AND
      2. one or more nets named ``B<digits>`` exist, AND
      3. they are electrically separate (different net codes), AND
      4. (strengthening signal) the suffixed variants look like fragments
         i.e. each has <= ``min_suffix_nodes`` nodes.

Condition 4 is tunable via ``--min-suffix-nodes`` (default 2). Setting it very
high makes the lint more aggressive; the default keeps it to small fragments,
which is what the SKiDL collision actually produces (each inline ``Net`` usually
gets one or two pins in a given subcircuit).

NOTE FOR LATER (SKiDL integration)
==================================
``find_disconnects`` operates purely on a list of ``NetEntry`` records
(name + code + node count). The same function can be fed SKiDL ``Net`` objects
from an in-memory ``Circuit`` before netlist emission — build ``NetEntry`` from
``net.name``, ``id(net)`` (as code), ``len(net.pins)`` — so the detection logic
is reusable as a build-time SKiDL lint. The parsing here is the PoC layer only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Node:
    ref: str
    pin: str

    def __str__(self) -> str:
        return f"{self.ref}.{self.pin}"


@dataclass
class NetEntry:
    """One electrical net. ``code`` is the unique netlist code (or any unique id)."""
    name: str          # raw name as it appears in the netlist
    code: str          # unique net code
    nodes: list = field(default_factory=list)  # list[Node] (or anything len()-able)

    @property
    def leaf_name(self) -> str:
        """Strip KiCad hierarchical-path prefix: ``/sheet/ENC_A`` -> ``ENC_A``."""
        return leaf_name(self.name)

    @property
    def node_count(self) -> int:
        return len(self.nodes)


# --------------------------------------------------------------------------- #
# Name helpers
# --------------------------------------------------------------------------- #

# A net leaf name that is a base + trailing digits, e.g. ENC_A1 -> (ENC_A, 1)
_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?P<num>\d+)$")


def leaf_name(name: str) -> str:
    """Return the leaf component of a (possibly hierarchical) net name.

    KiCad may present a net as ``/sheet/sub/ENC_A``. We compare on the final
    path segment. A leading '/' with no further segments (root) yields the
    remainder. Names with no '/' are returned unchanged.
    """
    n = name.strip()
    if "/" in n:
        n = n.rsplit("/", 1)[-1]
    return n


def split_base_suffix(leaf: str):
    """Split ``ENC_A1`` -> ('ENC_A', '1'); ``ENC_A`` -> ('ENC_A', None).

    Returns (base, numeric_suffix_or_None).
    """
    m = _SUFFIX_RE.match(leaf)
    if not m:
        return leaf, None
    return m.group("base"), m.group("num")


# --------------------------------------------------------------------------- #
# S-expression parsing (minimal, stdlib only)
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r'\s+|(?<![\\])"|;.*?$|[()]', re.MULTILINE)


def tokenize(text: str):
    """Tokenize a KiCad s-expression into '(' , ')' , and atom tokens.

    Handles double-quoted strings (which may contain spaces and parens).
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "()":
            tokens.append(c)
            i += 1
        elif c == '"':
            # read quoted string, honoring backslash escapes
            j = i + 1
            buf = []
            while j < n:
                cj = text[j]
                if cj == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if cj == '"':
                    break
                buf.append(cj)
                j += 1
            tokens.append(("STR", "".join(buf)))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            tokens.append(("SYM", text[i:j]))
            i = j
    return tokens


def parse_sexp(text: str):
    """Parse s-expression text into nested lists. Atoms are strings."""
    tokens = tokenize(text)
    pos = 0

    def parse():
        nonlocal pos
        tok = tokens[pos]
        if tok == "(":
            pos += 1
            lst = []
            while tokens[pos] != ")":
                lst.append(parse())
            pos += 1  # consume ')'
            return lst
        elif tok == ")":
            raise ValueError("unexpected )")
        else:
            pos += 1
            return tok[1]  # the atom value (SYM or STR payload)

    result = []
    while pos < len(tokens):
        result.append(parse())
    return result[0] if len(result) == 1 else result


def _find_all(node, key):
    """Yield every sub-list whose head atom == key, recursively."""
    if isinstance(node, list):
        if node and node[0] == key:
            yield node
        for child in node:
            yield from _find_all(child, key)


def _first(node, key):
    """Return the value following the first atom == key in this list's children."""
    if isinstance(node, list):
        for i, child in enumerate(node):
            if isinstance(child, list) and child and child[0] == key:
                return child[1] if len(child) > 1 else None
    return None


def parse_netlist(path: str) -> list:
    """Parse a .kicad_netlist file into a list[NetEntry]."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    tree = parse_sexp(text)

    nets = []
    for net in _find_all(tree, "net"):
        code = _first(net, "code")
        name = _first(net, "name")
        if name is None:
            continue
        nodes = []
        for node in net:
            if isinstance(node, list) and node and node[0] == "node":
                ref = _first(node, "ref")
                pin = _first(node, "pin")
                nodes.append(Node(ref=ref or "?", pin=pin or "?"))
        nets.append(NetEntry(name=name, code=str(code), nodes=nodes))
    return nets


# --------------------------------------------------------------------------- #
# The detection logic (reusable; could be fed SKiDL Net objects)
# --------------------------------------------------------------------------- #

@dataclass
class Suspect:
    base: str                 # leaf base name, e.g. "ENC_A"
    bare: NetEntry            # the net named exactly base
    variants: list            # list[NetEntry] named base + digits
    @property
    def all_nets(self):
        return [self.bare] + self.variants


def find_disconnects(nets: Iterable[NetEntry], min_suffix_nodes: int = 2):
    """Identify probable cross-sheet net disconnects.

    See module docstring for the rule. Returns list[Suspect].

    ``min_suffix_nodes``: a base is only flagged if EVERY suffixed variant has
    <= this many nodes (the "fragment" strengthening signal). Raise it to be
    more aggressive, lower (e.g. 1) to be stricter.
    """
    # Group nets by leaf name.
    by_leaf: dict[str, list[NetEntry]] = defaultdict(list)
    for ne in nets:
        by_leaf[ne.leaf_name].append(ne)

    # Index: base name -> {suffix or None -> [nets]}
    families: dict[str, dict] = defaultdict(lambda: {"bare": [], "suffixed": []})
    for leaf, entries in by_leaf.items():
        base, num = split_base_suffix(leaf)
        if num is None:
            families[leaf]["bare"].extend(entries)
        else:
            families[base]["suffixed"].extend(entries)

    suspects = []
    for base, fam in families.items():
        bare_nets = fam["bare"]
        suffixed = fam["suffixed"]
        if not bare_nets or not suffixed:
            # No bare base => looks like a bus (SD_D0..3). Skip.
            # Bare base but no suffixed => ordinary single net. Skip.
            continue

        bare = bare_nets[0]
        bare_codes = {b.code for b in bare_nets}

        # Condition 3: electrically separate (different net codes).
        sep_variants = [s for s in suffixed if s.code not in bare_codes]
        if not sep_variants:
            continue

        # Condition 4: strengthening fragment signal — every separate variant
        # is small. (If a "variant" is large it may be a real distinct net.)
        if not all(s.node_count <= min_suffix_nodes for s in sep_variants):
            continue

        suspects.append(Suspect(base=base, bare=bare, variants=sep_variants))

    suspects.sort(key=lambda s: s.base)
    return suspects


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def format_report(suspects: list, total_nets: int) -> str:
    lines = []
    if not suspects:
        lines.append(f"net-lint: OK — no cross-sheet disconnects found "
                     f"({total_nets} nets scanned).")
        return "\n".join(lines)

    lines.append(f"net-lint: {len(suspects)} suspect net family(ies) found "
                 f"out of {total_nets} nets scanned.\n")
    for s in suspects:
        all_nets = s.all_nets
        node_total = sum(n.node_count for n in all_nets)
        lines.append(f"  [SUSPECT] base '{s.base}' split across "
                     f"{len(all_nets)} electrically-separate nets:")
        for ne in all_nets:
            nodes = ", ".join(str(x) for x in ne.nodes) or "(no nodes)"
            tag = "bare " if ne is s.bare else "     "
            lines.append(f"      {tag}{ne.leaf_name:<16} (code {ne.code}): {nodes}")
        lines.append(f"      -> likely meant to be ONE net "
                     f"({node_total} nodes total across the family).\n")
    return "\n".join(lines)


def build_json(suspects: list, total_nets: int) -> dict:
    return {
        "total_nets": total_nets,
        "suspect_count": len(suspects),
        "suspects": [
            {
                "base": s.base,
                "nets": [
                    {
                        "name": ne.leaf_name,
                        "raw_name": ne.name,
                        "code": ne.code,
                        "is_bare_base": (ne is s.bare),
                        "node_count": ne.node_count,
                        "nodes": [str(x) for x in ne.nodes],
                    }
                    for ne in s.all_nets
                ],
            }
            for s in suspects
        ],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Flag probable cross-sheet net disconnects in a KiCad netlist "
                    "(SKiDL inline-Net collision: bare NAME + NAME1/NAME2 not joined)."
    )
    ap.add_argument("netlist", help="path to .kicad_netlist (s-expression)")
    ap.add_argument("--json", dest="json_path", metavar="FILE",
                    help="also write a JSON report to FILE")
    ap.add_argument("--min-suffix-nodes", type=int, default=2, metavar="N",
                    help="flag only when each suffixed variant has <= N nodes "
                         "(default 2; raise to be more aggressive)")
    args = ap.parse_args(argv)

    nets = parse_netlist(args.netlist)
    suspects = find_disconnects(nets, min_suffix_nodes=args.min_suffix_nodes)

    print(format_report(suspects, total_nets=len(nets)))

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(build_json(suspects, len(nets)), f, indent=2)

    return 1 if suspects else 0


if __name__ == "__main__":
    sys.exit(main())
