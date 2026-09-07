"""Turning a sentence a judge typed into a disruption event, or refusing to.

This is the one input PRI does not control. Everywhere else the model selects
strategy families from a fixed list and the engine does the rest; here a human
types free text and something has to decide what it means. That makes it the
place the architecture's guarantee could be quietly lost, so the boundary is
drawn explicitly and enforced twice.

What the model is allowed to do:

    Choose one of exactly two event types.
    Choose one entity id from a list of ids that already exist in state.
    Choose one date from the list of days already on the board.

What the model cannot do, structurally rather than by instruction:

    Invent an entity. The prompt carries a closed set, and — because a prompt
    is a request, not a constraint — every id it returns is checked against
    the live state again on return. An id that is not there is not a smaller
    problem to be repaired; it means the model guessed, and the answer is a
    clarification.
    Invent a date. Same closed set, same recheck.
    Invent a number, a move, a cost, or a plan. It never sees them.
    Reach the engine at all. This module returns a `DisruptionEvent` and
    nothing else; that event is published to Kafka and travels the identical
    path as an event from any other source, through the same consumer, the
    same validation and the same verification.

The failure mode this is built against is a plausible-sounding lie: a model
asked about "the lighthouse" when no lighthouse exists is quite capable of
answering "LOC-04" with confidence. `tests/agent/test_injection_compliance.py`
attacks exactly that.
"""

from __future__ import annotations

import difflib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Literal

import structlog

from pri.domain.models import DisruptionEvent

if TYPE_CHECKING:
    from pri.config import Settings
    from pri.domain.models import ProductionState

__all__ = [
    "SUPPORTED_EVENT_TYPES",
    "Clarification",
    "EntityChoice",
    "Extracted",
    "ExtractionError",
    "build_event",
    "closed_sets",
    "extract_disruption",
    "resolve",
]

_log = structlog.get_logger(__name__)

#: The only two the live endpoint accepts. Both are hardened end to end with a
#: narrated scenario test; the other three are not, and returning a plan for
#: them from a judge's sentence would be presenting untested output as a
#: demonstration. See the README's limitations section.
SUPPORTED_EVENT_TYPES: tuple[str, ...] = ("location.blocked", "actor.unavailable")

#: How many near-misses a clarification offers back. Enough to be useful,
#: few enough that the reply is readable on a screen behind a presenter.
_MAX_SUGGESTIONS = 4


class ExtractionError(RuntimeError):
    """The model could not be reached or returned nothing usable."""


@dataclass(frozen=True)
class EntityChoice:
    """One thing in the production the text could be referring to."""

    id: str
    label: str
    kind: Literal["location", "person"]


@dataclass(frozen=True)
class Clarification:
    """A refusal that helps. Never accompanied by an event."""

    reason: str
    suggestions: tuple[EntityChoice, ...] = ()


@dataclass(frozen=True)
class Extracted:
    """A resolved disruption, every field traced to something already in state."""

    event_type: str
    entity: EntityChoice
    day: date
    raw_text: str


def closed_sets(state: ProductionState) -> tuple[tuple[EntityChoice, ...], tuple[date, ...]]:
    """The entities and dates the model is allowed to choose between.

    Inputs:
        state: The production as currently committed.

    Outputs:
        ``(entities, dates)`` — every location and cast member in the
        production, and every date the board can actually shoot on.

    Failure modes:
        None. A production with no cast or no days yields empty sets, and
        every extraction against it will clarify rather than guess.
    """
    entities: list[EntityChoice] = [
        EntityChoice(id=location.id, label=location.name, kind="location")
        for location in state.locations
    ]
    entities.extend(
        EntityChoice(id=person.id, label=person.name, kind="person")
        for person in state.people
        if person.role == "CAST"
    )
    dates = tuple(
        sorted(day.date for day in state.schedule.days if day.day_kind == "SHOOT" and day.scene_ids)
    )
    return tuple(entities), dates


