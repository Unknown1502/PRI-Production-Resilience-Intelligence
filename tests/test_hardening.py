"""Deployment hardening: the failures that only appear in front of a stranger.

Every test here corresponds to something that works locally and breaks on a
public URL — a broker that stops answering, a judge with no API key, a travel
day a generator mistook for a free day, a call sheet with nothing on it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from confluent_kafka import KafkaException

from pri.api.kafka_bridge import KafkaBridge
from pri.config import Settings
from pri.domain.models import (
    DisruptionEvent,
    Location,
    Person,
    Production,
    ProductionState,
    Scene,
    Schedule,
    ShootingDay,
)
from pri.engine.constraints.validator import validate
from pri.engine.graph.dependency import impact_of
from pri.engine.simulation.candidates import generate_candidates
from pri.events.producer import ProductionEventProducer, PublishResult
from pri.events.schemas import EventEnvelope

_DSN = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"


def settings_with(**env: str) -> Settings:
    values: dict[str, Any] = {"DATABASE_URL": _DSN, **env}
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def kafka_settings(**env: str) -> Settings:
    return settings_with(
        CONFLUENT_BOOTSTRAP_SERVERS="broker:9092",
        CONFLUENT_API_KEY="key",
        CONFLUENT_API_SECRET="secret",
        **env,
    )


class FakeKafka:
    """A librdkafka double that can be told to fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.produced: list[tuple[str, bytes]] = []
        self.polls: list[float] = []
        self.attempts = 0

    def produce(self, topic: str, key: bytes, value: bytes, on_delivery: Any) -> None:
        del key
        self.attempts += 1
        if self.fail:
            raise KafkaException("broker is not answering")
        self.produced.append((topic, value))
        on_delivery(None, _FakeMessage(topic))

    def poll(self, _timeout: float) -> int:
        self.polls.append(_timeout)
        return 0

    def flush(self, _timeout: float = 10.0) -> int:
        return 0

    def list_topics(self, timeout: float = 5.0) -> Any:
        del timeout
        if self.fail:
            raise KafkaException("broker is not answering")
        return object()


class _FakeMessage:
    def __init__(self, topic: str) -> None:
        self._topic = topic

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return 1


# ---------------------------------------------------------------------------
# B2 — the producer cannot fail a request
# ---------------------------------------------------------------------------


class TestProducerDegradesInsteadOfRaising:
    def test_a_broker_error_is_returned_not_raised(self) -> None:
        producer = ProductionEventProducer(kafka_settings(), producer=FakeKafka(fail=True))
        result = producer.publish("t", "k", b"{}")
        assert isinstance(result, PublishResult)
        assert result.ok is False
        assert result.error and "not answering" in result.error

    def test_a_success_reports_the_topic_and_a_latency(self) -> None:
        producer = ProductionEventProducer(kafka_settings(), producer=FakeKafka())
        result = producer.publish("t", "k", b"{}")
        assert result.ok is True
        assert result.topic == "t"
        assert result.latency_ms >= 0

    def test_the_circuit_opens_after_three_failures(self) -> None:
        """The fourth call must not touch the network at all.

        Without this, every request pays the full delivery timeout once the
        broker is gone, and an API that answers in eight seconds reads as hung.
        """
        fake = FakeKafka(fail=True)
        producer = ProductionEventProducer(kafka_settings(), producer=fake)

        for _ in range(3):
            assert producer.publish("t", "k", b"{}").ok is False
        assert fake.attempts == 3, "the first three should each have tried"

        fourth = producer.publish("t", "k", b"{}")
        assert fourth.ok is False
        assert fake.attempts == 3, "the fourth must short-circuit"
        assert fourth.error and "circuit open" in fourth.error
        assert producer.degraded is True

    def test_the_circuit_closes_again_after_the_cooldown(self) -> None:
        fake = FakeKafka(fail=True)
        producer = ProductionEventProducer(
            kafka_settings(KAFKA_BREAKER_COOLDOWN_SECONDS="0"), producer=fake
        )
        for _ in range(3):
            producer.publish("t", "k", b"{}")

        fake.fail = False
        assert producer.publish("t", "k", b"{}").ok is True
        assert producer.degraded is False

    def test_a_flush_failure_is_survivable(self) -> None:
        class Exploding(FakeKafka):
            def flush(self, _timeout: float = 10.0) -> int:
                raise KafkaException("gone")

        producer = ProductionEventProducer(kafka_settings(), producer=Exploding())
        assert producer.flush() == -1

    def test_the_client_timeouts_are_bounded(self) -> None:
        from pri.events.config import producer_config

        config = producer_config(kafka_settings())
        assert config["socket.timeout.ms"] == 5000
        assert config["message.timeout.ms"] == 8000
        assert config["request.timeout.ms"] == 5000
        assert config["delivery.timeout.ms"] == 8000


