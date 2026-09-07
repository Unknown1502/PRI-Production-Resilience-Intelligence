"""Pytest configuration for persistence integration tests.

These tests require a running PostgreSQL instance.  The connection URL is read
from the ``TEST_DATABASE_URL`` environment variable (falls back to the default
docker-compose.dev.yml URL so you can run ``docker-compose -f
docker-compose.dev.yml up -d`` and then ``pytest tests/persistence/``).

The conftest:
1. Creates a fresh async engine per test function (avoids cross-loop issues).
2. Applies every migration to a clean schema at the start of every test.
3. Tears the schema down after each test so tests are fully isolated.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pri.persistence.bootstrap import split_sql_statements
from pri.persistence.database import SessionFactory

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ---------------------------------------------------------------------------
# Default URL matches docker-compose.dev.yml
# ---------------------------------------------------------------------------

_DEFAULT_URL = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"
#: Every migration, in filename order — the same schema the deploy builds.
#: Applying only 001 meant a test could pass against a schema that no
#: deployment has had since, which is how a primary-key change went unnoticed.
_MIGRATIONS_DIR = Path(__file__).parents[2] / "src" / "pri" / "persistence" / "migrations"


def _migration_sql() -> str:
    return "\n".join(path.read_text() for path in sorted(_MIGRATIONS_DIR.glob("*.sql")))


def _db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", _DEFAULT_URL)


# ---------------------------------------------------------------------------
# Function-scoped engine + schema: fully isolated per test
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def db_session_factory() -> AsyncGenerator[SessionFactory, None]:
    """Create engine, apply clean schema, yield SessionFactory, then tear down."""
    migration_sql = _migration_sql()

    # Fresh engine per test — avoids sharing asyncpg connections across event loops.
    engine = create_async_engine(_db_url(), echo=False, pool_pre_ping=False)

    async with engine.begin() as conn:
        # Drop all tables from any previous run.
        await conn.execute(
            text(
                """
                DROP TABLE IF EXISTS
                    artifacts, audit_log, approvals, candidate_plans,
                    recovery_sessions, state_versions, events, productions
                CASCADE
                """
            )
        )
        # Apply schema — split on semicolons and execute each statement.
        for stmt in split_sql_statements(migration_sql):
            await conn.execute(text(stmt))

    yield SessionFactory(engine)

    # Teardown.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DROP TABLE IF EXISTS
                    artifacts, audit_log, approvals, candidate_plans,
                    recovery_sessions, state_versions, events, productions
                CASCADE
                """
            )
        )

    await engine.dispose()
