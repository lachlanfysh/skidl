from __future__ import annotations

import re
from dataclasses import dataclass, field

from .backends import OptionalBackendStatus, optional_backend_status
from .constraints import AlignConstraint, EdgeAnchor, FaceEdgeConstraint, FarConstraint, KeepOut, NearConstraint
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
COAX_RE = re.compile(r"(?:^|[\s_/:.,-])(coax|coaxial|sma|u\.?fl|ipex|antenna|rf.?conn)(?:[\s_/:.,-]|$)", re.I)
XTAL_PIN_RE = re.compile(r"^(XTAL|OSC|XTALI|XTALO|XIN|XOUT)$", re.I)
AUDIO_IC_RE = re.compile(r"\b(dac|codec|audio|i2s|pcm510|wm874|max9814|sgtl5000|tlv320)\b", re.I)
DISPLAY_NET_RE = re.compile(r"(?:^|[_/.\s-])(eink|e.ink|oled|lcd|disp|tft|epd|dc|busy)(?:[_/.\s-]|$)", re.I)
NAV_RE = re.compile(r"\b(nav|joystick|d-pad|dpad|5.?way|4.?way)\b", re.I)


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
    near_constraints: list[NearConstraint] = field(default_factory=list)
    far_constraints: list[FarConstraint] = field(default_factory=list)
    align_constraints: list[AlignConstraint] = field(default_factory=list)
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
        if self.near_constraints:
            lines.append(f"  near constraints: {len(self.near_constraints)}")
        if self.far_constraints:
            lines.append(f"  far constraints: {len(self.far_constraints)}")
        if self.align_constraints:
            lines.append(f"  align constraints: {len(self.align_constraints)}")
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
        is_display_fpc = any(DISPLAY_NET_RE.search(n) for n in nets)
        if is_display_fpc:
            return MatingIntent(
                ref=ref,
                kind="ffc",
                edge_preference="top",
                mating_side="cable_exit",
                allowed_rotations=(0.0, 180.0),
                confidence=0.9,
                reasons=["FFC/FPC connector with display nets"],
            )
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
    if NAV_RE.search(text):
        return MatingIntent(
            ref=ref,
            kind="nav_control",
            edge_preference="right",
            mating_side="user_control",
            confidence=0.8,
            reasons=["nav switch/joystick metadata"],
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


def _is_coax_connector(part) -> bool:
    """Return True if *part* looks like a coaxial/antenna connector."""
    text = _part_text(part)
    return bool(COAX_RE.search(text))


def _find_rf_ic(antenna_part, circuit):
    """Follow the signal net from an antenna connector to find the RF IC.

    The signal pin is typically named ``In``, ``Signal``, or ``1``.  We walk
    the net looking for a non-passive, non-connector IC (ref starting with
    ``U``).
    """
    signal_pin = None
    for pin in antenna_part.pins:
        pin_name = getattr(pin, "name", None) or ""
        if pin_name in ("In", "Signal", "1"):
            signal_pin = pin
            break
    if signal_pin is None and antenna_part.pins:
        # Fallback: use first pin that is not on GND
        for pin in antenna_part.pins:
            net = getattr(pin, "net", None)
            net_name = getattr(net, "name", "") if net else ""
            if not GND_NET_RE.match(net_name):
                signal_pin = pin
                break
    if signal_pin is None:
        return None

    net = getattr(signal_pin, "net", None)
    if net is None:
        return None

    # Walk all pins on this net to find an IC
    net_pins = getattr(net, "_pins", None) or getattr(net, "pins", [])
    if callable(net_pins):
        net_pins = net_pins()
    for pin in net_pins:
        part = getattr(pin, "part", None)
        if part is None or part is antenna_part:
            continue
        ref = str(getattr(part, "ref", ""))
        if ref.startswith("U"):
            return part
    return None


def _find_crystal_for_ic(ic_part, circuit):
    """Find a crystal connected to the IC's XTAL/OSC pins."""
    for pin in ic_part.pins:
        pin_name = getattr(pin, "name", None) or ""
        if not XTAL_PIN_RE.match(pin_name):
            continue
        net = getattr(pin, "net", None)
        if net is None:
            continue
        net_pins = getattr(net, "_pins", None) or getattr(net, "pins", [])
        if callable(net_pins):
            net_pins = net_pins()
        for other_pin in net_pins:
            other_part = getattr(other_pin, "part", None)
            if other_part is None or other_part is ic_part:
                continue
            other_text = _part_text(other_part)
            other_ref = str(getattr(other_part, "ref", ""))
            if (
                "crystal" in other_text
                or "resonator" in other_text
                or other_ref.startswith(("Y", "X"))
            ):
                return other_part
    return None


def _find_audio_ics(circuit):
    """Return parts that look like DAC, codec, or audio amplifier ICs."""
    audio_parts = []
    for part in circuit.parts:
        text = _part_text(part)
        if AUDIO_IC_RE.search(text):
            audio_parts.append(part)
    return audio_parts


def _infer_rf_intents(circuit, plan: PlacementIntentPlan, outline) -> None:
    """Detect antenna connectors and emit RF path constraints."""
    for part in circuit.parts:
        if not _is_coax_connector(part):
            continue

        ref = str(getattr(part, "ref", ""))

        # EdgeAnchor for the antenna connector — replace any generic
        # connector anchor that was already emitted for this ref.
        offset = None
        if outline is not None:
            offset = (outline.x_min + outline.x_max) / 2
        plan.edge_anchors = [a for a in plan.edge_anchors if a.ref != ref]
        plan.edge_anchors.append(
            EdgeAnchor(ref=ref, edge="top", offset_mm=offset)
        )
        plan.face_edges = [f for f in plan.face_edges if f.ref != ref]
        plan.face_edges.append(FaceEdgeConstraint(ref=ref, edge="top"))

        # Mating intent for the coaxial connector
        plan.mating_intents.append(
            MatingIntent(
                ref=ref,
                kind="coaxial",
                edge_preference="top",
                mating_side="outside_board",
                allowed_rotations=(0.0, 90.0, 180.0, 270.0),
                confidence=0.9,
                reasons=["coaxial/antenna connector metadata"],
            )
        )
        _add_intent(plan, ref, "edge_connector", 90, "antenna/coaxial connector")
        _add_intent(plan, ref, "rf_module", 85, "antenna connector")
        _add_intent(plan, ref, "mechanical_mating", 88, "coaxial mating intent")

        # Find the RF IC
        rf_ic = _find_rf_ic(part, circuit)
        if rf_ic is None:
            continue
        rf_ic_ref = str(getattr(rf_ic, "ref", ""))

        # NearConstraint: RF IC close to antenna
        plan.near_constraints.append(
            NearConstraint(ref=rf_ic_ref, target_ref=ref, distance_mm=8.0)
        )
        _add_intent(
            plan, rf_ic_ref, "rf_module", 85, f"RF IC near antenna {ref}"
        )

        # Find crystal on the RF IC
        crystal = _find_crystal_for_ic(rf_ic, circuit)
        if crystal is not None:
            xtal_ref = str(getattr(crystal, "ref", ""))
            plan.near_constraints.append(
                NearConstraint(
                    ref=xtal_ref, target_ref=rf_ic_ref, distance_mm=4.0
                )
            )
            _add_intent(
                plan,
                xtal_ref,
                "crystal_network",
                80,
                f"crystal near RF IC {rf_ic_ref}",
            )

        # Analog separation: audio ICs far from RF IC
        audio_ics = _find_audio_ics(circuit)
        for audio_part in audio_ics:
            audio_ref = str(getattr(audio_part, "ref", ""))
            if audio_ref == rf_ic_ref:
                continue
            plan.far_constraints.append(
                FarConstraint(
                    ref=audio_ref, target_ref=rf_ic_ref, distance_mm=15.0
                )
            )
            _add_intent(
                plan,
                audio_ref,
                "analog_separation",
                75,
                f"audio IC separated from RF IC {rf_ic_ref}",
            )


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


def _colocate_display_and_controls(
    plan: PlacementIntentPlan,
    outline=None,
) -> None:
    """Ensure display and user-control parts share the same board edge.

    Finds display-related mating intents (kind "display" or "ffc" with display
    in the reasons) and control-related intents (button, encoder, pot,
    nav_control).  When both exist, the display edge is authoritative: all
    controls are moved to the same edge.  An ``AlignConstraint`` is emitted so
    the placer keeps them on the same line along that edge.
    """
    _DISPLAY_KINDS = {"display"}
    _CONTROL_KINDS = {"button", "encoder", "pot", "nav_control"}

    display_refs: list[str] = []
    control_refs: list[str] = []

    for mi in plan.mating_intents:
        if mi.kind in _DISPLAY_KINDS:
            display_refs.append(mi.ref)
        elif mi.kind == "ffc" and any(
            "display" in r.lower() for r in mi.reasons
        ):
            display_refs.append(mi.ref)
        elif mi.kind in _CONTROL_KINDS:
            control_refs.append(mi.ref)

    if not display_refs or not control_refs:
        return

    # Use the first display's edge as the authority.
    display_edge = next(
        (
            mi.edge_preference
            for mi in plan.mating_intents
            if mi.ref == display_refs[0]
        ),
        "top",
    )

    # Move controls to the same edge.
    for ref in control_refs:
        for mi in plan.mating_intents:
            if mi.ref == ref:
                mi.edge_preference = display_edge
        for ea in plan.edge_anchors:
            if ea.ref == ref:
                ea.edge = display_edge
        for fe in plan.face_edges:
            if fe.ref == ref:
                fe.edge = display_edge

    # Emit AlignConstraint for display + controls on the shared edge.
    axis = "y" if display_edge in {"top", "bottom"} else "x"
    plan.align_constraints.append(
        AlignConstraint(
            refs=display_refs + control_refs,
            axis=axis,
        )
    )


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
    _infer_rf_intents(circuit, plan, outline)
    _colocate_display_and_controls(plan, outline)
    return plan
