"""Turn a disruption's assertion into a fact the validator can see.

A disruption event says something about the world: this actor cannot work on
these dates. Until that claim is written into the state the planner validates
against, the constraint rules have nothing to check it with, and the planner
will happily produce a plan that ignores it.

That was not theoretical. For an `actor.unavailable` event against the Night
Train fixture, the RELOCATE family moved the blocked scenes to a different
location and left them on the day the actor was away — and the plan validated,
because `Person.unavailable_windows` was empty in state, so C003 had nothing to
compare against. The SWAP family did worse: it moved *other* scenes needing the
same actor onto the blocked day, and the only complaint was about crew
turnaround. A producer would have been shown both as options.

`location.blocked` never had this problem, which is why it looked hardened and
the other did not. Its remedy — put the scene somewhere else — genuinely
resolves a blocked location, so a plausible plan is usually a correct one. An
absent actor is not fixed by changing the address.

So the fix is not a new rule and not a new candidate family. It is making the
event's claim part of the state the existing rules already know how to read:

    actor.unavailable  ->  add the window to that person's unavailable_windows
                           ->  C003 (cast_availability) now fires

Everything downstream is unchanged. The planner still cannot author a fact:
the window comes from the event, the event came through the same ingest and
validation path as any other, and the projection only ever *adds* a
restriction — it can never make an invalid plan look valid.

Scope note: only `actor.unavailable` is projected here. `location.blocked` is
already handled correctly by the families themselves, and permit windows model
allowed time rather than forbidden time, so expressing a blockage as a permit
is not a change this module should make quietly. The other three event types
remain unhardened and are documented as such.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pri.domain.models import TimeWindow
from pri.engine.graph.dependency import extract_window

if TYPE_CHECKING:
    from pri.domain.models import DisruptionEvent, ProductionState

__all__ = ["project_disruption"]


def project_disruption(state: ProductionState, event: DisruptionEvent) -> ProductionState:
    """Return the state a plan for this disruption must be validated against.

    Inputs:
        state: The committed state the recovery is based on.
        event: The disruption being recovered from.

    Outputs:
        A state carrying the event's assertion as a modelled constraint, or
        ``state`` unchanged when the event type has no projection. The result
        is used for generation, validation and repair; it is never committed.

    Failure modes:
        Does not raise. An event naming a person who is not in the production,
        or carrying an unparseable window, projects nothing — the recovery then
        proceeds exactly as it did before, which is the previous behaviour
        rather than a new failure. An unparseable window would already have
        failed in `impact_of`, which runs first.
    """
    if event.event_type != "actor.unavailable":
        return state

    person_id = str(event.payload.get("person_id", ""))
    if not person_id:
        return state

    try:
        start, end = extract_window(dict(event.payload), event.occurred_at)
    except ValueError:
        return state
    if start >= end:
        return state

    window = TimeWindow(start=start, end=end)
    people = []
    found = False
    for person in state.people:
        if person.id != person_id:
            people.append(person)
            continue
        found = True
        if any(w.start == window.start and w.end == window.end for w in person.unavailable_windows):
            # Already asserted — projecting twice would double the window and
            # make the same disruption look like two.
            people.append(person)
            continue
        people.append(
            person.model_copy(update={"unavailable_windows": (*person.unavailable_windows, window)})
        )

    if not found:
        return state
    return state.model_copy(update={"people": tuple(people)})
