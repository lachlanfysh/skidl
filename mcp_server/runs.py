"""In-memory and on-disk run store for MCP pipeline runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import DesignException


class RunStore:
    """Small run store that mirrors each run to artifacts/runs/{run_id}/."""

    def __init__(self, root: str | Path = "artifacts/runs"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, dict[str, Any]] = {}

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def save(
        self,
        run_id: str,
        spec: CircuitSpec | dict,
        exceptions: list[DesignException] | list[dict],
        response,
    ) -> dict:
        spec_dict = (
            spec.model_dump(mode="json") if isinstance(spec, CircuitSpec) else dict(spec)
        )
        exc_dicts = [
            exc.model_dump(mode="json") if isinstance(exc, DesignException) else dict(exc)
            for exc in exceptions
        ]
        response_dict = (
            response.model_dump(mode="json")
            if hasattr(response, "model_dump")
            else dict(response)
        )
        snapshot = {
            "spec": spec_dict,
            "exceptions": exc_dicts,
            "response": response_dict,
        }
        self._runs[run_id] = snapshot

        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in snapshot.items():
            with open(run_dir / f"{name}.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
        return snapshot

    def load(self, run_id: str) -> dict:
        if run_id in self._runs:
            return self._runs[run_id]
        run_dir = self.run_dir(run_id)
        snapshot = {}
        for name in ("spec", "exceptions", "response"):
            path = run_dir / f"{name}.json"
            if not path.exists():
                raise KeyError(f"run {run_id!r} has no {name}.json")
            with open(path, "r", encoding="utf-8") as f:
                snapshot[name] = json.load(f)
        self._runs[run_id] = snapshot
        return snapshot

    def load_spec(self, run_id: str) -> CircuitSpec:
        return CircuitSpec.model_validate(self.load(run_id)["spec"])

    def load_exceptions(self, run_id: str) -> list[DesignException]:
        return [
            DesignException.model_validate(exc)
            for exc in self.load(run_id)["exceptions"]
        ]

    def load_response(self, run_id: str) -> dict:
        return dict(self.load(run_id)["response"])
