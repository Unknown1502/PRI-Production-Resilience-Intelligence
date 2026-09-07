"""The demo, as one test file, over HTTP, against a real database.

If this goes red the video is wrong. It walks the exact path a judge walks:
seed, publish the LOC-04 block, look at the impact, read the three candidates,
watch Plan B get rejected by C001, see B2 repair it, approve B2, execute it
through the seven gates, and check the six verification rows.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pytest_asyncio

from tests.api.conftest import APPROVER, PRODUCTION, api_headers, loc04_event_body

if TYPE_CHECKING:
    from httpx import AsyncClient

    from pri.persistence.repository import PriRepository


@pytest_asyncio.fixture()
async def recovered(client: AsyncClient) -> dict[str, Any]:
    """Ingest the disruption and run recovery, returning the response body."""
    ingest = await client.post(
        f"/api/productions/{PRODUCTION}/events",
        json=loc04_event_body(),
        headers=api_headers(),
    )
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["duplicate"] is False

    response = await client.post(
        f"/api/productions/{PRODUCTION}/recover",
        json={"event_id": loc04_event_body()["event_id"]},
        headers=api_headers(),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


@pytest_asyncio.fixture()
async def executed(client: AsyncClient, recovered: dict[str, Any]) -> dict[str, Any]:
    """Approve B2 and run it through the seven gates."""
    session_id = recovered["session_id"]
    approve = await client.post(
        f"/api/sessions/{session_id}/approve",
        json={
            "plan_id": "plan-B2",
            "approver": APPROVER,
            "decision": "APPROVED",
            "note": "Protects the VFX plates.",
        },
        headers=api_headers(),
    )
    assert approve.status_code == 200, approve.text

    response = await client.post(
        f"/api/sessions/{session_id}/execute",
        json={"plan_id": "plan-B2", "approver": APPROVER},
        headers=api_headers(),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _plan(body: dict[str, Any], label: str) -> dict[str, Any]:
    return next(c for c in body["candidates"] if c["label"] == label)


# ---------------------------------------------------------------------------
# The baseline
# ---------------------------------------------------------------------------


async def test_health_reports_the_build(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert "git_sha" in body


async def test_schedule_starts_at_version_one(client: AsyncClient) -> None:
    response = await client.get(f"/api/productions/{PRODUCTION}/schedule")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 1
    assert body["title"] == "Night Train to Kochi"
    assert len(body["days"]) == 9

    sep_10 = next(d for d in body["days"] if d["date"] == "2026-09-10")
    assert sep_10["location_id"] == "LOC-04"
    assert sep_10["scene_ids"] == ["S17", "S18", "S21"]


async def test_seeded_state_is_legal(client: AsyncClient) -> None:
    response = await client.get(f"/api/productions/{PRODUCTION}/state")
    assert response.status_code == 200
    assert response.json()["hard_violation_codes"] == []


async def test_graph_is_all_ok_before_the_disruption(client: AsyncClient) -> None:
    response = await client.get(f"/api/productions/{PRODUCTION}/graph")
    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"]
    assert {n["status"] for n in payload["nodes"]} == {"ok"}


async def test_mutating_routes_require_the_api_key(client: AsyncClient) -> None:
    response = await client.post(f"/api/productions/{PRODUCTION}/events", json=loc04_event_body())
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


async def test_impact_matches_the_narration(recovered: dict[str, Any]) -> None:
    impact = recovered["impact"]
    assert set(impact["directly_affected_scene_ids"]) == {"S17", "S18", "S21"}
    assert set(impact["downstream_scene_ids"]) == {"S28"}
    assert date.fromisoformat(impact["affected_days"][0]) == date(2026, 9, 10)


async def test_graph_recolours_once_the_event_is_known(
    client: AsyncClient, recovered: dict[str, Any]
) -> None:
    response = await client.get(
        f"/api/productions/{PRODUCTION}/graph",
        params={"event_id": recovered["event_id"]},
    )
    assert response.status_code == 200
    statuses = {n["status"] for n in response.json()["nodes"]}
    assert "impacted" in statuses
    assert "downstream" in statuses


# ---------------------------------------------------------------------------
# The three candidates and the rejection
# ---------------------------------------------------------------------------


async def test_three_candidates_plus_one_repair(recovered: dict[str, Any]) -> None:
    assert [c["label"] for c in recovered["candidates"]] == ["A", "B", "C", "B2"]
    assert recovered["mode"] == "deterministic"


async def test_plan_b_is_rejected_by_c001_at_nine_hours(recovered: dict[str, Any]) -> None:
    plan_b = _plan(recovered, "B")
    assert plan_b["valid"] is False
    hard = [v for v in plan_b["violations"] if v["severity"] == "HARD"]
    assert len(hard) == 1
    assert hard[0]["code"] == "C001"
    assert hard[0]["observed"] == "9.0h"
    assert hard[0]["required"] == ">= 10.0h"


async def test_the_timeline_says_what_the_narrator_says(recovered: dict[str, Any]) -> None:
    first_round = recovered["rounds"][0]
    assert "3 candidates generated" in first_round["note"]
    assert "C001" in first_round["note"]
    assert first_round["failed_rule_codes"] == ["C001"]
    assert recovered["rounds"][1]["repaired_plan_ids"] == ["plan-B2"]


async def test_b2_repairs_it_and_the_frontier_is_a_and_b2(recovered: dict[str, Any]) -> None:
    assert _plan(recovered, "B2")["valid"] is True
    assert recovered["pareto_plan_ids"] == ["plan-A", "plan-B2"]


async def test_explanation_quotes_the_rule_verbatim(recovered: dict[str, Any]) -> None:
    explanation = recovered["explanation"]
    assert "C001" in explanation
    assert "9.0h" in explanation
    assert ">= 10.0h" in explanation
    assert "failed deterministic validation" in explanation


async def test_candidates_are_persisted(repo: PriRepository, recovered: dict[str, Any]) -> None:
    stored = await repo.get_candidates(recovered["session_id"])
    assert {row["label"] for row in stored} == {"A", "B", "C", "B2"}


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


async def test_execute_without_approval_is_refused_at_step_five(
    client: AsyncClient, recovered: dict[str, Any]
) -> None:
    response = await client.post(
        f"/api/sessions/{recovered['session_id']}/execute",
        json={"plan_id": "plan-B2", "approver": APPROVER},
        headers=api_headers(),
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "approval_missing"
    assert body["step"] == "request_approval"


async def test_execute_by_someone_other_than_the_approver_is_refused(
    client: AsyncClient, recovered: dict[str, Any]
) -> None:
    session_id = recovered["session_id"]
    await client.post(
        f"/api/sessions/{session_id}/approve",
        json={"plan_id": "plan-B2", "approver": APPROVER, "decision": "APPROVED"},
        headers=api_headers(),
    )
    response = await client.post(
        f"/api/sessions/{session_id}/execute",
        json={"plan_id": "plan-B2", "approver": "someone-else"},
        headers=api_headers(),
    )
    assert response.status_code == 409
    assert response.json()["step"] == "request_approval"


async def test_execution_walks_all_seven_steps(executed: dict[str, Any]) -> None:
    assert executed["steps_completed"] == [
        "validate_state",
        "validate_constraints",
        "validate_policy",
        "verify_authorization",
        "request_approval",
        "execute",
        "verify_result",
    ]


async def test_version_increments_to_two(executed: dict[str, Any]) -> None:
    assert executed["base_version"] == 1
    assert executed["new_version"] == 2


async def test_all_six_verification_checks_pass(executed: dict[str, Any]) -> None:
    checks = executed["verification"]["checks"]
    assert [c["code"] for c in checks] == ["V1", "V2", "V3", "V4", "V5", "V6"]
    assert all(c["passed"] for c in checks)
    assert executed["verification"]["valid"] is True


async def test_call_sheets_were_regenerated(executed: dict[str, Any]) -> None:
    assert executed["artifacts"]
    assert any("2026-09-11" in path for path in executed["artifacts"])


async def test_executing_twice_writes_no_second_version(
    client: AsyncClient, recovered: dict[str, Any], executed: dict[str, Any]
) -> None:
    again = await client.post(
        f"/api/sessions/{recovered['session_id']}/execute",
        json={"plan_id": "plan-B2", "approver": APPROVER},
        headers=api_headers(),
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["replayed"] is True
    assert body["new_version"] == executed["new_version"]

    head = await client.get(f"/api/productions/{PRODUCTION}/state")
    assert head.json()["version"] == 2


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


async def test_new_schedule_has_the_0830_call(
    client: AsyncClient, executed: dict[str, Any]
) -> None:
    del executed
    body = (await client.get(f"/api/productions/{PRODUCTION}/schedule")).json()
    assert body["version"] == 2

    # The repair moved the call, not the workload: 08:30 to the 19:00 permit
    # wall is 10.5h, and the courtyard block still fits inside it.
    sep_11 = next(d for d in body["days"] if d["date"] == "2026-09-11")
    assert sep_11["call_time"].endswith("08:30:00+05:30")
    assert sep_11["location_id"] == "LOC-04"
    assert sep_11["scene_ids"] == ["S17", "S18", "S21"]

    # And the swap put the warehouse night onto the 10th.
    sep_10 = next(d for d in body["days"] if d["date"] == "2026-09-10")
    assert sep_10["location_id"] == "LOC-03"
    assert sep_10["scene_ids"] == ["S24", "S25"]


async def test_new_state_is_legal(client: AsyncClient, executed: dict[str, Any]) -> None:
    del executed
    state = (await client.get(f"/api/productions/{PRODUCTION}/state")).json()
    assert state["hard_violation_codes"] == []
    assert state["parent_version"] == 1


async def test_verification_endpoint_recomputes_green(
    client: AsyncClient, executed: dict[str, Any]
) -> None:
    del executed
    response = await client.get(f"/api/productions/{PRODUCTION}/verification/2")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert len(body["checks"]) == 6
    assert body["artifacts"]


async def test_call_sheet_downloads_as_a_pdf(client: AsyncClient, executed: dict[str, Any]) -> None:
    del executed
    verification = await client.get(f"/api/productions/{PRODUCTION}/verification/2")
    sheet = next(a for a in verification.json()["artifacts"] if a["kind"] == "call_sheet")
    response = await client.get(f"/api/artifacts/{sheet['id']}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


async def test_audit_log_records_every_gate(client: AsyncClient, executed: dict[str, Any]) -> None:
    del executed
    entries = (await client.get(f"/api/productions/{PRODUCTION}/audit")).json()
    actions = {e["action"] for e in entries}
    for step in (
        "validate_state",
        "validate_constraints",
        "validate_policy",
        "verify_authorization",
        "request_approval",
    ):
        assert f"transition.{step}" in actions
    assert "transition.completed" in actions
    assert "approval.approved" in actions


# ---------------------------------------------------------------------------
# Idempotency and reset
# ---------------------------------------------------------------------------


async def test_reposting_the_same_event_is_a_no_op(client: AsyncClient) -> None:
    body = loc04_event_body()
    first = await client.post(
        f"/api/productions/{PRODUCTION}/events", json=body, headers=api_headers()
    )
    second = await client.post(
        f"/api/productions/{PRODUCTION}/events", json=body, headers=api_headers()
    )
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
