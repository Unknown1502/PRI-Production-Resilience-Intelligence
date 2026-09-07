# Artifact 08 — Verification Engine & Transition Service

**Prompt:** [PROMPT_08_verification_transition.md](../prompts/PROMPT_08_verification_transition.md)
**Gate:** `SCORE`
**Outcome:** complete — 11 verification tests, 16 transition tests

---

## Plan

Two modules. `verify.py` answers "is what we wrote what we promised?" as a pure
function. `transition.py` implements architecture law 4 — the seven-guard
execution path — and it is the only place in the codebase that does.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`engine/verification/verify.py`](../../../src/pri/engine/verification/verify.py) | 331 | V1–V6, `VerificationExpectation`, `VerificationReport` |
| [`engine/transition.py`](../../../src/pri/engine/transition.py) | 613 | `TransitionService`, seven typed exceptions, `TransitionResult` |
| `tests/engine/verification/test_verify.py` | 11 tests | One passing path, one failure per check |
| `tests/engine/test_transition.py` | 16 tests | Happy path, five guards, idempotency, rollback |

## Files removed

`verification/verifier.py` — an earlier pass using codes V001–V005 and a
different signature. Replaced.

## The six checks

| Code | Name | Invariant |
|---|---|---|
| V1 | `no_hard_violations` | Committed state satisfies all ten rules |
| V2 | `scene_set_intact` | No scene dropped, duplicated or invented (via `Counter`) |
| V3 | `version_chain` | Parent pointer correct, version incremented, digest recomputes |
| V4 | `moves_landed` | Every approved move visible in the committed state |
| V5 | `prerequisites_preserved` | No C008 in the committed state |
| V6 | `artifacts_regenerated` | A `call_sheet` is registered for the new version |

## The seven guards

| # | Step | Refuses with |
|---|---|---|
| 1 | `validate_state` | `StaleStateError` |
| 2 | `validate_constraints` | `ConstraintViolationError` |
| 3 | `validate_policy` | `PolicyViolationError` |
| 4 | `verify_authorization` | `AuthorizationError` |
| 5 | `request_approval` | `ApprovalRequiredError` |
| 6 | `execute` | `ExecutionError` |
| 7 | `verify_result` | `VerificationFailedError`, and the write is rolled back |

Each step writes to `audit_log` **before** the next runs, so a process that dies
mid-sequence leaves a record of exactly how far it got.

## Decisions

**`verify()` is pure and never raises.** It reads no database and touches no
filesystem — the caller passes in what was written. A verifier that goes and
looks things up can be fooled by the same bug that corrupted the write. A
verification failure is a *result*, not an exception; the transition service
decides what to do about it.

**Step 2 re-validates. It does not trust the stored verdict.** The verdict
recorded when the plan was generated described a different head. Between then
and now anything could have changed.

**Rollback without DELETE.** `state_versions` is append-only, so "roll back the
commit" cannot mean deleting a row. Verification runs inside the insert's
transaction, through `commit_state`'s `post_insert` hook: if the checks fail the
transaction aborts and the version was never written at all. The session is then
parked in `HUMAN_REVIEW`.

**Idempotency is read out of the audit log, not held in memory.** The guarantee
has to survive a process restart, because a retry after a dropped connection is
the case it exists for.

**A replayed execution re-verifies.** `_reverify` runs the six checks against the
stored state rather than returning an empty report. "We already did this" is not
the same claim as "and it was correct."

## The tripwire test

The prompt asks for proof that no path reaches `commit_state` without passing
steps 1–5. `test_cannot_reach_commit_without_passing_guards` monkeypatches
`commit_state` with a function that records being called and raises, then drives
two failure paths — an unapproved plan and an approved-but-invalid one — and
asserts the recorder stays empty.

If a future refactor reorders the sequence so the write happens before the
approval check, that test fails rather than shipping a system that writes first
and asks later.

## How to run it

```bash
docker compose -f docker-compose.dev.yml up -d
pytest tests/engine/verification tests/engine/test_transition.py -v
```

## Acceptance criteria

- ✅ `verify()` pure — no IO, no mutation
- ✅ No code path reaches `commit_state` without passing steps 1–5 (tripwire test)
- ✅ Double execute returns the first result and writes no new version
- ✅ Failed verification rolls the commit back; head stays at version 1
- ✅ Session marked `HUMAN_REVIEW` on rollback
- ✅ Typed exception per step
- ✅ ruff + `mypy --strict` clean

## Not covered

**`verify_authorization` checks a name, not an identity.** There is no identity
provider. The `authorization` argument is recorded in the audit detail but never
validated against anything, and `permitted_approvers` defaults to "any non-blank
name". That is the demo posture and it is called out in the docstring.

**Artifacts are written before the transaction, not inside it.** A rolled-back
transaction leaves orphaned PDFs under a version directory that no state row
references. Harmless, and cheaper than a two-phase write, but it is litter.

**V6 trusts what the caller says was registered.** It checks
`artifact_kinds` rather than the filesystem — correct for purity, but it means a
caller that passes `("call_sheet",)` without having written one gets a pass.
