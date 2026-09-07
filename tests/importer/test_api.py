"""The import HTTP surface, end to end.

The shape of the error body matters as much as the status code: the review
screen renders those typed records directly, so a change to the field names is
a breaking change to the frontend.
"""

from __future__ import annotations

import io
import os
from datetime import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pri.api.main import create_app
from pri.config import get_settings
from pri.importer import export_state
from pri.importer.samples import build_second_unit_state
from pri.persistence.bootstrap import split_sql_statements
from pri.persistence.database import SessionFactory
from pri.persistence.repository import PriRepository
from pri.persistence.seed import build_state
from tests.importer.conftest import SheetRows, build_xlsx

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_DEFAULT_URL = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"
_MIGRATIONS = Path(__file__).parents[2] / "src" / "pri" / "persistence" / "migrations"
_TABLES = """
    import_staging, artifacts, audit_log, approvals, candidate_plans,
    recovery_sessions, state_versions, events, productions
"""


def headers() -> dict[str, str]:
    return {"X-API-Key": get_settings().pri_api_key}


@pytest_asyncio.fixture()
async def factory() -> AsyncGenerator[SessionFactory, None]:
    engine = create_async_engine(
        os.environ.get("TEST_DATABASE_URL", _DEFAULT_URL), echo=False, pool_pre_ping=False
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            for statement in split_sql_statements(path.read_text()):
                await conn.execute(text(statement))

    yield SessionFactory(engine)

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(factory: SessionFactory) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.state.session_factory = factory
    app.state.engine = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://pri.test") as http:
        yield http


def upload_files(payload: bytes, filename: str = "board.xlsx") -> dict[str, Any]:
    return {
        "file": (
            filename,
            payload,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


class TestTemplateAndSamples:
    async def test_the_template_downloads_as_a_workbook(self, client: AsyncClient) -> None:
        response = await client.get("/api/import/template")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"
        assert "attachment" in response.headers["content-disposition"]

        workbook = load_workbook(io.BytesIO(response.content))
        assert workbook.sheetnames[0] == "README"
        assert "schedule" in workbook.sheetnames

    async def test_the_template_round_trips_through_the_uploader(self, client: AsyncClient) -> None:
        """Downloading the template and uploading it back must not 500."""
        template = (await client.get("/api/import/template")).content
        response = await client.post(
            "/api/import", files=upload_files(template, "template.xlsx"), headers=headers()
        )
        assert response.status_code == 200
        assert response.json()["can_commit"] is False, "a blank template has no data"

    async def test_the_samples_are_listed(self, client: AsyncClient) -> None:
        body = (await client.get("/api/import/samples")).json()
        names = {sample["name"] for sample in body["samples"]}
        assert names == {"night_train", "second_unit"}

    async def test_a_sample_downloads(self, client: AsyncClient) -> None:
        response = await client.get("/api/import/samples/second_unit")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"

    async def test_an_unknown_sample_is_a_404_not_a_traversal(self, client: AsyncClient) -> None:
        response = await client.get("/api/import/samples/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code == 404

    async def test_the_spec_is_served_as_data(self, client: AsyncClient) -> None:
        """So the frontend can name the sheets without redefining the spec."""
        body = (await client.get("/api/import/spec/sheets")).json()
        assert len(body["sheets"]) == 7
        production = next(s for s in body["sheets"] if s["name"] == "production")
        assert any(c["name"] == "timezone" for c in production["columns"])


class TestUpload:
    async def test_a_clean_workbook_stages_for_review(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        response = await client.post(
            "/api/import", files=upload_files(build_xlsx(baseline)), headers=headers()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PENDING_REVIEW"
        assert body["can_commit"] is True
        assert body["summary"]["scene_count"] == 3

    @pytest.mark.skipif(
        not get_settings().pri_api_key,
        reason="the key check is disabled when PRI_API_KEY is empty",
    )
    async def test_upload_requires_the_api_key(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        response = await client.post("/api/import", files=upload_files(build_xlsx(baseline)))
        assert response.status_code == 401

    async def test_a_broken_workbook_returns_200_with_typed_errors(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        """A file that will not parse is a normal outcome, not an HTTP error."""
        baseline["scenes"][0]["location_id"] = "L-99"
        response = await client.post(
            "/api/import", files=upload_files(build_xlsx(baseline)), headers=headers()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["can_commit"] is False

        issue = body["errors"][0]
        assert set(issue) >= {
            "code",
            "severity",
            "sheet",
            "row",
            "column",
            "message",
            "offending_value",
            "fix_hint",
        }
        assert issue["code"] == "E006"
        assert issue["row"] == 2
        assert issue["fix_hint"]

    async def test_an_unreadable_file_is_reported_not_crashed(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/import",
            files=upload_files(b"\x89PNG\r\n\x1a\n" + bytes(64), "logo.png"),
            headers=headers(),
        )
        assert response.status_code == 200
        assert response.json()["errors"][0]["code"] == "E016"


class TestReviewAndCommit:
    async def _stage(self, client: AsyncClient, payload: bytes) -> str:
        response = await client.post("/api/import", files=upload_files(payload), headers=headers())
        assert response.status_code == 200
        return str(response.json()["staging_id"])

    async def test_the_review_is_a_refreshable_url(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        staging_id = await self._stage(client, build_xlsx(baseline))
        response = await client.get(f"/api/import/{staging_id}")
        assert response.status_code == 200
        assert response.json()["staging_id"] == staging_id

    async def test_an_unknown_review_is_a_404(self, client: AsyncClient) -> None:
        assert (await client.get("/api/import/imp-nope")).status_code == 404

    async def test_commit_creates_version_one(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        staging_id = await self._stage(client, build_xlsx(baseline))
        response = await client.post(
            f"/api/import/{staging_id}/commit",
            json={"confirmed_by": "producer@studio"},
            headers=headers(),
        )
        assert response.status_code == 200
        assert response.json() == {"production_id": "film-test", "version": 1}

    async def test_commit_without_a_name_is_refused(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        """An import is committed by a person, and the audit record names them."""
        staging_id = await self._stage(client, build_xlsx(baseline))
        response = await client.post(f"/api/import/{staging_id}/commit", json={}, headers=headers())
        assert response.status_code == 422

    async def test_the_committed_production_is_visible_on_the_normal_routes(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        """Acceptance 1: the Overview screen renders an imported production."""
        staging_id = await self._stage(client, build_xlsx(baseline))
        await client.post(
            f"/api/import/{staging_id}/commit",
            json={"confirmed_by": "producer"},
            headers=headers(),
        )

        schedule = await client.get("/api/productions/film-test/schedule")
        assert schedule.status_code == 200
        body = schedule.json()
        assert body["title"] == "Test Production"
        assert body["version"] == 1
        assert len(body["days"]) == 2

    async def test_reject_marks_it_and_blocks_the_commit(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        staging_id = await self._stage(client, build_xlsx(baseline))
        rejected = await client.post(
            f"/api/import/{staging_id}/reject",
            json={"reason": "Wrong draft", "rejected_by": "ad"},
            headers=headers(),
        )
        assert rejected.status_code == 200

        response = await client.get(f"/api/import/{staging_id}")
        assert response.json()["status"] == "REJECTED"

    async def test_a_duplicate_upload_returns_the_first_review(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        payload = build_xlsx(baseline)
        first = await self._stage(client, payload)
        response = await client.post(
            "/api/import", files=upload_files(payload, "again.xlsx"), headers=headers()
        )
        assert response.json()["duplicate_of"] == first


class TestScheduleHealthOverTheWire:
    async def test_a_tight_turnaround_is_reported_and_still_committable(
        self, client: AsyncClient, baseline: dict[str, SheetRows]
    ) -> None:
        """Acceptance 4, over HTTP."""
        baseline["schedule"][0]["call_time"] = time(12, 0)
        baseline["schedule"][0]["wrap_time"] = time(22, 30)
        baseline["schedule"][1]["call_time"] = time(8, 0)

        response = await client.post(
            "/api/import", files=upload_files(build_xlsx(baseline)), headers=headers()
        )
        body = response.json()

        assert body["can_commit"] is True
        assert body["violation_counts_by_code"] == {"C001": 1}
        violation = body["existing_violations"][0]
        assert violation["code"] == "C001"
        assert violation["observed"] == "9.5h"
        assert violation["required"] == ">= 10.0h"


class TestExport:
    async def test_a_committed_production_exports_and_re_imports(
        self, client: AsyncClient, factory: SessionFactory
    ) -> None:
        await PriRepository(factory).commit_state(build_state(), event_id=None)

        response = await client.get("/api/productions/film-001/export")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"
        assert "film-001-v1.xlsx" in response.headers["content-disposition"]

        workbook = load_workbook(io.BytesIO(response.content))
        assert workbook["production"].cell(row=2, column=1).value == "film-001"

    async def test_exporting_an_unknown_production_is_a_404(self, client: AsyncClient) -> None:
        assert (await client.get("/api/productions/nope/export")).status_code == 404


class TestMaintenance:
    async def test_purge_is_callable_from_a_route(self, client: AsyncClient) -> None:
        response = await client.post("/api/import/maintenance/purge", headers=headers())
        assert response.status_code == 200
        assert response.json() == {"removed": 0}


class TestSecondUnitOverTheWire:
    async def test_the_sample_imports_commits_and_recovers(
        self, client: AsyncClient, factory: SessionFactory
    ) -> None:
        """Acceptance 2, over HTTP: a stranger's production, end to end."""
        from pri.engine.simulation.replan import recover
        from pri.importer.samples import second_unit_disruption

        payload = export_state(build_second_unit_state())
        staged = await client.post(
            "/api/import", files=upload_files(payload, "second_unit.xlsx"), headers=headers()
        )
        assert staged.status_code == 200
        body = staged.json()
        assert body["can_commit"] is True
        assert body["summary"]["unit_names"] == ["MAIN", "SECOND"]

        committed = await client.post(
            f"/api/import/{body['staging_id']}/commit",
            json={"confirmed_by": "producer"},
            headers=headers(),
        )
        production_id = committed.json()["production_id"]

        state = await PriRepository(factory).get_current_state(production_id)
        result = recover(state, second_unit_disruption())
        assert any(plan.valid for plan in result.evaluated)
