"""Simulation engine: candidate generation, scoring, Pareto selection, replanning."""

from pri.engine.simulation.candidates import (
    MAX_RAW_CANDIDATES,
    STRATEGY_FAMILIES,
    CandidateGenerationError,
    generate_candidates,
)
from pri.engine.simulation.pareto import dominates, mark_pareto
from pri.engine.simulation.replan import ReplanError, evaluate_plan, recover
from pri.engine.simulation.scoring import ScoringError, score

__all__ = [
    "MAX_RAW_CANDIDATES",
    "STRATEGY_FAMILIES",
    "CandidateGenerationError",
    "ReplanError",
    "ScoringError",
    "dominates",
    "evaluate_plan",
    "generate_candidates",
    "mark_pareto",
    "recover",
    "score",
]
