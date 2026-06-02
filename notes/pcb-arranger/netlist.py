"""Minimal KiCad netlist (.kicad_netlist) parser.

Per DESIGN.md S12.1 (BINDING): connectivity + grouping come from the netlist,
joined to footprints by reference. We extract, per component:
  - ref, value, footprint, sheetpath name
And, per net:
  - net name + list of (ref, pin) nodes.

We use a small s-expression tokenizer rather than regex so nesting is correct.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _tokenize(text):
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            out.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            out.append(("STR", "".join(buf)))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            out.append(("SYM", text[i:j]))
            i = j
    return out


def _parse(tokens):
    """Parse token stream into nested lists. STR/SYM become ('s', val)/('y', val)."""
    pos = 0

    def parse_list():
        nonlocal pos
        assert tokens[pos] == "("
        pos += 1
        node = []
        while pos < len(tokens):
            t = tokens[pos]
            if t == ")":
                pos += 1
                return node
            if t == "(":
                node.append(parse_list())
            else:
                pos += 1
                node.append(t)  # ("STR"/"SYM", value)
        return node

    # Skip to first '('
    while pos < len(tokens) and tokens[pos] != "(":
        pos += 1
    return parse_list()


def _head(node):
    """Symbol name heading an s-expr list, or None."""
    if isinstance(node, list) and node and isinstance(node[0], tuple) and node[0][0] == "SYM":
        return node[0][1]
    return None


def _children(node, name):
    return [c for c in node if isinstance(c, list) and _head(c) == name]


def _child(node, name):
    cs = _children(node, name)
    return cs[0] if cs else None


def _val(node):
    """First STR or SYM payload after the head symbol."""
    if not isinstance(node, list):
        return None
    for c in node[1:]:
        if isinstance(c, tuple):
            return c[1]
    return None


@dataclass
class NLComp:
    ref: str
    value: str = ""
    footprint: str = ""
    sheet: str = "/"


@dataclass
class NLNet:
    name: str
    code: str = ""
    nodes: list = field(default_factory=list)  # list of (ref, pin)


@dataclass
class Netlist:
    comps: dict = field(default_factory=dict)        # ref -> NLComp
    nets: list = field(default_factory=list)         # list[NLNet]
    ref_pin_to_net: dict = field(default_factory=dict)  # (ref, pin) -> net name


def parse_netlist(path) -> Netlist:
    text = open(path, "r", encoding="utf-8").read()
    root = _parse(_tokenize(text))
    nl = Netlist()

    comps_node = _child(root, "components")
    if comps_node:
        for comp in _children(comps_node, "comp"):
            ref = _val(_child(comp, "ref"))
            if ref is None:
                continue
            value = _val(_child(comp, "value")) or ""
            fp = _val(_child(comp, "footprint")) or ""
            sheet = "/"
            sp = _child(comp, "sheetpath")
            if sp:
                names = _child(sp, "names")
                if names:
                    sheet = _val(names) or "/"
            nl.comps[ref] = NLComp(ref=ref, value=value, footprint=fp, sheet=sheet)

    nets_node = _child(root, "nets")
    if nets_node:
        for net in _children(nets_node, "net"):
            name = _val(_child(net, "name")) or ""
            code = _val(_child(net, "code")) or ""
            nn = NLNet(name=name, code=code)
            for node in _children(net, "node"):
                r = _val(_child(node, "ref"))
                p = _val(_child(node, "pin"))
                if r is not None and p is not None:
                    nn.nodes.append((r, p))
                    nl.ref_pin_to_net[(r, p)] = name
            nl.nets.append(nn)

    return nl
