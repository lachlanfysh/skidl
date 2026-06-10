"""Deterministic spec mutations — how candidate corrections are applied.

apply_candidate is pure: it deep-copies the spec, applies one candidate's
action, and returns the new spec. The engine's constraints re-validate the
result on the next run, so a bad pick re-raises rather than silently
producing garbage.
"""

from __future__ import annotations

import math

from .circuit_spec import CircuitSpec, NetSpec
from .exceptions import ActionType, Candidate, DesignException


class CorrectionError(ValueError):
    """Raised when a candidate's params can't be applied to this spec."""


def apply_candidate(spec: CircuitSpec, exc: DesignException, cand: Candidate) -> CircuitSpec:
    new = spec.model_copy(deep=True)
    p = cand.params

    if cand.action == ActionType.REPLACE_LIB:
        old, repl = p["old"], p["new"]
        scope_ref = p.get("ref")
        hit = False
        for part in new.parts:
            if part.lib == old and (scope_ref in (None, "*") or part.ref == scope_ref):
                part.lib = repl
                hit = True
        if not hit:
            raise CorrectionError(f"no part with lib={old!r} (ref scope {scope_ref!r})")

    elif cand.action == ActionType.REPLACE_PART:
        part = new.part_by_ref(p["ref"])
        if part is None:
            raise CorrectionError(f"no part with ref {p['ref']!r}")
        part.part = p["new"]

    elif cand.action == ActionType.REPLACE_PIN:
        ref, old, repl = p["ref"], p["old"], p["new"]
        old_key, new_key = f"{ref}.{old}", f"{ref}.{repl}"
        hit = False
        for net in new.nets:
            net.pins = [new_key if pin == old_key else pin for pin in net.pins]
            hit = hit or new_key in net.pins
        if not hit:
            raise CorrectionError(f"pin {old_key!r} not found on any net")

    elif cand.action == ActionType.REPLACE_FOOTPRINT:
        old, repl = p["old"], p["new"]
        hit = False
        for part in new.parts:
            if part.footprint == old:
                part.footprint = repl
                hit = True
        if not hit:
            raise CorrectionError(f"no part with footprint {old!r}")

    elif cand.action == ActionType.REMOVE_PART:
        ref = p["ref"]
        if new.part_by_ref(ref) is None:
            raise CorrectionError(f"no part with ref {ref!r}")
        new.parts = [part for part in new.parts if part.ref != ref]
        prefix = f"{ref}."
        kept: list[NetSpec] = []
        for net in new.nets:
            net.pins = [pin for pin in net.pins if not pin.startswith(prefix)]
            if net.pins:
                kept.append(net)
        new.nets = kept

    elif cand.action == ActionType.REMOVE_NET_PIN:
        net_name, pin = p["net"], p["pin"]
        net = next((n for n in new.nets if n.name == net_name), None)
        if net is None or pin not in net.pins:
            raise CorrectionError(f"pin {pin!r} not on net {net_name!r}")
        net.pins.remove(pin)
        if not net.pins:
            new.nets.remove(net)

    elif cand.action == ActionType.STUB_NET:
        net = next((n for n in new.nets if n.name == p["net"]), None)
        if net is None:
            raise CorrectionError(f"no net named {p['net']!r}")
        net.stub = True

    elif cand.action == ActionType.SET_FORM_FACTOR:
        new.board.form_factor = p["name"]
        new.board.outline_hint_mm = None

    elif cand.action == ActionType.SET_OUTLINE:
        new.board.outline_hint_mm = (float(p["w_mm"]), float(p["h_mm"]))
        new.board.form_factor = None

    elif cand.action == ActionType.SCALE_OUTLINE:
        factor = math.sqrt(float(p["area_factor"]))
        if new.board.outline_hint_mm:
            w, h = new.board.outline_hint_mm
        else:
            # No explicit outline to scale — drop form factor (it was too
            # tight) and let the engine re-derive, biased larger.
            w, h = p.get("base_w_mm", 50.0), p.get("base_h_mm", 50.0)
        new.board.outline_hint_mm = (round(w * factor, 2), round(h * factor, 2))
        new.board.form_factor = None

    elif cand.action == ActionType.ACCEPT_ADVISORY:
        key = exc.waiver_key()
        if key not in new.waivers:
            new.waivers.append(key)

    elif cand.action == ActionType.REGENERATE:
        pass

    else:  # pragma: no cover — enum is exhaustive
        raise CorrectionError(f"unknown action {cand.action!r}")

    return new
