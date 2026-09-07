"""Async SQLAlchemy 2 engine and session factory.

Call ``build_engine(url)`` once at startup and pass the returned engine to
``SessionFactory``.  Never import a module-level engine singleton here — callers
supply the URL so the module stays testable without environment variables.

Example::

    from pri.persistence.database import build_engine, SessionFactory

    engine = build_engine(settings.database_dsn)
    factory = SessionFactory(engine)

    async with factory() as session:
        result = await session.execute(select(…))
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def build_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an ``AsyncEngine`` from a connection URL.

    Inputs:
        url:  A ``postgresql+asyncpg://…`` DSN string.
        echo: When ``True``, emit all SQL to stdout (for debugging only).

    Outputs:
        A configured :class:`AsyncEngine`.

    Failure modes:
        Raises ``sqlalchemy.exc.ArgumentError`` if ``url`` is malformed.
    """
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


class SessionFactory:
    """Async session factory wrapping an ``AsyncEngine``.

    Inputs (constructor):
        engine: A live :class:`AsyncEngine`.

    Usage::

        factory = SessionFactory(engine)
        async with factory() as session:
            ...   # session is committed/rolled-back automatically

    Failure modes:
        The context manager rolls back and re-raises on any exception.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield an ``AsyncSession`` with automatic commit/rollback.

        Outputs:
            An open :class:`AsyncSession` for the duration of the ``async with`` block.

        Failure modes:
            Rolls back and re-raises any exception propagating from the block.
        """
        async with self._maker() as session, session.begin():
            yield session
