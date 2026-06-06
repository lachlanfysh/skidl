from .constraints import (
    AnchorZone,
    AlignConstraint,
    BoardOutline,
    DistributeConstraint,
    EdgeAnchor,
    FaceEdgeConstraint,
    FarConstraint,
    FixedPosition,
    KeepOut,
    LayoutConstraints,
    NearConstraint,
)
from .backends import OptionalBackendStatus, optional_backend_status
from .candidates import PlacementCandidate, generate_placement_candidates
from .congestion import (
    CongestionMap,
    CongestionRegion,
    build_congestion_map,
)
from .decaps import (
    DecapPlacementIntent,
    DecapRefinementResult,
    infer_decap_placement_intents,
    refine_candidate_decaps,
    refine_decaps,
)
from .engine import LayoutResult, plan_layout
from .geometry import (
    FootprintGeometry,
    PadGeometry,
    load_footprint_geometries,
    load_footprint_geometry,
)
from .hierarchy import PlacementGroup, extract_groups
from .intent import (
    ChannelSlot,
    MatingIntent,
    PlacementIntent,
    PlacementIntentPlan,
    RepeatedChannelIntent,
    infer_placement_intents,
)
from .placer import derive_outline, place_parts
from .orientation import (
    OrientationResult,
    refine_candidate_orientations,
    refine_orientations,
)
from .power import (
    PowerChain,
    PowerCorridor,
    PowerNet,
    PowerRouteIntent,
    PowerRoutePlan,
    PowerTopology,
    identify_power_nets,
    infer_power_topology,
    plan_power_routes,
)
from .reader import read_board_outline, read_footprint_bboxes, read_placed_positions
from .refinement import (
    RefinementResult,
    refine_candidate_placement,
    refine_placement,
)
from .report import CandidateReport, PlacementReport
from .roles import PartRole, classify_part, classify_parts
from .scoring import LayoutScore, score_placement
from .validator import ValidationResult, find_kicad_cli, run_kicad_drc, validate
from .writer import PlacedPart, load_footprint_bboxes, write_kicad_pcb