def _prompt(text: str, entities: tuple[EntityChoice, ...], dates: tuple[date, ...]) -> str:
    """The extraction turn. A closed set in, a selection out."""
    locations = "\n".join(f"  {e.id} = {e.label}" for e in entities if e.kind == "location")
    cast = "\n".join(f"  {e.id} = {e.label}" for e in entities if e.kind == "person")
    days = ", ".join(d.isoformat() for d in dates)
    return f"""You classify a film production disruption reported in plain language.

Reply with ONE JSON object and nothing else:
{{"event_type": ..., "entity_id": ..., "date": ..., "confident": true|false}}

event_type must be exactly one of:
  "location.blocked"   - a place cannot be used
  "actor.unavailable"  - a cast member cannot work

entity_id MUST be copied exactly from one of these lists. Do not invent an id,
do not adapt one, and do not answer with an id that is not written below.

LOCATIONS:
{locations or "  (none)"}

CAST:
{cast or "  (none)"}

date MUST be copied exactly from this list: {days or "(none)"}

Set "confident" to false, and entity_id to null, if the report names something
that is not in the lists above, is too vague to identify one entry, or could
mean several of them. A wrong identification cancels a shooting day, so
answering "I am not sure" is correct and useful. Do not guess.

THE REPORT:
{text}
"""


def _parse(raw: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply that may be wrapped in prose."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match is None:
        raise ExtractionError("the model returned no JSON object")
    try:
        parsed: dict[str, Any] = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"the model returned malformed JSON: {exc}") from exc
    return parsed


def _suggest(text: str, entities: tuple[EntityChoice, ...]) -> tuple[EntityChoice, ...]:
    """Nearest entities by name, so a clarification can offer a way forward.

    Deterministic string similarity, deliberately: the model has already
    declined to identify this, and asking it again for suggestions would be
    asking the component that just said "I am not sure" to be sure.
    """
    lowered = text.lower()
    scored: list[tuple[float, EntityChoice]] = []
    for entity in entities:
        name = entity.label.lower()
        ratio = difflib.SequenceMatcher(None, lowered, name).ratio()
        # A name appearing verbatim in the sentence beats fuzzy similarity.
        if name in lowered or entity.id.lower() in lowered:
            ratio = 1.0
        else:
            for word in re.findall(r"[a-z']{3,}", lowered):
                if word in name:
                    ratio = max(ratio, 0.75)
        scored.append((ratio, entity))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return tuple(entity for ratio, entity in scored[:_MAX_SUGGESTIONS] if ratio > 0.25)


def resolve(
    reply: dict[str, Any],
    text: str,
    state: ProductionState,
) -> Extracted | Clarification:
    """Check the model's answer against the live production. Never trust it.

    This runs whether or not the model claimed confidence, and it is the half
    that makes the boundary real: the prompt asks for a closed set, this
    enforces one. Passing the list in the prompt reduces how often a model
    invents an id; it does not prevent it.

    Inputs:
        reply: The parsed JSON object the model returned.
        text:  The original report, for suggestions.
        state: The production to resolve against.

    Outputs:
        An :class:`Extracted` every field of which came from ``state``, or a
        :class:`Clarification` explaining why nothing was emitted.

    Failure modes:
        Does not raise. Every unusable answer becomes a clarification, because
        the caller must never be left deciding what a half-parsed extraction
        meant.
    """
    entities, dates = closed_sets(state)
    by_id = {entity.id: entity for entity in entities}

    if not reply.get("confident", False):
        return Clarification(
            reason="That did not name a location or cast member I could identify.",
            suggestions=_suggest(text, entities),
        )

    event_type = str(reply.get("event_type") or "")
    if event_type not in SUPPORTED_EVENT_TYPES:
        return Clarification(
            reason=(
                "Live injection currently supports a blocked location or an "
                "unavailable cast member. Equipment, weather and crew "
                "disruptions run in PRI but are not hardened enough to drive "
                "from typed input yet."
            ),
        )

    entity_id = reply.get("entity_id")
    entity = by_id.get(str(entity_id)) if entity_id else None
    if entity is None:
        # The model named something that is not in this production. This is
        # the adversarial case, and it is a refusal rather than a repair.
        _log.info("injection_entity_rejected", claimed=str(entity_id), text=text[:120])
        return Clarification(
            reason=(
                f"There is nothing called {entity_id!r} in this production."
                if entity_id
                else "I could not tell which location or cast member that refers to."
            ),
            suggestions=_suggest(text, entities),
        )

    # An event type that does not match the kind of thing named is a
    # misclassification, not a detail to smooth over: blocking a person or
    # making a location ill would both produce an impact of zero scenes.
    if event_type == "location.blocked" and entity.kind != "location":
        return Clarification(
            reason=f"{entity.label} is a cast member, not a location.",
            suggestions=(entity,),
        )
    if event_type == "actor.unavailable" and entity.kind != "person":
        return Clarification(
            reason=f"{entity.label} is a location, not a cast member.",
            suggestions=(entity,),
        )

    day = _resolve_day(reply.get("date"), dates, entity, state)
    if day is None:
        return Clarification(
            reason=(
                f"{entity.label} is not on the schedule on a day I can act on. "
                "Name a shooting day that is still ahead."
            ),
            suggestions=(entity,),
        )

    return Extracted(event_type=event_type, entity=entity, day=day, raw_text=text)


