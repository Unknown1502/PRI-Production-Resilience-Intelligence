# PROMPT 15 — Governance, Approval, Verification Screens

## Task

Build the closing half of the flow.

## `app/governance/page.tsx`

- Policy document rendered from `config/policies.yaml` (which rules are active)
- The **forced execution sequence as a 7-step checklist**, each step lighting up
  green as the transition service reports it
- Approval panel: plan summary, approver field, approve / reject, note

## `app/verification/page.tsx`

- The six verification checks **V1–V6** with pass/fail and detail text
- Before/after schedule diff for affected days, changed rows highlighted
- "Download updated call sheet" button hitting `/api/artifacts/{id}`
- State version banner: **v1 to v2** with parent pointer shown

## `app/audit/page.tsx`

- Append-only audit log table: time, actor, action, subject, detail
- Filter by action

## Acceptance Criteria

Approving in the UI triggers execution, all six checks render green, the version
banner increments, and the downloaded PDF shows the new schedule.
