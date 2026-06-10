"""Telemetry for the overnight PCB-engine product layer.

Pure stdlib + pydantic — safe to import from any worker or host process.
"""

from telemetry.features import extract_geometry
from telemetry.models import DEFAULT_STATUS, GeometryFeatures, LLMStage, RunRecord
from telemetry.store import RUNS_PATH, atomic_append, default_runs_path, read_records, session

__all__ = [
    "DEFAULT_STATUS",
    "GeometryFeatures",
    "LLMStage",
    "RunRecord",
    "RUNS_PATH",
    "atomic_append",
    "default_runs_path",
    "extract_geometry",
    "read_records",
    "session",
]
