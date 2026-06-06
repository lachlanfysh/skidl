from __future__ import annotations

import re
from dataclasses import dataclass, field

from .backends import OptionalBackendStatus, optional_backend_status
from .constraints import EdgeAnchor, FaceEdgeConstraint, KeepOut
from .roles import GND_NET_RE, POWER_NET_RE, PartRole, classify_parts, pin_net_names


CHANNEL_RE = re.compile(r"(?:^|[_/.-])(?:CH|CHAN|CHANNEL)(\d+)(?:[_/.-]|$)", re.I)
MUX_RE = re.compile(r"(mux|multiplex|tca954|pca954|switch)", re.I)
RF_RE = re.compile(r"(antenna|rf|wifi|wi-fi|ble|bluetooth|esp32|nrf52|wroom)", re.I)
UI_RE = re.compile(r"(button|switch|encoder|pot|display|oled|lcd|led)", re.I)
DEBUG_RE = re.compile(r"(swd|jtag|icsp|debug|program|uart|serial)", re.I)
POWER_INPUT_RE = re.compile(r"(usb|barrel|battery|batt|jst|terminal|power)", re.I)
BARREL_RE = re.compile(r"(barrel|dc jack|power jack)", re.I)
JST_RE = re.compile(r"\b(jst|battery|batt|lipo|li-po)\b", re.I)
FFC_RE = re.compile(r"\b(ffc|fpc|flat flex|ribbon)\b", re.I)
HEADER_RE = re.compile(r"\b(header|pinheader|pin header|tagconnect|swd|jtag)\b", re.I)
BUTTON_RE = re.compile(r"\b(button|pushbutton|tact|switch)\b", re.I)
LED_RE = re.compile(r"\b(led|neopixel|indicator)\b", re.I)
DISPLAY_RE = re.compile(r"\b(display|oled|lcd|screen)\b", re.I)
POT_ENCODER_RE = re.compile(r"\b(pot|potentiometer|encoder|knob)\b", re.I)


@dataclass
class PlacementIntent:
    ref: str
    kind: str
    priority: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class ChannelSlot:
    channel_number: int
    slot_index: int
    refs: list[str] = field(default_factory=list)
    sensor_refs: list[str] = field(default_factory=list)
    passive_refs: list[str] = field(default_factory=list)
    connector_refs: list[str] = field(default_factory=list)
    other_refs: list[str] = field(default_factory=list)


@dataclass
class RepeatedChannelIntent:
    name: str
    refs: list[str] = field(default_factory=list)
    channel_numbers: list[int] = field(default_factory=list)
    refs_by_channel: dict[int, list[str]] = field(default_factory=dict)
    pattern: str = ""
    shared_refs: list[str] = field(default_factory=list)
    controller_refs: list[str] = field(default_factory=list)
    slots: list[ChannelSlot] = field(default_factory=list)
    backbone_nets: list[str] = field(default_factory=list)


@dataclass
class MatingIntent:
    ref: str
    kind: str
    edge_preference: str | None = None
    mating_side: str | None = None
    allowed_rotations: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)


@dataclass
class PlacementIntentPlan:
    intents: dict[str, list[PlacementIntent]] = field(default_factory=dict)
    edge_anchors: list[EdgeAnchor] = field(default_factory=list)
    face_edges: list[FaceEdgeConstraint] = field(default_factory=list)
    keepouts: list[KeepOut] = field(default_factory=list)
    repeated_channels: list[RepeatedChannelIntent] = field(default_factory=list)
    mating_intents: list[MatingIntent] = field(default_factory=list)
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
        if self.face_edges:
            lines.append(f"  inferred face-edge constraints: {len(self.face_edges)}")
        if self.mating_intents:
            lines.append(f"  mating intents: {len(self.mating_intents)}")
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


