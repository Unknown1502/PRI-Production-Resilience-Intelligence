"""Request and response bodies for the HTTP API.

Domain models are frozen and carry more than a browser needs, so the API has
its own shapes.  They are flat, JSON-native and stable: the TypeScript client
in ``web/lib/api.ts`` mirrors this file, and changing a field name here is a
breaking change there.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from pri.domain.models import (
    EvaluatedPlan,
    ImpactReport,
    ProductionState,
    RecoveryResult,
    RoundTrace,
)
from pri.engine.verification.verify import VerificationReport

__all__ = [
    "ApproveRequest",
    "ApproveResponse",
    "AuditEntry",
    "CandidateOut",
    "ErrorBody",
    "EventIn",
    "ExecuteRequest",
    "ExecuteResponse",
    "HealthOut",
    "MoveOut",
    "RecoverRequest",
    "RecoveryOut",
    "ResetResponse",
    "RoundOut",
    "ScheduleDayOut",
    "ScheduleOut",
    "ScoreOut",
    "StateOut",
    "ViolationOut",
]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthChecks(BaseModel):
    """Per-dependency health, each independent of the others."""

    database: Literal["ok", "error"]
    kafka: Literal["ok", "degraded", "disabled"]
    gemini: Literal["configured", "unconfigured"]


class HealthOut(BaseModel):
    """Liveness plus enough provenance to tell two deployments apart.

    ``status`` is ``degraded`` when Kafka is unhappy but the database is fine.
    That is still a 200: PRI works without Kafka by design, and a 503 would
    make Cloud Run cycle an instance that is serving correctly. Only a database
    failure is a 503.
    """

    status: Literal["ok", "degraded"]
    version: str
    git_sha: str
    checks: HealthChecks
    #: Retained so existing clients and tests keep working.
    database: Literal["up", "down"]
    agent_enabled: bool
    kafka: dict[str, Any] = {}
    at: datetime


# ---------------------------------------------------------------------------
# State and schedule
# ---------------------------------------------------------------------------


class ScheduleDayOut(BaseModel):
    """One shooting day, flattened for the schedule strip."""

    date: date
    call_time: datetime
    wrap_time: datetime
    location_id: str
    location_name: str
    unit: str
    scene_ids: list[str]
    scene_count: int
    scheduled_minutes: int
    is_reserve: bool


class ScheduleOut(BaseModel):
    """The whole shooting schedule at one version."""

    production_id: str
    title: str
    currency: str
    version: int
    shoot_start: date
    shoot_end: date
    reserve_days: list[date]
    days: list[ScheduleDayOut]


class StateOut(BaseModel):
    """A full state snapshot, plus its live validity."""

    version: int
    parent_version: int | None
    event_id: str | None
    created_at: datetime
    digest: str
    hard_violation_codes: list[str]
    state: ProductionState


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class EventIn(BaseModel):
    """A disruption arriving over HTTP rather than Kafka.

    ``event_id`` is the idempotency key: posting the same id twice records the
    event once and returns ``duplicate=True``.
    """

    event_id: str
    event_type: Literal[
        "location.blocked",
        "actor.unavailable",
        "equipment.failed",
        "weather.changed",
        "crew.unavailable",
    ]
    occurred_at: datetime
    source: str
    severity: float = Field(ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class EventAccepted(BaseModel):
    """Acknowledgement that an event was stored."""

    event_id: str
    duplicate: bool
    production_id: str


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


class RecoverRequest(BaseModel):
    """Ask for recovery options for a previously ingested event."""

    event_id: str
    strategy_hints: list[str] | None = None


class ViolationOut(BaseModel):
    """A constraint failure, in the exact words the UI prints."""

    code: str
    severity: Literal["HARD", "SOFT"]
    message: str
    subject_ids: list[str]
    observed: str
    required: str


class ScoreOut(BaseModel):
    """The six deterministic metrics."""

    schedule_delay_days: float
    incremental_cost: Decimal
    operational_risk: float
    affected_scene_count: int
    crew_disruption_hours: float
    downstream_dependency_impact: int


class MoveOut(BaseModel):
    """One schedule edit, rendered for display."""

    kind: str
    summary: str
    detail: dict[str, Any]


class CandidateOut(BaseModel):
    """An evaluated plan as the recovery screen shows it."""

    id: str
    label: str
    family: str | None
    valid: bool
    pareto_optimal: bool
    moves: list[MoveOut]
    violations: list[ViolationOut]
    score: ScoreOut | None
    resulting_state_digest: str | None


class RoundOut(BaseModel):
    """One round of the replanning loop, for the timeline."""

    round_number: int
    strategy_hints: list[str]
    generated_plan_ids: list[str]
    invalid_plan_ids: list[str]
    failed_rule_codes: list[str]
    repaired_plan_ids: list[str]
    note: str


class RecoveryOut(BaseModel):
    """Everything the recovery screen needs in one response."""

    session_id: str
    production_id: str
    event_id: str
    base_version: int
    impact: ImpactReport
    candidates: list[CandidateOut]
    rounds: list[RoundOut]
    pareto_plan_ids: list[str]
    recommended_plan_id: str | None
    explanation: str
    tradeoff_summary: str
    mode: Literal["agent", "deterministic"]
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Approval and execution
# ---------------------------------------------------------------------------


class ApproveRequest(BaseModel):
    """A producer's decision on one candidate."""

    plan_id: str
    approver: str
    note: str | None = None
    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"


class ApproveResponse(BaseModel):
    approval_id: str
    session_id: str
    plan_id: str
    decision: str


