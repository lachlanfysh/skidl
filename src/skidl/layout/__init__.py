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
from .engine import LayoutResult, plan_layout
from .geometry import (
    FootprintGeometry,
    PadGeometry,
    load_footprint_geometries,
    load_footprint_geometry,
)
from .hierarchy import PlacementGroup, extract_groups
from .intent import (
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
    PowerCorridor,
    PowerNet,
    PowerRouteIntent,
    PowerRoutePlan,
    identify_power_nets,
    plan_power_routes,
)
from .reader import read_board_outline, read_footprint_bboxes, read_placed_positions
from .report import CandidateReport, PlacementReport
from .roles import PartRole, classify_part, classify_parts
from .scoring import LayoutScore, score_placement
from .validator import ValidationResult, find_kicad_cli, run_kicad_drc, validate
from .writer import PlacedPart, load_footprint_bboxes, write_kicad_pcb
