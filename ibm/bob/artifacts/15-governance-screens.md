# Artifact 15 — Governance, Verification & Audit Screens

**Prompt:** [PROMPT_15_governance_screens.md](../prompts/PROMPT_15_governance_screens.md)
**Gate:** `SCORE`
**Outcome:** complete

---

## Plan

The closing half of the flow: the policy and the seven gates, the six checks and
the diff, and the append-only log.

## Files created

| File | Contents |
|---|---|
| [`web/app/governance/page.tsx`](../../../web/app/governance/page.tsx) | Seven-step checklist, active policy, approval panel |
| [`web/app/verification/page.tsx`](../../../web/app/verification/page.tsx) | V1–V6, version banner, schedule diff, call-sheet download |
| [`web/app/audit/page.tsx`](../../../web/app/audit/page.tsx) | Append-only log with an action filter |

## Decisions

**The checklist lights up from what the service reports, not from a timer.**
Each step is green only if it appears in `steps_completed` on the response, and
the step that refused turns red from the error body's `step` field. Animating
them on a delay would be theatre; this is the actual sequence.

**Verification is recomputed on every load.** The screen calls
`/verification/{version}`, which re-runs the six checks against the stored
state. Reading a cached verdict out of a table would prove only that something
once wrote it there.

**The diff shows changed days only.** A full diff of a 26-day shoot is noise.
What a producer needs is the two or three days that moved and what they moved
to, so `sameDay()` filters and the rest are dropped.

**The version banner is `v1 → v2` with the parent pointer shown.** Same reason
the top bar carries the version: the increment is the proof.

**Audit rows are colour-coded by outcome, not by step.** Refusals and
human-review entries are alert; completions and approvals are clear; the
in-sequence gate entries are caution. Scanning for what went wrong should not
require reading every row.

## How to run it

```bash
make run-api
cd web && npm run dev
# Recovery screen → Request approval → Governance → Execute
```

## Acceptance criteria

- ✅ Policy rendered as the ten active rules
- ✅ Seven-step checklist lighting up as the service reports each one
- ✅ Approval panel: plan summary, approver field, approve / reject, note
- ✅ V1–V6 with pass/fail and detail text
- ✅ Before/after schedule diff, changed rows highlighted
- ✅ "Download updated call sheet" hitting `/api/artifacts/{id}`
- ✅ Version banner incrementing with the parent pointer
- ✅ Audit table with action filter

## Not covered

**The policy table is hard-coded in the page.** The prompt asks for it to be
rendered *from* `config/policies.yaml`; there is no endpoint serving that YAML,
so the ten rows are a constant in `governance/page.tsx`. They match the file
today. If someone edits a threshold, the screen will lie. A
`GET /api/policy` endpoint would fix it properly and was not built.

**The governance screen only knows about a session the stream told it about.**
Reload the page after the `AWAITING_APPROVAL` frame has passed and the approval
panel is empty, because there is no "list open sessions" endpoint to recover
from. The demo path never hits this; a judge clicking around might.

**Approver is a free-text field defaulted to a constant.** Consistent with the
backend having no identity provider, but it means the approval panel looks more
like a form than a control.
