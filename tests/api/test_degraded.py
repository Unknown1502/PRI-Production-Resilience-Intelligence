"""The API with the event fabric broken, and the reset button under abuse.

Acceptance 1 lives here: with the broker unreachable, the whole flow still
completes and ``/health`` says degraded with a 200. The point of Kafka being a
partner service is that it is genuinely called; the point of it being off the
critical path is that a judge never finds out when it is down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from confluent_kafka import KafkaException

from pri.api.kafka_bridge import KafkaBridge
from pri.config import get_settings
from tests.api.conftest import PRODUCTION, api_headers, loc04_event_body

if TYPE_CHECKING:
    from httpx import AsyncClient


class DeadBroker:
    """Every operation fails, the way an unreachable broker does."""

    def __init__(self) -> None:
        self.attempts = 0

    def produce(self, **_kwargs: Any) -> None:
        self.attempts += 1
        raise KafkaException("broker unreachable")

    def poll(self, _timeout: float) -> int:
        return 0

    def flush(self, _timeout: float = 10.0) -> int:
        return 0

    def list_topics(self, timeout: float = 5.0) -> Any:
        del timeout
        raise KafkaException("broker unreachable")


@pytest.fixture()
def dead_kafka(client: AsyncClient) -> DeadBroker:
    """Attach a broken broker to the running app."""
    broker = DeadBroker()
    settings = get_settings()
    app = client._transport.app  # type: ignore[attr-defined,union-attr]
    app.state.kafka = KafkaBridge(settings, producer=broker)
    return broker


class TestKafkaDown:
    async def test_health_reports_degraded_with_a_200(
        self, client: AsyncClient, dead_kafka: DeadBroker
    ) -> None:
        """A 503 here would make Cloud Run cycle a service that works."""
        app = client._transport.app  # type: ignore[attr-defined,union-attr]
        await app.state.kafka.probe_once()

        response = await client.get("/health")
        assert response.status_code == 200, "Kafka is not a reason to fail a probe"

        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["kafka"] == "degraded"
        assert body["checks"]["database"] == "ok"

    async def test_an_event_is_still_accepted(
        self, client: AsyncClient, dead_kafka: DeadBroker
    ) -> None:
        response = await client.post(
            f"/api/productions/{PRODUCTION}/events",
            json=loc04_event_body(),
            headers=api_headers(),
        )
        assert response.status_code == 200
        assert response.json()["duplicate"] is False

    async def test_recovery_still_returns_candidates_including_an_invalid_one(
        self, client: AsyncClient, dead_kafka: DeadBroker
    ) -> None:
        """Acceptance 1: the demo survives a dead event fabric."""
        body = loc04_event_body()
        await client.post(f"/api/productions/{PRODUCTION}/events", json=body, headers=api_headers())

        response = await client.post(
            f"/api/productions/{PRODUCTION}/recover",
            json={"event_id": body["event_id"]},
            headers=api_headers(),
        )
        assert response.status_code == 200

        candidates = response.json()["candidates"]
        assert candidates, "no candidates with the broker down"
        assert any(not c["valid"] for c in candidates), (
            "the rejected plan is the evidence that validation is real"
        )
        assert any(c["valid"] for c in candidates)

    async def test_the_database_is_still_authoritative(
        self, client: AsyncClient, dead_kafka: DeadBroker, repo: Any
    ) -> None:
        """The event must be recorded even though the announcement failed."""
        body = loc04_event_body()
        await client.post(f"/api/productions/{PRODUCTION}/events", json=body, headers=api_headers())
        assert await repo.get_event(body["event_id"]) is not None


class TestHealthShape:
    async def test_the_checks_block_is_present_and_typed(self, client: AsyncClient) -> None:
        body = (await client.get("/health")).json()
        assert set(body["checks"]) == {"database", "kafka", "gemini"}
        assert body["checks"]["kafka"] in {"ok", "degraded", "disabled"}
        assert body["checks"]["gemini"] in {"configured", "unconfigured"}

    async def test_health_does_not_block_on_the_broker(self, client: AsyncClient) -> None:
        """It reads the last probe result, so it cannot be slow.

        A handler that opened a connection would take the socket timeout here,
        which is what fails a Cloud Run probe and turns degraded into down.
        """
        import time

        started = time.perf_counter()
        await client.get("/health")
        assert (time.perf_counter() - started) < 2.0


class TestResetFromAnywhere:
    """D2: reset is safe from any point in the flow."""

    async def _v1(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo/reset", headers=api_headers())
        assert response.status_code == 200
        assert response.json()["version"] == 1

        schedule = await client.get(f"/api/productions/{PRODUCTION}/schedule")
        assert schedule.json()["version"] == 1

    async def test_from_a_clean_start(self, client: AsyncClient) -> None:
        await self._v1(client)

    async def test_after_an_event_has_been_ingested(self, client: AsyncClient) -> None:
        await client.post(
            f"/api/productions/{PRODUCTION}/events",
            json=loc04_event_body(),
            headers=api_headers(),
        )
        await self._v1(client)

    async def test_mid_recovery(self, client: AsyncClient) -> None:
        body = loc04_event_body()
        await client.post(f"/api/productions/{PRODUCTION}/events", json=body, headers=api_headers())
        await client.post(
            f"/api/productions/{PRODUCTION}/recover",
            json={"event_id": body["event_id"]},
            headers=api_headers(),
        )
        await self._v1(client)

    async def test_after_a_staged_import(self, client: AsyncClient) -> None:
        """A half-reviewed upload must not survive the reset."""
        from pri.importer import export_state
        from pri.importer.samples import build_second_unit_state

        upload = await client.post(
            "/api/import",
            files={
                "file": (
                    "hl.xlsx",
                    export_state(build_second_unit_state()),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=api_headers(),
        )
        staging_id = upload.json()["staging_id"]

        await self._v1(client)
        assert (await client.get(f"/api/import/{staging_id}")).status_code == 404


class TestSampleReset:
    """D3: a judge can explore a production PRI was not built around."""

    async def test_the_sample_seeds_and_is_readable(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/demo/reset", params={"sample": "harbour_lights"}, headers=api_headers()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["production_id"] == "film-hl-002"
        assert body["version"] == 1

        schedule = await client.get("/api/productions/film-hl-002/schedule")
        assert schedule.status_code == 200
        assert schedule.json()["title"] == "Harbour Lights"

    async def test_the_sample_renders_a_call_sheet_with_a_real_address(
        self, client: AsyncClient
    ) -> None:
        """Acceptance 5, over HTTP."""
        await client.post(
            "/api/demo/reset", params={"sample": "harbour_lights"}, headers=api_headers()
        )
        schedule = (await client.get("/api/productions/film-hl-002/schedule")).json()
        day = next(d for d in schedule["days"] if d["scene_ids"])

        response = await client.get(f"/api/productions/film-hl-002/call-sheet/{day['date']}")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")
        assert len(response.content) > 2000

    async def test_an_unknown_sample_is_a_404(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/demo/reset", params={"sample": "nope"}, headers=api_headers()
        )
        assert response.status_code == 404
