"""Structured design exceptions — the product's course-correction interface.

Every engine failure or quality-gate miss is expressed as a DesignException
carrying machine-readable resolution candidates. A reviewing agent (any tier)
picks a candidate by id; it never re-describes the fix in natural language.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ExcCode(str, Enum):
    # Spec translation (pre-engine)
    SPEC_MALFORMED = "SPEC_MALFORMED"
    SPEC_UNKNOWN_LIB = "SPEC_UNKNOWN_LIB"
    SPEC_UNKNOWN_PART = "SPEC_UNKNOWN_PART"
    SPEC_UNKNOWN_PIN = "SPEC_UNKNOWN_PIN"
    SPEC_BAD_FOOTPRINT = "SPEC_BAD_FOOTPRINT"
    # Engine runtime
    FOOTPRINT_MISSING = "FOOTPRINT_MISSING"
    SCH_PLACEMENT_FAILURE = "SCH_PLACEMENT_FAILURE"
    SCH_ROUTING_FAILURE = "SCH_ROUTING_FAILURE"
    # ERC (post-schematic)
    ERC_PIN_NOT_CONNECTED = "ERC_PIN_NOT_CONNECTED"
    ERC_PIN_NOT_DRIVEN = "ERC_PIN_NOT_DRIVEN"
    ERC_REAL_ERROR = "ERC_REAL_ERROR"
    # Layout validation
    LAYOUT_OVERLAP = "LAYOUT_OVERLAP"
    LAYOUT_OUTLINE_VIOLATION = "LAYOUT_OUTLINE_VIOLATION"
    LAYOUT_KEEPOUT = "LAYOUT_KEEPOUT"
    LAYOUT_MISSING_REF = "LAYOUT_MISSING_REF"
    # Advisory quality signals
    HIGH_CONGESTION = "HIGH_CONGESTION"
    LONG_POWER_NET = "LONG_POWER_NET"
    # Design completeness (post-enrich review)
    DESIGN_MISSING_BULK_CAP = "DESIGN_MISSING_BULK_CAP"
    DESIGN_NO_CONNECTOR = "DESIGN_NO_CONNECTOR"
    DESIGN_NO_POWER_RAIL = "DESIGN_NO_POWER_RAIL"
    DESIGN_POWER_FLAG = "DESIGN_POWER_FLAG"
    DESIGN_MISSING_FEATURE = "DESIGN_MISSING_FEATURE"
    # Routing (Freerouting)
    ROUTE_UNCONNECTED = "ROUTE_UNCONNECTED"
    ROUTE_CONGESTION = "ROUTE_CONGESTION"
    ROUTE_TIMEOUT = "ROUTE_TIMEOUT"
    ROUTE_UNAVAILABLE = "ROUTE_UNAVAILABLE"
    # DRC (kicad-cli)
    DRC_CLEARANCE = "DRC_CLEARANCE"
    DRC_UNCONNECTED = "DRC_UNCONNECTED"
    DRC_SHORT = "DRC_SHORT"
    DRC_COURTYARD = "DRC_COURTYARD"
    DRC_TOOL_FAILURE = "DRC_TOOL_FAILURE"
    MANUFACTURING_OUTPUT_FAILURE = "MANUFACTURING_OUTPUT_FAILURE"
    # SKiDL code execution
    CODE_EXEC_ERROR = "CODE_EXEC_ERROR"
    # Harness-level
    ENGINE_TIMEOUT = "ENGINE_TIMEOUT"
    ENGINE_CRASH = "ENGINE_CRASH"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class Severity(str, Enum):
    FATAL = "fatal"        # no outputs produced; must fix to proceed
    ERROR = "error"        # outputs exist but a quality gate failed
    ADVISORY = "advisory"  # informational; may be waived


class ActionType(str, Enum):
    REPLACE_LIB = "replace_lib"            # params: ref|"*", old, new
    REPLACE_PART = "replace_part"          # params: ref, new
    REPLACE_PIN = "replace_pin"            # params: ref, old, new
    REPLACE_FOOTPRINT = "replace_footprint"  # params: old, new
    REMOVE_PART = "remove_part"            # params: ref
    REMOVE_NET_PIN = "remove_net_pin"      # params: net, pin ("REF.PIN")
    STUB_NET = "stub_net"                  # params: net
    SET_FORM_FACTOR = "set_form_factor"    # params: name
    SET_OUTLINE = "set_outline"            # params: w_mm, h_mm
    SCALE_OUTLINE = "scale_outline"        # params: area_factor
    ADD_PARTS = "add_parts"                # params: parts[], net_connections[]
    SET_LAYERS = "set_layers"              # params: layers (int)
    ACCEPT_ADVISORY = "accept_advisory"    # params: {} — waive this exception
    REGENERATE = "regenerate"              # params: {} — rerun unchanged


class Candidate(BaseModel):
    """One machine-applicable resolution option for an exception."""

    id: str = Field(description="Short stable id (c1, c2, ...) — pass this to apply_correction")
    action: ActionType = Field(description="The mutation this candidate applies to the spec")
    params: dict = Field(default_factory=dict, description="Typed parameters for the action")
    human_summary: str = Field(description="One sentence describing the fix, for the reviewing agent")
    cost_hint: str = Field(default="free", description="Relative cost of applying: free | cheap | expensive")
    confidence: float = Field(default=0.9, description="0.0-1.0; deterministic policy auto-applies >=0.8, LLM reviews <0.8")
    source: str = Field(default="deterministic", description="Where this candidate came from: deterministic | jlc_package | jlc_search | jlc_lcsc")


class DesignException(BaseModel):
    """A structured engine exception with action-ready resolution candidates."""

    id: str = Field(description="Unique id within this run (e.g. e1, e2)")
    code: ExcCode = Field(description="Stable error code — see ExcCode enum")
    severity: Severity = Field(description="fatal: no outputs; error: gate failed; advisory: waivable")
    message: str = Field(description="One-sentence human/agent-readable description")
    subject: dict = Field(default_factory=dict, description="What the exception is about: refs, nets, pins, coordinates")
    candidates: list[Candidate] = Field(default_factory=list, description="Resolution options, best first. c1 is the deterministic-policy pick")
    retry_hint: str = Field(default="", description="Literal instruction for fixing an invalid follow-up call")

    def waiver_key(self) -> str:
        """Stable key used to match this exception against spec.waivers."""
        subj = self.subject
        ident = subj.get("ref") or subj.get("net") or subj.get("pair") or subj.get("footprint") or ""
        if isinstance(ident, (list, tuple)):
            ident = "+".join(str(x) for x in ident)
        return f"{self.code.value}:{ident}"