# ---------------------------------------------------------------------------
# B1 / B4 — the bridge, and what /health says about it
# ---------------------------------------------------------------------------


class TestKafkaBridge:
    def _event(self) -> DisruptionEvent:
        return DisruptionEvent(
            event_id="evt-1",
            production_id="film-001",
            event_type="location.blocked",
            occurred_at=datetime.now(UTC),
            source="test",
            severity=0.8,
            payload={},
        )

    def test_no_credentials_means_disabled_not_degraded(self) -> None:
        """A developer with no Confluent account has not broken anything."""
        bridge = KafkaBridge(settings_with())
        assert bridge.enabled is False
        assert bridge.status() == "disabled"

    def test_the_templates_placeholder_key_counts_as_no_credentials(self) -> None:
        """`.env.example` ships REPLACE_WITH_API_KEY, a non-empty string.

        A naive truthiness check called that configured, so an untouched `.env`
        built a plaintext client against a TLS endpoint and reported *degraded*
        — which reads as "your broker is down" rather than "you have not set
        this up". The client builder already skipped the placeholder; the
        health story had not caught up.
        """
        settings = settings_with(
            CONFLUENT_BOOTSTRAP_SERVERS="pkc-xxxxx.confluent.cloud:9092",
            CONFLUENT_API_KEY="REPLACE_WITH_API_KEY",
            CONFLUENT_API_SECRET="REPLACE_WITH_API_SECRET",
        )
        assert settings.confluent_configured is False
        assert KafkaBridge(settings).status() == "disabled"

    async def test_publishing_does_not_block_the_caller(self) -> None:
        fake = FakeKafka()
        bridge = KafkaBridge(kafka_settings(), producer=fake)

        bridge.publish_disruption(self._event())
        assert fake.produced == [], "publish must be scheduled, not awaited"

        await asyncio.sleep(0)
        await asyncio.gather(*list(bridge._tasks))
        assert len(fake.produced) == 1

    async def test_the_delivery_callback_is_drained_after_publishing(self) -> None:
        """`produce` only queues; the acknowledgement waits for a poll.

        Without a blocking poll after the publish, `delivered` and
        `last_delivery` stay empty for messages the broker accepted — and a
        health page that cannot distinguish a working integration from a silent
        one is the exact failure this whole area is about. Found by
        `infra/verify.sh` against a live cluster, not by a unit test.
        """
        fake = FakeKafka()
        bridge = KafkaBridge(kafka_settings(), producer=fake)

        bridge.publish_disruption(self._event())
        await asyncio.sleep(0)
        await asyncio.gather(*list(bridge._tasks))

        blocking = [t for t in fake.polls if t > 0]
        assert blocking, f"no blocking poll after publish; polls were {fake.polls}"

    async def test_a_dead_broker_does_not_raise_into_the_caller(self) -> None:
        bridge = KafkaBridge(kafka_settings(), producer=FakeKafka(fail=True))
        bridge.publish_disruption(self._event())
        await asyncio.sleep(0)
        await asyncio.gather(*list(bridge._tasks), return_exceptions=False)

    async def test_an_unreachable_broker_reports_degraded(self) -> None:
        bridge = KafkaBridge(kafka_settings(), producer=FakeKafka(fail=True))
        assert await bridge.probe_once() is False
        assert bridge.status() == "degraded"
        assert bridge.health()["reachable"] is False

    async def test_a_reachable_broker_reports_ok(self) -> None:
        bridge = KafkaBridge(kafka_settings(), producer=FakeKafka())
        assert await bridge.probe_once() is True
        assert bridge.status() == "ok"

    def test_health_carries_the_broker_acknowledgement(self) -> None:
        """Absence of errors is not evidence; a partition and offset are.

        A publish to a topic that does not exist returns normally and is
        rejected later in the delivery callback, which logs at WARNING. The
        only proof a message landed is the acknowledgement.
        """
        producer = ProductionEventProducer(kafka_settings(), producer=FakeKafka())
        assert producer.health()["last_delivery"] is None

        producer.publish("production.events", "k", b"{}")
        ack = producer.health()["last_delivery"]
        assert ack is not None
        assert ack["topic"] == "production.events"
        assert ack["partition"] == 0
        assert ack["offset"] == 1

    def test_a_refused_delivery_leaves_no_acknowledgement(self) -> None:
        producer = ProductionEventProducer(kafka_settings(), producer=FakeKafka(fail=True))
        producer.publish("production.events", "k", b"{}")
        assert producer.health()["last_delivery"] is None
        assert producer.health()["last_error"]

    def test_the_bridge_reports_the_resolved_topics(self) -> None:
        bridge = KafkaBridge(kafka_settings(), producer=FakeKafka())
        # Read from settings rather than restated: a literal here is the same
        # mistake the topic mismatch was.
        from pri.events.config import topics_for

        assert bridge.health()["topics"] == topics_for(kafka_settings()).all


