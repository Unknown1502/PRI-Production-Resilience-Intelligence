# Artifact 14 — Impact and Recovery Screens

**Prompt:** [PROMPT_14_impact_recovery_screens.md](../prompts/PROMPT_14_impact_recovery_screens.md)
**Gate:** `SCORE`
**Outcome:** complete

---

## Plan

The two screens that carry the demo. One shows the damage spreading; the other
shows a plan being rejected and repaired.

## Files created

| File | Contents |
|---|---|
| [`web/app/disruption/page.tsx`](../../../web/app/disruption/page.tsx) | Impact strip, `@xyflow/react` graph, live event feed |
| [`web/app/recovery/page.tsx`](../../../web/app/recovery/page.tsx) | Replanning timeline, plan grid, Pareto scatter, explanation, tool trace |
| [`web/components/Timeline.tsx`](../../../web/components/Timeline.tsx) | One row per pipeline stage |
| [`web/components/PlanCard.tsx`](../../../web/components/PlanCard.tsx) | One candidate, with its violation |

## Decisions

**The rejected-plan row is the loudest thing on either screen, and it stays.**
`CANDIDATE_INVALID` renders a `RuleViolationCard` with the rule code, the
observed value and the required value in a bordered, pulsing panel that does not
disappear when the repair arrives. The next row says the planner generated a
repair. The two together are the difference between this and a schedule chatbot.

**`observed` and `required` are printed verbatim.** The narrator reads them
aloud. A UI that turns `"9.0h"` into `"9 hours"` makes the narrator wrong, so
the strings go from the validator to the DOM untouched.

**The graph is laid out in columns by node type, not force-directed.** A force
layout looks livelier and tells you less. What a producer needs to see is which
scenes are hit and what they hang off, and columns make that readable at a
glance. Colour comes from the `status` field the backend computes — one
implementation of "what is affected", not two.

**The Pareto scatter is hand-drawn SVG.** Four points and a connected frontier
is not worth 90kB of charting dependency, and this way the axes are labelled in
the same units the plan cards use. Dominated plans grey, rejected plans hollow.

**Dominated plans are dimmed, not hidden.** The point of showing a frontier is
that a producer can see what they are *not* choosing, and why.

**The recovery screen fetches on `AWAITING_APPROVAL`.** The stream says a
recovery happened; the API has the numbers. Fetching on that stage means the
grid fills exactly when the run finished, with no polling.

## How to run it

```bash
make run-api
cd web && npm run dev
make demo            # both screens move with no manual refresh
```

## Acceptance criteria

- ✅ Event feed from SSE, newest first
- ✅ Graph coloured `ok` / `impacted` / `downstream`, re-colouring when the event lands
- ✅ Impact strip with blast radius as a percentage
- ✅ `RoundTrace` timeline rendering the loop as it streams
- ✅ The invalid card is visually loud and stays on screen
- ✅ Plan grid with Pareto badges, dominated plans dimmed
- ✅ Pareto scatter with the frontier connected
- ✅ Explanation panel plus collapsible tool-call trace
- ✅ "Request approval" on the recommended plan
- ✅ `emit_disruption.py` updates both screens live, zero manual refresh

## Not covered

**The tool-call trace is only populated in agent mode.** In deterministic mode
the panel is absent rather than showing "no tool calls were made", which is a
missed opportunity to make the fallback legible.

**The graph does not animate the recolour.** It refetches and re-renders. On
screen it reads as instant, which is the desired effect, but it is a swap not a
transition — a slower dissolve would sell the causality better on video.

**No empty-state for a production with no disruption history.** The disruption
screen shows the graph at rest with a line of explanatory text, which is fine,
but the event feed just says it is waiting.
