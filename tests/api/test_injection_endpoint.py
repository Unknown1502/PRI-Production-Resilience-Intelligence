"""The guards on the live-injection endpoint.

`tests/agent/test_injection_compliance.py` proves the model cannot fabricate an
entity. This proves the endpoint around it is not a way into the system: it is
closed unless a passcode is configured, rate-limited before the model call, and
has no path to the engine that skips the topic.

The model is stubbed throughout. What is under test here is the plumbing, and a
test that needed a live Gemini call to check a 401 would be slow, billed and
occasionally wrong for reasons unrelated to the thing it asserts.
"""

from __future__ import annotations

import os
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from pri.agent.extraction import Clarification, EntityChoice, Extracted
from pri.api.main import create_app
from pri.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from pri.persistence.database import SessionFactory

PASSCODE = "test-passcode"


class _FakeKafka:
    """Stands in for the bridge, and records what it was asked to publish."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.published: list[Any] = []

    def publish_disruption(self, event: Any) -> None:
        self.published.append(event)


@pytest.fixture()
def _configured() -> Any:
    """Injection enabled, with a known passcode, for the duration of one test."""
    previous = {
        key: os.environ.get(key)
        for key in ("PRI_INJECT_PASSCODE", "PRI_INJECT_RATE_LIMIT_PER_MINUTE")
    }
    os.environ["PRI_INJECT_PASSCODE"] = PASSCODE
    os.environ["PRI_INJECT_RATE_LIMIT_PER_MINUTE"] = "3"
    get_settings.cache_clear()
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


@pytest_asyncio.fixture()
async def app_and_client(
    seeded_factory: SessionFactory,
) -> AsyncGenerator[tuple[Any, AsyncClient], None]:
    """Like the shared `client` fixture, but hands back the app too.

    The tests need to attach a fake broker to `app.state`, which the shared
    fixture does not expose.
    """
    app = create_app()
    app.state.session_factory = seeded_factory
    app.state.engine = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://pri.test") as http:
        yield app, http


@pytest.fixture(autouse=True)
def _clear_rate_limit() -> None:
    """Each test starts with an empty window."""
    from pri.api.injection import _RECENT

    _RECENT.clear()


def _stub(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    async def _fake(text: str, state: Any, settings: Any) -> Any:
        return outcome

    monkeypatch.setattr("pri.api.injection.extract_disruption", _fake)


@pytest.mark.asyncio
class TestTheEndpointIsClosedByDefault:
    async def test_no_passcode_configured_means_it_does_not_exist(
        self, app_and_client: tuple[Any, AsyncClient]
    ) -> None:
        """An unset secret must never mean "open".

        The deployment sets this from Secret Manager. If that ever fails to
        arrive, the endpoint has to be unreachable rather than unguarded.
        """
        get_settings.cache_clear()
        _, client = app_and_client
        response = await client.post("/api/disruptions/inject", json={"text": "anything"})
        assert response.status_code == 404

    async def test_a_wrong_passcode_is_rejected(
        self, _configured: Any, app_and_client: tuple[Any, AsyncClient]
    ) -> None:
        _, client = app_and_client
        response = await client.post(
            "/api/disruptions/inject",
            json={"text": "the courtyard is blocked"},
            headers={"X-Inject-Passcode": "wrong"},
        )
        assert response.status_code == 401

    async def test_a_missing_passcode_is_rejected(
        self, _configured: Any, app_and_client: tuple[Any, AsyncClient]
    ) -> None:
        _, client = app_and_client
        response = await client.post(
            "/api/disruptions/inject", json={"text": "the courtyard is blocked"}
        )
        assert response.status_code == 401


@pytest.mark.asyncio
class TestTheRateLimit:
    async def test_it_bites_before_the_model_is_called(
        self,
        _configured: Any,
        app_and_client: tuple[Any, AsyncClient],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ordering matters: a rejected caller must not cost a billed call."""
        calls = 0

        async def _counting(text: str, state: Any, settings: Any) -> Any:
            nonlocal calls
            calls += 1
            return Clarification(reason="no")

        monkeypatch.setattr("pri.api.injection.extract_disruption", _counting)
        app, client = app_and_client
        app.state.kafka = _FakeKafka()

        headers = {"X-Inject-Passcode": PASSCODE}
        statuses = [
            (
                await client.post(
                    "/api/disruptions/inject", json={"text": "something"}, headers=headers
                )
            ).status_code
            for _ in range(5)
        ]

        assert statuses[:3] == [200, 200, 200]
        assert statuses[3:] == [429, 429]
        assert calls == 3, "the model was called for a request the limit had already refused"


