#!/usr/bin/env python
"""Go/no-go gate: prove two judges can watch the same demo at once.

    python scripts/verify_live_demo.py https://pri-web-xxxx.run.app
    python scripts/verify_live_demo.py https://pri-api-xxxx.run.app --no-reset

Run this immediately before the judging window. It is the one check that
exercises the assumption the deployment is pinned to: the SSE broker holds its
subscribers in a process-local dict, so a second viewer is only guaranteed to
see the pipeline if both viewers and the recovery land on the same instance.
``infra/deploy.sh`` pins ``pri-api`` to one instance for exactly this reason,
and this script is what tells you the pin is still in force.

What it does:

  1. Opens two independent SSE connections, on separate HTTP clients, and
     waits for both to be attached before anything is published.
  2. Drives one disruption all the way through — ingest, recover, approve,
     execute — because the ten stages span all four calls.
  3. Asserts both connections saw all ten stages, in order, inside a deadline.
  4. Fetches the resulting call sheet on a third, fresh client.

Any failure exits non-zero naming the assertion that broke. Silence is not a
pass: every check prints.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from pri.config import get_settings

APPROVER = "verify_live_demo"

#: The happy path, in the order the UI renders it. Seven of these are published
#: by the recovery call, one by approve, two by execute — which is why this
#: script drives the whole governance path rather than stopping at recovery.
#: FAILED is deliberately absent: it is the error path, and seeing it is a
#: failure of this gate.
EXPECTED_STAGES = (
    "EVENT_RECEIVED",
    "IMPACT_COMPUTED",
    "CANDIDATES_GENERATED",
    "CANDIDATE_INVALID",
    "REPLANNING",
    "CANDIDATE_VALID",
    "AWAITING_APPROVAL",
    "APPROVED",
    "EXECUTING",
    "VERIFIED",
)

GREEN = "\x1b[32m"
RED = "\x1b[31m"
RESET = "\x1b[0m"


class GateFailed(Exception):
    """One assertion failed. The message says which, and what was seen."""


def ok(message: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"  {GREEN}PASS{RESET}  {message}{suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pre-judging go/no-go gate.")
    parser.add_argument(
        "base_url",
        help="Deployed base URL — either the web app or the API service.",
    )
    parser.add_argument(
        "--production", default=None, help="Production id. Defaults to the demo production."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="Seconds to wait for both streams to complete the pipeline.",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not reset to version 1 first. The invalid-candidate stages "
        "only occur from a clean board, so expect CANDIDATE_INVALID to be "
        "missing if the production has already moved.",
    )
    return parser.parse_args()


async def resolve_api_base(base_url: str, headers: dict[str, str]) -> str:
    """Find the API root, whether the caller passed the web app or the API.

    The web service proxies the API under ``/api/pri`` and injects the key
    server-side. Pointing a checking script at the wrong one of the two URLs
    produces a page of spurious failures that look like a broken deployment, so
    this resolves it rather than trusting the operator to remember which is
    which at the worst possible moment.
    """
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as probe:
        for candidate in (base, f"{base}/api/pri"):
            try:
                response = await probe.get(f"{candidate}/health")
            except httpx.HTTPError:
                continue
            if response.status_code == 200 and "status" in response.text[:200]:
                return candidate
    raise GateFailed(
        f"no PRI API answered /health at {base} or {base}/api/pri — "
        "is the deployment up, and is this the right URL?"
    )


async def watch(
    name: str, api: str, production: str, headers: dict[str, str], attached: asyncio.Event
) -> list[str]:
    """One SSE viewer. Returns the stages it saw, in the order they arrived.

    Returns as soon as the last expected stage lands so the gate does not sit
    out its whole deadline on a healthy run.
    """
    seen: list[str] = []
    async with (
        httpx.AsyncClient(timeout=None, headers=headers) as client,
        client.stream("GET", f"{api}/api/stream/{production}") as response,
    ):
        if response.status_code != 200:
            raise GateFailed(
                f"{name}: the SSE stream returned HTTP {response.status_code}, expected 200"
            )
        async for line in response.aiter_lines():
            # ": connected" is sent immediately so a client knows it is
            # attached before anything is published.
            if line.startswith(":"):
                attached.set()
                continue
            if not line.startswith("event:"):
                continue
            stage = line.split(":", 1)[1].strip()
            seen.append(stage)
            if stage == "FAILED":
                raise GateFailed(f"{name}: the pipeline published FAILED")
            if stage == EXPECTED_STAGES[-1]:
                return seen
    return seen


async def drive(api: str, production: str, headers: dict[str, str], reset: bool) -> dict[str, Any]:
    """Publish one disruption and take it through to a committed version."""
    from pri.persistence.seed import build_disruption_event

    async with httpx.AsyncClient(base_url=api, headers=headers, timeout=300.0) as http:
        if reset:
            response = await http.post("/api/demo/reset")
            if response.status_code != 200:
                raise GateFailed(
                    f"demo reset returned HTTP {response.status_code}, expected 200 — "
                    "the gate needs a clean board to produce the invalid candidate"
                )

        event = build_disruption_event()
        ingest = await http.post(
            f"/api/productions/{production}/events",
            json={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at.isoformat(),
                "source": event.source,
                "severity": event.severity,
                "payload": event.payload,
            },
        )
        if ingest.status_code != 200:
            raise GateFailed(
                f"publishing the disruption returned HTTP {ingest.status_code}: {ingest.text[:200]}"
            )

        recover = await http.post(
            f"/api/productions/{production}/recover", json={"event_id": event.event_id}
        )
        if recover.status_code != 200:
            raise GateFailed(f"recovery returned HTTP {recover.status_code}: {recover.text[:200]}")
        body = recover.json()

        plan_id = body.get("recommended_plan_id")
        if not plan_id:
            raise GateFailed(
                "recovery recommended no plan, so there is nothing to approve — "
                f"candidates: {[c.get('label') for c in body.get('candidates', [])]}"
            )

        session_id = body["session_id"]
        approve = await http.post(
            f"/api/sessions/{session_id}/approve",
            json={"plan_id": plan_id, "approver": APPROVER, "decision": "APPROVED"},
        )
        if approve.status_code != 200:
            raise GateFailed(
                f"approving {plan_id} returned HTTP {approve.status_code}: {approve.text[:200]}"
            )

        execute = await http.post(
            f"/api/sessions/{session_id}/execute",
            json={"plan_id": plan_id, "approver": APPROVER},
        )
        if execute.status_code != 200:
            raise GateFailed(
                f"executing {plan_id} returned HTTP {execute.status_code}: {execute.text[:200]}"
            )
        return dict(execute.json())


def assert_sequence(name: str, seen: list[str]) -> None:
    """Every expected stage, in the expected order, with nothing missing."""
    missing = [stage for stage in EXPECTED_STAGES if stage not in seen]
    if missing:
        raise GateFailed(
            f"{name} did not see {', '.join(missing)}. It saw: "
            f"{' -> '.join(seen) if seen else '(nothing at all)'}"
        )

    filtered = [stage for stage in seen if stage in EXPECTED_STAGES]
    positions = [filtered.index(stage) for stage in EXPECTED_STAGES]
    if positions != sorted(positions):
        out_of_order = [
            EXPECTED_STAGES[i] for i in range(1, len(positions)) if positions[i] < positions[i - 1]
        ]
        raise GateFailed(
            f"{name} saw every stage but out of order — {', '.join(out_of_order)} "
            f"arrived early. Order seen: {' -> '.join(filtered)}"
        )


async def fetch_call_sheet(api: str, production: str, headers: dict[str, str]) -> str:
    """A fresh client, sharing nothing with either stream."""
    async with httpx.AsyncClient(base_url=api, headers=headers, timeout=120.0) as fresh:
        schedule = await fresh.get(f"/api/productions/{production}/schedule")
        if schedule.status_code != 200:
            raise GateFailed(f"the schedule returned HTTP {schedule.status_code} after execution")
        day = next((d for d in schedule.json()["days"] if d["scene_ids"]), None)
        if day is None:
            raise GateFailed("no shooting day on the board to render a call sheet for")

        pdf = await fresh.get(f"/api/productions/{production}/call-sheet/{day['date']}")
        if pdf.status_code != 200:
            raise GateFailed(
                f"the call sheet for {day['date']} returned HTTP {pdf.status_code}, expected 200"
            )
        if not pdf.content.startswith(b"%PDF"):
            raise GateFailed(
                f"the call sheet for {day['date']} is not a PDF — it starts {pdf.content[:16]!r}"
            )
        return f"{day['date']}, {len(pdf.content)} bytes"


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    production = args.production or settings.pri_demo_production_id
    headers = {"X-API-Key": settings.pri_api_key} if settings.pri_api_key else {}

    api = await resolve_api_base(args.base_url, headers)
    print(f"\nPRI live demo gate — {api} — {production}\n")
    ok("the API answers /health", api)

    attached_a, attached_b = asyncio.Event(), asyncio.Event()
    viewer_a = asyncio.create_task(watch("viewer A", api, production, headers, attached_a))
    viewer_b = asyncio.create_task(watch("viewer B", api, production, headers, attached_b))
    try:
        # Nothing is published until both are attached, otherwise a viewer can
        # miss EVENT_RECEIVED and the run fails for a reason that is this
        # script's fault rather than the deployment's.
        await asyncio.wait_for(asyncio.gather(attached_a.wait(), attached_b.wait()), timeout=60.0)
        ok("two independent SSE connections are attached")

        result = await asyncio.wait_for(
            drive(api, production, headers, reset=not args.no_reset), timeout=args.timeout
        )
        ok(
            "the pipeline ran to a committed version",
            f"v{result['base_version']} -> v{result['new_version']}",
        )

        seen_a, seen_b = await asyncio.wait_for(
            asyncio.gather(viewer_a, viewer_b), timeout=args.timeout
        )
    except TimeoutError as exc:
        for task in (viewer_a, viewer_b):
            task.cancel()
        raise GateFailed(
            f"timed out after {args.timeout:.0f}s waiting for both viewers to see the "
            "pipeline. If one viewer saw it and the other did not, the API is "
            "serving more than one instance — check --max-instances in infra/deploy.sh"
        ) from exc
    finally:
        for task in (viewer_a, viewer_b):
            task.cancel()

    assert_sequence("viewer A", seen_a)
    ok("viewer A saw all ten stages in order")
    assert_sequence("viewer B", seen_b)
    ok("viewer B saw all ten stages in order")

    if seen_a != seen_b:
        raise GateFailed(
            "the two viewers saw different streams, which means they were served "
            f"by different instances.\n    A: {' -> '.join(seen_a)}\n    B: {' -> '.join(seen_b)}"
        )
    ok("both viewers saw the same stream", f"{len(seen_a)} frames each")

    detail = await fetch_call_sheet(api, production, headers)
    ok("the call sheet fetches on a fresh connection", detail)

    # The gate commits a version to prove the pipeline runs, which would leave
    # the board at v2 for the demo this is supposed to be clearing. Put it back.
    if not args.no_reset:
        async with httpx.AsyncClient(base_url=api, headers=headers, timeout=300.0) as http:
            response = await http.post("/api/demo/reset")
            if response.status_code != 200:
                raise GateFailed(
                    f"the run passed but the board was left at v{result['new_version']}: "
                    f"the closing reset returned HTTP {response.status_code}. "
                    "Reset it before demonstrating."
                )
        ok("the board is back at version 1, ready to demonstrate")


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except GateFailed as exc:
        print(f"\n  {RED}FAIL{RESET}  {exc}\n")
        print("NO-GO. Do not start the judging window until this passes.\n")
        return 1
    print("\nGO. Two viewers, ten stages each, call sheet served.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