class ExecuteRequest(BaseModel):
    """Run the seven-step transition for an approved plan."""

    plan_id: str
    approver: str
    authorization: str | None = None


class ExecuteResponse(BaseModel):
    """What the execution produced, including the six checks."""

    session_id: str
    plan_id: str
    base_version: int
    new_version: int
    digest: str
    steps_completed: list[str]
    artifacts: list[str]
    replayed: bool
    verification: VerificationReport | None


# ---------------------------------------------------------------------------
# Audit, artifacts, demo
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """One append-only audit row."""

    at: datetime
    actor: str
    action: str
    subject: str
    detail: dict[str, Any]


class ArtifactOut(BaseModel):
    """A generated artifact registered against a version."""

    id: str
    version: int
    kind: str
    path: str
    created_at: datetime


class ResetResponse(BaseModel):
    """Confirmation that the demo was returned to version 1."""

    production_id: str
    version: int
    scenes: int
    days: int


class ErrorBody(BaseModel):
    """The error envelope every 4xx and 5xx uses.

    ``rule_code`` is populated when a constraint rule caused the failure, so
    the frontend can render the violation card without string-matching the
    message.
    """

    error: str
    detail: str
    step: str | None = None
    rule_code: str | None = None
    rule_codes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------


def move_out(move: Any) -> MoveOut:
    """Render one ``Move`` for display.

    The summary is written the way a first AD would say it out loud, because
    it appears verbatim on the plan card.
    """
    kind = getattr(move, "kind", type(move).__name__)
    detail = move.model_dump(mode="json", exclude={"kind"})
    if kind == "MoveSceneToDay":
        summary = f"Move {move.scene_id} to {move.target_date.isoformat()}"
    elif kind == "SwapDays":
        summary = f"Swap {move.date_a.isoformat()} with {move.date_b.isoformat()}"
    elif kind == "ShiftCallTime":
        summary = f"Call {move.date.isoformat()} at {move.new_call_time.strftime('%H:%M')}"
    elif kind == "RelocateScene":
        summary = f"Relocate {move.scene_id} to {move.target_location_id}"
    else:  # pragma: no cover - guards a future move kind
        summary = kind
    return MoveOut(kind=kind, summary=summary, detail=detail)


def candidate_out(evaluated: EvaluatedPlan) -> CandidateOut:
    """Convert an evaluated plan into its API shape."""
    return CandidateOut(
        id=evaluated.plan.id,
        label=evaluated.plan.label,
        family=evaluated.plan.rationale_hint,
        valid=evaluated.valid,
        pareto_optimal=evaluated.pareto_optimal,
        moves=[move_out(m) for m in evaluated.plan.moves],
        violations=[
            ViolationOut(
                code=v.code,
                severity=v.severity,
                message=v.message,
                subject_ids=list(v.subject_ids),
                observed=v.observed,
                required=v.required,
            )
            for v in evaluated.violations
        ],
        score=ScoreOut(**evaluated.score.model_dump()) if evaluated.score else None,
        resulting_state_digest=evaluated.resulting_state_digest,
    )


def round_out(trace: RoundTrace) -> RoundOut:
    """Convert a round trace into its API shape."""
    return RoundOut(
        round_number=trace.round_number,
        strategy_hints=list(trace.strategy_hints),
        generated_plan_ids=list(trace.generated_plan_ids),
        invalid_plan_ids=list(trace.invalid_plan_ids),
        failed_rule_codes=list(trace.failed_rule_codes),
        repaired_plan_ids=list(trace.repaired_plan_ids),
        note=trace.note,
    )


def schedule_out(state: ProductionState) -> ScheduleOut:
    """Flatten a state into the schedule strip the overview renders."""
    locations = {loc.id: loc.name for loc in state.locations}
    scenes = {s.id: s for s in state.scenes}
    reserve = set(state.production.reserve_days)

    return ScheduleOut(
        production_id=state.production.id,
        title=state.production.title,
        currency=state.production.currency,
        version=state.version,
        shoot_start=state.production.shoot_start,
        shoot_end=state.production.shoot_end,
        reserve_days=list(state.production.reserve_days),
        days=[
            ScheduleDayOut(
                date=day.date,
                call_time=day.call_time,
                wrap_time=day.wrap_time,
                location_id=day.location_id,
                location_name=locations.get(day.location_id, day.location_id),
                unit=day.unit,
                scene_ids=list(day.scene_ids),
                scene_count=len(day.scene_ids),
                scheduled_minutes=sum(
                    scenes[sid].estimated_minutes for sid in day.scene_ids if sid in scenes
                ),
                is_reserve=day.date in reserve,
            )
            for day in sorted(state.schedule.days, key=lambda d: d.date)
        ],
    )


def recovery_out(
    result: RecoveryResult,
    *,
    explanation: str,
    tradeoff_summary: str,
    mode: Literal["agent", "deterministic"],
    recommended_plan_id: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> RecoveryOut:
    """Assemble the recovery response the UI consumes."""
    return RecoveryOut(
        session_id=result.session_id,
        production_id=result.production_id,
        event_id=result.event_id,
        base_version=result.base_version,
        impact=result.impact,
        candidates=[candidate_out(ep) for ep in result.evaluated],
        rounds=[round_out(r) for r in result.rounds],
        pareto_plan_ids=[ep.plan.id for ep in result.pareto_plans],
        recommended_plan_id=recommended_plan_id,
        explanation=explanation,
        tradeoff_summary=tradeoff_summary,
        mode=mode,
        tool_calls=tool_calls or [],
    )