# ---------------------------------------------------------------------------
# A — configuration reconciliation
# ---------------------------------------------------------------------------


class TestConsumerDoesNotStarveItsHealthServer:
    """The Kafka poll must not block the event loop.

    `Consumer.poll` is a synchronous librdkafka call. Running it directly on
    the loop starves the health server the runner starts beside it: the socket
    is bound, so a TCP connect succeeds and a port check passes, but nothing
    ever answers the HTTP request. Cloud Run probes with a GET, so the revision
    hangs and is marked failed while the consumer is working perfectly.

    Found by curling the container. A unit test could not see it because the
    fakes return instantly.
    """

    async def test_the_loop_yields_while_polling(self) -> None:
        import asyncio as _asyncio

        from pri.events.consumer import RecoveryConsumer

        ticks = 0

        async def heartbeat() -> None:
            """Stands in for the health server: another task on the same loop."""
            nonlocal ticks
            while True:
                ticks += 1
                await _asyncio.sleep(0.01)

        class BlockingConsumer:
            """Blocks the calling thread the way librdkafka does."""

            def __init__(self) -> None:
                self.polls = 0

            def poll(self, timeout: float) -> None:
                self.polls += 1
                time.sleep(timeout)  # a real, thread-blocking wait
                return None

            def subscribe(self, topics: list[str]) -> None: ...
            def commit(self, **_kwargs: Any) -> None: ...
            def close(self) -> None: ...

        fake = BlockingConsumer()
        consumer = RecoveryConsumer(
            kafka_settings(),
            repo=None,  # type: ignore[arg-type]
            producer=None,  # type: ignore[arg-type]
            invoke=None,  # type: ignore[arg-type]
            consumer=fake,
        )

        beat = _asyncio.create_task(heartbeat())
        runner = _asyncio.create_task(consumer.run(poll_timeout=0.05))
        await _asyncio.sleep(0.3)

        # Cancelled, not stopped. `stop()` only takes effect at the top of the
        # loop, so a loop blocked inside poll() would never see it and this
        # test would hang instead of failing — a hanging test reports nothing.
        consumer.stop()
        runner.cancel()
        beat.cancel()
        for task in (runner, beat):
            with contextlib.suppress(BaseException):
                await task

        assert fake.polls > 1, "the loop did not poll"
        assert ticks > 5, (
            f"the event loop was starved: only {ticks} heartbeats while polling. "
            "poll() is blocking the loop instead of running in a thread."
        )


class TestConfiguration:
    def test_the_sdk_location_name_is_what_is_read(self) -> None:
        assert settings_with(GOOGLE_CLOUD_LOCATION="europe-west4").google_cloud_location == (
            "europe-west4"
        )

    def test_the_old_region_name_no_longer_silently_applies(self) -> None:
        """A rename that leaves a reader behind is worse than no rename.

        GOOGLE_CLOUD_REGION is gone. If it were still half-wired, this would
        come back as europe-west4 and the deployment would quietly call Vertex
        in a region the operator did not choose.
        """
        settings = settings_with(GOOGLE_CLOUD_REGION="europe-west4")
        assert settings.google_cloud_location == "us-central1"

    def test_gemini_is_configured_when_vertex_has_a_project(self) -> None:
        assert settings_with(GOOGLE_CLOUD_PROJECT="p").gemini_configured is True

    def test_gemini_is_unconfigured_without_a_project(self) -> None:
        # Explicitly empty rather than merely absent: the ADK projects its
        # settings onto os.environ before every call, so a test that ran the
        # agent earlier leaves GOOGLE_CLOUD_PROJECT behind in this process.
        assert settings_with(GOOGLE_CLOUD_PROJECT="").gemini_configured is False

    def test_credentials_are_never_read_from_the_environment(self) -> None:
        """The field is gone. A stale path must not become a crash."""
        assert not hasattr(settings_with(), "google_application_credentials")

    def test_a_stale_credentials_variable_is_reported(self, monkeypatch: Any) -> None:
        from pri.config import warn_on_stale_credentials

        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/nope/key.json")
        assert warn_on_stale_credentials() == "/nope/key.json"

    def test_topics_are_resolved_from_settings_not_hardcoded(self) -> None:
        from pri.events.config import topics_for

        topics = topics_for(settings_with(CONFLUENT_TOPIC_PRODUCTION_EVENTS="custom.events"))
        assert topics.events == "custom.events"
        assert "custom.events" in topics.all

    def test_the_schema_registry_settings_are_gone(self) -> None:
        """They were credentials for a service PRI does not use."""
        assert not hasattr(settings_with(), "confluent_schema_registry_url")


