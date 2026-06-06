from __future__ import annotations

import re
from dataclasses import dataclass, field

from .backends import OptionalBackendStatus, optional_backend_status
from .constraints import EdgeAnchor, KeepOut
from .roles import GND_NET_RE, POWER_NET_RE, PartRole, classify_parts, pin_net_names


CHANNEL_RE = re.compile(r"(?:^|[_/.-])(?:CH|CHAN|CHANNEL)(\d+)(?:[_/.-]|$)", re.I)
MUX_RE = re.compile(r"(mux|multiplex|tca954|pca954|switch)", re.I)
RF_RE = re.compile(r"(antenna|rf|wifi|wi-fi|ble|bluetooth|esp32|nrf52|wroom)", re.I)
UI_RE = re.compile(r"(button|switch|encoder|pot|display|oled|lcd|led)", re.I)
DEBUG_RE = re.compile(r"(swd|jtag|icsp|debug|program|uart|serial)", re.I)
POWER_INPUT_RE = re.compile(r"(usb|barrel|battery|batt|jst|terminal|power)", re.I)


@dataclass
class PlacementIntent:
    ref: str
    kind: str
    priority: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class RepeatedChannelIntent:
    name: str
    refs: list[str] = field(default_factory=list)
    channel_numbers: list[int] = field(default_factory=list)
    pattern: str = ""


@dataclass
class PlacementIntentPlan:
    intents: dict[str, list[PlacementIntent]] = field(default_factory=dict)
    edge_anchors: list[EdgeAnchor] = field(default_factory=list)
    keepouts: list[KeepOut] = field(default_factory=list)
    repeated_channels: list[RepeatedChannelIntent] = field(default_factory=list)
    backend_status: OptionalBackendStatus = field(default_factory=optional_backend_status)
    warnings: list[str] = field(default_factory=list)

    def intents_for(self, ref: str) -> list[PlacementIntent]:
        return self.intents.get(ref, [])

    def refs_with_kind(self, kind: str) -> list[str]:
        return [
            ref
            for ref, intents in self.intents.items()
            if any(intent.kind == kind for intent in intents)
        ]

    def summary(self) -> str:
        lines = ["Placement intent:"]
        if self.backend_status.enabled:
            lines.append(
                "  optional backends: " + ", ".join(self.backend_status.enabled)
            )
        kind_counts: dict[str, int] = {}
        for intents in self.intents.values():
            for intent in intents:
                kind_counts[intent.kind] = kind_counts.get(intent.kind, 0) + 1
        for kind, count in sorted(kind_counts.items()):
            lines.append(f"  {kind}: {count}")
        if self.edge_anchors:
            lines.append(f"  inferred edge anchors: {len(self.edge_anchors)}")
        if self.repeated_channels:
            lines.append(f"  repeated channel groups: {len(self.repeated_channels)}")
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings[:20]:
                lines.append(f"  {warning}")
        return "\n".join(lines)


def _part_text(part) -> str:
    chunks = [
        getattr(part, "ref", ""),
        getattr(part, "name", ""),
        getattr(part, "value", ""),
        getattr(part, "foot", ""),
        getattr(part, "footprint", ""),
        getattr(part, "description", ""),
    ]
    return " ".join(str(chunk or "") for chunk in chunks).lower()


def _add_intent(
    plan: PlacementIntentPlan,
    ref: str,
    kind: str,
    priority: int,
    reason: str,
):
    plan.intents.setdefault(ref, []).append(
        PlacementIntent(ref=ref, kind=kind, priority=priority, reasons=[reason])
    )


def _edge_for_part(text: str, role: PartRole, nets: list[str]) -> str | None:
    if "usb" in text:
        return "bottom"
    if DEBUG_RE.search(text):
        return "right"
    if UI_RE.search(text) and role.role == "connector":
        return "right"
    if POWER_INPUT_RE.search(text) or any(
        POWER_NET_RE.match(net) and not GND_NET_RE.match(net) for net in nets
    ):
        return "bottom"
    if role.role == "connector":
        return "right"
    return None


def _infer_repeated_channels(circuit) -> list[RepeatedChannelIntent]:
    channel_refs: dict[int, set[str]] = {}
    for part in circuit.parts:
        for net_name in pin_net_names(part):
            match = CHANNEL_RE.search(net_name)
            if match is None:
                continue
            channel_refs.setdefault(int(match.group(1)), set()).add(part.ref)

    if len(channel_refs) < 2:
        return []

    refs = sorted({ref for refs_for_ch in channel_refs.values() for ref in refs_for_ch})
    return [
        RepeatedChannelIntent(
            name="channel",
            refs=refs,
            channel_numbers=sorted(channel_refs),
            pattern="channel-numbered net names",
        )
    ]


def infer_placement_intents(
    circuit,
    outline=None,
    backend_status: OptionalBackendStatus | None = None,
) -> PlacementIntentPlan:
    """Infer first-draft placement intent from schematic roles and net names."""
    plan = PlacementIntentPlan(
        backend_status=backend_status or optional_backend_status()
    )
    roles = classify_parts(circuit)

    for part in circuit.parts:
        ref = str(getattr(part, "ref", "") or "")
        role = roles.get(ref)
        text = _part_text(part)
        nets = pin_net_names(part)

        if role is not None and role.role == "connector":
            edge = _edge_for_part(text, role, nets)
            _add_intent(plan, ref, "edge_connector", 90, "connector-like part")
            if edge is not None:
                offset = None
                if outline is not None and edge in {"top", "bottom"}:
                    offset = (outline.x_min + outline.x_max) / 2
                elif outline is not None:
                    offset = (outline.y_min + outline.y_max) / 2
                plan.edge_anchors.append(
                    EdgeAnchor(ref=ref, edge=edge, offset_mm=offset)
                )

        if UI_RE.search(text):
            _add_intent(plan, ref, "board_ui", 75, "UI-like metadata")

        if role is not None and role.role in {"regulator", "inductor", "diode"}:
            _add_intent(plan, ref, "power_cluster", 85, f"{role.role} role")

        if role is not None and role.role == "decoupling_cap":
            _add_intent(plan, ref, "decoupling", 80, "decoupling capacitor")

        if (
            MUX_RE.search(text)
            or sum(1 for net in nets if CHANNEL_RE.search(net)) >= 4
        ):
            _add_intent(plan, ref, "mux_bank_controller", 80, "channelized mux nets")

        if RF_RE.search(text):
            _add_intent(plan, ref, "rf_module", 85, "RF/antenna-like metadata")

        if role is not None and role.role == "crystal":
            _add_intent(plan, ref, "crystal_network", 80, "timing source")

        if DEBUG_RE.search(text):
            _add_intent(plan, ref, "test_debug", 80, "debug connector metadata")

        if any(POWER_NET_RE.match(net) for net in nets) and (
            POWER_INPUT_RE.search(text)
            or (role is not None and role.role == "connector")
        ):
            _add_intent(plan, ref, "power_input", 85, "connector on supply net")

    plan.repeated_channels = _infer_repeated_channels(circuit)
    return plan
