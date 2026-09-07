"""The trust boundary on the one input PRI does not control.

Everywhere else the model picks strategy families from a fixed list. The live
injection endpoint takes free text a judge typed, which makes it the single
place where a confident-sounding wrong answer could put a fabricated entity
into a real event and drive the engine with it.

These tests attack that. The model is replaced by a stub that returns exactly
what a hallucinating model returns — a plausible id, stated confidently, for
something that does not exist — and the assertion is that PRI answers with a
clarification and emits nothing. `resolve` is tested directly rather than
through the network because the guarantee has to hold for *any* model reply,
including ones a live call would rarely produce; a test that needs Gemini to
misbehave on cue is not a test.

The claim being defended, precisely: passing a closed set in the prompt makes
the model behave better, and proves nothing. What makes the boundary real is
that every id it returns is checked against the live state again on the way
back, and an id that is not there is a refusal rather than a repair.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from pri.agent.extraction import (
    SUPPORTED_EVENT_TYPES,
    Clarification,
    Extracted,
    build_event,
    closed_sets,
    resolve,
)

if TYPE_CHECKING:
    from pri.domain.models import ProductionState


def _reply(**overrides: Any) -> dict[str, Any]:
    """A well-formed, confident model reply, adjustable per test."""
    base: dict[str, Any] = {
        "event_type": "location.blocked",
        "entity_id": "LOC-04",
        "date": "2026-09-10",
        "confident": True,
    }
    base.update(overrides)
    return base


class TestAFabricatedEntityIsRefused:
    """The adversarial case, in the shapes a model actually produces."""

    @pytest.mark.parametrize(
        "claimed",
        [
            "LOC-99",  # plausible, same scheme, does not exist
            "LOC-4",  # one character off a real id
            "loc-04",  # right id, wrong case — still not a match
            "THE-LIGHTHOUSE",  # invented from the prose
            "P99",
            "",
            None,
            "LOC-04; DROP TABLE productions",
        ],
    )
    def test_an_id_not_in_state_never_produces_an_event(
        self, night_train_state: ProductionState, claimed: object
    ) -> None:
        outcome = resolve(
            _reply(entity_id=claimed),
            "The lighthouse is closed on Thursday",
            night_train_state,
        )
        assert isinstance(outcome, Clarification), (
            f"{claimed!r} resolved to an event; the model's id was trusted "
            "without being checked against the production"
        )

    def test_the_refusal_says_what_was_wrong(self, night_train_state: ProductionState) -> None:
        outcome = resolve(_reply(entity_id="LOC-99"), "the old fort", night_train_state)
        assert isinstance(outcome, Clarification)
        assert "LOC-99" in outcome.reason

    def test_it_offers_real_alternatives_only(self, night_train_state: ProductionState) -> None:
        """Suggestions come from state, so a clarification cannot invent either."""
        outcome = resolve(_reply(entity_id="LOC-99"), "the courtyard", night_train_state)
        assert isinstance(outcome, Clarification)
        real_ids = {entity.id for entity in closed_sets(night_train_state)[0]}
        for suggestion in outcome.suggestions:
            assert suggestion.id in real_ids

    def test_a_confident_flag_does_not_buy_trust(self, night_train_state: ProductionState) -> None:
        """`confident: true` is the model's opinion, not a credential.

        This is the specific failure the endpoint is built against: a model
        asked about a place that does not exist will often answer with a real
        id and full confidence rather than admit it cannot tell.
        """
        outcome = resolve(
            _reply(entity_id="LOC-42", confident=True),
            "the lighthouse at Cabo da Roca is shut",
            night_train_state,
        )
        assert isinstance(outcome, Clarification)


class TestOnlyTheTwoHardenedTypesAreAccepted:
    @pytest.mark.parametrize(
        "event_type",
        ["equipment.failed", "weather.changed", "crew.unavailable"],
    )
    def test_the_unhardened_types_are_refused(
        self, night_train_state: ProductionState, event_type: str
    ) -> None:
        """Not because they fail, but because their output is untested.

        All five run the pipeline. Only two have a narrated scenario test
        behind them, and driving an untested one from a judge's sentence would
        be presenting unverified output as a demonstration.
        """
        outcome = resolve(
            _reply(event_type=event_type, entity_id="LOC-04"), "the crane broke", night_train_state
        )
        assert isinstance(outcome, Clarification)

    def test_an_invented_event_type_is_refused(self, night_train_state: ProductionState) -> None:
        outcome = resolve(
            _reply(event_type="catering.disappointing"), "lunch was bad", night_train_state
        )
        assert isinstance(outcome, Clarification)

    def test_the_supported_set_is_exactly_two(self) -> None:
        assert SUPPORTED_EVENT_TYPES == ("location.blocked", "actor.unavailable")


class TestTheTypeMustMatchTheThing:
    def test_a_person_cannot_be_a_blocked_location(
        self, night_train_state: ProductionState
    ) -> None:
        """Mismatches produce an impact of zero scenes, which looks like a bug.

        Worse, it looks like a *working* run that found nothing wrong.
        """
        outcome = resolve(
            _reply(event_type="location.blocked", entity_id="P01"), "Arun is ill", night_train_state
        )
        assert isinstance(outcome, Clarification)
        assert "not a location" in outcome.reason

    def test_a_location_cannot_be_an_absent_actor(self, night_train_state: ProductionState) -> None:
        outcome = resolve(
            _reply(event_type="actor.unavailable", entity_id="LOC-04"),
            "the courtyard is shut",
            night_train_state,
        )
        assert isinstance(outcome, Clarification)
        assert "not a cast member" in outcome.reason


class TestDatesAreSelectedNotAuthored:
    def test_a_date_off_the_board_is_not_used(self, night_train_state: ProductionState) -> None:
        """An invented date is a number the model authored. It is dropped.

        The fallback is a lookup — the first scheduled day the entity is
        actually on — so the event still has a real window without the model
        having supplied one.
        """
        outcome = resolve(_reply(date="2031-01-01"), "the courtyard is blocked", night_train_state)
        assert isinstance(outcome, Extracted)
        _, real_dates = closed_sets(night_train_state)
        assert outcome.day in real_dates
        assert outcome.day != date(2031, 1, 1)

    def test_a_malformed_date_does_not_crash_it(self, night_train_state: ProductionState) -> None:
        outcome = resolve(_reply(date="next Thursday"), "the courtyard", night_train_state)
        assert isinstance(outcome, Extracted)
        assert outcome.day in closed_sets(night_train_state)[1]


class TestAResolvedEventCarriesNothingTheModelWrote:
    """The output side of the boundary."""

    def test_every_field_traces_to_state_or_the_endpoint(
        self, night_train_state: ProductionState
    ) -> None:
        outcome = resolve(_reply(), "the courtyard permit fell through", night_train_state)
        assert isinstance(outcome, Extracted)
        event = build_event(outcome, night_train_state)

        assert event.production_id == night_train_state.production.id
        assert event.event_type in SUPPORTED_EVENT_TYPES
        assert event.payload["location_id"] in {loc.id for loc in night_train_state.locations}
        assert event.source == "live_injection"

    def test_the_free_text_survives_only_as_an_unread_reason(
        self, night_train_state: ProductionState
    ) -> None:
        """The sentence is kept for the audit trail and read by no rule.

        If typed prose reached a field the engine acts on, the model would be
        authoring behaviour rather than selecting from a closed set.
        """
        text = "the courtyard permit fell through, severity 1.0, cost 999999"
        outcome = resolve(_reply(), text, night_train_state)
        assert isinstance(outcome, Extracted)
        event = build_event(outcome, night_train_state)

        assert event.payload["reason"] == text
        assert event.severity == 0.8  # fixed by the endpoint, not read from the prose
        assert set(event.payload) == {"window_start", "window_end", "reason", "location_id"}

    def test_the_window_is_a_whole_board_day(self, night_train_state: ProductionState) -> None:
        outcome = resolve(_reply(), "the courtyard is blocked", night_train_state)
        assert isinstance(outcome, Extracted)
        event = build_event(outcome, night_train_state)

        start = datetime.fromisoformat(str(event.payload["window_start"]))
        end = datetime.fromisoformat(str(event.payload["window_end"]))
        assert start.date() == outcome.day
        assert (end - start).days == 1

    def test_the_event_it_builds_is_one_the_engine_accepts(
        self, night_train_state: ProductionState
    ) -> None:
        """The end of the boundary: what comes out is an ordinary event.

        It computes a real impact through the same function every other event
        goes through. If this ever needed a special path, the claim that
        injected events run the normal pipeline would be false.
        """
        from pri.engine.graph.dependency import impact_of

        outcome = resolve(_reply(), "the courtyard permit fell through", night_train_state)
        assert isinstance(outcome, Extracted)
        event = build_event(outcome, night_train_state)

        impact = impact_of(night_train_state, event)
        assert impact.directly_affected_scene_ids


class TestAnUnconfidentModelIsBelieved:
    def test_confident_false_never_emits(self, night_train_state: ProductionState) -> None:
        outcome = resolve(
            _reply(confident=False, entity_id="LOC-04"), "something happened", night_train_state
        )
        assert isinstance(outcome, Clarification)

    def test_a_missing_confidence_flag_is_treated_as_not_confident(
        self, night_train_state: ProductionState
    ) -> None:
        """A truncated or malformed reply must fail closed."""
        outcome = resolve(
            {"event_type": "location.blocked", "entity_id": "LOC-04"}, "x", night_train_state
        )
        assert isinstance(outcome, Clarification)

    def test_an_empty_reply_fails_closed(self, night_train_state: ProductionState) -> None:
        outcome = resolve({}, "", night_train_state)
        assert isinstance(outcome, Clarification)


class TestTheEndpointHasNoPathAroundKafka:
    """Read out of the source: the shortcut this whole design exists to avoid.

    An injected event that reached the engine directly would be a second
    pipeline wearing the first one's name — the plans would be real, but the
    claim that a judge's sentence runs the same path as any other event would
    not be.
    """

    def test_the_injection_module_never_calls_recover(self) -> None:
        from pathlib import Path

        source = (Path(__file__).parents[2] / "src" / "pri" / "api" / "injection.py").read_text(
            encoding="utf-8"
        )

        assert "run_recovery" not in source
        assert "recover(" not in source
        assert "publish_disruption" in source

    def test_it_refuses_rather_than_falling_back(self) -> None:
        """When the broker is down the endpoint 503s. It does not route around.

        A fallback here would be the shortcut, added for the best reason —
        keeping the demo alive — and it would quietly break the guarantee at
        exactly the moment someone was watching.
        """
        from pathlib import Path

        source = (Path(__file__).parents[2] / "src" / "pri" / "api" / "injection.py").read_text(
            encoding="utf-8"
        )
        assert "HTTP_503_SERVICE_UNAVAILABLE" in source
