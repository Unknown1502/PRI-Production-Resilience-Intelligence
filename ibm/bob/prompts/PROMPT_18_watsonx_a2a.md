# PROMPT 18 — watsonx Orchestrate A2A

> `[OPTIONAL — hard timebox 4 hours]`
>
> Not required by the rules. Bob + Confluent already satisfy the IBM track.
> If it is not working by your stop time, ship without it and say nothing about it.

## Task

Expose PRI's agent over A2A and register it in watsonx Orchestrate as an external agent.

**Before writing code**, read the current IBM watsonx Orchestrate external-agent /
A2A documentation and the current A2A spec version supported by both sides.
Do not invent wire syntax. If the two sides disagree on version, **stop and report**
rather than improvising.

## Build

| Path | Contents |
|---|---|
| `src/pri/agent/a2a/agent_card.json` | capabilities, skills, auth scheme, endpoint |
| `src/pri/agent/a2a/server.py` | A2A-conformant HTTPS endpoint mounted on the FastAPI app, bearer-token auth from Secret Manager, task lifecycle (submit / status / result), idempotency on task id, timeouts, structured errors |
| `ibm/orchestrate/` | exported agent, tool and connection assets |

## Task type

`production_recovery`: accepts `{production_id, state_version, event_id, event}`,
returns the `RecoveryResult` contract.

Everything behind `PRI_A2A_ENABLED`. **The system must run identically with it off.**

## Acceptance Criteria

Orchestrate invokes the PRI agent and receives plans; capture screenshots into
`docs/evidence/orchestrate/`.

## Stop Condition

If not connected after **4 hours**, disable the flag and move on.
