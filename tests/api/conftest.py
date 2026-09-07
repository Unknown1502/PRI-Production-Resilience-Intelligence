"""Fixtures for the HTTP integration tests.

These run against a real PostgreSQL — the same one ``docker-compose.dev.yml``
brings up — because the guarantees under test are transactional. A fake
repository would happily "roll back" a write it never made.

The client is ``httpx.AsyncClient`` over ``ASGITransport`` rather than
``TestClient``: asyncpg connections are bound to the event loop that opened
them, and ``TestClient`` drives the app from its own portal thread, so a pool
created in the test's loop cannot be used from inside a request. Everything
here stays on one loop.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pri.api.main import create_app
from pri.api.stream import get_broker
from pri.config import get_settings
from pri.persistence.bootstrap import split_sql_statements
from pri.persistence.database import SessionFactory
from pri.persistence.repository import PriRepository
from pri.persistence.seed import build_disruption_event, build_state

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_DEFAULT_URL = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"
#: Every migration, in filename order — the same schema the deploy builds.
_MIGRATIONS_DIR = Path(__file__).parents[2] / "src" / "pri" / "persistence" / "migrations"
_TABLES = """
    artifacts, audit_log, approvals, candidate_plans,
    recovery_sessions, state_versions, events, productions
"""

PRODUCTION = "film-001"
APPROVER = "producer@nighttrain"


def api_headers() -> dict[str, str]:
    """The header every mutating route requires."""
    return {"X-API-Key": get_settings().pri_api_key}


def loc04_event_body() -> dict[str, Any]:
    """The canonical demo disruption, as the HTTP API takes it."""
    event = build_disruption_event()
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "source": event.source,
        "severity": event.severity,
        "payload": event.payload,
    }


def _db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", _DEFAULT_URL)


@pytest_asyncio.fixture()
async def seeded_factory(tmp_path: Path) -> AsyncGenerator[SessionFactory, None]:
    """A clean schema with the demo production committed as version 1."""
    # Point call-sheet output at the test's tmp dir before anything reads
    # settings, so a run never litters the repo with PDFs.
    get_settings.cache_clear()
    os.environ["PRI_ARTIFACT_ROOT"] = str(tmp_path / "artifacts")

    engine = create_async_engine(_db_url(), echo=False, pool_pre_ping=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            for statement in split_sql_statements(path.read_text()):
                await conn.execute(text(statement))

    factory = SessionFactory(engine)
    await PriRepository(factory).commit_state(build_state(), event_id=None)

    yield factory

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
    await engine.dispose()

    os.environ.pop("PRI_ARTIFACT_ROOT", None)
    get_settings.cache_clear()


@pytest_asyncio.fixture()
async def client(seeded_factory: SessionFactory) -> AsyncGenerator[AsyncClient, None]:
    """An ASGI client bound to the seeded schema, on the test's own event loop."""
    app = create_app()
    app.state.session_factory = seeded_factory
    app.state.engine = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://pri.test") as http:
        yield http


@pytest.fixture()
def repo(seeded_factory: SessionFactory) -> PriRepository:
    """Direct repository access, for assertions the API does not expose."""
    return PriRepository(seeded_factory)


@pytest.fixture(autouse=True)
def _quiet_broker() -> Any:
    """Drop any subscribers left behind by a previous test's stream."""
    broker = get_broker()
    yield
    broker._subscribers.clear()