# ---------------------------------------------------------------------------
# F1 — travel days
# ---------------------------------------------------------------------------

_ZONE = ZoneInfo("Europe/London")


def _travel_day_production() -> ProductionState:
    """Three days: shoot, travel, shoot — plus one reserve day at the end.

    The travel day sits exactly where a deferral would want to put work.
    """

    def at(day: date, hour: int) -> datetime:
        return datetime.combine(day, datetime.min.time(), tzinfo=_ZONE).replace(hour=hour)

    d1, d2, d3, d4 = (date(2027, 6, 1 + offset) for offset in range(4))

    scenes = tuple(
        Scene(
            id=f"S{i}",
            number=str(i),
            slug=f"SCENE {i}",
            description="",
            int_ext="INT",
            time_of_day="DAY",
            estimated_minutes=60,
            location_id="L1",
            cast_ids=("P1",),
            equipment_ids=(),
            vfx_plate=False,
            prerequisite_scene_ids=(),
        )
        for i in (1, 2)
    )

    return ProductionState(
        production=Production(
            id="film-travel",
            title="Travel Day Test",
            currency="GBP",
            shoot_start=d1,
            shoot_end=d4,
            reserve_days=(d4,),
        ),
        scenes=scenes,
        people=(
            Person(
                id="P1",
                name="Lead",
                role="CAST",
                character="Lead",
                daily_rate=Decimal("100"),
                unavailable_windows=(),
            ),
        ),
        locations=(
            Location(
                id="L1",
                name="Stage",
                kind="studio",
                day_rate=Decimal("100"),
                permit_windows=(),
                supports_int_ext=("INT", "EXT"),
                supports_time_of_day=("DAY", "NIGHT", "DAWN", "DUSK"),
            ),
        ),
        equipment=(),
        schedule=Schedule(
            days=(
                ShootingDay(
                    date=d1,
                    call_time=at(d1, 8),
                    wrap_time=at(d1, 18),
                    location_id="L1",
                    scene_ids=("S1",),
                    day_kind="SHOOT",
                ),
                # Fourteen hours on a coach. Empty, and emphatically not free.
                ShootingDay(
                    date=d2,
                    call_time=at(d2, 6),
                    wrap_time=at(d2, 20),
                    location_id="L1",
                    scene_ids=(),
                    day_kind="TRAVEL",
                ),
                ShootingDay(
                    date=d3,
                    call_time=at(d3, 8),
                    wrap_time=at(d3, 18),
                    location_id="L1",
                    scene_ids=("S2",),
                    day_kind="SHOOT",
                ),
                ShootingDay(
                    date=d4,
                    call_time=at(d4, 8),
                    wrap_time=at(d4, 18),
                    location_id="L1",
                    scene_ids=(),
                    day_kind="RESERVE",
                ),
            )
        ),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=datetime(2027, 1, 1, tzinfo=UTC),
    )


class TestTravelDays:
    def test_no_candidate_schedules_a_scene_on_a_travel_day(self) -> None:
        """The hole that making scene_ids optional opened."""
        state = _travel_day_production()
        travel = date(2027, 6, 2)

        event = DisruptionEvent(
            event_id="evt-travel",
            production_id=state.production.id,
            event_type="location.blocked",
            occurred_at=datetime(2027, 6, 1, 6, tzinfo=UTC),
            source="test",
            severity=0.9,
            payload={"location_id": "L1", "date": "2027-06-01"},
        )
        impact = impact_of(state, event)
        plans = list(generate_candidates(state, impact))
        assert plans, "the scenario must produce at least one candidate to be meaningful"

        for plan in plans:
            for move in plan.moves:
                target = getattr(move, "to_date", None) or getattr(move, "date_b", None)
                assert target != travel, (
                    f"{plan.label} would put work on the travel day via {type(move).__name__}"
                )

    def test_a_travel_day_is_not_available_for_scenes(self) -> None:
        state = _travel_day_production()
        travel = next(d for d in state.schedule.days if d.day_kind == "TRAVEL")
        assert travel.is_available_for_scenes is False
        assert travel.is_shoot is False

    def test_c002_ignores_a_long_travel_day(self) -> None:
        """Fourteen hours on a coach is not a shooting-hours breach."""
        state = _travel_day_production()
        assert not [v for v in validate(state) if v.code == "C002"]

    def test_a_reserve_day_is_still_available(self) -> None:
        state = _travel_day_production()
        reserve = next(d for d in state.schedule.days if d.day_kind == "RESERVE")
        assert reserve.is_available_for_scenes is True


