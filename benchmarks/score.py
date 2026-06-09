"""Scoring framework for benchmark results."""

import json
import os
from dataclasses import dataclass, field, asdict


@dataclass
class BenchmarkScore:
    board_name: str
    tier: int

    # Generation phase
    parse_ok: bool = False
    schematic_ok: bool = False
    attempts: int = 0

    # Circuit metrics
    part_count: int = 0
    net_count: int = 0
    subcircuit_count: int = 0
    ics_generated: list = field(default_factory=list)

    # ERC
    erc_warnings: int = 0
    erc_errors: int = 0

    # Layout (phase 2)
    layout_ok: bool = False
    layout_overlaps: int = 0
    layout_outline_violations: int = 0
    layout_missing_refs: int = 0
    hpwl_total: float = 0.0

    # Routing (phase 3)
    routing_attempted: bool = False
    routing_completion_pct: float = 0.0
    unrouted_nets: int = 0
    drc_violations: int = 0

    errors: list = field(default_factory=list)
    notes: str = ""

    @property
    def generation_score(self) -> float:
        """0-100 score for the generation phase."""
        score = 0.0
        if self.parse_ok:
            score += 30.0
        if self.schematic_ok:
            score += 30.0
        if self.erc_errors == 0:
            score += 20.0
        elif self.erc_errors < 5:
            score += 10.0
        if self.subcircuit_count > 0:
            score += 10.0
        if self.attempts == 1:
            score += 10.0
        elif self.attempts <= 3:
            score += 5.0
        return score

    @property
    def layout_score(self) -> float:
        """0-100 score for the layout phase."""
        if not self.layout_ok:
            return 0.0
        score = 50.0
        if self.layout_overlaps == 0:
            score += 25.0
        elif self.layout_overlaps < 5:
            score += 10.0
        if self.layout_outline_violations == 0:
            score += 15.0
        if self.layout_missing_refs == 0:
            score += 10.0
        return score

    @property
    def routing_score(self) -> float:
        """0-100 score for the routing phase."""
        if not self.routing_attempted:
            return 0.0
        return self.routing_completion_pct

    @property
    def total_score(self) -> float:
        """Weighted total: generation 50%, layout 30%, routing 20%."""
        return (
            self.generation_score * 0.5
            + self.layout_score * 0.3
            + self.routing_score * 0.2
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["generation_score"] = self.generation_score
        d["layout_score"] = self.layout_score
        d["routing_score"] = self.routing_score
        d["total_score"] = self.total_score
        return d


def load_scores(results_dir: str) -> list[BenchmarkScore]:
    """Load all benchmark scores from a results directory."""
    scores = []
    for name in sorted(os.listdir(results_dir)):
        score_path = os.path.join(results_dir, name, "score.json")
        if os.path.isfile(score_path):
            with open(score_path) as f:
                data = json.load(f)
            scores.append(BenchmarkScore(**{
                k: v for k, v in data.items()
                if k in BenchmarkScore.__dataclass_fields__
            }))
    return scores


def summary_table(scores: list[BenchmarkScore]) -> str:
    """Generate a markdown summary table."""
    lines = [
        "| Board | Tier | Gen | Layout | Route | Total | Parts | Errors |",
        "|-------|------|-----|--------|-------|-------|-------|--------|",
    ]
    for s in sorted(scores, key=lambda x: (-x.total_score, x.tier)):
        lines.append(
            f"| {s.board_name[:30]:<30} | {s.tier} | "
            f"{s.generation_score:5.0f} | {s.layout_score:5.0f} | "
            f"{s.routing_score:5.0f} | {s.total_score:5.0f} | "
            f"{s.part_count:5d} | {len(s.errors):5d} |"
        )

    # Tier averages
    lines.append("")
    lines.append("### Tier Averages")
    lines.append("| Tier | Boards | Avg Gen | Avg Layout | Avg Route | Avg Total |")
    lines.append("|------|--------|---------|------------|-----------|-----------|")
    for tier in sorted(set(s.tier for s in scores)):
        tier_scores = [s for s in scores if s.tier == tier]
        n = len(tier_scores)
        lines.append(
            f"| {tier} | {n} | "
            f"{sum(s.generation_score for s in tier_scores)/n:.0f} | "
            f"{sum(s.layout_score for s in tier_scores)/n:.0f} | "
            f"{sum(s.routing_score for s in tier_scores)/n:.0f} | "
            f"{sum(s.total_score for s in tier_scores)/n:.0f} |"
        )

    return "\n".join(lines)
