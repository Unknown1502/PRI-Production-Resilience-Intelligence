"""The six verification checks, against the demo fixture."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pri.domain.models import (
    CandidatePlan,
    DisruptionEvent,
    MoveSceneToDay,
    ProductionState,
)
from pri.engine.simulation.replan import recover
from pri.engine.verification.verify import (
    VERIFICATION_CODES,
    VerificationExpectation,
    verify,
)
from pri.persistence.serialization import state_to_snapshot

SEP_16 = date(2026, 9, 16)


@pytest.fixture()
def approved(
    night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
) -> tuple[CandidatePlan, ProductionState, str]:
    """Plan B2 approved, applied, and its evaluated digest."""
    result = recover(night_train_state, loc04_blocked_event)
    plan = next(ep for ep in result.evaluated if ep.plan.label == "B2")
    committed = night_train_state.apply(list(plan.plan.moves))
    _, digest = state_to_snapshot(committed)
    return plan.plan, committed, digest


def _expectation(
    base: ProductionState,
    plan: CandidatePlan,
    digest: str | None = None,
    kinds: tuple[str, ...] = ("call_sheet",),
) -> VerificationExpectation:
    return VerificationExpectation(
        base_state=base, plan=plan, expected_digest=digest, artifact_kinds=kinds
    )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_all_six_checks_pass_for_a_clean_execution(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    plan, committed, digest = approved
    report = verify(committed, _expectation(night_train_state, plan, digest))
    assert report.valid is True
    assert [c.code for c in report.checks] == list(VERIFICATION_CODES)
    assert report.failures == ()


def test_report_always_has_exactly_six_checks_in_order(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    plan, committed, digest = approved
    report = verify(committed, _expectation(night_train_state, plan, digest))
    assert len(report.checks) == 6
    assert [c.code for c in report.checks] == ["V1", "V2", "V3", "V4", "V5", "V6"]


# ---------------------------------------------------------------------------
# One failure per check
# ---------------------------------------------------------------------------


def _check(report, code: str):
    return next(c for c in report.checks if c.code == code)


def test_v1_catches_a_hard_violation(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    """Pull the courtyard call back to 07:00 and the turnaround breaks again."""
    from pri.domain.models import ShiftCallTime

    plan, committed, _ = approved
    broken = committed.apply(
        [
            ShiftCallTime(
                date=date(2026, 9, 11),
                new_call_time=committed.schedule.days[0].call_time.replace(
                    year=2026, month=9, day=11, hour=7, minute=0
                ),
            )
        ]
    )
    report = verify(broken, _expectation(night_train_state, plan))
    assert _check(report, "V1").passed is False
    assert "C001" in _check(report, "V1").detail


def test_v2_catches_a_dropped_scene(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    plan, committed, _ = approved
    days = list(committed.schedule.days)
    days[0] = days[0].model_copy(update={"scene_ids": days[0].scene_ids[:-1]})
    mangled = committed.model_copy(
        update={"schedule": committed.schedule.model_copy(update={"days": tuple(days)})}
    )
    report = verify(mangled, _expectation(night_train_state, plan))
    assert _check(report, "V2").passed is False
    assert "dropped" in _check(report, "V2").detail


def test_v3_catches_a_broken_parent_pointer(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    plan, committed, _ = approved
    orphan = committed.model_copy(update={"parent_version": None})
    report = verify(orphan, _expectation(night_train_state, plan))
    assert _check(report, "V3").passed is False
    assert "parent_version" in _check(report, "V3").detail.lower()


def test_v3_catches_a_digest_that_does_not_recompute(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    plan, committed, _ = approved
    report = verify(committed, _expectation(night_train_state, plan, "deadbeef" * 8))
    assert _check(report, "V3").passed is False
    assert "digest" in _check(report, "V3").detail.lower()


def test_v4_catches_a_move_that_did_not_land(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    """Approve a plan that claims to move S12 somewhere it was never sent."""
    plan, committed, _ = approved
    lying_plan = plan.model_copy(
        update={"moves": (*plan.moves, MoveSceneToDay(scene_id="S12", target_date=SEP_16))}
    )
    report = verify(committed, _expectation(night_train_state, lying_plan))
    assert _check(report, "V4").passed is False
    assert "S12" in _check(report, "V4").detail


def test_v5_catches_a_prerequisite_shot_out_of_order(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    """Send S18 past S28, which depends on it."""
    plan, committed, _ = approved
    broken = committed.apply([MoveSceneToDay(scene_id="S18", target_date=date(2026, 9, 30))])
    report = verify(broken, _expectation(night_train_state, plan))
    assert _check(report, "V5").passed is False


def test_v6_catches_a_missing_call_sheet(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    plan, committed, digest = approved
    report = verify(committed, _expectation(night_train_state, plan, digest, kinds=()))
    assert _check(report, "V6").passed is False
    assert "call_sheet" in _check(report, "V6").detail
    assert report.valid is False


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_verify_is_pure(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    plan, committed, digest = approved
    before = state_to_snapshot(committed)
    verify(committed, _expectation(night_train_state, plan, digest))
    assert state_to_snapshot(committed) == before


def test_report_timestamp_is_recent(
    night_train_state: ProductionState,
    approved: tuple[CandidatePlan, ProductionState, str],
) -> None:
    from datetime import UTC, datetime

    plan, committed, digest = approved
    report = verify(committed, _expectation(night_train_state, plan, digest))
    assert datetime.now(UTC) - report.at < timedelta(seconds=5)
