"""Let someone type a disruption during the demo, without lowering the bar.

A judge types "the courtyard permit fell through for Thursday" and watches it
run. The appeal is obvious; the risk is that it becomes a second, softer way
into the engine, where the model's output is trusted because it came from a
nicer-looking endpoint.

It does not get one. The endpoint's whole job is to turn a sentence into a
`DisruptionEvent` and put that event on `production.events`. From there it is
consumed, recorded, validated, planned and verified by exactly the code every
other event goes through — the consumer cannot tell an injected event from one
a first AD's tooling published, because there is nothing different about it
except `source: live_injection` in the audit trail.

Three guards, none of them optional:

    A passcode. The deployed URL is public and this is a write path with a
    billed model call behind it. An unset passcode disables the endpoint
    rather than opening it.

    A rate limit, applied before the model call, so a loop cannot spend the
    project's quota or fill the topic.

    The closed-set check in `agent/extraction.py`, which refuses rather than
    guesses when the text names something that is not in the production.

What this endpoint deliberately does not do is call `recover`. Publishing to
Kafka and letting the consumer drive is slower to write and slower to run, and
it is the only version where "it goes through the same pipeline" is a fact
rather than a claim.
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from pri.agent.extraction import (
    Clarification,
    ExtractionError,
    build_event,
    closed_sets,
    extract_disruption,
)

# Runtime imports, not type-only: FastAPI resolves these annotations with
# get_type_hints() to build the dependency graph, so moving them into a
# TYPE_CHECKING block makes every route in this module fail at startup.
from pri.api.deps import RepoDep, SettingsDep  # noqa: TC001

if TYPE_CHECKING:
    from pri.config import Settings

__all__ = ["router"]

_log = structlog.get_logger(__name__)

router = APIRouter(tags=["injection"])

#: Recent request timestamps per caller, for the rate limit. In-process, which
#: matches the deployment: `pri-api` runs as a single instance because the SSE
#: broker requires it, so there is no second process for a caller to spill
#: into. If that ever changes, this needs the same shared backing the broker
#: does — and `test_cloud_run_contract.py` fails the moment the pin is lifted.
_RECENT: dict[str, deque[float]] = {}
_WINDOW_SECONDS = 60.0


class InjectRequest(BaseModel):
    """What the console posts."""

    text: str = Field(min_length=1, max_length=500)
    production_id: str | None = None


class InjectResponse(BaseModel):
    """Either an accepted disruption or a request to say it differently."""

    status: str
    detail: str
    event_id: str | None = None
    event_type: str | None = None
    entity_id: str | None = None
    entity_label: str | None = None
    day: str | None = None
    suggestions: list[dict[str, str]] = []


def _require_passcode(settings: Settings, supplied: str | None) -> None:
    """Closed unless a passcode is configured and matches.

    Both halves matter. A missing configured passcode disables the endpoint
    instead of waving callers through, because the failure of an unset secret
    must be "nobody can use this", never "everybody can".
    """
    expected = settings.pri_inject_passcode
    if not expected:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Live injection is not enabled on this deployment.",
        )
    if not supplied or supplied != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong or missing injection passcode.")


def _rate_limit(request: Request, settings: Settings) -> None:
    """Before the model call, so a rejected caller costs nothing but a lookup."""
    limit = settings.pri_inject_rate_limit_per_minute
    if limit <= 0:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Live injection is rate-limited to zero."
        )

    caller = request.client.host if request.client else "unknown"
    now = time.monotonic()
    seen = _RECENT.setdefault(caller, deque())
    while seen and now - seen[0] > _WINDOW_SECONDS:
        seen.popleft()
    if len(seen) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"More than {limit} disruptions a minute. Wait a moment and try again.",
        )
    seen.append(now)


@router.post(
    "/api/disruptions/inject",
    response_model=InjectResponse,
    summary="Report a disruption in plain language",
)
async def inject_disruption(
    body: InjectRequest,
    request: Request,
    repo: RepoDep,
    settings: SettingsDep,
    x_inject_passcode: Annotated[str | None, Header()] = None,
) -> InjectResponse:
    """Classify typed text against the live production, then publish it.

    Inputs:
        body:              The sentence, and optionally which production.
        x_inject_passcode: The shared passcode header.

    Outputs:
        ``accepted`` with the event id once it is on the topic, or
        ``needs_clarification`` with the nearest matches and no event.

    Failure modes:
        401 for a bad passcode, 404 when injection is disabled, 429 over the
        rate limit, 503 if the model or the broker is unreachable. A sentence
        that cannot be resolved is a 200 carrying a clarification: it is an
        answer about the input, not a failure of the service.
    """
    _require_passcode(settings, x_inject_passcode)
    _rate_limit(request, settings)

    production_id = body.production_id or settings.pri_demo_production_id
    state = await repo.get_current_state(production_id)

    try:
        outcome = await extract_disruption(body.text, state, settings)
    except ExtractionError as exc:
        _log.warning("injection_model_unavailable", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach the model to classify that. Use the demo button instead.",
        ) from exc

    if isinstance(outcome, Clarification):
        return InjectResponse(
            status="needs_clarification",
            detail=outcome.reason,
            suggestions=[
                {"id": choice.id, "label": choice.label, "kind": choice.kind}
                for choice in outcome.suggestions
            ],
        )

    event = build_event(outcome, state)

    # Kafka is the only way out of this function. There is no fallback that
    # calls the engine directly: an injected event that skipped the topic would
    # be a different pipeline wearing the same name, and the claim this
    # endpoint makes is that it is the same one.
    kafka = getattr(request.app.state, "kafka", None)
    if kafka is None or not kafka.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The event fabric is unavailable, and this path deliberately has no "
            "way around it. Use the demo button, which records directly.",
        )
    kafka.publish_disruption(event)

    _log.info(
        "injection_published",
        event_id=event.event_id,
        event_type=event.event_type,
        production_id=production_id,
    )
    return InjectResponse(
        status="accepted",
        detail=(
            f"Published to production.events. {outcome.entity.label} on "
            f"{outcome.day.isoformat()} — watch the timeline."
        ),
        event_id=event.event_id,
        event_type=event.event_type,
        entity_id=outcome.entity.id,
        entity_label=outcome.entity.label,
        day=outcome.day.isoformat(),
    )


@router.get(
    "/api/disruptions/injectable",
    summary="What can be named in a typed disruption",
)
async def injectable_entities(
    repo: RepoDep,
    settings: SettingsDep,
    production_id: str | None = None,
) -> dict[str, Any]:
    """The closed set, so the console can show a judge what exists.

    Open, unlike the POST: it reveals nothing a viewer cannot already read off
    the strip board, and a presenter needs it on screen while typing.
    """
    state = await repo.get_current_state(production_id or settings.pri_demo_production_id)
    entities, dates = closed_sets(state)
    return {
        "production_id": state.production.id,
        "locations": [{"id": e.id, "label": e.label} for e in entities if e.kind == "location"],
        "cast": [{"id": e.id, "label": e.label} for e in entities if e.kind == "person"],
        "dates": [d.isoformat() for d in dates],
        "enabled": bool(settings.pri_inject_passcode),
    }
