# PROMPT 17 — Demo Scenario Runner

## Task

Make the demo **reproducible and unbreakable**.

## `scripts/run_demo.py`

One command that:

1. Resets to version 1
2. Waits for the web client to connect to SSE
3. Publishes the **LOC-04 blocked** event to Confluent
4. Prints each pipeline stage to the terminal with timings

## `POST /api/demo/reset`

Must fully restore version 1, clear sessions, approvals, audit and artifacts, and
be **safe to press mid-flow**.

## Demo button

Add a "Demo" button in the UI top bar that calls it, so a judge exploring the
hosted URL at 2am can replay the whole story without reading the README.
Most submissions forget this; it converts a passive judge into an engaged one.

## Scripted fallback

If `PRI_AGENT_ENABLED` is false or Gemini errors, the pipeline still completes
deterministically with a templated explanation, and the UI shows a small
**"deterministic mode"** badge.

**Never let a quota error kill the hosted demo.**

## Acceptance Criteria

reset, demo, reset, demo — three times, **identical results**.