@pytest.mark.asyncio
class TestAClarificationEmitsNothing:
    async def test_it_returns_the_reason_and_publishes_nothing(
        self,
        _configured: Any,
        app_and_client: tuple[Any, AsyncClient],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub(
            monkeypatch,
            Clarification(
                reason="There is nothing called 'LOC-99' in this production.",
                suggestions=(EntityChoice(id="LOC-04", label="Courtyard", kind="location"),),
            ),
        )
        app, client = app_and_client
        kafka = _FakeKafka()
        app.state.kafka = kafka

        response = await client.post(
            "/api/disruptions/inject",
            json={"text": "the lighthouse is shut"},
            headers={"X-Inject-Passcode": PASSCODE},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "needs_clarification"
        assert body["event_id"] is None
        assert body["suggestions"][0]["id"] == "LOC-04"
        assert kafka.published == [], "a clarification published an event"


@pytest.mark.asyncio
class TestAResolvedDisruptionGoesToKafkaAndOnlyKafka:
    async def test_it_publishes_the_event(
        self,
        _configured: Any,
        app_and_client: tuple[Any, AsyncClient],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub(
            monkeypatch,
            Extracted(
                event_type="location.blocked",
                entity=EntityChoice(id="LOC-04", label="Courtyard", kind="location"),
                day=date(2026, 9, 10),
                raw_text="the courtyard permit fell through",
            ),
        )
        app, client = app_and_client
        kafka = _FakeKafka()
        app.state.kafka = kafka

        response = await client.post(
            "/api/disruptions/inject",
            json={"text": "the courtyard permit fell through"},
            headers={"X-Inject-Passcode": PASSCODE},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert body["entity_id"] == "LOC-04"
        assert len(kafka.published) == 1
        published = kafka.published[0]
        assert published.event_type == "location.blocked"
        assert published.payload["location_id"] == "LOC-04"
        assert published.source == "live_injection", (
            "an injected event must be identifiable in the audit trail"
        )

    async def test_a_dead_broker_refuses_rather_than_routing_around_it(
        self,
        _configured: Any,
        app_and_client: tuple[Any, AsyncClient],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The shortcut this design exists to avoid.

        Calling the engine directly when Kafka is down would keep the demo
        alive and silently make "it runs the same pipeline" untrue, at exactly
        the moment someone was watching. It 503s instead.
        """
        _stub(
            monkeypatch,
            Extracted(
                event_type="location.blocked",
                entity=EntityChoice(id="LOC-04", label="Courtyard", kind="location"),
                day=date(2026, 9, 10),
                raw_text="the courtyard permit fell through",
            ),
        )
        app, client = app_and_client
        app.state.kafka = _FakeKafka(enabled=False)

        response = await client.post(
            "/api/disruptions/inject",
            json={"text": "the courtyard permit fell through"},
            headers={"X-Inject-Passcode": PASSCODE},
        )
        assert response.status_code == 503

    async def test_the_recovery_is_not_driven_from_here(
        self,
        _configured: Any,
        app_and_client: tuple[Any, AsyncClient],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No session is created by the request itself.

        The consumer creates it when it reads the topic. If this endpoint ever
        started a recovery of its own, an injected event would run twice.
        """
        _stub(
            monkeypatch,
            Extracted(
                event_type="location.blocked",
                entity=EntityChoice(id="LOC-04", label="Courtyard", kind="location"),
                day=date(2026, 9, 10),
                raw_text="the courtyard permit fell through",
            ),
        )
        app, client = app_and_client
        app.state.kafka = _FakeKafka()

        await client.post(
            "/api/disruptions/inject",
            json={"text": "the courtyard permit fell through"},
            headers={"X-Inject-Passcode": PASSCODE},
        )

        latest = await client.get("/api/productions/film-001/sessions/latest")
        assert latest.json().get("session_id") is None


@pytest.mark.asyncio
class TestTheClosedSetIsReadable:
    async def test_it_lists_what_can_be_named(
        self, app_and_client: tuple[Any, AsyncClient]
    ) -> None:
        """Open on purpose: it shows only what the strip board already shows."""
        _, client = app_and_client
        response = await client.get("/api/disruptions/injectable")

        assert response.status_code == 200
        body = response.json()
        assert {loc["id"] for loc in body["locations"]} >= {"LOC-04"}
        assert {person["id"] for person in body["cast"]} >= {"P01"}
        assert body["dates"]

    async def test_it_reports_whether_injection_is_enabled(
        self, _configured: Any, app_and_client: tuple[Any, AsyncClient]
    ) -> None:
        _, client = app_and_client
        response = await client.get("/api/disruptions/injectable")
        assert response.json()["enabled"] is True