# ---------------------------------------------------------------------------
# F2 — call sheets on imported productions
# ---------------------------------------------------------------------------


class TestImportedCallSheets:
    def test_the_locations_sheet_supplies_the_address(self) -> None:
        """logistics.yaml cannot know a stranger's locations; the board can."""
        from pri.artifacts.call_sheet import generate_call_sheet
        from pri.importer.samples import build_second_unit_state

        state = build_second_unit_state()
        location = next(loc for loc in state.locations if loc.id == "HL-DOCK")
        assert location.address, "the sample must carry an address to prove anything"
        assert location.nearest_hospital

        day = next(d for d in state.schedule.days if d.location_id == "HL-DOCK")
        pdf = generate_call_sheet(state, day.date)
        assert pdf.startswith(b"%PDF")

    def test_the_three_columns_round_trip(self) -> None:
        from pri.importer import export_state, parse_workbook
        from pri.importer.samples import build_second_unit_state

        original = build_second_unit_state()
        result = parse_workbook(export_state(original), "hl.xlsx")
        assert result.errors == ()
        assert result.state is not None

        before = next(loc for loc in original.locations if loc.id == "HL-PUB")
        after = next(loc for loc in result.state.locations if loc.id == "HL-PUB")
        assert after.address == before.address
        assert after.parking_note == before.parking_note
        assert after.nearest_hospital == before.nearest_hospital

    def test_a_location_with_no_address_still_renders(self) -> None:
        """The em-dash fallback has to survive; it is the honest answer."""
        from pri.artifacts.call_sheet import generate_call_sheet

        state = _travel_day_production()
        day = state.schedule.days[0]
        assert generate_call_sheet(state, day.date).startswith(b"%PDF")


# ---------------------------------------------------------------------------
# A3 — the Cloud SQL DSN builds an engine
# ---------------------------------------------------------------------------


class TestUnixSocketEngine:
    def test_an_engine_is_constructible_from_the_socket_dsn(self) -> None:
        """Constructing, not connecting — there is no Cloud SQL socket here."""
        from pri.persistence.database import build_engine

        settings = settings_with(
            DATABASE_URL="postgresql+asyncpg://pri_user@/pri?host=/cloudsql/p:r:i",
            DATABASE_PASSWORD="hunter2",
        )
        engine = build_engine(settings.database_dsn)
        assert engine.url.database == "pri"
        assert dict(engine.url.query)["host"] == "/cloudsql/p:r:i"


# ---------------------------------------------------------------------------
# Schema/round-trip guards for the two new columns
# ---------------------------------------------------------------------------


class TestSpecAdditions:
    def test_day_kind_is_optional_so_old_boards_still_import(self) -> None:
        from pri.importer.spec import SHEET_SCHEDULE, sheet_by_name

        column = sheet_by_name(SHEET_SCHEDULE).column("day_kind")
        assert column is not None
        assert column.required is False

    def test_the_template_version_did_not_move(self) -> None:
        """Both new columns are optional, so a v1 file is still a v1 file."""
        from pri.importer.spec import TEMPLATE_VERSION

        assert TEMPLATE_VERSION == "1"

    @pytest.mark.parametrize("name", ["address", "parking_note", "nearest_hospital"])
    def test_the_call_sheet_columns_are_optional(self, name: str) -> None:
        from pri.importer.spec import SHEET_LOCATIONS, sheet_by_name

        column = sheet_by_name(SHEET_LOCATIONS).column(name)
        assert column is not None
        assert column.required is False


# ---------------------------------------------------------------------------
# Envelope helper used above
# ---------------------------------------------------------------------------


def test_the_envelope_still_serialises() -> None:
    """Guards the bridge's assumption that `.to_bytes()` exists."""
    event = DisruptionEvent(
        event_id="e",
        production_id="p",
        event_type="location.blocked",
        occurred_at=datetime.now(UTC) - timedelta(minutes=1),
        source="t",
        severity=0.5,
        payload={},
    )
    assert EventEnvelope.of(event).to_bytes().startswith(b"{")