def _resolve_day(
    claimed: object,
    dates: tuple[date, ...],
    entity: EntityChoice,
    state: ProductionState,
) -> date | None:
    """The day the disruption falls on, always one already on the board.

    A model that picks a date outside the closed set has invented a number, so
    its answer is dropped rather than corrected. The fallback is a lookup, not
    a guess: the first scheduled day this entity actually appears on.
    """
    if claimed:
        try:
            parsed = date.fromisoformat(str(claimed))
        except ValueError:
            parsed = None
        if parsed is not None and parsed in dates:
            return parsed

    scenes_by_id = {scene.id: scene for scene in state.scenes}
    for day in sorted(state.schedule.days, key=lambda d: d.date):
        if day.date not in dates:
            continue
        if entity.kind == "location" and day.location_id == entity.id:
            return day.date
        if entity.kind == "person" and any(
            entity.id in scenes_by_id[sid].cast_ids for sid in day.scene_ids if sid in scenes_by_id
        ):
            return day.date
    return None


def build_event(extracted: Extracted, state: ProductionState) -> DisruptionEvent:
    """Assemble the event PRI will actually process.

    Every field is either copied from the resolved entity, derived from the
    board, or generated here. Nothing is carried over from the model's prose:
    the free text survives only as ``reason``, which no rule reads.

    Inputs:
        extracted: A resolution that already passed :func:`resolve`.
        state:     The production, for the board's own timezone.

    Outputs:
        A :class:`DisruptionEvent` shaped exactly like one from any other
        source, so the consumer cannot tell where it came from.

    Failure modes:
        Raises ``pydantic.ValidationError`` if the assembled event is not a
        valid event, which would be a bug here rather than bad input.
    """
    # The board's own timezone, taken from a real call time rather than assumed,
    # so the window lines up with the days the validator compares against.
    reference = next(
        (day.call_time for day in state.schedule.days if day.date == extracted.day), None
    )
    tzinfo = reference.tzinfo if reference is not None else None
    start = datetime.combine(extracted.day, time(0, 0), tzinfo=tzinfo)
    end = start + timedelta(days=1)

    payload: dict[str, Any] = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "reason": extracted.raw_text[:280],
    }
    if extracted.event_type == "location.blocked":
        payload["location_id"] = extracted.entity.id
    else:
        payload["person_id"] = extracted.entity.id

    return DisruptionEvent(
        event_id=f"inject-{uuid.uuid4().hex[:12]}",
        production_id=state.production.id,
        event_type=extracted.event_type,
        occurred_at=datetime.now(tz=tzinfo),
        # Traceable in the audit log as having come from typed input.
        source="live_injection",
        severity=0.8,
        payload=payload,
    )


async def extract_disruption(
    text: str,
    state: ProductionState,
    settings: Settings,
) -> Extracted | Clarification:
    """One Gemini call, then the closed-set check that does not trust it.

    Inputs:
        text:     What the judge typed.
        state:    The production to resolve against.
        settings: For credentials and the model name.

    Outputs:
        An :class:`Extracted`, or a :class:`Clarification` if the report could
        not be tied to something already in the production.

    Failure modes:
        Raises :class:`ExtractionError` if the model cannot be reached or
        returns nothing parseable. The caller turns that into a 503 — unlike a
        clarification, it is not a statement about the input.
    """
    stripped = text.strip()
    if not stripped:
        return Clarification(reason="Type what happened, in a sentence.")

    entities, dates = closed_sets(state)
    if not entities:
        return Clarification(reason="This production has no locations or cast to disrupt.")

    from google import genai

    from pri.agent.service import _configure_genai_env

    _configure_genai_env(settings)
    client = genai.Client()
    try:
        response = await client.aio.models.generate_content(
            model=settings.google_genai_model,
            contents=_prompt(stripped, entities, dates),
        )
    except Exception as exc:
        raise ExtractionError(str(exc)) from exc

    raw = getattr(response, "text", None)
    if not raw:
        raise ExtractionError("the model returned an empty response")

    resolved = resolve(_parse(raw), stripped, state)
    _log.info(
        "injection_extracted",
        outcome=type(resolved).__name__,
        event_type=getattr(resolved, "event_type", None),
        entity=getattr(getattr(resolved, "entity", None), "id", None),
    )
    return resolved
