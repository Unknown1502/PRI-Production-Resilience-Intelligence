# PROMPT 08 — Verification Engine & Transition Service

## Context

A production state can be **valid** (satisfies constraints) without being
**verified** (actually matches what was approved). Verification answers:
*"Is what we wrote exactly what we promised?"*

Without it, a bug between approval and commit is invisible. This module is the
reason a producer can trust the system's output.

---

## `src/pri/engine/verification/verify.py`

```python
verify(
    new_state: ProductionState,
    expected: VerificationExpectation,
    *,
    policy: Policy | None = None,
) -> VerificationReport
```

`verify` is a **pure function** — it reads no database and touches no filesystem.
The caller passes in what was written; this module decides whether it matches
what was promised. That keeps the check honest.

### Types

```python
class VerificationExpectation(BaseModel, frozen=True):
    base_state: ProductionState
    plan: CandidatePlan
    expected_digest: str | None = None    # None skips the digest half of V3
    artifact_kinds: tuple[str, ...] = ()  # kinds registered for this version

class CheckResult(BaseModel, frozen=True):
    code: Literal["V1", "V2", "V3", "V4", "V5", "V6"]
    name: str
    passed: bool
    detail: str    # human-readable; UI prints verbatim; demo narrator reads aloud

class VerificationReport(BaseModel, frozen=True):
    valid: bool
    checks: tuple[CheckResult, ...]   # always exactly six, always in V1…V6 order
    at: datetime
```

### The six checks

| Code | Name | Invariant |
|------|------|-----------|
| V1 | `no_hard_violations` | Committed state has zero HARD constraint violations |
| V2 | `scene_set_intact` | No scene silently dropped, duplicated or invented vs base |
| V3 | `version_chain` | `new.parent_version == base.version`, `new.version == base.version + 1`, digest recomputes |
| V4 | `moves_landed` | Every move in the approved plan is visible in the committed state |
| V5 | `prerequisites_preserved` | No C008 violation in the committed state |
| V6 | `artifacts_regenerated` | A `call_sheet` artifact is registered for the new version |

**V3 detail**: use `state_to_snapshot(new_state)` to compute the actual digest,
compare to `expected.expected_digest`. If `expected_digest` is `None`, skip the
digest comparison but still check version and parent_version.

**V4 detail**: for each move type:
- `MoveSceneToDay`: scene must appear on `target_date` in the schedule
- `RelocateScene`: scene must have `target_location_id` in `new_state.scenes`
- `ShiftCallTime`: the shooting day must have `call_time == new_call_time`
- `SwapDays`: both dates must still exist in the schedule

**V2 detail**: use `Counter` — a scene that appears twice is a duplication bug.

**Detail strings**: write them to be read aloud. The word "observed" should
appear for failures (e.g. `"3 HARD violations in the committed state: C001, C004, C009"`).

### Module-level constant

```python
VERIFICATION_CODES: tuple[str, ...] = ("V1", "V2", "V3", "V4", "V5", "V6")
```

---

## `src/pri/engine/transition.py`

The seven-step guarded execution path. Architecture law 4 is implemented here
and **nowhere else**. Every step is recorded to the audit log *before* proceeding.

```python
class TransitionService:
    def execute(
        self,
        session_id: str,
        plan_id: str,
        approver: str,
        authorization: str,
    ) -> TransitionResult
```

### The seven steps — in this exact order

| Step | Guard | Failure exception |
|------|-------|-------------------|
| 1 | `validate_state` | head version == `plan.base_version` | `StaleStateError` |
| 2 | `validate_constraints` | re-run validator on the resulting state, **not cached** | `ConstraintViolationError` |
| 3 | `validate_policy` | `policy.approval.schedule_change.required` is satisfied | `PolicyViolationError` |
| 4 | `verify_authorization` | approver present and permitted | `AuthorizationError` |
| 5 | `request_approval` | approval row exists with `decision == APPROVED` | `ApprovalRequiredError` |
| 6 | `execute` | `commit_state` atomically, regenerate artifacts | `ExecutionError` |
| 7 | `verify_result` | `verify()` all six checks pass | rolls back commit, sets `HUMAN_REVIEW` |

**No code path may reach step 6 without passing steps 1–5.**
Write a test that asserts this by monkeypatching `commit_state` and confirming
the guard raises when step 2 is bypassed.

**Idempotency**: executing the same `(session_id, plan_id)` twice returns the
first result and writes **no new version**.

**Rollback on V7 failure**: if `verify()` returns `valid=False`, mark the
session `HUMAN_REVIEW` and roll back the committed state in the same transaction.

```python
class TransitionResult(BaseModel, frozen=True):
    session_id: str
    plan_id: str
    new_version: int
    verification: VerificationReport
    audit_entries: tuple[str, ...]    # one per step completed
```

---

## Tests

```python
# tests/engine/test_transition.py

class TestTransitionHappyPath:
    def test_full_execution_produces_version_increment(...)
    def test_six_verification_checks_all_pass(...)

class TestTransitionGuards:
    def test_stale_state_raises_before_commit(...)
    def test_hard_violation_raises_before_commit(...)
    def test_missing_approval_raises_before_commit(...)
    def test_cannot_reach_commit_without_passing_guards(...)  # monkeypatch

class TestTransitionIdempotency:
    def test_double_execute_returns_first_result(...)
    def test_double_execute_writes_no_new_version(...)

class TestVerificationRollback:
    def test_failed_v1_rolls_back_commit(...)
```

---

## Acceptance criteria

- `verify()` is a pure function: no IO, no mutation
- **No code path can reach `commit_state` without passing steps 1–5** — covered by test
- The idempotency case is tested
- The rollback case is tested
- `ruff` + `mypy --strict` clean on both modules

## Hard constraints

- All six `CheckResult.detail` strings are written to be read aloud
- `verify()` never raises — a verification failure is a result, not an exception
- The transition service must use typed exceptions, one per step
