"""Project-wide fixtures.

The demo production is shared by the constraint, simulation, verification and
end-to-end suites, so it is built once here rather than re-declared per package.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from pri.config import get_settings
from pri.persistence.seed import build_disruption_event, build_state, load_fixture

if TYPE_CHECKING:
    from pri.domain.models import DisruptionEvent, ProductionState

# A fixed creation timestamp keeps the snapshot digest reproducible across runs,
# which the deduplication and verification tests depend on.
_FIXED_CREATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session", autouse=True)
def _hermetic_agent_mode() -> Any:
    """Pin the agent off for the whole suite.

    Without this the tests inherit whatever a developer has in `.env`. Turning
    `PRI_AGENT_ENABLED=true` on to verify the Vertex gate made the HTTP tests
    call a live model: six minutes instead of one, a billed request per test,
    and a suite that fails on an aeroplane. The agent path has its own tests in
    `tests/agent`, which drive a stubbed model.

    `os.environ` wins over `.env` in pydantic-settings, so this holds however
    the file is configured.
    """
    previous = os.environ.get("PRI_AGENT_ENABLED")
    os.environ["PRI_AGENT_ENABLED"] = "false"
    get_settings.cache_clear()
    yield
    if previous is None:
        os.environ.pop("PRI_AGENT_ENABLED", None)
    else:
        os.environ["PRI_AGENT_ENABLED"] = previous
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def night_train_fixture() -> dict[str, Any]:
    """The parsed ``data/fixtures/night_train.json`` document."""
    return load_fixture()


@pytest.fixture()
def night_train_state(night_train_fixture: dict[str, Any]) -> ProductionState:
    """Version 1 of the demo production, exactly as ``make seed`` would store it."""
    return build_state(night_train_fixture, created_at=_FIXED_CREATED_AT)


@pytest.fixture()
def loc04_blocked_event(night_train_fixture: dict[str, Any]) -> DisruptionEvent:
    """The canonical LOC-04 blocked disruption that drives the whole demo."""
    return build_disruption_event(night_train_fixture)
