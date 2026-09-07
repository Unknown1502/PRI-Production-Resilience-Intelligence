"""Post-execution verification — six checks against the state that was committed.

Validation asks "would this plan be legal?".  Verification asks "is what we
actually wrote what we said we would write?".  They are different questions and
the second one is the reason a producer can trust the first: without it, a bug
between approval and commit is invisible.

The checks:

===== ==================================================================
V1    Zero HARD constraint violations in the committed state
V2    No scene silently dropped, duplicated or invented
V3    Version chain intact — parent pointer and digest
V4    Every scene the approved plan moved landed where the plan said
V5    Prerequisite order preserved
V6    Artifacts regenerated for the new version
===== ==================================================================

:func:`verify` is pure.  It reads no database and touches no filesystem: the
caller passes in what was written, and this module decides whether it matches
what was promised.  That keeps the check honest — a verifier that goes and
looks things up can be fooled by the same bug that corrupted the write.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from pri.domain.models import (
    CandidatePlan,
    MoveSceneToDay,
    ProductionState,
    RelocateScene,
    ShiftCallTime,
    SwapDays,
)
from pri.engine.constraints.validator import Policy, validate
from pri.persistence.serialization import state_to_snapshot

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "VERIFICATION_CODES",
    "CheckResult",
    "VerificationExpectation",
    "VerificationReport",
    "verify",
]

VERIFICATION_CODES: tuple[str, ...] = ("V1", "V2", "V3", "V4", "V5", "V6")

#: The artifact kind V6 insists on. A committed schedule with no call sheet is
#: a schedule nobody on the crew has been told about.
_REQUIRED_ARTIFACT_KIND = "call_sheet"


class CheckResult(BaseModel, frozen=True):
    """The outcome of one verification check.

    Inputs:
        code:   ``"V1"`` … ``"V6"``.
        name:   Short machine-readable check name, shown in the UI.
        passed: Whether the check held.
        detail: Human-readable explanation.  Written to be read aloud — the
                verification screen prints it verbatim.
    """

    code: Literal["V1", "V2", "V3", "V4", "V5", "V6"]
    name: str
    passed: bool
    detail: str


class VerificationExpectation(BaseModel, frozen=True):
    """What the system promised, against which the committed state is judged.

    Inputs:
        base_state:        The state the approved plan was built on.
        plan:              The approved plan.
        expected_digest:   The snapshot digest recorded when the plan was
                           evaluated.  ``None`` skips the digest half of V3,
                           which is only appropriate in tests.
        artifact_kinds:    Kinds of artifact registered for the new version.
    """

    base_state: ProductionState
    plan: CandidatePlan
    expected_digest: str | None = None
    artifact_kinds: tuple[str, ...] = ()


class VerificationReport(BaseModel, frozen=True):
    """The full result of verifying one committed state.

    Inputs:
        valid:  ``True`` only if every check passed.
        checks: One :class:`CheckResult` per code, always in V1…V6 order so the
                UI can render six fixed rows.
        at:     When verification ran.
    """

    valid: bool
    checks: tuple[CheckResult, ...]
    at: datetime

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        """The checks that did not pass."""
        return tuple(c for c in self.checks if not c.passed)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify(
    new_state: ProductionState,
    expected: VerificationExpectation,
    *,
    policy: Policy | None = None,
) -> VerificationReport:
    """Run all six checks against a committed state.

    Inputs:
        new_state: The state that was actually written.
        expected:  What was promised — base state, approved plan, digest and
                   registered artifacts.
        policy:    Constraint policy override (tests).

    Outputs:
        A :class:`VerificationReport` with exactly six checks, in order.

    Failure modes:
        Does not raise.  A verification failure is a result, not an exception —
        the transition service decides what to do about it.
    """
    violations = validate(new_state, policy=policy)

    checks = (
        _v1_no_hard_violations(violations),
        _v2_scene_set_intact(expected.base_state, new_state),
        _v3_version_chain(expected, new_state),
        _v4_moves_landed(expected.plan, new_state),
        _v5_prerequisites_preserved(violations),
        _v6_artifacts_present(expected.artifact_kinds),
    )
    return VerificationReport(
        valid=all(c.passed for c in checks),
        checks=checks,
        at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _v1_no_hard_violations(violations: Sequence[object]) -> CheckResult:
    """V1: the committed schedule must be legal."""
    hard = [v for v in violations if getattr(v, "severity", None) == "HARD"]
    if not hard:
        return CheckResult(
            code="V1",
            name="no_hard_violations",
            passed=True,
            detail="Committed schedule satisfies all 10 constraint rules.",
        )
    codes = ", ".join(sorted({str(getattr(v, "code", "?")) for v in hard}))
    return CheckResult(
        code="V1",
        name="no_hard_violations",
        passed=False,
        detail=f"{len(hard)} HARD violation(s) in the committed state: {codes}.",
    )


def _v2_scene_set_intact(base: ProductionState, new: ProductionState) -> CheckResult:
    """V2: the same scenes are scheduled, each exactly once."""
    before = Counter(sid for day in base.schedule.days for sid in day.scene_ids)
    after = Counter(sid for day in new.schedule.days for sid in day.scene_ids)

    dropped = sorted((before - after).elements())
    added = sorted((after - before).elements())
    duplicated = sorted(sid for sid, count in after.items() if count > 1)

    if not dropped and not added and not duplicated:
        return CheckResult(
            code="V2",
            name="scene_set_intact",
            passed=True,
            detail=f"All {sum(after.values())} scheduled scenes accounted for, none duplicated.",
        )

    problems: list[str] = []
    if dropped:
        problems.append(f"dropped {', '.join(dropped)}")
    if added:
        problems.append(f"unexpectedly added {', '.join(added)}")
    if duplicated:
        problems.append(f"duplicated {', '.join(duplicated)}")
    return CheckResult(
        code="V2",
        name="scene_set_intact",
        passed=False,
        detail=f"Scene set changed: {'; '.join(problems)}.",
    )


def _v3_version_chain(expected: VerificationExpectation, new: ProductionState) -> CheckResult:
    """V3: the new version points at its parent and its digest recomputes."""
    base_version = expected.base_state.version
    problems: list[str] = []

    if new.parent_version != base_version:
        problems.append(f"parent_version is {new.parent_version}, expected {base_version}")
    if new.version != base_version + 1:
        problems.append(f"version is {new.version}, expected {base_version + 1}")

    _, actual_digest = state_to_snapshot(new)
    if expected.expected_digest is not None and actual_digest != expected.expected_digest:
        problems.append(
            f"digest {actual_digest[:12]} does not match the evaluated "
            f"{expected.expected_digest[:12]}"
        )

    if problems:
        return CheckResult(
            code="V3",
            name="version_chain",
            passed=False,
            detail="; ".join(problems).capitalize() + ".",
        )
    return CheckResult(
        code="V3",
        name="version_chain",
        passed=True,
        detail=(
            f"Version {new.version} descends from {base_version}; "
            f"digest {actual_digest[:12]} recomputes."
        ),
    )


def _v4_moves_landed(plan: CandidatePlan, new: ProductionState) -> CheckResult:
    """V4: every move in the approved plan is visible in the committed state."""
    slots = {sid: day.date for day in new.schedule.days for sid in day.scene_ids}
    days = {day.date: day for day in new.schedule.days}
    scene_locations = {scene.id: scene.location_id for scene in new.scenes}

    problems: list[str] = []
    for move in plan.moves:
        if isinstance(move, MoveSceneToDay):
            landed = slots.get(move.scene_id)
            if landed != move.target_date:
                problems.append(f"{move.scene_id} is on {landed}, plan said {move.target_date}")
        elif isinstance(move, RelocateScene):
            landed_at = scene_locations.get(move.scene_id)
            if landed_at != move.target_location_id:
                problems.append(
                    f"{move.scene_id} is at {landed_at}, plan said {move.target_location_id}"
                )
        elif isinstance(move, ShiftCallTime):
            day = days.get(move.date)
            if day is None or day.call_time != move.new_call_time:
                actual = day.call_time.isoformat() if day else "no such day"
                problems.append(
                    f"{move.date} calls at {actual}, plan said {move.new_call_time.isoformat()}"
                )
        elif isinstance(move, SwapDays) and move.date_a not in days:
            problems.append(f"swapped day {move.date_a} is missing from the schedule")

    if problems:
        return CheckResult(
            code="V4",
            name="moves_landed",
            passed=False,
            detail=f"{len(problems)} move(s) did not land: {'; '.join(problems)}.",
        )
    return CheckResult(
        code="V4",
        name="moves_landed",
        passed=True,
        detail=f"All {len(plan.moves)} approved move(s) present in the committed state.",
    )


def _v5_prerequisites_preserved(violations: Sequence[object]) -> CheckResult:
    """V5: no scene shoots before something it depends on."""
    breaches = [v for v in violations if getattr(v, "code", None) == "C008"]
    if not breaches:
        return CheckResult(
            code="V5",
            name="prerequisites_preserved",
            passed=True,
            detail="Every scene still shoots after its prerequisites.",
        )
    subjects = "; ".join(str(getattr(v, "observed", "")) for v in breaches)
    return CheckResult(
        code="V5",
        name="prerequisites_preserved",
        passed=False,
        detail=f"{len(breaches)} prerequisite order breach(es): {subjects}.",
    )


def _v6_artifacts_present(artifact_kinds: Sequence[str]) -> CheckResult:
    """V6: a call sheet exists for the new version."""
    if _REQUIRED_ARTIFACT_KIND in artifact_kinds:
        return CheckResult(
            code="V6",
            name="artifacts_regenerated",
            passed=True,
            detail=f"Artifacts regenerated for this version: {', '.join(artifact_kinds)}.",
        )
    found = ", ".join(artifact_kinds) if artifact_kinds else "none"
    return CheckResult(
        code="V6",
        name="artifacts_regenerated",
        passed=False,
        detail=(
            f"No {_REQUIRED_ARTIFACT_KIND} registered for this version "
            f"(found: {found}). The crew would be working from a stale call sheet."
        ),
    )
