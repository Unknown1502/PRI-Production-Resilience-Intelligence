"""Shared dependencies: settings, database handles, auth.

The engine is stateless and pure, so there is very little to wire.  What lives
here is the database engine — one per process, created at startup and disposed
at shutdown — and the API-key check that guards every mutating route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from pri.config import Settings, get_settings
from pri.persistence.repository import PriRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = [
    "RepoDep",
    "SettingsDep",
    "get_repository",
    "require_api_key",
    "settings_dep",
]


def settings_dep() -> Settings:
    """Return the cached application settings."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]


def get_repository(request: Request) -> PriRepository:
    """Return the process-wide repository.

    Failure modes:
        Raises ``HTTPException`` 503 if the app is serving before its startup
        hook has run — which happens if a health probe races the lifespan.
    """
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not initialised yet",
        )
    return PriRepository(factory)


RepoDep = Annotated[PriRepository, Depends(get_repository)]


def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Guard mutating routes with a shared key.

    An empty ``PRI_API_KEY`` disables the check.  That is a deliberate escape
    hatch for local development and for the public demo, where every mutating
    route is either idempotent or resets to a fixture — but it is a setting, so
    a real deployment turns it on by giving it a value.

    Failure modes:
        Raises ``HTTPException`` 401 when a key is configured and the request
        does not carry it.
    """
    expected = settings.pri_api_key
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key",
        )


def engine_of(request: Request) -> AsyncEngine | None:
    """Return the app's database engine, if startup has created one."""
    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    return engine
