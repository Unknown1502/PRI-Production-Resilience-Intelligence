#!/usr/bin/env python
"""One command that runs the whole story, with timings.

    python scripts/run_demo.py                 # against a local API
    python scripts/run_demo.py --api https://pri-api-xxxxx.run.app

It resets to version 1, waits for a browser to attach to the SSE stream so the
screens are watching, publishes the disruption, then drives approval, execution
and verification — printing each stage as it lands.

Run it three times in a row and you should get identical output three times.
That is the acceptance criterion, and it is the reason the demo can be given
live without a rehearsal.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from pri.config import get_settings
from pri.persistence.seed import build_disruption_event

APPROVER = "producer@nighttrain"


class Timer:
    """Prints each stage with the milliseconds it took."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._last = self._start

    def stage(self, label: str, detail: str = "") -> None:
        now = time.perf_counter()
        step_ms = (now - self._last) * 1000
        total_ms = (now - self._start) * 1000
        self._last = now
        suffix = f"  {detail}" if detail else ""
        print(f"  [{step_ms:7.0f}ms / {total_ms:7.0f}ms]  {label}{suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=None, help="API base URL.")
    parser.add_argument(
        "--production", default=None, help="Production id. Defaults to the demo production."
    )
    parser.add_argument(
        "--wait-for-viewer",
        type=float,
        default=0.0,
        help="Seconds to wait before publishing, so a browser can attach to the stream.",
    )
    parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Stop after recovery, leaving the approval for a human to click.",
    )
    return parser.parse_args()


def _api_base(settings: Any, override: str | None) -> str:
    if override:
        return override.rstrip("/")
    host = settings.pri_api_host
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    return f"http://{host}:{settings.pri_api_port}"


def main() -> int:
    args = parse_args()
    settings = get_settings()
    base = _api_base(settings, args.api)
    production = args.production or settings.pri_demo_production_id
    headers = {"X-API-Key": settings.pri_api_key} if settings.pri_api_key else {}

    print(f"\nPRI demo  -  {base}  -  {production}\n")
    timer = Timer()

    with httpx.Client(base_url=base, headers=headers, timeout=120.0) as http:
        health = http.get("/health").json()
        timer.stage(
            "health",
            f"v{health['version']} sha {health['git_sha']} db {health['database']}",
        )

        reset = http.post("/api/demo/reset")
        reset.raise_for_status()
        timer.stage("reset", f"version {reset.json()['version']}")

        if args.wait_for_viewer > 0:
            print(f"\n  Open {settings.pri_frontend_url} now.")
            time.sleep(args.wait_for_viewer)
            timer.stage("viewer window closed")

        event = build_disruption_event()
        ingest = http.post(
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
        ingest.raise_for_status()
        timer.stage("EVENT_RECEIVED", f"{event.event_type} {event.payload.get('location_id')}")

        recover = http.post(
            f"/api/productions/{production}/recover", json={"event_id": event.event_id}
        )
        recover.raise_for_status()
        body = recover.json()
        impact = body["impact"]
        timer.stage(
            "IMPACT_COMPUTED",
            f"{len(impact['directly_affected_scene_ids'])} scenes, "
            f"blast radius {impact['blast_radius'] * 100:.0f}%",
        )
        timer.stage(
            "CANDIDATES_GENERATED",
            ", ".join(c["label"] for c in body["candidates"] if c["label"] != "B2"),
        )

        for candidate in body["candidates"]:
            if candidate["valid"]:
                continue
            hard = next(v for v in candidate["violations"] if v["severity"] == "HARD")
            timer.stage(
                "CANDIDATE_INVALID",
                f"Plan {candidate['label']} - {hard['code']}: "
                f"observed {hard['observed']}, required {hard['required']}",
            )

        repaired = [c for c in body["candidates"] if c["label"].endswith("2")]
        for candidate in repaired:
            timer.stage(
                "REPLANNING" if not candidate["valid"] else "CANDIDATE_VALID",
                f"Plan {candidate['label']} generated and "
                f"{'valid' if candidate['valid'] else 'still invalid'}",
            )

        recommended = body["recommended_plan_id"]
        timer.stage(
            "AWAITING_APPROVAL",
            f"recommending {recommended}, frontier {body['pareto_plan_ids']} ({body['mode']} mode)",
        )

        if args.no_execute or recommended is None:
            print("\n  Stopping before approval. Click it in the console.\n")
            return 0

        session_id = body["session_id"]
        approve = http.post(
            f"/api/sessions/{session_id}/approve",
            json={"plan_id": recommended, "approver": APPROVER, "decision": "APPROVED"},
        )
        approve.raise_for_status()
        timer.stage("APPROVED", f"{recommended} by {APPROVER}")

        execute = http.post(
            f"/api/sessions/{session_id}/execute",
            json={"plan_id": recommended, "approver": APPROVER},
        )
        execute.raise_for_status()
        result = execute.json()
        timer.stage("EXECUTING", " -> ".join(result["steps_completed"]))

        checks = result["verification"]["checks"]
        passed = sum(1 for c in checks if c["passed"])
        timer.stage(
            "VERIFIED",
            f"{passed}/{len(checks)} checks green, "
            f"v{result['base_version']} -> v{result['new_version']}",
        )

        schedule = http.get(f"/api/productions/{production}/schedule").json()
        sep_11 = next((d for d in schedule["days"] if d["date"] == "2026-09-11"), None)
        if sep_11:
            call = sep_11["call_time"][11:16]
            timer.stage("call sheet", f"Sep 11 now calls at {call} with {sep_11['scene_ids']}")

    print("\nDone.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