def _mating_intent_for_part(
    ref: str,
    text: str,
    role: PartRole | None,
    nets: list[str],
) -> MatingIntent | None:
    role_name = role.role if role is not None else ""
    if "usb" in text:
        return MatingIntent(
            ref=ref,
            kind="usb",
            edge_preference="bottom",
            mating_side="outside_board",
            allowed_rotations=(0.0, 180.0),
            confidence=0.95,
            reasons=["USB connector metadata"],
        )
    if BARREL_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="barrel",
            edge_preference="bottom",
            mating_side="outside_board",
            allowed_rotations=(0.0, 90.0, 180.0, 270.0),
            confidence=0.9,
            reasons=["barrel/power jack metadata"],
        )
    if JST_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="jst",
            edge_preference="bottom",
            mating_side="cable_exit",
            allowed_rotations=(0.0, 180.0),
            confidence=0.85,
            reasons=["JST/battery connector metadata"],
        )
    if FFC_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="ffc",
            edge_preference="bottom",
            mating_side="cable_exit",
            allowed_rotations=(0.0, 180.0),
            confidence=0.85,
            reasons=["FFC/FPC connector metadata"],
        )
    if HEADER_RE.search(text) or role_name == "connector":
        edge = _edge_for_part(text, role or PartRole(ref, "connector", 0.5), nets)
        return MatingIntent(
            ref=ref,
            kind="header" if HEADER_RE.search(text) else "generic_connector",
            edge_preference=edge,
            mating_side="pin_access",
            confidence=0.75,
            reasons=["connector/header metadata"],
        )
    if DISPLAY_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="display",
            edge_preference="top",
            mating_side="visible_face",
            confidence=0.8,
            reasons=["display metadata"],
        )
    if POT_ENCODER_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="encoder" if "encoder" in text else "pot",
            edge_preference="right",
            mating_side="user_control",
            confidence=0.8,
            reasons=["panel control metadata"],
        )
    if BUTTON_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="button",
            edge_preference="right",
            mating_side="user_control",
            confidence=0.75,
            reasons=["button/switch metadata"],
        )
    if LED_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="led",
            edge_preference="right",
            mating_side="visible_face",
            confidence=0.7,
            reasons=["LED/indicator metadata"],
        )
    return None


def _slot_for_channel(
    channel_number: int,
    slot_index: int,
    refs: list[str],
    roles: dict[str, PartRole],
) -> ChannelSlot:
    sensor_refs: list[str] = []
    passive_refs: list[str] = []
    connector_refs: list[str] = []
    other_refs: list[str] = []

    for ref in refs:
        role = roles.get(ref)
        role_name = role.role if role is not None else "unknown"
        if role_name == "connector":
            connector_refs.append(ref)
        elif role_name in {"signal_passive", "decoupling_cap"}:
            passive_refs.append(ref)
        elif role_name == "ic":
            sensor_refs.append(ref)
        else:
            other_refs.append(ref)

    return ChannelSlot(
        channel_number=channel_number,
        slot_index=slot_index,
        refs=refs,
        sensor_refs=sensor_refs,
        passive_refs=passive_refs,
        connector_refs=connector_refs,
        other_refs=other_refs,
    )


def _infer_repeated_channels(
    circuit,
    roles: dict[str, PartRole],
) -> list[RepeatedChannelIntent]:
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
    ref_counts: dict[str, int] = {}
    for refs_for_ch in channel_refs.values():
        for ref in refs_for_ch:
            ref_counts[ref] = ref_counts.get(ref, 0) + 1
    shared_refs = sorted(ref for ref, count in ref_counts.items() if count > 1)
    controller_refs = sorted(
        ref
        for ref in shared_refs
        if roles.get(ref) is not None and roles[ref].role in {"ic", "connector"}
    )
    slots = []
    refs_by_channel = {
        channel: sorted(refs_for_ch)
        for channel, refs_for_ch in sorted(channel_refs.items())
    }
    for slot_index, channel in enumerate(sorted(refs_by_channel)):
        slot_refs = [
            ref
            for ref in refs_by_channel[channel]
            if ref_counts.get(ref, 0) == 1
        ]
        slots.append(_slot_for_channel(channel, slot_index, slot_refs, roles))

    return [
        RepeatedChannelIntent(
            name="channel",
            refs=refs,
            channel_numbers=sorted(channel_refs),
            refs_by_channel=refs_by_channel,
            pattern="channel-numbered net names",
            shared_refs=shared_refs,
            controller_refs=controller_refs,
            slots=slots,
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
        mating_intent = _mating_intent_for_part(ref, text, role, nets)
        if mating_intent is not None:
            plan.mating_intents.append(mating_intent)
            _add_intent(
                plan,
                ref,
                "mechanical_mating",
                88,
                f"{mating_intent.kind} mating intent",
            )
            if mating_intent.edge_preference is not None:
                plan.face_edges.append(
                    FaceEdgeConstraint(ref=ref, edge=mating_intent.edge_preference)
                )

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

    plan.repeated_channels = _infer_repeated_channels(circuit, roles)
    return plan
