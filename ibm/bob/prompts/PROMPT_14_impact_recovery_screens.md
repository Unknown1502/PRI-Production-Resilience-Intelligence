# PROMPT 14 — Impact and Recovery Screens

## Task

Build the two screens that carry the demo.

## `app/disruption/page.tsx` — Live Disruption + Impact

- Event feed from SSE, newest first, each with type, severity, timestamp
- `@xyflow/react` graph from `GET /graph`, nodes coloured by status:
  `ok` / `impacted` / `downstream`. Scene nodes show scene number + location.
- Impact summary strip: affected scenes, cast, locations, downstream count,
  **blast radius as a percentage**
- The graph must **visibly re-colour** when the disruption event arrives

## `app/recovery/page.tsx` — Counterfactual Recovery

- A `RoundTrace` timeline rendering the recovery loop as it streams:

  ```
  3 candidates generated
  Plan B INVALID — C001 crew_turnaround: observed 9.0h, required >= 10.0h
  Replanning
  Plan B2 generated
  Plan B2 valid
  ```

  The invalid card must be **visually loud** and must **stay on screen**.
  This single component is the difference between this project and a schedule chatbot.

- `PlanCard` grid: label, valid/invalid, delay, cost, risk, affected scenes,
  Pareto badge on non-dominated plans, dominated plans dimmed
- A small **Pareto scatter** (cost on Y, delay on X) with the frontier connected
- Gemini's explanation panel + a collapsible **tool-call trace** showing the real
  ADK tool invocations
- "Request approval" on the recommended plan

## Acceptance Criteria

Driving the flow with `scripts/emit_disruption.py` updates both screens live with
**zero manual refresh**.
