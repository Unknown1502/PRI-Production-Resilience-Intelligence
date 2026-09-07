"""Domain exceptions mapped to HTTP responses.

Every handler returns the same :class:`~pri.api.schemas.ErrorBody` shape, and
constraint failures carry their rule code as a field rather than buried in the
message.  The recovery screen renders a violation card straight off that code;
making the frontend regex an error string would be a bad contract.

Status choices:
    409  the request was well-formed but the world moved, or a gate refused
    422  the request itself cannot be satisfied
    404  the thing does not exist
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from pri.api.schemas import ErrorBody
from pri.artifacts.call_sheet import CallSheetError
from pri.engine.simulation.candidates import CandidateGenerationError
from pri.engine.simulation.replan import ReplanError
from pri.engine.transition import (
    ApprovalRequiredError,
    AuthorizationError,
    ConstraintViolationError,
    PolicyViolationError,
    TransitionError,
    VerificationFailedError,
)
from pri.importer.service import ImportNotFoundError
from pri.persistence.errors import (
    PersistenceError,
    ProductionNotFoundError,
    SessionNotFoundError,
    StaleStateError,
    StateVersionNotFoundError,
)

__all__ = ["install_error_handlers"]

_log = structlog.get_logger(__name__)


def _body(
    error: str,
    detail: str,
    *,
    step: str | None = None,
    rule_code: str | None = None,
    rule_codes: tuple[str, ...] = (),
) -> dict[str, object]:
    return ErrorBody(
        error=error,
        detail=detail,
        step=step,
        rule_code=rule_code,
        rule_codes=list(rule_codes),
    ).model_dump()


def install_error_handlers(app: FastAPI) -> None:
    """Register every domain exception handler on the app."""

    @app.exception_handler(StaleStateError)
    async def _stale(_request: Request, exc: StaleStateError) -> JSONResponse:
        _log.warning("stale_state", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body("stale_state", str(exc), step="validate_state"),
        )

    @app.exception_handler(ConstraintViolationError)
    async def _constraints(_request: Request, exc: ConstraintViolationError) -> JSONResponse:
        _log.warning("constraints_failed", codes=exc.codes, error=exc.message)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body(
                "constraints_failed",
                exc.message,
                step=exc.step,
                rule_code=exc.codes[0] if exc.codes else None,
                rule_codes=exc.codes,
            ),
        )

    @app.exception_handler(VerificationFailedError)
    async def _verification(_request: Request, exc: VerificationFailedError) -> JSONResponse:
        codes = tuple(c.code for c in exc.report.failures)
        _log.error("verification_failed", codes=codes)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body(
                "verification_failed",
                exc.message,
                step=exc.step,
                rule_code=codes[0] if codes else None,
                rule_codes=codes,
            ),
        )

    @app.exception_handler(ApprovalRequiredError)
    async def _approval(_request: Request, exc: ApprovalRequiredError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body("approval_missing", exc.message, step=exc.step),
        )

    @app.exception_handler(AuthorizationError)
    async def _authz(_request: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_body("not_authorized", exc.message, step=exc.step),
        )

    @app.exception_handler(PolicyViolationError)
    async def _policy(_request: Request, exc: PolicyViolationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body("policy_violation", exc.message, step=exc.step),
        )

    @app.exception_handler(TransitionError)
    async def _transition(_request: Request, exc: TransitionError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body("transition_failed", exc.message, step=exc.step),
        )

    @app.exception_handler(ProductionNotFoundError)
    async def _no_production(_request: Request, exc: ProductionNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("production_not_found", str(exc)),
        )

    @app.exception_handler(StateVersionNotFoundError)
    async def _no_version(_request: Request, exc: StateVersionNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("version_not_found", str(exc)),
        )

    @app.exception_handler(SessionNotFoundError)
    async def _no_session(_request: Request, exc: SessionNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("session_not_found", str(exc)),
        )

    @app.exception_handler(CallSheetError)
    async def _no_call_sheet(_request: Request, exc: CallSheetError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_body("call_sheet_unavailable", str(exc)),
        )

    @app.exception_handler(CandidateGenerationError)
    async def _no_candidates(_request: Request, exc: CandidateGenerationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_body("candidate_generation_failed", str(exc)),
        )

    @app.exception_handler(ReplanError)
    async def _replan(_request: Request, exc: ReplanError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_body("recovery_failed", str(exc)),
        )

    @app.exception_handler(ImportNotFoundError)
    async def _no_import(_request: Request, exc: ImportNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("import_not_found", str(exc)),
        )

    @app.exception_handler(PersistenceError)
    async def _persistence(_request: Request, exc: PersistenceError) -> JSONResponse:
        _log.error("persistence_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body("persistence_error", str(exc)),
        )
