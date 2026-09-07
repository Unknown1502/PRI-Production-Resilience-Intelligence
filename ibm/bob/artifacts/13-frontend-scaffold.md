# Artifact 13 — Frontend Scaffold & Design System

**Prompt:** [PROMPT_13_frontend_scaffold.md](../prompts/PROMPT_13_frontend_scaffold.md)
**Gate:** `SCORE`
**Outcome:** complete — `next build` clean, 9 routes

---

## Plan

A production control room, not a chat app. Dark neutral base, exactly two signal
colours, dense information, and monospaced numerals on every figure.

## Files created

| File | Contents |
|---|---|
| `web/package.json`, `tsconfig.json`, `next.config.mjs`, `postcss.config.mjs` | Next.js 15, React 19, TypeScript strict |
| [`web/tailwind.config.ts`](../../../web/tailwind.config.ts) | The palette and the two signal colours |
| [`web/app/globals.css`](../../../web/app/globals.css) | Base layer, `.tnum`, panel and button components |
| [`web/lib/types.ts`](../../../web/lib/types.ts) | Types mirroring `api/schemas.py` |
| [`web/lib/api.ts`](../../../web/lib/api.ts) | Typed client, `PriApiError`, formatters |
| [`web/lib/stream.ts`](../../../web/lib/stream.ts) | `useRecoveryStream` with backoff reconnect |
| [`web/components/primitives.tsx`](../../../web/components/primitives.tsx) | `StatusPill`, `MetricCell`, `Panel`, `RuleViolationCard`, `CheckRow` |
| [`web/components/TopBar.tsx`](../../../web/components/TopBar.tsx) | Title, state version, live indicator, reset |
| [`web/components/Shell.tsx`](../../../web/components/Shell.tsx) | Left nav, shared SSE context |
| [`web/app/layout.tsx`](../../../web/app/layout.tsx), [`page.tsx`](../../../web/app/page.tsx) | Shell and Production Overview |

## Decisions

**Two signal colours, and only two.** `alert` for impact and violations, `clear`
for validated and approved. Every other distinction is made with weight and
spacing — a screen where six things are coloured is a screen where nothing reads
as urgent.

**`font-variant-numeric: tabular-nums` on every figure.** Columns of costs only
read as columns when the digits line up. That is the `.tnum` class and it is on
every number in the app.

**The state version lives in the top bar in large digits.** Watching it tick
from v1 to v2 after execution is the clearest single signal that something real
happened, and it is on screen for the whole demo rather than only on the
verification page.

**Types are hand-written, not generated.** The generated output for a
discriminated union of four move kinds is worse to read than the file, and the
API surface is small enough to keep honest by hand. The file says so, and says
that changing a field name in `schemas.py` means changing it here.

**The SSE connection lives in the shell, not per page.** Navigating between
Disruption and Recovery mid-demo would otherwise drop and re-establish it,
losing the frames that arrived while the page swapped.

**`PriApiError` carries `ruleCode` as a field.** The recovery screen renders a
violation card straight off it. Regexing an error message would be a contract
that breaks the first time someone rewords a sentence.

## Corrections made during the build

**`outputFileTracingRoot` set with `URL.pathname`.** On Windows that yields
`/C:/Users/...`, and the standalone build tried to `mkdir C:\Users\Users`.
Replaced with `fileURLToPath` + `dirname`. Needed at all because a stray
`package-lock.json` in the user's home directory made Next guess the wrong
workspace root.

**ESLint 9 versus `eslint-config-next`.** The flat config failed to patch and
the build printed a rushstack error. Pinned ESLint to `^8.57.1` with a legacy
`.eslintrc.json`; `npm run lint` is now clean rather than warning.

## How to run it

```bash
cd web
npm ci
npm run dev            # http://localhost:3000
npm run typecheck && npm run lint && npm run build
```

## Acceptance criteria

- ✅ `npm run build` clean, no TypeScript errors
- ✅ TypeScript strict, plus `noUncheckedIndexedAccess`
- ✅ Overview renders live seeded data
- ✅ No chat interface
- ✅ No component library beyond Tailwind primitives
- ✅ Every number on screen comes from the API

## Not covered

**No frontend tests at all.** No Vitest, no Playwright, no component tests. The
screens are verified by `tsc`, ESLint and a clean build — which catches type and
lint errors and nothing about behaviour. A Playwright run driving the demo would
be the single highest-value addition here.

**No loading skeletons.** Panels show a text line while fetching. Honest, but
plain.

**Accessibility is untested.** Semantic elements and labels are used throughout,
but nothing has been run through axe, and the colour contrast of `chalk-600` on
`ink-850` has not been measured.
