# PROMPT 13 — Frontend Scaffold + Design System

> Design is 25% of the score: *"Does the project deliver a complete, coherent
> product experience, not just a technical proof of concept?"*
> **Four finished screens beat ten wireframes.**

## Task

Create `web/` — Next.js 15 App Router, TypeScript strict, Tailwind,
`@xyflow/react`, no component library beyond Tailwind primitives.

## Visual direction

A **production control room**, not a chat app.

- Dark neutral base
- One signal colour for impact/violation, one for validated/approved
- Dense information, generous line-height
- **Monospaced numerals for all figures**
- Every number on screen is a real number from the API — **no placeholder data anywhere**

## Build

| Path | Contents |
|---|---|
| `lib/api.ts` | typed client for every route in PROMPT 09, generated types mirroring the Pydantic models |
| `lib/stream.ts` | `EventSource` hook for `/api/stream/{id}` with reconnect |
| `components/` | `StatusPill`, `MetricCell`, `RuleViolationCard` (shows code, observed, required), `PlanCard`, `Timeline`, `TopBar` (production title, state version, live connection indicator) |
| `app/layout.tsx` | the shell: left nav (Overview, Disruption, Recovery, Governance, Audit), top bar showing `state v{n}` prominently — the version number visibly incrementing after execution is a strong demo beat |
| `app/page.tsx` | Production Overview: schedule strip of shooting days, each day a card with call/wrap, location, scenes; a "Reset demo" button hitting `POST /api/demo/reset` |

## Acceptance Criteria

- `npm run build` clean, no TS errors
- Overview renders live seeded data

## Constraints

- DO NOT add a chat interface. **This is an operations console.**
