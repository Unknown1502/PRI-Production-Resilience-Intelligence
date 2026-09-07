"""Assertions about a *deployed* PRI, run against a live URL.

Skipped unless ``PRI_DEPLOYED_URL`` is set, so a normal ``pytest`` run and CI
are unaffected::

    PRI_DEPLOYED_URL=https://pri-api-xxxxx.run.app \\
    PRI_API_KEY=... \\
    pytest tests/integration -v

These exist because the rest of the suite is deliberately hermetic. `conftest`
pins ``PRI_AGENT_ENABLED=false`` for every other test — otherwise the HTTP
tests call a live model, which costs six minutes and a billed request per test
and fails on an aeroplane.

That pinning creates one specific hole, and this file is the patch for it:
**nothing else asserts that the deployed configuration actually calls Gemini.**
A change that flips ``PRI_AGENT_ENABLED`` off, or a model id that is not served
in the deployed region, passes CI cleanly and silently drops the hosted demo
into deterministic mode — which is precisely the runtime requirement PRI has to
satisfy. It would go unnoticed until a judge opened the URL.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("PRI_DEPLOYED_URL"),
        reason="set PRI_DEPLOYED_URL to run against a deployment",
    ),
]

PRODUCTION = os.environ.get("PRI_DEMO_PRODUCTION_ID", "film-001")


def base_url() -> str:
    return os.environ["PRI_DEPLOYED_URL"].rstrip("/")


def headers() -> dict[str, str]:
    key = os.environ.get("PRI_API_KEY", "")
    return {"X-API-Key": key} if key else {}


@pytest.fixture()
async def client() -> Any:
    import httpx

    async with httpx.AsyncClient(base_url=base_url(), timeout=120.0) as http:
        yield http


async def _fresh_event(client: Any) -> str:
    """Post a disruption and return its id."""
    event_id = f"integration-{uuid.uuid4().hex[:10]}"
    response = await client.post(
        f"/api/productions/{PRODUCTION}/events",
        headers=headers(),
        json={
            "event_id": event_id,
            "event_type": "location.blocked",
            "occurred_at": datetime.now(UTC).isoformat(),
            "source": "integration-test",
            "severity": 0.8,
            "payload": {
                "location_id": "LOC-04",
                "window_start": "2026-09-10T00:00:00+05:30",
                "window_end": "2026-09-11T00:00:00+05:30",
                "reason": "integration test",
            },
        },
    )
    assert response.status_code == 200, response.text
    return event_id


class TestDeployedGemini:
    """The runtime requirement: the SDK is imported *and called* in production."""

    async def test_health_reports_gemini_configured(self, client: Any) -> None:
        body = (await client.get("/health")).json()
        assert body["checks"]["gemini"] == "configured", (
            "the deployment has no Vertex project or location — the agent "
            f"cannot run: {body['checks']}"
        )

    async def test_the_agent_is_enabled_on_the_deployment(self, client: Any) -> None:
        body = (await client.get("/health")).json()
        assert body["agent_enabled"] is True, (
            "PRI_AGENT_ENABLED is false on the deployment, so the hosted demo "
            "never calls Gemini. This is the runtime requirement."
        )

    async def test_recovery_runs_in_agent_mode(self, client: Any) -> None:
        """The assertion the hermetic suite cannot make.

        `mode` is `agent` only when Gemini actually answered. A model id that
        is not served in the deployed region falls back to deterministic
        without raising, so this is the check that catches it.
        """
        event_id = await _fresh_event(client)
        response = await client.post(
            f"/api/productions/{PRODUCTION}/recover",
            headers=headers(),
            json={"event_id": event_id},
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["mode"] == "agent", (
            "the deployment fell back to deterministic mode — either the agent "
            "is disabled, or the configured model is not served in this region"
        )
        assert body.get("tool_calls"), "agent mode with no tool calls is not agent mode"

    async def test_the_deterministic_result_is_unchanged_by_the_agent(self, client: Any) -> None:
        """Gemini selects strategies; it never computes a number.

        Same four candidates, same frontier, whichever mode produced them.
        """
        event_id = await _fresh_event(client)
        body = (
            await client.post(
                f"/api/productions/{PRODUCTION}/recover",
                headers=headers(),
                json={"event_id": event_id},
            )
        ).json()

        labels = [c["label"] for c in body["candidates"]]
        assert labels == ["A", "B", "C", "B2"], labels
        assert any(not c["valid"] for c in body["candidates"]), (
            "the rejected plan is the evidence that validation is real"
        )


class TestDeployedKafka:
    """Confluent is a partner service: it must be reached at runtime."""

    async def test_the_broker_acknowledged_a_real_message(self, client: Any) -> None:
        """Positive evidence — a partition and an offset, not a lack of errors.

        `produce` only queues; a publish to a topic that does not exist returns
        normally and is rejected later in the delivery callback. Only the
        acknowledgement proves the message landed.
        """
        import asyncio

        health = (await client.get("/health")).json()
        if health["checks"]["kafka"] == "disabled":
            pytest.skip("this deployment has no Confluent credentials")

        await _fresh_event(client)
        await asyncio.sleep(5)  # the publish is fire-and-forget

        kafka = (await client.get("/health")).json()["kafka"]
        assert kafka["last_delivery"] is not None, (
            f"nothing acknowledged; topics may not exist on the cluster. "
            f"delivered={kafka['delivered']} failed={kafka['failed']} "
            f"last_error={kafka['last_error']}"
        )
        assert kafka["last_delivery"]["offset"] >= 0
        assert kafka["last_delivery"]["topic"] in kafka["topics"]


class TestDeployedBasics:
    async def test_health_is_200_and_the_database_is_up(self, client: Any) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["checks"]["database"] == "ok"

    async def test_a_stranger_with_no_key_can_read(self, client: Any) -> None:
        """Read routes are open so the hosted demo works in a clean browser."""
        response = await client.get(f"/api/productions/{PRODUCTION}/schedule")
        assert response.status_code == 200
        assert response.json()["days"]
